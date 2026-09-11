"""Deterministic pins for the Issue #211 current-time bond duration ``D_B``.

The approved contract is ``D_B = -(1 / P_basis) x dP/dY`` at the bond's
current cash-bond spot settlement, by 1 bp central difference over the
existing reviewed price<->yield primitive, on an explicitly selected price
basis. Every clause of that sentence is pinned below, because each one was a
decision rather than a default.

The basis pins matter most: CLEAN and DIRTY share one derivative and differ
only in the division, so the tests check that the numerator really is
identical and that each denominator really is the one its label claims --
the two durations are a few percent apart and both look entirely ordinary.
"""

from __future__ import annotations

from datetime import date

import pytest

import shiori_pricing_lab.pricing.bli_bond_modified_duration as module
from shiori_pricing_lab.pricing.bli_bond_convention_profile import get_convention_profile
from shiori_pricing_lab.pricing.bli_bond_modified_duration import (
    DURATION_METHODOLOGY_VERSION,
    SUPPORTED_DURATION_CONVENTION_PROFILES,
    YIELD_BUMP_BASIS_POINTS,
    BLIBondDurationError,
    calculate_bond_modified_duration,
    duration_type_for_basis,
    spot_settlement_date,
)
from shiori_pricing_lab.pricing.bli_bond_option_price_basis import (
    DEFAULT_BOND_OPTION_PRICE_BASIS,
    BondOptionPriceBasis,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
    IrregularFirstCoupon,
    accrued_interest_per_100,
    clean_price_from_yield,
)

# One supported-profile UST, seasoned and well clear of both its first and
# its final coupon period. Settlement sits deep inside a coupon period on
# purpose: that is where clean and dirty denominators separate most, so a
# test that passed on either basis would prove nothing.
#
# tS is derived from t0, never chosen (Codex review, PR #212). t0 =
# 2027-02-11 is a Thursday, so UST T+1 settles Friday 2027-02-12 -- three
# days before the 15 Feb coupon, giving near-maximal accrued. The previous
# fixture named a settlement date directly and picked 2027-02-14, a *Sunday*,
# which no UST ever settles on; that is precisely the class of error deriving
# tS removes.
_SECURITY = "/isin/US0000000000"
_MATURITY = date(2035, 8, 15)
_COUPON = 4.25
_CLEAN = 101.067593
_T0 = "2027-02-11T16:00:00+00:00"
_SETTLEMENT = date(2027, 2, 12)
_CALCULATED_AT = "2027-02-11T16:00:05+00:00"

# t0 whose UST T+1 lands exactly on a coupon date, so accrued is zero and the
# two price bases coincide. 15 Feb 2027 is a US holiday, so the nearest such
# date is 2028-02-15.
_T0_ON_COUPON = "2028-02-14T16:00:00+00:00"
_SETTLEMENT_ON_COUPON = date(2028, 2, 15)


def _duration(**overrides):
    kwargs = {
        "security": _SECURITY,
        "convention_profile": "UST",
        "price_basis": BondOptionPriceBasis.DIRTY,
        "clean_price_per_100": _CLEAN,
        "maturity_date": _MATURITY,
        "coupon_percent": _COUPON,
        "pricing_timestamp": _T0,
        "calculated_at": _CALCULATED_AT,
    }
    kwargs.update(overrides)
    return calculate_bond_modified_duration(**kwargs)


# --- The approved contract, pinned -------------------------------------------


def test_both_bases_share_one_derivative_numerator():
    # One primitive, one derivative calculation, basis-specific denominator.
    # Same bond, same settlement, same base yield, same 1 bp central
    # difference -- so dP/dY must be bit-identical and only the division may
    # differ. If these ever diverge there are two engines, not one.
    clean = _duration(price_basis=BondOptionPriceBasis.CLEAN)
    dirty = _duration(price_basis=BondOptionPriceBasis.DIRTY)

    assert clean.price_derivative_per_unit_yield == dirty.price_derivative_per_unit_yield
    assert clean.base_yield_percent == dirty.base_yield_percent
    assert clean.bumped_clean_price_up_per_100 == dirty.bumped_clean_price_up_per_100
    assert clean.bumped_clean_price_down_per_100 == dirty.bumped_clean_price_down_per_100

    # Both price states are recorded on both results, whichever was selected.
    assert clean.clean_price_per_100 == dirty.clean_price_per_100
    assert clean.accrued_interest_per_100 == dirty.accrued_interest_per_100
    assert clean.dirty_price_per_100 == dirty.dirty_price_per_100


