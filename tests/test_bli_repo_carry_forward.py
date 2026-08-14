"""Tests for `pricing/bli_repo_carry_forward.py` (Issues #173, #175).

Two layers, following `test_bli_forward_clean_price.py`'s own precedent:

1. **Pure convention/formula tests** (no QuantLib import, always run) --
   the ACT/360 term helpers and the ``1 + r x t`` FPA carry factor over
   plain numbers.
2. **Integration tests** (`@pytest.mark.skipif` on QuantLib availability)
   -- the public `repo_carry_forward_clean_price`, composing the real
   `accrued_interest_per_100` / `coupon_flows_before` (#81) helpers. Every
   expected value here is rebuilt inside the test from those same
   already-reviewed helper calls, never a hand-typed magic number.

Every bond used below is the existing shared synthetic fixture; no
Bloomberg, OVME, or market observation of any kind appears in this file.
"""

from __future__ import annotations

from datetime import date

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import SYNTHETIC_BLI_MVP_INPUT_BUNDLE
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondMaturityCashflowUnsupportedError,
    accrued_interest_per_100,
    coupon_flows_before,
    is_quantlib_available,
)
from shiori_pricing_lab.pricing.bli_repo_carry_forward import (
    REPO_COMPOUNDING_CONVENTION,
    REPO_DAY_COUNT_CONVENTION,
    RepoCarryInterimCouponPaymentDateUnresolvedError,
    carry_factor_from_simple_repo_rate,
    repo_carry_forward_clean_price,
    repo_term_days,
    repo_term_year_fraction,
)

SYNTHETIC_BOND = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data

# The shared synthetic bullet pays semi-annual coupons on 15 June / 15
# December, so a window starting 2026-08-13 stays coupon-free up to
# 2026-12-14, picks up exactly one coupon by 2027-02-16, and two by
# 2027-07-15. Every one of those facts is asserted from `coupon_flows_before`
# below rather than assumed.
SPOT_SETTLEMENT_DATE = "2026-08-13"
CASE_A_FORWARD_DATES = ("2026-09-15", "2026-11-13", "2026-12-01")
CASE_B_FORWARD_DATE = "2027-02-16"
CASE_B_TWO_COUPON_FORWARD_DATE = "2027-07-15"
# A coupon paid exactly on the forward settlement date -- the zero-term
# boundary of the reinvestment leg.
CASE_B_COUPON_ON_FORWARD_DATE = "2026-12-15"

requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)


# --- pure convention / formula layer (no QuantLib) --------------------------------


def test_the_repo_term_is_actual_days_over_360_and_names_its_convention():
    assert REPO_DAY_COUNT_CONVENTION == "ACT/360"
    assert REPO_COMPOUNDING_CONVENTION == "SIMPLE"
    assert repo_term_days("2026-08-13", "2026-11-13") == 92
    assert repo_term_year_fraction("2026-08-13", "2026-11-13") == 92 / 360.0


def test_a_leap_day_is_counted_as_an_actual_day():
    assert repo_term_days("2028-02-28", "2028-03-01") == 2


@pytest.mark.parametrize(
    "spot, forward",
    [("2026-08-13", "2026-08-13"), ("2026-08-13", "2026-08-12")],
)
def test_a_zero_or_negative_repo_term_is_refused(spot, forward):
    with pytest.raises(ValueError, match="strictly after"):
        repo_term_days(spot, forward)


def test_a_malformed_date_is_reported_by_the_existing_date_parser():
    with pytest.raises(ValueError, match="forward_settlement_date"):
        repo_term_days("2026-08-13", "13/11/2026")


def test_the_carry_factor_is_exactly_one_plus_rate_times_term():
    assert carry_factor_from_simple_repo_rate(
        repo_rate_decimal=0.04, repo_term_year_fraction=0.25
    ) == pytest.approx(1.01)


def test_a_zero_repo_rate_carries_nothing():
    assert (
        carry_factor_from_simple_repo_rate(repo_rate_decimal=0.0, repo_term_year_fraction=0.25)
        == 1.0
    )


def test_a_negative_repo_rate_is_allowed_and_carries_the_other_way():
    assert carry_factor_from_simple_repo_rate(
        repo_rate_decimal=-0.02, repo_term_year_fraction=0.5
    ) == pytest.approx(0.99)


def test_a_carry_factor_that_would_not_be_positive_is_refused():
    with pytest.raises(ValueError, match="carry factor must be positive"):
        carry_factor_from_simple_repo_rate(repo_rate_decimal=-5.0, repo_term_year_fraction=1.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "0.04", None, True])
