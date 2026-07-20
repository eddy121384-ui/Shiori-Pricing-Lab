"""Tests for `data/bloomberg_bond_quote.py` (Issue #6 production slice).

Covers the approved live field mapping (BID/MID/OFFER -> exactly one of
PX_BID/PX_MID/PX_ASK, plus CRNCY, ID_ISIN, and INT_ACC), the
caller-remediation contract (blpapi-unavailable, connection, service-open,
timeout, response, security, field-exception, and missing/malformed-field
failures all raise `BLIBloombergDapiError`), the session lifecycle
guarantee (`stop()` on every post-session-start path), the "no PX_LAST
fallback" hard constraint, the canonical ISIN verification (ID_ISIN must
equal the caller-supplied isin exactly), and that a successful call
returns an actual validated `BLIBondQuote`.

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


class _FakeBlpapiException(Exception):
    """Stand-in for `blpapi.exception.Exception` -- the base of every native

    `blpapi` element-access error (e.g. `InvalidConversionException`).
    """


class _FakeBlpapiExceptionNamespace:
    Exception = _FakeBlpapiException


class _FakeElement:
    def __init__(
        self, sub_elements=None, values=None, string_value=None, label="", raise_on_value=None
    ):
        self._sub = sub_elements or {}
        self._values = values
        self._string_value = string_value
        self._label = label
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
        return self._label or (self._string_value or "")


def _string_or_malformed_element(value) -> _FakeElement:
    """A scalar `_FakeElement` -- `value` is either a string, or an

    `Exception` instance to inject as a native `blpapi` element-access
    failure (finding #3's "malformed element" case).
    """

    if isinstance(value, BaseException):
        return _FakeElement(raise_on_value=value)
    return _FakeElement(string_value=value)


def _field_data(fields: dict) -> _FakeElement:
    sub = {name: _string_or_malformed_element(value) for name, value in fields.items()}
    return _FakeElement(sub_elements=sub)


def _security_data(
    *,
    security=_SECURITY,
    omit_security=False,
    fields=None,
    security_error=None,
    field_exceptions=None,
) -> _FakeElement:
    sub = {"fieldData": _field_data(fields or {})}
    if not omit_security:
        sub["security"] = _string_or_malformed_element(security)
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
        self.next_event_timeout_calls: list[int] = []

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
        self.next_event_timeout_calls.append(timeout_ms)
        if not self._events:
            raise AssertionError("fake session ran out of queued events")
        return self._events.pop(0)

    def stop(self):
        self.stopped = True


class _FakeClock:
    """Deterministic `_monotonic` stand-in: pops a queued reading per call.

    The last value repeats once exhausted, so a test only needs to queue
    exactly as many distinct readings as it cares about.
    """

    def __init__(self, values):
        self._values = list(values)

    def __call__(self):
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


class _FakeSessionOptions:
    def __init__(self):
        self.host = None
        self.port = None

    def setServerHost(self, host):
        self.host = host

    def setServerPort(self, port):
        self.port = port


def _install_fake_blpapi(
    monkeypatch, *, start_result=True, open_service_result=True, events=(), clock_values=None
):
    """Install a fake `blpapi` module and return the `_FakeSession` it will build.

    The loader's `import blpapi` is lazy (inside the function body), so
    patching `sys.modules["blpapi"]` before the call is enough -- no
    module-level attribute exists to monkeypatch instead.

    `clock_values`, if given, replaces the loader's `_monotonic` seam with a
    `_FakeClock` over those readings (finding #2 tests); by default the
    clock is constant so the whole-request deadline never expires.
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
    fake_module.exception = _FakeBlpapiExceptionNamespace

    monkeypatch.setitem(sys.modules, "blpapi", fake_module)
    clock = _FakeClock(clock_values) if clock_values is not None else (lambda: 0.0)
    monkeypatch.setattr(module, "_monotonic", clock)
    return holder


def _success_events(
    *,
    currency="USD",
    price_value="99.320312",
    accrued="0.235394",
    price_field="PX_MID",
    isin=_ISIN,
):
    fields = {"CRNCY": currency, "ID_ISIN": isin, price_field: price_value, "INT_ACC": accrued}
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
    assert sent_fields == ["CRNCY", "ID_ISIN", expected_price_field, "INT_ACC"]
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
    ["CRNCY", "ID_ISIN", "PX_MID", "INT_ACC"],
)
def test_missing_required_field_raises_clear_error_and_stops_session(monkeypatch, missing_field):
    all_fields = {"CRNCY": "USD", "ID_ISIN": _ISIN, "PX_MID": "99.32", "INT_ACC": "0.23"}
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
    security_data = _security_data(
        fields={"CRNCY": "USD", "ID_ISIN": _ISIN, "PX_MID": "99.32", "INT_ACC": "0.23"}
    )
    events = [
        _FakeEvent(_EventType.PARTIAL_RESPONSE, []),
        _FakeEvent(_EventType.RESPONSE, [_response_message(security_data_list=[security_data])]),
    ]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    quote = _load_mid()

    assert quote.clean_price_per_100 == 99.32
    assert holder["session"].stopped is True


# --- 5. Exactly one matching securityData record (PR #128 Codex finding #1) -

_FIELDS = {"CRNCY": "USD", "ID_ISIN": _ISIN, "PX_MID": "99.32", "INT_ACC": "0.23"}


def test_two_records_in_one_response_raises_clear_error(monkeypatch):
    record_a = _security_data(fields=_FIELDS)
    record_b = _security_data(fields=_FIELDS)
    message = _response_message(security_data_list=[record_a, record_b])
    holder = _install_fake_blpapi(monkeypatch, events=[_FakeEvent(_EventType.RESPONSE, [message])])

    with pytest.raises(BLIBloombergDapiError, match="2 securityData records"):
        _load_mid()

    assert holder["session"].stopped is True


def test_one_record_in_partial_plus_one_in_final_response_raises_clear_error(monkeypatch):
    partial_message = _response_message(security_data_list=[_security_data(fields=_FIELDS)])
    partial = _FakeEvent(_EventType.PARTIAL_RESPONSE, [partial_message])
    final = _response_event(_security_data(fields=_FIELDS))
    holder = _install_fake_blpapi(monkeypatch, events=[partial, final])

    with pytest.raises(BLIBloombergDapiError, match="2 securityData records"):
        _load_mid()

    assert holder["session"].stopped is True


def test_mismatched_security_identifier_raises_clear_error(monkeypatch):
    security_data = _security_data(security="SOME_OTHER Govt", fields=_FIELDS)
    holder = _install_fake_blpapi(monkeypatch, events=[_response_event(security_data)])

    with pytest.raises(BLIBloombergDapiError, match="does not match requested security"):
        _load_mid()

    assert holder["session"].stopped is True


def test_final_response_with_zero_records_raises_clear_error(monkeypatch):
    events = [_FakeEvent(_EventType.RESPONSE, [_response_message(security_data_list=[])])]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="zero securityData records"):
        _load_mid()

    assert holder["session"].stopped is True


