"""``calculate_bond_modified_duration``: current-time bond duration ``D_B``
for the Historical Yield Vol -> Equivalent Price Vol path (Issue #211).

**What this module is.** One canonical, deterministic producer of the bond
duration Annex A v1.4 §A.8.6 consumes::

    D_B = -(1 / P_dirty) x dP/dY

It is the *only* duration in this repository. It builds no cashflow engine of
its own: every price and yield comes from the already-reviewed price<->yield
primitive in :mod:`shiori_pricing_lab.pricing.treasury_futures_implied_yield`
(Issue #190's RED contract, ACT/ACT ISMA/Bond), and the settlement roll comes
from the already-reviewed
:func:`~shiori_pricing_lab.pricing.bli_bond_advanced_field_resolver.advance_settlement_business_days`
on the convention profile's own calendar.

**The approved contract (Trading Desk decision, Issue #211).** Every one of
these was decided by the owner after the Phase-1 audit, and none of them is
this module's choice to make:

- **Denominator is the DIRTY price.** ``P_dirty = P_clean + AI(tS)``.
  ``σ_P`` must describe the proportional volatility of the same price state
  the approved standalone production path actually prices Black-76 on --
  dirty forward and dirty strike, ratified in Issue #94 / PR #122. A clean
  denominator would be a basis mismatch *and* a coupon-cycle artifact: the
  Phase-1 audit measured the gap ranging from 0.011% just after a coupon to
  2.091% just before one, which is up to ~10.6 vol bp of ``σ_P`` on a single
  bond, arriving as a sawtooth with no market move behind it.
- **Two distinct dates, and they are not the same date.** The market state
  (the observed clean price) is taken at the pricing timestamp ``t0``; the
  bond analytics -- accrued interest, the coupon grid, the discounting -- run
  on the bond's current cash-bond **spot settlement date** ``tS``. Both are
  carried separately in the result so neither can be read as the other.
  ``tS`` is the *current* spot settlement, never the option expiry and never
  the forward settlement date: this is a current-time duration per
  authoritative §A.8.6, not the retired v1.3 "duration at expiry" wording.
- **1 bp central difference.** ``dP/dY`` is numerical, not analytic. The
  Phase-1 audit showed the answer stable to 8+ significant figures for every
  bump at or below 1 bp (1 bp / 0.1 bp / 0.01 bp / 0.001 bp agree), while a
  100 bp bump drifts ~0.14% on second-order leakage. Independently,
  QuantLib's analytic ``BondFunctions.duration(..., Duration.Modified, ...)``
  agrees with this construction to ~1e-6 -- see
  ``tests/test_bli_bond_modified_duration.py``, which pins that cross-check
  rather than leaving it as a claim in prose.

**Why the numerator may come from the clean-price function.** Accrued
interest on a given settlement date does not depend on the yield, so
``d(dirty)/dY == d(clean)/dY`` exactly. Bumping the clean price and dividing
by the dirty price is therefore not a mixed basis -- it is the dirty-basis
derivative, obtained from the one repriced leg that actually moves. The
result records both prices and both bumped prices so a reviewer can redo the
division without rerunning anything.

**Supported universe, fail-closed.** Only convention profiles whose stated
conventions the reusable price<->yield primitive *exactly* implements:

- ``UST`` -- SEMI_ANNUAL, ``ACT_ACT_BOND``, T+1. Exact match.
- ``GERMAN_GOVT`` -- ANNUAL, ``ACT_ACT_BOND``, T+2. Exact match via the
  Issue #204 ``coupons_per_year=1`` leg.

``US_CORPORATE`` is refused, and the refusal is not cosmetic: that profile is
``THIRTY_360``, the reusable primitive has no day-count parameter at all and
is hard-wired to ACT/ACT ISMA, so accepting it would silently price a
corporate on the wrong day count and report a duration nobody could tell was
wrong from its magnitude. The gate checks the profile's *day count* as well
as its name, so a future edit to a registered profile cannot slip a
non-ACT/ACT bond through an allowlist that still spells the same name.

**What this module deliberately is not.**

- It acquires nothing. The observed clean price is an argument; no Bloomberg
  session is opened and no field is requested. In particular **no Bloomberg
  duration field is read**: the Phase-1 audit found none confirmed in this
  repository, and ``DUR_ADJ_MID`` remains a candidate with none of its six
  required semantics pinned. A number that looks like a duration is not
  evidence that it is one (§A.8.1).
- It prices nothing and reaches no pricing chain. It produces a duration;
  ``pricing/bli_historical_equivalent_price_vol.py`` is what turns one into a
  volatility, and the raw ``YIELD_VOL`` basis stays refused by
  ``pricing/bli_mvp_required_input_guard.py`` regardless.
- It chooses no fallback. An unsupported profile, an unusable price, a
  settlement inside the final coupon period -- each is a refusal, never a
  substituted convention or an approximated number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from shiori_pricing_lab.pricing.bli_bond_advanced_field_resolver import (
    PROVENANCE_SHIORI_DERIVED,
    advance_settlement_business_days,
)
from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
    BLIConventionProfile,
    get_convention_profile,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
    EUREX_COUPONS_PER_YEAR,
    TREASURY_COUPONS_PER_YEAR,
    IrregularFirstCoupon,
    TreasuryFuturesYieldError,
    accrued_interest_per_100,
    clean_price_from_yield,
    yield_from_clean_price,
)
from shiori_pricing_lab.products.enums import DayCount, Frequency

# The one methodology token for this producer. It names the *approved
# contract*, not the module: a future duration on another basis or another
# bump is a different version string and therefore a different number in
# every audit trail that carries it.
DURATION_METHODOLOGY_VERSION = "SHIORI_DIRTY_MODIFIED_DURATION_V1"

# What the number is, spelled out. Recorded on every result because the
# Phase-1 audit found Macaulay (7.0841) sitting within 0.004 of a
# clean-denominator duration (7.0870) on a real bond: duration types are not
# distinguishable by magnitude and must never be identified that way.
DURATION_TYPE = "MODIFIED_DURATION_DIRTY_PRICE"
DURATION_PRICE_BASIS = "DIRTY"

# The approved derivative convention (Trading Desk decision, Issue #211).
YIELD_BUMP_BASIS_POINTS = 1.0
_BASIS_POINTS_PER_PERCENT = 100.0

# Only profiles the reusable ACT/ACT ISMA price<->yield primitive exactly
# implements. See the module docstring on why US_CORPORATE is absent.
SUPPORTED_DURATION_CONVENTION_PROFILES: tuple[str, ...] = ("UST", "GERMAN_GOVT")

# The one day count that primitive implements. Checked alongside the name so
# a profile edit cannot widen the gate silently.
_SUPPORTED_DURATION_DAY_COUNT = DayCount.ACT_ACT_BOND

_COUPONS_PER_YEAR_BY_FREQUENCY: dict[Frequency, int] = {
    Frequency.SEMI_ANNUAL: TREASURY_COUPONS_PER_YEAR,
    Frequency.ANNUAL: EUREX_COUPONS_PER_YEAR,
}


class BLIBondDurationError(ValueError):
    """One fail-closed refusal type for every condition in this module."""


@dataclass(frozen=True)
class BLIBondModifiedDuration:
    """One bond's current-time ``D_B``, with everything needed to redo it.

    Enough provenance to reproduce the value *and* the arithmetic: both
    dates, all three prices, the base and both bumped yields, both bumped
    prices, the derivative, and the signed and absolute duration. A reviewer
    holding only this object can recompute ``-(dP/dY) / P_dirty`` by hand and
    get the reported number back.

    ``modified_duration`` is signed and is negative for an ordinary bond
    (price falls as yield rises, so ``dP/dY < 0`` and ``-(dP/dY)/P > 0`` --
    the sign convention here reports the conventional *positive* modified
    duration). ``absolute_modified_duration`` is what §A.8.6's ``|D_B|``
    consumes; both are carried so a consumer never has to guess which one it
    is holding.
    """

    security: str
    convention_profile: str
    pricing_timestamp: str
    settlement_date: date
    maturity_date: date
    coupon_percent: float
    coupons_per_year: int
    day_count: str
    clean_price_per_100: float
    accrued_interest_per_100: float
    dirty_price_per_100: float
    base_yield_percent: float
    yield_bump_basis_points: float
    bumped_yield_up_percent: float
    bumped_yield_down_percent: float
    bumped_clean_price_up_per_100: float
    bumped_clean_price_down_per_100: float
    price_derivative_per_unit_yield: float
    modified_duration: float
    absolute_modified_duration: float
    duration_type: str
    price_basis: str
    source: str
    methodology_version: str
    calculated_at: str
    warnings: tuple[str, ...] = ()


def _require_finite_number(value: object, field_name: str) -> float:
    """Return ``value`` as a finite float, or refuse naming ``field_name``."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BLIBondDurationError(
            f"{field_name} must be a real number, got {value!r}"
        )
    try:
        converted = float(value)
    except (ValueError, OverflowError) as exc:
        raise BLIBondDurationError(f"{field_name} is not a usable number: {exc}") from exc
    if not math.isfinite(converted):
        raise BLIBondDurationError(f"{field_name} must be finite, got {converted!r}")
    return converted


