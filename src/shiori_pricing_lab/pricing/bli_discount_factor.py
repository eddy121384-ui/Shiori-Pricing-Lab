"""BLI continuous-compounding discount factor (docs/28 dependency chain).

Scope: a single tiny, deterministic helper -- given an already-resolved
continuously-compounded zero rate and a positive year fraction, return
the discount factor `exp(-zero_rate * year_fraction)` (Annex A
§A.10.2's continuous-compounding convention). This module does not
interpolate, does not select curve points, does not compute a forward
clean price, and does not compute a PV -- it consumes only the two
plain numbers a caller already resolved upstream (e.g. via
`pricing/bli_zero_rate_interpolation.py::interpolate_continuous_zero_rate`
and `pricing/bli_valuation_time.py::year_fraction_to_expiry`), before a
caller ever reaches this helper.

This module is not imported by, and does not change the behavior of,
`pricing/bli_pricing_engine.py::price_bli_mvp`.
"""

from __future__ import annotations

from math import exp, isfinite


def continuous_discount_factor(zero_rate: float, year_fraction: float) -> float:
    """Return `exp(-zero_rate * year_fraction)`.

    Raises :class:`ValueError` if ``zero_rate`` is not finite, if
    ``year_fraction`` is not finite, or if ``year_fraction`` is not
    strictly positive. Signed zero rates are allowed; no sign
    constraint is imposed on ``zero_rate``.
    """

    if not isfinite(zero_rate):
        raise ValueError(f"zero_rate must be a finite number, got {zero_rate!r}")
    if not isfinite(year_fraction) or year_fraction <= 0:
        raise ValueError(
            f"year_fraction must be a finite, positive number, got {year_fraction!r}"
        )

    return exp(-zero_rate * year_fraction)
