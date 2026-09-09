"""CME-style CTD implied forward yield for U.S. Treasury futures (Issue #190).

The one canonical calculation path for the desk's futures <-> yield utility.
Both directions the desk asks for live here, and nothing else re-implements
them -- the Workbench API route and the browser panel both call this module,
so a Python answer and a screen answer cannot drift apart.

**Methodology anchor (Issue #190's RED contract): the CME Treasury Analytics
``Yield`` definition.** The implied yield is the ordinary U.S. Treasury
yield-to-maturity of the *current CTD cash security*, priced forward to the
futures contract's delivery:

- settlement date = the futures contract's **last delivery day**
- maturity, coupon = the **current CTD's** maturity and annual coupon
- converted clean price = ``futures price x CTD conversion factor``
- semiannual coupons, U.S. Treasury Actual/Actual (ISMA/Bond basis), par 100
- accrued interest is computed once, from the same coupon period the
  discounting uses, and applied consistently on both the clean -> dirty and
  dirty -> clean legs

This is **not** the on-the-run cash yield, a par-curve yield, or a curve
point, and this module never computes any of those. There is also **no
net-basis, repo or carry adjustment anywhere in it** -- deliberately, not by
omission. Issue #190 requires that such an adjustment must never silently
alter the primary answer; the smallest way to guarantee that is not to have
one, so a futures price maps to exactly one implied yield and back.

**Actual/Actual (ISMA/Bond), stated as formulas, because that is the whole
convention.** With ``prev``/``next`` the coupon dates bracketing settlement,
``c`` the semiannual coupon amount per 100, ``y`` the semiannual yield, and
``w = (next - settlement) / (next - prev)`` in actual days::

    accrued = c * (settlement - prev) / (next - prev)
    dirty   = sum_i  cf_i / (1 + y)^(w + i)      i = 0, 1, ... over the
                                                 remaining coupon dates
    clean   = dirty - accrued

Every period contributes exactly one unit of the exponent regardless of its
actual day count -- that is exactly what makes this ISMA/Bond rather than
ISDA, and it matches the ``DayCount.ACT_ACT_BOND`` member this repository
already defines for U.S. Treasury accrual (see
``pricing/bli_bond_convention_profile.UST_CONVENTION_PROFILE``, whose day
count is the same convention, and ``pricing/bli_quantlib_bond_adapter``'s
own ``_day_counter`` note on why ISDA is not a substitute).

**Why this does not call ``bli_quantlib_bond_adapter`` (Issue #190 asks for
current reviewed schedule/accrual machinery to be reused where feasible).**
That adapter is the right module for a BLI bond and is deliberately left
untouched, but it cannot answer this question:

1. It needs a full ``BondReferenceData`` -- ``issue_date``,
   ``first_coupon_date``, ``last_coupon_date``, frequency, day count. A CTD
   record carries only coupon, maturity and conversion factor, which is all
   Bloomberg/CME publish for the delivery basket; inventing the missing
   schedule anchors to satisfy the adapter's regular-grid validation would be
   fabricating reference data.
2. ``coupon_flows_before`` raises ``BLIBondMaturityCashflowUnsupportedError``
   for any window reaching maturity, and a yield-to-maturity needs precisely
   the maturity flow (final coupon plus redemption) that adapter refuses to
   model.
3. It is import-guarded on QuantLib, an optional ``quant`` extra. A desk
   utility whose answer disappears unless an optional dependency is installed
   is not a desk utility.

What *is* reused is the convention itself and its vocabulary
(``Frequency.SEMI_ANNUAL``, ``DayCount.ACT_ACT_BOND``), recorded on every
result so no consumer can show a yield without the convention that produced
it -- plus the schedule rule the adapter already establishes for month-end
bonds, restated below for the one grid this module needs.

**Month-end coupon grids are handled, and matter here.** A ZT CTD is a
2-year note, and 2-year notes mature on the last calendar day of a month. If
the maturity is a month end, every coupon date is that month's last calendar
day (31 Aug -> 28/29 Feb), not "the same day number, clamped". Getting this
wrong moves accrued interest and the first discount exponent on most ZT CTDs.
Coupon dates are unadjusted calendar dates, exactly as
``bli_quantlib_bond_adapter`` generates them: Treasury accrual convention
stops on the nominal date, and no business-day calendar is consulted here.

**Fails closed, never approximates.** A CTD record missing any of coupon,
maturity, conversion factor or last delivery date is rejected by
``data/treasury_futures_ctd`` before it reaches this module. A settlement
date inside the CTD's final coupon period is rejected here rather than
silently priced with compound discounting, because the U.S. Treasury street
convention switches to simple interest for a single remaining coupon and
this module does not implement that convention. No contract in scope can
reach it -- the shortest CTD in the basket still has years of coupons after
delivery -- so this is a guard, not a limitation of the utility.

**Eurex German leg (Issue #204).** ``FGBS``/``FGBM``/``FGBL``/``FGBX`` run
the same invariant -- futures price x conversion factor -> CTD clean price
-> market-correct yield -- priced on the registered ``GERMAN_GOVT`` bond
convention profile (annual, ACT/ACT), resolved explicitly through
``_resolve_pricing_policy``: contract -> market -> registered profile ->
frequency/day count -> shared yield engine. There is one code path,
parameterized by the policy, never a German copy of the math; U.S. Treasury
semiannual assumptions are never applied to a German CTD. The methodology
stamp on every answer names the market, the ``GERMAN_GOVT`` profile and the
Shiori-owned basis so no consumer can mistake one leg's convention for the
other's -- the complete German calculation is Shiori's, reconciled against
Bloomberg/Eurex analytics as UAT, not Eurex's own methodology.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date

from shiori_pricing_lab.data.treasury_futures_ctd import TreasuryFuturesCTD
from shiori_pricing_lab.pricing.bli_bond_convention_profile import get_convention_profile
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    MARKET_EUREX_GERMAN,
    TreasuryFuturesQuote,
    format_futures_quote,
    get_contract,
    parse_futures_quote,
    round_to_tick,
)
from shiori_pricing_lab.products.enums import DayCount, Frequency

#: UST semiannual coupons per year. This is the default grid for the shared
#: math functions below; the production path always resolves the authoritative
#: value from the registered convention profile (see ``_resolve_pricing_policy``).
TREASURY_COUPONS_PER_YEAR = 2
TREASURY_PAR = 100.0

#: German government CTDs pay annual coupons. Like ``TREASURY_COUPONS_PER_YEAR``,
#: this is referenced through the registered ``GERMAN_GOVT`` profile, never
#: applied by market-name checks in the engine.
EUREX_COUPONS_PER_YEAR = 1

#: Bond convention profile selected by market, recorded on every answer so no
#: consumer can show a yield without the convention that produced it.
BOND_CONVENTION_PROFILE_BY_MARKET = {
    "UST": "UST",
    MARKET_EUREX_GERMAN: "GERMAN_GOVT",
}

#: Methodology basis by market. Neither leg attributes the complete yield
#: algorithm to the venue: the UST basis names the long-standing CME Treasury
#: Analytics anchor (Issue #190's RED contract); the Eurex leg is Shiori's own
#: German-CTD calculation reconciled against Bloomberg/Eurex analytics as UAT,
#: so the basis names Shiori, not Eurex, as the methodology owner.
METHODOLOGY_BASIS_BY_MARKET = {
    "UST": "CME_TREASURY_ANALYTICS_CTD_IMPLIED_FORWARD_YIELD",
    MARKET_EUREX_GERMAN: "SHIORI_EUREX_GERMAN_CTD_IMPLIED_FORWARD_YIELD",
}

#: Methodology note by market, served by the workbench catalogue so the panel
#: can state the selected contract's convention without hard-coding copy.
#: The UST sentence is the panel's long-standing subtitle, verbatim.
METHODOLOGY_NOTE_BY_MARKET = {
    "UST": (
        "CME Treasury Analytics methodology: the CTD\u2019s yield to maturity, settled on the "
        "contract\u2019s last delivery day, from futures price \u00d7 conversion factor. "
        "Semiannual, U.S. Treasury Actual/Actual, par 100. "
        "No net-basis, repo or carry adjustment."
    ),
    MARKET_EUREX_GERMAN: (
        "Shiori Eurex German CTD implied forward yield: the CTD\u2019s yield to maturity, "
        "settled on the contract\u2019s last delivery day, from futures price \u00d7 "
        "conversion factor. Annual, ACT/ACT, par 100. "
        "No net-basis, repo or carry adjustment."
    ),
}


#: Coupon frequencies the shared yield engine implements, keyed by the
#: registered convention profile's frequency. Anything else (quarterly,
#: monthly, ...) fails closed: the engine has no grid for it, and silently
#: pricing it on an annual or semiannual grid would be a wrong answer.
_COUPONS_PER_YEAR_BY_FREQUENCY = {
    Frequency.ANNUAL: EUREX_COUPONS_PER_YEAR,
    Frequency.SEMI_ANNUAL: TREASURY_COUPONS_PER_YEAR,
}


@dataclass(frozen=True)
class FuturesPricingPolicy:
    """Authoritative pricing convention for one futures contract's CTD leg.

    Resolved as contract -> market -> registered bond convention profile
    (``pricing/bli_bond_convention_profile``), never hard-coded per market:
    the frequency and day count the engine prices on are read off the
    registered profile and validated here. The shared deterministic yield
    solver is unchanged; this policy is about authoritative convention
    selection, not replacing the solver.
    """

    market: str
    convention_profile: str
    coupon_frequency: Frequency
    coupons_per_year: int
    day_count: DayCount


def _policy_for_market(market: str) -> FuturesPricingPolicy:
    """Resolve the pricing policy for ``market``, fail-closed.

    An unrecognized market raises instead of inheriting UST behavior; a
    profile with anything but exactly one supported frequency, or anything
    but actual/actual day count, raises instead of being priced on a grid
    the engine does not implement.
    """

    profile_name = BOND_CONVENTION_PROFILE_BY_MARKET.get(str(market))
    if profile_name is None:
        raise TreasuryFuturesYieldError(
            f"no futures pricing policy is registered for market {market!r} -- "
            f"registered: {', '.join(sorted(BOND_CONVENTION_PROFILE_BY_MARKET))}"
        )
    profile = get_convention_profile(profile_name)
    if len(profile.coupon_frequencies) != 1:
        raise TreasuryFuturesYieldError(
            f"convention profile {profile.name!r} names {len(profile.coupon_frequencies)} "
            "coupon frequencies -- the futures pricing path needs exactly one"
        )
    frequency = profile.coupon_frequencies[0]
    coupons_per_year = _COUPONS_PER_YEAR_BY_FREQUENCY.get(frequency)
    if coupons_per_year is None:
        raise TreasuryFuturesYieldError(
            f"convention profile {profile.name!r} uses coupon frequency {frequency!r}, "
            "which the futures yield engine does not implement"
        )
    if profile.day_count != DayCount.ACT_ACT_BOND:
        raise TreasuryFuturesYieldError(
            f"convention profile {profile.name!r} uses day count {profile.day_count!r} -- "
            "the futures yield engine implements actual/actual only"
        )
    return FuturesPricingPolicy(
        market=str(market),
        convention_profile=profile.name,
        coupon_frequency=frequency,
        coupons_per_year=coupons_per_year,
        day_count=profile.day_count,
    )


def _resolve_pricing_policy(contract_code: str) -> FuturesPricingPolicy:
    """Resolve the pricing policy for ``contract_code``'s market."""

    return _policy_for_market(get_contract(contract_code).market)


