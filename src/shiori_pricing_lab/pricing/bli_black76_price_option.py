"""BLI Black-76 price-based option premium and European Greeks (Issues #83, #133).

Scope: compute the European, price-based bond option premium per 100,
using Black-76 on an already-resolved forward clean price, per Annex A
§A.2.2/§A.2.3 exactly:

```text
d1 = [ln(F / K) + 0.5 * sigma^2 * T] / (sigma * sqrt(T))
d2 = d1 - sigma * sqrt(T)

Call PV per 100 = DF * [F * Phi(d1) - K * Phi(d2)]
Put  PV per 100 = DF * [K * Phi(-d2) - F * Phi(-d1)]
```

where Phi is the standard normal CDF and phi the standard normal PDF,
both computed here from `math.erf` / `math.exp` (stdlib, exact,
deterministic) -- no `scipy` or other new dependency is added.

**European closed-form Greeks (Issue #133, Slice A).**
:func:`black76_dirty_price_option_greeks_per_100` adds the four Annex A
§A.2.5 European Greeks the standalone OVME-aligned path needs, computed
from *exactly* the same five resolved inputs as the premium (dirty
forward, dirty strike, sigma, T, effective reporting-date DF) and from
the same single copy of the d1/d2/Phi/phi math:

```text
Delta_F  = DF * Phi(d1)                     # Call
Delta_F  = -DF * Phi(-d1)                   # Put
Gamma_F  = DF * phi(d1) / (F * sigma * sqrt(T))
Vega     = DF * F * phi(d1) * sqrt(T) * VOLATILITY_POINT
r_eff    = -ln(DF) / T
Theta_yr = -DF * F * phi(d1) * sigma / (2 * sqrt(T)) + r_eff * PV per 100
Theta_d  = Theta_yr / CALENDAR_DAYS_PER_YEAR
```

Delta/Gamma are derivatives with respect to the **explicit forward clean
price input**: the standalone path's dirty forward is that clean input
plus an accrued interest that does not depend on it, so d(F_dirty)/
d(F_clean) = 1 and the dirty-basis derivatives above *are* the
per-clean-price-point sensitivities, with no extra chain-rule factor.

The ``+ r_eff * PV per 100`` sign is deliberate and is the correct sign
for ``Theta = -dV/dT`` at fixed forward: differentiating
``V = DF(T) * [F Phi(d1) - K Phi(d2)]`` with ``DF = exp(-r T)`` gives
``dV/dT = -r V + DF F phi(d1) sigma / (2 sqrt(T))``, hence
``Theta = r V - DF F phi(d1) sigma / (2 sqrt(T))``. Annex A §A.2.5
carried a defective ``-r`` on that line; it is corrected in the same PR
as this implementation. The identity ``F phi(d1) = K phi(d2)`` makes the
same first term correct for a put, and ``PV per 100`` is the option's
own premium, so one expression covers Call and Put.

**Still out of scope here:** DV01, CS01, yield/spot delta, curve or
spread bumps, scenarios, American/yield-based/physical options, and any
notional or BUY/SELL scaling (see below).

**Two public wrappers, one formula (Issue #94):** the clean-basis
``black76_price_option_pv_per_100`` (unchanged signature/behavior, used
by the legacy bundle path) and the dirty-basis
``black76_dirty_price_option_pv_per_100`` (the OVME-aligned standalone
path prices Black-76 on dirty forward/strike) both delegate to the
single private, price-basis-neutral ``_black76_option_pv_per_100_core``.
The d1/d2/Phi formula and its input validation exist in exactly one
place; no dirty value is ever passed through an argument whose name
claims it is clean.

**Zero composition, by design (unlike `bli_forward_clean_price.py`):**
this module does not import `bli_forward_clean_price`,
`bli_curve_discount_factor`, `bli_curve_selector`, `bli_valuation_time`,
`bli_quantlib_bond_adapter`, `BLIMVPInputBundle`, or any curve/market-data
type. Every one of F, K, sigma, T, DF is received as a plain,
already-resolved ``float`` -- this helper has no notion of dates, curves,
or bundles at all. Assembling those five numbers from a
`BLIMVPInputBundle` (forward clean price, strike, volatility, time to
expiry, and Option Discount Curve discount factor) is explicitly #44's
job (engine wiring), not this helper's.

**Output is per 100 only** -- matching every other helper in this
dependency chain (`forward_clean_price_per_100`, coupon amounts,
discount factors). Neither the premium nor the Greeks helper accepts or
applies Bond Option Notional (`N`) or the BUY/SELL `Position`. Annex A
§A.2.3's separate "Option PV = (PV per 100) * N / 100" line is a trivial
scaling step done by the engine, which keeps the premium an unsigned
fair-value magnitude (`position_sign_applied = False`) and publishes
BUY/SELL-signed Greek risk only under explicitly `position_`-prefixed
names.

**No pricing math beyond these formulas is added here.** No forward
clean price construction, no volatility conversion, no yield-based
option, no bump-and-revalue risk, no American exercise, no physical
delivery. This module is not imported by, and does not change the
behavior of, `pricing/bli_pricing_engine.py::price_bli_mvp`.

**No system clock, and no dates of any kind:** `time_to_expiry` is
received as an already-computed year fraction (``float``) -- this module
never reads an ISO date string, and the current wall-clock date/time is
never read.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, isfinite, log, pi, sqrt

from shiori_pricing_lab.products.enums import OptionType, coerce_enum

# One absolute volatility point. Vega is reported per +0.01 *absolute*
# volatility (3.314% -> 4.314% is one point), never per +1% relative move
# and never per 1.00 absolute vol.
VOLATILITY_POINT = 0.01

# Theta is reported per calendar day, so the annual figure is divided by
# 365 calendar days -- not by a business-day count and not by the option's
# own ACT/ACT year-fraction denominator.
CALENDAR_DAYS_PER_YEAR = 365.0


def _require_finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _require_positive(value: float, field_name: str) -> float:
    if not value > 0:
        raise ValueError(
            f"{field_name} must be positive, got {value!r} -- pricing is blocked rather "
            "than producing a fabricated option value"
        )
    return value


def _standard_normal_cdf(x: float) -> float:
    """Return Phi(x), the standard normal CDF, via `math.erf`.

    ``Phi(x) = 0.5 * (1 + erf(x / sqrt(2)))`` -- exact and deterministic,
    no external dependency.
    """

    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _standard_normal_pdf(x: float) -> float:
    """Return phi(x), the standard normal PDF, via `math.exp`.

    ``phi(x) = exp(-x^2 / 2) / sqrt(2 pi)`` -- exact and deterministic,
    no external dependency. Used only by Annex A §A.2.5's closed-form
    Gamma / Vega / Theta.
    """

    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


@dataclass(frozen=True)
class _Black76Inputs:
    """The five validated Black-76 inputs plus the derived d1/d2 and sqrt(T).

    Internal only: the single place where Annex A §A.2.4's boundary
    conditions are enforced and where d1/d2 are computed, so the premium
    and the Greeks can never drift apart numerically.
    """

    forward_price: float
    strike_price: float
    price_volatility: float
    time_to_expiry: float
    discount_factor: float
    option_type: OptionType
    sqrt_time_to_expiry: float
    d1: float
    d2: float


def _validated_black76_inputs(
    *,
    forward_price: float,
    strike_price: float,
    forward_field_name: str,
    strike_field_name: str,
    price_volatility: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: OptionType | str,
) -> _Black76Inputs:
    """Validate the five inputs and return them with d1/d2 (one copy of the math).

    Price-basis-neutral: ``forward_price``/``strike_price`` are whatever
    basis (clean or dirty) the caller has already resolved, and
    ``forward_field_name``/``strike_field_name`` are used only to build the
    validation error messages so each public wrapper reports its own
    argument names.

    Per Annex A §A.2.4, F > 0, K > 0, sigma > 0, and T > 0 are all
    required; ``discount_factor`` is validated the same way (finite,
    strictly positive) as a defensive fail-fast measure. Any violation
    raises :class:`ValueError` rather than returning a fabricated number.
    """

    option_type = coerce_enum(option_type, OptionType, "option_type")

    forward_price = _require_positive(
        _require_finite_number(forward_price, forward_field_name), forward_field_name
    )
    strike_price = _require_positive(
        _require_finite_number(strike_price, strike_field_name), strike_field_name
    )
    price_volatility = _require_positive(
        _require_finite_number(price_volatility, "price_volatility"), "price_volatility"
    )
    time_to_expiry = _require_positive(
        _require_finite_number(time_to_expiry, "time_to_expiry"), "time_to_expiry"
    )
    discount_factor = _require_positive(
        _require_finite_number(discount_factor, "discount_factor"), "discount_factor"
    )

    sqrt_t = sqrt(time_to_expiry)
    d1 = (
        log(forward_price / strike_price) + 0.5 * price_volatility**2 * time_to_expiry
    ) / (price_volatility * sqrt_t)
    d2 = d1 - price_volatility * sqrt_t

    return _Black76Inputs(
        forward_price=forward_price,
        strike_price=strike_price,
        price_volatility=price_volatility,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        option_type=option_type,
        sqrt_time_to_expiry=sqrt_t,
        d1=d1,
        d2=d2,
    )


def _pv_per_100_from_inputs(inputs: _Black76Inputs) -> float:
    """Annex A §A.2.3's Call/Put PV per 100 from already-validated inputs."""

    if inputs.option_type is OptionType.CALL:
        return inputs.discount_factor * (
            inputs.forward_price * _standard_normal_cdf(inputs.d1)
            - inputs.strike_price * _standard_normal_cdf(inputs.d2)
        )
    return inputs.discount_factor * (
        inputs.strike_price * _standard_normal_cdf(-inputs.d2)
        - inputs.forward_price * _standard_normal_cdf(-inputs.d1)
    )


