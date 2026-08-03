"""Browser-driven regression tests for the minimum-input trader workflow
(Issue #143, Milestone 1/2; building on PR #136 / #138 / #140 / #144 / #147).

These exercise script.js's runtime behavior -- the workflow-group model, the
single unresolved-dependency panel, the builder-backed Price gate, override
provenance, request staleness/AbortController, and the whole-run state reset --
which cannot be proven by a pure-Python test. They drive one real
``ThreadingHTTPServer`` (see ``standalone_option_workbench_server``) in a
background thread and one real headless Chromium page via Playwright.

The Bloomberg lookup route is mocked; everything downstream of it is real. The
headline pricing tests deliberately go all the way through ``POST /api/case``
into the reviewed typed builder and pricing engine, so a passing premium here is
a genuine engine result and not a fixture.

**Fixtures.** ``_treasury_lookup_response`` and ``_gilt_lookup_response`` mirror
what the real loader returns for the two securities Issue #149 was verified
against: the DAPI-confirmed Bond Master fields, plus the raw display-only
description strings that must never be auto-mapped into a typed enum. Day count,
bond type, ex-dividend days, last coupon date and status are deliberately absent
from both -- no UST / conventional-Gilt convention profile is approved, so
Bloomberg's own strings are evidence only.

**CI must not silently skip these tests.** Locally, missing Playwright is a skip
(optional test tooling, not a declared project dependency, same pattern as the
QuantLib skip elsewhere in this suite). In CI (detected via the standard
``CI=true`` environment variable GitHub Actions sets), a missing Playwright
install is a hard collection-time error instead -- merge protection cannot
depend on a check that quietly no-ops.

The Chromium executable path is never hardcoded: by default this file lets
Playwright's own browser discovery find the ``playwright install``-managed
browser (what CI uses), with an optional ``PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH``
environment variable escape hatch for a local sandbox whose pinned browser cache
doesn't line up with a freshly-installed Playwright package version.
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

    The default timeout is generous (20s, not a tight bound) because these tests
    assert that a condition *eventually* becomes true, not how fast -- a real CI
    runner can be meaningfully slower/more contended than a local sandbox.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _wait_until_pumping(page, predicate, timeout: float = 20.0) -> None:
    """Like :func:`_wait_until`, but pumps the Playwright connection each tick.

    Playwright's sync API only dispatches route handlers while a page call is in
    flight, so a predicate that reads Python-side state a handler populates
    (e.g. a request counter) would never become true under a bare sleep loop.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"condition not met within {timeout}s")


def _is_disabled(page, selector: str) -> bool:
    return page.eval_on_selector(selector, "el => el.classList.contains('is-disabled')")


def _is_actually_hidden(page, element_id: str) -> bool:
    """True only if the element is genuinely not rendered (computed
    ``display: none``), never merely the ``hidden`` IDL property.

    An element whose own CSS sets an explicit ``display`` can tie the UA
    stylesheet's ``[hidden] { display: none }`` rule in specificity and, being an
    author rule, win -- silently defeating ``el.hidden = true`` even though the
    property itself reads ``true``.
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


# --- Fixtures mirroring the two Issue #149 verification securities -----------

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

_EMPTY_BOND_MASTER_RAW = {
    "day_count": None,
    "maturity_type": None,
    "calc_type": None,
}


def _default_bloomberg_bond_lookup_response(
    *, bond_master: dict | None = None, bond_master_raw: dict | None = None, **overrides
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
        "bond_master_raw": {**_EMPTY_BOND_MASTER_RAW, **(bond_master_raw or {})},
    }
    payload.update(overrides)
    return payload


# Fixture shape, not market data. These mirror the *shape* of what the real
# loader returns -- the Bond Master keys PR #141 confirmed, and the raw
# display-only description strings Issue #149 recorded -- for the two ISINs #149
# was verified against. The numbers and dates are synthetic test values chosen
# to exercise the reviewed engine (notably a regular coupon grid, which the
# reviewed adapter requires); they are not a claim about either security's real
# terms and are never used as evidence for anything.
#
# Day count, bond type, ex-dividend days, last coupon date and status are
# deliberately absent from both: no UST / conventional-Gilt convention profile is
# approved, so Bloomberg's own strings stay evidence only.
_TREASURY_BOND_MASTER = {
    "coupon": 0.0375,
    "coupon_frequency": "SEMI_ANNUAL",
    "issue_date": "2024-01-31",
    "maturity_date": "2031-01-31",
    "first_coupon_date": "2024-07-31",
    "callable_flag": False,
    "sinkable_flag": False,
}
_TREASURY_BOND_MASTER_RAW = {
    "day_count": "ACT/ACT",
    "maturity_type": "AT MATURITY",
    "calc_type": "STREET CONVENTION",
}

# A GBP, Gilt-*shaped* bond with a deliberately REGULAR coupon grid: 2018-04-22
# to 2028-10-22 is exactly 21 semi-annual periods. The reviewed coupon adapter
# rejects an irregular grid outright rather than approximating a stub, so a
# fixture meant to reach the engine has to be regular.
#
# This does NOT represent the real GB00BFX0ZL78, whose issue/first-coupon
# structure carries a stub. That structure is covered separately by
# _GILT_STUB_BOND_MASTER below, which documents the current rejection rather
# than pretending it prices.
_GILT_BOND_MASTER = {
    "coupon": 0.0162,
    "coupon_frequency": "SEMI_ANNUAL",
    "issue_date": "2018-04-22",
    "maturity_date": "2028-10-22",
    "first_coupon_date": "2018-10-22",
    "callable_flag": False,
    "sinkable_flag": False,
}

# The same bond with a stub between issue and first coupon -- the shape a real
# conventional Gilt commonly has. Used only to pin the current, honest
# behaviour: the typed builder accepts it and the reviewed engine then returns a
# truthful FAILED result naming the irregular grid. No stub methodology is
# invented anywhere to make it price.
_GILT_STUB_BOND_MASTER = {**_GILT_BOND_MASTER, "issue_date": "2018-06-05"}
_GILT_BOND_MASTER_RAW = {
    "day_count": "ACT/ACT",
    "maturity_type": "NORMAL",
    "calc_type": "UK:BUMP/DMO METHOD",
}


def _treasury_lookup_response(*, bond_master: dict | None = None, **overrides) -> dict:
    payload_overrides = {"acquired_at": "2026-07-20T11:28:00+08:00", **overrides}
    return _default_bloomberg_bond_lookup_response(
        bond_master=bond_master if bond_master is not None else _TREASURY_BOND_MASTER,
        bond_master_raw=_TREASURY_BOND_MASTER_RAW,
        **payload_overrides,
    )


def _gilt_lookup_response(*, bond_master: dict | None = None, **overrides) -> dict:
    payload_overrides = {
        "isin": "GB00BFX0ZL78",
        "cusip": "G4527HGS7",
        "name": "UNITED KINGDOM GILT",
        "currency": "GBP",
        "clean_price_per_100": 96.12,
        "accrued_interest_per_100": 0.31,
        "acquired_at": "2026-07-20T11:28:00+08:00",
        **overrides,
    }
    return _default_bloomberg_bond_lookup_response(
        bond_master=bond_master if bond_master is not None else _GILT_BOND_MASTER,
        bond_master_raw=_GILT_BOND_MASTER_RAW,
        **payload_overrides,
    )


# --- Driving helpers ---------------------------------------------------------


def _select_quote_side(page, side: str) -> None:
    page.click(f'#bond-quote-side-toggle .opt[data-value="{side}"]')


def _resolved_bond_panel_hidden(page) -> bool:
    return page.eval_on_selector("#resolved-bond-panel", "el => el.hidden")


def _unresolved_group_ids(page) -> list[str]:
    return page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")


def _override_provenance(page) -> list[dict]:
    return page.evaluate("() => window.__shioriTestOverrideProvenance()")


def _main_screen_group_ids(page) -> list[str]:
    return page.eval_on_selector_all(
        "[data-workflow-group]", "els => els.map(el => el.dataset.workflowGroup)"
    )


def _load_bloomberg_bond(
    page, *, identifier: str = "US91282CLJ89", side: str = "MID", response: dict | None = None
) -> None:
    """Mocks ``/api/bloomberg/bond`` and drives one full successful lookup
    through the real UI controls, waiting for the resolved-bond panel to show
    *this* lookup's own ISIN before returning (not merely "not hidden", which
    would already be true if an earlier lookup populated it)."""

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


def _refresh_display(
    *, acquired_at: str, clean_price: float = 99.81, quote_side: str = "MID"
) -> dict:
    """A ``POST /api/case/bloomberg`` success payload.

    Mirrors the real route's shape: the display dict verbatim plus the bounded
    ``live_bloomberg_quote`` section, whose ``acquired_at`` is the very
    timestamp the route substituted as that run's ``pricing_timestamp``.
    """

    return {
        "status": "SUCCESS",
        "result_currency": "USD",
        "total_notional_model_fair_premium": 1234.5,
        "model_fair_premium_per_100": 0.12345,
        "forward_price_delta_per_100": 0.4,
        "forward_price_gamma_per_100": 0.2,
        "vega_per_vol_point_per_100": 0.1,
        "theta_per_calendar_day_per_100": -0.003,
        "errors": [],
        "live_bloomberg_quote": {
            "security": "/isin/US91282CLJ89",
            "verified_isin": "US91282CLJ89",
            "source_system": "BLOOMBERG_DAPI",
            "quote_side": quote_side,
            "currency": "USD",
            "clean_price_per_100": clean_price,
            "accrued_interest_per_100": 0.44,
            "acquired_at": acquired_at,
            "timestamp_basis": "SHIORI_ACQUISITION_TIME",
        },
    }


def _set_curve_nodes(page, nodes) -> None:
    """Set the Option Discount Curve editor's rows to exactly ``nodes``."""

    existing = page.query_selector_all(".curve-row")
    while len(existing) < len(nodes):
        page.click("#curve-add-row-btn")
        existing = page.query_selector_all(".curve-row")
    for row, (tenor, rate) in zip(existing, nodes, strict=False):
        row.query_selector(".curve-tenor-input").fill(tenor)
        row.query_selector(".curve-rate-input").fill(rate)
    page.wait_for_timeout(120)


