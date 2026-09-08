"""Browser-driven tests for the Historical Yield Vol card (Issue #197).

Exercises ``historical_yield_vol_view.js`` -- the Middle Office Historical
Yield Vol card inside Markets -> Bond Yield History -- against one real
``ThreadingHTTPServer`` and one real headless Chromium page. The one
read-only route is intercepted at the browser network layer with
``page.route``, the pattern the sibling Markets browser files already use, so
the fixtures are deterministic and no Bloomberg session is involved.

Every value below is made up. No Bloomberg value, no real Yield series and no
real field mnemonic appears here.

The invariant this file exists to hold down: **the page displays the server's
calculation and computes nothing.** The fixture's figures are deliberately
not the standard deviation of anything -- if the page ever grew its own
statistic, the numbers on screen would stop matching the payload and these
tests would fail.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test files: locally, missing Playwright is a skip; in CI
(``CI=true``) it is a hard collection-time error.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from collections.abc import Iterator
from datetime import date, timedelta

import pytest

import shiori_pricing_lab.app.standalone_option_workbench_server as server_module
from shiori_pricing_lab.app.standalone_option_workbench_server import create_server
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (
    BloombergBondYieldHistory,
    BondYieldObservation,
)
from shiori_pricing_lab.data.historical_yield_volatility import (
    HistoricalYieldVolUnavailableError,
    calculate_historical_yield_volatility,
    historical_yield_vol_volatility_input,
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

pytestmark = pytest.mark.skipif(
    not _PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed locally (local-only skip; CI hard-fails instead)",
)

if _PLAYWRIGHT_AVAILABLE:
    from playwright.sync_api import sync_playwright

_CHROMIUM_EXECUTABLE_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")

_ROUTE = "**/api/bloomberg/historical-yield-vol"
_HISTORY_ROUTE = "**/api/bloomberg/bond-yield-history"
_ISIN = "US0000000000"
_FIELD = "SYNTHETIC_TEST_YIELD_FIELD"

# Digits a toFixed/round renderer would silently rewrite, so a page that
# re-formatted instead of printing the server's own string would be caught.
_DAILY_TEXT = "0.4000000000000001"
_ANNUALIZED_TEXT = "6.349803146555018"
# The same figure normalized to the unit BLIVolatilityInput states. It is a
# different number from the headline on purpose: the page must show both, each
# under its own unit, and must never derive one from the other.
_NORMALIZED_TEXT = "0.06349803146555018"


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _is_actually_hidden(page, element_id: str) -> bool:
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


# 180 ascending dates whose endpoints are the ones the card displays. Built
# rather than abbreviated: the fixture used to count 180 observations and list
# two dates, a shape `result_shape_problem` refuses and the server can never
# emit, and every test in this file treated it as canonical (Codex review,
# PR #200).
_OBSERVATION_DATES = [
    (date(2026, 1, 8) + timedelta(days=offset)).isoformat() for offset in range(179)
] + ["2026-09-01"]

_FULL_PAYLOAD = {
    "methodology": "HISTORICAL_YIELD_VOL_MO",
    "requested_identifier": f"/isin/{_ISIN}",
    "security": "SYNTHETIC TEST Corp",
    "yield_field": _FIELD,
    "field_meaning": None,
    "field_unit": "PERCENT",
    "source_system": "BLOOMBERG_DAPI",
    "acquired_at": "2026-08-31T14:05:00+00:00",
    "calculated_at": "2026-08-31T14:05:02+00:00",
    "requested_start_date": "2026-01-01",
    "requested_end_date": "2026-09-01",
    "series_observation_count": 200,
    "requested_observation_count": 180,
    "observation_count": 180,
    "observation_dates": _OBSERVATION_DATES,
    "first_observation_date": "2026-01-08",
    "last_observation_date": "2026-09-01",
    "yield_change_count": 179,
    "standard_deviation_convention": "SAMPLE_STDEV_S_DDOF_1",
    "annualization_trading_days": 252,
    "annualization_factor": 15.874507866387544,
    "daily_yield_vol": float(_DAILY_TEXT),
    "daily_yield_vol_text": _DAILY_TEXT,
    "annualized_yield_vol": float(_ANNUALIZED_TEXT),
    "annualized_yield_vol_text": _ANNUALIZED_TEXT,
    "window_status": "FULL_WINDOW",
    "blockers": [],
    "warnings": [],
    "volatility_source": {
        "source_system": "HISTORICAL_YIELD_VOL_MO",
        "volatility_basis": "YIELD_VOL",
        "volatility": float(_NORMALIZED_TEXT),
        "volatility_text": _NORMALIZED_TEXT,
        "volatility_unit": "DECIMAL_ANNUAL",
        "source_unit": "PERCENT",
        "normalization_factor": 0.01,
        "status": "ACTIVE",
        "override_or_fallback_audit": "HISTORICAL_YIELD_VOL_MO FULL_WINDOW: calculated from "
        "180 of the requested 180 Yield observations (179 Yield Changes); source unit PERCENT "
        "normalized to DECIMAL_ANNUAL by factor 0.01.",
    },
    "volatility_source_unavailable_reason": None,
}

_SHORT_PAYLOAD = {
    **_FULL_PAYLOAD,
    "series_observation_count": 90,
    "observation_count": 90,
    "yield_change_count": 89,
    # The dates move with the count. Overriding one and not the other builds a
    # shape `result_shape_problem` refuses, which is what these derived
    # fixtures did until the endpoint rule caught them (Codex review, #200).
    "observation_dates": _OBSERVATION_DATES[:90],
    "first_observation_date": _OBSERVATION_DATES[0],
    "last_observation_date": _OBSERVATION_DATES[89],
    "window_status": "INSUFFICIENT_HISTORY",
    "blockers": [],
    "warnings": [
        "INSUFFICIENT_HISTORY: 90 of the requested 180 Yield observations exist. This is "
        "not a full-window Historical Yield Vol, and no flat extension, benchmark, index "
        "or VCUB substitute has been applied"
    ],
    "volatility_source": {
        **_FULL_PAYLOAD["volatility_source"],
        "override_or_fallback_audit": "INSUFFICIENT_HISTORY: calculated from 90 of the "
        "requested 180 Yield observations (89 Yield Changes).",
    },
}

_NO_HISTORY_PAYLOAD = {
    **_FULL_PAYLOAD,
    "series_observation_count": 0,
    "observation_count": 0,
    "observation_dates": [],
    "first_observation_date": None,
    "last_observation_date": None,
    "yield_change_count": 0,
    "daily_yield_vol": None,
    "daily_yield_vol_text": None,
    "annualized_yield_vol": None,
    "annualized_yield_vol_text": None,
    "window_status": "NO_HISTORY",
    "warnings": [],
    "blockers": [
        "Bloomberg returned no Yield observations for 'SYNTHETIC TEST Corp' over "
        "2026-01-01..2026-09-01 -- there is no approved proxy for an instrument with no "
        "history, so no Historical Yield Vol is available"
    ],
    "volatility_source": None,
    "volatility_source_unavailable_reason": "no Historical Yield Vol is available for "
    "'SYNTHETIC TEST Corp' (NO_HISTORY)",
}

# Short AND too short: one warning, one blocker, and no number at all. The
# warning heading must not claim this result is usable.
_BLOCKED_SHORT_PAYLOAD = {
    **_SHORT_PAYLOAD,
    "series_observation_count": 2,
    "observation_count": 2,
    "yield_change_count": 1,
    "observation_dates": _OBSERVATION_DATES[:2],
    "first_observation_date": _OBSERVATION_DATES[0],
    "last_observation_date": _OBSERVATION_DATES[1],
    "daily_yield_vol": None,
    "daily_yield_vol_text": None,
    "annualized_yield_vol": None,
    "annualized_yield_vol_text": None,
    "warnings": [
        "INSUFFICIENT_HISTORY: 2 of the requested 180 Yield observations exist. This is not "
        "a full-window Historical Yield Vol, and no flat extension, benchmark, index or VCUB "
        "substitute has been applied"
    ],
    "blockers": [
        "1 Yield Change(s) is below the 2 the SAMPLE_STDEV_S_DDOF_1 convention needs -- no "
        "standard deviation is reported for this window"
    ],
    "volatility_source": None,
    "volatility_source_unavailable_reason": "no Historical Yield Vol is available for "
    "'SYNTHETIC TEST Corp' (INSUFFICIENT_HISTORY)",
}

_NO_UNIT_PAYLOAD = {
    **_FULL_PAYLOAD,
    "field_unit": None,
    "volatility_source": None,
    "volatility_source_unavailable_reason": "the Yield field's unit was not established by "
    "this request, so its Historical Yield Vol cannot be normalized to DECIMAL_ANNUAL -- "
    "confirm the unit on the workstation and supply one of BASIS_POINTS, DECIMAL, PERCENT",
}


def _raw_payload_with(key: str, literal: str) -> str:
    """The full payload as wire JSON, with one number replaced verbatim.

    `json.dumps` of a Python int would already have lost the precision this
    exercises, so the literal is spliced into the encoded body instead --
    9007199254740993 has to reach the browser as those digits to be parsed
    into a different number.
    """

    encoded = json.dumps(_FULL_PAYLOAD)
    original = f'"{key}": {_FULL_PAYLOAD[key]}'
    assert original in encoded
    return encoded.replace(original, f'"{key}": {literal}', 1)


def _route_vol(
    page, *, payload=None, error: str | None = None, status: int = 400, raw_payload=None
):
    calls: list[dict] = []

    def _handle(route):
        calls.append(json.loads(route.request.post_data))
        if error is not None:
            route.fulfill(
                status=status,
                content_type="application/json",
                body=json.dumps({"error": error}),
            )
            return
        if raw_payload is not None:
            route.fulfill(status=200, content_type="application/json", body=raw_payload)
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload if payload is not None else _FULL_PAYLOAD),
        )

    page.route(_ROUTE, _handle)
    return calls


def _route_other_markets_away(page):
    """Keep the sibling Markets views' own fetches off live Bloomberg."""

    page.route(
        "**/api/bloomberg/option-discount-curve",
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "no Bloomberg in this test"}),
        ),
    )
    page.route(
        "**/api/vol-surface/atm/list",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"surfaces": [], "database": "test.sqlite3"}),
        ),
    )
    page.route(
        _HISTORY_ROUTE,
        lambda route: route.fulfill(
            status=502,
            content_type="application/json",
            body=json.dumps({"error": "no Bloomberg in this test"}),
        ),
    )


