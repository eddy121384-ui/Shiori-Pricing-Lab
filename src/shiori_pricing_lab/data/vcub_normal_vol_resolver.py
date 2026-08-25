"""Resolve one confirmed canonical VCUB snapshot into an in-grid normal
swaption volatility ``sigma_vcub`` (Issue #188).

The layer between the canonical vol-surface store (Issues #183/#185) and
anything that wants a *number* out of it. It reads a
:class:`~shiori_pricing_lab.data.vol_surface.CanonicalVolSurface` that a
trader confirmed, reconstructs each captured node's absolute normal vol from
the screen's own ``ATM absolute + spread-to-ATM`` semantics, resolves the
smile at the requested additive moneyness, and combines the four bracketing
expiry/tenor corners by bilinear interpolation.

**It stops at ``sigma_vcub``.** Nothing here scales by ``lambda_vcub``,
aligns ``DCF_VCUB`` against ``DCF_BondVol``, derives a normal bond *yield*
vol, multiplies by a duration, or reaches Black-76 -- those remain the RED
methodology gate Annex A.8.5 describes, and this module imports nothing from
:mod:`shiori_pricing_lab.pricing` or
:mod:`shiori_pricing_lab.products` so that boundary is structural rather
than a convention to remember.

**Every convention this resolver cannot prove is an input, not a guess.**

* *The volatility unit* is read from the surface's stated
  ``volatility_unit`` and from nowhere else. A VCUB capture states no unit,
  so such a surface fails closed here rather than having ``bp`` inferred
  from the magnitude of its numbers -- exactly the silent unit coercion
  Annex A.8.1 forbids. A stated ``bp`` normalizes explicitly at ``1bp =
  1e-4``.
* *The expiry/tenor axis coordinates* come from a
  :class:`VCUBGridCoordinates` map the caller supplies and tests prove. The
  screen's labels (``"18Mo"``, ``"10Yr"``) are text; turning a calendar date
  or a label into a VCUB year fraction is the unresolved ``DCF_VCUB``
  question, and this module neither answers it nor works around it. The
  repository's ``tenor_label_nominal_days`` is an ordering check on an OCR
  read and is deliberately not reused as a methodology axis.
* *The smile model* is stated by the caller as part of the query contract.
  It is never inferred from the surface type, the column count, or the shape
  of the numbers. :attr:`SmileModel.PWL` is implemented; a query that names
  :attr:`SmileModel.SABR` -- or names no model at all -- is refused with the
  contract that is missing, because the canonical snapshot carries no
  calibrated ``alpha/rho/nu`` and this repository holds no pinned copy of
  the Bloomberg calibration objective to reproduce them from.
* *The volatility space* is read from the surface's stated ``vol_type``.
  ``sigma_vcub`` is a normal vol, and a surface stating a lognormal or
  Black type -- or stating none -- is refused rather than relabelled. The
  canonical model is vendor-neutral, so ``surface_type`` names a screen and
  is not on its own evidence of which space the numbers live in.
* *The corner forwards* ``F_ij`` are optional caller inputs. The smile is
  resolved in additive-moneyness space, so no forward is needed to *compute*
  ``sigma_vcub``; a forward is needed only to report the absolute corner
  strike ``K_ij = F_ij + mu*``, and a corner strike is left unreported
  rather than invented when the caller has no forward to state.

**An unreadable cell is not a gap to interpolate over.** A captured strike
coordinate the capture could not read stays in the resolver's view of the
node as *unresolved*, and a query landing on or reaching across one blocks.
Answering it from the resolved columns either side would present a number at
a coordinate the snapshot explicitly failed to read, with no fallback flag
to say so.

**Out-of-range stays fail-closed** (``VCUB_EXTRAPOLATION_MODE =
FAIL_CLOSED``). Bloomberg's own VCUB flat-extrapolates expiry and tenor
beyond its data; Shiori does not, and this module has no nearest-node, flat,
linear-extension, or smile-extension path to fall into. A query outside the
confirmed coverage on any of the three axes raises.

**What the resolver claims, and what it does not.** Between captured strike
nodes it interpolates piecewise-linearly in additive moneyness on normal
vols, and between expiry/tenor nodes it interpolates bilinearly. That is a
versioned Shiori resolver contract (:data:`RESOLVER_VERSION`), reproducible
and auditable from its inputs. It is not a claim that Bloomberg fills the
gaps between its own published nodes the same way: Annex A.8.3's rule that
no parity may be claimed before live parity testing is unchanged by this
module.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfaceType,
    VolValueKind,
)

#: What this resolver is, in audit output. The name is the module's
#: behaviour; the version is the contract that behaviour is pinned to, and
#: it is the value Annex A.12's ``VCUB_RESOLVER_VERSION`` switch carries
#: when this resolver is the one that produced a number.
RESOLVER_NAME = "VCUB_IN_GRID_NORMAL_VOL_RESOLVER"
RESOLVER_VERSION = "IN_GRID_BILINEAR_V1"

#: Unchanged by this module and reported on every result so a reader never
#: has to infer it: nothing here extrapolates.
EXTRAPOLATION_MODE = "FAIL_CLOSED"

#: The one unit conversion this module performs, stated rather than derived
#: (Annex A.8.1, SPEC §3.3).
BASIS_POINT_IN_DECIMAL = 1e-4

#: The unit of every normalized value this module returns, and the unit
#: :attr:`VCUBNormalVolResolution.volatility_unit` names. Reported rather
#: than left implicit so a serialized result never has a value beside a unit
#: that belongs to a different number (Codex review, PR #189).
NORMALIZED_VOLATILITY_UNIT = "decimal"

#: The volatility units a surface may *state*, and what one of its numbers
#: is worth as an absolute decimal rate vol. A surface stating anything else
#: -- or nothing at all -- fails closed: the magnitude of the numbers is
#: never evidence of their unit.
STATED_UNIT_SCALES: Mapping[str, float] = {
    "bp": BASIS_POINT_IN_DECIMAL,
    "bps": BASIS_POINT_IN_DECIMAL,
    "decimal": 1.0,
}


#: The surfaces this resolver is entitled to read. Both of today's members
#: are swaption screens, which is what ``sigma_vcub`` *is*; the model
#: anticipates a later Caps/Floors member, and a resolver that labels its
#: output a swaption normal vol must not read one (Codex review, PR #189).
RESOLVABLE_SURFACE_TYPES: frozenset[VolSurfaceType] = frozenset(
    {VolSurfaceType.ATM_SWAPTION, VolSurfaceType.OTM_SWAPTION_SABR}
)

#: What a surface must state for its numbers to *be* normal volatilities.
#: The VCUB screens draw their vol type from one closed vocabulary --
#: ``Normal`` / ``Black`` / ``Lognormal`` / ``Shifted Lognormal`` / ``SABR``,
#: optionally with a parenthesised curve suffix -- so ``Normal Vol (OIS)``
#: and ``Normal Vol Skew`` both declare normal space and ``Lognormal Vol``
#: declares that it is not. Matched on the stated text rather than assumed
#: from the surface type: :class:`CanonicalVolSurface` is vendor-neutral and
#: an ``OTM_SWAPTION_SABR`` surface is a *screen*, not a promise about which
#: volatility space its numbers live in (Codex review, PR #189).
_NORMAL_VOL_TYPE_RE = re.compile(r"^normal\s+vol\b")


class SmileModel(StrEnum):
    """Which smile methodology a query asks for.

    Stated by the caller, never inferred. :attr:`PWL` is what this resolver
    version implements; :attr:`SABR` exists so a caller can *ask* for it and
    get a specific refusal naming the missing contract, rather than silently
    receiving a piecewise-linear answer under a SABR label.
    """

    PWL = "PWL"
    SABR = "SABR"


#: The versioned behaviour behind each supported model. A model absent from
#: this map is not implemented at this resolver version.
SMILE_MODEL_VERSIONS: Mapping[SmileModel, str] = {
    SmileModel.PWL: "PWL_ADDITIVE_MONEYNESS_NORMAL_V1",
}


class VCUBResolverError(ValueError):
    """Base for every blocking condition this resolver reports.

    All of them are fail-closed: the resolver raises rather than returning a
    degraded number, so no caller can mistake a fallback for a resolution.
    """


class VolUnitContractError(VCUBResolverError):
    """The surface states no volatility unit, or states one nothing pins."""


class SmileContractError(VCUBResolverError):
    """The query names no smile model, or one this version cannot reproduce."""


class VolSpaceContractError(VCUBResolverError):
    """The surface does not state that its numbers are normal volatilities."""


class NegativeVolatilityError(VCUBResolverError):
    """A reconstruction produced a negative absolute normal volatility."""


class GridCoordinateContractError(VCUBResolverError):
    """The caller's coordinate/forward contract does not cover the surface."""


