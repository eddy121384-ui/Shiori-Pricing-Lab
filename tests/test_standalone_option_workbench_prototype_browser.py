"""Browser-driven regression tests for the Codex review follow-up round on
PR #136: the in-flight pricing race and the unified failure-cleanup path.

These specifically exercise script.js's runtime behavior (request
generation/staleness, AbortController, and the single renderFailure path),
which cannot be proven by a pure-Python test -- they drive one real
``ThreadingHTTPServer`` (see ``standalone_option_workbench_server``) in a
background thread and one real headless Chromium page via Playwright,
intercepting ``/api/base`` / ``/api/price`` with ``page.route`` to control
response timing and shape deterministically. No pricing math is exercised
or asserted here; the mocked payloads only need the few display-dict keys
script.js reads.

Skipped entirely if Playwright is not installed -- it is optional test
tooling, not a declared project dependency, exactly like the QuantLib skip
already used elsewhere in this test suite.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from collections.abc import Iterator

import pytest

from shiori_pricing_lab.app.standalone_option_workbench_server import create_server

_PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None
_PLAYWRIGHT_SKIP = pytest.mark.skipif(
    not _PLAYWRIGHT_AVAILABLE, reason="playwright is not installed in this environment"
)

if _PLAYWRIGHT_AVAILABLE:
    from playwright.sync_api import sync_playwright

_CHROMIUM_PATH = "/opt/pw-browsers/chromium"


def _fake_display(premium_per_100: float, status: str = "SUCCESS") -> dict:
    return {
        "status": status,
        "total_notional_model_fair_premium": premium_per_100 / 2.0,
        "model_fair_premium_per_100": premium_per_100,
        "result_currency": "USD",
        "forward_price_delta_per_100": 0.1,
        "forward_price_gamma_per_100": 0.01,
        "vega_per_vol_point_per_100": 0.2,
        "theta_per_calendar_day_per_100": -0.01,
        "errors": [],
    }


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
        browser = p.chromium.launch(executable_path=_CHROMIUM_PATH)
        pg = browser.new_page(viewport={"width": 1672, "height": 941})
        yield pg
        browser.close()


@_PLAYWRIGHT_SKIP
def test_older_price_response_does_not_overwrite_a_newer_request(server_url, page) -> None:
    """An earlier Price click's slow response must not overwrite a later click's result."""

    page.goto(f"{server_url}/")
    page.wait_for_timeout(200)

    # Delay the FIRST (should-lose) request's fulfillment on the Python side
    # so it resolves after the second (should-win) request even though it
    # was sent first -- proving the guard is generation-based, not merely
    # "last response wins by arrival order coincidence".
    def handle_price_with_delay(route):
        body = json.loads(route.request.post_data)
        if body["strike_price"] == 100.0:
            time.sleep(0.4)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_fake_display(111.0)),
            )
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_fake_display(222.0)),
            )

    page.route("**/api/price", handle_price_with_delay)

    page.fill("#strike-price-input", "100.0")
    page.click("#price-btn")  # request A: strike 100 -> delayed, premium 111
    page.wait_for_timeout(50)
    page.fill("#strike-price-input", "200.0")
    page.click("#price-btn")  # request B: strike 200 -> fast, premium 222

    page.wait_for_timeout(700)  # long enough for both requests to settle

    assert page.inner_text("#price-per-100") == "222.000000"


@_PLAYWRIGHT_SKIP
def test_clear_invalidates_a_pending_price_request(server_url, page) -> None:
    """Clicking Clear while a Price request is in flight must win the race."""

    def handle_price_slow(route):
        time.sleep(0.4)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_display(999.0)),
        )

    page.goto(f"{server_url}/")
    page.wait_for_timeout(200)
    base_price_per_100 = page.inner_text("#price-per-100")

    page.route("**/api/price", handle_price_slow)
    page.fill("#strike-price-input", "123.0")
    page.click("#price-btn")
    page.wait_for_timeout(50)
    page.click("#clear-btn")

    page.wait_for_timeout(700)  # long enough for the slow response to arrive

    # The slow Price response (premium 999) must never have rendered --
    # Clear's own base-case result must be what's on screen.
    assert page.inner_text("#price-per-100") == base_price_per_100
    assert "999" not in page.inner_text("#price-per-100")


@_PLAYWRIGHT_SKIP
def test_http_400_clears_stale_results_and_shows_error(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(200)
    # Confirm a real successful result is showing first, so clearing it is
    # actually exercised.
    assert page.inner_text("#price-per-100") != "—"

    page.route(
        "**/api/price",
        lambda route: route.fulfill(
            status=400, content_type="application/json", body=json.dumps({"error": "bad input"})
        ),
    )
    page.click("#price-btn")
    page.wait_for_timeout(200)

    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#price-per-100") == "—"
    assert page.inner_text("#greek-delta") == "—"
    assert page.inner_text("#pricing-error-banner") != ""
    assert page.eval_on_selector("#status-indicator", "el => el.classList.contains('failed')")


@_PLAYWRIGHT_SKIP
def test_network_level_fetch_rejection_clears_stale_results(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(200)
    assert page.inner_text("#price-per-100") != "—"

    page.route("**/api/price", lambda route: route.abort())
    page.click("#price-btn")
    page.wait_for_timeout(200)

    assert page.inner_text("#price-per-100") == "—"
    assert page.inner_text("#greek-theta") == "—"
    assert page.inner_text("#pricing-error-banner") != ""
    assert page.eval_on_selector("#status-indicator", "el => el.classList.contains('failed')")


@_PLAYWRIGHT_SKIP
def test_invalid_json_response_clears_stale_results(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(200)
    assert page.inner_text("#price-per-100") != "—"

    page.route(
        "**/api/price",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body="not actually json"
        ),
    )
    page.click("#price-btn")
    page.wait_for_timeout(200)

    assert page.inner_text("#price-per-100") == "—"
    assert page.inner_text("#greek-vega") == "—"
    assert page.inner_text("#pricing-error-banner") != ""
    assert page.eval_on_selector("#status-indicator", "el => el.classList.contains('failed')")


@_PLAYWRIGHT_SKIP
def test_base_load_failure_never_shows_loaded_status(server_url, page) -> None:
    page.route(
        "**/api/base",
        lambda route: route.fulfill(
            status=500, content_type="application/json", body=json.dumps({"error": "boom"})
        ),
    )
    page.goto(f"{server_url}/")
    page.wait_for_timeout(300)

    assert page.inner_text("#status-text") != "Local synthetic case loaded"
    assert page.eval_on_selector("#status-indicator", "el => el.classList.contains('failed')")
    assert page.inner_text("#pricing-error-banner") != ""
