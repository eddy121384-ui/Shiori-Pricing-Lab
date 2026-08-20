"""Browser-driven regression tests for the Capture view (Issue #181).

Exercises ``vcub_capture.js`` -- view switching, the file -> parse round
trip, the reconstructed ``Expiry x Swap Tenor`` table, the metadata strip,
the blocking/unresolved lists, and the Confirm/Reject gate -- against one
real ``ThreadingHTTPServer`` and one real headless Chromium page.
``POST /api/vcub/atm/parse`` and the two review routes are intercepted at
the browser network layer with ``page.route``, the same pattern the sibling
browser-test files already use, so no OCR engine and no screenshot are
involved.

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

_EXPIRY_LABELS = ["1Mo", "3Mo", "6Mo"]
_TENOR_LABELS = ["1Yr", "2Yr", "4Yr"]


def _capture_payload(**overrides) -> dict:
    capture = {
        "provenance": {
            "source_reference": "vcub_atm_usd.png",
            "source_image_sha256": "a" * 64,
            "source_image_bytes": 4096,
            "captured_at": "2026-08-18T09:30:00Z",
            "parser_name": "shiori-vcub-atm-template",
            "parser_version": "0.1.0",
        },
        "metadata": {
            "currency": "USD",
            "curve_config": "USD RFR BVOL Cube (Default)",
            "side": "Mid",
            "quote_date": "08/18/26",
            "tab": "ATM Swaptions",
            "vol_type": "Normal Vol (OIS)",
            "source": None,
            "unresolved_fields": ["source"],
        },
        "grid": {
            "expiry_labels": list(_EXPIRY_LABELS),
            "tenor_labels": list(_TENOR_LABELS),
            "values": [[80.0, 80.25, 80.75], [81.5, None, 82.0], [83.0, 83.25, 83.75]],
        },
        "blocking_errors": [],
        "warnings": [
            {
                "code": "UNRESOLVED_CELL",
                "message": "3Mo x 2Yr has no resolved value; compare it against the screenshot",
                "expiry": "3Mo",
                "tenor": "2Yr",
            }
        ],
        "review_status": "PENDING_REVIEW",
        "reviewed_by": None,
        "reviewed_at": None,
        "can_confirm": True,
    }
    capture.update(overrides)
    return {"capture_id": "cap-1", "capture": capture, "reader_notes": []}


def _confirmed_payload(storage: dict | None) -> dict:
    """The confirm route's answer, with Issue #183's ``storage`` block.

    ``storage=None`` models an older workbench process that persists
    nothing and says nothing -- the case the page must fail closed on.
    """

    payload = _capture_payload()
    payload["capture"].update(
        {
            "review_status": "CONFIRMED",
            "reviewed_by": "Eddy",
            "reviewed_at": "2026-08-18T09:45:00Z",
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


def _open_capture_view(page, server_url: str) -> None:
    page.goto(server_url)
    page.click("#nav-capture")
    _wait_until(lambda: not _is_actually_hidden(page, "view-capture"))


def _choose_and_parse(page, tmp_path) -> None:
    screenshot = tmp_path / "vcub_atm_usd.png"
    screenshot.write_bytes(_ONE_PIXEL_PNG)
    page.set_input_files("#capture-file-input", str(screenshot))
    _wait_until(lambda: not _is_actually_hidden(page, "capture-file-row"))
    page.click("#capture-parse-btn")


# ---------------------------------------------------------------------------
# View switching
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_the_capture_nav_item_shows_the_capture_view_and_hides_the_others(
    page, server_url
) -> None:
    _open_capture_view(page, server_url)

    assert _is_actually_hidden(page, "view-pricing")
    assert _is_actually_hidden(page, "view-markets")
    assert _is_actually_hidden(page, "app-footer")
    assert "active" in page.get_attribute("#nav-capture", "class")


@_PLAYWRIGHT_SKIP
def test_leaving_the_capture_view_brings_the_pricing_view_back(page, server_url) -> None:
    _open_capture_view(page, server_url)

    page.click("#nav-pricing")
    _wait_until(lambda: _is_actually_hidden(page, "view-capture"))

    assert not _is_actually_hidden(page, "view-pricing")
    assert not _is_actually_hidden(page, "app-footer")


@_PLAYWRIGHT_SKIP
def test_the_markets_view_still_works_beside_the_new_capture_view(page, server_url) -> None:
    _open_capture_view(page, server_url)

    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))

    assert _is_actually_hidden(page, "view-capture")
    assert _is_actually_hidden(page, "view-pricing")


# ---------------------------------------------------------------------------
# Parse and render
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_parsing_renders_the_expiry_by_tenor_topology_of_the_source_screen(
    page, server_url, tmp_path
) -> None:
    posted = _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _open_capture_view(page, server_url)

    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-compare-card"))

    assert posted[0]["source_reference"] == "vcub_atm_usd.png"
    assert base64.b64decode(posted[0]["image_base64"]) == _ONE_PIXEL_PNG

    headers = page.eval_on_selector_all(
        "#capture-grid-head th", "els => els.map(e => e.textContent)"
    )
    assert headers == ["Expiry"] + _TENOR_LABELS
    rows = page.eval_on_selector_all(
        "#capture-grid-body tr",
        "els => els.map(row => Array.from(row.children).map(cell => cell.textContent))",
    )
    assert rows == [
        ["1Mo", "80.00", "80.25", "80.75"],
        ["3Mo", "81.50", "unresolved", "82.00"],
        ["6Mo", "83.00", "83.25", "83.75"],
    ]


@pytest.mark.parametrize(
    "expiry,tenor,expected", [("1Mo", "1Yr", "80.00"), ("6Mo", "4Yr", "83.75")]
)
@_PLAYWRIGHT_SKIP
def test_each_rendered_cell_names_the_intersection_it_belongs_to(
    page, server_url, tmp_path, expiry, tenor, expected
) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-compare-card"))

    cells = page.eval_on_selector_all(
        "#capture-grid-body td.num",
        "els => els.map(e => [e.title, e.textContent])",
    )

    assert [f"{expiry} × {tenor}", expected] in cells


@_PLAYWRIGHT_SKIP
def test_a_negative_zero_cell_is_not_rendered_as_a_positive_zero(
    page, server_url, tmp_path
) -> None:
    """Codex review, PR #182.

    JavaScript hides signed zero twice: ``(-0).toFixed(2)`` is ``"0.00"`` and
    ``Number("0.00") === -0`` is true, so a naive round-trip check calls the
    rendering lossless while showing a different number from the one the
    bridge holds. The bridge now treats -0.0 and 0.0 as distinct captures, so
    the page must not collapse them back together.
    """

    payload = _capture_payload()
    payload["capture"]["grid"]["values"] = [
        [-0.0, 0.0, 80.75],
        [81.5, None, 82.0],
        [83.0, 83.25, 85.153],
    ]
    _route_json(page, "/api/vcub/atm/parse", payload)
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-compare-card"))

    rows = page.eval_on_selector_all(
        "#capture-grid-body tr",
        "els => els.map(row => Array.from(row.children).map(cell => cell.textContent))",
    )

    assert rows[0][1] == "-0.00"
    assert rows[0][2] == "0.00"
    assert rows[2][3] == "85.153"  # never silently rounded either


@_PLAYWRIGHT_SKIP
def test_the_screenshot_cannot_be_swapped_while_a_parse_is_in_flight(
    page, server_url, tmp_path
) -> None:
    """Codex review, PR #182.

    Left live, choosing another screenshot mid-request swapped the selection
    and the preview under the response still on its way, so one file's grid
    could render beside another file's image and Confirm would apply to a
    capture the trader was no longer looking at.
    """

    # The handler never fulfils, so the request stays genuinely in flight
    # while the assertions run. Blocking *inside* the handler instead would
    # stall Playwright's own dispatch loop, and the request would already
    # have completed by the time the page could be inspected.
    page.route("**/api/vcub/atm/parse", lambda route, request: None)
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-loading"))

    assert page.eval_on_selector("#capture-file-input", "el => el.disabled") is True
    assert "is-disabled" in page.get_attribute('label[for="capture-file-input"]', "class")
    # The picker being frozen is what makes the swap impossible; the response
    # is pinned to its own request as a second, independent guard.
    assert page.eval_on_selector("#capture-parse-btn", "el => el.className").find(
        "is-disabled"
    ) >= 0


@_PLAYWRIGHT_SKIP
def test_the_source_screenshot_is_shown_beside_the_reconstructed_grid(
    page, server_url, tmp_path
) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _open_capture_view(page, server_url)

    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-compare-card"))

    assert page.eval_on_selector("#capture-shot-image", "el => el.naturalWidth") == 1
    shot_box = page.eval_on_selector("#capture-shot-image", "el => el.getBoundingClientRect().left")
    grid_box = page.eval_on_selector(
        ".capture-grid-wrap", "el => el.getBoundingClientRect().left"
    )
    assert shot_box < grid_box


@_PLAYWRIGHT_SKIP
def test_an_unresolved_metadata_field_is_shown_as_unresolved_not_invented(
    page, server_url, tmp_path
) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    pairs = page.eval_on_selector_all(
        ".capture-meta-item",
        "els => els.map(e => [e.querySelector('.capture-meta-k').textContent,"
        " e.querySelector('.capture-meta-v').textContent])",
    )

    assert ["Vol Type", "Normal Vol (OIS)"] in pairs
    assert ["Source", "Unresolved"] in pairs
    assert ["Currency", "USD"] in pairs


@_PLAYWRIGHT_SKIP
def test_an_unresolved_cell_is_listed_for_the_trader_to_check(page, server_url, tmp_path) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-warnings"))

    text = page.text_content("#capture-warning-list")
    assert "3Mo × 2Yr" in text
    assert "UNRESOLVED_CELL" in text


@_PLAYWRIGHT_SKIP
def test_a_bridge_error_is_shown_verbatim_and_renders_no_grid(page, server_url, tmp_path) -> None:
    _route_json(
        page,
        "/api/vcub/atm/parse",
        {"error": 'run: .venv\\Scripts\\python -m pip install -e ".[capture]"'},
        status=501,
    )
    _open_capture_view(page, server_url)

    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-error"))

    assert 'pip install -e ".[capture]"' in page.text_content("#capture-error-detail")
    assert _is_actually_hidden(page, "capture-compare-card")


# ---------------------------------------------------------------------------
# Confirm / reject
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_confirm_is_unavailable_while_the_capture_has_a_blocking_error(
    page, server_url, tmp_path
) -> None:
    blocked = _capture_payload(
        blocking_errors=[
            {
                "code": "CELL_POSITION_AMBIGUOUS",
                "message": "'85.15' sits on a column boundary",
                "expiry": "3Mo",
                "tenor": "4Yr",
            }
        ],
        can_confirm=False,
    )
    _route_json(page, "/api/vcub/atm/parse", blocked)
    _open_capture_view(page, server_url)

    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-blockers"))

    assert "is-disabled" in page.get_attribute("#capture-confirm-btn", "class")
    assert "CELL_POSITION_AMBIGUOUS" in page.text_content("#capture-blocker-list")
    assert page.text_content("#capture-status-pill").strip() == "Blocked"


@_PLAYWRIGHT_SKIP
def test_confirming_sends_the_capture_id_and_reviewer_and_shows_the_result(
    page, server_url, tmp_path
) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    posted = _route_json(
        page, "/api/vcub/atm/confirm", _confirmed_payload(_SAVED_STORAGE)
    )
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-confirm-btn")
    _wait_until(
        lambda: page.text_content("#capture-status-pill").strip() == "Confirmed & saved"
    )

    assert posted == [{"capture_id": "cap-1", "reviewed_by": "Eddy"}]
    assert "Confirmed by Eddy" in page.text_content("#capture-review-status")
    assert "is-disabled" in page.get_attribute("#capture-confirm-btn", "class")


@_PLAYWRIGHT_SKIP
def test_a_saved_surface_shows_the_identity_it_was_stored_under(
    page, server_url, tmp_path
) -> None:
    """Issue #183: the trader must be able to see *which* surface was saved."""

    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _route_json(page, "/api/vcub/atm/confirm", _confirmed_payload(_SAVED_STORAGE))
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-confirm-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "capture-storage"))

    detail = page.text_content("#capture-storage-detail")
    assert _SAVED_STORAGE["surface_id"] in detail
    assert "points: 9" in detail
    assert "/repo/data/vol_surfaces.sqlite3" in detail
    assert "saved to the local vol-surface store" in page.text_content("#capture-storage-title")
    assert "survives a workbench restart" in page.text_content("#capture-review-status")


