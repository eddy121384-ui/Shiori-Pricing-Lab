"""The canonical vol-surface model and the VCUB adapter that fills it
(Issue #183).

These tests are about *shape and honesty*: what a stored surface may
assert, what it may never invent, and what a confirmed VCUB capture
becomes. Storage itself is ``test_vol_surface_store``.

The fixtures here build a 21x15 = 315-cell grid, the real live-acceptance
size from Issue #181, from synthetic numbers. No live Bloomberg values
appear anywhere in this repository.
"""

from __future__ import annotations

import csv
import io
import json
import math

import pytest

from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    PARSER_NAME,
    PARSER_VERSION,
    VCUBATMCapture,
    VCUBATMGrid,
    VCUBCaptureIssue,
    VCUBCaptureProvenance,
    VCUBSourceMetadata,
)
from shiori_pricing_lab.data.vcub_vol_surface_adapter import (
    UnconfirmedCaptureError,
    canonical_surface_from_confirmed_capture,
)
from shiori_pricing_lab.data.vol_surface import (
    EXPORT_COLUMNS,
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfaceIdentity,
    VolSurfacePoint,
    VolSurfaceProvenance,
    VolSurfaceType,
    export_rows,
    export_surface_as_csv,
    export_surface_as_json,
)

#: Issue #181's live acceptance shape: 21 expiries down, 15 tenors across.
EXPIRY_LABELS = tuple(f"{n}Mo" for n in range(1, 22))
TENOR_LABELS = tuple(f"{n}Yr" for n in range(1, 16))
POINT_COUNT = len(EXPIRY_LABELS) * len(TENOR_LABELS)

CAPTURED_AT = "2026-08-18T09:30:00Z"
CONFIRMED_AT = "2026-08-18T09:41:00Z"
CAPTURE_ID = "0123456789abcdef0123456789abcdef"


def synthetic_value(row_index: int, column_index: int) -> float:
    """A distinct, entirely made-up number for every intersection."""

    return round(80.0 + row_index + column_index / 100.0, 2)


def grid(*, unresolved_cells: frozenset[tuple[int, int]] = frozenset()) -> VCUBATMGrid:
    return VCUBATMGrid(
        expiry_labels=EXPIRY_LABELS,
        tenor_labels=TENOR_LABELS,
        values=tuple(
            tuple(
                None
                if (row_index, column_index) in unresolved_cells
                else synthetic_value(row_index, column_index)
                for column_index in range(len(TENOR_LABELS))
            )
            for row_index in range(len(EXPIRY_LABELS))
        ),
    )


def metadata(**overrides) -> VCUBSourceMetadata:
    fields = {
        "currency": "USD",
        "curve_config": "USD RFR BVOL Cube (Default)",
        "side": "Mid",
        "quote_date": "08/18/26",
        "tab": "ATM Swaptions",
        "vol_type": "Normal Vol (OIS)",
        "source": "BVOL",
    }
    fields.update(overrides)
    unresolved = tuple(name for name, value in fields.items() if value is None)
    return VCUBSourceMetadata(**fields, unresolved_fields=unresolved)


def capture_provenance(**overrides) -> VCUBCaptureProvenance:
    fields = {
        "source_reference": "vcub_atm_usd.png",
        "source_image_sha256": "a" * 64,
        "source_image_bytes": 4096,
        "captured_at": CAPTURED_AT,
    }
    fields.update(overrides)
    return VCUBCaptureProvenance(**fields)


def confirmed_capture(
    *,
    metadata_overrides: dict | None = None,
    unresolved_cells: frozenset[tuple[int, int]] = frozenset(),
    reviewed_by: str = "Eddy",
    provenance_overrides: dict | None = None,
) -> VCUBATMCapture:
    return VCUBATMCapture(
        provenance=capture_provenance(**(provenance_overrides or {})),
        metadata=metadata(**(metadata_overrides or {})),
        grid=grid(unresolved_cells=unresolved_cells),
    ).confirm(reviewed_by=reviewed_by, reviewed_at=CONFIRMED_AT)


def confirmed_surface(*, capture_id: str = CAPTURE_ID, **kwargs) -> CanonicalVolSurface:
    return canonical_surface_from_confirmed_capture(
        confirmed_capture(**kwargs), capture_id=capture_id
    )


# -- the adapter ------------------------------------------------------------


def test_a_confirmed_capture_becomes_every_one_of_its_315_points() -> None:
    surface = confirmed_surface()

    assert len(surface.points) == POINT_COUNT == 315
    assert {point.expiry for point in surface.points} == set(EXPIRY_LABELS)
    assert {point.underlying_tenor for point in surface.points} == set(TENOR_LABELS)


