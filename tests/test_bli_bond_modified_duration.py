"""Deterministic pins for the Issue #211 current-time bond duration ``D_B``.

The approved contract is ``D_B = -(1 / P_dirty) x dP/dY`` at the bond's
current cash-bond spot settlement, by 1 bp central difference over the
existing reviewed price<->yield primitive. Every clause of that sentence is
pinned below, because each one was a decision rather than a default: the
denominator is the one the Trading Desk chose over a clean denominator worth
up to ~10.6 vol bp of sigma_P, and the derivative is numerical rather than
analytic only because the Phase-1 audit showed the two agree to ~1e-6.
"""

from __future__ import annotations

from datetime import date

import pytest

import shiori_pricing_lab.pricing.bli_bond_modified_duration as module
from shiori_pricing_lab.pricing.bli_bond_convention_profile import get_convention_profile
from shiori_pricing_lab.pricing.bli_bond_modified_duration import (
    DURATION_METHODOLOGY_VERSION,
    DURATION_PRICE_BASIS,
    DURATION_TYPE,
    SUPPORTED_DURATION_CONVENTION_PROFILES,
    YIELD_BUMP_BASIS_POINTS,
    BLIBondDurationError,
    calculate_bond_modified_duration,
    spot_settlement_date,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
    accrued_interest_per_100,
    clean_price_from_yield,
)

# One supported-profile UST, seasoned and well clear of both its first and
# its final coupon period. Settlement sits deep inside a coupon period on
# purpose: that is where clean and dirty denominators separate most, so a
# test that passed on either basis would prove nothing.
_SECURITY = "/isin/US0000000000"
_SETTLEMENT = date(2027, 2, 14)
_MATURITY = date(2035, 8, 15)
_COUPON = 4.25
_CLEAN = 101.067593
_T0 = "2027-02-12T16:00:00+00:00"


def _duration(**overrides):
    kwargs = {
        "security": _SECURITY,
        "convention_profile": "UST",
        "clean_price_per_100": _CLEAN,
        "settlement_date": _SETTLEMENT,
        "maturity_date": _MATURITY,
        "coupon_percent": _COUPON,
        "pricing_timestamp": _T0,
    }
    kwargs.update(overrides)
    return calculate_bond_modified_duration(**kwargs)


# --- The approved contract, pinned -------------------------------------------


def test_the_denominator_is_the_dirty_price_not_the_clean_price():
    # The decision this whole slice waited on. Reconstructed independently
    # from the result's own recorded numerator rather than from the
    # calculator's internals: -(dP/dY) divided by dirty must be what is
    # reported, and dividing by clean must not be.
    result = _duration()

    expected_dirty = -result.price_derivative_per_unit_yield / result.dirty_price_per_100
    expected_clean = -result.price_derivative_per_unit_yield / result.clean_price_per_100

    assert result.modified_duration == expected_dirty
    assert result.modified_duration != expected_clean
    assert result.price_basis == "DIRTY"
    assert DURATION_PRICE_BASIS == "DIRTY"

    # And the two really are materially apart on this bond -- if they were
    # not, this test would be passing for the wrong reason.
    assert abs(expected_clean / expected_dirty - 1) > 0.02


def test_the_dirty_price_is_clean_plus_accrued_at_the_settlement_date():
    result = _duration()

    expected_accrued = accrued_interest_per_100(
        _SETTLEMENT, _MATURITY, _COUPON, coupons_per_year=2
    )
    assert result.accrued_interest_per_100 == expected_accrued
    assert result.dirty_price_per_100 == _CLEAN + expected_accrued
    assert result.clean_price_per_100 == _CLEAN


def test_the_derivative_is_a_one_basis_point_central_difference():
    # Sign and math both: the bumps are symmetric about the base yield, the
    # difference is central (not forward, not backward), and the x100 that
    # takes "per yield percentage point" to "per unit decimal yield" is
    # applied exactly once. A missing x100 is a 100x error in sigma_P.
    result = _duration()

    assert result.yield_bump_basis_points == 1.0
    assert YIELD_BUMP_BASIS_POINTS == 1.0
    assert result.bumped_yield_up_percent == result.base_yield_percent + 0.01
    assert result.bumped_yield_down_percent == result.base_yield_percent - 0.01

    expected_up = clean_price_from_yield(
        result.bumped_yield_up_percent, _SETTLEMENT, _MATURITY, _COUPON, coupons_per_year=2
    )
    expected_down = clean_price_from_yield(
        result.bumped_yield_down_percent, _SETTLEMENT, _MATURITY, _COUPON, coupons_per_year=2
    )
    assert result.bumped_clean_price_up_per_100 == expected_up
    assert result.bumped_clean_price_down_per_100 == expected_down

    expected_derivative = (expected_up - expected_down) / (2.0 * 0.01) * 100.0
    assert result.price_derivative_per_unit_yield == expected_derivative


