"""The workbench bridge's two read-only vol-surface routes (Issue #194).

``POST /api/vol-surface/atm/list`` and ``POST /api/vol-surface/atm/surface``
are what the Markets Swaption Vol Surface view reads. Every test here drives
the real ``ThreadingHTTPServer`` over loopback against a throwaway store, so
nothing touches the workbench's own ``data/vol_surfaces.sqlite3`` and no live
Bloomberg value appears in any fixture -- the surfaces come from
``test_vol_surface``'s synthetic 21x15 grid.

The two things this file exists to hold down: the routes hand back exactly
what was stored, and they hand back *nothing else* -- no write, no capture,
no OCR, no pricing, no resolver call, no repaired grid.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
from test_vol_surface import (
    EXPIRY_LABELS,
    TENOR_LABELS,
    confirmed_surface,
    synthetic_value,
)
from test_vol_surface_store_otm_dimension import otm_surface

import shiori_pricing_lab.app.standalone_option_workbench_server as server_module
from shiori_pricing_lab.app.standalone_option_workbench_server import create_server
from shiori_pricing_lab.data.vol_surface_store import VolSurfaceStore


@pytest.fixture()
def store(monkeypatch, tmp_path) -> VolSurfaceStore:
    """A clean, throwaway canonical store for the process-wide one."""

    fresh = VolSurfaceStore(tmp_path / "vol_surfaces.sqlite3")
    monkeypatch.setattr(server_module, "VOL_SURFACE_STORE", fresh)
    return fresh


@pytest.fixture()
def server_url(store) -> Iterator[str]:
    server = create_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post_json(url: str, payload: object) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# --- Listing -----------------------------------------------------------------


def test_an_empty_store_lists_nothing_rather_than_failing(server_url) -> None:
    status, body = _post_json(f"{server_url}/api/vol-surface/atm/list", {})

    assert status == 200
    assert body["surfaces"] == []


def test_a_confirmed_atm_surface_is_listed_with_the_identity_that_names_it(
    server_url, store
) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    status, body = _post_json(f"{server_url}/api/vol-surface/atm/list", {})

    assert status == 200
    (row,) = body["surfaces"]
    assert row["surface_id"] == surface.surface_id
    assert row["surface_type"] == "ATM_SWAPTION"
    assert row["capture_id"] == surface.identity.capture_id
    assert row["currency"] == "USD"
    assert row["curve_config"] == "USD RFR BVOL Cube (Default)"
    assert row["side"] == "Mid"
    assert row["business_date"] == "08/18/26"
    assert row["vol_type"] == "Normal Vol (OIS)"
    assert row["source"] == "BVOL"
    assert row["point_count"] == 315
    assert row["confirmed_by"] == "Eddy"


def test_an_otm_surface_is_never_offered_by_the_atm_listing(server_url, store) -> None:
    store.save_confirmed_surface(confirmed_surface())
    store.save_confirmed_surface(otm_surface())

    status, body = _post_json(f"{server_url}/api/vol-surface/atm/list", {})

    assert status == 200
    assert [row["surface_type"] for row in body["surfaces"]] == ["ATM_SWAPTION"]


def test_two_captures_of_the_same_screen_are_two_distinguishable_snapshots(
    server_url, store
) -> None:
    # Same business date, same everything but the capture -- the listing must
    # keep them apart, or the view would have to pick one arbitrarily.
    first = confirmed_surface(capture_id="1" * 32)
    second = confirmed_surface(capture_id="2" * 32)
    store.save_confirmed_surface(first)
    store.save_confirmed_surface(second)

    status, body = _post_json(f"{server_url}/api/vol-surface/atm/list", {})

    assert status == 200
    assert len(body["surfaces"]) == 2
    assert {row["capture_id"] for row in body["surfaces"]} == {"1" * 32, "2" * 32}
    assert {row["surface_id"] for row in body["surfaces"]} == {
        first.surface_id,
        second.surface_id,
    }


# --- Fetching one surface -----------------------------------------------------


def test_the_fetched_surface_is_the_stored_matrix_value_for_value(server_url, store) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    status, body = _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": surface.surface_id}
    )

    assert status == 200
    assert body["surface_id"] == surface.surface_id
    assert body["point_count"] == 315
    grid = body["grid"]
    assert grid["expiries"] == list(EXPIRY_LABELS)
    assert grid["underlying_tenors"] == list(TENOR_LABELS)
    assert len(grid["rows"]) == 21
    for row_index, row in enumerate(grid["rows"]):
        assert row == [
            synthetic_value(row_index, column_index) for column_index in range(len(TENOR_LABELS))
        ]


def test_the_fetched_surface_carries_the_provenance_the_header_shows(server_url, store) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    _, body = _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": surface.surface_id}
    )

    assert body["identity"]["currency"] == "USD"
    assert body["identity"]["curve_config"] == "USD RFR BVOL Cube (Default)"
    assert body["identity"]["side"] == "Mid"
    assert body["identity"]["business_date"] == "08/18/26"
    assert body["identity"]["vol_type"] == "Normal Vol (OIS)"
    assert body["identity"]["source"] == "BVOL"
    assert body["volatility_unit"] == "bp"
    assert body["provenance"]["captured_at"] == surface.provenance.captured_at
    assert body["provenance"]["confirmed_by"] == "Eddy"


def test_an_unresolved_cell_comes_back_unresolved_never_as_a_number(server_url, store) -> None:
    surface = confirmed_surface(unresolved_cells=frozenset({(3, 4)}))
    store.save_confirmed_surface(surface)

    _, body = _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": surface.surface_id}
    )

    assert body["grid"]["rows"][3][4] is None
    assert body["grid"]["rows"][3][5] == synthetic_value(3, 5)


def test_a_surface_the_store_does_not_hold_is_a_404_not_an_empty_grid(server_url) -> None:
    status, body = _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": "no-such-surface"}
    )

    assert status == 404
    assert "no-such-surface" in body["error"]
    assert "grid" not in body


def test_an_otm_surface_cannot_be_fetched_through_the_atm_route(server_url, store) -> None:
    surface = otm_surface()
    store.save_confirmed_surface(surface)

    status, body = _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": surface.surface_id}
    )

    assert status == 400
    assert "OTM_SWAPTION_SABR" in body["error"]


def test_a_blank_surface_id_is_refused(server_url) -> None:
    status, body = _post_json(f"{server_url}/api/vol-surface/atm/surface", {"surface_id": "  "})

    assert status == 400
    assert "surface_id" in body["error"]


def test_a_body_without_a_surface_id_is_refused(server_url) -> None:
    status, body = _post_json(f"{server_url}/api/vol-surface/atm/surface", {})

    assert status == 400
    assert "surface_id" in body["error"]


def test_a_surface_edited_under_the_workbench_fails_visibly(server_url, store) -> None:
    # Issue #194: a malformed stored surface must fail rather than be
    # silently repaired. The store verifies against the fingerprint it saved,
    # so a row changed behind its back is refused -- and the route must pass
    # that refusal on rather than draw the altered grid.
    import sqlite3

    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute(
            "UPDATE vol_surface_point SET volatility = 999.0 WHERE surface_id = ? "
            "AND point_index = 0",
            (surface.surface_id,),
        )
        connection.commit()
    finally:
        connection.close()

    status, body = _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": surface.surface_id}
    )

    assert status == 400
    assert "fingerprint" in body["error"]
    assert "999" not in json.dumps(body.get("grid", {}))


# --- What these routes must never do ------------------------------------------


def test_neither_route_writes_anything_to_the_store(server_url, store) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)
    before = store.database_path.read_bytes()

    _post_json(f"{server_url}/api/vol-surface/atm/list", {})
    _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": surface.surface_id}
    )
    _post_json(f"{server_url}/api/vol-surface/atm/surface", {"surface_id": "no-such-surface"})

    assert store.database_path.read_bytes() == before
    assert len(store.list_surfaces()) == 1
    assert store.fetch_surface(surface.surface_id) == surface


def test_neither_route_reaches_the_capture_pricing_or_resolver_seams(
    server_url, store, monkeypatch
) -> None:
    surface = confirmed_surface()
    store.save_confirmed_surface(surface)

    def _forbidden(*args, **kwargs):  # pragma: no cover - the point is that it never runs
        raise AssertionError("the Swaption Vol Surface routes must not call this")

    import shiori_pricing_lab.app.vcub_capture_review as review_module
    import shiori_pricing_lab.data.vcub_normal_vol_resolver as resolver_module

    monkeypatch.setattr(review_module, "read_tokens_from_image_bytes", _forbidden)
    monkeypatch.setattr(server_module, "price_standalone_option_case", _forbidden)
    monkeypatch.setattr(
        server_module, "load_bloomberg_usd_sofr_option_discount_curve", _forbidden
    )
    for name in dir(resolver_module):
        attribute = getattr(resolver_module, name)
        if callable(attribute) and not name.startswith("_") and name.islower():
            monkeypatch.setattr(resolver_module, name, _forbidden)

    status, _ = _post_json(f"{server_url}/api/vol-surface/atm/list", {})
    assert status == 200
    status, body = _post_json(
        f"{server_url}/api/vol-surface/atm/surface", {"surface_id": surface.surface_id}
    )
    assert status == 200
    assert body["point_count"] == 315


def test_the_page_and_its_new_module_are_served_from_disk(server_url) -> None:
    with urllib.request.urlopen(f"{server_url}/vol_surface_view.js") as response:
        assert response.status == 200
        body = response.read().decode("utf-8")
    assert "/api/vol-surface/atm/list" in body
    assert "/api/vol-surface/atm/surface" in body


def test_the_api_contract_marker_moved_with_the_new_routes() -> None:
    # The launcher refuses to reuse a process whose contract marker differs,
    # so the two must always agree (see scripts/launch_workbench.py).
    launcher = (
        server_module.PROTOTYPE_DIR.parent.parent / "scripts" / "launch_workbench.py"
    ).read_text(encoding="utf-8")
    assert f'"{server_module.API_CONTRACT_ID}"' in launcher