def test_every_point_keeps_the_value_at_its_own_expiry_and_tenor() -> None:
    surface = confirmed_surface()

    by_coordinate = {
        (point.expiry, point.underlying_tenor): point.volatility for point in surface.points
    }
    for row_index, expiry in enumerate(EXPIRY_LABELS):
        for column_index, tenor in enumerate(TENOR_LABELS):
            assert by_coordinate[(expiry, tenor)] == synthetic_value(row_index, column_index)


def test_the_screen_order_of_both_axes_survives_the_conversion() -> None:
    """Row-major in the screen's own order, so a reader sees what the trader saw."""

    surface = confirmed_surface()

    assert [point.expiry for point in surface.points[: len(TENOR_LABELS)]] == [
        EXPIRY_LABELS[0]
    ] * len(TENOR_LABELS)
    assert [
        point.underlying_tenor for point in surface.points[: len(TENOR_LABELS)]
    ] == list(TENOR_LABELS)


def test_an_unresolved_cell_is_kept_as_a_point_with_no_value() -> None:
    """Dropping it would lose the coordinate the trader actually reviewed."""

    surface = confirmed_surface(unresolved_cells=frozenset({(2, 3)}))

    assert len(surface.points) == POINT_COUNT
    unresolved = [point for point in surface.points if point.volatility is None]
    assert [(point.expiry, point.underlying_tenor) for point in unresolved] == [
        (EXPIRY_LABELS[2], TENOR_LABELS[3])
    ]
    assert len(surface.resolved_points()) == POINT_COUNT - 1


def test_every_atm_point_is_marked_atm_and_carries_no_strike_offset() -> None:
    surface = confirmed_surface()

    assert {point.strike_dimension for point in surface.points} == {StrikeDimension.ATM}
    assert {point.strike_offset for point in surface.points} == {None}


def test_the_identity_copies_the_screens_own_metadata_verbatim() -> None:
    surface = confirmed_surface()

    assert surface.identity.to_dict() == {
        "surface_type": "ATM_SWAPTION",
        "capture_id": CAPTURE_ID,
        "business_date": "08/18/26",
        "currency": "USD",
        "curve_config": "USD RFR BVOL Cube (Default)",
        "side": "Mid",
        "vol_type": "Normal Vol (OIS)",
        "source": "BVOL",
        "unresolved_fields": [],
    }


def test_an_unresolved_metadata_field_stays_unresolved_and_is_never_filled_in() -> None:
    surface = confirmed_surface(metadata_overrides={"source": None, "side": None})

    assert surface.identity.source is None
    assert surface.identity.side is None
    assert sorted(surface.identity.unresolved_fields) == ["side", "source"]


def test_the_provenance_carries_the_screenshot_hash_parser_and_confirmation() -> None:
    surface = confirmed_surface()

    assert surface.provenance.to_dict() == {
        "capture_id": CAPTURE_ID,
        "source_reference": "vcub_atm_usd.png",
        "source_image_sha256": "a" * 64,
        "source_image_bytes": 4096,
        "captured_at": CAPTURED_AT,
        "parser_name": PARSER_NAME,
        "parser_version": PARSER_VERSION,
        "confirmed_by": "Eddy",
        "confirmed_at": CONFIRMED_AT,
    }


def test_a_confirmed_normal_vol_capture_states_bp() -> None:
    """The screen states a vol *type*, and the type is what pins the unit.

    Bloomberg's volatility-cube methodology document (DOCS #2063620) states
    that normal volatility quotes are expressed in basis points, and this
    screen states ``Normal Vol (OIS)`` on its face. The unit is therefore
    stated by the source rather than guessed from the numbers -- which is
    what Annex A.8.1 requires before any of them can be normalized (Eddy's
    decision on PR #189).
    """

    assert confirmed_surface().volatility_unit == "bp"


def test_a_capture_whose_vol_type_is_unresolved_states_no_unit() -> None:
    """No stated space, no stated unit. 80.0 is not evidence of anything."""

    surface = confirmed_surface(metadata_overrides={"vol_type": None})

    assert surface.identity.vol_type is None
    assert "vol_type" in surface.identity.unresolved_fields
    assert surface.volatility_unit is None


