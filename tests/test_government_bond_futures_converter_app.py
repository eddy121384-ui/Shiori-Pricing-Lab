"""The Government Bond Futures Converter desk app (Issue #206).

Three things are proved here, in order of how badly each would hurt if it
broke:

1. **The app and the Workbench are the same calculation.** Not "agree to
   six decimals" -- literally the same function objects, asserted with ``is``,
   and then cross-checked route-to-route on real conversions. Issue #206's
   hard constraint is that packaging must not become a second implementation.
2. **The app shell is safe and quiet**: loopback-only, no console, a clean
   shutdown, and no user-facing internal branding anywhere a trader can see.
3. **The desk flow works**: all ten registry contracts are offered, picking
   one goes through the existing Bloomberg CTD path, and both the automatic
   and the manual routes still price the German irregular-first schedule.

CTD values below are the pinned test records the futures suite already uses;
none of them is fresh market data.
"""

from __future__ import annotations

import ast
import json
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_treasury_futures_ctd import (
    ACTIVE_ZN,
    DELIVERY_ZN,
    _install_fake_blpapi,
    _two_stage_responder,
)
from test_treasury_futures_eurex_fgb import (
    LIVE_DELIVERY_SYMBOL,
    LIVE_SCHEDULE,
    LIVE_STAGE_TWO,
    _three_stage_responder,
)

from shiori_pricing_lab.app import government_bond_futures_converter_app as app_module
from shiori_pricing_lab.app import government_bond_futures_converter_server as converter
from shiori_pricing_lab.app import standalone_option_workbench_server as workbench
from shiori_pricing_lab.data.treasury_futures_ctd import bloomberg_active_contract
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
    format_futures_quote,
    get_contract,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = PROJECT_ROOT / "prototype" / "government-bond-futures-converter"
SPEC_PATH = PROJECT_ROOT / "packaging" / "government_bond_futures_converter.spec"

#: The ten contracts Issue #206 requires the app to offer, in registry order.
EXPECTED_CONTRACT_CODES = ("ZT", "ZF", "ZN", "ZB", "UXY", "WN", "FGBS", "FGBM", "FGBL", "FGBX")

UST_CTD = {
    "contract_code": "ZN",
    "contract_symbol": "TYZ6",
    "ctd_identifier": "US91282CTEST",
    "ctd_coupon_percent": 4.25,
    "ctd_maturity_date": "2034-05-15",
    "conversion_factor": 0.8012,
    "last_delivery_date": "2026-12-31",
    "as_of": "2026-08-25T14:00:00Z",
}

#: The pinned FGBS "YAS RED" record, with its irregular first coupon period.
FGBS_CTD = {
    "contract_code": "FGBS",
    "contract_symbol": "DUZ6",
    "ctd_identifier": "DE000BU22148",
    "ctd_coupon_percent": 2.7,
    "ctd_maturity_date": "2028-09-13",
    "conversion_factor": 0.946091,
    "last_delivery_date": "2026-12-10",
    "first_accrual_start": "2026-07-16",
    "first_coupon_date": "2027-09-13",
    "as_of": "2026-09-08T00:00:00Z",
}

FGBM_CTD = {
    "contract_code": "FGBM",
    "contract_symbol": "OEZ6",
    "ctd_identifier": "DE000BU25075",
    "ctd_coupon_percent": 2.9,
    "ctd_maturity_date": "2031-10-08",
    "conversion_factor": 0.872911,
    "last_delivery_date": "2026-12-10",
    "first_accrual_start": "2026-07-23",
    "first_coupon_date": "2027-10-08",
    "as_of": "2026-09-08T00:00:00Z",
}


def _serve(server) -> Iterator[str]:
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
def app_url() -> Iterator[str]:
    yield from _serve(converter.create_server())


@pytest.fixture()
def workbench_url() -> Iterator[str]:
    yield from _serve(workbench.create_server(host="127.0.0.1", port=0))


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


