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


def _missing_category_badges(page) -> list[str]:
    return page.eval_on_selector_all(
        "#missing-categories .missing-category-badge", "els => els.map(el => el.textContent)"
    )


def _remaining_input_count(page) -> int:
    return len(_missing_fields_text(page))


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
    assert page.inner_text("#remaining-input-summary") == "28 required inputs still unresolved"
    missing = _missing_fields_text(page)
    assert any("Strike (per 100)" in item for item in missing)
    assert any("Call / Put" in item for item in missing)
    # One compact count and category badges show first; full actionable detail
    # appears only after "Show details".
    assert page.inner_text("#remaining-input-summary").endswith("required inputs still unresolved")
    assert "Option terms incomplete" in page.inner_text("#missing-categories")
    assert "Bond reference data incomplete" in page.inner_text("#missing-categories")

    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#bloomberg-refresh-btn")


@_PLAYWRIGHT_SKIP
def test_acquisition_event_mechanically_populates_timing_without_changing_its_local_date(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    acquired_at = "2026-07-20T00:30:00+08:00"
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(acquired_at=acquired_at),
    )

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

    for selector in (
        "#pricing-timestamp-input",
        "#valuation-date-input",
        "#as-of-timestamp-input",
    ):
        assert page.eval_on_selector(selector, "el => el.readOnly")
    assert "Bloomberg acquisition event" in page.inner_text("#timing-body")


@_PLAYWRIGHT_SKIP
def test_auto_timing_does_not_overwrite_explicit_trader_editable_fields(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    original = page.evaluate(
        """() => {
          const d = window.__shioriTestGetCurrentDraft();
          return {
            valuation_date: d.valuation_date,
            as_of_timestamp: d.as_of_timestamp,
            pricing_timestamp: d.pricing_timestamp,
          };
        }"""
    )

    page.fill("#expiry-date-input", "2026-10-20")
    page.fill("#expiry-timestamp-input", "2026-10-20T05:20:00+08:00")
    page.click("#timing-head")
    page.fill("#reporting-date-input", "2026-10-21")
    # Trigger another ordinary form sync after all values are set.
    page.fill("#strike-price-input", "99.32")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["reporting_date"] == "2026-10-21"
    assert draft["bond_option"]["expiry_date"] == "2026-10-20"
    assert draft["expiry_timestamp"] == "2026-10-20T05:20:00+08:00"
    assert {key: draft[key] for key in original} == original


@_PLAYWRIGHT_SKIP
def test_remaining_input_count_is_contract_aligned_and_locates_advanced_blockers(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    # PR #144 required 24 manual blockers for this Bloomberg-shaped draft.
    # Three timing values now come from the acquisition event, leaving 21.
    assert _remaining_input_count(page) == 21
    assert page.inner_text("#remaining-input-summary") == "21 required inputs still unresolved"
    assert page.inner_text("#timing-summary") == "4 required"
    assert page.inner_text("#bond-ref-summary") == "8 required"
    assert _is_actually_hidden(page, "timing-body")
    assert _is_actually_hidden(page, "bond-ref-body")

    page.click("#missing-details-toggle-btn")
    page.locator("#missing-fields-list .missing-field-link", has_text="Reporting Date").click()
    assert not _is_actually_hidden(page, "timing-body")
    assert page.evaluate("() => document.activeElement.id") == "reporting-date-input"
    page.locator("#missing-fields-list .missing-field-link", has_text="Day Count").click()
    assert not _is_actually_hidden(page, "bond-ref-body")
    assert page.evaluate("() => document.activeElement.id") == "day-count-select"


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
            bond_master={"coupon": 0.04125, "maturity_date": "2031-01-31"}
        ),
    )

    # Stored/priced internally as a decimal fraction (0.04125), but the
    # trader-facing UI displays it as a percentage (4.125%).
    assert page.inner_text("#resolved-bond-coupon") == "4.125%"
    assert page.inner_text("#resolved-bond-maturity") == "2031-01-31"
    assert page.inner_text("#details-coupon") == "4.125%"
    assert page.inner_text("#details-maturity") == "2031-01-31"
    # A field still unconfirmed/unreturned stays honestly "Not available",
    # never a fabricated or synthetic value.
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
    """Coupon is stored/priced internally as a decimal fraction (Bloomberg's
    CPN percentage point divided by 100, e.g. 0.0375) -- this only changes
    how it is *displayed* to the trader (3.750%). Checks both halves: the
    Underlying Bond summary and Instrument Details show the percentage, and
    the underlying draft's own coupon field is untouched."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(bond_master={"coupon": raw_coupon}),
    )

    assert page.inner_text("#resolved-bond-coupon") == expected_percent
    assert page.inner_text("#details-coupon") == expected_percent

    draft_coupon = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0].coupon"
    )
    assert draft_coupon == raw_coupon


@_PLAYWRIGHT_SKIP
def test_null_coupon_still_shows_not_available_not_a_blank_percentage(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)  # default response: bond_master.coupon is None

    assert page.inner_text("#resolved-bond-coupon") == "Not available"
    assert page.inner_text("#details-coupon") == "Not available"

    draft_coupon = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0].coupon"
    )
    assert draft_coupon is None


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
        "details-bloomberg-day-count",
        "details-bloomberg-maturity-type",
        "details-bloomberg-calc-type",
    ):
        assert page.inner_text(f"#{details_id}") == "Not available"
    # Identity fields Bloomberg genuinely returned are never blanked out.
    assert page.inner_text("#details-issuer") == "UNITED STATES TREAS NTS"
    assert page.inner_text("#details-isin") == "US91282CLJ89"
    assert page.inner_text("#details-cusip") == "91282CLJ8"
    assert page.inner_text("#details-source") == "BLOOMBERG_DAPI"


@_PLAYWRIGHT_SKIP
def test_bond_master_raw_fields_render_display_only_and_never_enter_typed_schema(
    server_url, page
) -> None:
    """Bloomberg's raw Day Count/Maturity Type/Calculation Type mnemonics are
    confirmed to return a value but must never be coerced into the typed
    ``details-day-count``/``details-bond-type``/``details-yield-convention``
    fields (e.g. "ACT/ACT" must never become "ACT_ACT_ISDA") -- they render
    only in their own "Bloomberg ..." labeled rows."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(
            bond_master_raw={
                "day_count": "ACT/ACT",
                "maturity_type": "AT MATURITY",
                "calc_type": "STREET CONVENTION",
            }
        ),
    )

    assert page.inner_text("#details-bloomberg-day-count") == "ACT/ACT"
    assert page.inner_text("#details-bloomberg-maturity-type") == "AT MATURITY"
    assert page.inner_text("#details-bloomberg-calc-type") == "STREET CONVENTION"
    # The typed schema fields stay honestly "Not available" -- never
    # auto-converted from the raw Bloomberg description strings above.
    assert page.inner_text("#details-day-count") == "Not available"
    assert page.inner_text("#details-bond-type") == "Not available"
    assert page.inner_text("#details-yield-convention") == "Not available"


