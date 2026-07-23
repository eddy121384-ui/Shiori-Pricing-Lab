"""Browser-driven regression tests for the manual workbench prototype
(PR #136): bootstrap lifecycle, in-flight pricing races, and the unified
failure-cleanup path.

These specifically exercise script.js's runtime behavior (bootstrap
enable/disable gating, request generation/staleness, AbortController, and
the single renderFailure path), which cannot be proven by a pure-Python
test -- they drive one real ``ThreadingHTTPServer`` (see
``standalone_option_workbench_server``) in a background thread and one
real headless Chromium page via Playwright. No pricing math is exercised
or asserted here; the mocked payloads only need the few display-dict keys
script.js reads.

**Race-condition tests are non-blocking (Codex final re-review fix).** An
earlier version of this file called ``time.sleep()`` directly inside a
synchronous ``page.route`` handler to "delay" a response. That blocks the
Playwright driver's background thread -- the same thread that services
``page.click()`` -- so the *second* click could itself stall until the
sleep finished, meaning the two requests might never actually race; the
test could pass even with the generation/abort protection removed. The
tests below instead **hold a route open**: the handler appends the
intercepted ``route`` to a list and returns immediately without calling
``route.fulfill()``/``route.continue_()`` -- the underlying HTTP request
genuinely stays pending, unresolved, with no thread blocked -- and the test
calls ``.fulfill()``/``.continue_()`` on that captured route explicitly,
later, from the main test thread, only after the *newer* action has
already completed and rendered. This proves the older response arrives
strictly after the newer one and still cannot overwrite it.

**CI must not silently skip these tests.** Locally, missing Playwright is a
skip (optional test tooling, not a declared project dependency, same
pattern as the QuantLib skip elsewhere in this suite). In CI (detected via
the standard ``CI=true`` environment variable GitHub Actions sets), a
missing Playwright install is a hard collection-time error instead --
merge protection cannot depend on a check that quietly no-ops.

The Chromium executable path is never hardcoded: by default this file lets
Playwright's own browser discovery find the ``playwright install``-managed
browser (what CI uses), with an optional ``PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH``
environment variable escape hatch for a local sandbox whose pinned browser
cache doesn't line up with a freshly-installed Playwright package version.
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


def _fake_context(**overrides) -> dict:
    context = {
        "underlying_isin": "XS9999999999",
        "expiry_date": "2026-09-29",
        "settlement_lag_days": 2,
        "issuer": "Uploaded Issuer X",
        "currency": "USD",
        "coupon": 0.04,
        "maturity_date": "2031-01-01",
        "coupon_frequency": "SEMI_ANNUAL",
        "day_count": "ACT_ACT_ISDA",
        "bond_type": "FIXED_COUPON_BULLET",
        "quote_side": "MID",
        "clean_price_per_100": 102.0,
        "accrued_interest_per_100": 0.5,
        "yield_value": None,
        "valuation_date": "2026-07-01",
        "pricing_timestamp": "2026-07-01T16:00:00Z",
        "expiry_timestamp": "2026-09-29T16:00:00Z",
        "forward_settlement_date": "2026-10-01",
        "option_settlement_date": "2026-10-01",
        "source_system": "TEST_SOURCE",
        "as_of_timestamp": "2026-07-01T16:00:00Z",
        "snapshot_id": "TEST-SNAPSHOT",
    }
    context.update(overrides)
    return context


def _fake_overlay(**overrides) -> dict:
    overlay = {
        "option_type": "CALL",
        "position": "BUY",
        "strike_price": 77.0,
        "notional": 25.0,
        "volatility": 0.2,
        "forward_clean_price_per_100": 103.0,
    }
    overlay.update(overrides)
    return overlay


def _fake_case_load_payload(
    issuer: str = "Uploaded Issuer X",
    premium_per_100: float = 333.0,
    status: str = "SUCCESS",
    case_marker: str | None = None,
) -> dict:
    return {
        "case": {"marker": case_marker or issuer},
        "overlay": _fake_overlay(),
        "context": _fake_context(issuer=issuer),
        "display": _fake_display(premium_per_100, status=status),
    }


def _upload_fake_case_file(page, name: str = "case.json") -> None:
    page.set_input_files(
        "#case-file-input",
        files=[{"name": name, "mimeType": "application/json", "buffer": b'{"placeholder": true}'}],
    )


def _wait_until(predicate, timeout: float = 8.0, interval: float = 0.02) -> None:
    """Poll ``predicate`` from the test thread until it's true or time out.

    This is test-side polling only -- it never runs inside a Playwright
    route handler, so it cannot reintroduce the blocking-handler bug this
    file exists to fix (see module docstring).
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _wait_until_via_page(page, predicate, timeout: float = 8.0, interval_ms: int = 20) -> None:
    """Like :func:`_wait_until`, but pumps Playwright's own event loop
    between polls via ``page.wait_for_timeout()`` instead of a bare
    ``time.sleep()``.

    Needed specifically when ``predicate`` depends on a route interception
    for a request whose body is a raw ``ArrayBuffer`` (our ``/api/case``
    upload's Content-Type) rather than a JSON string: empirically, Chromium's
    ``Fetch.requestPaused`` delivery to a *held-open*, unresolved route's
    Python-side handler for such a request was observed to stall for several
    seconds under a bare ``time.sleep()`` polling loop on the test's own
    thread (the request only appeared once *something* pumped Playwright's
    connection -- a page-level wait call did; wall-clock time passing alone,
    polled from a plain Python loop, did not). This does not reintroduce the
    blocking-route-handler bug the module docstring describes -- the wait
    happens on the test's own thread between polls, never inside the route
    handler itself, exactly like ``_wait_until`` above.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(interval_ms)
    raise AssertionError(f"condition not met within {timeout}s")


def _wait_bootstrap_ready(page) -> None:
    _wait_until(
        lambda: not page.eval_on_selector(
            "#price-btn", "el => el.classList.contains('is-disabled')"
        )
    )


def _goto_and_capture_route(page, url: str, route_pattern: str, pending: list, attempts: int = 3):
    """Navigate to ``url`` with ``route_pattern`` intercepted into ``pending``.

    Registering ``page.route()`` immediately before the very first
    ``page.goto()`` has a known rare Playwright/CDP-level race: the
    interception can occasionally not yet be armed for the page's earliest
    sub-resource requests, so the intercepted request never reaches our
    handler *and* never completes normally either (confirmed empirically:
    0/25 misses with no routing at all vs. an occasional miss with routing
    registered right before the first goto). This is a test-harness timing
    artifact, not a product bug -- retried here by reloading (routes stay
    registered across a reload) rather than by adding any retry to the
    application itself.
    """

    page.route(route_pattern, lambda route: pending.append(route))
    page.goto(url)
    for attempt in range(attempts):
        try:
            _wait_until(lambda: len(pending) >= 1, timeout=2.0)
            return
        except AssertionError:
            if attempt == attempts - 1:
                raise
            pending.clear()
            page.reload()


def _is_disabled(page, selector: str) -> bool:
    return page.eval_on_selector(selector, "el => el.classList.contains('is-disabled')")


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


# --- Bootstrap lifecycle -----------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_controls_disabled_before_bootstrap_completes(server_url, page) -> None:
    pending = []
    _goto_and_capture_route(page, f"{server_url}/", "**/api/base", pending)

    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#clear-btn")
    # Issue #138: Load Case JSON is gated the same way -- disabled both
    # visually (is-disabled on its label) and at the DOM level (the
    # underlying file input's own disabled attribute), so it cannot be used
    # before there is a valid active base to load a replacement case into.
    assert _is_disabled(page, "#load-case-label")
    assert page.eval_on_selector("#case-file-input", "el => el.disabled")
    assert page.inner_text("#status-text") != "Local synthetic case loaded"

    pending[0].continue_()  # let the real /api/base request through, cleanly


@_PLAYWRIGHT_SKIP
def test_controls_enabled_after_bootstrap_succeeds(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    assert not _is_disabled(page, "#price-btn")
    assert not _is_disabled(page, "#clear-btn")
    assert not _is_disabled(page, "#load-case-label")
    assert not page.eval_on_selector("#case-file-input", "el => el.disabled")
    assert page.inner_text("#instr-title") == "Synthetic Test Issuer A"
    assert page.inner_text("#price-per-100") != "—"


@_PLAYWRIGHT_SKIP
def test_early_click_during_bootstrap_does_not_break_initialization(server_url, page) -> None:
    """A Price/Clear click that fires before bootstrap resolves must no-op,
    and bootstrap must still complete normally once its real response
    arrives -- it must never get stuck half-initialized."""

    pending = []
    _goto_and_capture_route(page, f"{server_url}/", "**/api/base", pending)

    # force=True bypasses Playwright's pointer-events:none actionability
    # check, deliberately modeling a non-standard trigger (not just a
    # normal user click on a visually-dimmed button) -- the JS-level
    # bootstrapReady guard, not the CSS, is what must protect this.
    page.click("#price-btn", force=True)
    page.click("#clear-btn", force=True)
    page.wait_for_timeout(100)

    assert page.inner_text("#status-text") != "Local synthetic case loaded"
    assert page.inner_text("#pricing-error-banner") == ""

    pending[0].continue_()  # now let the real bootstrap response through

    _wait_bootstrap_ready(page)
    assert page.inner_text("#instr-title") == "Synthetic Test Issuer A"
    assert page.inner_text("#price-per-100") != "—"
    assert not _is_disabled(page, "#clear-btn")


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
    # Controls must stay disabled forever on a bootstrap failure -- there is
    # no base overlay/display to price or clear to.
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#clear-btn")


@_PLAYWRIGHT_SKIP
def test_base_load_http_200_with_failed_display_never_enables_controls(server_url, page) -> None:
    """A well-formed HTTP 200 /api/base response can still carry a FAILED
    PricingResult (the base case itself failed to price) -- that is a
    bootstrap failure exactly like a non-2xx response, and must be handled
    identically: show the real error, clear results, and never enable
    Price/Clear or cache a base state to price/clear against."""

    page.route(
        "**/api/base",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "overlay": {},
                    "context": {},
                    "display": _fake_display(0.0, status="FAILED"),
                }
            ),
        ),
    )
    price_requests = []
    page.route("**/api/case/price", lambda route: price_requests.append(route))

    page.goto(f"{server_url}/")
    page.wait_for_timeout(300)

    assert page.inner_text("#status-text") != "Local synthetic case loaded"
    assert page.eval_on_selector("#status-indicator", "el => el.classList.contains('failed')")
    assert page.inner_text("#pricing-error-banner") != ""
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#price-per-100") == "—"
    assert page.inner_text("#greek-delta") == "—"
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#clear-btn")

    # Even a forced click (bypassing the disabled-button actionability check,
    # modeling a non-standard trigger) must not send a pricing request -- the
    # JS-level bootstrapReady guard, not the CSS, is what protects this, and
    # it must never have flipped true on a FAILED base display.
    page.click("#price-btn", force=True)
    page.click("#clear-btn", force=True)
    page.wait_for_timeout(200)

    assert price_requests == []
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#clear-btn")


# --- In-flight pricing race (non-blocking route-holding) --------------------


@_PLAYWRIGHT_SKIP
def test_older_price_response_does_not_overwrite_a_newer_request(server_url, page) -> None:
    """An earlier Price click's response, held open and released only after
    a later click has already rendered, must not overwrite that render."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    pending = []

    def route_price(route):
        body = json.loads(route.request.post_data)
        if body["overlay"]["strike_price"] == 100.0:
            pending.append(route)  # hold this one open -- do not resolve yet
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_fake_display(222.0)),
            )

    page.route("**/api/case/price", route_price)

    # 1. First Price request sent (strike 100) -- its response stays pending.
    page.fill("#strike-price-input", "100.0")
    page.click("#price-btn")
    _wait_until(lambda: len(pending) == 1)

    # 2. Second Price request sent (strike 200) and completes immediately.
    page.fill("#strike-price-input", "200.0")
    page.click("#price-btn")

    # 3. Confirm the screen shows the SECOND request's result.
    _wait_until(lambda: page.inner_text("#price-per-100") == "222.000000")
    assert page.inner_text("#price-per-100") == "222.000000"

    # 4. Only now release the first (older) response.
    pending[0].fulfill(
        status=200, content_type="application/json", body=json.dumps(_fake_display(111.0))
    )
    page.wait_for_timeout(300)

    # 5. The older response must not have overwritten the newer render.
    assert page.inner_text("#price-per-100") == "222.000000"


