"""``historical_equivalent_price_vol``: Issue #197 Historical Yield Vol ->
Annex A v1.4 §A.8.6 Equivalent Price Vol (Issue #211).

**What this module is.** The one deterministic conversion producer for the
``HISTORICAL_YIELD_VOL_MO`` source::

    sigma_P = |D_B| x sigma_hist_abs

where ``sigma_hist_abs`` is the merged Issue #197 Historical Yield Vol
normalized to absolute decimal Yield units per annum, and ``D_B`` is the
approved current-time modified duration from
:mod:`shiori_pricing_lab.pricing.bli_bond_modified_duration`.

**The result inherits the duration's price basis; it never picks one.**
``sigma_P`` is the proportional volatility of a specific bond price state, so
a ``CLEAN`` duration yields a ``CLEAN`` Equivalent Price Vol and a ``DIRTY``
duration a ``DIRTY`` one::

    sigma_P_clean = |D_B_clean| x sigma_hist_abs
    sigma_P_dirty = |D_B_dirty| x sigma_hist_abs

Neither is "the" answer -- see
:mod:`shiori_pricing_lab.pricing.bli_bond_option_price_basis` for why both are
approved and for the end-to-end rule that a composition must use one basis for
``F``, ``K``, ``sigma_P`` and the duration denominator alike. This module's
part of that rule is to make the basis **impossible to lose**: it is carried
on every result, and a duration whose declared basis and duration-type label
disagree is refused rather than converted, because after the multiplication
nothing downstream could tell the two apart by inspection.

**Why this is the whole formula.** Issue #210's audit established that #197
produces an *absolute* (normal) annualized yield volatility -- a sample
standard deviation of arithmetic Yield *changes*, never divided by a Yield
level. Annex A v1.4 §A.8.6's ``sigma_P = |D_B| x sigma_Y^N`` consumes exactly
that basis. So the bridge is a single multiplication, and specifically:

- **no forward yield ``Y`` appears**, in either direction. The retired v1.3
  ``sigma_Y x Y x ModDur`` form expected a *relative/lognormal* yield vol; a
  ``/Y`` followed by a ``xY`` is an algebraic round-trip that cancels, adds a
  spurious ``Y -> 0`` singularity, and reintroduces a level this contract
  does not need;
- **no convexity correction** -- §A.8.6 states convexity lives in drift and
  higher-order terms and is not part of this conversion;
- **no ``DCF_VCUB`` / ``DCF_BondVol`` total-variance adjustment** -- that
  belongs to the VCUB chain (§A.8.5, still RED under Issue #192) and has no
  application to a historical source;
- **no calibration, no fitted multiplier** of any kind.

**This source is realized, not implied, and says so.** The result is labelled
``HISTORICAL_YIELD_VOL_MO`` and ``EQUIVALENT_PRICE_VOL`` -- never
``PRICE_VOL``, never ``YIELD_VOL``, and never ``VCUB_NORMAL_PROXY``. It is a
historical/realized proxy approved by the Trading Desk for internal-model
reconciliation (Issue #211), and mislabelling it as Bloomberg implied vol
would be the one failure the whole #210/#211 sequence exists to prevent.

**Unit normalization happens exactly once, and not here.** This module does
not rescale anything. It calls #197's own reviewed
:func:`~shiori_pricing_lab.data.historical_yield_volatility.historical_yield_vol_volatility_input`,
which is the single place a ``PERCENT`` / ``BASIS_POINTS`` / ``DECIMAL``
Yield unit becomes ``DECIMAL_ANNUAL``, and reads the already-normalized
value off the ``BLIVolatilityInput`` it returns. Every fail-closed condition
that helper already enforces -- an undeclared or unsupported unit, a
``NO_HISTORY`` window, too few changes, a vol that underflows its own
conversion -- therefore applies to this path automatically, rather than being
re-implemented (and drifting) here. The source unit and the factor applied
are carried into this result's provenance so the single conversion stays
visible.

**The two lineages are kept whole.** A published Equivalent Price Vol is
only auditable if both parents are: the result carries the full #197
statistic lineage (window status, both observation counts, change count,
STDEV convention, annualization, source unit and factor, #197's own
``calculated_at``) and the complete :class:`BLIBondModifiedDuration` object,
whose own provenance is enough to recompute ``-(dP/dY)/P_dirty`` by hand.

**The two parents must be the same bond.** A duration for one security
multiplied by a Historical Yield Vol for another is arithmetically fine and
financially meaningless, so the security identities must match exactly or the
conversion is refused. Nothing else in the chain would catch it.

**It reaches no pricing chain by itself.** Producing a
``BLIVolatilityInput`` on the ``EQUIVALENT_PRICE_VOL`` basis makes this
source *eligible* for the existing Black-76 path -- that basis is already in
``bli_mvp_required_input_guard``'s supported set and always has been. The raw
``YIELD_VOL`` basis stays refused by that guard, unchanged: this module
converts rather than making the guard permissive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shiori_pricing_lab.data.bli_snapshot import (
    BLIMarketDataStatus,
    BLIVolatilityBasis,
    BLIVolatilityInput,
)
from shiori_pricing_lab.data.historical_yield_volatility import (
    HISTORICAL_YIELD_VOL_MO_SOURCE,
    PUBLISHED_VOLATILITY_UNIT,
    HistoricalYieldVolResult,
    HistoricalYieldVolUnavailableError,
    decimal_annual_normalization_factor,
    historical_yield_vol_volatility_input,
)
from shiori_pricing_lab.pricing.bli_bond_modified_duration import (
    DURATION_METHODOLOGY_VERSION,
    BLIBondDurationError,
    BLIBondModifiedDuration,
    calculate_bond_modified_duration,
    duration_type_for_basis,
)
from shiori_pricing_lab.pricing.bli_bond_option_price_basis import (
    BondOptionPriceBasis,
    require_bond_option_price_basis,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import IrregularFirstCoupon

# The approved conversion, named. A different bridge -- a convexity term, a
# relative-vol round-trip, a DCF adjustment -- is a different version string.
EQUIVALENT_PRICE_VOL_METHODOLOGY_VERSION = "ANNEX_A_V1_4_A_8_6_DURATION_FIRST_ORDER_V1"

# The source mode this producer serves. Explicitly *not* VCUB_NORMAL_PROXY,
# DIRECT_PRICE_VOL or LOGNORMAL_YIELD_VOL_OVERRIDE.
BOND_VOL_SOURCE_MODE = HISTORICAL_YIELD_VOL_MO_SOURCE

# Carried on every result so a consumer never has to infer that this is a
# backward-looking statistic from the source name alone.
VOLATILITY_KIND = "HISTORICAL_REALIZED"

# Tolerance for re-deriving a duration from its own recorded numerator and
# denominator. The same relative tolerance #197 uses to re-check its own
# annualization (`historical_yield_volatility._require_publishable_shape`):
# a genuine record reproduces exactly, so this only absorbs a float
# round-trip, never a basis substitution -- CLEAN and DIRTY are percent
# apart, some twelve orders of magnitude outside it.
_DURATION_REL_TOL = 1e-12

# Which price bases may currently be published into the shared
# ``BLIVolatilityInput`` pricing contract. DIRTY only, and deliberately so:
# see :func:`historical_equivalent_price_vol_volatility_input`.
PUBLISHABLE_PRICE_BASES: frozenset[BondOptionPriceBasis] = frozenset(
    {BondOptionPriceBasis.DIRTY}
)


class BLIHistoricalEquivalentPriceVolError(ValueError):
    """One fail-closed refusal type for every condition in this module."""


@dataclass(frozen=True)
class BLIHistoricalEquivalentPriceVol:
    """One Equivalent Price Vol, with both parent lineages intact.

    ``equivalent_price_vol`` is the ``DECIMAL_ANNUAL`` lognormal bond *price*
    volatility Black-76 consumes, on the price basis ``price_basis`` names.
    ``historical_yield_vol_decimal_annual`` is the absolute *yield* volatility
    it came from -- the two are different quantities in different bases and
    are deliberately both present, named apart, so no reader has to work out
    which one a bare number is.

    ``price_basis`` is inherited from the duration, never chosen here. It must
    travel with the value: a ``CLEAN`` and a ``DIRTY`` Equivalent Price Vol
    for the same bond are both ordinary-looking numbers a few percent apart,
    and only this field distinguishes them.
    """

    security: str
    price_basis: BondOptionPriceBasis

    # --- #197 Historical Yield Vol lineage --------------------------------
    historical_yield_vol_source: str
    historical_yield_vol_decimal_annual: float
    historical_yield_vol_field_unit: str
    historical_yield_vol_normalization_factor: float
    historical_yield_vol_in_field_unit: float
    historical_yield_vol_window_status: str
    historical_yield_vol_observation_count: int
    historical_yield_vol_requested_observation_count: int
    historical_yield_vol_change_count: int
    historical_yield_vol_convention: str
    historical_yield_vol_annualization_trading_days: int
    historical_yield_vol_calculated_at: str

    # --- Parent lineages, whole (the objects, not copied numbers) ---------
    #
    # Both parents are retained in full so publication can revalidate them
    # rather than trusting the flattened scalars above (Codex review, PR
    # #212). Those scalars stay for audit and display; they are checked
    # against these on the way out, never read in their place.
    historical_yield_vol: HistoricalYieldVolResult
    duration: BLIBondModifiedDuration

    # --- Result ------------------------------------------------------------
    equivalent_price_vol: float
    volatility_basis: BLIVolatilityBasis
    source_system: str
    bond_vol_source_mode: str
    volatility_kind: str
    unit: str
    methodology_version: str
    calculated_at: str
    warnings: tuple[str, ...] = ()


#: Derived fields a re-run of the duration producer must reproduce. The
#: *inputs* are excluded on purpose -- they are what the re-run is fed, so
#: comparing them would only compare each value with itself.
_REPRODUCED_FLOAT_FIELDS: tuple[str, ...] = (
    "accrued_interest_per_100",
    "dirty_price_per_100",
    "basis_price_per_100",
    "base_yield_percent",
    "yield_bump_basis_points",
    "bumped_yield_up_percent",
    "bumped_yield_down_percent",
    "bumped_clean_price_up_per_100",
    "bumped_clean_price_down_per_100",
    "price_derivative_per_unit_yield",
    "modified_duration",
    "absolute_modified_duration",
)

_REPRODUCED_EXACT_FIELDS: tuple[str, ...] = (
    "coupons_per_year",
    "day_count",
    "duration_type",
    "price_basis",
    "source",
    "methodology_version",
)


def equivalent_price_vol_from(
    absolute_modified_duration: float, historical_yield_vol_decimal_annual: float
) -> float:
    """The Annex A v1.4 §A.8.6 conversion itself: ``|D_B| x sigma_hist_abs``.

    Separated from :func:`historical_equivalent_price_vol` because the two
    answer different questions. This is the *formula*, and it is the same
    formula on either price basis. That function additionally establishes
    that a particular pair of records may be multiplied at all -- same bond,
    reproducible duration, consistent basis, publishable unit -- which is
    where nearly all of its code lives.

    Keeping the formula addressable on its own is what lets the approved
    numerical fixture (``6.5 x 0.0073070244 = 0.0474956586``) be pinned as a
    generic formula test, rather than requiring a real bond whose duration
    happens to land on a round number.

    ``abs()`` is applied here as well as at the duration, so a sign
    convention can never reach Black-76 as a negative sigma even if a caller
    hands this the signed figure.
    """

    return abs(absolute_modified_duration) * historical_yield_vol_decimal_annual


def _schedule_of(duration: BLIBondModifiedDuration) -> IrregularFirstCoupon | None:
    """Rebuild the irregular schedule the record says it was calculated under.

    Both dates or neither, the same rule ``IrregularFirstCoupon`` itself
    enforces: a half schedule is refused rather than completed by guessing.
    """

    start = duration.schedule_accrual_start
    first = duration.schedule_first_coupon
    if start is None and first is None:
        return None
    if start is None or first is None:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} carries half an irregular first-coupon "
            f"schedule (accrual start {start!r}, first coupon {first!r}) -- neither the "
            "cashflows it used nor the ones it did not can be established from that"
        )
    return IrregularFirstCoupon(accrual_start=start, first_coupon=first)


def _require_reproducible_duration(duration: BLIBondModifiedDuration) -> None:
    """Re-run the duration producer over ``duration``'s own inputs and compare.

    Every derived figure must come back identical (floats at
    :data:`_DURATION_REL_TOL`, labels exactly). A genuine record reproduces
    exactly; a reconstructed one whose derived fields were edited -- even
    edited *consistently with each other* -- does not.
    """

    schedule = _schedule_of(duration)
    try:
        reproduced = calculate_bond_modified_duration(
            security=duration.security,
            convention_profile=duration.convention_profile,
            price_basis=duration.price_basis,
            clean_price_per_100=duration.clean_price_per_100,
            settlement_date=duration.settlement_date,
            maturity_date=duration.maturity_date,
            coupon_percent=duration.coupon_percent,
            pricing_timestamp=duration.pricing_timestamp,
            calculated_at=duration.calculated_at,
            schedule=schedule,
        )
    except BLIBondDurationError as exc:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} cannot be reproduced from the inputs "
            f"it declares: {exc}"
        ) from exc

    for field_name in _REPRODUCED_EXACT_FIELDS:
        recorded = getattr(duration, field_name)
        expected = getattr(reproduced, field_name)
        if recorded != expected:
            raise BLIHistoricalEquivalentPriceVolError(
                f"the duration for {duration.security!r} records {field_name}="
                f"{recorded!r}, but its own declared inputs produce {expected!r} -- the "
                "record is not reproducible, so the volatility it would produce describes "
                "no calculation that happened"
            )

    for field_name in _REPRODUCED_FLOAT_FIELDS:
        recorded = getattr(duration, field_name)
        expected = getattr(reproduced, field_name)
        if not math.isclose(recorded, expected, rel_tol=_DURATION_REL_TOL, abs_tol=0.0):
            raise BLIHistoricalEquivalentPriceVolError(
                f"the duration for {duration.security!r} records {field_name}="
                f"{recorded!r}, but re-running the duration producer over its own declared "
                f"inputs gives {expected!r} -- the record is not reproducible, so the "
                "volatility it would produce describes no calculation that happened"
            )


#: Flattened #197 fields that must still agree with the retained parent.
_ECHOED_HISTORICAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("historical_yield_vol_field_unit", "field_unit"),
    ("historical_yield_vol_in_field_unit", "annualized_yield_vol"),
    ("historical_yield_vol_observation_count", "observation_count"),
    ("historical_yield_vol_requested_observation_count", "requested_observation_count"),
    ("historical_yield_vol_change_count", "yield_change_count"),
    ("historical_yield_vol_convention", "standard_deviation_convention"),
    ("historical_yield_vol_annualization_trading_days", "annualization_trading_days"),
    ("historical_yield_vol_calculated_at", "calculated_at"),
)


#: Labels whose only correct value is this producer's own constant. A
#: reconstructed record can carry any string in them, and each one changes
#: what a reader believes the number *is* rather than what it equals --
#: which is the misrepresentation this whole source mode exists to prevent.
_PUBLICATION_LABEL_CONSTANTS: tuple[tuple[str, object], ...] = (
    ("historical_yield_vol_source", HISTORICAL_YIELD_VOL_MO_SOURCE),
    ("source_system", HISTORICAL_YIELD_VOL_MO_SOURCE),
    ("bond_vol_source_mode", BOND_VOL_SOURCE_MODE),
    ("volatility_kind", VOLATILITY_KIND),
    ("unit", PUBLISHED_VOLATILITY_UNIT),
    ("methodology_version", EQUIVALENT_PRICE_VOL_METHODOLOGY_VERSION),
    ("volatility_basis", BLIVolatilityBasis.EQUIVALENT_PRICE_VOL),
)


def _require_publishable_identity(converted: BLIHistoricalEquivalentPriceVol) -> None:
    """Both parents must be the same bond, and every label must be this one's.

    Two independent gaps with one cause (Codex review, PR #212): the parents
    were revalidated *separately*, and the labels were not validated at all.

    A reproducible duration for a different bond therefore passed, because
    each parent was internally sound -- the conversion's own same-bond check
    lives in :func:`historical_equivalent_price_vol`, and a reconstructed
    record never goes through it. One bond's duration multiplied by another's
    yield vol is a finite number that nothing downstream could recognize.

    And a record relabelled ``VCUB_NORMAL_PROXY`` / ``IMPLIED`` /
    ``BLOOMBERG_DAPI`` published a correct, fully-validated *number* under a
    description that makes it a different risk figure entirely. The arithmetic
    gates cannot see that, because the arithmetic is right.
    """

    security = converted.security
    if not isinstance(security, str) or not security.strip():
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol names no security (got {security!r}), so nothing "
            "identifies what it is a volatility of"
        )
    for label, parent_security in (
        ("duration", converted.duration.security),
        ("Historical Yield Vol", converted.historical_yield_vol.security),
    ):
        if parent_security != security:
            raise BLIHistoricalEquivalentPriceVolError(
                f"the Equivalent Price Vol of {security!r} carries a {label} for "
                f"{parent_security!r} -- one bond's duration is never combined with "
                "another bond's yield volatility, and the product of the two identifies "
                "no instrument at all"
            )

    for field_name, expected in _PUBLICATION_LABEL_CONSTANTS:
        recorded = getattr(converted, field_name)
        if recorded != expected:
            raise BLIHistoricalEquivalentPriceVolError(
                f"the Equivalent Price Vol of {security!r} records {field_name}="
                f"{recorded!r}, but this producer only ever emits {expected!r} -- a "
                "historical/realized proxy is never published under another source, "
                "basis, unit or methodology"
            )


def _require_normalized_yield_vol_matches(
    converted: BLIHistoricalEquivalentPriceVol,
) -> float:
    """Return the normalized yield vol #197 itself produces for the parent.

    The retained :class:`HistoricalYieldVolResult` is put back through
    #197's own reviewed publication helper -- the single normalization point
    -- and the recorded ``historical_yield_vol_decimal_annual`` must equal
    what comes back. The flattened echoes are checked against the parent too,
    so a record cannot carry a source-unit lineage describing one calculation
    and a normalized value from another.
    """

    parent = converted.historical_yield_vol
    if not isinstance(parent, HistoricalYieldVolResult):
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {converted.security!r} carries no usable "
            f"Historical Yield Vol parent (got {type(parent).__name__}), so its "
            "normalized volatility cannot be revalidated"
        )
    if parent.security != converted.security:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {converted.security!r} retains a Historical "
            f"Yield Vol for {parent.security!r} -- one bond's volatility is never "
            "published under another bond's name"
        )

    for echoed_name, parent_name in _ECHOED_HISTORICAL_FIELDS:
        echoed = getattr(converted, echoed_name)
        original = getattr(parent, parent_name)
        # ``field_unit`` is echoed through ``str()`` at construction.
        if echoed_name == "historical_yield_vol_field_unit":
            original = str(original)
        if echoed != original:
            raise BLIHistoricalEquivalentPriceVolError(
                f"the Equivalent Price Vol of {converted.security!r} records "
                f"{echoed_name}={echoed!r}, but its retained Historical Yield Vol says "
                f"{original!r} -- the lineage it displays is not the calculation it came "
                "from"
            )

    try:
        expected_factor = decimal_annual_normalization_factor(parent.field_unit)
        normalized = historical_yield_vol_volatility_input(parent).volatility
    except (HistoricalYieldVolUnavailableError, ValueError) as exc:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Historical Yield Vol retained for {converted.security!r} can no longer "
            f"be published by #197's own helper, so it cannot be revalidated: {exc}"
        ) from exc

    if converted.historical_yield_vol_normalization_factor != expected_factor:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {converted.security!r} records a normalization "
            f"factor of {converted.historical_yield_vol_normalization_factor!r}, but "
            f"{parent.field_unit!r} normalizes by {expected_factor!r} -- the unit and the "
            "factor it claims to have applied do not agree"
        )
    if not math.isclose(
        converted.historical_yield_vol_decimal_annual,
        normalized,
        rel_tol=_DURATION_REL_TOL,
        abs_tol=0.0,
    ):
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {converted.security!r} records a Historical "
            f"Yield Vol of {converted.historical_yield_vol_decimal_annual!r} "
            f"{converted.unit}, but normalizing its own retained result gives "
            f"{normalized!r} -- the value carried is not the one #197 produces"
        )
    return normalized


def historical_equivalent_price_vol(
    historical_yield_vol: HistoricalYieldVolResult,
    duration: BLIBondModifiedDuration,
    *,
    calculated_at: str | None = None,
) -> BLIHistoricalEquivalentPriceVol:
    """Convert one #197 result and one approved ``D_B`` into ``sigma_P``.

    ``sigma_P = |D_B| x sigma_hist_abs``, where ``sigma_hist_abs`` is read
    from #197's own publication helper (the single normalization point) and
    ``|D_B|`` is the absolute dirty-price modified duration.

    ``calculated_at`` defaults to the duration's own timestamp rather than to
    a clock reading: no module under ``shiori_pricing_lab/pricing/`` may read
    the system clock (``tests/test_pricing_engine.py`` enforces that over the
    package source, scanning for the wall-clock call forms), because a
    pricing result that silently depends on when it ran is not reproducible.
    A caller that wants its own run timestamp passes one.

    Raises :class:`BLIHistoricalEquivalentPriceVolError` when the two parents
    are not the same bond, when the duration is unusable, or when the product
    is not a finite positive volatility. A #197 result that cannot honestly be
    published -- no history, too few changes, an undeclared or unsupported
    Yield unit, a degenerate window -- is refused by its own helper, whose
    :class:`HistoricalYieldVolUnavailableError` is re-raised on this module's
    error type with the original reason preserved verbatim.
    """

    if not isinstance(historical_yield_vol, HistoricalYieldVolResult):
        raise BLIHistoricalEquivalentPriceVolError(
            "historical_yield_vol must be a HistoricalYieldVolResult, got "
            f"{type(historical_yield_vol).__name__}"
        )
    if not isinstance(duration, BLIBondModifiedDuration):
        raise BLIHistoricalEquivalentPriceVolError(
            f"duration must be a BLIBondModifiedDuration, got {type(duration).__name__}"
        )
    # The basis lineage must be present and self-consistent before anything
    # is multiplied. After the multiplication a CLEAN and a DIRTY sigma_P for
    # the same bond are both ordinary-looking numbers a few percent apart, so
    # this is the last point at which a corrupted basis is detectable at all.
    try:
        basis = require_bond_option_price_basis(
            duration.price_basis, "duration.price_basis"
        )
    except ValueError as exc:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} carries no usable price basis: {exc}"
        ) from exc

    expected_type = duration_type_for_basis(basis)
    if duration.duration_type != expected_type:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} declares price basis {basis.value} "
            f"but is labelled {duration.duration_type!r} rather than {expected_type!r} -- "
            "a duration whose basis and type disagree is refused rather than converted, "
            "because the resulting volatility would be indistinguishable from the other "
            "basis's"
        )

    expected_basis_price = (
        duration.clean_price_per_100
        if basis is BondOptionPriceBasis.CLEAN
        else duration.dirty_price_per_100
    )
    if duration.basis_price_per_100 != expected_basis_price:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} declares price basis {basis.value} "
            f"but divided by {duration.basis_price_per_100!r} rather than "
            f"{expected_basis_price!r} -- its own provenance is inconsistent, so the "
            "volatility it would produce belongs to no stated basis"
        )

    if duration.methodology_version != DURATION_METHODOLOGY_VERSION:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} was produced by "
            f"{duration.methodology_version!r}, but this conversion is approved only "
            f"against {DURATION_METHODOLOGY_VERSION!r} -- a duration from another "
            "methodology is not silently accepted onto this path"
        )

    # Reproduce the whole duration from the record's own declared inputs
    # (Codex review, PR #212). Re-deriving only ``-(dP/dY) / P_basis`` was not
    # enough: a record whose numerator *and* both magnitudes are scaled
    # together stays self-consistent, so every label and quotient gate passes
    # while sigma_P moves and the recorded bumped prices no longer support any
    # of it. Doubling those three fields doubled the published volatility.
    #
    # So the test is reproducibility, not internal agreement: the record
    # carries every input its own producer needs, and re-running that one
    # deterministic producer over them must return the same derived figures.
    # That subsumes the quotient check, the bump convention, the numerator,
    # the accrual and the denominator in a single statement, and it adds no
    # second implementation of anything -- it calls the same function.
    _require_reproducible_duration(duration)

    if calculated_at is None:
        calculated_at = duration.calculated_at
    elif not isinstance(calculated_at, str) or not calculated_at.strip():
        raise BLIHistoricalEquivalentPriceVolError(
            f"calculated_at must be a non-blank timestamp when supplied, got "
            f"{calculated_at!r}"
        )

    # Same bond, or no conversion. Neither parent checks this and nothing
    # downstream could detect it: the product of a duration for one security
    # and a yield vol for another is a perfectly finite number.
    if historical_yield_vol.security != duration.security:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Historical Yield Vol is for {historical_yield_vol.security!r} but the "
            f"duration is for {duration.security!r} -- one bond's yield volatility is "
            "never converted through another bond's duration"
        )

    # The one normalization point. Its refusals are this path's refusals.
    try:
        normalized_input = historical_yield_vol_volatility_input(historical_yield_vol)
    except HistoricalYieldVolUnavailableError as exc:
        raise BLIHistoricalEquivalentPriceVolError(
            f"no Equivalent Price Vol for {duration.security!r}: {exc}"
        ) from exc

    sigma_hist_abs = normalized_input.volatility
    factor = decimal_annual_normalization_factor(historical_yield_vol.field_unit)

    absolute_duration = duration.absolute_modified_duration
    if not isinstance(absolute_duration, float) or not math.isfinite(absolute_duration):
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} carries a non-finite "
            f"absolute_modified_duration ({absolute_duration!r}), which cannot scale a "
            "volatility"
        )
    if not absolute_duration > 0:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the duration for {duration.security!r} is {absolute_duration!r}, which is "
            "not positive -- a zero or negative duration cannot produce a usable price "
            "volatility, and this path publishes no substitute"
        )

    # The formula itself lives in one addressable place; abs() is applied
    # there, so a sign convention on D_B can never hand Black-76 a negative
    # sigma.
    equivalent_price_vol = equivalent_price_vol_from(absolute_duration, sigma_hist_abs)

    if not math.isfinite(equivalent_price_vol):
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {duration.security!r} is "
            f"{equivalent_price_vol!r} -- |D_B| {absolute_duration!r} times Historical "
            f"Yield Vol {sigma_hist_abs!r} is not representable as a finite number"
        )
    if not equivalent_price_vol > 0:
        # Both parents were positive and finite, so this is underflow rather
        # than a flat window -- said as what it is, not blamed on the data.
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {duration.security!r} is "
            f"{equivalent_price_vol!r}, which is not positive: |D_B| {absolute_duration!r} "
            f"times Historical Yield Vol {sigma_hist_abs!r} underflowed to zero, and a "
            "volatility that cannot survive its own conversion is refused rather than "
            "published as zero"
        )

    warnings = tuple(historical_yield_vol.warnings) + tuple(duration.warnings)

    return BLIHistoricalEquivalentPriceVol(
        security=duration.security,
        price_basis=basis,
        historical_yield_vol_source=HISTORICAL_YIELD_VOL_MO_SOURCE,
        historical_yield_vol_decimal_annual=sigma_hist_abs,
        historical_yield_vol_field_unit=str(historical_yield_vol.field_unit),
        historical_yield_vol_normalization_factor=factor,
        historical_yield_vol_in_field_unit=historical_yield_vol.annualized_yield_vol,
        historical_yield_vol_window_status=historical_yield_vol.window_status.value,
        historical_yield_vol_observation_count=historical_yield_vol.observation_count,
        historical_yield_vol_requested_observation_count=(
            historical_yield_vol.requested_observation_count
        ),
        historical_yield_vol_change_count=historical_yield_vol.yield_change_count,
        historical_yield_vol_convention=historical_yield_vol.standard_deviation_convention,
        historical_yield_vol_annualization_trading_days=(
            historical_yield_vol.annualization_trading_days
        ),
        historical_yield_vol_calculated_at=historical_yield_vol.calculated_at,
        historical_yield_vol=historical_yield_vol,
        duration=duration,
        equivalent_price_vol=equivalent_price_vol,
        volatility_basis=BLIVolatilityBasis.EQUIVALENT_PRICE_VOL,
        source_system=HISTORICAL_YIELD_VOL_MO_SOURCE,
        bond_vol_source_mode=BOND_VOL_SOURCE_MODE,
        volatility_kind=VOLATILITY_KIND,
        unit=PUBLISHED_VOLATILITY_UNIT,
        methodology_version=EQUIVALENT_PRICE_VOL_METHODOLOGY_VERSION,
        calculated_at=calculated_at,
        warnings=warnings,
    )


def historical_equivalent_price_vol_volatility_input(
    converted: BLIHistoricalEquivalentPriceVol,
) -> BLIVolatilityInput:
    """Publish ``converted`` as an ``EQUIVALENT_PRICE_VOL`` pricing input.

    The already-reviewed ``BLIVolatilityInput`` is the one normalized
    volatility contract in this repository, so this constructs one rather
    than adding a second schema beside it -- the same shape #197's own
    publication helper and ``pricing/bli_effective_forward.py`` established.

    **CLEAN publication is refused until the basis-aware pricing wiring
    exists** (Codex review, PR #212). ``BLIVolatilityInput`` carries a
    volatility and a ``volatility_basis``, but no *price* basis -- and the
    current standalone engine takes ``snapshot.volatility_input.volatility``
    and applies it unconditionally to a dirty forward and a dirty strike
    (``bli_pricing_engine.black76_dirty_price_option_pv_per_100``). So a
    ``CLEAN`` volatility published into this contract would be consumed as
    though it were ``DIRTY``, silently constructing exactly the mixed
    clean-vol / dirty-F/K state the convention forbids -- and it would not
    raise anywhere, because both numbers are ordinary.

    Refusing here, at the boundary where the value would enter the shared
    pricing contract, is the smallest safe answer: the ``CLEAN`` conversion
    result itself remains fully computable, inspectable and auditable, and it
    simply cannot be handed to a runtime that has no way to honour its basis.
    ``PUBLISHABLE_PRICE_BASES`` becomes a one-line change when Phase 4/5
    carries the price basis structurally to the pricing boundary; nothing
    here coerces ``CLEAN`` to ``DIRTY``, and no basis is dropped to prose.

    No rescaling happens here either: ``equivalent_price_vol`` is already
    ``DECIMAL_ANNUAL``, which is the unit ``BLIVolatilityInput`` states
    (docs/30 §1). The value crosses this boundary unchanged.

    ``override_or_fallback_audit`` is always populated. A converted historical
    statistic is not the "directly observed value" a blank audit would claim,
    and §A.8.8's reporting discipline requires the derivation to travel with
    the resolved result -- so a consumer holding only this input can still see
    the source mode, that the vol is realized rather than implied, both
    parents' key figures, and the exact conversion performed.
    """

    if not isinstance(converted, BLIHistoricalEquivalentPriceVol):
        raise BLIHistoricalEquivalentPriceVolError(
            "converted must be a BLIHistoricalEquivalentPriceVol, got "
            f"{type(converted).__name__}"
        )

    # Before anything is constructed: the refusal has to happen here, not
    # downstream, because once a BLIVolatilityInput exists the engine cannot
    # tell which price basis its number belongs to.
    _require_publishable_identity(converted)

    basis = require_bond_option_price_basis(converted.price_basis, "converted.price_basis")

    # The top-level basis is a *label*; the volatility's actual basis is
    # whatever the nested duration divided by (Codex review, PR #212).
    # Checking the allowlist against the label alone let a CLEAN conversion
    # be relabelled DIRTY -- `dataclasses.replace(converted,
    # price_basis=DIRTY)` -- and published straight into the dirty-F/K
    # engine, which is the exact state this allowlist exists to prevent. So
    # the label must agree with the duration, and the arithmetic between them
    # must still hold, before the allowlist is consulted at all.
    duration_basis = require_bond_option_price_basis(
        converted.duration.price_basis, "converted.duration.price_basis"
    )
    if duration_basis is not basis:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {converted.security!r} is labelled "
            f"{basis.value} but its duration divided by the {duration_basis.value} price "
            f"({converted.duration.basis_price_per_100!r}) -- the value is "
            f"{duration_basis.value}-derived and relabelling it does not change what it "
            "is, so it is refused rather than published under a basis it does not have"
        )

    _require_reproducible_duration(converted.duration)

    # The normalized yield vol is an editable field too (Codex review, PR
    # #212). Checking it only against ``equivalent_price_vol`` compared two
    # editable numbers with each other, so doubling both published double the
    # calculated risk figure while the retained source-unit lineage still
    # recorded the original.
    #
    # #197 cannot be re-run the way the duration producer can -- it
    # deliberately does not carry the Yield values or the Yield Changes, so
    # its arithmetic is unreplayable by design. What *is* replayable is its
    # normalization, so the retained result is put back through #197's own
    # publication helper and the answer must match. That helper also re-runs
    # #197's internal shape checks, so a tampered parent fails there rather
    # than here.
    sigma_hist = _require_normalized_yield_vol_matches(converted)

    expected_vol = converted.duration.absolute_modified_duration * sigma_hist
    if not math.isclose(
        converted.equivalent_price_vol, expected_vol, rel_tol=_DURATION_REL_TOL, abs_tol=0.0
    ):
        raise BLIHistoricalEquivalentPriceVolError(
            f"the Equivalent Price Vol of {converted.security!r} is "
            f"{converted.equivalent_price_vol!r}, but |D_B| "
            f"{converted.duration.absolute_modified_duration!r} x Historical Yield Vol "
            f"{converted.historical_yield_vol_decimal_annual!r} is {expected_vol!r} -- the "
            "value carried is not the one its own recorded parents produce"
        )

    if basis not in PUBLISHABLE_PRICE_BASES:
        raise BLIHistoricalEquivalentPriceVolError(
            f"the {basis.value}-basis Equivalent Price Vol of {converted.security!r} "
            f"({converted.equivalent_price_vol!r} {converted.unit}) cannot be published "
            f"into BLIVolatilityInput: that contract carries no price basis, and the "
            "current standalone pricing path applies whatever volatility it receives to a "
            "dirty forward and a dirty strike -- publishing this value would silently "
            f"create the forbidden {basis.value}-vol / DIRTY-F/K state. Only "
            f"{sorted(member.value for member in PUBLISHABLE_PRICE_BASES)!r} is publishable "
            "until the basis-aware pricing wiring of Issue #211 Phase 4/5 exists. The "
            f"{basis.value} conversion result itself remains available for inspection and "
            "audit; it is not coerced to DIRTY and no substitute is published in its place"
        )

    duration = converted.duration
    audit = (
        f"{converted.bond_vol_source_mode} {converted.volatility_kind} "
        f"{converted.price_basis.value} price basis: "
        f"{converted.methodology_version} sigma_P = |D_B| x sigma_hist_abs = "
        f"{duration.absolute_modified_duration!r} x "
        f"{converted.historical_yield_vol_decimal_annual!r} = "
        f"{converted.equivalent_price_vol!r} {converted.unit}. "
        f"This volatility is on the {converted.price_basis.value} price basis and must "
        f"only be composed with {converted.price_basis.value} forward/strike. "
        f"D_B is {duration.duration_type} ({duration.methodology_version}) for "
        f"{duration.security!r} on the "
        f"{duration.convention_profile} convention: t0={duration.pricing_timestamp}, "
        f"tS={duration.settlement_date.isoformat()}, clean "
        f"{duration.clean_price_per_100!r} + accrued "
        f"{duration.accrued_interest_per_100!r} = dirty "
        f"{duration.dirty_price_per_100!r}, divided by "
        f"{duration.basis_price_per_100!r}, base yield "
        f"{duration.base_yield_percent!r}% bumped "
        f"+/-{duration.yield_bump_basis_points!r}bp. Historical Yield Vol from "
        f"{converted.historical_yield_vol_observation_count} of "
        f"{converted.historical_yield_vol_requested_observation_count} observations "
        f"({converted.historical_yield_vol_change_count} Yield Changes), "
        f"{converted.historical_yield_vol_convention} x sqrt("
        f"{converted.historical_yield_vol_annualization_trading_days}), source unit "
        f"{converted.historical_yield_vol_field_unit} normalized by factor "
        f"{converted.historical_yield_vol_normalization_factor!r}. "
        "Realized/historical proxy for internal-model reconciliation -- not Bloomberg "
        "implied vol, no VCUB DCF adjustment, no convexity correction, no fitted factor."
    )

    return BLIVolatilityInput(
        volatility=converted.equivalent_price_vol,
        volatility_basis=BLIVolatilityBasis.EQUIVALENT_PRICE_VOL,
        source_system=converted.source_system,
        status=BLIMarketDataStatus.ACTIVE,
        override_or_fallback_audit=audit,
    )