def _fill_trade_group(
    page,
    *,
    option_type: str = "CALL",
    position: str = "BUY",
    strike: str = "99.32",
    notional: str = "1000000",
    expiry_local: str = "2026-10-20T17:20",
    expiry_offset: str = "+08:00",
) -> None:
    """Fill the five main-screen trade decisions through the real controls."""

    page.click(f'#option-type-toggle .opt[data-value="{option_type}"]')
    page.click(f'#position-toggle .opt[data-value="{position}"]')
    page.fill("#strike-price-input", strike)
    page.fill("#notional-input", notional)
    page.fill("#expiry-datetime-input", expiry_local)
    page.fill("#expiry-offset-input", expiry_offset)


def _fill_market_review(
    page, *, forward: str = "99.234375", volatility: str = "0.03395"
) -> None:
    page.fill("#forward-price-input", forward)
    page.fill("#volatility-input", volatility)


def _fill_advanced_overrides(
    page,
    *,
    last_coupon_date: str = "2030-07-31",
    curve_nodes=(("1M", "0.0374"), ("1Y", "0.0374")),
    leave_open: bool = False,
) -> None:
    """Fill everything that lives inside the collapsed Advanced section."""

    if _is_actually_hidden(page, "advanced-body"):
        page.click("#advanced-head")
    page.select_option("#day-count-select", "ACT_ACT_ISDA")
    page.select_option("#bond-type-select", "FIXED_COUPON_BULLET")
    page.fill("#ex-dividend-days-input", "0")
    page.fill("#last-coupon-date-input", last_coupon_date)
    page.select_option("#bond-status-select", "ACTIVE")
    page.fill("#reporting-date-input", "2026-10-21")
    page.fill("#forward-settlement-date-input", "2026-10-21")
    page.fill("#option-settlement-date-input", "2026-10-21")
    _set_curve_nodes(page, curve_nodes)
    if not leave_open:
        page.click("#advanced-head")


def _complete_draft(page, **kwargs) -> None:
    """Complete every remaining input a Bloomberg-loaded draft needs.

    Deliberately writes each value through the real control the trader uses, so
    this doubles as proof that every required input actually has a UI. Waits for
    the server-side builder validation to settle, since Price is gated on it.
    """

    trade_keys = ("option_type", "position", "strike", "notional", "expiry_local", "expiry_offset")
    trade_kwargs = {key: kwargs.pop(key) for key in trade_keys if key in kwargs}
    market_kwargs = {
        key: kwargs.pop(key) for key in ("forward", "volatility") if key in kwargs
    }
    _fill_trade_group(page, **trade_kwargs)
    _fill_market_review(page, **market_kwargs)
    _fill_advanced_overrides(page, **kwargs)


def _wait_for_price_enabled(page) -> None:
    page.wait_for_function(
        "() => !document.querySelector('#price-btn').classList.contains('is-disabled')"
    )


def _wait_for_refresh_enabled(page) -> None:
    """A failed refresh invalidates the builder validation too, so Refresh comes
    back only once the real builder has re-confirmed the (unchanged) draft."""

    page.wait_for_function(
        "() => !document.querySelector('#bloomberg-refresh-btn')"
        ".classList.contains('is-disabled')"
    )


# --- Initial state -----------------------------------------------------------


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
    assert _resolved_bond_panel_hidden(page)


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


# --- Acceptance criterion 2: the ordinary workflow is 6-9 groups -------------


@_PLAYWRIGHT_SKIP
def test_ordinary_workflow_is_between_six_and_nine_groups(server_url, page) -> None:
    """The headline product check: the normal trader flow is a short sequence of
    real decisions and reviews, not a wall of backend contract fields."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    ordinary = page.evaluate("() => window.__shioriTestOrdinaryWorkflowGroupIds()")
    assert 6 <= len(ordinary) <= 9
    assert ordinary == [
        "instrument",
        "option-type",
        "position",
        "strike",
        "notional",
        "expiry",
        "forward-review",
        "vol-review",
        "discounting-review",
    ]
    # Every ordinary group is actually present on the main screen, and the main
    # screen carries nothing else that claims to be a workflow group.
    assert _main_screen_group_ids(page) == ordinary


@_PLAYWRIGHT_SKIP
def test_main_screen_asks_for_no_backend_metadata(server_url, page) -> None:
    """Requirement 2: technical metadata, schedule details and typed enums are
    not on the main screen. Each of these controls exists, but only inside the
    collapsed Advanced section."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    assert _is_actually_hidden(page, "advanced-body")
    for selector in (
        "#day-count-select",
        "#bond-type-select",
        "#ex-dividend-days-input",
        "#last-coupon-date-input",
        "#bond-status-select",
        "#reporting-date-input",
        "#forward-settlement-date-input",
        "#option-settlement-date-input",
        "#curve-rows",
        "#valuation-date-input",
        "#as-of-timestamp-input",
        "#pricing-timestamp-input",
        "#expiry-timestamp-input",
        "#timing-product-id",
        "#timing-snapshot-id",
        "#bond-master-body",
    ):
        assert page.query_selector(selector) is not None, f"{selector} disappeared entirely"
        assert page.eval_on_selector(
            selector, "el => el.closest('#advanced-body') !== null"
        ), f"{selector} is not inside the collapsed Advanced section"


