"""BLI curve tenor-to-year-fraction parser (docs/27 implementation slice).

Scope, per `docs/27_bli_curve_interpolation_preflight.md` §9: a single
pure tenor-label parser, converting a `BLICurvePoint.tenor`-shaped
string (e.g. `"3M"`, `"2Y"`) into a year fraction. **This module never
reads or interprets `BLICurvePoint.rate` -- it has no notion of
whether a curve's rates are zero rates, par rates, swap rates, bond
yields, funding rates, or anything else (docs/27 §6.1's curve-rate-basis
blocker is untouched by this slice).** No curve-purpose selection, no
interpolation, no discount factor, no par/source-curve conversion, no
forward clean price, no coupon schedule, no accrued interest, no
volatility conversion, no yield-to-price conversion, no Black-76, no
PV, no Greeks, no QuantLib, no Bloomberg/API connector, no FTP
ingestion, and no UI is added here. This module is not imported by, and
does not change the behavior of, `pricing/bli_pricing_engine.py::
price_bli_mvp`.
"""

from __future__ import annotations

import re

_TENOR_SHAPE = re.compile(r"(?P<value>[1-9][0-9]*)(?P<unit>[DMY])")


def tenor_to_year_fraction(tenor: str) -> float:
    """Return the year fraction represented by a strict `<int>D`/`<int>M`/`<int>Y` tenor label.

    Only a positive integer followed by `D` (days), `M` (months), or `Y`
    (years) is accepted, e.g. `"3D"` -> `3 / 365`, `"3M"` -> `0.25`,
    `"2Y"` -> `2.0` -- no whitespace, no lowercase, no zero/negative
    values, no other tenor vocabulary (week tenors, `"O/N"`, bare numbers,
    fractional labels, ...). Raises :class:`ValueError` for any other
    input, including non-string values, with the offending tenor in the
    message.

    The `D` unit (Issue #94) uses the same continuous-zero-curve
    *coordinate* convention as the rest of this parser -- `nD -> n / 365.0`
    -- so an explicit reporting/settlement date expressed as an exact
    day count can be looked up on the same curve as month/year tenors.
    This is a curve coordinate only; it is deliberately **not** the
    option's own ACT/ACT time-to-expiry convention (that lives in
    `bli_valuation_time.py`), and the two must not be conflated.
    """

    if not isinstance(tenor, str):
        raise ValueError(f"tenor must be a string, got {tenor!r}")

    match = _TENOR_SHAPE.fullmatch(tenor)
    if not match:
        raise ValueError(f"unsupported tenor label: {tenor!r}")

    value = int(match.group("value"))
    unit = match.group("unit")
    if unit == "D":
        return value / 365.0
    if unit == "M":
        return value / 12.0
    return float(value)