class SurfaceIdentityError(VCUBResolverError):
    """The surface is not the snapshot the query says it is pricing against."""


class SurfaceCoverageError(VCUBResolverError):
    """The query falls outside the confirmed surface, or a corner is missing."""


class SpreadReconstructionError(VCUBResolverError):
    """A spread-to-ATM point has no ATM absolute vol to be reconstructed from."""


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _require_non_blank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


@dataclass(frozen=True)
class VCUBGridCoordinates:
    """The numeric expiry/tenor axis the caller resolves against.

    One entry per label the surface carries, supplied explicitly. This is
    the seam Issue #188 keeps open on purpose: the resolver interpolates on
    *numbers*, and which number a screen label such as ``"18Mo"`` is worth
    on Bloomberg's own VCUB axis is a day-count question this repository has
    not pinned. A caller that cannot state the mapping cannot resolve, which
    is the intended outcome -- not a mapping this module makes up.

    Coverage is required to be complete rather than partial. A label left
    unmapped would silently drop a node that may lie *between* the query's
    brackets, which would change the answer without changing anything a
    reader can see.
    """

    expiry: Mapping[str, float]
    tenor: Mapping[str, float]

    def __post_init__(self) -> None:
        for axis in ("expiry", "tenor"):
            mapping = getattr(self, axis)
            if not isinstance(mapping, Mapping) or not mapping:
                raise GridCoordinateContractError(
                    f"{axis} must be a non-empty mapping of label to numeric coordinate"
                )
            coordinates: dict[str, float] = {}
            for label, value in mapping.items():
                _require_non_blank(label, f"{axis} label")
                coordinate = _require_finite(value, f"{axis}[{label!r}]")
                if coordinate <= 0:
                    raise GridCoordinateContractError(
                        f"{axis}[{label!r}] must be a positive coordinate, got {coordinate!r}"
                    )
                coordinates[label] = coordinate
            duplicates = sorted(
                {
                    coordinate
                    for coordinate in coordinates.values()
                    if list(coordinates.values()).count(coordinate) > 1
                }
            )
            if duplicates:
                # Two labels on one coordinate makes bracketing ambiguous:
                # which of them is "the" node at that point has no answer.
                raise GridCoordinateContractError(
                    f"{axis} maps more than one label onto the same coordinate: {duplicates}"
                )
            object.__setattr__(self, axis, coordinates)

    def coordinate_for(self, axis: str, label: str) -> float:
        mapping: Mapping[str, float] = getattr(self, axis)
        try:
            return mapping[label]
        except KeyError:
            raise GridCoordinateContractError(
                f"the surface carries the {axis} label {label!r}, which this coordinate map "
                f"does not name; a resolver cannot place a node it cannot locate on the axis"
            ) from None