@_PLAYWRIGHT_SKIP
def test_new_lookup_fully_replaces_the_prior_bonds_bond_master(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(bond_master={"coupon": 0.04125}),
    )
    assert page.inner_text("#details-coupon") == "4.125%"

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
        response=_default_bloomberg_bond_lookup_response(bond_master={"coupon": 0.04125}),
    )
    assert page.inner_text("#details-coupon") == "4.125%"

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
                        name="SECOND LOOKUP BOND", bond_master={"coupon": 0.025}
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
    assert page.inner_text("#details-coupon") == "2.500%"

    # Only now release the first (stale) lookup's response -- it must not
    # overwrite the newer draft's Bond Master or identity.
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
    assert page.inner_text("#details-coupon") == "2.500%"


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


# --- Collapsible sections (PR #141 third revision) ---------------------------
#
# Each of the six main sections has a whole-header-row toggle that shows/
# hides only its own body element -- no data refetch, no form clearing, no
# draft/pricing-result mutation. `getComputedStyle(...).display` is checked
# (not just the `hidden` IDL property) because a body element whose own CSS
# sets an explicit `display` (e.g. `.id-grid`'s `display: grid`) can tie the
# UA stylesheet's `[hidden] { display: none }` rule in specificity and,
# being an author rule, win -- silently defeating `el.hidden = true` even
# though the property itself reads `true`. This exact bug was already fixed
# once for `.instrument-details`/`.workspace`/`.instr-header`; this section
# proves it doesn't recur for the newly collapsible `.id-grid` (Instrument
# Details' body) or the plain new `.card-body` wrappers.

