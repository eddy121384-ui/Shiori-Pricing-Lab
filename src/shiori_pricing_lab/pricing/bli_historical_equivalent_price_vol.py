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
    BLIBondModifiedDuration,
    duration_type_for_basis,
)
from shiori_pricing_lab.pricing.bli_bond_option_price_basis import (
    BondOptionPriceBasis,
    require_bond_option_price_basis,
)

# The approved conversion, named. A different bridge -- a convexity term, a
# relative-vol round-trip, a DCF adjustment -- is a different version string.
EQUIVALENT_PRICE_VOL_METHODOLOGY_VERSION = "ANNEX_A_V1_4_A_8_6_DURATION_FIRST_ORDER_V1"

# The source mode this producer serves. Explicitly *not* VCUB_NORMAL_PROXY,
# DIRECT_PRICE_VOL or LOGNORMAL_YIELD_VOL_OVERRIDE.
BOND_VOL_SOURCE_MODE = HISTORICAL_YIELD_VOL_MO_SOURCE

# Carried on every result so a consumer never has to infer that this is a
# backward-looking statistic from the source name alone.
VOLATILITY_KIND = "HISTORICAL_REALIZED"


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

    # --- Duration lineage (full object, not a copied number) --------------
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

    # abs() on purpose and stated in the contract: a sign convention on D_B
    # must never be able to hand Black-76 a negative sigma.
    equivalent_price_vol = absolute_duration * sigma_hist_abs

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