def test_the_clean_basis_divides_by_the_clean_price():
    result = _duration(price_basis=BondOptionPriceBasis.CLEAN)

    assert result.price_basis is BondOptionPriceBasis.CLEAN
    assert result.basis_price_per_100 == result.clean_price_per_100
    assert result.modified_duration == (
        -result.price_derivative_per_unit_yield / result.clean_price_per_100
    )
    assert result.duration_type == "MODIFIED_DURATION_CLEAN_PRICE"


def test_the_dirty_basis_divides_by_the_dirty_price():
    result = _duration(price_basis=BondOptionPriceBasis.DIRTY)

    assert result.price_basis is BondOptionPriceBasis.DIRTY
    assert result.basis_price_per_100 == result.dirty_price_per_100
    assert result.basis_price_per_100 == (
        result.clean_price_per_100 + result.accrued_interest_per_100
    )
    assert result.modified_duration == (
        -result.price_derivative_per_unit_yield / result.dirty_price_per_100
    )
    assert result.duration_type == "MODIFIED_DURATION_DIRTY_PRICE"


def test_with_accrued_interest_the_two_bases_differ_materially():
    # The fixture settles deep inside a coupon period on purpose. If the two
    # bases agreed here, every other basis test would be passing vacuously.
    clean = _duration(price_basis=BondOptionPriceBasis.CLEAN)
    dirty = _duration(price_basis=BondOptionPriceBasis.DIRTY)

    assert clean.accrued_interest_per_100 > 0
    assert clean.modified_duration != dirty.modified_duration
    assert abs(clean.modified_duration / dirty.modified_duration - 1) > 0.02


def test_with_no_accrued_interest_the_two_bases_coincide():
    # On a coupon date the clean and dirty price states are the same number,
    # so the one derivative divided by either gives the same duration. The
    # bases are not arbitrary labels -- they collapse exactly when the thing
    # that separates them is zero.
    clean = _duration(
        pricing_timestamp=_T0_ON_COUPON, price_basis=BondOptionPriceBasis.CLEAN
    )
    dirty = _duration(
        pricing_timestamp=_T0_ON_COUPON, price_basis=BondOptionPriceBasis.DIRTY
    )

    assert clean.settlement_date == _SETTLEMENT_ON_COUPON
    assert clean.accrued_interest_per_100 == 0.0
    assert clean.basis_price_per_100 == dirty.basis_price_per_100
    assert clean.modified_duration == dirty.modified_duration


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_selected_basis_survives_onto_the_result(basis):
    result = _duration(price_basis=basis)

    assert result.price_basis is basis
    assert result.duration_type == duration_type_for_basis(basis)


@pytest.mark.parametrize("basis", ["CLEAN", "DIRTY"])
def test_a_basis_given_as_a_plain_string_is_coerced_to_the_enum(basis):
    result = _duration(price_basis=basis)
    assert result.price_basis is BondOptionPriceBasis(basis)


@pytest.mark.parametrize("bad", [None, "", "   ", "GROSS", "dirty ", 1, True])
def test_a_missing_or_unknown_price_basis_fails_closed(bad):
    # Never inferred, never defaulted inside the calculation.
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(price_basis=bad)
    assert "price_basis" in str(excinfo.value)


def test_the_default_basis_is_dirty_but_the_primitive_never_applies_it():
    # Configuration and the later Workbench may default to DIRTY; the pure
    # function must still require an explicit choice.
    assert DEFAULT_BOND_OPTION_PRICE_BASIS is BondOptionPriceBasis.DIRTY

    import inspect

    signature = inspect.signature(calculate_bond_modified_duration)
    assert signature.parameters["price_basis"].default is inspect.Parameter.empty


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


# --- Irregular first coupon (Codex review, PR #212) --------------------------

# A long first coupon: issued 2026-11-20, first coupon on the nominal grid at
# 2027-08-15, settling inside that stub. Everything the duration touches --
# accrued interest, the yield solve, both bumped repricings -- must run on the
# same ICMA frame.
_IRREGULAR = IrregularFirstCoupon(
    accrual_start=date(2026, 11, 20), first_coupon=date(2027, 8, 15)
)
# t0 = Thursday 2027-01-07, so UST T+1 settles Friday 2027-01-08, inside the
# stub and well before the 2027-08-15 first coupon.
_IRREGULAR_T0 = "2027-01-07T16:00:00+00:00"
_IRREGULAR_SETTLEMENT = date(2027, 1, 8)