@_PLAYWRIGHT_SKIP
def test_advanced_is_collapsed_by_default_and_toggles(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    assert page.eval_on_selector("#advanced-body", "el => el.hidden")
    assert _is_actually_hidden(page, "advanced-body")
    assert page.inner_text("#advanced-indicator") == "Expand"

    page.click("#advanced-head")
    assert not page.eval_on_selector("#advanced-body", "el => el.hidden")
    assert not _is_actually_hidden(page, "advanced-body")
    assert page.inner_text("#advanced-indicator") == "Collapse"

    page.click("#advanced-head")
    assert _is_actually_hidden(page, "advanced-body")


@_PLAYWRIGHT_SKIP
def test_sourced_and_derived_values_are_read_only(server_url, page) -> None:
    """Requirement 5: sourced / derived values are read-only by default."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    page.click("#advanced-head")

    for selector in (
        "#valuation-date-input",
        "#as-of-timestamp-input",
        "#pricing-timestamp-input",
        "#expiry-timestamp-input",
    ):
        assert page.eval_on_selector(selector, "el => el.readOnly"), selector


# --- Bloomberg lookup: UST and Gilt both start the workflow ------------------


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
def test_successful_lookup_shows_resolved_bond_identity_and_the_workflow(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    assert page.inner_text("#resolved-bond-name") == "UNITED STATES TREAS NTS"
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.inner_text("#resolved-bond-cusip") == "91282CLJ8"
    assert page.inner_text("#resolved-bond-currency") == "USD"
    assert page.inner_text("#resolved-bond-clean-price") == "99.750000"
    assert page.inner_text("#resolved-bond-accrued") == "0.420000"
    assert page.inner_text("#resolved-bond-source") == "BLOOMBERG_DAPI"

    # The workflow shows immediately, but the instrument header (which needs a
    # genuinely completed price) stays hidden.
    assert not page.eval_on_selector("#workspace-section", "el => el.hidden")
    assert not _is_actually_hidden(page, "workspace-section")
    assert page.eval_on_selector("#instrument-header-section", "el => el.hidden")

    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")


@_PLAYWRIGHT_SKIP
def test_acquisition_event_mechanically_populates_timing_without_changing_its_local_date(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    acquired_at = "2026-07-20T00:30:00+08:00"
    _load_bloomberg_bond(page, response=_treasury_lookup_response(acquired_at=acquired_at))

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    # Pricing preserves the recorded acquisition spelling and offset exactly.
    assert draft["pricing_timestamp"] == acquired_at
    assert page.input_value("#pricing-timestamp-input") == acquired_at
    # Valuation uses the represented local date, even though the instant is
    # still July 19 in UTC.
    assert draft["valuation_date"] == "2026-07-20"
    assert page.input_value("#valuation-date-input") == "2026-07-20"
    # As-of follows the already-approved one-field UTC normalization rule.
    assert draft["as_of_timestamp"] == "2026-07-19T16:30:00Z"
    assert page.input_value("#as-of-timestamp-input") == "2026-07-19T16:30:00Z"

    page.click("#advanced-head")
    assert "Bloomberg acquisition event" in page.inner_text("#adv-derived")


@_PLAYWRIGHT_SKIP
def test_deterministic_policy_fields_are_populated_without_asking_the_trader(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    # The only values the reviewed pricing guard accepts for this route.
    assert draft["bond_option"]["payoff_basis"] == "PRICE"
    assert draft["bond_option"]["exercise_style"] == "EUROPEAN"
    assert draft["bond_option"]["settlement_type"] == "CASH"
    # Shiori identifiers and truthful provenance, not market data.
    assert draft["bond_option"]["product_id"].startswith("SHIORI-WORKBENCH-")
    assert draft["snapshot_id"].startswith("SHIORI-SNAPSHOT-")
    assert draft["source_system"] == "SHIORI_MANUAL_WORKBENCH"
    assert draft["forward_clean_price_input"]["source_system"] == "MANUAL_TRADER_ENTRY"
    assert draft["volatility_input"]["source_system"] == "MANUAL_TRADER_ENTRY"
    # The contract requires the forward's side to equal the spot side, so it is
    # mirrored rather than asked for twice.
    assert draft["forward_clean_price_input"]["quote_side"] == draft["bond_quote"]["quote_side"]
    # Direct price vol only; YIELD_VOL is not offered anywhere.
    assert draft["volatility_input"]["volatility_basis"] == "PRICE_VOL"
    assert page.eval_on_selector_all(
        "#volatility-basis-select option", "els => els.map(e => e.value)"
    ) == ["PRICE_VOL", "EQUIVALENT_PRICE_VOL"]
    assert draft["bond_option"]["expiry_date"] is None


# --- Bloomberg raw descriptions stay display-only ---------------------------


@_PLAYWRIGHT_SKIP
def test_bloomberg_raw_descriptions_are_evidence_and_never_enter_the_typed_draft(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    page.click("#advanced-head")

    # Route-consumed descriptions are shown beside the enum the trader must
    # pick; the unused calculation type remains display-only provenance.
    assert page.inner_text("#hint-day-count") == "ACT/ACT"
    assert page.inner_text("#hint-bond-type") == "AT MATURITY"
    assert page.inner_text("#details-bloomberg-calc-type") == "STREET CONVENTION"

    # Nothing was auto-selected from them, and no removed typed field enters the
    # standalone request draft.
    assert page.input_value("#day-count-select") == ""
    assert page.input_value("#bond-type-select") == ""
    reference = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0]"
    )
    assert reference["day_count"] is None
    assert reference["bond_type"] is None
    assert reference["status"] is None
    assert reference["ex_dividend_days"] is None
    assert reference["last_coupon_date"] is None
    assert "yield_convention" not in reference
    assert "business_day_convention" not in reference
    assert "redemption_amount" not in reference


@_PLAYWRIGHT_SKIP
def test_gilt_raw_descriptions_are_never_mapped_either(server_url, page) -> None:
    """The Gilt's own prohibited strings -- 'NORMAL' and 'UK:BUMP/DMO METHOD' --
    must not become FIXED_COUPON_BULLET or a yield convention."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page, identifier="GB00BFX0ZL78", response=_gilt_lookup_response()
    )
    page.click("#advanced-head")

    assert page.inner_text("#hint-bond-type") == "NORMAL"
    assert page.inner_text("#details-bloomberg-calc-type") == "UK:BUMP/DMO METHOD"
    assert page.input_value("#bond-type-select") == ""
    assert page.input_value("#day-count-select") == ""
    reference = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0]"
    )
    assert reference["bond_type"] is None
    assert reference["day_count"] is None


@_PLAYWRIGHT_SKIP
def test_confirmed_bond_master_values_enter_the_clean_draft_and_render(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(
            bond_master={"coupon": 0.04125, "maturity_date": "2031-01-31"}
        ),
    )

    # Stored/priced internally as a decimal fraction (0.04125), displayed as a
    # percentage (4.125%).
    assert page.inner_text("#resolved-bond-coupon") == "4.125%"
    assert page.inner_text("#resolved-bond-maturity") == "2031-01-31"
    page.click("#advanced-head")
    assert page.inner_text("#details-coupon") == "4.125%"
    assert page.inner_text("#details-maturity") == "2031-01-31"
    # A field still unconfirmed/unreturned stays honestly "Not available".
    assert page.inner_text("#details-day-count") == "Not available"
    assert page.inner_text("#details-callable") == "Not available"


@pytest.mark.parametrize(
    ("raw_coupon", "expected_percent"),
    [(0.0375, "3.750%"), (0.01625, "1.625%")],
)
@_PLAYWRIGHT_SKIP
def test_coupon_displays_as_a_percentage_while_the_draft_keeps_the_decimal_fraction(
    server_url, page, raw_coupon, expected_percent
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(bond_master={"coupon": raw_coupon}),
    )

    assert page.inner_text("#resolved-bond-coupon") == expected_percent
    draft_coupon = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0].coupon"
    )
    assert draft_coupon == raw_coupon


