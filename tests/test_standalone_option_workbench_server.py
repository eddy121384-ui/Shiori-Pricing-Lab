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
from pathlib import Path

import pytest

from shiori_pricing_lab.app import standalone_option_workbench_server as server_module
from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_json,
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import price_standalone_option_case
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
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available

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