def _open_card(page, server_url: str) -> None:
    page.goto(f"{server_url}/")
    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))
    page.click("#markets-tab-yield-history")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-yield-history"))


def _fill_query(page, *, identifier=_ISIN, field=_FIELD, count="180", unit="PERCENT"):
    page.fill("#byh-identifier", identifier)
    page.fill("#byh-yield-field", field)
    page.fill("#byh-start", "2026-01-01")
    page.fill("#byh-end", "2026-09-01")
    page.fill("#hyv-observation-count", count)
    page.fill("#hyv-field-unit", unit)


def _calculate(page) -> None:
    page.click("#hyv-calculate-btn")


def _wait_for_result(page) -> None:
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-result"))


# --- the card is there, and starts idle ---------------------------------------


def test_the_card_lives_in_the_bond_yield_history_view_and_starts_idle(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page)
    _open_card(page, server_url)

    assert not _is_actually_hidden(page, "hyv-idle")
    assert _is_actually_hidden(page, "hyv-result")
    assert page.inner_text("#hyv-methodology").strip() == "HISTORICAL_YIELD_VOL_MO"
    # Opening the tab sends nothing on its own.
    assert page.evaluate("() => window.__shioriTestHistoricalYieldVolRequestedRoutes()") == []


def test_the_window_defaults_to_middle_offices_confirmed_180(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page)
    _open_card(page, server_url)

    assert page.input_value("#hyv-observation-count") == "180"


