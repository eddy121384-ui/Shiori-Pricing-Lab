"""Browser-driven tests for the Historical vol source panel (Issue #214).

Exercises the Volatility row's ``Vol source`` selector, the
``BOND_OPTION_PRICE_BASIS`` selector and the Historical Yield Vol ->
Equivalent Price Vol panel in ``script.js``, against one real
``ThreadingHTTPServer`` and one real headless Chromium page. The Bloomberg
lookup and the derivation route are intercepted at the browser network layer
with ``page.route`` -- the pattern the sibling browser files already use -- so
the fixtures are deterministic and no Bloomberg session is involved.

Every value below is made up. No Bloomberg value, no real Yield series and no
real field mnemonic appears here.

The invariants this file exists to hold down:

* the page displays the server's derivation and computes nothing -- the
  fixture's figures are deliberately not the product of anything, so a page
  that started deriving its own sigma_P would stop matching the payload;
* the Historical source is never selected or adopted on the trader's behalf;
* a derived value is withdrawn the instant an input it depended on moves --
  a new bond, a Clear, or a change of price basis;
* an unusable result refuses ``Use for Pricing`` and states the real reason.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test files: locally, missing Playwright is a skip; in CI
(``CI=true``) it is a hard collection-time error.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
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

_ROUTE = "**/api/pricing/historical-equivalent-price-vol"
_LOOKUP_ROUTE = "**/api/bloomberg/bond"
_HISTORICAL_SOURCE = "HISTORICAL_YIELD_VOL_MO"

# Digits a toFixed/round renderer would silently rewrite, so a page that
# re-formatted instead of printing the server's own string would be caught.
_EQUIVALENT_PRICE_VOL = 0.0474956586
_ABSOLUTE_DURATION = 6.5
_YIELD_VOL_DECIMAL = 0.0073070244
_YIELD_VOL_PERCENT = 0.73070244


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

# Fixture shape, not market data: a synthetic USD bond with a regular
# semi-annual grid, which is what the reviewed adapter carries.
_BOND_MASTER = {
    **_EMPTY_BOND_MASTER,
    "coupon": 0.0375,
    "coupon_frequency": "SEMI_ANNUAL",
    "issue_date": "2024-01-31",
    "maturity_date": "2031-01-31",
    "first_coupon_date": "2024-07-31",
    "callable_flag": False,
    "sinkable_flag": False,
}


def _lookup_response(*, isin: str = "US91282CLJ89", **overrides) -> dict:
    payload = {
        "isin": isin,
        "cusip": "91282CLJ8",
        "name": "SYNTHETIC TEST NOTE",
        "currency": "USD",
        "quote_side": "MID",
        "clean_price_per_100": 99.75,
        "accrued_interest_per_100": 0.42,
        "acquired_at": "2026-07-20T11:28:00+08:00",
        "source_system": "BLOOMBERG_DAPI",
        "bond_master": _BOND_MASTER,
        "bond_master_raw": {
            "day_count": "ACT/ACT",
            "maturity_type": "AT MATURITY",
            "calc_type": "STREET CONVENTION",
        },
    }
    payload.update(overrides)
    return payload


def _derivation_payload(*, price_basis: str = "DIRTY", **overrides) -> dict:
    """One ``historical_volatility_source`` section, shaped like the route's.

    The figures are the issue's canonical fixture -- 6.5 x 0.0073070244 =
    0.0474956586 -- carried as both the number and the server's own text, the
    way the real route sends them.
    """

    section = {
        "vol_source": _HISTORICAL_SOURCE,
        "volatility_kind": "HISTORICAL_REALIZED",
        "volatility_basis": "EQUIVALENT_PRICE_VOL",
        "price_basis": price_basis,
        "disclosure": "Historical / realized proxy for internal-model reconciliation.",
        "security": "/isin/US91282CLJ89",
        "requested_identifier": "/isin/US91282CLJ89",
        "yield_field": "SYNTHETIC_TEST_YIELD_FIELD",
        "field_meaning": None,
        "historical_yield_vol_field_unit": "PERCENT",
        "historical_yield_vol_in_field_unit": _YIELD_VOL_PERCENT,
        "historical_yield_vol_in_field_unit_text": repr(_YIELD_VOL_PERCENT),
        "historical_yield_vol_decimal_annual": _YIELD_VOL_DECIMAL,
        "historical_yield_vol_decimal_annual_text": repr(_YIELD_VOL_DECIMAL),
        "historical_yield_vol_normalization_factor": 0.01,
        "historical_yield_vol_window_status": "FULL_WINDOW",
        "historical_yield_vol_observation_count": 181,
        "historical_yield_vol_requested_observation_count": 181,
        "historical_yield_vol_change_count": 180,
        "historical_yield_vol_convention": "SAMPLE_STDEV_S_DDOF_1",
        "historical_yield_vol_annualization_trading_days": 252,
        "historical_yield_vol_requested_start_date": "2026-01-02",
        "historical_yield_vol_requested_end_date": "2026-06-30",
        "historical_yield_vol_first_observation_date": "2026-01-02",
        "historical_yield_vol_last_observation_date": "2026-06-30",
        "historical_yield_vol_source_system": "BLOOMBERG_DAPI",
        "historical_yield_vol_acquired_at": "2026-07-20T11:20:00+08:00",
        "historical_yield_vol_calculated_at": "2026-07-20T11:20:01+08:00",
        "duration_convention_profile": "UST",
        "duration_pricing_timestamp": "2026-07-20T11:28:00+08:00",
        "duration_settlement_date": "2026-07-21",
        "duration_maturity_date": "2031-01-31",
        "duration_coupon_percent": 3.75,
        "duration_coupons_per_year": 2,
        "duration_day_count": "ACT_ACT_BOND",
        "duration_clean_price_per_100": 99.75,
        "duration_accrued_interest_per_100": 1.79,
        "duration_dirty_price_per_100": 101.54,
        "duration_basis_price_per_100": 101.54 if price_basis == "DIRTY" else 99.75,
        "duration_base_yield_percent": 4.01,
        "duration_price_derivative_per_unit_yield": -660.0,
        "modified_duration": _ABSOLUTE_DURATION,
        "absolute_modified_duration": _ABSOLUTE_DURATION,
        "absolute_modified_duration_text": repr(_ABSOLUTE_DURATION),
        "duration_type": (
            "MODIFIED_DURATION_DIRTY_PRICE"
            if price_basis == "DIRTY"
            else "MODIFIED_DURATION_CLEAN_PRICE"
        ),
        "duration_source": "SHIORI_DERIVED",
        "duration_methodology_version": "SHIORI_MODIFIED_DURATION_V1",
        "equivalent_price_vol": _EQUIVALENT_PRICE_VOL,
        "equivalent_price_vol_text": repr(_EQUIVALENT_PRICE_VOL),
        "equivalent_price_vol_unit": "DECIMAL_ANNUAL",
        "equivalent_price_vol_formula": "sigma_P = |D_B| x sigma_hist_abs",
        "equivalent_price_vol_methodology_version": (
            "ANNEX_A_V1_4_A_8_6_DURATION_FIRST_ORDER_V1"
        ),
        "calculated_at": "2026-07-20T11:28:05+08:00",
        "warnings": [],
        "override_or_fallback_audit": "SYNTHETIC TEST AUDIT",
        "requested_observation_count": 181,
    }
    section.update(overrides)
    return {"historical_volatility_source": section}


def _route_derivation(page, *, payload=None, status: int = 200, sent: list | None = None):
    def _handle(route, request):
        if sent is not None:
            sent.append(json.loads(request.post_data))
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload if payload is not None else _derivation_payload()),
        )

    page.route(_ROUTE, _handle)


def _load_bond(page, *, isin: str = "US91282CLJ89") -> None:
    payload = _lookup_response(isin=isin)

    def _handle(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route(_LOOKUP_ROUTE, _handle)
    page.fill("#bond-identifier-input", isin)
    page.click('#bond-quote-side-toggle .opt[data-value="MID"]')
    generation_before = page.evaluate("() => window.__shioriTestConventionProfileGeneration()")
    page.click("#load-bloomberg-bond-btn")
    page.wait_for_function(
        "before => window.__shioriTestConventionProfileGeneration() > before",
        arg=generation_before,
    )
    page.wait_for_function(
        "expected => document.querySelector('#resolved-bond-isin').textContent === expected",
        arg=isin,
    )
    page.unroute(_LOOKUP_ROUTE, _handle)
    candidates = page.wait_for_function(
        "() => window.__shioriTestConventionProfileCandidates()"
    ).json_value()
    if "UST" in candidates["candidates"]:
        page.select_option("#convention-profile-select", "UST")


def _select_historical_source(page) -> None:
    page.select_option("#vol-source-select", _HISTORICAL_SOURCE)


def _fill_query(page) -> None:
    page.fill("#hev-yield-field", "SYNTHETIC_TEST_YIELD_FIELD")
    page.fill("#hev-start", "2026-01-02")
    page.fill("#hev-end", "2026-06-30")


def _derive(page) -> None:
    page.click("#hev-derive-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolResult() !== null")


def _override_provenance(page) -> list[dict]:
    return page.evaluate("() => window.__shioriTestOverrideProvenance()")


def _is_actually_hidden(page, element_id: str) -> bool:
    return page.eval_on_selector(f"#{element_id}", "el => getComputedStyle(el).display") == "none"


# --- The source is explicit, never reached for -------------------------------


def test_the_panel_is_hidden_until_the_trader_selects_the_historical_source(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bond(page)

    assert page.input_value("#vol-source-select") == "DIRECT_PRICE_VOL"
    assert _is_actually_hidden(page, "historical-vol-panel")
    # And the volatility field is an ordinary trader entry.
    assert page.eval_on_selector("#volatility-input", "el => el.readOnly") is False

    _select_historical_source(page)

    assert not _is_actually_hidden(page, "historical-vol-panel")
    # In Historical mode the number is the server's to supply, so the field
    # cannot be typed into -- the manual re-entry this source removes.
    assert page.eval_on_selector("#volatility-input", "el => el.readOnly") is True


def test_selecting_the_source_does_not_by_itself_derive_or_price_anything(
    server_url, page
) -> None:
    sent: list = []
    page.goto(f"{server_url}/")
    _route_derivation(page, sent=sent)
    _load_bond(page)
    _select_historical_source(page)

    # No request was made, nothing was adopted, and the volatility group is
    # still outstanding so Price stays blocked.
    assert sent == []
    assert page.evaluate("() => window.__shioriTestHistoricalVolAdoptedText()") is None
    assert page.input_value("#volatility-input") == ""
    assert "vol-review" in page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")


def test_the_unresolved_panel_explains_the_historical_step_not_the_manual_one(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _load_bond(page)
    _select_historical_source(page)
    page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")

    page.click('[data-workflow-group="vol-review"]')
    # The vol row's own note now describes the derived source honestly.
    note = page.text_content("#vol-sourcing-note")
    assert "Derived, not sourced" in note
    assert "not a market-implied volatility" in note
    assert "YIELD_VOL still never reaches Black-76" in note


# --- The page displays, and never computes -----------------------------------


def test_every_figure_is_the_servers_own_string(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)

    # Digit-for-digit the payload's own *_text values -- a page that rounded,
    # rescaled or recomputed would not match.
    assert page.text_content("#hev-equivalent-price-vol") == repr(_EQUIVALENT_PRICE_VOL)
    assert page.text_content("#hev-duration") == repr(_ABSOLUTE_DURATION)
    assert page.text_content("#hev-yield-vol-decimal") == repr(_YIELD_VOL_DECIMAL)
    assert page.text_content("#hev-yield-vol-field-unit") == (
        f"{_YIELD_VOL_PERCENT!r} PERCENT"
    )
    assert page.text_content("#hev-source") == _HISTORICAL_SOURCE
    assert page.text_content("#hev-price-basis") == "DIRTY"
    assert page.text_content("#hev-duration-type") == "MODIFIED_DURATION_DIRTY_PRICE"


def test_a_figure_that_does_not_read_back_as_its_own_number_is_refused(
    server_url, page
) -> None:
    # The page prints strings, and adopting one hands that string to the
    # pricing draft -- so a string that is not the number beside it is refused
    # rather than displayed and then priced.
    payload = _derivation_payload()
    payload["historical_volatility_source"]["equivalent_price_vol_text"] = "0.9"
    page.goto(f"{server_url}/")
    _route_derivation(page, payload=payload)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    page.click("#hev-derive-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolError() !== null")

    assert page.evaluate("() => window.__shioriTestHistoricalVolResult()") is None
    assert "does not read back" in page.text_content("#hev-status")
    assert _is_actually_hidden(page, "hev-fields")


def test_a_derivation_on_the_other_price_basis_is_refused(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(page, payload=_derivation_payload(price_basis="CLEAN"))
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    page.click("#hev-derive-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolError() !== null")

    status = page.text_content("#hev-status")
    assert "CLEAN" in status
    assert "DIRTY" in status


def test_a_relabelled_source_is_refused(server_url, page) -> None:
    payload = _derivation_payload(vol_source="VCUB_NORMAL_PROXY")
    page.goto(f"{server_url}/")
    _route_derivation(page, payload=payload)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    page.click("#hev-derive-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolError() !== null")

    assert "vol_source" in page.text_content("#hev-status")


# --- Use for Pricing ---------------------------------------------------------


def test_use_for_pricing_applies_the_server_value_without_manual_re_entry(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)

    # Reviewed but not yet in use: nothing has reached the draft.
    assert page.input_value("#volatility-input") == ""
    assert "vol-review" in page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")

    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    # The trader typed no volatility anywhere: the field and the draft both
    # carry exactly the server's own number.
    assert page.input_value("#volatility-input") == repr(_EQUIVALENT_PRICE_VOL)
    assert draft["volatility_input"]["volatility"] == _EQUIVALENT_PRICE_VOL
    assert draft["volatility_input"]["volatility_basis"] == "EQUIVALENT_PRICE_VOL"
    assert draft["volatility_input"]["source_system"] == _HISTORICAL_SOURCE
    assert "vol-review" not in page.evaluate(
        "() => window.__shioriTestUnresolvedGroupIds()"
    )


def test_the_request_and_the_draft_carry_the_query_and_the_basis(server_url, page) -> None:
    sent: list = []
    page.goto(f"{server_url}/")
    _route_derivation(page, sent=sent)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    page.fill("#hev-observation-count", "181")
    _derive(page)

    assert len(sent) == 1
    reviewed_case = sent[0]["case"]
    assert reviewed_case["bond_option_price_basis"] == "DIRTY"
    assert reviewed_case["volatility_input"]["source_system"] == _HISTORICAL_SOURCE
    query = reviewed_case["historical_yield_vol_request"]
    assert query["bond_identifier"] == "US91282CLJ89"
    assert query["yield_field"] == "SYNTHETIC_TEST_YIELD_FIELD"
    assert query["start_date"] == "2026-01-02"
    assert query["end_date"] == "2026-06-30"
    assert query["requested_observation_count"] == 181

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["historical_yield_vol_request"] == query
    assert draft["bond_option_price_basis"] == "DIRTY"


def test_an_unusable_result_refuses_use_for_pricing_and_states_the_reason(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(
        page,
        payload={"error": "too few usable Yield Changes to calculate a sample stdev"},
        status=400,
    )
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    page.click("#hev-derive-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolError() !== null")

    assert "too few usable Yield Changes" in page.text_content("#hev-status")
    # No Use for Pricing offered, nothing adopted, and no substitute of any
    # kind reached the volatility field.
    assert page.eval_on_selector("#hev-use-btn", "el => el.hidden") is True
    assert page.evaluate("() => window.__shioriTestHistoricalVolAdoptedText()") is None
    assert page.input_value("#volatility-input") == ""
    assert "vol-review" in page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")


# --- Staleness: nothing derived survives its own inputs ----------------------


def test_changing_the_price_basis_withdraws_the_previous_basis_value(
    server_url, page
) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)
    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    page.select_option("#price-basis-select", "CLEAN")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() === null")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert page.input_value("#volatility-input") == ""
    assert draft["volatility_input"]["volatility"] is None
    assert draft["bond_option_price_basis"] == "CLEAN"
    assert page.evaluate("() => window.__shioriTestHistoricalVolResult()") is None
    assert page.text_content("#price-basis-status") == "CLEAN"
    assert "vol-review" in page.evaluate("() => window.__shioriTestUnresolvedGroupIds()")


def test_editing_the_history_window_withdraws_the_derived_value(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)
    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    page.fill("#hev-end", "2026-05-29")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() === null")

    assert page.input_value("#volatility-input") == ""
    assert page.evaluate("() => window.__shioriTestHistoricalVolResult()") is None


def test_clear_leaves_nothing_of_the_previous_tickets_derivation(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)
    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    page.click("#clear-btn")
    page.wait_for_function("() => window.__shioriTestGetCurrentDraft() === null")

    assert page.evaluate("() => window.__shioriTestHistoricalVolAdoptedText()") is None
    assert page.evaluate("() => window.__shioriTestHistoricalVolResult()") is None
    assert page.input_value("#volatility-input") == ""
    # Back to the default source and the default basis.
    assert page.input_value("#vol-source-select") == "DIRECT_PRICE_VOL"
    assert page.input_value("#price-basis-select") == "DIRTY"
    assert _is_actually_hidden(page, "historical-vol-panel")


def test_a_second_bond_cannot_inherit_the_first_bonds_derivation(server_url, page) -> None:
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)
    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    _load_bond(page, isin="US91282CLK52")

    assert page.evaluate("() => window.__shioriTestHistoricalVolAdoptedText()") is None
    assert page.evaluate("() => window.__shioriTestHistoricalVolResult()") is None
    assert page.input_value("#volatility-input") == ""
    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert draft["volatility_input"]["volatility"] is None


def test_switching_back_to_the_direct_source_drops_the_derived_value(
    server_url, page
) -> None:
    # The adopted number belongs to the Historical source; leaving it behind
    # under "Direct price vol" would present a Shiori derivation as a trader
    # entry.
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)
    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    page.select_option("#vol-source-select", "DIRECT_PRICE_VOL")

    draft = page.evaluate("() => window.__shioriTestGetCurrentDraft()")
    assert page.input_value("#volatility-input") == ""
    assert draft["volatility_input"]["volatility"] is None
    assert draft["volatility_input"]["source_system"] == "MANUAL_TRADER_ENTRY"
    assert draft["historical_yield_vol_request"] is None
    assert page.eval_on_selector("#volatility-input", "el => el.readOnly") is False


def test_a_late_answer_for_withdrawn_inputs_never_repaints(server_url, page) -> None:
    # The generation fence: an in-flight derivation whose inputs have since
    # changed must not land on screen beside the inputs it does not describe.
    page.goto(f"{server_url}/")
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)

    page.route(
        _ROUTE,
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_derivation_payload()),
        ),
    )
    page.evaluate("() => window.__shioriTestHistoricalVolGeneration()")
    page.click("#hev-derive-btn")
    # Move an input the derivation depends on while the request is outstanding.
    page.fill("#hev-end", "2026-05-29")
    page.wait_for_timeout(300)

    assert page.evaluate("() => window.__shioriTestHistoricalVolResult()") is None
    assert page.evaluate("() => window.__shioriTestHistoricalVolAdoptedText()") is None


# --- The provenance line names the source that produced the number -----------


def test_an_adopted_historical_vol_is_never_stamped_as_a_trader_entry(
    server_url, page
) -> None:
    # Workstation UAT found the Volatility row's provenance line still reading
    # MANUAL_TRADER_ENTRY / TRADER_OVERRIDE under an adopted sigma_P. The
    # number is Shiori's own derivation and the field is not typable in this
    # mode, so that line described a trader entry that never happened.
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)
    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    provenance = page.inner_text("#vol-provenance")
    assert "MANUAL_TRADER_ENTRY" not in provenance
    assert "TRADER_OVERRIDE" not in provenance
    # It names the source, the basis it published and the duration it used --
    # every token the server's own payload carries.
    assert _HISTORICAL_SOURCE in provenance
    assert "EQUIVALENT_PRICE_VOL" in provenance
    assert "MODIFIED_DURATION_DIRTY_PRICE" in provenance
    assert "DIRTY" in provenance

    # And the same claim is absent from the override log and the exported
    # records behind it: a derivation is not an override anywhere.
    logged = {record["path"] for record in _override_provenance(page)}
    assert "volatility_input.volatility" not in logged
    assert "volatility_input.volatility_basis" not in logged
    assert "Price Vol" not in page.inner_text("#override-provenance-log")


def test_switching_back_to_the_direct_source_restores_the_manual_entry_provenance(
    server_url, page
) -> None:
    # The fix is scoped to the derived mode: a hand-typed volatility is a
    # trader entry and still says so.
    page.goto(f"{server_url}/")
    _route_derivation(page)
    _load_bond(page)
    _select_historical_source(page)
    _fill_query(page)
    _derive(page)
    page.click("#hev-use-btn")
    page.wait_for_function("() => window.__shioriTestHistoricalVolAdoptedText() !== null")

    page.select_option("#vol-source-select", "DIRECT_PRICE_VOL")
    page.fill("#volatility-input", "0.2153")
    page.wait_for_timeout(150)

    provenance = page.inner_text("#vol-provenance")
    assert "MANUAL_TRADER_ENTRY · TRADER_OVERRIDE" in provenance
    assert _HISTORICAL_SOURCE not in provenance
    logged = {record["path"] for record in _override_provenance(page)}
    assert "volatility_input.volatility" in logged
    assert "volatility_input.volatility_basis" in logged
