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
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available

# Issue #157: the UST Advanced-field profile derives its coupon date and its
# settlement roll from QuantLib, so tests that assert an auto-filled value need
# it installed. Locally that is a skip, exactly like the QuantLib skips
# elsewhere in this suite; CI installs the optional 'quant' group.
_QUANTLIB_SKIP = pytest.mark.skipif(
    not is_quantlib_available(),
    reason="QuantLib is not installed in this environment",
)

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
    """The ``display`` half of a ``POST /api/case/bloomberg`` success payload.

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


def _refresh_payload(
    *,
    case: dict,
    acquired_at: str,
    clean_price: float = 99.81,
    quote_side: str = "MID",
) -> dict:
    """A full ``POST /api/case/bloomberg`` success payload: ``{case, display}``.

    The route returns the envelope it actually priced -- the sent case with the
    live quote and the acquisition timestamp already substituted -- so this
    fixture performs the same two substitutions on the case it was given. The
    browser adopts this ``case`` verbatim and assembles nothing itself.
    """

    display = _refresh_display(
        acquired_at=acquired_at, clean_price=clean_price, quote_side=quote_side
    )
    priced_case = json.loads(json.dumps(case))
    priced_case["pricing_timestamp"] = acquired_at
    priced_case["bond_quote"] = {
        **priced_case["bond_quote"],
        "quote_side": quote_side,
        "clean_price_per_100": clean_price,
        "accrued_interest_per_100": display["live_bloomberg_quote"][
            "accrued_interest_per_100"
        ],
    }
    return {"case": priced_case, "display": display}


def _refresh_route(
    *,
    acquired_at: str,
    clean_price: float = 99.81,
    quote_side: str = "MID",
    sent: list | None = None,
):
    """Return a ``/api/case/bloomberg`` handler that answers like the real route.

    It reads the case the browser actually sent and echoes it back with the two
    substitutions the route makes, so a test can never accidentally prove the
    browser assembled a correct case for itself. Pass ``sent`` to capture the
    request bodies.
    """

    def handler(route):
        body = json.loads(route.request.post_data)
        if sent is not None:
            sent.append(body)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                _refresh_payload(
                    case=body["case"],
                    acquired_at=acquired_at,
                    clean_price=clean_price,
                    quote_side=quote_side,
                )
            ),
        )

    return handler


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


# The eight profile-owned Advanced controls, as the page itself spells them.
_PROFILE_CONTROL_SELECTORS = (
    "#day-count-select",
    "#bond-type-select",
    "#ex-dividend-days-input",
    "#last-coupon-date-input",
    "#bond-status-select",
    "#reporting-date-input",
    "#forward-settlement-date-input",
    "#option-settlement-date-input",
)


def _profile_controls_disabled(page) -> bool:
    """True when all eight profile-owned Advanced controls are disabled.

    Issue #161's fake-exit rule: a bond whose product or coupon schedule the
    current pricing path cannot carry must not be offered an input route that
    could never complete the ticket.
    """

    return page.evaluate(
        "(selectors) => selectors.every((sel) => document.querySelector(sel).disabled)",
        list(_PROFILE_CONTROL_SELECTORS),
    )


def _fill_non_profile_inputs(page, **trade_kwargs) -> None:
    """Fill every input still reachable when the eight profile controls are off.

    The counterpart of :func:`_complete_draft` for an unsupported product: it
    proves a trader who fills in everything they *can* still cannot bypass the
    refusal, without pretending the disabled controls are fillable.
    """

    _fill_trade_group(page, **trade_kwargs)
    _fill_market_review(page)
    if _is_actually_hidden(page, "advanced-body"):
        page.click("#advanced-head")
    _set_curve_nodes(page, (("1M", "0.0374"), ("1Y", "0.0374")))


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
@_QUANTLIB_SKIP
def test_bloomberg_raw_descriptions_are_evidence_and_never_enter_the_typed_draft(
    server_url, page
) -> None:
    """Issue #157 fills these fields, but not from these strings.

    The confirmed descriptions are still display-only evidence: they are shown
    beside the control, and they can only *gate* the profile (a bond whose
    strings do not match it is refused outright). What actually lands in the
    control is the approved profile's own value, which says so.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    page.click("#advanced-head")

    assert page.inner_text("#hint-day-count") == "ACT/ACT"
    assert page.inner_text("#hint-bond-type") == "AT MATURITY"
    assert page.inner_text("#details-bloomberg-calc-type") == "STREET CONVENTION"

    # Neither description string became the value, and neither value claims
    # Bloomberg as its source.
    assert page.input_value("#day-count-select") != "ACT/ACT"
    assert page.input_value("#bond-type-select") != "AT MATURITY"
    provenance = _field_provenance(page)
    assert provenance["bond_reference_data_universe.0.day_count"] == "UST_PROFILE_DEFAULT"
    assert provenance["bond_reference_data_universe.0.bond_type"] == "UST_PROFILE_DEFAULT"

    # And no removed typed field enters the standalone request draft.
    reference = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0]"
    )
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

    _fill_non_profile_inputs(page)
    page.wait_for_timeout(300)

    # No UI anywhere offers to type a Bond Master field by hand.
    assert page.query_selector("#coupon-input") is None
    assert page.query_selector("#maturity-date-input") is None
    assert _is_disabled(page, "#price-btn")
    assert "bloomberg-reference" in _unresolved_group_ids(page)
    # Issue #161: with no Bond Master at all the resolver cannot admit the
    # product, so the Advanced controls are disabled and no Go-to is offered
    # -- filling them by hand was never a route to a priced ticket.
    assert _profile_controls_disabled(page)
    assert page.eval_on_selector("#unresolved-goto-btn", "el => el.hidden")


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
    assert "option_settlement_date" not in also
    # The settlement dates are still outstanding at this point: the profile
    # derives them from an expiry the trader has not entered yet, and Shiori
    # does not guess one (Issue #157's bond-reference group, by contrast, no
    # longer appears here at all -- it is filled on load).
    assert "Timing & settlement override" in also


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
@_QUANTLIB_SKIP
def test_gilt_shaped_regular_grid_bond_is_refused_by_the_selected_ust_profile(
    server_url, page
) -> None:
    """A Gilt can still be resolved and displayed, but the browser's explicitly
    selected UST convention profile must refuse it before builder validation.

    Manually completing the ticket cannot turn that profile refusal into a
    priceable run.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page, identifier="GB00BFX0ZL78", response=_gilt_lookup_response()
    )
    _wait_for_ust_profile(page)

    assert page.inner_text("#resolved-bond-name") == "UNITED KINGDOM GILT"
    assert page.inner_text("#resolved-bond-currency") == "GBP"
    profile = _ust_profile(page)
    assert profile["supported"] is False
    assert any("is not USD" in reason for reason in profile["rejection_reasons"])

    _fill_non_profile_inputs(page, strike="95.50")
    page.wait_for_timeout(250)

    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "unknown"
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert _profile_controls_disabled(page)
    status = page.inner_text("#ust-profile-status").lower()
    assert "does not fit the selected ust convention profile" in status
    assert "no advanced edit can make this ticket price" in status



@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_gilt_stub_schedule_cannot_bypass_the_selected_ust_profile(
    server_url, page
) -> None:
    """A non-UST ticket never reaches either pricing route merely because the
    trader manually fills every Advanced field."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="GB00BFX0ZL78",
        response=_gilt_lookup_response(bond_master=_GILT_STUB_BOND_MASTER),
    )
    _wait_for_ust_profile(page)
    _fill_non_profile_inputs(page, strike="95.50")
    page.wait_for_timeout(250)

    priced: list = []
    refreshed: list = []
    page.route("**/api/case", lambda route: priced.append(route))
    page.route("**/api/case/bloomberg", lambda route: refreshed.append(route))
    page.dispatch_event("#price-btn", "click")
    page.dispatch_event("#bloomberg-refresh-btn", "click")
    page.wait_for_timeout(250)

    assert priced == []
    assert refreshed == []
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "unknown"
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
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
@_QUANTLIB_SKIP
def test_callable_ust_is_stopped_by_the_profile_before_builder_validation(
    server_url, page
) -> None:
    """The selected UST profile rejects a callable bond before the typed builder.

    Even a structurally complete-looking form must not schedule validation or
    enable either pricing action after that refusal.
    """

    page.goto(f"{server_url}/")
    validation_calls: list = []
    page.route("**/api/case/validate", lambda route: validation_calls.append(route))
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            bond_master={**_TREASURY_BOND_MASTER, "callable_flag": True}
        ),
    )
    _wait_for_ust_profile(page)
    _fill_non_profile_inputs(page)
    page.wait_for_timeout(300)

    profile = _ust_profile(page)
    assert profile["supported"] is False
    assert any("callable" in reason.lower() for reason in profile["rejection_reasons"])
    assert validation_calls == []
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "unknown"
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert _profile_controls_disabled(page)
    status = page.inner_text("#ust-profile-status").lower()
    assert "not supported on the current pricing path" in status
    assert "callable" in status



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
    """An offset the trader has cleared blocks rather than being filled in.

    Issue #161 pre-fills the offset control on a *new ticket* only. Nothing
    re-fills it afterwards, so clearing it leaves a genuinely empty offset --
    and no time-of-day or timezone convention is guessed to cover for it.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    page.fill("#expiry-datetime-input", "2026-10-20T17:20")
    page.fill("#expiry-offset-input", "")
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
@_QUANTLIB_SKIP
def test_every_advanced_override_is_provenance_stamped(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    # Issue #157: the eight technical fields arrive pre-filled and already
    # stamped -- with their own tier, not a trader-override claim. Nothing the
    # trader is actually responsible for is stamped yet.
    stamped_on_load = {record["path"]: record for record in _override_provenance(page)}
    assert "bond_reference_data_universe.0.day_count" in stamped_on_load
    assert stamped_on_load["bond_reference_data_universe.0.day_count"]["basis"] == (
        "UST_PROFILE_DEFAULT"
    )
    assert "forward_clean_price_input.forward_clean_price_per_100" not in stamped_on_load

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
    # Bloomberg acquisition event the run is anchored to. ``_complete_draft``
    # types all eight technical fields through the real controls, so on this
    # path every one of them is genuinely the trader's.
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
        "#volatility-input",
        "#forward-price-input",
    ):
        assert page.input_value(selector) == "", f"{selector} kept the prior bond's value"
    # The one exception, by design (Issue #161): a second Load starts a new
    # ticket, and a new ticket's offset is the Taipei default again -- not the
    # previous ticket's value, and not blank.
    assert page.input_value("#expiry-offset-input") == "+08:00"
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
        _refresh_route(acquired_at="2026-07-20T11:31:00+08:00"),
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

    page.route("**/api/case/bloomberg", _refresh_route(acquired_at=t2))
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
                _refresh_payload(
                    case=json.loads(route.request.post_data)["case"],
                    acquired_at="2026-07-20T11:31:00+08:00",
                    clean_price=refreshed_clean_price,
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
    page.route(
        "**/api/case/bloomberg",
        _refresh_route(
            acquired_at="2026-07-20T11:31:00+08:00", quote_side="OFFER", sent=sent
        ),
    )
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
def test_an_edit_during_a_pending_refresh_discards_that_refresh(server_url, page) -> None:
    """Codex review round 4: an in-flight refresh describes the case that was
    sent, which is no longer the ticket on screen once the trader edits it.

    Adopting that response would either silently discard the edit or show a
    result computed from inputs the trader has already changed. The edit
    invalidates the request instead, so the ticket on screen and the draft
    behind it never disagree.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)

    pending: list = []
    page.route("**/api/case/bloomberg", lambda route: pending.append(route))

    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    # The trader keeps working while the request is still outstanding.
    page.fill("#forward-price-input", "97.5")
    page.wait_for_timeout(150)

    sent_case = json.loads(pending[0].request.post_data)["case"]
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _refresh_payload(case=sent_case, acquired_at="2026-07-20T11:31:00+08:00")
        ),
    )
    page.wait_for_timeout(400)

    # The superseded response is discarded outright: no result is shown, and
    # the edit survives in both the form and the draft behind it.
    assert page.input_value("#forward-price-input") == "97.5"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_clean_price_input"]["forward_clean_price_per_100"] == 97.5
    assert draft["pricing_timestamp"] == "2026-07-20T11:28:00+08:00"
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#status-text") != "Draft priced"

    # The case that was sent was a genuine deep copy: the mid-flight edit never
    # reached through into it.
    assert sent_case["forward_clean_price_input"]["forward_clean_price_per_100"] == 99.234375


