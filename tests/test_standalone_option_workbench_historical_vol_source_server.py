"""``HISTORICAL_YIELD_VOL_MO`` as a live pricing source (Issue #214).

Drives the real ``ThreadingHTTPServer`` over loopback with the Issue #196
Yield-series loader replaced by a stand-in, so no Bloomberg session is ever
opened and every value below is made up. The bond is the repository's own
synthetic case.

What this file holds down:

* the derived volatility reaches Black-76 as ``EQUIVALENT_PRICE_VOL`` and
  never as ``YIELD_VOL``, which the pricing guard still refuses;
* the source is only ever used when the case explicitly names it -- a
  missing or unusable Historical result never falls back to another source,
  and no other source ever falls back to this one;
* the whole chain is re-derived on every priced run, so a stale volatility
  from a previous bond, a previous price state or the other price basis
  cannot survive into a later ticket;
* ``DIRTY`` stays the default and ``CLEAN`` is an explicit selection, and
  the two produce different, basis-consistent answers;
* the review route and the priced run compute the same number, because they
  call the same function;
* the priced run's export carries the whole lineage.
"""

from __future__ import annotations

import copy
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

import shiori_pricing_lab.app.standalone_option_historical_vol_source as source_module
import shiori_pricing_lab.app.standalone_option_workbench_server as server_module
from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench_server import create_server
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (
    BloombergBondYieldHistory,
    BondYieldObservation,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import is_quantlib_available

_requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

_BASE_CASE_PATH = Path(__file__).resolve().parents[1] / "examples" / "standalone_option_case.json"
_ISIN = "XS0000000001"
_FIELD = "SYNTHETIC_TEST_YIELD_FIELD"
_PRICE_ROUTE = "/api/case"
_REVIEW_ROUTE = "/api/pricing/historical-equivalent-price-vol"
_VALIDATE_ROUTE = "/api/case/validate"

# Four made-up Yield readings. Deliberately not anyone's real series, and
# deliberately not a round number when annualized.
_YIELD_VALUES = (4.00, 4.10, 3.80, 4.30)
_WINDOW_START = date(2026, 1, 1)


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


def _history(identifier: str, values=_YIELD_VALUES, *, field_unit=None, security=None):
    return BloombergBondYieldHistory(
        requested_identifier=identifier,
        security=security or identifier,
        yield_field=_FIELD,
        field_meaning=None,
        field_unit=field_unit,
        requested_start_date=_WINDOW_START,
        requested_end_date=date(2026, 6, 30),
        observations=tuple(
            BondYieldObservation(
                observation_date=_WINDOW_START + timedelta(days=index),
                yield_value=value,
                raw_value=None if value is None else repr(value),
            )
            for index, value in enumerate(values)
        ),
        source_system="BLOOMBERG_DAPI",
        acquired_at="2026-07-01T09:00:00+00:00",
    )


def _stub_yield_loader(monkeypatch, *, values=_YIELD_VALUES, raises=None, security=None):
    """Replace the one #196 loader, on the module that actually calls it."""

    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return _history(
            kwargs["identifier"],
            values,
            field_unit=kwargs.get("field_unit"),
            security=security,
        )

    monkeypatch.setattr(source_module, "load_bloomberg_bond_yield_history", _fake)
    return calls


def _no_live_curve(monkeypatch) -> None:
    """The bundled case carries explicit curve nodes, so no curve is fetched.

    Pinned as a stand-in that fails loudly rather than assumed, so a change
    that started reaching Bloomberg for a curve here would be caught instead
    of quietly opening a session.
    """

    def _fail(*_args, **_kwargs):
        raise AssertionError("no live Bloomberg curve acquisition belongs in these tests")

    monkeypatch.setattr(
        server_module, "load_bloomberg_usd_sofr_option_discount_curve", _fail
    )


def _historical_case(**overrides) -> dict:
    case = json.loads(_BASE_CASE_PATH.read_text(encoding="utf-8"))
    case["convention_profile"] = "UST"
    case["volatility_input"] = {
        "volatility": None,
        "volatility_basis": "EQUIVALENT_PRICE_VOL",
        "source_system": source_module.HISTORICAL_YIELD_VOL_SOURCE,
        "status": "ACTIVE",
        "override_or_fallback_audit": None,
    }
    case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY] = {
        "bond_identifier": _ISIN,
        "yield_field": _FIELD,
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "requested_observation_count": len(_YIELD_VALUES),
        "field_unit": "PERCENT",
    }
    case.update(overrides)
    return case


def _manual_case(**overrides) -> dict:
    case = json.loads(_BASE_CASE_PATH.read_text(encoding="utf-8"))
    case.update(overrides)
    return case


# --- The derived volatility is what prices ----------------------------------