@dataclass(frozen=True)
class VCUBVolQuery:
    """What is being asked of one confirmed snapshot.

    ``moneyness_bp`` is ``mu* = K* - F*`` in basis points -- the same
    additive strike coordinate the captured
    :attr:`~shiori_pricing_lab.data.vol_surface.StrikeDimension.YIELD_OFFSET_BP`
    columns are expressed in, and the quantity Annex A.8.2 drives from
    ``KY - FY``. It is a stated unit, not one read off the magnitude.

    ``expected_surface_id`` is the caller's declaration of *which* confirmed
    snapshot it means. It is optional, and when given it is checked: a
    resolver call cannot then drift onto another capture's or another
    business date's surface while the rest of the caller's inputs still
    belong to the first.

    ``corner_forwards`` maps ``(expiry_label, tenor_label)`` to that node's
    forward/ATM rate as an absolute decimal. It is optional because the
    smile is resolved in moneyness space and no forward is needed to compute
    ``sigma_vcub``; supplying it is what lets the result report the absolute
    corner strike ``K_ij = F_ij + mu*``. When it is supplied it must cover
    every bracketing corner: a half-stated forward contract would report
    some corner strikes and silently omit others.
    """

    expiry_coordinate: float
    tenor_coordinate: float
    moneyness_bp: float
    smile_model: SmileModel | None = None
    expected_surface_id: str | None = None
    corner_forwards: Mapping[tuple[str, str], float] | None = None

    def __post_init__(self) -> None:
        for name in ("expiry_coordinate", "tenor_coordinate", "moneyness_bp"):
            object.__setattr__(self, name, _require_finite(getattr(self, name), name))
        for name in ("expiry_coordinate", "tenor_coordinate"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)!r}")
        if self.smile_model is not None and not isinstance(self.smile_model, SmileModel):
            raise SmileContractError("smile_model must be a SmileModel")
        if self.expected_surface_id is not None:
            _require_non_blank(self.expected_surface_id, "expected_surface_id")
        if self.corner_forwards is not None:
            if not isinstance(self.corner_forwards, Mapping):
                raise GridCoordinateContractError(
                    "corner_forwards must map (expiry_label, tenor_label) to a forward rate"
                )
            forwards: dict[tuple[str, str], float] = {}
            for key, value in self.corner_forwards.items():
                if (
                    not isinstance(key, tuple)
                    or len(key) != 2
                    or any(not isinstance(part, str) for part in key)
                ):
                    raise GridCoordinateContractError(
                        "corner_forwards keys must be (expiry_label, tenor_label) pairs, "
                        f"got {key!r}"
                    )
                forwards[key] = _require_finite(value, f"corner_forwards[{key!r}]")
            object.__setattr__(self, "corner_forwards", forwards)

    @property
    def moneyness_decimal(self) -> float:
        """``mu*`` as an absolute decimal rate offset."""

        return self.moneyness_bp * BASIS_POINT_IN_DECIMAL


@dataclass(frozen=True)
class VCUBSmileNode:
    """One captured strike node of one corner, reconstructed to an absolute vol.

    Both the raw number the trader confirmed and the reconstruction are
    kept: ``source_value_raw`` is what the screen showed at this column --
    an absolute vol in the ATM column, a spread everywhere else, which
    :attr:`value_kind` states -- and ``volatility_raw`` is the absolute
    normal vol Annex A.8.3 reconstructs from it, still in the surface's own
    stated unit. ``volatility`` is that value normalized to an absolute
    decimal rate vol.
    """

    moneyness_bp: float
    value_kind: VolValueKind
    source_value_raw: float
    volatility_raw: float
    volatility: float

    def to_dict(self) -> dict:
        return {
            "moneyness_bp": self.moneyness_bp,
            "value_kind": self.value_kind.value,
            "source_value_raw": self.source_value_raw,
            "volatility_raw": self.volatility_raw,
            "volatility": self.volatility,
        }