def _require_date(value: object, field_name: str) -> date:
    """Return ``value`` as a ``date``, refusing a ``datetime`` masquerading."""

    # `datetime` is a `date` subclass, and one arriving here would carry a
    # time component that the coupon grid has no meaning for. Refused rather
    # than silently truncated: the caller decides which calendar day it means.
    if not isinstance(value, date) or isinstance(value, datetime):
        raise BLIBondDurationError(
            f"{field_name} must be a datetime.date (not a datetime), got {value!r}"
        )
    return value


def _require_supported_profile(profile: BLIConventionProfile) -> int:
    """Return ``profile``'s coupons per year, or refuse the profile.

    Three independent checks, because each one catches a different way a
    wrong bond reaches a duration: the profile must be on the allowlist, its
    day count must be the one the reusable primitive actually implements, and
    it must state exactly one coupon frequency that maps to a coupon count.
    """

    if profile.name not in SUPPORTED_DURATION_CONVENTION_PROFILES:
        raise BLIBondDurationError(
            f"convention profile {profile.name!r} has no approved duration convention in "
            f"this slice (supported: {SUPPORTED_DURATION_CONVENTION_PROFILES!r}). The "
            "reusable price<->yield primitive implements ACT/ACT ISMA only and takes no "
            "day-count argument, so a profile on another day count would be priced on the "
            "wrong convention and report a duration whose magnitude looks ordinary"
        )
    if profile.day_count is not _SUPPORTED_DURATION_DAY_COUNT:
        raise BLIBondDurationError(
            f"convention profile {profile.name!r} states day count "
            f"{profile.day_count.value}, but the reusable price<->yield primitive "
            f"implements {_SUPPORTED_DURATION_DAY_COUNT.value} only -- this profile cannot "
            "produce a duration without a second cashflow engine, which this slice does "
            "not build"
        )

    frequencies = tuple(dict.fromkeys(profile.coupon_frequencies))
    if len(frequencies) != 1:
        raise BLIBondDurationError(
            f"convention profile {profile.name!r} states {len(frequencies)} coupon "
            f"frequencies ({frequencies!r}); a duration needs exactly one coupon grid and "
            "this module does not pick between them"
        )
    coupons_per_year = _COUPONS_PER_YEAR_BY_FREQUENCY.get(frequencies[0])
    if coupons_per_year is None:
        raise BLIBondDurationError(
            f"convention profile {profile.name!r} states coupon frequency "
            f"{frequencies[0].value}, which the reusable price<->yield primitive has no "
            f"coupon grid for (supported: "
            f"{tuple(f.value for f in _COUPONS_PER_YEAR_BY_FREQUENCY)!r})"
        )
    return coupons_per_year


