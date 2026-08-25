"""The workbench bridge's Treasury futures yield routes (Issue #190).

Every test drives the real ``ThreadingHTTPServer`` over loopback, the same
way the served page does. There is no Bloomberg seam to stub: the automatic
CTD path is unavailable by construction until its field mnemonics are
confirmed, and this file asserts that it says so rather than inventing data.

The other half of this file is the canonical-path guard. Issue #190's
architecture requirement is that the browser must not carry a second
implementation of the pricing maths (PR #9 did, in ~200 lines of JavaScript),
so the served page is checked for the absence of any of it.

CTD values below are arbitrary test inputs, never real market data.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from shiori_pricing_lab.app.standalone_option_workbench_server import (
    PROTOTYPE_DIR,
    create_server,
)
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
    minimum_tick,
)

CTD = {
    "contract_code": "ZN",
    "contract_symbol": "TYZ6",
    "ctd_identifier": "US91282CTEST",
    "ctd_coupon_percent": 4.25,
    "ctd_maturity_date": "2034-05-15",
    "conversion_factor": 0.8012,
    "last_delivery_date": "2026-12-31",
    "as_of": "2026-08-25T14:00:00Z",
}


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


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post(url: str, payload: object) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _convert(server_url: str, **overrides) -> tuple[int, dict]:
    body = {"ctd": dict(CTD)}
    body.update(overrides)
    return _post(f"{server_url}/api/treasury-futures/convert", body)


# ---------------------------------------------------------------------------
# The contract catalogue
# ---------------------------------------------------------------------------


def test_the_contract_catalogue_lists_the_four_mvp_contracts(server_url: str) -> None:
    status, payload = _get(f"{server_url}/api/treasury-futures/contracts")
    assert status == 200
    assert [contract["code"] for contract in payload["contracts"]] == list(
        SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES
    )


def test_the_catalogue_carries_each_contracts_own_tick_so_the_page_never_guesses(
    server_url: str,
) -> None:
    _, payload = _get(f"{server_url}/api/treasury-futures/contracts")
    ticks = {c["code"]: c["minimum_tick"] for c in payload["contracts"]}
    assert ticks == {code: minimum_tick(code) for code in SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES}
    digits = {c["code"]: c["sub_32nd_digits"] for c in payload["contracts"]}
    assert digits["ZB"] == ["0"]
    assert digits["ZN"] == ["0", "5"]
    # The tick's human label is the server's too, so the page never computes
    # a reciprocal to say what the tick is.
    labels = {c["code"]: c["minimum_tick_label"] for c in payload["contracts"]}
    assert labels == {
        "ZT": "1/256 point",
        "ZF": "1/128 point",
        "ZN": "1/64 point",
        "ZB": "1/32 point",
    }


# ---------------------------------------------------------------------------
# The automatic CTD path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_code", SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES)
def test_the_automatic_ctd_route_reports_unavailable_rather_than_inventing_data(
    server_url: str, contract_code
) -> None:
    status, payload = _post(
        f"{server_url}/api/treasury-futures/ctd", {"contract_code": contract_code}
    )
    assert status == 502
    assert payload["automatic_source_available"] is False
    assert "conversion_factor" in payload["error"]
    assert "bloomberg_treasury_futures_ctd_probe.py" in payload["error"]
    # No CTD numbers come back at all -- there is nothing to mistake for data.
    assert "ctd_identifier" not in payload


def test_the_automatic_ctd_route_rejects_an_unsupported_contract(server_url: str) -> None:
    status, payload = _post(f"{server_url}/api/treasury-futures/ctd", {"contract_code": "ZQ"})
    assert status == 400
    assert "ZQ" in payload["error"]


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def test_both_directions_answer_in_one_round_trip(server_url: str) -> None:
    status, payload = _convert(
        server_url, futures_price="112-165", target_yield_percent=4.2
    )
    assert status == 200
    assert payload["implied_yield"]["implied_yield_percent"] > 0
    assert payload["futures_price"]["exchange_quote"].startswith("1")


def test_either_direction_alone_is_enough(server_url: str) -> None:
    status, payload = _convert(server_url, futures_price="112-165")
    assert status == 200
    assert payload["implied_yield"] is not None
    assert payload["futures_price"] is None

    status, payload = _convert(server_url, target_yield_percent=4.2)
    assert status == 200
    assert payload["implied_yield"] is None
    assert payload["futures_price"] is not None


def test_neither_direction_is_a_request_error(server_url: str) -> None:
    status, payload = _convert(server_url)
    assert status == 400
    assert "futures_price" in payload["error"]


def test_a_bad_target_yield_never_hides_a_good_implied_yield(server_url: str) -> None:
    # Each direction reports its own failure, so the trader keeps whichever
    # answer is actually computable.
    status, payload = _convert(
        server_url, futures_price="112-165", target_yield_percent=-500.0
    )
    assert status == 200
    assert payload["implied_yield"]["implied_yield_percent"] > 0
    assert payload["futures_price"] is None
    assert payload["futures_price_error"]


@pytest.mark.parametrize("target_yield", ["abc", "", " ", "4.2.1", None])
def test_an_unreadable_target_yield_is_never_silently_dropped(
    server_url: str, target_yield
) -> None:
    # The page sends what was typed. A blank means "no target yield"; anything
    # unreadable must come back as a visible error on that direction, never as
    # a missing answer with no explanation.
    status, payload = _convert(
        server_url, futures_price="112-165", target_yield_percent=target_yield
    )
    assert status == 200
    assert payload["implied_yield"] is not None
    assert payload["futures_price"] is None
    if target_yield not in (None, "", " "):
        assert payload["futures_price_error"]


def test_a_target_yield_typed_with_a_percent_sign_is_read(server_url: str) -> None:
    status, payload = _convert(server_url, target_yield_percent="4.20%")
    assert status == 200
    assert payload["futures_price"]["target_yield_percent"] == 4.2


def test_an_off_tick_fractional_quote_is_reported_against_this_contract(
    server_url: str,
) -> None:
    status, payload = _convert(server_url, futures_price="112-162")
    assert status == 200
    assert payload["implied_yield"] is None
    assert "ZN" in payload["implied_yield_error"]


def test_an_incomplete_ctd_fails_the_whole_request_closed(server_url: str) -> None:
    incomplete = dict(CTD)
    incomplete["conversion_factor"] = None
    status, payload = _post(
        f"{server_url}/api/treasury-futures/convert",
        {"ctd": incomplete, "futures_price": "112-165"},
    )
    assert status == 400
    assert "conversion_factor" in payload["error"]


def test_every_conversion_answer_carries_the_unconfirmed_source_beside_it(
    server_url: str,
) -> None:
    _, payload = _convert(server_url, futures_price="112-165", target_yield_percent=4.2)
    assert payload["ctd"]["is_confirmed_source"] is False
    assert payload["ctd"]["source"] == "MANUAL_UNCONFIRMED"
    assert payload["implied_yield"]["ctd"]["is_confirmed_source"] is False
    assert payload["futures_price"]["ctd"]["is_confirmed_source"] is False


def test_a_malformed_json_body_is_a_request_error(server_url: str) -> None:
    request = urllib.request.Request(
        f"{server_url}/api/treasury-futures/convert",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 400


# ---------------------------------------------------------------------------
# One canonical calculation path
# ---------------------------------------------------------------------------


def test_the_futures_yield_static_file_is_served(server_url: str) -> None:
    with urllib.request.urlopen(f"{server_url}/treasury_futures_yield.js") as response:
        assert response.status == 200
        served = response.read()
    assert served == (PROTOTYPE_DIR / "treasury_futures_yield.js").read_bytes()


def test_the_browser_module_contains_no_bond_or_tick_mathematics() -> None:
    """PR #9 re-implemented the pricer in JavaScript. This one must not.

    A yield solve, a discounting loop, an accrual, a conversion-factor
    multiplication or a tick conversion appearing here would mean the page
    can disagree with Python -- exactly the drift Issue #190 forbids.
    """

    source = (PROTOTYPE_DIR / "treasury_futures_yield.js").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("//")
    )
    # Arithmetic, not field names: the module is free to *display* a coupon
    # or an accrued figure the server computed, and must never compute one.
    for fragment in (
        "conversion_factor *",
        "* conversionFactor",
        "/ 32",
        "* 32",
        "/ 64",
        "Math.pow",
        "** ",
        "accrued =",
        "bisect",
        "yieldFrom",
        "cleanPrice =",
        "Math.round",
        "1 /",
    ):
        assert fragment not in code, f"browser module contains pricing maths: {fragment!r}"
    # No loop that could be a cashflow discounting loop or a solver.
    assert not re.search(r"\bfor\s*\(", code)
    assert not re.search(r"\bwhile\s*\(", code)


def test_the_browser_module_reads_the_tick_from_the_server_not_a_constant() -> None:
    source = (PROTOTYPE_DIR / "treasury_futures_yield.js").read_text(encoding="utf-8")
    assert "/api/treasury-futures/contracts" in source
    for hard_coded_tick in ("1 / 256", "1/256", "0.015625", "0.00390625", "0.03125"):
        assert hard_coded_tick not in source


def test_the_page_wires_the_view_the_module_expects() -> None:
    index = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="nav-futures-yield"' in index
    assert 'id="view-futures-yield"' in index
    assert 'src="treasury_futures_yield.js"' in index
    script = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    assert '"futures-yield"' in script


def test_the_view_shows_the_ctd_small_print_the_issue_asks_for() -> None:
    index = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "fy-detail-ctd",
        "fy-detail-coupon",
        "fy-detail-maturity",
        "fy-detail-cf",
        "fy-detail-delivery",
        "fy-detail-tick",
        "fy-detail-source",
        "fy-detail-as-of",
    ):
        assert f'id="{element_id}"' in index


def test_the_view_never_offers_a_carry_or_net_basis_control() -> None:
    # Issue #190: no net-basis/repo/carry adjustment may reach the primary
    # answer. The simplest guarantee is that no such control exists.
    index = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    view = index.split('id="view-futures-yield"', 1)[1]
    view = view.split("<!-- ===================== FOOTER")[0]
    # Every interactive element in the view, by tag -- the panel may *say*
    # there is no carry adjustment, it may not offer one.
    controls = re.findall(r"<(?:input|select|textarea)\b[^>]*>", view, flags=re.IGNORECASE)
    assert controls, "the view should have inputs"
    for control in controls:
        lowered = control.lower()
        for forbidden in ("basis", "repo", "carry"):
            assert forbidden not in lowered, control