@_PLAYWRIGHT_SKIP
def test_missing_bond_master_fields_show_not_available_and_block_with_an_explanation(
    server_url, page
) -> None:
    """A wholly-null Bond Master must never degrade the identity/quote result,
    and must never be papered over with a convention default -- it blocks with
    its own explanation instead."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)  # default response: bond_master entirely None

    assert page.inner_text("#resolved-bond-name") == "UNITED STATES TREAS NTS"
    assert page.inner_text("#resolved-bond-clean-price") == "99.750000"
    assert page.inner_text("#resolved-bond-coupon") == "Not available"
    assert "bloomberg-reference" in _unresolved_group_ids(page)

    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_advanced_overrides(page)
    page.wait_for_timeout(300)

    # No UI anywhere offers to type a Bond Master field by hand.
    assert page.query_selector("#coupon-input") is None
    assert page.query_selector("#maturity-date-input") is None
    assert _is_disabled(page, "#price-btn")
    assert "bloomberg-reference" in _unresolved_group_ids(page)


# --- Acceptance criterion 4 + 6: one clear unresolved dependency -------------


@_PLAYWRIGHT_SKIP
def test_no_bond_loaded_shows_one_unresolved_dependency(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)

    assert not page.eval_on_selector("#unresolved-dependency", "el => el.hidden")
    assert page.inner_text("#unresolved-title") == "No bond is loaded"
    assert "Nothing else is outstanding." in page.inner_text("#unresolved-also")


@_PLAYWRIGHT_SKIP
def test_missing_forward_states_what_why_evidence_and_next_step(server_url, page) -> None:
    """Acceptance criterion 6, for the forward: what is missing, why, what
    Bloomberg actually answered, and what the trader can do next."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _fill_trade_group(page)
    page.wait_for_timeout(150)

    assert page.inner_text("#unresolved-title") == (
        "Forward clean price cannot be sourced from Bloomberg"
    )
    assert "forward clean price" in page.inner_text("#unresolved-missing").lower()
    assert "OPT_UNDL_FORWARD_PX" in page.inner_text("#unresolved-why")
    evidence = page.inner_text("#unresolved-evidence")
    assert "BAD_FLD" in evidence
    assert "Field not applicable to security" in evidence
    assert "OP046" in evidence and "OP188" in evidence
    # OP131 is named only to say it is never sent -- never defaulted to 0.
    assert "never defaulted to 0" in evidence
    next_step = page.inner_text("#unresolved-next")
    assert "Enter the forward clean price" in next_step
    assert "repo" in next_step

    assert _is_disabled(page, "#price-btn")
    assert page.inner_text("#forward-review-status") == "Trader override required"


@_PLAYWRIGHT_SKIP
def test_missing_direct_price_vol_states_the_real_bloomberg_answer(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _fill_trade_group(page)
    page.fill("#forward-price-input", "99.234375")
    page.wait_for_timeout(150)

    assert page.inner_text("#unresolved-title") == (
        "Direct price volatility cannot be sourced from Bloomberg"
    )
    evidence = page.inner_text("#unresolved-evidence")
    assert "PRICE_VOL returned BAD_FLD" in evidence
    assert "EQUIVALENT_PRICE_VOL returned BAD_FLD" in evidence
    assert "YIELD_VOL" in page.inner_text("#unresolved-next")
    assert _is_disabled(page, "#price-btn")
    assert page.inner_text("#vol-review-status") == "Trader override required"
    # The forward is settled by now, so it reads as reviewed.
    assert page.inner_text("#forward-review-status") == "Reviewed"


@_PLAYWRIGHT_SKIP
def test_missing_discounting_states_the_unapproved_methodology(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _fill_trade_group(page)
    _fill_market_review(page)
    page.wait_for_timeout(150)

    assert page.inner_text("#unresolved-title") == (
        "Option Discount Curve has no approved Bloomberg source"
    )
    evidence = page.inner_text("#unresolved-evidence")
    assert "MMkt, repo, FTP, par and swap rates must not be relabelled" in evidence
    assert "SWDF" in evidence
    assert "never bootstraps, converts or extrapolates" in page.inner_text("#unresolved-next")
    assert _is_disabled(page, "#price-btn")
    assert page.inner_text("#discounting-review-status") == "Trader override required"


@_PLAYWRIGHT_SKIP
def test_only_one_unresolved_dependency_is_explained_at_a_time(server_url, page) -> None:
    """Requirement 4: never a scattering of unexplained blank fields. Exactly one
    blocker is explained; the rest are named compactly beneath it."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    assert page.eval_on_selector_all("#unresolved-dependency .ud-title", "els => els.length") == 1
    also = page.inner_text("#unresolved-also")
    assert also.startswith("Also outstanding, one at a time:")
    # Group names, never raw contract field paths.
    assert "bond_reference_data_universe" not in also
    assert "Bond reference override" in also


@_PLAYWRIGHT_SKIP
def test_unresolved_go_to_button_opens_advanced_and_focuses_the_control(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_advanced_overrides(page, curve_nodes=())
    page.wait_for_timeout(150)

    assert _is_actually_hidden(page, "advanced-body")
    assert page.inner_text("#unresolved-title") == (
        "Option Discount Curve has no approved Bloomberg source"
    )
    page.click("#unresolved-goto-btn")
    page.wait_for_timeout(150)

    assert not _is_actually_hidden(page, "advanced-body")
    assert page.evaluate(
        "() => document.activeElement.classList.contains('curve-tenor-input')"
    )


# --- Acceptance criterion 5: real typed builder, real engine ------------------


@_PLAYWRIGHT_SKIP
def test_ust_prices_end_to_end_through_the_real_engine(server_url, page) -> None:
    """The headline acceptance test: load a real-shaped UST, complete it through
    the browser controls only, and get a genuine premium and Greeks out of the
    existing reviewed pricing path."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    assert _is_disabled(page, "#price-btn")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    assert _unresolved_group_ids(page) == []
    assert page.eval_on_selector("#unresolved-dependency", "el => el.hidden")

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    price_total = page.inner_text("#price-total")
    assert price_total not in ("—", "")
    assert float(price_total) > 0
    assert float(page.inner_text("#price-per-100")) > 0
    for greek in ("#greek-delta", "#greek-gamma", "#greek-vega", "#greek-theta"):
        assert page.inner_text(greek) not in ("—", "")
    assert page.inner_text("#result-currency") == "USD"
    assert page.inner_text("#instr-isin") == "US91282CLJ89"
    assert not _is_disabled(page, "#download-json-btn")
    assert not _is_disabled(page, "#download-markdown-btn")


@_PLAYWRIGHT_SKIP
def test_gilt_shaped_regular_grid_bond_prices_end_to_end_through_the_real_engine(
    server_url, page
) -> None:
    """A GBP, Gilt-shaped bond with a **regular** coupon grid starts from the
    same Bloomberg lookup and reaches the same reviewed engine.

    This proves the workflow is not USD/UST-specific -- currency, identifiers
    and curve nodes all follow the loaded instrument. It deliberately does *not*
    claim that every real conventional Gilt prices: a Gilt whose issue/
    first-coupon structure carries a stub is still rejected by the reviewed
    adapter, which
    ``test_a_gilt_with_a_real_world_stub_schedule_fails_truthfully_rather_than_pricing``
    below pins.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page, identifier="GB00BFX0ZL78", response=_gilt_lookup_response()
    )
    assert page.inner_text("#resolved-bond-name") == "UNITED KINGDOM GILT"
    assert page.inner_text("#resolved-bond-currency") == "GBP"

    _complete_draft(page, strike="95.50", forward="96.05", last_coupon_date="2028-04-22")
    _wait_for_price_enabled(page)

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    assert float(page.inner_text("#price-total")) > 0
    assert page.inner_text("#result-currency") == "GBP"
    assert page.inner_text("#instr-isin") == "GB00BFX0ZL78"
    # The curve nodes the trader entered were recorded in the Gilt's own
    # currency -- never carried over from a prior instrument.
    nodes = page.evaluate("() => window.__shioriTestGetCurrentDraft().curve_points")
    assert {node["currency"] for node in nodes} == {"GBP"}


@_PLAYWRIGHT_SKIP
def test_a_gilt_with_a_real_world_stub_schedule_fails_truthfully_rather_than_pricing(
    server_url, page
) -> None:
    """Pins the current conventional-Gilt limitation honestly.

    A real conventional Gilt's issue/first-coupon structure commonly carries a
    stub. The typed builder accepts such a bond -- nothing about it is
    structurally ineligible -- and the reviewed coupon adapter then refuses to
    approximate the irregular grid, so the run comes back as a truthful FAILED
    result naming the exact problem.

    This is an existing adapter limitation, not a regression from the
    minimum-input workflow, and it is the reason #143 cannot be called complete
    for conventional Gilts. No stub-schedule methodology is invented here to
    make it price.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="GB00BFX0ZL78",
        response=_gilt_lookup_response(bond_master=_GILT_STUB_BOND_MASTER),
    )
    _complete_draft(page, strike="95.50", forward="96.05", last_coupon_date="2028-04-22")

    # The typed builder raises no objection -- the bond is structurally eligible.
    _wait_for_price_enabled(page)
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "ready"

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Pricing failed")

    banner = page.inner_text("#pricing-error-banner")
    assert "irregular coupon grid" in banner
    assert "no stub approximation is computed" in banner
    # No fabricated premium or Greek is left on screen.
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#greek-delta") == "—"


@pytest.mark.parametrize("option_type", ["CALL", "PUT"])
@pytest.mark.parametrize("position", ["BUY", "SELL"])
@_PLAYWRIGHT_SKIP
def test_every_call_put_buy_sell_combination_prices(
    server_url, page, option_type, position
) -> None:
    """Acceptance criterion 8: Call / Put and Buy / Sell are each exercised
    through the real engine, and the position sign reaches the position Greeks."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page, option_type=option_type, position=position)
    _wait_for_price_enabled(page)

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    assert float(page.inner_text("#price-total")) > 0
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_option"]["option_type"] == option_type
    assert draft["bond_option"]["position"] == position


@_PLAYWRIGHT_SKIP
def test_price_gate_is_decided_by_the_real_typed_builder_not_by_non_blank_fields(
    server_url, page
) -> None:
    """Requirement 5: Price enablement must come from real route validation.

    Every field is non-blank and every workflow group is structurally resolved,
    but the bond is callable -- which the reviewed standalone eligibility gate
    rejects. A purely front-end "is it non-empty" check would enable Price here.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            bond_master={**_TREASURY_BOND_MASTER, "callable_flag": True}
        ),
    )
    _complete_draft(page)
    page.wait_for_timeout(600)

    assert _unresolved_group_ids(page) == []  # structurally complete...
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "failed"
    assert _is_disabled(page, "#price-btn")  # ...but the real builder said no
    assert page.inner_text("#unresolved-title") == "The typed builder rejected this draft"
    why = page.inner_text("#unresolved-why")
    assert "FOUND_INELIGIBLE" in why
    assert "callable" in why


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