def _black76_option_pv_per_100_core(
    *,
    forward_price: float,
    strike_price: float,
    forward_field_name: str,
    strike_field_name: str,
    price_volatility: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: OptionType | str,
) -> float:
    """Price-basis-neutral Black-76 European option PV per 100.

    The single numerical core shared by both public premium wrappers
    (:func:`black76_price_option_pv_per_100`, clean; and
    :func:`black76_dirty_price_option_pv_per_100`, dirty), and -- through
    :func:`_validated_black76_inputs` / :func:`_pv_per_100_from_inputs` --
    by :func:`black76_dirty_price_option_greeks_per_100`. The d1/d2/Phi
    formula exists in exactly one place; no caller duplicates it.
    """

    return _pv_per_100_from_inputs(
        _validated_black76_inputs(
            forward_price=forward_price,
            strike_price=strike_price,
            forward_field_name=forward_field_name,
            strike_field_name=strike_field_name,
            price_volatility=price_volatility,
            time_to_expiry=time_to_expiry,
            discount_factor=discount_factor,
            option_type=option_type,
        )
    )


def black76_price_option_pv_per_100(
    *,
    forward_clean_price: float,
    strike_clean_price: float,
    price_volatility: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: OptionType | str,
) -> float:
    """Return the Black-76 European price-based bond option PV per 100 (clean basis).

    ``forward_clean_price`` (F), ``strike_clean_price`` (K),
    ``price_volatility`` (sigma), ``time_to_expiry`` (T, a year fraction),
    and ``discount_factor`` (DF, from the Option Discount Curve -- never
    the Bond Reference Curve) are all plain, already-resolved floats; this
    function performs no date, curve, or market-data lookup of any kind.
    ``option_type`` is coerced via the existing
    `products.enums.OptionType` vocabulary (``CALL``/``PUT``).

    Per Annex A §A.2.4, F > 0, K > 0, sigma > 0, and T > 0 are all
    required -- any violation raises `ValueError` ("pricing blocked")
    rather than returning a fabricated number. ``discount_factor`` is not
    explicitly listed in §A.2.4's boundary set, but is validated the same
    way (finite, strictly positive) as a defensive fail-fast measure,
    consistent with the discount-factor validation already reviewed in
    `bli_forward_clean_price.py` (#42) -- a zero, negative, NaN, or
    infinite discount factor never silently produces an option value.

    Unchanged public signature and behavior (Issue #94): this wrapper
    delegates to the shared :func:`_black76_option_pv_per_100_core`, which
    carries the one copy of the d1/d2/Phi formula. Returns PV **per 100**
    only -- no Bond Option Notional scaling is accepted or applied.
    """

    return _black76_option_pv_per_100_core(
        forward_price=forward_clean_price,
        strike_price=strike_clean_price,
        forward_field_name="forward_clean_price",
        strike_field_name="strike_clean_price",
        price_volatility=price_volatility,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        option_type=option_type,
    )


