import ast
import copy
import importlib.machinery
import importlib.util
import inspect
import json
import re
from dataclasses import fields

import pytest

from shiori_pricing_lab.data import bli_quote_record_codec as codec_module
from shiori_pricing_lab.data.bli_quote_record_codec import (
    quote_record_from_dict,
    quote_record_from_json,
    quote_record_to_dict,
    quote_record_to_json,
)
from shiori_pricing_lab.pricing.result import (
    PricingMessage,
    PricingStatus,
    PricingWarningCode,
)
from shiori_pricing_lab.products.enums import PayReceive

_loader = importlib.machinery.SourceFileLoader(
    "quote_record_helpers", "tests/test_bli_quote_record.py"
)
_spec = importlib.util.spec_from_loader("quote_record_helpers", _loader)
_helpers = importlib.util.module_from_spec(_spec)
_loader.exec_module(_helpers)
record = _helpers.record
calibration_result = _helpers.calibration_result
pricing_result = _helpers.pricing_result


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


# --- Reserved codec keys rejected in free-form containers ------------------------

_RESERVED_KEYS = ["__tuple__", "__class__"]


def _reserved_key_in_assumptions(reserved_key):
    return pricing_result(assumptions={"black76_pv_per_100": 4.5, reserved_key: "x"})


def _reserved_key_nested_deep_in_assumptions(reserved_key):
    return pricing_result(
        assumptions={
            "black76_pv_per_100": 4.5,
            "nested": {"deeper": [{"deepest": {reserved_key: "x"}}]},
        }
    )


def _reserved_key_in_diagnostics(reserved_key):
    return pricing_result(diagnostics={reserved_key: "x"})


def _reserved_key_in_scenario_results(reserved_key):
    return pricing_result(scenario_results={reserved_key: "x"})


def _reserved_key_in_cashflows(reserved_key):
    return pricing_result(cashflows=({"amount": 1.0, reserved_key: "x"},))


def _reserved_key_in_message_detail(reserved_key):
    return pricing_result(
        warnings=(
            PricingMessage(
                code=PricingWarningCode.DATA_QUALITY,
                message="synthetic",
                detail={reserved_key: "x"},
            ),
        )
    )


_RESERVED_KEY_BUILDERS = [
    _reserved_key_in_assumptions,
    _reserved_key_nested_deep_in_assumptions,
    _reserved_key_in_diagnostics,
    _reserved_key_in_scenario_results,
    _reserved_key_in_cashflows,
    _reserved_key_in_message_detail,
]


@pytest.mark.parametrize("reserved_key", _RESERVED_KEYS)
@pytest.mark.parametrize("build_pricing_result", _RESERVED_KEY_BUILDERS)
def test_reserved_codec_keys_rejected_before_encoding(reserved_key, build_pricing_result):
    pricing = build_pricing_result(reserved_key)
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape(reserved_key)):
        quote_record_to_dict(rec)


def test_reserved_key_rejection_does_not_escape_rename_or_normalize():
    # The error must name the exact offending key/path; the codec must not
    # silently rewrite it into something else and continue encoding.
    pricing = pricing_result(assumptions={"black76_pv_per_100": 4.5, "__class__": "x"})
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError) as excinfo:
        quote_record_to_dict(rec)
    assert "assumptions" in str(excinfo.value)
    assert "__class__" in str(excinfo.value)


def test_actual_tuples_still_use_the_internal_tuple_tag():
    # The reserved-key rejection must not disable the codec's own legitimate
    # tuple tagging for real tuple values in free-form data.
    pricing = pricing_result(assumptions={"black76_pv_per_100": 4.5, "real_tuple": (1, 2)})
    rec = record(pricing_result=pricing)
    payload = quote_record_to_dict(rec)
    assert payload["pricing_result"]["assumptions"]["real_tuple"] == {"__tuple__": [1, 2]}
    restored = quote_record_from_dict(payload)
    assert restored.pricing_result.assumptions["real_tuple"] == (1, 2)


# --- Arbitrary Enum values rejected in free-form containers ----------------------


def test_arbitrary_enum_rejected_in_assumptions():
    pricing = pricing_result(assumptions={"black76_pv_per_100": 4.5, "bad": PayReceive.PAY})
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="Enum"):
        quote_record_to_dict(rec)


def test_arbitrary_enum_rejected_in_message_detail():
    pricing = pricing_result(
        warnings=(
            PricingMessage(
                code=PricingWarningCode.DATA_QUALITY,
                message="synthetic",
                detail={"bad": PayReceive.PAY},
            ),
        )
    )
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="Enum"):
        quote_record_to_dict(rec)


def test_arbitrary_enum_rejected_nested_in_diagnostics_list():
    pricing = pricing_result(diagnostics={"items": [{"nested": PayReceive.PAY}]})
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="Enum"):
        quote_record_to_dict(rec)