def _coupons_per_year(contract_code: str) -> int:
    """Coupons per year for ``contract_code``'s CTD, via the pricing policy."""

    return _resolve_pricing_policy(contract_code).coupons_per_year


def _street_convention_bond_phrase(coupons_per_year: int) -> str:
    """Bond class named by final-period refusal messages (naming, not method)."""

    if coupons_per_year == EUREX_COUPONS_PER_YEAR:
        return "German government bond"
    return "U.S. Treasury"


def _compounding_adverb(coupons_per_year: int) -> str:
    if coupons_per_year == EUREX_COUPONS_PER_YEAR:
        return "annually"
    return "semiannually"

# Bisection bracket for the yield solve, in decimal per annum. Deliberately
# far wider than any Treasury has ever traded so an unusual-but-real price
# still solves, and deliberately bounded so a nonsensical price fails with a
# clear message instead of iterating forever.
_YIELD_SOLVE_LOWER = -0.20
_YIELD_SOLVE_UPPER = 1.00
_YIELD_SOLVE_ITERATIONS = 200
# Stop once the bracket is narrower than this, in percent. 1e-12% is 1e-10 bp
# -- eight orders of magnitude tighter than Issue #190's 0.5 bp acceptance
# tolerance, and reached in ~50 halvings rather than the full 200.
_YIELD_SOLVE_TOLERANCE_PERCENT = 1e-12