_COLLAPSIBLE_SECTIONS = {
    "underlying-bond": (
        "underlying-bond-head",
        "underlying-bond-body",
        "underlying-bond-indicator",
    ),
    "option-terms": ("option-terms-head", "option-terms-body", "option-terms-indicator"),
    "pricing-results": (
        "pricing-results-head",
        "pricing-results-body",
        "pricing-results-indicator",
    ),
    "forward-carry": ("forward-carry-head", "forward-carry-body", "forward-carry-indicator"),
    "underlying-snapshot": (
        "underlying-snapshot-head",
        "underlying-snapshot-body",
        "underlying-snapshot-indicator",
    ),
    "instrument-details": ("bond-master-head", "bond-master-body", "bond-master-toggle-btn"),
    # The three manual-completion sections (Issue #143).
    "timing": ("timing-head", "timing-body", "timing-indicator"),
    "bond-ref": ("bond-ref-head", "bond-ref-body", "bond-ref-indicator"),
    "curve": ("curve-head", "curve-body", "curve-indicator"),
}


def _is_actually_hidden(page, element_id: str) -> bool:
    """True only if the element is genuinely not rendered (computed
    `display: none`), never merely the `hidden` IDL property -- the whole
    point of this check is to catch the CSS-specificity bug described above,
    which leaves `hidden` reading `true` on an element still visibly
    rendered on screen."""

    return (
        page.eval_on_selector(f"#{element_id}", "el => getComputedStyle(el).display") == "none"
    )


@_PLAYWRIGHT_SKIP
def test_default_collapse_states_before_any_lookup(server_url, page) -> None:
    page.goto(f"{server_url}/")

    # Primary trader decisions are expanded by default.
    for body_id in (
        "underlying-bond-body",
        "option-terms-body",
        "pricing-results-body",
        "forward-carry-body",
        "curve-body",
    ):
        assert not _is_actually_hidden(page, body_id)
        assert not page.eval_on_selector(f"#{body_id}", "el => el.hidden")

    # Advanced/uncommon fields and detail-only sections are collapsed.
    for body_id, indicator_id in (
        ("underlying-snapshot-body", "underlying-snapshot-indicator"),
        ("bond-master-body", "bond-master-toggle-btn"),
        ("timing-body", "timing-indicator"),
        ("bond-ref-body", "bond-ref-indicator"),
    ):
        assert page.eval_on_selector(f"#{body_id}", "el => el.hidden")
        assert _is_actually_hidden(page, body_id)
        assert page.inner_text(f"#{indicator_id}") == "Expand"

    for indicator_id in (
        "underlying-bond-indicator",
        "option-terms-indicator",
        "pricing-results-indicator",
        "forward-carry-indicator",
        "curve-indicator",
    ):
        assert page.inner_text(f"#{indicator_id}") == "Collapse"

    assert page.inner_text("#forward-carry-summary") == ""


@_PLAYWRIGHT_SKIP
def test_instrument_details_collapsed_by_default_after_a_lookup(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    assert page.eval_on_selector("#bond-master-body", "el => el.hidden")
    assert _is_actually_hidden(page, "bond-master-body")
    assert page.inner_text("#bond-master-toggle-btn") == "Expand"
    assert page.inner_text("#bond-master-summary") == "Bloomberg DAPI"


@pytest.mark.parametrize("section", list(_COLLAPSIBLE_SECTIONS))
@_PLAYWRIGHT_SKIP
def test_clicking_the_header_row_toggles_the_section(server_url, page, section) -> None:
    head_id, body_id, indicator_id = _COLLAPSIBLE_SECTIONS[section]
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)  # so Instrument Details is present to toggle too

    was_hidden = page.eval_on_selector(f"#{body_id}", "el => el.hidden")

    page.click(f"#{head_id}")

    assert page.eval_on_selector(f"#{body_id}", "el => el.hidden") != was_hidden
    assert _is_actually_hidden(page, body_id) == (not was_hidden)
    expected_indicator = "Expand" if not was_hidden else "Collapse"
    assert page.inner_text(f"#{indicator_id}") == expected_indicator

    # Toggling back restores the original state exactly.
    page.click(f"#{head_id}")
    assert page.eval_on_selector(f"#{body_id}", "el => el.hidden") == was_hidden
    assert _is_actually_hidden(page, body_id) == was_hidden


