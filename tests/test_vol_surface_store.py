"""The local SQLite store for confirmed vol surfaces (Issue #183).

Every test here writes to a ``tmp_path`` database and throws it away. No
test touches the workbench's real store, and no live Bloomberg value appears
in any fixture -- the surfaces come from ``test_vol_surface``'s synthetic
21x15 grid.

The point of this file is durability with no silent loss: a confirmed
surface survives a restart intact, an exact retry is not a duplicate, a
conflicting observation is refused rather than overwritten, and a failed
write leaves nothing behind.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading

import pytest
from test_vol_surface import (
    CAPTURE_ID,
    EXPIRY_LABELS,
    POINT_COUNT,
    TENOR_LABELS,
    confirmed_surface,
    synthetic_value,
)

import shiori_pricing_lab.data.vol_surface_store as store_module
from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    VolSurfacePoint,
    VolSurfaceType,
    VolValueKind,
)
from shiori_pricing_lab.data.vol_surface_store import (
    _ADDITIVE_COLUMNS,
    _READ_ONLY_COLUMN_DEFAULTS,
    DEFAULT_DATABASE_PATH,
    SCHEMA_VERSION,
    SaveStatus,
    VolSurfaceConflictError,
    VolSurfaceIntegrityError,
    VolSurfaceSchemaError,
    VolSurfaceStore,
    VolSurfaceStoreError,
    default_database_path,
)


@pytest.fixture()
def database_path(tmp_path):
    return tmp_path / "vol_surfaces.sqlite3"


@pytest.fixture()
def store(database_path) -> VolSurfaceStore:
    return VolSurfaceStore(database_path)


class _RefusesToWritePoints(sqlite3.Connection):
    """A real connection whose bulk insert fails, and nothing else.

    The points are the only bulk insert the store performs, so this fails
    the write exactly where a partial surface would appear: after the
    surface row has claimed its primary key inside the same transaction.
    """

    def executemany(self, *args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")


def break_point_writes(monkeypatch) -> None:
    """Make every write from now on fail partway through its transaction.

    Called rather than taken as a fixture so a test can decide *when* the
    breakage starts -- one below needs a healthy save first. ``monkeypatch``
    undoes it, and a test that must read the store back afterwards calls
    ``monkeypatch.undo()`` itself.
    """

    real_connect = sqlite3.connect

    def failing_connect(*args, **kwargs):
        kwargs.pop("factory", None)
        return real_connect(*args, factory=_RefusesToWritePoints, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", failing_connect)


# -- saving -----------------------------------------------------------------


def test_a_confirmed_surface_is_saved_with_every_one_of_its_315_points(store) -> None:
    surface = confirmed_surface()

    outcome = store.save_confirmed_surface(surface)

    assert outcome.status is SaveStatus.SAVED
    assert outcome.surface_id == surface.surface_id
    assert outcome.point_count == POINT_COUNT == 315


def test_constructing_the_store_touches_no_disk(database_path) -> None:
    """Importing the workbench must not create a database nobody asked for."""

    VolSurfaceStore(database_path)

    assert not database_path.exists()


def test_a_saved_surface_reloads_intact_from_a_brand_new_store(database_path) -> None:
    """A restart is exactly this: a different object over the same file."""

    surface = confirmed_surface(unresolved_cells=frozenset({(4, 5)}))
    VolSurfaceStore(database_path).save_confirmed_surface(surface)

    reloaded = VolSurfaceStore(database_path).fetch_surface(surface.surface_id)

    assert reloaded == surface
    assert reloaded.content_fingerprint == surface.content_fingerprint


def test_every_point_reloads_at_exactly_the_coordinate_it_was_saved_at(store) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    points = store.fetch_points(surface.surface_id)

    assert len(points) == POINT_COUNT
    by_coordinate = {(point.expiry, point.underlying_tenor): point.volatility for point in points}
    for row_index, expiry in enumerate(EXPIRY_LABELS):
        for column_index, tenor in enumerate(TENOR_LABELS):
            assert by_coordinate[(expiry, tenor)] == synthetic_value(row_index, column_index)


def test_the_stored_axis_order_is_the_order_the_trader_reviewed(store) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    assert store.fetch_points(surface.surface_id) == surface.points


def test_an_unresolved_cell_reloads_as_unresolved_never_as_zero(store) -> None:
    surface = confirmed_surface(unresolved_cells=frozenset({(2, 3)}))
    store.save_confirmed_surface(surface)

    points = store.fetch_points(surface.surface_id)

    unresolved = [point for point in points if point.volatility is None]
    assert [(point.expiry, point.underlying_tenor) for point in unresolved] == [
        (EXPIRY_LABELS[2], TENOR_LABELS[3])
    ]


def test_unresolved_metadata_reloads_exactly_as_it_was_reviewed(store) -> None:
    surface = confirmed_surface(metadata_overrides={"source": None, "side": None})
    store.save_confirmed_surface(surface)

    identity = store.fetch_surface(surface.surface_id).identity

    assert identity.source is None
    assert identity.side is None
    assert sorted(identity.unresolved_fields) == ["side", "source"]
    assert identity.currency == "USD"


def test_the_provenance_and_confirmation_survive_the_round_trip(store) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    provenance = store.fetch_surface(surface.surface_id).provenance

    assert provenance == surface.provenance
    assert provenance.capture_id == CAPTURE_ID
    assert provenance.source_image_sha256 == "a" * 64
    assert provenance.confirmed_by == "Eddy"


def test_the_screenshot_bytes_are_never_written_into_the_database(store, database_path) -> None:
    """Only the hash. The image stays operator-local evidence."""

    store.save_confirmed_surface(confirmed_surface())

    assert b"\x89PNG" not in database_path.read_bytes()


# -- values SQLite does not round-trip on its own ---------------------------


def _surface_with_first_volatility(value):
    """The canonical surface with its first point's vol replaced by ``value``."""

    base = confirmed_surface()
    return CanonicalVolSurface(
        identity=base.identity,
        provenance=base.provenance,
        points=(
            VolSurfacePoint(
                expiry=EXPIRY_LABELS[0], underlying_tenor=TENOR_LABELS[0], volatility=value
            ),
        )
        + base.points[1:],
    )


