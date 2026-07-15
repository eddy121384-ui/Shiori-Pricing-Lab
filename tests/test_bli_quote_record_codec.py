import ast
import copy
import dataclasses
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


def test_raw_tuple_key_in_assumptions_rejected():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"][("raw", "key")] = "x"
    with pytest.raises(ValueError, match="non-string dict key"):
        quote_record_from_dict(payload)


def test_raw_tuple_key_in_nested_free_form_dict_rejected():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["nested"] = {"inner": {}}
    payload["pricing_result"]["assumptions"]["nested"]["inner"][("deep", "key")] = 1
    with pytest.raises(ValueError, match="non-string dict key"):
        quote_record_from_dict(payload)


def test_raw_tuple_key_rejection_reports_precise_path_and_key():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"][("raw", "key")] = "x"
    with pytest.raises(ValueError) as excinfo:
        quote_record_from_dict(payload)
    message = str(excinfo.value)
    assert "pricing_result.assumptions" in message
    assert "('raw', 'key')" in message


def test_raw_tuple_key_rejected_even_with_finite_json_safe_contents():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"][(1, 2)] = "safe-value"
    with pytest.raises(ValueError, match="non-string dict key"):
        quote_record_from_dict(payload)


@pytest.mark.parametrize("bad_key", [1, 1.5, None, True, frozenset({"x"})])
def test_non_tuple_non_string_key_rejected(bad_key):
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"][bad_key] = "x"
    with pytest.raises(ValueError, match="non-string dict key"):
        quote_record_from_dict(payload)


def test_legitimate_string_key_dicts_still_round_trip():
    pricing = pricing_result(
        assumptions={"black76_pv_per_100": 4.5, "a_string_key": "value"},
    )
    rec = record(pricing_result=pricing)
    restored = quote_record_from_dict(quote_record_to_dict(rec))
    assert restored.pricing_result.assumptions["a_string_key"] == "value"


def test_explicit_tuple_tag_values_still_round_trip_with_key_validation_active():
    pricing = pricing_result(
        assumptions={
            "black76_pv_per_100": 4.5,
            "genuine_tuple": ("left", "right"),
        },
    )
    rec = record(pricing_result=pricing)
    payload = quote_record_to_dict(rec)
    assert payload["pricing_result"]["assumptions"]["genuine_tuple"] == {
        "__tuple__": ["left", "right"]
    }
    restored = quote_record_from_dict(payload)
    assert restored == rec
    assert restored.pricing_result.assumptions["genuine_tuple"] == ("left", "right")


# --- Decode/encode symmetry: raw non-JSON-ready Python objects rejected ----------
#
# ``quote_record_from_dict`` accepts only a JSON-ready canonical typed dict --
# the same contract already enforced for raw tuple values/keys. A raw
# ``Enum`` member, dataclass instance, or other custom object is a payload
# shape the encoder could never legitimately produce (``_encode_free_form_value``
# already rejects Enum/dataclass values before encoding), so decode must
# reject it too rather than silently embedding the live Python object.


@pytest.mark.parametrize(
    "bad_value",
    [
        PayReceive.PAY,
        PricingMessage(code=next(iter(PricingWarningCode)), message="x"),
        frozenset({"x"}),
    ],
    ids=["enum", "dataclass", "frozenset"],
)
def test_raw_non_json_ready_value_in_assumptions_rejected(bad_value):
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["sneaky"] = bad_value
    with pytest.raises(ValueError, match="non-canonical value"):
        quote_record_from_dict(payload)


def test_raw_enum_value_nested_in_diagnostics_list_rejected():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["diagnostics"] = {"items": [{"leg": PayReceive.PAY}]}
    with pytest.raises(ValueError, match="non-canonical value"):
        quote_record_from_dict(payload)


def test_raw_enum_value_in_message_detail_rejected():
    pricing = pricing_result(
        status=PricingStatus.SUCCESS_WITH_WARNINGS,
        warnings=(_message(detail={}),),
    )
    payload = quote_record_to_dict(record(pricing_result=pricing))
    payload["pricing_result"]["warnings"]["__tuple__"][0]["detail"]["sneaky"] = PayReceive.PAY
    with pytest.raises(ValueError, match="non-canonical value"):
        quote_record_from_dict(payload)


