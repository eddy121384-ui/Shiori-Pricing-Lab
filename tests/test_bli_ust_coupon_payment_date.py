"""Tests for `pricing/bli_ust_coupon_payment_date.py` (Issue #175).

Eddy's Issue #175 RED decision (convention B): a UST coupon scheduled on a
day that is not a Federal Reserve business day is paid on the next Federal
Reserve business day, with no additional coupon interest.

Every date below is a real calendar fact (a named weekday, or a named US
market holiday), asserted from `datetime.date` or from QuantLib's own
calendar rather than hand-waved -- no Bloomberg, OVME, or market observation
of any kind appears in this file, and this module returns dates only, never
an amount.
"""

from __future__ import annotations

from datetime import date

import pytest

from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.bli_ust_coupon_payment_date import (
    UST_COUPON_PAYMENT_CALENDAR,
    UST_COUPON_PAYMENT_ROLL_CONVENTION,
    is_federal_reserve_business_day,
    resolve_ust_coupon_payment_date,
)

requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)


def test_the_calendar_and_roll_convention_are_named_and_say_what_they_do():
    assert UST_COUPON_PAYMENT_CALENDAR == "US_FEDERAL_RESERVE"
    # Both halves of Eddy's decision are in the name: which direction the
    # roll goes, and that the amount is untouched by it.
    assert UST_COUPON_PAYMENT_ROLL_CONVENTION == (
        "FOLLOWING_FEDERAL_RESERVE_BUSINESS_DAY__NO_ADDITIONAL_COUPON_INTEREST"
    )


@requires_quantlib
@pytest.mark.parametrize(
    "scheduled, weekday",
    [
        ("2026-12-15", "Tuesday"),
        ("2026-11-02", "Monday"),
        ("2027-06-15", "Tuesday"),
    ],
)
def test_a_scheduled_date_that_is_already_a_business_day_does_not_move(
    scheduled, weekday
):
    assert date.fromisoformat(scheduled).strftime("%A") == weekday
    assert is_federal_reserve_business_day(scheduled)

    resolved = resolve_ust_coupon_payment_date(scheduled)
    assert resolved.scheduled_payment_date == scheduled
    assert resolved.payment_date == scheduled
    assert resolved.roll_days == 0


@requires_quantlib
@pytest.mark.parametrize(
    "scheduled, weekday, expected_payment, expected_roll",
    [
        # Issue #175's own acceptance coupon: a Saturday, so Monday.
        ("2026-10-31", "Saturday", "2026-11-02", 2),
        # A Sunday, so the very next day.
        ("2027-01-31", "Sunday", "2027-02-01", 1),
    ],
)
def test_a_weekend_scheduled_date_rolls_to_the_next_business_day(
    scheduled, weekday, expected_payment, expected_roll
):
    assert date.fromisoformat(scheduled).strftime("%A") == weekday
    assert not is_federal_reserve_business_day(scheduled)

    resolved = resolve_ust_coupon_payment_date(scheduled)
    assert resolved.payment_date == expected_payment
    assert resolved.roll_days == expected_roll


@requires_quantlib
@pytest.mark.parametrize(
    "scheduled, holiday, expected_payment",
    [
        # A weekday holiday -- the class a weekend-only rule would miss, and
        # the reason this module consults a real calendar rather than
        # weekday arithmetic.
        ("2026-11-11", "Veterans Day (Wednesday)", "2026-11-12"),
        ("2026-12-25", "Christmas Day (Friday)", "2026-12-28"),
    ],
)
def test_a_weekday_market_holiday_also_rolls(scheduled, holiday, expected_payment):
    assert date.fromisoformat(scheduled).weekday() < 5, holiday
    assert not is_federal_reserve_business_day(scheduled)
    assert resolve_ust_coupon_payment_date(scheduled).payment_date == expected_payment


@requires_quantlib
def test_the_payment_date_is_never_before_the_scheduled_date():
    # Sweep a whole year of scheduled dates: the roll is Following, so it can
    # only ever move a date forward or leave it alone.
    scheduled = date(2026, 1, 1)
    while scheduled < date(2027, 1, 1):
        resolved = resolve_ust_coupon_payment_date(scheduled.isoformat())
        paid = date.fromisoformat(resolved.payment_date)
        assert paid >= scheduled
        assert resolved.roll_days == (paid - scheduled).days
        assert is_federal_reserve_business_day(resolved.payment_date)
        scheduled = date.fromordinal(scheduled.toordinal() + 1)


@requires_quantlib
def test_every_result_carries_its_own_calendar_and_convention():
    resolved = resolve_ust_coupon_payment_date("2026-10-31")
    assert resolved.payment_calendar == UST_COUPON_PAYMENT_CALENDAR
    assert resolved.roll_convention == UST_COUPON_PAYMENT_ROLL_CONVENTION


@requires_quantlib
def test_the_federal_reserve_calendar_is_not_the_sifma_settlement_calendar():
    """Eddy's instruction, pinned: these are two different calendars.

    Good Friday is a SIFMA (bond-market) holiday but an ordinary Fedwire
    business day, so silently reusing the profile's settlement calendar for
    coupon payments would move a coupon that in fact pays on time.
    """

    from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
        UST_CONVENTION_PROFILE,
    )

    good_friday = "2027-03-26"
    assert date.fromisoformat(good_friday).strftime("%A") == "Friday"

    import QuantLib as ql

    sifma = UST_CONVENTION_PROFILE.calendar()
    ql_date = ql.Date(26, 3, 2027)
    assert not sifma.isBusinessDay(ql_date)
    assert is_federal_reserve_business_day(good_friday)
    assert resolve_ust_coupon_payment_date(good_friday).payment_date == good_friday


@requires_quantlib
@pytest.mark.parametrize("bad", ["31/10/2026", "2026-13-01", "", None, 20261031])
def test_a_malformed_scheduled_date_is_reported_by_the_shared_date_parser(bad):
    with pytest.raises(ValueError, match="scheduled_payment_date"):
        resolve_ust_coupon_payment_date(bad)