def _read_text(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


def _install_fake_eurex_blpapi(monkeypatch, contract_code: str) -> None:
    """Point the loader at the pinned live payloads for one Eurex contract."""

    resolved = LIVE_DELIVERY_SYMBOL[contract_code]
    live = LIVE_STAGE_TWO[contract_code]
    _install_fake_blpapi(
        monkeypatch,
        _three_stage_responder(
            active_fields={"PARSEKYABLE_DES": f"{resolved} Comdty"},
            stage_two_fields=dict(live),
            schedule_fields=dict(LIVE_SCHEDULE[contract_code]),
            active=bloomberg_active_contract(contract_code),
            delivery=f"{resolved} Comdty",
            bond=f"/isin/{live['FUT_CTD_ISIN']}",
        ),
    )


# ---------------------------------------------------------------------------
# 1. The app exposes the whole registry, and only the registry
# ---------------------------------------------------------------------------


def test_the_app_offers_all_ten_registry_contracts(app_url: str) -> None:
    status, payload = _get(f"{app_url}/api/treasury-futures/contracts")
    assert status == 200
    assert tuple(c["code"] for c in payload["contracts"]) == EXPECTED_CONTRACT_CODES


def test_the_app_never_carries_a_second_contract_registry(app_url: str) -> None:
    # The catalogue follows the production registry rather than restating it,
    # so adding a contract there is all it takes to offer it in the app.
    status, payload = _get(f"{app_url}/api/treasury-futures/contracts")
    assert status == 200
    assert tuple(c["code"] for c in payload["contracts"]) == (
        SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES
    )
    source = Path(converter.__file__).read_text(encoding="utf-8")
    for code in EXPECTED_CONTRACT_CODES:
        assert f'"{code}"' not in source, f"{code} is hard-coded in the app's own server"


# ---------------------------------------------------------------------------
# 2. Same calculation as the Workbench -- by identity, then by result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "treasury_futures_contract_catalogue",
        "load_treasury_futures_ctd",
        "convert_treasury_futures",
    ],
)
def test_the_app_serves_the_workbenchs_own_handler_objects(name: str) -> None:
    # `is`, not `==`: the only guarantee that survives a refactor is that
    # there is exactly one function, reached from both servers.
    assert getattr(converter, name) is getattr(workbench, name)


@pytest.mark.parametrize(
    ("ctd", "futures_price", "target_yield"),
    [
        (UST_CTD, "112-165", "4.20"),  # UST 32nds, both directions
        (UST_CTD, "112.515625", None),  # UST decimal entry of the same grid
        (FGBS_CTD, 105.065, "3.05"),  # German parity pin, decimal grid
        (FGBM_CTD, 113.24, "3.16"),
    ],
)
def test_the_app_and_the_workbench_answer_identically(
    app_url: str, workbench_url: str, ctd, futures_price, target_yield
) -> None:
    body = {"ctd": dict(ctd), "futures_price": futures_price}
    if target_yield is not None:
        body["target_yield_percent"] = target_yield
    app_status, app_payload = _post(f"{app_url}/api/treasury-futures/convert", body)
    wb_status, wb_payload = _post(f"{workbench_url}/api/treasury-futures/convert", body)
    assert (app_status, app_payload) == (wb_status, wb_payload)
    assert app_status == 200


# ---------------------------------------------------------------------------
# 3. Quote conventions are unchanged through the app
# ---------------------------------------------------------------------------


def test_ust_32nds_behaviour_is_unchanged(app_url: str) -> None:
    status, payload = _post(
        f"{app_url}/api/treasury-futures/convert",
        {"ctd": dict(UST_CTD), "futures_price": "112-165"},
    )
    assert status == 200
    result = payload["implied_yield"]
    # 112 + 16.5/32 exactly, and echoed back in the desk's own 32nds notation.
    assert result["futures_price"] == pytest.approx(112.515625)
    assert result["exchange_quote"] == format_futures_quote("ZN", 112.515625) == "112-16 1/2"
    assert result["on_tick"] is True
    assert result["minimum_tick_label"] == get_contract("ZN").minimum_tick_label == "1/64 point"


def test_an_off_tick_ust_price_is_reported_not_silently_rounded(app_url: str) -> None:
    status, payload = _post(
        f"{app_url}/api/treasury-futures/convert",
        {"ctd": dict(UST_CTD), "futures_price": 112.5200},
    )
    assert status == 200
    assert payload["implied_yield"]["on_tick"] is False


