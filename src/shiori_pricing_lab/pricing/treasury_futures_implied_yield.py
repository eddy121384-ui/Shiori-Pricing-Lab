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
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date

from shiori_pricing_lab.data.treasury_futures_ctd import TreasuryFuturesCTD
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    TreasuryFuturesQuote,
    format_futures_quote,
    get_contract,
    parse_futures_quote,
    round_to_tick,
)
from shiori_pricing_lab.products.enums import DayCount, Frequency

#: Fixed by Issue #190's methodology anchor, and by U.S. Treasury convention.
TREASURY_COUPON_FREQUENCY = Frequency.SEMI_ANNUAL
TREASURY_COUPONS_PER_YEAR = 2
TREASURY_COUPON_PERIOD_MONTHS = 12 // TREASURY_COUPONS_PER_YEAR
TREASURY_DAY_COUNT = DayCount.ACT_ACT_BOND
TREASURY_PAR = 100.0

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
            "methodology": _methodology_payload(),
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
            "minimum_tick_label": contract.minimum_tick_label,
            "methodology": _methodology_payload(),
            "ctd": self.ctd.as_display_payload(),
        }


def _methodology_payload() -> dict[str, object]:
    """The convention every answer is stamped with -- never inferred by a consumer."""

    return {
        "basis": "CME_TREASURY_ANALYTICS_CTD_IMPLIED_FORWARD_YIELD",
        "settlement_date_rule": "FUTURES_CONTRACT_LAST_DELIVERY_DAY",
        "coupon_frequency": str(TREASURY_COUPON_FREQUENCY),
        "day_count": str(TREASURY_DAY_COUNT),
        "par": TREASURY_PAR,
        "carry_adjustment": "NONE",
    }


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


def coupon_period_bounds(settlement_date: date, maturity_date: date) -> tuple[date, date]:
    """Return the semiannual coupon dates bracketing ``settlement_date``.

    The grid is anchored on ``maturity_date`` and stepped backwards, which is
    how a Treasury's coupon dates are actually defined. Settlement exactly on
    a coupon date returns that date as the period start (zero accrued).
    """

    if settlement_date >= maturity_date:
        raise TreasuryFuturesYieldError(
            f"settlement date {settlement_date.isoformat()} must be before the CTD's "
            f"maturity {maturity_date.isoformat()}"
        )

    month_end = _is_month_end(maturity_date)
    next_coupon = maturity_date
    for step in range(1, _MAX_COUPON_PERIODS + 1):
        previous_coupon = _add_months(
            maturity_date, -TREASURY_COUPON_PERIOD_MONTHS * step, month_end=month_end
        )
        if previous_coupon <= settlement_date:
            return previous_coupon, next_coupon
        next_coupon = previous_coupon
    raise TreasuryFuturesYieldError(
        f"CTD maturity {maturity_date.isoformat()} is more than "
        f"{_MAX_COUPON_PERIODS // TREASURY_COUPONS_PER_YEAR} years after settlement "
        f"{settlement_date.isoformat()}"
    )


def remaining_coupon_dates(settlement_date: date, maturity_date: date) -> list[date]:
    """Coupon dates strictly after ``settlement_date``, up to and including maturity.

    Every date is measured from ``maturity_date``, exactly as
    :func:`coupon_period_bounds` measures its own -- never by stepping forward
    from one generated date to the next. Cumulative forward stepping drifts
    the moment a step lands on a shorter month (28 Feb from a 29th/30th/31st
    day-of-month anchor) and never recovers the anchor day, which would put a
    spurious extra coupon just before maturity. Re-anchoring cannot drift, and
    keeps this grid identical to the one accrued interest is prorated on.
    """

    coupon_period_bounds(settlement_date, maturity_date)  # validates the pair
    month_end = _is_month_end(maturity_date)
    dates = [maturity_date]
    for step in range(1, _MAX_COUPON_PERIODS + 1):
        coupon_date = _add_months(
            maturity_date, -TREASURY_COUPON_PERIOD_MONTHS * step, month_end=month_end
        )
        if coupon_date <= settlement_date:
            dates.reverse()
            return dates
        dates.append(coupon_date)
    raise TreasuryFuturesYieldError(
        f"CTD maturity {maturity_date.isoformat()} is more than "
        f"{_MAX_COUPON_PERIODS // TREASURY_COUPONS_PER_YEAR} years after settlement "
        f"{settlement_date.isoformat()}"
    )


