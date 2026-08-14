"""UST coupon *payment* date resolution (Issue #175, Eddy's RED decision).

Scope: one small, explicit, deterministic function that turns a UST coupon's
**scheduled (unadjusted) date** into the date its cash is **actually paid**.
Nothing else: no coupon schedule, no coupon amount, no accrual, no price, no
curve, no settlement-date derivation.

**Why this module exists.** ``pricing/bli_quantlib_bond_adapter`` generates
coupon dates with ``ql.NullCalendar()`` -- unadjusted -- and that is correct
for **accrued interest**, where accrual conventionally stops on the nominal
date. It is not the date the money moves. A US Treasury coupon whose
scheduled date is not a business day is paid on the next business day, and a
repo-carry reinvestment leg needs *that* date. Issue #175 stopped on exactly
this question; Eddy's decision is convention **B**::

    payment date = the first Federal Reserve business day on or after the
                   scheduled coupon date, with NO additional coupon interest
                   for the delay

Both halves are named constants below and are carried verbatim onto every
result, so no consumer can report a rolled date without saying which
calendar and which rule produced it.

**The calendar is the Federal Reserve calendar, deliberately not SIFMA.**
``ql.UnitedStates(ql.UnitedStates.FederalReserve)`` is the Fedwire funds
calendar -- the one that governs when the money actually moves. This module
deliberately does **not** reuse ``bli_bond_convention_profile``'s
``CALENDAR_US_SIFMA`` (``ql.UnitedStates(ql.UnitedStates.GovernmentBond)``),
which is the bond-market calendar scoped to that profile's *settlement*
rolling: the two differ (Good Friday is a SIFMA holiday but a Fedwire
business day, among others), and silently reusing a settlement calendar as a
payment calendar is the kind of methodology inference AGENTS.md rule 7
forbids. That is Eddy's explicit instruction, not an inference of this
module's.

For the same reason this calendar is **not** registered in that module's
``_CALENDAR_FACTORIES``: everything in that registry is selectable as a
profile ``settlement_calendar``, and the Federal Reserve calendar is not a
settlement calendar. Keeping it here keeps the two purposes from being
interchangeable by accident.

**No accrual, ever.** ``NO_ADDITIONAL_COUPON_INTEREST`` in the roll
convention's own name is the substantive half of Eddy's decision: the coupon
amount is unchanged by the roll. This module returns dates only and computes
no amount at all, so it cannot violate that.

**No system clock, no settlement lag, no schedule.** The scheduled date is an
explicit caller-supplied ISO date string; nothing here reads a clock, derives
a settlement date, or knows what a coupon is worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

try:  # pragma: no cover - import guard mirrors bli_quantlib_bond_adapter's own
    import QuantLib as ql
except ImportError:  # pragma: no cover
    ql = None

from shiori_pricing_lab.data._validation import _parse_iso_date

# The Fedwire funds calendar -- when the money moves. Deliberately distinct
# from bli_bond_convention_profile's CALENDAR_US_SIFMA settlement calendar;
# see the module docstring for why they are not interchangeable.
UST_COUPON_PAYMENT_CALENDAR = "US_FEDERAL_RESERVE"

# Eddy's Issue #175 RED decision, stated in full so a rolled date can never be
# reported without the rule that produced it.
UST_COUPON_PAYMENT_ROLL_CONVENTION = (
    "FOLLOWING_FEDERAL_RESERVE_BUSINESS_DAY__NO_ADDITIONAL_COUPON_INTEREST"
)


class BLICouponPaymentCalendarUnavailableError(RuntimeError):
    """QuantLib is not installed, so no payment calendar can be consulted.

    Raised instead of falling back to a weekday-only approximation: a
    partial calendar would silently misdate every coupon that lands on a
    market holiday, which is the exact failure Issue #175 refused.
    """


@dataclass(frozen=True)
class USTCouponPaymentDate:
    """One coupon's scheduled date and the date its cash is actually paid.

    ``roll_days`` is the number of calendar days the roll moved the date --
    ``0`` whenever the scheduled date is already a Federal Reserve business
    day, which is the ordinary case. ``payment_calendar`` and
    ``roll_convention`` carry this module's own named constants verbatim.
    """

    scheduled_payment_date: str
    payment_date: str
    roll_days: int
    payment_calendar: str
    roll_convention: str


def _federal_reserve_calendar() -> ql.Calendar:
    if ql is None:
        raise BLICouponPaymentCalendarUnavailableError(
            "QuantLib is not installed -- install the optional 'quant' dependency group "
            '(pip install "shiori-pricing-lab[quant]") to resolve UST coupon payment dates'
        )
    return ql.UnitedStates(ql.UnitedStates.FederalReserve)


def is_federal_reserve_business_day(value: str) -> bool:
    """Return whether ``value`` is a Federal Reserve (Fedwire) business day.

    Exposed so a caller can report *why* a coupon's payment date moved
    without re-deriving the calendar itself.
    """

    parsed = _parse_iso_date(value, "value")
    return bool(_federal_reserve_calendar().isBusinessDay(_to_ql_date(parsed)))


def _to_ql_date(value: date) -> ql.Date:
    return ql.Date(value.day, value.month, value.year)


def _from_ql_date(value: ql.Date) -> date:
    return date(value.year(), value.month(), value.dayOfMonth())


def resolve_ust_coupon_payment_date(scheduled_payment_date: str) -> USTCouponPaymentDate:
    """Return the date ``scheduled_payment_date``'s coupon cash is actually paid.

    Applies ``UST_COUPON_PAYMENT_ROLL_CONVENTION``: QuantLib's own
    ``Calendar.adjust(..., ql.Following)`` on the Federal Reserve calendar,
    which returns the scheduled date unchanged when it is already a business
    day and otherwise the first business day after it. The roll itself is
    QuantLib's -- this module writes no holiday table, partial or otherwise,
    and never approximates one from weekday arithmetic.

    Raises :class:`BLICouponPaymentCalendarUnavailableError` if QuantLib is
    absent, and ``ValueError`` (from the shared date parser) for a malformed
    date. The returned ``payment_date`` is never before
    ``scheduled_payment_date``.
    """

    scheduled = _parse_iso_date(scheduled_payment_date, "scheduled_payment_date")
    calendar = _federal_reserve_calendar()
    paid = _from_ql_date(calendar.adjust(_to_ql_date(scheduled), ql.Following))
    return USTCouponPaymentDate(
        scheduled_payment_date=scheduled_payment_date,
        payment_date=paid.isoformat(),
        roll_days=(paid - scheduled).days,
        payment_calendar=UST_COUPON_PAYMENT_CALENDAR,
        roll_convention=UST_COUPON_PAYMENT_ROLL_CONVENTION,
    )