def test_eurex_decimal_behaviour_is_unchanged(app_url: str) -> None:
    status, payload = _post(
        f"{app_url}/api/treasury-futures/convert",
        {"ctd": dict(FGBS_CTD), "futures_price": 105.065},
    )
    assert status == 200
    result = payload["implied_yield"]
    assert result["minimum_tick"] == 0.005
    assert result["minimum_tick_label"] == "0.005 point"
    # The pinned Bloomberg YAS RED answer. Unchanged by packaging.
    assert result["implied_yield_percent"] == pytest.approx(3.044683, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. The German irregular-first schedule survives both flows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ctd", "futures_price", "expected_percent"),
    [(FGBS_CTD, 105.065, 3.044683), (FGBM_CTD, 113.24, 3.155945)],
)
def test_the_manual_flow_prices_the_irregular_first_coupon(
    app_url: str, ctd, futures_price, expected_percent
) -> None:
    status, payload = _post(
        f"{app_url}/api/treasury-futures/convert",
        {"ctd": dict(ctd), "futures_price": futures_price},
    )
    assert status == 200
    assert payload["implied_yield"]["implied_yield_percent"] == pytest.approx(
        expected_percent, abs=1e-5
    )
    assert payload["ctd"]["first_accrual_start"] == ctd["first_accrual_start"]
    assert payload["ctd"]["first_coupon_date"] == ctd["first_coupon_date"]


@pytest.mark.parametrize("contract_code", ["FGBS", "FGBM"])
def test_the_automatic_flow_carries_the_irregular_first_schedule(
    app_url: str, monkeypatch, contract_code: str
) -> None:
    _install_fake_eurex_blpapi(monkeypatch, contract_code)
    status, payload = _post(
        f"{app_url}/api/treasury-futures/ctd", {"contract_code": contract_code}
    )
    assert status == 200
    assert payload["first_accrual_start"] == LIVE_SCHEDULE[contract_code]["ISSUE_DT"]
    assert payload["first_coupon_date"] == LIVE_SCHEDULE[contract_code]["FIRST_CPN_DT"]
    assert payload["is_confirmed_source"] is True


def test_a_half_specified_schedule_is_refused_rather_than_guessed(app_url: str) -> None:
    half = dict(FGBS_CTD)
    del half["first_coupon_date"]
    status, payload = _post(
        f"{app_url}/api/treasury-futures/convert",
        {"ctd": half, "futures_price": 105.065},
    )
    assert status == 400
    assert "first-coupon schedule" in payload["error"]


# ---------------------------------------------------------------------------
# 5. Selecting a contract goes through the existing Bloomberg CTD path
# ---------------------------------------------------------------------------


def test_the_ctd_route_performs_the_existing_two_stage_bloomberg_lookup(
    app_url: str, monkeypatch
) -> None:
    harness = _install_fake_blpapi(monkeypatch, _two_stage_responder())
    status, payload = _post(f"{app_url}/api/treasury-futures/ctd", {"contract_code": "ZN"})
    assert status == 200
    # The desk-active alias first, then the resolved delivery month -- exactly
    # the sequence the production loader has always performed.
    assert [security for security, _ in harness["requests"]] == [ACTIVE_ZN, DELIVERY_ZN]
    assert payload["source"] == "BLOOMBERG_DAPI"
    assert payload["is_confirmed_source"] is True


def test_a_bloomberg_sourced_conversion_never_reads_a_ctd_from_the_request(
    app_url: str, monkeypatch
) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    status, payload = _post(
        f"{app_url}/api/treasury-futures/convert",
        {
            "ctd_source": "BLOOMBERG",
            "contract_code": "ZN",
            # An invented CTD in the body must be ignored outright, never
            # merged into, or displayed as, the confirmed live record.
            "ctd": dict(UST_CTD, conversion_factor=0.1234, ctd_identifier="US000INVENTED"),
            "futures_price": "112-165",
        },
    )
    assert status == 200
    assert payload["ctd"]["ctd_identifier"] == "US91282CRJ26"
    assert payload["ctd"]["conversion_factor"] == 0.9202


def test_an_unsupported_contract_is_refused_before_bloomberg_is_asked(
    app_url: str, monkeypatch
) -> None:
    harness = _install_fake_blpapi(monkeypatch, _two_stage_responder())
    status, payload = _post(f"{app_url}/api/treasury-futures/ctd", {"contract_code": "ZQ"})
    assert status == 400
    assert "ZQ" in payload["error"]
    assert harness["requests"] == []


