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

    status, payload = _post_json(f"{server_url}/api/case/price", {"case": case, "overlay": overlay})
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

    status, payload = _post_json(f"{server_url}/api/case/price", {"case": case, "overlay": overlay})
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
    (
        _,
        _,
        _,
        expected_display,
        expected_case,
    ) = price_standalone_option_case_with_bloomberg_quote(
        overlaid_case,
        bloomberg_security=_BLOOMBERG_SECURITY,
        quote_side=TreasuryFTPQuoteSide.MID,
    )
    # The route returns the envelope it actually priced alongside the display,
    # so a client adopts a priced case instead of assembling one (Issue #143).
    assert payload == {"case": expected_case, "display": expected_display}
    display = payload["display"]
    assert display["live_bloomberg_quote"]["refreshed_scope"] == "BOND_QUOTE_ONLY"
    assert display["live_bloomberg_quote"]["other_market_inputs"] == "CASE_JSON_UNCHANGED"

    # The returned case is the overlaid case with exactly the two documented
    # substitutions -- nothing else moved, and nothing was invented.
    priced_case = payload["case"]
    assert priced_case["pricing_timestamp"] == display["live_bloomberg_quote"]["acquired_at"]
    assert (
        priced_case["bond_quote"]["clean_price_per_100"]
        == display["live_bloomberg_quote"]["clean_price_per_100"]
    )
    unchanged = {
        k: v for k, v in priced_case.items() if k not in ("bond_quote", "pricing_timestamp")
    }
    assert unchanged == {
        k: v for k, v in overlaid_case.items() if k not in ("bond_quote", "pricing_timestamp")
    }


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


def test_api_bloomberg_bond_passes_bond_master_through_verbatim(
    server_url: str, monkeypatch
) -> None:
    """PR #141 second revision: the loader's ``bond_master`` dict (whatever
    it currently contains, confirmed or still-None pending mnemonic
    confirmation) flows through this route unchanged -- the server adds no
    Bond Master mapping/parsing of its own."""

    bond_master = {
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

    def fake_loader(*, identifier, quote_side):
        return {
            "isin": "US91282CLJ89",
            "cusip": "91282CLJ8",
            "name": "T 4 1/8 01/31/31 Govt",
            "currency": "USD",
            "quote_side": quote_side,
            "clean_price_per_100": 99.75,
            "accrued_interest_per_100": 0.51,
            "bond_master": bond_master,
        }

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", fake_loader)
    monkeypatch.setattr(
        server_module, "_shiori_acquisition_now", lambda: datetime(2026, 7, 1, 16, 5, 0, tzinfo=UTC)
    )

    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "US91282CLJ89", "quote_side": "MID"},
    )

    assert status == 200
    assert payload["bond_master"] == bond_master


def test_api_bloomberg_bond_passes_bond_master_raw_through_verbatim(
    server_url: str, monkeypatch
) -> None:
    """PR #141 third revision: the loader's ``bond_master_raw`` dict (raw,
    display-only Bloomberg fields such as day count/maturity type/calc type)
    flows through this route unchanged, exactly like ``bond_master`` -- the
    server adds no Bond Master mapping/parsing of its own."""

    bond_master_raw = {
        "day_count": "ACT/ACT",
        "maturity_type": "AT MATURITY",
        "calc_type": "STREET CONVENTION",
    }

    def fake_loader(*, identifier, quote_side):
        return {
            "isin": "US91282CLJ89",
            "cusip": "91282CLJ8",
            "name": "T 4 1/8 01/31/31 Govt",
            "currency": "USD",
            "quote_side": quote_side,
            "clean_price_per_100": 99.75,
            "accrued_interest_per_100": 0.51,
            "bond_master": dict.fromkeys(
                (
                    "coupon",
                    "coupon_frequency",
                    "issue_date",
                    "maturity_date",
                    "day_count",
                    "first_coupon_date",
                    "last_coupon_date",
                    "redemption_amount",
                    "callable_flag",
                    "sinkable_flag",
                    "bond_type",
                    "yield_convention",
                    "business_day_convention",
                )
            ),
            "bond_master_raw": bond_master_raw,
        }

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", fake_loader)
    monkeypatch.setattr(
        server_module, "_shiori_acquisition_now", lambda: datetime(2026, 7, 1, 16, 5, 0, tzinfo=UTC)
    )

    status, payload = _post_json(
        f"{server_url}/api/bloomberg/bond",
        {"bond_identifier": "US91282CLJ89", "quote_side": "MID"},
    )

    assert status == 200
    assert payload["bond_master_raw"] == bond_master_raw


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