# --- One expiry interaction --------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_one_expiry_interaction_composes_both_contract_fields(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    page.fill("#expiry-datetime-input", "2026-10-20T17:20")
    page.fill("#expiry-offset-input", "+08:00")
    page.wait_for_timeout(150)

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_option"]["expiry_date"] == "2026-10-20"
    assert draft["expiry_timestamp"] == "2026-10-20T17:20:00+08:00"
    preview = page.inner_text("#expiry-derived-preview")
    assert "Expiry date 2026-10-20" in preview
    assert "2026-10-20T17:20:00+08:00" in preview
    assert "same instant in UTC: 2026-10-20T09:20:00Z" in preview


@_PLAYWRIGHT_SKIP
def test_expiry_without_an_offset_is_flagged_and_never_guessed(server_url, page) -> None:
    """No default time-of-day or timezone convention is approved, so a missing
    offset blocks rather than being filled in."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    page.fill("#expiry-datetime-input", "2026-10-20T17:20")
    page.wait_for_timeout(150)

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["expiry_timestamp"] is None
    assert "UTC offset" in page.inner_text("#expiry-derived-preview")
    assert "expiry" in _unresolved_group_ids(page)

    page.fill("#expiry-offset-input", "BST")
    page.wait_for_timeout(150)
    assert "is not a UTC offset" in page.inner_text("#expiry-derived-preview")
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().expiry_timestamp") is None


@_PLAYWRIGHT_SKIP
def test_local_times_whose_utc_date_differs_still_match_their_explicit_dates(
    server_url, page
) -> None:
    """The timing contract compares the *represented local* calendar date.

    An 05:20 +08:00 expiry is the previous day in UTC, but it belongs to the
    local expiry date the trader entered -- so it must price.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page, response=_treasury_lookup_response(acquired_at="2026-07-20T05:00:00+08:00")
    )
    _complete_draft(page, expiry_local="2026-10-20T05:20", expiry_offset="+08:00")
    _wait_for_price_enabled(page)

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_option"]["expiry_date"] == "2026-10-20"
    assert draft["expiry_timestamp"] == "2026-10-20T05:20:00+08:00"
    assert "same instant in UTC: 2026-10-19T21:20:00Z" in page.inner_text(
        "#expiry-derived-preview"
    )

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert float(page.inner_text("#price-total")) > 0


@_PLAYWRIGHT_SKIP
def test_malformed_acquisition_timestamp_is_flagged_rather_than_silently_repaired(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            acquired_at="2026-07-20 11:28:00"  # no offset, space separator
        ),
    )
    page.click("#advanced-head")
    page.wait_for_timeout(120)

    preview = page.inner_text("#pricing-timestamp-utc")
    assert "explicit offset" in preview
    assert "same instant in UTC" not in preview
    assert page.eval_on_selector(
        "#pricing-timestamp-utc", "el => el.classList.contains('is-invalid')"
    )


# --- Option Discount Curve ----------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_curve_needs_at_least_two_valid_nodes(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    page.click("#advanced-head")

    assert "At least 2 valid curve nodes are required" in page.inner_text("#curve-coverage")

    _set_curve_nodes(page, [("1M", "0.0374")])
    assert "At least 2 valid curve nodes are required; 1 entered" in page.inner_text(
        "#curve-coverage"
    )
    assert _is_disabled(page, "#price-btn")