def accrued_interest_per_100(
    settlement_date: date, maturity_date: date, coupon_percent: float
) -> float:
    """U.S. Treasury Actual/Actual (ISMA/Bond) accrued interest per 100 par."""

    previous_coupon, next_coupon = coupon_period_bounds(settlement_date, maturity_date)
    period_days = (next_coupon - previous_coupon).days
    elapsed_days = (settlement_date - previous_coupon).days
    coupon_amount = TREASURY_PAR * (coupon_percent / 100.0) / TREASURY_COUPONS_PER_YEAR
    return coupon_amount * elapsed_days / period_days


# --------------------------------------------------------------------------
# Clean price <-> yield to maturity
# --------------------------------------------------------------------------


def clean_price_from_yield(
    yield_percent: float,
    settlement_date: date,
    maturity_date: date,
    coupon_percent: float,
) -> float:
    """Treasury clean price per 100 from a semiannual-compounded YTM in percent."""

    previous_coupon, next_coupon = coupon_period_bounds(settlement_date, maturity_date)
    coupon_dates = remaining_coupon_dates(settlement_date, maturity_date)
    if len(coupon_dates) < 2:
        raise TreasuryFuturesYieldError(
            f"settlement {settlement_date.isoformat()} is inside the CTD's final coupon "
            f"period (maturity {maturity_date.isoformat()}). The U.S. Treasury street "
            "convention discounts a single remaining coupon with simple interest, which "
            "this module does not implement, so no yield is reported rather than a "
            "compounded approximation of one."
        )

    period_yield = (yield_percent / 100.0) / TREASURY_COUPONS_PER_YEAR
    if period_yield <= -1.0:
        raise TreasuryFuturesYieldError(
            f"yield {yield_percent}% is too negative to discount semiannually"
        )

    period_days = (next_coupon - previous_coupon).days
    first_exponent = (next_coupon - settlement_date).days / period_days
    coupon_amount = TREASURY_PAR * (coupon_percent / 100.0) / TREASURY_COUPONS_PER_YEAR

    dirty_price = 0.0
    for index, coupon_date in enumerate(coupon_dates):
        cashflow = coupon_amount
        if coupon_date == maturity_date:
            cashflow += TREASURY_PAR
        dirty_price += cashflow / (1.0 + period_yield) ** (first_exponent + index)

    return dirty_price - accrued_interest_per_100(
        settlement_date, maturity_date, coupon_percent
    )


def yield_from_clean_price(
    clean_price: float,
    settlement_date: date,
    maturity_date: date,
    coupon_percent: float,
) -> float:
    """Semiannual-compounded YTM in percent from a Treasury clean price per 100.

    Bisection, because clean price is strictly decreasing in yield over the
    bracket: it cannot diverge, needs no derivative, and converges to full
    double precision in a fixed, deterministic number of steps.
    """

    if clean_price <= 0:
        raise TreasuryFuturesYieldError(f"clean price must be positive, got {clean_price}")

    def residual(yield_percent: float) -> float:
        return (
            clean_price_from_yield(yield_percent, settlement_date, maturity_date, coupon_percent)
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
    accrued = accrued_interest_per_100(
        settlement_date, ctd.ctd_maturity_date, ctd.ctd_coupon_percent
    )
    implied_yield_percent = yield_from_clean_price(
        clean_price, settlement_date, ctd.ctd_maturity_date, ctd.ctd_coupon_percent
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
    clean_price = clean_price_from_yield(
        float(target_yield_percent),
        settlement_date,
        ctd.ctd_maturity_date,
        ctd.ctd_coupon_percent,
    )
    if clean_price <= 0:
        raise TreasuryFuturesYieldError(
            f"target yield {target_yield_percent}% implies a non-positive CTD clean price"
        )
    accrued = accrued_interest_per_100(
        settlement_date, ctd.ctd_maturity_date, ctd.ctd_coupon_percent
    )
    price = futures_price_from_clean_price(clean_price, ctd.conversion_factor)
    contract = get_contract(ctd.contract_code)
    return TreasuryFuturesPriceFromYield(
        ctd=ctd,
        target_yield_percent=float(target_yield_percent),
        settlement_date=settlement_date,
        accrued_interest=accrued,
        dirty_price=clean_price + accrued,
        converted_clean_price=clean_price,
        futures_price=price,
        exchange_price=round_to_tick(contract.code, price),
        exchange_quote=format_futures_quote(contract.code, price),
        minimum_tick=contract.minimum_tick,
    )