# 30 years of semiannual coupons is 60; 200 leaves room for any Treasury that
# exists while still terminating on a corrupt maturity date.
_MAX_COUPON_PERIODS = 200


class TreasuryFuturesYieldError(ValueError):
    """The implied-yield calculation cannot be performed as specified.

    Covers a CTD whose contract code is not a supported futures contract, a
    last delivery date on or after the CTD's maturity, a settlement date
    inside the CTD's final coupon period, and a price/yield that cannot be
    solved inside the bracket above.
    """


@dataclass(frozen=True)
class TreasuryFuturesImpliedYield:
    """Workflow A: a futures price, and the CTD implied forward yield it means."""

    ctd: TreasuryFuturesCTD
    quote: TreasuryFuturesQuote
    settlement_date: date
    converted_clean_price: float
    accrued_interest: float
    dirty_price: float
    implied_yield_percent: float

    def as_payload(self) -> dict[str, object]:
        contract = get_contract(self.ctd.contract_code)
        return {
            "direction": "FUTURES_PRICE_TO_IMPLIED_YIELD",
            "contract_code": contract.code,
            "contract_name": contract.name,
            "futures_price": self.quote.decimal_price,
            "exchange_price": self.quote.exchange_price,
            "exchange_quote": self.quote.exchange_quote,
            "on_tick": self.quote.on_tick,
            "minimum_tick": self.quote.minimum_tick,
            "minimum_tick_label": contract.minimum_tick_label,
            "settlement_date": self.settlement_date.isoformat(),
            "converted_clean_price": self.converted_clean_price,
            "accrued_interest": self.accrued_interest,
            "dirty_price": self.dirty_price,
            "implied_yield_percent": self.implied_yield_percent,
            "methodology": _methodology_payload(_resolve_pricing_policy(self.ctd.contract_code)),
            "ctd": self.ctd.as_display_payload(),
        }


@dataclass(frozen=True)
class TreasuryFuturesPriceFromYield:
    """Workflow B: a target CTD implied forward yield, and the futures price it means."""

    ctd: TreasuryFuturesCTD
    target_yield_percent: float
    settlement_date: date
    accrued_interest: float
    dirty_price: float
    converted_clean_price: float
    futures_price: float
    exchange_price: float
    exchange_quote: str
    minimum_tick: float
    on_tick: bool

    def as_payload(self) -> dict[str, object]:
        contract = get_contract(self.ctd.contract_code)
        return {
            "direction": "TARGET_YIELD_TO_FUTURES_PRICE",
            "contract_code": contract.code,
            "contract_name": contract.name,
            "target_yield_percent": self.target_yield_percent,
            "settlement_date": self.settlement_date.isoformat(),
            "accrued_interest": self.accrued_interest,
            "dirty_price": self.dirty_price,
            "converted_clean_price": self.converted_clean_price,
            "futures_price": self.futures_price,
            "exchange_price": self.exchange_price,
            "exchange_quote": self.exchange_quote,
            "minimum_tick": self.minimum_tick,
            "on_tick": self.on_tick,
            "minimum_tick_label": contract.minimum_tick_label,
            "methodology": _methodology_payload(_resolve_pricing_policy(self.ctd.contract_code)),
            "ctd": self.ctd.as_display_payload(),
        }