def test_a_negative_zero_volatility_reloads_negative(store) -> None:
    """Codex review, PR #184.

    SQLite's REAL column is the one place an IEEE double does not survive:
    ``-0.0`` comes back as ``0.0``. The capture slice treats ``-0.00`` and
    ``0.00`` as different readings, so losing the sign hands back a grid the
    trader never confirmed.
    """

    surface = _surface_with_first_volatility(-0.0)
    store.save_confirmed_surface(surface)

    reloaded = store.fetch_surface(surface.surface_id)

    assert math.copysign(1.0, reloaded.points[0].volatility) < 0
    assert reloaded.content_fingerprint == surface.content_fingerprint


@pytest.mark.parametrize("value", [-0.0, 0.0, 80, 84.99, -25.5])
def test_a_saved_surface_never_conflicts_with_its_own_reloaded_self(store, value) -> None:
    """The round trip has to be exact, or the retry path reads as a conflict."""

    surface = _surface_with_first_volatility(value)
    store.save_confirmed_surface(surface)

    reloaded = store.fetch_surface(surface.surface_id)

    assert store.save_confirmed_surface(reloaded).status is SaveStatus.ALREADY_SAVED


@pytest.mark.parametrize(
    "value",
    [
        1e-300,
        -1e-300,
        1.7976931348623157e308,
        0.1 + 0.2,
        84.99,
        -84.99,
        1234567.891011,
        5e-324,
    ],
)
def test_awkward_doubles_round_trip_bit_exact(store, value) -> None:
    """The integrity gate must never fire on a value the store itself wrote.

    SQLite REAL is an IEEE double, so everything except signed zero survives
    -- but that is the claim the gate now depends on, so it is asserted
    rather than assumed.
    """

    surface = _surface_with_first_volatility(value)
    store.save_confirmed_surface(surface)

    reloaded = store.fetch_surface(surface.surface_id)

    assert reloaded.points[0].volatility == value
    assert reloaded.content_fingerprint == surface.content_fingerprint