@_PLAYWRIGHT_SKIP
def test_clear_invalidates_a_pending_price_request(server_url, page) -> None:
    """Clicking Clear while a Price request is in flight must win the race
    even though that Price response is released only afterward."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    base_price_per_100 = page.inner_text("#price-per-100")

    pending = []
    page.route("**/api/case/price", lambda route: pending.append(route))

    # 1. Price request sent -- response stays pending.
    page.fill("#strike-price-input", "123.0")
    page.click("#price-btn")
    _wait_until(lambda: len(pending) == 1)

    # 2. Clear is clicked while that response is still pending.
    page.click("#clear-btn")

    # 3. The screen must already show the base form/result.
    assert page.inner_text("#price-per-100") == base_price_per_100

    # 4. Only now release the stale Price response.
    pending[0].fulfill(
        status=200, content_type="application/json", body=json.dumps(_fake_display(999.0))
    )
    page.wait_for_timeout(300)

    # 5. It must not have overwritten Clear's base state.
    assert page.inner_text("#price-per-100") == base_price_per_100
    assert "999" not in page.inner_text("#price-per-100")


# --- Unified failure cleanup --------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_failed_pricing_result_clears_stale_results_and_shows_error(server_url, page) -> None:
    """A well-formed HTTP 200 response carrying a FAILED PricingResult
    (a domain pricing failure, not a bridge/transport error) must still
    clear every result field and show the real errors -- never a stale
    prior success or a fabricated replacement value."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    assert page.inner_text("#price-per-100") != "—"

    page.route(
        "**/api/case/price",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_display(0.0, status="FAILED")),
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
def test_http_400_clears_stale_results_and_shows_error(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    assert page.inner_text("#price-per-100") != "—"

    page.route(
        "**/api/case/price",
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
    _wait_bootstrap_ready(page)
    assert page.inner_text("#price-per-100") != "—"

    page.route("**/api/case/price", lambda route: route.abort())
    page.click("#price-btn")
    page.wait_for_timeout(200)

    assert page.inner_text("#price-per-100") == "—"
    assert page.inner_text("#greek-theta") == "—"
    assert page.inner_text("#pricing-error-banner") != ""
    assert page.eval_on_selector("#status-indicator", "el => el.classList.contains('failed')")


@_PLAYWRIGHT_SKIP
def test_invalid_json_response_clears_stale_results(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    assert page.inner_text("#price-per-100") != "—"

    page.route(
        "**/api/case/price",
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


# --- Removed unsupported market-input rows -----------------------------------


@_PLAYWRIGHT_SKIP
def test_page_has_no_unsupported_market_input_rows(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    body_text = page.inner_text("body")
    for stale_label in ("Normal Yield Vol", "Lognormal Yield Vol", "USD Rate (MMkt)"):
        assert stale_label not in body_text


# --- Issue #138: Load Case JSON ----------------------------------------------


@_PLAYWRIGHT_SKIP
def test_load_case_success_replaces_context_form_and_display(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _fake_case_load_payload(issuer="Uploaded Issuer X", premium_per_100=333.0)
            ),
        ),
    )
    _upload_fake_case_file(page)

    _wait_until(lambda: page.inner_text("#instr-title") == "Uploaded Issuer X")
    assert page.inner_text("#price-per-100") == "333.000000"
    assert page.input_value("#strike-price-input") == "77"
    assert not _is_disabled(page, "#price-btn")
    assert not _is_disabled(page, "#clear-btn")


@_PLAYWRIGHT_SKIP
def test_clear_restores_the_uploaded_case_not_the_bundled_default(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _fake_case_load_payload(issuer="Uploaded Issuer X", premium_per_100=333.0)
            ),
        ),
    )
    _upload_fake_case_file(page)
    _wait_until(lambda: page.inner_text("#instr-title") == "Uploaded Issuer X")

    page.fill("#strike-price-input", "12345.0")
    page.click("#clear-btn")

    assert page.inner_text("#instr-title") == "Uploaded Issuer X"
    assert page.input_value("#strike-price-input") == "77"
    assert page.inner_text("#price-per-100") == "333.000000"


@_PLAYWRIGHT_SKIP
def test_second_successful_upload_replaces_the_first(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    responses = iter(
        [
            _fake_case_load_payload(issuer="First Uploaded Issuer", premium_per_100=111.0),
            _fake_case_load_payload(issuer="Second Uploaded Issuer", premium_per_100=222.0),
        ]
    )
    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(next(responses))
        ),
    )

    _upload_fake_case_file(page, name="first.json")
    _wait_until(lambda: page.inner_text("#instr-title") == "First Uploaded Issuer")

    _upload_fake_case_file(page, name="second.json")
    _wait_until(lambda: page.inner_text("#instr-title") == "Second Uploaded Issuer")
    assert page.inner_text("#price-per-100") == "222.000000"