# ---------------------------------------------------------------------------
# 6. Bloomberg unavailable is a readable answer, never a traceback
# ---------------------------------------------------------------------------


def test_bloomberg_unavailable_gives_a_readable_error_not_a_traceback(
    app_url: str, monkeypatch
) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder(), start_result=False)
    status, payload = _post(f"{app_url}/api/treasury-futures/ctd", {"contract_code": "ZN"})
    assert status == 400
    detail = payload["error"]
    assert "Traceback" not in detail
    assert "  File \"" not in detail
    # Names what actually failed, so the trader knows to check the Terminal.
    assert "Bloomberg" in detail


def test_a_failed_bloomberg_conversion_answers_nothing_rather_than_guessing(
    app_url: str, monkeypatch
) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder(), start_result=False)
    status, payload = _post(
        f"{app_url}/api/treasury-futures/convert",
        {"ctd_source": "BLOOMBERG", "contract_code": "ZN", "futures_price": "112-165"},
    )
    assert status == 400
    assert "implied_yield" not in payload


def test_a_malformed_body_is_a_request_error_not_a_crash(app_url: str) -> None:
    request = urllib.request.Request(
        f"{app_url}/api/treasury-futures/convert",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 400
    assert "malformed request body" in json.loads(exc.value.read())["error"]


def test_an_unknown_route_is_a_404_not_a_stack_trace(app_url: str) -> None:
    status, payload = _get(f"{app_url}/api/does-not-exist")
    assert status == 404
    assert "no such route" in payload["error"]


# ---------------------------------------------------------------------------
# 7. The app shell: loopback only, no console, clean shutdown, no branding
# ---------------------------------------------------------------------------


def test_the_backend_binds_loopback_only() -> None:
    server = converter.create_server()
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
    # Not merely the default -- there is no configured non-loopback host at all.
    assert converter.HOST == "127.0.0.1"


def test_the_backend_is_not_reachable_from_a_non_loopback_address(app_url: str) -> None:
    port = int(app_url.rsplit(":", 1)[1])
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        # Connecting to a routable local address must fail: the listener is
        # bound to 127.0.0.1, so nothing off the loopback interface can reach it.
        with pytest.raises(OSError):
            probe.connect((socket.gethostbyname(socket.gethostname()), port))
    finally:
        probe.close()


def test_the_backend_takes_an_ephemeral_port_so_it_can_never_collide() -> None:
    assert converter.EPHEMERAL_PORT == 0
    first = converter.create_server()
    second = converter.create_server()
    try:
        assert first.server_address[1] != second.server_address[1]
        assert first.server_address[1] > 0
    finally:
        first.server_close()
        second.server_close()


def test_closing_the_app_leaves_no_listener_behind() -> None:
    """After shutdown the old endpoint answers nothing. Closing the window is
    the trader's only stop button, so a backend that kept serving after it
    would be a listener nobody knows is there.

    The assertion is deliberately *"nothing answers there any more"* and not
    *"the port can be rebound"*. Those are different claims: a correctly
    closed listening socket can still leave the port unavailable for a while
    because the connection just made sits in TCP TIME_WAIT, which is how this
    test failed on Linux in CI while the production shutdown was perfectly
    correct. Rebindability is a kernel timing detail; not serving is the
    behaviour that matters.
    """

    server, url = app_module.start_backend()
    health_url = f"{url}api/health"
    with urllib.request.urlopen(health_url, timeout=10) as response:
        assert json.loads(response.read())["api_contract"] == converter.API_CONTRACT_ID

    server.shutdown()
    server.server_close()

    # Bounded poll rather than a single immediate probe: shutdown() returns
    # once the serve loop has stopped, and the OS can take a moment to stop
    # accepting on a socket that is already closed. A connection that is
    # refused, reset or simply times out all mean the same thing here -- no
    # listener -- so the loop ends on any of them and only a *successful*
    # health response is a failure.
    deadline = time.monotonic() + 10.0
    while True:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                served = response.read()
        except (urllib.error.URLError, OSError, TimeoutError):
            return
        assert time.monotonic() < deadline, (
            f"the backend was still serving {health_url} 10s after shutdown: {served!r}"
        )


def test_the_serving_thread_is_a_daemon_so_it_cannot_outlive_the_app() -> None:
    server, _ = app_module.start_backend()
    try:
        threads = [t for t in threading.enumerate() if t.name == "converter-backend"]
        assert threads and all(t.daemon for t in threads)
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# 8. No user-facing internal branding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("file_name", ["index.html", "app.css", "app.js"])
def test_the_app_ui_never_shows_internal_branding(file_name: str) -> None:
    text = (ASSET_DIR / file_name).read_text(encoding="utf-8")
    assert "Shiori" not in text
    assert "shiori" not in text


def test_the_served_page_is_titled_for_the_desk_not_the_repository(app_url: str) -> None:
    page = _read_text(app_url + "/")
    assert "<title>Government Bond Futures Converter</title>" in page
    assert "Shiori" not in page
    assert "Bond Option Pricer" not in page


def test_the_app_and_executable_are_named_for_the_desk() -> None:
    assert app_module.APP_NAME == "Government Bond Futures Converter"
    spec = SPEC_PATH.read_text(encoding="utf-8")
    assert 'APP_NAME = "Government Bond Futures Converter"' in spec
    assert "Shiori" not in spec


def test_a_startup_failure_message_reads_as_a_sentence_not_a_traceback(monkeypatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        app_module, "show_error_dialog", lambda message, **kw: shown.append(message)
    )
    monkeypatch.setattr(
        app_module, "find_browser", lambda: (_ for _ in ()).throw(app_module.ConverterAppError(
            "Microsoft Edge could not be found on this computer, and "
            "Government Bond Futures Converter needs it to display its window."
        ))
    )
    assert app_module.run([]) == 1
    assert shown and "Traceback" not in shown[0]
    assert "Microsoft Edge" in shown[0]


# ---------------------------------------------------------------------------
# 9. No second pricing implementation in the front end or the app shell
# ---------------------------------------------------------------------------

#: Arithmetic, not field names -- the same distinction the Workbench's own
#: guard on `treasury_futures_yield.js` draws. This module is free to
#: *display* an accrued figure or a conversion factor the server computed, and
#: must never compute one.
_FORBIDDEN_MATHS_TOKENS = (
    "conversion_factor *",
    "* conversionFactor",
    "/ 32",
    "* 32",
    "/ 64",
    "Math.pow",
    "Math.exp",
    "Math.log",
    "Math.sqrt",
    "Math.round",
    "** ",
    "accrued =",
    "bisect",
    "newton",
    "yieldFrom",
    "cleanPrice =",
    "1 /",
)


def test_the_front_end_contains_no_bond_or_tick_mathematics() -> None:
    """PR #9 re-implemented the pricer in JavaScript. This one must not.

    A yield solve, a discounting loop, an accrual, a conversion-factor
    multiplication or a tick conversion appearing here would mean the window
    can disagree with Python -- the exact drift packaging must not introduce.
    """

    source = (ASSET_DIR / "app.js").read_text(encoding="utf-8")
    # Comments explain what the file deliberately does *not* do, and naming
    # those operations is the point -- so only executable lines are searched.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//")
    )
    for token in _FORBIDDEN_MATHS_TOKENS:
        assert token not in code, f"{token!r} looks like pricing maths in app.js"
    # No loop that could be a cashflow discounting loop or a solver.
    assert not re.search(r"\bfor\s*\(", code)
    assert not re.search(r"\bwhile\s*\(", code)


