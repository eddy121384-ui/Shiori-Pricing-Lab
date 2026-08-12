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
from datetime import UTC, date, datetime, timedelta
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


# --- Issue #171: live Option Discount Curve wiring (/api/case, /api/case/bloomberg) ---
#
# Deterministic: the production Curve #490 loader and this module's own
# platform-clock seam are both monkeypatched directly (the same pattern the
# Bloomberg quote-refresh tests above already use for their own loader/
# clock seams) -- no real blpapi/network/system clock anywhere in this
# section, and CI never needs a live Bloomberg Terminal.

_LIVE_CURVE_VALUATION_DATE = "2026-07-01"
_LIVE_CURVE_CLOCK = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _fake_live_option_discount_curve_result(points=None):
    from shiori_pricing_lab.data.bli_snapshot import (
        BLICurvePoint,
        BLICurvePurpose,
        BLICurveRateBasis,
        BLIMarketDataStatus,
    )
    from shiori_pricing_lab.data.bloomberg_option_discount_curve import (
        BloombergUsdSofrOptionDiscountCurveResult,
    )
    from shiori_pricing_lab.products.enums import Currency

    def _point(tenor: str, maturity_date: str, rate: float) -> BLICurvePoint:
        return BLICurvePoint(
            curve_id="USD_SOFR_OPTION_DISCOUNT_CURVE",
            curve_name="USD SOFR Option Discount Curve (Bloomberg Curve #490)",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
            source_system="BLOOMBERG_DAPI",
            status=BLIMarketDataStatus.ACTIVE,
            maturity_date=maturity_date,
        )

    # Brackets the example case's reporting_date/option_settlement_date
    # (2026-10-01) relative to its valuation_date (2026-07-01).
    default_points = (
        _point("1M", "2026-08-01", 0.030),
        _point("1Y", "2027-01-01", 0.032),
    )
    return BloombergUsdSofrOptionDiscountCurveResult(
        curve_points=points if points is not None else default_points,
        discount_factor_evidence=(),
    )


def _full_default_tenor_curve_points(base_date: date = date(2026, 7, 1)) -> tuple:
    """32 rows, one per ``DEFAULT_USD_SOFR_TENORS`` label in order -- the
    exact collection shape ``_is_previously_injected_live_curve`` (Codex P2
    review of PR #172, round 6) requires before recognizing a "previously
    injected" curve, since this injector always calls the loader with its
    own full default universe. Only used by the tests that specifically
    exercise that recognition; every other test keeps the small two-tenor
    ``_fake_live_option_discount_curve_result`` default, which deliberately
    does *not* match this shape."""

    from shiori_pricing_lab.data.bli_snapshot import (
        BLICurvePoint,
        BLICurvePurpose,
        BLICurveRateBasis,
        BLIMarketDataStatus,
    )
    from shiori_pricing_lab.data.bloomberg_option_discount_curve import DEFAULT_USD_SOFR_TENORS
    from shiori_pricing_lab.products.enums import Currency

    return tuple(
        BLICurvePoint(
            curve_id="USD_SOFR_OPTION_DISCOUNT_CURVE",
            curve_name="USD SOFR Option Discount Curve (Bloomberg Curve #490)",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=0.03,
            rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
            source_system="BLOOMBERG_DAPI",
            status=BLIMarketDataStatus.ACTIVE,
            maturity_date=(base_date + timedelta(days=30 * (index + 1))).isoformat(),
        )
        for index, tenor in enumerate(DEFAULT_USD_SOFR_TENORS)
    )


def _install_fake_live_curve_loader(monkeypatch, *, error=None, points=None):
    calls: list = []

    def fake_loader(tenors=None):
        calls.append(tenors)
        if error is not None:
            raise error
        return _fake_live_option_discount_curve_result(points=points)

    monkeypatch.setattr(
        server_module, "load_bloomberg_usd_sofr_option_discount_curve", fake_loader
    )
    return calls


def _install_fixed_curve_clock(monkeypatch, now: datetime = _LIVE_CURVE_CLOCK):
    monkeypatch.setattr(server_module, "_shiori_acquisition_now", lambda: now)


def _case_with_empty_curve_points() -> dict:
    case = json.loads(_example_case_bytes())
    case["curve_points"] = []
    assert case["valuation_date"] == _LIVE_CURVE_VALUATION_DATE
    return case


