"""Tests for `data/bloomberg_bond_quote.py` (Issue #6 production slice).

Covers the approved live field mapping (BID/MID/OFFER -> exactly one of
PX_BID/PX_MID/PX_ASK, plus CRNCY and INT_ACC), the caller-remediation
contract (blpapi-unavailable, connection, service-open, timeout, response,
security, field-exception, and missing/malformed-field failures all raise
`BLIBloombergDapiError`), the session lifecycle guarantee (`stop()` on
every post-session-start path), the "no PX_LAST fallback" hard constraint,
and that a successful call returns an actual validated `BLIBondQuote`.

No network access, no real `blpapi`, no system clock: Bloomberg is faked
with minimal stand-in objects that mirror only the small slice of the real
`blpapi` Session/Service/Request/Event/Message surface this loader calls,
injected via `sys.modules["blpapi"]` so the loader's *lazy* `import blpapi`
picks them up.
"""

from __future__ import annotations

import sys

import pytest

from shiori_pricing_lab.data import bloomberg_bond_quote as module
from shiori_pricing_lab.data.bli_snapshot import BLIBondQuote, BLIMarketDataStatus, BLIQuoteBasis
from shiori_pricing_lab.data.bloomberg_bond_quote import (
    BLIBloombergDapiError,
    load_bloomberg_bond_quote,
)
from shiori_pricing_lab.products.enums import Currency, TreasuryFTPQuoteSide

_SECURITY = "91282CQX Govt"
_ISIN = "US91282CQX00"


def _load_mid(**overrides):
    params = dict(security=_SECURITY, isin=_ISIN, quote_side=TreasuryFTPQuoteSide.MID)
    params.update(overrides)
    return load_bloomberg_bond_quote(**params)


# --- Fake blpapi surface -----------------------------------------------------
#
# Mirrors only the calls `load_bloomberg_bond_quote` actually makes:
# SessionOptions.setServerHost/setServerPort, Session(options)/start()/
# openService()/getService()/sendRequest()/nextEvent()/stop(), Service.
# createRequest(), Request.append(), Event.eventType()/iteration, Message/
# Element .hasElement()/.getElement()/.getElementAsString()/.numValues()/
# .getValueAsElement().


class _FakeElement:
    def __init__(self, sub_elements=None, values=None, string_value=None, label=""):
        self._sub = sub_elements or {}
        self._values = values
        self._string_value = string_value
        self._label = label

    def hasElement(self, name):
        return name in self._sub

    def getElement(self, name):
        return self._sub[name]

    def getElementAsString(self, name):
        return self._sub[name].getValueAsString()

    def getValueAsString(self):
        return self._string_value

    def numValues(self):
        return len(self._values or [])

    def getValueAsElement(self, index):
        return self._values[index]

    def __str__(self):
        return self._label or (self._string_value or "")


def _field_data(fields: dict) -> _FakeElement:
    sub = {name: _FakeElement(string_value=value) for name, value in fields.items()}
    return _FakeElement(sub_elements=sub)


def _security_data(*, fields=None, security_error=None, field_exceptions=None) -> _FakeElement:
    sub = {"fieldData": _field_data(fields or {})}
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error, label=security_error)
    if field_exceptions is not None:
        sub["fieldExceptions"] = _FakeElement(
            values=[_FakeElement(string_value=fe, label=fe) for fe in field_exceptions]
        )
    return _FakeElement(sub_elements=sub)


def _response_message(*, security_data_list=None, response_error=None) -> _FakeElement:
    if response_error is not None:
        error_element = _FakeElement(string_value=response_error, label=response_error)
        return _FakeElement(sub_elements={"responseError": error_element})
    security_data = _FakeElement(values=security_data_list or [])
    return _FakeElement(sub_elements={"securityData": security_data})


def _response_event(security_data) -> _FakeEvent:
    """One RESPONSE event carrying a single security's `securityData`."""

    return _FakeEvent(_EventType.RESPONSE, [_response_message(security_data_list=[security_data])])


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