def test_the_front_end_reads_the_tick_from_the_server_not_a_constant() -> None:
    source = (ASSET_DIR / "app.js").read_text(encoding="utf-8")
    assert "minimum_tick_label" in source
    # No literal tick grid: 0.005/0.01/0.02 and the 32nd alphabet all arrive
    # from GET /api/treasury-futures/contracts.
    for literal in ("0.005", "0.01562", "0.0078125", "1/32", "1/64"):
        assert literal not in source


def test_the_app_server_contains_no_pricing_of_its_own() -> None:
    """Structural, not textual: the app server computes no numbers at all.

    A token scan would trip over prose in the docstring, so the module is
    parsed instead and every arithmetic operator is rejected outright. The one
    allowed binary operator is ``/``, which this module uses only to join
    filesystem paths -- and a division that *was* pricing would need a
    multiplication, subtraction or power beside it to be one.
    """

    tree = ast.parse(Path(converter.__file__).read_text(encoding="utf-8"))
    arithmetic = (ast.Add, ast.Sub, ast.Mult, ast.Pow, ast.Mod, ast.FloorDiv)
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, arithmetic), (
                f"arithmetic at line {node.lineno} of the app server"
            )
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "math" not in imported and "decimal" not in imported


# ---------------------------------------------------------------------------
# 10. Packaging: windowed, portable, and carrying the Bloomberg native library
# ---------------------------------------------------------------------------


