"""The local SQLite store for confirmed volatility surfaces (Issue #183).

This is the **only** module in the repository that opens a database. Every
other caller -- the workbench bridge today, vol-cube assembly later --
speaks :mod:`shiori_pricing_lab.data.vol_surface`'s typed model through
:class:`VolSurfaceStore`, so "production pricing code never reads SQLite"
is a fact about the import graph rather than a convention (a test asserts
it).

**Why SQLite.** Local file, no server, no cloud service, durable across
workbench restarts, and queryable by date/currency/curve/type/expiry/tenor
for the multi-surface cube assembly a later issue builds. The database is
runtime state: it lives outside the working tree's tracked files (see
:data:`DEFAULT_DATABASE_PATH` and ``.gitignore``) and no screenshot bytes
are ever written into it -- only the SHA-256 the capture kept.

**Duplicate policy, stated once.** A surface's id is derived from its
identity fields, and its fingerprint from everything it asserts:

* nothing stored under that id yet -> insert, :attr:`SaveStatus.SAVED`;
* stored with the same fingerprint -> the same save retried,
  :attr:`SaveStatus.ALREADY_SAVED`, nothing written;
* stored with a different fingerprint -> :class:`VolSurfaceConflictError`.
  A conflicting observation of one logical surface is an operator decision,
  never a silent overwrite of a surface someone already confirmed.

**All-or-nothing.** A surface and its points are written in one transaction
and the surface row's primary key is claimed first, so a failure anywhere
-- including partway through the points -- leaves the store exactly as it
was. There is no state in which a surface exists with some of its grid.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfaceIdentity,
    VolSurfacePoint,
    VolSurfaceProvenance,
    VolSurfaceType,
)

#: Bumped whenever the tables below change shape. A database written by a
#: newer schema is refused rather than read with today's column meanings.
SCHEMA_VERSION = 1

#: Where the workbench keeps its store by default: local runtime state under
#: the repository's already-ignored ``data/`` directory (``.gitignore``
#: ignores ``/data/`` and ``*.sqlite3``), never a tracked file. Override with
#: ``SHIORI_VOL_SURFACE_DB`` to point one session at another database.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = _PROJECT_ROOT / "data" / "vol_surfaces.sqlite3"

_SCHEMA_VERSION_STATEMENT = """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    )
"""

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS vol_surface (
        surface_id          TEXT    NOT NULL PRIMARY KEY,
        surface_type        TEXT    NOT NULL,
        business_date       TEXT,
        currency            TEXT,
        curve_config        TEXT,
        side                TEXT,
        vol_type            TEXT,
        source              TEXT,
        unresolved_fields   TEXT    NOT NULL,
        volatility_unit     TEXT,
        capture_id          TEXT    NOT NULL,
        source_reference    TEXT    NOT NULL,
        source_image_sha256 TEXT    NOT NULL,
        source_image_bytes  INTEGER NOT NULL,
        captured_at         TEXT    NOT NULL,
        parser_name         TEXT    NOT NULL,
        parser_version      TEXT    NOT NULL,
        confirmed_by        TEXT    NOT NULL,
        confirmed_at        TEXT    NOT NULL,
        content_fingerprint TEXT    NOT NULL,
        saved_at            TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vol_surface_point (
        surface_id       TEXT    NOT NULL REFERENCES vol_surface(surface_id),
        point_index      INTEGER NOT NULL,
        expiry           TEXT    NOT NULL,
        underlying_tenor TEXT    NOT NULL,
        strike_dimension TEXT    NOT NULL,
        strike_offset    REAL,
        volatility       REAL,
        PRIMARY KEY (surface_id, point_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS vol_surface_point_coordinate
        ON vol_surface_point (surface_id, expiry, underlying_tenor)
    """,
    """
    CREATE INDEX IF NOT EXISTS vol_surface_lookup
        ON vol_surface (surface_type, business_date, currency)
    """,
)