def test_a_positive_zero_is_not_quietly_turned_negative(store) -> None:
    """The sign column must carry the sign, not impose one."""

    surface = _surface_with_first_volatility(0.0)
    store.save_confirmed_surface(surface)

    assert math.copysign(1.0, store.fetch_points(surface.surface_id)[0].volatility) > 0


# -- integrity --------------------------------------------------------------


def test_a_surface_edited_under_the_store_is_refused_not_returned(
    store, database_path
) -> None:
    """Codex review, PR #184.

    A dropped point row leaves a smaller grid that is still a perfectly legal
    ``CanonicalVolSurface``, so nothing downstream would notice. The
    fingerprint stored beside it is what makes that detectable.
    """

    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM vol_surface_point WHERE surface_id = ? AND point_index = 7",
            (surface.surface_id,),
        )

    with pytest.raises(VolSurfaceIntegrityError, match="does not match the fingerprint"):
        store.fetch_surface(surface.surface_id)


def test_an_edited_volatility_is_refused_too(store, database_path) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE vol_surface_point SET volatility = 1.0 WHERE surface_id = ? "
            "AND point_index = 0",
            (surface.surface_id,),
        )

    with pytest.raises(VolSurfaceIntegrityError):
        store.fetch_points(surface.surface_id)


def test_a_drifted_surface_is_never_reported_as_already_saved(store, database_path) -> None:
    """Codex review round 2, PR #184.

    The already-stored branch consulted only the surface row's own
    fingerprint, which a deleted point row leaves untouched. So a retry
    answered ``ALREADY_SAVED`` -- and the page said "Confirmed & saved" --
    while fetching that very surface raised.
    """

    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM vol_surface_point WHERE surface_id = ? AND point_index = 7",
            (surface.surface_id,),
        )

    with pytest.raises(VolSurfaceIntegrityError):
        store.save_confirmed_surface(surface)


def test_a_drifted_surface_is_reported_as_drift_not_as_a_conflict(store, database_path) -> None:
    """The stored rows are verified before either verdict is reached.

    Otherwise a corrupt store blames the incoming capture, which may be
    perfectly right.
    """

    stored = confirmed_surface()
    store.save_confirmed_surface(stored)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE vol_surface_point SET volatility = 1.0 WHERE surface_id = ? "
            "AND point_index = 0",
            (stored.surface_id,),
        )

    with pytest.raises(VolSurfaceIntegrityError):
        store.save_confirmed_surface(confirmed_surface(unresolved_cells=frozenset({(0, 0)})))


def test_a_database_from_the_previous_schema_fails_closed(database_path) -> None:
    """Codex review round 2, PR #184.

    Schema 1 -- an earlier commit on this branch -- has no
    ``volatility_sign`` column, and ``CREATE TABLE IF NOT EXISTS`` would
    leave it that way. Without the version moving with the shape, the next
    save failed on a missing column instead of failing closed here.
    """

    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version (version) VALUES (1)")

    with pytest.raises(VolSurfaceSchemaError, match="schema version 1"):
        VolSurfaceStore(database_path).list_surfaces()


def test_a_database_from_schema_version_2_also_fails_closed(database_path) -> None:
    """Eddy's PR #184 decision #1 bumped the schema again, 2 -> 3.

    ``capture_id`` joined the identity fields that ``surface_id`` and
    ``content_fingerprint`` hash. A version-2 database's stored fingerprints
    were computed without it, so this build's recomputed fingerprint would
    mismatch every row -- not because the data drifted, but because the
    formula did. Refusing the whole database at the schema gate is the
    honest failure; a false ``VolSurfaceIntegrityError`` on perfectly good
    rows would not be.
    """

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE schema_version (id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1), "
            "version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version (id, version) VALUES (1, 2)")

    with pytest.raises(VolSurfaceSchemaError, match="schema version 2"):
        VolSurfaceStore(database_path).list_surfaces()


def test_the_schema_version_moved_with_the_snapshot_dimension() -> None:
    """Pinned literally: the two must never drift apart again."""

    assert SCHEMA_VERSION == 3