@_PLAYWRIGHT_SKIP
def test_failed_second_upload_leaves_the_first_base_intact(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_case_load_payload(issuer="Good Issuer", premium_per_100=111.0)),
        ),
    )
    _upload_fake_case_file(page, name="good.json")
    _wait_until(lambda: page.inner_text("#instr-title") == "Good Issuer")

    page.unroute("**/api/case")
    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "schema violation: missing field"}),
        ),
    )
    _upload_fake_case_file(page, name="bad.json")
    page.wait_for_timeout(200)

    # The failed upload must show an error but leave the first base's
    # context, form, and premium completely intact -- never a partial swap,
    # never stale results cleared just because a *later* attempt failed.
    assert page.inner_text("#pricing-error-banner") != ""
    assert page.inner_text("#instr-title") == "Good Issuer"
    assert page.inner_text("#price-per-100") == "111.000000"
    assert not _is_disabled(page, "#price-btn")
    assert not _is_disabled(page, "#clear-btn")
    assert not _is_disabled(page, "#load-case-label")


@_PLAYWRIGHT_SKIP
def test_domain_failed_case_load_shows_error_and_leaves_bundled_base_intact(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_title = page.inner_text("#instr-title")
    original_price = page.inner_text("#price-per-100")

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_case_load_payload(status="FAILED")),
        ),
    )
    _upload_fake_case_file(page)
    page.wait_for_timeout(200)

    assert page.inner_text("#pricing-error-banner") != ""
    # The bundled base must remain exactly as it was -- a domain FAILED
    # during a *load* never becomes the active base (mirrors the bootstrap
    # HTTP-200-but-FAILED fix from PR #136).
    assert page.inner_text("#instr-title") == original_title
    assert page.inner_text("#price-per-100") == original_price
    assert not _is_disabled(page, "#price-btn")
    assert not _is_disabled(page, "#clear-btn")


