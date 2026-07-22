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

import pytest

from shiori_pricing_lab.app.standalone_option_workbench import price_standalone_option_case
from shiori_pricing_lab.app.standalone_option_workbench_overlay import (
    apply_standalone_option_case_overlay,
    extract_standalone_option_case_overlay,
)
from shiori_pricing_lab.app.standalone_option_workbench_server import (
    PROTOTYPE_DIR,
    create_server,
    load_base_case,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available

_QUANTLIB_SKIP = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)


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