def test_missing_security_identifier_on_record_raises_clear_error(monkeypatch):
    security_data = _security_data(omit_security=True, fields=_FIELDS)
    holder = _install_fake_blpapi(monkeypatch, events=[_response_event(security_data)])

    with pytest.raises(BLIBloombergDapiError, match="missing its own"):
        _load_mid()

    assert holder["session"].stopped is True


# --- 6. Whole-request deadline (PR #128 Codex finding #2) ------------------


def test_next_event_receives_remaining_time_not_a_fresh_timeout_each_call(monkeypatch):
    events = [
        _FakeEvent(_EventType.PARTIAL_RESPONSE, []),
        _response_event(_security_data(fields=_FIELDS)),
    ]
    holder = _install_fake_blpapi(monkeypatch, events=events, clock_values=[0.0, 2.0, 4.0])

    quote = _load_mid()

    assert quote.clean_price_per_100 == 99.32
    calls = holder["session"].next_event_timeout_calls
    assert calls == [8000, 6000]
    assert all(ms < module._REQUEST_TIMEOUT_MS for ms in calls)


def test_deadline_exceeded_after_non_response_events_raises_timeout(monkeypatch):
    events = [_FakeEvent(_EventType.PARTIAL_RESPONSE, [])]
    holder = _install_fake_blpapi(monkeypatch, events=events, clock_values=[0.0, 1.0, 11.0])

    with pytest.raises(BLIBloombergDapiError, match="timed out"):
        _load_mid()

    assert holder["session"].stopped is True
    # Raised via the accumulated deadline check, not by exhausting the queue.
    assert len(holder["session"].next_event_timeout_calls) == 1


