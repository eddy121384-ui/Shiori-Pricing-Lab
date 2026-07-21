"""Tests for the Annex A SS A.2.5 European Black-76 Greeks (Issue #133, Slice A).

No QuantLib is required by any test here except the final optional
cross-check, which uses the repository's existing
``is_quantlib_available()`` skip pattern. Every expected value is either
recomputed independently inside the test with ``statistics.NormalDist``
(a different stdlib code path from the production module's ``math.erf`` /
``math.exp``) or derived by central finite differences of the **existing**
premium function -- never a hand-typed magic number.

The finite-difference Theta check re-derives the discount factor as
``exp(-r_eff * T)`` at each bumped ``T`` while holding ``r_eff`` fixed,
because Theta is ``-dV/dT`` along the flat continuously compounded rate
implied by the effective discount factor, not at a frozen DF.
"""

from __future__ import annotations

from math import exp, log, sqrt
from statistics import NormalDist

import pytest

from shiori_pricing_lab.pricing.bli_black76_price_option import (
    CALENDAR_DAYS_PER_YEAR,
    VOLATILITY_POINT,
    black76_dirty_price_option_greeks_per_100,
    black76_dirty_price_option_pv_per_100,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.products.enums import OptionType

_requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

_NORMAL = NormalDist()

# Near-the-money, plus a clearly ITM and a clearly OTM case, so no test
# passes only because Phi(d1) happens to sit near 0.5.
_BASE_INPUTS = dict(
    forward_dirty_price=100.61141304347827,
    strike_dirty_price=100.69344429347827,
    price_volatility=0.03314,
    time_to_expiry=0.2483295281582953,
    discount_factor=0.987242198040646,
)
_ITM_INPUTS = {**_BASE_INPUTS, "forward_dirty_price": 106.5}
_OTM_INPUTS = {**_BASE_INPUTS, "forward_dirty_price": 94.25}
_ALL_INPUT_SETS = (_BASE_INPUTS, _ITM_INPUTS, _OTM_INPUTS)


def _independent_greeks(inputs: dict, option_type: OptionType) -> dict:
    """Recompute the four Greeks from Annex A SS A.2.5 via a separate stdlib path."""

    forward = inputs["forward_dirty_price"]
    strike = inputs["strike_dirty_price"]
    sigma = inputs["price_volatility"]
    time = inputs["time_to_expiry"]
    discount = inputs["discount_factor"]

    sqrt_t = sqrt(time)
    d1 = (log(forward / strike) + 0.5 * sigma**2 * time) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = _NORMAL.pdf(d1)

    if option_type is OptionType.CALL:
        delta = discount * _NORMAL.cdf(d1)
        pv = discount * (forward * _NORMAL.cdf(d1) - strike * _NORMAL.cdf(d2))
    else:
        delta = -discount * _NORMAL.cdf(-d1)
        pv = discount * (strike * _NORMAL.cdf(-d2) - forward * _NORMAL.cdf(-d1))

    rate = -log(discount) / time
    theta_per_year = -discount * forward * pdf_d1 * sigma / (2.0 * sqrt_t) + rate * pv

    return {
        "delta": delta,
        "gamma": discount * pdf_d1 / (forward * sigma * sqrt_t),
        "vega": discount * forward * pdf_d1 * sqrt_t * 0.01,
        "theta_per_year": theta_per_year,
        "theta_per_day": theta_per_year / 365.0,
        "rate": rate,
    }


def _premium(inputs: dict, option_type: OptionType) -> float:
    return black76_dirty_price_option_pv_per_100(**inputs, option_type=option_type)


def _premium_at_forward(inputs: dict, forward: float, option_type: OptionType) -> float:
    return _premium({**inputs, "forward_dirty_price": forward}, option_type)


# --- 1. Independent analytic reference values, call and put ------------------


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_greeks_match_independently_computed_annex_values(inputs, option_type):
    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=option_type)
    expected = _independent_greeks(inputs, option_type)

    assert greeks.forward_price_delta_per_100 == pytest.approx(expected["delta"], abs=1e-12)
    assert greeks.forward_price_gamma_per_100 == pytest.approx(expected["gamma"], abs=1e-12)
    assert greeks.vega_per_vol_point_per_100 == pytest.approx(expected["vega"], abs=1e-12)
    assert greeks.theta_per_year_per_100 == pytest.approx(expected["theta_per_year"], abs=1e-12)
    assert greeks.theta_per_calendar_day_per_100 == pytest.approx(
        expected["theta_per_day"], abs=1e-14
    )
    assert greeks.theta_effective_continuous_rate == pytest.approx(expected["rate"], abs=1e-14)