def black76_dirty_price_option_pv_per_100(
    *,
    forward_dirty_price: float,
    strike_dirty_price: float,
    price_volatility: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: OptionType | str,
) -> float:
    """Return the Black-76 European bond option PV per 100 on a dirty-price basis.

    The OVME-aligned standalone path (Issue #94 human methodology approval,
    comment 5001749998) prices Black-76 on **dirty** forward and strike
    (clean values plus accrued interest at the bond forward settlement
    date), not clean. This wrapper takes ``forward_dirty_price`` (F) and
    ``strike_dirty_price`` (K) already on the dirty basis and delegates to
    the same shared :func:`_black76_option_pv_per_100_core` the clean
    wrapper uses -- the d1/d2/Phi formula is never duplicated, and the
    dirty basis is carried honestly in this function's own argument names
    rather than passed through a ``*_clean_price`` argument whose contract
    claims it is clean.

    ``price_volatility`` (sigma), ``time_to_expiry`` (T), and
    ``discount_factor`` (DF, the effective Option-Discount-Curve reporting
    factor) are plain floats; the same F > 0, K > 0, sigma > 0, T > 0, and
    finite/positive DF boundaries apply, each violation raising
    :class:`ValueError`. Returns PV **per 100** only.
    """

    return _black76_option_pv_per_100_core(
        forward_price=forward_dirty_price,
        strike_price=strike_dirty_price,
        forward_field_name="forward_dirty_price",
        strike_field_name="strike_dirty_price",
        price_volatility=price_volatility,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        option_type=option_type,
    )