def _methodology_payload(policy: FuturesPricingPolicy) -> dict[str, object]:
    """The convention every answer is stamped with -- never inferred by a consumer."""

    return {
        "basis": METHODOLOGY_BASIS_BY_MARKET[policy.market],
        "market": policy.market,
        "bond_convention_profile": policy.convention_profile,
        "settlement_date_rule": "FUTURES_CONTRACT_LAST_DELIVERY_DAY",
        "coupon_frequency": str(policy.coupon_frequency),
        "day_count": str(policy.day_count),
        "par": TREASURY_PAR,
        "carry_adjustment": "NONE",
    }


# --------------------------------------------------------------------------
# Irregular first coupon (ACT/ACT ICMA, Issue #204 YAS diagnostic)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IrregularFirstCoupon:
    """A bond's real first-coupon schedule, from live Bond Master evidence.

    ``accrual_start`` (``ISSUE_DT``) is the day the first coupon starts
    accruing from; ``first_coupon`` (``FIRST_CPN_DT``) the first actual
    coupon date. Both or neither -- a half schedule is refused wherever one
    is built, never completed by guessing.
    """

    accrual_start: date
    first_coupon: date


@dataclass(frozen=True)
class _FrameSegment:
    """One quasi-period slice of an irregular first coupon period.

    ``denominator_days`` is the actual day count of the nominal (regular)
    reference period this slice belongs to -- the determination period
    ending at the slice's end date. A stub ending at a quasi date shares
    that date's reference period, exactly as ACT/ACT ICMA attributes each
    accrued day to its own determination period.
    """

    start: date
    end: date
    denominator_days: int


@dataclass(frozen=True)
class _FirstCouponFrame:
    """The ICMA decomposition of one irregular first coupon period.

    The nominal (quasi) grid is the regular maturity-anchored grid; the
    first actual coupon covers ``accrual_start`` -> ``first_coupon`` as a
    run of ``segments`` (a stub slice plus whole nominal periods), each
    divided by its own reference-period day count. ``first_factor`` is the
    whole-first-coupon worth in regular coupons. Accrued inside the first
    period sums elapsed segment fractions; discounting runs on the nominal
    grid with the remaining fraction as the first exponent. A schedule
    whose first coupon is exactly one regular period after accrual start
    never becomes a frame at all -- the plain grid prices it identically.
    """

    accrual_start: date
    first_coupon: date
    nominal_prev: date
    segments: tuple[_FrameSegment, ...]
    first_factor: float
    later_coupons: tuple[date, ...]

    def elapsed_fraction(self, settlement_date: date) -> float:
        """Elapsed first-period fraction at ``settlement_date`` (ICMA sum)."""

        total = 0.0
        for segment in self.segments:
            if settlement_date >= segment.end:
                total += (segment.end - segment.start).days / segment.denominator_days
            elif settlement_date > segment.start:
                total += (
                    (settlement_date - segment.start).days / segment.denominator_days
                )
                break
            else:
                break
        return total


def _first_coupon_frame(
    settlement_date: date,
    maturity_date: date,
    coupons_per_year: int,
    schedule: IrregularFirstCoupon | None,
) -> _FirstCouponFrame | None:
    """Return the ICMA frame when ``settlement_date`` needs it, else ``None``.

    ``None`` means the plain maturity-anchored grid prices this settlement
    exactly: no schedule, or settlement at/after the first coupon (the frame
    only ever changes the first period). Anything structurally unusable --
    settlement before accrual start, a first coupon off the nominal grid, an
    accrual start at/after the first coupon -- fails closed.
    """

    if schedule is None or settlement_date >= schedule.first_coupon:
        return None
    accrual_start = schedule.accrual_start
    first_coupon = schedule.first_coupon
    if settlement_date < accrual_start:
        raise TreasuryFuturesYieldError(
            f"settlement date {settlement_date.isoformat()} is before the CTD's "
            f"accrual start {accrual_start.isoformat()} -- no accrued interest exists"
        )
    if not accrual_start < first_coupon:
        raise TreasuryFuturesYieldError(
            f"accrual start {accrual_start.isoformat()} must be before the first "
            f"coupon {first_coupon.isoformat()}"
        )

    period_months = 12 // coupons_per_year
    month_end = _is_month_end(maturity_date)
    # Nominal grid dates at or below the first coupon, newest first. Bounded
    # like every other grid walk in this module.
    grid_down: list[date] = [maturity_date]
    for step in range(1, _MAX_COUPON_PERIODS + 1):
        candidate = _add_months(maturity_date, -period_months * step, month_end=month_end)
        grid_down.append(candidate)
        if candidate <= accrual_start:
            break
    else:
        raise TreasuryFuturesYieldError(
            f"CTD maturity {maturity_date.isoformat()} is more than "
            f"{_MAX_COUPON_PERIODS // coupons_per_year} years after accrual start "
            f"{accrual_start.isoformat()}"
        )
    if first_coupon not in grid_down:
        raise TreasuryFuturesYieldError(
            f"first coupon {first_coupon.isoformat()} is not on the nominal "
            f"{coupons_per_year}-per-year grid anchored on maturity "
            f"{maturity_date.isoformat()} -- refusing to guess the reference period"
        )
    nominal_prev = max(candidate for candidate in grid_down if candidate < first_coupon)
    if accrual_start == nominal_prev and not any(
        accrual_start < candidate < first_coupon for candidate in grid_down
    ):
        # Exactly one regular period: the plain grid prices this identically,
        # so no frame -- seasoned and regular-first bonds never diverge.
        return None
    grid_up = sorted(candidate for candidate in grid_down if candidate > accrual_start)
    segments = tuple(
        _FrameSegment(
            start=previous,
            end=current,
            denominator_days=(
                current - max(candidate for candidate in grid_down if candidate < current)
            ).days,
        )
        for previous, current in zip([accrual_start, *grid_up[:-1]], grid_up, strict=True)
        if current <= first_coupon
    )
    return _FirstCouponFrame(
        accrual_start=accrual_start,
        first_coupon=first_coupon,
        nominal_prev=nominal_prev,
        segments=segments,
        first_factor=sum(
            (segment.end - segment.start).days / segment.denominator_days
            for segment in segments
        ),
        later_coupons=tuple(
            candidate for candidate in reversed(grid_down) if candidate > first_coupon
        ),
    )