# --- 2. Call/put relations --------------------------------------------------


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
def test_call_minus_put_forward_delta_equals_discount_factor(inputs):
    # DF*Phi(d1) - (-DF*Phi(-d1)) = DF*(Phi(d1)+Phi(-d1)) = DF.
    call = black76_dirty_price_option_greeks_per_100(**inputs, option_type=OptionType.CALL)
    put = black76_dirty_price_option_greeks_per_100(**inputs, option_type=OptionType.PUT)

    assert call.forward_price_delta_per_100 - put.forward_price_delta_per_100 == pytest.approx(
        inputs["discount_factor"], abs=1e-12
    )
    assert 0.0 < call.forward_price_delta_per_100 < inputs["discount_factor"]
    assert -inputs["discount_factor"] < put.forward_price_delta_per_100 < 0.0


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
def test_call_and_put_share_gamma_and_vega_on_identical_inputs(inputs):
    call = black76_dirty_price_option_greeks_per_100(**inputs, option_type=OptionType.CALL)
    put = black76_dirty_price_option_greeks_per_100(**inputs, option_type=OptionType.PUT)

    assert call.forward_price_gamma_per_100 == put.forward_price_gamma_per_100
    assert call.vega_per_vol_point_per_100 == put.vega_per_vol_point_per_100
    assert call.forward_price_gamma_per_100 > 0.0
    assert call.vega_per_vol_point_per_100 > 0.0
    # r_eff depends only on DF and T, never on the option type.
    assert call.theta_effective_continuous_rate == put.theta_effective_continuous_rate


# --- 3. Central finite-difference checks against the existing premium -------


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_forward_delta_matches_central_difference_in_the_forward(inputs, option_type):
    # The explicit forward CLEAN price input moves the dirty forward one for
    # one (accrued interest does not depend on it), so bumping the dirty
    # forward by +/-h IS a +/-h clean-price-point bump.
    step = 1e-5
    forward = inputs["forward_dirty_price"]
    up = _premium_at_forward(inputs, forward + step, option_type)
    down = _premium_at_forward(inputs, forward - step, option_type)
    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=option_type)

    assert greeks.forward_price_delta_per_100 == pytest.approx(
        (up - down) / (2.0 * step), abs=1e-8
    )


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_forward_gamma_matches_central_second_difference_in_the_forward(inputs, option_type):
    step = 1e-3
    forward = inputs["forward_dirty_price"]
    base = _premium(inputs, option_type)
    up = _premium_at_forward(inputs, forward + step, option_type)
    down = _premium_at_forward(inputs, forward - step, option_type)
    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=option_type)

    assert greeks.forward_price_gamma_per_100 == pytest.approx(
        (up - 2.0 * base + down) / step**2, abs=1e-6
    )


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_vega_matches_central_difference_in_volatility(inputs, option_type):
    step = 1e-6
    up = _premium({**inputs, "price_volatility": inputs["price_volatility"] + step}, option_type)
    down = _premium({**inputs, "price_volatility": inputs["price_volatility"] - step}, option_type)
    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=option_type)

    per_unit_vol = (up - down) / (2.0 * step)
    assert greeks.vega_per_vol_point_per_100 == pytest.approx(per_unit_vol * 0.01, abs=1e-9)


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_theta_matches_central_difference_in_time_at_fixed_effective_rate(inputs, option_type):
    # Theta = -dV/dT along DF(T) = exp(-r_eff * T) with r_eff held fixed --
    # NOT at a frozen DF, which would drop the + r_eff * PV term entirely and
    # is exactly the defect Annex A SS A.2.5's old minus sign encoded.
    rate = -log(inputs["discount_factor"]) / inputs["time_to_expiry"]
    step = 1e-6

    def premium_at(time: float) -> float:
        return _premium(
            {**inputs, "time_to_expiry": time, "discount_factor": exp(-rate * time)},
            option_type,
        )

    up = premium_at(inputs["time_to_expiry"] + step)
    down = premium_at(inputs["time_to_expiry"] - step)
    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=option_type)

    expected_per_year = -(up - down) / (2.0 * step)
    assert greeks.theta_per_year_per_100 == pytest.approx(expected_per_year, abs=1e-6)
    assert greeks.theta_per_calendar_day_per_100 == pytest.approx(
        expected_per_year / 365.0, abs=1e-8
    )