@_PLAYWRIGHT_SKIP
def test_invalid_utf8_upload_shows_error_and_leaves_base_intact(server_url, page) -> None:
    # A genuine end-to-end run against the real server (no route mock): the
    # uploaded bytes are deliberately invalid UTF-8, so the server's own
    # strict decode must reject them -- proving script.js's failure path
    # for a real HTTP 400 from an actual bad upload, not just a mocked one.
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_title = page.inner_text("#instr-title")

    page.set_input_files(
        "#case-file-input",
        files=[
            {
                "name": "bad.json",
                "mimeType": "application/json",
                "buffer": b"\xff\xfe not valid utf-8",
            }
        ],
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#pricing-error-banner") != ""
    assert page.inner_text("#instr-title") == original_title
    assert not _is_disabled(page, "#price-btn")
    assert not _is_disabled(page, "#clear-btn")


@_PLAYWRIGHT_SKIP
def test_case_load_invalidates_a_pending_price_request(server_url, page) -> None:
    """A Price request in flight when a case load starts must never be
    allowed to overwrite the freshly loaded case's render."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    pending_price = []
    page.route("**/api/case/price", lambda route: pending_price.append(route))

    page.click("#price-btn")
    _wait_until(lambda: len(pending_price) == 1)

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _fake_case_load_payload(issuer="Loaded While Pricing", premium_per_100=444.0)
            ),
        ),
    )
    _upload_fake_case_file(page)
    _wait_until(lambda: page.inner_text("#instr-title") == "Loaded While Pricing")
    assert page.inner_text("#price-per-100") == "444.000000"

    # Only now release the stale Price response.
    pending_price[0].fulfill(
        status=200, content_type="application/json", body=json.dumps(_fake_display(999.0))
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#price-per-100") == "444.000000"
    assert "999" not in page.inner_text("#price-per-100")


@_PLAYWRIGHT_SKIP
def test_stale_case_load_response_cannot_overwrite_newer_state(server_url, page) -> None:
    """An earlier case-load response, held open and released only after a
    later case load has already resolved, must not overwrite that render."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    pending = []
    request_count = 0

    def route_case(route):
        # Distinguish the two requests by arrival order, not by inspecting
        # route.request.post_data -- reading post_data on a route that is
        # deliberately held open (not yet fulfilled/continued) has been
        # observed to stall for several seconds when the body is a raw
        # ArrayBuffer (our /api/case upload's Content-Type), unlike a JSON
        # string body. Order-based dispatch avoids touching post_data on
        # the held-open route entirely.
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            pending.append(route)  # hold the FIRST upload's response open
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _fake_case_load_payload(issuer="Second Loaded Issuer", premium_per_100=555.0)
                ),
            )

    page.route("**/api/case", route_case)

    page.set_input_files(
        "#case-file-input",
        files=[
            {
                "name": "first.json",
                "mimeType": "application/json",
                "buffer": b'{"placeholder": true}',
            }
        ],
    )
    _wait_until_via_page(page, lambda: len(pending) == 1)

    page.set_input_files(
        "#case-file-input",
        files=[
            {"name": "second.json", "mimeType": "application/json", "buffer": b'{"other": true}'}
        ],
    )
    _wait_until(lambda: page.inner_text("#instr-title") == "Second Loaded Issuer")
    assert page.inner_text("#price-per-100") == "555.000000"

    # Only now release the first (stale) upload's response.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _fake_case_load_payload(issuer="First Loaded Issuer", premium_per_100=111.0)
        ),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#instr-title") == "Second Loaded Issuer"
    assert page.inner_text("#price-per-100") == "555.000000"


