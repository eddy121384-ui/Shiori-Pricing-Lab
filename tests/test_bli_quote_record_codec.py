import copy
import importlib.machinery
import importlib.util
import json

import pytest

from shiori_pricing_lab.data.bli_quote_record import BLIQuoteRecordOverrideReason
from shiori_pricing_lab.data.bli_quote_record_codec import (
    bli_quote_record_from_canonical_json,
    bli_quote_record_from_typed_dict,
    bli_quote_record_to_canonical_json,
    bli_quote_record_to_typed_dict,
)

_loader = importlib.machinery.SourceFileLoader(
    "quote_record_helpers", "tests/test_bli_quote_record.py"
)
_spec = importlib.util.spec_from_loader("quote_record_helpers", _loader)
_helpers = importlib.util.module_from_spec(_spec)
_loader.exec_module(_helpers)
record = _helpers.record


def test_typed_dict_round_trip_is_exact_and_non_mutating():
    rec = record()
    before = rec
    payload = bli_quote_record_to_typed_dict(rec)
    payload_before = copy.deepcopy(payload)
    restored = bli_quote_record_from_typed_dict(payload)
    assert restored == rec
    assert rec == before
    assert payload == payload_before
    assert payload["model_fair_value_per_100"] == 1.25
    assert payload["benchmark_quote"]["premium_per_100"] == 1.2
    assert payload["client_quote_per_100"] == 1.3


def test_canonical_json_round_trip_is_deterministic_with_trailing_newline():
    rec = record()
    first = bli_quote_record_to_canonical_json(rec)
    second = bli_quote_record_to_canonical_json(rec)
    assert first == second
    assert first.endswith("\n")
    assert json.loads(first)["schema_version"] == 1
    assert bli_quote_record_from_canonical_json(first) == rec


@pytest.mark.parametrize("field", ["schema_version", "pricing_result", "benchmark_quote"])
def test_missing_top_level_field_rejected(field):
    payload = bli_quote_record_to_typed_dict(record())
    payload.pop(field)
    with pytest.raises(ValueError):
        bli_quote_record_from_typed_dict(payload)


def test_unknown_top_level_field_rejected():
    payload = bli_quote_record_to_typed_dict(record())
    payload["extra"] = "nope"
    with pytest.raises(ValueError):
        bli_quote_record_from_typed_dict(payload)


@pytest.mark.parametrize("path", [
    ("pricing_result", "status", "NOT_A_STATUS"),
    ("benchmark_quote", "quote_side", "SIDEWAYS"),
    ("comparison_result", "reason", "NOPE"),
])
def test_nested_invalid_enum_rejected(path):
    section, key, value = path
    payload = bli_quote_record_to_typed_dict(record())
    payload[section][key] = value
    with pytest.raises(ValueError):
        bli_quote_record_from_typed_dict(payload)


def test_nested_unknown_field_rejected():
    payload = bli_quote_record_to_typed_dict(record())
    payload["benchmark_quote"]["unknown"] = "nope"
    with pytest.raises(ValueError):
        bli_quote_record_from_typed_dict(payload)


def test_unsupported_schema_version_rejected():
    payload = bli_quote_record_to_typed_dict(record())
    payload["schema_version"] = 2
    with pytest.raises(ValueError):
        bli_quote_record_from_typed_dict(payload)


@pytest.mark.parametrize("text", ["{", "[]", "null", "1"])
def test_malformed_or_non_object_json_rejected(text):
    with pytest.raises(ValueError):
        bli_quote_record_from_canonical_json(text)


def test_non_finite_numbers_rejected_before_json_output():
    payload = bli_quote_record_to_typed_dict(record())
    payload["model_total_value"] = float("inf")
    with pytest.raises(ValueError):
        bli_quote_record_from_typed_dict(payload)


def test_override_codec_round_trip():
    rec = record(
        override_applied=True,
        override_reason=BLIQuoteRecordOverrideReason.OTHER,
        override_note="synthetic override note",
    )
    restored = bli_quote_record_from_canonical_json(bli_quote_record_to_canonical_json(rec))
    assert restored.override_applied is True
    assert restored.override_reason is BLIQuoteRecordOverrideReason.OTHER