@_PLAYWRIGHT_SKIP
def test_curve_rejects_a_tenor_outside_the_existing_grammar(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    page.click("#advanced-head")

    # "1W" and "O/N" are real Bloomberg/FTP tenor labels the reviewed parser
    # deliberately does not accept -- they must not become nodes here either.
    _set_curve_nodes(page, [("1W", "0.0374"), ("1Y", "0.0374")])

    coverage = page.inner_text("#curve-coverage")
    assert "invalid node row" in coverage
    assert "nD / nM / nY" in coverage
    nodes = page.evaluate("() => window.__shioriTestGetCurrentDraft().curve_points")
    assert [node["tenor"] for node in nodes] == ["1Y"]


@_PLAYWRIGHT_SKIP
def test_insufficient_curve_coverage_blocks_pricing_with_the_exact_range(
    server_url, page
) -> None:
    """The reviewed interpolator rejects an out-of-range target rather than
    flat-extrapolating, so an uncovered date is a hard stop -- and the trader is
    told exactly which date, which coordinate, and which range."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    # 1M/2M cannot reach an option settling ~3 months out.
    _complete_draft(page, curve_nodes=(("1M", "0.0374"), ("2M", "0.0374")), leave_open=True)

    coverage = page.inner_text("#curve-coverage")
    assert "Option Settlement Date (2026-10-21)" in coverage
    assert "0.2548 years" in coverage
    assert "[0.0833, 0.1667] years" in coverage
    assert "never extrapolated" in coverage
    assert page.eval_on_selector("#curve-coverage", "el => el.classList.contains('is-blocking')")

    assert _is_disabled(page, "#price-btn")
    assert "discounting-review" in _unresolved_group_ids(page)

    # Extending the curve past the required coordinate unblocks pricing.
    _set_curve_nodes(page, [("1M", "0.0374"), ("1Y", "0.0374")])
    _wait_for_price_enabled(page)
    assert page.eval_on_selector("#curve-coverage", "el => el.classList.contains('is-covered')")


# --- Credit spread contract rules --------------------------------------------


@_PLAYWRIGHT_SKIP
def test_credit_spread_not_required_needs_no_spread_value_or_basis(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    credit = page.evaluate("() => window.__shioriTestGetCurrentDraft().credit_spread_input")
    assert credit["spread_treatment"] == "NOT_REQUIRED"
    # The contract forbids these two for NOT_REQUIRED -- no fabricated number.
    assert credit["credit_spread"] is None
    assert credit["credit_spread_basis"] is None
    # ... and requires a non-blank audit explanation.
    assert credit["override_or_fallback_audit"]
    assert "never reads credit_spread" in credit["override_or_fallback_audit"]
    # The trader is never asked about it anywhere on the page.
    assert page.query_selector("#credit-spread-input") is None


# --- Acceptance criterion 8: Advanced override provenance --------------------


@_PLAYWRIGHT_SKIP
def test_every_advanced_override_is_provenance_stamped(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    assert _override_provenance(page) == []

    _complete_draft(page, leave_open=True)
    page.wait_for_timeout(200)

    records = _override_provenance(page)
    stamped = {record["path"] for record in records}
    for path in (
        "forward_clean_price_input.forward_clean_price_per_100",
        "volatility_input.volatility",
        "volatility_input.volatility_basis",
        "curve_points",
        "bond_reference_data_universe.0.day_count",
        "bond_reference_data_universe.0.bond_type",
        "bond_reference_data_universe.0.ex_dividend_days",
        "bond_reference_data_universe.0.last_coupon_date",
        "bond_reference_data_universe.0.status",
        "reporting_date",
        "forward_settlement_date",
        "option_settlement_date",
    ):
        assert path in stamped, f"{path} was overridden without provenance"

    # Every stamp says what it is, why it could not be sourced, and which
    # Bloomberg acquisition event the run is anchored to.
    for record in records:
        assert record["source_system"] == "MANUAL_TRADER_ENTRY"
        assert record["basis"] == "TRADER_OVERRIDE"
        assert record["reason_not_sourced"]
        assert record["run_acquired_at"] == "2026-07-20T11:28:00+08:00"
        assert record["value"]

    # And the log is visible in Advanced, not just in memory.
    log_text = page.inner_text("#override-provenance-log")
    assert "Day Count = ACT_ACT_ISDA" in log_text
    assert "BAD_FLD" in log_text
    assert page.eval_on_selector("#override-provenance-empty", "el => el.hidden")


@_PLAYWRIGHT_SKIP
def test_review_rows_show_provenance_only_once_a_value_is_entered(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    assert "not entered yet" in page.inner_text("#forward-provenance")
    assert "not entered yet" in page.inner_text("#vol-provenance")

    _fill_market_review(page)
    page.wait_for_timeout(150)

    assert "MANUAL_TRADER_ENTRY · TRADER_OVERRIDE" in page.inner_text("#forward-provenance")
    assert "2026-07-20T11:28:00+08:00" in page.inner_text("#vol-provenance")


# --- Acceptance criterion 8: exports remain functional -----------------------


@_PLAYWRIGHT_SKIP
def test_exports_remain_functional_and_carry_override_provenance(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    with page.expect_download() as json_download:
        page.click("#download-json-btn")
    json_path = json_download.value.path()
    exported = json.loads(open(json_path, encoding="utf-8").read())
    assert exported["status"] == "SUCCESS"
    assert exported["total_notional_model_fair_premium"] > 0
    overrides = {record["path"] for record in exported["trader_override_provenance"]}
    assert "forward_clean_price_input.forward_clean_price_per_100" in overrides
    assert "volatility_input.volatility" in overrides

    with page.expect_download() as md_download:
        page.click("#download-markdown-btn")
    markdown = open(md_download.value.path(), encoding="utf-8").read()
    assert "# Shiori Standalone Bond Option — Current Run Export" in markdown
    assert "## Trader Override Provenance" in markdown
    assert "Forward Clean Price (per 100)" in markdown
    assert "trader overrides, never observed market data" in markdown


@_PLAYWRIGHT_SKIP
def test_download_buttons_disabled_without_a_completed_run(server_url, page) -> None:
    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)
    assert _is_disabled(page, "#download-json-btn")
    assert _is_disabled(page, "#download-markdown-btn")

    _load_bloomberg_bond(page)
    assert _is_disabled(page, "#download-json-btn")
    assert _is_disabled(page, "#download-markdown-btn")


# --- Acceptance criterion 7: state isolation ---------------------------------


@_PLAYWRIGHT_SKIP
def test_a_second_lookup_discards_every_input_override_and_result_from_the_first(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    first_draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert _override_provenance(page)

    _load_bloomberg_bond(
        page, identifier="GB00BFX0ZL78", response=_gilt_lookup_response()
    )

    # trader inputs
    for selector in (
        "#strike-price-input",
        "#notional-input",
        "#expiry-datetime-input",
        "#expiry-offset-input",
        "#volatility-input",
        "#forward-price-input",
    ):
        assert page.input_value(selector) == "", f"{selector} kept the prior bond's value"
    assert page.query_selector("#option-type-toggle .opt.on") is None
    assert page.query_selector("#position-toggle .opt.on") is None

    # overrides
    page.click("#advanced-head")
    for selector in (
        "#reporting-date-input",
        "#forward-settlement-date-input",
        "#option-settlement-date-input",
        "#ex-dividend-days-input",
        "#last-coupon-date-input",
        "#day-count-select",
        "#bond-type-select",
        "#bond-status-select",
    ):
        assert page.input_value(selector) == "", f"{selector} kept the prior bond's override"

    # curve rows
    assert page.eval_on_selector_all(
        ".curve-row .curve-tenor-input", "els => els.map(e => e.value)"
    ) == ["", ""]
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().curve_points") == []

    # provenance and pricing results
    assert _override_provenance(page) == []
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#greek-delta") == "—"
    assert _is_disabled(page, "#download-json-btn")

    # status and errors
    assert page.inner_text("#status-text") == "Bloomberg bond loaded"
    assert page.eval_on_selector("#pricing-error-banner", "el => el.hidden")
    assert not page.eval_on_selector("#status-indicator", "el => el.classList.contains('failed')")

    # a genuinely new draft identity
    second_draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert second_draft["bond_option"]["underlying_isin"] == "GB00BFX0ZL78"
    assert second_draft["bond_option"]["product_id"] != first_draft["bond_option"]["product_id"]
    assert second_draft["snapshot_id"] != first_draft["snapshot_id"]
    assert _is_disabled(page, "#price-btn")


@_PLAYWRIGHT_SKIP
def test_clear_removes_every_input_override_provenance_and_result(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page, leave_open=True)
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    page.click("#clear-btn")
    page.wait_for_timeout(200)

    assert page.inner_text("#status-text") == "No bond loaded"
    assert page.eval_on_selector("#workspace-section", "el => el.hidden")
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert _override_provenance(page) == []
    assert _resolved_bond_panel_hidden(page)
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#details-issuer") == "—"
    assert page.inner_text("#details-coupon") == "Not available"
    assert page.inner_text("#timing-product-id") == "—"
    assert page.inner_text("#timing-snapshot-id") == "—"
    for selector in (
        "#strike-price-input",
        "#expiry-datetime-input",
        "#valuation-date-input",
        "#as-of-timestamp-input",
        "#pricing-timestamp-input",
        "#expiry-timestamp-input",
        "#day-count-select",
        "#bond-status-select",
    ):
        assert page.input_value(selector) == ""
    # Volatility basis returns to its default rather than to blank.
    assert page.input_value("#volatility-basis-select") == "PRICE_VOL"
    assert page.eval_on_selector_all(
        ".curve-row .curve-tenor-input", "els => els.map(e => e.value)"
    ) == ["", ""]
    # Advanced returns to collapsed.
    assert _is_actually_hidden(page, "advanced-body")
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#download-json-btn")

    body_text = page.inner_text("body")
    for stale in ("Synthetic Test Issuer A", "XS0000000001"):
        assert stale not in body_text


@_PLAYWRIGHT_SKIP
def test_successful_refresh_reprices_through_the_bloomberg_route(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)

    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_refresh_display(acquired_at="2026-07-20T11:31:00+08:00")),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    assert page.inner_text("#price-total") == "1234.500000"
    # The refreshed quote replaces the displayed one.
    assert page.inner_text("#resolved-bond-clean-price") == "99.810000"
    assert page.inner_text("#resolved-bond-acquired-at") == "2026-07-20T11:31:00+08:00"


@_PLAYWRIGHT_SKIP
def test_a_refresh_adopts_the_new_acquisition_event_everywhere(server_url, page) -> None:
    """T1 -> T2 regression (Codex review of PR #152).

    A refresh reprices against a newer acquisition (T2) than the lookup that
    seeded the draft (T1): the route substitutes T2 as that run's
    ``pricing_timestamp`` and returns it verbatim as
    ``live_bloomberg_quote.acquired_at``. Every surface describing the run must
    therefore adopt T2 -- the draft's own anchor, the read-only timing display,
    the displayed quote, every override provenance stamp, and both exports. T1
    must not survive anywhere that describes this run.
    """

    t1 = "2026-07-20T11:28:00+08:00"
    t2 = "2026-07-20T11:31:00+08:00"

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(acquired_at=t1))
    _complete_draft(page)
    _wait_for_price_enabled(page)

    # Before any refresh, everything is anchored to the original lookup (T1).
    assert {record["run_acquired_at"] for record in _override_provenance(page)} == {t1}
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().pricing_timestamp") == t1

    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_refresh_display(acquired_at=t2)),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    # 1. The run's own acquisition anchor, and the acquisition-derived timing
    #    state shown read-only beside it.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["pricing_timestamp"] == t2
    assert page.input_value("#pricing-timestamp-input") == t2
    # The builder's own pricing_timestamp/valuation_date invariant had to hold
    # for this refresh to price at all, so the valuation date is unchanged --
    # and as_of stays the source-observation value this route never relabels.
    assert draft["valuation_date"] == "2026-07-20"
    assert draft["as_of_timestamp"] == "2026-07-20T03:28:00Z"

    # 2. The displayed quote.
    assert page.inner_text("#resolved-bond-acquired-at") == t2
    assert page.inner_text("#resolved-bond-clean-price") == "99.810000"

    # 3. Every override provenance stamp, in memory and on screen.
    assert {record["run_acquired_at"] for record in _override_provenance(page)} == {t2}
    assert t2 in page.inner_text("#override-provenance-log")
    assert t1 not in page.inner_text("#override-provenance-log")
    assert t2 in page.inner_text("#forward-provenance")

    # 4. Both exports, consistent with the live quote in the same artifact.
    with page.expect_download() as json_download:
        page.click("#download-json-btn")
    exported_text = open(json_download.value.path(), encoding="utf-8").read()
    exported = json.loads(exported_text)
    assert exported["live_bloomberg_quote"]["acquired_at"] == t2
    assert {r["run_acquired_at"] for r in exported["trader_override_provenance"]} == {t2}
    assert t1 not in exported_text

    with page.expect_download() as md_download:
        page.click("#download-markdown-btn")
    markdown = open(md_download.value.path(), encoding="utf-8").read()
    assert t2 in markdown
    assert t1 not in markdown


@_PLAYWRIGHT_SKIP
def test_a_refresh_adopts_the_new_quote_into_the_draft_that_becomes_the_run(
    server_url, page
) -> None:
    """Codex review round 2: the refreshed quote is what the route priced
    against, so the draft must adopt it -- not just the display-side bond.

    Otherwise the instrument header shows a stale clean price, and pressing
    Price next would silently re-price the T1 quote through ``/api/case`` and
    replace the refreshed result.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)

    before = page.evaluate("() => window.__shioriTestGetCurrentDraft().bond_quote")
    assert before["clean_price_per_100"] == 99.75

    refreshed_clean_price = 101.5
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _refresh_display(
                    acquired_at="2026-07-20T11:31:00+08:00", clean_price=refreshed_clean_price
                )
            ),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    # The draft that is now the active run carries the refreshed quote.
    quote = page.evaluate("() => window.__shioriTestGetCurrentDraft().bond_quote")
    assert quote["clean_price_per_100"] == refreshed_clean_price
    assert quote["accrued_interest_per_100"] == 0.44
    assert quote["isin"] == "US91282CLJ89"
    assert quote["source_system"] == "BLOOMBERG_DAPI"
    # Route-fixed fields are untouched, not invented.
    assert quote["price_type"] == "PRICE"
    assert quote["status"] == "ACTIVE"
    # The contract's forward-side-equals-spot-side invariant still holds.
    assert (
        page.evaluate(
            "() => window.__shioriTestGetCurrentDraft().forward_clean_price_input.quote_side"
        )
        == quote["quote_side"]
    )

    # The header context shows the refreshed quote, not the T1 one.
    assert page.inner_text("#stat-clean-price") == str(refreshed_clean_price)
    assert page.inner_text("#resolved-bond-clean-price") == "101.500000"

    # And pressing Price now re-prices the refreshed quote, not the stale one.
    priced_bodies: list = []

    def capture_case(route):
        priced_bodies.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/case", capture_case)
    page.click("#price-btn")
    _wait_until(lambda: len(priced_bodies) == 1)
    assert priced_bodies[0]["bond_quote"]["clean_price_per_100"] == refreshed_clean_price
    assert priced_bodies[0]["pricing_timestamp"] == "2026-07-20T11:31:00+08:00"


