"""The workbench bridge's read-only Bond Yield History route (Issue #196).

``POST /api/bloomberg/bond-yield-history`` is what Markets -> Bond Yield
History reads. Every test here drives the real ``ThreadingHTTPServer`` over
loopback with the production loader replaced by a stand-in, so no Bloomberg
session is ever opened and every value below is made up.

The things this file exists to hold down: the route reuses the existing
ISIN/CUSIP identity path, passes the trader's own field and date range
through untouched, never supplies a Yield field of its own, hands back
exactly the observations the loader returned (holes included), and computes
no statistic of any kind.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import date

import pytest

import shiori_pricing_lab.app.standalone_option_workbench_server as server_module
from shiori_pricing_lab.app.standalone_option_workbench_server import create_server
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (
    BloombergBondYieldHistory,
    BondYieldObservation,
)

_ISIN = "US0000000000"
_FIELD = "SYNTHETIC_TEST_YIELD_FIELD"
_ROUTE = "/api/bloomberg/bond-yield-history"


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


def _post_json(url: str, payload: object) -> tuple[int, dict]:
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


def _history(observations=()) -> BloombergBondYieldHistory:
    return BloombergBondYieldHistory(
        requested_identifier=f"/isin/{_ISIN}",
        security="SYNTHETIC TEST Corp",
        yield_field=_FIELD,
        field_meaning=None,
        field_unit=None,
        requested_start_date=date(2026, 1, 1),
        requested_end_date=date(2026, 1, 31),
        observations=tuple(observations),
        source_system="BLOOMBERG_DAPI",
        acquired_at="2026-08-31T14:05:00+00:00",
    )


def _observation(iso_date: str, raw: str | None) -> BondYieldObservation:
    return BondYieldObservation(
        observation_date=date.fromisoformat(iso_date),
        yield_value=None if raw is None else float(raw),
        raw_value=raw,
    )


def _stub_loader(monkeypatch, *, history=None, raises=None):
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return history if history is not None else _history()

    monkeypatch.setattr(server_module, "load_bloomberg_bond_yield_history", _fake)
    return calls


def _body(**overrides) -> dict:
    body = {
        "bond_identifier": _ISIN,
        "yield_field": _FIELD,
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
    }
    body.update(overrides)
    return body


# --- what the route asks the loader for ---------------------------------------


def test_reuses_the_existing_isin_identity_path(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, _ = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    assert calls[0]["identifier"] == f"/isin/{_ISIN}"


def test_reuses_the_existing_cusip_identity_path(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, _ = _post_json(f"{server_url}{_ROUTE}", _body(bond_identifier="912828XX0"))

    assert status == 200
    assert calls[0]["identifier"] == "/cusip/912828XX0"


def test_passes_the_traders_field_and_range_through_untouched(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    _post_json(
        f"{server_url}{_ROUTE}",
        _body(yield_field="ANOTHER_TEST_FIELD", start_date="2025-03-04", end_date="2025-09-09"),
    )

    assert calls[0]["yield_field"] == "ANOTHER_TEST_FIELD"
    assert calls[0]["start_date"] == "2025-03-04"
    assert calls[0]["end_date"] == "2025-09-09"


def test_the_bridge_never_supplies_a_yield_field_of_its_own(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, body = _post_json(
        f"{server_url}{_ROUTE}",
        {"bond_identifier": _ISIN, "start_date": "2026-01-01", "end_date": "2026-01-31"},
    )

    assert status == 400
    assert "yield_field" in body["error"]
    assert calls == []


# --- what the route hands back ------------------------------------------------


def test_returns_every_observation_exactly_as_the_loader_returned_it(
    server_url, monkeypatch
) -> None:
    _stub_loader(
        monkeypatch,
        history=_history(
            [
                _observation("2026-01-06", "4.1200000"),
                _observation("2026-01-07", None),
                _observation("2026-01-09", "4.4"),
            ]
        ),
    )

    status, body = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    assert body["observations"] == [
        {"date": "2026-01-06", "yield_value": 4.12, "raw_value": "4.1200000"},
        {"date": "2026-01-07", "yield_value": None, "raw_value": None},
        {"date": "2026-01-09", "yield_value": 4.4, "raw_value": "4.4"},
    ]


def test_a_gap_is_never_filled_in_by_the_bridge(server_url, monkeypatch) -> None:
    _stub_loader(
        monkeypatch,
        history=_history([_observation("2026-01-06", "4.0"), _observation("2026-01-20", "4.5")]),
    )

    _, body = _post_json(f"{server_url}{_ROUTE}", _body())

    assert [row["date"] for row in body["observations"]] == ["2026-01-06", "2026-01-20"]
    assert body["observation_count"] == 2


def test_returns_the_provenance_the_view_displays(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, history=_history([_observation("2026-01-06", "4.0")]))

    _, body = _post_json(f"{server_url}{_ROUTE}", _body())

    assert body["requested_identifier"] == f"/isin/{_ISIN}"
    assert body["security"] == "SYNTHETIC TEST Corp"
    assert body["yield_field"] == _FIELD
    assert body["field_meaning"] is None
    assert body["field_unit"] is None
    assert body["requested_start_date"] == "2026-01-01"
    assert body["requested_end_date"] == "2026-01-31"
    assert body["source_system"] == "BLOOMBERG_DAPI"
    assert body["acquired_at"] == "2026-08-31T14:05:00+00:00"
    assert body["first_observation_date"] == "2026-01-06"
    assert body["last_observation_date"] == "2026-01-06"


def test_an_empty_series_is_a_normal_answer_not_a_synthetic_one(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, history=_history([]))

    status, body = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    assert body["observations"] == []
    assert body["observation_count"] == 0
    assert body["first_observation_date"] is None
    assert body["last_observation_date"] is None


def test_the_response_carries_no_statistic_of_any_kind(server_url, monkeypatch) -> None:
    _stub_loader(
        monkeypatch,
        history=_history([_observation("2026-01-06", "4.0"), _observation("2026-01-07", "4.25")]),
    )

    _, body = _post_json(f"{server_url}{_ROUTE}", _body())

    forbidden = ("change", "vol", "volatility", "stdev", "std_dev", "annual", "sigma")
    assert not [key for key in body if any(word in key.lower() for word in forbidden)]
    for row in body["observations"]:
        assert set(row) == {"date", "yield_value", "raw_value"}


# --- failure is visible -------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key", ["bond_identifier", "yield_field", "start_date", "end_date"]
)
def test_a_missing_required_key_is_refused(server_url, monkeypatch, missing_key) -> None:
    calls = _stub_loader(monkeypatch)
    body = _body()
    del body[missing_key]

    status, response = _post_json(f"{server_url}{_ROUTE}", body)

    assert status == 400
    assert missing_key in response["error"]
    assert calls == []


def test_a_malformed_identifier_is_refused_before_bloomberg(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, body = _post_json(f"{server_url}{_ROUTE}", _body(bond_identifier="US912828 Govt"))

    assert status == 400
    assert "ISIN" in body["error"]
    assert calls == []


def test_a_non_object_body_is_refused(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, body = _post_json(f"{server_url}{_ROUTE}", ["not", "an", "object"])

    assert status == 400
    assert "JSON object" in body["error"]
    assert calls == []


def test_invalid_json_is_refused(server_url) -> None:
    request = urllib.request.Request(
        f"{server_url}{_ROUTE}",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:  # pragma: no cover - defensive
            raise AssertionError(f"expected HTTP 400, got {response.status}")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert "invalid JSON body" in json.loads(exc.read())["error"]


def test_a_bloomberg_failure_is_reported_verbatim_never_repaired(
    server_url, monkeypatch
) -> None:
    _stub_loader(
        monkeypatch,
        raises=BLIBloombergDapiError("Bloomberg DAPI field exception for BAD_FLD"),
    )

    status, body = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 502
    assert body["error"] == "Bloomberg DAPI field exception for BAD_FLD"
    assert "observations" not in body


def test_a_loader_value_error_is_a_client_error(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, raises=ValueError("start_date must not be after end_date"))

    status, body = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 400
    assert "start_date" in body["error"]
