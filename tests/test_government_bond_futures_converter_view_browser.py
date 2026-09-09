"""Browser-driven regression tests for the Government Bond Futures Converter
window (Issue #206).

Exercises ``prototype/government-bond-futures-converter/app.js`` end to end
against one real ``ThreadingHTTPServer`` and one real headless Chromium page:
the startup flow, the automatic CTD load on contract selection, both
conversion directions, the manual/unconfirmed fallback, and the
Bloomberg-unavailable Retry state.

Two different fakes, deliberately:

- Conversions let the **real routes** answer, and the rendered text is checked
  against the same canonical Python functions. That is the whole point of the
  canonical-path requirement -- the number on the screen has to have come from
  the pricing library.
- The Bloomberg CTD route is intercepted at the network layer *only* where the
  test is about what the window does with an answer (that it asked at all,
  that a failure becomes a readable Retry). Interception is what proves the
  window calls the existing route rather than some path of its own.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test files: locally, missing Playwright is a skip; in CI
(``CI=true``) it is a hard collection-time error.

The CTD typed in below is an arbitrary test input, never real market data.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from collections.abc import Iterator

import pytest

from shiori_pricing_lab.app.government_bond_futures_converter_server import create_server
from shiori_pricing_lab.data.treasury_futures_ctd import treasury_futures_ctd_from_manual_entry
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    MARKET_EUREX_GERMAN,
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
    METHODOLOGY_NOTE_BY_MARKET,
    futures_price_from_target_yield,
    implied_yield_from_futures_price,
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

CTD_ENTRY = {
    "contract_code": "ZN",
    "contract_symbol": "TYZ6",
    "ctd_identifier": "US91282CTEST",
    "ctd_coupon_percent": 4.25,
    "ctd_maturity_date": "2034-05-15",
    "conversion_factor": 0.8012,
    "last_delivery_date": "2026-12-31",
    "as_of": "2026-08-25T14:00:00Z",
}

#: The pinned FGBS record with its irregular first coupon period, as the
#: automatic path would deliver it.
FGBS_CTD_PAYLOAD = {
    "contract_code": "FGBS",
    "contract_symbol": "DUZ6",
    "ctd_identifier": "DE000BU22148",
    "ctd_cusip": "DM9594068",
    "ctd_description": "BKO 2.7 09/13/28",
    "ctd_coupon_percent": 2.7,
    "ctd_maturity_date": "2028-09-13",
    "conversion_factor": 0.946091,
    "last_delivery_date": "2026-12-10",
    "first_accrual_start": "2026-07-16",
    "first_coupon_date": "2027-09-13",
    "source": "BLOOMBERG_DAPI",
    "as_of": "2026-09-08T00:00:00Z",
    "is_confirmed_source": True,
}

MANUAL_FIELD_IDS = {
    "contract_symbol": "m-contract-symbol",
    "ctd_identifier": "m-ctd-identifier",
    "ctd_coupon_percent": "m-ctd-coupon",
    "ctd_maturity_date": "m-ctd-maturity",
    "conversion_factor": "m-conversion-factor",
    "last_delivery_date": "m-last-delivery",
    "first_accrual_start": "m-first-accrual-start",
    "first_coupon_date": "m-first-coupon-date",
    "as_of": "m-as-of",
}


def _wait_until(page, predicate, timeout: float = 20.0, interval_ms: int = 20) -> None:
    """Poll ``predicate`` while letting Playwright's own event loop run.

    ``page.wait_for_timeout`` rather than ``time.sleep``: the sync API drives
    everything from this thread, so a plain sleep blocks the dispatcher and
    intercepted routes are never handled -- a predicate counting requests would
    then wait out its whole timeout while the request sat queued.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(interval_ms)
    raise AssertionError(f"condition not met within {timeout}s")


@pytest.fixture()
def server_url() -> Iterator[str]:
    server = create_server()
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
        pg = browser.new_page(viewport={"width": 1180, "height": 900})
        yield pg
        browser.close()


class _CtdRoute:
    """Stands in for the Bloomberg CTD route and records what was asked of it."""

    def __init__(self, page, *, payload=None, error=None, status=200):
        self.requests: list[dict] = []
        self.payload = payload
        self.error = error
        self.status = status
        page.route("**/api/treasury-futures/ctd", self._fulfil)

    def _fulfil(self, route, request):
        self.requests.append(json.loads(request.post_data or "{}"))
        body = {"error": self.error} if self.error else self.payload
        route.fulfill(
            status=self.status,
            content_type="application/json",
            body=json.dumps(body),
        )


