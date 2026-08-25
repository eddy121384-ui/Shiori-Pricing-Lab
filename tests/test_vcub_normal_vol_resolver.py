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
from test_vol_surface import confirmed_surface as confirmed_atm_surface
from test_vol_surface_store_otm_dimension import otm_surface

from shiori_pricing_lab.data import vcub_normal_vol_resolver
from shiori_pricing_lab.data.vcub_normal_vol_resolver import (
    BASIS_POINT_IN_DECIMAL,
    EXTRAPOLATION_MODE,
    RESOLVER_VERSION,
    GridCoordinateContractError,
    NegativeVolatilityError,
    SmileContractError,
    SmileModel,
    SpreadReconstructionError,
    SurfaceCoverageError,
    SurfaceIdentityError,
    VCUBGridCoordinates,
    VCUBVolQuery,
    VolSpaceContractError,
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
from shiori_pricing_lab.data.vol_surface_store import VolSurfaceStore

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
    spreads: dict[tuple[str, str], dict[float, float] | dict[float, float | None]]
    | None = None,
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
    vol_type: str | None = "Normal Vol Skew",
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
            vol_type=vol_type,
            source="CMPN",
            unresolved_fields=() if vol_type is not None else ("vol_type",),
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


def test_each_value_is_labelled_with_its_own_unit() -> None:
    """Codex review, PR #189.

    ``volatility`` is already normalized, so labelling it with the source's
    ``bp`` would invite a consumer reading the serialized result as a
    value/unit pair to scale 0.008 by another 1e-4.
    """

    resolution = resolve()

    assert resolution.volatility_raw == 80.0
    assert resolution.source_volatility_unit == "bp"
    assert resolution.volatility == 0.008
    assert resolution.volatility_unit == "decimal"
    assert resolution.volatility_unit != resolution.source_volatility_unit


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
    # A surface already stating decimal has both units agree, which is the
    # same convention rather than an exception to it.
    assert resolution.source_volatility_unit == resolution.volatility_unit == "decimal"


def test_a_surface_stating_no_unit_blocks_instead_of_being_read_as_bp() -> None:
    # A capture whose vol type pins no unit leaves this unresolved, and
    # 80.0 looks like bp only to a reader who already assumed it. Nothing
    # here infers a unit from magnitude.
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
    assert payload["volatility_unit"] == "decimal"


def test_an_accepted_resolution_never_reports_a_fallback() -> None:
    resolution = resolve(expiry_coordinate=1.25, tenor_coordinate=7.5, moneyness_bp=10.0)

    assert resolution.fallback_used is False
    assert resolution.blocking is False


def test_a_query_across_an_unresolved_captured_column_blocks() -> None:
    """Codex review, PR #189.

    The +10bp column exists on the screen and the capture could not read
    it. Answering a +10bp query from the resolved ATM and +25bp columns
    either side would return 82.00bp -- a number at a coordinate the
    snapshot explicitly failed to read -- and report no fallback.
    """

    holed = dict(SPREADS_BP)
    holed[("1Yr", "5Yr")] = {-25.0: -3.0, 10.0: None, 25.0: 5.0}

    with pytest.raises(SurfaceCoverageError, match="left unresolved"):
        resolve(surface=surface(spreads=holed), moneyness_bp=10.0)


def test_a_query_reaching_across_an_unresolved_column_blocks_too() -> None:
    holed = dict(SPREADS_BP)
    holed[("1Yr", "5Yr")] = {-25.0: -3.0, 10.0: None, 25.0: 5.0}

    # 12bp brackets [0, 25], which spans the unreadable 10bp column.
    with pytest.raises(SurfaceCoverageError, match="left unresolved"):
        resolve(surface=surface(spreads=holed), moneyness_bp=12.0)


def test_a_query_clear_of_an_unresolved_column_still_resolves() -> None:
    # The hole is on the payer wing; a -25bp query brackets [-25, 0] and
    # never touches it. Fail-closed is not fail-everything.
    holed = dict(SPREADS_BP)
    holed[("1Yr", "5Yr")] = {-25.0: -3.0, 10.0: None, 25.0: 5.0}

    assert resolve(surface=surface(spreads=holed), moneyness_bp=-25.0).volatility_raw == 77.0


def test_an_unresolved_atm_blocks_a_query_that_brackets_it() -> None:
    # Every non-ATM column here is an absolute vol rather than a spread, so
    # the spread-reconstruction guard does not fire and the unresolved ATM
    # coordinate itself is what blocks.
    absolute_wings = tuple(
        VolSurfacePoint(
            expiry="1Yr",
            underlying_tenor="5Yr",
            volatility=volatility,
            strike_dimension=StrikeDimension.YIELD_OFFSET_BP,
            strike_offset=offset,
            value_kind=VolValueKind.ABSOLUTE_VOL,
        )
        for offset, volatility in ((-25.0, 77.0), (25.0, 85.0))
    )
    unresolved_atm = VolSurfacePoint(
        expiry="1Yr",
        underlying_tenor="5Yr",
        volatility=None,
        strike_dimension=StrikeDimension.ATM,
    )
    one_node = CanonicalVolSurface(
        identity=surface().identity,
        provenance=surface().provenance,
        points=(unresolved_atm, *absolute_wings),
        volatility_unit="bp",
    )

    with pytest.raises(SurfaceCoverageError, match="left unresolved"):
        resolve_vcub_normal_vol(
            one_node,
            query(expiry_coordinate=1.0, tenor_coordinate=5.0, moneyness_bp=0.0),
            coordinates=COORDINATES,
        )


def test_a_reconstruction_that_goes_negative_blocks() -> None:
    """Codex review, PR #189.

    80.00 ATM against a -90.00 spread is not a 10bp-low vol, it is evidence
    that the numbers or their spread semantics are not what they claim. A
    normal vol is non-negative and this one would be handed downstream as a
    model input.
    """

    impossible = dict(SPREADS_BP)
    impossible[("1Yr", "5Yr")] = {-25.0: -90.0, 25.0: 5.0}

    with pytest.raises(NegativeVolatilityError, match="is not a volatility"):
        resolve(surface=surface(spreads=impossible), moneyness_bp=-25.0)


# --------------------------------------------------------------------------
# The volatility-space contract (Codex review, PR #189)
# --------------------------------------------------------------------------


def test_a_surface_that_states_no_vol_type_blocks() -> None:
    with pytest.raises(VolSpaceContractError, match="leaves vol_type unresolved"):
        resolve(surface=surface(vol_type=None))


def test_a_surface_stating_a_lognormal_vol_type_blocks() -> None:
    # sigma_vcub is a normal vol. A lognormal surface's numbers are not that
    # quantity, and normalizing them at 1bp = 1e-4 would relabel rather than
    # convert them.
    with pytest.raises(VolSpaceContractError, match="does not declare normal"):
        resolve(surface=surface(vol_type="Lognormal Vol (OIS)"))


def test_the_atm_screens_own_normal_vol_type_is_accepted() -> None:
    # The ATM tab spells it "Normal Vol (OIS)" and the OTM tab spells it
    # "Normal Vol Skew"; both declare normal space in the screen's own
    # closed vocabulary.
    assert resolve(surface=surface(vol_type="Normal Vol (OIS)")).vol_type == "Normal Vol (OIS)"


# --------------------------------------------------------------------------
# The real capture -> canonical store -> resolver path (Eddy's decision on
# PR #189: a stored VCUB normal-vol surface must resolve as stored, with no
# unit injected by hand)
# --------------------------------------------------------------------------

#: The captured screens' own axis labels, given the numeric coordinates this
#: test resolves against. Stated here rather than derived: Issue #188 keeps
#: the calendar-date / label -> VCUB year-fraction convention out of scope,
#: so this map is the caller's contract, not a day count the repository
#: claims. It covers every label the captured surface carries, which the
#: resolver requires.
CAPTURED_GRID = VCUBGridCoordinates(
    expiry={
        "1Mo": 1.0 / 12.0,
        "3Mo": 0.25,
        "6Mo": 0.5,
        "9Mo": 0.75,
        "1Yr": 1.0,
        "2Yr": 2.0,
        "3Yr": 3.0,
        "5Yr": 5.0,
        "7Yr": 7.0,
        "10Yr": 10.0,
        "15Yr": 15.0,
        "20Yr": 20.0,
        "30Yr": 30.0,
    },
    tenor={
        "1Yr": 1.0,
        "2Yr": 2.0,
        "5Yr": 5.0,
        "10Yr": 10.0,
        "15Yr": 15.0,
        "20Yr": 20.0,
        "30Yr": 30.0,
    },
)


def test_a_confirmed_otm_capture_resolves_straight_out_of_the_store(tmp_path) -> None:
    """The operational Definition of Done, end to end.

    A surface built by the real merge -> confirm -> adapter path, saved to
    the real store and fetched back, resolves with no unit injected by the
    test. Before Eddy's PR #189 decision this was impossible: the adapter
    left ``volatility_unit`` unresolved and the resolver refused every
    surface the capture path could produce.

    The fixture's ``1Yr x 5Yr`` ATM cell is synthetic value
    ``-4.00 + 30 x 2.50 + 4 x 0.25 = 72.00``bp, so an exact-node query at
    that coordinate is that number, normalized at ``1bp = 1e-4``.
    """

    captured = otm_surface()
    store = VolSurfaceStore(tmp_path / "vol_surfaces.sqlite3")
    store.save_confirmed_surface(captured)
    surface = store.fetch_surface(captured.surface_id)

    assert surface.volatility_unit == "bp"

    resolution = resolve_vcub_normal_vol(
        surface,
        VCUBVolQuery(
            expiry_coordinate=1.0,
            tenor_coordinate=5.0,
            moneyness_bp=0.0,
            smile_model=SmileModel.PWL,
            expected_surface_id=captured.surface_id,
        ),
        coordinates=CAPTURED_GRID,
    )

    assert resolution.source_volatility_unit == "bp"
    assert resolution.unit_scale_to_decimal == 1e-4
    assert resolution.volatility_raw == 72.0
    assert resolution.volatility == pytest.approx(0.0072, abs=1e-15)
    assert resolution.volatility_unit == "decimal"
    assert resolution.fallback_used is False


def test_a_stored_capture_also_resolves_off_grid_on_both_axes(tmp_path) -> None:
    """The same stored surface, bilinearly interpolated.

    ``1.50`` sits halfway between the ``1Yr`` and ``2Yr`` terms and ``7.50``
    halfway between the ``5Yr`` and ``10Yr`` tenors, so each corner takes a
    quarter of the weight. The four ATM cells are rows 30, 31, 37 and 38 of
    the fixture -- ``72.00``, ``74.50``, ``89.50`` and ``92.00``bp -- and
    ``(72.00 + 74.50 + 89.50 + 92.00) / 4 = 82.00``bp.
    """

    captured = otm_surface()
    store = VolSurfaceStore(tmp_path / "vol_surfaces.sqlite3")
    store.save_confirmed_surface(captured)
    surface = store.fetch_surface(captured.surface_id)

    resolution = resolve_vcub_normal_vol(
        surface,
        VCUBVolQuery(
            expiry_coordinate=1.5,
            tenor_coordinate=7.5,
            moneyness_bp=0.0,
            smile_model=SmileModel.PWL,
        ),
        coordinates=CAPTURED_GRID,
    )

    assert [corner.atm_volatility_raw for corner in resolution.corners] == [
        72.0,
        74.5,
        89.5,
        92.0,
    ]
    assert [corner.weight for corner in resolution.corners] == [0.25] * 4
    assert resolution.volatility_raw == pytest.approx(82.0, abs=1e-12)
    assert resolution.volatility == pytest.approx(0.0082, abs=1e-15)


def test_a_confirmed_atm_capture_resolves_at_its_own_atm_coordinate() -> None:
    """The ATM screen states ``Normal Vol (OIS)``, so it states ``bp`` too.

    That surface carries only ATM points, so it answers a ``mu* = 0`` query
    and nothing else -- which is the honest extent of what an ATM-only
    capture knows.
    """

    surface = confirmed_atm_surface()
    coordinates = VCUBGridCoordinates(
        expiry={f"{months}Mo": months / 12.0 for months in range(1, 22)},
        tenor={f"{years}Yr": float(years) for years in range(1, 16)},
    )

    assert surface.volatility_unit == "bp"

    resolution = resolve_vcub_normal_vol(
        surface,
        VCUBVolQuery(
            expiry_coordinate=1.0 / 12.0,
            tenor_coordinate=1.0,
            moneyness_bp=0.0,
            smile_model=SmileModel.PWL,
        ),
        coordinates=coordinates,
    )

    # The ATM fixture's (0, 0) cell is 80.00.
    assert resolution.volatility_raw == 80.0
    assert resolution.volatility == pytest.approx(0.008, abs=1e-15)

    # And it cannot answer anything off the ATM column, because it holds no
    # other column to answer from.
    with pytest.raises(SurfaceCoverageError, match="additive moneyness"):
        resolve_vcub_normal_vol(
            surface,
            VCUBVolQuery(
                expiry_coordinate=1.0 / 12.0,
                tenor_coordinate=1.0,
                moneyness_bp=25.0,
                smile_model=SmileModel.PWL,
            ),
            coordinates=coordinates,
        )


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
        "shiori_pricing_lab.data.bloomberg_vcub_screen_reader",
        "shiori_pricing_lab.data.vol_surface",
    }