# --- POST /api/case/validate: the real typed builder decides (Issue #143) -----


def test_validate_case_accepts_the_bundled_base_case() -> None:
    assert server_module.validate_case(load_base_case()) == {"ready": True, "error": None}


def test_validate_case_runs_the_real_builder_and_prices_nothing(monkeypatch) -> None:
    """Requirement 5/6: validation must be the *same* typed builder the pricing
    call uses, and must not reach the engine."""

    calls: list[object] = []
    real_builder = server_module.build_request_from_standalone_option_case

    def _spy(case):
        calls.append(case)
        return real_builder(case)

    monkeypatch.setattr(server_module, "build_request_from_standalone_option_case", _spy)

    def _explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("validation must not price")

    monkeypatch.setattr(server_module, "price_standalone_option_case", _explode)

    base_case = load_base_case()
    assert server_module.validate_case(base_case)["ready"] is True
    assert calls == [base_case]


def test_validate_case_reports_an_unknown_top_level_key_verbatim() -> None:
    case = {**load_base_case(), "surprise_key": 1}
    result = server_module.validate_case(case)
    assert result["ready"] is False
    assert "unknown top-level key" in result["error"]
    assert "surprise_key" in result["error"]


def test_validate_case_reports_a_missing_required_value_verbatim() -> None:
    case = load_base_case()
    case["bond_option"] = {**case["bond_option"], "strike_price": None}
    result = server_module.validate_case(case)
    assert result["ready"] is False
    assert "strike_price is required" in result["error"]


def test_validate_case_reports_standalone_eligibility_rejection() -> None:
    """A structurally complete but route-ineligible bond is exactly the case a
    front-end 'is every field non-blank' check would wave through."""

    case = load_base_case()
    universe = [dict(record) for record in case["bond_reference_data_universe"]]
    universe[0]["callable_flag"] = True
    case["bond_reference_data_universe"] = universe

    result = server_module.validate_case(case)
    assert result["ready"] is False
    assert "FOUND_INELIGIBLE" in result["error"]
    assert "callable" in result["error"]


def test_api_case_validate_returns_ready_for_a_valid_case(server_url: str) -> None:
    status, payload = _post_json(f"{server_url}/api/case/validate", load_base_case())
    assert status == 200
    assert payload == {"ready": True, "error": None}


def test_api_case_validate_returns_http_200_with_ready_false_for_a_bad_case(
    server_url: str,
) -> None:
    """A draft the builder rejects is a normal outcome while the trader is still
    completing the ticket -- never a bridge error."""

    case = load_base_case()
    case["bond_option"] = {**case["bond_option"], "notional": -5.0}
    status, payload = _post_json(f"{server_url}/api/case/validate", case)
    assert status == 200
    assert payload["ready"] is False
    assert "notional must be positive" in payload["error"]


def test_api_case_validate_rejects_a_malformed_body(server_url: str) -> None:
    status, payload = _post_bytes(f"{server_url}/api/case/validate", b"{not json")
    assert status == 400
    assert "invalid JSON body" in payload["error"]


# --- Issues #157/#161: POST /api/bond/advanced-profile ----------------------------------------

_PROFILE_BODY = {
    "convention_profile": "UST",
    "isin": "US91282CLJ89",
    "currency": "USD",
    "bond_master": {
        "coupon": 0.0375,
        "coupon_frequency": "SEMI_ANNUAL",
        "issue_date": "2024-01-31",
        "maturity_date": "2031-01-31",
        "first_coupon_date": "2024-07-31",
        "callable_flag": False,
        "sinkable_flag": False,
        "day_count": None,
        "bond_type": None,
        "last_coupon_date": None,
    },
    "bond_master_raw": {
        "day_count": "ACT/ACT",
        "maturity_type": "AT MATURITY",
        "calc_type": "STREET CONVENTION",
    },
    "valuation_date": "2026-07-20",
    "expiry_date": "2026-10-20",
}