# --- what the page sends ------------------------------------------------------


def test_the_query_above_is_reused_and_the_count_is_sent_verbatim(server_url, page) -> None:
    _route_other_markets_away(page)
    calls = _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page, count="180")
    _calculate(page)
    _wait_for_result(page)

    assert calls == [
        {
            "bond_identifier": _ISIN,
            "yield_field": _FIELD,
            "start_date": "2026-01-01",
            "end_date": "2026-09-01",
            "requested_observation_count": 180,
            "field_unit": "PERCENT",
        }
    ]


def test_an_empty_unit_box_sends_no_unit_at_all(server_url, page) -> None:
    _route_other_markets_away(page)
    calls = _route_vol(page, payload=_NO_UNIT_PAYLOAD)
    _open_card(page, server_url)
    _fill_query(page, unit="")
    _calculate(page)
    _wait_for_result(page)

    assert "field_unit" not in calls[0]


def test_no_yield_field_sends_no_request_at_all(server_url, page) -> None:
    _route_other_markets_away(page)
    calls = _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page, field="")
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert calls == []
    assert "will not guess one" in page.inner_text("#hyv-error-detail")


def test_a_non_integer_observation_count_sends_no_request_at_all(server_url, page) -> None:
    _route_other_markets_away(page)
    calls = _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page, count="180.5")
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert calls == []


def test_a_count_javascript_cannot_represent_exactly_sends_no_request(server_url, page) -> None:
    # 2^53 + 1 survives the whole-number regex but not Number(): it would
    # arrive as ...992 and the server would audit a window the trader never
    # asked for, while this card claims the count is sent verbatim.
    _route_other_markets_away(page)
    calls = _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page, count="9007199254740993")
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert calls == []
    assert "without rounding" in page.inner_text("#hyv-error-detail")


def test_the_card_calls_only_its_own_route(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    routes = page.evaluate("() => window.__shioriTestHistoricalYieldVolRequestedRoutes()")
    assert routes == ["/api/bloomberg/historical-yield-vol"]


# --- what the page displays ---------------------------------------------------


def test_the_displayed_figures_are_the_servers_exact_digits(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    # Digit-for-digit the payload's own strings: no rounding, no toFixed, no
    # re-formatting through a JavaScript number.
    assert page.inner_text("#hyv-annualized").strip() == _ANNUALIZED_TEXT
    assert page.inner_text("#hyv-daily").strip() == _DAILY_TEXT


def test_there_is_no_browser_side_volatility_statistic(server_url, page) -> None:
    _route_other_markets_away(page)
    # A payload whose figures are deliberately unrelated to any statistic of
    # the observations: a page computing its own would disagree with them.
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "annualized_yield_vol": 1234.5,
            "annualized_yield_vol_text": "1234.5",
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-annualized").strip() == "1234.5"
    payload = page.evaluate("() => window.__shioriTestHistoricalYieldVolPayload()")
    assert payload["annualized_yield_vol_text"] == "1234.5"


@pytest.mark.parametrize(
    "overrides",
    [
        # The finding as reported: a string that is not a number at all,
        # printed verbatim as the risk headline (Codex review, PR #200).
        {"annualized_yield_vol_text": "not calculated"},
        {"annualized_yield_vol_text": ""},
        {"annualized_yield_vol_text": "   "},
        # A number that is not the number: the text is what reaches the
        # screen, so a text/value disagreement is a wrong figure on screen.
        {"annualized_yield_vol_text": "0.123"},
        {"daily_yield_vol_text": "0.123"},
        # Text where the calculator produced no figure, and the reverse.
        {"annualized_yield_vol": None, "annualized_yield_vol_text": "0.5"},
        {"daily_yield_vol": None, "daily_yield_vol_text": "0.5"},
        {"annualized_yield_vol_text": None},
        # A non-finite numeric counterpart cannot back a headline either.
        {"annualized_yield_vol": "0.5"},
    ],
)
def test_a_headline_text_that_is_not_its_own_number_is_refused(
    server_url, page, overrides
) -> None:
    """The two figures are printed verbatim, so the string IS the risk number.

    Checking only `typeof === "string"` let `annualized_yield_vol_text:
    "not calculated"` through, and render() put it on screen under the Middle
    Office heading. Each text field must now be present exactly when its
    numeric counterpart is, and read back as exactly that number.
    """

    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "volatility_overrides",
    [
        # The third displayed figure, missed when the rule was written for the
        # two top-level ones (Codex review, PR #200).
        {"volatility_text": "not calculated"},
        {"volatility_text": "   "},
        # Text for a different number: this string is the published figure on
        # screen, so a disagreement is a wrong normalized volatility shown as
        # an ACTIVE risk source.
        {"volatility_text": "0.99"},
        {"volatility": 0.99},
        {"volatility": None},
    ],
)
def test_the_published_figures_text_must_be_its_own_number(
    server_url, page, volatility_overrides
) -> None:
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "volatility_source": {
                **_FULL_PAYLOAD["volatility_source"],
                **volatility_overrides,
            },
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "source_overrides",
    [
        # The factor the card prints as the exact normalization applied. An
        # arbitrary number here is false provenance on an ACTIVE risk source.
        {"normalization_factor": -1},
        {"normalization_factor": 0.1},
        {"normalization_factor": 1},
        {"source_unit": "PERCENTAGE_POINTS"},
        # DECIMAL normalizes by 1, not by PERCENT's 0.01.
        {"source_unit": "DECIMAL"},
    ],
)
def test_a_normalization_factor_its_unit_does_not_fix_is_refused(
    server_url, page, source_overrides
) -> None:
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "volatility_source": {**_FULL_PAYLOAD["volatility_source"], **source_overrides},
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    ("unit", "factor"),
    [("DECIMAL", 1), ("PERCENT", 0.01), ("BASIS_POINTS", 0.0001)],
)
def test_every_supported_unit_and_its_own_factor_still_renders(
    server_url, page, unit, factor
) -> None:
    # The rule must accept the whole approved vocabulary, not just the unit
    # the fixture happens to use. `field_unit` moves with it: the server
    # publishes `source_unit` as a copy of it, so overriding only the nested
    # one asserts a shape the server cannot produce -- which is what this
    # test did until the headline/source unit rule caught it.
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "field_unit": unit,
            "volatility_source": {
                **_FULL_PAYLOAD["volatility_source"],
                "source_unit": unit,
                "normalization_factor": factor,
            },
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert unit in page.inner_text("#hyv-source-detail")


