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


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.02) -> None:
    """Poll ``predicate`` from the test thread until it's true or time out.

    This is test-side polling only -- it never runs inside a Playwright
    route handler, so it cannot reintroduce the blocking-handler bug this
    file exists to fix (see module docstring). The default timeout is
    generous (20s, not a tight bound) because these tests assert that a
    condition *eventually* becomes true, not how fast -- a real CI runner
    can be meaningfully slower/more contended than a local sandbox (this
    was observed directly: test_early_click_during_bootstrap_does_not_break_initialization
    failed twice on GitHub Actions at an 8s bound while passing reliably
    locally), and a longer bound never weakens what is actually asserted.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _wait_until_via_page(page, predicate, timeout: float = 20.0, interval_ms: int = 20) -> None:
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
    # Uses _wait_until_via_page, not the bare _wait_until: several tests call
    # this immediately after resolving (fulfilling/continuing) a route that
    # was held open, and CDP event delivery in that situation was directly
    # observed (see _wait_until_via_page's own docstring, and the CI-only
    # failures of test_early_click_during_bootstrap_does_not_break_initialization
    # this fix responds to) to sometimes need a page-level wait to be pumped
    # promptly -- a bare time.sleep() polling loop does not reliably do that.
    _wait_until_via_page(
        page,
        lambda: not page.eval_on_selector(
            "#price-btn", "el => el.classList.contains('is-disabled')"
        ),
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
    artifact, not a product bug -- retried here by reloading rather than by
    adding any retry to the application itself.

    Reproduced-on-CI fix: each attempt gets its own private list and its own
    route registration, rather than all attempts sharing and re-clearing one
    list. A prior version cleared the shared list and reloaded, but a
    request from the *old* (about-to-be-abandoned) page can still be in
    flight when the reload fires, and its callback can append into the
    shared list *after* the clear -- so ``pending`` could end up holding a
    stale route from the old page alongside (or instead of) the real one
    from the reloaded page, and a caller that reads ``pending[0]`` assuming
    exactly one live entry could silently ``.continue_()``/``.fulfill()``
    the wrong (dead) request while the page's actual in-flight fetch -- tied
    to the *other* entry -- is never resolved and hangs forever. Only the
    winning attempt's own private list is ever copied into the caller's
    ``pending``, so a late-arriving callback from an abandoned attempt can
    no longer land there at all.

    Unrouting only ever happens on a *failed* attempt, right before the next
    one registers its own fresh handler -- never on the winning attempt.
    ``page.unroute()`` was observed to force-resolve (auto-continue) any
    already-paused request still matching the pattern being removed, which
    would silently let the real captured request through before the caller
    ever gets a chance to inspect or resolve it itself via
    ``route.continue_()``/``route.fulfill()`` -- exactly the "hold this
    request open" contract every caller of this helper depends on.
    """

    for attempt in range(attempts):
        attempt_pending: list = []

        def _capture(route, request=None, _attempt_pending=attempt_pending):
            # Playwright invokes route handlers as handler(route,
            # route.request) -- accept and ignore that second positional
            # arg so it can never collide with the _attempt_pending default.
            _attempt_pending.append(route)

        page.route(route_pattern, _capture)
        if attempt == 0:
            page.goto(url)
        else:
            page.reload()
        try:
            _wait_until(
                lambda _pending=attempt_pending: len(_pending) >= 1, timeout=2.0
            )
            pending.extend(attempt_pending)
            return
        except AssertionError:
            page.unroute(route_pattern, _capture)
            if attempt == attempts - 1:
                raise


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