@dataclass(frozen=True)
class Black76EuropeanGreeksPer100:
    """The four Annex A §A.2.5 European Greeks, all on a per-100 premium basis.

    Every field is a sensitivity of the **premium per 100** -- the same
    quantity :func:`black76_dirty_price_option_pv_per_100` returns.

    These are **instrument analytics**: they carry the CALL/PUT direction
    (Delta is positive for a call, negative for a put) but no Bond Option
    Notional and no BUY/SELL ``Position`` sign. Turning them into
    position risk -- multiplying by ``notional / 100`` and by ``+1`` for
    BUY / ``-1`` for SELL -- is the engine's job, which publishes those
    figures under explicitly ``position_``-prefixed names so the two
    bases can never be mistaken for each other.

    Units and signs:

    ``forward_price_delta_per_100``
        Change in premium per 100 per **+1.00 clean price point** in the
        explicit forward clean price input. Positive for a call
        (``DF * Phi(d1)``), negative for a put (``-DF * Phi(-d1)``);
        magnitude is bounded by ``DF``.
    ``forward_price_gamma_per_100``
        Change in ``forward_price_delta_per_100`` per **+1.00 clean price
        point**, i.e. the second derivative per price point squared.
        Identical for a call and a put on identical inputs, and always
        positive.
    ``vega_per_vol_point_per_100``
        Change in premium per 100 per **+0.01 absolute volatility**
        (:data:`VOLATILITY_POINT`). Identical for a call and a put on
        identical inputs, and always positive.
    ``theta_per_calendar_day_per_100``
        Change in premium per 100 per **+1 calendar day** of elapsed time
        at a fixed forward, i.e. ``-dV/dT`` divided by
        :data:`CALENDAR_DAYS_PER_YEAR`.
    ``theta_per_year_per_100``
        The same Theta before the per-day division, kept so the reported
        per-day figure's annual basis is explicit rather than implied.
    ``theta_effective_continuous_rate``
        ``r_eff = -ln(DF) / T``, the flat continuously compounded rate
        implied by the effective reporting-date discount factor actually
        used for the premium. It is derived from that DF alone -- no
        curve is re-read and no separate rate input is introduced.
    """

    forward_price_delta_per_100: float
    forward_price_gamma_per_100: float
    vega_per_vol_point_per_100: float
    theta_per_calendar_day_per_100: float
    theta_per_year_per_100: float
    theta_effective_continuous_rate: float


