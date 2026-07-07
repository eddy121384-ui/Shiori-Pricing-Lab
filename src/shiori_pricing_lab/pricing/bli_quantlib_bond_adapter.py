"""BLI QuantLib bond-mechanics adapter (docs/29 implementation slice).

Scope, per `docs/29_bli_quantlib_bond_adapter_preflight.md` §2/§8:
QuantLib is used for bond mechanics only -- regular coupon schedule
generation, per-100 coupon cashflow amounts, and accrued interest at
one explicit, caller-supplied date. **No curve, no discount factor, no
forward clean price, no yield-to-price conversion, no volatility, no
Black-76, no option PV, and no Greeks are computed, imported, or read
here.** QuantLib never prices anything in this module.

**Calendar (docs/29 §2):** coupon dates are unadjusted -- schedule
generation uses `ql.NullCalendar()` regardless of
`BondReferenceData.business_day_convention`. Calendar-adjusted payment
dates are out of scope until a separate, reviewed calendar-source
contract exists.

**Coupon amount (docs/29 §5):** a fixed per-100-face amount,
`coupon * 100 / periods_per_year` -- never scaled by
`redemption_amount`, which this module never reads for coupon-flow
purposes. `redemption_amount` is reserved for a future
principal/redemption slice this module does not implement.

**Day-count mapping (docs/29 §4):** `DayCount.ACT_ACT_ISDA` maps
literally to `ql.ActualActual(ql.ActualActual.ISDA)` -- never to the
ISMA/bond-basis variant. No new `DayCount` member is introduced here.

**Irregular stubs (docs/29 §5):** an irregular first or last coupon
period (detected by comparing `first_coupon_date`/`last_coupon_date`
against the unadjusted regular-frequency-implied dates) raises
`BLIBondScheduleError` -- the regular-coupon-period formula above is
never silently applied to a bond with a real stub.

**Ex-dividend window (docs/29 §6):** `accrued_interest_per_100` raises
`BLIBondExDividendWindowError` for any `as_of_date` inside a bond's
ex-dividend window (`ex_dividend_days` calendar days immediately before
a coupon date). Negative accrued interest is not implemented by this
slice.

**No system clock (docs/29 §2):** every date this module touches is an
explicit caller-supplied ISO date string on `BondReferenceData` or a
function argument -- the current wall-clock date is never read, and
`ql.Settings.instance().evaluationDate` is never set from it either;
`ql.DayCounter.yearFraction` and `ql.Schedule` construction need no
evaluation date at all.

**Not wired into anything (docs/29 §8/§9):** this module is not
imported by, and does not change the behavior of,
`pricing/bli_pricing_engine.py::price_bli_mvp`, and it does not import
`BLIMVPInputBundle`, `BLIMarketDataSnapshot`, `PricingResult`, or any
of the curve/discount-factor chain (`bli_curve_tenor`,
`bli_curve_selector`, `bli_zero_curve_nodes`,
`bli_zero_rate_interpolation`, `bli_discount_factor`,
`bli_curve_discount_factor`, `bli_valuation_time`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from shiori_pricing_lab.products.enums import DayCount, Frequency
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData

try:
    import QuantLib as ql
except ImportError:  # QuantLib is optional -- pyproject.toml [project.optional-dependencies].quant
    ql = None


class BLIQuantLibNotAvailableError(RuntimeError):
    """Raised when a QuantLib-backed function is called without QuantLib installed."""


class BLIBondScheduleError(ValueError):
    """Raised for a bond whose first/last coupon period is not regular (docs/29 §5).

    This adapter's coupon-amount formula and accrued-interest calculation
    are both valid only for a regular coupon schedule; an irregular stub
    is refused rather than approximated.
    """


class BLIBondExDividendWindowError(ValueError):
    """Raised for an `as_of_date` inside a bond's ex-dividend window (docs/29 §6).

    Negative accrued interest during an ex-dividend period is not
    implemented by this adapter slice.
    """


@dataclass(frozen=True)
class BLIBondCouponFlow:
    """One scheduled coupon payment, per 100 face (docs/29 §5/§8).

    `amount_per_100` is a fixed, day-count-independent amount
    (`coupon * 100 / periods_per_year`) -- it is never derived from
    `redemption_amount` and never varies by the actual calendar length
    of the period it falls in.
    """

    payment_date: str
    amount_per_100: float


_ISO_DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}")

_FREQUENCY_MONTHS = {
    Frequency.MONTHLY: 1,
    Frequency.QUARTERLY: 3,
    Frequency.SEMI_ANNUAL: 6,
    Frequency.ANNUAL: 12,
}


def is_quantlib_available() -> bool:
    """Return whether the optional QuantLib dependency is importable."""

    return ql is not None


def _require_quantlib() -> None:
    if ql is None:
        raise BLIQuantLibNotAvailableError(
            "QuantLib is not installed -- install the optional 'quant' dependency group "
            '(pip install "shiori-pricing-lab[quant]") to use this function'
        )


def _parse_iso_date(value: str, field_name: str) -> date:
    if not isinstance(value, str) or not _ISO_DATE_SHAPE.fullmatch(value):
        raise ValueError(f"{field_name} must be an ISO date string (YYYY-MM-DD), got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO date string (YYYY-MM-DD), got {value!r}"
        ) from exc


def _add_months(value: date, months: int) -> date:
    # Mirrors pricing/schedule.py's own unadjusted month-stepping arithmetic
    # (duplicated locally so this module has no import relationship with
    # the vanilla-rates-core schedule module) -- does not support stepping
    # onto a day-of-month that does not exist in the target month.
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    try:
        return date(year, month, value.day)
    except ValueError as exc:
        raise ValueError(
            "bond schedule generation does not support month-end date adjustment"
        ) from exc


def _coupon_period_months(frequency: Frequency) -> int:
    try:
        return _FREQUENCY_MONTHS[frequency]
    except KeyError as exc:
        raise ValueError(
            f"unsupported coupon_frequency for bond schedule generation: {frequency!r}"
        ) from exc


def _check_regular_schedule(
    bond: BondReferenceData,
    *,
    issue: date,
    maturity: date,
    first_coupon: date,
    last_coupon: date,
    months: int,
) -> None:
    expected_first = _add_months(issue, months)
    expected_last = _add_months(maturity, -months)
    if first_coupon != expected_first or last_coupon != expected_last:
        raise BLIBondScheduleError(
            f"bond {bond.isin!r} has an irregular first/last coupon period, which this "
            "adapter slice does not support (no stub approximation is computed): expected "
            f"first_coupon_date {expected_first.isoformat()!r} (got "
            f"{bond.first_coupon_date!r}), expected last_coupon_date "
            f"{expected_last.isoformat()!r} (got {bond.last_coupon_date!r})"
        )


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _from_ql_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


def _schedule_dates(issue: date, maturity: date, months: int) -> tuple[date, ...]:
    tenor = ql.Period(months, ql.Months)
    calendar = ql.NullCalendar()
    schedule = ql.Schedule(
        _to_ql_date(issue),
        _to_ql_date(maturity),
        tenor,
        calendar,
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    return tuple(_from_ql_date(d) for d in schedule)


def _day_counter(day_count: DayCount) -> ql.DayCounter:
    if day_count is DayCount.ACT_360:
        return ql.Actual360()
    if day_count is DayCount.ACT_365_FIXED:
        return ql.Actual365Fixed()
    if day_count is DayCount.THIRTY_360:
        return ql.Thirty360(ql.Thirty360.BondBasis)
    if day_count is DayCount.ACT_ACT_ISDA:
        return ql.ActualActual(ql.ActualActual.ISDA)
    raise ValueError(f"unsupported day_count for QuantLib mapping: {day_count!r}")


def coupon_flows_before(
    bond: BondReferenceData,
    *,
    after_date: str,
    on_or_before_date: str,
) -> tuple[BLIBondCouponFlow, ...]:
    """Return coupon flows with payment date in `(after_date, on_or_before_date]`.

    Only regular coupon dates strictly between `first_coupon_date` and
    `last_coupon_date` (inclusive of both) are ever returned -- the final
    coupon-at-maturity event (which combines with principal redemption)
    is out of scope for this slice (docs/29 §5/§8) and is never included,
    regardless of the requested window. Raises `BLIBondScheduleError` for
    a bond with an irregular first/last coupon period.
    """

    _require_quantlib()
    if not isinstance(bond, BondReferenceData):
        raise TypeError(f"bond must be a BondReferenceData, got {type(bond).__name__}")

    issue = _parse_iso_date(bond.issue_date, "issue_date")
    maturity = _parse_iso_date(bond.maturity_date, "maturity_date")
    first_coupon = _parse_iso_date(bond.first_coupon_date, "first_coupon_date")
    last_coupon = _parse_iso_date(bond.last_coupon_date, "last_coupon_date")
    months = _coupon_period_months(bond.coupon_frequency)
    _check_regular_schedule(
        bond,
        issue=issue,
        maturity=maturity,
        first_coupon=first_coupon,
        last_coupon=last_coupon,
        months=months,
    )

    after = _parse_iso_date(after_date, "after_date")
    on_or_before = _parse_iso_date(on_or_before_date, "on_or_before_date")
    if on_or_before <= after:
        raise ValueError(
            f"on_or_before_date ({on_or_before_date!r}) must be after "
            f"after_date ({after_date!r})"
        )

    schedule_dates = _schedule_dates(issue, maturity, months)
    coupon_dates = [d for d in schedule_dates[1:] if first_coupon <= d <= last_coupon]

    periods_per_year = 12 // months
    amount_per_100 = bond.coupon * 100 / periods_per_year

    return tuple(
        BLIBondCouponFlow(payment_date=d.isoformat(), amount_per_100=amount_per_100)
        for d in coupon_dates
        if after < d <= on_or_before
    )


def accrued_interest_per_100(bond: BondReferenceData, *, as_of_date: str) -> float:
    """Return accrued interest per 100 face at the explicit `as_of_date`.

    `as_of_date` must lie in `[issue_date, maturity_date]`. Raises
    `BLIBondScheduleError` for a bond with an irregular first/last coupon
    period, and `BLIBondExDividendWindowError` if `as_of_date` falls
    inside the bond's ex-dividend window -- negative accrued interest is
    not implemented by this slice.
    """

    _require_quantlib()
    if not isinstance(bond, BondReferenceData):
        raise TypeError(f"bond must be a BondReferenceData, got {type(bond).__name__}")

    issue = _parse_iso_date(bond.issue_date, "issue_date")
    maturity = _parse_iso_date(bond.maturity_date, "maturity_date")
    first_coupon = _parse_iso_date(bond.first_coupon_date, "first_coupon_date")
    last_coupon = _parse_iso_date(bond.last_coupon_date, "last_coupon_date")
    months = _coupon_period_months(bond.coupon_frequency)
    _check_regular_schedule(
        bond,
        issue=issue,
        maturity=maturity,
        first_coupon=first_coupon,
        last_coupon=last_coupon,
        months=months,
    )

    as_of = _parse_iso_date(as_of_date, "as_of_date")
    if as_of < issue or as_of > maturity:
        raise ValueError(
            f"as_of_date ({as_of_date!r}) is outside bond {bond.isin!r}'s "
            f"[issue_date, maturity_date] range [{bond.issue_date!r}, {bond.maturity_date!r}]"
        )

    if as_of == maturity:
        # The final coupon/redemption date -- accrual has just reset to zero.
        return 0.0

    schedule_dates = _schedule_dates(issue, maturity, months)
    period_start: date | None = None
    period_end: date | None = None
    for start, end in zip(schedule_dates, schedule_dates[1:], strict=False):
        if start <= as_of < end:
            period_start, period_end = start, end
            break

    if period_start is None or period_end is None:
        # Unreachable: as_of is in [issue, maturity) and schedule_dates spans
        # exactly [issue, ..., maturity] with no gaps.
        raise AssertionError("unreachable: as_of_date not bracketed by any schedule period")

    if bond.ex_dividend_days > 0:
        days_to_next_coupon = (period_end - as_of).days
        if 0 < days_to_next_coupon <= bond.ex_dividend_days:
            raise BLIBondExDividendWindowError(
                f"as_of_date ({as_of_date!r}) is inside bond {bond.isin!r}'s ex-dividend "
                f"window ({bond.ex_dividend_days} day(s) before {period_end.isoformat()!r}) "
                "-- negative accrued interest is not implemented by this adapter slice"
            )

    day_counter = _day_counter(bond.day_count)
    year_fraction = day_counter.yearFraction(_to_ql_date(period_start), _to_ql_date(as_of))
    return bond.coupon * year_fraction * 100