@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_theta_frozen_discount_factor_difference_would_miss_the_rate_term(inputs, option_type):
    # Proves the + r_eff * PV term is real and material: differencing with the
    # discount factor frozen (the defective reading) differs from the reported
    # annual Theta by exactly r_eff * PV per 100.
    step = 1e-6
    up = _premium({**inputs, "time_to_expiry": inputs["time_to_expiry"] + step}, option_type)
    down = _premium({**inputs, "time_to_expiry": inputs["time_to_expiry"] - step}, option_type)
    frozen_df_theta = -(up - down) / (2.0 * step)

    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=option_type)
    premium = _premium(inputs, option_type)
    rate_term = greeks.theta_effective_continuous_rate * premium

    assert greeks.theta_per_year_per_100 - frozen_df_theta == pytest.approx(rate_term, abs=1e-6)


@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_theta_rate_term_is_material_at_the_money(option_type):
    # The identity above holds even where the rate term is numerically tiny
    # (a deep-OTM premium is near zero). On the at-the-money golden inputs the
    # term is large enough that a wrong sign would be a visible trader-facing
    # error, so pin its materiality here rather than in the identity test.
    greeks = black76_dirty_price_option_greeks_per_100(**_BASE_INPUTS, option_type=option_type)
    rate_term = greeks.theta_effective_continuous_rate * _premium(_BASE_INPUTS, option_type)

    assert abs(rate_term) > 1e-3
    # Flipping the sign (Annex A's former defect) would move annual Theta by
    # twice the rate term -- far outside any rounding tolerance.
    assert abs(2.0 * rate_term) > 1e-3 * abs(greeks.theta_per_year_per_100)


# --- 4. Unit contracts ------------------------------------------------------


def test_one_vol_point_is_exactly_0_01_absolute_volatility():
    # An explicit +0.01 ABSOLUTE volatility bump (0.03314 -> 0.04314), NOT a
    # +1% relative move and NOT a +1.00 absolute vol unit.
    assert VOLATILITY_POINT == 0.01

    inputs = _BASE_INPUTS
    sigma = inputs["price_volatility"]
    half_point = VOLATILITY_POINT / 2.0
    up = _premium({**inputs, "price_volatility": sigma + half_point}, OptionType.CALL)
    down = _premium({**inputs, "price_volatility": sigma - half_point}, OptionType.CALL)
    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=OptionType.CALL)

    # A central difference across exactly one vol point reproduces Vega.
    assert greeks.vega_per_vol_point_per_100 == pytest.approx(up - down, rel=1e-4)
    # And a +1.00 absolute-vol reading would be 100x too large.
    assert greeks.vega_per_vol_point_per_100 == pytest.approx(
        (up - down) / VOLATILITY_POINT * 0.01, rel=1e-4
    )


def test_theta_per_day_is_the_annual_figure_over_365_calendar_days():
    assert CALENDAR_DAYS_PER_YEAR == 365.0
    greeks = black76_dirty_price_option_greeks_per_100(**_BASE_INPUTS, option_type=OptionType.CALL)
    assert greeks.theta_per_calendar_day_per_100 * 365.0 == pytest.approx(
        greeks.theta_per_year_per_100, abs=1e-15
    )


