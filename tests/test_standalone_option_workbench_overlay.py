"""Tests for the bounded six-field standalone-option-case overlay (PR #136).

Proves :func:`apply_standalone_option_case_overlay` is pure (never mutates
its inputs), touches exactly the six approved fields and nothing else, and
rejects a missing/unknown overlay key explicitly. Also proves the resulting
case still prices through the unmodified ``price_standalone_option_case``
and that invalid/non-finite overlay values fail through the existing typed
constructors, exactly like any other case field would.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from shiori_pricing_lab.app.standalone_option_workbench import price_standalone_option_case
from shiori_pricing_lab.app.standalone_option_workbench_overlay import (
    OVERLAY_FIELDS,
    apply_standalone_option_case_overlay,
    extract_standalone_option_case_overlay,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingStatus

_QUANTLIB_SKIP = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"


def _base_case() -> dict:
    return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))


def _identity_overlay(case: dict) -> dict:
    """Return the overlay that reproduces ``case``'s own current values."""

    return extract_standalone_option_case_overlay(case)


def test_overlay_fields_has_exactly_six_approved_keys() -> None:
    assert OVERLAY_FIELDS == {
        "option_type",
        "position",
        "strike_price",
        "notional",
        "volatility",
        "forward_clean_price_per_100",
    }


def test_apply_overlay_never_mutates_base_case() -> None:
    base_case = _base_case()
    snapshot = copy.deepcopy(base_case)
    overlay = {
        "option_type": "PUT",
        "position": "SELL",
        "strike_price": 100.0,
        "notional": 25.0,
        "volatility": 0.2,
        "forward_clean_price_per_100": 102.0,
    }

    apply_standalone_option_case_overlay(base_case, overlay)

    assert base_case == snapshot


def test_apply_overlay_replaces_only_the_six_fields() -> None:
    base_case = _base_case()
    overlay = {
        "option_type": "PUT",
        "position": "SELL",
        "strike_price": 100.0,
        "notional": 25.0,
        "volatility": 0.2,
        "forward_clean_price_per_100": 102.0,
    }

    overlaid_case = apply_standalone_option_case_overlay(base_case, overlay)

    # Every top-level key other than the three touched sections is untouched.
    for key, value in base_case.items():
        if key in ("bond_option", "volatility_input", "forward_clean_price_input"):
            continue
        assert overlaid_case[key] == value

    # The touched sections carry the overlay values for exactly the six
    # fields, and every other field in those sections is unchanged.
    for key, value in base_case["bond_option"].items():
        if key in ("option_type", "position", "strike_price", "notional"):
            continue
        assert overlaid_case["bond_option"][key] == value
    assert overlaid_case["bond_option"]["option_type"] == "PUT"
    assert overlaid_case["bond_option"]["position"] == "SELL"
    assert overlaid_case["bond_option"]["strike_price"] == 100.0
    assert overlaid_case["bond_option"]["notional"] == 25.0

    for key, value in base_case["volatility_input"].items():
        if key == "volatility":
            continue
        assert overlaid_case["volatility_input"][key] == value
    assert overlaid_case["volatility_input"]["volatility"] == 0.2

    for key, value in base_case["forward_clean_price_input"].items():
        if key == "forward_clean_price_per_100":
            continue
        assert overlaid_case["forward_clean_price_input"][key] == value
    assert overlaid_case["forward_clean_price_input"]["forward_clean_price_per_100"] == 102.0


def test_apply_overlay_with_identity_overlay_reproduces_base_case() -> None:
    base_case = _base_case()

    overlaid_case = apply_standalone_option_case_overlay(base_case, _identity_overlay(base_case))

    assert overlaid_case == base_case


@pytest.mark.parametrize(
    "overlay",
    [
        {},
        {"option_type": "CALL"},
        {
            "option_type": "CALL",
            "position": "BUY",
            "strike_price": 1.0,
            "notional": 1.0,
            "volatility": 0.1,
            # missing forward_clean_price_per_100
        },
    ],
)
def test_apply_overlay_rejects_missing_keys(overlay: dict) -> None:
    with pytest.raises(ValueError, match="missing required field"):
        apply_standalone_option_case_overlay(_base_case(), overlay)


def test_apply_overlay_rejects_unknown_keys() -> None:
    overlay = {
        "option_type": "CALL",
        "position": "BUY",
        "strike_price": 1.0,
        "notional": 1.0,
        "volatility": 0.1,
        "forward_clean_price_per_100": 100.0,
        "extra_field": 1,
    }

    with pytest.raises(ValueError, match="unknown field"):
        apply_standalone_option_case_overlay(_base_case(), overlay)


def test_apply_overlay_rejects_non_mapping_inputs() -> None:
    with pytest.raises(ValueError, match="base_case must be a mapping"):
        apply_standalone_option_case_overlay([], {})
    with pytest.raises(ValueError, match="overlay must be a mapping"):
        apply_standalone_option_case_overlay(_base_case(), [])


@_QUANTLIB_SKIP
def test_overlaid_case_prices_through_unmodified_entry_point() -> None:
    base_case = _base_case()
    overlay = _identity_overlay(base_case)

    overlaid_case = apply_standalone_option_case_overlay(base_case, overlay)
    _, result, display = price_standalone_option_case(overlaid_case)

    assert result.status is PricingStatus.SUCCESS
    assert display["model_fair_premium_per_100"] == pytest.approx(4.551011126839255)
    assert display["total_notional_model_fair_premium"] == pytest.approx(2.2755055634196273)


@_QUANTLIB_SKIP
@pytest.mark.parametrize(
    "field,bad_value,match",
    [
        ("strike_price", math.nan, "finite number"),
        ("strike_price", -5.0, "positive"),
        ("notional", math.inf, "finite number"),
        ("notional", 0.0, "positive"),
        ("volatility", math.nan, "finite number"),
        ("volatility", -0.1, "positive"),
        ("forward_clean_price_per_100", math.nan, "finite number"),
        ("forward_clean_price_per_100", 0.0, "positive"),
        ("option_type", "STRADDLE", None),
        ("position", "HOLD", None),
    ],
)
def test_invalid_overlay_values_fail_through_existing_constructors(
    field: str, bad_value: object, match: str | None
) -> None:
    base_case = _base_case()
    overlay = _identity_overlay(base_case)
    overlay[field] = bad_value
    overlaid_case = apply_standalone_option_case_overlay(base_case, overlay)

    with pytest.raises((ValueError, TypeError), match=match):
        price_standalone_option_case(overlaid_case)