def test_arbitrary_enum_rejected_nested_in_scenario_results():
    pricing = pricing_result(scenario_results={"path": [{"leg": PayReceive.RECEIVE}]})
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="Enum"):
        quote_record_to_dict(rec)


def test_arbitrary_enum_rejection_does_not_silently_convert_to_value():
    # Confirms the rejection message is not just a passthrough of .value --
    # the encoder must refuse the payload outright, not stringify it.
    pricing = pricing_result(assumptions={"black76_pv_per_100": 4.5, "bad": PayReceive.PAY})
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError):
        quote_record_to_dict(rec)


def test_typed_schema_enums_still_round_trip_normally():
    rec = record(calibration_result=calibration_result())
    restored = quote_record_from_dict(quote_record_to_dict(rec))
    assert restored.request.bond_option.currency is rec.request.bond_option.currency
    assert restored.benchmark_quote.quote_side is rec.benchmark_quote.quote_side
    assert restored.benchmark_comparison.status is rec.benchmark_comparison.status
    assert restored.calibration_result.reason is rec.calibration_result.reason
    assert restored.calibration_result.solver_result.reason == (
        rec.calibration_result.solver_result.reason
    )


# --- Exact native object types (subclasses rejected) ------------------------------


def test_codec_rejects_quote_record_subclass():
    base = record()
    record_cls = type(base)
    subclass = type(f"_{record_cls.__name__}Subclass", (record_cls,), {})
    kwargs = {f.name: getattr(base, f.name) for f in fields(record_cls) if f.init}
    subclassed = subclass(**kwargs)
    with pytest.raises(TypeError):
        quote_record_to_dict(subclassed)


# --- Successful-status policy: SUCCESS_WITH_WARNINGS round-trips exactly ---------


def test_success_with_warnings_round_trips_exactly():
    warning_pricing = pricing_result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=(
            PricingMessage(
                code=PricingWarningCode.DATA_QUALITY,
                message="synthetic data-quality warning",
                detail={"note": "synthetic"},
            ),
        ),
    )
    rec = record(pricing_result=warning_pricing)
    restored = quote_record_from_json(quote_record_to_json(rec))
    assert restored == rec
    assert restored.pricing_result.status is PricingStatus.SUCCESS_WITH_WARNINGS
    assert restored.pricing_result.warnings[0].code is PricingWarningCode.DATA_QUALITY
    assert restored.pricing_result.warnings[0].message == "synthetic data-quality warning"


# --- Exact PricingMessage items in warnings/errors (subclasses rejected) ---------


class _PricingMessageSubclass(PricingMessage):
    """A synthetic PricingMessage subclass used only to prove exact-type rejection."""


def _message(detail=None):
    return PricingMessage(
        code=PricingWarningCode.DATA_QUALITY,
        message="synthetic",
        detail={} if detail is None else detail,
    )


def _message_subclass(detail=None):
    return _PricingMessageSubclass(
        code=PricingWarningCode.DATA_QUALITY,
        message="synthetic subclass",
        detail={} if detail is None else detail,
    )


def test_pricing_message_subclass_in_warnings_rejected_with_indexed_path():
    pricing = pricing_result(warnings=(_message_subclass(),))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape("PricingResult.warnings[0]")):
        quote_record_to_dict(rec)


def test_pricing_message_subclass_in_errors_rejected_with_indexed_path():
    # errors must be tuples-of-PricingMessage; a FAILED status is not needed to
    # carry an errors entry on a SUCCESS_WITH_WARNINGS result for this encode test.
    pricing = pricing_result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        errors=(_message_subclass(),),
    )
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape("PricingResult.errors[0]")):
        quote_record_to_dict(rec)


def test_subclass_in_warnings_rejected_even_at_nonzero_index():
    pricing = pricing_result(warnings=(_message(), _message_subclass()))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape("PricingResult.warnings[1]")):
        quote_record_to_dict(rec)


def test_subclass_with_reserved_key_in_detail_cannot_bypass_validation():
    # The subclass is rejected outright at encode time; the reserved-key
    # payload it carries can never reach the wire.
    pricing = pricing_result(warnings=(_message_subclass(detail={"__class__": "x"}),))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape("PricingResult.warnings[0]")):
        quote_record_to_dict(rec)


def test_subclass_with_arbitrary_enum_in_detail_cannot_bypass_validation():
    pricing = pricing_result(warnings=(_message_subclass(detail={"bad": PayReceive.PAY}),))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape("PricingResult.warnings[0]")):
        quote_record_to_dict(rec)


def test_subclass_rejected_before_any_encoded_payload_via_json_entrypoint():
    pricing = pricing_result(errors=(_message_subclass(),))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape("PricingResult.errors[0]")):
        quote_record_to_json(rec)