def _irregular(**overrides):
    return _duration(
        pricing_timestamp=_IRREGULAR_T0, schedule=_IRREGULAR, **overrides
    )


def test_the_irregular_schedule_reaches_the_accrued_interest_too():
    # The regression: accrued was computed on the regular maturity-anchored
    # grid while the numerator used the irregular ICMA cashflows, so P_dirty
    # and dP/dY came from two different schedules. Both figures below are
    # real and a few tenths apart -- the bug produced an ordinary-looking
    # duration, which is why it needs an explicit pin.
    result = _irregular()

    with_schedule = accrued_interest_per_100(
        _IRREGULAR_SETTLEMENT, _MATURITY, _COUPON, coupons_per_year=2, schedule=_IRREGULAR
    )
    without_schedule = accrued_interest_per_100(
        _IRREGULAR_SETTLEMENT, _MATURITY, _COUPON, coupons_per_year=2
    )

    assert with_schedule != without_schedule
    assert result.accrued_interest_per_100 == with_schedule
    assert result.accrued_interest_per_100 != without_schedule
    assert result.dirty_price_per_100 == _CLEAN + with_schedule


def test_the_irregular_schedule_changes_the_duration_it_produces():
    # If supplying the schedule left the answer unchanged, the test above
    # would be pinning a field nobody uses.
    assert _irregular().modified_duration != _duration(
        pricing_timestamp=_IRREGULAR_T0
    ).modified_duration


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_irregular_duration_is_still_the_declared_basis_quotient(basis):
    result = _irregular(price_basis=basis)

    assert result.modified_duration == (
        -result.price_derivative_per_unit_yield / result.basis_price_per_100
    )


def test_the_irregular_schedule_is_recorded_on_the_result():
    # A stored duration must say which cashflows produced it; otherwise an
    # irregular bond is indistinguishable from a regular one after the fact.
    result = _irregular()

    assert result.schedule_accrual_start == _IRREGULAR.accrual_start
    assert result.schedule_first_coupon == _IRREGULAR.first_coupon


def test_a_regular_bond_records_no_schedule_rather_than_inventing_one():
    result = _duration()

    assert result.schedule_accrual_start is None
    assert result.schedule_first_coupon is None


@pytest.mark.parametrize("bad", [("2026-11-20", "2027-08-15"), "2026-11-20", 7])
def test_a_schedule_that_is_not_an_irregular_first_coupon_is_refused(bad):
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(schedule=bad)
    assert "IrregularFirstCoupon" in str(excinfo.value)


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
        _duration(pricing_timestamp="2035-08-20T16:00:00+00:00")
    assert "maturity" in str(excinfo.value)


def test_a_settlement_inside_the_final_coupon_period_is_refused_on_this_modules_error():
    # The reusable primitive's own refusal -- the U.S. street convention
    # switches to simple interest there and it does not implement that. It
    # must surface as this module's error type, not as a futures error.
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(pricing_timestamp="2035-05-01T16:00:00+00:00")
    assert "final coupon period" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
def test_a_blank_security_is_refused(bad):
    with pytest.raises(BLIBondDurationError):
        _duration(security=bad)


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_a_blank_pricing_timestamp_is_refused(bad):
    with pytest.raises(BLIBondDurationError):
        _duration(pricing_timestamp=bad)


@pytest.mark.parametrize("bad", ["not-a-time", "2027-02-11 16:00 EST", "11/02/2027"])
def test_a_pricing_timestamp_that_places_no_moment_is_refused(bad):
    # NB "20270211" is *not* in this list: ISO-8601 basic format is a real
    # timestamp and `fromisoformat` accepts it, so refusing it would be a
    # false positive.
    # tS is derived from t0, so an unparseable t0 makes the settlement
    # underivable rather than merely undocumented.
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(pricing_timestamp=bad)
    assert "ISO-8601" in str(excinfo.value)


