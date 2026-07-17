"""Tests for ``price_bli_mvp_standalone_option`` (Issue #95 + OVME alignment #94).

The standalone entrypoint implements the Eddy-approved Bloomberg (OVME)
methodology (Issue #94, comments 5001749998 / 5003670704) for one standalone
European price-based cash-settled bond option: an explicit forward clean
price input, dirty forward / dirty strike in Black-76, fractional-timestamp
ACT/ACT option time, and an Option Discount Curve discount factor to option
settlement divided by the factor to the reporting date. It is a **separate
numeric composition** from the legacy bundle path ``price_bli_mvp`` -- there
is no bundle-equivalence here (the two methodologies deliberately differ),
and the bundle path is numerically and contractually unchanged by this slice.

Layers:

1. **Pricing-path tests** (``@_requires_quantlib``): pinned success premium
   + intermediate OVME outputs; a yield-only spot quote still prices (the
   forward is explicit); out-of-range Option Discount Curve -> ``FAILED`` /
   ``ENGINE_ERROR``.
2. **Guard-rejection-path tests** (no QuantLib required): American, yield
   payoff, physical settlement, ``YIELD_VOL`` -- all rejected as explicit
   ``FAILED`` results before any QuantLib-backed pricing math.

The pinned constants below are reproduced by the anonymized OVME golden case
in ``tests/test_bli_ovme_golden_case.py`` for a different, hand-derived set
of inputs; these synthetic-fixture pins guard the wiring for the shared
bundle-fixture economics.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from shiori_pricing_lab.data.bli_mvp_input_bundle_fixtures import (
    SYNTHETIC_BLI_MVP_INPUT_BUNDLE,
)
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIForwardCleanPriceInput,
    BLIMarketDataStatus,
    BLIVolatilityBasis,
)
from shiori_pricing_lab.data.bli_snapshot_fixtures import SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
from shiori_pricing_lab.data.bli_standalone_option_request import (
    BLIStandaloneBondOptionRequest,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import (
    price_bli_mvp_standalone_option,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingResult, PricingStatus
from shiori_pricing_lab.products.enums import (
    ExerciseStyle,
    PayoffBasis,
    SettlementType,
    TreasuryFTPQuoteSide,
)

_QUANTLIB_AVAILABLE = is_quantlib_available()
_requires_quantlib = pytest.mark.skipif(
    not _QUANTLIB_AVAILABLE, reason="QuantLib is not installed in this environment"
)

# Pinned expected OVME values for the shared bundle-fixture economics with an
# explicit MID forward clean price of 101.30. Accrued interest is at the
# forward settlement date 2026-10-01; dirty = clean + accrued. reporting_date
# equals valuation_date, so the pricing-to-reporting DF is exactly 1.0 and the
# effective DF equals the pricing-to-option-settlement DF.
_EXPECTED_FORWARD_CLEAN_PRICE_PER_100 = 101.30
_EXPECTED_ACCRUED_AT_FORWARD_SETTLEMENT = 0.9590163934426231
_EXPECTED_FORWARD_DIRTY_PRICE_PER_100 = 102.25901639344262
_EXPECTED_STRIKE_DIRTY_PRICE_PER_100 = 100.45901639344262
_EXPECTED_TIME_TO_EXPIRY = 0.2465753424657534
_EXPECTED_PRICING_TO_REPORTING_DF = 1.0
_EXPECTED_PRICING_TO_OPTION_SETTLEMENT_DF = 0.9927018791932419
_EXPECTED_EFFECTIVE_DF = 0.9927018791932419
_EXPECTED_BLACK76_PV_PER_100 = 4.550638980692688
_EXPECTED_PV = 2.275319490346344

# Synthetic timing/date contract values (Issue #94 human methodology approval,
# comment 5001749998). The bundle fixture's bond_option.expiry_date is
# "2026-09-29"; its valuation_date is "2026-07-01".
_PRICING_TIMESTAMP = "2026-07-01T16:00:00Z"
_EXPIRY_TIMESTAMP = "2026-09-29T16:00:00Z"
_REPORTING_DATE = "2026-07-01"
_FORWARD_SETTLEMENT_DATE = "2026-10-01"
_OPTION_SETTLEMENT_DATE = "2026-10-02"

_FORWARD_INPUT = BLIForwardCleanPriceInput(
    forward_clean_price_per_100=_EXPECTED_FORWARD_CLEAN_PRICE_PER_100,
    quote_side=TreasuryFTPQuoteSide.MID,
    source_system="TEST_LOCAL_FORWARD_FEED",
    status=BLIMarketDataStatus.ACTIVE,
)


def _local_option_discount_curve(currency) -> tuple[BLICurvePoint, ...]:
    """Short-tenor Option Discount Curve bracketing the ~93-day settlement."""

    common = {
        "currency": currency,
        "rate_basis": BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        "source_system": "TEST_LOCAL_CURVE",
        "status": BLIMarketDataStatus.ACTIVE,
    }
    return tuple(
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


def _local_supported_snapshot(**overrides):
    params = dict(
        snapshot_id="TEST_LOCAL_SUPPORTED_SNAPSHOT",
        source_system="TEST_LOCAL_CURVE",
        curve_points=_local_option_discount_curve(
            SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.currency
        ),
        forward_clean_price_input=_FORWARD_INPUT,
    )
    params.update(overrides)
    return replace(SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, **params)


def _supported_request(**overrides) -> BLIStandaloneBondOptionRequest:
    params = dict(
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
    params.update(overrides)
    return BLIStandaloneBondOptionRequest(**params)


# --- 1. Supported case: success + pinned OVME outputs ------------------------


@_requires_quantlib
def test_standalone_supported_case_returns_success_with_pinned_pv():
    result = price_bli_mvp_standalone_option(_supported_request())

    assert isinstance(result, PricingResult)
    assert result.status is PricingStatus.SUCCESS
    assert result.errors == ()
    # product_id / product_type come from the bare BondOption, not a wrapper.
    assert result.product_id == "BONDOPT-SYNTHETIC-0001"
    assert result.product_type == "BOND_OPTION"
    assert result.method == "black76_forward_dirty_price_ovme_v1"

    a = result.assumptions
    assert a["methodology"] == "ovme_dirty_price_black76_act_act_option_discount_curve"
    assert a["forward_clean_price_per_100"] == pytest.approx(
        _EXPECTED_FORWARD_CLEAN_PRICE_PER_100
    )
    assert a["forward_clean_price_source_system"] == "TEST_LOCAL_FORWARD_FEED"
    assert a["forward_clean_price_quote_side"] == "MID"
    assert a["accrued_interest_at_forward_settlement_per_100"] == pytest.approx(
        _EXPECTED_ACCRUED_AT_FORWARD_SETTLEMENT
    )
    assert a["forward_dirty_price_per_100"] == pytest.approx(
        _EXPECTED_FORWARD_DIRTY_PRICE_PER_100
    )
    assert a["strike_dirty_price_per_100"] == pytest.approx(
        _EXPECTED_STRIKE_DIRTY_PRICE_PER_100
    )
    assert a["time_to_expiry_year_fraction"] == pytest.approx(_EXPECTED_TIME_TO_EXPIRY)
    assert a["time_to_expiry_convention"] == "ACT_ACT_ISDA_fractional_timestamp"
    assert a["pricing_to_reporting_discount_factor"] == pytest.approx(
        _EXPECTED_PRICING_TO_REPORTING_DF
    )
    assert a["pricing_to_option_settlement_discount_factor"] == pytest.approx(
        _EXPECTED_PRICING_TO_OPTION_SETTLEMENT_DF
    )
    assert a["effective_reporting_date_discount_factor"] == pytest.approx(
        _EXPECTED_EFFECTIVE_DF
    )
    assert a["black76_pv_per_100"] == pytest.approx(_EXPECTED_BLACK76_PV_PER_100)
    assert a["position_sign_applied"] is False
    assert a["forward_construction"] == (
        "explicit_forward_clean_price_input_no_repo_forward_construction"
    )
    # No legacy bond-reference-curve-forward assumption survives.
    assert "bond_reference_curve_purpose" not in a
    assert result.pv == pytest.approx(_EXPECTED_PV)
    assert result.dv01 is None


@_requires_quantlib
def test_standalone_success_documents_option_leg_only_scope():
    result = price_bli_mvp_standalone_option(_supported_request())
    assumptions = result.assumptions
    assert assumptions["priced_component"] == "bond_option_leg"
    assert assumptions["priced_component_scope"] == "option_leg_only_not_full_structured_product"
    assert "deposit_leg" in assumptions["excluded_components"]


@_requires_quantlib
def test_standalone_yield_only_spot_quote_still_prices():
    # OVME alignment (Issue #94): the forward is explicit, so a yield-only
    # spot bond_quote (no clean price) no longer blocks pricing.
    yield_only_snapshot = _local_supported_snapshot(
        bond_quote=replace(
            SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.bond_quote,
            clean_price_per_100=None,
            yield_value=0.035,
        )
    )
    request = _supported_request(market_data_snapshot=yield_only_snapshot)
    result = price_bli_mvp_standalone_option(request)
    assert result.status is PricingStatus.SUCCESS
    assert result.pv == pytest.approx(_EXPECTED_PV)


# --- 2. Out-of-range Option Discount Curve -> FAILED / ENGINE_ERROR ----------


@_requires_quantlib
def test_standalone_out_of_range_curve_maps_to_engine_error():
    # The shared fixture snapshot's OPTION_DISCOUNT_CURVE only carries
    # "2Y"/"5Y" nodes, too far out to bracket the ~93-day option settlement
    # coordinate -- a deterministic domain failure.
    out_of_range_snapshot = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, forward_clean_price_input=_FORWARD_INPUT
    )
    request = _supported_request(market_data_snapshot=out_of_range_snapshot)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.ENGINE_ERROR
    assert "outside the node range" in result.errors[0].message
    assert result.errors[0].detail["exception_type"] == "ValueError"
    # Standalone error detail uses product_id, never bundle_id.
    assert result.errors[0].detail["product_id"] == "BONDOPT-SYNTHETIC-0001"
    assert "bundle_id" not in result.errors[0].detail
    assert result.pv is None


# --- 3. Guard-rejection path (no QuantLib required) --------------------------


def test_standalone_american_exercise_fails():
    american_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = _supported_request(bond_option=american_option)

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
    request = _supported_request(bond_option=yield_option)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("payoff_basis" in reason for reason in result.errors[0].detail["reasons"])


def test_standalone_physical_settlement_fails():
    # Reachable only via the standalone path: a bare BondOption is not
    # wrapped, so PHYSICAL settlement is constructible and the guard rejects
    # it.
    physical_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        settlement_type=SettlementType.PHYSICAL,
    )
    request = _supported_request(bond_option=physical_option)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("settlement_type" in reason for reason in result.errors[0].detail["reasons"])
    assert result.pv is None


def test_standalone_yield_vol_fails():
    yield_vol_snapshot = _local_supported_snapshot(
        volatility_input=replace(
            SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.volatility_input,
            volatility_basis=BLIVolatilityBasis.YIELD_VOL,
        ),
    )
    request = _supported_request(market_data_snapshot=yield_vol_snapshot)

    result = price_bli_mvp_standalone_option(request)

    assert result.status is PricingStatus.FAILED
    assert result.errors[0].code is PricingErrorCode.UNSUPPORTED_PRODUCT
    assert any("volatility_basis" in reason for reason in result.errors[0].detail["reasons"])


# --- 4. Contract guards ------------------------------------------------------


def test_rejects_raw_bundle():
    with pytest.raises(TypeError, match="BLIStandaloneBondOptionRequest"):
        price_bli_mvp_standalone_option(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)


def test_rejects_none():
    with pytest.raises(TypeError, match="BLIStandaloneBondOptionRequest"):
        price_bli_mvp_standalone_option(None)


def test_does_not_mutate_request():
    # Uses a guard-rejected (American) request so this test is deterministic
    # regardless of whether QuantLib is installed -- guard rejection returns
    # before any QuantLib-backed pricing math.
    american_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    request = _supported_request(bond_option=american_option)
    before_bond_option = request.bond_option
    before_reference_data = request.resolved_bond_reference_data
    before_snapshot = request.market_data_snapshot
    before_valuation_date = request.valuation_date

    price_bli_mvp_standalone_option(request)

    assert request.bond_option is before_bond_option
    assert request.resolved_bond_reference_data is before_reference_data
    assert request.market_data_snapshot is before_snapshot
    assert request.valuation_date == before_valuation_date
