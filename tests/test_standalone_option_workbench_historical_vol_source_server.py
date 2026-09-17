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
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import shiori_pricing_lab.app.standalone_option_historical_vol_source as source_module
import shiori_pricing_lab.app.standalone_option_workbench as workbench_module
import shiori_pricing_lab.app.standalone_option_workbench_server as server_module
import shiori_pricing_lab.data.historical_yield_volatility as statistic_module
from shiori_pricing_lab.app.standalone_option_run_export import (
    render_standalone_run_as_markdown,
)
from shiori_pricing_lab.app.standalone_option_workbench import (
    price_standalone_option_case,
    price_standalone_option_case_with_benchmark,
)
from shiori_pricing_lab.app.standalone_option_workbench_overlay import (
    extract_standalone_option_case_overlay,
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

# A synthetic benchmark quote for the bundled synthetic case. Made up; it
# exists only to reach the benchmark composition's own entry point.
_BENCHMARK_CASE = {
    "benchmark_id": "TEST-BENCHMARK-0001",
    "source_type": "VENDOR",
    "source_system": "SYNTHETIC_TEST_BENCHMARK",
    "source_as_of": "2026-07-01T16:00:00Z",
    "retrieved_at": "2026-07-01T16:00:05Z",
    "quote_side": "MID",
    "premium_per_100": 4.5,
    "total_premium": 2.25,
    "currency": "USD",
    "product_id": "BONDOPT-SYNTHETIC-0001",
    "snapshot_id": "SANITIZED_SYNTHETIC_STANDALONE_SNAPSHOT_0001",
    "underlying_id": "XS0000000001",
    "source_reference": "SYNTHETIC_TEST_REFERENCE",
    "notes": None,
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
    # #197's own reason, carried verbatim rather than rewritten into
    # something vaguer on the way out.
    assert "Yield Change" in payload["error"]
    assert "SAMPLE_STDEV_S_DDOF_1" in payload["error"]
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


# --- A case declaring this source is never priced from its envelope ----------
#
# Codex review, PR #215, rounds 1-3. Two earlier designs let a caller derive
# the volatility and then present evidence of having done so -- first the
# provenance mapping, then a single-use licence object -- and each had the same
# hole somewhere new, because any value a caller holds is a value a caller can
# hold again or rebuild. `price_standalone_option_case` derives it itself now,
# immediately before the request is built, so there is no interval to exploit
# and nothing below is testing a guard: it is testing that the derivation
# happens where the pricing happens.


@_requires_quantlib
def test_pricing_a_historical_case_derives_it_rather_than_trusting_the_envelope(
    monkeypatch,
) -> None:
    calls = _stub_yield_loader(monkeypatch)
    # A number left in the envelope by some earlier run, deliberately nothing
    # like what this bond's inputs produce.
    stale = _historical_case()
    stale["volatility_input"] = {**stale["volatility_input"], "volatility": 0.99}

    _request, result, display, priced_case = price_standalone_option_case(stale)

    assert len(calls) == 1
    provenance = display["historical_volatility_source"]
    priced_volatility = display["assumptions"]["price_volatility"]
    assert result.status.value == "SUCCESS"
    assert priced_volatility == provenance["equivalent_price_vol"]
    assert priced_volatility != 0.99
    # And the envelope handed back is the one that priced, so a caller echoing
    # it to a client echoes the derived number rather than the stale one.
    assert priced_case["volatility_input"]["volatility"] == priced_volatility


@_requires_quantlib
def test_the_streamlit_style_direct_caller_derives_too(monkeypatch) -> None:
    # The exposure Codex found in round 1: this entry point is reached directly
    # by the Streamlit UI, which never ran a transform of its own.
    calls = _stub_yield_loader(monkeypatch)

    _request, _result, display, _priced = price_standalone_option_case(
        json.dumps(_historical_case())
    )

    assert len(calls) == 1
    assert display["historical_volatility_source"]["vol_source"] == (
        source_module.HISTORICAL_YIELD_VOL_SOURCE
    )


@_requires_quantlib
def test_the_benchmark_composition_derives_too(monkeypatch) -> None:
    calls = _stub_yield_loader(monkeypatch)

    _request, result, _benchmark, _comparison, _calibration, display = (
        price_standalone_option_case_with_benchmark(
            _historical_case(), _BENCHMARK_CASE, active_quote_side="MID"
        )
    )

    assert len(calls) == 1
    assert result.status.value == "SUCCESS"
    assert display["assumptions"]["price_volatility"] == (
        display["historical_volatility_source"]["equivalent_price_vol"]
    )


@_requires_quantlib
def test_each_run_derives_again_rather_than_reusing_the_previous_one(monkeypatch) -> None:
    # The scenario behind round 2's finding: if Bloomberg corrects an
    # observation in the requested window between two runs, the second run must
    # see the correction. It does, because it measures the series again.
    values = [list(_YIELD_VALUES), [4.00, 4.10, 3.80, 4.55]]

    def _fake(**kwargs):
        return _history(kwargs["identifier"], values.pop(0), field_unit=kwargs.get("field_unit"))

    monkeypatch.setattr(source_module, "load_bloomberg_bond_yield_history", _fake)
    case = _historical_case()

    _r1, _res1, first, _c1 = price_standalone_option_case(case)
    _r2, _res2, second, _c2 = price_standalone_option_case(case)

    assert values == []
    assert (
        second["assumptions"]["price_volatility"]
        != first["assumptions"]["price_volatility"]
    )


def test_a_case_that_does_not_name_the_source_makes_no_acquisition(monkeypatch) -> None:
    # The no-op branch: every pre-#214 envelope, every fixture, the bundled
    # example. No Bloomberg call, and the case's own volatility prices.
    calls = _stub_yield_loader(monkeypatch)

    _request, _result, display, priced_case = price_standalone_option_case(_manual_case())

    assert calls == []
    assert "historical_volatility_source" not in display
    assert priced_case["volatility_input"] == _manual_case()["volatility_input"]


# --- The window may not reach past the valuation being priced ----------------


def test_a_window_ending_after_the_valuation_date_is_refused_before_bloomberg(
    server_url, monkeypatch
) -> None:
    # Codex review, PR #215, round 4. The conversion producer already refuses
    # observations dated after t0, so a future-informed sigma_P was never
    # priceable -- what this adds is refusing the *question* deterministically,
    # before a DAPI round trip, and naming the window rather than the answer.
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    # The bundled case values at 2026-07-01.
    case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY] = {
        **case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY],
        "end_date": "2026-07-31",
    }

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert status == 400
    assert "2026-07-31" in payload["error"]
    assert "2026-07-01" in payload["error"]
    assert calls == []


