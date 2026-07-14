import ast
import copy
import importlib.machinery
import importlib.util
import inspect
import json

import pytest

from shiori_pricing_lab.data import bli_quote_record_codec as codec_module
from shiori_pricing_lab.data.bli_quote_record_codec import (
    quote_record_from_dict,
    quote_record_from_json,
    quote_record_to_dict,
    quote_record_to_json,
)
from shiori_pricing_lab.pricing.result import PricingMessage, PricingWarningCode

_loader = importlib.machinery.SourceFileLoader(
    "quote_record_helpers", "tests/test_bli_quote_record.py"
)
_spec = importlib.util.spec_from_loader("quote_record_helpers", _loader)
_helpers = importlib.util.module_from_spec(_spec)
_loader.exec_module(_helpers)
record = _helpers.record
calibration_result = _helpers.calibration_result


def test_exact_dict_round_trip_with_non_none_calibration_solver_and_no_mutation():
    rec = record(calibration_result=calibration_result())
    before = copy.deepcopy(rec)
    payload = quote_record_to_dict(rec)
    payload_before = copy.deepcopy(payload)
    restored = quote_record_from_dict(payload)
    assert restored == rec
    assert rec == before
    assert payload == payload_before
    curve_points_payload = payload["request"]["market_data_snapshot"]["curve_points"]
    curve_point_payload = curve_points_payload["__tuple__"][0]
    assert curve_point_payload["__class__"] == "BLICurvePoint"
    solver_payload = payload["calibration_result"]["solver_result"]
    assert solver_payload["__class__"] == "BLIImpliedPriceVolSolverResult"


def test_tuple_tag_preserves_free_form_tuples_and_lists_after_round_trip():
    pricing = _helpers.pricing_result(
        warnings=(
            PricingMessage(
                code=PricingWarningCode.DATA_QUALITY,
                message="synthetic warning",
                detail={"nested": ("curve", ("node", 1)), "real_list": ["a", "b"]},
            ),
        ),
        assumptions={
            "black76_pv_per_100": 4.5,
            "genuine_list": ["left", "right"],
            "genuine_tuple": ("left", "right"),
        },
        diagnostics={"nested_tuple": (("outer", "inner"),)},
        scenario_results={"path": ("base", ["list-stays-list"])},
    )
    comparison = _helpers.benchmark_comparison()
    rec = record(pricing_result=pricing, benchmark_comparison=comparison)
    restored = quote_record_from_json(quote_record_to_json(rec))

    assert restored.pricing_result.warnings[0].detail["nested"] == ("curve", ("node", 1))
    assert restored.pricing_result.warnings[0].detail["real_list"] == ["a", "b"]
    assert restored.pricing_result.assumptions["genuine_list"] == ["left", "right"]
    assert restored.pricing_result.assumptions["genuine_tuple"] == ("left", "right")
    assert restored.pricing_result.diagnostics["nested_tuple"] == (("outer", "inner"),)
    assert restored.pricing_result.scenario_results["path"] == ("base", ["list-stays-list"])


def test_exact_canonical_json_round_trip_after_sorted_keys():
    rec = record(calibration_result=calibration_result())
    first = quote_record_to_json(rec)
    second = quote_record_to_json(rec)
    assert first == second
    assert first.endswith("\n")
    assert first == json.dumps(
        quote_record_to_dict(rec),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    assert quote_record_from_json(first) == rec


@pytest.mark.parametrize("field", ["schema_version", "request", "benchmark_comparison"])
def test_missing_required_top_level_fields_rejected(field):
    payload = quote_record_to_dict(record())
    payload.pop(field)
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)


def test_unknown_top_level_field_rejected():
    payload = quote_record_to_dict(record())
    payload["migration_alias"] = "not authorized"
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("request", "valuation_date", "not-a-date"),
        ("pricing_result", "status", "NOPE"),
        ("benchmark_quote", "quote_side", "NOPE"),
        ("benchmark_comparison", "reason", "NOPE"),
        ("calibration_result", "reason", "NOPE"),
    ],
)
def test_malformed_nested_payloads_and_invalid_enums_rejected(section, field, value):
    payload = quote_record_to_dict(record())
    payload[section][field] = value
    with pytest.raises((TypeError, ValueError)):
        quote_record_from_dict(payload)


def test_missing_and_unknown_nested_fields_rejected():
    payload = quote_record_to_dict(record())
    payload["request"]["market_data_snapshot"]["curve_points"]["__tuple__"][0].pop("rate")
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)

    payload = quote_record_to_dict(record())
    payload["pricing_result"]["extra"] = "nope"
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)


def test_malformed_tuple_tag_and_unknown_tagged_class_rejected():
    payload = quote_record_to_dict(record())
    payload["exclusions"] = {"__tuple__": "not-list"}
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)

    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["bad_tag"] = {"__class__": "FutureThing"}
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)

    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["bad_tuple"] = {
        "__tuple__": [],
        "extra": "nope",
    }
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)


def test_unsupported_schema_version_rejected():
    payload = quote_record_to_dict(record())
    payload["schema_version"] = 2
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)


@pytest.mark.parametrize("text", ["{", "[]", "null", "1", "true"])
def test_malformed_and_non_object_json_rejected(text):
    with pytest.raises(ValueError):
        quote_record_from_json(text)


@pytest.mark.parametrize("field,value", [
    ("client_quote_premium_per_100", float("nan")),
    ("trader_adjustment_total", float("inf")),
])
def test_non_finite_numbers_rejected(field, value):
    payload = quote_record_to_dict(record())
    payload[field] = value
    with pytest.raises(ValueError):
        quote_record_from_dict(payload)


def test_lists_where_tuples_are_required_after_reconstruction_are_rejected():
    rec = record()
    with pytest.raises(TypeError):
        type(rec)(**{**rec.__dict__, "exclusions": ["not-a-tuple"]})


def test_no_economic_derivation_or_buy_sell_sign_application():
    rec = record(
        client_quote_premium_per_100=4.6,
        client_quote_total_premium=999.0,
        trader_adjustment_per_100=-0.2,
        trader_adjustment_total=123.0,
        override_reason="manual separated economics",
    )
    restored = quote_record_from_json(quote_record_to_json(rec))
    assert restored.client_quote_premium_per_100 == 4.6
    assert restored.client_quote_total_premium == 999.0
    assert restored.trader_adjustment_per_100 == -0.2
    assert restored.trader_adjustment_total == 123.0


def test_public_api_names_are_authorized_only():
    assert not hasattr(codec_module, "bli_quote_record_to_typed_dict")
    assert not hasattr(codec_module, "bli_quote_record_from_typed_dict")
    assert not hasattr(codec_module, "bli_quote_record_to_canonical_json")
    assert not hasattr(codec_module, "bli_quote_record_from_canonical_json")


def test_codec_boundary_has_no_filesystem_clock_provider_migration_or_prbc_behavior():
    source = inspect.getsource(codec_module)
    tree = ast.parse(source)
    forbidden_import_roots = {
        "os", "pathlib", "tempfile", "uuid", "getpass", "sqlite3", "requests", "urllib"
    }
    imported = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert forbidden_import_roots.isdisjoint(imported)
    forbidden_calls = {
        "open", "uuid4", "now", "today", "price_bli_mvp", "calibrate_bli_implied_price_vol"
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert forbidden_calls.isdisjoint(called)
    assert "migration" not in source.lower()
    assert "alias" not in source.lower()
