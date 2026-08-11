"""Browser-driven regression tests for the Markets curve viewer (Issue #167).

Exercises script.js's Markets IIFE -- view switching, the curve fetch/render
cycle, the loading/error/malformed/stale-refresh states, the chart, the
summary strip, and the node table -- against one real ``ThreadingHTTPServer``
(see ``standalone_option_workbench_server``) and one real headless Chromium
page via Playwright. ``POST /api/bloomberg/option-discount-curve`` is
intercepted at the browser network layer with ``page.route`` (the same
pattern ``test_standalone_option_workbench_prototype_browser.py`` already
uses for ``/api/bloomberg/bond``) -- deterministic fake Bloomberg data, no
live Bloomberg, no Python-side monkeypatching of the server module.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test file: locally, missing Playwright is a skip; in CI
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

_PLAYWRIGHT_SKIP = pytest.mark.skipif(
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
    """True only if the element is genuinely not rendered (computed
    ``display: none``), never merely the ``hidden`` IDL property -- see
    ``test_standalone_option_workbench_prototype_browser._is_actually_hidden``
    for why the computed style, not the property, is what actually proves it.
    """

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


# --- Fake curve fixtures -----------------------------------------------------

_FAKE_CURVE = {
    "curve_id": "USD_SOFR_OPTION_DISCOUNT_CURVE",
    "curve_name": "USD SOFR Option Discount Curve (Bloomberg Curve #490)",
    "source_system": "BLOOMBERG_DAPI",
    "rate_basis": "CONTINUOUS_ZERO_RATE",
    "acquired_at": "2026-08-11T12:34:45+08:00",
    "coverage": {
        "first_tenor": "1W",
        "last_tenor": "50Y",
        "first_maturity": "2026-08-18",
        "last_maturity": "2076-08-11",
    },
    "nodes": [
        {
            "tenor": "1W",
            "maturity": "2026-08-18",
            "zero_rate_percent": 3.4525,
            "discount_factor": 0.999280,
            "par_rate_percent": 3.40,
        },
        {
            "tenor": "1Y",
            "maturity": "2027-08-11",
            "zero_rate_percent": 3.8172,
            "discount_factor": 0.972346,
            # No verified par-rate route for this node (Issue #167 RED gate) --
            # must render as unavailable, never fabricated or equal to zero.
            "par_rate_percent": None,
        },
        {
            "tenor": "10Y",
            "maturity": "2036-08-11",
            "zero_rate_percent": 4.2020,
            "discount_factor": 0.653380,
            "par_rate_percent": 4.05,
        },
        {
            "tenor": "30Y",
            "maturity": "2056-08-11",
            "zero_rate_percent": 4.4563,
            "discount_factor": 0.258870,
            "par_rate_percent": 4.30,
        },
    ],
}


def _route_curve(page, payload: dict | None = None, *, status: int = 200, error: str | None = None):
    calls = []

    def _handle(route):
        calls.append(route.request)
        if error is not None:
            route.fulfill(
                status=status, content_type="application/json", body=json.dumps({"error": error})
            )
        else:
            route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/bloomberg/option-discount-curve", _handle)
    return calls


def _goto_markets(page, server_url: str) -> None:
    page.goto(f"{server_url}/")
    page.click("#nav-markets")


# --- Navigation (Acceptance A) -----------------------------------------------


def test_markets_nav_switches_view_and_pricing_switches_back(server_url, page) -> None:
    _route_curve(page, _FAKE_CURVE)
    page.goto(f"{server_url}/")

    assert _is_actually_hidden(page, "view-markets")
    assert not _is_actually_hidden(page, "view-pricing")

    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))
    assert _is_actually_hidden(page, "view-pricing")
    assert _is_actually_hidden(page, "app-footer")
    assert page.eval_on_selector("#nav-markets", "el => el.classList.contains('active')")

    page.click("#nav-pricing")
    _wait_until(lambda: not _is_actually_hidden(page, "view-pricing"))
    assert _is_actually_hidden(page, "view-markets")
    assert not _is_actually_hidden(page, "app-footer")
    assert page.eval_on_selector("#nav-pricing", "el => el.classList.contains('active')")


def test_switching_to_markets_and_back_does_not_disturb_pricing_state(server_url, page) -> None:
    # Regression guard for "preserve existing Pricing behavior" (Issue #167):
    # a trader-entered value in a Pricing control must survive a Markets
    # round trip untouched.
    # #strike-price-input lives inside .workspace, hidden until a bond loads
    # -- #bond-identifier-input is the one Pricing control always visible on
    # a fresh page, so it is the one this guard can reliably touch.
    _route_curve(page, _FAKE_CURVE)
    page.goto(f"{server_url}/")
    page.fill("#bond-identifier-input", "US91282CLJ89")

    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))
    page.click("#nav-pricing")
    _wait_until(lambda: not _is_actually_hidden(page, "view-pricing"))

    assert page.input_value("#bond-identifier-input") == "US91282CLJ89"


# --- Successful load (Acceptance B/C) ----------------------------------------


def test_successful_load_renders_header_chart_summary_and_table(server_url, page) -> None:
    calls = _route_curve(page, _FAKE_CURVE)
    _goto_markets(page, server_url)

    _wait_until(lambda: not _is_actually_hidden(page, "markets-content"))
    assert len(calls) == 1

    assert (
        page.text_content("#markets-curve-meta")
        == "Bloomberg Curve #490 · Bloomberg DAPI · Continuous Zero Rate"
    )
    assert page.text_content("#markets-acquired-at-value") == "2026-08-11T12:34:45+08:00"

    assert page.text_content("#markets-summary-nodes") == "4"
    assert page.text_content("#markets-summary-coverage") == "1W – 50Y"
    assert page.text_content("#markets-summary-1y") == "3.8172%"
    assert page.text_content("#markets-summary-10y") == "4.2020%"
    assert page.text_content("#markets-summary-30y") == "4.4563%"
    assert page.text_content("#markets-summary-source") == "Bloomberg #490"

    node_count = page.eval_on_selector_all(
        "#markets-chart-svg-wrap g.markets-chart-node", "els => els.length"
    )
    assert node_count == len(_FAKE_CURVE["nodes"])

    rows = page.eval_on_selector_all(
        "#markets-table-body tr",
        "rows => rows.map(r => Array.from(r.children).map(c => c.textContent))",
    )
    assert rows == [
        ["1W", "2026-08-18", "3.4000%", "3.4525%", "0.999280"],
        ["1Y", "2027-08-11", "—", "3.8172%", "0.972346"],
        ["10Y", "2036-08-11", "4.0500%", "4.2020%", "0.653380"],
        ["30Y", "2056-08-11", "4.3000%", "4.4563%", "0.258870"],
    ]


def test_table_columns_are_exactly_the_five_required(server_url, page) -> None:
    _route_curve(page, _FAKE_CURVE)
    _goto_markets(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "markets-table-card"))

    headers = page.eval_on_selector_all(
        "#markets-table-card thead th", "els => els.map(e => e.textContent.trim())"
    )
    assert headers == ["Tenor", "Maturity", "SWAP (Par Rate)", "Zero Rate", "DF"]


def test_missing_par_rate_node_shows_unavailable_not_fabricated(server_url, page) -> None:
    _route_curve(page, _FAKE_CURVE)
    _goto_markets(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "markets-table-card"))

    # The 1Y node's par_rate_percent is None -- must render as the literal
    # unavailable marker, never the zero rate (3.8172%) or any other value.
    second_row_swap_cell = page.eval_on_selector(
        "#markets-table-body tr:nth-child(2) td:nth-child(3)", "el => el.textContent"
    )
    assert second_row_swap_cell == "—"


def test_hovering_a_chart_node_shows_tooltip_matching_api_data(server_url, page) -> None:
    _route_curve(page, _FAKE_CURVE)
    _goto_markets(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "markets-content"))

    # Third node in _FAKE_CURVE is 10Y.
    page.hover("#markets-chart-svg-wrap g.markets-chart-node >> nth=2")
    page.wait_for_selector(".markets-chart-tooltip:not([hidden])")
    tooltip_text = page.text_content(".markets-chart-tooltip")
    assert "10Y" in tooltip_text
    assert "2036-08-11" in tooltip_text
    assert "4.2020%" in tooltip_text
    assert "0.653380" in tooltip_text


def test_refresh_calls_the_server_again(server_url, page) -> None:
    calls = _route_curve(page, _FAKE_CURVE)
    _goto_markets(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "markets-content"))
    assert len(calls) == 1

    page.click("#markets-refresh-btn")
    _wait_until(lambda: len(calls) == 2)


# --- Loading / error / malformed states (Acceptance D/E) --------------------


def test_loading_state_shows_before_the_response_arrives(server_url, page) -> None:
    pending = []
    page.route("**/api/bloomberg/option-discount-curve", lambda route: pending.append(route))
    _goto_markets(page, server_url)

    _wait_until(lambda: not _is_actually_hidden(page, "markets-loading"))
    assert _is_actually_hidden(page, "markets-content")
    assert _is_actually_hidden(page, "markets-error")

    pending[0].fulfill(status=200, content_type="application/json", body=json.dumps(_FAKE_CURVE))
    _wait_until(lambda: not _is_actually_hidden(page, "markets-content"))
    assert _is_actually_hidden(page, "markets-loading")


def test_bloomberg_failure_shows_a_clear_error_and_no_sample_data(server_url, page) -> None:
    _route_curve(
        page, error="Bloomberg DAPI session failed to start against localhost:8194", status=502
    )
    _goto_markets(page, server_url)

    _wait_until(lambda: not _is_actually_hidden(page, "markets-error"))
    assert _is_actually_hidden(page, "markets-content")
    assert _is_actually_hidden(page, "markets-table-card")
    assert "Bloomberg DAPI session failed to start" in page.text_content("#markets-error-detail")

    # No hidden sample/fake node text anywhere on the page while erroring.
    table_rows = page.eval_on_selector_all("#markets-table-body tr", "els => els.length")
    assert table_rows == 0


def test_malformed_response_is_treated_as_a_failure_not_a_partial_render(server_url, page) -> None:
    _route_curve(page, {"nodes": []})
    _goto_markets(page, server_url)

    _wait_until(lambda: not _is_actually_hidden(page, "markets-error"))
    assert "malformed" in page.text_content("#markets-error-detail").lower()
    assert _is_actually_hidden(page, "markets-content")


def test_failed_refresh_keeps_showing_stale_data_with_an_explicit_warning(server_url, page) -> None:
    _route_curve(page, _FAKE_CURVE)
    _goto_markets(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "markets-content"))

    page.unroute("**/api/bloomberg/option-discount-curve")
    _route_curve(page, error="Bloomberg DAPI timed out", status=502)
    page.click("#markets-refresh-btn")

    _wait_until(lambda: not _is_actually_hidden(page, "markets-stale-banner"))
    # The last successful data must still be visible -- a failed refresh must
    # never look like a successful one, and must never silently swap in
    # sample data.
    assert not _is_actually_hidden(page, "markets-content")
    assert page.text_content("#markets-stale-acquired-at") == "2026-08-11T12:34:45+08:00"
    assert "Bloomberg DAPI timed out" in page.text_content("#markets-stale-detail")
    # The stale rows are still the last successful ones, not blanked.
    row_count = page.eval_on_selector_all("#markets-table-body tr", "els => els.length")
    assert row_count == len(_FAKE_CURVE["nodes"])