@_requires_quantlib
def test_the_derived_equivalent_price_vol_is_what_black76_prices(
    server_url, monkeypatch
) -> None:
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    status, payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}", _historical_case()
    )

    assert status == 200
    # Exactly one Yield-series acquisition: no second historical-data path.
    assert len(calls) == 1
    assert calls[0]["identifier"] == f"/isin/{_ISIN}"
    assert calls[0]["yield_field"] == _FIELD

    display = payload["display"]
    provenance = display["historical_volatility_source"]
    assert display["status"] == "SUCCESS"
    assert display["assumptions"]["price_volatility"] == provenance["equivalent_price_vol"]
    assert display["assumptions"]["volatility_basis"] == "EQUIVALENT_PRICE_VOL"

    # The conversion, not a coincidence: |D_B| x sigma_hist_abs is the number.
    assert provenance["equivalent_price_vol"] == pytest.approx(
        provenance["absolute_modified_duration"]
        * provenance["historical_yield_vol_decimal_annual"]
    )
    assert provenance["vol_source"] == source_module.HISTORICAL_YIELD_VOL_SOURCE
    assert provenance["volatility_kind"] == "HISTORICAL_REALIZED"

    # The case echoed back is the one that priced, with the derived value on
    # the reviewed contract's own field -- so an adopting browser holds what
    # was priced and never has to retype it.
    priced_volatility_input = payload["case"]["volatility_input"]
    assert priced_volatility_input["volatility"] == provenance["equivalent_price_vol"]
    assert priced_volatility_input["volatility_basis"] == "EQUIVALENT_PRICE_VOL"
    assert priced_volatility_input["source_system"] == (
        source_module.HISTORICAL_YIELD_VOL_SOURCE
    )


@_requires_quantlib
def test_the_raw_yield_vol_never_reaches_pricing(server_url, monkeypatch) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    status, payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}", _historical_case()
    )

    assert status == 200
    provenance = payload["display"]["historical_volatility_source"]
    # The two are different quantities, and it is the converted one that
    # priced -- the yield statistic is an order of magnitude away.
    assert provenance["volatility_basis"] == "EQUIVALENT_PRICE_VOL"
    assert (
        payload["display"]["assumptions"]["price_volatility"]
        != provenance["historical_yield_vol_decimal_annual"]
    )
    assert payload["case"]["volatility_input"]["volatility_basis"] != "YIELD_VOL"


def test_a_case_declaring_yield_vol_is_still_refused_by_the_pricing_guard(
    server_url, monkeypatch
) -> None:
    _no_live_curve(monkeypatch)
    case = _manual_case()
    case["volatility_input"] = {**case["volatility_input"], "volatility_basis": "YIELD_VOL"}

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert status == 200
    assert payload["display"]["status"] == "FAILED"
    assert any("YIELD_VOL" in error["message"] for error in payload["display"]["errors"])


# --- Explicit selection, and no fallback in either direction -----------------


def test_a_case_that_does_not_name_this_source_is_untouched(server_url, monkeypatch) -> None:
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", _manual_case())

    assert status == 200
    # No Historical acquisition happened at all, and the case's own
    # trader-entered volatility is exactly what priced.
    assert calls == []
    assert "historical_volatility_source" not in payload["display"]
    assert payload["case"]["volatility_input"] == _manual_case()["volatility_input"]
    assert payload["display"]["assumptions"]["price_volatility"] == 0.18


def test_an_unusable_historical_result_blocks_the_run_rather_than_substituting(
    server_url, monkeypatch
) -> None:
    # Two observations make one Yield Change, and one change has no ddof=1
    # standard deviation. There is no number, so there is no run.
    _stub_yield_loader(monkeypatch, values=(4.00, 4.10))
    _no_live_curve(monkeypatch)

    status, payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}", _historical_case()
    )

    assert status == 400
    assert "error" in payload
    # Not a different source, not a flat vol, not the last good value.
    assert "0.18" not in payload["error"]


def test_a_bloomberg_failure_is_reported_as_one_and_prices_nothing(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(
        monkeypatch, raises=BLIBloombergDapiError("synthetic DAPI failure")
    )
    _no_live_curve(monkeypatch)

    status, payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}", _historical_case()
    )

    assert status == 400
    assert "synthetic DAPI failure" in payload["error"]


def test_a_historical_query_for_another_bond_is_refused_before_bloomberg(
    server_url, monkeypatch
) -> None:
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY] = {
        **case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY],
        "bond_identifier": "XS0000000009",
    }

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert status == 400
    assert "XS0000000009" in payload["error"]
    assert _ISIN in payload["error"]
    # Refused deterministically, without spending a Bloomberg round trip.
    assert calls == []


def test_the_yield_field_and_window_are_never_inferred(server_url, monkeypatch) -> None:
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    del case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY]["yield_field"]

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert status == 400
    assert "yield_field" in payload["error"]
    assert calls == []