def test_an_untouched_surface_passes_the_integrity_check_every_time(store) -> None:
    """The gate must not fire on the ordinary path."""

    surface = confirmed_surface(unresolved_cells=frozenset({(0, 0), (20, 14)}))
    store.save_confirmed_surface(surface)

    assert store.fetch_surface(surface.surface_id) == surface
    assert store.fetch_surface(surface.surface_id).content_fingerprint == (
        surface.content_fingerprint
    )


# -- schema initialisation --------------------------------------------------


def test_only_one_schema_version_row_can_ever_exist(store, database_path) -> None:
    """Codex review, PR #184.

    Two processes opening a brand-new database could both find the version
    table empty and both insert, leaving the fail-closed gate checking an
    arbitrary row of two.
    """

    store.save_confirmed_surface(confirmed_surface())

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO schema_version (id, version) VALUES (1, ?)", (SCHEMA_VERSION + 1,)
            )


def test_concurrent_first_opens_agree_on_one_version(database_path) -> None:
    """Eight threads racing to initialise one brand-new database.

    Driven through the first *save* since Issue #194: a read no longer opens
    a database for writing, so saving is the only thing that can create one,
    and it is therefore the only place this race can still happen. Eight
    distinct captures rather than one, so the race under test is the version
    insert and not the duplicate policy.
    """

    barrier = threading.Barrier(8)
    failures: list[BaseException] = []

    def initialise(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            VolSurfaceStore(database_path).save_confirmed_surface(
                confirmed_surface(capture_id=f"{index:032x}")
            )
        except BaseException as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [threading.Thread(target=initialise, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert failures == []
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchall() == [
            (SCHEMA_VERSION,)
        ]
    assert len(VolSurfaceStore(database_path).list_surfaces()) == 8


# -- duplicate policy -------------------------------------------------------


def test_saving_the_exact_same_surface_twice_is_an_idempotent_success(store) -> None:
    surface = confirmed_surface()

    first = store.save_confirmed_surface(surface)
    second = store.save_confirmed_surface(surface)

    assert first.status is SaveStatus.SAVED
    assert second.status is SaveStatus.ALREADY_SAVED
    assert second.surface_id == first.surface_id


def test_an_exact_retry_leaves_exactly_one_surface_and_one_set_of_points(
    store, database_path
) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    store.save_confirmed_surface(surface)

    assert len(store.list_surfaces()) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vol_surface_point").fetchone()[0] == (
            POINT_COUNT
        )


def test_an_exact_retry_after_a_restart_is_still_recognised(database_path) -> None:
    """The fingerprint is derived from the data, so it survives the process."""

    surface = confirmed_surface()
    VolSurfaceStore(database_path).save_confirmed_surface(surface)

    outcome = VolSurfaceStore(database_path).save_confirmed_surface(surface)

    assert outcome.status is SaveStatus.ALREADY_SAVED


def test_the_same_identity_with_different_values_is_refused_not_overwritten(store) -> None:
    stored = confirmed_surface()
    store.save_confirmed_surface(stored)
    conflicting = confirmed_surface(unresolved_cells=frozenset({(0, 0)}))
    assert conflicting.surface_id == stored.surface_id

    with pytest.raises(VolSurfaceConflictError, match="already stored with different content"):
        store.save_confirmed_surface(conflicting)

    assert store.fetch_surface(stored.surface_id) == stored


def test_the_same_identity_from_a_different_screenshot_is_also_a_conflict(store) -> None:
    """Same numbers, different evidence: which provenance is true is not ours to pick."""

    stored = confirmed_surface()
    store.save_confirmed_surface(stored)

    with pytest.raises(VolSurfaceConflictError):
        store.save_confirmed_surface(
            confirmed_surface(provenance_overrides={"source_image_sha256": "b" * 64})
        )

    assert store.fetch_surface(stored.surface_id).provenance == stored.provenance


def test_a_rejected_conflict_leaves_the_stored_points_untouched(store, database_path) -> None:
    stored = confirmed_surface()
    store.save_confirmed_surface(stored)

    with pytest.raises(VolSurfaceConflictError):
        store.save_confirmed_surface(confirmed_surface(unresolved_cells=frozenset({(0, 0)})))

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vol_surface_point").fetchone()[0] == (
            POINT_COUNT
        )
    assert store.fetch_points(stored.surface_id) == stored.points


def test_a_different_identity_is_a_separate_surface_not_a_conflict(store) -> None:
    first = confirmed_surface()
    second = confirmed_surface(metadata_overrides={"quote_date": "08/19/26"})

    store.save_confirmed_surface(first)
    store.save_confirmed_surface(second)

    assert {summary.surface_id for summary in store.list_surfaces()} == {
        first.surface_id,
        second.surface_id,
    }


def test_a_later_capture_the_same_day_is_a_new_surface_not_a_conflict(store) -> None:
    """Eddy's PR #184 decision #1.

    A canonical stored surface is one capture/market snapshot, not the only
    surface a business date is allowed to hold. Re-capturing the same VCUB
    screen later -- same currency, curve/config, side, vol type, source,
    business date, and even identical values -- must never be treated as an
    illegal replacement of the day's first surface.
    """

    morning = confirmed_surface(capture_id="capture-morning")
    afternoon = confirmed_surface(capture_id="capture-afternoon")
    assert morning.surface_id != afternoon.surface_id

    first = store.save_confirmed_surface(morning)
    second = store.save_confirmed_surface(afternoon)

    assert first.status is SaveStatus.SAVED
    assert second.status is SaveStatus.SAVED
    assert {summary.surface_id for summary in store.list_surfaces()} == {
        morning.surface_id,
        afternoon.surface_id,
    }
    assert store.fetch_surface(morning.surface_id).provenance.capture_id == "capture-morning"
    assert store.fetch_surface(afternoon.surface_id).provenance.capture_id == "capture-afternoon"


def test_retrying_the_same_capture_is_still_idempotent_under_the_snapshot_dimension(
    store,
) -> None:
    """The other half of decision #1: only the *same* snapshot can collide."""

    surface = confirmed_surface(capture_id="capture-once")

    first = store.save_confirmed_surface(surface)
    second = store.save_confirmed_surface(surface)

    assert first.status is SaveStatus.SAVED
    assert second.status is SaveStatus.ALREADY_SAVED
    assert len(store.list_surfaces()) == 1


def test_the_same_capture_with_conflicting_content_still_fails_closed(store) -> None:
    """The snapshot dimension narrows what conflicts; it does not remove conflict."""

    stored = confirmed_surface(capture_id="capture-once")
    store.save_confirmed_surface(stored)
    conflicting = confirmed_surface(
        capture_id="capture-once", unresolved_cells=frozenset({(0, 0)})
    )
    assert conflicting.surface_id == stored.surface_id

    with pytest.raises(VolSurfaceConflictError):
        store.save_confirmed_surface(conflicting)

    assert store.fetch_surface(stored.surface_id) == stored


def test_the_listing_shows_which_capture_each_same_day_surface_belongs_to(store) -> None:
    """A browse that cannot tell two same-day surfaces apart is dishonest."""

    store.save_confirmed_surface(confirmed_surface(capture_id="capture-morning"))
    store.save_confirmed_surface(confirmed_surface(capture_id="capture-afternoon"))

    assert {summary.capture_id for summary in store.list_surfaces()} == {
        "capture-morning",
        "capture-afternoon",
    }


# -- failure behaviour ------------------------------------------------------


def test_a_failed_points_write_leaves_no_partial_surface(store, monkeypatch) -> None:
    """The surface row is claimed first, so a mid-write failure must undo it too."""

    break_point_writes(monkeypatch)
    surface = confirmed_surface()

    with pytest.raises(VolSurfaceStoreError, match="refused the write"):
        store.save_confirmed_surface(surface)

    monkeypatch.undo()
    assert store.list_surfaces() == ()
    with pytest.raises(KeyError):
        store.fetch_surface(surface.surface_id)


def test_a_failed_write_leaves_both_tables_empty(store, database_path, monkeypatch) -> None:
    break_point_writes(monkeypatch)

    with pytest.raises(VolSurfaceStoreError):
        store.save_confirmed_surface(confirmed_surface())

    monkeypatch.undo()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vol_surface").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM vol_surface_point").fetchone()[0] == 0