def test_a_window_ending_on_the_valuation_date_is_the_ordinary_case(
    server_url, monkeypatch
) -> None:
    # Same-day is not look-ahead: an observation from the valuation day itself
    # existed at t0.
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY] = {
        **case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY],
        "end_date": "2026-07-01",
    }

    status, payload = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert status == 200
    assert len(calls) == 1
    assert calls[0]["end_date"] == "2026-07-01"
    # With the window bounded here, the conversion producer's own look-ahead
    # gate (`_require_no_look_ahead`, pinned in
    # tests/test_bli_historical_equivalent_price_vol.py) becomes defence in
    # depth on this path rather than the thing standing between a future
    # observation and a priced premium.


# --- A refresh says it re-sourced the Yield history and the volatility -------


def _stub_bloomberg_quote(monkeypatch, *, clean_price: float = 100.75) -> None:
    """One live spot quote and one fixed acquisition clock for the refresh route.

    The clock must land on the case's own valuation date, because the route
    stamps its return as this run's ``pricing_timestamp`` and the reviewed
    request contract requires the two to agree.
    """

    from datetime import UTC, datetime

    from shiori_pricing_lab.data.bli_snapshot import (
        BLIBondQuote,
        BLIMarketDataStatus,
        BLIQuoteBasis,
    )
    from shiori_pricing_lab.products.enums import Currency, TreasuryFTPQuoteSide

    def _fake_quote(*, security, isin, quote_side):
        return BLIBondQuote(
            isin=isin,
            currency=Currency.USD,
            price_type=BLIQuoteBasis.PRICE,
            quote_side=TreasuryFTPQuoteSide(quote_side),
            source_system="BLOOMBERG_DAPI",
            status=BLIMarketDataStatus.ACTIVE,
            clean_price_per_100=clean_price,
            yield_value=None,
            accrued_interest_per_100=0.44,
        )

    monkeypatch.setattr(workbench_module, "load_bloomberg_bond_quote", _fake_quote)
    monkeypatch.setattr(
        workbench_module,
        "_shiori_acquisition_now",
        lambda: datetime(2026, 7, 1, 16, 0, 0, tzinfo=UTC),
    )