@_PLAYWRIGHT_SKIP
def test_changing_quote_side_then_refreshing_sends_a_matching_forward_side(
    server_url, page
) -> None:
    """Codex review round 3: the forward's side must be mirrored *before* the
    request, not after it.

    The route sources a spot quote on the newly selected side and substitutes it
    into the case before pricing, and the reviewed request contract requires
    ``forward_clean_price_input.quote_side`` to equal ``bond_quote.quote_side``.
    Mirroring after the response can never run, because the builder rejects the
    mismatch first -- so changing BID/MID/OFFER used to make every refresh fail
    and (since round 1) invalidate an otherwise usable run.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, side="MID", response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)
    assert (
        page.evaluate(
            "() => window.__shioriTestGetCurrentDraft().forward_clean_price_input.quote_side"
        )
        == "MID"
    )

    # The trader switches side, then refreshes.
    _select_quote_side(page, "OFFER")

    sent: list = []

    def route_refresh(route):
        sent.append(json.loads(route.request.post_data))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _refresh_display(
                    acquired_at="2026-07-20T11:31:00+08:00", quote_side="OFFER"
                )
            ),
        )

    page.route("**/api/case/bloomberg", route_refresh)
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    # The request itself already carried the new side on both the quote side
    # parameter and the forward input, so the builder has no mismatch to reject.
    assert len(sent) == 1
    assert sent[0]["quote_side"] == "OFFER"
    assert sent[0]["case"]["forward_clean_price_input"]["quote_side"] == "OFFER"

    # And the adopted draft is the case that was actually priced.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_clean_price_input"]["quote_side"] == "OFFER"
    assert draft["bond_quote"]["quote_side"] == "OFFER"


@_PLAYWRIGHT_SKIP
def test_a_refresh_never_restamps_the_bond_master_acquisition_time(
    server_url, page
) -> None:
    """Codex review round 3: a refresh re-acquires only the quote.

    The Bond Master fields and the raw display-only descriptions still come from
    the lookup that fetched them, so their acquisition time must stay there --
    restamping it to T2 would claim Shiori re-acquired coupon and schedule
    evidence it never touched.
    """

    t1 = "2026-07-20T11:28:00+08:00"
    t2 = "2026-07-20T11:31:00+08:00"

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(acquired_at=t1))
    _complete_draft(page)
    _wait_for_price_enabled(page)

    page.click("#advanced-head")
    assert page.inner_text("#details-acquired-at") == t1
    page.click("#advanced-head")

    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_refresh_display(acquired_at=t2)),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    # The quote moved to T2 ...
    assert page.inner_text("#resolved-bond-acquired-at") == t2
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().pricing_timestamp") == t2
    # ... while the Bond Master evidence still says when it was actually fetched.
    page.click("#advanced-head")
    assert page.inner_text("#details-acquired-at") == t1
    assert page.inner_text("#details-coupon") == "3.750%"
    assert page.inner_text("#details-bloomberg-day-count") == "ACT/ACT"


@_PLAYWRIGHT_SKIP
def test_a_validation_transport_failure_is_not_reported_as_a_builder_rejection(
    server_url, page
) -> None:
    """Codex review round 3: a transport failure must not strand the ticket.

    A refresh failure invalidates the builder validation, which then depends on
    a fresh ``/api/case/validate`` call. If that call fails at the transport
    level, the draft is unchanged and the builder has said nothing -- so Shiori
    must neither claim a rejection nor latch a state that leaves Price and
    Refresh both dead with no way back.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)

    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )

    # Every validation attempt fails at the transport level from here on.
    validate_attempts: list = []

    def fail_validate(route):
        validate_attempts.append(1)
        route.abort()

    page.route("**/api/case/validate", fail_validate)

    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg refresh failed")
    # Waited on via page-observable state rather than the Python-side counter:
    # Playwright's sync API only dispatches route handlers while a page call is
    # pumping its connection, so a bare sleep loop would never see the request.
    page.wait_for_function(
        "() => document.querySelector('#unresolved-title').textContent"
        " === 'Shiori cannot reach its own validator'"
    )
    # It keeps retrying on its own rather than latching -- proven by a second
    # intercepted attempt with no trader action in between.
    _wait_until_pumping(page, lambda: len(validate_attempts) >= 2)

    # Never described as a rejection, and never latched as one.
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "unknown"
    assert page.inner_text("#unresolved-title") == "Shiori cannot reach its own validator"
    assert "transport failure, not a rejection" in page.inner_text("#unresolved-evidence")
    assert "retries automatically" in page.inner_text("#unresolved-next")
    # The trader's ticket is still intact.
    assert page.input_value("#strike-price-input") == "99.32"

    # And it recovers on its own as soon as the bridge answers again -- no form
    # edit, no draft-wiping lookup, no dead end.
    page.unroute("**/api/case/validate", fail_validate)
    _wait_for_refresh_enabled(page)
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "ready"
    assert page.input_value("#strike-price-input") == "99.32"


