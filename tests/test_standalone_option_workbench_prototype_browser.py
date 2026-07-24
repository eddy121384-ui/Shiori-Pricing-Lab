"""Browser-driven regression tests for the manual workbench prototype
(PR #136, extended by Issue #138/#140): the trader-draft revision's
instrument-first Bloomberg lookup, missing-input gating, and Clear/lookup
cross-action races.

These specifically exercise script.js's runtime behavior (request
generation/staleness, AbortController, the missing-field gate, and the
unified failure-cleanup path), which cannot be proven by a pure-Python test
-- they drive one real ``ThreadingHTTPServer`` (see
``standalone_option_workbench_server``) in a background thread and one real
headless Chromium page via Playwright. No pricing math is exercised or
asserted here; the mocked payloads only need the few display-dict keys
script.js reads.

**Trader-draft revision (Issue #140 second revision).** The bundled
synthetic case and "Load Case JSON" are no longer part of the trader
workflow: the page starts with nothing loaded, and a Bloomberg bond lookup
is the only way to start a run, seeding a brand-new in-memory pricing draft.
Because no UI in this revision can supply curve points, credit-spread
inputs, or full bond reference data, Price and Refresh Bloomberg & Price
structurally can never be enabled through the browser alone this round --
that is the intended, honest end state this revision delivers (a concrete
missing-input list), not a gap. Consequently, cross-action race coverage
below is scoped to the two actions that can actually fire a request through
the UI (a fresh lookup and Clear); Price/Refresh's own stale-response
guards reuse the exact same generation/AbortController pattern, exercised
directly by the lookup races. POST /api/case and POST /api/case/bloomberg
themselves, and the bundled synthetic fixture, remain fully covered by the
existing Python-level server tests and are unaffected by anything here --
this file only tests the browser's use of them.

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


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.02) -> None:
    """Poll ``predicate`` from the test thread until it's true or time out.

    The default timeout is generous (20s, not a tight bound) because these
    tests assert that a condition *eventually* becomes true, not how fast --
    a real CI runner can be meaningfully slower/more contended than a local
    sandbox.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


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


# --- Bloomberg bond lookup helpers -------------------------------------------


def _select_quote_side(page, side: str) -> None:
    page.click(f'#bond-quote-side-toggle .opt[data-value="{side}"]')


def _resolved_bond_panel_hidden(page) -> bool:
    return page.eval_on_selector("#resolved-bond-panel", "el => el.hidden")


def _missing_fields_text(page) -> list[str]:
    return page.eval_on_selector_all(
        "#missing-fields-list li", "items => items.map(el => el.textContent)"
    )


_EMPTY_BOND_MASTER = {
    "coupon": None,
    "coupon_frequency": None,
    "issue_date": None,
    "maturity_date": None,
    "day_count": None,
    "first_coupon_date": None,
    "last_coupon_date": None,
    "redemption_amount": None,
    "callable_flag": None,
    "sinkable_flag": None,
    "bond_type": None,
    "yield_convention": None,
    "business_day_convention": None,
}


def _default_bloomberg_bond_lookup_response(
    *, bond_master: dict | None = None, **overrides
) -> dict:
    payload = {
        "isin": "US91282CLJ89",
        "cusip": "91282CLJ8",
        "name": "UNITED STATES TREAS NTS",
        "currency": "USD",
        "quote_side": "MID",
        "clean_price_per_100": 99.75,
        "accrued_interest_per_100": 0.42,
        "acquired_at": "2026-07-01T16:05:00+00:00",
        "source_system": "BLOOMBERG_DAPI",
        "bond_master": {**_EMPTY_BOND_MASTER, **(bond_master or {})},
    }
    payload.update(overrides)
    return payload