def _open(page, server_url: str):
    """Open the app and wait for the contract list to have rendered."""

    page.goto(server_url)
    expected = len(SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES)
    _wait_until(
        page,
        lambda: page.eval_on_selector("#contract-select", "el => el.options.length") == expected,
    )
    return page


def _fill_manual_ctd(page, entry=None) -> None:
    entry = CTD_ENTRY if entry is None else entry
    page.select_option("#contract-select", entry["contract_code"])
    # Manual entry is the secondary mode, so its fields sit inside a closed
    # <details>. A trader opens it to use them, and so must this test.
    page.eval_on_selector("#advanced", "el => { el.open = true; }")
    for key, element_id in MANUAL_FIELD_IDS.items():
        if key in entry:
            page.fill(f"#{element_id}", str(entry[key]))


def _text(page, element_id: str) -> str:
    return page.eval_on_selector(f"#{element_id}", "el => el.textContent")


# ---------------------------------------------------------------------------
# Startup and the contract list
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_the_window_offers_every_registry_contract_grouped_by_market(page, server_url) -> None:
    _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    codes = page.eval_on_selector_all("#contract-select option", "els => els.map(e => e.value)")
    assert tuple(codes) == SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES
    groups = page.eval_on_selector_all("#contract-select optgroup", "els => els.map(e => e.label)")
    # Grouped from the server's own market labels; the page never names a market.
    assert len(groups) == 2
    assert all(label for label in groups)


@_PLAYWRIGHT_SKIP
def test_the_window_shows_no_internal_branding(page, server_url) -> None:
    """The trader's normal screen never names the repository or the product.

    Checked on the *rendered* page, not on the static files: the contract
    catalogue supplies its own copy, so a server-side string can put branding
    on screen that a file scan would never see.
    """

    route = _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    page.select_option("#contract-select", "FGBS")
    _wait_until(page, lambda: len(route.requests) >= 2)
    _wait_until(page, lambda: "BKO 2.7 09/13/28" in _text(page, "d-ctd"))

    assert page.title() == "Government Bond Futures Converter"
    visible = page.eval_on_selector("body", "el => el.innerText")
    assert "Shiori" not in visible
    assert "shiori" not in visible.lower()


@_PLAYWRIGHT_SKIP
def test_the_methodology_attribution_lives_under_details(page, server_url) -> None:
    """The German methodology note names its owner, and that stays off the desk screen.

    ``METHODOLOGY_NOTE_BY_MARKET`` deliberately attributes the German
    calculation to its owner rather than to Eurex (PR #205), so the sentence
    carries a name Issue #206 keeps off the trader's normal screen. Both
    requirements are met by putting the note under Details, verbatim: the
    attribution is never rewritten here, and it is never on the primary
    screen. Rewriting that sentence would be a methodology-attribution
    change, not a packaging one.
    """

    route = _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    page.select_option("#contract-select", "FGBS")
    _wait_until(page, lambda: len(route.requests) >= 2)

    note = _text(page, "methodology")
    assert note == METHODOLOGY_NOTE_BY_MARKET[MARKET_EUREX_GERMAN]
    # Present in the DOM, but not on screen until the trader opens Details.
    assert page.is_visible("#methodology") is False
    assert page.eval_on_selector("#advanced", "el => el.open") is False
    # It lives inside the disclosure, not merely next to it.
    assert page.eval_on_selector("#methodology", "el => !!el.closest('#advanced')") is True


# ---------------------------------------------------------------------------
# Selecting a contract loads its CTD automatically
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_the_first_contract_loads_its_ctd_without_being_touched(page, server_url) -> None:
    route = _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    # No click, no selection: opening the window is the whole trader action.
    _wait_until(page, lambda: len(route.requests) == 1)
    assert route.requests[0]["contract_code"] == SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES[0]


