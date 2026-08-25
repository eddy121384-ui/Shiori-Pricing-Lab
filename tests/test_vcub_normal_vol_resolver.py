"""The VCUB normal-vol resolver: what it resolves, and what it refuses
(Issue #188).

Two halves, and the second is the point of the module.

*What it resolves*: a confirmed canonical snapshot's ATM column plus its
spread-to-ATM columns become absolute normal vols, the smile is read at the
requested additive moneyness, and the four bracketing expiry/tenor corners
combine bilinearly into one ``sigma_vcub`` -- every expected value below is
hand-computed from the fixture rather than recorded from a run.

*What it refuses*: a surface stating no volatility unit, a query naming no
smile model or naming SABR, a coordinate map that does not cover the
surface, another snapshot's identity, anything outside the confirmed
expiry/tenor/strike coverage, a bracketing node the surface does not hold,
and a spread with no ATM to reconstruct it from. All of them raise; none of
them return a degraded number.

Every fixture is synthetic. No live Bloomberg value appears in this
repository.
"""

from __future__ import annotations

import ast
import inspect
import math

import pytest

from shiori_pricing_lab.data import vcub_normal_vol_resolver
from shiori_pricing_lab.data.vcub_normal_vol_resolver import (
    BASIS_POINT_IN_DECIMAL,
    EXTRAPOLATION_MODE,
    RESOLVER_VERSION,
    GridCoordinateContractError,
    SmileContractError,
    SmileModel,
    SpreadReconstructionError,
    SurfaceCoverageError,
    SurfaceIdentityError,
    VCUBGridCoordinates,
    VCUBVolQuery,
    VolUnitContractError,
    resolve_vcub_normal_vol,
)
from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfaceIdentity,
    VolSurfacePoint,
    VolSurfaceProvenance,
    VolSurfaceType,
    VolValueKind,
)

CAPTURE_ID = "0123456789abcdef0123456789abcdef"
OTHER_CAPTURE_ID = "fedcba9876543210fedcba9876543210"
IMAGE_SHA256 = "a" * 64
CAPTURED_AT = "2026-08-18T09:30:00Z"
CONFIRMED_AT = "2026-08-18T09:41:00Z"

#: The synthetic surface every test below reads, in the stated unit ``bp``.
#: Two expiries by two tenors, an ATM absolute vol at each node, and a
#: spread-to-ATM column either side of it -- the shape of the VCUB OTM
#: Swaptions / SABR screen, at the smallest size that can still bracket an
#: off-grid query on both axes.
ATM_BP: dict[tuple[str, str], float] = {
    ("1Yr", "5Yr"): 80.0,
    ("1Yr", "10Yr"): 90.0,
    ("2Yr", "5Yr"): 100.0,
    ("2Yr", "10Yr"): 120.0,
}

#: Spread to ATM, in bp, at each captured strike offset. Negative on the
#: receiver wing and positive on the payer wing, so both signs of Annex
#: A.8.3's reconstruction are exercised by real fixture data.
SPREADS_BP: dict[tuple[str, str], dict[float, float]] = {
    ("1Yr", "5Yr"): {-25.0: -3.0, 25.0: 5.0},
    ("1Yr", "10Yr"): {-25.0: -4.0, 25.0: 10.0},
    ("2Yr", "5Yr"): {-25.0: -2.0, 25.0: 5.0},
    ("2Yr", "10Yr"): {-25.0: -6.0, 25.0: 20.0},
}

#: The caller-stated expiry/tenor axis. Issue #188 keeps the calendar-date
#: to VCUB year-fraction convention out of scope, so these numbers are the
#: test's own contract, not a day count this repository claims.
COORDINATES = VCUBGridCoordinates(
    expiry={"1Yr": 1.0, "2Yr": 2.0},
    tenor={"5Yr": 5.0, "10Yr": 10.0},
)