def test_a_failed_write_never_disturbs_a_surface_already_stored(store, monkeypatch) -> None:
    stored = confirmed_surface()
    store.save_confirmed_surface(stored)

    break_point_writes(monkeypatch)
    with pytest.raises(VolSurfaceStoreError):
        store.save_confirmed_surface(confirmed_surface(metadata_overrides={"currency": "EUR"}))

    monkeypatch.undo()
    assert [summary.surface_id for summary in store.list_surfaces()] == [stored.surface_id]
    assert store.fetch_surface(stored.surface_id) == stored


def test_fetching_a_surface_the_store_does_not_hold_is_an_error(store) -> None:
    with pytest.raises(KeyError, match="no vol surface is stored"):
        store.fetch_surface("0" * 32)


def test_a_database_from_a_future_schema_is_refused_rather_than_read(database_path) -> None:
    VolSurfaceStore(database_path).save_confirmed_surface(confirmed_surface())
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))

    with pytest.raises(VolSurfaceSchemaError, match="Refusing to read it"):
        VolSurfaceStore(database_path).list_surfaces()


def test_only_a_canonical_surface_can_be_saved(store) -> None:
    with pytest.raises(VolSurfaceStoreError, match="only a CanonicalVolSurface"):
        store.save_confirmed_surface({"points": []})


