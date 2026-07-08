"""Tests for the UI-only BLI MVP demo fixtures (Issue #84).

Proves the demo bundles are real ``BLIMVPInputBundle`` instances built from
existing shared fixtures via ``dataclasses.replace`` -- not UI mocks -- and
that the happy-path bundle actually prices to SUCCESS through the real
``price_bli_mvp`` engine, and the error-path bundle is the unmodified
shared fixture reused as-is.
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.app.bli_mvp_ui_demo_fixture import (
    SYNTHETIC_UI_DEMO_BUNDLE,
    SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR,
)
from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingStatus

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)


def test_demo_bundles_are_bli_mvp_input_bundles():
    assert isinstance(SYNTHETIC_UI_DEMO_BUNDLE, BLIMVPInputBundle)
    assert isinstance(SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR, BLIMVPInputBundle)


def test_curve_range_error_demo_is_the_unmodified_shared_fixture():
    assert SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR is SYNTHETIC_BLI_MVP_INPUT_BUNDLE


def test_happy_path_demo_reuses_shared_product_and_bundle_id():
    # Only the market_data_snapshot's curve_points differ from the shared
    # fixture -- product, bundle_id, valuation_date are untouched.
    assert SYNTHETIC_UI_DEMO_BUNDLE.bundle_id == SYNTHETIC_BLI_MVP_INPUT_BUNDLE.bundle_id
    assert SYNTHETIC_UI_DEMO_BUNDLE.product is SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product
    assert (
        SYNTHETIC_UI_DEMO_BUNDLE.valuation_date == SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date
    )
    assert (
        SYNTHETIC_UI_DEMO_BUNDLE.market_data_snapshot.curve_points
        != SYNTHETIC_BLI_MVP_INPUT_BUNDLE.market_data_snapshot.curve_points
    )


@_requires_quantlib
def test_happy_path_demo_prices_to_success():
    result = price_bli_mvp(SYNTHETIC_UI_DEMO_BUNDLE)
    assert result.status is PricingStatus.SUCCESS
    assert result.pv is not None
    assert result.errors == ()


@_requires_quantlib
def test_error_path_demo_prices_to_engine_error():
    result = price_bli_mvp(SYNTHETIC_UI_DEMO_BUNDLE_CURVE_RANGE_ERROR)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.ENGINE_ERROR
    assert result.pv is None
