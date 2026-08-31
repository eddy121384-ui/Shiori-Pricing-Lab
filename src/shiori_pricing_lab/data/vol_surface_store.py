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
identity fields -- which include ``capture_id``, the snapshot dimension
(Eddy's PR #184 decision #1) -- and its fingerprint from everything it
asserts:

* nothing stored under that id yet -> insert, :attr:`SaveStatus.SAVED`.
  A second capture of the same screen later the same day, with every other
  identity field unchanged, lands here: a *different* ``capture_id`` is a
  *different* id, so it is a new surface, never a conflict with the first;
* stored with the same fingerprint -> the same save retried,
  :attr:`SaveStatus.ALREADY_SAVED`, nothing written. Reachable only when
  ``capture_id`` also matches, since it is part of the id;
* stored with a different fingerprint -> :class:`VolSurfaceConflictError`.
  Only the *same* ``capture_id`` under different content reaches this --
  two different snapshots never do. A conflicting observation of one
  logical capture is an operator decision, never a silent overwrite of a
  surface someone already confirmed.

**All-or-nothing.** A surface and its points are written in one transaction
and the surface row's primary key is claimed first, so a failure anywhere
-- including partway through the points -- leaves the store exactly as it
was. There is no state in which a surface exists with some of its grid.

**Reads are checked against what was confirmed.** Every fetched surface is
rebuilt and re-fingerprinted, and a mismatch against the fingerprint stored
beside it raises :class:`VolSurfaceIntegrityError`. Rows that changed under
the store -- a hand-edited database, a lost point row -- still rebuild into
a perfectly legal :class:`CanonicalVolSurface`, so without this nothing
downstream would notice it was no longer the grid a trader signed off on
(Codex review, PR #184). ``list_surfaces`` deliberately does not check: it
reads no points, and is a browse rather than a source of data.

**A read never writes.** ``list_surfaces`` and ``fetch_surface`` open the
database read-only and take no part in creating or upgrading it: creating
the file, creating the tables, and bringing an older database's additive
columns up to this build's shape all belong to ``save_confirmed_surface``
alone. Reading through the write path meant that merely browsing the Markets
view rewrote a store written before Issue #185, and that the same browse
failed outright against a read-only file (Codex review, PR #195). A store
with no database yet therefore lists nothing rather than bringing one into
existence, and a supported older database is read as it stands -- the read
substituting the additive column's own ``DEFAULT`` (see
:data:`_READ_ONLY_COLUMN_DEFAULTS`) rather than adding the column. The
fail-closed gate is not skipped on the way: the read has its own copy of the
schema-version check, because the write path's lives inside the transaction
it guards and a read that skipped it would answer from a database written by
a newer build using this build's meanings.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Iterable, Sequence
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
    VolSurfaceSourceImage,
    VolSurfaceType,
    VolValueKind,
)

#: Bumped whenever the tables below change shape, or whenever the derived
#: ``surface_id``/``content_fingerprint`` formula changes in a way that makes
#: a value computed by an earlier version incomparable to one this build
#: computes: a database whose version is not exactly this one is refused
#: rather than read with today's meaning.
#:
#: 2 -- ``vol_surface_point.volatility_sign`` and the singleton key on
#: ``schema_version`` (Codex review, PR #184). Version 1 was written by an
#: earlier commit on this branch and has neither column;
#: ``CREATE TABLE IF NOT EXISTS`` would leave that database alone and the
#: next save would fail on a missing column instead of failing closed here,
#: so the version has to move with the shape.
#:
#: 3 -- ``capture_id`` joined the identity fields that
#: :meth:`~shiori_pricing_lab.data.vol_surface.VolSurfaceIdentity.surface_id`
#: hashes, so a surface a snapshot's own screen already stored under the
#: previous formula (Eddy's PR #184 decision #1) no longer decides
#: "already saved" the same way. No table column changed, but every row's
#: ``content_fingerprint`` was computed under the old formula and would
#: mismatch a version-3 rebuild -- not because the data drifted, but because
#: the formula did -- which would raise :class:`VolSurfaceIntegrityError`
#: for perfectly good rows. Refusing the whole database with a clear
#: schema-version message is the honest failure, not a false "your data was
#: tampered with".
#:
#: There is no migration for any of the above -- the store is local runtime
#: state rebuilt by re-confirming a capture, and this branch has never been
#: merged.
#:
#: **Issue #185 deliberately does not bump this.** It adds a
#: ``vol_surface_point.value_kind`` column and a ``vol_surface_source_image``
#: table, both purely additive and both applied to an existing version-3
#: database by :meth:`VolSurfaceStore._ensure_schema` -- the column with a
#: default that reproduces exactly what a version-3 row already meant, and
#: the table empty for every capture that had one image, which is every
#: capture written before this build. Neither the ``surface_id`` nor the
#: ``content_fingerprint`` formula changed for such a surface: the two new
#: model fields serialise only when they say something the older shape could
#: not (see ``vol_surface``). So a version-3 row still rebuilds into exactly
#: the surface it stored, which is the property this gate exists to protect,
#: and refusing those databases would destroy ATM surfaces Issue #185
#: requires to stay readable. What version 3 no longer promises is the other
#: direction: a *multi-image* surface written by this build is not readable
#: by a build that predates it, which fails closed on the fingerprint rather
#: than being misread.
SCHEMA_VERSION = 3

#: Where the workbench keeps its store by default: local runtime state under
#: the repository's already-ignored ``data/`` directory (``.gitignore``
#: ignores ``/data/`` and ``*.sqlite3``), never a tracked file. Override with
#: ``SHIORI_VOL_SURFACE_DB`` to point one session at another database.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = _PROJECT_ROOT / "data" / "vol_surfaces.sqlite3"

#: One row, enforced by the table itself. Two processes opening a brand-new
#: database can both find it empty and both insert; without the singleton key
#: that left two version rows, and a later ``fetchone()`` then checked an
#: arbitrary one -- defeating the fail-closed gate exactly when two builds
#: disagree, which is the one case it exists for (Codex review, PR #184).
#: :meth:`VolSurfaceStore._ensure_schema` also runs the whole check inside a
#: write transaction, so the second process reads the first's row rather than
#: racing it; the constraint is what makes the invariant structural.
_SCHEMA_VERSION_STATEMENT = """
    CREATE TABLE IF NOT EXISTS schema_version (
        id      INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
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
        volatility_sign  INTEGER NOT NULL DEFAULT 1 CHECK (volatility_sign IN (-1, 1)),
        value_kind       TEXT    NOT NULL DEFAULT 'ABSOLUTE_VOL',
        PRIMARY KEY (surface_id, point_index)
    )
    """,
    # One row per image of a capture that had *more than one* (Issue #185).
    # Absent for a single-image capture, whose one image the ``vol_surface``
    # row already names in full -- which is what makes this table additive to
    # a version-3 database rather than a reshaping of it.
    """
    CREATE TABLE IF NOT EXISTS vol_surface_source_image (
        surface_id          TEXT    NOT NULL REFERENCES vol_surface(surface_id),
        image_index         INTEGER NOT NULL,
        source_reference    TEXT    NOT NULL,
        source_image_sha256 TEXT    NOT NULL,
        source_image_bytes  INTEGER NOT NULL,
        PRIMARY KEY (surface_id, image_index)
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


class VolSurfaceIntegrityError(VolSurfaceStoreError):
    """A stored surface no longer matches the fingerprint saved with it.

    The database is syntactically fine and the rows rebuild into a valid
    surface -- they are simply not the surface that was confirmed. A dropped
    point row leaves a smaller grid that is still a legal
    :class:`CanonicalVolSurface`, so nothing else would notice (Codex review,
    PR #184). Reading fails closed instead.
    """


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

    ``capture_id`` is carried through so two surfaces that share every other
    identity field -- two captures of the same screen on the same day -- are
    legible as two different snapshots in the listing itself, not only
    distinguishable by an opaque ``surface_id`` (Eddy's PR #184 decision #1).
    """

    surface_id: str
    surface_type: VolSurfaceType
    capture_id: str
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
            "capture_id": self.capture_id,
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


def _sign_of(value: float | None) -> int:
    """``-1`` for a negative value (negative zero included), ``1`` otherwise.

    SQLite's REAL column is the one place an IEEE double does not survive a
    round trip: ``-0.0`` comes back as ``0.0``. Every other finite double
    stores and reloads bit-exact, so this single extra column is the whole
    of what REAL cannot carry.

    It matters because the capture slice deliberately treats ``-0.00`` and
    ``0.00`` as different readings -- a trader shown one must not confirm the
    other (PR #182, Codex round 8). Losing the sign here made a reloaded
    surface's fingerprint differ from the one stored beside it, so a surface
    conflicted with itself on the next save (Codex review, PR #184).
    """

    if value is None:
        # The column is NOT NULL and an unresolved point has no sign to
        # carry; ``_volatility_from_row`` returns ``None`` for it regardless
        # of what is stored here.
        return 1
    return -1 if math.copysign(1.0, value) < 0 else 1


def _volatility_from_row(row: sqlite3.Row) -> float | None:
    """The stored volatility with its sign restored. ``None`` stays ``None``."""

    value = row["volatility"]
    if value is None:
        return None
    return math.copysign(float(value), row["volatility_sign"])


#: Columns added to an existing table after its version was settled, as
#: ``(table, column, definition)``. Only a column whose default reproduces
#: exactly what a row already meant may be listed here: that is what makes
#: adding it to an existing database a no-op for every surface stored in it
#: (see :data:`SCHEMA_VERSION`). Anything that changes what an existing row
#: means needs a version bump instead.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("vol_surface_point", "value_kind", "TEXT NOT NULL DEFAULT 'ABSOLUTE_VOL'"),
)

#: What a *read* substitutes for an additive column a supported older
#: database does not have yet, as ``(table, column) -> value``.
#:
#: A read never migrates (see :meth:`VolSurfaceStore._read_only_connection`),
#: so it has to stand in for the column itself. The value here must be
#: exactly the ``DEFAULT`` its entry in :data:`_ADDITIVE_COLUMNS` carries --
#: that default is what makes the column additive at all, since it reproduces
#: what a row written before the column existed already meant. Anything else
#: would be this layer inventing a value for data a trader confirmed.
#:
#: Every entry of :data:`_ADDITIVE_COLUMNS` must appear here, so a column
#: added later cannot silently leave the read path guessing; a test asserts
#: the two agree.
_READ_ONLY_COLUMN_DEFAULTS: dict[tuple[str, str], str] = {
    ("vol_surface_point", "value_kind"): VolValueKind.ABSOLUTE_VOL.value,
}

#: The tables this build cannot read a surface without. A database whose
#: version row says it is readable but which lacks one of these is malformed,
#: and saying so is better than letting a bare "no such table" out.
_REQUIRED_TABLES: tuple[str, ...] = ("vol_surface", "vol_surface_point")


#: The columns a point is rebuilt from, in the order ``_surface_from_rows``
#: reads them. Named here rather than inline so the read can be built from
#: the list rather than hard-coding which of them a supported older database
#: might be missing.
_POINT_COLUMNS: tuple[str, ...] = (
    "expiry",
    "underlying_tenor",
    "strike_dimension",
    "strike_offset",
    "volatility",
    "volatility_sign",
    "value_kind",
)


def _has_any_table(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
        ).fetchone()
        is not None
    )


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _select_with_stand_ins(
    connection: sqlite3.Connection, table: str, columns: Sequence[str], path: Path
) -> tuple[str, tuple[object, ...]]:
    """The select list for ``columns``, standing in for what ``table`` lacks.

    Built from :data:`_READ_ONLY_COLUMN_DEFAULTS` rather than from a
    hard-coded column name, so a column added to :data:`_ADDITIVE_COLUMNS`
    later is stood in for by the same machinery instead of making every read
    of a database that predates it fail on a missing column (Codex review,
    PR #195). A column that is absent and has *no* declared stand-in is a
    database this build cannot read, and says so rather than letting a bare
    ``no such column`` out.

    The column names come from this module's own constants, never from a
    caller, and every substituted value is bound rather than interpolated.
    """

    present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    fragments: list[str] = []
    parameters: list[object] = []
    for column in columns:
        if column in present:
            fragments.append(column)
            continue
        if (table, column) not in _READ_ONLY_COLUMN_DEFAULTS:
            raise VolSurfaceSchemaError(
                f"{path} has no {table}.{column} column and this build declares no value to "
                f"read in its place. Refusing to rebuild a surface from a shape it does not "
                "have rather than guessing what the column would have said."
            )
        fragments.append(f"? AS {column}")
        parameters.append(_READ_ONLY_COLUMN_DEFAULTS[(table, column)])
    return ", ".join(fragments), tuple(parameters)


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    """Bring an older database's tables up to this build's column set.

    ``CREATE TABLE IF NOT EXISTS`` leaves an existing table exactly as it
    is, so a column added to the statement above never reaches a database
    that already has the table -- the next insert would fail on a missing
    column instead (the version-1 case in :data:`SCHEMA_VERSION`). Runs
    inside :meth:`VolSurfaceStore._ensure_schema`'s write transaction.
    """

    for table, column, definition in _ADDITIVE_COLUMNS:
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if existing and column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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

    def _read_only_connection(self) -> sqlite3.Connection:
        """Open the store for reading, creating and changing nothing.

        A read never migrates. :meth:`_connect` is the *write* path's door:
        it makes the directory, creates every table, and brings an older
        database's additive columns up to this build's shape -- all correct
        for a save, and all wrong for a browse. Reading through it meant that
        merely listing the store on a machine whose database predates Issue
        #185 rewrote that database, and that the same listing failed outright
        against a genuinely read-only file (Codex review, PR #195).

        ``mode=ro`` is SQLite's own refusal rather than a convention this
        module has to keep, and ``query_only`` says the same thing a second
        way on the connection itself: neither the schema catch-up nor any
        other statement can write through this handle even by mistake.
        """

        try:
            connection = sqlite3.connect(
                f"{self._path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None
            )
        except sqlite3.Error as exc:
            raise VolSurfaceStoreError(f"cannot open {self._path} for reading: {exc}") from exc
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = 1")
        except Exception:
            connection.close()
            raise
        return connection

    def _open_for_reading(self) -> sqlite3.Connection | None:
        """A verified read-only connection, or ``None`` if the store is empty.

        ``None`` means the store provably holds no surface: either there is
        no database file, or there is one with no tables in it at all. The
        second case is not only an empty file -- it is exactly what a
        concurrent reader sees while the very first save is still inside
        ``_ensure_schema``'s transaction, since the file is created on
        connect and nothing is committed until that transaction ends (Codex
        review, PR #195). Reading through the write path used to serialise
        behind that transaction; a read-only connection sees the database as
        it was, which is empty, and answering "nothing is stored" is the
        truth at that instant rather than a refusal a trader would have to
        interpret.

        A file that *does* have tables is a different thing: it either is a
        vol-surface store this build can read, or it is refused. Nothing
        with a table in it is ever mistaken for an empty store.
        """

        if not self._path.exists():
            return None
        connection = self._read_only_connection()
        try:
            if not _has_any_table(connection):
                connection.close()
                return None
            self._require_readable_schema(connection)
        except Exception:
            connection.close()
            raise
        return connection

    def _require_readable_schema(self, connection: sqlite3.Connection) -> None:
        """Refuse a database this build cannot read -- without repairing it.

        The same gate :meth:`_ensure_schema` applies on the way in, minus
        every statement that would change something. It has to exist
        separately because that gate lives inside the write transaction it
        guards: a read path that simply skipped it would answer from a
        database written by a *newer* build using this build's meanings,
        which is a worse failure than the migration this method exists to
        avoid.

        An unreadable database is one whose version is not exactly this
        build's, whose version is missing entirely, or which lacks a table a
        surface cannot be rebuilt without. A *supported older* database --
        version 3 without Issue #185's additive column or image table -- is
        readable, and the read stands in for what it lacks rather than adding
        it (see :data:`_READ_ONLY_COLUMN_DEFAULTS`).
        """

        # The version is settled first, exactly as on the write path, and for
        # the same reason: a database that records a version this build does
        # not read is refused *as that version*, whatever shape its other
        # tables are in. Checking the tables first would report an old
        # database as merely malformed and hide the one fact that explains
        # it.
        if not _table_exists(connection, "schema_version"):
            raise VolSurfaceSchemaError(
                f"{self._path} records no vol-surface schema version at all. Refusing to read "
                "a database that is not a vol-surface store rather than creating the tables "
                "it lacks."
            )
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            raise VolSurfaceSchemaError(
                f"{self._path} records no vol-surface schema version. Refusing to read it "
                f"rather than assume it is version {SCHEMA_VERSION}."
            )
        stored = int(row["version"])
        if stored != SCHEMA_VERSION:
            raise self._wrong_schema_version_error(stored)
        missing = [name for name in _REQUIRED_TABLES if not _table_exists(connection, name)]
        if missing:
            raise VolSurfaceSchemaError(
                f"{self._path} claims vol-surface schema version {stored} but is missing the "
                f"table(s) {', '.join(missing)}. Refusing to read a database whose shape "
                "contradicts its own version rather than creating what it lacks."
            )

    def _wrong_schema_version_error(self, stored: int) -> VolSurfaceSchemaError:
        """The one refusal both the read and the write gate give.

        Shared so the two can never drift into disagreeing about which
        databases this build will touch.
        """

        return VolSurfaceSchemaError(
            f"{self._path} was written with vol-surface schema version {stored}, "
            f"but this build reads version {SCHEMA_VERSION}. Refusing to read it "
            "rather than guess what its columns mean."
        )

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        """Create the tables if they are absent, and refuse a version we cannot read.

        The version is settled *before* any other table is touched, so a
        database written by a later schema is never reshaped on the way to
        being rejected -- a ``CREATE TABLE IF NOT EXISTS`` run against it
        first could put back a table that schema deliberately dropped.

        The whole check runs in one ``BEGIN IMMEDIATE`` transaction, which
        takes SQLite's write lock for its duration. Read-then-insert in
        autocommit let two processes opening a brand-new database both see an
        empty version table and both insert, so the gate that exists to catch
        two builds disagreeing was defeated by exactly that case (Codex
        review, PR #184).
        """

        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(_SCHEMA_VERSION_STATEMENT)
            row = connection.execute("SELECT version FROM schema_version").fetchone()
            if row is not None:
                stored = int(row["version"])
                if stored != SCHEMA_VERSION:
                    raise self._wrong_schema_version_error(stored)
            for statement in _SCHEMA_STATEMENTS:
                connection.execute(statement)
            _add_missing_columns(connection)
            if row is None:
                connection.execute(
                    "INSERT INTO schema_version (id, version) VALUES (1, ?)", (SCHEMA_VERSION,)
                )
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        connection.execute("COMMIT")

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
                    f"SELECT {', '.join(_SURFACE_COLUMNS)} FROM vol_surface "
                    "WHERE surface_id = ?",
                    (surface_id,),
                ).fetchone()
                if existing is not None:
                    # Verified before either verdict is reached, so a surface
                    # whose rows have drifted is reported as drifted -- never
                    # as "already saved, unchanged", and never as a conflict
                    # with an incoming capture that may be perfectly right.
                    self._verified_surface(connection, existing)
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
                self._insert_source_images(
                    connection, surface_id, surface.provenance.source_images
                )
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
            "strike_dimension, strike_offset, volatility, volatility_sign, value_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    surface_id,
                    index,
                    point.expiry,
                    point.underlying_tenor,
                    point.strike_dimension.value,
                    point.strike_offset,
                    point.volatility,
                    _sign_of(point.volatility),
                    point.value_kind.value,
                )
                for index, point in enumerate(points)
            ],
        )

    def _insert_source_images(
        self,
        connection: sqlite3.Connection,
        surface_id: str,
        images: Sequence[VolSurfaceSourceImage],
    ) -> None:
        """Record the images of a capture that had more than one.

        A single-image capture writes nothing here: the ``vol_surface`` row
        already names its one image in full, and a row that merely repeated
        it would be a second place for the same fact to drift from.
        """

        if len(images) < 2:
            return
        connection.executemany(
            "INSERT INTO vol_surface_source_image (surface_id, image_index, "
            "source_reference, source_image_sha256, source_image_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (surface_id, index, image.source_reference, image.sha256, image.size_bytes)
                for index, image in enumerate(images)
            ],
        )

    # -- reading ------------------------------------------------------------

    def fetch_surface(self, surface_id: str) -> CanonicalVolSurface:
        """Return one stored surface with all of its points, in stored order.

        Raises ``KeyError`` for an id this store does not hold -- a missing
        surface is an error, never an empty one. A store with no database
        file yet holds no surface, so it raises the same ``KeyError`` rather
        than bringing a database into existence to answer a question about
        one that was never saved.
        """

        connection = self._open_for_reading()
        if connection is None:
            raise KeyError(f"no vol surface is stored with id {surface_id!r}")
        try:
            row = connection.execute(
                f"SELECT {', '.join(_SURFACE_COLUMNS)} FROM vol_surface WHERE surface_id = ?",
                (surface_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"no vol surface is stored with id {surface_id!r}")
            return self._verified_surface(connection, row)
        finally:
            connection.close()

    def _verified_surface(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> CanonicalVolSurface:
        """Rebuild the surface ``row`` heads, and refuse it if it has drifted.

        Shared by :meth:`fetch_surface` and by the already-stored branch of
        :meth:`save_confirmed_surface`, which must reach the same verdict:
        answering ``ALREADY_SAVED`` from the surface row's fingerprint alone
        told a trader their grid was stored unchanged while a fetch of that
        same surface immediately raised (Codex review round 2, PR #184).
        """

        surface_id = row["surface_id"]
        # A supported older database (version 3 written before Issue #185)
        # has neither the ``value_kind`` column nor the image table. Since a
        # read may not add them, it selects each missing column's own
        # ``DEFAULT`` in its place -- the value that makes the column
        # additive, and the one such a row has always meant -- and reads no
        # images, which is exactly what a single-image capture stored. Both
        # substitutions rebuild the surface the older build saved,
        # fingerprint included; neither invents anything, and the check below
        # still has to agree.
        point_select, point_parameters = _select_with_stand_ins(
            connection, "vol_surface_point", _POINT_COLUMNS, self._path
        )
        point_rows = connection.execute(
            f"SELECT {point_select} FROM vol_surface_point "
            "WHERE surface_id = ? ORDER BY point_index",
            (*point_parameters, surface_id),
        ).fetchall()
        image_rows = (
            connection.execute(
                "SELECT source_reference, source_image_sha256, source_image_bytes "
                "FROM vol_surface_source_image WHERE surface_id = ? ORDER BY image_index",
                (surface_id,),
            ).fetchall()
            if _table_exists(connection, "vol_surface_source_image")
            else ()
        )
        surface = _surface_from_rows(row, point_rows, image_rows)
        stored_fingerprint = row["content_fingerprint"]
        if surface.content_fingerprint != stored_fingerprint:
            raise VolSurfaceIntegrityError(
                f"surface {surface_id} does not match the fingerprint stored with it "
                f"(stored {stored_fingerprint}, rebuilt {surface.content_fingerprint}). The rows "
                f"in {self._path} have changed since the surface was confirmed -- refusing to "
                "hand back a surface that is not the one a trader signed off on."
            )
        return surface

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

        A store with no database file yet holds nothing, and says so without
        creating one: browsing is not what brings the store into existence,
        the first confirmed save is.
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
        connection = self._open_for_reading()
        if connection is None:
            return ()
        try:
            rows = connection.execute(
                "SELECT s.surface_id, s.surface_type, s.capture_id, s.business_date, "
                "s.currency, s.curve_config, s.side, s.vol_type, s.source, s.confirmed_by, "
                "s.confirmed_at, s.saved_at, (SELECT COUNT(*) FROM vol_surface_point p "
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
                capture_id=row["capture_id"],
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


def _surface_from_rows(
    row: sqlite3.Row,
    point_rows: Iterable[sqlite3.Row],
    image_rows: Iterable[sqlite3.Row] = (),
) -> CanonicalVolSurface:
    """Rebuild the typed surface from its stored rows.

    Every invariant is re-checked on the way out, because the dataclasses do
    the checking: a database edited by hand into a state the model forbids
    fails here rather than becoming a surface nothing validated.
    """

    identity = VolSurfaceIdentity(
        surface_type=VolSurfaceType(row["surface_type"]),
        capture_id=row["capture_id"],
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
        # Empty for a single-image capture -- including every surface stored
        # before Issue #185 -- which the model then fills in from the three
        # fields above, reproducing exactly the provenance that was saved.
        source_images=tuple(
            VolSurfaceSourceImage(
                source_reference=image["source_reference"],
                sha256=image["source_image_sha256"],
                size_bytes=int(image["source_image_bytes"]),
            )
            for image in image_rows
        ),
    )
    points = tuple(
        VolSurfacePoint(
            expiry=point["expiry"],
            underlying_tenor=point["underlying_tenor"],
            volatility=_volatility_from_row(point),
            strike_dimension=StrikeDimension(point["strike_dimension"]),
            strike_offset=point["strike_offset"],
            value_kind=VolValueKind(point["value_kind"]),
        )
        for point in point_rows
    )
    return CanonicalVolSurface(
        identity=identity,
        provenance=provenance,
        points=points,
        volatility_unit=row["volatility_unit"],
    )
