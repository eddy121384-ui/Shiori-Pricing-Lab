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

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import SYNTHETIC_BLI_MVP_INPUT_BUNDLE
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondMaturityCashflowUnsupportedError,
    accrued_interest_per_100,
    coupon_flows_before,
    is_quantlib_available,
)
from shiori_pricing_lab.pricing.bli_repo_carry_forward import (
    INTERIM_COUPON_TREATMENT,
    REPO_COMPOUNDING_CONVENTION,
    REPO_DAY_COUNT_CONVENTION,
    carry_factor_from_simple_repo_rate,
    reinvest_interim_coupon_to_forward_settlement,
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
    assert result.interim_coupons == ()
    assert result.interim_coupon_forward_value_per_100 == 0.0
    # The carried spot dirty price is the whole forward dirty price -- Case A
    # is the same code path over an empty coupon set, not a second formula.
    assert result.forward_dirty_price_per_100 == result.carried_spot_dirty_price_per_100
    assert result.interim_coupon_treatment == INTERIM_COUPON_TREATMENT


# --- Case B: one or more coupons paid in (tS, tF] (Issue #175) ---------------------


@requires_quantlib
@pytest.mark.parametrize(
    "forward_date, expected_coupon_count",
    [
        (CASE_B_COUPON_ON_FORWARD_DATE, 1),
        (CASE_B_FORWARD_DATE, 1),
        (CASE_B_TWO_COUPON_FORWARD_DATE, 2),
    ],
)
def test_every_interim_coupon_is_carried_to_the_forward_date_and_subtracted(
    forward_date, expected_coupon_count
):
    repo_rate = 0.0375

    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=forward_date,
        repo_rate_decimal=repo_rate,
    )

    # The coupon set is the composed adapter's own answer for (tS, tF],
    # never a schedule this module rebuilt.
    expected_flows = coupon_flows_before(
        SYNTHETIC_BOND,
        after_date=SPOT_SETTLEMENT_DATE,
        on_or_before_date=forward_date,
    )
    assert len(expected_flows) == expected_coupon_count
    assert [coupon.payment_date for coupon in result.interim_coupons] == [
        flow.payment_date for flow in expected_flows
    ]
    assert [coupon.amount_per_100 for coupon in result.interim_coupons] == [
        flow.amount_per_100 for flow in expected_flows
    ]

    expected_total = 0.0
    for coupon, flow in zip(result.interim_coupons, expected_flows, strict=True):
        expected_term_days = (
            0
            if flow.payment_date == forward_date
            else repo_term_days(flow.payment_date, forward_date)
        )
        expected_factor = 1.0 + repo_rate * expected_term_days / 360.0
        assert coupon.reinvestment_term_days == expected_term_days
        assert coupon.reinvestment_term_year_fraction == pytest.approx(
            expected_term_days / 360.0
        )
        assert coupon.reinvestment_factor == pytest.approx(expected_factor)
        assert coupon.forward_value_per_100 == pytest.approx(
            flow.amount_per_100 * expected_factor
        )
        expected_total += flow.amount_per_100 * expected_factor

    expected_spot_dirty = 99.5 + accrued_interest_per_100(
        SYNTHETIC_BOND, as_of_date=SPOT_SETTLEMENT_DATE
    )
    expected_carried = expected_spot_dirty * (
        1.0 + repo_rate * repo_term_days(SPOT_SETTLEMENT_DATE, forward_date) / 360.0
    )
    expected_forward_dirty = expected_carried - expected_total

    assert result.interim_coupon_treatment == INTERIM_COUPON_TREATMENT
    assert result.interim_coupon_forward_value_per_100 == pytest.approx(expected_total)
    assert result.carried_spot_dirty_price_per_100 == pytest.approx(expected_carried)
    assert result.forward_dirty_price_per_100 == pytest.approx(expected_forward_dirty)
    assert result.forward_clean_price_per_100 == pytest.approx(
        expected_forward_dirty
        - accrued_interest_per_100(SYNTHETIC_BOND, as_of_date=forward_date)
    )


