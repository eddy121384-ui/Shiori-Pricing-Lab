"""Tests for the BLI MVP Streamlit page module (Issue #84).

Three layers:

1. Import smoke test (no Streamlit runtime required).
2. ``prepare_bli_mvp_display`` maps ``PricingResult`` fields verbatim, for
   both a hand-built SUCCESS result (deterministic, no QuantLib needed)
   and a hand-built FAILED result -- proving the UI does not recompute or
   relabel anything.
3. A static source scan proving the module does not import or define any
   pricing-math / valuation / curve helper of its own.
"""

from __future__ import annotations

import inspect

import pytest

from shiori_pricing_lab.app import bli_mvp_ui as bli_mvp_ui_module
from shiori_pricing_lab.app.bli_mvp_ui import prepare_bli_mvp_display
from shiori_pricing_lab.app.bli_mvp_ui_demo_fixture import SYNTHETIC_UI_DEMO_BUNDLE
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import (
    PricingErrorCode,
    PricingMessage,
    PricingResult,
    PricingStatus,
)

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)


# --- 1. Import smoke test ----------------------------------------------------


def test_module_imports_cleanly():
    assert hasattr(bli_mvp_ui_module, "render_bli_mvp_page")
    assert hasattr(bli_mvp_ui_module, "prepare_bli_mvp_display")


# --- 2. prepare_bli_mvp_display maps PricingResult verbatim ------------------

_SUCCESS_RESULT = PricingResult(
    product_id="TEST_PRODUCT",
    product_type="BondLinkedStructuredProduct",
    valuation_date="2026-07-01",
    result_currency="USD",
    status=PricingStatus.SUCCESS,
    engine_name="test_engine",
    engine_version="1.0.0",
    method="black76_forward_clean_price_v1",
    market_data_as_of="2026-07-01T00:00:00Z",
    pv=2.237384924264648,
    assumptions={
        "forward_clean_price_per_100": 101.22605288103159,
        "black76_pv_per_100": 4.474769848529296,
        "option_discount_factor": 0.9929452501091504,
        "time_to_expiry_year_fraction": 0.2465753424657534,
        "pv_scaling_formula": "pv = black76_pv_per_100 * notional / 100",
        "priced_component": "bond_option_leg",
        "priced_component_scope": "option_leg_only_not_full_structured_product",
        "excluded_components": ["deposit_leg", "principal_redemption", "physical_delivery"],
    },
)

_FAILED_RESULT = PricingResult(
    product_id="TEST_PRODUCT",
    product_type="BondLinkedStructuredProduct",
    valuation_date="2026-07-01",
    result_currency="USD",
    status=PricingStatus.FAILED,
    engine_name="test_engine",
    engine_version="1.0.0",
    method="black76_forward_clean_price_v1",
    market_data_as_of="2026-07-01T00:00:00Z",
    errors=(
        PricingMessage(
            code=PricingErrorCode.ENGINE_ERROR,
            message="target_year_fraction is outside the node range",
            detail={"exception_type": "ValueError"},
        ),
    ),
)


def test_prepare_display_maps_success_result_verbatim():
    display = prepare_bli_mvp_display(_SUCCESS_RESULT)

    assert display["status"] == "SUCCESS"
    assert display["method"] == _SUCCESS_RESULT.method
    assert display["pv"] == _SUCCESS_RESULT.pv
    for key in (
        "forward_clean_price_per_100",
        "black76_pv_per_100",
        "option_discount_factor",
        "time_to_expiry_year_fraction",
        "pv_scaling_formula",
        "priced_component",
        "priced_component_scope",
        "excluded_components",
    ):
        assert display[key] == _SUCCESS_RESULT.assumptions[key]
    assert display["errors"] == []


def test_prepare_display_maps_failed_result_verbatim():
    display = prepare_bli_mvp_display(_FAILED_RESULT)

    assert display["status"] == "FAILED"
    assert display["pv"] is None
    assert display["forward_clean_price_per_100"] is None
    assert display["errors"] == [
        {
            "code": "ENGINE_ERROR",
            "message": "target_year_fraction is outside the node range",
        }
    ]


@_requires_quantlib
def test_prepare_display_on_real_engine_success_result():
    result = price_bli_mvp(SYNTHETIC_UI_DEMO_BUNDLE)
    assert result.status is PricingStatus.SUCCESS

    display = prepare_bli_mvp_display(result)
    assert display["pv"] == result.pv
    assert (
        display["forward_clean_price_per_100"]
        == result.assumptions["forward_clean_price_per_100"]
    )
    assert display["black76_pv_per_100"] == result.assumptions["black76_pv_per_100"]


# --- 3. No pricing-math / valuation logic in the UI module -------------------


def test_module_defines_no_pricing_math_or_curve_logic():
    module_names = set(dir(bli_mvp_ui_module))
    forbidden_names = {
        "black76_price_option_pv_per_100",
        "forward_clean_price_per_100_for_bundle",
        "discount_factor_from_continuous_zero_curve",
        "year_fraction_to_expiry",
        "check_bli_mvp_required_inputs",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_module_source_has_no_recomputation_of_pricing_math():
    source = inspect.getsource(bli_mvp_ui_module)
    forbidden_substrings = (
        "math.exp(",
        "math.log(",
        "norm.cdf",
        "scipy.stats",
        "import QuantLib",
        "date.today()",
        "datetime.now()",
    )
    for substring in forbidden_substrings:
        assert substring not in source, f"unexpected pricing-math substring: {substring}"