# --- 7. Malformed Bloomberg element access (PR #128 Codex finding #3) ------


def test_malformed_currency_raises_clear_error(monkeypatch):
    fields = {"CRNCY": _FakeBlpapiException("bad conversion"), "PX_MID": "99.32", "INT_ACC": "0.23"}
    events = [_response_event(_security_data(fields=fields))]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="malformed value for CRNCY"):
        _load_mid()

    assert holder["session"].stopped is True


def test_malformed_selected_price_field_raises_clear_error(monkeypatch):
    fields = {
        "CRNCY": "USD",
        "ID_ISIN": _ISIN,
        "PX_MID": _FakeBlpapiException("bad conversion"),
        "INT_ACC": "0.23",
    }
    events = [_response_event(_security_data(fields=fields))]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="malformed value for PX_MID"):
        _load_mid()

    assert holder["session"].stopped is True


def test_malformed_accrued_interest_raises_clear_error(monkeypatch):
    fields = {
        "CRNCY": "USD",
        "ID_ISIN": _ISIN,
        "PX_MID": "99.32",
        "INT_ACC": _FakeBlpapiException("bad conversion"),
    }
    events = [_response_event(_security_data(fields=fields))]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="malformed value for INT_ACC"):
        _load_mid()

    assert holder["session"].stopped is True


def test_malformed_security_identifier_raises_clear_error(monkeypatch):
    security_data = _security_data(security=_FakeBlpapiException("bad conversion"), fields=_FIELDS)
    events = [_response_event(security_data)]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="malformed value for security"):
        _load_mid()

    assert holder["session"].stopped is True


def test_unrelated_programming_error_is_not_swallowed(monkeypatch):
    # A KeyError is not a blpapi-native error -- the narrow
    # `except blpapi.exception.Exception` must not catch it, so it
    # propagates as itself rather than being mislabeled BLIBloombergDapiError.
    fields = {"CRNCY": "USD", "ID_ISIN": _ISIN, "PX_MID": KeyError("boom"), "INT_ACC": "0.23"}
    events = [_response_event(_security_data(fields=fields))]
    _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(KeyError, match="boom"):
        _load_mid()


# --- 8. Canonical ISIN verification (Issue #6) -------------------------------
#
# ID_ISIN was identified as the canonical field via a live //blp/apiflds
# field-metadata search and confirmed against 91282CQX Govt / US91282CQX29
# before this slice was implemented (see the module docstring).


def test_exact_isin_match_succeeds(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_success_events(isin=_ISIN))

    quote = _load_mid(isin=_ISIN)

    assert quote.isin == _ISIN


def test_blank_isin_field_value_raises_clear_error(monkeypatch):
    fields = {"CRNCY": "USD", "ID_ISIN": "", "PX_MID": "99.32", "INT_ACC": "0.23"}
    events = [_response_event(_security_data(fields=fields))]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="missing ID_ISIN"):
        _load_mid()

    assert holder["session"].stopped is True


def test_malformed_isin_field_raises_clear_error(monkeypatch):
    fields = {
        "CRNCY": "USD",
        "ID_ISIN": _FakeBlpapiException("bad conversion"),
        "PX_MID": "99.32",
        "INT_ACC": "0.23",
    }
    events = [_response_event(_security_data(fields=fields))]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="malformed value for ID_ISIN"):
        _load_mid()

    assert holder["session"].stopped is True


def test_mismatched_isin_raises_clear_error(monkeypatch):
    fields = {"CRNCY": "USD", "ID_ISIN": "US00000000AA", "PX_MID": "99.32", "INT_ACC": "0.23"}
    events = [_response_event(_security_data(fields=fields))]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="does not match requested isin"):
        _load_mid(isin=_ISIN)

    assert holder["session"].stopped is True


def test_isin_field_exception_raises_clear_error(monkeypatch):
    security_data = _security_data(
        fields={"CRNCY": "USD"}, field_exceptions=["[BAD_FLD] ID_ISIN not applicable to security"]
    )
    events = [_response_event(security_data)]
    holder = _install_fake_blpapi(monkeypatch, events=events)

    with pytest.raises(BLIBloombergDapiError, match="field exception"):
        _load_mid()

    assert holder["session"].stopped is True
