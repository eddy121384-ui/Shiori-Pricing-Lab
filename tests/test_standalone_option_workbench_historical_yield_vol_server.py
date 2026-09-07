"""The workbench bridge's read-only Historical Yield Vol route (Issue #197).

``POST /api/bloomberg/historical-yield-vol`` is what the Markets -> Bond
Yield History view's Historical Yield Vol card calls. Every test here drives
the real ``ThreadingHTTPServer`` over loopback with the Issue #196 loader
replaced by a stand-in, so no Bloomberg session is ever opened and every
value below is made up.

The things this file exists to hold down: the route reuses the existing
ISIN/CUSIP identity path and the one canonical #196 loader (it never opens a
second historical-data path), it computes nothing of its own, it defaults to
Middle Office's confirmed 180-observation window without deriving one from an
expiry or a tenor, it publishes the result only as the normalized
``HISTORICAL_YIELD_VOL_MO`` source, and it reports insufficient/zero history
honestly rather than substituting anything.
"""

from __future__ import annotations

import json
import math
import re
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta

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
_ROUTE = "/api/bloomberg/historical-yield-vol"
_START = date(2026, 1, 1)


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


def _history(values, *, field_unit=None) -> BloombergBondYieldHistory:
    observations = tuple(
        BondYieldObservation(
            observation_date=_START + timedelta(days=index),
            yield_value=value,
            raw_value=None if value is None else repr(value),
        )
        for index, value in enumerate(values)
    )
    return BloombergBondYieldHistory(
        requested_identifier=f"/isin/{_ISIN}",
        security="SYNTHETIC TEST Corp",
        yield_field=_FIELD,
        field_meaning=None,
        field_unit=field_unit,
        requested_start_date=_START,
        requested_end_date=date(2026, 12, 31),
        observations=observations,
        source_system="BLOOMBERG_DAPI",
        acquired_at="2026-08-31T14:05:00+00:00",
    )


def _stub_loader(monkeypatch, *, history=None, raises=None):
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        answer = history if history is not None else _history([4.00, 4.10, 3.80, 4.30])
        # The real #196 loader carries field_meaning/field_unit through
        # verbatim onto its result; the stand-in does the same so a test can
        # exercise the unit-dependent paths through the route's own body.
        return replace(
            answer,
            field_meaning=kwargs.get("field_meaning", answer.field_meaning),
            field_unit=kwargs.get("field_unit", answer.field_unit),
        )

    monkeypatch.setattr(server_module, "load_bloomberg_bond_yield_history", _fake)
    return calls


def _body(**overrides) -> dict:
    body = {
        "bond_identifier": _ISIN,
        "yield_field": _FIELD,
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "requested_observation_count": 4,
    }
    body.update(overrides)
    return body


# --- one acquisition path, one calculation path ------------------------------


