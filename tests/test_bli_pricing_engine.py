"""Tests for the BLI pricing engine (Issue #44, MVP-8 under #82).

This is now the first real `price_bli_mvp` methodology test suite, not a
callable-seam/skeleton test suite. Two layers, mirroring the pattern
already used in `test_bli_forward_clean_price.py`:

1. **Guard-rejection-path tests** (no QuantLib required): unsupported
   exercise/payoff, missing clean price, unsupported volatility basis --
   all caught by #41's guard before the engine ever touches curves or
   QuantLib-backed bond mechanics, so these always run.
2. **Pricing-path tests** (`@_requires_quantlib`): the supported success
   case, per-100-vs-notional scaling, and the shared-fixture curve-range
   gap mapping to a FAILED/ENGINE_ERROR result -- all of these call
   `forward_clean_price_per_100_for_bundle`, which composes #81's
   QuantLib-backed bond-mechanics adapter.

**Known, already-documented shared-fixture limitation (from #42's PR):**
`SYNTHETIC_BLI_MVP_INPUT_BUNDLE`'s `BOND_REFERENCE_CURVE`/
`OPTION_DISCOUNT_CURVE` nodes are only `"2Y"`/`"5Y"`, which cannot
bracket this fixture's own ~0.24-year bond option expiry. Rather than
mutating the shared fixture (out of scope for this PR) or hiding the
gap, the supported-success-path tests use a **local, test-only bundle
variant** with short-tenor curve nodes (same technique #42 used), and
the real shared fixture's own out-of-range failure becomes the
deterministic fixture for the "helper exception maps to FAILED, not a
crash" test.

**Acceptance-criteria-to-test/citation mapping:**

- supported case success + full PV:
  `test_supported_case_returns_success_with_recomputed_pv`.
- per-100 vs notional scaling asserted separately:
  `test_pv_equals_pv_per_100_times_notional_over_100`.
- shared fixture curve-range gap -> FAILED/ENGINE_ERROR:
  `test_shared_mvp_fixture_curve_range_gap_maps_to_engine_error`.
- unsupported exercise -> FAILED/UNSUPPORTED_PRODUCT:
  `test_unsupported_exercise_style_fails`.
- unsupported payoff basis -> FAILED/UNSUPPORTED_PRODUCT:
  `test_unsupported_payoff_basis_fails`.
- unsupported settlement: **not reachable** via bundle construction
  (`BondLinkedStructuredProduct.__post_init__` already rejects
  `PHYSICAL`); see
  `tests/test_bond_linked_structured_product.py::
  test_wrapper_rejects_physical_settlement` (existing, cited, not
  duplicated here).
- missing clean price -> FAILED/MISSING_MARKET_DATA (Codex P2 review of
  PR #89: a reachable data-remediation failure, distinct from an
  unsupported product shape): `test_missing_clean_price_fails`.
- unsupported volatility basis -> FAILED/UNSUPPORTED_PRODUCT (an
  unsupported basis with no conversion available, not merely absent
  data): `test_unsupported_volatility_basis_fails`.
- combined shape-and-data failure -> FAILED/UNSUPPORTED_PRODUCT wins
  over MISSING_MARKET_DATA: `test_combined_shape_and_data_failure_prefers_unsupported_product`.
- missing `volatility_input` object: **not reachable**; see
  `tests/test_bli_mvp_required_input_guard.py::
  test_missing_volatility_input_cannot_reach_guard` (existing, cited).
- missing Bond Reference Curve / Option Discount Curve: **not
  reachable** via bundle construction (`BLIMVPInputBundle.__post_init__`
  already requires both); see the existing curve-purpose-presence tests
  in `tests/test_bli_mvp_input_bundle.py` (existing, cited).
- no system date: `test_module_reads_no_system_clock`.
- no data fetching / LLM / UI:
  `test_module_defines_no_data_fetching_or_llm_or_ui_logic`.
- no yield conversion / no American / physical / Greeks:
  `test_module_defines_no_valuation_or_interpolation_or_connector_logic`,
  `test_no_greeks_produced`.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from shiori_pricing_lab.data import bli_mvp_input_bundle_builder as builder_module
from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataSnapshot,
    BLIMarketDataStatus,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing import bli_pricing_engine as bli_pricing_engine_module
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingResult, PricingStatus
from shiori_pricing_lab.products.enums import ExerciseStyle, PayoffBasis
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)


def _local_short_tenor_curve_points(currency) -> tuple[BLICurvePoint, ...]:
    """Short-tenor Bond Reference + Option Discount Curve nodes, local to this file.

    Deliberately not added to `data/bli_snapshot_fixtures.py` (out of
    scope for this PR): keeps the supported-success-path tests
    self-contained, with no change to the widely-shared MVP fixture. The
    existing `DEPOSIT_CURVE` rows from the shared snapshot are reused
    unchanged (`BLIMVPInputBundle` still requires all three purposes).
    """

    common = {
        "currency": currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "TEST_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    bond_reference_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_name="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.030), ("1Y", 0.035))
    )
    option_discount_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_name="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )
    existing_deposit_curve_nodes = tuple(
        point
        for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points
        if point.curve_purpose is BLICurvePurpose.DEPOSIT_CURVE
    )
    return bond_reference_nodes + option_discount_nodes + existing_deposit_curve_nodes


def _local_supported_bundle() -> BLIMVPInputBundle:
    local_snapshot = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
        curve_points=_local_short_tenor_curve_points(
            SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
        ),
    )
    return replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=local_snapshot)


# --- 1. Supported case: success + deterministic full PV ---------------------
#
# Codex P2 review of PR #89: the expected values below are pinned literal
# constants, independently derived by hand from Annex A's own formulas
# and the local test fixture's documented inputs -- NOT computed inside
# this test by calling forward_clean_price_per_100_for_bundle /
# discount_factor_from_continuous_zero_curve / black76_price_option_pv_per_100
# (the same production helpers price_bli_mvp itself wires), so a
# formula/unit regression in the composed methodology cannot be silently
# reproduced into the "expected" value here.
#
# Derivation (all inputs are the local test fixture's own documented
# constants -- bond XS0000000001: SEMI_ANNUAL coupon 0.0325, coupon
# period [2026-06-15, 2026-12-15], day_count ACT_ACT_ISDA; bond_option:
# valuation_date 2026-07-01, expiry_date 2026-09-29, strike 99.5, CALL,
# notional 50.0; spot clean price 101.25; sigma 0.18; local curve nodes
# BOND_REFERENCE_CURVE "1M"=0.030/"1Y"=0.035, OPTION_DISCOUNT_CURVE
# "1M"=0.028/"1Y"=0.032):
#
# 1. Accrued interest (ACT_ACT_ISDA, period entirely within the single
#    non-leap calendar year 2026, so this reduces to a plain day-count
#    ratio -- the /365 year-fraction factor cancels out of both the
#    elapsed and full-period fractions):
#      period_coupon_amount = 0.0325 * 100 / 2 = 1.625
#      full_period_days = (2026-12-15 - 2026-06-15) = 183 days
#      AI(valuation=2026-07-01) = 1.625 * (2026-07-01 - 2026-06-15).days / 183
#                                = 1.625 * 16 / 183 = 0.14207650273224043
#      AI(expiry=2026-09-29)    = 1.625 * (2026-09-29 - 2026-06-15).days / 183
#                                = 1.625 * 106 / 183 = 0.9412568306010929
#    No coupon payment date falls in (2026-07-01, 2026-09-29] (next
#    coupon is 2026-12-15), so PV(coupons before expiry) = 0.
# 2. T = (2026-09-29 - 2026-07-01).days / 365 = 90 / 365 = 0.2465753424657534
# 3. Bond Reference Curve zero rate at T, piecewise-linear between
#    ("1M"=1/12, 0.030) and ("1Y"=1.0, 0.035):
#      zero_rate = 0.030 + 0.005 * (T - 1/12) / (1 - 1/12) = 0.03089041095890411
#      DF_bondref = exp(-zero_rate * T) = 0.992412120754784
# 4. Spot dirty = 101.25 + 0.14207650273224043 = 101.39207650273224
#    Forward dirty = 101.39207650273224 / 0.992412120754784 = 102.16730971163268
#    Forward clean (F) = 102.16730971163268 - 0.9412568306010929 = 101.22605288103159
# 5. Option Discount Curve zero rate at T, piecewise-linear between
#    ("1M"=1/12, 0.028) and ("1Y"=1.0, 0.032):
#      zero_rate = 0.028 + 0.004 * (T - 1/12) / (1 - 1/12) = 0.028712328767123287
#      DF_option = exp(-zero_rate * T) = 0.9929452501091504
# 6. Black-76 (CALL), K=99.5, sigma=0.18, T=0.2465753424657534:
#      d1 = [ln(F/K) + 0.5*sigma^2*T] / (sigma*sqrt(T)) = 0.23710784472196783
#      d2 = d1 - sigma*sqrt(T) = 0.1477264087529121
#      pv_per_100 = DF_option * [F*Phi(d1) - K*Phi(d2)] = 4.474769848529296
# 7. pv = pv_per_100 * notional / 100 = 4.474769848529296 * 50.0 / 100 = 2.237384924264648
#
# (Steps 1-2 and 4-7 above were computed independently using plain
# `math`/`datetime` arithmetic, not by calling the production wrapper
# functions; the resulting values were then cross-checked once against
# the real forward_clean_price_per_100_for_bundle /
# discount_factor_from_continuous_zero_curve / black76_price_option_pv_per_100
# call path during development to confirm no transcription error -- that
# check is not part of this test file.)

_EXPECTED_FORWARD_CLEAN_PRICE_PER_100 = 101.22605288103159
_EXPECTED_TIME_TO_EXPIRY = 0.2465753424657534
_EXPECTED_OPTION_DISCOUNT_FACTOR = 0.9929452501091504
_EXPECTED_BLACK76_PV_PER_100 = 4.474769848529296
_EXPECTED_PV = 2.237384924264648


@_requires_quantlib
def test_supported_case_returns_success_with_pinned_pv():
    bundle = _local_supported_bundle()

    result = price_bli_mvp(bundle)

    assert isinstance(result, PricingResult)
    assert result.status is PricingStatus.SUCCESS
    assert result.errors == ()
    assert result.assumptions["forward_clean_price_per_100"] == pytest.approx(
        _EXPECTED_FORWARD_CLEAN_PRICE_PER_100
    )
    assert result.assumptions["time_to_expiry_year_fraction"] == pytest.approx(
        _EXPECTED_TIME_TO_EXPIRY
    )
    assert result.assumptions["option_discount_factor"] == pytest.approx(
        _EXPECTED_OPTION_DISCOUNT_FACTOR
    )
    assert result.assumptions["black76_pv_per_100"] == pytest.approx(
        _EXPECTED_BLACK76_PV_PER_100
    )
    assert result.pv == pytest.approx(_EXPECTED_PV)
    assert result.dv01 is None
    assert result.cashflows is None
    assert result.method == "black76_forward_clean_price_v1"


@_requires_quantlib
def test_pv_equals_pv_per_100_times_notional_over_100():
    bundle = _local_supported_bundle()
    result = price_bli_mvp(bundle)

    notional = bundle.product.bond_option.notional
    assert notional != 100.0  # otherwise this test would not distinguish scaling
    assert notional == 50.0

    assert result.assumptions["black76_pv_per_100"] == pytest.approx(
        _EXPECTED_BLACK76_PV_PER_100
    )
    assert result.pv == pytest.approx(
        result.assumptions["black76_pv_per_100"] * notional / 100.0
    )
    assert result.pv == pytest.approx(_EXPECTED_PV)
    assert result.pv != pytest.approx(result.assumptions["black76_pv_per_100"])
    assert result.assumptions["notional"] == notional
    assert result.assumptions["pv_scaling_formula"] == "pv = black76_pv_per_100 * notional / 100"


@_requires_quantlib
def test_success_assumptions_document_units_and_curve_usage():
    bundle = _local_supported_bundle()
    result = price_bli_mvp(bundle)

    assumptions = result.assumptions
    assert "forward_clean_price_per_100" in assumptions
    assert "black76_pv_per_100" in assumptions
    assert "notional" in assumptions
    assert "pv_scaling_formula" in assumptions
    assert "BOND_REFERENCE_CURVE" in assumptions["bond_reference_curve_purpose"]
    assert "OPTION_DISCOUNT_CURVE" in assumptions["option_discount_curve_purpose"]
    assert assumptions["volatility_basis"] in ("PRICE_VOL", "EQUIVALENT_PRICE_VOL")
    assert assumptions["position_sign_applied"] is False


@_requires_quantlib
def test_success_assumptions_document_priced_component_scope():
    bundle = _local_supported_bundle()
    result = price_bli_mvp(bundle)

    assumptions = result.assumptions
    assert assumptions["priced_component"] == "bond_option_leg"
    assert assumptions["priced_component_scope"] == "option_leg_only_not_full_structured_product"
    assert "deposit_leg" in assumptions["excluded_components"]
    assert "principal_redemption" in assumptions["excluded_components"]
    assert "physical_delivery" in assumptions["excluded_components"]


# --- 2. Shared fixture curve-range gap: helper exception -> FAILED/ENGINE_ERROR --


@_requires_quantlib
def test_shared_mvp_fixture_curve_range_gap_maps_to_engine_error():
    # SYNTHETIC_BLI_MVP_INPUT_BUNDLE passes the guard (it is exactly the
    # supported shape) but its BOND_REFERENCE_CURVE only carries "2Y"/"5Y"
    # nodes, too far out to bracket its own ~0.24-year expiry -- a known,
    # pre-existing gap (see #42's test_bli_forward_clean_price.py), reused
    # here deliberately as a real, deterministic domain-failure fixture.
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.ENGINE_ERROR
    assert "outside the node range" in result.errors[0].message
    assert result.errors[0].detail["exception_type"] == "ValueError"
    assert result.pv is None
    assert result.method == "black76_forward_clean_price_v1"


# --- 3. Guard-rejection path: unsupported exercise / payoff -----------------


def test_unsupported_exercise_style_fails():
    bond_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    product = replace(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=bond_option)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product)

    result = price_bli_mvp(bundle)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("exercise_style" in reason for reason in result.errors[0].detail["reasons"])
    assert result.pv is None
    assert result.method == "not_supported"


def test_unsupported_payoff_basis_fails():
    bond_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        payoff_basis=PayoffBasis.YIELD,
        strike_price=None,
        strike_yield=0.035,
    )
    product = replace(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=bond_option)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product)

    result = price_bli_mvp(bundle)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("payoff_basis" in reason for reason in result.errors[0].detail["reasons"])


def test_missing_clean_price_fails():
    yield_only_quote = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot.bond_quote,
        clean_price_per_100=None,
        yield_value=0.035,
    )
    snapshot = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot, bond_quote=yield_only_quote
    )
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=snapshot)

    result = price_bli_mvp(bundle)

    assert result.status is PricingStatus.FAILED
    # Codex P2 review of PR #89: a missing clean price is a reachable
    # data-remediation failure (the product shape itself is otherwise
    # fully supported), not an unsupported product shape -- so this maps
    # to MISSING_MARKET_DATA, not UNSUPPORTED_PRODUCT.
    assert result.errors[0].code is PricingErrorCode.MISSING_MARKET_DATA
    assert any(
        "clean_price_per_100" in reason for reason in result.errors[0].detail["reasons"]
    )


def test_unsupported_volatility_basis_fails():
    yield_vol_input = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot.volatility_input,
        volatility_basis=BLIVolatilityBasis.YIELD_VOL,
    )
    snapshot = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot, volatility_input=yield_vol_input
    )
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=snapshot)

    result = price_bli_mvp(bundle)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any(
        "volatility_basis" in reason for reason in result.errors[0].detail["reasons"]
    )


def test_combined_shape_and_data_failure_prefers_unsupported_product():
    # Both an unsupported exercise style AND a missing clean price are
    # present at once -- UNSUPPORTED_PRODUCT must win, since a caller
    # should not be told "just supply the missing price" when the
    # product shape itself is also unsupported.
    bond_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    product = replace(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=bond_option)

    yield_only_quote = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot.bond_quote,
        clean_price_per_100=None,
        yield_value=0.035,
    )
    snapshot = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot, bond_quote=yield_only_quote
    )

    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product, market_data_snapshot=snapshot)

    result = price_bli_mvp(bundle)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    reasons = result.errors[0].detail["reasons"]
    assert any("exercise_style" in reason for reason in reasons)
    assert any("clean_price_per_100" in reason for reason in reasons)


def test_guard_rejection_produces_no_fake_numeric_output():
    bond_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    product = replace(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=bond_option)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product)

    result = price_bli_mvp(bundle)
    assert result.pv is None
    assert result.dv01 is None
    assert result.cashflows is None
    assert result.scenario_results is None


# --- 4. Wrong input type raises TypeError ------------------------------------


def test_rejects_raw_bond_linked_structured_product():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT)


def test_rejects_raw_bond_reference_data():
    bond_reference_data = SYNTHETIC_BOND_FIXTURES[0]
    assert isinstance(bond_reference_data, BondReferenceData)
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(bond_reference_data)


def test_rejects_raw_market_data_snapshot():
    assert isinstance(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, BLIMarketDataSnapshot)
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT)


def test_rejects_none():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(None)


# --- 5. Does not mutate the bundle -------------------------------------------


def test_does_not_mutate_bundle():
    # Uses a guard-rejected bundle deliberately (not
    # SYNTHETIC_BLI_MVP_INPUT_BUNDLE directly) so this test is
    # deterministic regardless of whether QuantLib is installed --
    # guard rejection returns before the engine ever touches curves or
    # QuantLib-backed bond mechanics.
    bond_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    product = replace(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT, bond_option=bond_option)
    bundle = replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, product=product)

    before_bundle_id = bundle.bundle_id
    before_valuation_date = bundle.valuation_date
    before_product = bundle.product
    before_snapshot = bundle.market_data_snapshot

    price_bli_mvp(bundle)

    assert bundle.bundle_id == before_bundle_id
    assert bundle.valuation_date == before_valuation_date
    assert bundle.product is before_product
    assert bundle.market_data_snapshot is before_snapshot


# --- 6. Does not call the builder or the resolver ----------------------------


@_requires_quantlib
def test_does_not_call_build_bli_mvp_input_bundle(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("price_bli_mvp must not call build_bli_mvp_input_bundle")

    monkeypatch.setattr(builder_module, "build_bli_mvp_input_bundle", _fail_if_called)
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert result.status is PricingStatus.FAILED


@_requires_quantlib
def test_does_not_call_resolve_bond_reference_data(monkeypatch):
    from shiori_pricing_lab.reference_data import resolution as resolution_module

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("price_bli_mvp must not call resolve_bond_reference_data")

    monkeypatch.setattr(resolution_module, "resolve_bond_reference_data", _fail_if_called)
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert result.status is PricingStatus.FAILED


def test_module_does_not_import_builder_or_resolver_functions():
    module_names = set(dir(bli_pricing_engine_module))
    forbidden_names = {
        "build_bli_mvp_input_bundle",
        "resolve_bond_reference_data",
    }
    assert module_names.isdisjoint(forbidden_names)


# --- 7. No system date --------------------------------------------------------


def test_module_reads_no_system_clock():
    source = inspect.getsource(bli_pricing_engine_module)
    assert "date.today()" not in source
    assert "datetime.now()" not in source
    assert "utcnow()" not in source


# --- 8. No data fetching / LLM / UI ------------------------------------------


def test_module_defines_no_data_fetching_or_llm_or_ui_logic():
    module_names = set(dir(bli_pricing_engine_module))
    forbidden_names = {
        "requests",
        "httpx",
        "urllib",
        "openai",
        "anthropic",
        "render",
        "render_ui",
        "fetch_market_data",
        "fetch",
    }
    assert module_names.isdisjoint(forbidden_names)


# --- 9. Scope boundary: no interpolation/schedule/connector/yield-conversion --


def test_module_defines_no_valuation_or_interpolation_or_connector_logic():
    module_names = set(dir(bli_pricing_engine_module))
    forbidden_names = {
        "compute_pv",
        "interpolate",
        "interpolate_curve",
        "convert_yield_to_price",
        "convert_price_to_yield",
        "generate_cashflows",
        "build_schedule",
        "QuantLib",
        "register_engine",
        "PricingEngineRegistry",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_no_bli_specific_result_or_status_type_is_defined():
    module_names = set(dir(bli_pricing_engine_module))
    assert "BLIPricingResult" not in module_names
    assert "BLIPricingStatus" not in module_names


def test_does_not_register_a_product_type_engine():
    assert not hasattr(bli_pricing_engine_module, "register_engine")


# --- 10. No American / physical delivery / Greeks ----------------------------


@_requires_quantlib
def test_no_greeks_produced():
    result = price_bli_mvp(_local_supported_bundle())
    assert result.dv01 is None
    assert result.scenario_results is None


def test_module_defines_no_greeks_logic():
    module_names = set(dir(bli_pricing_engine_module))
    forbidden_names = {"delta", "gamma", "vega", "theta", "dv01", "cs01"}
    assert module_names.isdisjoint(forbidden_names)


def test_bundle_is_still_the_bli_mvp_input_bundle_type():
    assert isinstance(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, BLIMVPInputBundle)