def test_the_price_derivative_is_negative_and_the_duration_positive():
    # An ordinary bond's price falls as its yield rises, so dP/dY < 0 and
    # the reported modified duration is the conventional positive number.
    result = _duration()

    assert result.price_derivative_per_unit_yield < 0
    assert result.modified_duration > 0
    assert result.absolute_modified_duration == abs(result.modified_duration)


def test_the_duration_matches_quantlibs_own_analytic_modified_duration():
    # The Phase-1 cross-check, pinned rather than left as a claim in prose:
    # an independent implementation of modified duration agrees with this
    # construction, which is what establishes that the numerical derivative
    # and the dirty denominator together are the market-standard quantity.
    ql = pytest.importorskip("QuantLib")

    result = _duration()

    settle = ql.Date(_SETTLEMENT.day, _SETTLEMENT.month, _SETTLEMENT.year)
    ql.Settings.instance().evaluationDate = settle
    schedule = ql.Schedule(
        ql.Date(15, 8, 2025),
        ql.Date(_MATURITY.day, _MATURITY.month, _MATURITY.year),
        ql.Period(ql.Semiannual),
        ql.NullCalendar(),
        ql.Unadjusted,
        ql.Unadjusted,
        ql.DateGeneration.Backward,
        False,
    )
    day_count = ql.ActualActual(ql.ActualActual.Bond, schedule)
    bond = ql.FixedRateBond(0, 100.0, schedule, [_COUPON / 100.0], day_count)
    analytic = ql.BondFunctions.duration(
        bond,
        ql.InterestRate(
            result.base_yield_percent / 100.0, day_count, ql.Compounded, ql.Semiannual
        ),
        ql.Duration.Modified,
        settle,
    )

    assert result.modified_duration == pytest.approx(analytic, abs=1e-5)

    # Macaulay is the near-miss the Phase-1 audit warned about: on this bond
    # it sits within 0.004 of a *clean*-denominator duration. Pinned so a
    # future edit that quietly switches basis cannot pass by looking right.
    macaulay = ql.BondFunctions.duration(
        bond,
        ql.InterestRate(
            result.base_yield_percent / 100.0, day_count, ql.Compounded, ql.Semiannual
        ),
        ql.Duration.Macaulay,
        settle,
    )
    assert abs(result.modified_duration - macaulay) > 0.1


# --- Date semantics ----------------------------------------------------------


def test_the_pricing_timestamp_and_the_settlement_date_are_kept_apart():
    # t0 is when the market state was observed; tS is the date every
    # cashflow calculation runs on. They are different fields because they
    # are different things, and neither may be read as the other.
    result = _duration()

    assert result.pricing_timestamp == _T0
    assert result.settlement_date == _SETTLEMENT


def test_spot_settlement_rolls_on_the_profiles_own_calendar():
    # T+1 for UST, T+2 for German -- taken from each profile, never hardcoded
    # here, and rolled by the already-reviewed helper.
    ust = get_convention_profile("UST")
    german = get_convention_profile("GERMAN_GOVT")

    assert ust.settlement_business_days == 1
    assert german.settlement_business_days == 2

    # 2026-09-10 is a Thursday; T+1 is the Friday.
    assert spot_settlement_date(date(2026, 9, 10), ust) == date(2026, 9, 11)
    # T+2 from the same Thursday crosses the weekend.
    assert spot_settlement_date(date(2026, 9, 10), german) == date(2026, 9, 14)


# --- Supported universe, fail-closed -----------------------------------------


def test_us_corporate_fails_closed_because_its_day_count_is_not_supported():
    # The scope trap from the Phase-1 audit: US_CORPORATE is 30/360 and the
    # reusable primitive is hard-wired ACT/ACT ISMA with no day-count
    # argument, so accepting it would price a corporate on the wrong
    # convention and report a duration of entirely ordinary magnitude.
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(convention_profile="US_CORPORATE")

    message = str(excinfo.value)
    assert "US_CORPORATE" in message
    assert "US_CORPORATE" not in SUPPORTED_DURATION_CONVENTION_PROFILES
    assert "ACT/ACT" in message or "ACT_ACT" in message