# --------------------------------------------------------------------------
# Coupon grid (unadjusted calendar dates, month-end aware)
# --------------------------------------------------------------------------


def _is_month_end(value: date) -> bool:
    return value.day == calendar.monthrange(value.year, value.month)[1]


def _add_months(value: date, months: int, *, month_end: bool) -> date:
    total = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day if month_end else min(value.day, last_day))


def coupon_period_bounds(
    settlement_date: date,
    maturity_date: date,
    *,
    coupons_per_year: int = TREASURY_COUPONS_PER_YEAR,
    schedule: IrregularFirstCoupon | None = None,
) -> tuple[date, date]:
    """Return the coupon dates bracketing ``settlement_date``.

    The grid is anchored on ``maturity_date`` and stepped backwards, which is
    how a Treasury's coupon dates are actually defined. Settlement exactly on
    a coupon date returns that date as the period start (zero accrued).
    ``coupons_per_year`` selects the grid (2 for UST semiannual, 1 for Eurex
    German annual); callers resolving it from the contract keep every
    existing two-argument call on the UST grid. With ``schedule`` and
    settlement inside the first coupon period, the bracket is the nominal
    first period (quasi date, first actual coupon) the ICMA treatment
    discounts on.
    """

    if settlement_date >= maturity_date:
        raise TreasuryFuturesYieldError(
            f"settlement date {settlement_date.isoformat()} must be before the CTD's "
            f"maturity {maturity_date.isoformat()}"
        )

    frame = _first_coupon_frame(settlement_date, maturity_date, coupons_per_year, schedule)
    if frame is not None:
        return frame.nominal_prev, frame.first_coupon

    period_months = 12 // coupons_per_year
    month_end = _is_month_end(maturity_date)
    next_coupon = maturity_date
    for step in range(1, _MAX_COUPON_PERIODS + 1):
        previous_coupon = _add_months(maturity_date, -period_months * step, month_end=month_end)
        if previous_coupon <= settlement_date:
            return previous_coupon, next_coupon
        next_coupon = previous_coupon
    raise TreasuryFuturesYieldError(
        f"CTD maturity {maturity_date.isoformat()} is more than "
        f"{_MAX_COUPON_PERIODS // coupons_per_year} years after settlement "
        f"{settlement_date.isoformat()}"
    )


def remaining_coupon_dates(
    settlement_date: date,
    maturity_date: date,
    *,
    coupons_per_year: int = TREASURY_COUPONS_PER_YEAR,
    schedule: IrregularFirstCoupon | None = None,
) -> list[date]:
    """Coupon dates strictly after ``settlement_date``, up to and including maturity.

    Every date is measured from ``maturity_date``, exactly as
    :func:`coupon_period_bounds` measures its own -- never by stepping forward
    from one generated date to the next. Cumulative forward stepping drifts
    the moment a step lands on a shorter month (28 Feb from a 29th/30th/31st
    day-of-month anchor) and never recovers the anchor day, which would put a
    spurious extra coupon just before maturity. Re-anchoring cannot drift, and
    keeps this grid identical to the one accrued interest is prorated on.
    """

    coupon_period_bounds(  # validates the pair
        settlement_date, maturity_date, coupons_per_year=coupons_per_year, schedule=schedule
    )
    frame = _first_coupon_frame(settlement_date, maturity_date, coupons_per_year, schedule)
    if frame is not None:
        # The quasi date carries no cashflow: the first payment is the long
        # first coupon at ``first_coupon``, then the nominal grid resumes.
        return [frame.first_coupon, *frame.later_coupons]
    month_end = _is_month_end(maturity_date)
    dates = [maturity_date]
    period_months = 12 // coupons_per_year
    for step in range(1, _MAX_COUPON_PERIODS + 1):
        coupon_date = _add_months(maturity_date, -period_months * step, month_end=month_end)
        if coupon_date <= settlement_date:
            dates.reverse()
            return dates
        dates.append(coupon_date)
    raise TreasuryFuturesYieldError(
        f"CTD maturity {maturity_date.isoformat()} is more than "
        f"{_MAX_COUPON_PERIODS // coupons_per_year} years after settlement "
        f"{settlement_date.isoformat()}"
    )