@_PLAYWRIGHT_SKIP
def test_a_surface_that_was_already_stored_still_reads_as_saved(
    page, server_url, tmp_path
) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _route_json(
        page,
        "/api/vcub/atm/confirm",
        _confirmed_payload({**_SAVED_STORAGE, "status": "ALREADY_SAVED"}),
    )
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-confirm-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "capture-storage"))

    assert page.text_content("#capture-status-pill").strip() == "Confirmed & saved"
    assert "already stored" in page.text_content("#capture-storage-title")


@_PLAYWRIGHT_SKIP
def test_a_storage_failure_is_never_shown_as_a_saved_surface(
    page, server_url, tmp_path
) -> None:
    """Issue #183: a failed write must not be dressed up as durable acceptance."""

    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _route_json(
        page,
        "/api/vcub/atm/confirm",
        _confirmed_payload(
            {
                "status": "FAILED",
                "surface_id": None,
                "point_count": None,
                "error": "the vol-surface store refused the write: disk I/O error",
                "database": "/repo/data/vol_surfaces.sqlite3",
            }
        ),
    )
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-confirm-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "capture-storage"))

    assert page.text_content("#capture-status-pill").strip() == "Confirmed — not saved"
    assert "NOT saved" in page.text_content("#capture-storage-title")
    assert "disk I/O error" in page.text_content("#capture-storage-detail")
    assert "lost on restart" in page.text_content("#capture-review-status")