def _load_bloomberg_bond(
    page, *, identifier: str = "US91282CLJ89", side: str = "MID", response: dict | None = None
) -> None:
    """Mocks ``/api/bloomberg/bond`` and drives one full successful lookup
    through the real UI controls, waiting for the resolved-bond panel to
    show *this* lookup's own ISIN before returning (not merely "not
    hidden", which would already be true if an earlier lookup populated it)."""

    payload = response if response is not None else _default_bloomberg_bond_lookup_response()

    def _handle(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/bloomberg/bond", _handle)
    page.fill("#bond-identifier-input", identifier)
    _select_quote_side(page, side)
    page.click("#load-bloomberg-bond-btn")
    page.wait_for_function(
        "expected => document.querySelector('#resolved-bond-isin').textContent === expected",
        arg=payload["isin"],
    )
    page.unroute("**/api/bloomberg/bond", _handle)


# --- Initial state: no synthetic instrument, no Load Case JSON control ------


@_PLAYWRIGHT_SKIP
def test_initial_page_has_no_synthetic_instrument_or_result(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)

    body_text = page.inner_text("body")
    for stale in ("Synthetic Test Issuer A", "XS0000000001", "SANITIZED_SYNTHETIC_MARKET_SOURCE"):
        assert stale not in body_text

    assert page.inner_text("#status-text") == "No bond loaded"
    assert page.eval_on_selector("#instrument-header-section", "el => el.hidden")
    assert page.eval_on_selector("#workspace-section", "el => el.hidden")
    assert page.eval_on_selector("#instrument-details-section", "el => el.hidden")
    assert _resolved_bond_panel_hidden(page)
    assert page.eval_on_selector("#draft-incomplete-note", "el => el.hidden")


@_PLAYWRIGHT_SKIP
def test_initial_page_has_no_load_case_json_control(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)

    assert page.query_selector("#load-case-label") is None
    assert page.query_selector("#case-file-input") is None
    assert "Load Case JSON" not in page.inner_text("body")


@_PLAYWRIGHT_SKIP
def test_initial_state_controls_gated_correctly(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)

    # No async bootstrap dependency any more -- Load Bloomberg Bond and
    # Clear are usable immediately; Price/Refresh/export need a completed
    # run that does not exist yet.
    assert not _is_disabled(page, "#load-bloomberg-bond-btn")
    assert not _is_disabled(page, "#clear-btn")
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert _is_disabled(page, "#download-json-btn")
    assert _is_disabled(page, "#download-markdown-btn")


@_PLAYWRIGHT_SKIP
def test_page_has_no_unsupported_market_input_rows(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)

    body_text = page.inner_text("body")
    for stale_label in ("Normal Yield Vol", "Lognormal Yield Vol", "USD Rate (MMkt)"):
        assert stale_label not in body_text


# --- Instrument-first Bloomberg bond lookup: creates a clean draft ----------


@_PLAYWRIGHT_SKIP
def test_load_bloomberg_bond_rejects_an_invalid_identifier_without_calling_the_server(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")

    calls = []
    page.route("**/api/bloomberg/bond", lambda route: calls.append(route) or route.abort())

    page.fill("#bond-identifier-input", "TOOSHORT")
    _select_quote_side(page, "MID")
    page.click("#load-bloomberg-bond-btn")
    page.wait_for_timeout(150)

    assert calls == []
    assert "ISIN or 9-character CUSIP" in page.inner_text("#pricing-error-banner")
    assert _resolved_bond_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_load_bloomberg_bond_requires_a_quote_side(server_url, page) -> None:
    page.goto(f"{server_url}/")

    calls = []
    page.route("**/api/bloomberg/bond", lambda route: calls.append(route) or route.abort())

    page.fill("#bond-identifier-input", "US91282CLJ89")
    page.click("#load-bloomberg-bond-btn")
    page.wait_for_timeout(150)

    assert calls == []
    assert "Quote Side" in page.inner_text("#pricing-error-banner")


@_PLAYWRIGHT_SKIP
def test_successful_lookup_shows_resolved_bond_identity_and_pricing_form(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    assert page.inner_text("#resolved-bond-name") == "UNITED STATES TREAS NTS"
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.inner_text("#resolved-bond-cusip") == "91282CLJ8"
    assert page.inner_text("#resolved-bond-currency") == "USD"
    assert page.inner_text("#resolved-bond-clean-price") == "99.750000"
    assert page.inner_text("#resolved-bond-accrued") == "0.420000"
    assert page.inner_text("#resolved-bond-source") == "BLOOMBERG_DAPI"

    # The pricing form (Option Terms) and Instrument Details (Bloomberg Bond
    # Master) both show immediately -- but the old instrument header, which
    # needs a genuinely completed price, stays hidden.
    assert not page.eval_on_selector("#workspace-section", "el => el.hidden")
    assert (
        page.eval_on_selector("#workspace-section", "el => getComputedStyle(el).display")
        != "none"
    )
    assert page.eval_on_selector("#instrument-header-section", "el => el.hidden")
    assert not page.eval_on_selector("#instrument-details-section", "el => el.hidden")
    assert page.inner_text("#details-issuer") == "UNITED STATES TREAS NTS"
    assert page.inner_text("#details-isin") == "US91282CLJ89"
    assert page.inner_text("#details-cusip") == "91282CLJ8"
    assert page.inner_text("#details-coupon") == "Not available"

    assert not page.eval_on_selector("#draft-incomplete-note", "el => el.hidden")
    assert "Complete the required pricing inputs before pricing." in page.inner_text(
        "#draft-incomplete-note"
    )
    missing = _missing_fields_text(page)
    assert any("Strike (per 100)" in item for item in missing)
    assert any("Call / Put" in item for item in missing)
    # Category summary badges show first, full detail only after "Show details".
    assert "Option terms incomplete" in page.inner_text("#missing-categories")
    assert "Bond reference data incomplete" in page.inner_text("#missing-categories")

    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")


@_PLAYWRIGHT_SKIP
def test_lookup_never_leaks_bundled_synthetic_case_values_into_the_draft(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    body_text = page.inner_text("body")
    for stale in ("Synthetic Test Issuer A", "XS0000000001", "SANITIZED_SYNTHETIC_MARKET_SOURCE"):
        assert stale not in body_text

    assert page.input_value("#strike-price-input") == ""
    assert page.input_value("#notional-input") == ""
    assert page.input_value("#volatility-input") == ""
    assert page.input_value("#forward-price-input") == ""
    assert page.query_selector("#option-type-toggle .opt.on") is None
    assert page.query_selector("#position-toggle .opt.on") is None


@_PLAYWRIGHT_SKIP
def test_lookup_failure_preserves_the_prior_screen(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)
    page.fill("#strike-price-input", "100")

    page.route(
        "**/api/bloomberg/bond",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.fill("#bond-identifier-input", "91282CLJ8")
    page.click("#load-bloomberg-bond-btn")
    page.wait_for_timeout(200)

    assert "Bloomberg DAPI session failed to start" in page.inner_text("#pricing-error-banner")
    # The previously resolved bond and its trader-entered draft stay intact.
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.input_value("#strike-price-input") == "100"


@_PLAYWRIGHT_SKIP
def test_newer_lookup_replaces_the_draft_and_discards_prior_trader_input(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)
    page.fill("#strike-price-input", "100")
    page.click('#option-type-toggle .opt[data-value="CALL"]')

    _load_bloomberg_bond(
        page,
        identifier="XS9999999999",
        response=_default_bloomberg_bond_lookup_response(
            isin="XS9999999999", cusip="999999999", name="SECOND RESOLVED BOND"
        ),
    )

    assert page.inner_text("#resolved-bond-name") == "SECOND RESOLVED BOND"
    assert page.inner_text("#resolved-bond-isin") == "XS9999999999"
    # A fresh lookup always starts a brand-new draft -- never retaining the
    # previous bond's trader-entered inputs.
    assert page.input_value("#strike-price-input") == ""
    assert page.query_selector("#option-type-toggle .opt.on") is None


# --- Bloomberg Bond Master (Issue #140 third revision) ------------------------


@_PLAYWRIGHT_SKIP
def test_confirmed_bond_master_values_enter_the_clean_draft_and_render(server_url, page) -> None:
    """A field the loader actually returns a value for (simulating a
    confirmed mnemonic) shows in both the top summary and Instrument
    Details -- never just silently dropped."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(
            bond_master={"coupon": "4.125", "maturity_date": "2031-01-31"}
        ),
    )

    assert page.inner_text("#resolved-bond-coupon") == "4.125"
    assert page.inner_text("#resolved-bond-maturity") == "2031-01-31"
    assert page.inner_text("#details-coupon") == "4.125"
    assert page.inner_text("#details-maturity") == "2031-01-31"
    # A field still unconfirmed/unreturned stays honestly "Not available",
    # never a fabricated or synthetic value.
    assert page.inner_text("#details-day-count") == "Not available"
    assert page.inner_text("#details-callable") == "Not available"


@_PLAYWRIGHT_SKIP
def test_missing_bond_master_fields_show_not_available_and_never_pollute_identity(
    server_url, page
) -> None:
    """The default (all-fields-unconfirmed) lookup response must still show
    every identity/quote field correctly -- a wholly-null bond_master must
    never degrade the already-reliable identity/quote result."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)  # default response: bond_master entirely None

    assert page.inner_text("#resolved-bond-name") == "UNITED STATES TREAS NTS"
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.inner_text("#resolved-bond-clean-price") == "99.750000"
    assert page.inner_text("#resolved-bond-coupon") == "Not available"
    assert page.inner_text("#resolved-bond-maturity") == "Not available"

    for details_id in (
        "details-coupon",
        "details-coupon-frequency",
        "details-issue-date",
        "details-maturity",
        "details-day-count",
        "details-first-coupon-date",
        "details-last-coupon-date",
        "details-redemption-amount",
        "details-callable",
        "details-sinkable",
        "details-bond-type",
        "details-yield-convention",
        "details-business-day-convention",
    ):
        assert page.inner_text(f"#{details_id}") == "Not available"
    # Identity fields Bloomberg genuinely returned are never blanked out.
    assert page.inner_text("#details-issuer") == "UNITED STATES TREAS NTS"
    assert page.inner_text("#details-isin") == "US91282CLJ89"
    assert page.inner_text("#details-cusip") == "91282CLJ8"
    assert page.inner_text("#details-source") == "BLOOMBERG_DAPI"


@_PLAYWRIGHT_SKIP
def test_new_lookup_fully_replaces_the_prior_bonds_bond_master(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(bond_master={"coupon": "4.125"}),
    )
    assert page.inner_text("#details-coupon") == "4.125"

    _load_bloomberg_bond(
        page,
        identifier="XS9999999999",
        response=_default_bloomberg_bond_lookup_response(
            isin="XS9999999999", cusip="999999999", name="SECOND RESOLVED BOND"
        ),
    )

    # The new bond's own (entirely unconfirmed) Bond Master replaces the old
    # one completely -- no leftover value from the first bond.
    assert page.inner_text("#details-issuer") == "SECOND RESOLVED BOND"
    assert page.inner_text("#details-coupon") == "Not available"
    assert page.inner_text("#resolved-bond-coupon") == "Not available"


@_PLAYWRIGHT_SKIP
def test_clear_resets_bond_master_to_empty(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(bond_master={"coupon": "4.125"}),
    )
    assert page.inner_text("#details-coupon") == "4.125"

    page.click("#clear-btn")
    page.wait_for_timeout(150)

    assert page.eval_on_selector("#instrument-details-section", "el => el.hidden")
    assert page.inner_text("#details-issuer") == "—"
    assert page.inner_text("#details-coupon") == "Not available"


@_PLAYWRIGHT_SKIP
def test_stale_lookup_cannot_overwrite_a_newer_drafts_bond_master(server_url, page) -> None:
    page.goto(f"{server_url}/")

    pending: list = []
    request_count = 0

    def route_lookup(route):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            pending.append(route)
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _default_bloomberg_bond_lookup_response(
                        name="SECOND LOOKUP BOND", bond_master={"coupon": "2.5"}
                    )
                ),
            )

    page.route("**/api/bloomberg/bond", route_lookup)
    page.fill("#bond-identifier-input", "US91282CLJ89")
    page.click('#bond-quote-side-toggle .opt[data-value="MID"]')
    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: len(pending) == 1)

    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: page.inner_text("#resolved-bond-name") == "SECOND LOOKUP BOND")
    assert page.inner_text("#details-coupon") == "2.5"

    # Only now release the first (stale) lookup's response -- it must not
    # overwrite the newer draft's Bond Master or identity.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _default_bloomberg_bond_lookup_response(
                name="FIRST LOOKUP BOND", bond_master={"coupon": "9.999"}
            )
        ),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#resolved-bond-name") == "SECOND LOOKUP BOND"
    assert page.inner_text("#details-coupon") == "2.5"