def points(
    *,
    atm: dict[tuple[str, str], float | None] | None = None,
    spreads: dict[tuple[str, str], dict[float, float]] | None = None,
    drop: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[VolSurfacePoint, ...]:
    atm_values: dict[tuple[str, str], float | None] = dict(ATM_BP if atm is None else atm)
    spread_values = SPREADS_BP if spreads is None else spreads
    built: list[VolSurfacePoint] = []
    for node in ATM_BP:
        if node in drop:
            continue
        expiry, tenor = node
        built.append(
            VolSurfacePoint(
                expiry=expiry,
                underlying_tenor=tenor,
                volatility=atm_values.get(node),
                strike_dimension=StrikeDimension.ATM,
            )
        )
        for offset, spread in sorted(spread_values.get(node, {}).items()):
            built.append(
                VolSurfacePoint(
                    expiry=expiry,
                    underlying_tenor=tenor,
                    volatility=spread,
                    strike_dimension=StrikeDimension.YIELD_OFFSET_BP,
                    strike_offset=offset,
                    value_kind=VolValueKind.SPREAD_TO_ATM,
                )
            )
    return tuple(built)


def surface(
    *,
    volatility_unit: str | None = "bp",
    capture_id: str = CAPTURE_ID,
    business_date: str = "08/18/26",
    **point_overrides,
) -> CanonicalVolSurface:
    return CanonicalVolSurface(
        identity=VolSurfaceIdentity(
            surface_type=VolSurfaceType.OTM_SWAPTION_SABR,
            capture_id=capture_id,
            business_date=business_date,
            currency="USD",
            curve_config="USD (30/360, S/A) vs. SOFR",
            side="Mid",
            vol_type="Normal Vol Skew",
            source="CMPN",
        ),
        provenance=VolSurfaceProvenance(
            capture_id=capture_id,
            source_reference="synthetic-vcub-otm.png",
            source_image_sha256=IMAGE_SHA256,
            source_image_bytes=4096,
            captured_at=CAPTURED_AT,
            parser_name="vcub_otm_template",
            parser_version="1.0.0",
            confirmed_by="eddy",
            confirmed_at=CONFIRMED_AT,
        ),
        points=points(**point_overrides),
        volatility_unit=volatility_unit,
    )


def query(**overrides) -> VCUBVolQuery:
    return VCUBVolQuery(
        **{
            "expiry_coordinate": 1.0,
            "tenor_coordinate": 5.0,
            "moneyness_bp": 0.0,
            "smile_model": SmileModel.PWL,
            **overrides,
        }
    )


def resolve(**overrides):
    coordinates = overrides.pop("coordinates", COORDINATES)
    resolved_surface = overrides.pop("surface", None) or surface()
    return resolve_vcub_normal_vol(resolved_surface, query(**overrides), coordinates=coordinates)


def resolved(**overrides) -> float:
    return resolve(**overrides).volatility


# --------------------------------------------------------------------------
# Reconstruction: ATM absolute + spread to ATM (Annex A.8.3)
# --------------------------------------------------------------------------


def test_an_atm_node_round_trips_as_its_own_absolute_vol() -> None:
    # 80.00bp stated, read back at exactly 80.00bp: an exact node is a
    # lookup, not an interpolation that happens to land nearby.
    resolution = resolve(expiry_coordinate=1.0, tenor_coordinate=5.0, moneyness_bp=0.0)

    assert resolution.volatility_raw == 80.0
    assert resolution.volatility == 80.0 * BASIS_POINT_IN_DECIMAL


def test_a_positive_spread_reconstructs_as_atm_plus_spread() -> None:
    # +25bp column: 100.00 ATM + 5.00 spread = 105.00bp absolute.
    resolution = resolve(expiry_coordinate=2.0, tenor_coordinate=5.0, moneyness_bp=25.0)

    assert resolution.volatility_raw == 105.0
    assert resolution.volatility == pytest.approx(0.0105, abs=1e-15)


def test_a_negative_spread_reconstructs_as_atm_plus_spread() -> None:
    # -25bp column: 80.00 ATM + (-3.00) spread = 77.00bp absolute. The
    # spread is added, never subtracted by sign convention and never
    # treated as a vol in its own right.
    resolution = resolve(expiry_coordinate=1.0, tenor_coordinate=5.0, moneyness_bp=-25.0)

    assert resolution.volatility_raw == 77.0
    assert resolution.volatility == pytest.approx(0.0077, abs=1e-15)


def test_a_spread_with_no_resolved_atm_blocks_rather_than_standing_alone() -> None:
    unresolved_atm = dict(ATM_BP)
    unresolved_atm[("1Yr", "5Yr")] = None

    with pytest.raises(SpreadReconstructionError, match="no resolved ATM absolute vol"):
        resolve(surface=surface(atm=unresolved_atm), moneyness_bp=25.0)


def test_the_reconstruction_is_reported_node_by_node() -> None:
    resolution = resolve(expiry_coordinate=1.0, tenor_coordinate=5.0, moneyness_bp=25.0)
    corner = resolution.corners[0]

    assert corner.atm_volatility_raw == 80.0
    payer_wing = next(node for node in corner.smile_nodes if node.moneyness_bp == 25.0)
    assert payer_wing.value_kind is VolValueKind.SPREAD_TO_ATM
    assert payer_wing.source_value_raw == 5.0
    assert payer_wing.volatility_raw == 85.0
    assert [node.moneyness_bp for node in corner.smile_nodes] == [-25.0, 0.0, 25.0]


# --------------------------------------------------------------------------
# Unit normalization (SPEC §3.3, Annex A.8.1)
# --------------------------------------------------------------------------


def test_a_stated_bp_unit_normalizes_at_one_basis_point_to_1e_minus_4() -> None:
    resolution = resolve()

    assert resolution.source_volatility_unit == "bp"
    assert resolution.unit_scale_to_decimal == 1e-4
    assert resolution.volatility == resolution.volatility_raw * 1e-4


def test_a_stated_decimal_unit_is_carried_through_unscaled() -> None:
    decimal_atm = {node: value * 1e-4 for node, value in ATM_BP.items()}
    decimal_spreads = {
        node: {offset: spread * 1e-4 for offset, spread in wings.items()}
        for node, wings in SPREADS_BP.items()
    }
    resolution = resolve(
        surface=surface(volatility_unit="decimal", atm=decimal_atm, spreads=decimal_spreads)
    )

    assert resolution.unit_scale_to_decimal == 1.0
    assert resolution.volatility == pytest.approx(0.008, abs=1e-15)


def test_a_surface_stating_no_unit_blocks_instead_of_being_read_as_bp() -> None:
    # The capture path states no unit, and 80.0 looks like bp only to a
    # reader who already assumed it. Nothing here infers from magnitude.
    with pytest.raises(VolUnitContractError, match="states no volatility_unit"):
        resolve(surface=surface(volatility_unit=None))


def test_a_surface_stating_an_unknown_unit_blocks() -> None:
    with pytest.raises(VolUnitContractError, match="does not pin"):
        resolve(surface=surface(volatility_unit="%"))


# --------------------------------------------------------------------------
# Smile resolution (Issue #188 §4)
# --------------------------------------------------------------------------


def test_pwl_interpolates_normal_vol_linearly_in_additive_moneyness() -> None:
    # At 1Yr x 5Yr the captured wings are 80.00 (ATM) and 85.00 (+25bp).
    # +10bp is 0.4 of the way across, so PWL on normal vol gives
    # 80.00 + 0.4 x 5.00 = 82.00bp -- linear in vol, not in variance and
    # not in log-moneyness.
    resolution = resolve(expiry_coordinate=1.0, tenor_coordinate=5.0, moneyness_bp=10.0)

    assert resolution.volatility_raw == pytest.approx(82.0, abs=1e-12)
    assert resolution.corners[0].bracketing_moneyness_bp == (0.0, 25.0)
    assert resolution.smile_model is SmileModel.PWL
    assert resolution.smile_model_version == "PWL_ADDITIVE_MONEYNESS_NORMAL_V1"


def test_every_captured_strike_node_round_trips_exactly() -> None:
    for (expiry, tenor), atm in ATM_BP.items():
        expiry_coordinate = COORDINATES.expiry[expiry]
        tenor_coordinate = COORDINATES.tenor[tenor]
        for offset, spread in {0.0: 0.0, **SPREADS_BP[(expiry, tenor)]}.items():
            resolution = resolve(
                expiry_coordinate=expiry_coordinate,
                tenor_coordinate=tenor_coordinate,
                moneyness_bp=offset,
            )

            assert resolution.volatility_raw == atm + spread


def test_a_query_naming_no_smile_model_blocks_instead_of_defaulting() -> None:
    with pytest.raises(SmileContractError, match="names no smile model"):
        resolve(smile_model=None)


def test_a_sabr_query_blocks_naming_the_contract_this_version_lacks() -> None:
    # The stored snapshot carries captured numbers, not a calibrated
    # alpha/rho/nu, and this repository pins no reproducible copy of the
    # Bloomberg calibration objective. A SABR answer would be invented, so
    # the resolver says which contract is missing instead of quietly
    # answering with PWL under a SABR label.
    with pytest.raises(SmileContractError, match="not implemented at resolver version"):
        resolve(smile_model=SmileModel.SABR)


def test_a_moneyness_outside_the_captured_wings_blocks() -> None:
    with pytest.raises(SurfaceCoverageError, match="additive moneyness"):
        resolve(moneyness_bp=300.0)


# --------------------------------------------------------------------------
# Same-additive-moneyness corner strikes (Issue #188 §3)
# --------------------------------------------------------------------------


def test_every_corner_uses_the_same_additive_moneyness_over_its_own_forward() -> None:
    forwards = {
        ("1Yr", "5Yr"): 0.0400,
        ("1Yr", "10Yr"): 0.0410,
        ("2Yr", "5Yr"): 0.0425,
        ("2Yr", "10Yr"): 0.0435,
    }
    resolution = resolve(
        expiry_coordinate=1.25,
        tenor_coordinate=7.5,
        moneyness_bp=10.0,
        corner_forwards=forwards,
    )

    assert resolution.query_moneyness_decimal == pytest.approx(10.0 * 1e-4, abs=1e-18)
    for corner in resolution.corners:
        forward = forwards[(corner.expiry_label, corner.tenor_label)]
        assert corner.forward == forward
        # K_ij = F_ij + mu*, so K_ij - F_ij is the same mu* at all four.
        assert corner.strike == pytest.approx(forward + 0.0010, abs=1e-15)
        assert corner.strike - corner.forward == pytest.approx(
            resolution.query_moneyness_decimal, abs=1e-15
        )


def test_a_corner_strike_is_left_unreported_rather_than_invented() -> None:
    # No forward stated: the smile is resolved in moneyness space, so
    # sigma_vcub is unaffected, and the strike is reported as unknown
    # rather than derived from an assumed forward.
    resolution = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5, moneyness_bp=10.0)

    assert all(corner.forward is None and corner.strike is None for corner in resolution.corners)
    assert resolution.volatility == resolved(
        expiry_coordinate=1.25,
        tenor_coordinate=7.5,
        moneyness_bp=10.0,
        corner_forwards={
            ("1Yr", "5Yr"): 0.04,
            ("1Yr", "10Yr"): 0.04,
            ("2Yr", "5Yr"): 0.04,
            ("2Yr", "10Yr"): 0.04,
        },
    )


