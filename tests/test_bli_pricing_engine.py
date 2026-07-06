"""Tests for the BLI pricing engine skeleton (docs/25 implementation slice).

Scope: this is a callable-seam test suite for `price_bli_mvp`, not a
pricing-methodology test suite -- there is no real valuation math to pin
down yet (docs/25 §7's explicit non-goals). These tests exist to lock in
the deterministic "not implemented" contract and the input/scope
boundaries docs/25 §8 requires, mirroring the boundary-test pattern
already used in tests/test_bli_mvp_input_bundle_builder.py.
"""

from __future__ import annotations

import pytest

from shiori_pricing_lab.data import bli_mvp_input_bundle_builder as builder_module
from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import BLIMarketDataSnapshot
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.pricing import bli_pricing_engine as bli_pricing_engine_module
from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingResult, PricingStatus
from shiori_pricing_lab.products.fixtures import SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.fixtures import SYNTHETIC_BOND_FIXTURES

# --- 1/2/3. Happy path: deterministic not-implemented result --------------


def test_accepts_synthetic_bundle_and_returns_pricing_result():
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert isinstance(result, PricingResult)
    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT


def test_result_is_deterministic_across_repeated_calls():
    first = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    second = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert first == second


def test_result_identity_fields_match_bundle():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    result = price_bli_mvp(bundle)
    assert result.product_id == bundle.product.product_id
    assert result.product_type == bundle.product.product_type
    assert result.valuation_date == bundle.valuation_date
    assert result.market_data_as_of == bundle.market_data_snapshot.as_of_timestamp
    assert result.errors[0].detail["bundle_id"] == bundle.bundle_id
    assert result.errors[0].detail["product_id"] == bundle.product.product_id


# --- 4. Wrong input type raises TypeError ----------------------------------


def test_rejects_raw_bond_linked_structured_product():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT)


def test_rejects_raw_bond_reference_data():
    bond_reference_data = SYNTHETIC_BOND_FIXTURES[0]
    assert isinstance(bond_reference_data, BondReferenceData)
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(bond_reference_data)


def test_rejects_raw_market_data_snapshot():
    assert isinstance(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, BLIMarketDataSnapshot)
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT)


def test_rejects_none():
    with pytest.raises(TypeError, match="BLIMVPInputBundle"):
        price_bli_mvp(None)


# --- 5. Does not mutate the bundle ------------------------------------------


def test_does_not_mutate_bundle():
    bundle = SYNTHETIC_BLI_MVP_INPUT_BUNDLE
    before_bundle_id = bundle.bundle_id
    before_valuation_date = bundle.valuation_date
    before_product = bundle.product
    before_snapshot = bundle.market_data_snapshot
    price_bli_mvp(bundle)
    # BLIMVPInputBundle is frozen (docs/24), so this is guaranteed by
    # construction -- this test documents the expectation explicitly
    # (docs/25 §8 test 5), including that nested references are unchanged.
    assert bundle.bundle_id == before_bundle_id
    assert bundle.valuation_date == before_valuation_date
    assert bundle.product is before_product
    assert bundle.market_data_snapshot is before_snapshot


# --- 6. Does not call the builder or the resolver ---------------------------


def test_does_not_call_build_bli_mvp_input_bundle(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("price_bli_mvp must not call build_bli_mvp_input_bundle")

    monkeypatch.setattr(builder_module, "build_bli_mvp_input_bundle", _fail_if_called)
    # price_bli_mvp imports BLIMVPInputBundle directly, not through the
    # builder module, so this monkeypatch would only matter if the engine
    # called through builder_module -- asserting it still succeeds proves
    # no such call path exists.
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert result.status is PricingStatus.FAILED


def test_does_not_call_resolve_bond_reference_data(monkeypatch):
    from shiori_pricing_lab.reference_data import resolution as resolution_module

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("price_bli_mvp must not call resolve_bond_reference_data")

    monkeypatch.setattr(resolution_module, "resolve_bond_reference_data", _fail_if_called)
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert result.status is PricingStatus.FAILED


def test_module_does_not_import_builder_or_resolver_functions():
    module_names = set(dir(bli_pricing_engine_module))
    forbidden_names = {
        "build_bli_mvp_input_bundle",
        "resolve_bond_reference_data",
    }
    assert module_names.isdisjoint(forbidden_names)


# --- 7. No fake numeric pricing output --------------------------------------


def test_no_fake_numeric_pricing_output():
    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)
    assert result.pv is None
    assert result.dv01 is None
    assert result.cashflows is None
    assert result.scenario_results is None


# --- 8. Scope boundary: no pricing/interpolation/schedule/connector --------


def test_module_defines_no_valuation_or_interpolation_or_connector_logic():
    module_names = set(dir(bli_pricing_engine_module))
    forbidden_names = {
        "compute_pv",
        "interpolate",
        "interpolate_curve",
        "convert_yield_to_price",
        "convert_price_to_yield",
        "generate_cashflows",
        "build_schedule",
        "QuantLib",
        "register_engine",
        "PricingEngineRegistry",
    }
    assert module_names.isdisjoint(forbidden_names)


def test_no_bli_specific_result_or_status_type_is_defined():
    # docs/25 §4's default-to-reuse question: this skeleton must not
    # introduce BLIPricingResult / BLIPricingStatus at all.
    module_names = set(dir(bli_pricing_engine_module))
    assert "BLIPricingResult" not in module_names
    assert "BLIPricingStatus" not in module_names


def test_does_not_register_a_product_type_engine():
    # docs/25 §4 Q2: the skeleton does not adapt to the PricingEngine
    # Protocol / registry -- it is not a registered per-product-type
    # engine, it is a direct bundle-based entrypoint.
    assert not hasattr(bli_pricing_engine_module, "register_engine")


def test_bundle_is_still_the_bli_mvp_input_bundle_type():
    # Sanity check that the fixture used throughout this file is really
    # the type price_bli_mvp requires -- guards against a future fixture
    # refactor silently changing the type under these tests.
    assert isinstance(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, BLIMVPInputBundle)