@_QUANTLIB_SKIP
def test_api_advanced_profile_returns_every_field_with_its_provenance(server_url: str) -> None:
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", _PROFILE_BODY)

    assert status == 200
    assert payload["supported"] is True
    assert payload["convention_profile"] == "UST"
    assert payload["rejection_reasons"] == []
    assert payload["pending_field_paths"] == []
    assert payload["unresolved_fields"] == []
    by_path = {field["path"]: field for field in payload["fields"]}
    assert set(by_path) == {
        "bond_reference_data_universe.0.day_count",
        "bond_reference_data_universe.0.bond_type",
        "bond_reference_data_universe.0.ex_dividend_days",
        "bond_reference_data_universe.0.last_coupon_date",
        "bond_reference_data_universe.0.status",
        "reporting_date",
        "forward_settlement_date",
        "option_settlement_date",
    }
    assert all(field["provenance"] for field in payload["fields"])
    assert by_path["reporting_date"]["value"] == "2026-07-20"
    assert by_path["option_settlement_date"]["value"] == "2026-10-21"


@_QUANTLIB_SKIP
def test_api_advanced_profile_omits_settlement_dates_until_an_expiry_exists(
    server_url: str,
) -> None:
    body = {**_PROFILE_BODY, "expiry_date": None}
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 200
    assert payload["supported"] is True
    assert payload["pending_field_paths"] == [
        "forward_settlement_date",
        "option_settlement_date",
    ]
    assert len(payload["fields"]) == 6


@_QUANTLIB_SKIP
def test_api_advanced_profile_reports_an_unsupported_bond_as_a_normal_answer(
    server_url: str,
) -> None:
    """A bond outside the profile is a real answer with reasons, not a bridge
    error -- the browser has to be able to show why it filled nothing."""

    body = {
        **_PROFILE_BODY,
        "isin": "GB00BFX0ZL78",
        "currency": "GBP",
        "bond_master_raw": {
            "day_count": "ACT/ACT",
            "maturity_type": "NORMAL",
            "calc_type": "UK:BUMP/DMO METHOD",
        },
    }
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 200
    assert payload["supported"] is False
    assert payload["fields"] == []
    assert any("is not USD" in reason for reason in payload["rejection_reasons"])


@_QUANTLIB_SKIP
def test_api_advanced_profile_admits_a_real_ust_returning_maturity_type_normal(
    server_url: str,
) -> None:
    """Issue #161's UAT regression, at the route: a display-only description
    string must not turn an ordinary Treasury into an unsupported product."""

    body = {
        **_PROFILE_BODY,
        "isin": "US91282CMC28",
        "bond_master_raw": {
            "day_count": "ACT/ACT",
            "maturity_type": "NORMAL",
            "calc_type": "STREET CONVENTION",
        },
    }
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 200
    assert payload["supported"] is True
    assert payload["rejection_reasons"] == []
    assert payload["unresolved_fields"] == []
    assert len(payload["fields"]) == 8


@_QUANTLIB_SKIP
def test_api_advanced_profile_serializes_a_partial_answer_with_its_blocked_field(
    server_url: str,
) -> None:
    """Issue #161: one field the resolver refused comes back in
    ``unresolved_fields``; the other seven still come back in ``fields``."""

    body = {
        **_PROFILE_BODY,
        "bond_master_raw": {**_PROFILE_BODY["bond_master_raw"], "day_count": "ISMA-30/360"},
    }
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 200
    assert payload["supported"] is True
    assert payload["rejection_reasons"] == []
    assert [item["path"] for item in payload["unresolved_fields"]] == [
        "bond_reference_data_universe.0.day_count"
    ]
    assert "ISMA-30/360" in payload["unresolved_fields"][0]["reason"]
    assert len(payload["fields"]) == 7
    assert "bond_reference_data_universe.0.day_count" not in {
        field["path"] for field in payload["fields"]
    }


