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
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_dirty_price_option_greeks_per_100,
)
from shiori_pricing_lab.pricing.bli_pricing_engine import (
    price_bli_mvp_standalone_option,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.pricing.result import PricingErrorCode, PricingResult, PricingStatus
from shiori_pricing_lab.products.enums import (
    ExerciseStyle,
    OptionType,
    PayoffBasis,
    Position,
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


# --- 1b. European Greeks wiring (Issue #133, Slice A) ------------------------

_GREEK_PER_100_KEYS = (
    "forward_price_delta_per_100",
    "forward_price_gamma_per_100",
    "vega_per_vol_point_per_100",
    "theta_per_calendar_day_per_100",
)
_GREEK_POSITION_TOTAL_KEYS = (
    "position_forward_price_delta_total",
    "position_forward_price_gamma_total",
    "position_vega_per_vol_point_total",
    "position_theta_per_calendar_day_total",
)


@_requires_quantlib
def test_standalone_success_reports_greeks_from_the_same_resolved_inputs():
    # The engine must not resolve inputs a second time or bump anything: the
    # reported Greeks have to equal the helper called on the SAME dirty
    # forward/strike, sigma, T, and effective DF the premium already used.
    result = price_bli_mvp_standalone_option(_supported_request())
    a = result.assumptions

    expected = black76_dirty_price_option_greeks_per_100(
        forward_dirty_price=a["forward_dirty_price_per_100"],
        strike_dirty_price=a["strike_dirty_price_per_100"],
        price_volatility=a["price_volatility"],
        time_to_expiry=a["time_to_expiry_year_fraction"],
        discount_factor=a["effective_reporting_date_discount_factor"],
        option_type=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.option_type,
    )

    assert a["forward_price_delta_per_100"] == expected.forward_price_delta_per_100
    assert a["forward_price_gamma_per_100"] == expected.forward_price_gamma_per_100
    assert a["vega_per_vol_point_per_100"] == expected.vega_per_vol_point_per_100
    assert a["theta_per_calendar_day_per_100"] == expected.theta_per_calendar_day_per_100
    assert a["theta_per_year_per_100"] == expected.theta_per_year_per_100
    assert a["theta_effective_continuous_rate"] == expected.theta_effective_continuous_rate

    assert a["greeks_methodology"] == "black76_forward_dirty_price_closed_form_european_v1"
    assert a["greeks_scaling_formula"] == (
        "position_total = per_100 * notional / 100 * position_multiplier "
        "(BUY = +1, SELL = -1)"
    )
    assert a["greeks_units"]["vega"] == "premium per 100 per +0.01 absolute volatility"
    assert "+1.00 forward clean price point" in a["greeks_units"]["forward_price_delta"]
    assert "+1 calendar day" in a["greeks_units"]["theta"]
    # The two bases are labelled explicitly, not left to the field names alone.
    assert a["greeks_per_100_position_sign_applied"] is False
    assert a["greeks_position_total_sign_applied"] is True
    assert a["greeks_per_100_basis"] == "instrument_analytics_option_type_direction_only"
    assert a["greeks_position_total_basis"] == (
        "trader_position_risk_notional_and_buy_sell_sign"
    )


def _buy_sell_options():
    base = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option
    return replace(base, position=Position.BUY), replace(base, position=Position.SELL)


@_requires_quantlib
def test_standalone_position_greek_totals_scale_by_notional_and_position_sign():
    notional = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.notional
    buy_option, sell_option = _buy_sell_options()

    buy = price_bli_mvp_standalone_option(_supported_request(bond_option=buy_option)).assumptions
    sell = price_bli_mvp_standalone_option(_supported_request(bond_option=sell_option)).assumptions

    assert buy["position"] == "BUY"
    assert buy["position_multiplier"] == 1.0
    assert sell["position"] == "SELL"
    assert sell["position_multiplier"] == -1.0

    keys = zip(_GREEK_PER_100_KEYS, _GREEK_POSITION_TOTAL_KEYS, strict=True)
    for per_100_key, total_key in keys:
        assert buy[total_key] == pytest.approx(
            buy[per_100_key] * notional / 100.0, rel=1e-15
        )
        assert sell[total_key] == pytest.approx(
            -sell[per_100_key] * notional / 100.0, rel=1e-15
        )


@_requires_quantlib
def test_standalone_greeks_scale_linearly_with_a_doubled_notional():
    base = price_bli_mvp_standalone_option(_supported_request()).assumptions
    doubled_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        notional=SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option.notional * 2.0,
    )
    doubled = price_bli_mvp_standalone_option(
        _supported_request(bond_option=doubled_option)
    ).assumptions

    keys = zip(_GREEK_PER_100_KEYS, _GREEK_POSITION_TOTAL_KEYS, strict=True)
    for per_100_key, total_key in keys:
        # Per-100 Greeks are notional-independent; position totals double.
        assert doubled[per_100_key] == base[per_100_key]
        assert doubled[total_key] == pytest.approx(2.0 * base[total_key], rel=1e-15)


@_requires_quantlib
def test_standalone_per_100_greeks_are_identical_for_buy_and_sell():
    # Per-100 values are INSTRUMENT analytics: the option's own CALL/PUT
    # direction, never the trader's side.
    buy_option, sell_option = _buy_sell_options()
    buy = price_bli_mvp_standalone_option(_supported_request(bond_option=buy_option)).assumptions
    sell = price_bli_mvp_standalone_option(_supported_request(bond_option=sell_option)).assumptions

    for key in _GREEK_PER_100_KEYS:
        assert buy[key] == sell[key]
    assert buy["theta_per_year_per_100"] == sell["theta_per_year_per_100"]
    assert buy["greeks_per_100_position_sign_applied"] is False
    assert sell["greeks_per_100_position_sign_applied"] is False


@_requires_quantlib
def test_standalone_sell_position_greek_totals_are_the_negative_of_buy():
    buy_option, sell_option = _buy_sell_options()
    buy = price_bli_mvp_standalone_option(_supported_request(bond_option=buy_option)).assumptions
    sell = price_bli_mvp_standalone_option(_supported_request(bond_option=sell_option)).assumptions

    for key in _GREEK_POSITION_TOTAL_KEYS:
        assert sell[key] == -buy[key]
        # A real sign flip, not two coincident zeros.
        assert buy[key] != 0.0
    assert buy["greeks_position_total_sign_applied"] is True
    assert sell["greeks_position_total_sign_applied"] is True


@_requires_quantlib
def test_standalone_premium_is_unchanged_by_position_and_stays_unsigned():
    # The premium contract is untouched by the Greek position rule: pv is an
    # unsigned fair-value magnitude and position_sign_applied stays False.
    buy_option, sell_option = _buy_sell_options()
    buy = price_bli_mvp_standalone_option(_supported_request(bond_option=buy_option))
    sell = price_bli_mvp_standalone_option(_supported_request(bond_option=sell_option))

    assert buy.pv == sell.pv
    assert buy.pv == pytest.approx(_EXPECTED_PV)
    assert buy.pv > 0.0
    assert buy.assumptions["black76_pv_per_100"] == sell.assumptions["black76_pv_per_100"]
    assert buy.assumptions["position_sign_applied"] is False
    assert sell.assumptions["position_sign_applied"] is False
    assert sell.assumptions["pv_scaling_formula"] == (
        "pv = black76_pv_per_100 * notional / 100"
    )


@_requires_quantlib
def test_standalone_sell_put_keeps_option_type_and_position_signs_independent():
    # The two sign rules compose without cancelling: a SELL PUT has a
    # negative per-100 delta (PUT) and a POSITIVE position delta total
    # (negative x SELL's -1).
    base = SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option
    sell_put = replace(base, option_type=OptionType.PUT, position=Position.SELL)
    a = price_bli_mvp_standalone_option(_supported_request(bond_option=sell_put)).assumptions

    assert a["forward_price_delta_per_100"] < 0.0
    assert a["position_forward_price_delta_total"] > 0.0
    # Gamma/Vega are positive instrument analytics, negative for a short.
    assert a["forward_price_gamma_per_100"] > 0.0
    assert a["position_forward_price_gamma_total"] < 0.0
    assert a["vega_per_vol_point_per_100"] > 0.0
    assert a["position_vega_per_vol_point_total"] < 0.0


@_requires_quantlib
def test_standalone_call_and_put_greeks_follow_the_option_type_convention():
    call_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option, option_type=OptionType.CALL
    )
    put_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option, option_type=OptionType.PUT
    )
    call = price_bli_mvp_standalone_option(_supported_request(bond_option=call_option)).assumptions
    put = price_bli_mvp_standalone_option(_supported_request(bond_option=put_option)).assumptions

    assert call["forward_price_delta_per_100"] > 0.0
    assert put["forward_price_delta_per_100"] < 0.0
    assert call["forward_price_delta_per_100"] - put["forward_price_delta_per_100"] == (
        pytest.approx(call["effective_reporting_date_discount_factor"], abs=1e-12)
    )
    assert call["forward_price_gamma_per_100"] == put["forward_price_gamma_per_100"]
    assert call["vega_per_vol_point_per_100"] == put["vega_per_vol_point_per_100"]