def test_reuses_the_existing_isin_identity_path_and_the_196_loader(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    assert len(calls) == 1
    assert calls[0]["identifier"] == f"/isin/{_ISIN}"
    assert calls[0]["yield_field"] == _FIELD
    assert calls[0]["start_date"] == "2026-01-01"
    assert calls[0]["end_date"] == "2026-12-31"
    assert payload["methodology"] == "HISTORICAL_YIELD_VOL_MO"


def test_reuses_the_existing_cusip_identity_path(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, _ = _post_json(f"{server_url}{_ROUTE}", _body(bond_identifier="912828XX0"))

    assert status == 200
    assert calls[0]["identifier"] == "/cusip/912828XX0"


def test_never_supplies_a_yield_field_of_its_own(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch)

    status, payload = _post_json(
        f"{server_url}{_ROUTE}", {k: v for k, v in _body().items() if k != "yield_field"}
    )

    assert status == 400
    assert "yield_field" in payload["error"]


def test_optional_unit_provenance_is_passed_through_untouched(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(field_unit="PERCENT"))

    assert status == 200
    assert calls[0]["field_unit"] == "PERCENT"
    assert payload["field_unit"] == "PERCENT"


def test_no_unit_is_supplied_when_the_trader_states_none(server_url, monkeypatch) -> None:
    calls = _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    assert "field_unit" not in calls[0]
    assert payload["field_unit"] is None


# --- the calculation contract -------------------------------------------------


def test_the_default_window_is_middle_offices_confirmed_180(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, history=_history([4.0 + (index % 7) * 0.01 for index in range(200)]))

    body = {k: v for k, v in _body().items() if k != "requested_observation_count"}
    status, payload = _post_json(f"{server_url}{_ROUTE}", body)

    assert status == 200
    assert payload["requested_observation_count"] == 180
    assert payload["observation_count"] == 180
    assert payload["yield_change_count"] == 179
    assert payload["window_status"] == "FULL_WINDOW"


def test_the_payload_carries_the_full_audit_trail(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, history=_history([4.00, 4.10, 3.80, 4.30]))

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(field_unit="PERCENT"))

    assert status == 200
    assert payload["security"] == "SYNTHETIC TEST Corp"
    assert payload["yield_field"] == _FIELD
    assert payload["source_system"] == "BLOOMBERG_DAPI"
    assert payload["acquired_at"] == "2026-08-31T14:05:00+00:00"
    assert payload["calculated_at"]
    assert payload["requested_start_date"] == "2026-01-01"
    assert payload["requested_end_date"] == "2026-12-31"
    assert payload["series_observation_count"] == 4
    assert payload["observation_count"] == 4
    assert payload["yield_change_count"] == 3
    assert payload["observation_dates"] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
        "2026-01-04",
    ]
    assert payload["first_observation_date"] == "2026-01-01"
    assert payload["last_observation_date"] == "2026-01-04"
    assert payload["standard_deviation_convention"] == "SAMPLE_STDEV_S_DDOF_1"
    assert payload["annualization_trading_days"] == 252
    assert payload["annualization_factor"] == pytest.approx(math.sqrt(252))
    assert payload["warnings"] == []
    assert payload["daily_yield_vol"] == pytest.approx(0.4, abs=1e-12)
    assert payload["annualized_yield_vol"] == pytest.approx(0.4 * math.sqrt(252), abs=1e-12)
    assert payload["blockers"] == []


def test_the_text_figures_are_the_exact_digits_the_calculator_produced(
    server_url, monkeypatch
) -> None:
    _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    # The browser prints these strings verbatim, so they must round-trip to the
    # same double the calculator returned -- no rounding happens on the way out.
    assert float(payload["daily_yield_vol_text"]) == payload["daily_yield_vol"]
    assert float(payload["annualized_yield_vol_text"]) == payload["annualized_yield_vol"]


# --- the normalized volatility source ----------------------------------------


def test_a_full_window_with_a_unit_publishes_the_historical_yield_vol_mo_source(
    server_url, monkeypatch
) -> None:
    _stub_loader(monkeypatch, history=_history([4.00, 4.10, 3.80, 4.30]))

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(field_unit="PERCENT"))

    assert status == 200
    source = payload["volatility_source"]
    assert source["source_system"] == "HISTORICAL_YIELD_VOL_MO"
    assert source["volatility_basis"] == "YIELD_VOL"
    assert source["status"] == "ACTIVE"
    # Normalized to the unit BLIVolatilityInput states, never the raw
    # percentage-point figure the headline shows.
    assert source["volatility_unit"] == "DECIMAL_ANNUAL"
    assert source["source_unit"] == "PERCENT"
    assert source["normalization_factor"] == 1e-2
    assert source["volatility"] == pytest.approx(payload["annualized_yield_vol"] / 100, rel=1e-12)
    assert float(source["volatility_text"]) == source["volatility"]
    assert "PERCENT" in source["override_or_fallback_audit"]
    assert "DECIMAL_ANNUAL" in source["override_or_fallback_audit"]
    assert payload["volatility_source_unavailable_reason"] is None


def test_an_unconfirmed_unit_reports_why_no_source_was_published(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    assert payload["volatility_source"] is None
    assert "unit was not established" in payload["volatility_source_unavailable_reason"]
    # The calculation itself still stands and is still shown, in the field's
    # own unit -- only the normalized publication is withheld.
    assert payload["annualized_yield_vol"] is not None


# --- insufficient / zero history ---------------------------------------------


def test_short_history_is_insufficient_with_both_counts(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, history=_history([4.0 + (index % 5) * 0.02 for index in range(90)]))

    status, payload = _post_json(
        f"{server_url}{_ROUTE}", _body(requested_observation_count=180, field_unit="PERCENT")
    )

    assert status == 200
    assert payload["window_status"] == "INSUFFICIENT_HISTORY"
    assert payload["requested_observation_count"] == 180
    assert payload["observation_count"] == 90
    # A short but usable window warns; it does not block.
    assert payload["blockers"] == []
    assert any("INSUFFICIENT_HISTORY" in warning for warning in payload["warnings"])
    # Publishable, but never silently: the audit states both counts, and the
    # published value is still normalized to the contract's own unit.
    source = payload["volatility_source"]
    assert "90 of the requested 180" in source["override_or_fallback_audit"]
    assert source["volatility"] == pytest.approx(payload["annualized_yield_vol"] / 100, rel=1e-12)


def test_a_unit_outside_the_vocabulary_refuses_publication_but_not_calculation(
    server_url, monkeypatch
) -> None:
    _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(field_unit="PERCENTAGE POINTS"))

    assert status == 200
    assert payload["annualized_yield_vol"] is not None
    assert payload["field_unit"] == "PERCENTAGE POINTS"
    assert payload["volatility_source"] is None
    assert "no approved normalization" in payload["volatility_source_unavailable_reason"]


