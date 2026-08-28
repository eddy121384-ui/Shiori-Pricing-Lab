"""Browser-driven tests for Markets -> Swaption Vol Surface (Issue #194).

Exercises ``vol_surface_view.js`` -- the market-view selector, the snapshot
picker, the stored matrix, and the interactive 3D surface -- against one real
``ThreadingHTTPServer`` and one real headless Chromium page. The two
read-only routes are intercepted at the browser network layer with
``page.route``, the pattern the sibling Markets browser file already uses, so
the fixtures are deterministic and no store is involved.

Every number in the fixtures below is made up. Nothing here is a Bloomberg
value.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test files: locally, missing Playwright is a skip; in CI
(``CI=true``) it is a hard collection-time error.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from collections.abc import Iterator

import pytest

from shiori_pricing_lab.app.standalone_option_workbench_server import create_server

_PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None
_RUNNING_IN_CI = os.environ.get("CI") == "true"

if _RUNNING_IN_CI and not _PLAYWRIGHT_AVAILABLE:
    raise RuntimeError(
        "Playwright is not installed in CI. The browser regression tests in "
        "this file are merge-protection, not optional -- CI must install "
        "'playwright' and run 'playwright install chromium' rather than let "
        "this file silently skip."
    )

pytestmark = pytest.mark.skipif(
    not _PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed locally (local-only skip; CI hard-fails instead)",
)

if _PLAYWRIGHT_AVAILABLE:
    from playwright.sync_api import sync_playwright

_CHROMIUM_EXECUTABLE_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _is_actually_hidden(page, element_id: str) -> bool:
    return page.eval_on_selector(f"#{element_id}", "el => getComputedStyle(el).display") == "none"


@pytest.fixture()
def server_url() -> Iterator[str]:
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


@pytest.fixture()
def page():
    with sync_playwright() as p:
        launch_kwargs = {}
        if _CHROMIUM_EXECUTABLE_PATH:
            launch_kwargs["executable_path"] = _CHROMIUM_EXECUTABLE_PATH
        browser = p.chromium.launch(**launch_kwargs)
        pg = browser.new_page(viewport={"width": 1672, "height": 941})
        yield pg
        browser.close()


# --- Synthetic fixtures ------------------------------------------------------

_EXPIRIES = ["1Mo", "3Mo", "6Mo", "1Yr", "2Yr"]
_TENORS = ["1Yr", "2Yr", "5Yr", "10Yr"]
# One deliberately unresolved cell, and one value whose exact decimal (82.4)
# a toFixed(2) renderer would silently turn into "82.40".
_ROWS = [
    [70.5, 71.25, 72.0, 73.125],
    [75.0, 76.5, 77.75, 78.0],
    [80.0, 81.0, 82.4, 83.5],
    [None, 86.0, 87.5, 88.25],
    [90.0, 91.5, 92.75, 94.0],
]

_SUMMARY_A = {
    "surface_id": "surface-a",
    "surface_type": "ATM_SWAPTION",
    "capture_id": "aaaa1111",
    "business_date": "08/18/26",
    "currency": "USD",
    "curve_config": "USD RFR BVOL Cube (Default)",
    "side": "Mid",
    "vol_type": "Normal Vol (OIS)",
    "source": "BVOL",
    "point_count": 20,
    "confirmed_by": "Eddy",
    "confirmed_at": "2026-08-18T09:41:00Z",
    "saved_at": "2026-08-18T09:41:02Z",
}
# Same screen, same day, different capture: distinguishable only by capture id.
_SUMMARY_B = {**_SUMMARY_A, "surface_id": "surface-b", "capture_id": "bbbb2222"}

_SURFACE_A = {
    "surface_id": "surface-a",
    "identity": {
        "surface_type": "ATM_SWAPTION",
        "capture_id": "aaaa1111",
        "business_date": "08/18/26",
        "currency": "USD",
        "curve_config": "USD RFR BVOL Cube (Default)",
        "side": "Mid",
        "vol_type": "Normal Vol (OIS)",
        "source": "BVOL",
        "unresolved_fields": [],
    },
    "provenance": {
        "capture_id": "aaaa1111",
        "source_reference": "vcub_atm_usd.png",
        "source_image_sha256": "a" * 64,
        "source_image_bytes": 4096,
        "captured_at": "2026-08-18T09:30:00Z",
        "parser_name": "vcub_atm_template",
        "parser_version": "1",
        "confirmed_by": "Eddy",
        "confirmed_at": "2026-08-18T09:41:00Z",
    },
    "volatility_unit": "bp",
    "point_count": 20,
    "grid": {"expiries": _EXPIRIES, "underlying_tenors": _TENORS, "rows": _ROWS},
}

_SURFACE_B = {
    **_SURFACE_A,
    "surface_id": "surface-b",
    "identity": {**_SURFACE_A["identity"], "capture_id": "bbbb2222"},
    "grid": {
        "expiries": _EXPIRIES,
        "underlying_tenors": _TENORS,
        # Every value shifted, so a stale render is unmistakable.
        "rows": [[None if v is None else v + 10 for v in row] for row in _ROWS],
    },
}


def _route_vol_surface(
    page,
    *,
    summaries=(_SUMMARY_A,),
    surfaces=None,
    list_error: str | None = None,
    surface_error: str | None = None,
    surface_status: int = 200,
):
    """Serve the two read-only routes from the fixtures above."""

    by_id = {"surface-a": _SURFACE_A, "surface-b": _SURFACE_B}
    if surfaces is not None:
        by_id = surfaces
    calls = {"list": 0, "surface": []}

    def _handle_list(route):
        calls["list"] += 1
        if list_error is not None:
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"error": list_error}),
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"surfaces": list(summaries), "database": "test.sqlite3"}),
        )

    def _handle_surface(route):
        requested = json.loads(route.request.post_data)["surface_id"]
        calls["surface"].append(requested)
        if surface_error is not None:
            route.fulfill(
                status=surface_status,
                content_type="application/json",
                body=json.dumps({"error": surface_error}),
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(by_id[requested]),
        )

    page.route("**/api/vol-surface/atm/list", _handle_list)
    page.route("**/api/vol-surface/atm/surface", _handle_surface)
    return calls


def _route_curve_away(page):
    """Keep the Option Discount Curve view's own fetch off live Bloomberg."""

    page.route(
        "**/api/bloomberg/option-discount-curve",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "no Bloomberg in this test"}),
        ),
    )