def test_a_partly_stated_forward_contract_blocks() -> None:
    with pytest.raises(GridCoordinateContractError, match="not for"):
        resolve(
            expiry_coordinate=1.25,
            tenor_coordinate=7.5,
            moneyness_bp=10.0,
            corner_forwards={("1Yr", "5Yr"): 0.04},
        )


# --------------------------------------------------------------------------
# Expiry/tenor interpolation (Issue #188 §5)
# --------------------------------------------------------------------------


def test_an_exact_expiry_tenor_node_gives_that_node_and_no_blend() -> None:
    resolution = resolve(expiry_coordinate=2.0, tenor_coordinate=10.0, moneyness_bp=0.0)

    assert resolution.volatility_raw == 120.0
    assert resolution.expiry_weight == 0.0
    assert resolution.tenor_weight == 0.0
    assert [corner.weight for corner in resolution.corners] == [1.0, 0.0, 0.0, 0.0]
    assert all(corner.expiry_label == "2Yr" for corner in resolution.corners)
    assert all(corner.tenor_label == "10Yr" for corner in resolution.corners)


def test_an_off_grid_expiry_on_an_exact_tenor_interpolates_on_one_axis_only() -> None:
    # 1.50 sits halfway between 1Yr and 2Yr; the tenor is an exact 5Yr.
    # ATM: 0.5 x 80.00 + 0.5 x 100.00 = 90.00bp.
    resolution = resolve(expiry_coordinate=1.5, tenor_coordinate=5.0, moneyness_bp=0.0)

    assert resolution.volatility_raw == pytest.approx(90.0, abs=1e-12)
    assert resolution.expiry_weight == 0.5
    assert resolution.tenor_weight == 0.0
    assert [corner.weight for corner in resolution.corners] == [0.5, 0.0, 0.5, 0.0]