# --- Missing-input gating -----------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_filling_option_terms_fields_shrinks_the_missing_list_live(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    before = _missing_fields_text(page)
    assert any("Strike (per 100)" in item for item in before)
    assert any("Call / Put" in item for item in before)

    page.fill("#strike-price-input", "99.5")
    page.fill("#notional-input", "50")
    page.fill("#volatility-input", "0.18")
    page.fill("#forward-price-input", "101.3")
    page.click('#option-type-toggle .opt[data-value="CALL"]')
    page.click('#position-toggle .opt[data-value="BUY"]')
    page.wait_for_timeout(150)

    after = _missing_fields_text(page)
    assert not any("Strike (per 100)" in item for item in after)
    assert not any("Call / Put" in item for item in after)
    assert not any("Notional" in item for item in after)
    assert not any("Direction (Buy/Sell)" in item for item in after)
    assert not any("Price Vol (σ)" in item for item in after)
    assert not any("Forward Clean Price (per 100)" in item for item in after)
    # Fields with no manual-entry UI in this revision (curve, credit spread,
    # full bond reference data, dates) remain honestly reported as missing
    # -- Price stays disabled rather than guessing or defaulting them.
    assert any("Option Discount Curve" in item for item in after)
    assert len(after) < len(before)
    assert "Bond reference data incomplete" in page.inner_text("#missing-categories")
    assert "Market curves unavailable" in page.inner_text("#missing-categories")
    assert _is_disabled(page, "#price-btn")