def accrued_interest_per_100(
    settlement_date: date,
    maturity_date: date,
    coupon_percent: float,
    *,
    coupons_per_year: int = TREASURY_COUPONS_PER_YEAR,
    schedule: IrregularFirstCoupon | None = None,
) -> float:
    """Actual/actual accrued interest per 100 par for ``coupons_per_year``.

    UST semiannual ISMA/Bond and German government annual ACT/ACT share this
    shape: one coupon amount prorated by actual elapsed days over the actual
    period length. The period the proration uses is the same one the
    discounting below uses. Inside an irregular first coupon period the
    elapsed days run from the real accrual start over the nominal period
    (ACT/ACT ICMA), which is exactly the plain formula when the first
    coupon is regular.
    """

    frame = _first_coupon_frame(settlement_date, maturity_date, coupons_per_year, schedule)
    coupon_amount = TREASURY_PAR * (coupon_percent / 100.0) / coupons_per_year
    if frame is not None:
        return coupon_amount * frame.elapsed_fraction(settlement_date)
    previous_coupon, next_coupon = coupon_period_bounds(
        settlement_date, maturity_date, coupons_per_year=coupons_per_year
    )
    period_days = (next_coupon - previous_coupon).days
    elapsed_days = (settlement_date - previous_coupon).days
    return coupon_amount * elapsed_days / period_days


# --------------------------------------------------------------------------
# Clean price <-> yield to maturity
# --------------------------------------------------------------------------


def clean_price_from_yield(
    yield_percent: float,
    settlement_date: date,
    maturity_date: date,
    coupon_percent: float,
    *,
    coupons_per_year: int = TREASURY_COUPONS_PER_YEAR,
    schedule: IrregularFirstCoupon | None = None,
) -> float:
    """Clean price per 100 from a compounded YTM in percent.

    ``coupons_per_year`` selects semiannual (UST) or annual (Eurex German)
    compounding with the matching coupon amount and period grid. The UST
    two-argument behavior is unchanged. With ``schedule`` and settlement
    inside the first coupon period, the first cashflow is the genuine long
    first coupon discounted on the nominal grid (ACT/ACT ICMA).
    """

    frame = _first_coupon_frame(settlement_date, maturity_date, coupons_per_year, schedule)
    if frame is not None:
        return _clean_price_first_coupon_frame(
            yield_percent, settlement_date, maturity_date, coupon_percent,
            coupons_per_year, frame,
        )

    previous_coupon, next_coupon = coupon_period_bounds(
        settlement_date, maturity_date, coupons_per_year=coupons_per_year
    )
    coupon_dates = remaining_coupon_dates(
        settlement_date, maturity_date, coupons_per_year=coupons_per_year
    )
    if len(coupon_dates) < 2:
        raise TreasuryFuturesYieldError(
            f"settlement {settlement_date.isoformat()} is inside the CTD's final coupon "
            f"period (maturity {maturity_date.isoformat()}). The "
            f"{_street_convention_bond_phrase(coupons_per_year)} street "
            "convention discounts a single remaining coupon with simple interest, which "
            "this module does not implement, so no yield is reported rather than a "
            "compounded approximation of one."
        )

    period_yield = (yield_percent / 100.0) / coupons_per_year
    if period_yield <= -1.0:
        raise TreasuryFuturesYieldError(
            f"yield {yield_percent}% is too negative to discount "
            f"{_compounding_adverb(coupons_per_year)}"
        )

    period_days = (next_coupon - previous_coupon).days
    first_exponent = (next_coupon - settlement_date).days / period_days
    coupon_amount = TREASURY_PAR * (coupon_percent / 100.0) / coupons_per_year

    dirty_price = 0.0
    for index, coupon_date in enumerate(coupon_dates):
        cashflow = coupon_amount
        if coupon_date == maturity_date:
            cashflow += TREASURY_PAR
        dirty_price += cashflow / (1.0 + period_yield) ** (first_exponent + index)

    return dirty_price - accrued_interest_per_100(
        settlement_date, maturity_date, coupon_percent, coupons_per_year=coupons_per_year
    )


