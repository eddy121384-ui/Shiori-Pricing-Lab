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

**Calendar-end-of-month schedule anchoring (Issue #94 fix, corrected per
Codex P1):** the selected schedule mode is **never inferred from
`issue_date`/`maturity_date` alone**. Two candidate grids are always
considered: the existing day-of-month-preserving `_add_months`
arithmetic (non-EOM), and -- only when both `issue_date` and
`maturity_date` are themselves calendar month-end -- QuantLib's own
`Schedule(..., endOfMonth=True)` (EOM). The bond's own declared
`first_coupon_date`/`last_coupon_date` resolve which candidate is
actually in effect: if only the non-EOM candidate's expected first/last
coupon dates match, non-EOM is selected; if only the EOM candidate
matches, EOM is selected; if both candidates match, non-EOM is preferred
(preserves prior behavior); if neither matches, `BLIBondScheduleError`
is raised. This matters because a bond can have month-end
`issue_date`/`maturity_date` and still use the ordinary day-of-month
schedule (e.g. `issue_date=2026-06-30` with
`first_coupon_date=2026-12-30`, not `2026-12-31`) -- treating month-end
endpoints as sufficient on their own to force EOM mode would silently
break that previously-valid bond. This is calendar end-of-month schedule
*anchoring* only: it adds no business-day payment adjustment, no holiday
calendar, no irregular-stub support, no new day-count convention, no
principal/redemption handling, and is not wired into any curve,
discount-factor, forward-price, or Black-76 logic. A bond where only one
of `issue_date`/`maturity_date` is a month-end (or neither) only has the
non-EOM candidate to match against, so it is validated exactly as
before, with no behavior change.

**Coupon amount (docs/29 §5):** a fixed per-100-face amount,
`coupon * 100 / periods_per_year` -- never scaled by
`redemption_amount`, which this module never reads for coupon-flow
purposes. `redemption_amount` is reserved for a future
principal/redemption slice this module does not implement.