@_requires_quantlib
def test_a_historical_refresh_reports_the_yield_history_it_re_sourced(
    server_url, monkeypatch
) -> None:
    # Codex review, PR #215, round 2. This case is explicit-forward and
    # manual-curve, so before the fix the whole refresh reported
    # BOND_QUOTE_ONLY / CASE_JSON_UNCHANGED -- while it had in fact fetched
    # this bond's Yield series and recomputed the volatility Black-76 priced
    # with, against the quote it had just acquired.
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    _stub_bloomberg_quote(monkeypatch)
    case = _historical_case()

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": extract_standalone_option_case_overlay(case),
            "bloomberg_security": "/isin/XS0000000001",
            "quote_side": "MID",
        },
    )

    assert status == 200
    live_quote = payload["display"]["live_bloomberg_quote"]
    assert live_quote["refreshed_inputs"] == [
        "BOND_QUOTE",
        "HISTORICAL_YIELD_SERIES",
        "HISTORICAL_EQUIVALENT_PRICE_VOL",
    ]
    assert live_quote["refreshed_scope"] == (
        "BOND_QUOTE_AND_HISTORICAL_YIELD_SERIES_AND_HISTORICAL_EQUIVALENT_PRICE_VOL"
    )
    assert live_quote["other_market_inputs"] == (
        "CASE_JSON_UNCHANGED_EXCEPT_THE_REFRESHED_INPUTS"
    )
    # And it genuinely did re-derive: one Yield acquisition, against the
    # freshly acquired quote rather than the case's previous one.
    assert len(calls) == 1
    provenance = payload["display"]["historical_volatility_source"]
    assert provenance["duration_clean_price_per_100"] == 100.75
    assert payload["case"]["volatility_input"]["volatility"] == (
        provenance["equivalent_price_vol"]
    )


@_requires_quantlib
def test_a_refresh_recovers_a_ticket_whose_carried_quote_has_no_price(
    server_url, monkeypatch
) -> None:
    """The refresh is the cure, so the carried quote must not block it.

    The offline clean-price precondition added for `/api/case` would
    otherwise refuse the one route whose whole purpose is to replace that
    quote -- the same reasoning that already skips
    ``require_usable_spot_clean_price_for_derived_forward`` here (Codex
    review, PR #215).
    """

    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    _stub_bloomberg_quote(monkeypatch)
    case = _historical_case()
    case["bond_quote"] = {
        **case["bond_quote"],
        "clean_price_per_100": None,
        "yield_value": 4.05,
        "price_type": "YIELD",
    }

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": extract_standalone_option_case_overlay(case),
            "bloomberg_security": "/isin/XS0000000001",
            "quote_side": "MID",
        },
    )

    assert status == 200
    # It derived against the price the refresh supplied, not the one the case
    # arrived without.
    assert len(calls) == 1
    provenance = payload["display"]["historical_volatility_source"]
    assert provenance["duration_clean_price_per_100"] == 100.75


def test_a_non_historical_refresh_still_reports_quote_only(
    server_url, monkeypatch
) -> None:
    # The pre-#214 wording is unchanged for a refresh that really did
    # re-source nothing but the quote.
    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    _stub_bloomberg_quote(monkeypatch)
    case = _manual_case()

    status, payload = _post_json(
        f"{server_url}/api/case/bloomberg",
        {
            "case": case,
            "overlay": extract_standalone_option_case_overlay(case),
            "bloomberg_security": "/isin/XS0000000001",
            "quote_side": "MID",
        },
    )

    assert status == 200
    live_quote = payload["display"]["live_bloomberg_quote"]
    assert live_quote["refreshed_scope"] == "BOND_QUOTE_ONLY"
    assert "refreshed_inputs" not in live_quote
    assert calls == []


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