@_PLAYWRIGHT_SKIP
def test_collapse_expand_never_triggers_a_lookup_price_or_clear(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)
    page.fill("#strike-price-input", "99.5")

    bloomberg_calls = []
    case_calls = []
    page.route("**/api/bloomberg/bond", lambda route: bloomberg_calls.append(route))
    page.route("**/api/case", lambda route: case_calls.append(route))

    for head_id, _body_id, _indicator_id in _COLLAPSIBLE_SECTIONS.values():
        page.click(f"#{head_id}")

    page.wait_for_timeout(200)

    assert bloomberg_calls == []
    assert case_calls == []
    # Neither the resolved bond nor the trader's own input was disturbed.
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.input_value("#strike-price-input") == "99.5"


@_PLAYWRIGHT_SKIP
def test_values_survive_a_collapse_expand_round_trip(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_default_bloomberg_bond_lookup_response(bond_master={"coupon": 0.04125}),
    )
    page.click('#option-type-toggle .opt[data-value="PUT"]')
    page.click('#position-toggle .opt[data-value="SELL"]')
    page.fill("#strike-price-input", "101.25")
    page.fill("#notional-input", "2500000")
    page.fill("#volatility-input", "0.22")
    page.fill("#forward-price-input", "100.75")

    # Collapse every section, then expand every section again.
    for head_id, _body_id, _indicator_id in _COLLAPSIBLE_SECTIONS.values():
        page.click(f"#{head_id}")
    for head_id, _body_id, _indicator_id in _COLLAPSIBLE_SECTIONS.values():
        page.click(f"#{head_id}")

    assert page.query_selector('#option-type-toggle .opt[data-value="PUT"].on') is not None
    assert page.query_selector('#position-toggle .opt[data-value="SELL"].on') is not None
    assert page.input_value("#strike-price-input") == "101.25"
    assert page.input_value("#notional-input") == "2500000"
    assert page.input_value("#volatility-input") == "0.22"
    assert page.input_value("#forward-price-input") == "100.75"
    assert page.inner_text("#resolved-bond-isin") == "US91282CLJ89"
    assert page.inner_text("#details-coupon") == "4.125%"


@_PLAYWRIGHT_SKIP
def test_clear_restores_default_collapse_states(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page)

    # Leave every section in the opposite of its default state.
    for head_id, _body_id, _indicator_id in _COLLAPSIBLE_SECTIONS.values():
        page.click(f"#{head_id}")

    page.click("#clear-btn")
    page.wait_for_timeout(150)

    for body_id in (
        "underlying-bond-body",
        "option-terms-body",
        "pricing-results-body",
        "forward-carry-body",
        "curve-body",
    ):
        assert not page.eval_on_selector(f"#{body_id}", "el => el.hidden")
    for body_id in (
        "underlying-snapshot-body",
        "bond-master-body",
        "timing-body",
        "bond-ref-body",
    ):
        assert page.eval_on_selector(f"#{body_id}", "el => el.hidden")
    assert page.inner_text("#forward-carry-summary") == ""


# --- Manual explicit-forward completion path (Issue #143) ---------------------
#
# The trader completes a Bloomberg-loaded draft entirely in the browser: option
# dates, the eight BondReferenceData fields Bloomberg has no confirmed mnemonic
# for, the explicit forward, a direct PRICE_VOL, the timing/settlement dates,
# and Option Discount Curve nodes. No Case JSON is involved at any point.

# Eddy's real US Treasury evidence (PR #141): the seven DAPI-confirmed Bond
# Master fields, plus the three raw display-only descriptions that must never
# be auto-mapped into a typed enum.
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


def _treasury_lookup_response(**overrides) -> dict:
    payload_overrides = {
        "acquired_at": "2026-07-20T11:28:00+08:00",
        **overrides,
    }
    return _default_bloomberg_bond_lookup_response(
        bond_master=_TREASURY_BOND_MASTER,
        bond_master_raw=_TREASURY_BOND_MASTER_RAW,
        **payload_overrides,
    )


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