def test_exact_pricing_message_warnings_and_errors_round_trip():
    pricing = pricing_result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=(
            _message(detail={"note": "w0", "nested": ("a", ("b", 1)), "lst": ["x"]}),
            _message(detail={}),
        ),
        errors=(_message(detail={"note": "e0"}),),
    )
    rec = record(pricing_result=pricing)
    restored = quote_record_from_json(quote_record_to_json(rec))
    assert restored == rec
    assert [type(m) for m in restored.pricing_result.warnings] == [
        PricingMessage,
        PricingMessage,
    ]
    assert [type(m) for m in restored.pricing_result.errors] == [PricingMessage]
    assert restored.pricing_result.warnings[0].detail["nested"] == ("a", ("b", 1))
    assert restored.pricing_result.warnings[0].detail["lst"] == ["x"]


@pytest.mark.parametrize("bad_detail", [{"__tuple__": []}, {"__class__": "x"}])
def test_exact_pricing_message_detail_still_rejects_reserved_keys(bad_detail):
    pricing = pricing_result(warnings=(_message(detail=bad_detail),))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match=re.escape("PricingMessage.detail")):
        quote_record_to_dict(rec)


def test_exact_pricing_message_detail_still_rejects_arbitrary_enum():
    pricing = pricing_result(warnings=(_message(detail={"bad": PayReceive.PAY}),))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="Enum"):
        quote_record_to_dict(rec)


# --- Raw Python tuples in a decode payload are non-canonical --------------------
#
# quote_record_from_dict accepts only JSON-ready canonical typed dicts. A raw
# Python tuple anywhere in the supplied payload is non-canonical and must be
# rejected before any native object is constructed; the only legal tuple wire
# form is the explicit {"__tuple__": [...]} tag. These tests feed raw dict
# input directly to quote_record_from_dict (not via an explicit tuple tag).


def test_raw_finite_tuple_in_assumptions_rejected():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["raw"] = (1, 2, 3)
    with pytest.raises(ValueError, match="raw Python tuple"):
        quote_record_from_dict(payload)


def test_raw_tuple_with_nan_in_assumptions_rejected():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["raw"] = (float("nan"),)
    with pytest.raises(ValueError, match="raw Python tuple"):
        quote_record_from_dict(payload)


@pytest.mark.parametrize("infinity", [float("inf"), float("-inf")])
def test_raw_tuple_with_infinity_in_assumptions_rejected(infinity):
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["raw"] = (infinity,)
    with pytest.raises(ValueError, match="raw Python tuple"):
        quote_record_from_dict(payload)


def test_raw_tuple_in_exact_pricing_message_detail_rejected():
    pricing = pricing_result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=(_message(detail={}),),
    )
    payload = quote_record_to_dict(record(pricing_result=pricing))
    payload["pricing_result"]["warnings"]["__tuple__"][0]["detail"]["raw"] = ("a", "b")
    with pytest.raises(ValueError, match="raw Python tuple"):
        quote_record_from_dict(payload)


def test_raw_tuple_in_diagnostics_rejected():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["diagnostics"]["raw"] = ("x",)
    with pytest.raises(ValueError, match="raw Python tuple"):
        quote_record_from_dict(payload)


def test_raw_tuple_rejection_reports_precise_keyed_and_indexed_path():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["nested"] = [{"deep": (1, 2)}]
    with pytest.raises(ValueError) as excinfo:
        quote_record_from_dict(payload)
    message = str(excinfo.value)
    assert "pricing_result.assumptions.nested" in message
    assert "[0]" in message
    assert "deep" in message


def test_raw_tuple_rejected_even_when_contents_are_all_json_safe_and_finite():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["raw"] = ("ok", 1, 2.5, True, None)
    with pytest.raises(ValueError, match="raw Python tuple"):
        quote_record_from_dict(payload)


def test_explicit_tuple_tags_still_decode_and_round_trip_exactly():
    pricing = pricing_result(
        assumptions={
            "black76_pv_per_100": 4.5,
            "genuine_tuple": ("left", "right"),
            "nested": {"inner": (1, (2, 3))},
        },
    )
    rec = record(pricing_result=pricing)
    payload = quote_record_to_dict(rec)
    # The encoder produced explicit tuple tags, not raw tuples.
    assert payload["pricing_result"]["assumptions"]["genuine_tuple"] == {
        "__tuple__": ["left", "right"]
    }
    restored = quote_record_from_dict(payload)
    assert restored == rec
    assert restored.pricing_result.assumptions["genuine_tuple"] == ("left", "right")
    assert restored.pricing_result.assumptions["nested"]["inner"] == (1, (2, 3))


def test_legitimate_lists_remain_lists_and_are_not_rejected():
    pricing = pricing_result(
        assumptions={"black76_pv_per_100": 4.5, "genuine_list": ["a", "b"]},
    )
    rec = record(pricing_result=pricing)
    restored = quote_record_from_dict(quote_record_to_dict(rec))
    assert restored.pricing_result.assumptions["genuine_list"] == ["a", "b"]
    assert isinstance(restored.pricing_result.assumptions["genuine_list"], list)