# --- Issue #138: current-run export (Download JSON / Download Markdown) ------


@_PLAYWRIGHT_SKIP
def test_download_buttons_disabled_before_bootstrap_completes(server_url, page) -> None:
    pending = []
    _goto_and_capture_route(page, f"{server_url}/", "**/api/base", pending)

    assert _is_disabled(page, "#download-json-btn")
    assert _is_disabled(page, "#download-markdown-btn")

    pending[0].continue_()


@_PLAYWRIGHT_SKIP
def test_download_buttons_enabled_after_bootstrap_succeeds(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    assert not _is_disabled(page, "#download-json-btn")
    assert not _is_disabled(page, "#download-markdown-btn")


@_PLAYWRIGHT_SKIP
def test_download_json_button_downloads_exact_render_helper_output(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.route(
        "**/api/export/json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "content": '{\n  "status": "SUCCESS"\n}\n',
                    "filename": "shiori_standalone_option_run.json",
                    "mime": "application/json",
                }
            ),
        ),
    )

    with page.expect_download() as download_info:
        page.click("#download-json-btn")
    download = download_info.value

    assert download.suggested_filename == "shiori_standalone_option_run.json"
    downloaded_path = download.path()
    assert downloaded_path.read_text(encoding="utf-8") == '{\n  "status": "SUCCESS"\n}\n'