@_PLAYWRIGHT_SKIP
def test_a_quote_side_change_during_a_pending_refresh_discards_that_refresh(
    server_url, page
) -> None:
    """Codex review round 5: Quote Side is an edit like any other.

    It is not a draft field -- Load and Refresh read the toggle when they build
    their request -- so it was wired straight to ``setToggle`` and bypassed the
    invalidation every other control goes through. Switching side while a
    refresh was outstanding therefore changed no generation, and the old-side
    response was adopted while the toggle displayed the newly selected side,
    leaving the draft's spot and forward sides disagreeing with what the trader
    could see.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    pending: list = []
    page.route("**/api/case/bloomberg", lambda route: pending.append(route))

    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    sent_case = json.loads(pending[0].request.post_data)["case"]
    assert sent_case["forward_clean_price_input"]["quote_side"] == "MID"

    # The trader switches side while that MID request is still outstanding.
    _select_quote_side(page, "BID")
    page.wait_for_timeout(150)

    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _refresh_payload(case=sent_case, acquired_at="2026-07-20T11:31:00+08:00")
        ),
    )
    page.wait_for_timeout(400)

    # The superseded MID response is discarded outright, so no priced result
    # and no MID-sided draft is shown under a toggle that now reads BID.
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#status-text") != "Draft priced"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["pricing_timestamp"] == "2026-07-20T11:28:00+08:00"

    selected = page.get_attribute("#bond-quote-side-toggle .opt.on", "data-value")
    assert selected == "BID"


@_PLAYWRIGHT_SKIP
def test_a_refresh_adopts_the_case_the_server_actually_priced(server_url, page) -> None:
    """The browser assembles no priced case of its own: it adopts the envelope
    the route returns, which is the one the pricing workflow substituted into
    and priced."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    _wait_for_price_enabled(page)

    # A marker the browser could not have produced itself: if the adopted draft
    # carries it, the draft came from the response rather than being rebuilt.
    def route_refresh(route):
        body = json.loads(route.request.post_data)
        payload = _refresh_payload(
            case=body["case"], acquired_at="2026-07-20T11:31:00+08:00"
        )
        payload["case"]["snapshot_id"] = "SERVER-PRICED-CASE-MARKER"
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)
        )

    page.route("**/api/case/bloomberg", route_refresh)
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["snapshot_id"] == "SERVER-PRICED-CASE-MARKER"
    assert draft["pricing_timestamp"] == "2026-07-20T11:31:00+08:00"


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

    page.route("**/api/case/bloomberg", _refresh_route(acquired_at=t2))
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
                body=json.dumps(
                    _refresh_payload(
                        case=json.loads(route.request.post_data)["case"],
                        acquired_at=retried_acquisition,
                    )
                ),
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
def test_a_quote_side_change_during_a_pending_lookup_discards_that_lookup(
    server_url, page
) -> None:
    """Codex review round 6: routing Quote Side through ``applyManualInputsToDraft``
    covered Price and Refresh but not Load.

    While the first lookup is outstanding there is no draft yet, so that
    function returns at its own ``!currentDraft`` guard before reaching any
    invalidation -- and it never invalidated bond lookups in any case. The
    old-side response was therefore accepted and used to build an old-sided
    draft while the toggle showed the newly selected side, and an ordinary
    Price could then price that draft on a side the trader had not chosen.
    """

    page.goto(f"{server_url}/")

    pending: list = []
    page.route("**/api/bloomberg/bond", lambda route: pending.append(route))
    page.fill("#bond-identifier-input", "US91282CLJ89")
    _select_quote_side(page, "MID")
    page.click("#load-bloomberg-bond-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    assert json.loads(pending[0].request.post_data)["quote_side"] == "MID"

    # The trader switches side while that MID lookup is still outstanding.
    _select_quote_side(page, "BID")
    page.wait_for_timeout(150)

    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_treasury_lookup_response(quote_side="MID")),
    )
    page.wait_for_timeout(400)

    # The superseded MID lookup is discarded outright: no MID-sided draft is
    # built behind a toggle that now reads BID, and nothing is left on screen
    # claiming an instrument was resolved.
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert _resolved_bond_panel_hidden(page)
    assert page.get_attribute("#bond-quote-side-toggle .opt.on", "data-value") == "BID"

    # The trader is told why nothing loaded, rather than left with a dead
    # button and no explanation.
    assert not page.eval_on_selector("#pricing-error-banner", "el => el.hidden")
    assert "Quote Side changed" in page.inner_text("#pricing-error-banner")

    # Pressing Load again on the new side resolves normally.
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="BID"), side="BID")
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_quote"]["quote_side"] == "BID"
    assert draft["forward_clean_price_input"]["quote_side"] == "BID"


@_PLAYWRIGHT_SKIP
def test_price_is_blocked_while_quote_side_differs_from_the_sourced_side(
    server_url, page
) -> None:
    """Codex review round 7: the case no request invalidation can reach.

    With a completed ticket and nothing in flight, selecting a different side
    leaves the draft's quote on the side it was actually sourced on. A Price
    started *after* that change is a brand-new request, so invalidating pending
    ones cannot help: Shiori would price MID while the trader reads BID.

    Gated rather than latched -- selecting the original side back restores the
    run untouched, with no Bloomberg call and nothing discarded.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    _select_quote_side(page, "BID")
    _wait_until(
        lambda: page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")
    )

    # The draft is untouched -- Shiori does not relabel a quote it did not
    # source on that side -- but it can no longer be priced.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_quote"]["quote_side"] == "MID"
    assert draft["forward_clean_price_input"]["quote_side"] == "MID"
    assert "instrument" in page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")
    assert "Quote Side does not match" in page.inner_text("#unresolved-title")

    # Refresh stays available once validation has re-run, so the trader can
    # re-source on the selected side. (Price does not come back with it.)
    _wait_until(
        lambda: page.evaluate("() => window.__shioriTestBuilderValidationState()") == "ready"
    )
    assert not page.eval_on_selector(
        "#bloomberg-refresh-btn", "el => el.classList.contains('is-disabled')"
    )
    assert page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")

    # Nothing was destroyed: selecting the original side back restores the run.
    _select_quote_side(page, "MID")
    _wait_for_price_enabled(page)
    restored = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert restored["bond_quote"]["quote_side"] == "MID"


@_PLAYWRIGHT_SKIP
def test_a_successful_refresh_on_the_selected_side_restores_pricing_with_that_side(
    server_url, page
) -> None:
    """Owner decision on §10 item 9, case 2: block Price until Refresh, never
    snap the toggle back.

    A refresh after a side change must source on the side the trader selected,
    adopt the authoritative server-returned case, clear the mismatch, and
    re-enable Price -- with BID on the request, on the adopted draft, and on the
    toggle throughout. The trader's selection is never mutated to restore
    consistency; the *quote* is re-sourced to match it.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    _select_quote_side(page, "BID")
    _wait_until(
        lambda: page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")
    )

    sent: list = []
    page.route(
        "**/api/case/bloomberg",
        _refresh_route(acquired_at="2026-07-20T11:31:00+08:00", quote_side="BID", sent=sent),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    # The request was sourced on the selected side, with the contract's
    # forward-side-equals-spot-side invariant already satisfied.
    assert sent[0]["quote_side"] == "BID"
    assert sent[0]["case"]["forward_clean_price_input"]["quote_side"] == "BID"

    # The adopted draft is the server's, quoted on the selected side.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_quote"]["quote_side"] == "BID"
    assert draft["forward_clean_price_input"]["quote_side"] == "BID"
    assert draft["pricing_timestamp"] == "2026-07-20T11:31:00+08:00"

    # Mismatch cleared, Price back, and the toggle still reads what the trader
    # chose -- it was never snapped back to MID at any point.
    assert "instrument" not in page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")
    assert not page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")
    assert page.get_attribute("#bond-quote-side-toggle .opt.on", "data-value") == "BID"


@_PLAYWRIGHT_SKIP
def test_a_failed_refresh_after_a_side_change_preserves_the_selection_and_never_falls_back(
    server_url, page
) -> None:
    """Owner decision on §10 item 9, case 3.

    A refresh that fails on the selected side must keep that selection and the
    trader-authored ticket, keep Price disabled with Refresh available, and
    never fall back to the previously sourced side's quote -- neither by
    pricing against it nor by showing it as though it were current.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    _select_quote_side(page, "BID")
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.evaluate("() => window.__shioriTestSourcedQuoteInvalidated()"))

    # The selection survives the failure -- it is not reverted to the side that
    # happens to still back the draft.
    assert page.get_attribute("#bond-quote-side-toggle .opt.on", "data-value") == "BID"

    # The trader-authored ticket survives intact.
    assert page.input_value("#strike-price-input") == "99.32"
    assert page.input_value("#notional-input") == "1000000"
    assert page.input_value("#forward-price-input") == "99.234375"
    assert page.input_value("#volatility-input") == "0.03395"

    # No fallback to the MID quote: nothing priced, and the disowned quote is
    # not on screen as though it were current.
    assert page.inner_text("#price-total") == "—"
    assert page.inner_text("#status-text") != "Draft priced"
    assert _resolved_bond_panel_hidden(page)

    # Price stays off; Refresh stays available to retry the very call that failed.
    _wait_until(
        lambda: page.evaluate("() => window.__shioriTestBuilderValidationState()") == "ready"
    )
    assert page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")
    assert not page.eval_on_selector(
        "#bloomberg-refresh-btn", "el => el.classList.contains('is-disabled')"
    )


@_PLAYWRIGHT_SKIP
def test_reselecting_the_sourced_side_does_not_price_while_the_quote_is_invalidated(
    server_url, page
) -> None:
    """Owner decision on §10 item 9, case 4, negative half.

    Reselecting the originally sourced side clears the *mismatch*, but that must
    only restore pricing when that side still has valid sourced quote state. If
    a refresh has since failed, the quote is disowned and Price stays off --
    otherwise clicking back to MID would silently price against a quote Shiori
    has already said it will not stand behind.

    The positive half -- reselecting with the sourced state still live restores
    the run untouched -- is pinned by
    ``test_price_is_blocked_while_quote_side_differs_from_the_sourced_side``.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    _select_quote_side(page, "BID")
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.evaluate("() => window.__shioriTestSourcedQuoteInvalidated()"))

    # Back to the side the draft's quote was actually sourced on.
    _select_quote_side(page, "MID")
    _wait_until(
        lambda: page.evaluate("() => window.__shioriTestBuilderValidationState()") == "ready"
    )

    # The mismatch is gone, but the sourced quote is still disowned, so the
    # instrument group stays open and Price stays off.
    assert page.evaluate("() => window.__shioriTestSourcedQuoteInvalidated()") is True
    assert "instrument" in page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")
    assert "invalidated" in page.inner_text("#unresolved-title")
    assert page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")


@_PLAYWRIGHT_SKIP
def test_a_pending_lookup_blocks_price_and_refresh_on_the_previous_bond(
    server_url, page
) -> None:
    """Merge-gate review: Price and Refresh stayed live during a pending lookup.

    With a completed ticket for bond A, typing bond B and pressing Load left
    both controls enabled from A. Either one begins by calling
    ``invalidatePendingBondLookupRequest()``, so a click silently cancelled the
    trader's Load and then priced or refreshed A while the security input named
    B -- the Load simply appeared to do nothing.

    Both are now gated on the lookup being settled, at the button and at the
    action, and both come back once it settles.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    # A lookup for a different bond is now outstanding.
    pending: list = []
    page.route("**/api/bloomberg/bond", lambda route: pending.append(route))
    page.fill("#bond-identifier-input", "GB00BFX0ZL78")
    page.click("#load-bloomberg-bond-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    assert page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")
    assert page.eval_on_selector(
        "#bloomberg-refresh-btn", "el => el.classList.contains('is-disabled')"
    )

    # Even driven directly, neither action may cancel the Load and act on the
    # previous bond.
    priced: list = []
    refreshed: list = []
    page.route("**/api/case", lambda route: priced.append(route))
    page.route("**/api/case/bloomberg", lambda route: refreshed.append(route))
    # dispatch_event, not click: `.is-disabled` sets pointer-events: none, so an
    # ordinary click cannot reach the handler at all. Dispatching directly
    # bypasses the CSS and proves the guard inside the action, not just the
    # styling that usually stops the trader reaching it.
    page.dispatch_event("#price-btn", "click")
    page.dispatch_event("#bloomberg-refresh-btn", "click")
    page.wait_for_timeout(300)

    assert priced == []
    assert refreshed == []
    assert len(pending) == 1  # the Load is still outstanding, not cancelled

    # Letting the Load land restores a normal, actionable run for the new bond.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_gilt_lookup_response(quote_side="MID")),
    )
    _wait_until(lambda: page.inner_text("#resolved-bond-isin") == "GB00BFX0ZL78")
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()")["bond_option"][
        "underlying_isin"
    ] == "GB00BFX0ZL78"


@_PLAYWRIGHT_SKIP
def test_a_pending_refresh_blocks_price_on_the_previous_quote(server_url, page) -> None:
    """Second merge-gate finding: Price stayed live during a pending refresh.

    ``priceCurrentDraft`` opens by calling ``invalidatePendingBloombergRequest()``,
    so clicking Price while a refresh was outstanding aborted that refresh and
    priced the draft with the quote the trader was in the middle of replacing --
    producing a priced and exportable run on stale market data *because* they
    had asked for fresh data.

    Price is the lowest-authority action and now waits for the refresh to
    settle. The reverse direction stays allowed: Refresh superseding a pending
    Price still yields a priced run, on fresher data.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    pending: list = []
    page.route("**/api/case/bloomberg", lambda route: pending.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    assert page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")

    # Driven past the CSS, Price must still refuse: no /api/case request, and
    # the refresh the trader asked for is still outstanding rather than aborted.
    priced: list = []
    page.route("**/api/case", lambda route: priced.append(route))
    page.dispatch_event("#price-btn", "click")
    page.wait_for_timeout(300)

    assert priced == []
    assert page.inner_text("#price-total") == "—"

    # Letting the refresh land prices the run on the refreshed quote, not the
    # one Price would have used.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _refresh_payload(
                case=json.loads(pending[0].request.post_data)["case"],
                acquired_at="2026-07-20T11:31:00+08:00",
                clean_price=101.5,
            )
        ),
    )
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_quote"]["clean_price_per_100"] == 101.5
    assert draft["pricing_timestamp"] == "2026-07-20T11:31:00+08:00"
    _wait_for_price_enabled(page)


