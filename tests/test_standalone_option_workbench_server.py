"""Tests for the minimal stdlib HTTP bridge (PR #136).

Proves the bridge's ``/api/base`` and ``/api/price`` responses are byte-for-
-byte the same JSON a direct call to the unmodified
``price_standalone_option_case`` would produce (Eddy's validation
requirement #2), that a malformed/invalid request fails explicitly over
HTTP (requirement #3), and that the static prototype files it serves are the
exact files on disk. Spins one real ``ThreadingHTTPServer`` in a background
thread and talks to it over a real socket via ``urllib`` -- no mocking of
the HTTP layer.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shiori_pricing_lab.app import standalone_option_workbench as workbench_module
from shiori_pricing_lab.app import standalone_option_workbench_server as server_module
from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import (
    price_standalone_option_case,
    price_standalone_option_case_with_bloomberg_quote,
)
from shiori_pricing_lab.app.standalone_option_workbench_context import (
    extract_standalone_option_case_context,
)
from shiori_pricing_lab.app.standalone_option_workbench_overlay import (
    apply_standalone_option_case_overlay,
    extract_standalone_option_case_overlay,
)
from shiori_pricing_lab.app.standalone_option_workbench_server import (
    PROTOTYPE_DIR,
    create_server,
    export_current_run_as_json,
    export_current_run_as_markdown,
    load_base_case,
)
from shiori_pricing_lab.data.bli_snapshot import BLIBondQuote, BLIMarketDataStatus, BLIQuoteBasis
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available
from shiori_pricing_lab.products.enums import Currency, TreasuryFTPQuoteSide

_QUANTLIB_SKIP = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

_EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"


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


def _get_json(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post_json(url: str, payload: object) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post_bytes(url: str, data: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/octet-stream"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_serves_static_prototype_files_verbatim(server_url: str) -> None:
    for route, file_name in (
        ("/", "index.html"),
        ("/styles.css", "styles.css"),
        ("/script.js", "script.js"),
    ):
        with urllib.request.urlopen(f"{server_url}{route}") as response:
            body = response.read()
        assert body == (PROTOTYPE_DIR / file_name).read_bytes()


def test_unknown_route_returns_404(server_url: str) -> None:
    status, payload = _get_json(f"{server_url}/does-not-exist")
    assert status == 404
    assert "error" in payload


def test_api_health_exposes_the_revision_specific_api_contract_id(server_url: str) -> None:
    # Codex review (PR #139): the launcher's classify_port() must be able to
    # tell this revision's server apart from an older one lacking the Case
    # JSON/export routes -- this is the endpoint it probes to do that.
    status, payload = _get_json(f"{server_url}/api/health")
    assert status == 200
    assert payload == {"api_contract": server_module.API_CONTRACT_ID}


@_QUANTLIB_SKIP
def test_api_base_matches_direct_call_to_price_standalone_option_case(server_url: str) -> None:
    status, payload = _get_json(f"{server_url}/api/base")
    assert status == 200

    base_case = load_base_case()
    _, _, expected_display = price_standalone_option_case(base_case)

    assert payload["display"] == expected_display
    assert payload["overlay"] == extract_standalone_option_case_overlay(base_case)
    assert payload["context"] == extract_standalone_option_case_context(base_case)
    assert "cusip" not in payload["context"]


@_QUANTLIB_SKIP
def test_api_price_matches_direct_call_to_price_standalone_option_case(server_url: str) -> None:
    base_case = load_base_case()
    overlay = extract_standalone_option_case_overlay(base_case)
    overlay["option_type"] = "PUT"
    overlay["strike_price"] = 100.0

    status, payload = _post_json(f"{server_url}/api/price", overlay)
    assert status == 200

    overlaid_case = apply_standalone_option_case_overlay(base_case, overlay)
    _, _, expected_display = price_standalone_option_case(overlaid_case)

    assert payload == expected_display


def test_api_price_rejects_malformed_json_body(server_url: str) -> None:
    request = urllib.request.Request(
        f"{server_url}/api/price",
        data=b"not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request)
        raise AssertionError("expected HTTPError")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        payload = json.loads(exc.read())
        assert "error" in payload


def test_api_price_rejects_overlay_with_missing_field(server_url: str) -> None:
    status, payload = _post_json(f"{server_url}/api/price", {"option_type": "CALL"})
    assert status == 400
    assert "missing required field" in payload["error"]


@_QUANTLIB_SKIP
def test_api_price_rejects_non_finite_value(server_url: str) -> None:
    base_case = load_base_case()
    overlay = extract_standalone_option_case_overlay(base_case)
    overlay["notional"] = float("nan")

    # Python's json module round-trips NaN as the non-standard "NaN" token
    # (both json.dumps and json.loads accept it by default), so this value
    # reaches the existing BondOption finite-number check unchanged -- the
    # 400 comes from that constructor, not from JSON parsing.
    body = json.dumps(overlay).encode("utf-8")
    request = urllib.request.Request(
        f"{server_url}/api/price",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request)
        raise AssertionError("expected HTTPError")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


# --- Issue #138: /api/case (load + validate + price an uploaded case) -----------


def _example_case_bytes() -> bytes:
    return _EXAMPLE_PATH.read_bytes()


@_QUANTLIB_SKIP
def test_api_case_matches_direct_call_to_price_standalone_option_case(server_url: str) -> None:
    case_bytes = _example_case_bytes()
    status, payload = _post_bytes(f"{server_url}/api/case", case_bytes)
    assert status == 200

    case = json.loads(case_bytes.decode("utf-8"))
    _, _, expected_display = price_standalone_option_case(case)

    assert payload["case"] == case
    assert payload["display"] == expected_display
    assert payload["overlay"] == extract_standalone_option_case_overlay(case)
    assert payload["context"] == extract_standalone_option_case_context(case)


def test_api_case_rejects_invalid_utf8_bytes(server_url: str) -> None:
    status, payload = _post_bytes(f"{server_url}/api/case", b"\xff\xfe not valid utf-8")
    assert status == 400
    assert "UTF-8" in payload["error"]


def test_api_case_rejects_malformed_json(server_url: str) -> None:
    status, payload = _post_bytes(f"{server_url}/api/case", b"not json at all")
    assert status == 400
    assert "error" in payload


def test_api_case_rejects_schema_violation(server_url: str) -> None:
    case = json.loads(_example_case_bytes())
    del case["valuation_date"]  # a required top-level key
    status, payload = _post_bytes(f"{server_url}/api/case", json.dumps(case).encode("utf-8"))
    assert status == 400
    assert "missing required top-level key" in payload["error"]


def test_api_case_uploaded_case_is_never_written_to_disk(server_url: str) -> None:
    # A stateless bridge: uploading a case must not create any file
    # anywhere the server can see, and must not touch the bundled example.
    before = load_base_case()
    _post_bytes(f"{server_url}/api/case", _example_case_bytes())
    after = load_base_case()
    assert before == after


def test_api_case_accepts_domain_failed_case_with_http_200(server_url: str) -> None:
    # A guard-FAILED (YIELD_VOL) case needs no QuantLib and carries no
    # assumptions (see test_standalone_option_workbench.py) -- this proves a
    # well-formed case that fails to *price* is not a bridge/schema error:
    # it is still a normal HTTP 200 response, exactly like /api/base and
    # /api/price already treat a domain FAILED PricingResult.
    case = json.loads(_example_case_bytes())
    case["volatility_input"] = {**case["volatility_input"], "volatility_basis": "YIELD_VOL"}

    status, payload = _post_bytes(f"{server_url}/api/case", json.dumps(case).encode("utf-8"))
    assert status == 200
    assert payload["display"]["status"] == "FAILED"
    assert payload["case"] == case


# --- Issue #138: /api/case/price (price an explicit case + overlay) -------------


@_QUANTLIB_SKIP
def test_api_case_price_matches_direct_call_to_price_standalone_option_case(
    server_url: str,
) -> None:
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)
    overlay["option_type"] = "PUT"
    overlay["strike_price"] = 100.0

    status, payload = _post_json(
        f"{server_url}/api/case/price", {"case": case, "overlay": overlay}
    )
    assert status == 200

    overlaid_case = apply_standalone_option_case_overlay(case, overlay)
    _, _, expected_display = price_standalone_option_case(overlaid_case)
    assert payload == expected_display


def test_api_case_price_rejects_missing_case_or_overlay(server_url: str) -> None:
    status, payload = _post_json(f"{server_url}/api/case/price", {"overlay": {}})
    assert status == 400
    assert "error" in payload

    status, payload = _post_json(f"{server_url}/api/case/price", {"case": {}})
    assert status == 400
    assert "error" in payload


def test_api_case_price_rejects_overlay_with_missing_field(server_url: str) -> None:
    case = json.loads(_example_case_bytes())
    status, payload = _post_json(
        f"{server_url}/api/case/price", {"case": case, "overlay": {"option_type": "CALL"}}
    )
    assert status == 400
    assert "missing required field" in payload["error"]


def test_api_case_price_uses_the_given_case_not_the_bundled_one(server_url: str) -> None:
    # Mutate the reference-data issuer in a fresh case copy; the response's
    # own pricing must reflect that explicit case, proving /api/case/price
    # never silently falls back to re-reading the bundled example from disk.
    case = json.loads(_example_case_bytes())
    case["bond_reference_data_universe"][0] = {
        **case["bond_reference_data_universe"][0],
        "issuer": "A Totally Different Issuer",
    }
    overlay = extract_standalone_option_case_overlay(case)

    status, payload = _post_json(
        f"{server_url}/api/case/price", {"case": case, "overlay": overlay}
    )
    assert status == 200

    _, _, expected_display = price_standalone_option_case(case)
    assert payload == expected_display


# --- Bloomberg quote refresh: /api/case/bloomberg --------------------------------
#
# No real blpapi/network/system clock: load_bloomberg_bond_quote itself is
# already fully covered by tests/test_bloomberg_bond_quote.py, and the
# Bloomberg orchestration (envelope parsing, ISIN read, acquisition
# timestamp, display merge) is already fully covered by
# tests/test_standalone_option_workbench.py. Here, only monkeypatch the same
# two seams the workbench module itself exposes for this purpose
# (load_bloomberg_bond_quote, _shiori_acquisition_now) to prove this route
# wires overlay application + the existing Bloomberg workflow together
# correctly, over real HTTP.

_BLOOMBERG_SECURITY = "91282CQX Govt"
_FIXED_ACQUIRED_AT = datetime(2026, 7, 1, 16, 5, 0, tzinfo=UTC)


def _install_fake_bloomberg_loader(monkeypatch, *, error=None):
    calls: list[dict] = []

    def fake_loader(*, security, isin, quote_side):
        calls.append({"security": security, "isin": isin, "quote_side": quote_side})
        if error is not None:
            raise error
        return BLIBondQuote(
            isin=isin,
            currency=Currency.USD,
            price_type=BLIQuoteBasis.PRICE,
            quote_side=TreasuryFTPQuoteSide.MID,
            source_system="BLOOMBERG_DAPI",
            status=BLIMarketDataStatus.ACTIVE,
            clean_price_per_100=101.25,
            accrued_interest_per_100=0.42,
        )

    monkeypatch.setattr(workbench_module, "load_bloomberg_bond_quote", fake_loader)
    return calls


def _install_fixed_clock(monkeypatch, acquired_at: datetime = _FIXED_ACQUIRED_AT):
    monkeypatch.setattr(workbench_module, "_shiori_acquisition_now", lambda: acquired_at)


@_QUANTLIB_SKIP
def test_api_case_bloomberg_matches_direct_call_to_the_existing_bloomberg_workflow(
    server_url: str, monkeypatch
) -> None:
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)
    overlay["strike_price"] = 100.0

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": overlay,
            "bloomberg_security": _BLOOMBERG_SECURITY,
            "quote_side": "MID",
        },
    )
    assert status == 200

    overlaid_case = apply_standalone_option_case_overlay(case, overlay)
    _, _, _, expected_display = price_standalone_option_case_with_bloomberg_quote(
        overlaid_case,
        bloomberg_security=_BLOOMBERG_SECURITY,
        quote_side=TreasuryFTPQuoteSide.MID,
    )
    assert payload == expected_display
    assert payload["live_bloomberg_quote"]["refreshed_scope"] == "BOND_QUOTE_ONLY"
    assert payload["live_bloomberg_quote"]["other_market_inputs"] == "CASE_JSON_UNCHANGED"


@_QUANTLIB_SKIP
def test_api_case_bloomberg_uses_the_given_cases_isin_not_the_bundled_ones(
    server_url: str, monkeypatch
) -> None:
    # Mutate the ISIN in a fresh case copy (both bond_option and its matching
    # reference-data record, so the case stays internally valid); the loader
    # must be called with *this* case's ISIN, proving the expected ISIN
    # always comes from the active case, never the bundled default and never
    # a separately supplied value.
    calls = _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    case = json.loads(_example_case_bytes())
    case["bond_option"] = {**case["bond_option"], "underlying_isin": "US9999999999"}
    case["bond_reference_data_universe"][0] = {
        **case["bond_reference_data_universe"][0],
        "isin": "US9999999999",
    }
    overlay = extract_standalone_option_case_overlay(case)

    status, _payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": overlay,
            "bloomberg_security": _BLOOMBERG_SECURITY,
            "quote_side": "MID",
        },
    )
    assert status == 200
    assert calls == [
        {
            "security": _BLOOMBERG_SECURITY,
            "isin": "US9999999999",
            "quote_side": TreasuryFTPQuoteSide.MID,
        }
    ]


@_QUANTLIB_SKIP
def test_api_case_bloomberg_calls_the_loader_exactly_once(server_url: str, monkeypatch) -> None:
    calls = _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)

    status, _payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": overlay,
            "bloomberg_security": _BLOOMBERG_SECURITY,
            "quote_side": "MID",
        },
    )
    assert status == 200
    assert len(calls) == 1


def test_api_case_bloomberg_rejects_missing_required_keys(server_url: str) -> None:
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {"case": case, "overlay": overlay, "bloomberg_security": _BLOOMBERG_SECURITY},
    )
    assert status == 400
    assert "error" in payload

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {"case": case, "overlay": overlay, "quote_side": "MID"},
    )
    assert status == 400
    assert "error" in payload


def test_api_case_bloomberg_rejects_blank_quote_side_with_no_hidden_default(
    server_url: str,
) -> None:
    # No monkeypatching here: load_bloomberg_bond_quote's own quote_side
    # validation (coerce_enum) runs before any network/blpapi involvement, so
    # a blank quote_side is rejected deterministically -- proving there is
    # no hidden default quote side substituted anywhere in this path.
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": overlay,
            "bloomberg_security": _BLOOMBERG_SECURITY,
            "quote_side": "",
        },
    )
    assert status == 400
    assert "quote_side" in payload["error"]


def test_api_case_bloomberg_failure_never_falls_back_to_the_original_quote(
    server_url: str, monkeypatch
) -> None:
    _install_fake_bloomberg_loader(
        monkeypatch, error=BLIBloombergDapiError("Bloomberg terminal not logged in")
    )
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)
    original_bond_quote = case["bond_quote"]

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": overlay,
            "bloomberg_security": _BLOOMBERG_SECURITY,
            "quote_side": "MID",
        },
    )
    assert status == 400
    assert "Bloomberg terminal not logged in" in payload["error"]
    # The request body's own case must never be mutated server-side, and no
    # fallback pricing using the original quote is ever attempted.
    assert case["bond_quote"] == original_bond_quote


def test_api_case_bloomberg_rejects_blank_security(server_url: str, monkeypatch) -> None:
    calls = _install_fake_bloomberg_loader(monkeypatch)
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {"case": case, "overlay": overlay, "bloomberg_security": "  ", "quote_side": "MID"},
    )
    assert status == 400
    assert "bloomberg_security" in payload["error"]
    assert calls == []


# --- Instrument-first Bloomberg lookup: /api/bloomberg/bond ----------------------


def test_api_bloomberg_bond_resolves_isin_and_calls_loader_with_qualified_identifier(
    server_url: str, monkeypatch
) -> None:
    calls = []

    def fake_loader(*, identifier, quote_side):
        calls.append({"identifier": identifier, "quote_side": quote_side})
        return {
            "isin": "US91282CLJ89",
            "cusip": "91282CLJ8",
            "name": "T 4 1/8 01/31/31 Govt",
            "currency": "USD",
            "quote_side": quote_side,
            "clean_price_per_100": 99.75,
            "accrued_interest_per_100": 0.51,
        }

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", fake_loader)
    monkeypatch.setattr(
        server_module, "_shiori_acquisition_now", lambda: datetime(2026, 7, 1, 16, 5, 0, tzinfo=UTC)
    )

    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "us91282clj89", "quote_side": "MID"},
    )

    assert status == 200
    assert payload == {
        "isin": "US91282CLJ89",
        "cusip": "91282CLJ8",
        "name": "T 4 1/8 01/31/31 Govt",
        "currency": "USD",
        "quote_side": "MID",
        "clean_price_per_100": 99.75,
        "accrued_interest_per_100": 0.51,
        "acquired_at": "2026-07-01T16:05:00+00:00",
        "source_system": "BLOOMBERG_DAPI",
    }
    # lowercased/whitespace-laden input still resolves to the exact
    # symbology-qualified, uppercased identifier -- never a yellow-key guess.
    assert calls == [{"identifier": "/isin/US91282CLJ89", "quote_side": "MID"}]


def test_api_bloomberg_bond_clock_captured_only_after_successful_loader_return(
    server_url: str, monkeypatch
) -> None:
    order: list[str] = []

    def fake_loader(*, identifier, quote_side):
        order.append("loader")
        return {
            "isin": "US91282CLJ89",
            "cusip": "91282CLJ8",
            "name": "x",
            "currency": "USD",
            "quote_side": quote_side,
            "clean_price_per_100": 100.0,
            "accrued_interest_per_100": 0.1,
        }

    def fake_clock():
        order.append("clock")
        return datetime(2026, 7, 1, 16, 5, 0, tzinfo=UTC)

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", fake_loader)
    monkeypatch.setattr(server_module, "_shiori_acquisition_now", fake_clock)

    status, _payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "US91282CLJ89", "quote_side": "MID"},
    )

    assert status == 200
    assert order == ["loader", "clock"]


def test_api_bloomberg_bond_clock_never_called_when_loader_raises(
    server_url: str, monkeypatch
) -> None:
    clock_calls = []

    def fake_loader(*, identifier, quote_side):
        raise BLIBloombergDapiError("boom")

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", fake_loader)
    monkeypatch.setattr(
        server_module, "_shiori_acquisition_now", lambda: clock_calls.append(1) or datetime.now()
    )

    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "US91282CLJ89", "quote_side": "MID"},
    )

    assert status == 400
    assert "boom" in payload["error"]
    assert clock_calls == []


def test_api_bloomberg_bond_resolves_a_cusip(server_url: str, monkeypatch) -> None:
    calls = []

    def fake_loader(*, identifier, quote_side):
        calls.append(identifier)
        return {
            "isin": "US91282CLJ89",
            "cusip": "91282CLJ8",
            "name": "x",
            "currency": "USD",
            "quote_side": quote_side,
            "clean_price_per_100": 100.0,
            "accrued_interest_per_100": 0.1,
        }

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", fake_loader)

    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "91282CLJ8", "quote_side": "BID"},
    )

    assert status == 200
    assert payload["cusip"] == "91282CLJ8"
    assert calls == ["/cusip/91282CLJ8"]


def test_api_bloomberg_bond_rejects_a_yellow_key_ticker(server_url: str, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        server_module,
        "load_bloomberg_bond_identity_and_quote",
        lambda **kwargs: calls.append(kwargs) or {},
    )

    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "91282CQX Govt", "quote_side": "MID"},
    )

    assert status == 400
    assert "ISIN" in payload["error"] and "CUSIP" in payload["error"]
    assert calls == []


def test_api_bloomberg_bond_rejects_missing_required_keys(server_url: str) -> None:
    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond", {"bond_identifier": "US91282CLJ89"}
    )
    assert status == 400
    assert "error" in payload

    status, payload = _post_json(f"{server_url}/api/bloomberg/bond", {"quote_side": "MID"})
    assert status == 400
    assert "error" in payload


def test_api_bloomberg_bond_requires_quote_side_with_no_hidden_default(server_url: str) -> None:
    # No monkeypatching: quote_side validation happens before any Bloomberg
    # call, so this never needs blpapi installed.
    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "US91282CLJ89", "quote_side": ""},
    )
    assert status == 400
    assert "quote_side" in payload["error"]


def test_api_bloomberg_bond_failure_returns_the_real_error(server_url: str, monkeypatch) -> None:
    def fake_loader(*, identifier, quote_side):
        raise BLIBloombergDapiError("Bloomberg DAPI session failed to start")

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", fake_loader)

    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "US91282CLJ89", "quote_side": "MID"},
    )

    assert status == 400
    assert "Bloomberg DAPI session failed to start" in payload["error"]


def test_api_bloomberg_bond_never_calls_pricing(server_url: str, monkeypatch) -> None:
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("bond lookup must never call the pricing entry point")

    monkeypatch.setattr(server_module, "price_standalone_option_case", _must_not_be_called)
    monkeypatch.setattr(
        server_module,
        "load_bloomberg_bond_identity_and_quote",
        lambda **kwargs: {
            "isin": "US91282CLJ89",
            "cusip": "91282CLJ8",
            "name": "x",
            "currency": "USD",
            "quote_side": kwargs["quote_side"],
            "clean_price_per_100": 100.0,
            "accrued_interest_per_100": 0.1,
        },
    )

    status, _payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "US91282CLJ89", "quote_side": "MID"},
    )
    assert status == 200


# --- Issue #138: /api/export/json and /api/export/markdown ----------------------


def _synthetic_display() -> dict:
    return {
        "status": "SUCCESS",
        "product_id": "TEST-PRODUCT-ID",
        "model_fair_premium_per_100": 4.5,
        "total_notional_model_fair_premium": 2.25,
        "result_currency": "USD",
        "errors": [],
    }


def test_api_export_json_matches_render_helper(server_url: str) -> None:
    display = _synthetic_display()
    status, payload = _post_json(f"{server_url}/api/export/json", {"display": display})
    assert status == 200
    assert payload["content"] == render_standalone_run_as_json(display)
    assert payload["filename"] == "shiori_standalone_option_run.json"
    assert payload["mime"] == "application/json"


def test_api_export_markdown_matches_render_helper(server_url: str) -> None:
    display = _synthetic_display()
    status, payload = _post_json(f"{server_url}/api/export/markdown", {"display": display})
    assert status == 200
    assert payload["content"] == render_standalone_run_as_markdown(display)
    assert payload["filename"] == "shiori_standalone_option_run.md"
    assert payload["mime"] == "text/markdown"


def test_api_export_rejects_missing_display(server_url: str) -> None:
    status, payload = _post_json(f"{server_url}/api/export/json", {})
    assert status == 400
    assert "error" in payload


def test_api_export_json_never_calls_pricing(server_url: str, monkeypatch) -> None:
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("export must never call the pricing entry point")

    monkeypatch.setattr(server_module, "price_standalone_option_case", _must_not_be_called)

    display = _synthetic_display()
    status, payload = _post_json(f"{server_url}/api/export/json", {"display": display})
    assert status == 200
    assert payload["content"] == render_standalone_run_as_json(display)


def test_api_export_markdown_never_calls_pricing(server_url: str, monkeypatch) -> None:
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("export must never call the pricing entry point")

    monkeypatch.setattr(server_module, "price_standalone_option_case", _must_not_be_called)

    display = _synthetic_display()
    status, payload = _post_json(f"{server_url}/api/export/markdown", {"display": display})
    assert status == 200
    assert payload["content"] == render_standalone_run_as_markdown(display)


def test_export_helpers_write_no_files(monkeypatch) -> None:
    def _open_must_not_be_called(*args, **kwargs):
        raise AssertionError("export must never open/write a file")

    monkeypatch.setattr("builtins.open", _open_must_not_be_called)

    display = _synthetic_display()
    json_result = export_current_run_as_json(display)
    markdown_result = export_current_run_as_markdown(display)

    assert json_result["content"] == render_standalone_run_as_json(display)
    assert markdown_result["content"] == render_standalone_run_as_markdown(display)
