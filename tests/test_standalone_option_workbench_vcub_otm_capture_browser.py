"""Browser-driven regression tests for the OTM/SABR Capture view (Issue #185).

Exercises ``vcub_otm_capture.js`` -- view switching, multi-file selection and
drag-and-drop, removing a file before Parse, the one-request round trip, the
merged ``Term x Tenor`` by strike table, the coverage readout, and the
Confirm/Reject gate -- against one real ``ThreadingHTTPServer`` and one real
headless Chromium page. ``POST /api/vcub/otm/parse`` and the two review
routes are intercepted at the browser network layer with ``page.route``, the
same pattern the sibling browser-test files use, so no OCR engine and no
screenshot are involved.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test files: locally, missing Playwright is a skip; in CI
(``CI=true``) it is a hard collection-time error.
"""

from __future__ import annotations

import base64
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

_PLAYWRIGHT_SKIP = pytest.mark.skipif(
    not _PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed locally (local-only skip; CI hard-fails instead)",
)

if _PLAYWRIGHT_AVAILABLE:
    from playwright.sync_api import sync_playwright

_CHROMIUM_EXECUTABLE_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")

# A one-pixel PNG. The Capture view only ever hands these bytes to the
# bridge and shows them back as an <img>; the parse response is faked, so
# nothing here needs a Bloomberg screenshot or an OCR engine.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_STRIKES = [
    {"label": "-100bps", "offset_bp": -100.0},
    {"label": "ATM", "offset_bp": None},
    {"label": "100bps", "offset_bp": 100.0},
]
_ROWS = [
    {"term": "1Mo", "tenor": "1Yr", "label": "1Mo x 1Yr", "values": [40.5, 75.0, 46.5]},
    {"term": "1Mo", "tenor": "2Yr", "label": "1Mo x 2Yr", "values": [33.5, None, 50.25]},
    {"term": "3Mo", "tenor": "1Yr", "label": "3Mo x 1Yr", "values": [31.0, 83.0, 36.0]},
]


def _source(name: str, digest: str) -> dict:
    return {
        "source_reference": name,
        "source_image_sha256": digest * 64,
        "source_image_bytes": 4096,
        "captured_at": "2026-08-21T09:30:00Z",
        "parser_name": "shiori-vcub-otm-sabr-template",
        "parser_version": "0.1.0",
    }


def _capture_payload(**overrides) -> dict:
    capture = {
        "sources": [_source("shot-a.png", "a"), _source("shot-b.png", "b")],
        "metadata": {
            "currency": None,
            "curve_config": None,
            "side": None,
            "quote_date": None,
            "tab": "OTM Swaptions / SABR",
            "vol_type": "Normal Vol Skew",
            "source": "BVOL",
            "display_mode": "Spread",
            "unresolved_fields": ["currency", "curve_config", "side", "quote_date"],
        },
        "table": {"strikes": list(_STRIKES), "rows": list(_ROWS)},
        # A complete capture in this fixture's own miniature template: the
        # page renders whatever the server says is missing, and decides
        # nothing about completeness itself.
        "missing_rows": [],
        "unexpected_rows": [],
        "expected_row_count": len(_ROWS),
        "coverage": [
            {
                "source_reference": "shot-a.png",
                "source_image_sha256": "a" * 64,
                "row_count": 2,
                "row_labels": ["1Mo x 1Yr", "1Mo x 2Yr"],
                "first_row": "1Mo x 1Yr",
                "last_row": "1Mo x 2Yr",
                "shared_row_count": 1,
                "shared_row_labels": ["1Mo x 2Yr"],
            },
            {
                "source_reference": "shot-b.png",
                "source_image_sha256": "b" * 64,
                "row_count": 2,
                "row_labels": ["1Mo x 2Yr", "3Mo x 1Yr"],
                "first_row": "1Mo x 2Yr",
                "last_row": "3Mo x 1Yr",
                "shared_row_count": 1,
                "shared_row_labels": ["1Mo x 2Yr"],
            },
        ],
        "blocking_errors": [],
        "warnings": [
            {
                "code": "UNRESOLVED_CELL",
                "message": "1Mo x 2Yr at ATM has no resolved value; compare it against the "
                "screenshot",
                "row": "1Mo x 2Yr",
                "strike": "ATM",
                "source": "shot-a.png",
            }
        ],
        "review_status": "PENDING_REVIEW",
        "reviewed_by": None,
        "reviewed_at": None,
        "can_confirm": True,
    }
    capture.update(overrides)
    return {"capture_id": "otm-cap-1", "capture": capture, "reader_notes": []}