class _FakeRequest:
    def __init__(self):
        self.securities: list[str] = []
        self.fields: list[str] = []

    def append(self, name, value):
        if name == "securities":
            self.securities.append(value)
        elif name == "fields":
            self.fields.append(value)
        else:  # pragma: no cover - defensive, loader never appends anything else
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
        self.opened_service = None
        self.last_request = None

    def start(self):
        return self._start_result

    def openService(self, uri):
        self.opened_service = uri
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
    def __init__(self):
        self.host = None
        self.port = None

    def setServerHost(self, host):
        self.host = host

    def setServerPort(self, port):
        self.port = port


def _install_fake_blpapi(monkeypatch, *, start_result=True, open_service_result=True, events=()):
    """Install a fake `blpapi` module and return the `_FakeSession` it will build.

    The loader's `import blpapi` is lazy (inside the function body), so
    patching `sys.modules["blpapi"]` before the call is enough -- no
    module-level attribute exists to monkeypatch instead.
    """

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

    monkeypatch.setitem(sys.modules, "blpapi", fake_module)
    return holder


def _success_events(
    *, currency="USD", price_value="99.320312", accrued="0.235394", price_field="PX_MID"
):
    fields = {"CRNCY": currency, price_field: price_value, "INT_ACC": accrued}
    return [_response_event(_security_data(fields=fields))]


# --- 1. Approved field mapping: exactly one price field per quote side ------


@pytest.mark.parametrize(
    ("quote_side", "expected_price_field"),
    [
        (TreasuryFTPQuoteSide.BID, "PX_BID"),
        (TreasuryFTPQuoteSide.MID, "PX_MID"),
        (TreasuryFTPQuoteSide.OFFER, "PX_ASK"),
    ],
)
def test_quote_side_requests_exactly_one_price_field(monkeypatch, quote_side, expected_price_field):
    holder = _install_fake_blpapi(
        monkeypatch, events=_success_events(price_field=expected_price_field, price_value="101.5")
    )

    quote = load_bloomberg_bond_quote(security=_SECURITY, isin=_ISIN, quote_side=quote_side)

    sent_fields = holder["session"].last_request.fields
    assert sent_fields == ["CRNCY", expected_price_field, "INT_ACC"]
    assert "PX_LAST" not in sent_fields
    assert quote.clean_price_per_100 == 101.5
    assert quote.quote_side is quote_side


def test_no_px_last_fallback_in_field_mapping():
    # The approved field mapping is exhaustive over all three
    # TreasuryFTPQuoteSide members and never maps any of them to PX_LAST.
    assert set(module._PRICE_FIELD_BY_QUOTE_SIDE) == set(TreasuryFTPQuoteSide)
    assert "PX_LAST" not in module._PRICE_FIELD_BY_QUOTE_SIDE.values()


# --- 2. Field mapping correctness (CRNCY / INT_ACC / raw price passthrough) --


def test_maps_currency_price_and_accrued_interest_exactly(monkeypatch):
    events = _success_events(
        currency="USD", price_field="PX_MID", price_value="99.316406", accrued="0.235394"
    )
    _install_fake_blpapi(monkeypatch, events=events)

    quote = _load_mid()

    assert quote.currency is Currency.USD
    assert quote.clean_price_per_100 == 99.316406
    assert quote.accrued_interest_per_100 == 0.235394


def test_successful_call_returns_validated_bond_quote_with_required_shape(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_success_events())

    quote = _load_mid()

    assert isinstance(quote, BLIBondQuote)
    assert quote.isin == _ISIN
    assert quote.price_type is BLIQuoteBasis.PRICE
    assert quote.source_system == "BLOOMBERG_DAPI"
    assert quote.status is BLIMarketDataStatus.ACTIVE
    assert quote.yield_value is None


def test_session_stopped_on_success(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_success_events())

    _load_mid()

    assert holder["session"].stopped is True


def test_no_input_mutation(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_success_events())
    security = _SECURITY
    isin = _ISIN
    quote_side = TreasuryFTPQuoteSide.MID

    load_bloomberg_bond_quote(security=security, isin=isin, quote_side=quote_side)

    assert security == _SECURITY
    assert isin == _ISIN
    assert quote_side is TreasuryFTPQuoteSide.MID


