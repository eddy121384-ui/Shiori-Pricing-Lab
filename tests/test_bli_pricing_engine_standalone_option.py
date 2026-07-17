"""Tests for ``price_bli_mvp_standalone_option`` (Issue #95).

The standalone entrypoint must reach the identical reviewed guard +
forward-clean-price + curve + Black-76 + result composition as the bundle
path ``price_bli_mvp``, for one standalone European price-based
cash-settled bond option with no ``DepositLeg`` /
``BondLinkedStructuredProduct`` wrapper and no Deposit Curve.

Layers (mirroring ``test_bli_pricing_engine.py``):

1. **Pricing-path tests** (``@_requires_quantlib``): pinned success premium
   + intermediate outputs; full ``PricingResult`` equivalence vs the bundle
   path for economically identical inputs (except the intentionally
   different ``product_id`` / ``product_type`` and error-detail
   identifiers); out-of-range curve -> ``FAILED`` / ``ENGINE_ERROR``.
2. **Guard-rejection-path tests** (no QuantLib required): American, yield
   payoff, physical settlement, ``YIELD_VOL``, missing clean price -- all
   rejected as explicit ``FAILED`` results, before any QuantLib-backed
   pricing math.

The pinned constants below equal the bundle path's own pinned constants in
``test_bli_pricing_engine.py`` (derivation lives there); the equivalence
test additionally proves the two paths agree field-for-field, so a
regression in either cannot pass silently.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle import BLIMVPInputBundle
from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import (
    price_bli_mvp,
    price_bli_mvp_standalone_option,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingResult, PricingStatus
from shiori_pricing_lab.products.enums import ExerciseStyle, PayoffBasis, SettlementType

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

# Pinned expected values -- identical to test_bli_pricing_engine.py's own
# constants (that file carries the by-hand Annex A derivation). Re-pinned
# here so a standalone-path regression is caught directly, not only via the
# equivalence assertion.
_EXPECTED_FORWARD_CLEAN_PRICE_PER_100 = 101.22605288103159
_EXPECTED_TIME_TO_EXPIRY = 0.2465753424657534
_EXPECTED_OPTION_DISCOUNT_FACTOR = 0.9929452501091504
_EXPECTED_BLACK76_PV_PER_100 = 4.474769848529296
_EXPECTED_PV = 2.237384924264648

# Synthetic timing/date contract values (Issue #94 human methodology
# approval, comment 5001749998). SYNTHETIC_BLI_MVP_INPUT_BUNDLE's
# bond_option.expiry_date is "2026-09-29"; its valuation_date is "2026-07-01".
_PRICING_TIMESTAMP = "2026-07-01T16:00:00Z"
_EXPIRY_TIMESTAMP = "2026-09-29T16:00:00Z"
_REPORTING_DATE = "2026-07-01"
_FORWARD_SETTLEMENT_DATE = "2026-10-01"
_OPTION_SETTLEMENT_DATE = "2026-10-02"

# The one intentional success-``assumptions`` difference between the two
# entrypoints (Sophira Red-zone review of PR #105). The bundle path
# preserves its exact legacy string (naming the _for_bundle wrapper) as an
# observable-result contract; the standalone path names the primitive it
# actually reaches. Both are pinned exactly below.
_BUNDLE_CURVE_PURPOSE_NOTE = (
    "BOND_REFERENCE_CURVE (used only for forward clean price, via "
    "forward_clean_price_per_100_for_bundle)"
)
_STANDALONE_CURVE_PURPOSE_NOTE = (
    "BOND_REFERENCE_CURVE (used only for forward clean price, via "
    "forward_clean_price_per_100)"
)


def _local_short_tenor_curve_points(currency) -> tuple[BLICurvePoint, ...]:
    """Short-tenor curves that bracket the fixture's ~0.24y expiry.

    Node-for-node identical to ``test_bli_pricing_engine.py``'s helper, so
    the bundle and standalone paths price the *same* economic inputs.
    """

    common = {
        "currency": currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "TEST_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    bond_reference_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_name="TEST_LOCAL_BOND_REFERENCE_CURVE",
            curve_purpose=BLICurvePurpose.BOND_REFERENCE_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.030), ("1Y", 0.035))
    )
    option_discount_nodes = tuple(
        BLICurvePoint(
            curve_id="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_name="TEST_LOCAL_OPTION_DISCOUNT_CURVE",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            **common,
        )
        for tenor, rate in (("1M", 0.028), ("1Y", 0.032))
    )
    existing_deposit_curve_nodes = tuple(
        point
        for point in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points
        if point.curve_purpose is BLICurvePurpose.DEPOSIT_CURVE
    )
    return bond_reference_nodes + option_discount_nodes + existing_deposit_curve_nodes


def _local_supported_snapshot():
    return replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
        snapshot_id="TEST_LOCAL_SUPPORTED_SNAPSHOT",
        source_system="TEST_LOCAL_CURVE",
        curve_points=_local_short_tenor_curve_points(
            SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
        ),
    )


def _local_supported_bundle() -> BLIMVPInputBundle:
    return replace(SYNTHETIC_BLI_MVP_INPUT_BUNDLE, market_data_snapshot=_local_supported_snapshot())


def _standalone_request_from_bundle(bundle: BLIMVPInputBundle) -> BLIStandaloneBondOptionRequest:
    return BLIStandaloneBondOptionRequest(
        bond_option=bundle.product.bond_option,
        resolved_bond_reference_data=bundle.resolved_bond_reference_data,
        valuation_date=bundle.valuation_date,
        market_data_snapshot=bundle.market_data_snapshot,
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )


def _supported_request() -> BLIStandaloneBondOptionRequest:
    return BLIStandaloneBondOptionRequest(
        bond_option=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        resolved_bond_reference_data=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data,
        valuation_date=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date,
        market_data_snapshot=_local_supported_snapshot(),
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )


# --- 1. Supported case: success + pinned outputs -----------------------------


@_requires_quantlib
def test_standalone_supported_case_returns_success_with_pinned_pv():
    result = price_bli_mvp_standalone_option(_supported_request())

    assert isinstance(result, PricingResult)
    assert result.status is PricingStatus.SUCCESS
    assert result.errors == ()
    # product_id / product_type come from the bare BondOption, not a wrapper.
    assert result.product_id == "BONDOPT-SYNTHETIC-0001"
    assert result.product_type == "BOND_OPTION"
    assert result.assumptions["forward_clean_price_per_100"] == pytest.approx(
        _EXPECTED_FORWARD_CLEAN_PRICE_PER_100
    )
    assert result.assumptions["time_to_expiry_year_fraction"] == pytest.approx(
        _EXPECTED_TIME_TO_EXPIRY
    )
    assert result.assumptions["option_discount_factor"] == pytest.approx(
        _EXPECTED_OPTION_DISCOUNT_FACTOR
    )
    assert result.assumptions["black76_pv_per_100"] == pytest.approx(
        _EXPECTED_BLACK76_PV_PER_100
    )
    assert result.pv == pytest.approx(_EXPECTED_PV)
    assert result.dv01 is None
    assert result.method == "black76_forward_clean_price_v1"
    # Standalone path names the primitive it actually reaches (Sophira
    # Red-zone review of PR #105) -- exact, and distinct from the bundle
    # path's preserved legacy string.
    assert (
        result.assumptions["bond_reference_curve_purpose"] == _STANDALONE_CURVE_PURPOSE_NOTE
    )


@_requires_quantlib
def test_standalone_success_documents_option_leg_only_scope():
    result = price_bli_mvp_standalone_option(_supported_request())
    assumptions = result.assumptions
    assert assumptions["priced_component"] == "bond_option_leg"
    assert assumptions["priced_component_scope"] == "option_leg_only_not_full_structured_product"
    assert "deposit_leg" in assumptions["excluded_components"]


# --- 2. Full equivalence vs the bundle path ----------------------------------


@_requires_quantlib
def test_standalone_result_equivalent_to_bundle_result():
    bundle = _local_supported_bundle()
    request = _standalone_request_from_bundle(bundle)

    bundle_result = price_bli_mvp(bundle)
    standalone_result = price_bli_mvp_standalone_option(request)

    assert bundle_result.status is PricingStatus.SUCCESS
    assert standalone_result.status is PricingStatus.SUCCESS

    # Intentionally different identity fields (Issue #95 correction #1).
    assert standalone_result.product_type == "BOND_OPTION"
    assert bundle_result.product_type == "BOND_LINKED_STRUCTURED_PRODUCT"
    assert standalone_result.product_id != bundle_result.product_id

    # The single intentional success-``assumptions`` difference (Sophira
    # Red-zone review of PR #105): both exact strings asserted separately.
    assert bundle_result.assumptions["bond_reference_curve_purpose"] == _BUNDLE_CURVE_PURPOSE_NOTE
    assert (
        standalone_result.assumptions["bond_reference_curve_purpose"]
        == _STANDALONE_CURVE_PURPOSE_NOTE
    )
    assert (
        standalone_result.assumptions["bond_reference_curve_purpose"]
        != bundle_result.assumptions["bond_reference_curve_purpose"]
    )

    # Everything else -- pv, every other numeric output, every OTHER
    # assumption key, currency, dates, provenance, warnings/errors -- is
    # identical. Normalize ONLY product_id / product_type and that one
    # explanatory assumption key (not the whole assumptions mapping), then
    # assert full dataclass equality: any drift in any other assumption key
    # or numeric field would fail this.
    normalized_assumptions = {
        **standalone_result.assumptions,
        "bond_reference_curve_purpose": bundle_result.assumptions["bond_reference_curve_purpose"],
    }
    normalized = replace(
        standalone_result,
        product_id=bundle_result.product_id,
        product_type=bundle_result.product_type,
        assumptions=normalized_assumptions,
    )
    assert normalized == bundle_result


# --- 3. Out-of-range curve -> FAILED / ENGINE_ERROR --------------------------


@_requires_quantlib
def test_standalone_out_of_range_curve_maps_to_engine_error():
    # The shared fixture snapshot's BOND_REFERENCE_CURVE only carries
    # "2Y"/"5Y" nodes, too far out to bracket the ~0.24y expiry -- the same
    # deterministic domain-failure fixture the bundle path uses.
    request = BLIStandaloneBondOptionRequest(
        bond_option=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        resolved_bond_reference_data=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.resolved_bond_reference_data,
        valuation_date=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.valuation_date,
        market_data_snapshot=SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT,
        pricing_timestamp=_PRICING_TIMESTAMP,
        expiry_timestamp=_EXPIRY_TIMESTAMP,
        reporting_date=_REPORTING_DATE,
        forward_settlement_date=_FORWARD_SETTLEMENT_DATE,
        option_settlement_date=_OPTION_SETTLEMENT_DATE,
    )

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.ENGINE_ERROR
    assert "outside the node range" in result.errors[0].message
    assert result.errors[0].detail["exception_type"] == "ValueError"
    # Standalone error detail uses product_id, never bundle_id (correction #5).
    assert result.errors[0].detail["product_id"] == "BONDOPT-SYNTHETIC-0001"
    assert "bundle_id" not in result.errors[0].detail
    assert result.pv is None


# --- 4. Guard-rejection path (no QuantLib required) --------------------------


def test_standalone_american_exercise_fails():
    american_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = replace(_supported_request(), bond_option=american_option)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("exercise_style" in reason for reason in result.errors[0].detail["reasons"])
    assert result.errors[0].detail["product_id"] == "BONDOPT-SYNTHETIC-0001"
    assert "bundle_id" not in result.errors[0].detail
    assert result.pv is None
    assert result.method == "not_supported"


def test_standalone_yield_payoff_fails():
    yield_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        payoff_basis=PayoffBasis.YIELD,
        strike_price=None,
        strike_yield=0.035,
    )
    request = replace(_supported_request(), bond_option=yield_option)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("payoff_basis" in reason for reason in result.errors[0].detail["reasons"])


def test_standalone_physical_settlement_fails():
    # Reachable only via the standalone path: a bare BondOption is not
    # wrapped, so PHYSICAL settlement is constructible and the guard is the
    # thing that rejects it (Issue #95 correction #4).
    physical_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        settlement_type=SettlementType.PHYSICAL,
    )
    request = replace(_supported_request(), bond_option=physical_option)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("settlement_type" in reason for reason in result.errors[0].detail["reasons"])
    assert result.pv is None


def test_standalone_yield_vol_fails():
    yield_vol_snapshot = replace(
        _local_supported_snapshot(),
        volatility_input=replace(
            SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input,
            volatility_basis=BLIVolatilityBasis.YIELD_VOL,
        ),
    )
    request = replace(_supported_request(), market_data_snapshot=yield_vol_snapshot)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("volatility_basis" in reason for reason in result.errors[0].detail["reasons"])


def test_standalone_missing_clean_price_fails():
    yield_only_snapshot = replace(
        _local_supported_snapshot(),
        bond_quote=replace(
            SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.bond_quote,
            clean_price_per_100=None,
            yield_value=0.035,
        ),
    )
    request = replace(_supported_request(), market_data_snapshot=yield_only_snapshot)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.MISSING_MARKET_DATA
    assert any(
        "clean_price_per_100" in reason for reason in result.errors[0].detail["reasons"]
    )


# --- 5. Contract guards ------------------------------------------------------


def test_rejects_raw_bundle():
    with pytest.raises(TypeError, match="BLIStandaloneBondOptionRequest"):
        price_bli_mvp_standalone_option(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)


def test_rejects_none():
    with pytest.raises(TypeError, match="BLIStandaloneBondOptionRequest"):
        price_bli_mvp_standalone_option(None)


def test_does_not_mutate_request():
    # Uses a guard-rejected (American) request so this test is
    # deterministic regardless of whether QuantLib is installed -- guard
    # rejection returns before any QuantLib-backed pricing math, mirroring
    # test_bli_pricing_engine.py::test_does_not_mutate_bundle.
    american_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = replace(_supported_request(), bond_option=american_option)
    before_bond_option = request.bond_option
    before_reference_data = request.resolved_bond_reference_data
    before_snapshot = request.market_data_snapshot
    before_valuation_date = request.valuation_date

    price_bli_mvp_standalone_option(request)

    assert request.bond_option is before_bond_option
    assert request.resolved_bond_reference_data is before_reference_data
    assert request.market_data_snapshot is before_snapshot
    assert request.valuation_date == before_valuation_date