@_PLAYWRIGHT_SKIP
def test_an_identifier_edit_during_a_pending_lookup_discards_that_response(
    server_url, page
) -> None:
    """Codex review round 7: only the Load button was wired to the lookup, so
    editing the identifier while one was outstanding invalidated nothing.

    The stale response was adopted and built the resolved panel and draft for
    identifier A while the input read B -- and on a second lookup it also ran
    ``resetRunState()``, discarding a completed ticket on behalf of a bond the
    trader was no longer naming. Checked at adoption rather than with another
    per-control listener.
    """

    page.goto(f"{server_url}/")

    pending: list = []
    page.route("**/api/bloomberg/bond", lambda route: pending.append(route))
    page.fill("#bond-identifier-input", "US91282CLJ89")
    _select_quote_side(page, "MID")
    page.click("#load-bloomberg-bond-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    # The trader retypes the identifier while that lookup is outstanding.
    page.fill("#bond-identifier-input", "GB00BFX0ZL78")
    page.wait_for_timeout(150)

    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(_treasury_lookup_response(quote_side="MID")),
    )
    page.wait_for_timeout(400)

    # The superseded answer is discarded: no draft or panel for the Treasury the
    # input no longer names.
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert _resolved_bond_panel_hidden(page)
    assert page.input_value("#bond-identifier-input") == "GB00BFX0ZL78"
    assert "lookup inputs changed" in page.inner_text("#pricing-error-banner")


@_PLAYWRIGHT_SKIP
def test_a_superseded_failed_lookup_never_discards_the_prior_ticket(server_url, page) -> None:
    """The sharper half of the same finding: a *failed* answer for a superseded
    identifier must not run the whole-run reset either.

    ``renderLookupFailure`` resets everything by design, so adopting a failure
    that belongs to an identifier the trader has moved away from would destroy a
    completed ticket on that bond's behalf.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    pending: list = []
    page.route("**/api/bloomberg/bond", lambda route: pending.append(route))
    page.fill("#bond-identifier-input", "GB00BFX0ZL78")
    page.click("#load-bloomberg-bond-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    # The trader changes their mind about which bond to look up.
    page.fill("#bond-identifier-input", "US91282CLJ89")
    page.wait_for_timeout(150)

    pending[0].fulfill(
        status=502,
        content_type="application/json",
        body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
    )
    page.wait_for_timeout(400)

    # The completed Treasury ticket is still there, unharmed.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft is not None
    assert draft["bond_option"]["underlying_isin"] == "US91282CLJ89"
    assert draft["bond_quote"]["quote_side"] == "MID"
    assert "lookup inputs changed" in page.inner_text("#pricing-error-banner")


@_PLAYWRIGHT_SKIP
def test_a_superseded_non_json_failure_never_discards_the_prior_ticket(
    server_url, page
) -> None:
    """Codex review round 8: the round-7 guard sat after ``await response.json()``.

    A non-JSON error body (or a rejected fetch) transfers control to the
    ``catch`` before any adoption check runs, and an identifier edit advances no
    generation -- so the superseded answer reached ``renderLookupFailure()`` and
    its ``resetRunState()`` deleted a completed ticket belonging to the bond the
    trader was still naming.

    The round-7 regression missed this because it fulfilled a 502 with a *valid
    JSON* body, which takes the ``!response.ok`` branch after the guard. This one
    returns HTML, so it goes through the ``catch``.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    pending: list = []
    page.route("**/api/bloomberg/bond", lambda route: pending.append(route))
    page.fill("#bond-identifier-input", "GB00BFX0ZL78")
    page.click("#load-bloomberg-bond-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    page.fill("#bond-identifier-input", "US91282CLJ89")
    page.wait_for_timeout(150)

    # Not JSON: `await response.json()` throws, landing in the catch path.
    pending[0].fulfill(
        status=502,
        content_type="text/html",
        body="<html><body>502 Bad Gateway</body></html>",
    )
    page.wait_for_timeout(400)

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft is not None
    assert draft["bond_option"]["underlying_isin"] == "US91282CLJ89"
    assert "lookup inputs changed" in page.inner_text("#pricing-error-banner")


@_PLAYWRIGHT_SKIP
def test_a_genuine_non_json_failure_still_reports_and_resets(server_url, page) -> None:
    """The other half of the same guard: when the inputs have *not* moved on, a
    real transport failure must still be reported and still reset the run.

    Guarding the catch path must not swallow the failure the trader needs to see
    for the bond they are still naming.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    page.route(
        "**/api/bloomberg/bond",
        lambda route: route.fulfill(
            status=502, content_type="text/html", body="<html>502</html>"
        ),
    )
    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg lookup failed")

    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert _resolved_bond_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_a_quote_side_mismatch_never_claims_a_bloomberg_request_failed(
    server_url, page
) -> None:
    """Codex review round 8: the mismatch panel reused the invalidated-state
    evidence, which asserts "The Bloomberg request for this run failed".

    Nothing was requested and nothing failed -- the trader changed a toggle. The
    evidence line must not put a Bloomberg failure on screen that never happened.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    _select_quote_side(page, "BID")
    _wait_until(lambda: "Quote Side does not match" in page.inner_text("#unresolved-title"))

    evidence = page.inner_text("#unresolved-evidence")
    assert "only valid for the side it was sourced on" in evidence
    assert "request for this run failed" not in evidence
    # Round 9: and no claim about request history in either direction -- see
    # test_a_quote_side_mismatch_claims_nothing_about_request_history.
    assert "request" not in evidence.replace("requests", "")

    # And the converse: a genuine refresh failure still says a request failed,
    # so distinguishing the two did not weaken the real failure explanation.
    _select_quote_side(page, "MID")
    _wait_for_price_enabled(page)
    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(
        lambda: "The Bloomberg request for this run failed"
        in page.inner_text("#unresolved-evidence")
    )


@_PLAYWRIGHT_SKIP
def test_a_quote_side_mismatch_claims_nothing_about_request_history(
    server_url, page
) -> None:
    """Codex review round 9: the round-8 evidence swapped one false history for
    another.

    It asserted "No Bloomberg request was made or failed". That holds when the
    trader simply clicks the toggle on a settled ticket, but changing the side
    while a refresh is outstanding *aborts a request that had already been
    sent* -- so the sentence is false exactly when a request did happen.

    The panel must therefore claim nothing about request history at all. This
    drives the aborting case specifically, which is the one the round-8 wording
    got wrong.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    # Hold a refresh open so the side change aborts a genuinely sent request.
    pending: list = []
    page.route("**/api/case/bloomberg", lambda route: pending.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    _select_quote_side(page, "BID")
    _wait_until(lambda: "Quote Side does not match" in page.inner_text("#unresolved-title"))

    evidence = page.inner_text("#unresolved-evidence")
    assert "only valid for the side it was sourced on" in evidence
    # Neither the invalidated-state failure claim nor round 8's denial: a
    # request *was* sent here, so both would be untrue.
    assert "request for this run failed" not in evidence
    assert "No Bloomberg request was made" not in evidence


@_PLAYWRIGHT_SKIP
def test_the_mismatch_next_step_claims_nothing_about_discarded_work(
    server_url, page
) -> None:
    """Codex review round 10: round 9 fixed the evidence line and left the same
    false history claim in the ``next`` field beside it.

    "Nothing has been discarded either way" is true when the toggle is clicked
    on a settled ticket, and false when the same click aborts a refresh that was
    already in flight. The panel now states what is *retained*, which holds on
    both paths.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(quote_side="MID"), side="MID")
    _complete_draft(page)
    _wait_for_price_enabled(page)

    pending: list = []
    page.route("**/api/case/bloomberg", lambda route: pending.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    _select_quote_side(page, "BID")
    _wait_until(lambda: "Quote Side does not match" in page.inner_text("#unresolved-title"))

    next_step = page.inner_text("#unresolved-next")
    assert "Nothing has been discarded" not in next_step
    assert "inputs, overrides and curve nodes are untouched" in next_step


@_PLAYWRIGHT_SKIP
def test_the_no_bond_panel_never_denies_a_lookup_that_failed(server_url, page) -> None:
    """Codex review round 10: the no-bond branch said "No Bloomberg request has
    been made yet in this run".

    ``renderLookupFailure()`` resets the draft and re-syncs gating, so this same
    branch renders after a genuine failed lookup -- denying a request on the very
    screen whose banner reports that request failing.
    """

    page.goto(f"{server_url}/")
    page.route(
        "**/api/bloomberg/bond",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.fill("#bond-identifier-input", "US91282CLJ89")
    _select_quote_side(page, "MID")
    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg lookup failed")

    evidence = page.inner_text("#unresolved-evidence")
    assert "No Bloomberg request has been made" not in evidence
    assert "until one has resolved successfully" in evidence
    # The failure itself is still reported, in the place that owns it.
    assert "Bloomberg DAPI session failed to start" in page.inner_text("#pricing-error-banner")


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


# --- Single run-request lifecycle (owner decision) ----------------------------
#
# One invariant, replacing the earlier supersession ordering: at most one
# run-state-changing request -- Price or Bloomberg Refresh -- may be in flight
# at a time. Neither may cancel the other, so no precedence rule is needed for
# the case where the superseding request fails.


def _both_actions_disabled(page) -> bool:
    return page.eval_on_selector(
        "#price-btn", "el => el.classList.contains('is-disabled')"
    ) and page.eval_on_selector(
        "#bloomberg-refresh-btn", "el => el.classList.contains('is-disabled')"
    )


def _ready_ticket(page, server_url, *, quote_side: str = "MID") -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page, response=_treasury_lookup_response(quote_side=quote_side), side=quote_side
    )
    _complete_draft(page)
    _wait_for_price_enabled(page)


@_PLAYWRIGHT_SKIP
def test_price_in_flight_disables_refresh_and_then_prices_normally(
    server_url, page
) -> None:
    _ready_ticket(page, server_url)

    pending: list = []
    page.route("**/api/case", lambda route: pending.append(route))
    page.click("#price-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    assert _both_actions_disabled(page)

    # Refresh may not abort or supersede the outstanding Price.
    refreshed: list = []
    page.route("**/api/case/bloomberg", lambda route: refreshed.append(route))
    page.dispatch_event("#bloomberg-refresh-btn", "click")
    page.wait_for_timeout(300)
    assert refreshed == []

    sent_case = json.loads(pending[0].request.post_data)
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "case": sent_case,
                "context": {},
                "display": _refresh_display(acquired_at="2026-07-20T11:28:00+08:00"),
            }
        ),
    )
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    # Availability is recomputed from state once the request settles.
    _wait_for_price_enabled(page)


@_PLAYWRIGHT_SKIP
def test_an_edit_during_a_pending_price_stops_that_price_committing(
    server_url, page
) -> None:
    _ready_ticket(page, server_url)

    pending: list = []
    page.route("**/api/case", lambda route: pending.append(route))
    page.click("#price-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    page.fill("#forward-price-input", "97.5")
    page.wait_for_timeout(150)

    sent_case = json.loads(pending[0].request.post_data)
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "case": sent_case,
                "context": {},
                "display": _refresh_display(acquired_at="2026-07-20T11:28:00+08:00"),
            }
        ),
    )
    page.wait_for_timeout(400)

    # The stale answer cannot overwrite the edited draft, and no result is shown.
    assert page.input_value("#forward-price-input") == "97.5"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_clean_price_input"]["forward_clean_price_per_100"] == 97.5
    assert page.inner_text("#price-total") == "—"
    # The single in-flight state was released cleanly by the edit.
    _wait_for_price_enabled(page)


@_PLAYWRIGHT_SKIP
def test_refresh_in_flight_disables_price_and_then_refreshes_normally(
    server_url, page
) -> None:
    _ready_ticket(page, server_url)

    pending: list = []
    page.route("**/api/case/bloomberg", lambda route: pending.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    assert _both_actions_disabled(page)

    priced: list = []
    page.route("**/api/case", lambda route: priced.append(route))
    page.dispatch_event("#price-btn", "click")
    page.wait_for_timeout(300)
    assert priced == []

    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _refresh_payload(
                case=json.loads(pending[0].request.post_data)["case"],
                acquired_at="2026-07-20T11:31:00+08:00",
                clean_price=101.5,
            )
        ),
    )
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_quote"]["clean_price_per_100"] == 101.5
    _wait_for_price_enabled(page)


@_PLAYWRIGHT_SKIP
def test_a_failed_refresh_releases_the_busy_state_and_allows_retry(
    server_url, page
) -> None:
    _ready_ticket(page, server_url)

    attempts: list = []

    def route_refresh(route):
        attempts.append(route)
        if len(attempts) == 1:
            route.fulfill(
                status=502,
                content_type="application/json",
                body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
            )
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _refresh_payload(
                        case=json.loads(route.request.post_data)["case"],
                        acquired_at="2026-07-20T11:31:00+08:00",
                    )
                ),
            )

    page.route("**/api/case/bloomberg", route_refresh)
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.evaluate("() => window.__shioriTestSourcedQuoteInvalidated()"))

    # Ticket preserved; Price stays off because the sourced quote is disowned;
    # Refresh comes back for a retry, so the busy state was released.
    assert page.input_value("#strike-price-input") == "99.32"
    assert page.eval_on_selector("#price-btn", "el => el.classList.contains('is-disabled')")
    _wait_for_refresh_enabled(page)

    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert len(attempts) == 2
    _wait_for_price_enabled(page)