@pytest.mark.parametrize("vol_type", ["Lognormal Vol (OIS)", "Black Vol", "SABR Vol"])
def test_a_capture_in_another_vol_space_never_states_bp(vol_type: str) -> None:
    """The bp evidence is about *normal* vol and says nothing about any other space."""

    surface = confirmed_surface(metadata_overrides={"vol_type": vol_type})

    assert surface.identity.vol_type == vol_type
    assert surface.volatility_unit is None


@pytest.mark.parametrize("decision", ["pending", "rejected"])
def test_a_capture_nobody_confirmed_cannot_become_a_surface(decision: str) -> None:
    pending = VCUBATMCapture(
        provenance=capture_provenance(), metadata=metadata(), grid=grid()
    )
    capture = (
        pending
        if decision == "pending"
        else pending.reject(reviewed_by="Eddy", reviewed_at=CONFIRMED_AT)
    )

    with pytest.raises(UnconfirmedCaptureError, match="only a CONFIRMED capture"):
        canonical_surface_from_confirmed_capture(capture, capture_id=CAPTURE_ID)


def test_a_blocked_capture_never_reaches_the_canonical_model_at_all() -> None:
    """It cannot be confirmed, so it can never be offered to the store."""

    blocked = VCUBATMCapture(
        provenance=capture_provenance(),
        metadata=metadata(),
        grid=grid(),
        blocking_errors=(VCUBCaptureIssue(code="TENOR_HEADERS_UNRESOLVED", message="no headers"),),
    )

    assert not blocked.can_confirm
    with pytest.raises(UnconfirmedCaptureError):
        canonical_surface_from_confirmed_capture(blocked, capture_id=CAPTURE_ID)


def test_a_capture_from_another_screen_is_not_filed_as_an_atm_swaption_surface() -> None:
    capture = VCUBATMCapture(
        provenance=capture_provenance(),
        metadata=VCUBSourceMetadata(
            currency="USD",
            curve_config="USD RFR BVOL Cube (Default)",
            side="Mid",
            quote_date="08/18/26",
            tab="OTM Swaptions",
            vol_type="Normal Vol (OIS)",
            source="BVOL",
        ),
        grid=grid(),
    ).confirm(reviewed_by="Eddy", reviewed_at=CONFIRMED_AT)

    with pytest.raises(UnconfirmedCaptureError, match="ATM Swaptions"):
        canonical_surface_from_confirmed_capture(capture, capture_id=CAPTURE_ID)


# -- identity ---------------------------------------------------------------


def test_the_same_identity_always_derives_the_same_surface_id() -> None:
    """Stable across processes: it is what makes a retry recognisable at all."""

    assert confirmed_surface().surface_id == confirmed_surface().surface_id


@pytest.mark.parametrize(
    "override",
    [
        {"currency": "EUR"},
        {"side": "Bid"},
        {"quote_date": "08/19/26"},
        {"vol_type": "Black Vol"},
        {"source": "CMPN"},
        {"curve_config": "USD RFR BVOL Cube (Alt)"},
    ],
)
def test_a_different_identity_field_is_a_different_surface(override: dict) -> None:
    assert confirmed_surface(metadata_overrides=override).surface_id != (
        confirmed_surface().surface_id
    )


def test_an_unresolved_field_is_its_own_identity_never_a_stand_in() -> None:
    """"Source unknown" and "source BVOL" are different surfaces, not the same one."""

    assert confirmed_surface(metadata_overrides={"source": None}).surface_id != (
        confirmed_surface().surface_id
    )


def test_a_different_capture_is_a_different_surface_even_with_identical_metadata() -> None:
    """Eddy's PR #184 decision #1.

    A stored surface is one capture, not the only surface a business date
    may hold. A second capture of the same screen -- same currency, curve,
    side, vol type, source, business date -- is a new observation, not an
    illegal replacement of the first.
    """

    assert confirmed_surface(capture_id="a-different-capture-id").surface_id != (
        confirmed_surface().surface_id
    )


def test_the_surface_id_ignores_provenance_and_values_but_not_the_snapshot() -> None:
    """Identity is *which* surface this is; content is what it says.

    ``capture_id`` is the one exception, and it is not really an exception:
    it is the snapshot dimension of the identity itself (Eddy's PR #184
    decision #1), not ordinary provenance -- covered separately above. Held
    fixed here so this test isolates the fields that really are ignored.
    """

    other = confirmed_surface(
        reviewed_by="Someone Else",
        provenance_overrides={"source_image_sha256": "b" * 64},
        unresolved_cells=frozenset({(0, 0)}),
    )

    assert other.surface_id == confirmed_surface().surface_id