def _blocked_payload() -> dict:
    payload = _capture_payload()
    payload["capture"]["blocking_errors"] = [
        {
            "code": "OVERLAP_VALUE_CONFLICT",
            "message": "1Mo x 2Yr at ATM reads 75.0 in 'shot-a.png' and 76.0 in 'shot-b.png'",
            "row": "1Mo x 2Yr",
            "strike": "ATM",
            "source": "shot-b.png",
        }
    ]
    payload["capture"]["can_confirm"] = False
    return payload


def _incomplete_payload() -> dict:
    payload = _capture_payload()
    payload["capture"]["missing_rows"] = ["3Mo x 2Yr", "3Mo x 5Yr"]
    payload["capture"]["expected_row_count"] = len(_ROWS) + 2
    payload["capture"]["blocking_errors"] = [
        {
            "code": "INCOMPLETE_SURFACE",
            "message": "2 of the 5 expected Term x Tenor rows were not captured, so this is "
            "part of the screen rather than the screen: 3Mo x 2Yr, 3Mo x 5Yr",
            "row": None,
            "strike": None,
            "source": None,
        }
    ]
    payload["capture"]["can_confirm"] = False
    return payload


def _confirmed_payload(storage: dict | None) -> dict:
    payload = _capture_payload()
    payload["capture"].update(
        {
            "review_status": "CONFIRMED",
            "reviewed_by": "Eddy",
            "reviewed_at": "2026-08-21T09:45:00Z",
            "can_confirm": False,
        }
    )
    if storage is not None:
        payload["storage"] = storage
    return payload


_SAVED_STORAGE = {
    "status": "SAVED",
    "surface_id": "3b4b3913f0c48ebda1b1cedc319f08a0",
    "point_count": 9,
    "error": None,
    "database": "/repo/data/vol_surfaces.sqlite3",
}

_FAILED_STORAGE = {
    "status": "FAILED",
    "surface_id": None,
    "point_count": None,
    "error": "the vol-surface store refused the write: disk I/O error",
    "database": "/repo/data/vol_surfaces.sqlite3",
}


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


def _route_json(page, path: str, payload: dict, status: int = 200) -> list[dict]:
    """Intercept ``path`` with a canned response and record what was posted."""

    seen: list[dict] = []

    def handler(route, request):
        seen.append(json.loads(request.post_data or "{}"))
        route.fulfill(
            status=status, content_type="application/json", body=json.dumps(payload)
        )

    page.route(f"**{path}", handler)
    return seen


def _open_otm_view(page, server_url: str) -> None:
    page.goto(server_url)
    page.click("#nav-capture-otm")
    _wait_until(lambda: not _is_actually_hidden(page, "view-capture-otm"))


def _screenshots(tmp_path, names=("shot-a.png", "shot-b.png")) -> list[str]:
    paths = []
    for index, name in enumerate(names):
        path = tmp_path / name
        # Distinct bytes per file, as three real screenshots would be.
        path.write_bytes(_ONE_PIXEL_PNG + bytes([index]))
        paths.append(str(path))
    return paths


def _choose(page, tmp_path, names=("shot-a.png", "shot-b.png")) -> None:
    page.set_input_files("#otm-file-input", _screenshots(tmp_path, names))
    _wait_until(lambda: not _is_actually_hidden(page, "otm-file-list"))


def _drop(page, tmp_path, names=("shot-a.png", "shot-b.png")) -> None:
    """Drop files onto the zone the way a trader drags them in."""

    paths = _screenshots(tmp_path, names)
    payloads = [
        {"name": os.path.basename(path), "mimeType": "image/png", "buffer": open(path, "rb").read()}
        for path in paths
    ]
    handle = page.evaluate_handle("() => new DataTransfer()")
    for payload in payloads:
        page.evaluate(
            """([transfer, name, bytes]) => {
                const file = new File([new Uint8Array(bytes)], name, { type: "image/png" });
                transfer.items.add(file);
            }""",
            [handle, payload["name"], list(payload["buffer"])],
        )
    page.dispatch_event("#otm-dropzone", "drop", {"dataTransfer": handle})
    _wait_until(lambda: not _is_actually_hidden(page, "otm-file-list"))