@_PLAYWRIGHT_SKIP
def test_a_refresh_after_a_completed_price_leaves_no_stranded_state(
    server_url, page
) -> None:
    """The sequence that produced the stranded ticket under the old ordering,
    now run one request at a time: Price completes, then Refresh fails."""

    _ready_ticket(page, server_url)

    page.route(
        "**/api/case",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "case": json.loads(route.request.post_data),
                    "context": {},
                    "display": _refresh_display(acquired_at="2026-07-20T11:28:00+08:00"),
                }
            ),
        ),
    )
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    page.route(
        "**/api/case/bloomberg",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        ),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.evaluate("() => window.__shioriTestSourcedQuoteInvalidated()"))

    # Not stranded: the ticket survives and Refresh is available to retry.
    assert page.input_value("#strike-price-input") == "99.32"
    assert page.input_value("#notional-input") == "1000000"
    _wait_for_refresh_enabled(page)


@_PLAYWRIGHT_SKIP
def test_a_price_after_a_completed_refresh_prices_the_refreshed_case(
    server_url, page
) -> None:
    _ready_ticket(page, server_url)

    page.route(
        "**/api/case/bloomberg",
        _refresh_route(acquired_at="2026-07-20T11:31:00+08:00", clean_price=101.5),
    )
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    _wait_for_price_enabled(page)

    sent: list = []

    def route_price(route):
        sent.append(json.loads(route.request.post_data))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "case": sent[-1],
                    "context": {},
                    "display": _refresh_display(acquired_at="2026-07-20T11:31:00+08:00"),
                }
            ),
        )

    page.route("**/api/case", route_price)
    page.click("#price-btn")
    _wait_until(lambda: len(sent) == 1)

    # The authoritative refreshed case is what gets priced.
    assert sent[0]["bond_quote"]["clean_price_per_100"] == 101.5
    assert sent[0]["pricing_timestamp"] == "2026-07-20T11:31:00+08:00"


@_PLAYWRIGHT_SKIP
def test_clear_during_a_pending_price_leaks_no_stale_result(server_url, page) -> None:
    _ready_ticket(page, server_url)

    pending: list = []
    page.route("**/api/case", lambda route: pending.append(route))
    page.click("#price-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    page.click("#clear-btn")
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "case": json.loads(pending[0].request.post_data),
                "context": {},
                "display": _refresh_display(acquired_at="2026-07-20T11:28:00+08:00"),
            }
        ),
    )
    page.wait_for_timeout(400)

    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert page.inner_text("#price-total") == "—"
    assert _resolved_bond_panel_hidden(page)


@_PLAYWRIGHT_SKIP
def test_a_second_lookup_during_a_pending_refresh_leaks_no_stale_result(
    server_url, page
) -> None:
    _ready_ticket(page, server_url)

    pending: list = []
    page.route("**/api/case/bloomberg", lambda route: pending.append(route))
    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    # A new instrument lookup is a cancel-and-reset boundary.
    _load_bloomberg_bond(page, response=_gilt_lookup_response(quote_side="MID"), side="MID")

    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _refresh_payload(
                case=json.loads(pending[0].request.post_data)["case"],
                acquired_at="2026-07-20T11:31:00+08:00",
            )
        ),
    )
    page.wait_for_timeout(400)

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_option"]["underlying_isin"] == "GB00BFX0ZL78"
    assert page.inner_text("#price-total") == "—"


@_PLAYWRIGHT_SKIP
def test_price_and_refresh_posts_are_never_simultaneously_in_flight(
    server_url, page
) -> None:
    """The invariant itself, observed at the network boundary.

    Deliberately *attempts* the overlap from both directions -- holding one
    request open and driving the other past its disabled styling -- so the test
    fails if the invariant is removed, rather than merely never exercising it.
    """

    _ready_ticket(page, server_url)

    inflight: list = []
    peak = {"value": 0}

    def hold(route, kind):
        inflight.append(kind)
        peak["value"] = max(peak["value"], len(inflight))

    held: list = []
    page.route("**/api/case/bloomberg", lambda r: (hold(r, "refresh"), held.append(r)))
    page.route("**/api/case", lambda r: (hold(r, "price"), held.append(r)))

    # Refresh outstanding -> try to start a Price.
    page.click("#bloomberg-refresh-btn")
    _wait_until_pumping(page, lambda: len(held) == 1)
    page.dispatch_event("#price-btn", "click")
    page.wait_for_timeout(300)
    assert peak["value"] == 1, f"a Price overlapped a Refresh (peak={peak['value']})"

    held[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            _refresh_payload(
                case=json.loads(held[0].request.post_data)["case"],
                acquired_at="2026-07-20T11:31:00+08:00",
            )
        ),
    )
    inflight.remove("refresh")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    _wait_for_price_enabled(page)

    # Price outstanding -> try to start a Refresh.
    page.click("#price-btn")
    _wait_until_pumping(page, lambda: len(held) == 2)
    page.dispatch_event("#bloomberg-refresh-btn", "click")
    page.wait_for_timeout(300)
    assert peak["value"] == 1, f"a Refresh overlapped a Price (peak={peak['value']})"

    held[1].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "case": json.loads(held[1].request.post_data),
                "context": {},
                "display": _refresh_display(acquired_at="2026-07-20T11:31:00+08:00"),
            }
        ),
    )
    inflight.remove("price")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert peak["value"] == 1


# --- Issue #157: the eight Advanced technical fields are pre-filled ----------
#
# These prove the browser half of the UST profile: that a supported UST is
# auto-filled after Bloomberg Load, that every value shows which of the four
# provenance tiers it came from, that a trader override survives every
# ordinary re-render and re-derivation, that an expiry change recomputes only
# what the trader has not taken over, and that no profile state, override or
# late response survives Clear, a second load, a refresh failure or a
# superseded request.
#
# The profile answers come from the real ``POST /api/ust/profile`` route and
# the reviewed resolver behind it -- only the Bloomberg lookup is mocked --
# so a passing value here is a genuine deterministic derivation, not a fixture.

_PROFILE_PATHS = (
    "bond_reference_data_universe.0.day_count",
    "bond_reference_data_universe.0.bond_type",
    "bond_reference_data_universe.0.ex_dividend_days",
    "bond_reference_data_universe.0.last_coupon_date",
    "bond_reference_data_universe.0.status",
    "reporting_date",
    "forward_settlement_date",
    "option_settlement_date",
)


def _field_provenance(page) -> dict:
    return page.evaluate("() => window.__shioriTestFieldProvenance()")


def _trader_overridden_paths(page) -> list[str]:
    return page.evaluate("() => window.__shioriTestTraderOverriddenPaths()")


def _ust_profile(page):
    return page.evaluate("() => window.__shioriTestUstProfile()")


def _wait_for_ust_profile(page) -> None:
    page.wait_for_function("() => window.__shioriTestUstProfile() !== null")


def _set_expiry(page, *, local: str = "2026-10-20T17:20", offset: str = "+08:00") -> None:
    page.fill("#expiry-datetime-input", local)
    page.fill("#expiry-offset-input", offset)


def _draft_reference(page) -> dict:
    return page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0]"
    )


