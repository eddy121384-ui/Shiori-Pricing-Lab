"""Browser-driven tests for Markets -> Bond Yield History (Issue #196).

Exercises ``bond_yield_history_view.js`` -- the third market-view tab, the
query form, the observation table, the Yield-history line chart, and the
optional daily-change display derivative -- against one real
``ThreadingHTTPServer`` and one real headless Chromium page. The one
read-only route is intercepted at the browser network layer with
``page.route``, the pattern the sibling Markets browser files already use, so
the fixtures are deterministic and no Bloomberg session is involved.

Every value and date in the fixtures below is made up. Nothing here is a
Bloomberg value, and no mnemonic below is a real Bloomberg field -- the
production loader has no default field precisely because the real one is
workstation evidence, not repository content.

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

pytestmark = pytest.mark.skipif(
    not _PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed locally (local-only skip; CI hard-fails instead)",
)

if _PLAYWRIGHT_AVAILABLE:
    from playwright.sync_api import sync_playwright

_CHROMIUM_EXECUTABLE_PATH = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")

_ROUTE = "**/api/bloomberg/bond-yield-history"
_ISIN = "US0000000000"
_FIELD = "SYNTHETIC_TEST_YIELD_FIELD"


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


# --- Synthetic fixtures ------------------------------------------------------
# A weekend gap (Jan 9 -> Jan 12), a returned row carrying no value (Jan 13),
# and a value whose exact decimal (4.1200000) a toFixed renderer would
# silently rewrite.

_OBSERVATIONS = [
    {"date": "2026-01-08", "yield_value": 4.0, "raw_value": "4.0"},
    {"date": "2026-01-09", "yield_value": 4.12, "raw_value": "4.1200000"},
    {"date": "2026-01-12", "yield_value": 4.25, "raw_value": "4.25"},
    {"date": "2026-01-13", "yield_value": None, "raw_value": None},
    {"date": "2026-01-14", "yield_value": 4.5, "raw_value": "4.50"},
]

_PAYLOAD = {
    "requested_identifier": f"/isin/{_ISIN}",
    "security": "SYNTHETIC TEST Corp",
    "yield_field": _FIELD,
    "field_meaning": None,
    "field_unit": None,
    "requested_start_date": "2026-01-01",
    "requested_end_date": "2026-01-31",
    "source_system": "BLOOMBERG_DAPI",
    "acquired_at": "2026-08-31T14:05:00+00:00",
    "observation_count": len(_OBSERVATIONS),
    "first_observation_date": "2026-01-08",
    "last_observation_date": "2026-01-14",
    "observations": _OBSERVATIONS,
}

_EMPTY_PAYLOAD = {
    **_PAYLOAD,
    "observation_count": 0,
    "first_observation_date": None,
    "last_observation_date": None,
    "observations": [],
}


def _route_history(page, *, payload=None, error: str | None = None, status: int = 502):
    """Serve the one read-only route from the fixtures above."""

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
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload if payload is not None else _PAYLOAD),
        )

    page.route(_ROUTE, _handle)
    return calls


def _route_other_markets_away(page):
    """Keep the two sibling Markets views' own fetches off live Bloomberg."""

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


def _open_yield_history(page, server_url: str) -> None:
    page.goto(f"{server_url}/")
    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))
    page.click("#markets-tab-yield-history")


def _fill_query(page, *, identifier=_ISIN, field=_FIELD, start="2026-01-01", end="2026-01-31"):
    page.fill("#byh-identifier", identifier)
    page.fill("#byh-yield-field", field)
    page.fill("#byh-start", start)
    page.fill("#byh-end", end)


def _load(page) -> None:
    page.click("#byh-load-btn")


def _wait_for_series(page) -> None:
    _wait_until(lambda: not _is_actually_hidden(page, "byh-table-card"))


