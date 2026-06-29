"""Tests for the CCS and FX Swap product schemas (Issue #12, second slice).

Schema-construction and validation tests only. No pricing, market data, FX
rates-as-market-data, or valuation date is involved; products are pure deal-term
objects. The near/far FX rates are frozen *trade terms*, not live market data.
"""

import json
from dataclasses import asdict

import pytest

from shiori_pricing_lab.products import (
    BusinessDayConvention,
    BuySell,
    CompoundingMethod,
    CrossCurrencyLeg,
    CrossCurrencySwap,
    Currency,
    DayCount,
    FixedLeg,
    FloatingIndex,
    FloatingLeg,
    Frequency,
    FXSwap,
    PayReceive,
)

# --- CCS builders ------------------------------------------------------------


def _usd_fixed_leg(pay_receive=PayReceive.PAY) -> FixedLeg:
    return FixedLeg(
        pay_receive=pay_receive,
        fixed_rate=0.0375,
        payment_frequency=Frequency.SEMI_ANNUAL,
        day_count=DayCount.THIRTY_360,
    )


def _eur_float_leg(pay_receive=PayReceive.RECEIVE, spread=0.0015) -> FloatingLeg:
    return FloatingLeg(
        pay_receive=pay_receive,
        index=FloatingIndex.EUR_ESTR,
        spread=spread,
        payment_frequency=Frequency.ANNUAL,
        day_count=DayCount.ACT_360,
        compounding_method=CompoundingMethod.DAILY_COMPOUNDED,
    )


def _ccs(**overrides) -> CrossCurrencySwap:
    """Build a synthetic, valid USD/EUR fixed-vs-floating CCS."""

    params = dict(
        product_id="CCS-0001",
        effective_date="2026-07-01",
        maturity_date="2031-07-01",
        leg_1=CrossCurrencyLeg(
            currency=Currency.USD,
            notional=10_000_000.0,
            leg=_usd_fixed_leg(PayReceive.PAY),
        ),
        leg_2=CrossCurrencyLeg(
            currency=Currency.EUR,
            notional=9_200_000.0,
            leg=_eur_float_leg(PayReceive.RECEIVE),
        ),
        initial_exchange=True,
        final_exchange=True,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )
    params.update(overrides)
    return CrossCurrencySwap(**params)


# --- CCS valid construction --------------------------------------------------


def test_valid_fixed_float_ccs():
    ccs = _ccs()

    assert ccs.product_type == "CCS"
    assert ccs.leg_1.currency is Currency.USD
    assert ccs.leg_2.currency is Currency.EUR
    assert ccs.leg_1.notional == 10_000_000.0
    assert ccs.leg_1.pay_receive.opposite() is ccs.leg_2.pay_receive
    assert ccs.initial_exchange is True
    assert ccs.final_exchange is True


def test_valid_float_float_ccs():
    # float/float basis swap: the basis lives on a FloatingLeg.spread, with no
    # separate basis_spread field and no enforcement of which leg carries it.
    ccs = _ccs(
        leg_1=CrossCurrencyLeg(
            currency=Currency.USD,
            notional=10_000_000.0,
            leg=FloatingLeg(
                pay_receive=PayReceive.PAY,
                index=FloatingIndex.USD_SOFR,
                spread=0.0,
                payment_frequency=Frequency.QUARTERLY,
                day_count=DayCount.ACT_360,
                compounding_method=CompoundingMethod.DAILY_COMPOUNDED,
            ),
        ),
        leg_2=CrossCurrencyLeg(
            currency=Currency.EUR,
            notional=9_200_000.0,
            leg=_eur_float_leg(PayReceive.RECEIVE, spread=-0.0008),
        ),
    )

    assert ccs.product_type == "CCS"
    assert ccs.leg_2.leg.spread == -0.0008


def test_valid_fixed_fixed_ccs():
    ccs = _ccs(
        leg_2=CrossCurrencyLeg(
            currency=Currency.EUR,
            notional=9_200_000.0,
            leg=FixedLeg(
                pay_receive=PayReceive.RECEIVE,
                fixed_rate=0.0285,
                payment_frequency=Frequency.ANNUAL,
                day_count=DayCount.ACT_360,
            ),
        ),
    )

    assert ccs.product_type == "CCS"
    assert isinstance(ccs.leg_1.leg, FixedLeg)
    assert isinstance(ccs.leg_2.leg, FixedLeg)


# --- CCS product_type discriminator ------------------------------------------


def test_ccs_product_type_is_not_a_constructor_argument():
    with pytest.raises(TypeError):
        _ccs(product_type="FX_SWAP")


def test_ccs_product_type_round_trips():
    assert asdict(_ccs())["product_type"] == "CCS"


