"""BLI curve-point selector by (currency, curve_purpose) (docs/27 implementation slice).

Scope, per `docs/27_bli_curve_interpolation_preflight.md` §4/§9.2: a
single pure structural filter, narrowing a collection of `BLICurvePoint`
rows down to the ones matching a requested `(currency, curve_purpose)`
pair. **This module does not parse tenor, does not call
`bli_curve_tenor.tenor_to_year_fraction`, and does not read or interpret
any curve point's rate value at all -- it has no notion of whether a
curve's rates are zero rates, par rates, swap rates, bond yields, or
funding rates, and its output must not be read as implying otherwise
(docs/27 §6.1's curve-rate-basis blocker is entirely untouched by this
slice).**
No interpolation, no discount factor, no par/source-curve conversion,
and no forward-price logic is added here. This module is not imported
by, and does not change the behavior of, `pricing/bli_pricing_engine.py
::price_bli_mvp`.
"""

from __future__ import annotations

from collections.abc import Iterable

from shiori_pricing_lab.data.bli_snapshot import BLICurvePoint, BLICurvePurpose
from shiori_pricing_lab.products.enums import Currency, coerce_enum


def select_curve_points_by_purpose(
    curve_points: Iterable[BLICurvePoint],
    *,
    currency: Currency | str,
    curve_purpose: BLICurvePurpose | str,
) -> tuple[BLICurvePoint, ...]:
    """Return the ``curve_points`` rows matching ``currency`` and ``curve_purpose``.

    Purely structural: filters on ``point.currency == currency`` and
    ``point.curve_purpose == curve_purpose`` only, preserving input
    order. Raises :class:`TypeError` if ``curve_points`` contains a
    non-``BLICurvePoint`` element, and :class:`ValueError` if
    ``curve_points`` is empty or if no row matches the requested pair --
    never silently falls back to a different currency or curve purpose.
    Does not sort or dedupe by tenor, does not parse tenor, and does not
    read any curve point's rate value.
    """

    currency = coerce_enum(currency, Currency, "currency")
    curve_purpose = coerce_enum(curve_purpose, BLICurvePurpose, "curve_purpose")

    points = tuple(curve_points)
    if not points:
        raise ValueError("curve_points must not be empty")

    for point in points:
        if not isinstance(point, BLICurvePoint):
            raise TypeError(
                "curve_points must contain only BLICurvePoint instances, got "
                f"{type(point).__name__}"
            )

    selected = tuple(
        point
        for point in points
        if point.currency == currency and point.curve_purpose == curve_purpose
    )
    if not selected:
        raise ValueError(
            "no curve_points match currency="
            f"{currency.value!r} curve_purpose={curve_purpose.value!r}"
        )
    return selected