def _complete_draft(page, *, curve_nodes=(("1M", "0.0374"), ("1Y", "0.0374"))) -> None:
    """Fill every remaining input a Bloomberg-loaded draft still needs.

    Deliberately writes each value through the real control the trader uses,
    so this doubles as proof that every required input actually has a UI.
    """

    page.click('#option-type-toggle .opt[data-value="CALL"]')
    page.click('#position-toggle .opt[data-value="BUY"]')
    page.fill("#strike-price-input", "99.32")
    page.fill("#notional-input", "1000000")
    page.fill("#expiry-date-input", "2026-10-20")
    page.fill("#expiry-timestamp-input", "2026-10-20T17:20:00+08:00")
    page.fill("#volatility-input", "0.03395")

    page.fill("#forward-price-input", "99.234375")

    # Uncommon contract fields stay in Advanced sections. Their live header
    # counts remain visible while collapsed, and the trader expands them only
    # when needed.
    page.click("#bond-ref-head")
    # The three enum selects are the trader's own choice; Bloomberg's raw
    # DAY_CNT_DES / MTY_TYP / CALC_TYP_DES are only shown beside them.
    page.select_option("#day-count-select", "ACT_ACT_ISDA")
    page.select_option("#bond-type-select", "FIXED_COUPON_BULLET")
    page.select_option("#yield-convention-select", "SEMI_ANNUAL_COMPOUND")
    page.select_option("#business-day-convention-select", "FOLLOWING")
    page.fill("#redemption-amount-input", "100")
    page.fill("#ex-dividend-days-input", "0")
    page.fill("#last-coupon-date-input", "2030-07-31")
    page.select_option("#bond-status-select", "ACTIVE")

    page.click("#timing-head")
    page.fill("#settlement-lag-input", "1")
    page.fill("#reporting-date-input", "2026-10-21")
    page.fill("#forward-settlement-date-input", "2026-10-21")
    page.fill("#option-settlement-date-input", "2026-10-21")

    _set_curve_nodes(page, curve_nodes)


@_PLAYWRIGHT_SKIP
def test_bloomberg_loaded_bond_prices_end_to_end_without_case_json(server_url, page) -> None:
    """The headline acceptance test: load a real-shaped Bloomberg bond,
    complete it through the browser controls only, and get a genuine premium
    and Greeks out of the existing reviewed pricing path."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    assert _is_disabled(page, "#price-btn")
    _complete_draft(page)

    # Nothing is outstanding any more: no badges, no note, Price enabled.
    assert _missing_category_badges(page) == []
    assert page.eval_on_selector("#draft-incomplete-note", "el => el.hidden")
    assert not _is_disabled(page, "#price-btn")

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    # A real number from the reviewed engine, not a placeholder.
    price_total = page.inner_text("#price-total")
    assert price_total not in ("—", "")
    assert float(price_total) > 0
    assert float(page.inner_text("#price-per-100")) > 0
    for greek in ("#greek-delta", "#greek-gamma", "#greek-vega", "#greek-theta"):
        assert page.inner_text(greek) not in ("—", "")
    assert page.inner_text("#result-currency") == "USD"
    # Provenance and export both become available on a real priced run.
    assert page.inner_text("#instr-isin") == "US91282CLJ89"
    assert not _is_disabled(page, "#download-json-btn")
    assert not _is_disabled(page, "#download-markdown-btn")


@_PLAYWRIGHT_SKIP
def test_local_offset_timestamps_are_accepted_and_dates_stay_explicit(
    server_url, page
) -> None:
    """`+08:00` timestamps are accepted; the preview is transparency only and
    labels which single field is actually normalized."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)

    # Only as_of is respelled in UTC from the acquisition event...
    assert page.inner_text("#as-of-timestamp-utc") == "Normalized to UTC: 2026-07-20T03:28:00Z"
    # ...the other two are sent exactly as entered, with the UTC equivalent
    # shown purely so the trader can see the instant.
    assert page.inner_text("#pricing-timestamp-utc") == (
        "Sent as entered · same instant in UTC: 2026-07-20T03:28:00Z"
    )
    assert page.inner_text("#expiry-timestamp-utc") == (
        "Sent as entered · same instant in UTC: 2026-10-20T09:20:00Z"
    )

    # The acquisition event's pricing spelling is preserved verbatim; expiry
    # remains the trader's explicit timestamp.
    assert page.input_value("#pricing-timestamp-input") == "2026-07-20T11:28:00+08:00"
    assert page.input_value("#expiry-timestamp-input") == "2026-10-20T17:20:00+08:00"
    # ... and in the draft that will actually be sent. As-of is already
    # normalized in the mechanically populated draft.
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["pricing_timestamp"] == "2026-07-20T11:28:00+08:00"
    assert draft["expiry_timestamp"] == "2026-10-20T17:20:00+08:00"
    assert draft["as_of_timestamp"] == "2026-07-20T03:28:00Z"

    # Valuation is the acquisition timestamp's local date. Other calendar
    # dates remain independent explicit inputs.
    assert page.input_value("#valuation-date-input") == "2026-07-20"
    assert page.input_value("#reporting-date-input") == "2026-10-21"
    assert page.input_value("#option-settlement-date-input") == "2026-10-21"

    # And the whole thing prices, which is what the #142 D2 defect prevented:
    # `as_of_timestamp` rejected every non-UTC offset while the other two
    # required an explicit offset.
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert float(page.inner_text("#price-total")) > 0