def spot_settlement_date(
    pricing_date: date, convention_profile: BLIConventionProfile
) -> date:
    """Return the bond's current cash-bond spot settlement ``tS``.

    The profile's own ``settlement_business_days`` rolled on the profile's own
    reviewed calendar, via the already-reviewed
    :func:`advance_settlement_business_days`. No calendar arithmetic and no
    holiday table is written here.
    """

    return advance_settlement_business_days(
        _require_date(pricing_date, "pricing_date"),
        convention_profile.settlement_business_days,
        convention_profile,
    )


def calculate_bond_modified_duration(
    *,
    security: str,
    convention_profile: str,
    clean_price_per_100: float,
    settlement_date: date,
    maturity_date: date,
    coupon_percent: float,
    pricing_timestamp: str,
    calculated_at: str,
    schedule: IrregularFirstCoupon | None = None,
) -> BLIBondModifiedDuration:
    """Return ``D_B = -(1 / P_dirty) x dP/dY`` for one bond at ``settlement_date``.

    ``clean_price_per_100`` is the market state observed at
    ``pricing_timestamp`` (``t0``); ``settlement_date`` is the bond's current
    cash-bond spot settlement (``tS``) that every cashflow calculation runs
    on. Use :func:`spot_settlement_date` to derive ``tS`` from a pricing date
    on the profile's own calendar.

    ``calculated_at`` is a required argument rather than a clock reading. No
    module under ``shiori_pricing_lab/pricing/`` may read the system clock --
    ``tests/test_pricing_engine.py`` enforces that directly over the
    package's source, scanning for the wall-clock call forms -- because a
    pricing result that silently depends on when it ran is not reproducible.
    The caller that owns the run supplies its timestamp, the same way
    ``time_to_expiry`` reaches Black-76 as an already-resolved number.

    ``schedule`` is passed through to the reusable price<->yield primitive
    unchanged, for a bond still inside an irregular first coupon period. It
    is not interpreted here.

    The five steps, each recorded on the result:

    1. ``base_yield = yield_from_clean_price(clean, tS, ...)``
    2. bump that yield by +/- 1 bp
    3. reprice clean at both bumped yields
    4. ``dP/dY`` = central difference, per **unit decimal** yield
    5. divide by ``P_dirty = clean + AI(tS)`` and negate

    Raises :class:`BLIBondDurationError` for every refusal: an unsupported or
    unregistered convention profile, a non-finite or non-positive price, a
    non-positive dirty price, a settlement date at or after maturity, a
    settlement inside the final coupon period (the reusable primitive's own
    refusal, re-raised on this module's error type), or any intermediate that
    cannot be represented as a finite number.
    """

    if not isinstance(security, str) or not security.strip():
        raise BLIBondDurationError(
            f"security is required and must name the bond this duration is for, got "
            f"{security!r}"
        )
    if not isinstance(pricing_timestamp, str) or not pricing_timestamp.strip():
        raise BLIBondDurationError(
            "pricing_timestamp is required and must record the market-state timestamp t0, "
            f"got {pricing_timestamp!r}"
        )
    if not isinstance(calculated_at, str) or not calculated_at.strip():
        raise BLIBondDurationError(
            "calculated_at is required and must be supplied by the caller -- no module "
            "under pricing/ reads the system clock, so this timestamp is never defaulted, "
            f"got {calculated_at!r}"
        )

    # Raises ValueError on a missing/blank/unregistered selection -- Shiori
    # never falls back to a default profile.
    profile = get_convention_profile(convention_profile)
    coupons_per_year = _require_supported_profile(profile)

    settlement = _require_date(settlement_date, "settlement_date")
    maturity = _require_date(maturity_date, "maturity_date")
    if settlement >= maturity:
        raise BLIBondDurationError(
            f"settlement date {settlement.isoformat()} is not before maturity "
            f"{maturity.isoformat()} for {security!r} -- a matured or same-day bond has no "
            "duration"
        )

    clean = _require_finite_number(clean_price_per_100, "clean_price_per_100")
    if not clean > 0:
        raise BLIBondDurationError(
            f"clean_price_per_100 must be positive, got {clean!r} for {security!r}"
        )
    coupon = _require_finite_number(coupon_percent, "coupon_percent")
    if coupon < 0:
        raise BLIBondDurationError(
            f"coupon_percent must be non-negative, got {coupon!r} for {security!r}"
        )

    # Every call into the reusable primitive is wrapped once, here: this
    # module promises one error type, and the primitive's own
    # TreasuryFuturesYieldError (settlement inside the final coupon period, a
    # price implying a yield outside the solve bracket, an unusable schedule)
    # must not escape under a name naming a futures contract.
    try:
        accrued = accrued_interest_per_100(
            settlement, maturity, coupon, coupons_per_year=coupons_per_year
        )
        base_yield = yield_from_clean_price(
            clean, settlement, maturity, coupon,
            coupons_per_year=coupons_per_year, schedule=schedule,
        )
        bump_percent = YIELD_BUMP_BASIS_POINTS / _BASIS_POINTS_PER_PERCENT
        yield_up = base_yield + bump_percent
        yield_down = base_yield - bump_percent
        price_up = clean_price_from_yield(
            yield_up, settlement, maturity, coupon,
            coupons_per_year=coupons_per_year, schedule=schedule,
        )
        price_down = clean_price_from_yield(
            yield_down, settlement, maturity, coupon,
            coupons_per_year=coupons_per_year, schedule=schedule,
        )
    except TreasuryFuturesYieldError as exc:
        raise BLIBondDurationError(
            f"no duration for {security!r} on the {profile.name} convention at settlement "
            f"{settlement.isoformat()}: {exc}"
        ) from exc

    accrued = _require_finite_number(accrued, "accrued_interest_per_100")
    if accrued < 0:
        raise BLIBondDurationError(
            f"accrued interest is {accrued!r} for {security!r} at settlement "
            f"{settlement.isoformat()} -- a negative accrual is not a state this duration "
            "convention models"
        )
    dirty = clean + accrued
    if not dirty > 0:
        raise BLIBondDurationError(
            f"dirty price is {dirty!r} for {security!r} (clean {clean!r} + accrued "
            f"{accrued!r}) -- the approved D_B denominator must be positive"
        )

    base_yield = _require_finite_number(base_yield, "base_yield_percent")
    price_up = _require_finite_number(price_up, "bumped_clean_price_up_per_100")
    price_down = _require_finite_number(price_down, "bumped_clean_price_down_per_100")

    # Central difference. The x100 takes the derivative from "per yield
    # percentage point" -- the unit the reusable primitive's yields are in --
    # to "per unit decimal yield", which is the unit A.8.6's D_B is stated in
    # and the unit the #197 DECIMAL_ANNUAL volatility multiplies against.
    # Getting this factor wrong is a 100x error in sigma_P, so it is applied
    # once, here, and never again downstream.
    derivative = (price_up - price_down) / (2.0 * bump_percent) * _BASIS_POINTS_PER_PERCENT
    derivative = _require_finite_number(derivative, "price_derivative_per_unit_yield")

    duration = -derivative / dirty
    duration = _require_finite_number(duration, "modified_duration")
    if duration == 0.0:
        raise BLIBondDurationError(
            f"the modified duration of {security!r} at settlement {settlement.isoformat()} "
            "is zero -- a zero duration converts every Historical Yield Vol to a zero "
            "price vol, which is not a volatility this path may publish"
        )

    return BLIBondModifiedDuration(
        security=security,
        convention_profile=profile.name,
        pricing_timestamp=pricing_timestamp,
        settlement_date=settlement,
        maturity_date=maturity,
        coupon_percent=coupon,
        coupons_per_year=coupons_per_year,
        day_count=profile.day_count.value,
        clean_price_per_100=clean,
        accrued_interest_per_100=accrued,
        dirty_price_per_100=dirty,
        base_yield_percent=base_yield,
        yield_bump_basis_points=YIELD_BUMP_BASIS_POINTS,
        bumped_yield_up_percent=yield_up,
        bumped_yield_down_percent=yield_down,
        bumped_clean_price_up_per_100=price_up,
        bumped_clean_price_down_per_100=price_down,
        price_derivative_per_unit_yield=derivative,
        modified_duration=duration,
        absolute_modified_duration=abs(duration),
        duration_type=DURATION_TYPE,
        price_basis=DURATION_PRICE_BASIS,
        source=PROVENANCE_SHIORI_DERIVED,
        methodology_version=DURATION_METHODOLOGY_VERSION,
        calculated_at=calculated_at,
    )
