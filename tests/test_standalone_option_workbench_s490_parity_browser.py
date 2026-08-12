"""Browser-driven regression tests for the S490 repo-carry Forward parity
panel (Issue #173/#174).

Exercises script.js's own wiring -- the panel's readiness gate, that
changing Expiry alone (with no button click) recomputes it, that it
recomputes for a fresh Spot Settlement Date, and that Clear resets both the
input and the display -- against one real ``ThreadingHTTPServer`` (see
``standalone_option_workbench_server``) and one real headless Chromium page
via Playwright. ``POST /api/bloomberg/bond`` is intercepted at the browser
network layer with ``page.route``, exactly like the sibling prototype
browser-test file; ``POST /api/case/s490-repo-carry`` itself is real and
unmocked -- it reaches the real, already-reviewed
``resolve_s490_repo_carry_parity`` -- with only the Bloomberg Curve #490
loader monkeypatched Python-side (no ``blpapi``/terminal available here),
the same seam ``test_standalone_option_workbench_server.py`` already uses.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test files: locally, missing Playwright is a skip; in CI
(``CI=true``) it is a hard collection-time error.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shiori_pricing_lab.app import standalone_option_workbench_server as server_module
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bloomberg_option_discount_curve import (
    BloombergUsdSofrOptionDiscountCurveResult,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available

# Reuses the sibling browser-test file's own Bloomberg lookup fixture/driving
# helpers unchanged, rather than a second copy of the same bond-lookup mock
# and form-filling logic. Both files live in the same directory with no
# package `__init__.py`, so pytest's default rootless import mode already
# puts this directory on `sys.path` -- this mirrors that, defensively, so the
# import works the same way whether this file is collected on its own or as
# part of the whole suite.
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from test_standalone_option_workbench_prototype_browser import (  # noqa: E402
    _complete_draft,
    _load_bloomberg_bond,
    _treasury_lookup_response,
    _wait_for_price_enabled,
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
_QUANTLIB_SKIP = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

if _PLAYWRIGHT_AVAILABLE:
    from playwright.sync_api import sync_playwright

_CHROMIUM_EXECUTABLE_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")

pytestmark = [_PLAYWRIGHT_SKIP, _QUANTLIB_SKIP]


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


# Fixed "now" so every fake curve node's maturity and the same-as-of RED gate
# (Issue #171) both stay coherent across a whole test, regardless of the real
# wall clock.
_CLOCK_NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)


def _fake_curve_result(base_date=None) -> BloombergUsdSofrOptionDiscountCurveResult:
    base_date = base_date if base_date is not None else _CLOCK_NOW.date()
    def point(tenor: str, days: int, rate: float) -> BLICurvePoint:
        return BLICurvePoint(
            curve_id="USD_SOFR_OPTION_DISCOUNT_CURVE",
            curve_name="USD SOFR Option Discount Curve (Bloomberg Curve #490)",
            currency="USD",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
            source_system="BLOOMBERG_DAPI",
            status=BLIMarketDataStatus.ACTIVE,
            maturity_date=(base_date + timedelta(days=days)).isoformat(),
        )

    return BloombergUsdSofrOptionDiscountCurveResult(
        curve_points=(
            point("1W", 7, 0.038),
            point("1M", 31, 0.0378),
            point("3M", 92, 0.0372),
            point("6M", 184, 0.0364),
            point("1Y", 365, 0.035),
        ),
        discount_factor_evidence=(),
    )


@pytest.fixture()
def server_url(monkeypatch) -> Iterator[str]:
    # Direct module-attribute monkeypatching (not page.route): this route
    # goes through the real, unmodified production wiring
    # (inject_live_option_discount_curve_if_absent), and only the actual
    # Bloomberg DAPI call at the bottom of that chain needs faking -- exactly
    # the seam test_standalone_option_workbench_server.py's own
    # `_install_fake_live_curve_loader` uses.
    monkeypatch.setattr(
        server_module,
        "load_bloomberg_usd_sofr_option_discount_curve",
        lambda tenors=None: _fake_curve_result(),
    )
    monkeypatch.setattr(server_module, "_shiori_acquisition_now", lambda: _CLOCK_NOW)

    server = server_module.create_server(host="127.0.0.1", port=0)
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


def _load_and_complete_ust(page, server_url: str) -> None:
    """One real bond, loaded and completed through the real controls.

    ``acquired_at`` is pinned to the same fixed clock the curve loader/RED
    gate use, so `valuation_date` (derived from the acquisition instant)
    matches the live curve's own as-of date -- the same invariant
    ``_install_fixed_curve_clock`` enforces for the Python-level route
    tests.
    """

    page.goto(f"{server_url}/")
    response = _treasury_lookup_response(acquired_at="2026-08-12T20:00:00+08:00")
    _load_bloomberg_bond(page, response=response)
    # The default expiry/settlement dates _complete_draft fills (Expiry
    # 2026-10-20, forward/option settlement 2026-10-21) all fall safely
    # after this fixture's valuation_date (2026-08-12) and within the fake
    # curve's own 1Y node range.
    _complete_draft(page)
    _wait_for_price_enabled(page)


_SPOT_SETTLEMENT_DATE = "2026-08-13"


# --- Readiness gate -----------------------------------------------------------


def test_panel_shows_a_resting_hint_before_a_bond_is_loaded(server_url, page) -> None:
    page.goto(f"{server_url}/")
    assert page.text_content("#s490-parity-status") != ""
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")


def test_panel_asks_for_the_ticket_to_be_completed_before_a_spot_date_helps(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    response = _treasury_lookup_response(acquired_at="2026-08-12T20:00:00+08:00")
    _load_bloomberg_bond(page, response=response)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    assert "Complete the ticket" in page.text_content("#s490-parity-status")
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")


def test_panel_asks_for_a_spot_settlement_date_once_the_ticket_is_complete(
    server_url, page
) -> None:
    _load_and_complete_ust(page, server_url)
    assert "Spot Settlement Date" in page.text_content("#s490-parity-status")
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")


# --- Recompute on Spot Settlement Date / Expiry --------------------------------


def test_entering_a_spot_settlement_date_resolves_and_displays_every_required_field(
    server_url, page
) -> None:
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)

    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))

    funding_rate = page.text_content("#s490-funding-rate")
    carry_factor = page.text_content("#s490-carry-factor")
    forward_decimal = page.text_content("#s490-forward-decimal")
    forward_fraction = page.text_content("#s490-forward-fraction")
    funding_method = page.text_content("#s490-funding-method")

    assert funding_rate.endswith("%")
    assert float(funding_rate.rstrip("%")) != 0.0
    assert float(carry_factor) > 1.0
    assert float(forward_decimal) > 0.0
    assert "-" in forward_fraction  # Treasury handle-32nds shape, e.g. "99-241"
    assert funding_method == "S490_TERM_RATE_FROM_CURVE_AS_OF__SIMPLE_ACT360__PROTOTYPE"
    # No repo rate control of any kind exists on this ticket.
    assert page.query_selector("input[id*='repo-rate']") is None


def test_changing_expiry_alone_recomputes_the_forward_with_no_button_click(
    server_url, page
) -> None:
    # Issue #174's own acceptance condition, driven through the real page.
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    before_forward = page.text_content("#s490-forward-decimal")
    before_rate = page.text_content("#s490-funding-rate")

    # Stays before the manually entered forward/option settlement dates
    # (2026-10-21, _fill_advanced_overrides' own default), which Expiry never
    # touches, and stays inside the fake curve's 1Y node range.
    page.fill("#expiry-datetime-input", "2026-09-15T17:20")

    _wait_until(
        lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden")
        and page.text_content("#s490-forward-decimal") != before_forward
    )
    assert page.text_content("#s490-funding-rate") != before_rate
    # No Price/Refresh click happened, and this is a side display only --
    # the main Pricing Results panel is untouched.
    assert page.text_content("#price-total") == "—"


def test_changing_the_spot_settlement_date_alone_also_recomputes(server_url, page) -> None:
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    before = page.text_content("#s490-forward-decimal")

    page.fill("#s490-spot-settlement-date-input", "2026-09-01")
    _wait_until(
        lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden")
        and page.text_content("#s490-forward-decimal") != before
    )


def test_a_slow_recompute_never_shows_the_previous_dates_stale_numbers(server_url, page) -> None:
    # Codex P2 review of PR #174: during a slow round trip the fields must
    # already be hidden (and the old numbers gone), not still showing the
    # previous Spot Settlement Date's own funding/Forward under the new
    # date. Delays the second request server-side (route.continue_() after a
    # sleep, on Playwright's own dispatch thread) rather than holding the
    # route object on the Python test thread and polling a plain list --
    # the polling loop below makes no Playwright call at all, so it would
    # never pump the event dispatch that delivers a held route to a plain
    # Python-side handler in the first place.
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    stale_forward = page.text_content("#s490-forward-decimal")

    def _delay_then_continue(route):
        time.sleep(1.0)
        route.continue_()

    page.route("**/api/case/s490-repo-carry", _delay_then_continue)
    page.fill("#s490-spot-settlement-date-input", "2026-09-01")

    # The panel clears synchronously, before the fetch for the new date even
    # starts (see maybeRefreshS490Parity), so this is true well within the
    # artificial 1s delay above -- comfortably before the delayed response
    # can possibly have arrived.
    _wait_until(lambda: page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    assert "Resolving" in page.text_content("#s490-parity-status")

    # And once the delayed response arrives, the new date's own numbers
    # appear -- the recompute itself still completes, this test only proves
    # the previous date's numbers were never shown in between.
    _wait_until(
        lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden")
        and page.text_content("#s490-forward-decimal") != stale_forward,
        timeout=15.0,
    )
    page.unroute("**/api/case/s490-repo-carry")


def test_editing_the_spot_settlement_date_never_invalidates_an_in_flight_price(
    server_url, page
) -> None:
    # The Spot Settlement Date field is deliberately not part of
    # MANUAL_TEXT_INPUTS -- unlike every real ticket field, editing it must
    # never abort an outstanding Price/Refresh request (Issue #174).
    _load_and_complete_ust(page, server_url)

    pending = []
    page.route("**/api/case", lambda route: pending.append(route))
    page.click("#price-btn")
    _wait_until(lambda: len(pending) == 1)

    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    page.wait_for_timeout(150)

    # The original Price request is still the one in flight -- nothing
    # aborted and re-sent it.
    assert len(pending) == 1
    page.unroute("**/api/case")


# --- Clear -----------------------------------------------------------------


def test_clear_blanks_the_spot_settlement_date_and_resets_the_panel(server_url, page) -> None:
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))

    page.click("#clear-btn")

    assert page.input_value("#s490-spot-settlement-date-input") == ""
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")


# --- Failure surfaces --------------------------------------------------------


def test_a_bloomberg_curve_failure_surfaces_on_the_panel_not_a_silent_blank(
    server_url, page, monkeypatch
) -> None:
    # _load_and_complete_ust's own default advanced overrides give the
    # ticket genuine manual curve nodes (for its Black-76 pricing) -- the
    # panel's own route forces a fresh acquisition regardless (see the next
    # test), so that manual curve must not shield this failure from
    # surfacing here either.
    def _fail(tenors=None):
        raise server_module.BLIBloombergDapiError("Bloomberg terminal not logged in")

    monkeypatch.setattr(server_module, "load_bloomberg_usd_sofr_option_discount_curve", _fail)

    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)

    _wait_until(
        lambda: "Bloomberg terminal not logged in" in page.text_content("#s490-parity-status")
    )
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")


def test_a_manual_curve_override_on_the_ticket_never_leaks_into_the_s490_panel(
    server_url, page
) -> None:
    # Codex P1 review of PR #174: a manual Option Discount Curve override is
    # a legitimate, supported thing to enter for Black-76 pricing -- it must
    # never become the silent source of a number this panel presents as
    # "S490 Funding Rate". _load_and_complete_ust's own default advanced
    # overrides already give this ticket two manual nodes of its own
    # (curve_id SHIORI_MANUAL_OPTION_DISCOUNT_CURVE); the panel's own
    # response must show the live curve's provenance regardless.
    responses = []
    page.on(
        "response",
        lambda response: responses.append(response)
        if "/api/case/s490-repo-carry" in response.url
        else None,
    )

    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    _wait_until(lambda: len(responses) >= 1)

    body = responses[-1].json()
    funding = body["s490_repo_carry"]["funding"]
    assert funding["curve_ids"] == ["USD_SOFR_OPTION_DISCOUNT_CURVE"]
    assert funding["source_systems"] == ["BLOOMBERG_DAPI"]
    assert (
        body["s490_repo_carry"]["curve_acquisition"]
        == "LIVE_PRODUCTION_CURVE_490_ACQUIRED_THIS_RUN"
    )
    # The ticket's own manual override really was present and really was
    # discarded, not merely absent to begin with.
    assert body["s490_repo_carry"]["case_curve_points_discarded"] == 2
    assert all(
        point["curve_id"] == "USD_SOFR_OPTION_DISCOUNT_CURVE"
        for point in body["case"]["curve_points"]
    )