# --- Unit-level: inject_live_option_discount_curve_if_absent --------------------


def test_inject_live_curve_leaves_a_case_with_manual_nodes_untouched(monkeypatch) -> None:
    calls = _install_fake_live_curve_loader(monkeypatch)
    case = json.loads(_example_case_bytes())

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []


def test_inject_live_curve_injects_the_loaders_own_rows_when_curve_points_is_empty(
    monkeypatch,
) -> None:
    calls = _install_fake_live_curve_loader(monkeypatch)
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is not case
    assert case["curve_points"] == []  # the caller's own mapping is never mutated
    assert len(calls) == 1
    assert calls[0] is None  # the loader's own full default (32-tenor) universe
    assert [point["tenor"] for point in result["curve_points"]] == ["1M", "1Y"]
    for point in result["curve_points"]:
        assert point["curve_purpose"] == "OPTION_DISCOUNT_CURVE"
        assert point["rate_basis"] == "CONTINUOUS_ZERO_RATE"
        assert point["source_system"] == "BLOOMBERG_DAPI"
        assert point["maturity_date"] is not None
    # Every other envelope field is carried through unchanged.
    unchanged = {k: v for k, v in result.items() if k != "curve_points"}
    assert unchanged == {k: v for k, v in case.items() if k != "curve_points"}


def test_inject_live_curve_refetches_a_previously_injected_curve_on_a_second_call(
    monkeypatch,
) -> None:
    """Codex P1 review of PR #172: ``POST /api/case`` echoes back whichever
    curve it priced with, so a second Price call sends back the *previous*
    live acquisition as ``curve_points`` -- this must not read as "the
    trader already supplied a manual override" and skip both the loader and
    the same-as-of gate. Every row this injector itself writes carries the
    loader's own ``curve_id``, so it must be recognized and refreshed."""

    full_tenor_points = _full_default_tenor_curve_points()
    calls = _install_fake_live_curve_loader(monkeypatch, points=full_tenor_points)
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()

    first = server_module.inject_live_option_discount_curve_if_absent(case)
    assert len(calls) == 1
    assert len(first["curve_points"]) == 32  # the loader's own full default universe

    second = server_module.inject_live_option_discount_curve_if_absent(first)
    assert len(calls) == 2  # refetched, not silently treated as a manual override
    assert [point["tenor"] for point in second["curve_points"]] == [
        point.tenor for point in full_tenor_points
    ]


