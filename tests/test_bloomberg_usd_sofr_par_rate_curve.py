"""Tests for `data/bloomberg_usd_sofr_par_rate_curve.py` (Issue #168).

Covers: (1) the exact, explicit 32-tenor `USOSFR*` mapping matches Eddy's
Issue #168 table verbatim and shares its tenor universe/order with
`bloomberg_option_discount_curve.DEFAULT_USD_SOFR_TENORS`; (2) only
`LAST_PRICE` is requested, never `MATURITY` or any other field; (3) the
returned `par_rate_percent` is never divided by 100 (kept in percent, per
Issue #168 Acceptance Criteria A); (4) every fail-closed condition from
Issue #168 Acceptance Criteria B (BAD_SEC/securityError, field exception,
missing/blank LAST_PRICE, non-numeric, non-finite, incomplete batch); (5) no
ticker outside the approved mapping is ever requested; (6) canonical
acquisition ordering is independent of caller input order.

No network access, no real `blpapi`, no system clock: Bloomberg is faked
with the same minimal stand-in objects
`tests/test_bloomberg_option_discount_curve.py` already established,
injected via `sys.modules["blpapi"]` so this loader's lazy `import blpapi`
picks them up.
"""

from __future__ import annotations

import math
import sys

import pytest

from shiori_pricing_lab.data import bloomberg_usd_sofr_par_rate_curve as module
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.data.bloomberg_option_discount_curve import DEFAULT_USD_SOFR_TENORS
from shiori_pricing_lab.data.bloomberg_usd_sofr_par_rate_curve import (
    USD_SOFR_PAR_RATE_SOURCE_SYSTEM,
    USD_SOFR_PAR_RATE_TICKERS,
    load_bloomberg_usd_sofr_par_rate_curve,
)

_USOSFR_1Y = "USOSFR1 Curncy"
_USOSFR_2Y = "USOSFR2 Curncy"


# --- Fake blpapi surface -----------------------------------------------------
# Mirrors only the calls the loader actually makes -- same shape as
# tests/test_bloomberg_option_discount_curve.py's own fake harness.


class _FakeBlpapiException(Exception):
    pass


class _FakeBlpapiExceptionNamespace:
    Exception = _FakeBlpapiException


class _FakeElement:
    def __init__(self, sub_elements=None, values=None, string_value=None, raise_on_value=None):
        self._sub = sub_elements or {}
        self._values = values
        self._string_value = string_value
        self._raise_on_value = raise_on_value

    def hasElement(self, name):
        return name in self._sub

    def getElement(self, name):
        return self._sub[name]

    def getElementAsString(self, name):
        return self._sub[name].getValueAsString()

    def getValueAsString(self):
        if self._raise_on_value is not None:
            raise self._raise_on_value
        return self._string_value

    def numValues(self):
        return len(self._values or [])

    def getValueAsElement(self, index):
        return self._values[index]

    def __str__(self):
        return self._string_value or "<element>"


def _leaf(value) -> _FakeElement:
    if isinstance(value, BaseException):
        return _FakeElement(raise_on_value=value)
    return _FakeElement(string_value=value)


def _field_data(fields: dict) -> _FakeElement:
    return _FakeElement(sub_elements={name: _leaf(value) for name, value in fields.items()})


def _security_record(
    *,
    security,
    omit_security=False,
    last_price=None,
    security_error=None,
    field_exceptions=None,
) -> _FakeElement:
    fields = {}
    if last_price is not None:
        fields["LAST_PRICE"] = last_price
    sub = {"fieldData": _field_data(fields)}
    if not omit_security:
        sub["security"] = _leaf(security)
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error)
    if field_exceptions is not None:
        sub["fieldExceptions"] = _FakeElement(
            values=[_FakeElement(string_value=fe) for fe in field_exceptions]
        )
    return _FakeElement(sub_elements=sub)


class _EventType:
    TIMEOUT = "TIMEOUT"
    PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
    RESPONSE = "RESPONSE"


class _FakeEvent:
    def __init__(self, event_type, messages=()):
        self._event_type = event_type
        self._messages = list(messages)

    def eventType(self):
        return self._event_type

    def __iter__(self):
        return iter(self._messages)


def _response_message(*, records=None, response_error=None) -> _FakeElement:
    if response_error is not None:
        return _FakeElement(
            sub_elements={"responseError": _FakeElement(string_value=response_error)}
        )
    return _FakeElement(sub_elements={"securityData": _FakeElement(values=list(records or []))})