def test_a_non_finite_or_non_numeric_repo_rate_is_refused(bad):
    with pytest.raises(ValueError, match="repo_rate_decimal must be a finite number"):
        carry_factor_from_simple_repo_rate(repo_rate_decimal=bad, repo_term_year_fraction=0.25)


# --- integration layer (QuantLib) -------------------------------------------------


@requires_quantlib
@pytest.mark.parametrize("forward_date", CASE_A_FORWARD_DATES)
def test_the_fpa_structure_is_reproduced_step_by_step_at_three_horizons(forward_date):
    repo_rate = 0.0375

    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=forward_date,
        repo_rate_decimal=repo_rate,
    )

    expected_spot_accrued = accrued_interest_per_100(
        SYNTHETIC_BOND, as_of_date=SPOT_SETTLEMENT_DATE
    )
    expected_forward_accrued = accrued_interest_per_100(SYNTHETIC_BOND, as_of_date=forward_date)
    expected_term_days = repo_term_days(SPOT_SETTLEMENT_DATE, forward_date)
    expected_carry_factor = 1.0 + repo_rate * expected_term_days / 360.0
    expected_spot_dirty = 99.5 + expected_spot_accrued
    expected_forward_dirty = expected_spot_dirty * expected_carry_factor

    assert result.repo_term_days == expected_term_days
    assert result.repo_term_year_fraction == pytest.approx(expected_term_days / 360.0)
    assert result.repo_day_count_convention == REPO_DAY_COUNT_CONVENTION
    assert result.repo_compounding_convention == REPO_COMPOUNDING_CONVENTION
    assert result.accrued_interest_at_spot_settlement_per_100 == pytest.approx(
        expected_spot_accrued
    )
    assert result.accrued_interest_at_forward_settlement_per_100 == pytest.approx(
        expected_forward_accrued
    )
    assert result.spot_dirty_price_per_100 == pytest.approx(expected_spot_dirty)
    assert result.carry_factor == pytest.approx(expected_carry_factor)
    assert result.forward_dirty_price_per_100 == pytest.approx(expected_forward_dirty)
    assert result.forward_clean_price_per_100 == pytest.approx(
        expected_forward_dirty - expected_forward_accrued
    )


@requires_quantlib
def test_the_three_case_a_horizons_really_are_coupon_free():
    for forward_date in CASE_A_FORWARD_DATES:
        assert (
            coupon_flows_before(
                SYNTHETIC_BOND,
                after_date=SPOT_SETTLEMENT_DATE,
                on_or_before_date=forward_date,
            )
            == ()
        )


@requires_quantlib
def test_a_longer_horizon_carries_further_at_the_same_repo_rate():
    forwards = [
        repo_carry_forward_clean_price(
            bond=SYNTHETIC_BOND,
            spot_clean_price_per_100=99.5,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date=forward_date,
            repo_rate_decimal=0.0375,
        )
        for forward_date in CASE_A_FORWARD_DATES
    ]
    carry_factors = [forward.carry_factor for forward in forwards]
    assert carry_factors == sorted(carry_factors)
    dirty_prices = [forward.forward_dirty_price_per_100 for forward in forwards]
    assert dirty_prices == sorted(dirty_prices)


@requires_quantlib
def test_a_case_a_horizon_carries_no_coupon_and_subtracts_exactly_nothing():
    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=CASE_A_FORWARD_DATES[1],
        repo_rate_decimal=0.0375,
    )
    assert coupon_flows_before(
        SYNTHETIC_BOND,
        after_date=SPOT_SETTLEMENT_DATE,
        on_or_before_date=CASE_A_FORWARD_DATES[1],
    ) == ()
    # The single carry factor is the whole forward dirty price.
    assert result.forward_dirty_price_per_100 == pytest.approx(
        result.spot_dirty_price_per_100 * result.carry_factor
    )


# --- Case B: coupons in (tS, tF] are refused, pending the payment date ------------
#
# Issue #175 RED: the coupon dates this repository holds are unadjusted
# NullCalendar schedule dates, not cash-receipt dates, and no approved
# coupon-payment convention exists to resolve them. No interim-coupon carry
# is implemented anywhere -- the earlier revision's arithmetic was deleted
# with its unapproved public entry point (Codex P1 review of PR #176) -- so
# what these tests pin is the refusal itself, and that it fires for every
# in-window coupon rather than a detectable subset.