# --- CCS validation rejections -----------------------------------------------


def test_ccs_same_currency_rejected():
    with pytest.raises(ValueError, match="must be in different currencies"):
        _ccs(
            leg_2=CrossCurrencyLeg(
                currency=Currency.USD,  # same as leg_1 -> invalid
                notional=9_200_000.0,
                leg=_eur_float_leg(PayReceive.RECEIVE),
            )
        )


@pytest.mark.parametrize("bad_notional", [0.0, -1.0, -10_000_000.0])
def test_ccs_non_positive_leg_notional_rejected(bad_notional):
    with pytest.raises(ValueError, match="notional must be positive"):
        CrossCurrencyLeg(
            currency=Currency.USD,
            notional=bad_notional,
            leg=_usd_fixed_leg(),
        )


def test_ccs_bad_date_order_rejected():
    with pytest.raises(ValueError, match="must be after effective_date"):
        _ccs(effective_date="2031-07-01", maturity_date="2026-07-01")


def test_ccs_equal_dates_rejected():
    with pytest.raises(ValueError, match="must be after effective_date"):
        _ccs(effective_date="2026-07-01", maturity_date="2026-07-01")


def test_ccs_compact_date_rejected():
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _ccs(effective_date="20260701")


def test_ccs_iso_week_date_rejected():
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _ccs(effective_date="2026-W27-3")


@pytest.mark.parametrize("blank", ["", "   "])
def test_ccs_blank_product_id_rejected(blank):
    with pytest.raises(ValueError, match="product_id must be a non-blank string"):
        _ccs(product_id=blank)


def test_ccs_same_direction_legs_rejected():
    with pytest.raises(ValueError, match="opposite pay/receive directions"):
        _ccs(
            leg_2=CrossCurrencyLeg(
                currency=Currency.EUR,
                notional=9_200_000.0,
                leg=_eur_float_leg(PayReceive.PAY),  # same as leg_1 -> invalid
            )
        )


@pytest.mark.parametrize("bad_flag", [1, 0, "true", None, 1.0])
def test_ccs_initial_exchange_must_be_bool(bad_flag):
    with pytest.raises(TypeError, match="initial_exchange must be a bool"):
        _ccs(initial_exchange=bad_flag)


@pytest.mark.parametrize("bad_flag", [1, 0, "false", None, 0.0])
def test_ccs_final_exchange_must_be_bool(bad_flag):
    with pytest.raises(TypeError, match="final_exchange must be a bool"):
        _ccs(final_exchange=bad_flag)


# --- CCS enum coercion -------------------------------------------------------


def test_ccs_raw_string_enum_coercion():
    ccs = _ccs(
        business_day_convention="FOLLOWING",
        leg_1=CrossCurrencyLeg(
            currency="USD",
            notional=10_000_000.0,
            leg=_usd_fixed_leg(PayReceive.PAY),
        ),
    )

    assert ccs.business_day_convention is BusinessDayConvention.FOLLOWING
    assert ccs.leg_1.currency is Currency.USD


def test_ccs_invalid_currency_string_rejected():
    with pytest.raises(ValueError, match="currency must be one of"):
        CrossCurrencyLeg(currency="ZZZ", notional=1.0, leg=_usd_fixed_leg())


def test_ccs_invalid_business_day_convention_string_rejected():
    with pytest.raises(ValueError, match="business_day_convention must be one of"):
        _ccs(business_day_convention="ROLL_SOMEHOW")


# --- CCS serialization -------------------------------------------------------


def test_ccs_serializes_via_asdict_and_json():
    ccs = _ccs()
    data = asdict(ccs)

    assert data["product_id"] == "CCS-0001"
    assert data["product_type"] == "CCS"
    assert data["leg_1"]["currency"] == "USD"
    assert data["leg_2"]["currency"] == "EUR"
    assert data["leg_1"]["leg"]["pay_receive"] == "PAY"
    assert data["initial_exchange"] is True

    # Round-trips through JSON without a custom encoder.
    assert json.loads(json.dumps(data))["leg_2"]["notional"] == 9_200_000.0


# --- FX Swap builder ---------------------------------------------------------


def _fxswap(**overrides) -> FXSwap:
    """Build a synthetic, valid EUR/USD FX swap (buy EUR near, sell EUR far)."""

    params = dict(
        product_id="FXSWAP-0001",
        base_currency=Currency.EUR,
        quote_currency=Currency.USD,
        near_date="2026-07-01",
        far_date="2026-10-01",
        base_notional=10_000_000.0,
        near_action=BuySell.BUY,
        near_rate=1.0850,
        far_rate=1.0875,
        business_day_convention=BusinessDayConvention.MODIFIED_FOLLOWING,
    )
    params.update(overrides)
    return FXSwap(**params)