@pytest.mark.parametrize(
    "overrides",
    [
        # No blocker, an ACTIVE source, and no figures: two dashes beside a
        # published risk number (Codex review, PR #200).
        {
            "daily_yield_vol": None,
            "daily_yield_vol_text": None,
            "annualized_yield_vol": None,
            "annualized_yield_vol_text": None,
        },
        # A fatal blocker beside a figure: the calculator's own `is_usable`
        # invariant says these two cannot both be true.
        {"blockers": ["this result must not be used"]},
    ],
)
def test_figure_availability_must_agree_with_blockers_and_source(
    server_url, page, overrides
) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


def test_a_figure_with_no_published_source_is_still_an_honest_answer(
    server_url, page
) -> None:
    # The availability rule must not be symmetric. An unconfirmed Yield unit
    # blocks publication without blocking the calculation, and that result is
    # exactly what this card exists to show.
    _route_other_markets_away(page)
    _route_vol(page, payload=_NO_UNIT_PAYLOAD)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-annualized").strip() == _ANNUALIZED_TEXT


@pytest.mark.parametrize("unit", ["percent", " Percent ", "basis_points", "decimal"])
def test_a_unit_spelled_the_way_a_trader_typed_it_still_renders(server_url, page, unit) -> None:
    """A false refusal I introduced, not a payload defect (Codex review, #200).

    `decimal_annual_normalization_factor` trims and upper-cases before it
    matches, and the server publishes `source_unit` as the trader typed it --
    so "percent" is a real HTTP 200 whose factor really is 0.01. The previous
    commit's raw map lookup rejected that honest answer and showed nothing.
    """

    factor = {"percent": 0.01, " percent ": 0.01, "basis_points": 0.0001, "decimal": 1}[
        unit.strip().lower()
    ]
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "field_unit": unit,
            "volatility_source": {
                **_FULL_PAYLOAD["volatility_source"],
                "source_unit": unit,
                "normalization_factor": factor,
            },
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert unit in page.inner_text("#hyv-source-detail")


def test_a_headline_unit_its_source_disagrees_with_is_refused(server_url, page) -> None:
    # The server publishes source_unit as a copy of field_unit, so the card
    # labelling the raw figure PERCENT while its normalized source claims
    # DECIMAL is impossible -- and each half was internally valid.
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "field_unit": "PERCENT",
            "volatility_source": {
                **_FULL_PAYLOAD["volatility_source"],
                "source_unit": "DECIMAL",
                "normalization_factor": 1,
            },
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        # One figure without the other: a real annualized risk number beside a
        # dash for the daily sigma it was annualized from.
        {"daily_yield_vol": None, "daily_yield_vol_text": None},
        {"annualized_yield_vol": None, "annualized_yield_vol_text": None},
    ],
)
def test_the_two_headline_figures_must_exist_together(server_url, page, overrides) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        # 180 observations and no changes, printed as this figure's provenance.
        {"yield_change_count": 0},
        {"yield_change_count": 180},
        # The window is the smaller of what was asked for and what came back.
        {"observation_count": 200},
        {"series_observation_count": 100},
        {"series_observation_count": "200"},
    ],
)
def test_the_provenance_counts_must_be_the_calculators_own_arithmetic(
    server_url, page, overrides
) -> None:
    # These three numbers are the provenance printed under the figure, and
    # they are not independent: the window is min(series, requested) and N
    # observations make N-1 changes (Codex review, PR #200).
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "source_overrides",
    [
        {"status": "STALE"},
        {"volatility_basis": "PRICE_VOL"},
        {"volatility_basis": "EQUIVALENT_PRICE_VOL"},
        {"source_system": "BLOOMBERG_VCUB"},
        {"volatility_unit": "PERCENT"},
    ],
)
def test_only_the_canonical_sources_labels_are_rendered(
    server_url, page, source_overrides
) -> None:
    """This route publishes one source, and the card must show only that one.

    `status: "STALE"` and `volatility_basis: "PRICE_VOL"` were drawn in the
    normal normalized-source block as though published -- the four labels were
    checked for non-blankness and nothing else (Codex review, PR #200). A
    VCUB or PRICE_VOL label under this card's Middle Office heading is the
    exact confusion Issue #197 asked to keep separate.
    """

    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "volatility_source": {**_FULL_PAYLOAD["volatility_source"], **source_overrides},
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        {"daily_yield_vol": -1.0, "daily_yield_vol_text": "-1.0"},
        {"annualized_yield_vol": -1.0, "annualized_yield_vol_text": "-1.0"},
    ],
)
def test_a_negative_headline_volatility_is_refused(server_url, page, overrides) -> None:
    # Number and text agreed, and both were finite -- so the card printed a
    # negative standard deviation, which the server's own guard refuses.
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "never negative" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        # A short window wearing the full-window label gets the green pill and
        # loses its qualification, while the counts underneath say otherwise.
        {
            "series_observation_count": 100,
            "observation_count": 100,
            "yield_change_count": 99,
            "window_status": "FULL_WINDOW",
            "warnings": [],
        },
        # A full window claiming to be short.
        {"window_status": "INSUFFICIENT_HISTORY"},
        # The right status, but the qualification stripped off it.
        {
            "series_observation_count": 100,
            "observation_count": 100,
            "yield_change_count": 99,
            "window_status": "INSUFFICIENT_HISTORY",
            "warnings": [],
        },
        # A warning on a window that is not short.
        {"warnings": ["Applied VCUB substitute"]},
        # A requested count below what a ddof=1 standard deviation needs.
        {
            "requested_observation_count": 2,
            "series_observation_count": 2,
            "observation_count": 2,
            "yield_change_count": 1,
        },
    ],
)
def test_the_window_status_must_follow_its_own_counts(server_url, page, overrides) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


