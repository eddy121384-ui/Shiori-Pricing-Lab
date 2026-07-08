"""Tests for `pricing/bli_quantlib_bond_adapter.py` (docs/29 implementation slice).

Covers the four QuantLib-bond-mechanics outputs docs/29 §2/§8 scope this
adapter to (regular coupon schedule dates, per-100 coupon cashflow
amounts, accrued interest at an explicit date, and the clean/dirty
arithmetic identity), the calendar/day-count/coupon-unit/ex-dividend/
stub decisions docs/29 §2/§4/§5/§6 pin, the three Codex P2 findings on
PR #81 (accrued-interest proration against the fixed coupon amount, a
whole-grid coupon-consistency check rather than an endpoints-only one,
and refusing rather than silently truncating a coupon window that
reaches `maturity_date`), and the module-boundary checks that slice
requires so this adapter cannot silently grow curve, discount-factor,
forward-price, yield-conversion, or Black-76 logic, or get wired into
`price_bli_mvp`.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import SYNTHETIC_BLI_MVP_INPUT_BUNDLE
from shiori_pricing_lab.pricing import bli_pricing_engine as bli_pricing_engine_module
from shiori_pricing_lab.pricing import bli_quantlib_bond_adapter as adapter_module
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondCouponFlow,
    BLIBondExDividendWindowError,
    BLIBondMaturityCashflowUnsupportedError,
    BLIBondScheduleError,
    BLIQuantLibNotAvailableError,
    accrued_interest_per_100,
    coupon_flows_before,
    is_quantlib_available,
)
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingStatus
from shiori_pricing_lab.products.enums import (
    BondYieldConvention,
    BusinessDayConvention,
    Currency,
    DayCount,
    Frequency,
)
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.enums import BondStatus, BondType
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

pytest.importorskip("QuantLib")

_ELIGIBLE_BOND = next(bond for bond in SYNTHETIC_BOND_FIXTURES if bond.isin == "XS0000000001")

# Every coupon date the fixture bond's regular schedule generates, per
# docs/29 §1/§8 -- first_coupon_date through last_coupon_date inclusive.
# The final coupon-at-maturity event (2030-06-15) is deliberately excluded
# (docs/29 §5/§8): it combines with principal redemption, which this
# adapter slice does not implement.
_EXPECTED_COUPON_DATES = (
    "2025-12-15",
    "2026-06-15",
    "2026-12-15",
    "2027-06-15",
    "2027-12-15",
    "2028-06-15",
    "2028-12-15",
    "2029-06-15",
    "2029-12-15",
)
_EXPECTED_AMOUNT_PER_100 = 1.625  # 0.0325 * 100 / 2


def _odd_first_stub_bond() -> BondReferenceData:
    # Regular semi-annual first_coupon_date would be 2025-07-15
    # (issue_date + 6 months); 2025-05-15 is a short, irregular first stub.
    return BondReferenceData(
        isin="XS0TEST-ODD-FIRST",
        issuer="Synthetic Odd-First-Stub Test Issuer",
        currency=Currency.USD,
        coupon=0.04,
        coupon_frequency=Frequency.SEMI_ANNUAL,
        maturity_date="2030-01-15",
        issue_date="2025-01-15",
        day_count=DayCount.ACT_ACT_ISDA,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        redemption_amount=100.0,
        callable_flag=False,
        sinkable_flag=False,
        bond_type=BondType.FIXED_COUPON_BULLET,
        yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
        ex_dividend_days=0,
        first_coupon_date="2025-05-15",
        last_coupon_date="2029-07-15",
        status=BondStatus.ACTIVE,
    )


def _act_360_bond() -> BondReferenceData:
    # A regular semi-annual bond (grid-consistent, no stub) using ACT_360 --
    # its full-period year fraction (actual_days / 360) is never exactly
    # 1/2, unlike ACT_ACT_ISDA over a single non-leap year, which is
    # exactly what exposes the Codex P2 accrued-interest proration bug.
    return BondReferenceData(
        isin="XS0TEST-ACT360",
        issuer="Synthetic ACT/360 Test Issuer",
        currency=Currency.USD,
        coupon=0.05,
        coupon_frequency=Frequency.SEMI_ANNUAL,
        maturity_date="2030-01-15",
        issue_date="2025-01-15",
        day_count=DayCount.ACT_360,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        redemption_amount=100.0,
        callable_flag=False,
        sinkable_flag=False,
        bond_type=BondType.FIXED_COUPON_BULLET,
        yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
        ex_dividend_days=0,
        first_coupon_date="2025-07-15",
        last_coupon_date="2029-07-15",
        status=BondStatus.ACTIVE,
    )


def _inconsistent_grid_bond() -> BondReferenceData:
    # Codex P2 regression case: both endpoints individually "look regular"
    # (first_coupon_date == issue_date + 6mo; last_coupon_date ==
    # maturity_date - 6mo), but issue_date (2025-01-15) to maturity_date
    # (2030-02-15) spans 61 months, not an exact multiple of 6 -- there is
    # no single consistent regular grid connecting all four dates.
    return BondReferenceData(
        isin="XS0TEST-INCONSISTENT-GRID",
        issuer="Synthetic Inconsistent-Grid Test Issuer",
        currency=Currency.USD,
        coupon=0.04,
        coupon_frequency=Frequency.SEMI_ANNUAL,
        maturity_date="2030-02-15",
        issue_date="2025-01-15",
        day_count=DayCount.ACT_ACT_ISDA,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        redemption_amount=100.0,
        callable_flag=False,
        sinkable_flag=False,
        bond_type=BondType.FIXED_COUPON_BULLET,
        yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
        ex_dividend_days=0,
        first_coupon_date="2025-07-15",
        last_coupon_date="2029-08-15",
        status=BondStatus.ACTIVE,
    )


def _odd_last_stub_bond() -> BondReferenceData:
    # Regular semi-annual last_coupon_date would be 2029-07-15
    # (maturity_date - 6 months); 2029-03-15 is a short, irregular last stub.
    return BondReferenceData(
        isin="XS0TEST-ODD-LAST",
        issuer="Synthetic Odd-Last-Stub Test Issuer",
        currency=Currency.USD,
        coupon=0.04,
        coupon_frequency=Frequency.SEMI_ANNUAL,
        maturity_date="2030-01-15",
        issue_date="2025-01-15",
        day_count=DayCount.ACT_ACT_ISDA,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
        redemption_amount=100.0,
        callable_flag=False,
        sinkable_flag=False,
        bond_type=BondType.FIXED_COUPON_BULLET,
        yield_convention=BondYieldConvention.SEMI_ANNUAL_COMPOUND,
        ex_dividend_days=0,
        first_coupon_date="2025-07-15",
        last_coupon_date="2029-03-15",
        status=BondStatus.ACTIVE,
    )


# --- 1. QuantLib availability behavior -------------------------------------


def test_is_quantlib_available_reflects_real_environment():
    assert is_quantlib_available() is True


def test_module_wraps_quantlib_import_in_try_except_import_error():
    # Structural proof that importing this module without QuantLib
    # installed cannot raise: the only `import QuantLib` statement is
    # guarded by `try`/`except ImportError`, mirrored by the module's own
    # `is_quantlib_available()`/`ql is None` behavior exercised below (a
    # dynamic reimport-without-QuantLib test is deliberately not attempted
    # here, since reloading this already-imported module would rebind its
    # classes to new objects and silently break every other test in this
    # file that imported those classes at module load time).
    source = inspect.getsource(adapter_module)
    assert "try:\n    import QuantLib as ql" in source
    assert "except ImportError:" in source


def test_calling_functions_without_quantlib_raises_clear_error(monkeypatch):
    monkeypatch.setattr(adapter_module, "ql", None)
    assert adapter_module.is_quantlib_available() is False
    with pytest.raises(BLIQuantLibNotAvailableError):
        coupon_flows_before(
            _ELIGIBLE_BOND, after_date="2026-07-01", on_or_before_date="2026-09-29"
        )
    with pytest.raises(BLIQuantLibNotAvailableError):
        accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-07-01")


# --- 2. Schedule/coupon dates (unadjusted, NullCalendar) --------------------


def test_full_regular_window_matches_hand_written_literal_coupon_dates():
    # Deliberately asks for every coupon date the regular schedule can
    # produce (a window strictly wider than [first_coupon_date,
    # last_coupon_date]) -- this window uses NullCalendar/unadjusted dates
    # only (docs/29 §2). Several of these dates (e.g. 2029-12-15, a
    # Saturday) fall on a weekend in this synthetic data; the literal list
    # below is the unadjusted date regardless, since this adapter never
    # rolls a coupon date onto a business day.
    flows = coupon_flows_before(
        _ELIGIBLE_BOND, after_date="2025-06-15", on_or_before_date="2029-12-15"
    )
    assert [flow.payment_date for flow in flows] == list(_EXPECTED_COUPON_DATES)
    assert all(flow.amount_per_100 == pytest.approx(_EXPECTED_AMOUNT_PER_100) for flow in flows)
    assert all(isinstance(flow, BLIBondCouponFlow) for flow in flows)


def test_window_reaching_maturity_date_raises_instead_of_silently_omitting_it():
    # Codex P2 review of PR #81: the final coupon-at-maturity (2030-06-15)
    # combines with principal redemption -- out of scope for this slice
    # (docs/29 §5/§8) -- so a window reaching maturity_date must raise
    # rather than silently return the flows before it, which would
    # understate the true coupon cashflows without any signal.
    with pytest.raises(BLIBondMaturityCashflowUnsupportedError, match="maturity_date"):
        coupon_flows_before(
            _ELIGIBLE_BOND, after_date="2029-12-15", on_or_before_date="2030-06-15"
        )


def test_window_reaching_past_maturity_date_also_raises():
    with pytest.raises(BLIBondMaturityCashflowUnsupportedError, match="maturity_date"):
        coupon_flows_before(
            _ELIGIBLE_BOND, after_date="2029-12-15", on_or_before_date="2031-01-01"
        )


def test_window_ending_strictly_before_maturity_date_does_not_raise():
    # A window that ends before maturity_date is unaffected -- only a
    # window that actually reaches maturity_date is refused.
    flows = coupon_flows_before(
        _ELIGIBLE_BOND, after_date="2029-12-15", on_or_before_date="2030-06-14"
    )
    assert flows == ()


# --- 3. Zero-coupon-before-expiry / one-coupon-before-expiry windows -------


def test_existing_mvp_fixture_window_has_zero_coupons_before_expiry():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    flows = coupon_flows_before(
        _ELIGIBLE_BOND,
        after_date=bundle.valuation_date,
        on_or_before_date=bundle.product.bond_option.expiry_date,
    )
    assert flows == ()


def test_non_fixture_window_has_exactly_one_coupon_before_expiry():
    flows = coupon_flows_before(
        _ELIGIBLE_BOND, after_date="2026-07-01", on_or_before_date="2027-01-15"
    )
    assert flows == (BLIBondCouponFlow(payment_date="2026-12-15", amount_per_100=1.625),)


# --- 4. Boundary inclusion/exclusion ----------------------------------------


def test_coupon_exactly_on_after_date_is_excluded():
    flows = coupon_flows_before(
        _ELIGIBLE_BOND, after_date="2026-06-15", on_or_before_date="2026-12-15"
    )
    assert [flow.payment_date for flow in flows] == ["2026-12-15"]


def test_coupon_exactly_on_on_or_before_date_is_included():
    flows = coupon_flows_before(
        _ELIGIBLE_BOND, after_date="2026-06-14", on_or_before_date="2026-06-15"
    )
    assert [flow.payment_date for flow in flows] == ["2026-06-15"]


def test_reversed_window_raises_value_error():
    with pytest.raises(ValueError, match="must be after"):
        coupon_flows_before(_ELIGIBLE_BOND, after_date="2026-12-15", on_or_before_date="2026-06-15")


# --- 5. Hand-computed accrued interest --------------------------------------


def test_accrued_interest_is_zero_on_a_coupon_date():
    assert accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-06-15") == pytest.approx(0.0)


def test_accrued_interest_one_day_after_a_coupon_date():
    # Accrued interest is the fixed period coupon amount (1.625) prorated
    # by elapsed/full-period days -- not an independent day-count fraction
    # of the annual coupon (Codex P2 review of PR #81).
    period_start = date(2026, 6, 15)
    period_end = date(2026, 12, 15)
    elapsed_days = (date(2026, 6, 16) - period_start).days
    full_period_days = (period_end - period_start).days
    expected = _EXPECTED_AMOUNT_PER_100 * elapsed_days / full_period_days
    actual = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-06-16")
    assert actual == pytest.approx(expected)
    assert actual > 0.0


def test_accrued_interest_mid_period_matches_hand_computed_proration():
    # 2026-06-15 -> 2026-12-15 is entirely inside the single non-leap
    # calendar year 2026, so ACT/ACT ISDA's yearFraction reduces exactly
    # to actual_days / 365 for both the elapsed and full-period spans --
    # so the elapsed/full ratio reduces to elapsed_days / full_period_days,
    # matching the fixed-coupon proration formula exactly.
    period_start = date(2026, 6, 15)
    period_end = date(2026, 12, 15)
    elapsed_days = (date(2026, 9, 15) - period_start).days
    full_period_days = (period_end - period_start).days
    expected = _EXPECTED_AMOUNT_PER_100 * elapsed_days / full_period_days
    actual = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-09-15")
    assert actual == pytest.approx(expected)
    # The fixed period coupon amount (1.625) is the correct upper bound as
    # elapsed_days approaches full_period_days -- the prior, unprorated
    # formula (coupon * yearFraction * 100 = 0.819...) is a different,
    # incorrect number for this exact period (183 actual days != 182.5).
    assert actual != pytest.approx(_ELIGIBLE_BOND.coupon * (92 / 365) * 100)


def test_accrued_interest_at_valuation_and_expiry_dates_of_existing_fixture():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    period_start = date(2026, 6, 15)
    period_end = date(2026, 12, 15)
    full_period_days = (period_end - period_start).days
    valuation_days = (date(2026, 7, 1) - period_start).days
    expiry_days = (date(2026, 9, 29) - period_start).days
    expected_valuation_ai = _EXPECTED_AMOUNT_PER_100 * valuation_days / full_period_days
    expected_expiry_ai = _EXPECTED_AMOUNT_PER_100 * expiry_days / full_period_days

    assert accrued_interest_per_100(
        _ELIGIBLE_BOND, as_of_date=bundle.valuation_date
    ) == pytest.approx(expected_valuation_ai)
    assert accrued_interest_per_100(
        _ELIGIBLE_BOND, as_of_date=bundle.product.bond_option.expiry_date
    ) == pytest.approx(expected_expiry_ai)


def test_accrued_interest_never_exceeds_fixed_period_coupon_amount():
    # The proration formula guarantees AI approaches, but never exceeds,
    # the fixed period coupon amount as as_of_date approaches period_end
    # (Codex P2 review of PR #81). 2026-12-13 (two days before the next
    # coupon) is the closest date outside XS0000000001's 1-day
    # ex-dividend window this adapter will compute accrued interest for.
    period_start = date(2026, 6, 15)
    period_end = date(2026, 12, 15)
    as_of = date(2026, 12, 13)
    elapsed_days = (as_of - period_start).days
    full_period_days = (period_end - period_start).days
    expected = _EXPECTED_AMOUNT_PER_100 * elapsed_days / full_period_days

    actual = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=as_of.isoformat())
    assert actual == pytest.approx(expected)
    assert actual < _EXPECTED_AMOUNT_PER_100


def test_accrued_interest_act_360_diverges_from_naive_unprorated_formula():
    # Codex P2 regression: under a day count whose full-period year
    # fraction is not exactly 1/periods_per_year (ACT_360's 181/360 for
    # this specific semi-annual period, not 0.5), the old, unprorated
    # formula (coupon * yearFraction(period_start, as_of) * 100) gives a
    # different, wrong number from the fixed-coupon-consistent proration.
    bond = _act_360_bond()
    period_start = date(2026, 1, 15)
    period_end = date(2026, 7, 15)
    as_of = date(2026, 5, 1)
    elapsed_days = (as_of - period_start).days
    full_period_days = (period_end - period_start).days
    period_coupon_amount = bond.coupon * 100 / 2  # periods_per_year = 2

    expected_prorated = period_coupon_amount * elapsed_days / full_period_days
    naive_unprorated = bond.coupon * (elapsed_days / 360) * 100

    actual = accrued_interest_per_100(bond, as_of_date=as_of.isoformat())
    assert actual == pytest.approx(expected_prorated)
    assert actual != pytest.approx(naive_unprorated)


def test_accrued_interest_out_of_range_as_of_date_raises():
    with pytest.raises(ValueError, match="issue_date, maturity_date"):
        accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2025-01-01")
    with pytest.raises(ValueError, match="issue_date, maturity_date"):
        accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2031-01-01")


def test_accrued_interest_is_zero_at_maturity():
    assert accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2030-06-15") == pytest.approx(0.0)


# --- 6. Ex-dividend window gate ---------------------------------------------


def test_ex_dividend_window_raises_for_eligible_fixture():
    # XS0000000001 has ex_dividend_days=1 -- the single calendar day
    # immediately before a coupon date is inside the ex-dividend window.
    with pytest.raises(BLIBondExDividendWindowError, match="ex-dividend"):
        accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-06-14")


def test_coupon_date_itself_is_not_inside_the_ex_dividend_window():
    # The coupon date resets accrual to zero and is not itself gated.
    assert accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-06-15") == pytest.approx(0.0)


def test_day_after_coupon_date_is_not_inside_the_ex_dividend_window():
    accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-06-16")  # must not raise


# --- 7. Irregular stub rejection ---------------------------------------------


def test_odd_first_stub_bond_raises_on_coupon_flows_before():
    with pytest.raises(BLIBondScheduleError, match="irregular"):
        coupon_flows_before(
            _odd_first_stub_bond(), after_date="2025-01-15", on_or_before_date="2029-07-15"
        )


def test_odd_first_stub_bond_raises_on_accrued_interest():
    with pytest.raises(BLIBondScheduleError, match="irregular"):
        accrued_interest_per_100(_odd_first_stub_bond(), as_of_date="2026-01-01")


def test_odd_last_stub_bond_raises_on_coupon_flows_before():
    with pytest.raises(BLIBondScheduleError, match="irregular"):
        coupon_flows_before(
            _odd_last_stub_bond(), after_date="2025-01-15", on_or_before_date="2029-07-15"
        )


def test_odd_last_stub_bond_raises_on_accrued_interest():
    with pytest.raises(BLIBondScheduleError, match="irregular"):
        accrued_interest_per_100(_odd_last_stub_bond(), as_of_date="2026-01-01")


def test_inconsistent_grid_bond_raises_on_coupon_flows_before():
    # Codex P2 regression: first_coupon_date/last_coupon_date each look
    # regular against their own endpoint in isolation, but issue_date to
    # maturity_date is not an exact multiple of the coupon period -- no
    # single consistent grid connects all four dates.
    with pytest.raises(BLIBondScheduleError, match="irregular"):
        coupon_flows_before(
            _inconsistent_grid_bond(), after_date="2025-01-15", on_or_before_date="2029-08-15"
        )


def test_inconsistent_grid_bond_raises_on_accrued_interest():
    with pytest.raises(BLIBondScheduleError, match="irregular"):
        accrued_interest_per_100(_inconsistent_grid_bond(), as_of_date="2026-01-01")


# --- 8. Clean/dirty arithmetic identity --------------------------------------


def test_clean_dirty_identity_at_valuation_and_expiry_dates():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    clean_price = 101.25  # matches the existing MVP fixture's spot clean price

    for as_of in (bundle.valuation_date, bundle.product.bond_option.expiry_date):
        accrued = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=as_of)
        dirty_price = clean_price + accrued
        assert dirty_price - clean_price == pytest.approx(accrued)


# --- 9. No system date -------------------------------------------------------


def test_module_source_does_not_use_system_date():
    source = inspect.getsource(adapter_module)
    assert "date.today(" not in source
    assert "datetime.now(" not in source


def test_repeated_calls_with_identical_arguments_are_identical():
    first = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-09-15")
    second = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date="2026-09-15")
    assert first == second


# --- 10. Module-boundary guards ----------------------------------------------


def test_module_does_not_import_curve_or_bundle_or_result_types():
    # Mirrors the precedent in tests/test_bli_curve_selector.py: scan only
    # actual import statements, not the whole source (this module's own
    # docstring legitimately names these modules/types in prose, listing
    # exactly what it does not import).
    import_lines = [
        line
        for line in inspect.getsource(adapter_module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    forbidden_substrings = (
        "bli_curve_tenor",
        "bli_curve_selector",
        "bli_zero_curve_nodes",
        "bli_zero_rate_interpolation",
        "bli_discount_factor",
        "bli_curve_discount_factor",
        "bli_valuation_time",
        "BLIMVPInputBundle",
        "BLIMarketDataSnapshot",
        "PricingResult",
        "bli_pricing_engine",
    )
    for name in forbidden_substrings:
        assert not any(name in line for line in import_lines)


def test_module_defines_no_curve_or_pricing_logic():
    module_names = set(dir(adapter_module))
    forbidden_names = {
        "compute_pv",
        "interpolate",
        "interpolate_curve",
        "discount_factor",
        "forward_price",
        "forward_clean_price",
        "convert_yield_to_price",
        "convert_price_to_yield",
        "black76",
        "black_76",
        "volatility",
        "price_bli_mvp",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_pricing_engine_module_does_not_import_this_adapter():
    source = inspect.getsource(bli_pricing_engine_module)
    assert "bli_quantlib_bond_adapter" not in source


def test_price_bli_mvp_behavior_unchanged_for_existing_fixture():
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT


# --- 11. Type / boundary cases -----------------------------------------------


def test_non_bond_reference_data_raises_type_error():
    with pytest.raises(TypeError, match="BondReferenceData"):
        coupon_flows_before(
            "not-a-bond", after_date="2026-07-01", on_or_before_date="2026-09-29"
        )
    with pytest.raises(TypeError, match="BondReferenceData"):
        accrued_interest_per_100("not-a-bond", as_of_date="2026-07-01")


@pytest.mark.parametrize("bad_date", ["2026/07/01", "not-a-date", None, "20260701"])
def test_malformed_date_strings_raise(bad_date):
    with pytest.raises(ValueError):
        accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=bad_date)
