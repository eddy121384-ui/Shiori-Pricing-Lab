"""Tests for the headless standalone bond-option JSON workflow (Issue #97, PR A).

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
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from shiori_pricing_lab.app import standalone_option_workbench as workbench_module
from shiori_pricing_lab.app.standalone_option_workbench import (
    build_request_from_standalone_option_case,
    prepare_standalone_display,
    price_standalone_option_case,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.data.bli_standalone_option_request_builder import (
    build_bli_standalone_option_request,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp_standalone_option
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingStatus
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"

# Pinned expected values -- identical to the standalone engine/builder tests,
# whose by-hand Annex A derivation is the source of truth.
_EXPECTED_FORWARD_CLEAN_PRICE_PER_100 = 101.22605288103159
_EXPECTED_TIME_TO_EXPIRY = 0.2465753424657534
_EXPECTED_OPTION_DISCOUNT_FACTOR = 0.9929452501091504
_EXPECTED_BLACK76_PV_PER_100 = 4.474769848529296
_EXPECTED_PV = 2.237384924264648


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
            curve_id="SANITIZED_BOND_REFERENCE_CURVE",
            curve_name="SANITIZED_BOND_REFERENCE_CURVE",
            curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.030), ("1Y", 0.035))
    ) + tuple(
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
    assert display["option_discount_factor"] == pytest.approx(_EXPECTED_OPTION_DISCOUNT_FACTOR)
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
    assert display["option_discount_factor"] == assumptions["option_discount_factor"]
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

    by_id = {(p.curve_id, p.curve_purpose) for p in snapshot.curve_points}
    assert ("SANITIZED_BOND_REFERENCE_CURVE", BLICurvePurpose.BOND_REFERENCE_CURVE) in by_id
    assert ("SANITIZED_OPTION_DISCOUNT_CURVE", BLICurvePurpose.OPTION_DISCOUNT_CURVE) in by_id


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