def test_inject_live_curve_still_enforces_the_same_as_of_gate_on_a_second_call(
    monkeypatch,
) -> None:
    """The same re-fetch must still fail closed if the clock has since moved
    past valuation_date -- e.g. a ticket left open across a date boundary
    must not silently reuse a curve acquired on the (now stale) prior day."""

    calls = _install_fake_live_curve_loader(
        monkeypatch, points=_full_default_tenor_curve_points()
    )
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()
    first = server_module.inject_live_option_discount_curve_if_absent(case)
    assert len(calls) == 1

    _install_fixed_curve_clock(monkeypatch, datetime(2026, 7, 2, 9, 0, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="today's date"):
        server_module.inject_live_option_discount_curve_if_absent(first)
    assert len(calls) == 1  # the loader is never reached once the gate fails


@pytest.mark.parametrize("malformed_curve_points", [None, "", {}, 0, "not-a-list"])
def test_inject_live_curve_never_substitutes_for_a_malformed_curve_points_value(
    monkeypatch, malformed_curve_points
) -> None:
    """Codex P2 review of PR #172: only an actual empty list is "no override
    supplied" -- a malformed non-list value must reach the existing, more
    specific ``curve_points must be a JSON array`` schema error instead of
    being silently replaced with a live curve."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    case = {**json.loads(_example_case_bytes()), "curve_points": malformed_curve_points}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    with pytest.raises(ValueError, match="curve_points must be a JSON array"):
        server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_does_not_discard_a_row_that_only_reuses_the_curve_id(
    monkeypatch,
) -> None:
    """Codex P2 review of PR #172, round 2: matching on curve_id alone would
    let a malformed (or deliberately caller-supplied) row using only that
    one id be silently discarded and replaced with a live curve, masking
    the real missing-field schema error a genuinely malformed row should
    raise. The full fixed-field fingerprint must not match a row missing
    every other field."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    case = {
        **json.loads(_example_case_bytes()),
        "curve_points": [{"curve_id": "USD_SOFR_OPTION_DISCOUNT_CURVE"}],
    }

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    with pytest.raises(TypeError):
        server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_does_not_discard_a_row_with_a_malformed_maturity_date(
    monkeypatch,
) -> None:
    """Codex P2 review of PR #172, round 3: matching every fixed field is not
    enough by itself -- a row satisfying the fingerprint but carrying a
    ``maturity_date`` ``BLICurvePoint`` itself would reject (a non-ISO
    string) must still reach that constructor's real error, checked with
    the exact same ``_parse_iso_date`` validator, rather than being
    misclassified as a trusted echo and silently discarded."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    malformed_point = {
        **server_module._LIVE_CURVE_POINT_FIXED_FIELDS,
        "tenor": "1Y",
        "rate": 0.03,
        "maturity_date": "not-a-date",
    }
    case = {**json.loads(_example_case_bytes()), "curve_points": [malformed_point]}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    with pytest.raises(ValueError, match="maturity_date"):
        server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_does_not_discard_a_row_with_a_non_finite_rate(monkeypatch) -> None:
    """Same as above, for ``rate`` -- checked with the exact same
    ``_require_finite_number`` validator ``BLICurvePoint`` itself uses."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    malformed_point = {
        **server_module._LIVE_CURVE_POINT_FIXED_FIELDS,
        "tenor": "1Y",
        "rate": float("nan"),
        "maturity_date": "2027-01-01",
    }
    case = {**json.loads(_example_case_bytes()), "curve_points": [malformed_point]}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    with pytest.raises(ValueError, match="rate"):
        server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_does_not_discard_a_row_with_a_blank_tenor(monkeypatch) -> None:
    """Codex P2 review of PR #172, round 4: a whitespace-only ``tenor`` is
    truthy in Python, so a truthiness check alone would still misclassify
    this row -- checked with the exact same ``_require_non_blank`` validator
    ``BLICurvePoint`` itself uses."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    malformed_point = {
        **server_module._LIVE_CURVE_POINT_FIXED_FIELDS,
        "tenor": " ",
        "rate": 0.03,
        "maturity_date": "2027-01-01",
    }
    case = {**json.loads(_example_case_bytes()), "curve_points": [malformed_point]}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    with pytest.raises(ValueError, match="tenor"):
        server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_does_not_discard_a_row_with_an_unexpected_key(monkeypatch) -> None:
    """Codex P2 review of PR #172, round 5: a row matching every fixed field
    and passing every per-field validator still must not be trusted if it
    carries one key BLICurvePoint's constructor does not accept -- that key
    set has to match exactly, or the row is left for the constructor's own
    real ``TypeError`` (unexpected keyword argument)."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    malformed_point = {
        **server_module._LIVE_CURVE_POINT_FIXED_FIELDS,
        "tenor": "1Y",
        "rate": 0.03,
        "maturity_date": "2027-01-01",
        "surprise_key": "unexpected",
    }
    case = {**json.loads(_example_case_bytes()), "curve_points": [malformed_point]}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    with pytest.raises(TypeError):
        server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_does_not_discard_a_row_missing_an_expected_key(monkeypatch) -> None:
    """Same as above, for a row missing one of BLICurvePoint's required keys
    despite otherwise matching every fixed field."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    malformed_point = {
        k: v for k, v in server_module._LIVE_CURVE_POINT_FIXED_FIELDS.items() if k != "status"
    }
    malformed_point.update(tenor="1Y", rate=0.03, maturity_date="2027-01-01")
    case = {**json.loads(_example_case_bytes()), "curve_points": [malformed_point]}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    with pytest.raises(TypeError):
        server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_rechecks_the_valuation_date_after_the_loader_returns(
    monkeypatch,
) -> None:
    """Codex P2 review of PR #172, round 5: a single pre-fetch clock read
    leaves a midnight-rollover race -- a request that passes the gate before
    the (possibly slow) Bloomberg round-trip, but whose valuation_date no
    longer matches "today" by the time the loader actually returns, must
    still fail closed rather than silently price a curve acquired on a
    different calendar day than the one it was validated against."""

    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()

    def fake_loader(tenors=None):
        # Simulate the clock rolling over to the next day while the
        # Bloomberg round-trip was in flight.
        monkeypatch.setattr(
            server_module,
            "_shiori_acquisition_now",
            lambda: datetime(2026, 7, 2, 0, 0, 5, tzinfo=UTC),
        )
        return _fake_live_option_discount_curve_result()

    monkeypatch.setattr(
        server_module, "load_bloomberg_usd_sofr_option_discount_curve", fake_loader
    )

    with pytest.raises(ValueError, match="today's date"):
        server_module.inject_live_option_discount_curve_if_absent(case)


def test_inject_live_curve_honors_an_explicit_subset_sharing_the_fingerprint(
    monkeypatch,
) -> None:
    """Codex P2 review of PR #172, round 6: matching every per-row check is
    still not enough -- this injector only ever calls the loader with its
    own full default (32-tenor) universe, so a genuine echo of its prior
    output always has exactly that many rows in that order. A caller-
    supplied collection of only a few rows that individually satisfy every
    per-row fingerprint check (e.g. two constructor-valid rows) is not that
    shape and must be honored as a deliberate explicit-subset override, not
    discarded and replaced with a fresh live fetch."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    two_row_points = [
        {
            **server_module._LIVE_CURVE_POINT_FIXED_FIELDS,
            "tenor": tenor,
            "rate": 0.03,
            "maturity_date": maturity,
        }
        for tenor, maturity in (("1M", "2026-08-01"), ("1Y", "2027-01-01"))
    ]
    case = {**json.loads(_example_case_bytes()), "curve_points": two_row_points}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []
    # A genuine, well-formed explicit subset still builds successfully --
    # this is the honored override, not a masked schema error.
    server_module.build_request_from_standalone_option_case(case)