@_PLAYWRIGHT_SKIP
def test_download_markdown_button_downloads_exact_render_helper_output(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.route(
        "**/api/export/markdown",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "content": "# Shiori Standalone Bond Option — Current Run Export\n",
                    "filename": "shiori_standalone_option_run.md",
                    "mime": "text/markdown",
                }
            ),
        ),
    )

    with page.expect_download() as download_info:
        page.click("#download-markdown-btn")
    download = download_info.value

    assert download.suggested_filename == "shiori_standalone_option_run.md"
    downloaded_path = download.path()
    assert downloaded_path.read_text(encoding="utf-8") == (
        "# Shiori Standalone Bond Option — Current Run Export\n"
    )


@_PLAYWRIGHT_SKIP
def test_download_does_not_trigger_a_second_pricing_request(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    price_requests = []
    page.route("**/api/case/price", lambda route: price_requests.append(route))
    page.route("**/api/price", lambda route: price_requests.append(route))
    page.route(
        "**/api/export/json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"content": "{}", "filename": "x.json", "mime": "application/json"}),
        ),
    )
    page.route(
        "**/api/export/markdown",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"content": "# x", "filename": "x.md", "mime": "text/markdown"}),
        ),
    )

    with page.expect_download():
        page.click("#download-json-btn")
    with page.expect_download():
        page.click("#download-markdown-btn")
    page.wait_for_timeout(200)

    assert price_requests == []