_SURFACE_COLUMNS = (
    "surface_id",
    "surface_type",
    "business_date",
    "currency",
    "curve_config",
    "side",
    "vol_type",
    "source",
    "unresolved_fields",
    "volatility_unit",
    "capture_id",
    "source_reference",
    "source_image_sha256",
    "source_image_bytes",
    "captured_at",
    "parser_name",
    "parser_version",
    "confirmed_by",
    "confirmed_at",
    "content_fingerprint",
    "saved_at",
)


class VolSurfaceStoreError(RuntimeError):
    """Anything that stopped the store from doing what it was asked."""


class VolSurfaceConflictError(VolSurfaceStoreError):
    """A different surface is already stored under this logical identity.

    Never resolved here. Whoever raised it decides: correct the capture,
    or record the new observation under an identity that actually differs.
    """


class VolSurfaceSchemaError(VolSurfaceStoreError):
    """The database on disk was written by a schema this build cannot read."""


class SaveStatus(StrEnum):
    """What a save actually did."""

    SAVED = "SAVED"
    ALREADY_SAVED = "ALREADY_SAVED"


@dataclass(frozen=True)
class SaveOutcome:
    """The result of one save: which surface, and whether it was new."""

    surface_id: str
    status: SaveStatus
    point_count: int

    def to_dict(self) -> dict:
        return {
            "surface_id": self.surface_id,
            "status": self.status.value,
            "point_count": self.point_count,
        }


@dataclass(frozen=True)
class VolSurfaceSummary:
    """One row of the "what is in the store" listing.

    Deliberately not the surface itself: listing is a browse, and pulling
    every point of every surface to draw a list would be the wrong shape as
    soon as more than a handful of surfaces are stored.
    """

    surface_id: str
    surface_type: VolSurfaceType
    business_date: str | None
    currency: str | None
    curve_config: str | None
    side: str | None
    vol_type: str | None
    source: str | None
    point_count: int
    confirmed_by: str
    confirmed_at: str
    saved_at: str

    def to_dict(self) -> dict:
        return {
            "surface_id": self.surface_id,
            "surface_type": self.surface_type.value,
            "business_date": self.business_date,
            "currency": self.currency,
            "curve_config": self.curve_config,
            "side": self.side,
            "vol_type": self.vol_type,
            "source": self.source,
            "point_count": self.point_count,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "saved_at": self.saved_at,
        }