def test_the_content_fingerprint_moves_with_anything_the_surface_asserts() -> None:
    baseline = confirmed_surface().content_fingerprint

    assert confirmed_surface().content_fingerprint == baseline
    assert confirmed_surface(unresolved_cells=frozenset({(0, 0)})).content_fingerprint != baseline
    assert confirmed_surface(reviewed_by="Someone Else").content_fingerprint != baseline
    assert (
        confirmed_surface(
            provenance_overrides={"source_image_sha256": "b" * 64}
        ).content_fingerprint
        != baseline
    )


def test_an_identity_field_cannot_be_both_valued_and_unresolved() -> None:
    with pytest.raises(ValueError, match="listed unresolved but carries a value"):
        VolSurfaceIdentity(
            surface_type=VolSurfaceType.ATM_SWAPTION,
            capture_id=CAPTURE_ID,
            currency="USD",
            unresolved_fields=("currency", "business_date", "curve_config", "side",
                               "vol_type", "source"),
        )


def test_an_identity_field_left_none_must_be_named_unresolved() -> None:
    with pytest.raises(ValueError, match="currency is unresolved"):
        VolSurfaceIdentity(
            surface_type=VolSurfaceType.ATM_SWAPTION,
            capture_id=CAPTURE_ID,
            business_date="08/18/26",
            curve_config="cube",
            side="Mid",
            vol_type="Normal Vol (OIS)",
            source="BVOL",
        )


@pytest.mark.parametrize("capture_id", ["", "   "])
def test_a_blank_capture_id_is_refused(capture_id: str) -> None:
    with pytest.raises(ValueError, match="capture_id must be a non-blank string"):
        VolSurfaceIdentity(surface_type=VolSurfaceType.ATM_SWAPTION, capture_id=capture_id)


# -- point and surface invariants -------------------------------------------


def test_an_integer_volatility_is_normalised_to_a_float() -> None:
    """Codex review, PR #184.

    ``_require_finite`` accepts an ``int``, and keeping it one made the point
    serialise as JSON ``80`` while the same point reloaded from SQLite's REAL
    column serialised as ``80.0`` -- so a surface saved and fetched back
    conflicted with itself on the next save.
    """

    point = VolSurfacePoint(expiry="3Mo", underlying_tenor="4Yr", volatility=80)

    assert isinstance(point.volatility, float)
    assert point.to_dict()["volatility"] == 80.0


def test_a_negative_zero_volatility_stays_negative_in_the_model() -> None:
    """The capture slice treats -0.00 and 0.00 as different readings (PR #182)."""

    point = VolSurfacePoint(expiry="3Mo", underlying_tenor="4Yr", volatility=-0.0)

    assert math.copysign(1.0, point.volatility) < 0
    assert json.dumps(point.to_dict()["volatility"]) == "-0.0"


def test_a_surface_differing_only_in_signed_zero_has_its_own_fingerprint() -> None:
    """Dataclass equality calls these the same; what the trader saw does not."""

    base = confirmed_surface()
    negative = CanonicalVolSurface(
        identity=base.identity,
        provenance=base.provenance,
        points=(
            VolSurfacePoint(
                expiry=EXPIRY_LABELS[0], underlying_tenor=TENOR_LABELS[0], volatility=-0.0
            ),
        )
        + base.points[1:],
    )
    positive = CanonicalVolSurface(
        identity=base.identity,
        provenance=base.provenance,
        points=(
            VolSurfacePoint(
                expiry=EXPIRY_LABELS[0], underlying_tenor=TENOR_LABELS[0], volatility=0.0
            ),
        )
        + base.points[1:],
    )

    assert negative == positive  # Python says so; the fingerprint must not
    assert negative.content_fingerprint != positive.content_fingerprint


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_volatility_is_refused(value: float) -> None:
    with pytest.raises(ValueError, match="volatility must be a finite number"):
        VolSurfacePoint(expiry="3Mo", underlying_tenor="4Yr", volatility=value)


def test_an_atm_point_may_not_carry_a_strike_offset() -> None:
    with pytest.raises(ValueError, match="an ATM point carries no strike offset"):
        VolSurfacePoint(
            expiry="3Mo", underlying_tenor="4Yr", volatility=84.99, strike_offset=25.0
        )


def test_a_surface_may_not_repeat_a_coordinate() -> None:
    point = VolSurfacePoint(expiry="3Mo", underlying_tenor="4Yr", volatility=84.99)

    with pytest.raises(ValueError, match="points repeat a coordinate"):
        CanonicalVolSurface(
            identity=confirmed_surface().identity,
            provenance=confirmed_surface().provenance,
            points=(point, point),
        )