@_PLAYWRIGHT_SKIP
def test_export_reflects_current_display_after_a_price_action(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.route(
        "**/api/case/price",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_fake_display(777.0))
        ),
    )
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") == "777.000000")

    captured = {}

    def route_export(route):
        captured["body"] = json.loads(route.request.post_data)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"content": "x", "filename": "x.json", "mime": "application/json"}),
        )

    page.route("**/api/export/json", route_export)
    with page.expect_download():
        page.click("#download-json-btn")

    assert captured["body"]["display"]["model_fair_premium_per_100"] == 777.0


@_PLAYWRIGHT_SKIP
def test_export_unavailable_after_a_price_action_transport_failure(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    assert not _is_disabled(page, "#download-json-btn")

    page.route("**/api/case/price", lambda route: route.abort())
    page.click("#price-btn")
    page.wait_for_timeout(200)

    assert _is_disabled(page, "#download-json-btn")
    assert _is_disabled(page, "#download-markdown-btn")


@_PLAYWRIGHT_SKIP
def test_export_available_after_a_domain_failed_price_action(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.route(
        "**/api/case/price",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_display(0.0, status="FAILED")),
        ),
    )
    page.click("#price-btn")
    page.wait_for_timeout(200)

    # A structured FAILED PricingResult from a real Price action is still a
    # completed run and must remain exportable.
    assert not _is_disabled(page, "#download-json-btn")
    assert not _is_disabled(page, "#download-markdown-btn")
