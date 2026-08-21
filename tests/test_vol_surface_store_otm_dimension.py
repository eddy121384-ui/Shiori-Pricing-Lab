"""The canonical store's third coordinate, and what Issue #185 promised not
to break.

Two halves:

* an OTM/SABR surface -- ``Term x Tenor x strike offset``, an ATM column of
  absolute vols beside eight columns of spreads, and several source images --
  survives a save, a restart, and a fetch with every coordinate intact;
* a database written by PR #184, before any of that existed, is still read
  exactly as it was stored.

The second half is written against the *literal* PR #184 table shape (see
:data:`_PR184_SCHEMA`) rather than against today's, because "the old shape
still reads" is not a claim today's schema can make on its own behalf.

Every fixture is synthetic. No live Bloomberg value appears in this
repository.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from test_bloomberg_vcub_otm_template import (
    ROW_LABELS,
    SLICE_A,
    SLICE_B,
    SLICE_C,
    STRIKE_LABELS,
    _synthetic_value,
    _three_slices,
    read,
    screenshot_tokens,
)
from test_vol_surface import confirmed_surface

from shiori_pricing_lab.data.bloomberg_vcub_otm_template import merge_vcub_otm_reads
from shiori_pricing_lab.data.vcub_vol_surface_adapter import (
    UnconfirmedCaptureError,
    canonical_surface_from_confirmed_otm_capture,
)
from shiori_pricing_lab.data.vol_surface import (
    StrikeDimension,
    VolSurfaceType,
    VolValueKind,
    export_rows,
)
from shiori_pricing_lab.data.vol_surface_store import (
    SCHEMA_VERSION,
    SaveStatus,
    VolSurfaceConflictError,
    VolSurfaceIntegrityError,
    VolSurfaceStore,
)

CONFIRMED_AT = "2026-08-21T09:31:00Z"
OTM_CAPTURE_ID = "fedcba9876543210fedcba9876543210"


def otm_surface(*, capture_id: str = OTM_CAPTURE_ID, reads=None):
    capture = merge_vcub_otm_reads(reads or _three_slices()).confirm(
        reviewed_by="Eddy", reviewed_at=CONFIRMED_AT
    )
    return canonical_surface_from_confirmed_otm_capture(capture, capture_id=capture_id)


@pytest.fixture()
def store(tmp_path) -> VolSurfaceStore:
    return VolSurfaceStore(tmp_path / "vol_surfaces.sqlite3")


# ---------------------------------------------------------------------------
# The adapter: what a confirmed OTM capture becomes
# ---------------------------------------------------------------------------


def test_every_coordinate_of_the_reviewed_table_becomes_a_point() -> None:
    surface = otm_surface()

    assert surface.identity.surface_type is VolSurfaceType.OTM_SWAPTION_SABR
    assert len(surface.points) == len(ROW_LABELS) * len(STRIKE_LABELS)
    by_coordinate = {
        (point.expiry, point.underlying_tenor, point.strike_dimension, point.strike_offset)
        for point in surface.points
    }
    assert len(by_coordinate) == len(surface.points)


def test_the_atm_column_is_an_absolute_vol_and_the_others_are_spreads() -> None:
    surface = otm_surface()

    atm = [point for point in surface.points if point.strike_dimension is StrikeDimension.ATM]
    skew = [
        point
        for point in surface.points
        if point.strike_dimension is StrikeDimension.YIELD_OFFSET_BP
    ]

    assert len(atm) == len(ROW_LABELS)
    assert len(skew) == len(ROW_LABELS) * (len(STRIKE_LABELS) - 1)
    assert {point.value_kind for point in atm} == {VolValueKind.ABSOLUTE_VOL}
    assert {point.value_kind for point in skew} == {VolValueKind.SPREAD_TO_ATM}
    assert {point.strike_offset for point in atm} == {None}
    assert {point.strike_offset for point in skew} == {
        -200.0,
        -100.0,
        -50.0,
        -25.0,
        25.0,
        50.0,
        100.0,
        200.0,
    }


def test_the_strike_offset_is_the_basis_point_number_the_screen_stated() -> None:
    surface = otm_surface()

    first_row = [
        point
        for point in surface.points
        if (point.expiry, point.underlying_tenor) == ROW_LABELS[0]
    ]
    by_offset = {point.strike_offset: point.volatility for point in first_row}
    for column_index, label in enumerate(STRIKE_LABELS):
        offset = None if label == "ATM" else float(label.replace("bps", ""))
        assert by_offset[offset] == pytest.approx(_synthetic_value(0, column_index))


def test_a_capture_sourced_otm_surface_never_asserts_a_volatility_unit() -> None:
    assert otm_surface().volatility_unit is None


def test_every_screenshot_of_the_session_reaches_the_provenance() -> None:
    surface = otm_surface()

    assert [image.source_reference for image in surface.provenance.source_images] == [
        "shot-a.png",
        "shot-b.png",
        "shot-c.png",
    ]
    assert len({image.sha256 for image in surface.provenance.source_images}) == 3
    # The single-image fields describe the first of them, never stand in for
    # the set.
    assert surface.provenance.source_reference == "shot-a.png"


def test_an_unconfirmed_otm_capture_cannot_become_canonical_data() -> None:
    pending = merge_vcub_otm_reads(_three_slices())

    with pytest.raises(UnconfirmedCaptureError):
        canonical_surface_from_confirmed_otm_capture(pending, capture_id=OTM_CAPTURE_ID)


def test_the_export_says_which_kind_each_number_is_and_how_many_images() -> None:
    rows = export_rows(otm_surface())

    kinds = {(row["strike_dimension"], row["value_kind"]) for row in rows}
    assert kinds == {("ATM", "ABSOLUTE_VOL"), ("YIELD_OFFSET_BP", "SPREAD_TO_ATM")}
    assert {row["source_image_count"] for row in rows} == {3}


# ---------------------------------------------------------------------------
# The store: durability with the third coordinate
# ---------------------------------------------------------------------------


def test_a_saved_otm_surface_reloads_with_every_coordinate_intact(tmp_path) -> None:
    """A restart is a new store object against the same file."""

    database = tmp_path / "vol_surfaces.sqlite3"
    surface = otm_surface()
    outcome = VolSurfaceStore(database).save_confirmed_surface(surface)
    assert outcome.status is SaveStatus.SAVED

    reloaded = VolSurfaceStore(database).fetch_surface(outcome.surface_id)

    assert reloaded.to_dict() == surface.to_dict()
    assert [point.value_kind for point in reloaded.points] == [
        point.value_kind for point in surface.points
    ]
    assert [image.to_dict() for image in reloaded.provenance.source_images] == [
        image.to_dict() for image in surface.provenance.source_images
    ]


def test_an_exact_retry_of_one_multi_image_capture_is_idempotent(store) -> None:
    surface = otm_surface()
    first = store.save_confirmed_surface(surface)

    second = store.save_confirmed_surface(surface)

    assert first.status is SaveStatus.SAVED
    assert second.status is SaveStatus.ALREADY_SAVED
    assert second.surface_id == first.surface_id


def test_the_same_capture_with_different_content_fails_closed(store) -> None:
    store.save_confirmed_surface(otm_surface())

    # The same three covering screenshots, one cell of the last one reading
    # differently -- a complete surface, so only its *content* differs.
    changed = otm_surface(
        reads=[
            read(screenshot_tokens(rows=SLICE_A), "shot-a.png", "a"),
            read(screenshot_tokens(rows=SLICE_B), "shot-b.png", "b"),
            read(
                screenshot_tokens(
                    rows=SLICE_C, value_overrides={(len(ROW_LABELS) - 1, 8): "1.23"}
                ),
                "shot-c.png",
                "c",
            ),
        ]
    )

    with pytest.raises(VolSurfaceConflictError):
        store.save_confirmed_surface(changed)


def test_a_second_capture_of_the_same_screen_is_a_second_surface(store) -> None:
    first = store.save_confirmed_surface(otm_surface())

    second = store.save_confirmed_surface(otm_surface(capture_id="1" * 32))

    assert second.status is SaveStatus.SAVED
    assert second.surface_id != first.surface_id
    assert len(store.list_surfaces()) == 2


def test_a_lost_source_image_row_is_caught_as_integrity_drift(store, tmp_path) -> None:
    outcome = store.save_confirmed_surface(otm_surface())

    with sqlite3.connect(tmp_path / "vol_surfaces.sqlite3") as connection:
        connection.execute(
            "DELETE FROM vol_surface_source_image WHERE surface_id = ? AND image_index = 2",
            (outcome.surface_id,),
        )

    with pytest.raises(VolSurfaceIntegrityError):
        store.fetch_surface(outcome.surface_id)


def test_an_edited_value_kind_is_caught_as_integrity_drift(store, tmp_path) -> None:
    outcome = store.save_confirmed_surface(otm_surface())

    with sqlite3.connect(tmp_path / "vol_surfaces.sqlite3") as connection:
        connection.execute(
            "UPDATE vol_surface_point SET value_kind = 'ABSOLUTE_VOL' "
            "WHERE surface_id = ? AND point_index = 0",
            (outcome.surface_id,),
        )

    with pytest.raises(VolSurfaceIntegrityError):
        store.fetch_surface(outcome.surface_id)


def test_an_atm_surface_and_an_otm_surface_share_one_store(store) -> None:
    atm = store.save_confirmed_surface(confirmed_surface())
    otm = store.save_confirmed_surface(otm_surface())

    types = {summary.surface_type for summary in store.list_surfaces()}
    assert types == {VolSurfaceType.ATM_SWAPTION, VolSurfaceType.OTM_SWAPTION_SABR}
    assert store.fetch_surface(atm.surface_id).identity.surface_type is (
        VolSurfaceType.ATM_SWAPTION
    )
    assert store.fetch_surface(otm.surface_id).identity.surface_type is (
        VolSurfaceType.OTM_SWAPTION_SABR
    )


# ---------------------------------------------------------------------------
# What PR #184 already stored must still read
# ---------------------------------------------------------------------------

#: The ``vol_surface_point`` table exactly as PR #184 created it: no
#: ``value_kind`` column, and no ``vol_surface_source_image`` table beside it.
_PR184_SCHEMA = (
    """
    CREATE TABLE schema_version (
        id      INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE vol_surface (
        surface_id          TEXT    NOT NULL PRIMARY KEY,
        surface_type        TEXT    NOT NULL,
        business_date       TEXT,
        currency            TEXT,
        curve_config        TEXT,
        side                TEXT,
        vol_type            TEXT,
        source              TEXT,
        unresolved_fields   TEXT    NOT NULL,
        volatility_unit     TEXT,
        capture_id          TEXT    NOT NULL,
        source_reference    TEXT    NOT NULL,
        source_image_sha256 TEXT    NOT NULL,
        source_image_bytes  INTEGER NOT NULL,
        captured_at         TEXT    NOT NULL,
        parser_name         TEXT    NOT NULL,
        parser_version      TEXT    NOT NULL,
        confirmed_by        TEXT    NOT NULL,
        confirmed_at        TEXT    NOT NULL,
        content_fingerprint TEXT    NOT NULL,
        saved_at            TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE vol_surface_point (
        surface_id       TEXT    NOT NULL REFERENCES vol_surface(surface_id),
        point_index      INTEGER NOT NULL,
        expiry           TEXT    NOT NULL,
        underlying_tenor TEXT    NOT NULL,
        strike_dimension TEXT    NOT NULL,
        strike_offset    REAL,
        volatility       REAL,
        volatility_sign  INTEGER NOT NULL DEFAULT 1 CHECK (volatility_sign IN (-1, 1)),
        PRIMARY KEY (surface_id, point_index)
    )
    """,
)


def write_pr184_database(path, surface) -> str:
    """Store ``surface`` the way PR #184's build would have stored it."""

    identity = surface.identity
    provenance = surface.provenance
    connection = sqlite3.connect(path)
    try:
        for statement in _PR184_SCHEMA:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_version (id, version) VALUES (1, ?)", (SCHEMA_VERSION,)
        )
        connection.execute(
            "INSERT INTO vol_surface VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?)",
            (
                surface.surface_id,
                identity.surface_type.value,
                identity.business_date,
                identity.currency,
                identity.curve_config,
                identity.side,
                identity.vol_type,
                identity.source,
                json.dumps(list(identity.unresolved_fields)),
                surface.volatility_unit,
                provenance.capture_id,
                provenance.source_reference,
                provenance.source_image_sha256,
                provenance.source_image_bytes,
                provenance.captured_at,
                provenance.parser_name,
                provenance.parser_version,
                provenance.confirmed_by,
                provenance.confirmed_at,
                surface.content_fingerprint,
                "2026-08-18T09:41:00Z",
            ),
        )
        connection.executemany(
            "INSERT INTO vol_surface_point (surface_id, point_index, expiry, "
            "underlying_tenor, strike_dimension, strike_offset, volatility, volatility_sign) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    surface.surface_id,
                    index,
                    point.expiry,
                    point.underlying_tenor,
                    point.strike_dimension.value,
                    point.strike_offset,
                    point.volatility,
                    1,
                )
                for index, point in enumerate(surface.points)
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return surface.surface_id


def test_an_atm_surface_stored_by_pr_184_still_reads_unchanged(tmp_path) -> None:
    """The fingerprint it was stored with is the one this build computes."""

    database = tmp_path / "pr184.sqlite3"
    surface = confirmed_surface()
    surface_id = write_pr184_database(database, surface)

    reloaded = VolSurfaceStore(database).fetch_surface(surface_id)

    assert reloaded.to_dict() == surface.to_dict()
    assert {point.value_kind for point in reloaded.points} == {VolValueKind.ABSOLUTE_VOL}
    assert [image.to_dict() for image in reloaded.provenance.source_images] == [
        {
            "source_reference": surface.provenance.source_reference,
            "sha256": surface.provenance.source_image_sha256,
            "size_bytes": surface.provenance.source_image_bytes,
        }
    ]


def test_a_pr_184_database_takes_an_otm_surface_without_being_rebuilt(tmp_path) -> None:
    """The new column and table are added to it, and its own row still reads."""

    database = tmp_path / "pr184.sqlite3"
    atm = confirmed_surface()
    atm_id = write_pr184_database(database, atm)

    store = VolSurfaceStore(database)
    outcome = store.save_confirmed_surface(otm_surface())

    assert outcome.status is SaveStatus.SAVED
    assert store.fetch_surface(atm_id).to_dict() == atm.to_dict()
    assert store.fetch_surface(outcome.surface_id).provenance.source_images[2].sha256 == (
        "c" * 64
    )


def test_re_saving_a_pr_184_surface_is_still_recognised_as_the_same_save(tmp_path) -> None:
    database = tmp_path / "pr184.sqlite3"
    surface = confirmed_surface()
    write_pr184_database(database, surface)

    outcome = VolSurfaceStore(database).save_confirmed_surface(surface)

    assert outcome.status is SaveStatus.ALREADY_SAVED