def test_the_derivation_is_stamped_after_the_series_it_converted(
    server_url, monkeypatch
) -> None:
    """No exported lineage may claim a conversion older than its own inputs.

    ``calculated_at`` used to be read from the clock by the caller, before the
    Yield series was requested, so on any DAPI call taking measurable time the
    duration and the conversion claimed a moment earlier than the acquisition
    they were computed from. The statistic's own post-calculation timestamp is
    stamped instead (Codex review, PR #215).
    """

    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    # The #197 calculator's own clock, moved far enough from the live one that
    # a timestamp read before the chain started cannot pass for it -- the two
    # real reads are otherwise the same second, which is precisely why this
    # defect survived a round of review.
    calculated = "2030-01-02T03:04:05+00:00"
    monkeypatch.setattr(
        statistic_module,
        "_calculation_now",
        lambda: datetime.fromisoformat(calculated),
    )

    _status, payload = _post_json(
        f"{server_url}{_REVIEW_ROUTE}", {"case": _historical_case()}
    )
    source = payload["historical_volatility_source"]

    assert source["historical_yield_vol_calculated_at"] == calculated
    # The conversion is stamped with that, not with a moment before the series
    # it converted was ever requested.
    assert source["calculated_at"] == calculated
    assert source["historical_yield_vol_acquired_at"] <= source["calculated_at"]


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


def test_readiness_ignores_a_stale_audit_the_derived_path_discards(
    server_url, monkeypatch
) -> None:
    """A saved case's audit string must not decide whether Price is offered.

    The derived path replaces ``volatility_input`` whole, audit included, so
    an envelope carrying a blank one prices perfectly well. Readiness judged
    that same string against the reviewed contract and answered "not ready",
    leaving the browser with Price disabled for a case Price would handle --
    the disagreement this stand-in exists to prevent (Codex review, PR #215).
    """

    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    case["volatility_input"] = {
        **case["volatility_input"],
        "override_or_fallback_audit": "   ",
    }

    ready_status, ready = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)
    price_status, _priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert ready_status == 200
    assert ready == {"ready": True, "error": None}
    # And readiness agreed with Price, which is the point of the stand-in.
    assert price_status == 200


def test_readiness_refuses_a_yield_only_quote_this_source_cannot_use(
    server_url, monkeypatch
) -> None:
    """Price is not offered for a ticket whose duration has no price to take.

    In Trader-Forward-Override mode nothing else in the case needs the spot
    clean price, so a yield-only quote parses and the request builds -- and
    then this source's duration refuses, deterministically, for a price the
    ticket never carried. Readiness has to say so first (Codex review,
    PR #215).
    """

    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    case["forward_clean_price_input"] = {
        **case["forward_clean_price_input"],
        "source_system": "TRADER_FORWARD_OVERRIDE",
    }
    case["bond_quote"] = {
        **case["bond_quote"],
        "clean_price_per_100": None,
        "yield_value": 4.05,
        "price_type": "YIELD",
    }

    ready_status, ready = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)
    price_status, priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert ready_status == 200
    assert ready["ready"] is False
    assert "clean_price_per_100" in ready["error"]
    # Readiness and Price agree, and neither opened a Bloomberg session for a
    # ticket that could never price.
    assert price_status == 400
    assert "clean_price_per_100" in priced["error"]
    assert calls == []


def test_readiness_refuses_a_price_basis_pricing_would_refuse(
    server_url, monkeypatch
) -> None:
    """``bond_option_price_basis`` is not part of the typed request.

    The builder therefore accepts any value for it, while Price reads it and
    raises on all but the two members -- so readiness answered "ready" for a
    run guaranteed to fail (Codex review, PR #215).
    """

    _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _manual_case(bond_option_price_basis="CLEANISH")

    ready_status, ready = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)
    price_status, _priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert ready_status == 200
    assert ready["ready"] is False
    assert "bond_option_price_basis" in ready["error"]
    assert price_status == 400