@_PLAYWRIGHT_SKIP
def test_a_successful_retry_after_a_failed_refresh_restores_a_priceable_run(
    server_url, page
) -> None:
    """The recovery path the preserved trader inputs exist for: retry the very
    refresh that failed, and the untouched ticket prices immediately."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)

    attempts = {"n": 0}
    retried_acquisition = "2026-07-20T11:45:00+08:00"

    def route_refresh(route):
        attempts["n"] += 1
        if attempts["n"] == 1:
            route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
            )
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_refresh_display(acquired_at=retried_acquisition)),
            )

    page.route("**/api/case/bloomberg", route_refresh)

    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg refresh failed")
    assert _is_disabled(page, "#price-btn")
    _wait_for_refresh_enabled(page)

    # Retrying the same action re-sources the quote -- no re-typing, no new
    # lookup, and no loss of the ticket.
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    assert page.evaluate("() => window.__shioriTestSourcedQuoteInvalidated()") is False
    assert not _resolved_bond_panel_hidden(page)
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.inner_text("#resolved-bond-acquired-at") == retried_acquisition
    assert "instrument" not in _unresolved_group_ids(page)
    # The Bloomberg hints come back with the rest of the re-sourced state.
    page.click("#advanced-head")
    assert page.inner_text("#hint-day-count") == "ACT/ACT"
    assert page.inner_text("#hint-bond-type") == "AT MATURITY"
    page.click("#advanced-head")
    assert not _is_disabled(page, "#price-btn")
    assert page.input_value("#strike-price-input") == "99.32"
    assert float(page.inner_text("#price-total")) > 0


@_PLAYWRIGHT_SKIP
def test_a_failed_refresh_never_falls_back_to_the_stale_quote_or_instrument(
    server_url, page
) -> None:
    """Requirement 5: a failed Bloomberg refresh must not fall back to showing
    the old quote or old instrument data.

    Everything Shiori sourced is invalidated -- and, per the Codex review of
    PR #152, nothing the trader authored is. A transient DAPI hiccup must not
    make a trader retype a completed ticket.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg refresh failed")

    assert "Bloomberg DAPI session failed to start" in page.inner_text("#pricing-error-banner")

    # Sourced state is gone: no stale quote, instrument identity, Bond Master,
    # priced header or exportable run survives.
    assert _resolved_bond_panel_hidden(page)
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#greek-delta") == "—"
    assert page.inner_text("#details-issuer") == "—"
    assert page.inner_text("#details-coupon") == "Not available"
    assert page.eval_on_selector("#instrument-header-section", "el => el.hidden")
    assert _is_disabled(page, "#download-json-btn")

    # Nothing can be priced against the data Shiori just disowned -- but Refresh
    # stays available, because re-sourcing is the way back.
    assert page.evaluate("() => window.__shioriTestSourcedQuoteInvalidated()") is True
    assert _is_disabled(page, "#price-btn")
    _wait_for_refresh_enabled(page)
    # Price is still off even once the builder re-confirms the draft: the
    # sourced quote, not the draft, is what went missing.
    assert _is_disabled(page, "#price-btn")
    assert "instrument" in _unresolved_group_ids(page)
    assert page.inner_text("#unresolved-title") == (
        "The sourced quote for this run has been invalidated"
    )
    assert "will not price against them" in page.inner_text("#unresolved-why")
    assert "Press Refresh Bloomberg & Price" in page.inner_text("#unresolved-next")
    assert page.inner_text("#instrument-group-status") == (
        "Sourced quote invalidated — refresh to re-source"
    )

    # The trader's own ticket is untouched.
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is not None
    assert page.input_value("#strike-price-input") == "99.32"
    assert page.input_value("#notional-input") == "1000000"
    assert page.input_value("#forward-price-input") == "99.234375"
    assert page.input_value("#volatility-input") == "0.03395"
    assert page.input_value("#expiry-offset-input") == "+08:00"
    assert page.query_selector('#option-type-toggle .opt[data-value="CALL"].on') is not None
    assert page.query_selector('#position-toggle .opt[data-value="BUY"].on') is not None
    page.click("#advanced-head")
    assert page.input_value("#day-count-select") == "ACT_ACT_ISDA"
    assert page.input_value("#reporting-date-input") == "2026-10-21"
    assert page.eval_on_selector_all(
        ".curve-row .curve-tenor-input", "els => els.map(e => e.value)"
    ) == ["1M", "1Y"]

    # Codex review round 2: the Advanced Bloomberg hints are instrument evidence
    # from the retained bond, and must go with the rest of the disowned sourced
    # state -- especially the last-coupon hint, which sits beside a preserved
    # trader override it could otherwise steer.
    assert page.inner_text("#hint-day-count") == "Not available"
    assert page.inner_text("#hint-bond-type") == "Not available"
    assert page.inner_text("#hint-last-coupon-date") == "Not available"
    assert page.inner_text("#details-bloomberg-day-count") == "Not available"


@_PLAYWRIGHT_SKIP
def test_a_mistyped_identifier_reports_the_problem_without_discarding_the_run(
    server_url, page
) -> None:
    """A client-side input mistake is caught before any request is made, so no
    Bloomberg answer is in question and nothing on screen has gone stale -- it
    must not throw away a ticket the trader has already filled in."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    page.fill("#strike-price-input", "100")

    calls = []
    page.route("**/api/bloomberg/bond", lambda route: calls.append(route) or route.abort())
    page.fill("#bond-identifier-input", "TOOSHORT")
    page.click("#load-bloomberg-bond-btn")
    page.wait_for_timeout(200)

    assert calls == []
    assert page.inner_text("#status-text") == "Check the lookup inputs"
    assert "ISIN or 9-character CUSIP" in page.inner_text("#pricing-error-banner")
    # The already-loaded bond and the trader's own input both survive.
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.input_value("#strike-price-input") == "100"
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is not None


@_PLAYWRIGHT_SKIP
def test_a_failed_lookup_never_leaves_the_previous_instrument_on_screen(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
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
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg lookup failed")

    assert "Bloomberg DAPI session failed to start" in page.inner_text("#pricing-error-banner")
    # A lookup failure means the trader was resolving an instrument, so there is
    # no ticket this failure can safely belong to: the whole run goes, exactly
    # like Clear. Nothing from the previous bond may survive to be applied to a
    # different one.
    assert _resolved_bond_panel_hidden(page)
    assert page.inner_text("#resolved-bond-isin") == "—"
    assert page.inner_text("#details-issuer") == "—"
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert _override_provenance(page) == []
    assert page.input_value("#strike-price-input") == ""
    assert page.eval_on_selector("#workspace-section", "el => el.hidden")
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert _is_disabled(page, "#download-json-btn")


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
                body=json.dumps(
                    _default_bloomberg_bond_lookup_response(name="SECOND LOOKUP BOND")
                ),
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
                        name="SECOND LOOKUP BOND", bond_master={"coupon": 0.025}
                    )
                ),
            )

    page.route("**/api/bloomberg/bond", route_lookup)
    page.fill("#bond-identifier-input", "US91282CLJ89")
    _select_quote_side(page, "MID")
    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: len(pending) == 1)

    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: page.inner_text("#resolved-bond-name") == "SECOND LOOKUP BOND")
    assert page.inner_text("#resolved-bond-coupon") == "2.500%"

    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _default_bloomberg_bond_lookup_response(
                name="FIRST LOOKUP BOND", bond_master={"coupon": 0.09999}
            )
        ),
    )
    page.wait_for_timeout(300)

    assert page.inner_text("#resolved-bond-name") == "SECOND LOOKUP BOND"
    assert page.inner_text("#resolved-bond-coupon") == "2.500%"