# -- reading never writes (Issue #194 / Codex review, PR #195) ---------------
#
# Browsing the Markets view reads this store. A read that had to migrate a
# database before it could answer would not be a read: it rewrote a store the
# trader only wanted to look at, and it failed outright against one on a
# read-only mount.


def _make_legacy_pre_185(database_path) -> None:
    """Roll a store back to the shape a build before Issue #185 wrote.

    Version 3 either way -- #185's column and table are additive and
    deliberately did not bump it -- so this is a *supported* older database,
    not a refused one. ``VACUUM`` rewrites the file so the comparison below
    is against a settled one rather than free pages left by the DDL.
    """

    with sqlite3.connect(database_path, isolation_level=None) as connection:
        connection.execute("ALTER TABLE vol_surface_point DROP COLUMN value_kind")
        connection.execute("DROP TABLE vol_surface_source_image")
        connection.execute("VACUUM")


def test_browsing_a_current_store_changes_not_one_byte(store, database_path) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    before = database_path.read_bytes()

    reader = VolSurfaceStore(database_path)
    assert len(reader.list_surfaces()) == 1
    assert reader.fetch_surface(surface.surface_id) == surface

    assert database_path.read_bytes() == before


def test_browsing_a_supported_older_store_neither_migrates_nor_misreads_it(
    store, database_path
) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    _make_legacy_pre_185(database_path)
    before = database_path.read_bytes()

    reader = VolSurfaceStore(database_path)
    (row,) = reader.list_surfaces()
    reloaded = reader.fetch_surface(surface.surface_id)

    # Read as it stands: no column added, no table created, no byte changed.
    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as connection:
        columns = {
            info[1] for info in connection.execute("PRAGMA table_info(vol_surface_point)")
        }
        tables = {
            name
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "value_kind" not in columns
    assert "vol_surface_source_image" not in tables

    # And the surface it rebuilds is exactly the one that was stored --
    # value_kind stood in for by the default that makes the column additive,
    # the image set rebuilt from the row's own single image. The fingerprint
    # check inside fetch_surface is what proves the substitution is faithful:
    # a wrong stand-in would not rebuild the digest saved beside the surface.
    assert reloaded == surface
    assert row.surface_id == surface.surface_id
    assert all(point.value_kind is VolValueKind.ABSOLUTE_VOL for point in reloaded.points)
    assert len(reloaded.provenance.source_images) == 1


def test_a_supported_older_store_is_readable_with_no_write_access_at_all(
    store, database_path, tmp_path
) -> None:
    """The failure a migrating read could not avoid: a read-only mount.

    Approximated with permissions -- the directory too, since SQLite needs to
    create a journal beside the file to write it.
    """

    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    _make_legacy_pre_185(database_path)

    os.chmod(database_path, 0o444)
    os.chmod(tmp_path, 0o555)
    try:
        reader = VolSurfaceStore(database_path)
        assert len(reader.list_surfaces()) == 1
        assert reader.fetch_surface(surface.surface_id) == surface
    finally:
        os.chmod(tmp_path, 0o755)
        os.chmod(database_path, 0o644)


def test_reading_a_store_that_does_not_exist_yet_creates_nothing(tmp_path) -> None:
    database_path = tmp_path / "never-created" / "vol_surfaces.sqlite3"
    store = VolSurfaceStore(database_path)

    assert store.list_surfaces() == ()
    with pytest.raises(KeyError):
        store.fetch_surface("any-surface-id")

    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_the_first_save_still_creates_the_database_a_read_would_not_have(tmp_path) -> None:
    """The other half: only the write path brings the store into existence."""

    database_path = tmp_path / "made-by-the-first-save" / "vol_surfaces.sqlite3"
    store = VolSurfaceStore(database_path)
    assert store.list_surfaces() == ()
    assert not database_path.exists()

    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    assert database_path.exists()
    assert store.fetch_surface(surface.surface_id) == surface


def test_saving_into_a_supported_older_store_still_brings_its_schema_up(
    store, database_path
) -> None:
    """Reads stopped migrating; writes must not have.

    A save into a pre-Issue-#185 database still needs the column it inserts
    into, so the catch-up has to happen on the way in.
    """

    store.save_confirmed_surface(confirmed_surface())
    _make_legacy_pre_185(database_path)

    second = confirmed_surface(capture_id="f" * 32)
    VolSurfaceStore(database_path).save_confirmed_surface(second)

    with sqlite3.connect(database_path) as connection:
        columns = {
            info[1] for info in connection.execute("PRAGMA table_info(vol_surface_point)")
        }
    assert "value_kind" in columns
    assert len(VolSurfaceStore(database_path).list_surfaces()) == 2


def test_a_newer_schema_version_is_refused_by_a_read_not_repaired(store, database_path) -> None:
    store.save_confirmed_surface(confirmed_surface())
    with sqlite3.connect(database_path, isolation_level=None) as connection:
        connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    before = database_path.read_bytes()

    reader = VolSurfaceStore(database_path)
    with pytest.raises(VolSurfaceSchemaError, match=f"schema version {SCHEMA_VERSION + 1}"):
        reader.list_surfaces()
    with pytest.raises(VolSurfaceSchemaError, match=f"schema version {SCHEMA_VERSION + 1}"):
        reader.fetch_surface("any-surface-id")

    assert database_path.read_bytes() == before


def test_a_store_recording_no_schema_version_is_refused_rather_than_stamped(
    store, database_path
) -> None:
    store.save_confirmed_surface(confirmed_surface())
    with sqlite3.connect(database_path, isolation_level=None) as connection:
        connection.execute("DELETE FROM schema_version")
    before = database_path.read_bytes()

    with pytest.raises(VolSurfaceSchemaError, match="records no vol-surface schema version"):
        VolSurfaceStore(database_path).list_surfaces()

    assert database_path.read_bytes() == before


def test_a_file_that_is_not_a_vol_surface_store_is_refused_rather_than_filled_in(
    database_path,
) -> None:
    with sqlite3.connect(database_path, isolation_level=None) as connection:
        connection.execute("CREATE TABLE something_else (x INTEGER)")
    before = database_path.read_bytes()

    with pytest.raises(VolSurfaceSchemaError, match="not a vol-surface store"):
        VolSurfaceStore(database_path).list_surfaces()

    assert database_path.read_bytes() == before


def test_every_additive_column_tells_a_read_what_to_stand_in_for() -> None:
    """The read path cannot silently fall behind the write path.

    A column added to ``_ADDITIVE_COLUMNS`` without a matching stand-in here
    would leave a read against a database lacking it failing on a missing
    column -- or, worse, quietly reshaping what an older row meant.
    """

    for table, column, definition in _ADDITIVE_COLUMNS:
        assert (table, column) in _READ_ONLY_COLUMN_DEFAULTS, (
            f"{table}.{column} is additive but no read-only stand-in is declared for it"
        )
        # The stand-in must be the column's own DEFAULT -- that default is
        # what makes the column additive, so anything else would be a value
        # this layer invented.
        stand_in = _READ_ONLY_COLUMN_DEFAULTS[(table, column)]
        assert f"DEFAULT '{stand_in}'" in definition


# -- listing ----------------------------------------------------------------


def test_an_empty_store_lists_nothing_rather_than_failing(store) -> None:
    assert store.list_surfaces() == ()


def test_the_listing_summarises_a_surface_without_pulling_its_points(store) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    (summary,) = store.list_surfaces()

    assert summary.surface_id == surface.surface_id
    assert summary.surface_type is VolSurfaceType.ATM_SWAPTION
    assert summary.point_count == POINT_COUNT
    assert summary.currency == "USD"
    assert summary.business_date == "08/18/26"
    assert summary.confirmed_by == "Eddy"


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"currency": "USD"}, True),
        ({"currency": "EUR"}, False),
        ({"business_date": "08/18/26"}, True),
        ({"business_date": "08/19/26"}, False),
        ({"surface_type": VolSurfaceType.ATM_SWAPTION}, True),
    ],
)
def test_the_listing_filters_on_resolved_identity_fields(store, filters, expected) -> None:
    store.save_confirmed_surface(confirmed_surface())

    assert bool(store.list_surfaces(**filters)) is expected