def test_inject_live_curve_honors_a_collection_with_a_duplicate_maturity_date(
    monkeypatch,
) -> None:
    """Codex P2 review of PR #172, round 7: matching the tenor sequence is
    still not enough -- the loader also guarantees strictly increasing
    ``MATURITY`` from one tenor to the next (one of its own fail-closed
    conditions). A collection with all 32 expected tenors and fixed fields,
    but every row sharing one repeated ``maturity_date`` instead of that
    strictly increasing sequence, cannot be this loader's own output and
    must not be discarded as a trusted echo."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    full_tenor_points = _full_default_tenor_curve_points()
    duplicated_points = [
        {
            **server_module._LIVE_CURVE_POINT_FIXED_FIELDS,
            "tenor": point.tenor,
            "rate": point.rate,
            # Every row repeats the very first row's own maturity_date,
            # instead of the loader's own strictly increasing sequence.
            "maturity_date": full_tenor_points[0].maturity_date,
        }
        for point in full_tenor_points
    ]
    case = {**json.loads(_example_case_bytes()), "curve_points": duplicated_points}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []


def test_inject_live_curve_honors_a_collection_with_reversed_maturity_dates(
    monkeypatch,
) -> None:
    """Same as above, for a collection whose maturity_date values are
    individually valid and distinct but run in reverse (decreasing) order
    instead of the loader's own strictly increasing sequence."""

    calls = _install_fake_live_curve_loader(monkeypatch)
    full_tenor_points = _full_default_tenor_curve_points()
    reversed_maturities = [point.maturity_date for point in reversed(full_tenor_points)]
    reversed_points = [
        {
            **server_module._LIVE_CURVE_POINT_FIXED_FIELDS,
            "tenor": point.tenor,
            "rate": point.rate,
            "maturity_date": maturity_date,
        }
        for point, maturity_date in zip(full_tenor_points, reversed_maturities, strict=True)
    ]
    case = {**json.loads(_example_case_bytes()), "curve_points": reversed_points}

    result = server_module.inject_live_option_discount_curve_if_absent(case)

    assert result is case
    assert calls == []


def test_validate_case_reports_not_ready_for_a_non_usd_drafts_empty_curve_points() -> None:
    """Codex P2 review of PR #172, round 2: the live loader can only ever
    supply USD, so a non-USD draft with no manual curve must not read as
    ready -- /api/case would reject that same draft for lacking a
    matching-currency Option Discount Curve."""

    case = {**load_base_case(), "curve_points": []}
    case["bond_option"] = {**case["bond_option"], "currency": "EUR"}
    case["bond_quote"] = {**case["bond_quote"], "currency": "EUR"}
    case["bond_reference_data_universe"][0] = {
        **case["bond_reference_data_universe"][0],
        "currency": "EUR",
    }

    result = server_module.validate_case(case)
    assert result["ready"] is False
    assert "curve_points must not be empty" in result["error"]