@_QUANTLIB_SKIP
def test_api_advanced_profile_never_offers_a_repair_route_for_a_missing_bond_master_date(
    server_url: str,
) -> None:
    """Issue #161 P2 correction: ``issue_date``, ``maturity_date`` and
    ``first_coupon_date`` have no Advanced override anywhere on this route,
    so a field that cannot resolve because one of them is missing must not
    be reported in ``unresolved_fields`` -- that list is the browser's
    signal that a real Advanced route exists, and here there is none."""

    body = {
        **_PROFILE_BODY,
        "bond_master": {**_PROFILE_BODY["bond_master"], "first_coupon_date": None},
    }
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 200
    assert payload["supported"] is True
    assert payload["rejection_reasons"] == []
    assert payload["unresolved_fields"] == []
    assert "bond_reference_data_universe.0.last_coupon_date" not in {
        field["path"] for field in payload["fields"]
    }
    # Every other field -- including status, since maturity_date is present --
    # still resolves; only last_coupon_date is affected by the missing
    # first_coupon_date.
    assert len(payload["fields"]) == 7


def test_api_advanced_profile_rejects_a_body_missing_required_keys(server_url: str) -> None:
    body = {key: value for key, value in _PROFILE_BODY.items() if key != "bond_master"}
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 400
    assert "bond_master" in payload["error"]


def test_api_advanced_profile_rejects_a_body_missing_convention_profile(server_url: str) -> None:
    """Issue #157 P1-1 correction: convention_profile is required browser
    state, never inferred or defaulted server-side."""

    body = {
        key: value for key, value in _PROFILE_BODY.items() if key != "convention_profile"
    }
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 400
    assert "convention_profile" in payload["error"]


@_QUANTLIB_SKIP
def test_api_advanced_profile_rejects_an_unknown_convention_profile_rather_than_defaulting(
    server_url: str,
) -> None:
    body = {**_PROFILE_BODY, "convention_profile": "GILT"}
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 400
    assert "convention_profile" in payload["error"]
    assert "GILT" in payload["error"]


@_QUANTLIB_SKIP
def test_api_advanced_profile_admits_a_shape_compatible_non_us_isin_bond(
    server_url: str,
) -> None:
    """Positive regression for the P1-1 correction: admission is shape-only.
    A bond whose ISIN carries no US country prefix at all is still admitted
    because its terms fit and "UST" was explicitly selected -- this makes no
    claim about who issued it."""

    body = {**_PROFILE_BODY, "isin": "XS0999999999"}
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 200
    assert payload["supported"] is True
    assert payload["rejection_reasons"] == []
    assert len(payload["fields"]) == 8


@_QUANTLIB_SKIP
def test_api_advanced_profile_rejects_an_irregular_schedule(
    server_url: str,
) -> None:
    body = {
        **_PROFILE_BODY,
        "bond_master": {
            **_PROFILE_BODY["bond_master"],
            "issue_date": "2024-03-05",
        },
    }
    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", body)

    assert status == 200
    assert payload["supported"] is False
    assert payload["fields"] == []
    assert payload["unresolved_fields"] == []
    assert "regular coupon schedules only" in payload["rejection_reasons"][0]
    assert "editing last_coupon_date cannot repair" in payload["rejection_reasons"][0]


def test_api_advanced_profile_rejects_a_malformed_body(server_url: str) -> None:
    status, payload = _post_bytes(f"{server_url}/api/bond/advanced-profile", b"{not json")
    assert status == 400
    assert "invalid JSON body" in payload["error"]


@_QUANTLIB_SKIP
def test_api_advanced_profile_makes_no_bloomberg_call_and_reads_no_clock(monkeypatch) -> None:
    """The route is a pure function of its body: it prices nothing, loads
    nothing, and must stay callable while the trader is still typing."""

    def _fail(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the UST profile route must not call Bloomberg or price")

    monkeypatch.setattr(server_module, "load_bloomberg_bond_identity_and_quote", _fail)
    monkeypatch.setattr(server_module, "price_standalone_option_case", _fail)
    monkeypatch.setattr(server_module, "_shiori_acquisition_now", _fail)

    payload = server_module.resolve_bond_advanced_profile(dict(_PROFILE_BODY))
    assert payload["supported"] is True