def test_a_surface_cannot_be_built_without_a_confirmer_and_a_time() -> None:
    """Only a confirmed capture can become canonical data, structurally."""

    with pytest.raises(ValueError, match="confirmed_by"):
        VolSurfaceProvenance(
            capture_id=CAPTURE_ID,
            source_reference="vcub.png",
            source_image_sha256="a" * 64,
            source_image_bytes=4096,
            captured_at=CAPTURED_AT,
            parser_name=PARSER_NAME,
            parser_version=PARSER_VERSION,
            confirmed_by="  ",
            confirmed_at=CONFIRMED_AT,
        )


def test_a_surface_cannot_disagree_with_itself_about_which_capture_it_is() -> None:
    """Codex review, PR #184.

    The store keeps one ``capture_id`` column, so a surface whose identity
    and provenance named different captures would save under a
    ``surface_id`` derived from one value and reload with the other --
    mismatching the fingerprint it had just stored and becoming unreadable
    the moment it was saved. Refused at construction instead.
    """

    base = confirmed_surface()
    mismatched_identity = VolSurfaceIdentity(
        surface_type=base.identity.surface_type,
        capture_id="a-different-capture-id",
        business_date=base.identity.business_date,
        currency=base.identity.currency,
        curve_config=base.identity.curve_config,
        side=base.identity.side,
        vol_type=base.identity.vol_type,
        source=base.identity.source,
    )

    with pytest.raises(ValueError, match="identity.capture_id and provenance.capture_id"):
        CanonicalVolSurface(
            identity=mismatched_identity, provenance=base.provenance, points=base.points
        )


# -- exports ----------------------------------------------------------------


def test_the_csv_export_has_one_row_per_point_under_the_contracted_columns() -> None:
    rows = list(csv.DictReader(io.StringIO(export_surface_as_csv(confirmed_surface()))))

    assert len(rows) == POINT_COUNT
    assert tuple(rows[0]) == EXPORT_COLUMNS


def test_the_csv_export_column_names_are_the_contract_itself() -> None:
    """Pinned literally: a rename breaks every audit script written against it.

    Issue #185's two columns sit at the end for the same reason: a script
    reading the first twenty-two by position still reads exactly what it did.
    """

    assert EXPORT_COLUMNS == (
        "surface_id",
        "surface_type",
        "business_date",
        "currency",
        "curve_config",
        "side",
        "vol_type",
        "source",
        "expiry",
        "underlying_tenor",
        "strike_dimension",
        "strike_offset",
        "volatility",
        "volatility_unit",
        "capture_id",
        "source_reference",
        "source_image_sha256",
        "captured_at",
        "parser_name",
        "parser_version",
        "confirmed_by",
        "confirmed_at",
        "value_kind",
        "source_image_count",
    )


def test_an_unresolved_value_exports_as_an_empty_cell_never_as_a_number() -> None:
    surface = confirmed_surface(
        metadata_overrides={"source": None}, unresolved_cells=frozenset({(0, 0)})
    )

    rows = list(csv.DictReader(io.StringIO(export_surface_as_csv(surface))))

    assert rows[0]["volatility"] == ""
    assert rows[0]["source"] == ""
    # The unit is not one of the empty cells: this capture's ``Normal Vol
    # (OIS)`` type states it. An empty cell means "nobody stated this",
    # which is the distinction this test is about.
    assert rows[0]["volatility_unit"] == "bp"
    assert rows[1]["volatility"] == str(synthetic_value(0, 1))


def test_the_json_export_carries_the_record_and_the_same_flat_rows() -> None:
    surface = confirmed_surface()

    payload = json.loads(export_surface_as_json(surface))

    assert payload["columns"] == list(EXPORT_COLUMNS)
    assert payload["surface"] == surface.to_dict()
    assert len(payload["rows"]) == POINT_COUNT
    assert payload["rows"][0]["volatility"] == synthetic_value(0, 0)
    assert payload["rows"][0]["surface_id"] == surface.surface_id


def test_every_export_row_repeats_the_identity_and_provenance_it_belongs_to() -> None:
    surface = confirmed_surface()

    rows = export_rows(surface)

    assert {row["surface_id"] for row in rows} == {surface.surface_id}
    assert {row["confirmed_by"] for row in rows} == {"Eddy"}
    assert {row["source_image_sha256"] for row in rows} == {"a" * 64}