def test_api_case_never_substitutes_a_live_curve_for_a_malformed_curve_points_value(
    server_url: str, monkeypatch
) -> None:
    calls = _install_fake_live_curve_loader(monkeypatch)
    case = {**json.loads(_example_case_bytes()), "curve_points": None}

    status, payload = _post_bytes(f"{server_url}/api/case", json.dumps(case).encode("utf-8"))
    assert status == 400
    assert "curve_points must be a JSON array" in payload["error"]
    assert calls == []


def test_inject_live_curve_rejects_a_valuation_date_that_is_not_today(monkeypatch) -> None:
    calls = _install_fake_live_curve_loader(monkeypatch)
    _install_fixed_curve_clock(monkeypatch, datetime(2026, 7, 2, 9, 0, 0, tzinfo=UTC))
    case = _case_with_empty_curve_points()

    with pytest.raises(ValueError, match="today's date"):
        server_module.inject_live_option_discount_curve_if_absent(case)
    assert calls == []


def test_inject_live_curve_propagates_a_bloomberg_failure_with_no_fallback(monkeypatch) -> None:
    _install_fake_live_curve_loader(
        monkeypatch, error=BLIBloombergDapiError("Bloomberg terminal not logged in")
    )
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()

    with pytest.raises(BLIBloombergDapiError, match="Bloomberg terminal not logged in"):
        server_module.inject_live_option_discount_curve_if_absent(case)


# --- POST /api/case ---------------------------------------------------------------


@_QUANTLIB_SKIP
def test_api_case_injects_the_live_curve_and_prices_when_curve_points_is_empty(
    server_url: str, monkeypatch
) -> None:
    _install_fake_live_curve_loader(monkeypatch)
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()

    status, payload = _post_bytes(f"{server_url}/api/case", json.dumps(case).encode("utf-8"))
    assert status == 200
    assert payload["display"]["status"] == "SUCCESS"
    assert [point["tenor"] for point in payload["case"]["curve_points"]] == ["1M", "1Y"]
    for point in payload["case"]["curve_points"]:
        assert point["source_system"] == "BLOOMBERG_DAPI"

    injected_case = server_module.inject_live_option_discount_curve_if_absent(case)
    _, _, expected_display = price_standalone_option_case(injected_case)
    assert payload["display"] == expected_display


@_QUANTLIB_SKIP
def test_api_case_never_calls_the_live_curve_loader_when_curve_points_is_supplied(
    server_url: str, monkeypatch
) -> None:
    calls = _install_fake_live_curve_loader(monkeypatch)
    case_bytes = _example_case_bytes()

    status, payload = _post_bytes(f"{server_url}/api/case", case_bytes)
    assert status == 200
    assert calls == []
    original_case = json.loads(case_bytes)
    assert payload["case"]["curve_points"] == original_case["curve_points"]


def test_api_case_live_curve_failure_returns_400_with_no_fallback(
    server_url: str, monkeypatch
) -> None:
    _install_fake_live_curve_loader(
        monkeypatch, error=BLIBloombergDapiError("Bloomberg terminal not logged in")
    )
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()

    status, payload = _post_bytes(f"{server_url}/api/case", json.dumps(case).encode("utf-8"))
    assert status == 400
    assert "Bloomberg terminal not logged in" in payload["error"]


def test_api_case_live_curve_same_as_of_mismatch_returns_400_and_never_calls_the_loader(
    server_url: str, monkeypatch
) -> None:
    calls = _install_fake_live_curve_loader(monkeypatch)
    _install_fixed_curve_clock(monkeypatch, datetime(2026, 7, 2, 9, 0, 0, tzinfo=UTC))
    case = _case_with_empty_curve_points()

    status, payload = _post_bytes(f"{server_url}/api/case", json.dumps(case).encode("utf-8"))
    assert status == 400
    assert "today" in payload["error"]
    assert calls == []


# --- POST /api/case/bloomberg ------------------------------------------------------