@dataclass(frozen=True)
class VCUBResolvedCorner:
    """One of the four bracketing expiry/tenor nodes, resolved at ``mu*``.

    ``weight`` is this corner's share of the bilinear combination, and the
    four of them sum to one. ``atm_volatility`` is this corner's ATM column,
    which every spread at the corner was reconstructed from; it is ``None``
    only for a surface that carries no ATM column at all, and such a surface
    cannot hold a spread (that is a blocking error). ``forward`` and
    ``strike`` are ``None`` unless
    the caller stated this corner's forward: the resolver reports the strike
    it can derive from a stated forward and reports nothing where it cannot,
    rather than presenting an assumed forward as a fact.
    """

    expiry_label: str
    tenor_label: str
    expiry_coordinate: float
    tenor_coordinate: float
    weight: float
    atm_volatility_raw: float | None
    atm_volatility: float | None
    smile_nodes: tuple[VCUBSmileNode, ...]
    bracketing_moneyness_bp: tuple[float, float]
    forward: float | None
    strike: float | None
    volatility_raw: float
    volatility: float

    def to_dict(self) -> dict:
        return {
            "expiry_label": self.expiry_label,
            "tenor_label": self.tenor_label,
            "expiry_coordinate": self.expiry_coordinate,
            "tenor_coordinate": self.tenor_coordinate,
            "weight": self.weight,
            "atm_volatility_raw": self.atm_volatility_raw,
            "atm_volatility": self.atm_volatility,
            "smile_nodes": [node.to_dict() for node in self.smile_nodes],
            "bracketing_moneyness_bp": list(self.bracketing_moneyness_bp),
            "forward": self.forward,
            "strike": self.strike,
            "volatility_raw": self.volatility_raw,
            "volatility": self.volatility,
        }