def _response_event(records) -> _FakeEvent:
    return _FakeEvent(_EventType.RESPONSE, [_response_message(records=records)])


class _FakeRequest:
    def __init__(self):
        self.securities: list[str] = []
        self.fields: list[str] = []

    def append(self, name, value):
        if name == "securities":
            self.securities.append(value)
        elif name == "fields":
            self.fields.append(value)
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected append field {name!r}")


class _FakeService:
    def __init__(self, session):
        self._session = session

    def createRequest(self, name):
        assert name == "ReferenceDataRequest"
        request = _FakeRequest()
        self._session.last_request = request
        return request


class _FakeSession:
    def __init__(self, options, *, start_result, open_service_result, events):
        self.options = options
        self._start_result = start_result
        self._open_service_result = open_service_result
        self._events = list(events)
        self.stopped = False
        self.last_request = None

    def start(self):
        return self._start_result

    def openService(self, uri):
        return self._open_service_result

    def getService(self, uri):
        return _FakeService(self)

    def sendRequest(self, request):
        pass

    def nextEvent(self, timeout_ms):
        if not self._events:
            raise AssertionError("fake session ran out of queued events")
        return self._events.pop(0)

    def stop(self):
        self.stopped = True


class _FakeSessionOptions:
    def setServerHost(self, host):
        self.host = host

    def setServerPort(self, port):
        self.port = port


def _install_fake_blpapi(monkeypatch, *, start_result=True, open_service_result=True, events=()):
    holder: dict = {}

    def _session_factory(options):
        session = _FakeSession(
            options,
            start_result=start_result,
            open_service_result=open_service_result,
            events=list(events),
        )
        holder["session"] = session
        return session

    fake_module = type(sys)("blpapi")
    fake_module.SessionOptions = _FakeSessionOptions
    fake_module.Session = _session_factory
    fake_module.Event = _EventType
    fake_module.exception = _FakeBlpapiExceptionNamespace

    monkeypatch.setitem(sys.modules, "blpapi", fake_module)
    monkeypatch.setattr(module, "_monotonic", lambda: 0.0)
    return holder


def _both_tenors_success(*, rate_1y="3.7500", rate_2y="3.4200"):
    records = [
        _security_record(security=_USOSFR_1Y, last_price=rate_1y),
        _security_record(security=_USOSFR_2Y, last_price=rate_2y),
    ]
    return [_response_event(records)]


# --- input validation (no blpapi needed) --------------------------------------------


def test_rejects_empty_tenors():
    with pytest.raises(ValueError, match="must not be empty"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=())


def test_rejects_a_blank_tenor():
    with pytest.raises(ValueError, match="non-blank"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "   "))


def test_rejects_a_duplicate_tenor():
    with pytest.raises(ValueError, match="duplicate tenor"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "1Y"))


@pytest.mark.parametrize(
    "malformed_tenor", ["1D", "1w", "0W", "1Q", "1 W", "abc", "12 MO", "60W", "14M"]
)
def test_rejects_a_malformed_or_unapproved_tenor_before_any_bloomberg_request(malformed_tenor):
    # No blpapi installed in this test process at all -- if this loader's
    # own local acquisition-label validation did not fail closed before
    # ever reaching Bloomberg, the lazy `import blpapi` a few lines later
    # would raise a different error (or, worse, a fake session might be
    # hit); "unsupported tenor label" proves rejection happens first, pre-DAPI.
    with pytest.raises(ValueError, match="unsupported tenor label"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=(malformed_tenor,))


# --- the exact, approved Issue #168 32-tenor mapping ------------------------