@_QUANTLIB_SKIP
def test_api_case_bloomberg_also_injects_the_live_curve_when_curve_points_is_empty(
    server_url: str, monkeypatch
) -> None:
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    _install_fake_live_curve_loader(monkeypatch)
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()
    overlay = extract_standalone_option_case_overlay(case)

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
    assert [point["tenor"] for point in payload["case"]["curve_points"]] == ["1M", "1Y"]
    for point in payload["case"]["curve_points"]:
        assert point["source_system"] == "BLOOMBERG_DAPI"


@_QUANTLIB_SKIP
def test_api_case_bloomberg_never_calls_the_live_curve_loader_when_curve_points_is_supplied(
    server_url: str, monkeypatch
) -> None:
    _install_fake_bloomberg_loader(monkeypatch)
    _install_fixed_clock(monkeypatch)
    calls = _install_fake_live_curve_loader(monkeypatch)
    case = json.loads(_example_case_bytes())
    overlay = extract_standalone_option_case_overlay(case)

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
    assert calls == []


def test_api_case_bloomberg_live_curve_failure_never_calls_the_bond_quote_loader(
    server_url: str, monkeypatch
) -> None:
    quote_calls = _install_fake_bloomberg_loader(monkeypatch)
    _install_fake_live_curve_loader(
        monkeypatch, error=BLIBloombergDapiError("curve terminal not logged in")
    )
    _install_fixed_curve_clock(monkeypatch)
    case = _case_with_empty_curve_points()
    overlay = extract_standalone_option_case_overlay(case)

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
    assert "curve terminal not logged in" in payload["error"]
    # The same-as-of failure is reported before the bond-quote loader is ever
    # called -- no live quote acquisition happens for a request that cannot
    # use it.
    assert quote_calls == []


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


# --- Issue #167: /api/bloomberg/option-discount-curve (Markets view) ------------


_CURVE_TENORS_AND_MATURITIES = [
    ("1Y", "2027-08-11"),
    ("2Y", "2028-08-11"),
    ("5Y", "2031-08-11"),
]
_CURVE_ZERO_RATES = {"1Y": 0.038172, "2Y": 0.039156, "5Y": 0.040781}
_CURVE_DFS = {"1Y": 0.972346, "2Y": 0.947680, "5Y": 0.814250}
# Deliberately different from the zero rates above (par vs. zero rate must
# never be confused -- Issue #167 boundary): a test asserting the wrong
# value here would mean the route mixed the two loaders up.
_CURVE_PAR_RATES = {"1Y": 3.75, "2Y": 3.80, "5Y": 3.95}


def _fake_curve_result():
    from shiori_pricing_lab.data.bli_snapshot import (
        BLICurvePoint,
        BLICurvePurpose,
        BLICurveRateBasis,
        BLIMarketDataStatus,
    )
    from shiori_pricing_lab.data.bloomberg_option_discount_curve import (
        BloombergDiscountFactorEvidence,
        BloombergUsdSofrOptionDiscountCurveResult,
    )
    from shiori_pricing_lab.products.enums import Currency

    curve_points = tuple(
        BLICurvePoint(
            curve_id="USD_SOFR_OPTION_DISCOUNT_CURVE",
            curve_name="USD SOFR Option Discount Curve (Bloomberg Curve #490)",
            currency=Currency.USD,
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=_CURVE_ZERO_RATES[tenor],
            rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
            source_system="BLOOMBERG_DAPI",
            status=BLIMarketDataStatus.ACTIVE,
            maturity_date=maturity,
        )
        for tenor, maturity in _CURVE_TENORS_AND_MATURITIES
    )
    evidence = tuple(
        BloombergDiscountFactorEvidence(
            tenor=tenor,
            security=f"S0490D {tenor} BLC2 Curncy",
            raw_last_price=str(_CURVE_DFS[tenor]),
            discount_factor=_CURVE_DFS[tenor],
            maturity=maturity,
        )
        for tenor, maturity in _CURVE_TENORS_AND_MATURITIES
    )
    return BloombergUsdSofrOptionDiscountCurveResult(
        curve_points=curve_points, discount_factor_evidence=evidence
    )


def _fake_par_rate_result(tenors=("1Y", "2Y", "5Y")):
    from shiori_pricing_lab.data.bloomberg_usd_sofr_par_rate_curve import (
        BloombergUsdSofrParRateCurveResult,
        BloombergUsdSofrParRatePoint,
    )

    points = tuple(
        BloombergUsdSofrParRatePoint(
            tenor=tenor,
            security=f"USOSFR{tenor} Curncy",
            raw_last_price=str(_CURVE_PAR_RATES[tenor]),
            par_rate_percent=_CURVE_PAR_RATES[tenor],
            source_system="BLOOMBERG_DAPI",
        )
        for tenor in tenors
    )
    return BloombergUsdSofrParRateCurveResult(points=points)