def default_database_path() -> Path:
    """The store path this process should use.

    ``SHIORI_VOL_SURFACE_DB`` wins when it is set to a non-blank value, so a
    session can be pointed at a scratch database without editing anything.
    """

    override = os.environ.get("SHIORI_VOL_SURFACE_DB", "").strip()
    return Path(override) if override else DEFAULT_DATABASE_PATH


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class VolSurfaceStore:
    """A local SQLite database of confirmed volatility surfaces.

    Construction touches no disk: the file and its schema appear on the
    first operation that needs them, so importing the workbench never
    creates a database a trader did not ask for. Each operation opens its
    own connection, which is what makes the store safe to share across the
    workbench's request threads.
    """

    def __init__(self, database_path: Path | str | None = None) -> None:
        self._path = Path(database_path) if database_path is not None else default_database_path()

    @property
    def database_path(self) -> Path:
        return self._path

    # -- connection handling ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            self._ensure_schema(connection)
        except Exception:
            connection.close()
            raise
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Create the tables if they are absent, and refuse a version we cannot read.

        The version is settled *before* any other table is touched, so a
        database written by a later schema is never reshaped on the way to
        being rejected -- a ``CREATE TABLE IF NOT EXISTS`` run against it
        first could put back a table that schema deliberately dropped.
        """

        connection.execute(_SCHEMA_VERSION_STATEMENT)
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        if row is not None:
            stored = int(row["version"])
            if stored != SCHEMA_VERSION:
                raise VolSurfaceSchemaError(
                    f"{self._path} was written with vol-surface schema version {stored}, but "
                    f"this build reads version {SCHEMA_VERSION}. Refusing to read it rather "
                    "than guess what its columns mean."
                )
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        if row is None:
            connection.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    # -- writing ------------------------------------------------------------

    def save_confirmed_surface(self, surface: CanonicalVolSurface) -> SaveOutcome:
        """Persist one confirmed surface, or explain why it cannot be.

        A :class:`CanonicalVolSurface` can only exist for a confirmed
        capture, so there is no "is this confirmed" branch to forget here --
        the type carries the guarantee. What this method decides is the
        duplicate policy in the module docstring.
        """

        if not isinstance(surface, CanonicalVolSurface):
            raise VolSurfaceStoreError("only a CanonicalVolSurface can be saved")
        surface_id = surface.surface_id
        fingerprint = surface.content_fingerprint
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT content_fingerprint FROM vol_surface WHERE surface_id = ?",
                    (surface_id,),
                ).fetchone()
                if existing is not None:
                    if existing["content_fingerprint"] == fingerprint:
                        connection.execute("ROLLBACK")
                        return SaveOutcome(
                            surface_id=surface_id,
                            status=SaveStatus.ALREADY_SAVED,
                            point_count=len(surface.points),
                        )
                    raise VolSurfaceConflictError(
                        f"surface {surface_id} is already stored with different content "
                        f"(stored fingerprint {existing['content_fingerprint']}, this one "
                        f"{fingerprint}). The same logical surface -- same date, currency, "
                        "curve/config, side, vol type and source -- cannot hold two different "
                        "observations, and a stored confirmed surface is never overwritten. "
                        "Resolve it by hand: either this capture is wrong, or it belongs to an "
                        "identity that actually differs."
                    )
                self._insert_surface(connection, surface, fingerprint)
                self._insert_points(connection, surface_id, surface.points)
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    # Whatever stopped the write is the reason worth
                    # reporting, and a rollback that cannot run is not a
                    # durability risk: the transaction is uncommitted, and
                    # closing the connection below discards it either way.
                    pass
                raise
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            raise VolSurfaceStoreError(f"the vol-surface store refused the write: {exc}") from exc
        finally:
            connection.close()
        return SaveOutcome(
            surface_id=surface_id, status=SaveStatus.SAVED, point_count=len(surface.points)
        )

    def _insert_surface(
        self, connection: sqlite3.Connection, surface: CanonicalVolSurface, fingerprint: str
    ) -> None:
        identity = surface.identity
        provenance = surface.provenance
        connection.execute(
            f"INSERT INTO vol_surface ({', '.join(_SURFACE_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _SURFACE_COLUMNS)})",
            (
                surface.surface_id,
                identity.surface_type.value,
                identity.business_date,
                identity.currency,
                identity.curve_config,
                identity.side,
                identity.vol_type,
                identity.source,
                json.dumps(list(identity.unresolved_fields)),
                surface.volatility_unit,
                provenance.capture_id,
                provenance.source_reference,
                provenance.source_image_sha256,
                provenance.source_image_bytes,
                provenance.captured_at,
                provenance.parser_name,
                provenance.parser_version,
                provenance.confirmed_by,
                provenance.confirmed_at,
                fingerprint,
                _utc_now_iso(),
            ),
        )

    def _insert_points(
        self,
        connection: sqlite3.Connection,
        surface_id: str,
        points: Iterable[VolSurfacePoint],
    ) -> None:
        connection.executemany(
            "INSERT INTO vol_surface_point (surface_id, point_index, expiry, underlying_tenor, "
            "strike_dimension, strike_offset, volatility) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    surface_id,
                    index,
                    point.expiry,
                    point.underlying_tenor,
                    point.strike_dimension.value,
                    point.strike_offset,
                    point.volatility,
                )
                for index, point in enumerate(points)
            ],
        )

    # -- reading ------------------------------------------------------------

    def fetch_surface(self, surface_id: str) -> CanonicalVolSurface:
        """Return one stored surface with all of its points, in stored order.

        Raises ``KeyError`` for an id this store does not hold -- a missing
        surface is an error, never an empty one.
        """

        connection = self._connect()
        try:
            row = connection.execute(
                f"SELECT {', '.join(_SURFACE_COLUMNS)} FROM vol_surface WHERE surface_id = ?",
                (surface_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"no vol surface is stored with id {surface_id!r}")
            point_rows = connection.execute(
                "SELECT expiry, underlying_tenor, strike_dimension, strike_offset, volatility "
                "FROM vol_surface_point WHERE surface_id = ? ORDER BY point_index",
                (surface_id,),
            ).fetchall()
        finally:
            connection.close()
        return _surface_from_rows(row, point_rows)

    def fetch_points(self, surface_id: str) -> tuple[VolSurfacePoint, ...]:
        """The normalized points of one surface, for downstream assembly."""

        return self.fetch_surface(surface_id).points

    def list_surfaces(
        self,
        *,
        surface_type: VolSurfaceType | None = None,
        currency: str | None = None,
        business_date: str | None = None,
    ) -> tuple[VolSurfaceSummary, ...]:
        """What the store holds, newest save first, optionally filtered.

        A filter matches only surfaces whose field is *resolved* and equal.
        An unresolved field never matches a value, which is what keeps a
        surface with an unknown currency out of a ``currency="USD"`` answer
        instead of being assumed into it.
        """

        clauses: list[str] = []
        parameters: list[object] = []
        if surface_type is not None:
            clauses.append("s.surface_type = ?")
            parameters.append(VolSurfaceType(surface_type).value)
        if currency is not None:
            clauses.append("s.currency = ?")
            parameters.append(currency)
        if business_date is not None:
            clauses.append("s.business_date = ?")
            parameters.append(business_date)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT s.surface_id, s.surface_type, s.business_date, s.currency, "
                "s.curve_config, s.side, s.vol_type, s.source, s.confirmed_by, s.confirmed_at, "
                "s.saved_at, (SELECT COUNT(*) FROM vol_surface_point p "
                "WHERE p.surface_id = s.surface_id) AS point_count "
                f"FROM vol_surface s{where} ORDER BY s.saved_at DESC, s.surface_id",
                tuple(parameters),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            VolSurfaceSummary(
                surface_id=row["surface_id"],
                surface_type=VolSurfaceType(row["surface_type"]),
                business_date=row["business_date"],
                currency=row["currency"],
                curve_config=row["curve_config"],
                side=row["side"],
                vol_type=row["vol_type"],
                source=row["source"],
                point_count=int(row["point_count"]),
                confirmed_by=row["confirmed_by"],
                confirmed_at=row["confirmed_at"],
                saved_at=row["saved_at"],
            )
            for row in rows
        )


def _surface_from_rows(row: sqlite3.Row, point_rows: Iterable[sqlite3.Row]) -> CanonicalVolSurface:
    """Rebuild the typed surface from its stored rows.

    Every invariant is re-checked on the way out, because the dataclasses do
    the checking: a database edited by hand into a state the model forbids
    fails here rather than becoming a surface nothing validated.
    """

    identity = VolSurfaceIdentity(
        surface_type=VolSurfaceType(row["surface_type"]),
        business_date=row["business_date"],
        currency=row["currency"],
        curve_config=row["curve_config"],
        side=row["side"],
        vol_type=row["vol_type"],
        source=row["source"],
        unresolved_fields=tuple(json.loads(row["unresolved_fields"])),
    )
    provenance = VolSurfaceProvenance(
        capture_id=row["capture_id"],
        source_reference=row["source_reference"],
        source_image_sha256=row["source_image_sha256"],
        source_image_bytes=int(row["source_image_bytes"]),
        captured_at=row["captured_at"],
        parser_name=row["parser_name"],
        parser_version=row["parser_version"],
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
    )
    points = tuple(
        VolSurfacePoint(
            expiry=point["expiry"],
            underlying_tenor=point["underlying_tenor"],
            volatility=point["volatility"],
            strike_dimension=StrikeDimension(point["strike_dimension"]),
            strike_offset=point["strike_offset"],
        )
        for point in point_rows
    )
    return CanonicalVolSurface(
        identity=identity,
        provenance=provenance,
        points=points,
        volatility_unit=row["volatility_unit"],
    )