_EXPECTED_USOSFR_TICKERS = {
    "1W": "USOSFR1Z Curncy",
    "2W": "USOSFR2Z Curncy",
    "3W": "USOSFR3Z Curncy",
    "1M": "USOSFRA Curncy",
    "2M": "USOSFRB Curncy",
    "3M": "USOSFRC Curncy",
    "4M": "USOSFRD Curncy",
    "5M": "USOSFRE Curncy",
    "6M": "USOSFRF Curncy",
    "7M": "USOSFRG Curncy",
    "8M": "USOSFRH Curncy",
    "9M": "USOSFRI Curncy",
    "10M": "USOSFRJ Curncy",
    "11M": "USOSFRK Curncy",
    "1Y": "USOSFR1 Curncy",
    "18M": "USOSFR1F Curncy",
    "2Y": "USOSFR2 Curncy",
    "3Y": "USOSFR3 Curncy",
    "4Y": "USOSFR4 Curncy",
    "5Y": "USOSFR5 Curncy",
    "6Y": "USOSFR6 Curncy",
    "7Y": "USOSFR7 Curncy",
    "8Y": "USOSFR8 Curncy",
    "9Y": "USOSFR9 Curncy",
    "10Y": "USOSFR10 Curncy",
    "12Y": "USOSFR12 Curncy",
    "15Y": "USOSFR15 Curncy",
    "20Y": "USOSFR20 Curncy",
    "25Y": "USOSFR25 Curncy",
    "30Y": "USOSFR30 Curncy",
    "40Y": "USOSFR40 Curncy",
    "50Y": "USOSFR50 Curncy",
}


def test_ticker_mapping_matches_eddys_approved_issue_168_table_exactly():
    assert USD_SOFR_PAR_RATE_TICKERS == _EXPECTED_USOSFR_TICKERS
    assert len(USD_SOFR_PAR_RATE_TICKERS) == 32


def test_ticker_mapping_shares_the_same_tenor_universe_as_curve_490():
    # Issue #168: "the same canonical 32-tenor universe already used by the
    # Curve #490 S0490Z/S0490D production path."
    assert set(USD_SOFR_PAR_RATE_TICKERS) == set(DEFAULT_USD_SOFR_TENORS)
    assert tuple(USD_SOFR_PAR_RATE_TICKERS) == DEFAULT_USD_SOFR_TENORS


def test_source_system_is_bloomberg_dapi():
    assert USD_SOFR_PAR_RATE_SOURCE_SYSTEM == "BLOOMBERG_DAPI"


# --- request shape -----------------------------------------------------------


def test_sends_one_security_per_tenor_and_only_last_price(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_both_tenors_success())
    load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "2Y"))

    request = holder["session"].last_request
    assert sorted(request.securities) == sorted([_USOSFR_1Y, _USOSFR_2Y])
    assert request.fields == ["LAST_PRICE"]


def test_never_requests_a_ticker_outside_the_approved_mapping(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_both_tenors_success())
    load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "2Y"))

    request = holder["session"].last_request
    assert set(request.securities).issubset(set(USD_SOFR_PAR_RATE_TICKERS.values()))


def test_records_are_matched_by_identity_regardless_of_response_order(monkeypatch):
    records = [
        _security_record(security=_USOSFR_2Y, last_price="3.42"),
        _security_record(security=_USOSFR_1Y, last_price="3.75"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])

    result = load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "2Y"))

    by_tenor = {point.tenor: point for point in result.points}
    assert by_tenor["1Y"].par_rate_percent == 3.75
    assert by_tenor["2Y"].par_rate_percent == 3.42


# --- fail-closed conditions (Issue #168 Acceptance Criteria B) --------------


def test_bad_sec_fails_closed(monkeypatch):
    records = [_security_record(security=_USOSFR_1Y, security_error="BAD_SEC")]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="securityError"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_missing_security_record_fails_closed(monkeypatch):
    records = []
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="expected exactly one"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_duplicate_security_record_fails_closed(monkeypatch):
    records = [
        _security_record(security=_USOSFR_1Y, last_price="3.75"),
        _security_record(security=_USOSFR_1Y, last_price="3.76"),
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="expected exactly one"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_field_exception_on_last_price_fails_closed(monkeypatch):
    records = [
        _security_record(security=_USOSFR_1Y, field_exceptions=["LAST_PRICE field exception"])
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="field exception"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_missing_last_price_fails_closed(monkeypatch):
    records = [_security_record(security=_USOSFR_1Y, last_price=None)]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="LAST_PRICE"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_blank_last_price_fails_closed(monkeypatch):
    records = [_security_record(security=_USOSFR_1Y, last_price="")]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="LAST_PRICE"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_non_numeric_last_price_fails_closed(monkeypatch):
    records = [_security_record(security=_USOSFR_1Y, last_price="not-a-number")]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="non-numeric"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


@pytest.mark.parametrize("non_finite", ["nan", "inf", "-inf"])
def test_non_finite_last_price_fails_closed(monkeypatch, non_finite):
    records = [_security_record(security=_USOSFR_1Y, last_price=non_finite)]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="non-finite"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_incomplete_batch_fails_closed_never_returns_partial_curve(monkeypatch):
    # Only 1Y comes back; 2Y was requested too -- the whole call must abort,
    # never silently return just the 1Y point as if it were a complete result.
    records = [_security_record(security=_USOSFR_1Y, last_price="3.75")]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="expected exactly one"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "2Y"))