def _fill_curve_nodes_only(page, nodes=(("1M", "0.0374"), ("1Y", "0.0374"))) -> None:
    """Open Advanced only for the Option Discount Curve, which #157 leaves
    exactly where it was, and close it again."""

    if _is_actually_hidden(page, "advanced-body"):
        page.click("#advanced-head")
    _set_curve_nodes(page, nodes)
    page.click("#advanced-head")


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_supported_ust_load_auto_fills_every_advanced_technical_field(server_url, page) -> None:
    """Acceptance criterion 1: after one Bloomberg Load and the expiry the
    trader was going to enter anyway, all eight fields carry a value."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_function(
        "() => window.__shioriTestGetCurrentDraft().option_settlement_date !== null"
    )

    reference = _draft_reference(page)
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    # The US Treasury bond-basis convention, not ISDA's Actual/Actual (Issue
    # #157 correction).
    assert reference["day_count"] == "ACT_ACT_BOND"
    assert reference["bond_type"] == "FIXED_COUPON_BULLET"
    assert reference["ex_dividend_days"] == 0
    assert reference["last_coupon_date"] == "2030-07-31"
    assert reference["status"] == "ACTIVE"
    assert draft["reporting_date"] == "2026-07-20"
    assert draft["forward_settlement_date"] == "2026-10-21"
    assert draft["option_settlement_date"] == "2026-10-21"

    # The controls themselves hold the same values, so the trader edits exactly
    # what the draft carries.
    assert page.input_value("#day-count-select") == "ACT_ACT_BOND"
    assert page.input_value("#bond-type-select") == "FIXED_COUPON_BULLET"
    assert page.input_value("#ex-dividend-days-input") == "0"
    assert page.input_value("#last-coupon-date-input") == "2030-07-31"
    assert page.input_value("#bond-status-select") == "ACTIVE"
    assert page.input_value("#reporting-date-input") == "2026-07-20"
    assert page.input_value("#forward-settlement-date-input") == "2026-10-21"
    assert page.input_value("#option-settlement-date-input") == "2026-10-21"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_every_auto_filled_value_shows_its_provenance(server_url, page) -> None:
    """Acceptance criterion 3: no unlabelled silent default anywhere."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_function(
        "() => window.__shioriTestGetCurrentDraft().option_settlement_date !== null"
    )

    provenance = _field_provenance(page)
    assert set(provenance) == set(_PROFILE_PATHS)
    assert provenance["bond_reference_data_universe.0.day_count"] == "UST_PROFILE_DEFAULT"
    assert provenance["bond_reference_data_universe.0.bond_type"] == "UST_PROFILE_DEFAULT"
    assert provenance["bond_reference_data_universe.0.ex_dividend_days"] == "UST_PROFILE_DEFAULT"
    assert provenance["bond_reference_data_universe.0.status"] == "UST_PROFILE_DEFAULT"
    assert provenance["bond_reference_data_universe.0.last_coupon_date"] == "SHIORI_DERIVED"
    assert provenance["reporting_date"] == "SHIORI_DERIVED"
    assert provenance["forward_settlement_date"] == "SHIORI_DERIVED"
    assert provenance["option_settlement_date"] == "SHIORI_DERIVED"

    # And it is visible, not merely in memory.
    page.click("#advanced-head")
    assert "UST_PROFILE_DEFAULT" in page.inner_text("#prov-day-count")
    assert "SHIORI_DERIVED" in page.inner_text("#prov-option-settlement-date")
    assert "Convention Profile: UST" in page.inner_text("#ust-profile-status")
    assert "shape fits it" in page.inner_text("#ust-profile-status")
    # The exported run carries the same tiers rather than claiming everything
    # was a trader override.
    stamped = {record["path"]: record for record in _override_provenance(page)}
    assert stamped["reporting_date"]["basis"] == "SHIORI_DERIVED"
    assert stamped["reporting_date"]["source_system"] == "SHIORI_UST_FIXED_COUPON_PROFILE"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_the_eight_fields_no_longer_block_a_trader_who_never_opens_advanced(
    server_url, page
) -> None:
    """Acceptance criterion 2, the headline product check: the two Advanced
    technical groups resolve themselves, so what is left outstanding is only
    the genuinely unresolved market input this PR is forbidden to fabricate."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    page.wait_for_function(
        "() => window.__shioriTestGetCurrentDraft().option_settlement_date !== null"
    )

    assert _is_actually_hidden(page, "advanced-body")  # never opened
    outstanding = _unresolved_group_ids(page)
    assert "advanced-bond-reference" not in outstanding
    assert "advanced-timing" not in outstanding
    # Only the Option Discount Curve is left, which #157 explicitly does not
    # fill: it is the one remaining market input, not a technical field.
    assert outstanding == ["discounting-review"]


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_forward_vol_and_discounting_are_never_given_a_fabricated_default(
    server_url, page
) -> None:
    """Acceptance criterion 9: this PR fills technical fields only."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_function(
        "() => window.__shioriTestGetCurrentDraft().option_settlement_date !== null"
    )

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_clean_price_input"]["forward_clean_price_per_100"] is None
    assert draft["volatility_input"]["volatility"] is None
    assert draft["curve_points"] == []
    outstanding = _unresolved_group_ids(page)
    for group in ("forward-review", "vol-review", "discounting-review"):
        assert group in outstanding
    assert page.input_value("#forward-price-input") == ""
    assert page.input_value("#volatility-input") == ""


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_an_advanced_override_really_reaches_the_typed_builder(server_url, page) -> None:
    """Acceptance criterion 4: the override is not a cosmetic form value -- the
    reviewed builder sees it and refuses the draft on its own terms."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    _wait_for_price_enabled(page)

    page.click("#advanced-head")
    page.select_option("#bond-status-select", "INACTIVE")
    page.wait_for_function(
        "() => window.__shioriTestBuilderValidationState() === 'failed'"
    )

    assert _draft_reference(page)["status"] == "INACTIVE"
    assert _is_disabled(page, "#price-btn")
    why = page.inner_text("#unresolved-why")
    assert "INACTIVE" in why
    assert _field_provenance(page)["bond_reference_data_universe.0.status"] == "TRADER_OVERRIDE"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_an_override_is_never_silently_reset_by_an_unrelated_edit(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    page.click("#advanced-head")
    page.select_option("#day-count-select", "ACT_360")
    page.fill("#last-coupon-date-input", "2029-07-31")
    page.click("#advanced-head")

    # Ordinary trade edits, market review entry, a validation round trip and a
    # profile re-derivation triggered by the expiry all follow.
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    page.wait_for_timeout(400)

    reference = _draft_reference(page)
    assert reference["day_count"] == "ACT_360"
    assert reference["last_coupon_date"] == "2029-07-31"
    assert page.input_value("#day-count-select") == "ACT_360"
    provenance = _field_provenance(page)
    assert provenance["bond_reference_data_universe.0.day_count"] == "TRADER_OVERRIDE"
    assert provenance["bond_reference_data_universe.0.last_coupon_date"] == "TRADER_OVERRIDE"
    # Everything the trader did not touch still says where it came from.
    assert provenance["bond_reference_data_universe.0.bond_type"] == "UST_PROFILE_DEFAULT"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_changing_expiry_recomputes_only_the_settlement_dates_not_overridden(
    server_url, page
) -> None:
    """Acceptance criterion 7, both halves in one run: the untouched date moves
    with the expiry, the overridden one does not."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)

    # A Friday expiry: one U.S. government-bond business day later is Monday.
    _set_expiry(page, local="2026-10-16T17:20")
    page.wait_for_function(
        "() => document.querySelector('#forward-settlement-date-input').value === '2026-10-19'"
    )
    assert page.input_value("#option-settlement-date-input") == "2026-10-19"

    page.click("#advanced-head")
    page.fill("#option-settlement-date-input", "2026-10-30")
    page.wait_for_timeout(150)

    _set_expiry(page, local="2026-10-20T17:20")
    page.wait_for_function(
        "() => document.querySelector('#forward-settlement-date-input').value === '2026-10-21'"
    )

    assert page.input_value("#option-settlement-date-input") == "2026-10-30"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_settlement_date"] == "2026-10-21"
    assert draft["option_settlement_date"] == "2026-10-30"
    provenance = _field_provenance(page)
    assert provenance["forward_settlement_date"] == "SHIORI_DERIVED"
    assert provenance["option_settlement_date"] == "TRADER_OVERRIDE"
    assert _trader_overridden_paths(page) == ["option_settlement_date"]


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_clearing_the_expiry_withdraws_the_settlement_dates_it_derived(
    server_url, page
) -> None:
    """A settlement date derived from an expiry the trader has moved away from
    is not a value Shiori still stands behind, so it is withdrawn rather than
    left behind as a stale-looking entry."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_function(
        "() => document.querySelector('#option-settlement-date-input').value === '2026-10-21'"
    )

    page.fill("#expiry-datetime-input", "")
    page.wait_for_function(
        "() => document.querySelector('#option-settlement-date-input').value === ''"
    )

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_settlement_date"] is None
    assert draft["option_settlement_date"] is None
    # The reporting date does not depend on expiry and is untouched.
    assert draft["reporting_date"] == "2026-07-20"
    assert "advanced-timing" in _unresolved_group_ids(page)


# --- Codex P1-2: no stale settlement window during an expiry-triggered
# profile refresh --------------------------------------------------------
#
# The exact race the review flagged: applyManualInputsToDraft used to leave
# the *previous* expiry's settlement dates sitting in the draft until the
# async /api/ust/profile refresh happened to overwrite them, during which a
# structurally-complete-looking draft could pass typed-builder validation and
# enable Price against a settlement date that no longer matches the expiry on
# screen. These tests hold that request open with page.route to prove the
# withdrawal is synchronous, not merely eventual.


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_expiry_change_blocks_price_until_the_new_profile_settles(server_url, page) -> None:
    """Covers five of the six required scenarios in one flow: a later-to-
    earlier expiry change, Price disabled while the response is outstanding,
    old dates not restored after a failed refresh, the trader override
    surviving untouched throughout, and Price re-enabling only once a
    genuinely new derived date has actually landed in the typed builder."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _fill_trade_group(page, expiry_local="2026-10-20T17:20", expiry_offset="+08:00")
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    _wait_for_price_enabled(page)

    # Override option_settlement_date so this run proves it survives
    # untouched through every step below.
    page.click("#advanced-head")
    page.fill("#option-settlement-date-input", "2026-10-30")
    page.wait_for_timeout(150)
    page.click("#advanced-head")
    _wait_for_price_enabled(page)
    assert (
        _field_provenance(page)["option_settlement_date"] == "TRADER_OVERRIDE"
    )

    pending: list = []
    page.route("**/api/ust/profile", lambda route: pending.append(route))

    # A later-to-earlier expiry change: 2026-10-20 -> 2026-09-10.
    _set_expiry(page, local="2026-09-10T17:20", offset="+08:00")
    _wait_until_pumping(page, lambda: len(pending) == 1)

    # While the new request is outstanding: the withdrawn (non-overridden)
    # forward date is gone immediately -- not merely stale-but-present -- the
    # override is untouched, and Price/builder validation must not have
    # raced ahead on the old settlement dates.
    assert page.input_value("#forward-settlement-date-input") == ""
    assert page.input_value("#option-settlement-date-input") == "2026-10-30"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_settlement_date"] is None
    assert draft["option_settlement_date"] == "2026-10-30"
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") != "ready"
    assert "advanced-timing" in _unresolved_group_ids(page)

    # A failed response must not restore the withdrawn date, or leave Price
    # enabled, or recover any prior state.
    pending[0].fulfill(
        status=400,
        content_type="application/json",
        body=json.dumps({"error": "profile request failed"}),
    )
    page.wait_for_timeout(200)
    assert page.input_value("#forward-settlement-date-input") == ""
    assert page.input_value("#option-settlement-date-input") == "2026-10-30"
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert "advanced-timing" in _unresolved_group_ids(page)

    # Retrying (an unrelated edit re-fires the request, since the failed
    # attempt released its key) succeeds, and only *then* does the new
    # derived date reach the typed builder and enable Price.
    page.unroute("**/api/ust/profile")
    page.fill("#strike-price-input", "99.50")
    _wait_for_price_enabled(page)
    assert page.input_value("#forward-settlement-date-input") == "2026-09-11"
    assert page.input_value("#option-settlement-date-input") == "2026-10-30"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_settlement_date"] == "2026-09-11"
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "ready"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_stale_profile_response_during_an_expiry_change_is_never_applied(
    server_url, page
) -> None:
    """The sixth required scenario: a first request (for an expiry the
    trader has already moved away from) answers after a second request (for
    the current expiry) has already been sent and applied -- the stale
    answer must never overwrite what the current one already wrote."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)

    pending: list = []
    request_count = 0

    def route_profile(route):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            pending.append(route)
        else:
            route.continue_()

    page.route("**/api/ust/profile", route_profile)

    _set_expiry(page, local="2026-10-16T17:20")  # Friday
    _wait_until_pumping(page, lambda: len(pending) == 1)

    # Move to a different expiry before the first request has answered --
    # the second request is allowed through by route_profile above.
    _set_expiry(page, local="2026-10-20T17:20")  # Tuesday
    page.wait_for_function(
        "() => document.querySelector('#forward-settlement-date-input').value === '2026-10-21'"
    )

    # The stale first request finally answers, describing the Friday
    # expiry's own dates -- it must not be applied over the current,
    # already-correct Tuesday-derived state.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "supported": True,
                "rejection_reasons": [],
                "pending_field_paths": [],
                "fields": [
                    {
                        "path": "forward_settlement_date",
                        "value": "2026-10-19",
                        "provenance": "SHIORI_DERIVED",
                    },
                    {
                        "path": "option_settlement_date",
                        "value": "2026-10-19",
                        "provenance": "SHIORI_DERIVED",
                    },
                ],
            }
        ),
    )
    page.wait_for_timeout(300)

    assert page.input_value("#forward-settlement-date-input") == "2026-10-21"
    assert page.input_value("#option-settlement-date-input") == "2026-10-21"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_settlement_date"] == "2026-10-21"
    assert draft["option_settlement_date"] == "2026-10-21"

    page.unroute("**/api/ust/profile", route_profile)


# --- Acceptance criterion 6: unsupported bonds are refused, with reasons -----


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_gilt_is_never_given_the_ust_profile(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, identifier="GB00BFX0ZL78", response=_gilt_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_timeout(250)

    assert _ust_profile(page)["supported"] is False
    reference = _draft_reference(page)
    for field in ("day_count", "bond_type", "ex_dividend_days", "last_coupon_date", "status"):
        assert reference[field] is None
    assert _field_provenance(page) == {}
    assert page.input_value("#day-count-select") == ""
    page.click("#advanced-head")
    status = page.inner_text("#ust-profile-status")
    assert "does not fit the selected UST convention profile" in status
    assert "is not USD" in status


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_callable_ust_is_never_given_the_ust_profile(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            bond_master={**_TREASURY_BOND_MASTER, "callable_flag": True}
        ),
    )
    _wait_for_ust_profile(page)

    assert _ust_profile(page)["supported"] is False
    assert _draft_reference(page)["bond_type"] is None
    assert "advanced-bond-reference" in _unresolved_group_ids(page)


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_sinkable_ust_is_never_given_the_ust_profile(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            bond_master={**_TREASURY_BOND_MASTER, "sinkable_flag": True}
        ),
    )
    _wait_for_ust_profile(page)

    assert _ust_profile(page)["supported"] is False
    assert _field_provenance(page) == {}


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_matured_ust_is_never_pre_filled_as_active(server_url, page) -> None:
    """The acquisition timestamp puts the valuation date past this bond's own
    maturity, so nothing about it may be described as ACTIVE."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page, response=_treasury_lookup_response(acquired_at="2031-06-20T11:28:00+08:00")
    )
    _wait_for_ust_profile(page)

    profile = _ust_profile(page)
    assert profile["supported"] is False
    assert any("is not after valuation_date" in reason for reason in profile["rejection_reasons"])
    assert _draft_reference(page)["status"] is None


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_an_irregular_schedule_is_never_given_a_derived_last_coupon_date(
    server_url, page
) -> None:
    """An irregular grid rejects all eight profile fields without erasing the
    Bloomberg Bond Master evidence that caused the refusal."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            bond_master={**_TREASURY_BOND_MASTER, "issue_date": "2024-03-05"}
        ),
    )
    _wait_for_ust_profile(page)

    profile = _ust_profile(page)
    assert profile["supported"] is False
    assert profile["fields"] == []
    assert profile["unresolved_fields"] == []

    reference = _draft_reference(page)
    for field in ("day_count", "bond_type", "ex_dividend_days", "last_coupon_date", "status"):
        assert reference[field] is None
    assert reference["coupon"] == 0.0375
    assert reference["issue_date"] == "2024-03-05"
    assert reference["maturity_date"] == "2031-01-31"

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["reporting_date"] is None
    assert draft["forward_settlement_date"] is None
    assert draft["option_settlement_date"] is None
    assert _field_provenance(page) == {}

    page.click("#advanced-head")
    status = page.inner_text("#ust-profile-status")
    assert "not supported on the current pricing path" in status
    assert "editing last_coupon_date" in status
    assert "yours to set" not in status
    assert _is_disabled(page, "#price-btn")
    assert _profile_controls_disabled(page)



@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_contradicting_day_count_description_blocks_only_that_field(
    server_url, page
) -> None:
    """Issue #161: a description string can still only *withhold*, never
    produce a typed value -- but it withholds the one field it is evidence
    about, not the whole ticket. The other seven stay filled and the control
    stays editable, because a trader override here is a genuine route."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(
            bond_master=_TREASURY_BOND_MASTER,
            bond_master_raw={
                "day_count": "ISMA-30/360",
                "maturity_type": "AT MATURITY",
                "calc_type": "STREET CONVENTION",
            },
            acquired_at="2026-07-20T11:28:00+08:00",
        ),
    )
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_timeout(250)

    profile = _ust_profile(page)
    assert profile["supported"] is True
    assert [item["path"] for item in profile["unresolved_fields"]] == [
        "bond_reference_data_universe.0.day_count"
    ]
    assert page.input_value("#day-count-select") == ""
    assert not _profile_controls_disabled(page)
    assert not page.eval_on_selector("#day-count-select", "el => el.disabled")

    # Every other field kept its resolved value -- the regression Issue #161
    # exists to fix.
    reference = _draft_reference(page)
    assert reference["bond_type"] == "FIXED_COUPON_BULLET"
    assert reference["ex_dividend_days"] == 0
    assert reference["last_coupon_date"] == "2030-07-31"
    assert reference["status"] == "ACTIVE"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["reporting_date"] == "2026-07-20"
    assert draft["forward_settlement_date"] == "2026-10-21"

    page.click("#advanced-head")
    assert "ISMA-30/360" in page.inner_text("#prov-day-count")
    assert "BLOCKED" in page.inner_text("#prov-day-count")