def test_readiness_refuses_a_convention_profile_the_duration_cannot_resolve(
    server_url, monkeypatch
) -> None:
    """Selected is not the same as supported.

    Readiness checked only that a profile name was present, so an unsupported
    one passed and Price fetched a whole Yield series before
    ``get_convention_profile`` refused it. Readiness resolves it with that
    same reader now, and no Bloomberg request is spent (Codex review,
    PR #215).
    """

    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case(convention_profile="NOT_A_PROFILE")

    ready_status, ready = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)
    price_status, priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert ready_status == 200
    assert ready["ready"] is False
    assert "NOT_A_PROFILE" in ready["error"]
    assert price_status == 400
    assert "NOT_A_PROFILE" in priced["error"]
    # Neither route opened a Bloomberg session for a ticket that cannot price.
    assert calls == []


def test_readiness_refuses_a_registered_profile_the_duration_does_not_support(
    server_url, monkeypatch
) -> None:
    """Registered is not the same as duration-supported.

    There are two gates, and readiness checked only the first:
    ``US_CORPORATE`` resolves through ``get_convention_profile`` and is then
    refused by the duration producer's own allowlist -- after the Yield
    series has been fetched (Codex review, PR #215).
    """

    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case(convention_profile="US_CORPORATE")

    ready_status, ready = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)
    price_status, priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert ready_status == 200
    assert ready["ready"] is False
    assert "US_CORPORATE" in ready["error"]
    assert price_status == 400
    assert "US_CORPORATE" in priced["error"]
    assert calls == []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("field_unit", "SYNTHETIC_TEST_NOT_A_UNIT", "SYNTHETIC_TEST_NOT_A_UNIT"),
        ("field_unit", None, "unit"),
        ("yield_field", "not a mnemonic!", "mnemonic"),
        ("start_date", "2026-13-45", "start_date"),
        ("end_date", "not-a-date", "end_date"),
    ],
)
def test_readiness_refuses_a_query_the_chain_could_never_answer(
    server_url, monkeypatch, field, value, expected
) -> None:
    """Non-blank is not the same as usable, and nothing supplies these later.

    A malformed mnemonic or date range is refused by the #196 loader; an
    absent or unapproved unit is refused by #197's normalization *after* the
    Bloomberg request has been spent. Both are deterministic and offline, so
    readiness answers them (Codex review, PR #215).
    """

    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY] = {
        **case[source_module.HISTORICAL_YIELD_VOL_REQUEST_KEY],
        field: value,
    }

    ready_status, ready = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)
    price_status, _priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert ready_status == 200
    assert ready["ready"] is False
    assert expected in ready["error"]
    # And Price agrees, without spending a Bloomberg request on it.
    assert price_status == 400
    assert calls == []


@pytest.mark.parametrize(
    ("maturity", "last_coupon", "expected"),
    [
        # Settlement at or after maturity: a matured bond.
        ("2026-07-02", "2026-01-02", "maturity"),
        # Settlement before maturity but inside the final coupon period --
        # decided by the dates alone, and reached only from inside the
        # repricing legs before `6f46d8c`'s successor (Codex review, PR #215).
        ("2026-12-15", "2026-06-15", "final coupon period"),
    ],
)
def test_readiness_refuses_a_bond_whose_duration_has_no_date_window(
    server_url, monkeypatch, maturity, last_coupon, expected
) -> None:
    """A matured bond is matured before any Yield series is fetched.

    The settlement roll, the settlement-before-maturity rule and the
    schedule's shape are all knowable offline, and all refused only inside
    the duration producer -- after the Bloomberg request had been spent.
    Readiness runs the producer's own prologue now, rather than the two gates
    I had enumerated by hand (Codex review, PR #215).
    """

    calls = _stub_yield_loader(monkeypatch)
    _no_live_curve(monkeypatch)
    case = _historical_case()
    record = case["bond_reference_data_universe"][0]
    # t0 is 2026-07-01, so the UST spot roll decides both states.
    record["maturity_date"] = maturity
    record["last_coupon_date"] = last_coupon

    ready_status, ready = _post_json(f"{server_url}{_VALIDATE_ROUTE}", case)
    price_status, _priced = _post_json(f"{server_url}{_PRICE_ROUTE}", case)

    assert ready_status == 200
    assert ready["ready"] is False
    assert expected in ready["error"]
    assert price_status == 400
    # And neither route spent a Bloomberg request on a bond that has no
    # duration to take.
    assert calls == []


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