@_PLAYWRIGHT_SKIP
def test_a_confirmation_with_no_storage_answer_is_treated_as_not_saved(
    page, server_url, tmp_path
) -> None:
    """An older workbench process persists nothing; the page must not assume it did."""

    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _route_json(page, "/api/vcub/atm/confirm", _confirmed_payload(None))
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-confirm-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "capture-storage"))

    assert page.text_content("#capture-status-pill").strip() == "Confirmed — not saved"
    assert "did not report a save" in page.text_content("#capture-storage-title")


@_PLAYWRIGHT_SKIP
def test_a_rejection_shows_no_storage_block_at_all(page, server_url, tmp_path) -> None:
    """A rejected capture is never offered to the canonical store."""

    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    rejected = _capture_payload()
    rejected["capture"].update(
        {
            "review_status": "REJECTED",
            "reviewed_by": "Eddy",
            "reviewed_at": "2026-08-18T09:45:00Z",
            "can_confirm": False,
        }
    )
    rejected["storage"] = {
        "status": "NOT_ATTEMPTED",
        "surface_id": None,
        "point_count": None,
        "error": None,
        "database": "/repo/data/vol_surfaces.sqlite3",
    }
    _route_json(page, "/api/vcub/atm/reject", rejected)
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-reject-btn")
    _wait_until(lambda: page.text_content("#capture-status-pill").strip() == "Rejected")

    assert _is_actually_hidden(page, "capture-storage")