def test_a_flat_window_still_shows_its_honest_zero(server_url, page) -> None:
    """The one payload the strict-positive rule must NOT refuse.

    Identical Yield Changes give an exact sigma of 0, the calculator reports
    it with no blocker, and the publication helper refuses to publish it with
    a reason. Requiring a positive figure everywhere would have refused that
    whole answer -- the same class of mistake as rejecting a unit typed
    "percent" (Codex review, PR #200).
    """

    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "daily_yield_vol": 0.0,
            "daily_yield_vol_text": "0.0",
            "annualized_yield_vol": 0.0,
            "annualized_yield_vol_text": "0.0",
            "volatility_source": None,
            "volatility_source_unavailable_reason": "the Historical Yield Vol of the selected "
            "180-observation window is 0.0 DECIMAL_ANNUAL, which is not positive",
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-annualized").strip() == "0.0"


@pytest.mark.parametrize("volatility", [0, 0.0, -0.0])
def test_a_zero_published_volatility_is_refused(server_url, page, volatility) -> None:
    # Strictly positive where it is published: the publication helper refuses
    # a zero, so an ACTIVE source carrying one cannot have come from it.
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "volatility_source": {
                **_FULL_PAYLOAD["volatility_source"],
                "volatility": volatility,
                "volatility_text": repr(volatility),
            },
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "never zero or negative" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_start_date": {"y": 2026}},
        {"requested_start_date": "2026-02-31"},
        {"requested_start_date": "01/01/2026"},
        {"requested_end_date": None},
        # Inverted: an end before its own start.
        {"requested_start_date": "2026-09-01", "requested_end_date": "2026-01-01"},
    ],
)
def test_the_displayed_request_range_must_be_a_real_ordered_range(
    server_url, page, overrides
) -> None:
    # Printed verbatim as this calculation's Bloomberg provenance, and never
    # inspected at all -- an object reached the card as "[object Object]".
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        # Two observations, one change, and a figure: ddof=1 cannot make a
        # standard deviation from one change.
        {
            "series_observation_count": 2,
            "observation_count": 2,
            "yield_change_count": 1,
            "window_status": "INSUFFICIENT_HISTORY",
            "warnings": ["INSUFFICIENT_HISTORY: 2 of the requested 180 observations exist"],
        },
    ],
)
def test_figures_must_follow_the_change_count(server_url, page, overrides) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        {"warnings": [{"text": "short"}]},
        {"warnings": [""]},
        {"warnings": ["   "]},
        {"blockers": [{"text": "blocked"}]},
        {"blockers": [""]},
    ],
)
def test_every_blocker_and_warning_entry_must_be_readable(server_url, page, overrides) -> None:
    # These entries are the refusal and the qualification a trader reads. An
    # object rendered as "[object Object]" and a blank string as an empty
    # bullet, in the one place the card explains itself.
    # Built from the already-consistent short fixtures rather than by
    # overriding counts on the full one: assembling a payload by hand is how
    # every one of these fixtures drifted in the first place.
    base = _SHORT_PAYLOAD if "warnings" in overrides else _BLOCKED_SHORT_PAYLOAD
    payload = {**base, **overrides}
    _route_other_markets_away(page)
    _route_vol(page, payload=payload)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "non-blank string" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        {"methodology": "BLOOMBERG_VCUB"},
        {"standard_deviation_convention": "POPULATION_STDEV_P"},
    ],
)
def test_only_the_canonical_methodology_labels_are_rendered(server_url, page, overrides) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "this card shows only" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "overrides",
    [
        {"series_observation_count": -1, "observation_count": -1, "yield_change_count": 0},
        {"requested_observation_count": -180},
    ],
)
def test_negative_counts_are_refused(server_url, page, overrides) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, **overrides})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