def test_raw_enum_value_rejection_reports_precise_path():
    # The encoder converts Enum -> .value for typed-schema fields, but a
    # decode-side raw Enum in free-form data must never be silently
    # normalized to its .value string -- it is rejected outright, with the
    # exact offending path.
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"]["sneaky"] = PayReceive.PAY
    with pytest.raises(ValueError) as excinfo:
        quote_record_from_dict(payload)
    assert "pricing_result.assumptions.sneaky" in str(excinfo.value)


def test_strenum_dict_key_in_free_form_data_rejected_at_encode():
    # PayReceive is a StrEnum, so isinstance(key, str) is True for its
    # members even though it is a live Enum object, not a plain string;
    # the free-form dict-key check must use an exact-type comparison so a
    # StrEnum key is rejected at encode time, not silently accepted because
    # it happens to also be a str.
    pricing = pricing_result(assumptions={"black76_pv_per_100": 4.5, PayReceive.PAY: "x"})
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="free-form dict keys must be strings"):
        quote_record_to_dict(rec)


# --- Nested dataclass subclasses rejected before a payload is returned -----------
#
# _encode_value tags a nested dataclass value with type(value); if the actual
# runtime type is a subclass (e.g. a BondOption subclass reachable through
# BLIStandaloneBondOptionRequest.bond_option, which the request constructor
# does not itself reject), the encoder must not tag it with the subclass's own
# name -- that would produce a payload the decoder can never legitimately
# reconstruct, breaking object -> dict -> object symmetry. This must hold at
# every reachable nesting depth, through the single generic _encode_value path.


def _record_with_subclass_bond_option():
    rec = record()
    subclass_bond_option = _helpers._subclass_instance(rec.request.bond_option)
    new_request = dataclasses.replace(rec.request, bond_option=subclass_bond_option)
    return dataclasses.replace(rec, request=new_request)


def _record_with_subclass_curve_point():
    rec = record()
    snapshot = rec.request.market_data_snapshot
    subclass_curve_point = _helpers._subclass_instance(snapshot.curve_points[0])
    new_curve_points = (subclass_curve_point,) + snapshot.curve_points[1:]
    new_snapshot = dataclasses.replace(snapshot, curve_points=new_curve_points)
    new_request = dataclasses.replace(rec.request, market_data_snapshot=new_snapshot)
    return dataclasses.replace(rec, request=new_request)


def _record_with_subclass_solver_result():
    rec = record(calibration_result=calibration_result())
    calib = rec.calibration_result
    subclass_solver = _helpers._subclass_instance(calib.solver_result)
    new_calib = dataclasses.replace(calib, solver_result=subclass_solver)
    return dataclasses.replace(rec, calibration_result=new_calib)


@pytest.mark.parametrize(
    "build_record,expected_path_fragment",
    [
        (_record_with_subclass_bond_option, "BLIStandaloneBondOptionRequest.bond_option"),
        (_record_with_subclass_curve_point, "curve_points[0]"),
        (_record_with_subclass_solver_result, "solver_result"),
    ],
    ids=["top-level-nested", "tuple-item-nested", "two-levels-deep"],
)
def test_nested_dataclass_subclass_rejected_at_encode(build_record, expected_path_fragment):
    rec = build_record()
    with pytest.raises(ValueError) as excinfo:
        quote_record_to_dict(rec)
    message = str(excinfo.value)
    assert expected_path_fragment in message
    assert "must be exactly" in message


# --- Custom dict/list subclasses rejected, never silently normalized -------------
#
# A dict/list subclass is a Python-only object json.loads can never produce.
# It must be rejected outright at both encode and decode, not traversed and
# normalized into a plain builtin dict/list.


class _SneakyDict(dict):
    pass


class _SneakyList(list):
    pass


def test_custom_dict_subclass_in_free_form_rejected_at_encode():
    pricing = pricing_result(assumptions=_SneakyDict({"black76_pv_per_100": 4.5}))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="unsupported free-form value type _SneakyDict"):
        quote_record_to_dict(rec)


def test_custom_list_subclass_in_free_form_rejected_at_encode():
    pricing = pricing_result(diagnostics={"items": _SneakyList(["a", "b"])})
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="unsupported free-form value type _SneakyList"):
        quote_record_to_dict(rec)