def _open_raw_details(page) -> None:
    # The raw chart/table live behind a native <details> that is closed by
    # default after every successful Load (Issue #208), so anything inside
    # it -- the daily-change checkbox included -- is not actionable until a
    # trader (or this test) opens it, exactly like a real click would.
    page.click("#byh-raw-summary")
    _wait_until(lambda: page.is_visible("#byh-show-change"))


def _table_rows(page):
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('#byh-table-body tr')).map(
             row => Array.from(row.children).map(cell => cell.textContent)
           )"""
    )


# --- the market-view selector -------------------------------------------------


def test_markets_offers_bond_yield_history_as_a_third_view(server_url, page) -> None:
    _route_other_markets_away(page)
    page.goto(f"{server_url}/")
    page.click("#nav-markets")
    _wait_until(lambda: not _is_actually_hidden(page, "view-markets"))

    assert page.inner_text("#markets-tab-yield-history").strip() == "Bond Yield History"
    assert _is_actually_hidden(page, "markets-panel-yield-history")
    assert not _is_actually_hidden(page, "markets-panel-curve")


def test_selecting_the_tab_shows_only_that_panel(server_url, page) -> None:
    _route_other_markets_away(page)
    _open_yield_history(page, server_url)

    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-yield-history"))
    assert _is_actually_hidden(page, "markets-panel-curve")
    assert _is_actually_hidden(page, "markets-panel-vol-surface")
    assert page.get_attribute("#markets-tab-yield-history", "aria-selected") == "true"
    assert page.get_attribute("#markets-tab-curve", "aria-selected") == "false"


def test_the_other_two_market_views_still_switch(server_url, page) -> None:
    _route_other_markets_away(page)
    _open_yield_history(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-yield-history"))

    page.click("#markets-tab-vol-surface")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-vol-surface"))
    assert _is_actually_hidden(page, "markets-panel-yield-history")

    page.click("#markets-tab-curve")
    _wait_until(lambda: not _is_actually_hidden(page, "markets-panel-curve"))
    assert _is_actually_hidden(page, "markets-panel-vol-surface")


def test_opening_the_view_loads_nothing_on_its_own(server_url, page) -> None:
    _route_other_markets_away(page)
    calls = _route_history(page)
    _open_yield_history(page, server_url)
    _wait_until(lambda: not _is_actually_hidden(page, "byh-idle"))

    assert calls == []
    assert _is_actually_hidden(page, "byh-table-card")


# --- the Yield field is never guessed ----------------------------------------


def test_an_empty_yield_field_sends_no_request_at_all(server_url, page) -> None:
    # The box carries a convenience default (Issue #208), but clearing it
    # deliberately is still refused rather than guessed or substituted.
    _route_other_markets_away(page)
    calls = _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page, field="")
    _load(page)
    _wait_until(lambda: not _is_actually_hidden(page, "byh-error"))

    assert calls == []
    assert "will not guess or substitute a field of its own" in page.inner_text("#byh-error-detail")


def test_the_traders_own_field_is_what_is_requested(server_url, page) -> None:
    _route_other_markets_away(page)
    calls = _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page, field="ANOTHER_TEST_FIELD", start="2025-03-04", end="2025-09-09")
    _load(page)
    _wait_for_series(page)

    assert calls == [
        {
            "bond_identifier": _ISIN,
            "yield_field": "ANOTHER_TEST_FIELD",
            "start_date": "2025-03-04",
            "end_date": "2025-09-09",
        }
    ]


# --- editable defaults (Issue #208) -------------------------------------------


def test_the_yield_field_starts_on_yld_ytm_mid_and_stays_freely_editable(
    server_url, page
) -> None:
    """A UI convenience default, never a server-side guess.

    The box is pre-filled so a trader is not forced to type the common case
    every time, but nothing about that changes what gets sent: replacing it
    is what is submitted, exactly like any other editable input.
    """

    _route_other_markets_away(page)
    calls = _route_history(page)
    _open_yield_history(page, server_url)

    assert page.input_value("#byh-yield-field") == "YLD_YTM_MID"

    _fill_query(page, field="ANOTHER_TEST_FIELD")
    _load(page)
    _wait_for_series(page)

    assert calls[0]["yield_field"] == "ANOTHER_TEST_FIELD"


def test_an_inverted_date_range_is_refused_before_the_request(server_url, page) -> None:
    _route_other_markets_away(page)
    calls = _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page, start="2026-03-01", end="2026-01-01")
    _load(page)
    _wait_until(lambda: not _is_actually_hidden(page, "byh-error"))

    assert calls == []
    assert "must not be after" in page.inner_text("#byh-error-detail")


# --- layout: Historical Yield Vol before raw history, collapsed (Issue #208) --


def test_historical_yield_vol_appears_before_the_raw_data_section(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    # DOM order, not visual position: unambiguous regardless of CSS.
    hyv_before_raw = page.evaluate(
        """() => {
             const hyv = document.querySelector('#markets-panel-yield-history .hyv-card');
             const raw = document.getElementById('byh-raw-details');
             const relation = hyv.compareDocumentPosition(raw);
             return Boolean(relation & Node.DOCUMENT_POSITION_FOLLOWING);
           }"""
    )
    assert hyv_before_raw is True


def test_the_raw_data_section_is_exposed_closed_and_named_by_its_count(
    server_url, page
) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    # Exposed -- the wrapper itself is no longer [hidden] once a series loads.
    assert not _is_actually_hidden(page, "byh-raw-details")
    # But CLOSED: a native <details> defaults to closed, and showOnly()
    # re-asserts that explicitly on every fresh successful load so a trader
    # who left it open for a previous bond does not carry that state forward.
    assert page.eval_on_selector("#byh-raw-details", "el => el.open") is False
    assert (
        page.inner_text("#byh-raw-summary")
        == f"Historical Yield Data — {len(_OBSERVATIONS)} observations"
    )


def test_opening_the_raw_data_section_reveals_the_chart_and_table(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    # Closed: neither is actionable/visible to a trader yet, even though both
    # were already rendered into the DOM (Codex-caught cases elsewhere in
    # this suite read them by attribute regardless of visibility -- this
    # assertion is deliberately the opposite check, on rendered visibility).
    assert not page.is_visible("#byh-chart-svg-wrap svg")
    assert not page.is_visible("#byh-table")

    page.click("#byh-raw-summary")

    assert page.is_visible("#byh-chart-svg-wrap svg")
    assert page.is_visible("#byh-table")


def test_recalculating_historical_yield_vol_does_not_need_the_raw_section_open(
    server_url, page
) -> None:
    """The two cards are two independent Bloomberg requests (Issue #197/#208).

    Loading the raw #196 history leaves the collapsible section closed; the
    Historical Yield Vol card above it must calculate successfully without
    the trader ever opening that section, and opening it is never a
    side-effect of calculating.
    """

    from test_standalone_option_workbench_historical_yield_vol_view_browser import (
        _FULL_PAYLOAD,
    )

    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    assert page.eval_on_selector("#byh-raw-details", "el => el.open") is False

    hyv_calls: list[dict] = []

    def _handle_hyv(route):
        hyv_calls.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_FULL_PAYLOAD))

    page.route("**/api/bloomberg/historical-yield-vol", _handle_hyv)
    page.click("#hyv-calculate-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "hyv-result"))

    assert len(hyv_calls) == 1
    # Untouched by the calculation that just happened.
    assert page.eval_on_selector("#byh-raw-details", "el => el.open") is False


# --- the table shows exactly what came back -----------------------------------


def test_the_table_shows_every_returned_value_to_its_exact_digits(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    rows = _table_rows(page)
    assert [row[0] for row in rows] == [
        "2026-01-08",
        "2026-01-09",
        "2026-01-12",
        "2026-01-13",
        "2026-01-14",
    ]
    # 4.1200000 is not rewritten to 4.12, and 4.50 is not rewritten to 4.5.
    assert [row[1] for row in rows] == ["4.0", "4.1200000", "4.25", "—", "4.50"]


def test_a_gap_is_a_gap_and_a_valueless_row_is_an_em_dash(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    rows = _table_rows(page)
    # The weekend between Jan 9 and Jan 12 is absent, not filled with rows.
    assert "2026-01-10" not in [row[0] for row in rows]
    assert "2026-01-11" not in [row[0] for row in rows]
    # The row Bloomberg returned with no value shows as missing, never as 0.
    assert rows[3] == ["2026-01-13", "—", "—"]


def test_the_provenance_the_trader_audits_is_on_screen(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    assert page.inner_text("#byh-requested-identifier") == f"/isin/{_ISIN}"
    assert page.inner_text("#byh-security") == "SYNTHETIC TEST Corp"
    assert page.inner_text("#byh-field-mnemonic") == _FIELD
    assert page.inner_text("#byh-source") == "BLOOMBERG_DAPI"
    assert page.inner_text("#byh-requested-range") == "2026-01-01 → 2026-01-31"
    assert page.inner_text("#byh-observation-count") == "5"
    assert page.inner_text("#byh-first-observation") == "2026-01-08"
    assert page.inner_text("#byh-last-observation") == "2026-01-14"
    assert page.inner_text("#byh-acquired-at") == "2026-08-31T14:05:00+00:00"


def test_an_unconfirmed_unit_is_never_claimed(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    assert page.inner_text("#byh-field-unit") == "Not confirmed by this request"
    assert page.inner_text("#byh-field-meaning") == "Not confirmed by this request"
    # The y-axis label lives inside the collapsed raw-data section (Issue
    # #208): open it before reading rendered text, exactly as a trader would
    # have to.
    _open_raw_details(page)
    assert "unit not confirmed" in page.inner_text("#byh-chart-ylabel")


def test_a_confirmed_unit_is_shown_verbatim(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(
        page,
        payload={**_PAYLOAD, "field_unit": "percent", "field_meaning": "Synthetic test meaning"},
    )
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    assert page.inner_text("#byh-field-unit") == "percent"
    assert page.inner_text("#byh-field-meaning") == "Synthetic test meaning"
    _open_raw_details(page)
    assert page.inner_text("#byh-chart-ylabel") == f"{_FIELD} (percent)"


# --- the chart draws the same observations ------------------------------------


def test_the_chart_uses_exactly_the_observations_the_table_shows(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    points = page.evaluate("() => window.__shioriTestYieldHistoryChartPoints()")
    rows = _table_rows(page)
    assert [point["date"] for point in points] == [row[0] for row in rows]
    assert [point["value"] for point in points] == [4.0, 4.12, 4.25, None, 4.5]


def test_the_chart_draws_a_dot_only_where_a_value_came_back(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    assert page.eval_on_selector_all("#byh-chart-svg-wrap circle.byh-dot", "els => els.length") == 4
    titles = page.eval_on_selector_all(
        "#byh-chart-svg-wrap circle.byh-dot title", "els => els.map(el => el.textContent)"
    )
    assert titles == [
        "2026-01-08 = 4.0",
        "2026-01-09 = 4.1200000",
        "2026-01-12 = 4.25",
        "2026-01-14 = 4.50",
    ]


def test_the_line_joins_across_dates_bloomberg_omitted(server_url, page) -> None:
    """A weekend is not an unresolved observation (Codex review, PR #198).

    With ACTIVE_DAYS_ONLY Bloomberg omits every weekend and holiday, so
    breaking the run on non-consecutive dates would break at every weekend --
    and telling "market closed" from "no history here" needs the security's
    own trading calendar, which this view does not have and must not invent.
    The dots stay the data; the axis is scaled by real dates so the omitted
    stretch reads as a wider span; the table states which dates came back.
    """

    _route_other_markets_away(page)
    _route_history(
        page,
        payload={
            **_PAYLOAD,
            "observation_count": 3,
            "first_observation_date": "2026-01-08",
            "last_observation_date": "2026-01-13",
            "observations": [
                {"date": "2026-01-08", "yield_value": 4.0, "raw_value": "4.0"},
                # Jan 9 is a Friday and Jan 12 the following Monday: the
                # weekend between them is absent from the response.
                {"date": "2026-01-09", "yield_value": 4.12, "raw_value": "4.12"},
                {"date": "2026-01-12", "yield_value": 4.25, "raw_value": "4.25"},
            ],
        },
    )
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    polylines = page.eval_on_selector_all(
        "#byh-chart-svg-wrap polyline.byh-line", "els => els.map(el => el.getAttribute('points'))"
    )
    assert len(polylines) == 1
    assert len(polylines[0].split(" ")) == 3
    # No point was manufactured for either omitted date, and no row exists for
    # them either -- the gap is real, it is simply not a break in the line.
    assert page.eval_on_selector_all("#byh-chart-svg-wrap circle.byh-dot", "els => els.length") == 3
    assert [row[0] for row in _table_rows(page)] == ["2026-01-08", "2026-01-09", "2026-01-12"]


def test_the_gap_is_visible_as_distance_rather_than_as_an_invented_point(
    server_url, page
) -> None:
    """The x axis is scaled by real dates, so an omitted stretch is wider."""

    _route_other_markets_away(page)
    _route_history(
        page,
        payload={
            **_PAYLOAD,
            "observation_count": 3,
            "first_observation_date": "2026-01-08",
            "last_observation_date": "2026-01-28",
            "observations": [
                {"date": "2026-01-08", "yield_value": 4.0, "raw_value": "4.0"},
                {"date": "2026-01-09", "yield_value": 4.12, "raw_value": "4.12"},
                # Nineteen days later: a stretch this bond has no history over.
                {"date": "2026-01-28", "yield_value": 4.25, "raw_value": "4.25"},
            ],
        },
    )
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    xs = page.eval_on_selector_all(
        "#byh-chart-svg-wrap circle.byh-dot", "els => els.map(el => Number(el.getAttribute('cx')))"
    )
    one_day = xs[1] - xs[0]
    nineteen_days = xs[2] - xs[1]
    # Positioned by elapsed time, not by row index: the empty stretch is
    # visibly nineteen times the one-day step, not the same width as it.
    assert nineteen_days > one_day * 15


def test_the_line_never_spans_a_valueless_row(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    # Jan 13 carried no value, so the run before it ends and a new one starts:
    # three points, then one -- and a single-point run draws no polyline.
    polylines = page.eval_on_selector_all(
        "#byh-chart-svg-wrap polyline.byh-line", "els => els.map(el => el.getAttribute('points'))"
    )
    assert len(polylines) == 1
    assert len(polylines[0].split(" ")) == 3


def test_every_x_axis_label_is_a_real_observation_date(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    labels = page.eval_on_selector_all(
        "#byh-chart-svg-wrap text.byh-x-tick", "els => els.map(el => el.textContent)"
    )
    assert labels
    assert set(labels) <= {observation["date"] for observation in _OBSERVATIONS}


def test_a_y_axis_tick_is_marked_as_a_number_the_axis_invented(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    ticks = page.eval_on_selector_all(
        "#byh-chart-svg-wrap text.byh-y-tick", "els => els.map(el => el.textContent)"
    )
    assert ticks
    assert all(tick.startswith("~") for tick in ticks)


# --- the daily change display derivative --------------------------------------


def test_the_daily_change_column_is_hidden_until_asked_for(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    assert page.eval_on_selector(
        "#byh-table thead .byh-change-col", "el => getComputedStyle(el).display"
    ) == "none"
    assert _is_actually_hidden(page, "byh-change-note")


def test_the_daily_change_is_exact_and_never_bridges_a_hole(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)
    _open_raw_details(page)
    page.check("#byh-show-change")

    rows = _table_rows(page)
    # Exact decimal subtraction, carried at the wider of the two operands'
    # scales: 4.1200000 - 4.0 is 0.1200000, not the 0.11999999999999957 a
    # binary float subtraction would put on screen, and 4.25 - 4.1200000 is
    # 0.1300000 rather than a rounded 0.13. The two rows either side of the
    # valueless Jan 13 both read as unstateable -- a change is never carried
    # across a hole.
    assert [row[2] for row in rows] == ["—", "0.1200000", "0.1300000", "—", "—"]
    assert not _is_actually_hidden(page, "byh-change-note")


def test_the_daily_change_carries_no_volatility_statistic(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)
    _open_raw_details(page)
    page.check("#byh-show-change")

    panel_text = page.inner_text("#markets-panel-yield-history").lower()
    for forbidden in ("standard deviation", "annualiz", "annualis", "historical vol"):
        # The panel says what it does NOT compute; it must never show a result.
        assert "= " + forbidden not in panel_text
    assert page.evaluate("() => window.__shioriTestYieldHistoryDailyChanges().length") == 5


# --- empty and failed answers -------------------------------------------------


def test_an_empty_series_is_shown_as_an_answer_not_a_failure(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page, payload=_EMPTY_PAYLOAD)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_until(lambda: not _is_actually_hidden(page, "byh-empty"))

    assert _is_actually_hidden(page, "byh-error")
    assert _is_actually_hidden(page, "byh-chart-card")
    assert _is_actually_hidden(page, "byh-table-card")
    # Provenance stays: "nothing came back for this field" is worth auditing.
    assert not _is_actually_hidden(page, "byh-provenance")
    assert page.inner_text("#byh-observation-count") == "0"


def test_a_bloomberg_failure_is_shown_verbatim_and_draws_nothing(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(page, error="Bloomberg DAPI field exception for BAD_FLD")
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_until(lambda: not _is_actually_hidden(page, "byh-error"))

    assert page.inner_text("#byh-error-detail") == "Bloomberg DAPI field exception for BAD_FLD"
    assert _is_actually_hidden(page, "byh-chart-card")
    assert _is_actually_hidden(page, "byh-table-card")


def test_a_malformed_payload_is_refused_rather_than_drawn(server_url, page) -> None:
    _route_other_markets_away(page)
    _route_history(
        page,
        payload={
            **_PAYLOAD,
            "observations": [{"date": "2026-01-08", "yield_value": "4.0", "raw_value": "4.0"}],
        },
    )
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_until(lambda: not _is_actually_hidden(page, "byh-error"))

    assert "non-finite value" in page.inner_text("#byh-error-detail")
    assert _is_actually_hidden(page, "byh-table-card")


# --- read-only, offline-safe --------------------------------------------------


def test_the_view_calls_only_the_one_read_only_route(server_url, page) -> None:
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)
    _open_raw_details(page)
    page.check("#byh-show-change")

    assert page.evaluate("() => window.__shioriTestYieldHistoryRequestedRoutes()") == [
        "/api/bloomberg/bond-yield-history"
    ]
    # Nothing this view did reached a pricing, capture, store, or export route.
    forbidden = ("/api/vcub/", "/api/price", "/api/case", "/api/export/", "/api/vol-surface/")
    for url in requests:
        assert not any(fragment in url for fragment in forbidden), url


def test_the_page_loads_no_script_or_style_from_another_origin(server_url, page) -> None:
    # Issue #196: the Markets page must render the same with corporate
    # internet/CDN access blocked, so nothing it draws may come from off-origin.
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    external = page.evaluate(
        """() => Array.from(document.querySelectorAll('script[src], link[href]'))
             .map(el => el.src || el.href)
             .filter(url => !url.startsWith(location.origin))"""
    )
    assert external == []


def test_every_request_the_page_makes_is_same_origin(server_url, page) -> None:
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    _route_other_markets_away(page)
    _route_history(page)
    _open_yield_history(page, server_url)
    _fill_query(page)
    _load(page)
    _wait_for_series(page)

    assert requests
    for url in requests:
        assert url.startswith(server_url), url