def test_the_packaged_app_never_shows_a_console() -> None:
    spec = SPEC_PATH.read_text(encoding="utf-8")
    assert "console=False" in spec
    assert "disable_windowed_traceback=True" in spec


def test_the_packaging_spec_collects_the_bloomberg_native_library() -> None:
    # blpapi ships blpapi3_64.dll inside its own package directory and loads it
    # by ctypes; without collect_all the frozen app imports blpapi and then
    # cannot find its DLL. This is the Issue #206 packaging gate in one line.
    spec = SPEC_PATH.read_text(encoding="utf-8")
    assert 'collect_all("blpapi")' in spec
    assert "blpapi_binaries" in spec
    assert "blpapi_hiddenimports" in spec


def test_the_packaging_spec_ships_the_apps_static_files() -> None:
    spec = SPEC_PATH.read_text(encoding="utf-8")
    assert '"government-bond-futures-converter"' in spec
    # The server resolves that same folder name under sys._MEIPASS when frozen.
    assert '"government-bond-futures-converter"' in Path(converter.__file__).read_text(
        encoding="utf-8"
    )


def test_the_asset_directory_resolves_in_the_source_tree() -> None:
    assert converter.asset_dir() == ASSET_DIR
    for file_name in ("index.html", "app.css", "app.js"):
        assert (converter.asset_dir() / file_name).is_file()


