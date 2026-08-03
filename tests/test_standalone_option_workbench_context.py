"""Tests for the bounded, read-only case-context extraction (PR #136
correctness follow-up).

Proves :func:`extract_standalone_option_case_context` reads every value
verbatim from the base case (no derivation), never invents a field the case
does not carry (e.g. no CUSIP), and fails explicitly rather than guessing
when the reference-data universe does not have exactly one record matching
the traded instrument's ISIN.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shiori_pricing_lab.app.standalone_option_workbench_context import (
    extract_standalone_option_case_context,
)

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"


def _base_case() -> dict:
    return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_context_fields_are_read_verbatim_from_the_base_case() -> None:
    base_case = _base_case()

    context = extract_standalone_option_case_context(base_case)

    bond_option = base_case["bond_option"]
    reference_data = base_case["bond_reference_data_universe"][0]
    bond_quote = base_case["bond_quote"]

    assert context == {
        "underlying_isin": bond_option["underlying_isin"],
        "expiry_date": bond_option["expiry_date"],
        "issuer": reference_data["issuer"],
        "currency": reference_data["currency"],
        "coupon": reference_data["coupon"],
        "maturity_date": reference_data["maturity_date"],
        "coupon_frequency": reference_data["coupon_frequency"],
        "day_count": reference_data["day_count"],
        "bond_type": reference_data["bond_type"],
        "quote_side": bond_quote["quote_side"],
        "clean_price_per_100": bond_quote["clean_price_per_100"],
        "accrued_interest_per_100": bond_quote["accrued_interest_per_100"],
        "yield_value": bond_quote["yield_value"],
        "valuation_date": base_case["valuation_date"],
        "pricing_timestamp": base_case["pricing_timestamp"],
        "expiry_timestamp": base_case["expiry_timestamp"],
        "forward_settlement_date": base_case["forward_settlement_date"],
        "option_settlement_date": base_case["option_settlement_date"],
        "source_system": base_case["source_system"],
        "as_of_timestamp": base_case["as_of_timestamp"],
        "snapshot_id": base_case["snapshot_id"],
    }


def test_timing_context_fields_come_verbatim_from_the_base_case() -> None:
    # Codex review follow-up: the prototype's old "Time to Expiry" and
    # row was a hardcoded fabricated countdown. These two fields replace it,
    # read verbatim, with no countdown/day-count/business-day math computed
    # anywhere.
    base_case = _base_case()

    context = extract_standalone_option_case_context(base_case)

    assert context["pricing_timestamp"] == base_case["pricing_timestamp"]
    assert context["expiry_timestamp"] == base_case["expiry_timestamp"]


def test_context_never_invents_a_field_the_case_does_not_carry() -> None:
    context = extract_standalone_option_case_context(_base_case())

    # This schema has no CUSIP field anywhere -- the context must not
    # contain one, derived or otherwise.
    assert "cusip" not in context
    assert "CUSIP" not in context


def test_context_preserves_null_yield_value_verbatim() -> None:
    context = extract_standalone_option_case_context(_base_case())

    # The bundled example's bond_quote reports price only; yield_value is
    # null. The context must carry that null through, never inventing or
    # backing out a yield from the price.
    assert context["yield_value"] is None


def test_context_never_mutates_base_case() -> None:
    base_case = _base_case()
    snapshot = copy.deepcopy(base_case)

    extract_standalone_option_case_context(base_case)

    assert base_case == snapshot


def test_context_rejects_non_mapping_input() -> None:
    with pytest.raises(ValueError, match="case must be a mapping"):
        extract_standalone_option_case_context([])


def test_context_rejects_zero_matching_reference_data_records() -> None:
    base_case = _base_case()
    base_case["bond_option"] = {**base_case["bond_option"], "underlying_isin": "NO-SUCH-ISIN"}

    with pytest.raises(ValueError, match="found 0"):
        extract_standalone_option_case_context(base_case)


def test_context_rejects_multiple_matching_reference_data_records() -> None:
    base_case = _base_case()
    duplicate = copy.deepcopy(base_case["bond_reference_data_universe"][0])
    base_case["bond_reference_data_universe"] = [
        *base_case["bond_reference_data_universe"],
        duplicate,
    ]

    with pytest.raises(ValueError, match="found 2"):
        extract_standalone_option_case_context(base_case)
