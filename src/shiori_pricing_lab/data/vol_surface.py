"""The canonical volatility-surface model Shiori stores confirmed captures as
(Issue #183).

This module is the *typed boundary* the rest of the repository sees. It is
deliberately vendor-neutral: it knows nothing about Bloomberg, VCUB, OCR, or
screenshots, and nothing about SQLite. ``vcub_vol_surface_adapter`` turns one
confirmed VCUB capture into these shapes; ``vol_surface_store`` is the only
module that writes them to disk. Downstream code -- including any future
vol-cube assembly -- consumes :class:`CanonicalVolSurface`, never a database
row and never a capture.

**Methodology boundary (Issue #183 non-goals).** Nothing here interpolates a
surface, builds a cube, converts a normal/yield vol into a price vol,
computes a ``Kproxy``, or reaches Black-76. A point's
:attr:`VolSurfacePoint.volatility` is the number a trader confirmed against
the screen, and :attr:`CanonicalVolSurface.volatility_unit` is only ever a
unit that was *stated*, never one inferred from the magnitude of the values.

**Nothing is invented.** A metadata field that was ``Unresolved`` at capture
time stays ``None`` here and is named in
:attr:`VolSurfaceIdentity.unresolved_fields` -- the same either/or invariant
``VCUBSourceMetadata`` enforces, kept structural so an unresolved identity
field can never be quietly filled in on the way into the store.

**Extensible to OTM/SABR without a second storage model.** Every point
carries a strike dimension and an optional strike offset. Today the only
dimension is :attr:`StrikeDimension.ATM`, whose offset must be ``None``; a
later OTM/SABR capture adds a dimension and populates the offset, and the
row shape does not change.

**A stored surface is one capture, not the only surface allowed for a day**
(Eddy's PR #184 decision #1). :attr:`VolSurfaceIdentity.capture_id` is a
required snapshot dimension alongside the other identity fields, not an
opaque provenance detail: two captures sharing every other identity field
are two different surfaces because they are two different observations, and
neither is a conflicting replacement of the other. Only the *same* capture
retried collides -- identical content is idempotent, different content under
the same ``capture_id`` fails closed. ``capture_id`` is a snapshot marker,
never a substitute for a market/quote timestamp: nothing here claims to know
when a screen's data was quoted, only when it was captured.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class VolSurfaceType(StrEnum):
    """Which screen's surface this is.

    One member today. A later OTM/SABR or Caps/Floors capture adds its own
    member and reuses everything else in this module.
    """

    ATM_SWAPTION = "ATM_SWAPTION"


class StrikeDimension(StrEnum):
    """How a point's strike is expressed.

    ``ATM`` is the whole of Issue #183. It is spelled out rather than left
    implicit so an ATM row and a future OTM row are distinguishable in the
    store without a schema change.
    """

    ATM = "ATM"


#: The fields that identify one logical surface, alongside the required
#: ``capture_id`` snapshot dimension (Eddy's PR #184 decision #1). Two
#: observations sharing all of these *and* the same ``capture_id`` are the
#: same surface and must agree; two that differ in any of them -- including
#: ``capture_id`` alone -- are different surfaces and never collide.
#: ``capture_id`` is deliberately not listed here: it is never unresolved,
#: so it has no place in the may-be-unresolved bookkeeping these names drive
#: (see :class:`VolSurfaceIdentity`).
IDENTITY_FIELDS: tuple[str, ...] = (
    "business_date",
    "currency",
    "curve_config",
    "side",
    "vol_type",
    "source",
)


def _require_non_blank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _require_iso_timestamp(value: object, field_name: str) -> str:
    _require_non_blank(value, field_name)
    assert isinstance(value, str)
    if not _ISO_UTC_RE.match(value):
        raise ValueError(
            f"{field_name} must be an ISO-8601 timestamp with an explicit offset, got {value!r}"
        )
    return value


def _digest(payload: object) -> str:
    """A stable 32-hex-character digest of ``payload``.

    ``sort_keys`` plus ``separators`` makes the encoding depend on the data
    alone, so the same surface hashes identically in a later process --
    which is what lets a re-save be recognised as a retry rather than a
    conflict after a restart.
    """

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


@dataclass(frozen=True)
class VolSurfacePoint:
    """One normalized observation: a coordinate plus the vol read there.

    ``volatility`` is ``None`` when that intersection was left unresolved by
    the capture. The point is still stored, because dropping it would make
    "this cell could not be read" indistinguishable from "this cell is not
    part of the surface" -- the coordinate grid must survive a round trip
    exactly as the trader reviewed it. ``None`` never means zero.
    """

    expiry: str
    underlying_tenor: str
    volatility: float | None
    strike_dimension: StrikeDimension = StrikeDimension.ATM
    strike_offset: float | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.expiry, "expiry")
        _require_non_blank(self.underlying_tenor, "underlying_tenor")
        if not isinstance(self.strike_dimension, StrikeDimension):
            raise ValueError("strike_dimension must be a StrikeDimension")
        if self.strike_dimension is StrikeDimension.ATM and self.strike_offset is not None:
            raise ValueError(
                "an ATM point carries no strike offset; "
                f"got strike_offset={self.strike_offset!r}"
            )
        # Normalised to ``float``, not merely validated. A caller may hand in
        # an ``int`` -- ``_require_finite`` accepts one -- and keeping it an
        # ``int`` made the point serialise as JSON ``80`` while the same point
        # reloaded from SQLite's REAL column serialised as ``80.0``. The two
        # fingerprints then differed, so a surface saved and fetched back
        # conflicted with *itself* on the next save (Codex review, PR #184).
        for name in ("volatility", "strike_offset"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_finite(value, name))

    @property
    def coordinate(self) -> tuple[str, str, str, float | None]:
        """What makes this point unique inside one surface."""

        return (
            self.expiry,
            self.underlying_tenor,
            self.strike_dimension.value,
            self.strike_offset,
        )

    def to_dict(self) -> dict:
        return {
            "expiry": self.expiry,
            "underlying_tenor": self.underlying_tenor,
            "strike_dimension": self.strike_dimension.value,
            "strike_offset": self.strike_offset,
            "volatility": self.volatility,
        }


@dataclass(frozen=True)
class VolSurfaceIdentity:
    """What distinguishes one stored surface from another.

    Every field but :attr:`surface_type` and :attr:`capture_id` may be
    unresolved, and an unresolved field is ``None`` *and* named in
    :attr:`unresolved_fields` -- there is no third state, exactly as on the
    capture it came from. An incomplete identity is therefore visible to
    every later operation rather than papered over: Issue #183 requires such
    an operation to fail closed rather than infer the missing metadata.

    ``business_date`` is the date **as the screen spelled it** (e.g.
    ``"08/18/26"``). It is deliberately not parsed into a calendar date
    here: choosing a day/month order for a vendor screen is an interpretation
    this layer is not entitled to make.

    ``capture_id`` is the snapshot dimension (Eddy's PR #184 decision #1): a
    stored surface represents *one capture*, not the only surface a business
    date may ever hold. It is required and never unresolved -- a capture
    always has one, assigned mechanically from the image read and the
    instant it was read, never from a claimed market quote time -- and it
    participates in :attr:`surface_id` exactly like the other identity
    fields. A second capture of the same screen later the same day is
    therefore a *new* surface, never a conflicting replacement of the first;
    only a retry of the *same* capture can collide, and Issue #183's
    duplicate policy (identical content idempotent, different content fails
    closed) applies to that retry alone.
    """

    surface_type: VolSurfaceType
    capture_id: str
    business_date: str | None = None
    currency: str | None = None
    curve_config: str | None = None
    side: str | None = None
    vol_type: str | None = None
    source: str | None = None
    unresolved_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.surface_type, VolSurfaceType):
            raise ValueError("surface_type must be a VolSurfaceType")
        _require_non_blank(self.capture_id, "capture_id")
        if not isinstance(self.unresolved_fields, tuple):
            raise ValueError("unresolved_fields must be a tuple")
        unknown = [name for name in self.unresolved_fields if name not in IDENTITY_FIELDS]
        if unknown:
            raise ValueError(f"unresolved_fields names unknown identity fields: {unknown}")
        if len(set(self.unresolved_fields)) != len(self.unresolved_fields):
            raise ValueError(f"unresolved_fields has duplicates: {self.unresolved_fields}")
        for name in IDENTITY_FIELDS:
            value = getattr(self, name)
            listed = name in self.unresolved_fields
            if value is None and not listed:
                raise ValueError(f"{name} is unresolved but is not listed in unresolved_fields")
            if value is not None:
                if listed:
                    raise ValueError(f"{name} is listed unresolved but carries a value")
                _require_non_blank(value, name)

    @property
    def surface_id(self) -> str:
        """A stable id derived from the identity fields themselves.

        Derived rather than allocated so the same logical surface gets the
        same id in every process and after every restart -- which is what
        makes "already saved" and "conflicting replacement" decidable at
        all. An unresolved field hashes as ``None``: it participates in the
        identity as *unresolved*, and is never stood in for.

        ``capture_id`` participates too (Eddy's PR #184 decision #1), which
        is what makes two captures sharing every other field two different
        surfaces rather than one surface in conflict with itself.
        """

        return _digest(
            {
                "surface_type": self.surface_type.value,
                "capture_id": self.capture_id,
                **{name: getattr(self, name) for name in IDENTITY_FIELDS},
            }
        )

    def to_dict(self) -> dict:
        payload: dict = {"surface_type": self.surface_type.value, "capture_id": self.capture_id}
        payload.update({name: getattr(self, name) for name in IDENTITY_FIELDS})
        payload["unresolved_fields"] = list(self.unresolved_fields)
        return payload


@dataclass(frozen=True)
class VolSurfaceProvenance:
    """Where a stored surface came from, and who accepted it.

    Enough to answer Issue #183's audit questions -- which screen, when,
    which parser, whose confirmation, which exact image -- without the image
    itself: the screenshot stays operator-local evidence and only its
    SHA-256 is kept, as in Issue #181.
    """

    capture_id: str
    source_reference: str
    source_image_sha256: str
    source_image_bytes: int
    captured_at: str
    parser_name: str
    parser_version: str
    confirmed_by: str
    confirmed_at: str

    def __post_init__(self) -> None:
        _require_non_blank(self.capture_id, "capture_id")
        _require_non_blank(self.source_reference, "source_reference")
        _require_non_blank(self.source_image_sha256, "source_image_sha256")
        if not _SHA256_HEX_RE.match(self.source_image_sha256):
            raise ValueError(
                "source_image_sha256 must be 64 lower-case hex characters, "
                f"got {self.source_image_sha256!r}"
            )
        if isinstance(self.source_image_bytes, bool) or not isinstance(
            self.source_image_bytes, int
        ):
            raise ValueError(
                f"source_image_bytes must be an int, got {self.source_image_bytes!r}"
            )
        if self.source_image_bytes <= 0:
            raise ValueError(
                f"source_image_bytes must be positive, got {self.source_image_bytes!r}"
            )
        _require_iso_timestamp(self.captured_at, "captured_at")
        _require_non_blank(self.parser_name, "parser_name")
        _require_non_blank(self.parser_version, "parser_version")
        _require_non_blank(self.confirmed_by, "confirmed_by")
        _require_iso_timestamp(self.confirmed_at, "confirmed_at")

    def to_dict(self) -> dict:
        return {
            "capture_id": self.capture_id,
            "source_reference": self.source_reference,
            "source_image_sha256": self.source_image_sha256,
            "source_image_bytes": self.source_image_bytes,
            "captured_at": self.captured_at,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
        }


@dataclass(frozen=True)
class CanonicalVolSurface:
    """One confirmed surface: identity, provenance, and its normalized points.

    Constructing one requires ``confirmed_by``/``confirmed_at`` on the
    provenance, so a surface that nobody confirmed cannot be built at all --
    "only a CONFIRMED capture reaches the store" is a fact about this type
    rather than a check the store has to remember to run.

    ``volatility_unit`` is ``None`` unless the source *stated* a unit. A
    VCUB ATM screen does not, so a capture-sourced surface leaves it
    unresolved rather than asserting bp, %, or anything else from the look
    of the numbers -- inferring it would be exactly the silent unit
    coercion Issue #181's methodology boundary forbids.

    ``identity.capture_id`` and ``provenance.capture_id`` must agree
    (Codex review, PR #184). The store keeps only one ``capture_id`` column
    -- the two are the same fact, the snapshot this surface came from, seen
    from the identity side and the provenance side -- and rebuilds both
    fields from it on load. A surface built with the two disagreeing would
    save under a ``surface_id`` derived from one value and reload with the
    other, mismatching the fingerprint it had just stored and becoming
    unreadable the moment it was saved.
    """

    identity: VolSurfaceIdentity
    provenance: VolSurfaceProvenance
    points: tuple[VolSurfacePoint, ...]
    volatility_unit: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, VolSurfaceIdentity):
            raise ValueError("identity must be a VolSurfaceIdentity")
        if not isinstance(self.provenance, VolSurfaceProvenance):
            raise ValueError("provenance must be a VolSurfaceProvenance")
        if self.identity.capture_id != self.provenance.capture_id:
            raise ValueError(
                "identity.capture_id and provenance.capture_id must agree -- they are the "
                f"same capture, got {self.identity.capture_id!r} and "
                f"{self.provenance.capture_id!r}"
            )
        if not isinstance(self.points, tuple) or not self.points:
            raise ValueError("points must be a non-empty tuple of VolSurfacePoint")
        if any(not isinstance(point, VolSurfacePoint) for point in self.points):
            raise ValueError("points must be a non-empty tuple of VolSurfacePoint")
        coordinates = [point.coordinate for point in self.points]
        if len(set(coordinates)) != len(coordinates):
            # Sorted by ``repr`` because a coordinate's strike offset may be
            # ``None``, which has no ordering against a float.
            duplicated = sorted(
                {
                    coordinate
                    for coordinate in coordinates
                    if coordinates.count(coordinate) > 1
                },
                key=repr,
            )
            raise ValueError(f"points repeat a coordinate: {duplicated}")
        if self.volatility_unit is not None:
            _require_non_blank(self.volatility_unit, "volatility_unit")

    @property
    def surface_id(self) -> str:
        return self.identity.surface_id

    @property
    def content_fingerprint(self) -> str:
        """A digest of everything this surface asserts.

        The whole record, provenance included: two saves agreeing on it are
        the same save retried, and any difference at all -- a changed vol, a
        different screenshot, a different confirmer -- is a conflict the
        store refuses rather than resolves. Duplicate policy lives on this
        one value.
        """

        return _digest(self.to_dict())

    def resolved_points(self) -> tuple[VolSurfacePoint, ...]:
        """Only the points that actually carry a vol.

        For a consumer that wants data rather than coordinates. The
        unresolved ones stay in :attr:`points`; they are never dropped from
        the record itself.
        """

        return tuple(point for point in self.points if point.volatility is not None)

    def to_dict(self) -> dict:
        return {
            "surface_id": self.surface_id,
            "identity": self.identity.to_dict(),
            "provenance": self.provenance.to_dict(),
            "volatility_unit": self.volatility_unit,
            "points": [point.to_dict() for point in self.points],
        }


#: The export column order, stable by contract: Issue #183 requires CSV and
#: JSON exports to keep the same column names across releases so an audit or
#: research script written against one export still reads the next.
EXPORT_COLUMNS: tuple[str, ...] = (
    "surface_id",
    "surface_type",
    "business_date",
    "currency",
    "curve_config",
    "side",
    "vol_type",
    "source",
    "expiry",
    "underlying_tenor",
    "strike_dimension",
    "strike_offset",
    "volatility",
    "volatility_unit",
    "capture_id",
    "source_reference",
    "source_image_sha256",
    "captured_at",
    "parser_name",
    "parser_version",
    "confirmed_by",
    "confirmed_at",
)


def export_rows(surface: CanonicalVolSurface) -> tuple[dict, ...]:
    """One flat row per point, keyed by :data:`EXPORT_COLUMNS`.

    The flat shape both exports share. An unresolved value is ``None``
    here; how that renders is each format's business.
    """

    identity = surface.identity
    provenance = surface.provenance
    shared = {
        "surface_id": surface.surface_id,
        "surface_type": identity.surface_type.value,
        "business_date": identity.business_date,
        "currency": identity.currency,
        "curve_config": identity.curve_config,
        "side": identity.side,
        "vol_type": identity.vol_type,
        "source": identity.source,
        "volatility_unit": surface.volatility_unit,
        "capture_id": provenance.capture_id,
        "source_reference": provenance.source_reference,
        "source_image_sha256": provenance.source_image_sha256,
        "captured_at": provenance.captured_at,
        "parser_name": provenance.parser_name,
        "parser_version": provenance.parser_version,
        "confirmed_by": provenance.confirmed_by,
        "confirmed_at": provenance.confirmed_at,
    }
    return tuple(
        {
            **shared,
            "expiry": point.expiry,
            "underlying_tenor": point.underlying_tenor,
            "strike_dimension": point.strike_dimension.value,
            "strike_offset": point.strike_offset,
            "volatility": point.volatility,
        }
        for point in surface.points
    )


def export_surface_as_csv(surface: CanonicalVolSurface) -> str:
    """``surface`` as CSV, one row per point, header first.

    An unresolved field is written as an empty cell -- CSV has no null, and
    a placeholder word would be indistinguishable from a value the screen
    actually showed.
    """

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(EXPORT_COLUMNS), lineterminator="\n", extrasaction="raise"
    )
    writer.writeheader()
    for row in export_rows(surface):
        writer.writerow({name: "" if row[name] is None else row[name] for name in EXPORT_COLUMNS})
    return buffer.getvalue()


def export_surface_as_json(surface: CanonicalVolSurface) -> str:
    """``surface`` as JSON: the nested record plus the same flat rows.

    Both because they answer different questions -- ``surface`` is what the
    store round-trips, ``rows`` is what a research script joins on -- and
    the flat rows carry exactly :data:`EXPORT_COLUMNS`, matching the CSV
    column for column.
    """

    return json.dumps(
        {
            "surface": surface.to_dict(),
            "columns": list(EXPORT_COLUMNS),
            "rows": [dict(row) for row in export_rows(surface)],
        },
        indent=2,
        sort_keys=False,
    )