@_PLAYWRIGHT_SKIP
def test_selecting_a_contract_triggers_the_existing_bloomberg_ctd_route(page, server_url) -> None:
    route = _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    _wait_until(page, lambda: len(route.requests) == 1)

    page.select_option("#contract-select", "FGBS")
    _wait_until(page, lambda: len(route.requests) == 2)
    # The request carries only the contract code: the CTD is fetched
    # server-side and never round-trips through the window.
    assert route.requests[1] == {"contract_code": "FGBS"}
    _wait_until(page, lambda: "BKO 2.7 09/13/28" in _text(page, "d-ctd"))
    assert _text(page, "d-identifier") == "DE000BU22148"
    assert _text(page, "d-cf") == "0.946091"
    assert "Bloomberg" in _text(page, "bloomberg-pill-text")


@_PLAYWRIGHT_SKIP
def test_an_automatic_load_fills_the_irregular_first_schedule_fields(page, server_url) -> None:
    route = _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    page.select_option("#contract-select", "FGBS")
    _wait_until(page, lambda: len(route.requests) >= 2)
    _wait_until(page, lambda: page.input_value("#m-first-accrual-start") == "2026-07-16"
    )
    assert page.input_value("#m-first-coupon-date") == "2027-09-13"


@_PLAYWRIGHT_SKIP
def test_the_load_button_is_a_retry_not_a_required_step(page, server_url) -> None:
    route = _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    _wait_until(page, lambda: len(route.requests) == 1)
    page.click("#load-ctd-btn")
    _wait_until(page, lambda: len(route.requests) == 2)
    assert route.requests[0]["contract_code"] == route.requests[1]["contract_code"]


# ---------------------------------------------------------------------------
# Bloomberg unavailable
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_bloomberg_unavailable_shows_a_readable_retry_state(page, server_url) -> None:
    _CtdRoute(
        page,
        error="Bloomberg DAPI is not reachable on localhost:8194 -- start the Terminal.",
        status=400,
    )
    _open(page, server_url)
    _wait_until(page, lambda: not page.eval_on_selector("#error-banner", "el => el.hidden"))
    detail = _text(page, "error-detail")
    assert "Bloomberg" in detail
    assert "Traceback" not in detail
    assert page.eval_on_selector("#retry-btn", "el => el.hidden") is False
    assert _text(page, "bloomberg-pill-text") == "Bloomberg unavailable"
    # The manual fields stay usable underneath, so the desk is not stopped.
    assert page.eval_on_selector("#m-ctd-identifier", "el => el.disabled") is False


@_PLAYWRIGHT_SKIP
def test_retry_asks_bloomberg_again(page, server_url) -> None:
    route = _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _wait_until(page, lambda: len(route.requests) == 1)
    _wait_until(page, lambda: not page.eval_on_selector("#error-banner", "el => el.hidden"))
    page.click("#retry-btn")
    _wait_until(page, lambda: len(route.requests) == 2)