# --- 3. Caller-input validation (ValueError, not BLIBloombergDapiError) -----


def test_blank_security_raises_value_error():
    with pytest.raises(ValueError, match="security"):
        load_bloomberg_bond_quote(security="  ", isin=_ISIN, quote_side=TreasuryFTPQuoteSide.MID)


def test_blank_isin_raises_value_error():
    with pytest.raises(ValueError, match="isin"):
        load_bloomberg_bond_quote(security=_SECURITY, isin="", quote_side=TreasuryFTPQuoteSide.MID)


def test_invalid_quote_side_raises_value_error():
    with pytest.raises(ValueError, match="quote_side"):
        load_bloomberg_bond_quote(security=_SECURITY, isin=_ISIN, quote_side="NOT_A_SIDE")


# --- 4. Explicit Bloomberg-side failures (all BLIBloombergDapiError) --------


def test_blpapi_unavailable_raises_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "blpapi", None)

    with pytest.raises(BLIBloombergDapiError, match="blpapi is not installed"):
        _load_mid()


def test_connection_failure_raises_clear_error_and_stops_session(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, start_result=False, events=[])

    with pytest.raises(BLIBloombergDapiError, match="failed to start"):
        _load_mid()

    assert holder["session"].stopped is True


def test_service_open_failure_raises_clear_error_and_stops_session(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, open_service_result=False, events=[])

    with pytest.raises(BLIBloombergDapiError, match="failed to open service"):
        _load_mid()

    assert holder["session"].stopped is True


def test_timeout_exits_loop_and_raises_clear_error_and_stops_session(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=[_FakeEvent(_EventType.TIMEOUT)])

    with pytest.raises(BLIBloombergDapiError, match="timed out"):
        _load_mid()

    assert holder["session"].stopped is True


def test_response_error_raises_clear_error_and_stops_session(monkeypatch):
    events = [_FakeEvent(_EventType.RESPONSE, [_response_message(response_error="bad request")])]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="responseError"):
        _load_mid()

    assert holder["session"].stopped is True


def test_security_error_raises_clear_error_and_stops_session(monkeypatch):
    security_data = _security_data(security_error="UNKNOWN_SECURITY")
    events = [_response_event(security_data)]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="securityError"):
        _load_mid()

    assert holder["session"].stopped is True


def test_field_exception_raises_clear_error_and_stops_session(monkeypatch):
    security_data = _security_data(
        fields={"CRNCY": "USD"}, field_exceptions=["[BAD_FLD] Field not applicable to security"]
    )
    events = [_response_event(security_data)]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="field exception"):
        _load_mid()

    assert holder["session"].stopped is True


@pytest.mark.parametrize(
    "missing_field",
    ["CRNCY", "PX_MID", "INT_ACC"],
)
def test_missing_required_field_raises_clear_error_and_stops_session(monkeypatch, missing_field):
    all_fields = {"CRNCY": "USD", "PX_MID": "99.32", "INT_ACC": "0.23"}
    del all_fields[missing_field]
    security_data = _security_data(fields=all_fields)
    events = [_response_event(security_data)]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match=f"missing {missing_field}"):
        _load_mid()

    assert holder["session"].stopped is True


def test_non_numeric_price_raises_clear_error(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_success_events(price_value="N/A"))

    with pytest.raises(BLIBloombergDapiError, match="non-numeric"):
        _load_mid()


def test_non_finite_price_raises_clear_error(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_success_events(price_value="nan"))

    with pytest.raises(BLIBloombergDapiError, match="non-finite"):
        _load_mid()


def test_partial_response_then_response_events_are_both_consumed(monkeypatch):
    security_data = _security_data(fields={"CRNCY": "USD", "PX_MID": "99.32", "INT_ACC": "0.23"})
    events = [
        _FakeEvent(_EventType.PARTIAL_RESPONSE, []),
        _FakeEvent(_EventType.RESPONSE, [_response_message(security_data_list=[security_data])]),
    ]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    quote = _load_mid()

    assert quote.clean_price_per_100 == 99.32
    assert holder["session"].stopped is True