@requires_quantlib
@pytest.mark.parametrize(
    "forward_date, expected_coupon_count",
    [
        (CASE_B_COUPON_ON_FORWARD_DATE, 1),
        (CASE_B_FORWARD_DATE, 1),
        (CASE_B_TWO_COUPON_FORWARD_DATE, 2),
    ],
)
def test_every_case_b_horizon_fails_closed_on_the_unresolved_payment_date(
    forward_date, expected_coupon_count
):
    # The horizons really do contain coupons -- this is a refusal of a real
    # Case B, not a vacuous pass.
    expected_flows = coupon_flows_before(
        SYNTHETIC_BOND,
        after_date=SPOT_SETTLEMENT_DATE,
        on_or_before_date=forward_date,
    )
    assert len(expected_flows) == expected_coupon_count

    with pytest.raises(RepoCarryInterimCouponPaymentDateUnresolvedError) as excinfo:
        repo_carry_forward_clean_price(
            bond=SYNTHETIC_BOND,
            spot_clean_price_per_100=99.5,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date=forward_date,
            repo_rate_decimal=0.0375,
        )

    # Every scheduled coupon is named, so the refusal says what it is about.
    message = str(excinfo.value)
    for flow in expected_flows:
        assert flow.payment_date in message
    assert "unadjusted" in message


@requires_quantlib
def test_a_weekday_coupon_is_refused_exactly_like_a_weekend_one():
    # The refusal is deliberately total, not weekend-only: a weekday US
    # government-securities holiday is equally not a payment date and is
    # undetectable without the calendar this repository lacks, so a partial
    # guard would have disguised the exposure rather than reduced it.
    assert date.fromisoformat("2026-12-15").strftime("%A") == "Tuesday"
    with pytest.raises(RepoCarryInterimCouponPaymentDateUnresolvedError):
        repo_carry_forward_clean_price(
            bond=SYNTHETIC_BOND,
            spot_clean_price_per_100=99.5,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date=CASE_B_FORWARD_DATE,
            repo_rate_decimal=0.0375,
        )


@requires_quantlib
def test_a_coupon_paid_on_the_spot_settlement_date_is_not_an_interim_coupon():
    # The window is half-open at tS: that coupon is not received by the
    # forward buyer, the spot dirty price on its own payment date already
    # carries zero accrued interest, and so the RED refusal does not fire.
    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=CASE_B_COUPON_ON_FORWARD_DATE,
        forward_settlement_date=CASE_B_FORWARD_DATE,
        repo_rate_decimal=0.0375,
    )
    assert result.accrued_interest_at_spot_settlement_per_100 == 0.0
    assert result.forward_clean_price_per_100 > 0


@requires_quantlib
def test_a_horizon_reaching_maturity_is_still_refused_by_the_coupon_adapter():
    # Coupon-at-maturity combines with principal redemption, which the
    # composed adapter slice does not implement -- a separate boundary from
    # the payment-date RED above, and it still fires first.
    with pytest.raises(BLIBondMaturityCashflowUnsupportedError):
        repo_carry_forward_clean_price(
            bond=SYNTHETIC_BOND,
            spot_clean_price_per_100=99.5,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date="2030-06-15",
            repo_rate_decimal=0.0375,
        )


@requires_quantlib
@pytest.mark.parametrize("bad_price", [0.0, -1.0, float("nan")])
def test_a_missing_or_impossible_spot_clean_price_blocks_the_forward(bad_price):
    with pytest.raises(ValueError, match="spot_clean_price_per_100"):
        repo_carry_forward_clean_price(
            bond=SYNTHETIC_BOND,
            spot_clean_price_per_100=bad_price,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date=CASE_A_FORWARD_DATES[0],
            repo_rate_decimal=0.0375,
        )


@requires_quantlib
def test_a_non_bond_argument_is_refused_by_the_existing_accrual_adapter():
    with pytest.raises(TypeError, match="bond must be"):
        repo_carry_forward_clean_price(
            bond={"isin": "XS0000000001"},
            spot_clean_price_per_100=99.5,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date=CASE_A_FORWARD_DATES[0],
            repo_rate_decimal=0.0375,
        )


@requires_quantlib
def test_the_two_settlement_dates_are_echoed_back_verbatim():
    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=CASE_A_FORWARD_DATES[1],
        repo_rate_decimal=0.0375,
    )
    assert result.spot_settlement_date == SPOT_SETTLEMENT_DATE
    assert result.forward_settlement_date == CASE_A_FORWARD_DATES[1]
    assert result.spot_clean_price_per_100 == 99.5
    assert result.repo_rate_decimal == 0.0375
