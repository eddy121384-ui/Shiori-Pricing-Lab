"""Browser-driven regression tests for the Futures Yield view (Issue #190).

Exercises ``treasury_futures_yield.js`` end to end -- view switching, the
contract catalogue, the manual CTD path, both conversion directions, the
unconfirmed-source flag, and the automatic-path-unavailable message --
against one real ``ThreadingHTTPServer`` and one real headless Chromium page.

Nothing is intercepted at the network layer: the whole point of Issue #190's
canonical-path requirement is that the number on the screen came from the
Python calculation, so these tests let the real routes answer and then check
the rendered text against the same canonical functions.

**CI must not silently skip these tests** -- same reasoning and mechanism as
the sibling browser-test files: locally, missing Playwright is a skip; in CI
(``CI=true``) it is a hard collection-time error.

The CTD typed in below is an arbitrary test input, never real market data.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from collections.abc import Iterator

import pytest

from shiori_pricing_lab.app.standalone_option_workbench_server import create_server
from shiori_pricing_lab.data.treasury_futures_ctd import treasury_futures_ctd_from_manual_entry
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
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


def _open_futures_yield(page, server_url: str):
    page.goto(server_url)
    page.click("#nav-futures-yield")
    _wait_until(lambda: not _is_actually_hidden(page, "view-futures-yield"))
    _wait_until(
        lambda: page.eval_on_selector("#fy-contract-select", "el => el.options.length") == 4
    )
    return page


def _fill_ctd(page) -> None:
    page.select_option("#fy-contract-select", CTD_ENTRY["contract_code"])
    page.fill("#fy-contract-symbol", CTD_ENTRY["contract_symbol"])
    page.fill("#fy-ctd-identifier", CTD_ENTRY["ctd_identifier"])
    page.fill("#fy-ctd-coupon", str(CTD_ENTRY["ctd_coupon_percent"]))
    page.fill("#fy-ctd-maturity", CTD_ENTRY["ctd_maturity_date"])
    page.fill("#fy-conversion-factor", str(CTD_ENTRY["conversion_factor"]))
    page.fill("#fy-last-delivery", CTD_ENTRY["last_delivery_date"])
    page.fill("#fy-as-of", CTD_ENTRY["as_of"])


@_PLAYWRIGHT_SKIP
def test_the_nav_item_switches_to_the_view_and_pricing_switches_back(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    assert _is_actually_hidden(page, "view-pricing")
    assert _is_actually_hidden(page, "app-footer")
    page.click("#nav-pricing")
    _wait_until(lambda: _is_actually_hidden(page, "view-futures-yield"))
    assert not _is_actually_hidden(page, "view-pricing")


@_PLAYWRIGHT_SKIP
def test_the_contract_selector_is_filled_from_the_server_catalogue(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    codes = page.eval_on_selector_all("#fy-contract-select option", "els => els.map(e => e.value)")
    assert codes == ["ZT", "ZF", "ZN", "ZB"]


@_PLAYWRIGHT_SKIP
def test_the_tick_summary_follows_the_selected_contract(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    page.select_option("#fy-contract-select", "ZB")
    _wait_until(lambda: "1/32 point" in page.text_content("#fy-tick-summary"))
    page.select_option("#fy-contract-select", "ZT")
    _wait_until(lambda: "1/256 point" in page.text_content("#fy-tick-summary"))
    assert "0, 1, 2, 3, 5, 6, 7, 8" in page.text_content("#fy-tick-summary")


@_PLAYWRIGHT_SKIP
def test_a_futures_price_shows_the_implied_yield_python_computes(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-futures-price", "112-165")
    page.click("#fy-convert-btn")
    _wait_until(lambda: page.text_content("#fy-implied-yield").strip() not in ("", "—"))

    ctd = treasury_futures_ctd_from_manual_entry(dict(CTD_ENTRY))
    expected = implied_yield_from_futures_price(ctd, "112-165").implied_yield_percent
    assert page.text_content("#fy-implied-yield").strip() == f"{expected:.4f}%"


@_PLAYWRIGHT_SKIP
def test_a_target_yield_shows_the_futures_price_python_computes(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-target-yield", "4.20")
    page.click("#fy-convert-btn")
    _wait_until(lambda: page.text_content("#fy-futures-price-out").strip() not in ("", "—"))

    ctd = treasury_futures_ctd_from_manual_entry(dict(CTD_ENTRY))
    expected = futures_price_from_target_yield(ctd, 4.20).exchange_quote
    assert page.text_content("#fy-futures-price-out").strip() == expected


@_PLAYWRIGHT_SKIP
def test_the_ctd_small_print_and_unconfirmed_source_are_shown_with_the_answer(
    page, server_url
) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-futures-price", "112-165")
    page.click("#fy-convert-btn")
    _wait_until(lambda: page.text_content("#fy-detail-ctd").strip() == CTD_ENTRY["ctd_identifier"])

    assert page.text_content("#fy-detail-coupon").strip() == "4.25%"
    assert page.text_content("#fy-detail-maturity").strip() == CTD_ENTRY["ctd_maturity_date"]
    assert page.text_content("#fy-detail-cf").strip() == str(CTD_ENTRY["conversion_factor"])
    assert page.text_content("#fy-detail-delivery").strip() == CTD_ENTRY["last_delivery_date"]
    assert page.text_content("#fy-detail-as-of").strip() == CTD_ENTRY["as_of"]
    assert "1/64 point" in page.text_content("#fy-detail-tick")
    # The unconfirmed source is visible, not merely absent-by-omission.
    assert "NOT confirmed" in page.text_content("#fy-source-pill")


@_PLAYWRIGHT_SKIP
def test_an_incomplete_ctd_shows_the_servers_refusal_and_no_answer(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-conversion-factor", "")
    page.fill("#fy-futures-price", "112-165")
    page.click("#fy-convert-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "fy-error"))
    assert "conversion_factor" in page.text_content("#fy-error-detail")
    assert page.text_content("#fy-implied-yield").strip() == "—"


@_PLAYWRIGHT_SKIP
def test_a_failed_second_conversion_never_leaves_the_first_answer_on_screen(
    page, server_url
) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-futures-price", "112-165")
    page.click("#fy-convert-btn")
    _wait_until(lambda: page.text_content("#fy-implied-yield").strip() not in ("", "—"))

    page.fill("#fy-conversion-factor", "")
    page.click("#fy-convert-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "fy-error"))
    # The previous yield must not still be sitting there looking current.
    assert page.text_content("#fy-implied-yield").strip() == "—"


@_PLAYWRIGHT_SKIP
def test_editing_any_calculation_input_invalidates_the_answer_on_screen(
    page, server_url
) -> None:
    """Codex review, PR #191 (P2).

    A yield sitting next to a futures price that did not produce it is a
    number a trader can read off and act on.
    """

    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-futures-price", "112-165")

    for selector, value in (
        ("#fy-futures-price", "113-165"),
        ("#fy-ctd-coupon", "4.5"),
        ("#fy-conversion-factor", "0.81"),
        ("#fy-ctd-maturity", "2034-11-15"),
        ("#fy-last-delivery", "2027-03-31"),
        ("#fy-target-yield", "4.3"),
    ):
        page.click("#fy-convert-btn")
        _wait_until(lambda: page.text_content("#fy-implied-yield").strip() not in ("", "—"))
        page.fill(selector, value)
        assert page.text_content("#fy-implied-yield").strip() == "—", selector
        assert page.text_content("#fy-futures-price-out").strip() == "—", selector


@_PLAYWRIGHT_SKIP
def test_changing_the_contract_clears_the_previous_contracts_ctd(page, server_url) -> None:
    """Codex review, PR #191 (P1).

    `contract_code` comes from the selector while the CTD fields come from the
    form, so a leftover CTD would be submitted as -- and tick-formatted for --
    a contract it does not belong to.
    """

    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-futures-price", "112-165")
    page.click("#fy-convert-btn")
    _wait_until(lambda: page.text_content("#fy-implied-yield").strip() not in ("", "—"))

    page.select_option("#fy-contract-select", "ZB")

    for selector in (
        "#fy-contract-symbol",
        "#fy-ctd-identifier",
        "#fy-ctd-coupon",
        "#fy-ctd-maturity",
        "#fy-conversion-factor",
        "#fy-last-delivery",
        "#fy-as-of",
    ):
        assert page.input_value(selector) == "", selector
    assert page.text_content("#fy-implied-yield").strip() == "—"
    assert page.text_content("#fy-detail-ctd").strip() == "—"
    assert page.text_content("#fy-detail-cf").strip() == "—"
    assert "No CTD loaded" in page.text_content("#fy-source-pill")

    # And the answer it would now give is refused outright, not computed off
    # the previous contract's CTD.
    page.click("#fy-convert-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "fy-error"))
    assert page.text_content("#fy-implied-yield").strip() == "—"


@_PLAYWRIGHT_SKIP
def test_an_off_tick_quote_for_this_contract_is_refused_in_the_panel(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-futures-price", "112-162")  # a quarter 32nd is not a ZN tick
    page.click("#fy-convert-btn")
    _wait_until(lambda: "ZN" in page.text_content("#fy-implied-yield-note"))
    assert page.text_content("#fy-implied-yield").strip() == "—"


@_PLAYWRIGHT_SKIP
def test_a_typo_in_a_ctd_number_is_reported_as_a_typo_not_as_a_missing_field(
    page, server_url
) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.fill("#fy-conversion-factor", "0.80l2")  # letter l, not a 1
    page.fill("#fy-futures-price", "112-165")
    page.click("#fy-convert-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "fy-error"))
    detail = page.text_content("#fy-error-detail")
    assert "conversion_factor" in detail
    assert "missing" not in detail.lower()


@_PLAYWRIGHT_SKIP
def test_the_automatic_bloomberg_path_reports_exactly_what_is_missing(page, server_url) -> None:
    _open_futures_yield(page, server_url)
    page.click("#fy-load-bloomberg-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "fy-automatic-note"))
    note = page.text_content("#fy-automatic-note")
    assert "conversion_factor" in note
    assert "bloomberg_treasury_futures_ctd_probe.py" in note
    # Nothing was filled in from a fallback.
    assert page.input_value("#fy-conversion-factor") == ""


@_PLAYWRIGHT_SKIP
def test_converting_with_neither_input_asks_for_one_instead_of_calling_the_server(
    page, server_url
) -> None:
    _open_futures_yield(page, server_url)
    _fill_ctd(page)
    page.click("#fy-convert-btn")
    _wait_until(lambda: not _is_actually_hidden(page, "fy-error"))
    assert "futures price" in page.text_content("#fy-error-detail")