def _patch_curve_loaders(monkeypatch, *, par_tenors=("1Y", "2Y", "5Y")) -> None:
    monkeypatch.setattr(
        server_module, "load_bloomberg_usd_sofr_option_discount_curve", _fake_curve_result
    )
    monkeypatch.setattr(
        server_module,
        "load_bloomberg_usd_sofr_par_rate_curve",
        lambda: _fake_par_rate_result(par_tenors),
    )


def test_api_option_discount_curve_returns_nodes_from_the_production_loaders(
    server_url: str, monkeypatch
) -> None:
    _patch_curve_loaders(monkeypatch)
    monkeypatch.setattr(
        server_module,
        "_shiori_acquisition_now",
        lambda: datetime(2026, 8, 11, 12, 34, 45, tzinfo=UTC),
    )

    status, payload = _post_json(f"{server_url}/api/bloomberg/option-discount-curve", {})

    assert status == 200
    assert payload["curve_id"] == "USD_SOFR_OPTION_DISCOUNT_CURVE"
    assert payload["source_system"] == "BLOOMBERG_DAPI"
    assert payload["rate_basis"] == "CONTINUOUS_ZERO_RATE"
    assert payload["acquired_at"] == "2026-08-11T12:34:45+00:00"
    assert payload["coverage"] == {
        "first_tenor": "1Y",
        "last_tenor": "5Y",
        "first_maturity": "2027-08-11",
        "last_maturity": "2031-08-11",
    }
    assert payload["nodes"] == [
        {
            "tenor": "1Y",
            "maturity": "2027-08-11",
            "zero_rate_percent": pytest.approx(3.8172),
            "discount_factor": pytest.approx(0.972346),
            "par_rate_percent": pytest.approx(3.75),
        },
        {
            "tenor": "2Y",
            "maturity": "2028-08-11",
            "zero_rate_percent": pytest.approx(3.9156),
            "discount_factor": pytest.approx(0.947680),
            "par_rate_percent": pytest.approx(3.80),
        },
        {
            "tenor": "5Y",
            "maturity": "2031-08-11",
            "zero_rate_percent": pytest.approx(4.0781),
            "discount_factor": pytest.approx(0.814250),
            "par_rate_percent": pytest.approx(3.95),
        },
    ]


def test_api_option_discount_curve_leaves_a_node_unavailable_when_par_rate_is_missing(
    server_url: str, monkeypatch
) -> None:
    # Only 1Y/5Y par rate points are supplied -- 2Y's zero/DF node must still
    # be returned, but its par_rate_percent must be None, never fabricated,
    # never derived from the zero rate, and never causing the whole
    # response to fail.
    _patch_curve_loaders(monkeypatch, par_tenors=("1Y", "5Y"))

    status, payload = _post_json(f"{server_url}/api/bloomberg/option-discount-curve", {})

    assert status == 200
    par_rates_by_tenor = {node["tenor"]: node["par_rate_percent"] for node in payload["nodes"]}
    assert par_rates_by_tenor == {
        "1Y": pytest.approx(3.75),
        "2Y": None,
        "5Y": pytest.approx(3.95),
    }


def test_api_option_discount_curve_returns_502_on_zero_curve_loader_failure(
    server_url: str, monkeypatch
) -> None:
    def _fail():
        raise BLIBloombergDapiError("Bloomberg DAPI session failed to start")

    monkeypatch.setattr(server_module, "load_bloomberg_usd_sofr_option_discount_curve", _fail)
    monkeypatch.setattr(
        server_module,
        "load_bloomberg_usd_sofr_par_rate_curve",
        lambda: (_ for _ in ()).throw(AssertionError("par-rate loader must not run")),
    )

    status, payload = _post_json(f"{server_url}/api/bloomberg/option-discount-curve", {})

    assert status == 502
    assert "Bloomberg DAPI session failed to start" in payload["error"]


