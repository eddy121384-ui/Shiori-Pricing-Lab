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
``calibrate_bli_implied_price_vol`` exactly once each).

**No bundled benchmark example (Codex P1 review of PR #126).** Unlike the
pricing case, there is no ``examples/standalone_option_benchmark.json`` --
``BLIBenchmarkQuote.source_type`` only accepts ``BLOOMBERG``/``VENDOR``, and
a bundled user-facing file claiming ``BLOOMBERG`` while carrying an
engineered synthetic premium would misrepresent the workbench as
Bloomberg-validated. The synthetic benchmark data these tests need
(``_synthetic_benchmark_envelope`` below) is test-local only, never read by
production/UI code, and is explicitly **not** a claim of Bloomberg
validation or market evidence -- only a deterministic fixture whose
``premium_per_100`` is set to the exact model fair premium the pricing
example produces, so the comparison and calibration outcomes are pinned.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shiori_pricing_lab.app import standalone_option_workbench as workbench_module
from shiori_pricing_lab.app.standalone_option_workbench import (
    build_benchmark_from_standalone_option_benchmark_case,
    build_request_from_standalone_option_case,
    prepare_live_bloomberg_quote_display,
    prepare_standalone_display,
    price_standalone_option_case,
    price_standalone_option_case_with_benchmark,
    price_standalone_option_case_with_bloomberg_quote,
    price_standalone_option_case_with_bloomberg_quote_and_benchmark,
)
from shiori_pricing_lab.data.bli_benchmark_quote import BLIBenchmarkQuote, BLIBenchmarkQuoteSide
from shiori_pricing_lab.data.bli_snapshot import (
    BLIBondQuote,
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataStatus,
    BLIQuoteBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    build_bli_standalone_option_request,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
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
from shiori_pricing_lab.products.enums import Currency, TreasuryFTPQuoteSide
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


def test_module_has_no_pricing_math_or_provider_dependency():
    source = inspect.getsource(workbench_module)
    for forbidden in (
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


def test_only_the_acquisition_clock_seam_reads_the_system_clock():
    # Eddy-approved acquisition-time contract (issue #6 comment 5028876767):
    # exactly one function in this module reads the real clock.
    module_source = inspect.getsource(workbench_module)
    assert module_source.count("datetime.now(") == 1
    seam_source = inspect.getsource(workbench_module._shiori_acquisition_now)
    assert "datetime.now().astimezone()" in seam_source


def test_manual_workflow_functions_never_read_the_clock():
    manual_functions = (
        workbench_module._parse_standalone_option_case,
        workbench_module.build_request_from_standalone_option_case,
        workbench_module.prepare_standalone_display,
        workbench_module.price_standalone_option_case,
        workbench_module._parse_standalone_option_benchmark_case,
        workbench_module.build_benchmark_from_standalone_option_benchmark_case,
        workbench_module.prepare_standalone_benchmark_display,
        workbench_module.price_standalone_option_case_with_benchmark,
    )
    for func in manual_functions:
        assert "datetime.now(" not in inspect.getsource(func)


def test_live_bloomberg_orchestration_only_calls_the_clock_seam():
    # The price-only workflow calls the seam directly; the benchmark variant
    # delegates to it unchanged (no clock call of its own -- see the module
    # docstring), so neither ever calls the real clock directly.
    source = inspect.getsource(price_standalone_option_case_with_bloomberg_quote)
    assert "datetime.now(" not in source
    assert "_shiori_acquisition_now()" in source

    benchmark_source = inspect.getsource(
        price_standalone_option_case_with_bloomberg_quote_and_benchmark
    )
    assert "datetime.now(" not in benchmark_source
    assert "_shiori_acquisition_now(" not in benchmark_source


def test_module_does_not_shortcut_the_builder():
    # The only request-construction path is build_bli_standalone_option_request;
    # the module must never call BLIStandaloneBondOptionRequest(...) directly.
    source = inspect.getsource(workbench_module)
    assert "BLIStandaloneBondOptionRequest(" not in source
    assert "build_bli_standalone_option_request(" in source
    assert "price_bli_mvp_standalone_option(" in source


# ==================================================================================
# Issue #125: benchmark comparison / implied PRICE_VOL orchestration.
#
# Codex P1 review of PR #126: no bundled benchmark example exists under
# examples/ -- BLIBenchmarkQuote's source_type only accepts BLOOMBERG/VENDOR,
# and a bundled file claiming BLOOMBERG while carrying an engineered
# synthetic premium would misrepresent the workbench as Bloomberg-validated.
# The deterministic synthetic benchmark data these tests need lives here,
# test-local only -- it is never read by production/UI code, never written
# to examples/, and this test file makes no Bloomberg-validation claim about
# it (see the docstring below).
# ==================================================================================

_EXPECTED_IMPLIED_PRICE_VOL = 0.18  # the pricing example's own PRICE_VOL input


def _synthetic_benchmark_envelope() -> dict:
    """Return one deterministic, test-local synthetic benchmark envelope.

    ``premium_per_100`` is deliberately set to the bundled pricing example's
    own model fair premium at its own ``PRICE_VOL`` (0.18), so the
    comparison/calibration outcomes below are pinned and deterministic.
    ``source_type`` is ``BLOOMBERG`` only because ``BLIBenchmarkQuote``
    accepts no other value (no ``SYNTHETIC``/``MANUAL``/``TEST`` member
    exists, and none is added here) -- this is test-only fixture data, never
    exposed to a user, and is explicitly **not** a claim of Bloomberg
    validation or market evidence.
    """

    return {
        "benchmark_id": "TEST_LOCAL_SYNTHETIC_BENCHMARK_0001",
        "source_type": "BLOOMBERG",
        "source_system": "TEST_LOCAL_SYNTHETIC_BENCHMARK_SOURCE",
        "source_as_of": "2026-07-01T16:00:00Z",
        "retrieved_at": "2026-07-01T16:05:00Z",
        "quote_side": "MID",
        "premium_per_100": _EXPECTED_BLACK76_PV_PER_100,
        "total_premium": _EXPECTED_PV,
        "currency": "USD",
        "product_id": "BONDOPT-SYNTHETIC-0001",
        "snapshot_id": "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001",
        "underlying_id": "XS0000000001",
        "source_reference": "TEST_LOCAL_SYNTHETIC_BENCHMARK_REFERENCE_0001",
        "notes": (
            "Test-local synthetic fixture for deterministic testing only. Not a "
            "Bloomberg or real-market observation, and never exposed to a user."
        ),
    }


def _synthetic_benchmark_text() -> str:
    return json.dumps(_synthetic_benchmark_envelope())


# --- 9. Benchmark JSON parser: valid case reconstructs the frozen dataclass -------


def test_valid_benchmark_json_reconstructs_expected_benchmark_quote():
    benchmark = build_benchmark_from_standalone_option_benchmark_case(_synthetic_benchmark_text())
    assert isinstance(benchmark, BLIBenchmarkQuote)
    envelope = _synthetic_benchmark_envelope()
    assert benchmark == BLIBenchmarkQuote(**envelope)


def test_benchmark_json_accepts_string_or_already_parsed_mapping():
    from_text = build_benchmark_from_standalone_option_benchmark_case(_synthetic_benchmark_text())
    from_dict = build_benchmark_from_standalone_option_benchmark_case(
        _synthetic_benchmark_envelope()
    )
    assert from_text == from_dict


def test_benchmark_json_loads_twice_to_equal_quotes():
    first = build_benchmark_from_standalone_option_benchmark_case(_synthetic_benchmark_text())
    second = build_benchmark_from_standalone_option_benchmark_case(_synthetic_benchmark_text())
    assert first == second


# --- 10. Benchmark envelope failure contract ---------------------------------------


def test_benchmark_missing_top_level_key_fails_explicitly():
    envelope = _synthetic_benchmark_envelope()
    del envelope["premium_per_100"]
    with pytest.raises(ValueError, match="missing required top-level key"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_missing_notes_key_fails_explicitly():
    # notes is optional ON THE DATACLASS but required PRESENT (nullable) on
    # this strict envelope -- omitting the key entirely must still fail.
    envelope = _synthetic_benchmark_envelope()
    del envelope["notes"]
    with pytest.raises(ValueError, match="missing required top-level key"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_null_notes_is_accepted():
    envelope = {**_synthetic_benchmark_envelope(), "notes": None}
    benchmark = build_benchmark_from_standalone_option_benchmark_case(envelope)
    assert benchmark.notes is None


def test_benchmark_unknown_top_level_key_fails_explicitly():
    envelope = {**_synthetic_benchmark_envelope(), "retrieved_at_alias": "2026-07-01T16:05:00Z"}
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
    envelope = {**_synthetic_benchmark_envelope(), "quote_side": "NOT_A_SIDE"}
    with pytest.raises(ValueError, match="quote_side"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_negative_premium_propagates_from_constructor():
    envelope = {**_synthetic_benchmark_envelope(), "premium_per_100": -1.0}
    with pytest.raises(ValueError, match="premium_per_100"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_blank_benchmark_id_propagates_from_constructor():
    envelope = {**_synthetic_benchmark_envelope(), "benchmark_id": ""}
    with pytest.raises(ValueError, match="benchmark_id"):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


def test_benchmark_unknown_nested_type_propagates_as_type_error():
    envelope = {**_synthetic_benchmark_envelope(), "premium_per_100": "not-a-number"}
    with pytest.raises((TypeError, ValueError)):
        build_benchmark_from_standalone_option_benchmark_case(envelope)


# --- 12. active_quote_side is mandatory, no hidden default -------------------------


def test_active_quote_side_has_no_default_and_is_keyword_only():
    with pytest.raises(TypeError):
        price_standalone_option_case_with_benchmark(  # type: ignore[call-arg]
            _example_text(), _synthetic_benchmark_text()
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
        _example_text(), _synthetic_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    direct_request, direct_result, _direct_display = price_standalone_option_case(_example_text())
    direct_benchmark = build_benchmark_from_standalone_option_benchmark_case(
        _synthetic_benchmark_text()
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
        _example_text(), _synthetic_benchmark_text(), active_quote_side="MID"
    )
    result_via_enum = price_standalone_option_case_with_benchmark(
        _example_text(), _synthetic_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
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
        _example_text(), _synthetic_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )

    assert call_counts["compare"] == 1
    assert call_counts["calibrate"] == 1


# --- 15. The synthetic example benchmark produces the pinned outcomes --------------


@_requires_quantlib
def test_synthetic_benchmark_produces_pinned_pass_comparison():
    _r, _res, _b, comparison, _c, _d = price_standalone_option_case_with_benchmark(
        _example_text(), _synthetic_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    assert comparison.status is BLIBenchmarkComparisonStatus.PASS
    assert comparison.reason is BLIBenchmarkComparisonReason.COMPARABLE
    assert comparison.relative_residual == pytest.approx(0.0, abs=1e-12)
    assert comparison.model_fair_premium_per_100 == pytest.approx(_EXPECTED_BLACK76_PV_PER_100)
    assert comparison.benchmark_premium_per_100 == pytest.approx(_EXPECTED_BLACK76_PV_PER_100)


@_requires_quantlib
def test_synthetic_benchmark_recovers_expected_implied_price_vol_deterministically():
    _r, _res, _b, _comp, calibration, _d = price_standalone_option_case_with_benchmark(
        _example_text(), _synthetic_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    assert calibration.status is BLIImpliedPriceVolCalibrationStatus.SUCCESS
    assert calibration.reason is BLIImpliedPriceVolCalibrationReason.CALIBRATED
    assert calibration.solver_result.implied_price_vol == pytest.approx(
        _EXPECTED_IMPLIED_PRICE_VOL, abs=1e-5
    )

    # Repeat run is deterministic.
    _r2, _res2, _b2, _comp2, calibration2, _d2 = price_standalone_option_case_with_benchmark(
        _example_text(), _synthetic_benchmark_text(), active_quote_side=BLIBenchmarkQuoteSide.MID
    )
    assert calibration.solver_result.implied_price_vol == pytest.approx(
        calibration2.solver_result.implied_price_vol, abs=1e-12
    )


# --- 16. NON_COMPARABLE / calibration failure display without fabricated values ---


@_requires_quantlib
def test_mismatched_product_id_yields_non_comparable_and_calibration_failed_no_fabrication():
    mismatched_envelope = {**_synthetic_benchmark_envelope(), "product_id": "OTHER-PRODUCT-ID"}
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
        **_synthetic_benchmark_envelope(),
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
    warning_envelope = {**_synthetic_benchmark_envelope(), "premium_per_100": 5.0}
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
    benchmark_text = _synthetic_benchmark_text()

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


# --- 20. Issue #6: live Bloomberg bond-quote wiring, Eddy-approved
#     acquisition-time contract (issue #6 comment 5028876767, PR #129
#     comment 5028878866) --------------------------------------------------
#
# No real blpapi/network/system clock: `load_bloomberg_bond_quote` itself is
# already fully covered by tests/test_bloomberg_bond_quote.py, so here it is
# monkeypatched directly on the workbench module (the smallest seam that
# exercises this module's own orchestration/validation logic in isolation).
# `_shiori_acquisition_now` -- the one clock seam this module adds -- is
# monkeypatched the same way, so no real clock is read in CI either.

_BLOOMBERG_SECURITY = "91282CQX Govt"

# The example envelope's valuation_date is "2026-07-01" -- a fixed clock
# reading a time on that same calendar date lets the acquisition-date guard
# pass deterministically.
_FIXED_ACQUIRED_AT = datetime(2026, 7, 1, 16, 5, 0, tzinfo=UTC)
_FIXED_ACQUIRED_AT_TEXT = _FIXED_ACQUIRED_AT.isoformat(timespec="seconds")
# A different calendar date, for the date-mismatch guard tests.
_MISMATCHED_ACQUIRED_AT = datetime(2026, 7, 2, 9, 0, 0, tzinfo=UTC)


def _live_quote(**overrides) -> BLIBondQuote:
    params = dict(
        isin=_example_envelope()["bond_option"]["underlying_isin"],
        currency=Currency.USD,
        price_type=BLIQuoteBasis.PRICE,
        quote_side=TreasuryFTPQuoteSide.MID,
        source_system="BLOOMBERG_DAPI",
        status=BLIMarketDataStatus.ACTIVE,
        clean_price_per_100=99.5,
        accrued_interest_per_100=0.31,
    )
    params.update(overrides)
    return BLIBondQuote(**params)


def _install_fake_bloomberg_loader(monkeypatch, *, quote=None, error=None):
    calls: list[dict] = []

    def fake_loader(*, security, isin, quote_side):
        calls.append({"security": security, "isin": isin, "quote_side": quote_side})
        if error is not None:
            raise error
        return quote if quote is not None else _live_quote()

    monkeypatch.setattr(workbench_module, "load_bloomberg_bond_quote", fake_loader)
    return calls


def _install_fixed_clock(monkeypatch, acquired_at: datetime = _FIXED_ACQUIRED_AT):
    clock_calls: list[datetime] = []

    def fake_clock() -> datetime:
        clock_calls.append(acquired_at)
        return acquired_at

    monkeypatch.setattr(workbench_module, "_shiori_acquisition_now", fake_clock)
    return clock_calls


def test_bloomberg_functions_have_no_source_as_of_or_retrieved_at_parameter():
    for func in (
        price_standalone_option_case_with_bloomberg_quote,
        price_standalone_option_case_with_bloomberg_quote_and_benchmark,
    ):
        sig = inspect.signature(func)
        assert "source_as_of" not in sig.parameters
        assert "retrieved_at" not in sig.parameters

    sig = inspect.signature(price_standalone_option_case_with_bloomberg_quote)
    assert sig.parameters["bloomberg_security"].default is inspect.Parameter.empty
    assert sig.parameters["quote_side"].default is inspect.Parameter.empty


def test_bloomberg_functions_reject_a_source_as_of_kwarg():
    envelope = _example_envelope()
    with pytest.raises(TypeError):
        price_standalone_option_case_with_bloomberg_quote(
            envelope,
            bloomberg_security=_BLOOMBERG_SECURITY,
            quote_side=TreasuryFTPQuoteSide.MID,
            source_as_of=envelope["as_of_timestamp"],
        )


def test_blank_bloomberg_security_raises_value_error(monkeypatch):
    calls = _install_fake_bloomberg_loader(monkeypatch)
    clock_calls = _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    with pytest.raises(ValueError, match="bloomberg_security"):
        price_standalone_option_case_with_bloomberg_quote(
            envelope,
            bloomberg_security="  ",
            quote_side=TreasuryFTPQuoteSide.MID,
        )

    assert calls == []
    assert clock_calls == []


@_requires_quantlib
def test_exactly_one_bloomberg_call_with_expected_isin_and_explicit_side(monkeypatch):
    calls = _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    price_standalone_option_case_with_bloomberg_quote(
        envelope,
        bloomberg_security=_BLOOMBERG_SECURITY,
        quote_side=TreasuryFTPQuoteSide.MID,
    )

    assert calls == [
        {
            "security": _BLOOMBERG_SECURITY,
            "isin": envelope["bond_option"]["underlying_isin"],
            "quote_side": TreasuryFTPQuoteSide.MID,
        }
    ]


@_requires_quantlib
def test_clock_captured_only_after_successful_loader_return(monkeypatch):
    order: list[str] = []

    def fake_loader(*, security, isin, quote_side):
        order.append("loader")
        return _live_quote()

    def fake_clock() -> datetime:
        order.append("clock")
        return _FIXED_ACQUIRED_AT

    monkeypatch.setattr(workbench_module, "load_bloomberg_bond_quote", fake_loader)
    monkeypatch.setattr(workbench_module, "_shiori_acquisition_now", fake_clock)
    envelope = _example_envelope()

    price_standalone_option_case_with_bloomberg_quote(
        envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
    )

    assert order == ["loader", "clock"]


def test_clock_never_called_when_loader_raises(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch, error=BLIBloombergDapiError("boom"))
    clock_calls = _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    with pytest.raises(BLIBloombergDapiError, match="boom"):
        price_standalone_option_case_with_bloomberg_quote(
            envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
        )

    assert clock_calls == []


@_requires_quantlib
def test_only_bond_quote_and_pricing_timestamp_are_replaced_everything_else_equal(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()
    reference_request = build_request_from_standalone_option_case(envelope)

    request, _result, live_quote, _display = price_standalone_option_case_with_bloomberg_quote(
        envelope,
        bloomberg_security=_BLOOMBERG_SECURITY,
        quote_side=TreasuryFTPQuoteSide.MID,
    )

    actual = asdict(request)
    expected = asdict(reference_request)
    actual_snapshot = actual["market_data_snapshot"]
    expected_snapshot = expected["market_data_snapshot"]
    assert actual_snapshot["bond_quote"] != expected_snapshot["bond_quote"]
    assert request.market_data_snapshot.bond_quote.isin == live_quote.isin
    assert request.market_data_snapshot.bond_quote.clean_price_per_100 == (
        live_quote.clean_price_per_100
    )
    # pricing_timestamp is replaced with the acquisition timestamp -- not
    # the case's original pricing_timestamp.
    assert actual["pricing_timestamp"] == _FIXED_ACQUIRED_AT_TEXT
    assert actual["pricing_timestamp"] != expected["pricing_timestamp"]
    # as_of_timestamp (case market-data provenance) is untouched.
    assert actual_snapshot["as_of_timestamp"] == expected_snapshot["as_of_timestamp"]

    actual_snapshot["bond_quote"] = expected_snapshot["bond_quote"]
    actual["pricing_timestamp"] = expected["pricing_timestamp"]
    assert actual == expected


@_requires_quantlib
def test_live_pricing_timestamp_equals_acquired_at(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    request, _result, _live_quote, display = price_standalone_option_case_with_bloomberg_quote(
        envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
    )

    assert request.pricing_timestamp == _FIXED_ACQUIRED_AT_TEXT
    assert display["live_bloomberg_quote"]["acquired_at"] == _FIXED_ACQUIRED_AT_TEXT
    assert display["retrieved_at"] == _FIXED_ACQUIRED_AT_TEXT


@_requires_quantlib
def test_case_as_of_timestamp_stays_unchanged(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    request, _result, _live_quote, display = price_standalone_option_case_with_bloomberg_quote(
        envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
    )

    assert request.market_data_snapshot.as_of_timestamp == envelope["as_of_timestamp"]
    assert display["live_bloomberg_quote"]["case_as_of_timestamp"] == envelope["as_of_timestamp"]


@_requires_quantlib
def test_input_mapping_and_nested_values_not_mutated(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()
    snapshot_before = json.loads(json.dumps(envelope))

    price_standalone_option_case_with_bloomberg_quote(
        envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
    )

    assert envelope == snapshot_before


def test_bloomberg_failure_propagates_and_never_falls_back(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch, error=BLIBloombergDapiError("boom"))
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    with pytest.raises(BLIBloombergDapiError, match="boom"):
        price_standalone_option_case_with_bloomberg_quote(
            envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
        )


def test_mismatched_live_quote_isin_is_rejected_by_existing_constructor(monkeypatch):
    mismatched_quote = replace(_live_quote(), isin="XS9999999999")
    _install_fake_bloomberg_loader(monkeypatch, quote=mismatched_quote)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    # The existing BLIStandaloneBondOptionRequest ISIN-coherence gate (not
    # anything reimplemented in the workbench) catches this -- proving live
    # quote/ISIN coherence still reaches the existing constructors unchanged.
    with pytest.raises(ValueError):
        price_standalone_option_case_with_bloomberg_quote(
            envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
        )


# --- 20a. Acquisition-date-mismatch guard: retrieval happens, pricing does not ----


def test_acquisition_date_mismatch_raises_after_retrieval_before_pricing(monkeypatch):
    calls = _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch, acquired_at=_MISMATCHED_ACQUIRED_AT)
    envelope = _example_envelope()
    assert envelope["valuation_date"] == "2026-07-01"

    with pytest.raises(ValueError, match="valuation_date"):
        price_standalone_option_case_with_bloomberg_quote(
            envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
        )

    # Retrieval already happened (the loader was called exactly once) --
    # only pricing was blocked.
    assert len(calls) == 1


def test_acquisition_date_mismatch_never_calls_the_pricing_engine(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch, acquired_at=_MISMATCHED_ACQUIRED_AT)
    envelope = _example_envelope()

    def _fail(*args, **kwargs):
        raise AssertionError("pricing engine must not run on an acquisition-date mismatch")

    monkeypatch.setattr(workbench_module, "price_bli_mvp_standalone_option", _fail)

    with pytest.raises(ValueError, match="valuation_date"):
        price_standalone_option_case_with_bloomberg_quote(
            envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
        )


def test_acquisition_date_mismatch_blocks_comparison_and_calibration(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch, acquired_at=_MISMATCHED_ACQUIRED_AT)
    envelope = _example_envelope()
    benchmark_text = _synthetic_benchmark_text()

    def _fail(*args, **kwargs):
        raise AssertionError("comparison/calibration must not run on a date mismatch")

    monkeypatch.setattr(workbench_module, "compare_bli_benchmark", _fail)
    monkeypatch.setattr(workbench_module, "calibrate_bli_implied_price_vol", _fail)

    with pytest.raises(ValueError, match="valuation_date"):
        price_standalone_option_case_with_bloomberg_quote_and_benchmark(
            envelope,
            benchmark_text,
            bloomberg_security=_BLOOMBERG_SECURITY,
            quote_side=TreasuryFTPQuoteSide.MID,
        )


# --- 20b. Price-only result equals a manually swapped reference; provenance -------


@_requires_quantlib
def test_price_only_mode_result_matches_manually_swapped_reference(monkeypatch):
    live_quote = _live_quote()
    _install_fake_bloomberg_loader(monkeypatch, quote=live_quote)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()

    request, result, returned_quote, display = price_standalone_option_case_with_bloomberg_quote(
        envelope, bloomberg_security=_BLOOMBERG_SECURITY, quote_side=TreasuryFTPQuoteSide.MID
    )

    # No financial calculation duplicated in the orchestration: pricing the
    # same envelope directly, with bond_quote and pricing_timestamp swapped
    # for the live quote/acquisition time (via the existing builder path,
    # not a hand-built object), must reach the exact same PricingResult.
    bloomberg_envelope = {
        **envelope,
        "bond_quote": asdict(live_quote),
        "pricing_timestamp": _FIXED_ACQUIRED_AT_TEXT,
    }
    reference_result = price_bli_mvp_standalone_option(
        build_request_from_standalone_option_case(bloomberg_envelope)
    )

    assert returned_quote == live_quote
    assert result.status == reference_result.status
    assert result.pv == reference_result.pv
    assert display["live_bloomberg_quote"] == prepare_live_bloomberg_quote_display(
        _BLOOMBERG_SECURITY, live_quote, _FIXED_ACQUIRED_AT_TEXT, envelope["as_of_timestamp"]
    )


def test_prepare_live_bloomberg_quote_display_is_bounded_and_verbatim():
    live_quote = _live_quote()
    display = prepare_live_bloomberg_quote_display(
        _BLOOMBERG_SECURITY, live_quote, _FIXED_ACQUIRED_AT_TEXT, "2026-07-01T16:00:00Z"
    )
    assert display == {
        "security": _BLOOMBERG_SECURITY,
        "verified_isin": live_quote.isin,
        "source_system": live_quote.source_system,
        "quote_side": "MID",
        "currency": "USD",
        "clean_price_per_100": live_quote.clean_price_per_100,
        "accrued_interest_per_100": live_quote.accrued_interest_per_100,
        "acquired_at": _FIXED_ACQUIRED_AT_TEXT,
        "timestamp_basis": "SHIORI_ACQUISITION_TIME",
        "bloomberg_quote_observation_time": None,
        "case_as_of_timestamp": "2026-07-01T16:00:00Z",
        "refreshed_scope": "BOND_QUOTE_ONLY",
        "other_market_inputs": "CASE_JSON_UNCHANGED",
    }
    assert "source_as_of" not in display


@_requires_quantlib
def test_benchmark_mode_calls_pricing_comparison_calibration_exactly_once_each(monkeypatch):
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    envelope = _example_envelope()
    benchmark_text = _synthetic_benchmark_text()

    compare_calls = []
    calibrate_calls = []
    real_compare = workbench_module.compare_bli_benchmark
    real_calibrate = workbench_module.calibrate_bli_implied_price_vol

    def spy_compare(*args, **kwargs):
        compare_calls.append(kwargs.get("active_quote_side"))
        return real_compare(*args, **kwargs)

    def spy_calibrate(*args, **kwargs):
        calibrate_calls.append(kwargs.get("active_quote_side"))
        return real_calibrate(*args, **kwargs)

    monkeypatch.setattr(workbench_module, "compare_bli_benchmark", spy_compare)
    monkeypatch.setattr(workbench_module, "calibrate_bli_implied_price_vol", spy_calibrate)

    (
        request,
        result,
        live_quote,
        benchmark,
        comparison,
        calibration,
        display,
    ) = price_standalone_option_case_with_bloomberg_quote_and_benchmark(
        envelope,
        benchmark_text,
        bloomberg_security=_BLOOMBERG_SECURITY,
        quote_side=TreasuryFTPQuoteSide.MID,
    )

    assert len(compare_calls) == 1
    assert len(calibrate_calls) == 1
    # The same explicit side reaches Bloomberg, comparison, and calibration.
    assert live_quote.quote_side is TreasuryFTPQuoteSide.MID
    assert compare_calls[0] == "MID"
    assert calibrate_calls[0] == "MID"
    assert comparison.active_quote_side.value == "MID"
    assert display["live_bloomberg_quote"]["quote_side"] == "MID"
    assert "benchmark" in display
    assert "comparison" in display
    assert "calibration" in display


# --- 20c. Manual mode is fully isolated from Bloomberg and the live clock ---------


@_requires_quantlib
def test_manual_mode_never_calls_bloomberg_or_the_live_clock(monkeypatch):
    def _fail_loader(*args, **kwargs):
        raise AssertionError("manual mode must never call the Bloomberg loader")

    def _fail_clock():
        raise AssertionError("manual mode must never read the live acquisition clock")

    monkeypatch.setattr(workbench_module, "load_bloomberg_bond_quote", _fail_loader)
    monkeypatch.setattr(workbench_module, "_shiori_acquisition_now", _fail_clock)

    envelope = _example_envelope()
    price_standalone_option_case(envelope)
    price_standalone_option_case_with_benchmark(
        envelope, _synthetic_benchmark_text(), active_quote_side="MID"
    )