def black76_dirty_price_option_greeks_per_100(
    *,
    forward_dirty_price: float,
    strike_dirty_price: float,
    price_volatility: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: OptionType | str,
) -> Black76EuropeanGreeksPer100:
    """Return the Annex A §A.2.5 European Greeks per 100 for the dirty-price basis.

    Takes **exactly** the same five already-resolved inputs as
    :func:`black76_dirty_price_option_pv_per_100` -- dirty forward, dirty
    strike, sigma, T, and the effective reporting-date DF -- and shares its
    validation and d1/d2 computation with the premium via
    :func:`_validated_black76_inputs`, so a Greek can never be computed
    from a different d1 than the premium it accompanies. The same Annex A
    §A.2.4 boundaries apply; each violation raises :class:`ValueError`
    rather than returning a fabricated number.

    Delta and Gamma are sensitivities to the **explicit forward clean price
    input**, not to the dirty forward as a separate quantity: the accrued
    interest added to reach ``forward_dirty_price`` does not depend on the
    clean forward, so ``d(F_dirty)/d(F_clean) = 1`` and the dirty-basis
    derivatives below need no extra chain-rule factor. Vega is scaled to
    ``+0.01`` absolute volatility and Theta to one calendar day; see
    :class:`Black76EuropeanGreeksPer100` for every unit and sign.

    Notional and BUY/SELL ``Position`` are neither accepted nor applied:
    these are instrument analytics, and the engine derives position risk
    from them under ``position_``-prefixed names.
    """

    inputs = _validated_black76_inputs(
        forward_price=forward_dirty_price,
        strike_price=strike_dirty_price,
        forward_field_name="forward_dirty_price",
        strike_field_name="strike_dirty_price",
        price_volatility=price_volatility,
        time_to_expiry=time_to_expiry,
        discount_factor=discount_factor,
        option_type=option_type,
    )

    forward = inputs.forward_price
    sigma = inputs.price_volatility
    sqrt_t = inputs.sqrt_time_to_expiry
    discount = inputs.discount_factor
    pdf_d1 = _standard_normal_pdf(inputs.d1)

    if inputs.option_type is OptionType.CALL:
        delta = discount * _standard_normal_cdf(inputs.d1)
    else:
        delta = -discount * _standard_normal_cdf(-inputs.d1)

    gamma = discount * pdf_d1 / (forward * sigma * sqrt_t)
    vega = discount * forward * pdf_d1 * sqrt_t * VOLATILITY_POINT

    pv_per_100 = _pv_per_100_from_inputs(inputs)
    effective_rate = -log(discount) / inputs.time_to_expiry
    theta_per_year = (
        -discount * forward * pdf_d1 * sigma / (2.0 * sqrt_t) + effective_rate * pv_per_100
    )

    return Black76EuropeanGreeksPer100(
        forward_price_delta_per_100=delta,
        forward_price_gamma_per_100=gamma,
        vega_per_vol_point_per_100=vega,
        theta_per_calendar_day_per_100=theta_per_year / CALENDAR_DAYS_PER_YEAR,
        theta_per_year_per_100=theta_per_year,
        theta_effective_continuous_rate=effective_rate,
    )