def test_the_asset_directory_follows_the_bundle_when_frozen(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(converter.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert converter.asset_dir() == tmp_path / "government-bond-futures-converter"


# ---------------------------------------------------------------------------
# 11. The window shell
# ---------------------------------------------------------------------------


def test_the_window_is_opened_without_browser_chrome(tmp_path) -> None:
    command = app_module.build_browser_command(
        Path("msedge.exe"), "http://127.0.0.1:1234/", tmp_path
    )
    assert "--app=http://127.0.0.1:1234/" in command
    # A private profile is what makes the window a process of our own to wait
    # on, instead of a tab handed to the trader's already-running browser.
    assert f"--user-data-dir={tmp_path}" in command
    # Nothing here may re-enable the address bar or the tab strip.
    assert not any(argument.startswith("--new-window") for argument in command)


def test_the_browser_is_found_in_the_standard_install_locations(tmp_path) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    found = app_module.find_browser(
        environ={"ProgramFiles(x86)": str(tmp_path)},
        which=lambda name: None,
    )
    assert found == edge


def test_a_missing_browser_is_explained_to_the_trader(tmp_path) -> None:
    with pytest.raises(app_module.ConverterAppError) as exc:
        app_module.find_browser(environ={}, which=lambda name: None)
    message = str(exc.value)
    assert "Microsoft Edge" in message
    assert "Traceback" not in message
    # Tells them what to do next, not what failed internally.
    assert "install" in message.lower()


def test_the_app_never_introduces_a_lan_listener_or_a_cache() -> None:
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("0.0.0.0", "requests.get", "sqlite3", "pickle", "urlopen"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# 12. One instance at a time
# ---------------------------------------------------------------------------


class _FakeMutexApi:
    """A stand-in for CreateMutexW/GetLastError, so the guard is testable anywhere.

    The real guard is a Windows kernel object, and CI runs on Linux -- but the
    behaviour worth pinning is the decision the guard makes, not the syscall.
    """

    def __init__(self, *, handle=4242, error=0):
        self.handle = handle
        self.error = error
        self.calls: list[tuple] = []
        self.closed: list[object] = []

    def create_mutex(self, security, initial_owner, name):
        self.calls.append((security, initial_owner, name))
        return self.handle

    def last_error(self):
        return self.error

    def close_handle(self, handle):
        self.closed.append(handle)
        return True


def test_the_first_instance_takes_the_guard_and_starts() -> None:
    api = _FakeMutexApi(handle=4242, error=0)
    assert app_module.acquire_single_instance(api.create_mutex, api.last_error) == 4242
    assert api.closed == []
    security, initial_owner, name = api.calls[0]
    assert initial_owner is True
    assert name == app_module.SINGLE_INSTANCE_MUTEX_NAME


def test_a_second_instance_is_refused_in_words_a_trader_can_act_on() -> None:
    api = _FakeMutexApi(error=183)  # ERROR_ALREADY_EXISTS
    with pytest.raises(app_module.AlreadyRunningError) as exc:
        app_module.acquire_single_instance(api.create_mutex, api.last_error, api.close_handle)
    message = str(exc.value)
    assert message.startswith("Government Bond Futures Converter is already running.")
    assert "Traceback" not in message
    # The duplicate handle CreateMutexW returned is released, not leaked.
    assert api.closed == [api.handle]


def test_a_second_launch_starts_no_backend_and_opens_no_window(monkeypatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        app_module, "show_error_dialog", lambda message, **kw: shown.append(message)
    )
    api = _FakeMutexApi(error=183)
    # Bound before patching: the replacement must call the real function, not
    # the name it is about to occupy.
    real_acquire = app_module.acquire_single_instance
    monkeypatch.setattr(
        app_module,
        "acquire_single_instance",
        lambda: real_acquire(api.create_mutex, api.last_error, api.close_handle),
    )
    # Either of these running would mean the refusal came too late.
    monkeypatch.setattr(
        app_module, "start_backend", lambda: pytest.fail("a second instance started a backend")
    )
    monkeypatch.setattr(
        app_module, "find_browser", lambda: pytest.fail("a second instance opened a window")
    )

    assert app_module.run([]) == app_module.EXIT_ALREADY_RUNNING
    assert shown and shown[0].startswith("Government Bond Futures Converter is already running.")


def test_a_failed_guard_never_locks_the_trader_out() -> None:
    """A guard that cannot be created must not stop the app from opening.

    A duplicate window is a smaller problem than a converter that will not
    start, so a null handle means "carry on unguarded", not "refuse".
    """

    api = _FakeMutexApi(handle=0, error=0)
    assert app_module.acquire_single_instance(api.create_mutex, api.last_error) is None


def test_the_guard_is_an_os_object_with_no_stale_state_to_clean_up() -> None:
    """No lock file, no PID file, no liveness check -- the kernel owns it.

    Windows releases the mutex when the process ends for any reason, so a crash
    or a kill cannot leave a guard behind that locks the desk out of its own
    tool. That property is the whole reason for choosing a mutex, so a future
    edit to a file-based scheme has to fail here first.
    """

    source = Path(app_module.__file__).read_text(encoding="utf-8")
    for forbidden in (".lock", "lockfile", "lock_file", "pidfile", "pid_file", "O_EXCL"):
        assert forbidden not in source
    assert "CreateMutexW" in source
    # Session-scoped, not machine-wide: two traders on one terminal server each
    # get their own instance.
    assert app_module.SINGLE_INSTANCE_MUTEX_NAME.startswith("Local\\")
    assert not app_module.SINGLE_INSTANCE_MUTEX_NAME.startswith("Global\\")


@pytest.mark.skipif(sys.platform != "win32", reason="the guard is a Windows kernel object")
def test_on_windows_the_real_second_acquisition_is_refused() -> None:
    """The real kernel32 path, on a name of this test's own.

    Deliberately not the app's own mutex name: taking that would fail whenever
    a trader actually has the converter open, and would leave the suite holding
    the guard against the next launch. A per-run name exercises the same
    syscall with no such coupling, and both handles are closed explicitly so
    nothing outlives the test.
    """

    import ctypes

    name = f"Local\\GovernmentBondFuturesConverter.Test.{uuid.uuid4().hex}"
    first = app_module.acquire_single_instance(name=name)
    assert first is not None
    try:
        with pytest.raises(app_module.AlreadyRunningError) as exc:
            app_module.acquire_single_instance(name=name)
        assert "already running" in str(exc.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(first)