def test_zero_history_is_a_blocking_answer_not_a_proxy(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, history=_history([]))

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(requested_observation_count=180))

    assert status == 200
    assert payload["window_status"] == "NO_HISTORY"
    assert payload["observation_count"] == 0
    assert payload["daily_yield_vol"] is None
    assert payload["annualized_yield_vol"] is None
    assert payload["daily_yield_vol_text"] is None
    assert payload["volatility_source"] is None
    assert "no approved proxy" in payload["blockers"][0]
    assert payload["warnings"] == []


# --- fail closed --------------------------------------------------------------


def test_a_row_with_no_value_inside_the_window_is_refused(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, history=_history([4.00, None, 3.80, 4.30]))

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 400
    assert "no Yield value" in payload["error"]


@pytest.mark.parametrize("bad", [0, 2, -5, "180", 180.5, None])
def test_an_unusable_observation_contract_is_refused(server_url, monkeypatch, bad) -> None:
    calls = _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(requested_observation_count=bad))

    assert status == 400
    assert "requested_observation_count" in payload["error"]
    # Refused before the Bloomberg round trip, not after it.
    assert calls == []


def test_an_overflowing_window_is_400_not_500(server_url, monkeypatch) -> None:
    # Before the fix this pair raised AttributeError out of statistics.stdev
    # and the route answered HTTP 500 -- an unhandled crash where the honest
    # answer is a refusal the trader can read (Codex review, PR #200).
    _stub_loader(monkeypatch, history=_history([1e308, -1e308, 1e308]))

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(requested_observation_count=3))

    assert status == 400
    assert "not a finite number" in payload["error"]


def test_an_overflowing_annualization_is_refused_not_returned(server_url, monkeypatch) -> None:
    # And this one returned `"annualized_yield_vol": Infinity` with no blocker
    # -- invalid JSON for a strict parser, and a usable-looking risk figure.
    _stub_loader(monkeypatch, history=_history([0.0, 1e308, 0.0]))

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body(requested_observation_count=3))

    assert status == 400
    assert "annualized Historical Yield Vol" in payload["error"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # statistics.stdev raises OverflowError converting its exact result
        # back to a float -- not ValueError, so this used to be an HTTP 500.
        ([0.0, sys.float_info.max, 0.0], "cannot be represented as a finite number"),
        # A strictly positive sigma that rounds to zero: a false zero risk
        # figure if returned, so the route refuses instead.
        ([0.0, 5e-324, 5e-324, 5e-324, 5e-324], "underflows to zero"),
    ],
)
def test_a_statistic_that_cannot_be_represented_is_400_not_500(
    server_url, monkeypatch, values, expected
) -> None:
    _stub_loader(monkeypatch, history=_history(values))

    status, payload = _post_json(
        f"{server_url}{_ROUTE}", _body(requested_observation_count=len(values))
    )

    assert status == 400
    assert expected in payload["error"]


def test_a_bloomberg_side_failure_is_502(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch, raises=BLIBloombergDapiError("synthetic DAPI failure"))

    status, payload = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 502
    assert payload["error"] == "synthetic DAPI failure"


def test_a_malformed_body_is_400(server_url, monkeypatch) -> None:
    _stub_loader(monkeypatch)

    status, payload = _post_json(f"{server_url}{_ROUTE}", ["not", "an", "object"])

    assert status == 400
    assert "JSON object" in payload["error"]