def test_a_missing_convention_profile_is_refused_rather_than_defaulted(
    server_url, monkeypatch
) -> None:
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    case["convention_profile"] = None

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert status == 400
    assert "convention profile" in payload["error"]
    assert calls == []


# --- Price basis, end to end -------------------------------------------------


@_requires_quantlib
def test_dirty_is_the_default_and_clean_is_an_explicit_selection(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    _status, default_payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}", _historical_case()
    )
    _status, dirty_payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}",
        _historical_case(bond_option_price_basis="DIRTY"),
    )
    _status, clean_payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}",
        _historical_case(bond_option_price_basis="CLEAN"),
    )

    default_display = default_payload["display"]
    dirty_display = dirty_payload["display"]
    clean_display = clean_payload["display"]

    assert default_display["bond_option_price_basis"] == "DIRTY"
    assert default_display["model_fair_premium_per_100"] == (
        dirty_display["model_fair_premium_per_100"]
    )
    assert clean_display["bond_option_price_basis"] == "CLEAN"
    assert clean_display["priced_bond_option_price_basis"] == "CLEAN"
    assert clean_display["model_fair_premium_per_100"] != (
        dirty_display["model_fair_premium_per_100"]
    )


@_requires_quantlib
def test_every_leg_of_a_clean_run_is_on_the_clean_basis(server_url, monkeypatch) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    _status, payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}",
        _historical_case(bond_option_price_basis="CLEAN"),
    )

    display = payload["display"]
    assumptions = display["assumptions"]
    provenance = display["historical_volatility_source"]

    # F and K are the clean pair.
    assert assumptions["model_forward_price_per_100"] == (
        assumptions["forward_clean_price_per_100"]
    )
    assert assumptions["model_strike_price_per_100"] == (
        assumptions["strike_clean_price_per_100"]
    )
    # The duration divided by the clean price, and says so.
    assert provenance["price_basis"] == "CLEAN"
    assert provenance["duration_type"] == "MODIFIED_DURATION_CLEAN_PRICE"
    assert provenance["duration_basis_price_per_100"] == (
        provenance["duration_clean_price_per_100"]
    )
    # And sigma_P is the product of that duration.
    assert assumptions["price_volatility"] == pytest.approx(
        provenance["absolute_modified_duration"]
        * provenance["historical_yield_vol_decimal_annual"]
    )


@_requires_quantlib
def test_every_leg_of_a_dirty_run_is_on_the_dirty_basis(server_url, monkeypatch) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    _status, payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}",
        _historical_case(bond_option_price_basis="DIRTY"),
    )

    assumptions = payload["display"]["assumptions"]
    provenance = payload["display"]["historical_volatility_source"]

    assert assumptions["model_forward_price_per_100"] == (
        assumptions["forward_dirty_price_per_100"]
    )
    assert assumptions["model_strike_price_per_100"] == (
        assumptions["strike_dirty_price_per_100"]
    )
    assert provenance["duration_type"] == "MODIFIED_DURATION_DIRTY_PRICE"
    assert provenance["duration_basis_price_per_100"] == (
        provenance["duration_dirty_price_per_100"]
    )


@_requires_quantlib
def test_changing_the_basis_cannot_keep_the_previous_basis_volatility(
    server_url, monkeypatch
) -> None:
    # Nothing is cached, so this is structural rather than a cache-invalidation
    # rule: a case carrying the DIRTY-derived number and then asking for CLEAN
    # prices the CLEAN derivation, and the stale number never appears.
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    _status, dirty_payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}",
        _historical_case(bond_option_price_basis="DIRTY"),
    )
    stale_case = copy.deepcopy(dirty_payload["case"])
    stale_case["bond_option_price_basis"] = "CLEAN"
    dirty_volatility = dirty_payload["case"]["volatility_input"]["volatility"]

    _status, clean_payload = _post_json(f"{server_url}{_PRICE_ROUTE}", stale_case)

    priced_volatility = clean_payload["display"]["assumptions"]["price_volatility"]
    assert priced_volatility != dirty_volatility
    assert clean_payload["display"]["historical_volatility_source"]["price_basis"] == "CLEAN"


@_requires_quantlib
def test_a_second_bonds_run_cannot_reuse_the_first_bonds_volatility(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    _status, first = _post_json(
        f"{server_url}{_PRICE_ROUTE}", _historical_case()
    )
    first_volatility = first["case"]["volatility_input"]["volatility"]

    # The same envelope, carrying the first run's derived number, but the
    # Historical query now names a different bond than the ticket.
    second_case = copy.deepcopy(first["case"])
    second_case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY] = {
        **second_case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY],
        "bond_identifier": "XS0000000009",
    }

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", second_case)

    assert status == 400
    assert str(first_volatility) not in payload["error"]