def test_malformed_security_identifier_element_fails_closed(monkeypatch):
    malformed = _FakeBlpapiException("boom")
    records = [
        _FakeElement(
            sub_elements={
                "security": _leaf(malformed),
                "fieldData": _field_data({"LAST_PRICE": "3.75"}),
            }
        )
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    with pytest.raises(BLIBloombergDapiError, match="malformed value"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_response_error_fails_closed(monkeypatch):
    event = _FakeEvent(
        _EventType.RESPONSE, [_response_message(response_error="bad request")]
    )
    _install_fake_blpapi(monkeypatch, events=[event])
    with pytest.raises(BLIBloombergDapiError, match="responseError"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_timeout_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=[_FakeEvent(_EventType.TIMEOUT)])
    with pytest.raises(BLIBloombergDapiError, match="timed out"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_session_start_failure_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, start_result=False)
    with pytest.raises(BLIBloombergDapiError, match="failed to start"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_open_service_failure_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, open_service_result=False)
    with pytest.raises(BLIBloombergDapiError, match="failed to open service"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


def test_blpapi_not_installed_raises_bli_bloomberg_dapi_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "blpapi", None)
    monkeypatch.delitem(sys.modules, "blpapi", raising=False)

    real_import = __import__

    def _raise_for_blpapi(name, *args, **kwargs):
        if name == "blpapi":
            raise ImportError("no blpapi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _raise_for_blpapi)

    with pytest.raises(BLIBloombergDapiError, match="blpapi is not installed"):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))


# --- successful acquisition / normalization ---------------------------------


def test_successful_curve_produces_one_point_per_tenor(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success())
    result = load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "2Y"))
    assert len(result.points) == 2


def test_par_rate_percent_is_never_divided_by_100(monkeypatch):
    # Issue #168 Acceptance Criteria A: "normalized as SWAP (Par Rate) in
    # percent" -- a displayed "3.7500" must stay 3.75, never become 0.0375.
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success(rate_1y="3.7500"))
    result = load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))
    assert result.points[0].par_rate_percent == 3.75


def test_point_preserves_tenor_security_and_raw_last_price(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success(rate_1y="3.7500"))
    result = load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))
    point = result.points[0]
    assert point.tenor == "1Y"
    assert point.security == "USOSFR1 Curncy"
    assert point.raw_last_price == "3.7500"
    assert point.source_system == "BLOOMBERG_DAPI"


def test_result_is_sorted_by_canonical_acquisition_order_regardless_of_input_order(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success())
    result = load_bloomberg_usd_sofr_par_rate_curve(tenors=("2Y", "1Y"))
    assert [point.tenor for point in result.points] == ["1Y", "2Y"]


def test_negative_par_rate_is_accepted_never_assumed_positive(monkeypatch):
    # A par rate may legitimately be negative in some rate regimes -- this
    # loader imposes no sign constraint, only finiteness (Issue #168).
    _install_fake_blpapi(monkeypatch, events=_both_tenors_success(rate_1y="-0.05"))
    result = load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))
    assert result.points[0].par_rate_percent == -0.05


def test_session_is_stopped_on_success(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_both_tenors_success())
    load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y", "2Y"))
    assert holder["session"].stopped is True


def test_session_is_stopped_on_failure(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, start_result=False)
    with pytest.raises(BLIBloombergDapiError):
        load_bloomberg_usd_sofr_par_rate_curve(tenors=("1Y",))
    assert holder["session"].stopped is True


def test_default_tenors_acquire_the_full_32_tenor_universe(monkeypatch):
    records = [
        _security_record(security=security, last_price="3.00")
        for security in USD_SOFR_PAR_RATE_TICKERS.values()
    ]
    _install_fake_blpapi(monkeypatch, events=[_response_event(records)])
    result = load_bloomberg_usd_sofr_par_rate_curve()
    assert len(result.points) == 32
    assert [point.tenor for point in result.points] == list(DEFAULT_USD_SOFR_TENORS)
    assert all(math.isfinite(point.par_rate_percent) for point in result.points)