def test_custom_dict_subclass_rejected_at_decode():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"] = _SneakyDict(
        payload["pricing_result"]["assumptions"]
    )
    with pytest.raises(ValueError, match="dict/list subclass"):
        quote_record_from_dict(payload)


def test_custom_list_subclass_rejected_at_decode_nested():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["diagnostics"] = {"items": _SneakyList(["a", "b"])}
    with pytest.raises(ValueError, match="dict/list subclass"):
        quote_record_from_dict(payload)


def test_container_subclass_rejection_reports_precise_path():
    payload = quote_record_to_dict(record())
    payload["pricing_result"]["assumptions"] = _SneakyDict(
        payload["pricing_result"]["assumptions"]
    )
    with pytest.raises(ValueError) as excinfo:
        quote_record_from_dict(payload)
    assert "pricing_result.assumptions" in str(excinfo.value)


# --- Tuple subclasses rejected, never silently normalized ------------------------
#
# A tuple subclass is the same defect class as the dict/list subclass
# normalization fixed above: isinstance(value, tuple) accepted it as an
# ordinary tuple, encoded it via the normal explicit tuple tag, and it would
# decode back out as a plain builtin tuple -- silently discarding its actual
# type. Fixed by matching tuples by exact type (type(value) is tuple), like
# dict/list.


class _TupleSubclass(tuple):
    pass


def test_tuple_subclass_in_free_form_nested_value_rejected_at_encode():
    pricing = pricing_result(
        assumptions={"black76_pv_per_100": 4.5, "nested": {"bad": _TupleSubclass((1, 2))}}
    )
    rec = record(pricing_result=pricing)
    with pytest.raises(
        ValueError, match="unsupported free-form value type _TupleSubclass"
    ) as excinfo:
        quote_record_to_dict(rec)
    assert "PricingResult.assumptions.nested.bad" in str(excinfo.value)


def test_tuple_subclass_in_typed_schema_tuple_field_rejected_at_encode():
    # exclusions is a typed-schema tuple[str, ...] field whose own validator
    # (_require_exclusions) accepts any tuple subclass unchanged rather than
    # normalizing it -- so the codec boundary is the only place left to
    # reject it.
    rec = record(exclusions=_TupleSubclass(("desk_override",)))
    assert type(rec.exclusions) is _TupleSubclass
    with pytest.raises(
        ValueError, match="must be exactly tuple, got _TupleSubclass"
    ) as excinfo:
        quote_record_to_dict(rec)
    assert "BLIQuoteRecord.exclusions" in str(excinfo.value)


def test_exact_builtin_tuples_still_round_trip_unchanged_after_tuple_subclass_fix():
    pricing = pricing_result(
        assumptions={"black76_pv_per_100": 4.5, "genuine_tuple": ("left", "right")}
    )
    rec = record(pricing_result=pricing, exclusions=("desk_override", "manual_review"))
    payload = quote_record_to_dict(rec)
    assert payload["pricing_result"]["assumptions"]["genuine_tuple"] == {
        "__tuple__": ["left", "right"]
    }
    assert payload["exclusions"] == {"__tuple__": ["desk_override", "manual_review"]}
    restored = quote_record_from_dict(payload)
    assert restored == rec
    assert type(restored.exclusions) is tuple
    assert restored.pricing_result.assumptions["genuine_tuple"] == ("left", "right")


# --- Typed-schema Enum boundary: field-aware, no generic Enum -> .value ----------
#
# An Enum may serialize only in a field explicitly declared enum-typed, and
# only when it is exactly the field's expected Enum class. A foreign Enum in
# a non-enum typed field, or the wrong Enum class in an enum-typed field, is
# rejected before payload return -- never silently normalized through
# .value. A foreign Enum in a non-enum-typed field is rejected by the same
# exact-shape check as any other wrong-typed value for that field (str/
# int/float/bool), since PayReceive is never among the field's own expected
# types -- there is no separate "Enum" carve-out left in the unified
# _FIELD_SHAPES mechanism.


def test_foreign_strenum_in_exclusions_rejected():
    # exclusions' own validator (_require_exclusions in bli_quote_record.py)
    # accepts a StrEnum member unchanged, since isinstance(member, str) is
    # True -- the codec boundary is the only place left to reject it.
    rec = record(exclusions=(PayReceive.PAY,))
    assert type(rec.exclusions[0]) is PayReceive
    with pytest.raises(ValueError, match="must be exactly str, got PayReceive") as excinfo:
        quote_record_to_dict(rec)
    assert "BLIQuoteRecord.exclusions[0]" in str(excinfo.value)