@_requires_quantlib
def test_a_changed_clean_price_changes_the_volatility_that_prices(
    server_url, monkeypatch
) -> None:
    # D_B is a function of the bond's own current price, so a re-quoted bond
    # cannot be priced against the volatility derived at the previous price.
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    _status, first = _post_json(f"{server_url}{_PRICE_ROUTE}", _historical_case())
    moved = copy.deepcopy(first["case"])
    moved["bond_quote"] = {**moved["bond_quote"], "clean_price_per_100": 97.5}

    _status, second = _post_json(f"{server_url}{_PRICE_ROUTE}", moved)

    assert (
        second["display"]["assumptions"]["price_volatility"]
        != first["display"]["assumptions"]["price_volatility"]
    )


@_requires_quantlib
def test_the_explicit_case_overlay_route_re_derives_too(server_url, monkeypatch) -> None:
    # POST /api/case/price is a reachable pricing endpoint, so it must
    # re-derive like the others -- otherwise a case declaring this source
    # would be priced from whatever number its envelope carried, under a label
    # saying Shiori derived it.
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    stale = {**case, "volatility_input": {**case["volatility_input"], "volatility": 0.99}}
    overlay = {
        "option_type": case["bond_option"]["option_type"],
        "position": case["bond_option"]["position"],
        "strike_price": case["bond_option"]["strike_price"],
        "notional": case["bond_option"]["notional"],
        "volatility": 0.99,
        "forward_clean_price_per_100": (
            case["forward_clean_price_input"]["forward_clean_price_per_100"]
        ),
    }

    status, display = _post_json(
        f"{server_url}/api/case/price", {"case": stale, "overlay": overlay}
    )

    assert status == 200
    assert len(calls) == 1
    provenance = display["historical_volatility_source"]
    assert display["assumptions"]["price_volatility"] == provenance["equivalent_price_vol"]
    assert display["assumptions"]["price_volatility"] != 0.99


# --- The review route is the same derivation ---------------------------------


@_requires_quantlib
def test_the_review_route_shows_exactly_what_a_priced_run_would_use(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case(bond_option_price_basis="CLEAN")

    review_status, review = _post_json(f"{server_url}{_REVIEW_ROUTE}", {"case": case})
    price_status, priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert review_status == 200
    assert price_status == 200
    reviewed = review["historical_volatility_source"]
    assert reviewed["equivalent_price_vol"] == (
        priced["display"]["assumptions"]["price_volatility"]
    )
    assert reviewed["price_basis"] == "CLEAN"
    # A review prices nothing and writes nothing.
    assert "display" not in review
    assert "case" not in review


def test_the_review_route_reports_the_real_reason_for_a_refusal(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(monkeypatch, values=(4.00, 4.10))
    _no_live_curve(monkeypatch)

    status, payload = _post_json(
        f"{server_url}{_REVIEW_ROUTE}", {"case": _historical_case()}
    )

    assert status == 400
    assert payload["error"].strip() != ""


# --- Readiness answers the same question Price will --------------------------


def test_readiness_does_not_fail_a_historical_case_for_the_number_it_discards(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    status, payload = _post_json(
        f"{server_url}{_VALIDATE_ROUTE}", _historical_case()
    )

    assert status == 200
    assert payload == {"ready": True, "error": None}


def test_readiness_reports_a_historical_cases_own_offline_precondition(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    del case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY]

    status, payload = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)

    assert status == 200
    assert payload["ready"] is False
    assert source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY in payload["error"]


# --- Export carries the lineage ----------------------------------------------


@_requires_quantlib
def test_the_exported_run_preserves_the_whole_historical_lineage(
    server_url, monkeypatch
) -> None:
    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)

    _status, payload = _post_json(
        f"{server_url}{_PRICE_ROUTE}", _historical_case()
    )
    display = payload["display"]

    json_status, json_payload = _post_json(
        f"{server_url}/api/export/json", {"display": display}
    )
    markdown_status, markdown_payload = _post_json(
        f"{server_url}/api/export/markdown", {"display": display}
    )

    assert json_status == 200
    assert markdown_status == 200
    exported = json.loads(json_payload["content"])["historical_volatility_source"]
    assert exported == display["historical_volatility_source"]

    text = markdown_payload["content"]
    assert text == render_standalone_run_as_markdown(display)
    assert "## Historical Volatility Source" in text
    assert "HISTORICAL_YIELD_VOL_MO" in text
    assert "HISTORICAL_REALIZED" in text
    assert "not a current market-implied volatility" in text
    for label in (
        "Modified duration |D_B|",
        "Equivalent Price Vol (sigma_P)",
        "Bond option price basis",
        "Historical Yield Vol (annualized, decimal)",
    ):
        assert label in text