@_PLAYWRIGHT_SKIP
def test_price_does_not_run_without_a_complete_draft(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    calls = []
    page.route("**/api/case", lambda route: calls.append(route) or route.abort())

    assert _is_disabled(page, "#price-btn")
    page.click("#price-btn", force=True)
    page.wait_for_timeout(150)

    assert calls == []


@_PLAYWRIGHT_SKIP
def test_refresh_does_not_run_without_a_complete_draft(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    calls = []
    page.route("**/api/case/bloomberg", lambda route: calls.append(route) or route.abort())

    assert _is_disabled(page, "#bloomberg-refresh-btn")
    page.click("#bloomberg-refresh-btn", force=True)
    page.wait_for_timeout(150)

    assert calls == []


# --- Clear returns to a clean empty state, never the synthetic case --------


@_PLAYWRIGHT_SKIP
def test_clear_returns_to_empty_state_not_the_synthetic_case(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)
    page.fill("#strike-price-input", "99.5")
    page.click('#option-type-toggle .opt[data-value="CALL"]')

    page.click("#clear-btn")
    page.wait_for_timeout(150)

    assert page.inner_text("#status-text") == "No bond loaded"
    assert _resolved_bond_panel_hidden(page)
    assert page.eval_on_selector("#workspace-section", "el => el.hidden")
    assert page.eval_on_selector("#draft-incomplete-note", "el => el.hidden")
    assert page.input_value("#strike-price-input") == ""
    assert page.query_selector("#option-type-toggle .opt.on") is None
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")

    body_text = page.inner_text("body")
    for stale in ("Synthetic Test Issuer A", "XS0000000001"):
        assert stale not in body_text


# --- Cross-action race protection --------------------------------------------


@_PLAYWRIGHT_SKIP
def test_clear_invalidates_a_pending_bond_lookup(server_url, page) -> None:
    page.goto(f"{server_url}/")

    pending = []
    page.route("**/api/bloomberg/bond", lambda route: pending.append(route))
    page.fill("#bond-identifier-input", "US91282CLJ89")
    _select_quote_side(page, "MID")
    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: len(pending) == 1)

    page.click("#clear-btn")

    # Only now release the stale lookup response.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_default_bloomberg_bond_lookup_response()),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#status-text") == "No bond loaded"
    assert _resolved_bond_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_newer_bond_lookup_beats_an_older_one(server_url, page) -> None:
    page.goto(f"{server_url}/")

    pending: list = []
    request_count = 0

    def route_lookup(route):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            pending.append(route)
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_default_bloomberg_bond_lookup_response(name="SECOND LOOKUP BOND")),
            )

    page.route("**/api/bloomberg/bond", route_lookup)
    page.fill("#bond-identifier-input", "US91282CLJ89")
    _select_quote_side(page, "MID")
    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: len(pending) == 1)

    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: page.inner_text("#resolved-bond-name") == "SECOND LOOKUP BOND")

    # Only now release the first (stale) lookup response.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_default_bloomberg_bond_lookup_response(name="FIRST LOOKUP BOND")),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#resolved-bond-name") == "SECOND LOOKUP BOND"


# --- Export controls stay disabled without a completed pricing result -------


@_PLAYWRIGHT_SKIP
def test_download_buttons_disabled_with_no_draft(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)

    assert _is_disabled(page, "#download-json-btn")
    assert _is_disabled(page, "#download-markdown-btn")


@_PLAYWRIGHT_SKIP
def test_download_buttons_disabled_with_an_incomplete_draft(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    assert _is_disabled(page, "#download-json-btn")
    assert _is_disabled(page, "#download-markdown-btn")