# ---------------------------------------------------------------------------
# View switching
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_the_otm_nav_item_shows_its_view_and_hides_the_others(page, server_url) -> None:
    _open_otm_view(page, server_url)

    assert _is_actually_hidden(page, "view-pricing")
    assert _is_actually_hidden(page, "view-markets")
    assert _is_actually_hidden(page, "view-capture")
    assert _is_actually_hidden(page, "app-footer")
    assert "active" in page.get_attribute("#nav-capture-otm", "class")


@_PLAYWRIGHT_SKIP
def test_the_atm_capture_view_still_works_beside_the_new_one(page, server_url) -> None:
    _open_otm_view(page, server_url)

    page.click("#nav-capture")
    _wait_until(lambda: not _is_actually_hidden(page, "view-capture"))

    assert _is_actually_hidden(page, "view-capture-otm")
    assert "active" in page.get_attribute("#nav-capture", "class")


# ---------------------------------------------------------------------------
# Assembling one capture session
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_several_files_can_be_chosen_at_once_through_the_picker(page, server_url, tmp_path) -> None:
    _open_otm_view(page, server_url)

    _choose(page, tmp_path, ("shot-a.png", "shot-b.png", "shot-c.png"))

    names = page.eval_on_selector_all(".otm-file-name", "els => els.map(el => el.textContent)")
    assert names == ["shot-a.png", "shot-b.png", "shot-c.png"]


@_PLAYWRIGHT_SKIP
def test_several_files_can_be_dragged_on_at_once(page, server_url, tmp_path) -> None:
    _open_otm_view(page, server_url)

    _drop(page, tmp_path, ("shot-a.png", "shot-b.png", "shot-c.png"))

    names = page.eval_on_selector_all(".otm-file-name", "els => els.map(el => el.textContent)")
    assert names == ["shot-a.png", "shot-b.png", "shot-c.png"]


@_PLAYWRIGHT_SKIP
def test_a_file_picked_by_mistake_can_be_removed_before_parsing(
    page, server_url, tmp_path
) -> None:
    posted = _route_json(page, "/api/vcub/otm/parse", _capture_payload())
    _open_otm_view(page, server_url)
    _choose(page, tmp_path, ("shot-a.png", "wrong.png", "shot-b.png"))

    page.click(".otm-file-item:nth-child(2) .otm-file-remove")
    names = page.eval_on_selector_all(".otm-file-name", "els => els.map(el => el.textContent)")
    assert names == ["shot-a.png", "shot-b.png"]

    page.click("#otm-parse-btn")
    # Waited on through the page rather than on ``posted`` alone: the sync
    # Playwright API only pumps its driver while a page call is in flight, so
    # a predicate that touches nothing but Python would never see the route
    # handler run at all.
    _wait_until(lambda: not _is_actually_hidden(page, "otm-compare-card"))

    assert [image["source_reference"] for image in posted[0]["images"]] == [
        "shot-a.png",
        "shot-b.png",
    ]