def test_a_surface_with_an_unresolved_field_is_never_matched_by_a_value(store) -> None:
    """An unknown currency must not be assumed into a currency="USD" answer."""

    store.save_confirmed_surface(confirmed_surface(metadata_overrides={"currency": None}))

    assert store.list_surfaces(currency="USD") == ()
    assert len(store.list_surfaces()) == 1


# -- location ---------------------------------------------------------------


def test_the_default_database_is_local_runtime_state_outside_version_control() -> None:
    """``.gitignore`` ignores ``/data/`` and ``*.sqlite3``; both must keep matching."""

    assert DEFAULT_DATABASE_PATH.parent.name == "data"
    assert DEFAULT_DATABASE_PATH.suffix == ".sqlite3"


def test_the_database_path_can_be_pointed_elsewhere_for_one_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SHIORI_VOL_SURFACE_DB", str(tmp_path / "scratch.sqlite3"))

    assert default_database_path() == tmp_path / "scratch.sqlite3"
    assert VolSurfaceStore().database_path == tmp_path / "scratch.sqlite3"


def test_a_blank_override_falls_back_to_the_default(monkeypatch) -> None:
    monkeypatch.setenv("SHIORI_VOL_SURFACE_DB", "   ")

    assert default_database_path() == DEFAULT_DATABASE_PATH


# -- boundaries -------------------------------------------------------------


def test_the_canonical_store_never_reaches_the_pricing_package() -> None:
    """Persistence is not pricing, and pricing must never read SQLite.

    Checked by importing the whole storage slice in a fresh interpreter and
    looking at what came with it, the same way the capture slice is checked
    in ``test_vcub_capture_review``.
    """

    import json
    import subprocess
    import sys

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, json;"
            "import shiori_pricing_lab.data.vol_surface;"
            "import shiori_pricing_lab.data.vol_surface_store;"
            "import shiori_pricing_lab.data.vcub_vol_surface_adapter;"
            "print(json.dumps([name for name in sys.modules "
            "if name.startswith('shiori_pricing_lab.pricing')]))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(probe.stdout) == []


def test_the_pricing_package_never_imports_sqlite_or_the_store() -> None:
    """The typed repository boundary, asserted against the source itself."""

    from pathlib import Path

    pricing_dir = Path(__file__).resolve().parents[1] / "src" / "shiori_pricing_lab" / "pricing"
    offenders = [
        path.name
        for path in sorted(pricing_dir.glob("*.py"))
        if "import sqlite3" in path.read_text(encoding="utf-8")
        or "vol_surface_store" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
