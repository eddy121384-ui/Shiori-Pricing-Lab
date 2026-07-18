"""Tests for the headless standalone bond-option JSON workflow (Issue #97, PR A;
Issue #125 benchmark comparison / implied PRICE_VOL extension).

The workflow parses one local JSON case (the approved envelope), constructs
existing typed objects, and drives them through the only approved path --
``build_bli_standalone_option_request`` then
``price_bli_mvp_standalone_option`` -- returning the existing request/result
plus a bounded verbatim display context. These tests prove that path is
deterministic and offline, that the display copies values verbatim (no
pricing math, no fabricated replacement), that ``retrieved_at`` stays
separate from ``source_as_of``, and that every envelope/schema failure
surfaces explicitly.

The bundled ``examples/standalone_option_case.json`` is **sanitized
synthetic market-shaped** data derived from existing reviewed fixture
economics -- it is **not** Bloomberg or real-market validation.

The Issue #125 section below tests the separate benchmark JSON parser and
the bounded ``price_standalone_option_case_with_benchmark`` orchestration
(prices via the unchanged ``price_standalone_option_case``, then calls the
existing, unmodified ``compare_bli_benchmark`` and
``calibrate_bli_implied_price_vol`` exactly once each). The bundled
``examples/standalone_option_benchmark.json`` is likewise sanitized
synthetic data -- its ``premium_per_100`` is deliberately set to the exact
model fair premium the pricing example produces, so the comparison and
calibration outcomes are pinned and deterministic, not a claim of Bloomberg
validation.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from shiori_pricing_lab.app import standalone_option_workbench as workbench_module
from shiori_pricing_lab.app.standalone_option_workbench import (
    build_benchmark_from_standalone_option_benchmark_case,
    build_request_from_standalone_option_case,
    prepare_standalone_display,
    price_standalone_option_case,
    price_standalone_option_case_with_benchmark,
)
from shiori_pricing_lab.data.bli_benchmark_quote import BLIBenchmarkQuote, BLIBenchmarkQuoteSide
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    build_bli_standalone_option_request,
)
from shiori_pricing_lab.pricing.bli_benchmark_comparison import (
    BLIBenchmarkComparisonReason,
    BLIBenchmarkComparisonStatus,
    compare_bli_benchmark,
)
from shiori_pricing_lab.pricing.bli_implied_price_vol_calibration import (
    BLIImpliedPriceVolCalibrationReason,
    BLIImpliedPriceVolCalibrationStatus,
    calibrate_bli_implied_price_vol,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp_standalone_option
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingStatus
from shiori_pricing_lab.products.enums import TreasuryFTPQuoteSide
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"

# Pinned expected OVME values for the example envelope (explicit MID forward
# of 101.30; reporting_date == valuation_date so DF-to-reporting is 1.0 and the
# effective DF equals the pricing-to-option-settlement DF at 2026-10-01).
_EXPECTED_FORWARD_CLEAN_PRICE_PER_100 = 101.30
_EXPECTED_TIME_TO_EXPIRY = 0.2465753424657534
_EXPECTED_EFFECTIVE_DF = 0.9927830612383566
_EXPECTED_BLACK76_PV_PER_100 = 4.551011126839255
_EXPECTED_PV = 2.2755055634196273


def _example_text() -> str:
    return _EXAMPLE_PATH.read_text(encoding="utf-8")


def _example_envelope() -> dict:
    return json.loads(_example_text())


def _direct_reference_request() -> BLIStandaloneBondOptionRequest:
    """Build the same case directly from reviewed fixtures (no JSON path).

    Same economics the example JSON was generated from, so the JSON workflow
    result must equal this direct builder+engine result.
    """

    common = {
        "currency": SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT.bond_option.currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "SANITIZED_SYNTHETIC_MARKET_SOURCE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    curve_points = tuple(
        BLICurvePoint(
            curve_id="SANITIZED_OPTION_DISCOUNT_CURVE",
            curve_name="SANITIZED_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )
    forward_clean_price_input = BLIForwardCleanPriceInput(
        forward_clean_price_per_100=101.30,
        quote_side=TreasuryFTPQuoteSide.MID,
        source_system="SANITIZED_SYNTHETIC_MARKET_SOURCE",
        status=BLIMarketDataStatus.ACTIVE,
    )
    return build_bli_standalone_option_request(
        bond_option=SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT.bond_option,
        bond_reference_data_universe=SYNTHETIC_BOND_FIXTURES,
        valuation_date="2026-07-01",
        as_of_timestamp="2026-07-01T16:00:00Z",
        source_system="SANITIZED_SYNTHETIC_MARKET_SOURCE",
        snapshot_id="SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001",
        snapshot_status=BLIMarketDataStatus.ACTIVE,
        bond_quote=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.bond_quote,
        curve_points=curve_points,
        volatility_input=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input,
        credit_spread_input=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.credit_spread_input,
        forward_clean_price_input=forward_clean_price_input,
        pricing_timestamp="2026-07-01T16:00:00Z",
        expiry_timestamp="2026-09-29T16:00:00Z",
        reporting_date="2026-07-01",
        forward_settlement_date="2026-10-01",
        option_settlement_date="2026-10-01",
        deposit_rate_observation=None,
        bond_reference_source_name="SANITIZED_SYNTHETIC_REFERENCE_UNIVERSE",
    )


# --- 1. Deterministic load + build -------------------------------------------


def test_example_builds_a_standalone_request():
    request = build_request_from_standalone_option_case(_example_text())
    assert isinstance(request, BLIStandaloneBondOptionRequest)


def test_example_loads_twice_to_equal_requests():
    first = build_request_from_standalone_option_case(_example_text())
    second = build_request_from_standalone_option_case(_example_text())
    assert first == second


def test_accepts_string_or_already_parsed_mapping():
    from_text = build_request_from_standalone_option_case(_example_text())
    from_dict = build_request_from_standalone_option_case(_example_envelope())
    assert from_text == from_dict


# --- 2. Reaches the engine, pinned premium + verbatim display ----------------


@_requires_quantlib
def test_example_reaches_engine_and_reproduces_pinned_premium():
    _request, result, display = price_standalone_option_case(_example_text())

    assert result.status is PricingStatus.SUCCESS
    assert result.pv == pytest.approx(_EXPECTED_PV)
    assert display["forward_clean_price_per_100"] == pytest.approx(
        _EXPECTED_FORWARD_CLEAN_PRICE_PER_100
    )
    assert display["time_to_expiry_year_fraction"] == pytest.approx(_EXPECTED_TIME_TO_EXPIRY)
    assert display["effective_reporting_date_discount_factor"] == pytest.approx(
        _EXPECTED_EFFECTIVE_DF
    )
    assert display["black76_pv_per_100"] == pytest.approx(_EXPECTED_BLACK76_PV_PER_100)


@_requires_quantlib
def test_workflow_result_equals_direct_builder_and_engine_output():
    _request, workflow_result, _display = price_standalone_option_case(_example_text())

    direct_request = _direct_reference_request()
    direct_result = price_bli_mvp_standalone_option(direct_request)

    assert workflow_result == direct_result


@_requires_quantlib
def test_repeated_pricing_is_deterministic():
    _r1, res1, _d1 = price_standalone_option_case(_example_text())
    _r2, res2, _d2 = price_standalone_option_case(_example_text())
    assert res1 == res2


@_requires_quantlib
def test_display_copies_result_values_verbatim():
    _request, result, display = price_standalone_option_case(_example_text())

    # Every display number is a verbatim read from the result.
    assert display["total_notional_model_fair_premium"] == result.pv
    assumptions = result.assumptions
    assert display["model_fair_premium_per_100"] == assumptions["black76_pv_per_100"]
    assert display["black76_pv_per_100"] == assumptions["black76_pv_per_100"]
    assert display["forward_clean_price_per_100"] == assumptions["forward_clean_price_per_100"]
    assert (
        display["effective_reporting_date_discount_factor"]
        == assumptions["effective_reporting_date_discount_factor"]
    )
    assert display["engine_name"] == result.engine_name
    assert display["engine_version"] == result.engine_version
    assert display["result_currency"] == result.result_currency
    assert display["assumptions"] == result.assumptions
    assert display["excluded_components"] == result.assumptions["excluded_components"]


@_requires_quantlib
def test_per_100_and_total_premium_are_separate_fields():
    _request, result, display = price_standalone_option_case(_example_text())
    per_100 = display["model_fair_premium_per_100"]
    total = display["total_notional_model_fair_premium"]
    assert per_100 == pytest.approx(_EXPECTED_BLACK76_PV_PER_100)
    assert total == pytest.approx(_EXPECTED_PV)
    assert per_100 != total  # notional 50 != 100, so they must differ


# --- 3. Provenance / identity preserved verbatim through the workflow --------


def test_workflow_preserves_provenance_quote_side_and_curve_identity():
    request = build_request_from_standalone_option_case(_example_text())
    snapshot = request.market_data_snapshot

    assert snapshot.source_system == "SANITIZED_SYNTHETIC_MARKET_SOURCE"
    assert snapshot.as_of_timestamp == "2026-07-01T16:00:00Z"  # source-as-of unchanged
    assert snapshot.snapshot_id == "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001"
    expected_quote_side = SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.bond_quote.quote_side
    assert snapshot.bond_quote.quote_side == expected_quote_side

    # OVME alignment (Issue #94): the explicit forward input replaces the
    # Bond Reference Curve; the example carries only the Option Discount Curve.
    by_id = {(p.curve_id, p.curve_purpose) for p in snapshot.curve_points}
    assert ("SANITIZED_OPTION_DISCOUNT_CURVE", BLICurvePurpose.OPTION_DISCOUNT_CURVE) in by_id
    assert all(
        p.curve_purpose is BLICurvePurpose.OPTION_DISCOUNT_CURVE for p in snapshot.curve_points
    )
    # Forward input provenance + side coherence preserved.
    assert snapshot.forward_clean_price_input.quote_side == snapshot.bond_quote.quote_side


# --- 4. retrieved_at stays separate, caller-supplied, defaults to None -------


def test_retrieved_at_defaults_to_none_and_is_separate_from_source_as_of():
    # Uses a guard-FAILED case so this test needs no QuantLib.
    envelope = _example_envelope()
    envelope["volatility_input"] = {
        **envelope["volatility_input"],
        "volatility_basis": "YIELD_VOL",
    }
    request = build_request_from_standalone_option_case(envelope)
    result = price_bli_mvp_standalone_option(request)

    default_display = prepare_standalone_display(result, request)
    assert default_display["retrieved_at"] is None
    assert default_display["source_as_of"] == "2026-07-01T16:00:00Z"

    supplied_display = prepare_standalone_display(
        result, request, retrieved_at="2026-07-11T09:00:00Z"
    )
    assert supplied_display["retrieved_at"] == "2026-07-11T09:00:00Z"
    # retrieved_at never overwrites source-as-of.
    assert supplied_display["source_as_of"] == "2026-07-01T16:00:00Z"


# --- 5. Envelope failure contract --------------------------------------------


def test_malformed_json_raises_json_decode_error():
    with pytest.raises(json.JSONDecodeError):
        build_request_from_standalone_option_case("{ not valid json ")


def test_missing_required_top_level_key_fails_explicitly():
    envelope = _example_envelope()
    del envelope["bond_quote"]
    with pytest.raises(ValueError, match="missing required top-level key"):
        build_request_from_standalone_option_case(envelope)


def test_unknown_top_level_key_fails_explicitly():
    envelope = _example_envelope()
    envelope["retrieved_at"] = "2026-07-11T09:00:00Z"  # not part of the envelope
    with pytest.raises(ValueError, match="unknown top-level key"):
        build_request_from_standalone_option_case(envelope)


def test_non_object_top_level_fails_explicitly():
    with pytest.raises(ValueError, match="must be a JSON object"):
        build_request_from_standalone_option_case("[1, 2, 3]")


def test_universe_must_be_an_array():
    envelope = _example_envelope()
    envelope["bond_reference_data_universe"] = envelope["bond_reference_data_universe"][0]
    with pytest.raises(ValueError, match="must be a JSON array"):
        build_request_from_standalone_option_case(envelope)


# --- 6. Nested schema/builder failures propagate unremapped ------------------


def test_bad_enum_propagates_from_constructor():
    envelope = _example_envelope()
    envelope["bond_option"] = {**envelope["bond_option"], "option_type": "NOT_AN_OPTION_TYPE"}
    with pytest.raises(ValueError, match="option_type"):
        build_request_from_standalone_option_case(envelope)


def test_unknown_nested_field_propagates_as_type_error():
    envelope = _example_envelope()
    envelope["bond_option"] = {**envelope["bond_option"], "not_a_real_field": 1}
    with pytest.raises(TypeError):
        build_request_from_standalone_option_case(envelope)


def test_wrong_identifier_propagates_from_request_contract():
    envelope = _example_envelope()
    envelope["bond_quote"] = {**envelope["bond_quote"], "isin": "XS0000000009"}
    with pytest.raises(ValueError, match="does not exactly match"):
        build_request_from_standalone_option_case(envelope)


def test_wrong_currency_propagates_from_request_contract():
    envelope = _example_envelope()
    envelope["bond_option"] = {**envelope["bond_option"], "currency": "EUR"}
    with pytest.raises(ValueError, match="does not match"):
        build_request_from_standalone_option_case(envelope)


def test_future_as_of_propagates_from_request_contract():
    envelope = _example_envelope()
    envelope["as_of_timestamp"] = "2026-07-02T00:00:00Z"
    with pytest.raises(ValueError, match="no-look-ahead"):
        build_request_from_standalone_option_case(envelope)


# --- 7. Pricing FAILED returns no replacement value --------------------------


def test_pricing_failed_preserves_none_pv_and_errors():
    envelope = _example_envelope()
    envelope["volatility_input"] = {
        **envelope["volatility_input"],
        "volatility_basis": "YIELD_VOL",
    }
    _request, result, display = price_standalone_option_case(envelope)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    # No fabricated replacement value anywhere in the display context.
    assert result.pv is None
    assert display["status"] == "FAILED"
    assert display["total_notional_model_fair_premium"] is None
    assert display["model_fair_premium_per_100"] is None
    assert display["forward_clean_price_per_100"] is None
    assert display["errors"][0]["code"] == "UNSUPPORTED_PRODUCT"


def test_failed_display_preserves_structured_error_detail_verbatim():
    # Sophira review of PR #107: each displayed error must preserve the full
    # structured PricingMessage (code, message, AND detail) verbatim -- detail
    # carries actionable machine-readable context (product_id / reasons /
    # exception_type) and must not be dropped or remapped.
    envelope = _example_envelope()
    envelope["volatility_input"] = {
        **envelope["volatility_input"],
        "volatility_basis": "YIELD_VOL",
    }
    _request, result, display = price_standalone_option_case(envelope)

    assert result.status is PricingStatus.FAILED
    assert len(display["errors"]) == len(result.errors)
    for displayed, original in zip(display["errors"], result.errors, strict=True):
        assert displayed["code"] == original.code.value
        assert displayed["message"] == original.message
        assert displayed["detail"] == original.detail
    # detail actually carries content (proves it is not silently dropped).
    assert display["errors"][0]["detail"]["product_id"]
    assert display["errors"][0]["detail"]["reasons"]


# --- 8. No provider / network / clock / pricing math in the app layer --------


def test_module_has_no_pricing_math_provider_or_system_clock():
    source = inspect.getsource(workbench_module)
    for forbidden in (
        "datetime.now(",
        "date.today(",
        "utcnow(",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "read_csv",
        "pandas",
        "numpy",
        "math.exp",
        "math.log",
        "norm.cdf",
        "QuantLib",
    ):
        assert forbidden not in source, f"unexpected reference to {forbidden!r}"


def test_module_does_not_shortcut_the_builder():
    # The only request-construction path is build_bli_standalone_option_request;
    # the module must never call BLIStandaloneBondOptionRequest(...) directly.
    source = inspect.getsource(workbench_module)
    assert "BLIStandaloneBondOptionRequest(" not in source
    assert "build_bli_standalone_option_request(" in source
    assert "price_bli_mvp_standalone_option(" in source


# ==================================================================================
# Issue #125: benchmark comparison / implied PRICE_VOL orchestration.
# ==================================================================================

_EXAMPLE_BENCHMARK_PATH = (
    Path(__file__).resolve().parents[1] / "examples" / "standalone_option_benchmark.json"
)
_EXPECTED_IMPLIED_PRICE_VOL = 0.18  # the example's own PRICE_VOL input


def _example_benchmark_text() -> str:
    return _EXAMPLE_BENCHMARK_PATH.read_text(encoding="utf-8")


def _example_benchmark_envelope() -> dict:
    return json.loads(_example_benchmark_text())


# --- 9. Benchmark JSON parser: valid case reconstructs the frozen dataclass -------


def test_valid_benchmark_json_reconstructs_expected_benchmark_quote():
    benchmark = build_benchmark_from_standalone_option_benchmark_case(_example_benchmark_text())
    assert isinstance(benchmark, BLIBenchmarkQuote)
    envelope = _example_benchmark_envelope()
    assert benchmark == BLIBenchmarkQuote(**envelope)


def test_benchmark_json_accepts_string_or_already_parsed_mapping():
    from_text = build_benchmark_from_standalone_option_benchmark_case(_example_benchmark_text())
    from_dict = build_benchmark_from_standalone_option_benchmark_case(
        _example_benchmark_envelope()
    )
    assert from_text == from_dict


def test_benchmark_json_loads_twice_to_equal_quotes():
    first = build_benchmark_from_standalone_option_benchmark_case(_example_benchmark_text())
    second = build_benchmark_from_standalone_option_benchmark_case(_example_benchmark_text())
    assert first == second


# --- 10. Benchmark envelope failure contract ---------------------------------------


def test_benchmark_missing_top_level_key_fails_explicitly():
    envelope = _example_benchmark_envelope()
    del envelope["premium_per_100"]
    with pytest.raises(ValueError, match="missing required top-level key"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_missing_notes_key_fails_explicitly():
    # notes is optional ON THE DATACLASS but required PRESENT (nullable) on
    # this strict envelope -- omitting the key entirely must still fail.
    envelope = _example_benchmark_envelope()
    del envelope["notes"]
    with pytest.raises(ValueError, match="missing required top-level key"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_null_notes_is_accepted():
    envelope = {**_example_benchmark_envelope(), "notes": None}
    benchmark = build_benchmark_from_standalone_option_benchmark_case(envelope)
    assert benchmark.notes is None


def test_benchmark_unknown_top_level_key_fails_explicitly():
    envelope = {**_example_benchmark_envelope(), "retrieved_at_alias": "2026-07-01T16:05:00Z"}
    with pytest.raises(ValueError, match="unknown top-level key"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_non_object_top_level_fails_explicitly():
    with pytest.raises(ValueError, match="must be a JSON object"):
        build_benchmark_from_standalone_option_benchmark_case("[1, 2, 3]")


def test_benchmark_malformed_json_raises_json_decode_error():
    with pytest.raises(json.JSONDecodeError):
        build_benchmark_from_standalone_option_benchmark_case("{ not valid json ")


# --- 11. Malformed enums/values propagate the existing constructor errors ----------


def test_benchmark_bad_quote_side_propagates_from_constructor():
    envelope = {**_example_benchmark_envelope(), "quote_side": "NOT_A_SIDE"}
    with pytest.raises(ValueError, match="quote_side"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_negative_premium_propagates_from_constructor():
    envelope = {**_example_benchmark_envelope(), "premium_per_100": -1.0}
    with pytest.raises(ValueError, match="premium_per_100"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_blank_benchmark_id_propagates_from_constructor():
    envelope = {**_example_benchmark_envelope(), "benchmark_id": ""}
    with pytest.raises(ValueError, match="benchmark_id"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_unknown_nested_type_propagates_as_type_error():
    envelope = {**_example_benchmark_envelope(), "premium_per_100": "not-a-number"}
    with pytest.raises((TypeError, ValueError)):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


# --- 12. active_quote_side is mandatory, no hidden default -------------------------


def test_active_quote_side_has_no_default_and_is_keyword_only():
    with pytest.raises(TypeError):
        price_standalone_option_case_with_benchmark(  # type: ignore[call-arg]
            _example_text(), _example_benchmark_text()
        )


# --- 13. Successful bounded workflow equals direct calls ---------------------------


@_requires_quantlib
def test_bounded_workflow_equals_direct_pricing_comparison_and_calibration_calls():
    (
        request,
        result,
        benchmark,
        comparison,
        calibration,
        display,
    ) = price_standalone_option_case_with_benchmark(
        _example_text(), _example_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    direct_request, direct_result, _direct_display = price_standalone_option_case(_example_text())
    direct_benchmark = build_benchmark_from_standalone_option_benchmark_case(
        _example_benchmark_text()
    )
    direct_comparison = compare_bli_benchmark(
        direct_result, direct_request, direct_benchmark, active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    direct_calibration = calibrate_bli_implied_price_vol(
        direct_request, direct_benchmark, active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    assert request == direct_request
    assert result == direct_result
    assert benchmark == direct_benchmark
    assert comparison == direct_comparison
    assert calibration == direct_calibration


@_requires_quantlib
def test_bounded_workflow_accepts_raw_string_quote_side():
    result_via_string = price_standalone_option_case_with_benchmark(
        _example_text(), _example_benchmark_text(), active_quote_side="MID"
    )
    result_via_enum = price_standalone_option_case_with_benchmark(
        _example_text(), _example_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    assert result_via_string[3] == result_via_enum[3]  # comparison
    assert result_via_string[4] == result_via_enum[4]  # calibration


# --- 14. Comparison and calibration are each called exactly once -------------------


@_requires_quantlib
def test_comparison_and_calibration_are_each_called_exactly_once(monkeypatch):
    call_counts = {"compare": 0, "calibrate": 0}
    real_compare = workbench_module.compare_bli_benchmark
    real_calibrate = workbench_module.calibrate_bli_implied_price_vol

    def _counting_compare(*args, **kwargs):
        call_counts["compare"] += 1
        return real_compare(*args, **kwargs)

    def _counting_calibrate(*args, **kwargs):
        call_counts["calibrate"] += 1
        return real_calibrate(*args, **kwargs)

    monkeypatch.setattr(workbench_module, "compare_bli_benchmark", _counting_compare)
    monkeypatch.setattr(workbench_module, "calibrate_bli_implied_price_vol", _counting_calibrate)

    price_standalone_option_case_with_benchmark(
        _example_text(), _example_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    assert call_counts["compare"] == 1
    assert call_counts["calibrate"] == 1


# --- 15. The synthetic example benchmark produces the pinned outcomes --------------


@_requires_quantlib
def test_example_benchmark_produces_pinned_pass_comparison():
    _r, _res, _b, comparison, _c, _d = price_standalone_option_case_with_benchmark(
        _example_text(), _example_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    assert comparison.status is BLIBenchmarkComparisonStatus.PASS
    assert comparison.reason is BLIBenchmarkComparisonReason.COMPARABLE
    assert comparison.relative_residual == pytest.approx(0.0, abs=1e-12)
    assert comparison.model_fair_premium_per_100 == pytest.approx(_EXPECTED_BLACK76_PV_PER_100)
    assert comparison.benchmark_premium_per_100 == pytest.approx(_EXPECTED_BLACK76_PV_PER_100)


@_requires_quantlib
def test_example_benchmark_recovers_expected_implied_price_vol_deterministically():
    _r, _res, _b, _comp, calibration, _d = price_standalone_option_case_with_benchmark(
        _example_text(), _example_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    assert calibration.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert calibration.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    assert calibration.solver_result.implied_price_vol == pytest.approx(
        _EXPECTED_IMPLIED_PRICE_VOL, abs=1e-5
    )

    # Repeat run is deterministic.
    _r2, _res2, _b2, _comp2, calibration2, _d2 = price_standalone_option_case_with_benchmark(
        _example_text(), _example_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    assert calibration.solver_result.implied_price_vol == pytest.approx(
        calibration2.solver_result.implied_price_vol, abs=1e-12
    )


# --- 16. NON_COMPARABLE / calibration failure display without fabricated values ---


@_requires_quantlib
def test_mismatched_product_id_yields_non_comparable_and_calibration_failed_no_fabrication():
    mismatched_envelope = {**_example_benchmark_envelope(), "product_id": "OTHER-PRODUCT-ID"}
    _r, _res, benchmark, comparison, calibration, display = (
        price_standalone_option_case_with_benchmark(
            _example_text(), mismatched_envelope, active_quote_side=BLIBenchmarkQuoteSide.MID
        )
    )

    assert comparison.status is BLIBenchmarkComparisonStatus.NON_COMPARABLE
    assert comparison.reason is BLIBenchmarkComparisonReason.PRODUCT_ID_MISMATCH
    assert comparison.signed_residual_per_100 is None
    assert comparison.absolute_residual_per_100 is None
    assert comparison.relative_residual is None

    assert calibration.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert calibration.reason is BLIImpliedPriceVolCalibrationReason.PRODUCT_ID_MISMATCH
    assert calibration.solver_result is None
    assert calibration.forward_dirty_price_per_100 is None

    # No fabricated replacement values anywhere in the merged display context.
    assert display["comparison"]["relative_residual"] is None
    assert display["comparison"]["signed_residual_per_100"] is None
    assert display["calibration"]["implied_price_vol"] is None
    assert display["calibration"]["solver_status"] is None


@_requires_quantlib
def test_solver_economic_failure_displays_without_fabricated_implied_vol():
    infeasible_envelope = {
        **_example_benchmark_envelope(),
        "premium_per_100": 0.0,
        "total_premium": 0.0,
    }
    _r, _res, _b, comparison, calibration, display = price_standalone_option_case_with_benchmark(
        _example_text(), infeasible_envelope, active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    assert calibration.status is BLIImpliedPriceVolCalibrationStatus.FAILED
    assert calibration.reason is BLIImpliedPriceVolCalibrationReason.SOLVER_FAILED
    assert calibration.solver_result is not None
    assert calibration.solver_result.implied_price_vol is None
    # Resolution itself succeeded (this is a solver failure, not a resolution failure).
    assert calibration.forward_dirty_price_per_100 is not None

    assert display["calibration"]["implied_price_vol"] is None
    assert display["calibration"]["solver_status"] == "FAILED"
    # Bounds/tolerances/config ARE preserved (never dropped), only the
    # theoretical-bound outcome's implied/model/residual fields stay None.
    assert display["calibration"]["lower_price_vol"] is not None
    assert display["calibration"]["upper_price_vol"] is not None


# --- 17. Model / benchmark / calibrated values stay visibly distinct ---------------


@_requires_quantlib
def test_model_benchmark_and_calibrated_values_are_visibly_distinct_fields():
    # A deliberately mismatched premium so model != benchmark != calibrated vol.
    warning_envelope = {**_example_benchmark_envelope(), "premium_per_100": 5.0}
    _r, result, benchmark, comparison, calibration, display = (
        price_standalone_option_case_with_benchmark(
            _example_text(), warning_envelope, active_quote_side=BLIBenchmarkQuoteSide.MID
        )
    )
    model_premium = result.assumptions["black76_pv_per_100"]
    benchmark_premium = benchmark.premium_per_100
    implied_vol = calibration.solver_result.implied_price_vol

    assert model_premium != benchmark_premium
    # The calibration's own resolved forward is neither the model premium nor
    # the benchmark premium -- these are structurally distinct quantities.
    assert implied_vol != model_premium
    assert implied_vol != benchmark_premium
    assert display["model_fair_premium_per_100"] == pytest.approx(model_premium)
    assert display["benchmark"]["premium_per_100"] == pytest.approx(benchmark_premium)
    assert display["calibration"]["implied_price_vol"] == pytest.approx(implied_vol)


# --- 18. No mutation of request / benchmark / pricing / comparison / calibration ---


@_requires_quantlib
def test_no_mutation_of_any_result_object():
    case_text = _example_text()
    benchmark_text = _example_benchmark_text()

    request, result, benchmark, comparison, calibration, _display = (
        price_standalone_option_case_with_benchmark(
            case_text, benchmark_text, active_quote_side=BLIBenchmarkQuoteSide.MID
        )
    )
    request_before = asdict(request)
    benchmark_before = asdict(benchmark)
    comparison_before = asdict(comparison)
    calibration_before = asdict(calibration)

    # Re-run the whole workflow again; the first run's returned objects must
    # still compare equal to their own pre-recorded snapshots (i.e. nothing
    # about the first call's objects was mutated by any subsequent use).
    price_standalone_option_case_with_benchmark(
        case_text, benchmark_text, active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    assert asdict(request) == request_before
    assert asdict(benchmark) == benchmark_before
    assert asdict(comparison) == comparison_before
    assert asdict(calibration) == calibration_before


# --- 19. Bounded-workflow module-boundary proof -------------------------------------


def test_workbench_module_calls_compare_and_calibrate_but_no_lower_layer_math():
    source = inspect.getsource(workbench_module)
    assert "compare_bli_benchmark(" in source
    assert "calibrate_bli_implied_price_vol(" in source
    # No resolver/solver/Black-76 math duplicated at this layer -- calibration
    # already encapsulates those.
    for forbidden in (
        "solve_implied_dirty_price_vol",
        "solve_implied_price_vol",
        "resolve_standalone_option_pricing_inputs",
        "black76_dirty_price_option_pv_per_100",
        "black76_price_option_pv_per_100",
    ):
        assert forbidden not in source, f"unexpected reference to {forbidden!r}"