def _open_vol_surface(page, server_url: str) -> None:
    page.goto(f"{server_url}/")
    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))
    page.click("#markets-tab-vol-surface")


def _wait_for_surface(page) -> None:
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-table-card"))


def _displayed(value: float) -> str:
    """What the page must show for one stored value.

    The shortest decimal that round-trips the stored double, zero-padded to
    two decimals so the column lines up -- every digit the value holds, and
    no digit it does not.
    """

    exact = repr(value)
    whole, _, fraction = exact.partition(".")
    return f"{whole}.{fraction.ljust(2, '0')}" if len(fraction) < 2 else exact


# --- The market-view selector ------------------------------------------------


def test_markets_offers_both_market_views_and_starts_on_the_curve(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    page.goto(f"{server_url}/")
    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))

    assert not _is_actually_hidden(page, "markets-panel-curve")
    assert _is_actually_hidden(page, "markets-panel-vol-surface")
    assert page.eval_on_selector("#markets-tab-curve", "el => el.classList.contains('is-active')")


def test_the_selector_switches_between_the_two_market_views(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    page.goto(f"{server_url}/")
    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))

    page.click("#markets-tab-vol-surface")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-vol-surface"))
    assert _is_actually_hidden(page, "markets-panel-curve")
    assert page.eval_on_selector(
        "#markets-tab-vol-surface", "el => el.getAttribute('aria-selected')"
    ) == "true"

    page.click("#markets-tab-curve")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-curve"))
    assert _is_actually_hidden(page, "markets-panel-vol-surface")