def test_foreign_enum_in_pricing_message_message_field_rejected():
    # PricingMessage.message is a plain str field with no __post_init__
    # validation of its own type.
    code = next(iter(PricingWarningCode))
    bad_message = PricingMessage(code=code, message=PayReceive.PAY)
    pricing = pricing_result(status=PricingStatus.SUCCESS_WITH_WARNINGS, warnings=(bad_message,))
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="must be exactly str, got PayReceive") as excinfo:
        quote_record_to_dict(rec)
    assert "PricingMessage.message" in str(excinfo.value)


def test_foreign_enum_in_non_enum_numeric_calibration_field_rejected():
    # BLIImpliedPriceVolCalibrationResult has no __post_init__ of its own, so
    # a foreign Enum can reach a plain Optional[float] field unvalidated.
    calib = dataclasses.replace(calibration_result(), forward_clean_price=PayReceive.PAY)
    rec = record(calibration_result=calib)
    with pytest.raises(
        ValueError, match="must be exactly int or float, got PayReceive"
    ) as excinfo:
        quote_record_to_dict(rec)
    assert "BLIImpliedPriceVolCalibrationResult.forward_clean_price" in str(excinfo.value)


def test_foreign_enum_in_enum_typed_field_rejected_not_coerced_by_value():
    # status is enum-typed (BLIImpliedPriceVolCalibrationStatus) but has no
    # alignment check and no __post_init__ validation of its own -- a wrong
    # enum class must be rejected outright, never coerced via .value.
    calib = dataclasses.replace(calibration_result(), status=PayReceive.PAY)
    rec = record(calibration_result=calib)
    with pytest.raises(ValueError) as excinfo:
        quote_record_to_dict(rec)
    message = str(excinfo.value)
    assert "BLIImpliedPriceVolCalibrationResult.status" in message
    assert "BLIImpliedPriceVolCalibrationStatus" in message
    assert "PayReceive" in message


@pytest.mark.parametrize(
    "build_record",
    [lambda: record(), lambda: record(calibration_result=calibration_result())],
    ids=["without-calibration", "with-calibration"],
)
def test_legitimate_exact_typed_enums_round_trip_across_the_object_graph(build_record):
    rec = build_record()
    restored = quote_record_from_dict(quote_record_to_dict(rec))
    assert restored == rec
    # Representative enum-typed fields across request/snapshot/benchmark/
    # comparison/calibration/solver all reconstruct to the exact same enum
    # member identity, not merely an equal string value.
    assert restored.request.bond_option.currency is rec.request.bond_option.currency
    assert restored.request.bond_option.option_type is rec.request.bond_option.option_type
    assert (
        restored.request.market_data_snapshot.volatility_input.volatility_basis
        is rec.request.market_data_snapshot.volatility_input.volatility_basis
    )
    assert restored.benchmark_quote.source_type is rec.benchmark_quote.source_type
    assert restored.benchmark_comparison.status is rec.benchmark_comparison.status
    if rec.calibration_result is not None:
        assert restored.calibration_result.status is rec.calibration_result.status
        assert restored.calibration_result.option_type is rec.calibration_result.option_type
        assert (
            restored.calibration_result.solver_result.status
            is rec.calibration_result.solver_result.status
        )


# --- Complete typed-schema shape validation (_FIELD_SHAPES) ----------------------
#
# Every typed-schema field (every field on every class in _SHAPE_CLASSES,
# except the free-form and warnings/errors fields, which keep their own
# already-approved dedicated validators) is now validated against its
# resolved annotation, on both encode and decode -- there is no remaining
# shape-blind generic fallback for a typed field.


def test_list_rejected_for_typed_schema_tuple_field_with_precise_path():
    calib = dataclasses.replace(calibration_result(), request_support_reasons=[])
    rec = record(calibration_result=calib)
    with pytest.raises(ValueError, match="must be exactly tuple, got list") as excinfo:
        quote_record_to_dict(rec)
    assert "BLIImpliedPriceVolCalibrationResult.request_support_reasons" in str(excinfo.value)