def test_an_off_grid_tenor_on_an_exact_expiry_interpolates_on_one_axis_only() -> None:
    # 7.50 sits halfway between 5Yr and 10Yr; the expiry is an exact 1Yr.
    # ATM: 0.5 x 80.00 + 0.5 x 90.00 = 85.00bp.
    resolution = resolve(expiry_coordinate=1.0, tenor_coordinate=7.5, moneyness_bp=0.0)

    assert resolution.volatility_raw == pytest.approx(85.0, abs=1e-12)
    assert resolution.expiry_weight == 0.0
    assert resolution.tenor_weight == 0.5


def test_the_bloomberg_pattern_fixture_resolves_by_hand_computable_bilinear() -> None:
    """Issue #188 acceptance C, worked through by hand.

    Off-grid on both axes, one additive moneyness held at all four corners,
    each corner's smile resolved first, and the four corner vols combined
    bilinearly::

        mu*   = +10bp, 0.4 of the way from the ATM column to the +25bp one
        T*    = 1.25   -> 0.25 of the way from 1Yr to 2Yr
        tau*  = 7.50   -> 0.50 of the way from 5Yr to 10Yr

        1Yr x  5Yr: 80.00 + 0.4 x  5.00 =  82.00bp, weight 0.75 x 0.50 = 0.375
        1Yr x 10Yr: 90.00 + 0.4 x 10.00 =  94.00bp, weight 0.75 x 0.50 = 0.375
        2Yr x  5Yr: 100.00 + 0.4 x 5.00 = 102.00bp, weight 0.25 x 0.50 = 0.125
        2Yr x 10Yr: 120.00 + 0.4 x 20.0 = 128.00bp, weight 0.25 x 0.50 = 0.125

        sigma_vcub = 0.375 x  82.00 + 0.375 x  94.00
                   + 0.125 x 102.00 + 0.125 x 128.00
                   = 30.750 + 35.250 + 12.750 + 16.000
                   = 94.75bp
                   = 0.009475 as an absolute decimal normal vol
    """

    resolution = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5, moneyness_bp=10.0)

    assert resolution.expiry_weight == pytest.approx(0.25, abs=1e-15)
    assert resolution.tenor_weight == pytest.approx(0.5, abs=1e-15)
    assert [corner.volatility_raw for corner in resolution.corners] == [
        pytest.approx(82.0, abs=1e-12),
        pytest.approx(94.0, abs=1e-12),
        pytest.approx(102.0, abs=1e-12),
        pytest.approx(128.0, abs=1e-12),
    ]
    assert [corner.weight for corner in resolution.corners] == [
        pytest.approx(0.375, abs=1e-15),
        pytest.approx(0.375, abs=1e-15),
        pytest.approx(0.125, abs=1e-15),
        pytest.approx(0.125, abs=1e-15),
    ]
    assert resolution.volatility_raw == pytest.approx(94.75, abs=1e-12)
    assert resolution.volatility == pytest.approx(0.009475, abs=1e-15)