def test_the_settlement_date_is_derived_from_t0_and_cannot_be_supplied():
    # The P1 this replaced: a caller could name any pre-maturity date while
    # the result went on calling it the current spot settlement. The
    # canonical fixture named a Sunday. There is now no argument to get wrong.
    import inspect

    signature = inspect.signature(calculate_bond_modified_duration)
    assert "settlement_date" not in signature.parameters

    result = _duration()
    assert result.settlement_date == spot_settlement_date(
        date(2027, 2, 11), get_convention_profile("UST")
    )
    assert result.settlement_date == _SETTLEMENT
    assert result.settlement_date.weekday() < 5


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("UST", date(2026, 9, 11)), ("GERMAN_GOVT", date(2026, 9, 14))],
)
def test_each_profile_rolls_settlement_by_its_own_convention(profile, expected):
    # UST is T+1, German T+2 -- taken from the profile, and the German roll
    # crosses the weekend from the same Thursday.
    result = _duration(
        convention_profile=profile, pricing_timestamp="2026-09-10T16:00:00+00:00"
    )
    assert result.settlement_date == expected


def test_a_negative_coupon_is_refused():
    with pytest.raises(BLIBondDurationError):
        _duration(coupon_percent=-0.5)


# --- Provenance --------------------------------------------------------------


@pytest.mark.parametrize("basis", list(BondOptionPriceBasis))
def test_the_result_carries_everything_needed_to_redo_the_arithmetic(basis):
    result = _duration(price_basis=basis)

    # A reviewer holding only this object recomputes the duration by hand,
    # on whichever basis it declares.
    redone = -(
        (result.bumped_clean_price_up_per_100 - result.bumped_clean_price_down_per_100)
        / (2.0 * result.yield_bump_basis_points / 100.0)
        * 100.0
    ) / result.basis_price_per_100
    assert redone == result.modified_duration

    assert result.security == _SECURITY
    assert result.maturity_date == _MATURITY
    assert result.coupon_percent == _COUPON
    assert result.day_count == "ACT_ACT_BOND"
    assert result.duration_type == duration_type_for_basis(basis)
    assert result.source == "SHIORI_DERIVED"
    assert result.methodology_version == DURATION_METHODOLOGY_VERSION
    assert result.calculated_at == _CALCULATED_AT


def test_no_module_in_the_pricing_package_reads_the_system_clock():
    # The repository invariant `tests/test_pricing_engine.py` enforces over
    # the whole package, pinned here too so this module's own contribution to
    # it is visible at the point of change: a pricing result that silently
    # depends on when it ran is not reproducible, so every timestamp arrives
    # as an argument.
    from pathlib import Path

    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "datetime.now(" not in text
    assert "date.today(" not in text


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_a_blank_calculated_at_is_refused_rather_than_defaulted(bad):
    with pytest.raises(BLIBondDurationError) as excinfo:
        _duration(calculated_at=bad)
    assert "calculated_at" in str(excinfo.value)


def test_the_duration_type_names_the_basis_rather_than_leaving_it_to_magnitude():
    # Phase-1 recorded Macaulay landing within 0.004 of a clean-denominator
    # duration on a real bond, and CLEAN/DIRTY on this fixture are ~2% apart.
    # The label is the only safe discriminator for any of the three.
    assert duration_type_for_basis(BondOptionPriceBasis.CLEAN) == (
        "MODIFIED_DURATION_CLEAN_PRICE"
    )
    assert duration_type_for_basis(BondOptionPriceBasis.DIRTY) == (
        "MODIFIED_DURATION_DIRTY_PRICE"
    )
    assert "DIRTY" not in duration_type_for_basis(BondOptionPriceBasis.CLEAN)


def test_the_methodology_version_is_not_basis_specific():
    # CLEAN and DIRTY are two selections of one approved methodology, not two
    # methodologies, so the basis is a per-result field rather than part of
    # the version token.
    assert "DIRTY" not in DURATION_METHODOLOGY_VERSION
    assert "CLEAN" not in DURATION_METHODOLOGY_VERSION
    clean = _duration(price_basis=BondOptionPriceBasis.CLEAN)
    dirty = _duration(price_basis=BondOptionPriceBasis.DIRTY)
    assert clean.methodology_version == dirty.methodology_version


def test_no_bloomberg_duration_field_is_read():
    # The Phase-1 audit found no confirmed Bloomberg duration field, and
    # DUR_ADJ_MID remains a candidate with none of its semantics pinned.
    source = (module.__file__ or "").replace("\\", "/")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "DUR_ADJ" not in text.split('"""', 2)[2]
    assert _duration().source == "SHIORI_DERIVED"