@_PLAYWRIGHT_SKIP
def test_rejecting_says_no_value_was_accepted(page, server_url, tmp_path) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    rejected = _capture_payload()
    rejected["capture"].update(
        {
            "review_status": "REJECTED",
            "reviewed_by": "Eddy",
            "reviewed_at": "2026-08-18T09:45:00Z",
            "can_confirm": False,
        }
    )
    _route_json(page, "/api/vcub/atm/reject", rejected)
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-reject-btn")
    _wait_until(lambda: page.text_content("#capture-status-pill").strip() == "Rejected")

    assert "No captured value was accepted" in page.text_content("#capture-review-status")


@_PLAYWRIGHT_SKIP
def test_a_review_without_a_reviewer_name_never_reaches_the_bridge(
    page, server_url, tmp_path
) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    posted = _route_json(page, "/api/vcub/atm/confirm", _capture_payload())
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.click("#capture-confirm-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "capture-error"))

    assert posted == []
    assert "Enter who is reviewing it" in page.text_content("#capture-error-detail")


@_PLAYWRIGHT_SKIP
def test_a_failed_confirm_leaves_the_trader_able_to_try_again(
    page, server_url, tmp_path
) -> None:
    """Codex review, PR #182.

    Confirm and Reject were disabled on the way into a request and restored
    only on the success path, so a failed confirm left both dead: the capture
    stayed pending with no way to retry short of reparsing the screenshot.
    """

    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _route_json(page, "/api/vcub/atm/confirm", {"error": "bridge unavailable"}, status=500)
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-review-card"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-confirm-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "capture-error"))

    assert "bridge unavailable" in page.text_content("#capture-error-detail")
    # Still pending, and still actionable.
    assert page.text_content("#capture-status-pill").strip() == "Pending review"
    assert "is-disabled" not in page.get_attribute("#capture-confirm-btn", "class")
    assert "is-disabled" not in page.get_attribute("#capture-reject-btn", "class")
    assert page.eval_on_selector("#capture-reviewed-by", "el => el.disabled") is False


@_PLAYWRIGHT_SKIP
def test_a_failed_confirm_on_a_blocked_capture_does_not_become_confirmable(
    page, server_url, tmp_path
) -> None:
    """Restoring the actions must restore them to what the capture allows,
    never simply re-enable both."""

    blocked = _capture_payload(
        blocking_errors=[
            {
                "code": "CELL_POSITION_AMBIGUOUS",
                "message": "'85.15' sits on a column boundary",
                "expiry": "3Mo",
                "tenor": "4Yr",
            }
        ],
        can_confirm=False,
    )
    _route_json(page, "/api/vcub/atm/parse", blocked)
    _route_json(page, "/api/vcub/atm/reject", {"error": "bridge unavailable"}, status=500)
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-blockers"))

    page.fill("#capture-reviewed-by", "Eddy")
    page.click("#capture-reject-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "capture-error"))

    assert "is-disabled" not in page.get_attribute("#capture-reject-btn", "class")
    assert "is-disabled" in page.get_attribute("#capture-confirm-btn", "class")


@_PLAYWRIGHT_SKIP
def test_choosing_another_screenshot_clears_the_previous_reconstruction(
    page, server_url, tmp_path
) -> None:
    _route_json(page, "/api/vcub/atm/parse", _capture_payload())
    _open_capture_view(page, server_url)
    _choose_and_parse(page, tmp_path)
    _wait_until(lambda: not _is_actually_hidden(page, "capture-compare-card"))

    other = tmp_path / "vcub_atm_other.png"
    other.write_bytes(_ONE_PIXEL_PNG)
    page.set_input_files("#capture-file-input", str(other))
    _wait_until(lambda: _is_actually_hidden(page, "capture-compare-card"))

    assert _is_actually_hidden(page, "capture-review-card")
    assert page.text_content("#capture-file-name") == "vcub_atm_other.png"