@_PLAYWRIGHT_SKIP
def test_a_failed_load_never_leaves_a_stale_answer_on_screen(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _wait_until(page, lambda: not page.eval_on_selector("#error-banner", "el => el.hidden"))
    assert _text(page, "implied-yield") == "—"
    assert _text(page, "futures-price-out") == "—"


# ---------------------------------------------------------------------------
# Converting -- against the real routes, checked against canonical Python
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_a_ust_price_converts_to_the_yield_python_computes(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _fill_manual_ctd(page)
    page.fill("#futures-price", "112-165")
    page.click("#convert-btn")

    expected = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(dict(CTD_ENTRY)), "112-165"
    )
    wanted = f"{expected.implied_yield_percent:.4f}%"
    _wait_until(page, lambda: _text(page, "implied-yield") == wanted)
    assert "settled" in _text(page, "implied-yield-note")


@_PLAYWRIGHT_SKIP
def test_a_target_yield_converts_to_the_price_python_computes(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _fill_manual_ctd(page)
    page.fill("#target-yield", "4.20")
    page.click("#convert-btn")

    expected = futures_price_from_target_yield(
        treasury_futures_ctd_from_manual_entry(dict(CTD_ENTRY)), 4.20
    )
    _wait_until(page, lambda: _text(page, "futures-price-out") == expected.exchange_quote)


@_PLAYWRIGHT_SKIP
def test_enter_converts_without_reaching_for_the_mouse(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _fill_manual_ctd(page)
    page.fill("#futures-price", "112-165")
    page.press("#futures-price", "Enter")

    expected = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(dict(CTD_ENTRY)), "112-165"
    )
    wanted = f"{expected.implied_yield_percent:.4f}%"
    _wait_until(page, lambda: _text(page, "implied-yield") == wanted)


@_PLAYWRIGHT_SKIP
def test_the_manual_flow_prices_the_german_irregular_first_coupon(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    manual_fgbs = {key: FGBS_CTD_PAYLOAD[key] for key in MANUAL_FIELD_IDS}
    manual_fgbs["contract_code"] = "FGBS"
    _fill_manual_ctd(page, manual_fgbs)
    page.fill("#futures-price", "105.065")
    page.click("#convert-btn")

    expected = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(
            {key: FGBS_CTD_PAYLOAD[key] for key in list(MANUAL_FIELD_IDS) + ["contract_code"]}
        ),
        105.065,
    )
    # The pinned Bloomberg YAS RED answer, reached through the window.
    assert expected.implied_yield_percent == pytest.approx(3.044683, abs=1e-6)
    wanted = f"{expected.implied_yield_percent:.4f}%"
    _wait_until(page, lambda: _text(page, "implied-yield") == wanted)


@_PLAYWRIGHT_SKIP
def test_converting_with_nothing_entered_says_so_rather_than_answering(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _fill_manual_ctd(page)
    page.click("#convert-btn")
    _wait_until(page, lambda: not page.eval_on_selector("#error-banner", "el => el.hidden"))
    assert "futures price" in _text(page, "error-detail")
    assert _text(page, "implied-yield") == "—"


# ---------------------------------------------------------------------------
# The stale-answer invariant
# ---------------------------------------------------------------------------


@_PLAYWRIGHT_SKIP
def test_editing_a_ctd_field_clears_the_answer_it_produced(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _fill_manual_ctd(page)
    page.fill("#futures-price", "112-165")
    page.click("#convert-btn")
    _wait_until(page, lambda: _text(page, "implied-yield") != "—")

    page.fill("#m-conversion-factor", "0.9000")
    _wait_until(page, lambda: _text(page, "implied-yield") == "—")
    assert _text(page, "d-cf") == "—"


@_PLAYWRIGHT_SKIP
def test_retyping_a_price_keeps_the_ctd_that_is_still_current(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _fill_manual_ctd(page)
    page.fill("#futures-price", "112-165")
    page.click("#convert-btn")
    _wait_until(page, lambda: _text(page, "d-cf") == "0.8012")

    page.fill("#futures-price", "112-170")
    _wait_until(page, lambda: _text(page, "implied-yield") == "—")
    # The CTD did not change, so its readout must not be blanked.
    assert _text(page, "d-cf") == "0.8012"


@_PLAYWRIGHT_SKIP
def test_changing_contract_clears_the_previous_contracts_ctd(page, server_url) -> None:
    _CtdRoute(page, error="Bloomberg is not reachable.", status=400)
    _open(page, server_url)
    _fill_manual_ctd(page)
    page.fill("#futures-price", "112-165")
    page.click("#convert-btn")
    _wait_until(page, lambda: _text(page, "implied-yield") != "—")

    page.select_option("#contract-select", "ZB")
    _wait_until(page, lambda: page.input_value("#m-conversion-factor") == "")
    assert _text(page, "implied-yield") == "—"
    assert _text(page, "d-cf") == "—"


@_PLAYWRIGHT_SKIP
def test_editing_a_loaded_ctd_drops_the_conversion_back_to_unconfirmed(page, server_url) -> None:
    route = _CtdRoute(page, payload=FGBS_CTD_PAYLOAD)
    _open(page, server_url)
    page.select_option("#contract-select", "FGBS")
    _wait_until(page, lambda: len(route.requests) >= 2)
    _wait_until(page, lambda: page.evaluate("window.__converterTestCtdSourceMode()") == "BLOOMBERG")

    # Editing a loaded CTD means opening the Details disclosure first, exactly
    # as a trader would.
    page.eval_on_selector("#advanced", "el => { el.open = true; }")
    page.fill("#m-conversion-factor", "0.9000")
    assert page.evaluate("window.__converterTestCtdSourceMode()") == "MANUAL"
    assert "Manual" in _text(page, "bloomberg-pill-text")
