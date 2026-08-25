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

**OTM/SABR without a second storage model (Issue #185).** Every point
carries a strike dimension and an optional strike offset:
:attr:`StrikeDimension.ATM` with no offset is the ATM screen, and
:attr:`StrikeDimension.YIELD_OFFSET_BP` with an offset in basis points is
the OTM Swaptions / SABR screen's third coordinate. That screen also states
that its non-ATM numbers are *spreads* to its ATM vol rather than vols, so a
point says which it holds through :attr:`VolSurfacePoint.value_kind`. A
spread is never stored as though it were a volatility, and nothing here adds
one to the other.

**An extension that leaves every stored surface exactly as it was.** A
surface's fingerprint is a digest of what it asserts, so a new field that
serialised unconditionally would change the fingerprint of every ATM surface
already in the store and make it unreadable. Both fields added for Issue
#185 therefore serialise only when they say something the Issue #183 shape
could not: :attr:`VolSurfacePoint.value_kind` appears only when it is not
``ABSOLUTE_VOL``, and :attr:`VolSurfaceProvenance.source_images` only when a
capture had more than one image. A surface stored before either existed
hashes today exactly as it hashed then.

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

    One member per observed screen. A later Caps/Floors capture adds its own
    and reuses everything else in this module.
    """

    ATM_SWAPTION = "ATM_SWAPTION"
    OTM_SWAPTION_SABR = "OTM_SWAPTION_SABR"


class StrikeDimension(StrEnum):
    """How a point's strike is expressed.

    ``ATM`` was the whole of Issue #183. It is spelled out rather than left
    implicit so an ATM row and an OTM row are distinguishable in the store
    without a schema change.

    ``YIELD_OFFSET_BP`` is Issue #185's third coordinate, and it is named
    after what the screen itself states: the VCUB OTM Swaptions / SABR tab
    heads its columns ``-200bps ... 25bps ... 200bps`` around an ``ATM``
    column, so the offset is a yield offset from ATM **in basis points**.
    The unit is transcribed, never inferred from the magnitude of the
    numbers, and no other strike convention is added here for screens this
    repository has not observed.
    """

    ATM = "ATM"
    YIELD_OFFSET_BP = "YIELD_OFFSET_BP"


class VolValueKind(StrEnum):
    """What the number at a point *is*.

    Issue #183's points were all absolute vols, so nothing had to say so.
    Issue #185's screen states ``Normal Vol Skew`` with display ``Spread``:
    its ATM column holds an absolute vol and every other column holds a
    spread to that vol -- which is why several of them are negative. Both
    kinds are numbers, and treating one as the other would be exactly the
    silent reinterpretation this model exists to prevent, so each point says
    which it is.

    Nothing in the capture or storage slice adds a spread to an ATM vol. A
    consumer that wants an absolute OTM vol must combine them deliberately,
    and that belongs to the later vol-cube issue.
    """

    ABSOLUTE_VOL = "ABSOLUTE_VOL"
    SPREAD_TO_ATM = "SPREAD_TO_ATM"


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

    ``value_kind`` says what the number is. It defaults to
    :attr:`VolValueKind.ABSOLUTE_VOL`, which is what every Issue #183 point
    was; an OTM/SABR skew point off the ATM column is a
    :attr:`VolValueKind.SPREAD_TO_ATM`, and the field is what stops the two
    being read as the same quantity. ``volatility`` is still the number the
    trader confirmed against the screen -- this field says which quantity
    that number is, and no arithmetic anywhere in this slice relates them.
    """

    expiry: str
    underlying_tenor: str
    volatility: float | None
    strike_dimension: StrikeDimension = StrikeDimension.ATM
    strike_offset: float | None = None
    value_kind: VolValueKind = VolValueKind.ABSOLUTE_VOL

    def __post_init__(self) -> None:
        _require_non_blank(self.expiry, "expiry")
        _require_non_blank(self.underlying_tenor, "underlying_tenor")
        if not isinstance(self.strike_dimension, StrikeDimension):
            raise ValueError("strike_dimension must be a StrikeDimension")
        if not isinstance(self.value_kind, VolValueKind):
            raise ValueError("value_kind must be a VolValueKind")
        if self.strike_dimension is StrikeDimension.ATM and self.strike_offset is not None:
            raise ValueError(
                "an ATM point carries no strike offset; "
                f"got strike_offset={self.strike_offset!r}"
            )
        if self.strike_dimension is StrikeDimension.YIELD_OFFSET_BP:
            if self.strike_offset is None:
                raise ValueError(
                    "a YIELD_OFFSET_BP point is defined by its offset from ATM and must "
                    "carry one; a point with no offset is an ATM point"
                )
            if self.strike_offset == 0:
                raise ValueError(
                    "a 0bp offset cannot be told apart from the ATM point at the same "
                    "coordinate; the ATM point carries no offset at all"
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
        payload = {
            "expiry": self.expiry,
            "underlying_tenor": self.underlying_tenor,
            "strike_dimension": self.strike_dimension.value,
            "strike_offset": self.strike_offset,
            "volatility": self.volatility,
        }
        # Emitted only when it says something the Issue #183 shape could not.
        # This dict is what a surface's fingerprint is computed from, so a
        # key added unconditionally would change the fingerprint of every ATM
        # surface already stored under PR #184 and make it fail its own
        # integrity check on the next read (Issue #185: existing ATM surfaces
        # must remain readable and unchanged). An absolute vol is what a
        # point without this key has always been.
        if self.value_kind is not VolValueKind.ABSOLUTE_VOL:
            payload["value_kind"] = self.value_kind.value
        return payload


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
class VolSurfaceSourceImage:
    """One image a capture was read from.

    The bytes themselves are never here: the screenshot stays operator-local
    evidence and only its reference, its SHA-256, and its size are kept, as
    in Issue #181.
    """

    source_reference: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        _require_non_blank(self.source_reference, "source_reference")
        _require_non_blank(self.sha256, "sha256")
        if not _SHA256_HEX_RE.match(self.sha256):
            raise ValueError(
                f"sha256 must be 64 lower-case hex characters, got {self.sha256!r}"
            )
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise ValueError(f"size_bytes must be an int, got {self.size_bytes!r}")
        if self.size_bytes <= 0:
            raise ValueError(f"size_bytes must be positive, got {self.size_bytes!r}")

    def to_dict(self) -> dict:
        return {
            "source_reference": self.source_reference,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class VolSurfaceProvenance:
    """Where a stored surface came from, and who accepted it.

    Enough to answer Issue #183's audit questions -- which screen, when,
    which parser, whose confirmation, which exact images -- without the
    images themselves.

    **A capture may have been read from several screenshots** (Issue #185:
    the VCUB OTM/SABR table is longer than one viewport, so an operator
    captures it as two to four overlapping images in one session).
    :attr:`source_images` is the complete ordered set, and it is the field to
    read: ``source_reference``/``source_image_sha256``/``source_image_bytes``
    describe its **first** image, which for a single-image capture is its
    only one. Nothing here ever stands one hash in for a set -- an audit can
    name every file that produced the surface -- and the two views are kept
    consistent by construction rather than by convention.
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
    source_images: tuple[VolSurfaceSourceImage, ...] = ()

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
        object.__setattr__(self, "source_images", self._validated_source_images())

    def _validated_source_images(self) -> tuple[VolSurfaceSourceImage, ...]:
        """The complete image set, defaulted from the single-image fields.

        Left empty by a caller that has one image, so every Issue #183
        construction keeps working unchanged and still ends up with a
        populated set. When a caller does supply one, its first entry must
        *be* the image the scalar fields name: the two are one fact seen
        twice, and letting them disagree would store a surface whose
        provenance contradicts itself.
        """

        first = VolSurfaceSourceImage(
            source_reference=self.source_reference,
            sha256=self.source_image_sha256,
            size_bytes=self.source_image_bytes,
        )
        if not isinstance(self.source_images, tuple):
            raise ValueError("source_images must be a tuple of VolSurfaceSourceImage")
        if not self.source_images:
            return (first,)
        if any(
            not isinstance(image, VolSurfaceSourceImage) for image in self.source_images
        ):
            raise ValueError("source_images must be a tuple of VolSurfaceSourceImage")
        if self.source_images[0] != first:
            raise ValueError(
                "source_images[0] must be the image source_reference/source_image_sha256/"
                f"source_image_bytes name, got {self.source_images[0]!r}"
            )
        digests = [image.sha256 for image in self.source_images]
        if len(set(digests)) != len(digests):
            raise ValueError(
                "one capture cannot list the same image twice: "
                f"{sorted({digest for digest in digests if digests.count(digest) > 1})}"
            )
        return self.source_images

    def to_dict(self) -> dict:
        payload = {
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
        # Emitted only for a capture that really had more than one image --
        # for a single-image capture the three fields above already say
        # everything this would repeat. The reason it is conditional rather
        # than always present is the same as for a point's ``value_kind``:
        # this dict is what a surface's fingerprint is computed from, and an
        # unconditional key would change the fingerprint of every surface
        # stored under PR #184 (Issue #185: existing ATM surfaces must remain
        # readable and unchanged).
        if len(self.source_images) > 1:
            payload["source_images"] = [image.to_dict() for image in self.source_images]
        return payload


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
#: research script written against one export still reads the next. Issue
#: #185's two columns are therefore *appended* rather than slotted in beside
#: the fields they belong with: every column a script already reads keeps its
#: name and its position.
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
    "value_kind",
    "source_image_count",
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
        # So a flat row can never imply a single screenshot produced a
        # capture that several did. Which images those were is in the JSON
        # export's nested ``surface.provenance.source_images``; a CSV cell
        # is the wrong place for a list of hashes.
        "source_image_count": len(provenance.source_images),
    }
    return tuple(
        {
            **shared,
            "expiry": point.expiry,
            "underlying_tenor": point.underlying_tenor,
            "strike_dimension": point.strike_dimension.value,
            "strike_offset": point.strike_offset,
            "volatility": point.volatility,
            # Always written, unlike in ``VolSurfacePoint.to_dict``: an
            # export is read by a person or a script rather than hashed, and
            # a column that appeared only sometimes would be worse than
            # useless to both.
            "value_kind": point.value_kind.value,
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