@_PLAYWRIGHT_SKIP
def test_price_invalidates_a_pending_case_load(server_url, page) -> None:
    """Codex review (PR #139): starting Price while a Case Load is in flight
    must invalidate that Case Load -- its eventual success response must
    never overwrite the Price result that started later."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_title = page.inner_text("#instr-title")

    pending_case = []
    page.route("**/api/case", lambda route: pending_case.append(route))
    _upload_fake_case_file(page)
    _wait_until_via_page(page, lambda: len(pending_case) == 1)

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") != "—")
    price_after_click = page.inner_text("#price-per-100")

    # Only now release the stale, slower Case Load's success response.
    pending_case[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _fake_case_load_payload(issuer="Should Never Appear", premium_per_100=777.0)
        ),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#instr-title") == original_title
    assert page.inner_text("#price-per-100") == price_after_click
    assert "777" not in page.inner_text("#price-per-100")


@_PLAYWRIGHT_SKIP
def test_clear_invalidates_a_pending_case_load(server_url, page) -> None:
    """Codex review (PR #139): clicking Clear while a Case Load is in flight
    must invalidate that Case Load -- its eventual failure response must
    never disturb the state Clear just restored, nor show an error banner
    for an attempt the user has already moved on from."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_title = page.inner_text("#instr-title")
    original_price = page.inner_text("#price-per-100")

    pending_case = []
    page.route("**/api/case", lambda route: pending_case.append(route))
    _upload_fake_case_file(page)
    _wait_until_via_page(page, lambda: len(pending_case) == 1)

    page.fill("#strike-price-input", "12345.0")
    page.click("#clear-btn")
    assert page.inner_text("#instr-title") == original_title
    assert page.input_value("#strike-price-input") != "12345.0"

    # Only now release the stale Case Load's *failure* response.
    pending_case[0].fulfill(
        status=400,
        content_type="application/json",
        body=json.dumps({"error": "schema violation: missing field"}),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#instr-title") == original_title
    assert page.inner_text("#price-per-100") == original_price
    assert page.inner_text("#pricing-error-banner") == ""


@_PLAYWRIGHT_SKIP
def test_uploaded_case_shows_its_own_source_system_not_synthetic_data(server_url, page) -> None:
    """Codex review (PR #139): the provenance badge must never claim
    "Synthetic Data" for an uploaded case that declares a different, real
    source_system -- it must show the case's own declared value verbatim."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    assert page.inner_text("#provenance-badge") != "Synthetic Data"

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _fake_case_load_payload(issuer="Real Desk Upload", premium_per_100=333.0)
            ),
        ),
    )
    _upload_fake_case_file(page)
    _wait_until(lambda: page.inner_text("#instr-title") == "Real Desk Upload")

    assert page.inner_text("#provenance-badge") == "TEST_SOURCE"
    assert page.inner_text("#provenance-badge") != "Synthetic Data"


# --- Bloomberg quote refresh --------------------------------------------------


def _fake_live_bloomberg_quote(**overrides) -> dict:
    quote = {
        "security": "91282CQX Govt",
        "verified_isin": "XS9999999999",
        "source_system": "BLOOMBERG_DAPI",
        "quote_side": "MID",
        "currency": "USD",
        "clean_price_per_100": 101.25,
        "accrued_interest_per_100": 0.42,
        "acquired_at": "2026-07-01T16:05:00+00:00",
        "timestamp_basis": "SHIORI_ACQUISITION_TIME",
        "bloomberg_quote_observation_time": None,
        "case_as_of_timestamp": "2026-07-01T00:00:00Z",
        "refreshed_scope": "BOND_QUOTE_ONLY",
        "other_market_inputs": "CASE_JSON_UNCHANGED",
    }
    quote.update(overrides)
    return quote


def _fake_bloomberg_display(premium_per_100: float = 444.0, **quote_overrides) -> dict:
    display = _fake_display(premium_per_100)
    display["live_bloomberg_quote"] = _fake_live_bloomberg_quote(**quote_overrides)
    return display


def _select_quote_side(page, side: str) -> None:
    page.click(f'#quote-side-toggle .opt[data-value="{side}"]')


def _live_bloomberg_panel_hidden(page) -> bool:
    return page.eval_on_selector("#live-bloomberg-panel", "el => el.hidden")


@_PLAYWRIGHT_SKIP
def test_bloomberg_refresh_requires_security_and_quote_side(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    calls = []
    page.route("**/api/case/bloomberg", lambda route: calls.append(route) or route.abort())

    page.click("#bloomberg-refresh-btn")
    page.wait_for_timeout(150)

    assert calls == []
    assert "Enter a Bloomberg Security and select a Quote Side" in page.inner_text(
        "#pricing-error-banner"
    )


@_PLAYWRIGHT_SKIP
def test_bloomberg_refresh_success_renders_display_and_live_quote_panel(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_title = page.inner_text("#instr-title")

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_bloomberg_display(premium_per_100=555.0)),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") == "555.000000")

    # The instrument identity/context never changes -- only the Pricing
    # Results and the Live Bloomberg Quote panel update.
    assert page.inner_text("#instr-title") == original_title
    assert not _live_bloomberg_panel_hidden(page)
    assert page.inner_text("#bloomberg-quote-security") == "91282CQX Govt"
    assert page.inner_text("#bloomberg-quote-scope") == "BOND_QUOTE_ONLY"
    assert page.inner_text("#bloomberg-quote-other-inputs") == "CASE_JSON_UNCHANGED"


@_PLAYWRIGHT_SKIP
def test_bloomberg_refresh_never_changes_the_active_case(server_url, page) -> None:
    """Clicking Clear after a Bloomberg refresh must restore the case's own
    original bond quote, not the Bloomberg-priced run -- the refresh never
    changes the underlying instrument identity or active case."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_price = page.inner_text("#price-per-100")

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_bloomberg_display(premium_per_100=555.0)),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") == "555.000000")

    page.click("#clear-btn")

    assert page.inner_text("#price-per-100") == original_price
    assert _live_bloomberg_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_bloomberg_refresh_failure_preserves_previous_case_and_display(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_title = page.inner_text("#instr-title")
    original_price = page.inner_text("#price-per-100")

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    page.wait_for_timeout(200)

    assert "Bloomberg DAPI session failed to start" in page.inner_text("#pricing-error-banner")
    # Nothing about the previously active case/display is disturbed -- no
    # fallback to the case's old bond quote, no partial live provenance.
    assert page.inner_text("#instr-title") == original_title
    assert page.inner_text("#price-per-100") == original_price
    assert _live_bloomberg_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_bloomberg_invalidates_a_pending_price_request(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    pending_price = []
    page.route("**/api/case/price", lambda route: pending_price.append(route))
    page.click("#price-btn")
    _wait_until(lambda: len(pending_price) == 1)

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_bloomberg_display(premium_per_100=777.0)),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") == "777.000000")

    # Only now release the stale Price response.
    pending_price[0].fulfill(
        status=200, content_type="application/json", body=json.dumps(_fake_display(999.0))
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#price-per-100") == "777.000000"


@_PLAYWRIGHT_SKIP
def test_bloomberg_invalidates_a_pending_case_load(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_title = page.inner_text("#instr-title")

    pending_case = []
    page.route("**/api/case", lambda route: pending_case.append(route))
    _upload_fake_case_file(page)
    _wait_until_via_page(page, lambda: len(pending_case) == 1)

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_fake_bloomberg_display(premium_per_100=777.0)),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") == "777.000000")

    # Only now release the stale, slower Case Load's success response.
    pending_case[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _fake_case_load_payload(issuer="Should Never Appear", premium_per_100=222.0)
        ),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#instr-title") == original_title
    assert page.inner_text("#price-per-100") == "777.000000"


@_PLAYWRIGHT_SKIP
def test_price_invalidates_a_pending_bloomberg_request(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    pending_bloomberg = []
    page.route("**/api/case/bloomberg", lambda route: pending_bloomberg.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: len(pending_bloomberg) == 1)

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") != "—")
    price_after_click = page.inner_text("#price-per-100")

    # Only now release the stale Bloomberg response.
    pending_bloomberg[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_fake_bloomberg_display(premium_per_100=777.0)),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#price-per-100") == price_after_click
    assert _live_bloomberg_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_clear_invalidates_a_pending_bloomberg_request(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)
    original_price = page.inner_text("#price-per-100")

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    pending_bloomberg = []
    page.route("**/api/case/bloomberg", lambda route: pending_bloomberg.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: len(pending_bloomberg) == 1)

    page.click("#clear-btn")

    # Only now release the stale Bloomberg response.
    pending_bloomberg[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_fake_bloomberg_display(premium_per_100=777.0)),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#price-per-100") == original_price
    assert _live_bloomberg_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_case_load_invalidates_a_pending_bloomberg_request(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")
    pending_bloomberg = []
    page.route("**/api/case/bloomberg", lambda route: pending_bloomberg.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: len(pending_bloomberg) == 1)

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _fake_case_load_payload(issuer="Loaded While Bloomberg", premium_per_100=333.0)
            ),
        ),
    )
    _upload_fake_case_file(page)
    _wait_until(lambda: page.inner_text("#instr-title") == "Loaded While Bloomberg")

    # Only now release the stale Bloomberg response.
    pending_bloomberg[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_fake_bloomberg_display(premium_per_100=777.0)),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#price-per-100") == "333.000000"
    assert _live_bloomberg_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_newer_bloomberg_request_beats_an_older_one(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    page.fill("#bloomberg-security-input", "91282CQX Govt")
    _select_quote_side(page, "MID")

    pending: list = []
    request_count = 0

    def route_bloomberg(route):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            pending.append(route)
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_fake_bloomberg_display(premium_per_100=222.0)),
            )

    page.route("**/api/case/bloomberg", route_bloomberg)

    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: len(pending) == 1)

    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") == "222.000000")

    # Only now release the first (stale) Bloomberg response.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_fake_bloomberg_display(premium_per_100=111.0)),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#price-per-100") == "222.000000"


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


@_PLAYWRIGHT_SKIP
def test_stale_export_response_cannot_download_after_display_changes(server_url, page) -> None:
    """Codex review (PR #139): an export request in flight for the
    displayed run must not turn into a download if a Price/Load/Clear
    action changes the displayed run before that export's response
    arrives."""

    page.goto(f"{server_url}/")
    _wait_bootstrap_ready(page)

    downloads = []
    page.on("download", lambda download: downloads.append(download))

    pending_export = []
    page.route(
        "**/api/export/json",
        lambda route: pending_export.append(route),
    )
    page.click("#download-json-btn")
    _wait_until_via_page(page, lambda: len(pending_export) == 1)

    # While that export is still pending, a real Price action changes the
    # displayed run.
    page.route(
        "**/api/case/price",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_fake_display(777.0))
        ),
    )
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#price-per-100") == "777.000000")

    # Only now release the stale export response.
    pending_export[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {"content": "stale", "filename": "stale.json", "mime": "application/json"}
        ),
    )
    page.wait_for_timeout(300)

    assert downloads == []