def test_the_existing_option_discount_curve_view_still_renders_unchanged(server_url, page) -> None:
    # Regression guard for "preserve the existing Markets Option Discount
    # Curve behavior": the curve loads and renders with the vol-surface view
    # present, and survives a round trip through the new selector.
    _route_vol_surface(page)
    page.route(
        "**/api/bloomberg/option-discount-curve",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "curve_id": "USD_SOFR_OPTION_DISCOUNT_CURVE",
                    "curve_name": "USD SOFR Option Discount Curve (Bloomberg Curve #490)",
                    "source_system": "BLOOMBERG_DAPI",
                    "rate_basis": "CONTINUOUS_ZERO_RATE",
                    "acquired_at": "2026-08-11T12:34:45+08:00",
                    "coverage": {
                        "first_tenor": "1Y",
                        "last_tenor": "10Y",
                        "first_maturity": "2027-08-11",
                        "last_maturity": "2036-08-11",
                    },
                    "nodes": [
                        {
                            "tenor": "1Y",
                            "maturity": "2027-08-11",
                            "zero_rate_percent": 3.8172,
                            "discount_factor": 0.972346,
                            "par_rate_percent": 3.75,
                        },
                        {
                            "tenor": "10Y",
                            "maturity": "2036-08-11",
                            "zero_rate_percent": 4.2020,
                            "discount_factor": 0.653380,
                            "par_rate_percent": 4.05,
                        },
                    ],
                }
            ),
        ),
    )
    page.goto(f"{server_url}/")
    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-table-card"))
    assert page.eval_on_selector_all("#markets-table-body tr", "rows => rows.length") == 2

    page.click("#markets-tab-vol-surface")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-vol-surface"))
    page.click("#markets-tab-curve")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-table-card"))

    assert page.eval_on_selector_all("#markets-table-body tr", "rows => rows.length") == 2
    assert page.inner_text("#markets-summary-1y") == "3.8172%"


# --- The stored matrix -------------------------------------------------------