def test_api_option_discount_curve_returns_502_on_par_rate_loader_failure(
    server_url: str, monkeypatch
) -> None:
    # A par-rate-only failure must still fail the whole response -- never a
    # curve with only the Zero/DF loader's data and a silently blank SWAP
    # column.
    monkeypatch.setattr(
        server_module, "load_bloomberg_usd_sofr_option_discount_curve", _fake_curve_result
    )

    def _fail():
        raise BLIBloombergDapiError("Bloomberg DAPI par rate request failed")

    monkeypatch.setattr(server_module, "load_bloomberg_usd_sofr_par_rate_curve", _fail)

    status, payload = _post_json(f"{server_url}/api/bloomberg/option-discount-curve", {})

    assert status == 502
    assert "Bloomberg DAPI par rate request failed" in payload["error"]
    assert "nodes" not in payload


def test_api_option_discount_curve_never_falls_back_to_sample_data(
    server_url: str, monkeypatch
) -> None:
    calls = []

    def _fail():
        calls.append(1)
        raise BLIBloombergDapiError("boom")

    def _must_not_price(*args, **kwargs):
        raise AssertionError("the curve route must never touch pricing")

    monkeypatch.setattr(server_module, "load_bloomberg_usd_sofr_option_discount_curve", _fail)
    monkeypatch.setattr(server_module, "price_standalone_option_case", _must_not_price)

    status, payload = _post_json(f"{server_url}/api/bloomberg/option-discount-curve", {})

    assert status == 502
    assert "nodes" not in payload
    assert calls == [1]


def test_api_option_discount_curve_reuses_the_production_loaders_unmodified(
    server_url: str,
) -> None:
    # No monkeypatching of either loader here -- proves the route imports
    # and calls the exact same function names Issue #165/#166 and #168
    # already shipped, not private copies. blpapi is not installed in CI, so
    # this fails closed with BLIBloombergDapiError rather than succeeding or
    # hanging.
    status, payload = _post_json(f"{server_url}/api/bloomberg/option-discount-curve", {})
    assert status == 502
    assert "blpapi" in payload["error"]


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


def test_validate_case_reports_ready_for_the_live_workbench_flows_empty_curve_points() -> None:
    """Issue #171: an empty curve_points is the expected shape of a
    live-Bloomberg-pending draft -- it must not itself make the Price gate
    stay closed, and this makes no Bloomberg call to prove it (no loader is
    monkeypatched here at all)."""

    case = {**load_base_case(), "curve_points": []}
    assert server_module.validate_case(case) == {"ready": True, "error": None}
    # The caller's own mapping is never mutated by the placeholder swap.
    assert case["curve_points"] == []


def test_validate_case_still_catches_an_unrelated_defect_with_empty_curve_points() -> None:
    """The placeholder swap must not mask a real, unrelated problem."""

    case = {**load_base_case(), "curve_points": [], "bond_option": {}}
    result = server_module.validate_case(case)
    assert result["ready"] is False
    assert "curve_points" not in result["error"]


@pytest.mark.parametrize("malformed_curve_points", [None, "", {}, 0, "not-a-list"])
def test_validate_case_never_substitutes_the_placeholder_for_a_malformed_value(
    malformed_curve_points,
) -> None:
    """Codex P2 review of PR #172: only an actual empty list gets the
    validation-only placeholder -- a malformed non-list value must still be
    reported by the real ``curve_points must be a JSON array`` schema error."""

    case = {**load_base_case(), "curve_points": malformed_curve_points}
    result = server_module.validate_case(case)
    assert result["ready"] is False
    assert "curve_points must be a JSON array" in result["error"]


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
def test_api_advanced_profile_source_system_is_the_pre_existing_ust_export_value(
    server_url: str,
) -> None:
    """Issue #161 follow-up item 6, compatibility correction, pinned at the
    HTTP boundary.

    UST's ``source_system`` in the run export is
    ``SHIORI_UST_FIXED_COUPON_PROFILE`` -- the value PR #162 shipped and real
    Bloomberg workstation UAT already passed against. Registering
    ``US_CORPORATE`` and ``GERMAN_GOVT`` alongside it must not move it, and
    each of those two carries its own distinct label rather than a value
    derived from a shared naming pattern.
    """

    status, payload = _post_json(f"{server_url}/api/bond/advanced-profile", _PROFILE_BODY)

    assert status == 200
    assert payload["source_system"] == "SHIORI_UST_FIXED_COUPON_PROFILE"


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