def test_the_corner_weights_always_sum_to_one() -> None:
    for expiry_coordinate, tenor_coordinate in ((1.0, 5.0), (1.5, 5.0), (1.0, 7.5), (1.25, 7.5)):
        resolution = resolve(
            expiry_coordinate=expiry_coordinate, tenor_coordinate=tenor_coordinate
        )

        assert math.fsum(corner.weight for corner in resolution.corners) == pytest.approx(
            1.0, abs=1e-15
        )


def test_resolving_the_same_query_twice_gives_the_same_number() -> None:
    first = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5, moneyness_bp=10.0)
    second = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5, moneyness_bp=10.0)

    assert first.to_dict() == second.to_dict()


# --------------------------------------------------------------------------
# Fail-closed coverage (Issue #188 §6)
# --------------------------------------------------------------------------


def test_an_expiry_beyond_the_confirmed_surface_blocks() -> None:
    with pytest.raises(SurfaceCoverageError, match="expiry") as blocked:
        resolve(expiry_coordinate=3.0, tenor_coordinate=5.0)

    assert EXTRAPOLATION_MODE in str(blocked.value)
    assert "nearest-node, flat, or smile-extension" in str(blocked.value)


def test_an_expiry_before_the_confirmed_surface_blocks() -> None:
    with pytest.raises(SurfaceCoverageError, match="expiry"):
        resolve(expiry_coordinate=0.5, tenor_coordinate=5.0)


def test_a_tenor_beyond_the_confirmed_surface_blocks() -> None:
    with pytest.raises(SurfaceCoverageError, match="tenor"):
        resolve(expiry_coordinate=1.0, tenor_coordinate=20.0)


def test_a_missing_bracketing_node_blocks_rather_than_borrowing_a_neighbour() -> None:
    with pytest.raises(SurfaceCoverageError, match="brackets the requested point"):
        resolve(
            surface=surface(drop=frozenset({("2Yr", "10Yr")})),
            expiry_coordinate=1.25,
            tenor_coordinate=7.5,
        )


