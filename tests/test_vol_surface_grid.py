"""Reshaping a stored ATM surface back into its Expiry x Swap Tenor matrix
(Issue #194).

The reshape is the one piece of arithmetic-free logic between the canonical
store and the Markets Swaption Vol Surface view, so this file pins exactly
two things: the matrix is the stored points and nothing else, and a surface
that is not a complete ATM matrix is refused rather than completed.

Every fixture is ``test_vol_surface``'s synthetic 21x15 grid -- made-up
numbers, no live Bloomberg value anywhere.
"""

from __future__ import annotations

import pytest
from test_vol_surface import (
    EXPIRY_LABELS,
    POINT_COUNT,
    TENOR_LABELS,
    confirmed_surface,
    synthetic_value,
)

from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfacePoint,
    VolValueKind,
)
from shiori_pricing_lab.data.vol_surface_grid import (
    VolSurfaceGrid,
    VolSurfaceGridError,
    atm_grid_from_surface,
)


def _with_points(surface: CanonicalVolSurface, points) -> CanonicalVolSurface:
    return CanonicalVolSurface(
        identity=surface.identity,
        provenance=surface.provenance,
        points=tuple(points),
        volatility_unit=surface.volatility_unit,
    )


def test_the_matrix_is_the_full_stored_grid() -> None:
    grid = atm_grid_from_surface(confirmed_surface())

    assert grid.expiries == EXPIRY_LABELS
    assert grid.underlying_tenors == TENOR_LABELS
    assert len(grid.rows) == len(EXPIRY_LABELS) == 21
    assert all(len(row) == len(TENOR_LABELS) == 15 for row in grid.rows)
    assert sum(len(row) for row in grid.rows) == POINT_COUNT == 315


def test_every_cell_is_the_value_stored_at_that_coordinate() -> None:
    surface = confirmed_surface()
    grid = atm_grid_from_surface(surface)

    stored = {(point.expiry, point.underlying_tenor): point.volatility for point in surface.points}
    for row_index, expiry in enumerate(grid.expiries):
        for column_index, tenor in enumerate(grid.underlying_tenors):
            assert grid.rows[row_index][column_index] == stored[(expiry, tenor)]
            # Belt and braces: and that value is the fixture's own number, so
            # a reshape that silently transposed the matrix would fail here.
            assert grid.rows[row_index][column_index] == synthetic_value(row_index, column_index)


def test_an_unresolved_cell_stays_unresolved_and_is_never_filled_in() -> None:
    surface = confirmed_surface(unresolved_cells=frozenset({(3, 4), (0, 0)}))

    grid = atm_grid_from_surface(surface)

    assert grid.rows[3][4] is None
    assert grid.rows[0][0] is None
    # None means "the capture could not read this", never zero, and never a
    # neighbour's value borrowed to fill the hole.
    assert grid.rows[3][5] == synthetic_value(3, 5)
    assert grid.rows[4][4] == synthetic_value(4, 4)


def test_rows_and_columns_keep_the_stored_order_and_are_not_re_sorted() -> None:
    # The fixture's labels ("1Mo".."21Mo", "1Yr".."15Yr") sort differently as
    # strings than they do as durations, so a reshape that sorted anything
    # would visibly disagree with the stored order here.
    surface = confirmed_surface()

    grid = atm_grid_from_surface(surface)

    first_seen_expiries = []
    first_seen_tenors = []
    for point in surface.points:
        if point.expiry not in first_seen_expiries:
            first_seen_expiries.append(point.expiry)
        if point.underlying_tenor not in first_seen_tenors:
            first_seen_tenors.append(point.underlying_tenor)
    assert grid.expiries == tuple(first_seen_expiries)
    assert grid.underlying_tenors == tuple(first_seen_tenors)
    assert grid.expiries != tuple(sorted(grid.expiries))


def test_a_grid_with_a_hole_is_refused_rather_than_completed() -> None:
    surface = confirmed_surface()
    # Drop one intersection entirely -- not "unresolved", *absent*. The
    # remaining 314 points still name all 21 expiries and all 15 tenors, so
    # the only way to draw a rectangle would be to invent the missing node.
    dropped = surface.points[7]
    kept = tuple(point for point in surface.points if point is not dropped)

    with pytest.raises(VolSurfaceGridError) as excinfo:
        atm_grid_from_surface(_with_points(surface, kept))

    message = str(excinfo.value)
    assert "not a complete 21 x 15 matrix" in message
    assert f"({dropped.expiry}, {dropped.underlying_tenor})" in message


def test_an_otm_surface_has_no_two_dimensional_matrix() -> None:
    from test_vol_surface_store_otm_dimension import otm_surface

    with pytest.raises(VolSurfaceGridError, match="OTM_SWAPTION_SABR"):
        atm_grid_from_surface(otm_surface())


def test_a_spread_to_atm_value_is_never_rendered_as_an_absolute_vol() -> None:
    surface = confirmed_surface()
    points = list(surface.points)
    points[0] = VolSurfacePoint(
        expiry=points[0].expiry,
        underlying_tenor=points[0].underlying_tenor,
        volatility=points[0].volatility,
        strike_dimension=StrikeDimension.ATM,
        value_kind=VolValueKind.SPREAD_TO_ATM,
    )

    with pytest.raises(VolSurfaceGridError, match="SPREAD_TO_ATM"):
        atm_grid_from_surface(_with_points(surface, points))


def test_a_strike_offset_point_has_no_cell_in_an_atm_matrix() -> None:
    surface = confirmed_surface()
    points = list(surface.points)
    points[0] = VolSurfacePoint(
        expiry=points[0].expiry,
        underlying_tenor=points[0].underlying_tenor,
        volatility=points[0].volatility,
        strike_dimension=StrikeDimension.YIELD_OFFSET_BP,
        strike_offset=25.0,
        value_kind=VolValueKind.SPREAD_TO_ATM,
    )

    with pytest.raises(VolSurfaceGridError, match="YIELD_OFFSET_BP"):
        atm_grid_from_surface(_with_points(surface, points))


def test_only_a_canonical_surface_can_be_reshaped() -> None:
    with pytest.raises(TypeError):
        atm_grid_from_surface({"grid": "not a surface"})


def test_the_dict_form_carries_the_matrix_verbatim() -> None:
    grid = atm_grid_from_surface(confirmed_surface(unresolved_cells=frozenset({(2, 2)})))

    payload = grid.to_dict()

    assert payload["expiries"] == list(EXPIRY_LABELS)
    assert payload["underlying_tenors"] == list(TENOR_LABELS)
    assert payload["rows"][2][2] is None
    assert payload["rows"][5][6] == synthetic_value(5, 6)
    # A round trip through the dict changes no value at all.
    assert VolSurfaceGrid(
        expiries=tuple(payload["expiries"]),
        underlying_tenors=tuple(payload["underlying_tenors"]),
        rows=tuple(tuple(row) for row in payload["rows"]),
    ) == grid