def test_an_unparseable_body_is_400(server_url) -> None:
    request = urllib.request.Request(
        f"{server_url}{_ROUTE}",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            status, payload = response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, json.loads(exc.read())

    assert status == 400
    assert "invalid JSON body" in payload["error"]


# --- no side effects ----------------------------------------------------------


def test_the_route_never_touches_the_196_route_or_any_pricing_route(
    server_url, monkeypatch
) -> None:
    # One loader call, and no pricing/VCUB/Forward entry point is reachable
    # from this route: the calculator is the only other thing it calls.
    calls = _stub_loader(monkeypatch)

    priced: list[object] = []
    monkeypatch.setattr(
        server_module,
        "price_standalone_option_case",
        lambda *args, **kwargs: priced.append(args) or {},
    )

    status, _ = _post_json(f"{server_url}{_ROUTE}", _body())

    assert status == 200
    assert len(calls) == 1
    assert priced == []


def test_the_api_contract_id_names_this_route(server_url) -> None:
    with urllib.request.urlopen(f"{server_url}/api/health") as response:
        health = json.loads(response.read())

    assert health["api_contract"].endswith("-v28")


def test_the_view_script_is_served(server_url) -> None:
    with urllib.request.urlopen(f"{server_url}/historical_yield_vol_view.js") as response:
        body = response.read().decode("utf-8")

    assert "api/bloomberg/historical-yield-vol" in body


def _normalized(markup: str) -> str:
    return " ".join(markup.split())


def _served_block(page: str, pattern: str) -> str:
    """Return one served block's inner markup with whitespace normalized."""

    found = re.search(pattern, page, re.S)
    assert found is not None, f"the served page has no block matching {pattern!r}"
    return _normalized(found.group(1))


# The exact provenance copy the Historical Yield Vol card serves. Pinned whole,
# not by keyword: three of Codex's findings on PR #200 were served strings
# asserting something the code contradicts, and a substring check cannot catch a
# contradiction ADDED beside the sentences it looks for. Any edit to either block
# fails this test on purpose -- these two paragraphs are what tell a trader where
# the number came from, so changing them is a deliberate act with a review
# attached, not a wording tweak.
_EXPECTED_CARD_INTRO = """
    A Middle Office-style historical volatility of <em>this bond's own</em> Yield, calculated
    server-side: daily Yield <em>Change</em>, sample standard deviation, annualized by
    &radic;252. It does <strong>not</strong> read the series displayed above &mdash; it sends
    the same query to Bloomberg again and calculates from that answer, so its observations are
    its own and its <span class="mono">Acquired at</span> below is the one that governs. If
    Bloomberg&rsquo;s answer changed between the two requests, the two panels are two
    acquisitions, not one. This is also <strong>not</strong> Bloomberg implied vol and not a
    VCUB number, and it is not wired into pricing &mdash; it is a Yield Vol, and no approved
    Yield-Vol&nbsp;&rarr;&nbsp;Price-Vol conversion exists in this build.
"""

_EXPECTED_CARD_NOTE = """
    Sends the bond identifier, Yield field and date range entered above as a fresh Bloomberg
    request, then uses the most recent <span class="mono">N</span> observations <em>that</em>
    request returned. 180 is Middle Office's confirmed 6M-style window; it is not derived from
    an expiry or a tenor, so change it only against evidence. The unit is never inferred from
    how large the numbers look. It is optional for the calculation &mdash; without it the
    volatility is still calculated and shown in the field&rsquo;s own unit &mdash; but
    publishing to the normalized volatility source needs a declared <span
    class="mono">DECIMAL</span>, <span class="mono">PERCENT</span> or <span
    class="mono">BASIS_POINTS</span>, because that contract carries a decimal annual volatility
    and nothing else may enter it unscaled.
"""


def test_the_cards_provenance_copy_is_exactly_what_was_reviewed(server_url) -> None:
    """The served copy must match what the route actually does, in full.

    The route performs its own Bloomberg acquisition rather than consuming the
    observations the #196 view is displaying, so the two panels can disagree if
    Bloomberg's answer changed between the two requests. Copy that told a trader
    the vol was computed "from the same series above" misrepresented the result's
    provenance (Codex review, PR #200).

    Both blocks are compared whole rather than probed for phrases, so a
    contradictory sentence added *beside* the honest ones fails here too -- which
    a substring check would have let through (Codex review, PR #200, again).
    """

    with urllib.request.urlopen(f"{server_url}/index.html") as response:
        page = response.read().decode("utf-8")

    assert _served_block(page, r'<div class="hyv-intro">(.*?)</div>') == _normalized(
        _EXPECTED_CARD_INTRO
    )
    notes = re.findall(r'<div class="byh-field-note">(.*?)</div>', page, re.S)
    assert _normalized(notes[1]) == _normalized(_EXPECTED_CARD_NOTE)