def test_effective_continuous_rate_reproduces_the_discount_factor():
    greeks = black76_dirty_price_option_greeks_per_100(**_BASE_INPUTS, option_type=OptionType.CALL)
    reconstructed = exp(
        -greeks.theta_effective_continuous_rate * _BASE_INPUTS["time_to_expiry"]
    )
    assert reconstructed == pytest.approx(_BASE_INPUTS["discount_factor"], abs=1e-15)


def test_greeks_helper_accepts_no_notional_or_position_argument():
    # These are instrument analytics. Turning them into position risk
    # (notional and the BUY/SELL sign) is the engine's job, published under
    # position_-prefixed names -- this helper must not grow such an argument.
    import inspect

    parameters = inspect.signature(black76_dirty_price_option_greeks_per_100).parameters
    assert set(parameters) == {
        "forward_dirty_price",
        "strike_dirty_price",
        "price_volatility",
        "time_to_expiry",
        "discount_factor",
        "option_type",
    }


# --- 5. Boundary conditions (Annex A SS A.2.4, shared with the premium) -----


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("forward_dirty_price", 0.0),
        ("forward_dirty_price", -1.0),
        ("strike_dirty_price", 0.0),
        ("price_volatility", 0.0),
        ("time_to_expiry", 0.0),
        ("discount_factor", 0.0),
        ("discount_factor", -0.5),
    ),
)
def test_greeks_reject_the_same_boundaries_as_the_premium(field, bad_value):
    with pytest.raises(ValueError):
        black76_dirty_price_option_greeks_per_100(
            **{**_BASE_INPUTS, field: bad_value}, option_type=OptionType.CALL
        )


def test_greeks_reject_an_unknown_option_type():
    with pytest.raises(ValueError):
        black76_dirty_price_option_greeks_per_100(
            **_BASE_INPUTS, option_type="STRADDLE"
        )


# --- 6. Optional QuantLib cross-check ---------------------------------------


@_requires_quantlib
@pytest.mark.parametrize("inputs", _ALL_INPUT_SETS)
@pytest.mark.parametrize("option_type", (OptionType.CALL, OptionType.PUT))
def test_greeks_match_quantlib_black_calculator(inputs, option_type):
    """Independent cross-check against QuantLib's own ``BlackCalculator``.

    ``BlackCalculator`` is constructed from the same forward, total standard
    deviation, and discount factor, so its ``deltaForward``/``gammaForward``/
    ``vega``/``theta``/``thetaPerDay`` are a genuinely separate implementation
    of the same closed forms. Its ``theta(spot, maturity)`` evaluated at
    ``spot = forward`` (zero carry adjustment, which is the standalone path's
    situation) is the ``+ r_eff * PV`` form -- independent confirmation that
    Annex A SS A.2.5's former minus sign was defective -- and its
    ``thetaPerDay`` divides by the same 365 calendar days.
    """

    import QuantLib as ql

    quantlib_option_type = (
        ql.Option.Call if option_type is OptionType.CALL else ql.Option.Put
    )
    calculator = ql.BlackCalculator(
        ql.PlainVanillaPayoff(quantlib_option_type, inputs["strike_dirty_price"]),
        inputs["forward_dirty_price"],
        inputs["price_volatility"] * sqrt(inputs["time_to_expiry"]),
        inputs["discount_factor"],
    )
    greeks = black76_dirty_price_option_greeks_per_100(**inputs, option_type=option_type)

    assert greeks.forward_price_delta_per_100 == pytest.approx(
        calculator.deltaForward(), abs=1e-11
    )
    assert greeks.forward_price_gamma_per_100 == pytest.approx(
        calculator.gammaForward(), abs=1e-12
    )
    assert greeks.vega_per_vol_point_per_100 == pytest.approx(
        calculator.vega(inputs["time_to_expiry"]) * VOLATILITY_POINT, abs=1e-12
    )
    assert greeks.theta_per_year_per_100 == pytest.approx(
        calculator.theta(inputs["forward_dirty_price"], inputs["time_to_expiry"]), abs=1e-11
    )
    assert greeks.theta_per_calendar_day_per_100 == pytest.approx(
        calculator.thetaPerDay(inputs["forward_dirty_price"], inputs["time_to_expiry"]),
        abs=1e-13,
    )