@_PLAYWRIGHT_SKIP
def test_parse_is_unavailable_until_at_least_one_screenshot_is_chosen(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    assert "is-disabled" in page.get_attribute("#otm-parse-btn", "class")

    _choose(page, tmp_path, ("shot-a.png",))
    assert "is-disabled" not in page.get_attribute("#otm-parse-btn", "class")

    page.click(".otm-file-item:nth-child(1) .otm-file-remove")
    assert "is-disabled" in page.get_attribute("#otm-parse-btn", "class")


@_PLAYWRIGHT_SKIP
def test_the_whole_session_is_posted_in_one_request(page, server_url, tmp_path) -> None:
    posted = _route_json(page, "/api/vcub/otm/parse", _capture_payload())
    _open_otm_view(page, server_url)
    _choose(page, tmp_path)

    page.click("#otm-parse-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "otm-compare-card"))

    assert len(posted) == 1
    assert len(posted[0]["images"]) == 2
    assert base64.b64decode(posted[0]["images"][0]["image_base64"]) == _ONE_PIXEL_PNG + b"\x00"


# ---------------------------------------------------------------------------
# Reviewing the merged capture
# ---------------------------------------------------------------------------


def _parse(page, tmp_path, payload=None) -> None:
    _route_json(page, "/api/vcub/otm/parse", payload or _capture_payload())
    _choose(page, tmp_path)
    page.click("#otm-parse-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "otm-compare-card"))


@_PLAYWRIGHT_SKIP
def test_the_merged_table_is_drawn_row_by_row_and_strike_by_strike(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    headers = page.eval_on_selector_all(
        "#otm-grid-head th", "els => els.map(el => el.textContent)"
    )
    assert headers == ["Term × Tenor", "-100bps", "ATM", "100bps"]

    rows = page.eval_on_selector_all(
        "#otm-grid-body tr",
        "els => els.map(row => Array.from(row.children).map(cell => cell.textContent))",
    )
    assert rows == [
        ["1Mo x 1Yr", "40.50", "75.00", "46.50"],
        ["1Mo x 2Yr", "33.50", "unresolved", "50.25"],
        ["3Mo x 1Yr", "31.00", "83.00", "36.00"],
    ]


@_PLAYWRIGHT_SKIP
def test_every_cell_names_the_coordinate_it_claims_to_be(page, server_url, tmp_path) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    titles = page.eval_on_selector_all(
        "#otm-grid-body tr:nth-child(2) td.num", "els => els.map(el => el.title)"
    )
    assert titles == ["1Mo x 2Yr × -100bps", "1Mo x 2Yr × ATM", "1Mo x 2Yr × 100bps"]


@_PLAYWRIGHT_SKIP
def test_the_atm_column_is_marked_apart_from_the_spread_columns(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    classes = page.eval_on_selector_all(
        "#otm-grid-head th", "els => els.map(el => el.className)"
    )
    assert [("is-atm" in name) for name in classes] == [False, False, True, False]


@_PLAYWRIGHT_SKIP
def test_the_review_shows_one_merged_table_not_one_per_screenshot(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    assert page.eval_on_selector_all("#otm-grid-body tr", "els => els.length") == 3
    assert page.eval_on_selector_all(".otm-grid-table", "els => els.length") == 1
    assert page.eval_on_selector_all("#otm-confirm-btn", "els => els.length") == 1


@_PLAYWRIGHT_SKIP
def test_the_coverage_readout_says_what_each_screenshot_contributed(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    rows = page.eval_on_selector_all(
        "#otm-coverage .otm-coverage-row", "els => els.map(el => el.textContent)"
    )
    assert len(rows) == 2
    assert "shot-a.png" in rows[0]
    assert "1Mo x 1Yr" in rows[0]
    assert "1 shared with another screenshot" in rows[0]


@_PLAYWRIGHT_SKIP
def test_unresolved_metadata_is_shown_as_unresolved_never_as_a_value(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    values = page.eval_on_selector_all(
        ".capture-meta-item", "els => els.map(el => el.textContent)"
    )
    assert any("CurrencyUnresolved" in text for text in values)
    assert any("DisplaySpread" in text for text in values)


@_PLAYWRIGHT_SKIP
def test_every_source_image_is_listed_in_the_provenance(page, server_url, tmp_path) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    provenance = page.eval_on_selector("#otm-provenance", "el => el.textContent")
    assert "shot-a.png" in provenance
    assert "shot-b.png" in provenance
    assert "a" * 64 in provenance
    assert "b" * 64 in provenance


@_PLAYWRIGHT_SKIP
def test_a_complete_capture_says_so_and_stays_confirmable(page, server_url, tmp_path) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)

    assert not _is_actually_hidden(page, "otm-completeness")
    title = page.eval_on_selector("#otm-completeness-title", "el => el.textContent")
    assert title.startswith("Complete")
    assert "is-complete" in page.get_attribute("#otm-completeness", "class")
    assert "is-disabled" not in page.get_attribute("#otm-confirm-btn", "class")


@_PLAYWRIGHT_SKIP
def test_an_incomplete_capture_names_the_missing_rows_and_blocks_confirm(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path, _incomplete_payload())

    assert "is-partial" in page.get_attribute("#otm-completeness", "class")
    title = page.eval_on_selector("#otm-completeness-title", "el => el.textContent")
    assert title == "Incomplete — 3 of 5 expected Term × Tenor rows captured"
    detail = page.eval_on_selector("#otm-completeness-detail", "el => el.textContent")
    assert "3Mo x 2Yr" in detail
    assert "3Mo x 5Yr" in detail
    assert "is-disabled" in page.get_attribute("#otm-confirm-btn", "class")


@_PLAYWRIGHT_SKIP
def test_a_blocking_conflict_is_shown_and_confirmation_is_unavailable(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path, _blocked_payload())

    assert not _is_actually_hidden(page, "otm-blockers")
    blockers = page.eval_on_selector("#otm-blocker-list", "el => el.textContent")
    assert "1Mo x 2Yr × ATM" in blockers
    assert "OVERLAP_VALUE_CONFLICT" in blockers
    assert "is-disabled" in page.get_attribute("#otm-confirm-btn", "class")
    assert page.eval_on_selector("#otm-status-pill", "el => el.textContent") == "Blocked"


# ---------------------------------------------------------------------------
# Confirm, and what durability the page may claim
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_one_confirm_stores_one_snapshot_and_says_so(page, server_url, tmp_path) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)
    posted = _route_json(page, "/api/vcub/otm/confirm", _confirmed_payload(_SAVED_STORAGE))

    page.fill("#otm-reviewed-by", "Eddy")
    page.click("#otm-confirm-btn")
    _wait_until(
        lambda: page.eval_on_selector("#otm-status-pill", "el => el.textContent")
        == "Confirmed & saved"
    )

    assert len(posted) == 1
    assert posted[0] == {"capture_id": "otm-cap-1", "reviewed_by": "Eddy"}
    storage = page.eval_on_selector("#otm-storage", "el => el.textContent")
    assert "3b4b3913f0c48ebda1b1cedc319f08a0" in storage


@_PLAYWRIGHT_SKIP
def test_a_failed_save_never_reads_as_saved_and_offers_a_retry(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)
    _route_json(page, "/api/vcub/otm/confirm", _confirmed_payload(_FAILED_STORAGE))

    page.fill("#otm-reviewed-by", "Eddy")
    page.click("#otm-confirm-btn")
    _wait_until(
        lambda: page.eval_on_selector("#otm-status-pill", "el => el.textContent")
        == "Confirmed — not saved"
    )

    assert "is-disabled" not in page.get_attribute("#otm-confirm-btn", "class")
    assert page.eval_on_selector("#otm-confirm-btn", "el => el.textContent") == "Retry save"
    assert "disk I/O error" in page.eval_on_selector("#otm-storage", "el => el.textContent")


@_PLAYWRIGHT_SKIP
def test_a_confirmation_with_no_storage_answer_is_treated_as_not_saved(
    page, server_url, tmp_path
) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)
    _route_json(page, "/api/vcub/otm/confirm", _confirmed_payload(None))

    page.fill("#otm-reviewed-by", "Eddy")
    page.click("#otm-confirm-btn")
    _wait_until(
        lambda: page.eval_on_selector("#otm-status-pill", "el => el.textContent")
        == "Confirmed — not saved"
    )

    assert "did not report a save" in page.eval_on_selector(
        "#otm-storage", "el => el.textContent"
    )


@_PLAYWRIGHT_SKIP
def test_confirming_requires_a_named_reviewer(page, server_url, tmp_path) -> None:
    _open_otm_view(page, server_url)
    _parse(page, tmp_path)
    posted = _route_json(page, "/api/vcub/otm/confirm", _confirmed_payload(_SAVED_STORAGE))

    page.click("#otm-confirm-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "otm-error"))

    assert posted == []
    assert "Enter who is reviewing it first." in page.eval_on_selector(
        "#otm-error-detail", "el => el.textContent"
    )


@_PLAYWRIGHT_SKIP
def test_changing_the_file_list_clears_the_capture_under_review(
    page, server_url, tmp_path
) -> None:
    """Never leave one session's table on screen beside another's files."""

    _open_otm_view(page, server_url)
    _parse(page, tmp_path)
    assert not _is_actually_hidden(page, "otm-review-card")

    page.click(".otm-file-item:nth-child(1) .otm-file-remove")

    assert _is_actually_hidden(page, "otm-review-card")
    assert _is_actually_hidden(page, "otm-compare-card")


@_PLAYWRIGHT_SKIP
def test_a_server_error_is_shown_rather_than_a_half_rendered_table(
    page, server_url, tmp_path
) -> None:
    _route_json(
        page,
        "/api/vcub/otm/parse",
        {"error": "the same screenshot was supplied more than once in this capture session"},
        status=400,
    )
    _open_otm_view(page, server_url)
    _choose(page, tmp_path)

    page.click("#otm-parse-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "otm-error"))

    assert _is_actually_hidden(page, "otm-review-card")
    assert "supplied more than once" in page.eval_on_selector(
        "#otm-error-detail", "el => el.textContent"
    )