# --- FX Swap valid construction ----------------------------------------------


def test_valid_fxswap_construction():
    fx = _fxswap()

    assert fx.product_type == "FX_SWAP"
    assert fx.base_currency is Currency.EUR
    assert fx.quote_currency is Currency.USD
    assert fx.near_action is BuySell.BUY
    assert fx.near_rate == 1.0850
    assert fx.far_rate == 1.0875


def test_fxswap_product_type_is_not_a_constructor_argument():
    with pytest.raises(TypeError):
        _fxswap(product_type="CCS")


def test_fxswap_product_type_round_trips():
    assert asdict(_fxswap())["product_type"] == "FX_SWAP"


# --- FX Swap validation rejections -------------------------------------------


def test_fxswap_same_currency_rejected():
    with pytest.raises(ValueError, match="base_currency and quote_currency must differ"):
        _fxswap(base_currency=Currency.USD, quote_currency=Currency.USD)


def test_fxswap_far_date_must_be_after_near_date():
    with pytest.raises(ValueError, match="must be after near_date"):
        _fxswap(near_date="2026-10-01", far_date="2026-07-01")


def test_fxswap_equal_dates_rejected():
    with pytest.raises(ValueError, match="must be after near_date"):
        _fxswap(near_date="2026-07-01", far_date="2026-07-01")


@pytest.mark.parametrize("bad_notional", [0.0, -1.0, -10_000_000.0])
def test_fxswap_non_positive_base_notional_rejected(bad_notional):
    with pytest.raises(ValueError, match="base_notional must be positive"):
        _fxswap(base_notional=bad_notional)


@pytest.mark.parametrize("bad_rate", [0.0, -0.01, -1.0850])
def test_fxswap_non_positive_near_rate_rejected(bad_rate):
    with pytest.raises(ValueError, match="near_rate must be positive"):
        _fxswap(near_rate=bad_rate)


@pytest.mark.parametrize("bad_rate", [0.0, -0.01, -1.0875])
def test_fxswap_non_positive_far_rate_rejected(bad_rate):
    with pytest.raises(ValueError, match="far_rate must be positive"):
        _fxswap(far_rate=bad_rate)


@pytest.mark.parametrize("blank", ["", "   "])
def test_fxswap_blank_product_id_rejected(blank):
    with pytest.raises(ValueError, match="product_id must be a non-blank string"):
        _fxswap(product_id=blank)


def test_fxswap_compact_date_rejected():
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _fxswap(near_date="20260701")


def test_fxswap_iso_week_date_rejected():
    with pytest.raises(ValueError, match="must be a YYYY-MM-DD date string"):
        _fxswap(far_date="2026-W40-3")


# --- FX Swap enum coercion ---------------------------------------------------


def test_fxswap_buysell_raw_string_coercion():
    fx = _fxswap(near_action="SELL")
    assert fx.near_action is BuySell.SELL


def test_fxswap_invalid_near_action_rejected():
    with pytest.raises(ValueError, match="near_action must be one of"):
        _fxswap(near_action="HOLD")


def test_fxswap_invalid_business_day_convention_string_rejected():
    with pytest.raises(ValueError, match="business_day_convention must be one of"):
        _fxswap(business_day_convention="ROLL_SOMEHOW")


# --- FX Swap serialization ---------------------------------------------------


def test_fxswap_serializes_via_asdict_and_json():
    fx = _fxswap()
    data = asdict(fx)

    assert data["product_id"] == "FXSWAP-0001"
    assert data["product_type"] == "FX_SWAP"
    assert data["base_currency"] == "EUR"
    assert data["quote_currency"] == "USD"
    assert data["near_action"] == "BUY"

    assert json.loads(json.dumps(data))["near_rate"] == 1.0850


def test_fxswap_dates_round_trip_unchanged():
    fx = _fxswap(near_date="2026-07-01", far_date="2026-10-01")
    data = asdict(fx)

    assert data["near_date"] == "2026-07-01"
    assert data["far_date"] == "2026-10-01"


# --- Layering / boundary guard (covers the new modules too) ------------------


def test_products_do_not_import_data_pricing_scenario_or_valuation():
    import importlib
    import sys

    forbidden_prefixes = (
        "shiori_pricing_lab.data",
        "shiori_pricing_lab.pricing",
        "shiori_pricing_lab.valuation",
    )

    for name in list(sys.modules):
        if name.startswith("shiori_pricing_lab.products") or name.startswith(
            forbidden_prefixes
        ):
            del sys.modules[name]

    importlib.import_module("shiori_pricing_lab.products")

    assert not any(name.startswith(forbidden_prefixes) for name in sys.modules)
