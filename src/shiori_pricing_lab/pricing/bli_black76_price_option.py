"""BLI Black-76 clean-price option premium helper (Issue #83, MVP-7 under #82).

Scope: compute the European, price-based bond option premium per 100,
using Black-76 on an already-resolved forward clean price, per Annex A
§A.2.2/§A.2.3 exactly:

```text
d1 = [ln(F / K) + 0.5 * sigma^2 * T] / (sigma * sqrt(T))
d2 = d1 - sigma * sqrt(T)

Call PV per 100 = DF * [F * Phi(d1) - K * Phi(d2)]
Put  PV per 100 = DF * [K * Phi(-d2) - F * Phi(-d1)]
```

where Phi is the standard normal CDF, computed here via `math.erf`
(stdlib, exact, deterministic) -- no `scipy` or other new dependency is
added. Only Phi (CDF) is implemented; the standard normal PDF (phi) is
used solely by Annex A §A.2.5's closed-form Greeks, which are explicitly
out of scope for this slice, so it is not added.

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

**Output is PV per 100 only** -- matching every other helper in this
dependency chain (`forward_clean_price_per_100`, coupon amounts,
discount factors). This function does not accept, and does not apply,
Bond Option Notional (`N`); Annex A §A.2.3's separate
"Option PV = (PV per 100) * N / 100" line is a trivial scaling step left
to a future engine-wiring slice, not implemented here.

**No pricing math beyond this one formula is added here.** No forward
clean price construction, no volatility conversion, no yield-based
option, no Greeks, no American exercise, no physical delivery. This
module is not imported by, and does not change the behavior of,
`pricing/bli_pricing_engine.py::price_bli_mvp`.

**No system clock, and no dates of any kind:** `time_to_expiry` is
received as an already-computed year fraction (``float``) -- this module
never reads an ISO date string, and the current wall-clock date/time is
never read.
"""

from __future__ import annotations

from math import erf, isfinite, log, sqrt

from shiori_pricing_lab.products.enums import OptionType, coerce_enum


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
    no external dependency. Only Phi is implemented; the standard normal
    PDF is not needed by Annex A §A.2.3's PV formula (only by §A.2.5's
    Greeks, out of scope here).
    """

    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


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

    The single numerical core shared by both public wrappers
    (:func:`black76_price_option_pv_per_100`, clean; and
    :func:`black76_dirty_price_option_pv_per_100`, dirty). It carries **no
    price-basis assumption of its own** -- ``forward_price``/``strike_price``
    are whatever basis (clean or dirty) the caller has already resolved,
    and ``forward_field_name``/``strike_field_name`` are used only to build
    the validation error messages so each public wrapper reports its own
    argument names. The d1/d2/Phi formula exists in exactly this one place;
    neither wrapper duplicates it.

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

    if option_type is OptionType.CALL:
        return discount_factor * (
            forward_price * _standard_normal_cdf(d1) - strike_price * _standard_normal_cdf(d2)
        )
    return discount_factor * (
        strike_price * _standard_normal_cdf(-d2) - forward_price * _standard_normal_cdf(-d1)
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