# --- Codex P1-1 (second revision): Convention Profile is browser state, not
# an issuer-identity claim ----------------------------------------------------
#
# The resolver's admission gate is shape-only now (currency, coupon shape,
# callable/sinkable, maturity, confirmed evidence strings): it makes no claim
# about who issued the bond, and "UST" is applied because the browser
# explicitly selected it, never because Shiori believes it has verified a
# Treasury issuer. These tests prove the browser actually sends that
# selection as an explicit request input, that a shape-compatible bond is
# admitted regardless of its ISIN, and that no "verified as Treasury" claim
# appears anywhere on the page.


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_convention_profile_is_sent_as_an_explicit_request_input(server_url, page) -> None:
    """The decisive wiring proof: the real request body actually carries
    convention_profile -- it is not a value the server invents on its own."""

    page.goto(f"{server_url}/")

    sent_bodies: list = []

    def capture(route):
        sent_bodies.append(json.loads(route.request.post_data))
        route.continue_()

    page.route("**/api/ust/profile", capture)
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    page.unroute("**/api/ust/profile", capture)

    assert len(sent_bodies) >= 1
    assert all(body.get("convention_profile") == "UST" for body in sent_bodies)
    assert page.evaluate("() => window.__shioriTestSelectedConventionProfile()") == "UST"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_convention_profile_badge_is_visible_even_with_advanced_collapsed(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")

    assert not _is_actually_hidden(page, "convention-profile-badge")
    assert page.inner_text("#convention-profile-badge") == "Convention Profile: UST"

    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)

    assert _is_actually_hidden(page, "advanced-body")  # still collapsed
    assert not _is_actually_hidden(page, "convention-profile-badge")
    assert page.inner_text("#convention-profile-badge") == "Convention Profile: UST"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_shape_compatible_non_us_isin_bond_is_admitted_to_the_ust_profile(
    server_url, page
) -> None:
    """The positive regression the correction asked for: a bond whose ISIN
    carries no US country prefix at all -- and is not any real Treasury's
    identifier -- still gets every field filled, because its shape fits and
    "UST" is the selected profile. Admission is shape-only."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(
            isin="XS0999999999",
            cusip="XS0999999",
            name="SYNTHETIC NON-TREASURY USD BOND",
            bond_master=_TREASURY_BOND_MASTER,
            bond_master_raw=_TREASURY_BOND_MASTER_RAW,
            acquired_at="2026-07-20T11:28:00+08:00",
        ),
    )
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_function(
        "() => window.__shioriTestGetCurrentDraft().option_settlement_date !== null"
    )

    profile = _ust_profile(page)
    assert profile["supported"] is True
    assert profile["rejection_reasons"] == []
    reference = _draft_reference(page)
    assert reference["day_count"] == "ACT_ACT_BOND"
    assert reference["bond_type"] == "FIXED_COUPON_BULLET"
    assert reference["status"] == "ACTIVE"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_no_page_text_claims_a_verified_treasury_identity(server_url, page) -> None:
    """Structural proof the withdrawn identity claim is gone, not merely
    relabeled: no rendered text anywhere on the page asserts that Shiori has
    verified the bond's issuer."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    page.click("#advanced-head")

    body_text = page.inner_text("body").lower()
    for forbidden in (
        "verified as treasury",
        "confirmed treasury",
        "treasury issuer",
        "issuer verified",
        "identity verified",
    ):
        assert forbidden not in body_text


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_an_irregular_schedule_never_suggests_a_last_coupon_date_fix(
    server_url, page
) -> None:
    """The refusal is visible before manual entry, and manual completion
    cannot bypass it afterward."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            bond_master={**_TREASURY_BOND_MASTER, "issue_date": "2024-03-05"}
        ),
    )
    _wait_for_ust_profile(page)

    profile = _ust_profile(page)
    assert profile["supported"] is False
    assert profile["fields"] == []
    assert profile["unresolved_fields"] == []

    page.click("#advanced-head")
    status_before = page.inner_text("#ust-profile-status").lower()
    assert "not supported on the current pricing path" in status_before
    assert "editing last_coupon_date cannot repair" in status_before
    assert "no advanced edit can make this ticket price" in status_before
    assert "manually fill last_coupon_date" not in status_before

    # Issue #161: the fake exit is gone. There is no editable control and no
    # Go-to button to offer a repair that could never work.
    assert _profile_controls_disabled(page)
    assert page.eval_on_selector("#unresolved-goto-btn", "el => el.hidden")
    assert "not supported on the current pricing path" in page.inner_text(
        "#unresolved-title"
    ).lower()

    _fill_non_profile_inputs(page)
    page.wait_for_timeout(250)

    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "unknown"
    assert _profile_controls_disabled(page)

    status_after = page.inner_text("#ust-profile-status").lower()
    assert "no advanced edit can make this ticket price" in status_after
    assert "manually fill last_coupon_date" not in status_after



# --- Acceptance criterion 8: nothing survives a reset or a stale answer ------


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_clear_discards_every_profile_value_override_and_tier(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.click("#advanced-head")
    page.select_option("#day-count-select", "ACT_360")
    page.wait_for_timeout(200)
    assert _trader_overridden_paths(page) == ["bond_reference_data_universe.0.day_count"]

    page.click("#clear-btn")
    page.wait_for_timeout(200)

    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert _ust_profile(page) is None
    assert _field_provenance(page) == {}
    assert _trader_overridden_paths(page) == []
    assert _override_provenance(page) == []
    for selector in (
        "#day-count-select",
        "#bond-type-select",
        "#ex-dividend-days-input",
        "#last-coupon-date-input",
        "#bond-status-select",
        "#reporting-date-input",
        "#forward-settlement-date-input",
        "#option-settlement-date-input",
    ):
        assert page.input_value(selector) == ""


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_second_load_never_inherits_the_previous_bonds_profile_or_override(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.click("#advanced-head")
    page.select_option("#day-count-select", "ACT_360")
    page.wait_for_timeout(200)

    _load_bloomberg_bond(page, identifier="GB00BFX0ZL78", response=_gilt_lookup_response())
    _wait_for_ust_profile(page)
    page.wait_for_timeout(200)

    assert _ust_profile(page)["supported"] is False
    assert _trader_overridden_paths(page) == []
    assert _field_provenance(page) == {}
    reference = _draft_reference(page)
    assert reference["day_count"] is None
    assert reference["last_coupon_date"] is None
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().reporting_date") is None


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_failed_lookup_leaves_no_profile_state_behind(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_timeout(200)

    def route_failed_lookup(route):
        route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        )

    page.route("**/api/bloomberg/bond", route_failed_lookup)
    page.fill("#bond-identifier-input", "US91282CLJ89")
    page.click("#load-bloomberg-bond-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg lookup failed")

    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    assert _ust_profile(page) is None
    assert _field_provenance(page) == {}
    assert _trader_overridden_paths(page) == []
    assert page.input_value("#reporting-date-input") == ""


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_failed_refresh_keeps_this_bonds_own_profile_values(server_url, page) -> None:
    """A refresh failure disowns what Bloomberg sourced, not the ticket. These
    eight fields belong to the same bond and the same ticket, so they stay --
    exactly like the trader's own overrides already do."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_for_ust_profile(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    _wait_for_price_enabled(page)

    def route_failed_refresh(route):
        route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
        )

    page.route("**/api/case/bloomberg", route_failed_refresh)
    page.click("#bloomberg-refresh-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Bloomberg refresh failed")

    reference = _draft_reference(page)
    assert reference["day_count"] == "ACT_ACT_BOND"
    assert reference["last_coupon_date"] == "2030-07-31"
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().option_settlement_date") == (
        "2026-10-21"
    )
    assert _field_provenance(page)["reporting_date"] == "SHIORI_DERIVED"
    # And the run itself is still disowned: no result, no right to price.
    assert _is_disabled(page, "#price-btn")


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_stale_profile_response_never_fills_the_next_bonds_fields(server_url, page) -> None:
    """The decisive staleness case: a profile answer for the first bond arrives
    after a different bond has been loaded. Applying it would fill one bond's
    Advanced fields from another's terms."""

    page.goto(f"{server_url}/")

    pending: list = []
    profile_requests = 0

    def route_profile(route):
        nonlocal profile_requests
        profile_requests += 1
        if profile_requests == 1:
            pending.append(route)
        else:
            route.continue_()

    page.route("**/api/ust/profile", route_profile)
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _wait_until_pumping(page, lambda: len(pending) == 1)
    assert _field_provenance(page) == {}  # nothing filled while it is outstanding

    _load_bloomberg_bond(page, identifier="GB00BFX0ZL78", response=_gilt_lookup_response())
    _wait_for_ust_profile(page)

    # The first bond's answer finally arrives, describing a supported UST.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "supported": True,
                "rejection_reasons": [],
                "pending_field_paths": [],
                "fields": [
                    {
                        "path": "bond_reference_data_universe.0.day_count",
                        "value": "ACT_ACT_BOND",
                        "provenance": "UST_PROFILE_DEFAULT",
                    },
                    {
                        "path": "reporting_date",
                        "value": "2026-07-20",
                        "provenance": "SHIORI_DERIVED",
                    },
                ],
            }
        ),
    )
    page.wait_for_timeout(300)

    assert _ust_profile(page)["supported"] is False
    assert _field_provenance(page) == {}
    assert _draft_reference(page)["day_count"] is None
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().reporting_date") is None
    assert page.input_value("#day-count-select") == ""


# =============================================================================
# Issue #161: trader intuition on a real UST
# =============================================================================
#
# Four separate regressions, all found on one Bloomberg workstation UAT run
# against ``US91282CMC28`` (T 4 1/2 12/31/31):
#
#   1. an ordinary Treasury note was refused outright because ``MTY_TYP``
#      read ``NORMAL`` rather than ``AT MATURITY``;
#   2. one unknown field blanked the seven that were known;
#   3. a bond the pricing path genuinely cannot carry still offered editable
#      Advanced controls and a "Go to this input" button that led nowhere;
#   4. Strike, Forward and Notional accepted only what ``Number()`` parses,
#      and the expiry offset had to be typed on every ticket.
#
# The fixture below is UST-*shaped* -- a regular semi-annual grid and the
# description strings the UAT recorded. Like every other fixture in this file
# it is synthetic and is never used as evidence about the real security.

_REAL_UST_BOND_MASTER = {
    "coupon": 0.045,
    "coupon_frequency": "SEMI_ANNUAL",
    "issue_date": "2024-12-31",
    "maturity_date": "2031-12-31",
    "first_coupon_date": "2025-06-30",
    "callable_flag": False,
    "sinkable_flag": False,
}
# The UAT's own reading: MTY_TYP = NORMAL on a perfectly ordinary Treasury.
_REAL_UST_BOND_MASTER_RAW = {
    "day_count": "ACT/ACT",
    "maturity_type": "NORMAL",
    "calc_type": "STREET CONVENTION",
}


def _real_ust_lookup_response(
    *, bond_master: dict | None = None, bond_master_raw: dict | None = None, **overrides
) -> dict:
    payload_overrides = {
        "isin": "US91282CMC28",
        "cusip": "91282CMC2",
        "name": "UNITED STATES TREAS NTS",
        "acquired_at": "2026-07-20T11:28:00+08:00",
        **overrides,
    }
    return _default_bloomberg_bond_lookup_response(
        bond_master=bond_master if bond_master is not None else _REAL_UST_BOND_MASTER,
        bond_master_raw=(
            bond_master_raw if bond_master_raw is not None else _REAL_UST_BOND_MASTER_RAW
        ),
        **payload_overrides,
    )


def _load_real_ust(page) -> None:
    _load_bloomberg_bond(
        page, identifier="US91282CMC28", response=_real_ust_lookup_response()
    )
    _wait_for_ust_profile(page)


def _complete_real_ust_draft(page, *, forward: str = "99.234375", **trade_kwargs) -> None:
    """Complete a real-UST ticket the way Issue #161 intends it to be completed.

    The trader fills the trade and the market review; the resolver fills the
    eight technical fields itself. Deliberately *not* ``_complete_draft``,
    whose hand-typed Advanced defaults belong to the synthetic fixture's own
    coupon grid -- typing them over a different bond's derived values is
    exactly the per-ticket hand-filling this issue removes.
    """

    _fill_trade_group(page, **trade_kwargs)
    _fill_market_review(page, forward=forward)
    _fill_curve_nodes_only(page)