def test_standalone_failed_result_exposes_no_greek_value():
    # A guard-rejected request carries no assumptions at all, so no Greek key
    # -- and therefore no fabricated 0.0 -- can reach a caller.
    american_option = replace(
        SYNTHETIC_BLI_MVP_INPUT_BUNDLE.product.bond_option,
        exercise_style=ExerciseStyle.AMERICAN,
        exercise_start_date="2026-06-01",
    )
    result = price_bli_mvp_standalone_option(_supported_request(bond_option=american_option))

    assert result.status is PricingStatus.FAILED
    for key in _GREEK_PER_100_KEYS + _GREEK_POSITION_TOTAL_KEYS:
        assert key not in result.assumptions
    assert "greeks_units" not in result.assumptions


@_requires_quantlib
def test_standalone_engine_error_result_exposes_no_greek_value():
    out_of_range_snapshot = replace(
        SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT, forward_clean_price_input=_FORWARD_INPUT
    )
    result = price_bli_mvp_standalone_option(
        _supported_request(market_data_snapshot=out_of_range_snapshot)
    )

    assert result.status is PricingStatus.FAILED
    for key in _GREEK_PER_100_KEYS + _GREEK_POSITION_TOTAL_KEYS:
        assert key not in result.assumptions


@_requires_quantlib
def test_bundle_path_reports_no_greeks():
    # Slice A is the standalone path only; the legacy bundle engine's result
    # contract is untouched.
    from shiori_pricing_lab.pricing.bli_pricing_engine import price_bli_mvp

    result = price_bli_mvp(SYNTHETIC_BLI_MVP_INPUT_BUNDLE)

    for key in _GREEK_PER_100_KEYS + _GREEK_POSITION_TOTAL_KEYS:
        assert key not in result.assumptions


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