@requires_quantlib
def test_a_coupon_paid_on_the_forward_date_earns_no_reinvestment():
    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=CASE_B_COUPON_ON_FORWARD_DATE,
        repo_rate_decimal=0.0375,
    )
    (coupon,) = result.interim_coupons
    assert coupon.payment_date == CASE_B_COUPON_ON_FORWARD_DATE
    assert coupon.reinvestment_term_days == 0
    assert coupon.reinvestment_factor == 1.0
    assert coupon.forward_value_per_100 == coupon.amount_per_100


@requires_quantlib
def test_a_coupon_paid_on_the_spot_settlement_date_is_not_an_interim_coupon():
    # The window is half-open at tS: that coupon is not received by the
    # forward buyer, and the spot dirty price on its own payment date
    # already carries zero accrued interest.
    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=CASE_B_COUPON_ON_FORWARD_DATE,
        forward_settlement_date=CASE_B_FORWARD_DATE,
        repo_rate_decimal=0.0375,
    )
    assert result.interim_coupons == ()
    assert result.accrued_interest_at_spot_settlement_per_100 == 0.0


@requires_quantlib
def test_carrying_the_coupon_is_worth_more_than_not_carrying_it():
    # The one materially different alternative reading (no reinvestment) is
    # recoverable from the trace, and is strictly smaller at a positive repo
    # rate -- so a reader can see exactly what the assumption is worth.
    result = repo_carry_forward_clean_price(
        bond=SYNTHETIC_BOND,
        spot_clean_price_per_100=99.5,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=CASE_B_FORWARD_DATE,
        repo_rate_decimal=0.0375,
    )
    face_only = sum(coupon.amount_per_100 for coupon in result.interim_coupons)
    assert result.interim_coupon_forward_value_per_100 > face_only


@requires_quantlib
def test_a_horizon_reaching_maturity_is_still_refused_by_the_coupon_adapter():
    # Coupon-at-maturity combines with principal redemption, which the
    # composed adapter slice does not implement -- Case B does not change
    # that boundary.
    with pytest.raises(BLIBondMaturityCashflowUnsupportedError):
        repo_carry_forward_clean_price(
            bond=SYNTHETIC_BOND,
            spot_clean_price_per_100=99.5,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date="2030-06-15",
            repo_rate_decimal=0.0375,
        )


# --- the reinvestment leg on its own (no QuantLib) --------------------------------


def test_one_coupon_reinvestment_leg_is_the_same_one_plus_r_t_factor():
    coupon = reinvest_interim_coupon_to_forward_settlement(
        payment_date="2026-10-31",
        amount_per_100=2.0,
        forward_settlement_date="2026-11-11",
        repo_rate_decimal=0.04,
    )
    assert coupon.reinvestment_term_days == 11
    assert coupon.reinvestment_term_year_fraction == pytest.approx(11 / 360.0)
    assert coupon.reinvestment_factor == pytest.approx(
        carry_factor_from_simple_repo_rate(
            repo_rate_decimal=0.04, repo_term_year_fraction=11 / 360.0
        )
    )
    assert coupon.forward_value_per_100 == pytest.approx(2.0 * coupon.reinvestment_factor)


def test_a_coupon_after_the_forward_settlement_date_is_refused():
    with pytest.raises(ValueError, match="must not be after forward_settlement_date"):
        reinvest_interim_coupon_to_forward_settlement(
            payment_date="2026-11-12",
            amount_per_100=2.0,
            forward_settlement_date="2026-11-11",
            repo_rate_decimal=0.04,
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "2.0", None, True])
def test_a_non_finite_or_non_numeric_coupon_amount_is_refused(bad):
    with pytest.raises(ValueError, match="amount_per_100 must be a finite number"):
        reinvest_interim_coupon_to_forward_settlement(
            payment_date="2026-10-31",
            amount_per_100=bad,
            forward_settlement_date="2026-11-11",
            repo_rate_decimal=0.04,
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