# --- Criterion 1-3: the real UST is admitted and fills itself ---------------


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_real_ust_returning_maturity_type_normal_is_no_longer_refused(
    server_url, page
) -> None:
    """The headline UAT regression: MTY_TYP = NORMAL must not reject the bond."""

    page.goto(f"{server_url}/")
    _load_real_ust(page)

    profile = _ust_profile(page)
    assert profile["supported"] is True
    assert profile["rejection_reasons"] == []
    assert profile["unresolved_fields"] == []
    # The description string is still shown as evidence -- it just decides
    # nothing any more.
    page.click("#advanced-head")
    assert page.inner_text("#hint-bond-type") == "NORMAL"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_real_ust_auto_fills_all_eight_advanced_fields_without_opening_advanced(
    server_url, page
) -> None:
    """Criterion 2/3: a trader who never opens Advanced is not blocked by it."""

    page.goto(f"{server_url}/")
    _load_real_ust(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    page.wait_for_function(
        "() => window.__shioriTestGetCurrentDraft().option_settlement_date !== null"
    )

    assert _is_actually_hidden(page, "advanced-body")  # never opened
    reference = _draft_reference(page)
    assert reference["day_count"] == "ACT_ACT_BOND"
    assert reference["bond_type"] == "FIXED_COUPON_BULLET"
    assert reference["ex_dividend_days"] == 0
    assert reference["last_coupon_date"] == "2031-06-30"
    assert reference["status"] == "ACTIVE"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["reporting_date"] == "2026-07-20"
    assert draft["forward_settlement_date"] == "2026-10-21"
    assert draft["option_settlement_date"] == "2026-10-21"

    outstanding = _unresolved_group_ids(page)
    assert "advanced-bond-reference" not in outstanding
    assert "advanced-timing" not in outstanding
    assert outstanding == ["discounting-review"]

    # Criterion 4: every value still says where it came from.
    assert _field_provenance(page) == {
        "bond_reference_data_universe.0.day_count": "UST_PROFILE_DEFAULT",
        "bond_reference_data_universe.0.bond_type": "UST_PROFILE_DEFAULT",
        "bond_reference_data_universe.0.ex_dividend_days": "UST_PROFILE_DEFAULT",
        "bond_reference_data_universe.0.last_coupon_date": "SHIORI_DERIVED",
        "bond_reference_data_universe.0.status": "UST_PROFILE_DEFAULT",
        "reporting_date": "SHIORI_DERIVED",
        "forward_settlement_date": "SHIORI_DERIVED",
        "option_settlement_date": "SHIORI_DERIVED",
    }


# --- Criterion 5 + 9: one blocked field, seven still filled, repairable ------


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_one_blocked_field_never_blanks_the_others_and_stays_repairable(
    server_url, page
) -> None:
    """Criterion 5 and 9 together: a field-level blocker keeps every other
    resolved value *and* offers a real override route, unlike a product
    refusal. Uses the day-count-evidence contradiction -- the one case that
    genuinely is repairable in Advanced -- since a missing Bloomberg schedule
    date is not (see the P2-correction tests below)."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="US91282CMC28",
        response=_real_ust_lookup_response(
            bond_master_raw={
                "day_count": "ISMA-30/360",
                "maturity_type": "NORMAL",
                "calc_type": "STREET CONVENTION",
            }
        ),
    )
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_timeout(300)

    profile = _ust_profile(page)
    assert profile["supported"] is True
    assert [item["path"] for item in profile["unresolved_fields"]] == [
        "bond_reference_data_universe.0.day_count"
    ]
    assert page.evaluate("() => window.__shioriTestBlockedFieldPaths()") == [
        "bond_reference_data_universe.0.day_count"
    ]

    # Seven fields survived the eighth.
    reference = _draft_reference(page)
    assert reference["day_count"] is None
    assert reference["bond_type"] == "FIXED_COUPON_BULLET"
    assert reference["last_coupon_date"] == "2031-06-30"
    assert reference["status"] == "ACTIVE"
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["reporting_date"] == "2026-07-20"
    assert draft["option_settlement_date"] == "2026-10-21"

    # Criterion 9: this one *is* repairable, so the route is offered for real.
    assert not _profile_controls_disabled(page)
    assert not page.eval_on_selector("#unresolved-goto-btn", "el => el.hidden")
    page.click("#advanced-head")
    assert not page.eval_on_selector("#day-count-select", "el => el.disabled")
    assert "BLOCKED" in page.inner_text("#prov-day-count")
    assert "ISMA-30/360" in page.inner_text("#prov-day-count")

    page.select_option("#day-count-select", "ACT_360")
    page.wait_for_timeout(250)
    assert _draft_reference(page)["day_count"] == "ACT_360"
    assert (
        _field_provenance(page)["bond_reference_data_universe.0.day_count"]
        == "TRADER_OVERRIDE"
    )

    # Independent-review finding (Issue #161 P2 review round): stopping here
    # only proves the draft/provenance updated, not that the override is a
    # genuine completion route. Complete the rest of the ticket the ordinary
    # way and prove it end-to-end against the real typed builder -- the same
    # rigor already applied to the non-repairable first_coupon_date case
    # below, now applied to the case that is supposed to contrast with it.
    _fill_trade_group(page, strike="99.5", notional="1000000")
    _fill_market_review(page)
    _fill_curve_nodes_only(page)

    _wait_for_price_enabled(page)
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "ready"

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_reference_data_universe"][0]["day_count"] == "ACT_360"
    response = page.request.post(
        f"{server_url}/api/case/validate",
        data=json.dumps(draft),
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["error"] is None

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert float(page.inner_text("#price-total")) > 0
    assert page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0].day_count"
    ) == "ACT_360"


# --- Issue #161 P2 correction: a missing schedule date is NOT repairable ----
#
# Real Bloomberg workstation UAT (US91282CMC28-shaped, no confirmed
# first_coupon_date) exposed a second-order fake exit: the first revision of
# the P1 field-level fix marked last_coupon_date BLOCKED-and-repairable in
# exactly this case, and the browser offered its Advanced control as a route
# to complete the ticket. It never was one -- issue_date, maturity_date and
# first_coupon_date are BondReferenceData destination fields with no
# Advanced override anywhere on this route, so the reviewed typed builder
# still refuses the draft on the missing one no matter what the trader types
# for last_coupon_date. These tests drive the override path all the way
# through the real POST /api/case/validate route (not just draft/provenance
# state) to prove it, and confirm the non-repairable UI takes over instead.


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_missing_first_coupon_date_leaves_last_coupon_date_unresolved_not_blocked(
    server_url, page
) -> None:
    """The resolver's own answer must not promise a route that does not
    exist: last_coupon_date is absent from both `fields` and
    `unresolved_fields`, not BLOCKED."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="US91282CMC28",
        response=_real_ust_lookup_response(
            bond_master={**_REAL_UST_BOND_MASTER, "first_coupon_date": None}
        ),
    )
    _wait_for_ust_profile(page)
    _set_expiry(page)
    page.wait_for_timeout(300)

    profile = _ust_profile(page)
    assert profile["supported"] is True
    assert profile["rejection_reasons"] == []
    assert profile["unresolved_fields"] == []
    field_paths = {field["path"] for field in profile["fields"]}
    assert "bond_reference_data_universe.0.last_coupon_date" not in field_paths
    # The seven fields that do not depend on first_coupon_date are unaffected.
    assert "bond_reference_data_universe.0.day_count" in field_paths
    assert "bond_reference_data_universe.0.status" in field_paths


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_the_last_coupon_date_control_is_disabled_and_offers_no_go_to(
    server_url, page
) -> None:
    """Issue #161's fake-exit rule extended to a single field: this control
    must not look like a legitimate completion route when it structurally
    cannot be one."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="US91282CMC28",
        response=_real_ust_lookup_response(
            bond_master={**_REAL_UST_BOND_MASTER, "first_coupon_date": None}
        ),
    )
    _wait_for_ust_profile(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    page.wait_for_timeout(300)

    # The whole ticket is not disabled -- day_count/bond_type/ex_dividend_days
    # genuinely did resolve and stay editable like any other field.
    assert not _profile_controls_disabled(page)
    page.click("#advanced-head")
    assert page.eval_on_selector("#last-coupon-date-input", "el => el.disabled")
    assert not page.eval_on_selector("#day-count-select", "el => el.disabled")
    prov_text = page.inner_text("#prov-last-coupon-date")
    assert "BLOCKED" not in prov_text
    assert "Bloomberg Bond Master" in prov_text or "no Advanced control" in prov_text

    # The primary unresolved panel names the real, actionable blocker --
    # Bloomberg's own missing data -- not last_coupon_date.
    assert page.inner_text("#unresolved-title") == (
        "Bloomberg did not return every confirmed Bond Master field"
    )
    assert "first_coupon_date" not in page.inner_text("#unresolved-why")


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_forcing_a_last_coupon_date_override_still_fails_real_builder_validation(
    server_url, page
) -> None:
    """The literal ask: prove this end-to-end against the real typed builder,
    not just draft/provenance state. Every other input is completed through
    the real controls; last_coupon_date is then forced past its disabled
    control (the worst case a broken future revision could reach) and the
    live /api/case/validate route -- the same one Price is gated on -- is
    asked directly whether the resulting draft is acceptable.

    It must not be: first_coupon_date is still missing, and the reviewed
    BLIStandaloneBondReferenceData construction requires it regardless of
    last_coupon_date.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="US91282CMC28",
        response=_real_ust_lookup_response(
            bond_master={**_REAL_UST_BOND_MASTER, "first_coupon_date": None}
        ),
    )
    _wait_for_ust_profile(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    page.wait_for_timeout(300)

    # Force the override past the disabled control -- exactly the same
    # "input" listener a real keystroke triggers (markTraderOverride, then
    # applyManualInputsToDraft), so this is not testing a different code path
    # than a trader typing would reach if the control were ever left enabled.
    page.evaluate(
        """() => {
            const el = document.querySelector("#last-coupon-date-input");
            el.value = "2031-06-30";
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
        }"""
    )
    page.wait_for_timeout(200)

    assert _draft_reference(page)["last_coupon_date"] == "2031-06-30"
    assert (
        _field_provenance(page)["bond_reference_data_universe.0.last_coupon_date"]
        == "TRADER_OVERRIDE"
    )

    # Front-end gating never even attempts validation, because the
    # "Bloomberg Bond Master" workflow group is still unresolved.
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "unknown"
    assert _is_disabled(page, "#price-btn")

    # The decisive check: ask the real typed builder directly, bypassing all
    # front-end gating, with the exact draft the override just produced.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    response = page.request.post(
        f"{server_url}/api/case/validate",
        data=json.dumps(draft),
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 200
    payload = response.json()
    assert payload["ready"] is False
    assert "first_coupon_date" in payload["error"]


# --- Issue #161 P3 correction: a malformed (non-empty) Bond Master date is
# not "present" -- it is not a repairable field either ----------------------
#
# Independent-review finding on this exact head: the P2 fix above closed the
# fake exit for a *missing* first_coupon_date, but its own browser-side
# `bondMasterGapPaths()` and the "Bloomberg Bond Master" workflow group's own
# `resolved()` both used `present()` -- a bare non-blank-string check -- for
# issue_date/maturity_date/first_coupon_date. Bloomberg's own ISSUE_DT /
# MATURITY / FIRST_CPN_DT mnemonics pass through with zero format validation
# (data/bloomberg_bond_quote.py's `_passthrough_bloomberg_string`), so a
# malformed-but-non-blank date (e.g. "20241231", no dashes -- a realistic
# shape depending on Bloomberg terminal/session date-format settings) slipped
# straight past `present()` while the resolver's own `_optional_iso_date`
# correctly rejected it. That silently reopened the exact class of fake exit
# Issue #161 exists to remove: last-coupon-date-input stayed enabled, the
# "Bloomberg Bond Master" group wrongly reported itself resolved, and the
# primary panel showed "Open Advanced → Bond reference and set the
# outstanding fields yourself" -- a route that could never make the ticket
# price, with the real "issue_date must be a YYYY-MM-DD date string" error
# only surfacing after the trader had already "completed" the fake route.
#
# These tests prove the fix: a malformed date is caught immediately (no
# window where the trader is invited down the fake route), the correct
# group/message/next-step is shown from the start, and the real
# /api/case/validate route still independently confirms no override can help.


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
@pytest.mark.parametrize(
    ("date_field", "malformed_value"),
    [
        ("issue_date", "20241231"),
        ("maturity_date", "31/12/2031"),
        ("first_coupon_date", "2025-13-40"),
        ("maturity_date", "2031-02-30"),  # correct shape, impossible calendar date
    ],
)
def test_a_malformed_bond_master_date_is_never_offered_as_a_repairable_field(
    server_url, page, date_field, malformed_value
) -> None:
    """Immediately after everything else is filled -- never touching
    last_coupon_date at all -- the correct, honest blocker is already shown,
    with no fake route offered anywhere on the page."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="US91282CMC28",
        response=_real_ust_lookup_response(
            bond_master={**_REAL_UST_BOND_MASTER, date_field: malformed_value}
        ),
    )
    _wait_for_ust_profile(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    page.wait_for_timeout(300)

    # The resolver's own answer is unaffected (Issue #161 P2's per-field
    # design is correct at that layer already) -- last_coupon_date is simply
    # absent, never falsely BLOCKED-and-repairable.
    profile = _ust_profile(page)
    assert profile["supported"] is True
    assert profile["rejection_reasons"] == []
    assert profile["unresolved_fields"] == []
    resolved_paths = {field["path"] for field in profile["fields"]}
    assert "bond_reference_data_universe.0.last_coupon_date" not in resolved_paths

    # The primary panel names the real, actionable blocker from the start --
    # not a generic "fill Advanced yourself" message.
    assert "advanced-bond-reference" not in _unresolved_group_ids(page)[:1]
    assert page.inner_text("#unresolved-title") == (
        "Bloomberg did not return every confirmed Bond Master field"
    )
    assert "Run Bloomberg Load again" in page.inner_text("#unresolved-next")
    assert "set the outstanding fields yourself" not in page.inner_text("#unresolved-next")

    # The affected control(s) are disabled and say why -- not "yours to
    # enter". status only depends on maturity_date specifically.
    page.click("#advanced-head")
    assert page.eval_on_selector("#last-coupon-date-input", "el => el.disabled")
    prov_text = page.inner_text("#prov-last-coupon-date")
    assert "BLOCKED" not in prov_text
    assert "yours to enter" not in prov_text
    assert "Run Bloomberg Load again" in prov_text
    assert page.eval_on_selector("#bond-status-select", "el => el.disabled") == (
        date_field == "maturity_date"
    )

    # Both actions this bug could otherwise mislead a trader into attempting
    # stay off, from the very first render -- no window where either looks
    # available.
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")
    assert page.evaluate("() => window.__shioriTestBuilderValidationState()") == "unknown"

    # And the real typed builder independently agrees no override can help,
    # confirming this is not merely a front-end gating assumption.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    response = page.request.post(
        f"{server_url}/api/case/validate",
        data=json.dumps(draft),
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 200
    assert response.json()["ready"] is False


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_malformed_issue_date_control_never_becomes_a_go_to_destination(
    server_url, page
) -> None:
    """The Go-to button, when shown, must lead to the real blocker (the
    read-only Bloomberg Bond Master panel) -- never to an Advanced control
    that cannot repair anything."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="US91282CMC28",
        response=_real_ust_lookup_response(
            bond_master={**_REAL_UST_BOND_MASTER, "issue_date": "20241231"}
        ),
    )
    _wait_for_ust_profile(page)
    _fill_trade_group(page)
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    page.wait_for_timeout(300)

    assert not page.eval_on_selector("#unresolved-goto-btn", "el => el.hidden")
    page.click("#unresolved-goto-btn")
    page.wait_for_timeout(150)
    # The Bloomberg Bond Master panel's own read-only field, not the
    # editable (but disabled and futile) last-coupon-date-input.
    assert page.evaluate("() => document.activeElement.id") == "details-coupon"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_malformed_bond_master_date_survives_a_bloomberg_load_retry_reset(
    server_url, page
) -> None:
    """Requirement #3's real next step (re-run Bloomberg Load) genuinely
    clears the gap once Bloomberg returns a usable date -- the fix is not a
    permanent dead ticket."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        identifier="US91282CMC28",
        response=_real_ust_lookup_response(
            bond_master={**_REAL_UST_BOND_MASTER, "issue_date": "20241231"}
        ),
    )
    _wait_for_ust_profile(page)
    assert "bloomberg-reference" in _unresolved_group_ids(page)

    _load_bloomberg_bond(page, identifier="US91282CMC28", response=_real_ust_lookup_response())
    _wait_for_ust_profile(page)

    assert _ust_profile(page)["supported"] is True
    assert "bloomberg-reference" not in _unresolved_group_ids(page)
    page.click("#advanced-head")
    assert not page.eval_on_selector("#last-coupon-date-input", "el => el.disabled")
    assert _draft_reference(page)["last_coupon_date"] == "2031-06-30"


# --- Criterion 10-11: Treasury 32nds on Strike and Forward ------------------


@_PLAYWRIGHT_SKIP
def test_treasury_quote_parser_normalizes_every_approved_form(server_url, page) -> None:
    """Criterion 10, on the page's own parser -- not a copy of it.

    The four worked examples in Issue #161, plus the decimal passthrough and
    the boundaries of each field.
    """

    page.goto(f"{server_url}/")
    cases = {
        "98.515625": 98.515625,
        "98-16": 98.5,
        "98-16+": 98.515625,
        "98-164": 98.515625,
        "99-032": 99.1015625,
        "99-037": 99.12109375,
        "100-00": 100.0,
        "100-000": 100.0,
        "99-31": 99.96875,
        "99-317": 99.99609375,
        "0-01": 0.03125,
    }
    for raw, expected in cases.items():
        parsed = page.evaluate("(raw) => window.__shioriTestParseTreasuryQuote(raw)", raw)
        assert parsed["error"] is None, f"{raw} was rejected: {parsed['error']}"
        assert parsed["value"] == expected, f"{raw} -> {parsed['value']}, expected {expected}"


@_PLAYWRIGHT_SKIP
@pytest.mark.parametrize(
    "raw",
    [
        "98-32",  # 32nds out of range
        "98-99",
        "98-168",  # sub-32nd digit out of range
        "98-169",
        "98-16++",  # duplicated modifier
        "98-16.5",  # mixed formats
        "98-1",  # not two 32nds digits
        "98-",
        "ninety-eight",
        "98 16",
        "98.5x",
        "-98.5",  # a price is never negative on this route
        "1e2",
    ],
)
def test_a_malformed_treasury_quote_is_named_not_silently_dropped(
    server_url, page, raw
) -> None:
    """Criterion 11: never null-in-silence, never 0, never the previous value."""

    page.goto(f"{server_url}/")
    parsed = page.evaluate("(raw) => window.__shioriTestParseTreasuryQuote(raw)", raw)

    assert parsed["value"] is None
    assert parsed["error"], f"{raw} was accepted or silently dropped"
    assert raw in parsed["error"]


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_32nds_strike_and_forward_reach_the_engine_as_decimals(server_url, page) -> None:
    """Criterion 10 + 16: the trader types 32nds, the contract stays decimal."""

    page.goto(f"{server_url}/")
    _load_real_ust(page)
    _complete_real_ust_draft(page, strike="99-16+", forward="99-234")
    _wait_for_price_enabled(page)

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_option"]["strike_price"] == 99.515625
    assert draft["forward_clean_price_input"]["forward_clean_price_per_100"] == 99.734375
    assert "normalized 99.515625 per 100" in page.inner_text("#strike-normalized-preview")

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert float(page.inner_text("#price-total")) > 0

    # Criterion 16: the priced case the page now holds still carries decimals,
    # and the control still shows the trader their own quote.
    priced = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert priced["bond_option"]["strike_price"] == 99.515625
    assert page.input_value("#strike-price-input") == "99-16+"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_malformed_strike_blocks_with_its_own_message(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_real_ust(page)
    _complete_real_ust_draft(page)
    _wait_for_price_enabled(page)

    page.fill("#strike-price-input", "98-33")
    page.wait_for_timeout(250)

    assert (
        page.evaluate("() => window.__shioriTestGetCurrentDraft().bond_option.strike_price")
        is None
    )
    assert "32nds part must be 00–31" in page.inner_text("#strike-normalized-preview")
    assert _is_disabled(page, "#price-btn")
    assert "strike" in _unresolved_group_ids(page)
    assert page.inner_text("#unresolved-title") == "Strike cannot be read"


# --- Criterion 12-13: K / M notional ----------------------------------------


@_PLAYWRIGHT_SKIP
def test_notional_parser_normalizes_every_approved_form(server_url, page) -> None:
    page.goto(f"{server_url}/")
    cases = {
        "1K": 1000,
        "1k": 1000,
        "250K": 250000,
        "1M": 1000000,
        "1m": 1000000,
        "1.5M": 1500000,
        "1.1M": 1100000,  # scaled-integer arithmetic, not 1100000.0000000001
        "0.5M": 500000,
        "1000000": 1000000,
        "1,000,000": 1000000,
        "999": 999,
    }
    for raw, expected in cases.items():
        parsed = page.evaluate("(raw) => window.__shioriTestParseNotional(raw)", raw)
        assert parsed["error"] is None, f"{raw} was rejected: {parsed['error']}"
        assert parsed["value"] == expected, f"{raw} -> {parsed['value']}, expected {expected}"


@_PLAYWRIGHT_SKIP
@pytest.mark.parametrize(
    "raw",
    [
        "K",
        "M",
        "1MM",
        "1.2.3M",
        "1B",  # an unrequested suffix is not quietly invented
        "-1M",
        "0",
        "0M",
        "1,00,000",  # not a legal thousands grouping
        "1M extra",
        "1 000 000",
        "one million",
    ],
)
def test_a_malformed_notional_is_named_not_part_read(server_url, page, raw) -> None:
    """Criterion 13: no partial parse, no swallowed trailing text."""

    page.goto(f"{server_url}/")
    parsed = page.evaluate("(raw) => window.__shioriTestParseNotional(raw)", raw)

    assert parsed["value"] is None
    assert parsed["error"], f"{raw} was accepted or part-read"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_shorthand_notional_reaches_the_engine_as_a_number(server_url, page) -> None:
    """Criterion 12 + 16: "1M" is a display format; the draft holds 1000000."""

    page.goto(f"{server_url}/")
    _load_real_ust(page)
    _complete_real_ust_draft(page, notional="1M")
    _wait_for_price_enabled(page)

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_option"]["notional"] == 1000000
    assert "1,000,000" in page.inner_text("#notional-normalized-preview")

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert float(page.inner_text("#price-total")) > 0
    assert (
        page.evaluate("() => window.__shioriTestGetCurrentDraft().bond_option.notional")
        == 1000000
    )


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_a_malformed_notional_blocks_with_its_own_message(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_real_ust(page)
    _complete_real_ust_draft(page)
    _wait_for_price_enabled(page)

    page.fill("#notional-input", "1MM")
    page.wait_for_timeout(250)

    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().bond_option.notional") is None
    assert "1MM" in page.inner_text("#notional-normalized-preview")
    assert _is_disabled(page, "#price-btn")
    assert page.inner_text("#unresolved-title") == "Notional cannot be read"


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_normalized_previews_never_change_the_exported_canonical_values(
    server_url, page
) -> None:
    """Criterion 16: the JSON export is byte-identical whether the trader typed
    the trade in desk shorthand or in canonical numbers."""

    exported: list = []

    def _capture(route):
        exported.append(json.loads(route.request.post_data))
        route.continue_()

    page.goto(f"{server_url}/")
    _load_real_ust(page)
    _complete_real_ust_draft(page, strike="99-16+", notional="1M", forward="99-234")
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    page.route("**/api/export/json", _capture)
    page.click("#download-json-btn")
    _wait_until_pumping(page, lambda: len(exported) == 1)

    shorthand_display = exported[0]["display"]

    page.click("#clear-btn")
    _load_real_ust(page)
    _complete_real_ust_draft(page, strike="99.515625", notional="1000000", forward="99.734375")
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    page.click("#download-json-btn")
    _wait_until_pumping(page, lambda: len(exported) == 2)

    assert exported[1]["display"] == shorthand_display


# --- Criterion 14-15: the Taipei expiry offset ------------------------------


@_PLAYWRIGHT_SKIP
def test_a_new_ticket_starts_with_the_taipei_offset_and_no_guessed_expiry(
    server_url, page
) -> None:
    """Criterion 14 and 15: the offset is pre-filled; the date and time are not."""

    page.goto(f"{server_url}/")
    page.wait_for_timeout(150)

    assert page.input_value("#expiry-offset-input") == "+08:00"
    assert page.input_value("#expiry-datetime-input") == ""
    assert page.evaluate("() => window.__shioriTestDefaultExpiryUtcOffset()") == "+08:00"

    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    assert page.input_value("#expiry-offset-input") == "+08:00"
    assert page.input_value("#expiry-datetime-input") == ""
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["bond_option"]["expiry_date"] is None
    assert draft["expiry_timestamp"] is None


@_PLAYWRIGHT_SKIP
@_QUANTLIB_SKIP
def test_an_overridden_expiry_offset_survives_the_whole_ticket(server_url, page) -> None:
    """Criterion 14: nothing in a re-render, a validation pass, an expiry
    change, a profile refresh or a priced run writes the offset back."""

    page.goto(f"{server_url}/")
    _load_real_ust(page)
    page.fill("#expiry-offset-input", "-05:00")
    page.fill("#expiry-datetime-input", "2026-10-20T17:20")
    page.wait_for_timeout(200)

    assert page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().expiry_timestamp"
    ) == "2026-10-20T17:20:00-05:00"

    # An expiry date change re-derives the settlement dates -- and must not
    # touch the offset while doing it.
    page.fill("#expiry-datetime-input", "2026-11-20T17:20")
    page.wait_for_function(
        "() => window.__shioriTestGetCurrentDraft().option_settlement_date === '2026-11-23'"
    )
    assert page.input_value("#expiry-offset-input") == "-05:00"

    # A full priced run, which rewrites the trade form from the priced case.
    _fill_trade_group(page, expiry_local="2026-11-20T17:20", expiry_offset="-05:00")
    _fill_market_review(page)
    _fill_curve_nodes_only(page)
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    assert page.input_value("#expiry-offset-input") == "-05:00"
    assert page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().expiry_timestamp"
    ) == "2026-11-20T17:20:00-05:00"


@_PLAYWRIGHT_SKIP
def test_clear_and_a_second_load_each_restore_the_taipei_offset(server_url, page) -> None:
    """Criterion 14's other half: a *new* ticket starts at +08:00 again."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    page.fill("#expiry-offset-input", "Z")
    page.wait_for_timeout(150)
    assert page.input_value("#expiry-offset-input") == "Z"

    page.click("#clear-btn")
    page.wait_for_timeout(150)
    assert page.input_value("#expiry-offset-input") == "+08:00"

    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    page.fill("#expiry-offset-input", "+09:00")
    page.wait_for_timeout(150)
    _load_bloomberg_bond(
        page, identifier="US91282CMC28", response=_real_ust_lookup_response()
    )
    assert page.input_value("#expiry-offset-input") == "+08:00"