def test_the_table_shows_the_whole_stored_matrix(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    header = page.eval_on_selector_all(
        "#vol-surface-table-head th", "cells => cells.map(c => c.textContent)"
    )
    assert header == ["Expiry \\ Tenor", *_TENORS]
    rows = page.eval_on_selector_all(
        "#vol-surface-table-body tr",
        "rows => rows.map(r => Array.from(r.children).map(c => c.textContent))",
    )
    assert len(rows) == len(_EXPIRIES)
    assert [row[0] for row in rows] == _EXPIRIES


def test_every_cell_is_the_exact_stored_value_never_a_rounding(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    rows = page.eval_on_selector_all(
        "#vol-surface-table-body tr",
        "rows => rows.map(r => Array.from(r.children).slice(1).map(c => c.textContent))",
    )
    for row_index, stored_row in enumerate(_ROWS):
        for column_index, stored in enumerate(stored_row):
            shown = rows[row_index][column_index]
            if stored is None:
                assert shown == "—"
            else:
                assert shown == _displayed(stored)
                # Whatever padding does, the text must still parse back to
                # the exact stored double.
                assert float(shown) == stored
    # Three stored decimals survive; two-decimal values are padded, not cut.
    assert rows[0][3] == "73.125"
    assert rows[2][2] == "82.40"


def test_an_unresolved_cell_reads_as_unresolved_never_as_zero(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    cell = page.eval_on_selector(
        "#vol-surface-table-body tr:nth-child(4) td:nth-child(2)",
        "el => ({ text: el.textContent, unresolved: el.classList.contains('unresolved') })",
    )
    assert cell["text"] == "—"
    assert cell["unresolved"] is True


def test_the_header_shows_the_stored_provenance(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    assert page.inner_text("#vol-surface-currency") == "USD"
    assert page.inner_text("#vol-surface-curve-config") == "USD RFR BVOL Cube (Default)"
    assert page.inner_text("#vol-surface-side") == "Mid"
    assert page.inner_text("#vol-surface-business-date") == "08/18/26"
    assert page.inner_text("#vol-surface-vol-type") == "Normal Vol (OIS)"
    assert page.inner_text("#vol-surface-source") == "BVOL"
    assert page.inner_text("#vol-surface-unit") == "bp"
    assert page.inner_text("#vol-surface-point-count") == "20"
    assert page.inner_text("#vol-surface-id") == "surface-a"


def test_an_unresolved_identity_field_says_so_rather_than_showing_a_guess(
    server_url, page
) -> None:
    unresolved = {
        **_SURFACE_A,
        "identity": {**_SURFACE_A["identity"], "side": None, "unresolved_fields": ["side"]},
        "volatility_unit": None,
    }
    _route_curve_away(page)
    _route_vol_surface(page, surfaces={"surface-a": unresolved})
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    assert page.inner_text("#vol-surface-side") == "unresolved"
    assert page.eval_on_selector("#vol-surface-side", "el => el.classList.contains('unresolved')")
    assert page.inner_text("#vol-surface-unit") == "unresolved"


# --- Choosing a snapshot ------------------------------------------------------


def test_a_single_stored_snapshot_is_shown_without_asking(server_url, page) -> None:
    _route_curve_away(page)
    calls = _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    assert calls["surface"] == ["surface-a"]
    assert page.eval_on_selector("#vol-surface-select", "el => el.value") == "surface-a"


def test_several_snapshots_are_never_chosen_for_the_trader(server_url, page) -> None:
    _route_curve_away(page)
    calls = _route_vol_surface(page, summaries=(_SUMMARY_A, _SUMMARY_B))
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-empty"))

    # Nothing fetched, nothing drawn: the trader picks.
    assert calls["surface"] == []
    assert _is_actually_hidden(page, "vol-surface-table-card")
    assert _is_actually_hidden(page, "vol-surface-chart-card")
    assert "does not pick a snapshot for you" in page.inner_text("#vol-surface-empty")


def test_two_captures_of_one_screen_are_distinguishable_in_the_picker(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page, summaries=(_SUMMARY_A, _SUMMARY_B))
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-empty"))

    labels = page.eval_on_selector_all(
        "#vol-surface-select option", "options => options.map(o => o.textContent)"
    )
    # A placeholder plus one distinct, self-describing label per snapshot.
    assert len(labels) == 3
    assert len(set(labels)) == 3
    assert any("aaaa1111" in label for label in labels)
    assert any("bbbb2222" in label for label in labels)


def test_choosing_a_snapshot_updates_the_table_and_the_surface_together(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page, summaries=(_SUMMARY_A, _SUMMARY_B))
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-empty"))

    page.select_option("#vol-surface-select", "surface-b")
    _wait_for_surface(page)

    assert page.eval_on_selector("#vol-surface-id", "el => el.textContent") == "surface-b"
    # The row header is the expiry, so the first vol cell is the second child.
    first_cell = page.inner_text("#vol-surface-table-body tr:nth-child(1) td:nth-child(2)")
    assert first_cell == "80.50"  # 70.5 + 10, the surface-b fixture
    nodes = page.evaluate("() => window.__shioriTestVolSurfaceProjectedNodes()")
    by_coordinate = {(n["expiry"], n["tenor"]): n["volatility"] for n in nodes}
    assert by_coordinate[("1Mo", "1Yr")] == 80.5
    # Both halves came from the same fetched surface_id.
    payload = page.evaluate("() => window.__shioriTestVolSurfacePayload()")
    assert payload["surface_id"] == "surface-b"


def test_an_empty_store_says_so_rather_than_showing_an_empty_grid(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page, summaries=())
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-empty"))

    assert "No confirmed ATM surface is stored yet" in page.inner_text("#vol-surface-empty")
    assert _is_actually_hidden(page, "vol-surface-table-card")
    assert _is_actually_hidden(page, "vol-surface-chart-card")


# --- The 3D surface -----------------------------------------------------------


def test_the_surface_draws_one_node_per_stored_value(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    nodes = page.evaluate("() => window.__shioriTestVolSurfaceProjectedNodes()")
    resolved = [value for row in _ROWS for value in row if value is not None]
    assert len(nodes) == len(resolved) == 19
    assert sorted(node["volatility"] for node in nodes) == sorted(resolved)
    # The unresolved intersection has no node at all -- it is never plotted at
    # zero, and never interpolated from its neighbours.
    assert ("1Yr", "1Yr") not in {(n["expiry"], n["tenor"]) for n in nodes}


def test_dragging_rotates_and_the_nodes_move_with_it(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    before_camera = page.evaluate("() => window.__shioriTestVolSurfaceCamera()")
    before_nodes = page.evaluate("() => window.__shioriTestVolSurfaceProjectedNodes()")

    box = page.eval_on_selector(
        "#vol-surface-canvas",
        "el => { const r = el.getBoundingClientRect(); "
        "return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }",
    )
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    page.mouse.move(box["x"] + 120, box["y"] + 40, steps=6)
    page.mouse.up()

    after_camera = page.evaluate("() => window.__shioriTestVolSurfaceCamera()")
    after_nodes = page.evaluate("() => window.__shioriTestVolSurfaceProjectedNodes()")
    assert after_camera["yaw"] != before_camera["yaw"]
    assert after_camera["pitch"] != before_camera["pitch"]
    assert [n["sx"] for n in after_nodes] != [n["sx"] for n in before_nodes]
    # Rotation is a camera change only: every stored value is untouched.
    assert [n["volatility"] for n in after_nodes] == [n["volatility"] for n in before_nodes]


def test_the_wheel_zooms_in_and_out_within_bounds(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    assert page.evaluate("() => window.__shioriTestVolSurfaceCamera()")["zoom"] == 1
    page.evaluate("() => window.__shioriTestVolSurfaceZoomBy(-300)")
    zoomed_in = page.evaluate("() => window.__shioriTestVolSurfaceCamera()")["zoom"]
    assert zoomed_in > 1
    page.evaluate("() => window.__shioriTestVolSurfaceZoomBy(600)")
    zoomed_out = page.evaluate("() => window.__shioriTestVolSurfaceCamera()")["zoom"]
    assert zoomed_out < zoomed_in
    for _ in range(40):
        page.evaluate("() => window.__shioriTestVolSurfaceZoomBy(-300)")
    assert page.evaluate("() => window.__shioriTestVolSurfaceCamera()")["zoom"] <= 4


def test_hovering_a_node_reports_its_exact_stored_value_and_unit(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    target = page.evaluate(
        """() => window.__shioriTestVolSurfaceProjectedNodes()
             .find(n => n.expiry === '6Mo' && n.tenor === '5Yr')"""
    )
    assert target["volatility"] == 82.4

    picked = page.evaluate(
        "([x, y]) => window.__shioriTestVolSurfaceHoverAt(x, y)", [target["sx"], target["sy"]]
    )
    assert picked == {"expiry": "6Mo", "tenor": "5Yr", "volatility": 82.4}
    assert not _is_actually_hidden(page, "vol-surface-tooltip")
    tooltip = page.inner_text("#vol-surface-tooltip")
    assert "6Mo × 5Yr = 82.40 bp" in tooltip
    assert "Option Expiry:" in tooltip
    assert "Swap Tenor:" in tooltip


def test_hovering_empty_space_reports_nothing_rather_than_a_nearest_guess(
    server_url, page
) -> None:
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    picked = page.evaluate("() => window.__shioriTestVolSurfaceHoverAt(2, 2)")
    assert picked is None
    assert _is_actually_hidden(page, "vol-surface-tooltip")


def test_a_surface_with_no_stated_unit_never_invents_one(server_url, page) -> None:
    unitless = {**_SURFACE_A, "volatility_unit": None}
    _route_curve_away(page)
    _route_vol_surface(page, surfaces={"surface-a": unitless})
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    target = page.evaluate(
        """() => window.__shioriTestVolSurfaceProjectedNodes()
             .find(n => n.expiry === '6Mo' && n.tenor === '5Yr')"""
    )
    page.evaluate(
        "([x, y]) => window.__shioriTestVolSurfaceHoverAt(x, y)", [target["sx"], target["sy"]]
    )
    tooltip = page.inner_text("#vol-surface-tooltip")
    assert "6Mo × 5Yr = 82.40" in tooltip
    assert "bp" not in tooltip


# --- Failing closed and staying read-only -------------------------------------


def test_a_failed_listing_is_an_error_not_an_empty_surface(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(page, list_error="the local store could not be opened")
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-error"))

    assert "the local store could not be opened" in page.inner_text("#vol-surface-error-detail")
    assert _is_actually_hidden(page, "vol-surface-table-card")
    assert _is_actually_hidden(page, "vol-surface-chart-card")


def test_a_refused_surface_is_an_error_not_a_repaired_grid(server_url, page) -> None:
    _route_curve_away(page)
    _route_vol_surface(
        page, surface_error="surface surface-a is not a complete 21 x 15 matrix", surface_status=400
    )
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-error"))

    assert "not a complete 21 x 15 matrix" in page.inner_text("#vol-surface-error-detail")
    assert _is_actually_hidden(page, "vol-surface-table-card")


def test_a_ragged_grid_is_refused_by_the_page_too(server_url, page) -> None:
    ragged = {
        **_SURFACE_A,
        "grid": {
            "expiries": _EXPIRIES,
            "underlying_tenors": _TENORS,
            "rows": [_ROWS[0][:2], *_ROWS[1:]],
        },
    }
    _route_curve_away(page)
    _route_vol_surface(page, surfaces={"surface-a": ragged})
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-error"))

    assert "one cell per swap tenor" in page.inner_text("#vol-surface-error-detail")
    assert _is_actually_hidden(page, "vol-surface-table-card")


def test_the_view_calls_only_the_two_read_only_routes(server_url, page) -> None:
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    _route_curve_away(page)
    _route_vol_surface(page, summaries=(_SUMMARY_A, _SUMMARY_B))
    _open_vol_surface(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "vol-surface-empty"))
    page.select_option("#vol-surface-select", "surface-b")
    _wait_for_surface(page)
    page.evaluate("() => window.__shioriTestVolSurfaceRotateBy(50, 20)")
    page.evaluate("() => window.__shioriTestVolSurfaceZoomBy(-200)")

    assert page.evaluate("() => window.__shioriTestVolSurfaceRequestedRoutes()") == [
        "/api/vol-surface/atm/list",
        "/api/vol-surface/atm/surface",
    ]
    # Nothing this view did reached a capture, confirm, price, or export route.
    forbidden = ("/api/vcub/", "/api/price", "/api/case", "/api/export/")
    for url in requests:
        assert not any(fragment in url for fragment in forbidden), url


def test_the_page_loads_no_script_or_style_from_another_origin(server_url, page) -> None:
    # Issue #194: the Markets page must not go blank when corporate
    # internet/CDN access is blocked, so nothing it renders may come from
    # off-origin.
    _route_curve_away(page)
    _route_vol_surface(page)
    _open_vol_surface(page, server_url)
    _wait_for_surface(page)

    external = page.evaluate(
        """() => Array.from(document.querySelectorAll('script[src], link[href]'))
             .map(el => el.src || el.href)
             .filter(url => !url.startsWith(location.origin))"""
    )
    assert external == []