def test_bool_rejected_for_str_typed_field_with_precise_path():
    pricing = pricing_result(method=True)
    rec = record(pricing_result=pricing)
    with pytest.raises(ValueError, match="must be exactly str, got bool") as excinfo:
        quote_record_to_dict(rec)
    assert "PricingResult.method" in str(excinfo.value)


def _record_with_solver_field_override(**overrides):
    calib = calibration_result()
    new_solver = dataclasses.replace(calib.solver_result, **overrides)
    return record(calibration_result=dataclasses.replace(calib, solver_result=new_solver))


@pytest.mark.parametrize(
    "overrides,expected_match,expected_path",
    [
        ({"diagnostic_note": True}, "must be exactly str, got bool", "diagnostic_note"),
        ({"max_iterations": True}, "must be exactly int, got bool", "max_iterations"),
        ({"max_iterations": 100.0}, "must be exactly int, got float", "max_iterations"),
        (
            {"forward_clean_price": "101.0"},
            "must be exactly int or float, got str",
            "forward_clean_price",
        ),
        (
            {"forward_clean_price": True},
            "must be exactly int or float, got bool",
            "forward_clean_price",
        ),
        ({"status": PayReceive.PAY}, "must be exactly one of", "status"),
    ],
    ids=[
        "str-field-gets-bool",
        "int-field-gets-bool",
        "int-field-gets-float",
        "float-field-gets-str",
        "float-field-gets-bool",
        "enum-field-gets-foreign-enum",
    ],
)
def test_representative_primitive_shape_matrix_rejected_at_encode(
    overrides, expected_match, expected_path
):
    rec = _record_with_solver_field_override(**overrides)
    with pytest.raises(ValueError, match=expected_match) as excinfo:
        quote_record_to_dict(rec)
    assert expected_path in str(excinfo.value)


def test_tuple_wrong_item_type_rejected_at_encode():
    calib = dataclasses.replace(calibration_result(), request_support_reasons=("ok", 5))
    rec = record(calibration_result=calib)
    with pytest.raises(ValueError, match="must be exactly str, got int") as excinfo:
        quote_record_to_dict(rec)
    assert "request_support_reasons[1]" in str(excinfo.value)


def test_int_accepted_for_float_typed_field_and_round_trips_exactly():
    # The approved numeric wire rule: a float-annotated field accepts an
    # exact builtin int too (bool excluded); no coercion to float() is
    # performed -- the int identity is preserved through the round trip.
    pricing = pricing_result(pv=2250)
    assert type(pricing.pv) is int
    rec = record(pricing_result=pricing)
    payload = quote_record_to_dict(rec)
    assert payload["pricing_result"]["pv"] == 2250
    assert type(payload["pricing_result"]["pv"]) is int
    restored = quote_record_from_dict(payload)
    assert type(restored.pricing_result.pv) is int
    assert restored.pricing_result.pv == 2250


def test_malformed_wire_primitive_rejected_directly_on_decode():
    payload = quote_record_to_dict(record(calibration_result=calibration_result()))
    payload["pricing_result"]["method"] = True
    with pytest.raises(ValueError, match="must be exactly str, got bool"):
        quote_record_from_dict(payload)


def test_malformed_wire_tuple_shape_rejected_directly_on_decode():
    payload = quote_record_to_dict(record(calibration_result=calibration_result()))
    payload["calibration_result"]["request_support_reasons"] = ["a", "b"]
    with pytest.raises(ValueError, match="must be an explicit tuple tag"):
        quote_record_from_dict(payload)


def test_field_shapes_cover_every_non_special_field_across_the_full_schema():
    # Module-load-time completeness: every non-free-form/non-message-tuple
    # field on every class in _SHAPE_CLASSES (the 15 tagged dataclasses plus
    # BLIQuoteRecord itself) must resolve to a shape -- proving there is no
    # remaining shape-unvalidated typed field anywhere in the reachable
    # schema, not only in the two original Codex reproductions.
    for cls in codec_module._SHAPE_CLASSES:
        for field in fields(cls):
            key = (cls.__name__, field.name)
            if key in codec_module._FREE_FORM_FIELDS or key in codec_module._MESSAGE_TUPLE_FIELDS:
                continue
            assert key in codec_module._FIELD_SHAPES, f"{key} has no resolved field shape"