**Day-count mapping (docs/29 §4; corrected for Issue #157).** `DayCount.ACT_ACT_ISDA`
maps literally to `ql.ActualActual(ql.ActualActual.ISDA)`, exactly as
before -- this behavior is unchanged. A second, genuinely distinct member,
`DayCount.ACT_ACT_BOND`, maps to `ql.ActualActual(ql.ActualActual.Bond)`
(QuantLib's name for the ISMA/bond-basis convention) -- the convention
actually used for US Treasury coupon accrual, which ISDA's Actual/Actual is
**not** a substitute for: ISDA prorates against the calendar year(s) the
period spans (splitting at Feb 29), while Bond/ISMA prorates strictly
within the bracketing coupon period, so a semi-annual period's full-period
fraction is always exactly `0.5` under Bond/ISMA regardless of its actual
day count, but not under ISDA. `ACT_ACT_ISDA` and `ACT_ACT_BOND` are never
aliased to each other and never produce the same accrued-interest result
over an irregular-length period.

**Irregular stubs and inconsistent coupon grids (docs/29 §5, Codex P2
review of PR #81):** `issue_date` through `maturity_date` must land on
one consistent regular coupon grid -- the span between them must be an
exact whole number of `coupon_frequency` periods, stepping by that
period from `issue_date` must land exactly on `maturity_date`, and the
declared `first_coupon_date`/`last_coupon_date` must equal that same
grid's first and second-to-last dates. Checking only the two endpoints
in isolation is not sufficient: it is possible for
`first_coupon_date == issue_date + one period` and
`last_coupon_date == maturity_date - one period` to both hold even
though `issue_date` to `maturity_date` is *not* an exact multiple of
the period (each endpoint's own arithmetic happens to "look regular"
while the two ends silently disagree on the grid in between). Any of
these mismatches raises `BLIBondScheduleError` -- the regular-coupon
formulas below are never silently applied to a bond whose grid is
irregular or internally inconsistent.

**Accrued interest is prorated from the fixed coupon amount, not
computed as an independent day-count fraction (Codex P2 review of PR
#81):** `AI(as_of) = period_coupon_amount * elapsed_fraction /
full_period_fraction`, where both fractions come from the same
`day_counter.yearFraction` call so the result is always consistent
with -- and never diverges from, or exceeds -- the fixed coupon amount
actually paid at the end of the period. A day-count convention whose
full-period year fraction is not exactly `1 / periods_per_year` (e.g.
`ACT_360` over an 182/183/184/185-actual-day semi-annual period) would
otherwise silently accrue a slightly wrong amount under a naive
`coupon * yearFraction(period_start, as_of) * 100` formula.

**Reference-period-aware year fractions (Issue #157 correction).** Both
`yearFraction` calls pass the bracketing coupon period's own start/end
dates as QuantLib's explicit `startRef`/`endRef` arguments. `ACT_ACT_BOND`
(`ql.ActualActual(ql.ActualActual.Bond)`) needs that reference period to
compute correctly -- without it, QuantLib cannot know the coupon period's
actual length and produces a different, wrong-for-this-purpose result (verified
against `ql.ActualActual(ql.ActualActual.Bond)` called with no reference
period in this module's own tests). `ACT_ACT_ISDA` ignores the extra
reference-period arguments entirely and returns the identical result with
or without them, so passing them unconditionally changes no existing
`ACT_ACT_ISDA` behavior. This is schedule-aware wiring only -- it reuses the
`period_start`/`period_end` this function already resolves from the
existing regular-schedule check, and introduces no second schedule
generator.

**Ex-dividend window (docs/29 §6):** `accrued_interest_per_100` raises
`BLIBondExDividendWindowError` for any `as_of_date` inside a bond's
ex-dividend window (`ex_dividend_days` calendar days immediately before
a coupon date). Negative accrued interest is not implemented by this
slice.

**Maturity-date coupon window (Codex P2 review of PR #81):**
`coupon_flows_before` raises `BLIBondMaturityCashflowUnsupportedError`
if the requested window would reach `maturity_date` -- the final
coupon-at-maturity cashflow combines with principal redemption, which
this adapter slice does not implement, so that window is refused
outright rather than silently returned with the maturity coupon
missing.

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
from datetime import date, timedelta

from shiori_pricing_lab.data.bli_standalone_contract import (
    BLIStandaloneBondReferenceData,
    StandaloneBondReferenceData,
)
from shiori_pricing_lab.products.enums import DayCount, Frequency, coerce_enum
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


class BLIBondMaturityCashflowUnsupportedError(ValueError):
    """Raised when a requested coupon window would reach `maturity_date`.

    The final coupon-at-maturity cashflow combines with principal
    redemption, which this adapter slice does not implement -- rather
    than silently omitting that cashflow from an otherwise-matching
    window, a window that reaches `maturity_date` is refused outright.
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


def _is_last_day_of_month(value: date) -> bool:
    """Return whether `value` is the last calendar day of its month.

    Pure stdlib arithmetic: `value` is the month's last day exactly when
    the very next calendar day rolls into day 1 of a new month -- true
    regardless of month length or leap years, so no month-length table is
    needed.
    """

    return (value + timedelta(days=1)).day == 1


def _check_regular_schedule(
    bond: StandaloneBondReferenceData,
    *,
    issue: date,
    maturity: date,
    first_coupon: date,
    last_coupon: date,
    months: int,
) -> bool:
    """Validate the regular coupon grid and return the resolved `end_of_month` flag.

    Two candidate grids are considered, never inferred from
    `issue`/`maturity` alone: the existing day-of-month-preserving
    `_add_months` arithmetic (non-EOM), and -- only when both `issue`
    and `maturity` are themselves calendar month-end -- QuantLib's own
    `Schedule(..., endOfMonth=True)` (EOM). The declared
    `first_coupon`/`last_coupon` resolve which candidate is actually in
    effect (Codex P1 regression: a bond may have month-end
    `issue`/`maturity` yet still use the ordinary non-EOM schedule, e.g.
    `issue=2026-06-30` with `first_coupon=2026-12-30`). If only one
    candidate's expected first/last coupon dates match the declared
    ones, that candidate is selected; if both match, non-EOM is
    preferred (preserves prior behavior); if neither matches, raises
    `BLIBondScheduleError`.

    A valid EOM schedule can have an intermediate month where the
    `issue` day-of-month simply does not exist (Codex P1 follow-up
    regression: `issue=2026-01-31` stepping monthly has no "February
    31st"), which makes `_add_months` raise. That is not by itself a
    reason to reject the bond -- it only means the non-EOM candidate is
    inapplicable, so the EOM candidate is still evaluated. No other
    exception from `_add_months` is caught or suppressed.
    """

    # Checking only the two endpoints in isolation (first_coupon_date vs.
    # issue_date + one period, last_coupon_date vs. maturity_date - one
    # period) is not sufficient (Codex P2 review of PR #81): both checks
    # can pass even though issue_date to maturity_date is not an exact
    # multiple of the coupon period, because each endpoint's own
    # arithmetic is computed independently and can "look regular" in
    # isolation while silently disagreeing with the other end's grid.
    # This checks the whole grid, anchored at issue_date, in one pass.
    # This part is candidate-agnostic: if the period count itself does
    # not divide evenly, neither the non-EOM nor the EOM candidate can
    # possibly work.
    total_months = (maturity.year - issue.year) * 12 + (maturity.month - issue.month)
    if total_months <= 0 or total_months % months != 0:
        raise BLIBondScheduleError(
            f"bond {bond.isin!r} has an irregular coupon grid: issue_date "
            f"({bond.issue_date!r}) to maturity_date ({bond.maturity_date!r}) is not an "
            f"exact multiple of the {months}-month coupon_frequency period, so no "
            "consistent regular schedule exists (no stub approximation is computed)"
        )

    periods = total_months // months

    # Non-EOM candidate: the existing, unchanged `_add_months` arithmetic.
    # `_add_months` raises `ValueError` when the issue day-of-month does
    # not exist in a stepped-to month (e.g. no "February 31st") -- a
    # legitimate EOM-only bond, not an irregular one, so this only marks
    # the non-EOM candidate inapplicable rather than propagating.
    non_eom_valid = False
    expected_first_non_eom: date | None = None
    expected_last_non_eom: date | None = None
    try:
        expected_first_non_eom = _add_months(issue, months)
        expected_last_non_eom = _add_months(issue, (periods - 1) * months)
        non_eom_valid = _add_months(issue, periods * months) == maturity
    except ValueError:
        non_eom_valid = False
    else:
        if non_eom_valid:
            non_eom_valid = (
                first_coupon == expected_first_non_eom and last_coupon == expected_last_non_eom
            )

    # EOM candidate: considered only when both endpoints are themselves
    # calendar month-end -- never as a consequence of the declared
    # first/last coupon dates alone, and never as a consequence of the
    # non-EOM candidate failing to construct.
    eom_valid = False
    expected_first_eom: date | None = None
    expected_last_eom: date | None = None
    if _is_last_day_of_month(issue) and _is_last_day_of_month(maturity):
        eom_schedule_dates = _schedule_dates(issue, maturity, months, end_of_month=True)
        if eom_schedule_dates[-1] == maturity:
            expected_first_eom = eom_schedule_dates[1]
            expected_last_eom = eom_schedule_dates[-2]
            eom_valid = first_coupon == expected_first_eom and last_coupon == expected_last_eom

    if non_eom_valid:
        return False
    if eom_valid:
        return True

    candidate_details = []
    if expected_first_non_eom is not None and expected_last_non_eom is not None:
        candidate_details.append(
            "non-EOM candidate expects first_coupon_date "
            f"{expected_first_non_eom.isoformat()!r} and last_coupon_date "
            f"{expected_last_non_eom.isoformat()!r}"
        )
    if expected_first_eom is not None and expected_last_eom is not None:
        candidate_details.append(
            f"EOM candidate expects first_coupon_date {expected_first_eom.isoformat()!r} "
            f"and last_coupon_date {expected_last_eom.isoformat()!r}"
        )
    candidate_detail_text = (
        "; ".join(candidate_details)
        if candidate_details
        else "no candidate regular schedule could be constructed from issue_date/maturity_date"
    )

    raise BLIBondScheduleError(
        f"bond {bond.isin!r} has an irregular first/last coupon period, which this "
        "adapter slice does not support (no stub approximation is computed): got "
        f"first_coupon_date {bond.first_coupon_date!r} and last_coupon_date "
        f"{bond.last_coupon_date!r}; {candidate_detail_text}"
    )


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _from_ql_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


def _schedule_dates(
    issue: date, maturity: date, months: int, *, end_of_month: bool = False
) -> tuple[date, ...]:
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
        end_of_month,
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
    if day_count is DayCount.ACT_ACT_BOND:
        # QuantLib's name for the ISMA/bond-basis convention -- the actual US
        # Treasury coupon-accrual convention. Correct results require the
        # bracketing coupon period's own reference dates, passed explicitly
        # by every caller of this day counter's `yearFraction` (see
        # `accrued_interest_per_100`) -- never a schedule-less construction.
        return ql.ActualActual(ql.ActualActual.Bond)
    raise ValueError(f"unsupported day_count for QuantLib mapping: {day_count!r}")


def derive_last_coupon_date(
    *,
    issue_date: str,
    maturity_date: str,
    first_coupon_date: str,
    coupon_frequency: Frequency | str,
) -> str:
    """Return this bond's final scheduled coupon date before `maturity_date`.

    The same regular-grid rules `_check_regular_schedule` already enforces,
    run one step earlier: a caller that does not yet *have* a
    `last_coupon_date` (because nothing has supplied one) cannot construct a
    `BondReferenceData` to ask that function, yet the grid is fully
    determined by `issue_date`, `maturity_date`, `first_coupon_date` and
    `coupon_frequency` alone. Both candidate grids are considered exactly as
    there -- the day-of-month-preserving `_add_months` arithmetic (non-EOM),
    and QuantLib's own `endOfMonth=True` schedule (EOM), the latter only when
    both endpoints are themselves calendar month-end -- with the declared
    `first_coupon_date` resolving which one is in effect and non-EOM
    preferred when both match.

    This is deliberately the grid's **second-to-last** date, which is what
    `last_coupon_date` means to every other function in this module (the
    final coupon-at-maturity event combines with principal redemption and is
    out of scope for this adapter slice). It is not a "previous coupon date
    relative to some as-of date", and it never depends on a valuation date,
    a settlement date, or the system clock.

    Raises `BLIBondScheduleError` when no consistent regular grid exists --
    including a bond with only one coupon period, whose grid has no coupon
    date strictly before maturity at all -- so an irregular or stubbed bond
    is refused rather than approximated. No stub methodology is introduced.
    """

    _require_quantlib()

    issue = _parse_iso_date(issue_date, "issue_date")
    maturity = _parse_iso_date(maturity_date, "maturity_date")
    first_coupon = _parse_iso_date(first_coupon_date, "first_coupon_date")
    frequency = coerce_enum(coupon_frequency, Frequency, "coupon_frequency")
    months = _coupon_period_months(frequency)

    total_months = (maturity.year - issue.year) * 12 + (maturity.month - issue.month)
    if total_months <= 0 or total_months % months != 0:
        raise BLIBondScheduleError(
            f"issue_date ({issue_date!r}) to maturity_date ({maturity_date!r}) is not an "
            f"exact multiple of the {months}-month coupon period, so no consistent regular "
            "schedule exists (no stub approximation is computed)"
        )
    periods = total_months // months
    if periods < 2:
        raise BLIBondScheduleError(
            f"issue_date ({issue_date!r}) to maturity_date ({maturity_date!r}) spans a "
            "single coupon period, so there is no coupon date strictly before maturity to "
            "derive -- the final coupon-at-maturity event is out of scope for this adapter"
        )

    # Non-EOM candidate: the existing, unchanged `_add_months` arithmetic. It
    # raises for a day-of-month that does not exist in a stepped-to month,
    # which only makes this candidate inapplicable (a legitimate EOM-only
    # bond), never the bond irregular.
    try:
        if (
            _add_months(issue, months) == first_coupon
            and _add_months(issue, periods * months) == maturity
        ):
            return _add_months(issue, (periods - 1) * months).isoformat()
    except ValueError:
        pass

    # EOM candidate: considered only when both endpoints are themselves
    # calendar month-end, exactly as in `_check_regular_schedule`.
    if _is_last_day_of_month(issue) and _is_last_day_of_month(maturity):
        schedule_dates = _schedule_dates(issue, maturity, months, end_of_month=True)
        if schedule_dates[-1] == maturity and schedule_dates[1] == first_coupon:
            return schedule_dates[-2].isoformat()

    raise BLIBondScheduleError(
        f"no regular coupon grid runs from issue_date ({issue_date!r}) through "
        f"maturity_date ({maturity_date!r}) with first_coupon_date "
        f"({first_coupon_date!r}) at a {months}-month period, so this bond's first/last "
        "coupon period is irregular and no last coupon date is derived"
    )


def coupon_flows_before(
    bond: StandaloneBondReferenceData,
    *,
    after_date: str,
    on_or_before_date: str,
) -> tuple[BLIBondCouponFlow, ...]:
    """Return coupon flows with payment date in `(after_date, on_or_before_date]`.

    Only regular coupon dates between `first_coupon_date` and
    `last_coupon_date` (both inclusive) are ever returned -- the final
    coupon-at-maturity event (which combines with principal redemption)
    is out of scope for this slice (docs/29 §5/§8). Rather than silently
    omitting that final cashflow whenever a caller's window happens to
    reach it, a window where `maturity_date` falls in
    `(after_date, on_or_before_date]` raises
    `BLIBondMaturityCashflowUnsupportedError` outright (Codex P2 review
    of PR #81) -- narrow the window to end before `maturity_date` to
    avoid this. Raises `BLIBondScheduleError` for a bond whose coupon
    grid is irregular or internally inconsistent.
    """

    _require_quantlib()
    if not isinstance(bond, (BondReferenceData, BLIStandaloneBondReferenceData)):
        raise TypeError(
            "bond must be a BondReferenceData or BLIStandaloneBondReferenceData, "
            f"got {type(bond).__name__}"
        )

    issue = _parse_iso_date(bond.issue_date, "issue_date")
    maturity = _parse_iso_date(bond.maturity_date, "maturity_date")
    first_coupon = _parse_iso_date(bond.first_coupon_date, "first_coupon_date")
    last_coupon = _parse_iso_date(bond.last_coupon_date, "last_coupon_date")
    months = _coupon_period_months(bond.coupon_frequency)
    end_of_month = _check_regular_schedule(
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

    if after < maturity <= on_or_before:
        raise BLIBondMaturityCashflowUnsupportedError(
            f"requested window (after_date={after_date!r}, "
            f"on_or_before_date={on_or_before_date!r}) reaches bond {bond.isin!r}'s "
            f"maturity_date ({bond.maturity_date!r}) -- the final coupon-at-maturity "
            "cashflow combines with principal redemption, which this adapter slice does "
            "not implement; narrow on_or_before_date to end before maturity_date"
        )

    schedule_dates = _schedule_dates(issue, maturity, months, end_of_month=end_of_month)
    coupon_dates = [d for d in schedule_dates[1:] if first_coupon <= d <= last_coupon]

    periods_per_year = 12 // months
    amount_per_100 = bond.coupon * 100 / periods_per_year

    return tuple(
        BLIBondCouponFlow(payment_date=d.isoformat(), amount_per_100=amount_per_100)
        for d in coupon_dates
        if after < d <= on_or_before
    )


def accrued_interest_per_100(
    bond: StandaloneBondReferenceData, *, as_of_date: str
) -> float:
    """Return accrued interest per 100 face at the explicit `as_of_date`.

    Prorates the fixed per-period coupon amount
    (`coupon * 100 / periods_per_year`) by the ratio of elapsed to full
    day-count fraction within the bracketing coupon period, so the
    result is always consistent with -- and never diverges from, or
    exceeds -- the fixed coupon amount actually paid at the end of that
    period (Codex P2 review of PR #81; see the module docstring).
    `as_of_date` must lie in `[issue_date, maturity_date]`. Raises
    `BLIBondScheduleError` for a bond whose coupon grid is irregular or
    internally inconsistent, and `BLIBondExDividendWindowError` if
    `as_of_date` falls inside the bond's ex-dividend window -- negative
    accrued interest is not implemented by this slice.
    """

    _require_quantlib()
    if not isinstance(bond, (BondReferenceData, BLIStandaloneBondReferenceData)):
        raise TypeError(
            "bond must be a BondReferenceData or BLIStandaloneBondReferenceData, "
            f"got {type(bond).__name__}"
        )

    issue = _parse_iso_date(bond.issue_date, "issue_date")
    maturity = _parse_iso_date(bond.maturity_date, "maturity_date")
    first_coupon = _parse_iso_date(bond.first_coupon_date, "first_coupon_date")
    last_coupon = _parse_iso_date(bond.last_coupon_date, "last_coupon_date")
    months = _coupon_period_months(bond.coupon_frequency)
    end_of_month = _check_regular_schedule(
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

    schedule_dates = _schedule_dates(issue, maturity, months, end_of_month=end_of_month)
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
    period_start_ql = _to_ql_date(period_start)
    period_end_ql = _to_ql_date(period_end)
    as_of_ql = _to_ql_date(as_of)
    # startRef/endRef are the bracketing coupon period's own bounds -- required
    # for ACT_ACT_BOND to compute correctly (see the module docstring), and
    # inert for every other day counter here (ACT_ACT_ISDA ignores them
    # entirely; ACT_360/ACT_365_FIXED/THIRTY_360 take no reference period at
    # all). Passing them unconditionally changes no existing day count's result.
    elapsed_fraction = day_counter.yearFraction(
        period_start_ql, as_of_ql, period_start_ql, period_end_ql
    )
    full_period_fraction = day_counter.yearFraction(
        period_start_ql, period_end_ql, period_start_ql, period_end_ql
    )
    if full_period_fraction <= 0:
        # Defensive guard (Codex P2 review of PR #81): unreachable given
        # period_start < period_end and a sane day counter, but proration
        # below would divide by zero (or silently flip sign) if it ever
        # were not, so this is rejected explicitly rather than computed.
        raise ValueError(
            f"bond {bond.isin!r}'s day_count ({bond.day_count!r}) produced a non-positive "
            f"full-period year fraction ({full_period_fraction!r}) for the coupon period "
            f"[{period_start.isoformat()!r}, {period_end.isoformat()!r}] -- accrued "
            "interest cannot be prorated"
        )

    periods_per_year = 12 // months
    period_coupon_amount = bond.coupon * 100 / periods_per_year
    return period_coupon_amount * elapsed_fraction / full_period_fraction
