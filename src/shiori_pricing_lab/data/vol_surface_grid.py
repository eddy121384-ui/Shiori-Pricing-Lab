"""Reshape one stored ATM surface into the Expiry x Swap Tenor grid it was
read from (Issue #194).

The VCUB ATM screen *is* a matrix: rows are option expiries, columns are
swap tenors. The canonical store keeps that matrix as a flat, ordered tuple
of points, because a point -- not a cell -- is what carries a coordinate and
a provenance. Anything that wants to show the screen back to a trader has to
put the rows and columns back, and this module is the one place that does
it, so the numeric table and the 3D surface in the Markets view are the same
reshape of the same stored points rather than two independent readings.

**Nothing here computes a volatility.** The reshape is a permutation: every
value in :attr:`VolSurfaceGrid.rows` is a value stored on a point, ``None``
included, and no cell is ever filled in, smoothed, or interpolated. A grid
with a hole in it is refused (:class:`VolSurfaceGridError`) rather than
completed -- Issue #194 requires a malformed or incomplete stored surface to
fail visibly instead of being silently repaired.

``None`` is not a hole. A point whose ``volatility`` is ``None`` is a cell
the capture could not read and the trader confirmed as unread; it is part of
the surface and it is kept, exactly as the store holds it. A *hole* is a
coordinate that has no point at all -- an (expiry, tenor) intersection the
stored surface simply does not mention -- and that is what makes the grid
non-rectangular and unshowable.
"""

from __future__ import annotations

from dataclasses import dataclass

from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfaceType,
    VolValueKind,
)


class VolSurfaceGridError(ValueError):
    """A stored surface cannot be shown as an Expiry x Swap Tenor matrix.

    Raised rather than returning a partial grid: a table or a chart drawn
    from a surface this refuses would be showing a shape the store never
    held.
    """


@dataclass(frozen=True)
class VolSurfaceGrid:
    """One stored ATM surface, rectangular again.

    :attr:`expiries` and :attr:`underlying_tenors` are in **stored order** --
    the order the points came back in, which is the order the capture read
    them off the screen -- never re-sorted. Sorting would mean parsing
    ``"18Mo"`` and ``"1Yr"`` into durations to compare them, which is an
    interpretation of vendor labels this layer is not entitled to make, and
    it would silently reorder the matrix a trader confirmed against the
    screen.

    ``rows[i][j]`` is the volatility stored at
    ``(expiries[i], underlying_tenors[j])``, or ``None`` where the capture
    left that intersection unresolved.
    """

    expiries: tuple[str, ...]
    underlying_tenors: tuple[str, ...]
    rows: tuple[tuple[float | None, ...], ...]

    def to_dict(self) -> dict:
        return {
            "expiries": list(self.expiries),
            "underlying_tenors": list(self.underlying_tenors),
            "rows": [list(row) for row in self.rows],
        }


def atm_grid_from_surface(surface: CanonicalVolSurface) -> VolSurfaceGrid:
    """Return ``surface``'s points as an Expiry x Swap Tenor matrix.

    Refuses anything that is not a complete ATM matrix:

    * a surface that is not an :attr:`VolSurfaceType.ATM_SWAPTION` one -- an
      OTM/SABR surface has a third (strike) coordinate this shape has no
      room for, so collapsing it into two dimensions would silently drop it;
    * a point that is not an absolute ATM vol, for the same reason: a
      spread-to-ATM value rendered in a cell of an ATM matrix would read as
      an absolute vol;
    * a grid with a hole -- an (expiry, tenor) intersection with no stored
      point. Filling it would invent a node.
    """

    if not isinstance(surface, CanonicalVolSurface):
        raise TypeError("surface must be a CanonicalVolSurface")
    if surface.identity.surface_type is not VolSurfaceType.ATM_SWAPTION:
        raise VolSurfaceGridError(
            "only an ATM_SWAPTION surface has an Expiry x Swap Tenor matrix; surface "
            f"{surface.surface_id} is a {surface.identity.surface_type.value} surface"
        )

    expiries: list[str] = []
    tenors: list[str] = []
    values: dict[tuple[str, str], float | None] = {}
    for point in surface.points:
        if point.strike_dimension is not StrikeDimension.ATM:
            raise VolSurfaceGridError(
                f"surface {surface.surface_id} carries a "
                f"{point.strike_dimension.value} point at "
                f"({point.expiry}, {point.underlying_tenor}); an ATM matrix has no "
                "strike axis to put it on"
            )
        if point.value_kind is not VolValueKind.ABSOLUTE_VOL:
            raise VolSurfaceGridError(
                f"surface {surface.surface_id} carries a {point.value_kind.value} value at "
                f"({point.expiry}, {point.underlying_tenor}); a cell of an ATM matrix is "
                "an absolute vol, and rendering a spread as one would misstate it"
            )
        if point.expiry not in expiries:
            expiries.append(point.expiry)
        if point.underlying_tenor not in tenors:
            tenors.append(point.underlying_tenor)
        # ``CanonicalVolSurface`` already refuses repeated coordinates, so a
        # second write to one key is unreachable here.
        values[(point.expiry, point.underlying_tenor)] = point.volatility

    missing = [
        (expiry, tenor)
        for expiry in expiries
        for tenor in tenors
        if (expiry, tenor) not in values
    ]
    if missing:
        raise VolSurfaceGridError(
            f"surface {surface.surface_id} is not a complete "
            f"{len(expiries)} x {len(tenors)} matrix: it stores no point at "
            + ", ".join(f"({expiry}, {tenor})" for expiry, tenor in missing[:5])
            + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
            + ". Refusing to show a matrix with a node the store does not hold."
        )

    return VolSurfaceGrid(
        expiries=tuple(expiries),
        underlying_tenors=tuple(tenors),
        rows=tuple(tuple(values[(expiry, tenor)] for tenor in tenors) for expiry in expiries),
    )