def test_a_bracketing_node_with_no_resolved_value_blocks() -> None:
    unresolved = dict(ATM_BP)
    unresolved[("2Yr", "10Yr")] = None

    with pytest.raises(SurfaceCoverageError, match="holds no resolved volatility"):
        resolve(
            surface=surface(atm=unresolved, spreads={}),
            expiry_coordinate=1.25,
            tenor_coordinate=7.5,
        )


# --------------------------------------------------------------------------
# Snapshot identity and the coordinate contract
# --------------------------------------------------------------------------


def test_a_surface_that_is_not_the_declared_snapshot_blocks() -> None:
    other = surface(capture_id=OTHER_CAPTURE_ID, business_date="08/19/26")

    with pytest.raises(SurfaceIdentityError, match="two observations"):
        resolve(surface=other, expected_surface_id=surface().surface_id)


def test_a_declared_snapshot_that_matches_resolves() -> None:
    resolution = resolve(expected_surface_id=surface().surface_id)

    assert resolution.surface_id == surface().surface_id


def test_all_four_corners_come_from_one_snapshot() -> None:
    resolution = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5)

    assert resolution.capture_id == CAPTURE_ID
    assert resolution.business_date == "08/18/26"
    assert len(resolution.corners) == 4


def test_a_label_the_coordinate_map_does_not_name_blocks() -> None:
    partial = VCUBGridCoordinates(expiry={"1Yr": 1.0}, tenor={"5Yr": 5.0, "10Yr": 10.0})

    with pytest.raises(GridCoordinateContractError, match="does not name"):
        resolve(coordinates=partial)


def test_two_labels_on_one_coordinate_block() -> None:
    with pytest.raises(GridCoordinateContractError, match="same coordinate"):
        VCUBGridCoordinates(expiry={"1Yr": 1.0, "12Mo": 1.0}, tenor={"5Yr": 5.0})


# --------------------------------------------------------------------------
# Provenance, and the methodology boundary this issue stops at
# --------------------------------------------------------------------------


def test_a_resolution_carries_the_snapshot_and_resolver_provenance() -> None:
    resolution = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5, moneyness_bp=10.0)
    payload = resolution.to_dict()

    assert payload["surface_id"] == surface().surface_id
    assert payload["capture_id"] == CAPTURE_ID
    assert payload["surface_type"] == VolSurfaceType.OTM_SWAPTION_SABR.value
    assert payload["business_date"] == "08/18/26"
    assert payload["vol_type"] == "Normal Vol Skew"
    assert payload["captured_at"] == CAPTURED_AT
    assert payload["confirmed_by"] == "eddy"
    assert payload["confirmed_at"] == CONFIRMED_AT
    assert payload["resolver_version"] == RESOLVER_VERSION
    assert payload["extrapolation_mode"] == EXTRAPOLATION_MODE
    assert payload["source_volatility_unit"] == "bp"
    assert payload["unit_scale_to_decimal"] == 1e-4
    assert payload["query_moneyness_bp"] == 10.0
    assert payload["expiry_bracket_labels"] == ["1Yr", "2Yr"]
    assert payload["tenor_bracket_labels"] == ["5Yr", "10Yr"]
    assert len(payload["corners"]) == 4
    assert payload["corners"][0]["smile_nodes"][0]["value_kind"] == "SPREAD_TO_ATM"
    assert payload["volatility_unit"] == "bp"


def test_an_accepted_resolution_never_reports_a_fallback() -> None:
    resolution = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5, moneyness_bp=10.0)

    assert resolution.fallback_used is False
    assert resolution.blocking is False


def test_the_resolver_never_reaches_the_bond_option_pricing_or_dcf_bridge() -> None:
    """Issue #188's hard boundary, checked structurally.

    The resolver stops at ``sigma_vcub``. ``lambda_vcub``, the
    ``DCF_VCUB``/``DCF_BondVol`` total-variance adjustment, ``sigma_Y^N``,
    the duration conversion to ``sigma_P``, and Black-76 all remain behind
    the RED methodology gate, so this module may not import the packages
    they live in -- a boundary a docstring alone cannot hold.
    """

    tree = ast.parse(inspect.getsource(vcub_normal_vol_resolver))
    imported: set[str] = set()
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            imported.update(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module:
            imported.add(statement.module)

    assert not any(
        name.startswith(("shiori_pricing_lab.pricing", "shiori_pricing_lab.products"))
        for name in imported
    ), sorted(imported)
    assert imported <= {
        "__future__",
        "math",
        "collections.abc",
        "dataclasses",
        "enum",
        "shiori_pricing_lab.data.vol_surface",
    }
