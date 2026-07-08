"""Tests for `pricing/bli_forward_clean_price.py` (Issue #42, MVP-6 under #82).

Two layers, deliberately kept separate:

1. **Pure formula tests** (no QuantLib import, always run) -- exercise
   `_forward_clean_price_from_components`, the private helper that
   encodes only the four lines of Annex A SS A.5.2 arithmetic over
   already-resolved plain numbers. These prove the Red-zone formula
   itself is correct even in an environment where QuantLib is not
   installed.
2. **Integration tests** (`@pytest.mark.skipif` on QuantLib availability)
   -- exercise the public `forward_clean_price_per_100` /
   `forward_clean_price_per_100_for_bundle` functions end to end,
   composing the real `coupon_flows_before` / `accrued_interest_per_100`
   (#81) and curve chain (docs/27-28) helpers. Every "golden" expected
   value in this layer is built by independently recomputing the same
   already-reviewed helper calls inside the test itself -- never a
   hand-typed magic number.

No forward-price test in this file uses the shared
`data/bli_snapshot_fixtures.py` curve fixtures for a *successful*
computation: those fixtures only carry "2Y"/"5Y" `BOND_REFERENCE_CURVE`
nodes, which cannot bracket the near-dated targets this MVP slice's own
example bond option expiry produces (see
`test_shared_mvp_fixture_curve_range_is_currently_too_short` below, which
documents this as a known, pre-existing gap rather than hiding it).
Successful integration tests instead use small, local, test-only
`BLICurvePoint` tuples built in this file -- the shared fixture file is
not modified.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import SYNTHETIC_BLI_MVP_INPUT_BUNDLE
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.pricing import bli_forward_clean_price as module
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.pricing.bli_forward_clean_price import (
    _forward_clean_price_from_components,
    forward_clean_price_per_100,
    forward_clean_price_per_100_for_bundle,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (
    BLIBondMaturityCashflowUnsupportedError,
    accrued_interest_per_100,
    coupon_flows_before,
    is_quantlib_available,
)
from shiori_pricing_lab.pricing.bli_valuation_time import year_fraction_to_expiry
from shiori_pricing_lab.products.enums import Currency
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

# The eligible FIXED_COUPON_BULLET bond (XS0000000001) already reviewed in
# reference_data.fixtures -- reused as-is, not duplicated.
_ELIGIBLE_BOND: BondReferenceData = SYNTHETIC_BOND_FIXTURES[0]


def _local_bond_reference_curve_points(
    currency: Currency, *, one_month_rate: float = 0.030, one_year_rate: float = 0.035
) -> tuple[BLICurvePoint, ...]:
    """Two BOND_REFERENCE_CURVE nodes ("1M", "1Y") -- local to this test file.

    Deliberately not added to `data/bli_snapshot_fixtures.py`: this keeps
    the successful near-dated integration tests self-contained, with no
    change to the widely-shared MVP fixture (see module docstring).
    """

    common = {
        "curve_name": "TEST_LOCAL_BOND_REFERENCE_CURVE",
        "currency": currency,
        "curve_purpose": BLICurvePurpose.BOND_REFERENCE_CURVE,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "TEST_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    return (
        BLICurvePoint(
            curve_id="TEST_LOCAL_BOND_REFERENCE_CURVE", tenor="1M", rate=one_month_rate, **common
        ),
        BLICurvePoint(
            curve_id="TEST_LOCAL_BOND_REFERENCE_CURVE", tenor="1Y", rate=one_year_rate, **common
        ),
    )


# --- 1. Pure formula tests (no QuantLib required) ----------------------------


def test_accrued_interest_at_valuation_is_added_to_spot_clean_price():
    result = _forward_clean_price_from_components(
        spot_clean_price=100.0,
        accrued_interest_at_valuation=1.5,
        coupon_pv=0.0,
        expiry_discount_factor=1.0,
        accrued_interest_at_expiry=0.0,
    )
    # spot_dirty = 100 + 1.5 = 101.5; forward_dirty = 101.5 / 1.0 = 101.5;
    # forward_clean = 101.5 - 0 = 101.5.
    assert result == pytest.approx(101.5)


def test_coupon_pv_is_subtracted_before_forward_division():
    # spot_dirty = 100; (100 - 5) / 2.0 = 47.5. If coupon_pv were wrongly
    # subtracted *after* division instead of before, the result would be
    # 100 / 2.0 - 5 = 45.0 -- a different number, so this test pins the
    # order of operations, not just the final value.
    result = _forward_clean_price_from_components(
        spot_clean_price=100.0,
        accrued_interest_at_valuation=0.0,
        coupon_pv=5.0,
        expiry_discount_factor=2.0,
        accrued_interest_at_expiry=0.0,
    )
    assert result == pytest.approx(47.5)
    assert result != pytest.approx(45.0)


def test_expiry_discount_factor_divides_the_dirty_value():
    result = _forward_clean_price_from_components(
        spot_clean_price=100.0,
        accrued_interest_at_valuation=1.0,
        coupon_pv=0.0,
        expiry_discount_factor=0.5,
        accrued_interest_at_expiry=0.0,
    )
    # spot_dirty = 101.0; forward_dirty = 101.0 / 0.5 = 202.0.
    assert result == pytest.approx(202.0)


def test_accrued_interest_at_expiry_is_subtracted():
    result = _forward_clean_price_from_components(
        spot_clean_price=100.0,
        accrued_interest_at_valuation=0.0,
        coupon_pv=0.0,
        expiry_discount_factor=1.0,
        accrued_interest_at_expiry=3.0,
    )
    assert result == pytest.approx(97.0)


def test_combined_formula_matches_annex_a_a5_2_exactly():
    spot_clean_price = 101.25
    ai_valuation = 0.42
    coupon_pv = 1.5
    expiry_df = 0.98
    ai_expiry = 0.10

    result = _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=ai_valuation,
        coupon_pv=coupon_pv,
        expiry_discount_factor=expiry_df,
        accrued_interest_at_expiry=ai_expiry,
    )

    expected = (spot_clean_price + ai_valuation - coupon_pv) / expiry_df - ai_expiry
    assert result == pytest.approx(expected)


@pytest.mark.parametrize("bad_discount_factor", [0.0, -0.5, -1.0])
def test_bad_expiry_discount_factor_fails_explicitly_not_a_number(bad_discount_factor):
    with pytest.raises(ValueError, match="expiry_discount_factor must be positive"):
        _forward_clean_price_from_components(
            spot_clean_price=100.0,
            accrued_interest_at_valuation=0.0,
            coupon_pv=0.0,
            expiry_discount_factor=bad_discount_factor,
            accrued_interest_at_expiry=0.0,
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "spot_clean_price",
        "accrued_interest_at_valuation",
        "coupon_pv",
        "expiry_discount_factor",
        "accrued_interest_at_expiry",
    ],
)
def test_non_finite_component_rejected(field_name):
    kwargs = {
        "spot_clean_price": 100.0,
        "accrued_interest_at_valuation": 0.0,
        "coupon_pv": 0.0,
        "expiry_discount_factor": 1.0,
        "accrued_interest_at_expiry": 0.0,
    }
    kwargs[field_name] = float("nan")
    with pytest.raises(ValueError, match="must be a finite number"):
        _forward_clean_price_from_components(**kwargs)


# --- 2. Integration tests: zero-coupon-before-expiry -------------------------


@_requires_quantlib
def test_zero_coupon_before_expiry_matches_recomputed_annex_a_formula():
    # Same valuation/expiry window as SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    # (2026-07-01 -> 2026-09-29): zero coupon dates fall in that window
    # for XS0000000001 (next coupon is 2026-12-15). Local short-tenor
    # curve nodes (not the shared fixture's 2Y/5Y-only curve) so the
    # near-dated target is actually in range.
    valuation_date = "2026-07-01"
    expiry_date = "2026-09-29"
    spot_clean_price = 101.25
    curve_points = _local_bond_reference_curve_points(_ELIGIBLE_BOND.currency)

    coupons = coupon_flows_before(
        _ELIGIBLE_BOND, after_date=valuation_date, on_or_before_date=expiry_date
    )
    assert coupons == ()

    ai_valuation = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=valuation_date)
    ai_expiry = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=expiry_date)
    expiry_df = discount_factor_from_continuous_zero_curve(
        curve_points,
        currency=_ELIGIBLE_BOND.currency,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        target_year_fraction=year_fraction_to_expiry(valuation_date, expiry_date),
    )
    expected = _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=ai_valuation,
        coupon_pv=0.0,
        expiry_discount_factor=expiry_df,
        accrued_interest_at_expiry=ai_expiry,
    )

    result = forward_clean_price_per_100(
        bond=_ELIGIBLE_BOND,
        spot_clean_price=spot_clean_price,
        valuation_date=valuation_date,
        expiry_date=expiry_date,
        curve_points=curve_points,
    )

    assert result == pytest.approx(expected)


# --- 3. Integration test: one-coupon-before-expiry ---------------------------


@_requires_quantlib
def test_one_coupon_before_expiry_matches_recomputed_annex_a_formula():
    # Expiry pushed past the 2026-12-15 coupon: coupon window
    # (2026-07-01, 2027-01-20] contains exactly that one coupon.
    valuation_date = "2026-07-01"
    expiry_date = "2027-01-20"
    spot_clean_price = 101.25
    curve_points = _local_bond_reference_curve_points(_ELIGIBLE_BOND.currency)

    coupons = coupon_flows_before(
        _ELIGIBLE_BOND, after_date=valuation_date, on_or_before_date=expiry_date
    )
    assert [flow.payment_date for flow in coupons] == ["2026-12-15"]

    coupon_pv = 0.0
    for flow in coupons:
        coupon_df = discount_factor_from_continuous_zero_curve(
            curve_points,
            currency=_ELIGIBLE_BOND.currency,
            curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
            target_year_fraction=year_fraction_to_expiry(valuation_date, flow.payment_date),
        )
        coupon_pv += flow.amount_per_100 * coupon_df
    assert coupon_pv > 0.0

    ai_valuation = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=valuation_date)
    ai_expiry = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=expiry_date)
    expiry_df = discount_factor_from_continuous_zero_curve(
        curve_points,
        currency=_ELIGIBLE_BOND.currency,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        target_year_fraction=year_fraction_to_expiry(valuation_date, expiry_date),
    )
    expected = _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=ai_valuation,
        coupon_pv=coupon_pv,
        expiry_discount_factor=expiry_df,
        accrued_interest_at_expiry=ai_expiry,
    )

    result = forward_clean_price_per_100(
        bond=_ELIGIBLE_BOND,
        spot_clean_price=spot_clean_price,
        valuation_date=valuation_date,
        expiry_date=expiry_date,
        curve_points=curve_points,
    )

    assert result == pytest.approx(expected)

    # The coupon-PV subtraction must actually matter -- proves it is not
    # accidentally zero or silently dropped.
    without_coupon_pv = _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=ai_valuation,
        coupon_pv=0.0,
        expiry_discount_factor=expiry_df,
        accrued_interest_at_expiry=ai_expiry,
    )
    assert result != pytest.approx(without_coupon_pv)


# --- 4. Valuation/expiry accrued-interest inclusion --------------------------


@_requires_quantlib
def test_accrued_interest_inclusion_changes_the_result():
    valuation_date = "2026-07-01"
    expiry_date = "2026-09-29"
    spot_clean_price = 101.25
    curve_points = _local_bond_reference_curve_points(_ELIGIBLE_BOND.currency)

    ai_valuation = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=valuation_date)
    ai_expiry = accrued_interest_per_100(_ELIGIBLE_BOND, as_of_date=expiry_date)
    # Both valuation and expiry fall mid-coupon-period for this bond, so
    # both must be strictly positive -- otherwise this test would not
    # actually exercise AI inclusion.
    assert ai_valuation > 0.0
    assert ai_expiry > 0.0

    expiry_df = discount_factor_from_continuous_zero_curve(
        curve_points,
        currency=_ELIGIBLE_BOND.currency,
        curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
        target_year_fraction=year_fraction_to_expiry(valuation_date, expiry_date),
    )

    result = forward_clean_price_per_100(
        bond=_ELIGIBLE_BOND,
        spot_clean_price=spot_clean_price,
        valuation_date=valuation_date,
        expiry_date=expiry_date,
        curve_points=curve_points,
    )

    without_ai = _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=0.0,
        coupon_pv=0.0,
        expiry_discount_factor=expiry_df,
        accrued_interest_at_expiry=0.0,
    )
    with_ai = _forward_clean_price_from_components(
        spot_clean_price=spot_clean_price,
        accrued_interest_at_valuation=ai_valuation,
        coupon_pv=0.0,
        expiry_discount_factor=expiry_df,
        accrued_interest_at_expiry=ai_expiry,
    )

    assert result == pytest.approx(with_ai)
    assert result != pytest.approx(without_ai)


# --- 5. Missing clean price ---------------------------------------------------


@_requires_quantlib
def test_missing_clean_price_fails_via_bundle_wrapper():
    yield_only_quote = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot.bond_quote,
        clean_price_per_100=None,
        yield_value=0.035,
    )
    snapshot = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot, bond_quote=yield_only_quote
    )
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=snapshot)

    with pytest.raises(ValueError, match="clean_price_per_100"):
        forward_clean_price_per_100_for_bundle(bundle)


# --- 6. Missing BOND_REFERENCE_CURVE ------------------------------------------


@_requires_quantlib
def test_missing_bond_reference_curve_fails_explicitly():
    # BLIMVPInputBundle.__post_init__ already requires BOND_REFERENCE_CURVE
    # presence, so no such bundle can be constructed -- this calls the
    # narrow function directly with curve_points that simply omit any
    # BOND_REFERENCE_CURVE row (only a DEPOSIT_CURVE row present), which is
    # a legitimate, reachable call to the public narrow API, not a
    # fabricated frozen-dataclass state.
    non_bond_reference_curve_points = (
        BLICurvePoint(
            curve_id="TEST_DEPOSIT_CURVE",
            curve_name="TEST_DEPOSIT_CURVE",
            currency=_ELIGIBLE_BOND.currency,
            curve_purpose=BLICurvePurpose.DEPOSIT_CURVE,
            tenor="1Y",
            rate=0.03,
            rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
            source_system="TEST_LOCAL_CURVE",
            status=BLIMarketDataStatus.ACTIVE,
        ),
    )

    with pytest.raises(ValueError, match="no curve_points match"):
        forward_clean_price_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=101.25,
            valuation_date="2026-07-01",
            expiry_date="2026-09-29",
            curve_points=non_bond_reference_curve_points,
        )


# --- 7. Maturity/principal boundary unsupported -------------------------------


@_requires_quantlib
def test_expiry_at_maturity_date_is_unsupported():
    curve_points = _local_bond_reference_curve_points(_ELIGIBLE_BOND.currency)
    with pytest.raises(BLIBondMaturityCashflowUnsupportedError):
        forward_clean_price_per_100(
            bond=_ELIGIBLE_BOND,
            spot_clean_price=101.25,
            valuation_date="2026-07-01",
            expiry_date=_ELIGIBLE_BOND.maturity_date,
            curve_points=curve_points,
        )


# --- 8. Documented existing shared-fixture curve-range gap -------------------


@_requires_quantlib
def test_shared_mvp_fixture_curve_range_is_currently_too_short():
    # SYNTHETIC_BLI_MVP_INPUT_BUNDLE's BOND_REFERENCE_CURVE only carries
    # "2Y"/"5Y" nodes, but its own bond_option expiry_date is ~0.24 years
    # from its valuation_date -- below the smallest node. This documents
    # that gap honestly (a known, pre-existing shared-fixture limitation,
    # not a defect introduced by this module) rather than silently working
    # around it or leaving it unmentioned.
    with pytest.raises(ValueError, match="outside the node range"):
        forward_clean_price_per_100_for_bundle(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)


# --- 9. No system date --------------------------------------------------------


def test_module_reads_no_system_clock():
    import inspect

    source = inspect.getsource(module)
    assert "date.today()" not in source
    assert "datetime.now()" not in source
    assert "utcnow()" not in source


# --- 10. No Black-76 / option premium / engine wiring -------------------------


def test_module_defines_no_black76_or_premium_or_engine_wiring():
    module_names = set(dir(module))
    forbidden_names = {
        "black_76",
        "black76",
        "norm_cdf",
        "option_premium",
        "premium",
        "price_bli_mvp",
        "PricingResult",
        "register_engine",
        "PricingEngineRegistry",
        "convert_yield_to_price_vol",
        "convert_price_to_yield",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_does_not_import_pricing_engine():
    import sys

    # bli_forward_clean_price must not import bli_pricing_engine anywhere
    # in its own module -- checked via the compiled module's own globals
    # rather than sys.modules (which could be populated by an unrelated
    # test import elsewhere in the same test session).
    assert "bli_pricing_engine" not in vars(module)
    assert module.__name__ in sys.modules  # sanity: module actually loaded


# --- 11. Wrong input types -----------------------------------------------------


def test_wrong_bond_type_raises_type_error():
    with pytest.raises(TypeError, match="BondReferenceData"):
        forward_clean_price_per_100(
            bond=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product,  # not a BondReferenceData
            spot_clean_price=100.0,
            valuation_date="2026-07-01",
            expiry_date="2026-09-29",
            curve_points=(),
        )


def test_wrong_bundle_type_raises_type_error():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        forward_clean_price_per_100_for_bundle(_ELIGIBLE_BOND)


def test_none_bundle_raises_type_error():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        forward_clean_price_per_100_for_bundle(None)