def _real_history(values, *, field_unit="PERCENT") -> BloombergBondYieldHistory:
    """A history the real #196 loader could have returned."""

    start = date(2026, 1, 1)
    observations = tuple(
        BondYieldObservation(
            observation_date=start + timedelta(days=index),
            yield_value=value,
            raw_value=repr(value),
        )
        for index, value in enumerate(values)
    )
    return BloombergBondYieldHistory(
        requested_identifier=f"/isin/{_ISIN}",
        security="SYNTHETIC TEST Corp",
        yield_field=_FIELD,
        field_meaning="Yield to maturity",
        field_unit=field_unit,
        requested_start_date=start,
        requested_end_date=date(2026, 9, 1),
        observations=observations,
        source_system="BLOOMBERG_DAPI",
        acquired_at="2026-09-01T14:05:00+00:00",
    )


_VARIED = [4.0 + (index % 7) * 0.01 for index in range(180)]
# Identical Yield Changes: an exact sigma of zero, which the calculator
# reports and the publication helper refuses. Whole-number steps on purpose --
# `4.0 + index * 0.01` looks flat and is not: its changes differ in the last
# bits, giving a sigma of 3.8e-16 that publishes successfully, so that series
# would have exercised the ordinary branch under a name claiming otherwise.
_FLAT = [4.0 + float(index) for index in range(180)]

# Every canonical branch of the real serializer, named by what it produces
# (Codex review, PR #200). The anchored test used to drive only the first.
_REAL_ROUTE_CASES = {
    "full window, published": (_VARIED, "PERCENT", "FULL_WINDOW", True, False, False),
    "short but usable: warning and source": (
        _VARIED[:90],
        "PERCENT",
        "INSUFFICIENT_HISTORY",
        True,
        False,
        True,
    ),
    "no history: blocker only": ([], "PERCENT", "NO_HISTORY", False, True, False),
    "too few changes: warning and blocker": (
        _VARIED[:2],
        "PERCENT",
        "INSUFFICIENT_HISTORY",
        False,
        True,
        True,
    ),
    "unconfirmed unit: figure, no source": (_VARIED, None, "FULL_WINDOW", True, False, False),
    "flat window: zero figure, no source": (_FLAT, "PERCENT", "FULL_WINDOW", True, False, False),
}


@pytest.mark.parametrize(
    ("values", "field_unit", "status", "figures", "blocked", "warned"),
    list(_REAL_ROUTE_CASES.values()),
    ids=list(_REAL_ROUTE_CASES),
)
def test_the_card_accepts_what_the_real_route_actually_serializes(
    server_url, page, monkeypatch, values, field_unit, status, figures, blocked, warned
) -> None:
    """The tests that do not hand-build the payload (Codex review, PR #200).

    Every other test in this file fulfils the request with a fixture I wrote,
    so none of them proves `validatePayload` accepts what the calculator and
    the route actually emit -- and the fixture had already drifted from that
    shape without any test noticing. Here only the #196 Bloomberg loader is
    stubbed; the request reaches the real `ThreadingHTTPServer` route, which
    runs the real calculator and the real serializer, and the card has to
    render the answer it gets.

    Parameterised over every canonical branch of that serializer rather than
    the one happy path: warning-plus-source, blocker-only,
    warning-plus-blocker, figure-without-source from an unconfirmed unit, and
    a zero-sigma window whose publication is refused. Three of the findings
    that reached this file landed on branches the single-shape version could
    not reach.

    This is the check the malformed-payload cases structurally cannot make:
    those prove the card refuses what the server would never send, and this
    proves it accepts what the server does send.
    """

    history = _real_history(values, field_unit=field_unit)
    monkeypatch.setattr(
        server_module, "load_bloomberg_bond_yield_history", lambda **kwargs: history
    )
    expected = calculate_historical_yield_volatility(history, requested_observation_count=180)
    assert expected.window_status.value == status

    _route_other_markets_away(page)
    _open_card(page, server_url)
    _fill_query(page, unit=field_unit or "")
    _calculate(page)
    _wait_for_result(page)

    assert _is_actually_hidden(page, "hyv-error")
    assert page.inner_text("#hyv-status").strip() == status
    assert _is_actually_hidden(page, "hyv-blockers") is not blocked
    assert _is_actually_hidden(page, "hyv-warnings") is not warned

    if figures:
        # Digit for digit against the figures the real calculator produced.
        assert page.inner_text("#hyv-annualized").strip() == repr(expected.annualized_yield_vol)
        assert page.inner_text("#hyv-daily").strip() == repr(expected.daily_yield_vol)
    else:
        assert expected.annualized_yield_vol is None
        assert page.inner_text("#hyv-annualized").strip() == "\u2014"

    assert page.inner_text("#hyv-actual-count").strip().startswith(
        str(expected.observation_count)
    )
    assert page.inner_text("#hyv-change-count").strip() == str(expected.yield_change_count)

    # The published source appears exactly when the real publication helper
    # produced one, which is the branch each case is named for.
    try:
        historical_yield_vol_volatility_input(expected)
    except HistoricalYieldVolUnavailableError:
        published = False
    else:
        published = True
    assert _is_actually_hidden(page, "hyv-source-block") is not True
    detail = page.inner_text("#hyv-source-detail")
    assert ("HISTORICAL_YIELD_VOL_MO" in detail) is published