def _clean_price_first_coupon_frame(
    yield_percent: float,
    settlement_date: date,
    maturity_date: date,
    coupon_percent: float,
    coupons_per_year: int,
    frame: _FirstCouponFrame,
) -> float:
    """Clean price with the genuine long first coupon (ACT/ACT ICMA).

    The first cashflow is ``first_factor`` whole coupons at ``first_coupon``;
    every later cashflow is one regular coupon on the nominal grid, plus
    redemption at maturity. Discount exponents run on the nominal grid from
    settlement, exactly as the plain path does once past the first coupon.
    A frame whose only cashflow date is maturity itself is the single-payment
    case the plain path also refuses rather than pricing with simple
    interest it does not implement.
    """

    if not frame.later_coupons:
        raise TreasuryFuturesYieldError(
            f"settlement {settlement_date.isoformat()} is inside the CTD's final coupon "
            f"period (maturity {maturity_date.isoformat()}). The "
            f"{_street_convention_bond_phrase(coupons_per_year)} street "
            "convention discounts a single remaining coupon with simple interest, which "
            "this module does not implement, so no yield is reported rather than a "
            "compounded approximation of one."
        )

    period_yield = (yield_percent / 100.0) / coupons_per_year
    if period_yield <= -1.0:
        raise TreasuryFuturesYieldError(
            f"yield {yield_percent}% is too negative to discount "
            f"{_compounding_adverb(coupons_per_year)}"
        )

    coupon_amount = TREASURY_PAR * (coupon_percent / 100.0) / coupons_per_year
    first_exponent = frame.first_factor - frame.elapsed_fraction(settlement_date)
    dirty_price = frame.first_factor * coupon_amount / (1.0 + period_yield) ** first_exponent
    for index, coupon_date in enumerate(frame.later_coupons, start=1):
        cashflow = coupon_amount
        if coupon_date == maturity_date:
            cashflow += TREASURY_PAR
        dirty_price += cashflow / (1.0 + period_yield) ** (first_exponent + index)

    return dirty_price - coupon_amount * frame.elapsed_fraction(settlement_date)


def yield_from_clean_price(
    clean_price: float,
    settlement_date: date,
    maturity_date: date,
    coupon_percent: float,
    *,
    coupons_per_year: int = TREASURY_COUPONS_PER_YEAR,
    schedule: IrregularFirstCoupon | None = None,
) -> float:
    """Compounded YTM in percent from a clean price per 100.

    Bisection, because clean price is strictly decreasing in yield over the
    bracket: it cannot diverge, needs no derivative, and converges to full
    double precision in a fixed, deterministic number of steps.
    ``coupons_per_year`` must match the one the price was computed with, and
    ``schedule`` must match too -- bisection inverts whichever pricing leg
    ``clean_price_from_yield`` uses.
    """

    if clean_price <= 0:
        raise TreasuryFuturesYieldError(f"clean price must be positive, got {clean_price}")

    def residual(yield_percent: float) -> float:
        return (
            clean_price_from_yield(
                yield_percent,
                settlement_date,
                maturity_date,
                coupon_percent,
                coupons_per_year=coupons_per_year,
                schedule=schedule,
            )
            - clean_price
        )

    low_percent = _YIELD_SOLVE_LOWER * 100.0
    high_percent = _YIELD_SOLVE_UPPER * 100.0
    low_residual = residual(low_percent)
    high_residual = residual(high_percent)

    # A root sitting exactly *on* a bracket endpoint is returned here rather
    # than bisected for (Codex review, PR #191). The endpoint is the answer,
    # and the sign test below cannot see it: a zero residual is not greater
    # than zero, so `(low_residual > 0) == (mid_residual > 0)` reads a root at
    # the lower bound as "same side as the midpoint" and moves `low` past it,
    # discarding the very root it was asked for -- the observed symptom was
    # `yield_from_clean_price(clean_price_from_yield(-20, ...))` returning
    # +100%. Handling both endpoints also leaves the product test below
    # comparing two genuinely non-zero residuals, which is the only case it
    # is a correct bracket test for.
    if low_residual == 0.0:
        return low_percent
    if high_residual == 0.0:
        return high_percent
    if low_residual * high_residual > 0:
        raise TreasuryFuturesYieldError(
            f"clean price {clean_price} implies a yield outside {low_percent}%..."
            f"{high_percent}% for a {coupon_percent}% coupon maturing "
            f"{maturity_date.isoformat()}"
        )

    for _ in range(_YIELD_SOLVE_ITERATIONS):
        mid_percent = (low_percent + high_percent) / 2.0
        mid_residual = residual(mid_percent)
        if mid_residual == 0.0:
            return mid_percent
        if (low_residual > 0) == (mid_residual > 0):
            low_percent, low_residual = mid_percent, mid_residual
        else:
            high_percent, high_residual = mid_percent, mid_residual
        if high_percent - low_percent < _YIELD_SOLVE_TOLERANCE_PERCENT:
            break
    return (low_percent + high_percent) / 2.0


# --------------------------------------------------------------------------
# Futures price <-> CTD implied forward yield
# --------------------------------------------------------------------------


def converted_clean_price(futures_price: float, conversion_factor: float) -> float:
    """CTD clean price per 100 implied by a futures price: ``price x factor``."""

    if conversion_factor <= 0:
        raise TreasuryFuturesYieldError(
            f"conversion factor must be positive, got {conversion_factor}"
        )
    return futures_price * conversion_factor


def futures_price_from_clean_price(clean_price: float, conversion_factor: float) -> float:
    """The exact inverse of :func:`converted_clean_price`."""

    if conversion_factor <= 0:
        raise TreasuryFuturesYieldError(
            f"conversion factor must be positive, got {conversion_factor}"
        )
    return clean_price / conversion_factor


def _settlement_date(ctd: TreasuryFuturesCTD) -> date:
    get_contract(ctd.contract_code)  # rejects a CTD tagged with an unsupported contract
    if ctd.last_delivery_date >= ctd.ctd_maturity_date:
        raise TreasuryFuturesYieldError(
            f"last delivery date {ctd.last_delivery_date.isoformat()} must be before the "
            f"CTD's maturity {ctd.ctd_maturity_date.isoformat()}"
        )
    return ctd.last_delivery_date