def test_only_ust_and_german_govt_are_supported():
    assert SUPPORTED_DURATION_CONVENTION_PROFILES == ("UST", "GERMAN_GOVT")


def test_the_german_annual_coupon_grid_is_used_for_the_german_profile():
    # Not a second engine: the same primitive's coupons_per_year=1 leg.
    result = _duration(convention_profile="GERMAN_GOVT")

    assert result.coupons_per_year == 1
    assert result.convention_profile == "GERMAN_GOVT"
    assert result.modified_duration > 0


def test_the_ust_profile_uses_the_semiannual_grid():
    assert _duration().coupons_per_year == 2


def test_an_unregistered_profile_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError):
        _duration(convention_profile="GILT")


@pytest.mark.parametrize("blank", [None, "", "   ", 7])
def test_a_missing_profile_selection_never_falls_back_to_a_default(blank):
    with pytest.raises(ValueError):
        _duration(convention_profile=blank)


# --- Fail-closed inputs ------------------------------------------------------


@pytest.mark.parametrize("bad", [0.0, -1.0, -0.0001])
def test_a_non_positive_clean_price_is_refused(bad):
    with pytest.raises(BLIBondDurationError):
        _duration(clean_price_per_100=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "101", None])
def test_an_unusable_clean_price_is_refused(bad):
    with pytest.raises(BLIBondDurationError):
        _duration(clean_price_per_100=bad)


def test_a_settlement_at_or_after_maturity_is_refused():
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(settlement_date=_MATURITY)
    assert "maturity" in str(excinfo.value)


def test_a_settlement_inside_the_final_coupon_period_is_refused_on_this_modules_error():
    # The reusable primitive's own refusal -- the U.S. street convention
    # switches to simple interest there and it does not implement that. It
    # must surface as this module's error type, not as a futures error.
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(settlement_date=date(2035, 5, 1))
    assert "final coupon period" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
def test_a_blank_security_is_refused(bad):
    with pytest.raises(BLIBondDurationError):
        _duration(security=bad)


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_a_blank_pricing_timestamp_is_refused(bad):
    with pytest.raises(BLIBondDurationError):
        _duration(pricing_timestamp=bad)


def test_a_datetime_is_not_accepted_where_a_settlement_date_is_required():
    from datetime import datetime

    with pytest.raises(BLIBondDurationError):
        _duration(settlement_date=datetime(2027, 2, 14, 12, 0))


def test_a_negative_coupon_is_refused():
    with pytest.raises(BLIBondDurationError):
        _duration(coupon_percent=-0.5)


# --- Provenance --------------------------------------------------------------


def test_the_result_carries_everything_needed_to_redo_the_arithmetic():
    result = _duration()

    # A reviewer holding only this object recomputes the duration by hand.
    redone = -(
        (result.bumped_clean_price_up_per_100 - result.bumped_clean_price_down_per_100)
        / (2.0 * result.yield_bump_basis_points / 100.0)
        * 100.0
    ) / result.dirty_price_per_100
    assert redone == result.modified_duration

    assert result.security == _SECURITY
    assert result.maturity_date == _MATURITY
    assert result.coupon_percent == _COUPON
    assert result.day_count == "ACT_ACT_BOND"
    assert result.duration_type == DURATION_TYPE
    assert result.source == "SHIORI_DERIVED"
    assert result.methodology_version == DURATION_METHODOLOGY_VERSION
    assert result.calculated_at


def test_the_duration_type_names_the_basis_rather_than_leaving_it_to_magnitude():
    # Phase-1 recorded Macaulay landing within 0.004 of a clean-denominator
    # duration on a real bond. The label is the only safe discriminator.
    assert DURATION_TYPE == "MODIFIED_DURATION_DIRTY_PRICE"
    assert _duration().duration_type == DURATION_TYPE


def test_no_bloomberg_duration_field_is_read():
    # The Phase-1 audit found no confirmed Bloomberg duration field, and
    # DUR_ADJ_MID remains a candidate with none of its semantics pinned.
    source = (module.__file__ or "").replace("\\", "/")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "DUR_ADJ" not in text.split('"""', 2)[2]
    assert _duration().source == "SHIORI_DERIVED"
