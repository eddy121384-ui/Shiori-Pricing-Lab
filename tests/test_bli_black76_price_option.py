"""Tests for `pricing/bli_black76_price_option.py` (Issue #83, MVP-7 under #82).

No QuantLib, no curve, no bundle, no date arithmetic anywhere in this
file -- every test uses plain floats, matching the module's own zero-
composition design. Expected call/put values are computed independently
inside each test using `statistics.NormalDist().cdf` -- a different
stdlib code path from the production module's own `math.erf`-based
`_standard_normal_cdf` -- never a hand-typed magic number.
"""

from __future__ import annotations

import inspect
from math import log, sqrt
from statistics import NormalDist

import pytest

from shiori_pricing_lab.pricing import bli_black76_price_option as module
from shiori_pricing_lab.pricing.bli_black76_price_option import (
    black76_price_option_pv_per_100,
)
from shiori_pricing_lab.products.enums import OptionType

_NORMAL_CDF = NormalDist().cdf


def _independent_call_put(
    *, forward_clean_price, strike_clean_price, price_volatility, time_to_expiry, discount_factor
):
    """Recompute Annex A SS A.2.3's Call/Put PV using a separate stdlib CDF path."""

    sqrt_t = sqrt(time_to_expiry)
    d1 = (
        log(forward_clean_price / strike_clean_price)
        + 0.5 * price_volatility**2 * time_to_expiry
    ) / (price_volatility * sqrt_t)
    d2 = d1 - price_volatility * sqrt_t

    call = discount_factor * (
        forward_clean_price * _NORMAL_CDF(d1) - strike_clean_price * _NORMAL_CDF(d2)
    )
    put = discount_factor * (
        strike_clean_price * _NORMAL_CDF(-d2) - forward_clean_price * _NORMAL_CDF(-d1)
    )
    return call, put


_SAMPLE_INPUTS = dict(
    forward_clean_price=101.25,
    strike_clean_price=99.5,
    price_volatility=0.06,
    time_to_expiry=0.25,
    discount_factor=0.98,
)


# --- 1/2. Call/put premium vs. independently-computed expected value --------


def test_call_premium_matches_independent_normaldist_computation():
    expected_call, _ = _independent_call_put(**_SAMPLE_INPUTS)
    result = black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type=OptionType.CALL)
    assert result == pytest.approx(expected_call)


def test_put_premium_matches_independent_normaldist_computation():
    _, expected_put = _independent_call_put(**_SAMPLE_INPUTS)
    result = black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type=OptionType.PUT)
    assert result == pytest.approx(expected_put)


# --- 3. Put-call parity -------------------------------------------------------


def test_put_call_parity():
    call = black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type=OptionType.CALL)
    put = black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type=OptionType.PUT)
    expected_difference = _SAMPLE_INPUTS["discount_factor"] * (
        _SAMPLE_INPUTS["forward_clean_price"] - _SAMPLE_INPUTS["strike_clean_price"]
    )
    assert call - put == pytest.approx(expected_difference)


@pytest.mark.parametrize(
    "forward_clean_price,strike_clean_price",
    [(95.0, 105.0), (100.0, 100.0), (110.0, 90.0)],
)
def test_put_call_parity_holds_across_moneyness(forward_clean_price, strike_clean_price):
    kwargs = {
        **_SAMPLE_INPUTS,
        "forward_clean_price": forward_clean_price,
        "strike_clean_price": strike_clean_price,
    }
    call = black76_price_option_pv_per_100(**kwargs, option_type=OptionType.CALL)
    put = black76_price_option_pv_per_100(**kwargs, option_type=OptionType.PUT)
    expected_difference = kwargs["discount_factor"] * (forward_clean_price - strike_clean_price)
    assert call - put == pytest.approx(expected_difference)


# --- 4. Call/put direction correctness ---------------------------------------


def test_call_premium_increases_and_put_premium_decreases_as_forward_rises():
    lower = {**_SAMPLE_INPUTS, "forward_clean_price": 99.0}
    higher = {**_SAMPLE_INPUTS, "forward_clean_price": 103.0}

    call_lower = black76_price_option_pv_per_100(**lower, option_type=OptionType.CALL)
    call_higher = black76_price_option_pv_per_100(**higher, option_type=OptionType.CALL)
    assert call_higher > call_lower

    put_lower = black76_price_option_pv_per_100(**lower, option_type=OptionType.PUT)
    put_higher = black76_price_option_pv_per_100(**higher, option_type=OptionType.PUT)
    assert put_higher < put_lower


# --- 5. Invalid F/K/sigma/T/DF fail explicitly -------------------------------


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("forward_clean_price", 0.0),
        ("forward_clean_price", -1.0),
        ("forward_clean_price", float("nan")),
        ("strike_clean_price", 0.0),
        ("strike_clean_price", -1.0),
        ("price_volatility", 0.0),
        ("price_volatility", -0.01),
        ("time_to_expiry", 0.0),
        ("time_to_expiry", -0.5),
        ("discount_factor", 0.0),
        ("discount_factor", -0.5),
        ("discount_factor", float("nan")),
        ("discount_factor", float("inf")),
    ],
)
def test_invalid_component_fails_explicitly(field_name, bad_value):
    kwargs = {**_SAMPLE_INPUTS, field_name: bad_value}
    with pytest.raises(ValueError, match=field_name):
        black76_price_option_pv_per_100(**kwargs, option_type=OptionType.CALL)


# --- 6. Invalid option_type ----------------------------------------------------


def test_invalid_option_type_fails_explicitly():
    with pytest.raises(ValueError, match="option_type"):
        black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type="NOT_A_REAL_TYPE")


def test_option_type_accepts_raw_string():
    result = black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type="CALL")
    expected = black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type=OptionType.CALL)
    assert result == pytest.approx(expected)


# --- 7. No system date --------------------------------------------------------


def test_module_reads_no_system_clock_or_dates():
    source = inspect.getsource(module)
    assert "date.today()" not in source
    assert "datetime.now()" not in source
    assert "utcnow()" not in source
    assert "import date" not in source
    assert "fromisoformat" not in source


# --- 8. No forward clean price construction -----------------------------------


def test_module_defines_no_forward_price_or_bond_mechanics_logic():
    module_names = set(dir(module))
    forbidden_names = {
        "forward_clean_price_per_100",
        "forward_clean_price_per_100_for_bundle",
        "coupon_flows_before",
        "accrued_interest_per_100",
        "discount_factor_from_continuous_zero_curve",
        "year_fraction_to_expiry",
        "BLIMVPInputBundle",
        "BLICurvePoint",
    }
    assert module_names.isdisjoint(forbidden_names)


# --- 9. No engine wiring -------------------------------------------------------


def test_module_defines_no_engine_wiring():
    module_names = set(dir(module))
    forbidden_names = {
        "price_bli_mvp",
        "PricingResult",
        "PricingEngineRegistry",
        "register_engine",
    }
    assert module_names.isdisjoint(forbidden_names)


# --- 10. No notional scaling ---------------------------------------------------


def test_signature_has_no_notional_parameter():
    parameters = set(inspect.signature(black76_price_option_pv_per_100).parameters)
    assert "notional" not in parameters
    assert "N" not in parameters


def test_at_the_money_pv_per_100_is_a_small_number_not_notional_scaled():
    # A sanity bound proving the output is a per-100 premium, not scaled
    # by any external notional -- for these sample inputs, PV per 100
    # must be comfortably below 100.
    result = black76_price_option_pv_per_100(**_SAMPLE_INPUTS, option_type=OptionType.CALL)
    assert 0.0 < result < 100.0