def _first_coupon_schedule(ctd: TreasuryFuturesCTD) -> IrregularFirstCoupon | None:
    """The CTD's real first-coupon schedule, or ``None`` for the regular grid.

    Both dates or neither: a record naming only one end of the first period
    is refused rather than completed by guessing. ``None`` keeps the plain
    maturity-anchored grid -- the UST path and every seasoned bond.
    """

    accrual_start = ctd.first_accrual_start
    first_coupon = ctd.first_coupon_date
    if accrual_start is None and first_coupon is None:
        return None
    if accrual_start is None or first_coupon is None:
        raise TreasuryFuturesYieldError(
            f"CTD {ctd.ctd_identifier} carries a half first-coupon schedule -- "
            "accrual start and first coupon are required together, or neither"
        )
    return IrregularFirstCoupon(accrual_start=accrual_start, first_coupon=first_coupon)


def implied_yield_from_futures_price(
    ctd: TreasuryFuturesCTD, futures_price: str | int | float
) -> TreasuryFuturesImpliedYield:
    """Workflow A: futures price -> CTD implied forward yield.

    ``futures_price`` is anything :func:`parse_futures_quote` accepts for this
    contract -- a decimal level or a valid exchange quote. The yield is
    computed from the exact price entered, never from its tick-rounded
    display.
    """

    settlement_date = _settlement_date(ctd)
    quote = parse_futures_quote(ctd.contract_code, futures_price)
    clean_price = converted_clean_price(quote.decimal_price, ctd.conversion_factor)
    coupons_per_year = _coupons_per_year(ctd.contract_code)
    schedule = _first_coupon_schedule(ctd)
    accrued = accrued_interest_per_100(
        settlement_date, ctd.ctd_maturity_date, ctd.ctd_coupon_percent,
        coupons_per_year=coupons_per_year, schedule=schedule,
    )
    implied_yield_percent = yield_from_clean_price(
        clean_price, settlement_date, ctd.ctd_maturity_date, ctd.ctd_coupon_percent,
        coupons_per_year=coupons_per_year, schedule=schedule,
    )
    return TreasuryFuturesImpliedYield(
        ctd=ctd,
        quote=quote,
        settlement_date=settlement_date,
        converted_clean_price=clean_price,
        accrued_interest=accrued,
        dirty_price=clean_price + accrued,
        implied_yield_percent=implied_yield_percent,
    )


def futures_price_from_target_yield(
    ctd: TreasuryFuturesCTD, target_yield_percent: float
) -> TreasuryFuturesPriceFromYield:
    """Workflow B: target CTD implied forward yield -> futures price.

    Returns the raw decimal price alongside the nearest price the contract can
    actually trade at and that price's exchange quote, so the trader sees both
    the exact inverse and the level they would put in the market.
    """

    if (
        isinstance(target_yield_percent, bool)
        or not isinstance(target_yield_percent, (int, float))
        or not math.isfinite(target_yield_percent)
    ):
        # NaN in particular: it survives every comparison below and would
        # otherwise propagate silently into a NaN price rather than an error.
        raise TreasuryFuturesYieldError(
            f"target yield must be a finite number in percent, got {target_yield_percent!r}"
        )

    settlement_date = _settlement_date(ctd)
    coupons_per_year = _coupons_per_year(ctd.contract_code)
    schedule = _first_coupon_schedule(ctd)
    try:
        clean_price = clean_price_from_yield(
            float(target_yield_percent),
            settlement_date,
            ctd.ctd_maturity_date,
            ctd.ctd_coupon_percent,
            coupons_per_year=coupons_per_year,
            schedule=schedule,
        )
    except OverflowError as exc:
        # Extreme but finite yields (e.g. 1e308) can cause numerical overflow
        # in the pricing math. Translate to TreasuryFuturesYieldError so the
        # existing per-direction fail-visible behavior is preserved.
        raise TreasuryFuturesYieldError(
            f"target yield {target_yield_percent}% causes numerical overflow in pricing: {exc}"
        ) from exc
    if clean_price <= 0:
        raise TreasuryFuturesYieldError(
            f"target yield {target_yield_percent}% implies a non-positive CTD clean price"
        )
    accrued = accrued_interest_per_100(
        settlement_date, ctd.ctd_maturity_date, ctd.ctd_coupon_percent,
        coupons_per_year=coupons_per_year, schedule=schedule,
    )
    price = futures_price_from_clean_price(clean_price, ctd.conversion_factor)
    contract = get_contract(ctd.contract_code)
    exchange_price = round_to_tick(contract.code, price)
    # Use the same tolerance as _build_quote for consistency.
    on_tick = abs(exchange_price - price) <= contract.minimum_tick * 1e-8
    return TreasuryFuturesPriceFromYield(
        ctd=ctd,
        target_yield_percent=float(target_yield_percent),
        settlement_date=settlement_date,
        accrued_interest=accrued,
        dirty_price=clean_price + accrued,
        converted_clean_price=clean_price,
        futures_price=price,
        exchange_price=exchange_price,
        exchange_quote=format_futures_quote(contract.code, price),
        minimum_tick=contract.minimum_tick,
        on_tick=on_tick,
    )