@_PLAYWRIGHT_SKIP
def test_local_times_whose_utc_date_differs_still_match_their_explicit_dates(
    server_url, page
) -> None:
    """The timing contract compares the *represented local* calendar date.

    An 05:20 +08:00 expiry is the previous day in UTC, but it belongs to the
    local expiry date the trader entered -- so it must price. Normalizing
    these two instants to UTC would wrongly reject this.
    """

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(acquired_at="2026-07-20T05:00:00+08:00"),
    )
    _complete_draft(page)

    # Both are early-morning local times whose UTC date is the day before.
    page.fill("#expiry-timestamp-input", "2026-10-20T05:20:00+08:00")
    page.wait_for_timeout(150)

    assert page.inner_text("#expiry-timestamp-utc") == (
        "Sent as entered · same instant in UTC: 2026-10-19T21:20:00Z"
    )
    # The explicit expiry date is still the local one, and is not adjusted.
    assert page.input_value("#expiry-date-input") == "2026-10-20"

    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    assert float(page.inner_text("#price-total")) > 0


@_PLAYWRIGHT_SKIP
def test_malformed_timestamp_is_flagged_rather_than_silently_repaired(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(
        page,
        response=_treasury_lookup_response(
            acquired_at="2026-07-20 11:28:00"  # no offset, space separator
        ),
    )
    page.wait_for_timeout(120)

    preview = page.inner_text("#pricing-timestamp-utc")
    assert "explicit offset" in preview
    assert "same instant in UTC" not in preview
    assert page.eval_on_selector(
        "#pricing-timestamp-utc", "el => el.classList.contains('is-invalid')"
    )


@_PLAYWRIGHT_SKIP
def test_credit_spread_not_required_needs_no_spread_value_or_basis(server_url, page) -> None:
    """#142 defect D1: the workbench demanded `credit_spread` and
    `credit_spread_basis` unconditionally, but `BLICreditSpreadInput` forbids
    both for NOT_REQUIRED and instead requires the audit explanation."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    labels = _missing_fields_text(page)
    assert not any("Credit Spread" in item for item in labels)

    credit = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().credit_spread_input"
    )
    assert credit["spread_treatment"] == "NOT_REQUIRED"
    # The contract forbids these two for NOT_REQUIRED -- no fabricated number.
    assert credit["credit_spread"] is None
    assert credit["credit_spread_basis"] is None
    # ... and requires a non-blank audit explanation, which #142 found missing
    # from the checklist entirely.
    assert credit["override_or_fallback_audit"]
    assert "never reads credit_spread" in credit["override_or_fallback_audit"]


@_PLAYWRIGHT_SKIP
def test_credit_spread_conditional_rules_track_the_treatment(server_url, page) -> None:
    """The checklist applies the real conditional contract rules, in both
    directions, for every treatment."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    def missing_credit_labels(treatment, **fields):
        return page.evaluate(
            """([treatment, fields]) => {
                const draft = window.__shioriTestGetCurrentDraft();
                draft.credit_spread_input = Object.assign(
                    { spread_treatment: treatment, source_system: "S", status: "ACTIVE",
                      credit_spread: null, credit_spread_basis: null,
                      override_or_fallback_audit: null },
                    fields
                );
                return window.__shioriTestComputeMissingLabels();
            }""",
            [treatment, fields],
        )

    # OBSERVED needs a spread value and basis, and no audit.
    observed = missing_credit_labels("OBSERVED")
    assert "Credit Spread" in observed
    assert "Credit Spread Basis" in observed
    assert "Credit Spread Audit Explanation" not in observed

    # OVERRIDE needs the spread value, the basis AND the audit.
    override = missing_credit_labels("OVERRIDE")
    assert "Credit Spread" in override
    assert "Credit Spread Basis" in override
    assert "Credit Spread Audit Explanation" in override

    # EMBEDDED needs only the audit -- never a spread value or basis.
    embedded = missing_credit_labels("EMBEDDED")
    assert "Credit Spread" not in embedded
    assert "Credit Spread Basis" not in embedded
    assert "Credit Spread Audit Explanation" in embedded

    # A fully-supplied OBSERVED spread leaves nothing outstanding.
    complete = missing_credit_labels(
        "OBSERVED", credit_spread=0.0125, credit_spread_basis="BPS_OVER_CURVE"
    )
    assert not any(label.startswith("Credit Spread") for label in complete)


@_PLAYWRIGHT_SKIP
def test_curve_needs_at_least_two_valid_nodes(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

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
    flat-extrapolating, so an uncovered date is a hard stop -- and the trader
    is told exactly which date, which coordinate, and which range."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    # 1M/2M cannot reach an option settling ~3 months out.
    _complete_draft(page, curve_nodes=(("1M", "0.0374"), ("2M", "0.0374")))

    coverage = page.inner_text("#curve-coverage")
    assert "Option Settlement Date (2026-10-21)" in coverage
    assert "0.2548 years" in coverage
    assert "[0.0833, 0.1667] years" in coverage
    assert "never extrapolated" in coverage
    assert page.eval_on_selector(
        "#curve-coverage", "el => el.classList.contains('is-blocking')"
    )

    assert _is_disabled(page, "#price-btn")
    assert "Market curves unavailable (1)" in page.inner_text("#missing-categories")

    # Extending the curve past the required coordinate unblocks pricing.
    _set_curve_nodes(page, [("1M", "0.0374"), ("1Y", "0.0374")])
    assert not _is_disabled(page, "#price-btn")
    assert page.eval_on_selector(
        "#curve-coverage", "el => el.classList.contains('is-covered')"
    )


@_PLAYWRIGHT_SKIP
def test_bloomberg_raw_descriptions_are_hints_and_never_enter_the_typed_draft(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())

    # Shown beside the enum the trader must pick.
    assert page.inner_text("#hint-day-count") == "ACT/ACT"
    assert page.inner_text("#hint-bond-type") == "AT MATURITY"
    assert page.inner_text("#hint-yield-convention") == "STREET CONVENTION"

    # But nothing was auto-selected from them, and the draft stays null.
    assert page.input_value("#day-count-select") == ""
    assert page.input_value("#bond-type-select") == ""
    assert page.input_value("#yield-convention-select") == ""
    reference = page.evaluate(
        "() => window.__shioriTestGetCurrentDraft().bond_reference_data_universe[0]"
    )
    assert reference["day_count"] is None
    assert reference["bond_type"] is None
    assert reference["yield_convention"] is None


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
    # The contract requires the forward's side to equal the spot side, so it
    # is mirrored rather than asked for twice.
    assert draft["forward_clean_price_input"]["quote_side"] == draft["bond_quote"]["quote_side"]
    # Direct price vol only; YIELD_VOL is not offered anywhere.
    assert draft["volatility_input"]["volatility_basis"] == "PRICE_VOL"
    assert page.eval_on_selector_all(
        "#volatility-basis-select option", "els => els.map(e => e.value)"
    ) == ["PRICE_VOL", "EQUIVALENT_PRICE_VOL"]
    # Generated identifiers, provenance and statuses remain inspectable in
    # the collapsed Advanced metadata section.
    assert page.inner_text("#timing-product-id") == draft["bond_option"]["product_id"]
    assert page.inner_text("#timing-snapshot-id") == draft["snapshot_id"]
    assert page.inner_text("#timing-snapshot-metadata") == "SHIORI_MANUAL_WORKBENCH · ACTIVE"
    assert page.inner_text("#timing-bond-quote-metadata") == "BLOOMBERG_DAPI · ACTIVE"
    assert "NOT_REQUIRED" in page.inner_text("#timing-credit-metadata")
    # Timing values already present in the Bloomberg acquisition event are
    # populated mechanically; genuinely unknown expiry remains null.
    assert draft["valuation_date"] == "2026-07-20"
    assert draft["pricing_timestamp"] == "2026-07-20T11:28:00+08:00"
    assert draft["as_of_timestamp"] == "2026-07-20T03:28:00Z"
    assert draft["bond_option"]["expiry_date"] is None


@_PLAYWRIGHT_SKIP
def test_a_second_bond_lookup_discards_every_manual_input_from_the_first(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    assert not _is_disabled(page, "#price-btn")
    first_draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")

    _load_bloomberg_bond(
        page,
        identifier="XS9999999999",
        response=_treasury_lookup_response(
            isin="XS9999999999",
            cusip="999999999",
            name="SECOND RESOLVED BOND",
            acquired_at="2026-07-21T00:30:00+08:00",
        ),
    )

    for selector in (
        "#strike-price-input",
        "#notional-input",
        "#expiry-date-input",
        "#settlement-lag-input",
        "#volatility-input",
        "#forward-price-input",
        "#reporting-date-input",
        "#forward-settlement-date-input",
        "#option-settlement-date-input",
        "#expiry-timestamp-input",
        "#redemption-amount-input",
        "#ex-dividend-days-input",
        "#last-coupon-date-input",
        "#day-count-select",
        "#bond-type-select",
        "#yield-convention-select",
        "#business-day-convention-select",
        "#bond-status-select",
    ):
        assert page.input_value(selector) == "", f"{selector} kept the prior bond's value"
    assert page.query_selector("#option-type-toggle .opt.on") is None
    assert page.query_selector("#position-toggle .opt.on") is None
    assert page.eval_on_selector_all(
        ".curve-row .curve-tenor-input", "els => els.map(e => e.value)"
    ) == ["", ""]
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft().curve_points") == []
    second_draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert second_draft["pricing_timestamp"] == "2026-07-21T00:30:00+08:00"
    assert second_draft["valuation_date"] == "2026-07-21"
    assert second_draft["as_of_timestamp"] == "2026-07-20T16:30:00Z"
    assert second_draft["bond_option"]["product_id"] != first_draft["bond_option"]["product_id"]
    assert second_draft["snapshot_id"] != first_draft["snapshot_id"]
    assert page.input_value("#pricing-timestamp-input") == second_draft["pricing_timestamp"]
    assert page.input_value("#valuation-date-input") == second_draft["valuation_date"]
    assert page.input_value("#as-of-timestamp-input") == second_draft["as_of_timestamp"]
    assert _is_disabled(page, "#price-btn")


@_PLAYWRIGHT_SKIP
def test_clear_removes_every_manual_input_and_restores_the_empty_state(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response())
    _complete_draft(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    page.click("#clear-btn")
    page.wait_for_timeout(200)

    assert page.inner_text("#status-text") == "No bond loaded"
    assert page.eval_on_selector("#workspace-section", "el => el.hidden")
    assert page.evaluate("() => window.__shioriTestGetCurrentDraft()") is None
    for selector in (
        "#strike-price-input",
        "#valuation-date-input",
        "#as-of-timestamp-input",
        "#pricing-timestamp-input",
        "#redemption-amount-input",
        "#day-count-select",
        "#bond-status-select",
    ):
        assert page.input_value(selector) == ""
    assert page.inner_text("#timing-product-id") == "—"
    assert page.inner_text("#timing-snapshot-id") == "—"
    # Volatility basis returns to its default rather than to blank.
    assert page.input_value("#volatility-basis-select") == "PRICE_VOL"
    assert page.eval_on_selector_all(
        ".curve-row .curve-tenor-input", "els => els.map(e => e.value)"
    ) == ["", ""]
    assert _is_disabled(page, "#price-btn")
    assert _is_disabled(page, "#download-json-btn")