@dataclass(frozen=True)
class VCUBNormalVolResolution:
    """A resolved ``sigma_vcub`` and everything it was resolved from.

    The record an audit, an OVME parity run, or a debugging session reads:
    which snapshot, which resolver and smile version, what was asked, which
    four nodes answered it, what each of them reconstructed from, how they
    were weighted, and what the final number is in both the surface's stated
    unit and absolute decimal.

    **Each value sits beside its own unit.** :attr:`volatility` is the
    normalized absolute decimal rate vol and :attr:`volatility_unit` is
    always :data:`NORMALIZED_VOLATILITY_UNIT`; :attr:`volatility_raw` is the
    same quantity in the surface's stated unit, which
    :attr:`source_volatility_unit` names. The two pairs are never crossed:
    reading ``volatility`` against the *source* unit would invite a consumer
    to scale an already-normalized 0.008 by another 1e-4 (Codex review, PR
    #189). Every other raw/normalized pair in this record -- on a corner and
    on a smile node -- follows the same convention.

    :attr:`corners` is ordered ``(expiry low, tenor low)``, ``(expiry low,
    tenor high)``, ``(expiry high, tenor low)``, ``(expiry high, tenor
    high)`` against :attr:`expiry_bracket_labels` and
    :attr:`tenor_bracket_labels`. When the query lands exactly on a grid
    coordinate the bracket is that node twice, so the four entries name the
    same node and only the first carries a non-zero weight.

    :attr:`fallback_used` is always ``False`` on a returned result -- there
    is no path through this module that produces a number any other way --
    and is reported rather than left implicit so a downstream consumer can
    assert on it instead of trusting it. :attr:`blocking` is its companion
    and is always ``False`` for the same reason: a blocking condition raises
    a :class:`VCUBResolverError` rather than returning a record that says it
    is blocked.
    """

    surface_id: str
    capture_id: str
    surface_type: str
    business_date: str | None
    currency: str | None
    curve_config: str | None
    side: str | None
    vol_type: str | None
    source: str | None
    unresolved_identity_fields: tuple[str, ...]
    captured_at: str
    confirmed_by: str
    confirmed_at: str
    parser_name: str
    parser_version: str
    source_volatility_unit: str
    unit_scale_to_decimal: float
    resolver_name: str
    resolver_version: str
    extrapolation_mode: str
    smile_model: SmileModel
    smile_model_version: str
    query_expiry_coordinate: float
    query_tenor_coordinate: float
    query_moneyness_bp: float
    query_moneyness_decimal: float
    expiry_bracket_labels: tuple[str, str]
    expiry_bracket_coordinates: tuple[float, float]
    expiry_weight: float
    tenor_bracket_labels: tuple[str, str]
    tenor_bracket_coordinates: tuple[float, float]
    tenor_weight: float
    corners: tuple[VCUBResolvedCorner, ...]
    volatility: float
    volatility_raw: float
    volatility_unit: str
    fallback_used: bool = False
    blocking: bool = False

    def to_dict(self) -> dict:
        return {
            "surface_id": self.surface_id,
            "capture_id": self.capture_id,
            "surface_type": self.surface_type,
            "business_date": self.business_date,
            "currency": self.currency,
            "curve_config": self.curve_config,
            "side": self.side,
            "vol_type": self.vol_type,
            "source": self.source,
            "unresolved_identity_fields": list(self.unresolved_identity_fields),
            "captured_at": self.captured_at,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "source_volatility_unit": self.source_volatility_unit,
            "unit_scale_to_decimal": self.unit_scale_to_decimal,
            "resolver_name": self.resolver_name,
            "resolver_version": self.resolver_version,
            "extrapolation_mode": self.extrapolation_mode,
            "smile_model": self.smile_model.value,
            "smile_model_version": self.smile_model_version,
            "query_expiry_coordinate": self.query_expiry_coordinate,
            "query_tenor_coordinate": self.query_tenor_coordinate,
            "query_moneyness_bp": self.query_moneyness_bp,
            "query_moneyness_decimal": self.query_moneyness_decimal,
            "expiry_bracket_labels": list(self.expiry_bracket_labels),
            "expiry_bracket_coordinates": list(self.expiry_bracket_coordinates),
            "expiry_weight": self.expiry_weight,
            "tenor_bracket_labels": list(self.tenor_bracket_labels),
            "tenor_bracket_coordinates": list(self.tenor_bracket_coordinates),
            "tenor_weight": self.tenor_weight,
            "corners": [corner.to_dict() for corner in self.corners],
            "volatility": self.volatility,
            "volatility_raw": self.volatility_raw,
            "volatility_unit": self.volatility_unit,
            "fallback_used": self.fallback_used,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class _Node:
    """The captured column set at one ``(expiry, tenor)`` coordinate.

    :attr:`unresolved_offsets` are the coordinates the surface *holds* but
    the capture could not read. They are kept rather than dropped because
    dropping them would let a query at -- or across -- an unreadable column
    be answered by interpolating its resolved neighbours, and returned as a
    resolution with no fallback flag (Codex review, PR #189). The stored
    surface says that column exists and says its value is unknown; both
    halves have to survive into the resolver.
    """

    expiry_label: str
    tenor_label: str
    atm_raw: float | None
    #: ``moneyness_bp -> (raw value, kind)`` for every resolved non-ATM column.
    offsets: Mapping[float, tuple[float, VolValueKind]]
    unresolved_offsets: tuple[float, ...] = ()


def _require_normal_vol_space(surface: CanonicalVolSurface) -> str:
    """The surface's stated vol type, once it is known to be a normal one.

    Read before any number is: this resolver labels what it returns a normal
    swaption volatility, and a surface that states a lognormal or Black vol
    type -- or states none at all -- would have that label applied to
    numbers that are not that quantity. The canonical model is deliberately
    vendor-neutral and its ``surface_type`` names a *screen*, so the vol
    space has to come from what the surface states about its own values.
    """

    identity = surface.identity
    if identity.surface_type not in RESOLVABLE_SURFACE_TYPES:
        raise VolSpaceContractError(
            f"this resolver returns a swaption normal vol and reads only "
            f"{sorted(surface_type.value for surface_type in RESOLVABLE_SURFACE_TYPES)}; "
            f"this surface is {identity.surface_type.value}"
        )
    stated = identity.vol_type
    if stated is None:
        raise VolSpaceContractError(
            "this surface leaves vol_type unresolved, so nothing states that its numbers "
            "are normal volatilities; sigma_vcub is a normal swaption vol and this "
            "resolver will not assert that of values whose space is unknown"
        )
    if _NORMAL_VOL_TYPE_RE.match(" ".join(stated.split()).casefold()) is None:
        raise VolSpaceContractError(
            f"this surface states vol_type={stated!r}, which does not declare normal "
            "volatility space; a lognormal, Black, or shifted-lognormal surface is not "
            "sigma_vcub and is never normalized as though it were"
        )
    return stated


def _unit_scale(surface: CanonicalVolSurface) -> tuple[str, float]:
    """The surface's stated unit and what one of its numbers is worth.

    The whole of this module's unit handling. A capture states no unit, so a
    capture-sourced surface stops here -- which is the point: Annex A.8.1
    forbids reading ``bp`` off the size of the numbers, and a surface whose
    unit nobody has stated has no meaning to normalize.
    """

    stated = surface.volatility_unit
    if stated is None:
        raise VolUnitContractError(
            "this surface states no volatility_unit, so its numbers cannot be normalized to "
            "an absolute decimal normal vol; the unit must be stated by the source, never "
            "inferred from the magnitude of the values (Annex A.8.1)"
        )
    scale = STATED_UNIT_SCALES.get(stated.strip().casefold())
    if scale is None:
        raise VolUnitContractError(
            f"this surface states volatility_unit={stated!r}, which this resolver does not "
            f"pin to an absolute decimal normal vol; known units are "
            f"{sorted(STATED_UNIT_SCALES)}"
        )
    return stated, scale


def _node_index(surface: CanonicalVolSurface) -> dict[tuple[str, str], _Node]:
    """The surface's points, grouped into one entry per expiry/tenor node.

    Unresolved points are dropped from the smile rather than defaulted:
    ``None`` never means zero, and a column the parser could not read is not
    a column at ``0bp`` of spread. The coordinate itself survives in the
    stored surface either way.
    """

    atm: dict[tuple[str, str], float | None] = {}
    offsets: dict[tuple[str, str], dict[float, tuple[float, VolValueKind]]] = {}
    unresolved: dict[tuple[str, str], set[float]] = {}
    for point in surface.points:
        key = (point.expiry, point.underlying_tenor)
        offsets.setdefault(key, {})
        unresolved.setdefault(key, set())
        if point.strike_dimension is StrikeDimension.ATM:
            if point.value_kind is not VolValueKind.ABSOLUTE_VOL:
                raise SpreadReconstructionError(
                    f"the ATM point at {key} carries {point.value_kind.value}; an ATM column "
                    "is the absolute vol every spread at that node is reconstructed from"
                )
            atm[key] = point.volatility
            if point.volatility is None:
                unresolved[key].add(0.0)
        else:
            assert point.strike_offset is not None  # enforced by VolSurfacePoint
            if point.volatility is None:
                unresolved[key].add(point.strike_offset)
            else:
                offsets[key][point.strike_offset] = (point.volatility, point.value_kind)
    return {
        key: _Node(
            expiry_label=key[0],
            tenor_label=key[1],
            atm_raw=atm.get(key),
            offsets=node_offsets,
            unresolved_offsets=tuple(sorted(unresolved[key])),
        )
        for key, node_offsets in offsets.items()
    }


def _bracket(
    coordinates: tuple[float, ...], value: float, axis: str, context: str = ""
) -> tuple[int, int, float]:
    """``(low index, high index, weight)`` for ``value`` on a sorted axis.

    ``weight`` is the share of the high node, so an exact hit returns the
    same index twice with weight ``0.0`` and the arithmetic downstream
    reproduces that node's value exactly rather than to within a rounding
    error.

    Outside the axis this raises: ``VCUB_EXTRAPOLATION_MODE = FAIL_CLOSED``
    is the whole of Shiori's out-of-range policy, and Bloomberg's own flat
    extrapolation is deliberately not mirrored here (Issue #188 §6).
    """

    if value < coordinates[0] or value > coordinates[-1]:
        raise SurfaceCoverageError(
            f"the requested {axis} coordinate {value!r} lies outside the confirmed "
            f"coverage [{coordinates[0]!r}, {coordinates[-1]!r}]{context}; "
            f"VCUB_EXTRAPOLATION_MODE={EXTRAPOLATION_MODE} and this resolver has no "
            "nearest-node, flat, or smile-extension path"
        )
    high = next(index for index, candidate in enumerate(coordinates) if candidate >= value)
    if coordinates[high] == value:
        return high, high, 0.0
    low = high - 1
    return low, high, (value - coordinates[low]) / (coordinates[high] - coordinates[low])


def _require_non_negative_volatility(value: float, node: _Node, offset: float) -> float:
    """Refuse a reconstruction that is not a volatility at all.

    A normal volatility is non-negative, so a spread whose magnitude exceeds
    the ATM vol it is a spread to -- 80.00 ATM against a -90.00 spread --
    has not produced a low vol, it has produced evidence that the capture or
    its spread semantics are wrong. Returning it would hand an impossible
    model input downstream under a resolved label (Codex review, PR #189).
    """

    if value < 0:
        raise NegativeVolatilityError(
            f"reconstructing {node.expiry_label!r} x {node.tenor_label!r} at {offset!r}bp "
            f"gives {value!r}, which is not a volatility; a normal vol is non-negative, so "
            "this surface's values or its spread-to-ATM semantics are not what they claim"
        )
    return value


def _smile_nodes(node: _Node, scale: float) -> tuple[VCUBSmileNode, ...]:
    """Every resolved column of ``node`` as an absolute normal vol.

    Annex A.8.3's reconstruction, and the only place in the repository where
    an ATM vol and a skew spread are added together: ``sigma_abs(T, tau, mu)
    = sigma_ATM(T, tau) + spread(T, tau, mu)``. A spread with no resolved
    ATM to add to is a blocking error, never a vol in its own right.
    """

    reconstructed: list[VCUBSmileNode] = []
    if node.atm_raw is not None:
        _require_non_negative_volatility(node.atm_raw, node, 0.0)
        reconstructed.append(
            VCUBSmileNode(
                moneyness_bp=0.0,
                value_kind=VolValueKind.ABSOLUTE_VOL,
                source_value_raw=node.atm_raw,
                volatility_raw=node.atm_raw,
                volatility=node.atm_raw * scale,
            )
        )
    for offset in sorted(node.offsets):
        raw, kind = node.offsets[offset]
        if kind is VolValueKind.SPREAD_TO_ATM:
            if node.atm_raw is None:
                raise SpreadReconstructionError(
                    f"the node {node.expiry_label!r} x {node.tenor_label!r} holds a "
                    f"spread-to-ATM at {offset!r}bp but no resolved ATM absolute vol to "
                    "reconstruct it from; a spread is not a volatility"
                )
            absolute = node.atm_raw + raw
        else:
            absolute = raw
        _require_non_negative_volatility(absolute, node, offset)
        reconstructed.append(
            VCUBSmileNode(
                moneyness_bp=offset,
                value_kind=kind,
                source_value_raw=raw,
                volatility_raw=absolute,
                volatility=absolute * scale,
            )
        )
    reconstructed.sort(key=lambda smile_node: smile_node.moneyness_bp)
    return tuple(reconstructed)


def _resolve_corner(
    node: _Node,
    *,
    query: VCUBVolQuery,
    coordinates: VCUBGridCoordinates,
    scale: float,
    weight: float,
) -> VCUBResolvedCorner:
    """One corner's normal vol at the query's additive moneyness.

    The corner uses the *same* ``mu*`` as every other corner and as the
    query itself -- Bloomberg's documented same-additive-moneyness rule,
    ``K_ij - F_ij = K* - F*`` -- so the strike that moves from corner to
    corner is derived from each corner's own forward and never from the
    query's.

    :attr:`SmileModel.PWL`: piecewise-linear in additive moneyness on normal
    vols, which reproduces a captured strike node exactly.
    """

    smile_nodes = _smile_nodes(node, scale)
    if not smile_nodes:
        raise SurfaceCoverageError(
            f"the bracketing node {node.expiry_label!r} x {node.tenor_label!r} holds no "
            "resolved volatility, so the requested point cannot be bracketed"
        )
    moneyness = tuple(smile_node.moneyness_bp for smile_node in smile_nodes)
    low, high, smile_weight = _bracket(
        moneyness,
        query.moneyness_bp,
        "additive moneyness (bp)",
        context=f" of the node {node.expiry_label!r} x {node.tenor_label!r}",
    )
    blocked = [
        offset
        for offset in node.unresolved_offsets
        if moneyness[low] <= offset <= moneyness[high]
    ]
    if blocked:
        raise SurfaceCoverageError(
            f"the node {node.expiry_label!r} x {node.tenor_label!r} holds the captured "
            f"strike coordinate(s) {blocked} that the capture left unresolved, and the "
            f"requested moneyness {query.moneyness_bp!r}bp falls on or across them; "
            "interpolating over an unreadable column would answer from its neighbours and "
            "report no fallback"
        )
    volatility_raw = (1.0 - smile_weight) * smile_nodes[low].volatility_raw + (
        smile_weight * smile_nodes[high].volatility_raw
    )
    forward = None
    if query.corner_forwards is not None:
        key = (node.expiry_label, node.tenor_label)
        if key not in query.corner_forwards:
            raise GridCoordinateContractError(
                f"corner_forwards states a forward for some bracketing nodes but not for "
                f"{key}; a partly stated forward contract would report some corner strikes "
                "and silently omit others"
            )
        forward = query.corner_forwards[key]
    return VCUBResolvedCorner(
        expiry_label=node.expiry_label,
        tenor_label=node.tenor_label,
        expiry_coordinate=coordinates.coordinate_for("expiry", node.expiry_label),
        tenor_coordinate=coordinates.coordinate_for("tenor", node.tenor_label),
        weight=weight,
        atm_volatility_raw=node.atm_raw,
        atm_volatility=None if node.atm_raw is None else node.atm_raw * scale,
        smile_nodes=smile_nodes,
        bracketing_moneyness_bp=(moneyness[low], moneyness[high]),
        forward=forward,
        # K_ij = F_ij + mu*, in the forward's own decimal units.
        strike=None if forward is None else forward + query.moneyness_decimal,
        volatility_raw=volatility_raw,
        volatility=volatility_raw * scale,
    )


def resolve_vcub_normal_vol(
    surface: CanonicalVolSurface,
    query: VCUBVolQuery,
    *,
    coordinates: VCUBGridCoordinates,
) -> VCUBNormalVolResolution:
    """Resolve ``sigma_vcub`` from one confirmed canonical VCUB snapshot.

    The whole path Issue #188 asks for, and nothing past it: ATM absolute +
    OTM spread reconstruction, same-additive-moneyness corner strikes,
    corner smile resolution, and bilinear expiry/tenor interpolation of the
    four corners, with every step of it on the returned record.

    One surface in, one number out. Four corners of one snapshot cannot be
    mixed with another's because there is no second surface to mix them
    with, and ``query.expected_surface_id`` lets a caller assert that the
    snapshot it was handed is the one the rest of its inputs came from.

    Raises a :class:`VCUBResolverError` -- never returns a degraded value --
    when the unit, the smile contract, the coordinate map, the snapshot
    identity, or the surface's coverage cannot answer the query.
    """

    if not isinstance(surface, CanonicalVolSurface):
        raise TypeError("surface must be a CanonicalVolSurface")
    if not isinstance(query, VCUBVolQuery):
        raise TypeError("query must be a VCUBVolQuery")
    if not isinstance(coordinates, VCUBGridCoordinates):
        raise TypeError("coordinates must be a VCUBGridCoordinates")

    if query.expected_surface_id is not None and query.expected_surface_id != surface.surface_id:
        raise SurfaceIdentityError(
            f"this query resolves against snapshot {query.expected_surface_id!r}, but the "
            f"surface supplied is {surface.surface_id!r} (capture "
            f"{surface.identity.capture_id!r}, business date "
            f"{surface.identity.business_date!r}); two snapshots are two observations and "
            "are never resolved against each other"
        )
    if query.smile_model is None:
        raise SmileContractError(
            "this query names no smile model; the model a surface is resolved with is part "
            "of the caller's contract and is never inferred from the surface type, the "
            f"column count, or the shape of the numbers. Supported: "
            f"{sorted(model.value for model in SMILE_MODEL_VERSIONS)}"
        )
    if query.smile_model not in SMILE_MODEL_VERSIONS:
        raise SmileContractError(
            f"{query.smile_model.value} smile resolution is not implemented at resolver "
            f"version {RESOLVER_VERSION}: the canonical snapshot carries no calibrated SABR "
            "parameters and this repository pins no reproducible copy of the Bloomberg "
            "calibration objective, so a SABR answer would be invented rather than "
            f"resolved. Supported: {sorted(model.value for model in SMILE_MODEL_VERSIONS)}"
        )

    vol_type = _require_normal_vol_space(surface)
    unit, scale = _unit_scale(surface)
    nodes = _node_index(surface)
    expiry_labels = {label for label, _ in nodes}
    tenor_labels = {tenor for _, tenor in nodes}
    expiry_axis = sorted(
        (coordinates.coordinate_for("expiry", label), label) for label in expiry_labels
    )
    tenor_axis = sorted(
        (coordinates.coordinate_for("tenor", label), label) for label in tenor_labels
    )
    expiry_low, expiry_high, expiry_weight = _bracket(
        tuple(coordinate for coordinate, _ in expiry_axis), query.expiry_coordinate, "expiry"
    )
    tenor_low, tenor_high, tenor_weight = _bracket(
        tuple(coordinate for coordinate, _ in tenor_axis), query.tenor_coordinate, "tenor"
    )

    corner_weights = (
        ((expiry_low, tenor_low), (1.0 - expiry_weight) * (1.0 - tenor_weight)),
        ((expiry_low, tenor_high), (1.0 - expiry_weight) * tenor_weight),
        ((expiry_high, tenor_low), expiry_weight * (1.0 - tenor_weight)),
        ((expiry_high, tenor_high), expiry_weight * tenor_weight),
    )
    corners: list[VCUBResolvedCorner] = []
    for (expiry_index, tenor_index), weight in corner_weights:
        expiry_label = expiry_axis[expiry_index][1]
        tenor_label = tenor_axis[tenor_index][1]
        node = nodes.get((expiry_label, tenor_label))
        if node is None:
            raise SurfaceCoverageError(
                f"the surface holds no node at {expiry_label!r} x {tenor_label!r}, which "
                "brackets the requested point; an incomplete bracket is never completed "
                "from its neighbours"
            )
        corners.append(
            _resolve_corner(
                node, query=query, coordinates=coordinates, scale=scale, weight=weight
            )
        )

    volatility_raw = math.fsum(corner.weight * corner.volatility_raw for corner in corners)
    identity = surface.identity
    provenance = surface.provenance
    return VCUBNormalVolResolution(
        surface_id=surface.surface_id,
        capture_id=identity.capture_id,
        surface_type=identity.surface_type.value,
        business_date=identity.business_date,
        currency=identity.currency,
        curve_config=identity.curve_config,
        side=identity.side,
        vol_type=vol_type,
        source=identity.source,
        unresolved_identity_fields=identity.unresolved_fields,
        captured_at=provenance.captured_at,
        confirmed_by=provenance.confirmed_by,
        confirmed_at=provenance.confirmed_at,
        parser_name=provenance.parser_name,
        parser_version=provenance.parser_version,
        source_volatility_unit=unit,
        unit_scale_to_decimal=scale,
        resolver_name=RESOLVER_NAME,
        resolver_version=RESOLVER_VERSION,
        extrapolation_mode=EXTRAPOLATION_MODE,
        smile_model=query.smile_model,
        smile_model_version=SMILE_MODEL_VERSIONS[query.smile_model],
        query_expiry_coordinate=query.expiry_coordinate,
        query_tenor_coordinate=query.tenor_coordinate,
        query_moneyness_bp=query.moneyness_bp,
        query_moneyness_decimal=query.moneyness_decimal,
        expiry_bracket_labels=(expiry_axis[expiry_low][1], expiry_axis[expiry_high][1]),
        expiry_bracket_coordinates=(expiry_axis[expiry_low][0], expiry_axis[expiry_high][0]),
        expiry_weight=expiry_weight,
        tenor_bracket_labels=(tenor_axis[tenor_low][1], tenor_axis[tenor_high][1]),
        tenor_bracket_coordinates=(tenor_axis[tenor_low][0], tenor_axis[tenor_high][0]),
        tenor_weight=tenor_weight,
        corners=tuple(corners),
        volatility=volatility_raw * scale,
        volatility_raw=volatility_raw,
        volatility_unit=NORMALIZED_VOLATILITY_UNIT,
    )