@pytest.mark.parametrize(
    "key",
    ["source_system", "security", "yield_field", "acquired_at", "calculated_at"],
)
def test_whitespace_only_provenance_is_refused(server_url, page, key) -> None:
    # Truthy and renders as nothing: the card showed visually blank audit
    # provenance beside the risk figure. `isNonBlankString` was already used
    # for the nested source's strings and the unavailability reason; this
    # loop -- the original one -- was still on truthiness (Codex review, #200).
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, key: "   "})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert f'"{key}" is missing' in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "dates",
    [
        # Begins before the requested start.
        ["2025-12-31"] + _OBSERVATION_DATES[1:],
        # Ends after the requested end.
        _OBSERVATION_DATES[:-1] + ["2026-09-02"],
    ],
)
def test_observations_outside_the_requested_range_are_refused(server_url, page, dates) -> None:
    # Ascending, counted and endpoint-matched was not enough: the card
    # displayed a Bloomberg request range its own observations contradict.
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "observation_dates": dates,
            "first_observation_date": dates[0],
            "last_observation_date": dates[-1],
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "outside the requested range" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "key",
    [
        "series_observation_count",
        "requested_observation_count",
        "observation_count",
        "yield_change_count",
    ],
)
def test_a_count_javascript_cannot_represent_exactly_is_refused(server_url, page, key) -> None:
    # 9007199254740993 on the wire is parsed as ...992, so the card would
    # display a different observation contract from the one it received. The
    # query path has refused this since the third round; the response path had
    # not (Codex review, PR #200).
    _route_other_markets_away(page)
    _route_vol(page, raw_payload=_raw_payload_with(key, "9007199254740993"))
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "this page can represent" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


def test_a_repr_javascript_would_spell_differently_is_still_accepted(
    server_url, page
) -> None:
    # The rule compares values, not spellings. Python's repr of this float is
    # "1e-05" and JavaScript's String() of it is "0.00001"; a string-equality
    # rule would refuse an entirely honest payload and print nothing.
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "annualized_yield_vol": 1e-05,
            "annualized_yield_vol_text": "1e-05",
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-annualized").strip() == "1e-05"


