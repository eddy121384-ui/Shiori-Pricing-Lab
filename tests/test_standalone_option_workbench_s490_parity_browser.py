"""Browser-driven regression tests for the S490 repo-carry Forward parity
panel (Issues #173, #174, #175).

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
import json
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
    _fill_advanced_overrides,
    _fill_trade_group,
    _is_disabled,
    _load_bloomberg_bond,
    _set_expiry,
    _treasury_lookup_response,
    _wait_for_price_enabled,
    _wait_for_refresh_enabled,
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


def _load_and_complete_ust(page, server_url: str, *, settlement_dates: bool = True) -> None:
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
    _complete_draft(page, settlement_dates=settlement_dates)
    _wait_for_price_enabled(page)


_SPOT_SETTLEMENT_DATE = "2026-08-13"


# --- Readiness gate -----------------------------------------------------------


def test_panel_shows_a_resting_hint_before_a_bond_is_loaded(server_url, page) -> None:
    page.goto(f"{server_url}/")
    assert page.text_content("#s490-parity-status") != ""
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")


def test_panel_asks_for_expiry_before_a_spot_date_helps(server_url, page) -> None:
    # Issue #174 round 6: a Bloomberg-loaded bond alone is enough for the
    # instrument-readiness gate (no Call/Put, Strike, Notional, Volatility,
    # or manual Forward required) -- but Expiry is a genuine, still-needed
    # S490 input, so entering only the Spot Settlement Date must not be
    # enough either.
    page.goto(f"{server_url}/")
    response = _treasury_lookup_response(acquired_at="2026-08-12T20:00:00+08:00")
    _load_bloomberg_bond(page, response=response)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    assert "Expiry" in page.text_content("#s490-parity-status")
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


def test_a_case_a_horizon_says_no_interim_coupon_and_still_shows_the_full_trace(
    server_url, page
) -> None:
    # Issue #175: the default expiry (2026-10-20) is before this bond's next
    # coupon, so the panel says so plainly rather than leaving the field
    # blank -- and every step Issue #175 asks to be traceable is one click
    # away rather than absent.
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)

    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))

    assert page.text_content("#s490-interim-coupon-summary") == "None in window"
    assert not page.eval_on_selector("#s490-parity-trace", "el => el.hidden")
    trace = page.text_content("#s490-parity-trace-body")
    for step in ("Spot Clean", "Spot AI", "Spot Dirty", "Carried Spot Dirty",
                 "Coupon treatment", "Forward Dirty", "Forward AI", "Forward Clean",
                 "S490 funding", "Curve acquisition"):
        assert step in trace
    assert "Interim coupon " not in trace


def test_an_interim_coupon_expiry_resolves_and_shows_the_federal_reserve_roll(
    server_url, page
) -> None:
    # Issue #175 Case B, driven exactly as a trader would: the same ticket,
    # with Expiry moved past this bond's 2027-01-31 coupon. That coupon is a
    # Sunday, so Eddy's convention B rolls its cash to Monday 2027-02-01 --
    # the panel must resolve, name the coupon, and show the roll in the trace.
    #
    # The convention profile owns the settlement dates here (Codex P1 review
    # of PR #178): tF is the forward settlement date, and typing it by hand
    # would correctly stop Expiry from re-deriving it -- which is exactly the
    # chain this test is about.
    _load_and_complete_ust(page, server_url, settlement_dates=False)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    case_a_forward = page.text_content("#s490-forward-decimal")

    _set_expiry(page, local="2027-02-15T17:20")

    _wait_until(lambda: page.text_content("#s490-forward-decimal") != case_a_forward)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))

    assert page.text_content("#s490-parity-status") == ""
    # 3.75% semi-annual = 1.875 per 100; the summary names the date the cash
    # actually arrives, not the scheduled Sunday.
    assert page.text_content("#s490-interim-coupon-summary") == "2027-02-01 · 1.875000"
    trace = page.text_content("#s490-parity-trace-body")
    assert "Interim coupon 2027-01-31" in trace
    assert "paid 2027-02-01" in trace
    assert "rolled 1d, US_FEDERAL_RESERVE" in trace
    assert "days to Expiry" in trace
    assert "PROTOTYPE" in trace  # the coupon-treatment label, never hidden
    assert float(page.text_content("#s490-forward-decimal")) > 0.0


def test_expiry_and_spot_settlement_date_alone_resolve_with_forward_vol_strike_notional_blank(
    server_url, page
) -> None:
    # Issue #174 round 6 acceptance test: Bloomberg-loaded bond + Expiry +
    # Spot Settlement Date, with Call/Put, Buy/Sell, Strike, Notional,
    # Volatility, and the manual Forward override all still blank, must
    # still display the S490-derived Forward -- full Black-76
    # pricing-builder readiness (and therefore Price itself) is not
    # required, and is not reached by this test.
    page.goto(f"{server_url}/")
    response = _treasury_lookup_response(acquired_at="2026-08-12T20:00:00+08:00")
    _load_bloomberg_bond(page, response=response)
    _set_expiry(page)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)

    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))

    assert float(page.text_content("#s490-forward-decimal")) > 0.0
    assert page.text_content("#s490-forward-fraction") != "—"
    # Confirms the blank fields really were blank -- this is not a false
    # positive from a draft that happened to already be complete.
    assert page.query_selector("#option-type-toggle .opt.on") is None
    assert page.query_selector("#position-toggle .opt.on") is None
    assert page.input_value("#strike-price-input") == ""
    assert page.input_value("#notional-input") == ""
    assert page.input_value("#volatility-input") == ""
    # Issue #177: the Forward field is no longer blank here -- it is the one
    # field the derivation now fills, which is exactly the point. Every
    # *trade* input above is still blank, so this remains proof that the
    # derivation does not wait on Black-76 readiness.
    assert page.input_value("#forward-price-input") == page.text_content(
        "#s490-forward-decimal"
    ) or float(page.input_value("#forward-price-input")) == pytest.approx(
        float(page.text_content("#s490-forward-decimal"))
    )
    assert _is_disabled(page, "#price-btn")


def test_editing_strike_notional_volatility_or_manual_forward_does_not_retrigger_s490(
    server_url, page
) -> None:
    # Codex P2 review of PR #174, round 7: none of these fields are read by
    # POST /api/case/s490-repo-carry (curve_points is discarded regardless,
    # and Strike/Notional/Volatility/the manual Forward override are never
    # read at all), so editing them once the panel already has a result
    # must not launch another Bloomberg Curve #490 acquisition whose answer
    # is only thrown away.
    calls = []
    page.on(
        "request",
        lambda req: calls.append(req) if "/api/case/s490-repo-carry" in req.url else None,
    )
    page.goto(f"{server_url}/")
    response = _treasury_lookup_response(acquired_at="2026-08-12T20:00:00+08:00")
    _load_bloomberg_bond(page, response=response)
    _set_expiry(page)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    assert len(calls) == 1

    page.fill("#strike-price-input", "99.5")
    page.fill("#notional-input", "1000000")
    page.fill("#volatility-input", "0.18")
    page.fill("#forward-price-input", "101.3")
    page.wait_for_timeout(500)

    assert len(calls) == 1


def test_a_failed_request_can_be_retried_via_the_retry_button(server_url, page) -> None:
    # Codex P2 review of PR #174, round 7: a transient failure must not
    # leave the panel permanently stuck -- lastS490ParityKey latches to the
    # failed attempt's now-narrowed fingerprint, and nothing still editable
    # at this readiness level (Strike, Notional, Volatility, the manual
    # Forward override) is part of that key any more, so an explicit retry
    # is the only way back.
    page.goto(f"{server_url}/")
    response = _treasury_lookup_response(acquired_at="2026-08-12T20:00:00+08:00")
    _load_bloomberg_bond(page, response=response)
    _set_expiry(page)

    attempts = {"n": 0}

    def route_s490(route):
        attempts["n"] += 1
        if attempts["n"] == 1:
            route.fulfill(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "Bloomberg DAPI session failed to start"}),
            )
        else:
            route.continue_()

    page.route("**/api/case/s490-repo-carry", route_s490)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)

    _wait_until(
        lambda: "Bloomberg DAPI session failed to start"
        in page.text_content("#s490-parity-status")
    )
    assert not page.eval_on_selector("#s490-parity-retry-btn", "el => el.hidden")
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")

    page.click("#s490-parity-retry-btn")

    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    assert attempts["n"] == 2
    assert page.eval_on_selector("#s490-parity-retry-btn", "el => el.hidden")
    page.unroute("**/api/case/s490-repo-carry")


def test_changing_expiry_alone_recomputes_the_forward_with_no_button_click(
    server_url, page
) -> None:
    # Issue #174's own acceptance condition, driven through the real page.
    # Since Codex's P1 review of PR #178 the Forward is carried to the forward
    # settlement date, which the convention profile re-derives from Expiry --
    # so the settlement dates are left to the profile rather than typed, and
    # this still proves that changing Expiry alone moves the Forward.
    _load_and_complete_ust(page, server_url, settlement_dates=False)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))
    before_forward = page.text_content("#s490-forward-decimal")
    before_rate = page.text_content("#s490-funding-rate")

    # Stays inside the fake curve's 1Y node range.
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
    #
    # Both facts are read in ONE page evaluation. renderS490Parity sets the
    # status and the hidden fields in a single synchronous pass, so the
    # pending state is only ever coherent when observed atomically: polling
    # one and then reading the other in a second call leaves a window in
    # which the artificially delayed response lands between them, and the
    # test fails on timing rather than on its actual subject. (Both orderings
    # were tried and both raced -- once on a loaded CI runner, once locally.)
    _wait_until(
        lambda: page.evaluate(
            "() => document.querySelector('#s490-parity-status')"
            ".textContent.includes('Resolving')"
            " && document.querySelector('#s490-parity-fields').hidden"
        )
    )

    # And once the delayed response arrives, the new date's own numbers
    # appear -- the recompute itself still completes, this test only proves
    # the previous date's numbers were never shown in between.
    _wait_until(
        lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden")
        and page.text_content("#s490-forward-decimal") != stale_forward,
        timeout=15.0,
    )
    page.unroute("**/api/case/s490-repo-carry")


def test_editing_the_spot_settlement_date_invalidates_an_in_flight_price(
    server_url, page
) -> None:
    # Issue #177 reversed the Issue #174 rule this test used to assert. Under
    # #174 the Spot Settlement Date drove a read-only panel, was not part of
    # the priced case, and therefore deliberately did *not* abort an
    # outstanding Price. It is now a real pricing input -- it is carried on
    # the case as `spot_settlement_date` and it moves the Forward Black-76
    # prices from -- so an in-flight response describes a case the trader has
    # moved away from, and adopting it would show a run priced against a
    # Forward that is no longer this ticket's. It must be invalidated, like
    # every other ticket field's edit.
    _load_and_complete_ust(page, server_url)

    pending = []
    page.route("**/api/case", lambda route: pending.append(route))
    page.click("#price-btn")
    _wait_until(lambda: len(pending) == 1)
    # While that run is genuinely outstanding, Price is held.
    assert _is_disabled(page, "#price-btn")

    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)

    # The edit releases the outstanding run (Price becomes available again
    # once the re-validation settles) rather than leaving it live to be
    # adopted against the new inputs...
    _wait_until(lambda: not _is_disabled(page, "#price-btn"))
    # ...and nothing is silently re-sent in its place: repricing stays the
    # trader's own explicit click.
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


def test_a_failed_bloomberg_refresh_hides_the_panel_until_a_successful_retry(
    server_url, page
) -> None:
    # Codex P1 review of PR #174: latestBuilderReady alone (the gate the
    # panel used to rely on exclusively) does not prove the sourced quote
    # is still live -- a failed Refresh Bloomberg sets sourcedQuoteInvalidated
    # and disowns the displayed quote while leaving currentDraft.bond_quote
    # holding the now-stale spot price, and builder validation keeps
    # re-succeeding regardless (same reasoning that already gates Price
    # itself via canPrice). The panel must hide, not silently recompute a
    # Forward from that disowned quote, until a real retry restores it.
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))

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
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")

    # The race Codex's review named: builder (re-)validation succeeds again
    # on the untouched draft alone -- Refresh re-enabling is the same
    # observable signal syncDraftGating uses for that -- while the sourced
    # quote is still the one this refresh just disowned. The panel must
    # still be showing the quote-invalidated reason, not silently recompute.
    _wait_for_refresh_enabled(page)
    assert page.eval_on_selector("#s490-parity-fields", "el => el.hidden")
    assert "Refresh Bloomberg" in page.text_content("#s490-parity-status")
    page.unroute("**/api/case/bloomberg")


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


# --- Issue #177: the derived Forward is the Black-76 default -------------------
#
# Everything above this point exercises the derivation itself. These exercise
# what Issue #177 promoted it to: the single main Forward field's default
# source, the Trader Forward Override that takes it over, and the reset that
# hands it back.


def _load_and_complete_ust_without_typing_a_forward(
    page, server_url: str, *, settlement_dates: bool = True
) -> None:
    """The Issue #177 trader flow: complete the whole ticket **except** the
    Forward, then enter the Spot Settlement Date and let the derivation fill
    it. Deliberately not ``_complete_draft``, which types a Forward (and so
    now exercises the override path instead)."""

    page.goto(f"{server_url}/")
    _load_bloomberg_bond(page, response=_treasury_lookup_response(
        acquired_at="2026-08-12T20:00:00+08:00"
    ))
    _fill_trade_group(page)
    page.fill("#volatility-input", "0.03395")
    _fill_advanced_overrides(page, settlement_dates=settlement_dates)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))


def _derived_forward_in_field(page) -> float:
    return float(page.input_value("#forward-price-input"))


def _forward_field_settled_on_a_new_value(page, previous: float) -> bool:
    """True once the field holds a usable number other than ``previous``.

    The field is deliberately blanked while a re-derivation is in flight
    (Issue #177 fail-closed: the previous inputs' Forward must not sit there
    looking like this ticket's), so a predicate that just parses the field
    races against that blank window and raises ``ValueError`` out of
    ``_wait_until``. This waits for the settled state instead.
    """

    value = page.input_value("#forward-price-input")
    if not value:
        return False
    return float(value) != previous


def test_the_derivation_fills_the_forward_field_and_black76_prices_from_it(
    server_url, page
) -> None:
    # The core Issue #177 acceptance, end to end through the real controls
    # and the real POST /api/case: the trader never types a Forward, and
    # Black-76 still prices -- from the derived one.
    _load_and_complete_ust_without_typing_a_forward(page, server_url)

    assert _derived_forward_in_field(page) == pytest.approx(
        float(page.text_content("#s490-forward-decimal")), abs=5e-7
    )
    assert page.evaluate("() => window.__shioriTestTraderForwardOverrideActive()") is False
    assert "SHIORI_DERIVED_S490" in page.text_content("#forward-source-line")
    # No trader-override log entry: a derived Forward is Shiori's own
    # verified derivation, not a value the trader had to supply.
    assert all(
        record["path"] != "forward_clean_price_input.forward_clean_price_per_100"
        for record in page.evaluate("() => window.__shioriTestOverrideProvenance()")
    )

    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_clean_price_input"]["source_system"] == "SHIORI_DERIVED_S490"
    assert draft["spot_settlement_date"] == _SPOT_SETTLEMENT_DATE
    # The priced run's F is the derived Forward, not a leftover manual entry.
    assert draft["forward_clean_price_input"][
        "forward_clean_price_per_100"
    ] == pytest.approx(float(page.text_content("#s490-forward-decimal")), abs=5e-7)


def test_changing_expiry_moves_the_forward_field_with_the_derivation(
    server_url, page
) -> None:
    # Settlement dates left to the convention profile, so Expiry genuinely
    # drives tF -- see the note on the Issue #174 recompute test above.
    _load_and_complete_ust_without_typing_a_forward(
        page, server_url, settlement_dates=False
    )
    first = _derived_forward_in_field(page)

    _set_expiry(page, local="2026-11-20T17:20")
    _wait_until(lambda: _forward_field_settled_on_a_new_value(page, first))

    assert _derived_forward_in_field(page) == pytest.approx(
        float(page.text_content("#s490-forward-decimal")), abs=5e-7
    )
    assert page.evaluate("() => window.__shioriTestTraderForwardOverrideActive()") is False


def test_typing_a_forward_becomes_a_trader_override_that_a_refresh_never_overwrites(
    server_url, page
) -> None:
    # Issue #177's override acceptance: the trader takes the field over, a
    # fresh derivation keeps updating beside it, and the override survives.
    _load_and_complete_ust_without_typing_a_forward(
        page, server_url, settlement_dates=False
    )
    derived_before = float(page.text_content("#s490-forward-decimal"))

    page.fill("#forward-price-input", "97.75")

    assert page.evaluate("() => window.__shioriTestTraderForwardOverrideActive()") is True
    assert "TRADER_FORWARD_OVERRIDE" in page.text_content("#forward-source-line")
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_clean_price_input"]["source_system"] == "TRADER_FORWARD_OVERRIDE"
    assert draft["forward_clean_price_input"]["forward_clean_price_per_100"] == 97.75

    # A real re-derivation (a new Expiry) must move the derived comparison
    # value and leave the override exactly where the trader put it.
    _set_expiry(page, local="2026-11-20T17:20")
    _wait_until(
        lambda: float(page.text_content("#s490-forward-decimal")) != derived_before
    )
    assert page.input_value("#forward-price-input") == "97.75"
    assert page.evaluate("() => window.__shioriTestTraderForwardOverrideActive()") is True
    # The still-current derived Forward is shown beside it for comparison.
    assert f"{float(page.text_content('#s490-forward-decimal')):.6f}" in page.text_content(
        "#forward-source-line"
    )
    assert (
        page.evaluate("() => window.__shioriTestGetCurrentDraft()")[
            "forward_clean_price_input"
        ]["forward_clean_price_per_100"]
        == 97.75
    )


def test_reset_use_shiori_derived_returns_the_field_to_the_current_derived_forward(
    server_url, page
) -> None:
    _load_and_complete_ust_without_typing_a_forward(page, server_url)
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")

    page.fill("#forward-price-input", "97.75")
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(
        lambda: page.evaluate("() => window.__shioriTestGetCurrentDraft()")[
            "forward_clean_price_input"
        ]["source_system"]
        == "TRADER_FORWARD_OVERRIDE"
    )
    assert not page.eval_on_selector("#forward-use-derived-btn", "el => el.hidden")

    page.click("#forward-use-derived-btn")

    # Immediately back on the *current* derived Forward -- not a cached
    # "value before the override started".
    assert page.evaluate("() => window.__shioriTestTraderForwardOverrideActive()") is False
    assert _derived_forward_in_field(page) == pytest.approx(
        float(page.text_content("#s490-forward-decimal")), abs=5e-7
    )
    assert page.eval_on_selector("#forward-use-derived-btn", "el => el.hidden")
    # ...and the run is repriced from it, with no second Price click.
    _wait_until(
        lambda: page.evaluate("() => window.__shioriTestGetCurrentDraft()")[
            "forward_clean_price_input"
        ]["source_system"]
        == "SHIORI_DERIVED_S490"
    )
    assert page.inner_text("#status-text") == "Draft priced"


def test_a_failed_derivation_blanks_the_forward_field_and_blocks_price(
    server_url, page, monkeypatch
) -> None:
    # Fail closed: no spot clean price, no previous Forward, no residual
    # number left sitting in the field looking like this ticket's F.
    _load_and_complete_ust_without_typing_a_forward(page, server_url)
    assert _derived_forward_in_field(page) > 0.0

    page.route(
        "**/api/case/s490-repo-carry",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg terminal not logged in"}),
        ),
    )
    # Re-trigger through the Spot Settlement Date rather than Expiry: moving
    # Expiry past this ticket's settlement dates would disable Price for a
    # second, unrelated reason and this test would stop proving anything.
    page.fill("#s490-spot-settlement-date-input", "2026-08-14")

    _wait_until(lambda: page.input_value("#forward-price-input") == "")
    _wait_until(lambda: _is_disabled(page, "#price-btn"))
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["forward_clean_price_input"]["forward_clean_price_per_100"] is None
    # The real reason survives all the way onto the Forward field's own line.
    assert "Bloomberg terminal not logged in" in page.text_content("#forward-source-line")
    page.unroute("**/api/case/s490-repo-carry")


def test_an_active_override_still_prices_when_the_derivation_fails(
    server_url, page
) -> None:
    # The other half of fail-closed: an explicit override is an explicit
    # Forward, so it prices under the existing override contract regardless.
    _load_and_complete_ust_without_typing_a_forward(page, server_url)
    page.fill("#forward-price-input", "97.75")

    page.route(
        "**/api/case/s490-repo-carry",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": "Bloomberg terminal not logged in"}),
        ),
    )
    # Same reasoning as the test above: re-trigger via the Spot Settlement
    # Date, so the only thing that can hold Price back is the Forward.
    page.fill("#s490-spot-settlement-date-input", "2026-08-14")
    _wait_until(
        lambda: "Bloomberg terminal not logged in" in page.text_content("#s490-parity-status")
    )

    assert page.input_value("#forward-price-input") == "97.75"
    _wait_for_price_enabled(page)
    page.click("#price-btn")
    _wait_until(lambda: page.inner_text("#status-text") == "Draft priced")
    page.unroute("**/api/case/s490-repo-carry")


def test_clear_drops_the_trader_forward_override(server_url, page) -> None:
    _load_and_complete_ust_without_typing_a_forward(page, server_url)
    page.fill("#forward-price-input", "97.75")
    assert page.evaluate("() => window.__shioriTestTraderForwardOverrideActive()") is True

    page.click("#clear-btn")

    assert page.evaluate("() => window.__shioriTestTraderForwardOverrideActive()") is False
    assert page.input_value("#forward-price-input") == ""
    assert page.eval_on_selector("#forward-use-derived-btn", "el => el.hidden")


# --- Codex P2 review of PR #178, round 3: the selected convention profile
# --- must reach the priced case, not just the panel ---------------------------


def _draft_convention_profile(page):
    return page.evaluate("() => window.__shioriTestGetCurrentDraft().convention_profile")


def test_changing_the_convention_profile_reaches_the_priced_case(server_url, page) -> None:
    # `_load_and_complete_ust` types all eight profile-owned Advanced controls,
    # so every one of them is a trader override -- which is exactly the state
    # where `applyAdvancedProfileFields` sees no control move and therefore
    # never reached `applyManualInputsToDraft`. The panel reads the selection
    # from the case now, but the real point is that the case itself must carry
    # the trader's current choice: an interim-coupon ticket that derives a
    # Forward under a newly selected UST profile must not then fail at Price
    # time under the previous one.
    _load_and_complete_ust(page, server_url)
    assert _draft_convention_profile(page) == "UST"
    assert page.evaluate("() => window.__shioriTestTraderOverriddenPaths().length") == 8

    page.select_option("#convention-profile-select", "US_CORPORATE")

    _wait_until(lambda: _draft_convention_profile(page) == "US_CORPORATE")
    assert page.evaluate("() => window.__shioriTestSelectedConventionProfile()") == (
        "US_CORPORATE"
    )


def test_the_s490_panel_derives_under_the_same_profile_the_case_carries(
    server_url, page
) -> None:
    # One source of truth: whatever the panel sends is read off the same case
    # field Price sends, so the two cannot report different methodologies.
    requests = []
    page.on(
        "request",
        lambda request: requests.append(request.post_data)
        if "/api/case/s490-repo-carry" in request.url
        else None,
    )
    _load_and_complete_ust(page, server_url)
    page.fill("#s490-spot-settlement-date-input", _SPOT_SETTLEMENT_DATE)
    _wait_until(lambda: not page.eval_on_selector("#s490-parity-fields", "el => el.hidden"))

    _wait_until(lambda: len(requests) > 0)
    body = json.loads(requests[-1])
    assert body["convention_profile"] == _draft_convention_profile(page)
    assert body["case"]["convention_profile"] == body["convention_profile"]