def test_the_full_provenance_is_on_screen(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-methodology-value").strip() == "HISTORICAL_YIELD_VOL_MO"
    assert page.inner_text("#hyv-security").strip() == "SYNTHETIC TEST Corp"
    assert page.inner_text("#hyv-field").strip() == _FIELD
    assert page.inner_text("#hyv-unit").strip() == "PERCENT"
    assert page.inner_text("#hyv-source").strip() == "BLOOMBERG_DAPI"
    assert page.inner_text("#hyv-requested-count").strip() == "180"
    assert "180 of 200" in page.inner_text("#hyv-actual-count")
    assert page.inner_text("#hyv-change-count").strip() == "179"
    assert page.inner_text("#hyv-first-observation").strip() == "2026-01-08"
    assert page.inner_text("#hyv-last-observation").strip() == "2026-09-01"
    assert page.inner_text("#hyv-stdev-convention").strip() == "SAMPLE_STDEV_S_DDOF_1"
    assert "252" in page.inner_text("#hyv-annualization")
    assert page.inner_text("#hyv-acquired-at").strip() == "2026-08-31T14:05:00+00:00"
    assert page.inner_text("#hyv-calculated-at").strip() == "2026-08-31T14:05:02+00:00"


def test_a_full_window_shows_the_normalized_source_and_no_blockers(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-status").strip() == "FULL_WINDOW"
    assert _is_actually_hidden(page, "hyv-blockers")
    assert _is_actually_hidden(page, "hyv-warnings")
    detail = page.inner_text("#hyv-source-detail")
    assert "HISTORICAL_YIELD_VOL_MO" in detail
    assert "YIELD_VOL" in detail
    # The normalized value, its unit, the source unit and the exact factor are
    # all on screen, and the raw headline figure is not what was published.
    assert _NORMALIZED_TEXT in detail
    assert "DECIMAL_ANNUAL" in detail
    assert "PERCENT" in detail
    assert "0.01" in detail
    assert page.inner_text("#hyv-annualized").strip() == _ANNUALIZED_TEXT


def test_an_unconfirmed_unit_says_so_rather_than_naming_one(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload=_NO_UNIT_PAYLOAD)
    _open_card(page, server_url)
    _fill_query(page, unit="")
    _calculate(page)
    _wait_for_result(page)

    assert "not confirmed" in page.inner_text("#hyv-unit")
    assert "not confirmed" in page.inner_text("#hyv-annualized-unit")
    assert "cannot be normalized" in page.inner_text("#hyv-source-detail")


# --- short and absent history -------------------------------------------------


def test_a_short_window_never_looks_like_a_full_one(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload=_SHORT_PAYLOAD)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-status").strip() == "INSUFFICIENT_HISTORY"
    # A usable-but-qualified window shows in the warnings box, and the
    # blocking box stays away: the trader can tell "short" from "nothing".
    assert not _is_actually_hidden(page, "hyv-warnings")
    assert _is_actually_hidden(page, "hyv-blockers")
    assert "90 of the requested 180" in page.inner_text("#hyv-warning-list")
    assert "90 of 90" in page.inner_text("#hyv-actual-count")
    # Nothing is blocking, so the heading may say the number is usable.
    # (The heading is text-transform: uppercase, hence the case fold.)
    assert "usable" in page.inner_text("#hyv-warnings-title").lower()
    assert "INSUFFICIENT_HISTORY" in page.inner_text("#hyv-source-detail")


def test_zero_history_shows_no_number_and_no_substitute(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload=_NO_HISTORY_PAYLOAD)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    assert page.inner_text("#hyv-status").strip() == "NO_HISTORY"
    assert page.inner_text("#hyv-annualized").strip() == "—"
    assert page.inner_text("#hyv-daily").strip() == "—"
    assert not _is_actually_hidden(page, "hyv-blockers")
    assert _is_actually_hidden(page, "hyv-warnings")
    assert "no approved proxy" in page.inner_text("#hyv-blocker-list")
    assert "no Historical Yield Vol is available" in page.inner_text("#hyv-source-detail")


def test_a_short_and_blocked_window_is_never_called_usable(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload=_BLOCKED_SHORT_PAYLOAD)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    # Both boxes are up, there is no number, and the warning heading must not
    # claim usability the result does not have.
    assert not _is_actually_hidden(page, "hyv-warnings")
    assert not _is_actually_hidden(page, "hyv-blockers")
    assert page.inner_text("#hyv-annualized").strip() == "—"
    heading = page.inner_text("#hyv-warnings-title").lower()
    assert "usable" not in heading
    assert "not a full-window result" in heading


# --- refusals -----------------------------------------------------------------


def test_a_server_refusal_is_shown_verbatim(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(
        page,
        error="Bloomberg returned 1 row(s) with no Yield value inside the selected window",
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "no Yield value" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize(
    "source",
    [
        {},  # truthy, so the renderer used to draw a normal block of dashes
        {"source_system": "HISTORICAL_YIELD_VOL_MO"},
        {**_FULL_PAYLOAD["volatility_source"], "volatility": None},
        {**_FULL_PAYLOAD["volatility_source"], "volatility": "0.06"},
        {**_FULL_PAYLOAD["volatility_source"], "volatility_unit": ""},
        [],  # an array is an object to typeof, and is not one
    ],
)
def test_a_half_understood_volatility_source_is_refused(server_url, page, source) -> None:
    # A published risk source drawn from a payload the page does not
    # understand is worse than no source at all (Codex review, PR #200).
    _route_other_markets_away(page)
    _route_vol(page, payload={**_FULL_PAYLOAD, "volatility_source": source})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


def test_no_source_and_no_reason_is_also_refused(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "volatility_source": None,
            "volatility_source_unavailable_reason": None,
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "no reason for its absence" in page.inner_text("#hyv-error-detail")


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_blank_source_unavailability_reason_is_refused(server_url, page, reason) -> None:
    # typeof "" === "string", so a type check alone accepted it and text()
    # rendered it as an em dash: an unpublished risk figure presented with no
    # explanation of why publication was refused (Codex review, PR #200).
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "volatility_source": None,
            "volatility_source_unavailable_reason": reason,
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "no reason for its absence" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_string_inside_the_published_source_is_refused(server_url, page, blank) -> None:
    # Same rule, same file, the other branch: a whitespace-only audit line
    # renders as whitespace on the card rather than as the calculation
    # provenance the block exists to carry.
    _route_other_markets_away(page)
    _route_vol(
        page,
        payload={
            **_FULL_PAYLOAD,
            "volatility_source": {
                **_FULL_PAYLOAD["volatility_source"],
                "override_or_fallback_audit": blank,
            },
        },
    )
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "override_or_fallback_audit" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


def test_a_malformed_answer_is_refused_rather_than_displayed(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page, payload={"window_status": "FULL_WINDOW"})
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    assert "malformed response" in page.inner_text("#hyv-error-detail")
    assert _is_actually_hidden(page, "hyv-result")


# --- state does not survive between calculations -------------------------------


def test_a_second_calculation_leaves_nothing_of_the_first_on_screen(server_url, page) -> None:
    """A blocked window followed by a clean one must not keep the blocked state.

    The card's boxes and its warnings heading are set from the payload every
    render; this drives the transition a trader actually makes rather than
    trusting that.
    """

    _route_other_markets_away(page)
    _open_card(page, server_url)
    _fill_query(page)

    # First: short AND blocked -- both boxes up, no number, neutral heading.
    page.unroute(_ROUTE)
    _route_vol(page, payload=_BLOCKED_SHORT_PAYLOAD)
    _calculate(page)
    _wait_for_result(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-blockers"))
    assert "usable" not in page.inner_text("#hyv-warnings-title").lower()

    # Then: a clean full window over the top of it.
    page.unroute(_ROUTE)
    _route_vol(page)
    _calculate(page)
    _wait_until(lambda: _is_actually_hidden(page, "hyv-blockers"))

    assert _is_actually_hidden(page, "hyv-warnings")
    assert page.inner_text("#hyv-status").strip() == "FULL_WINDOW"
    assert page.inner_text("#hyv-annualized").strip() == _ANNUALIZED_TEXT
    assert "HISTORICAL_YIELD_VOL_MO" in page.inner_text("#hyv-source-detail")


def test_a_failure_after_a_result_hides_the_stale_result(server_url, page) -> None:
    _route_other_markets_away(page)
    _open_card(page, server_url)
    _fill_query(page)

    _route_vol(page)
    _calculate(page)
    _wait_for_result(page)
    assert page.inner_text("#hyv-annualized").strip() == _ANNUALIZED_TEXT

    page.unroute(_ROUTE)
    _route_vol(page, error="synthetic refusal")
    _calculate(page)
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-error"))

    # The previous run's numbers must not still be readable underneath.
    assert _is_actually_hidden(page, "hyv-result")
    assert page.evaluate("() => window.__shioriTestHistoricalYieldVolPayload()") is None


# --- no side effects on the #196 view or on pricing ---------------------------


def test_calculating_does_not_disturb_the_196_history_view(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_vol(page)
    _open_card(page, server_url)
    _fill_query(page)
    _calculate(page)
    _wait_for_result(page)

    # The #196 view was never loaded, and this card did not load it: its own
    # idle state still stands and its payload is still empty.
    assert not _is_actually_hidden(page, "byh-idle")
    assert page.evaluate("() => window.__shioriTestYieldHistoryPayload()") is None
    assert page.evaluate("() => window.__shioriTestYieldHistoryRequestedRoutes()") == []
