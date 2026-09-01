"""Tests for ``data/bloomberg_bond_yield_history.py`` (Issue #196).

Covers: (1) the Yield field is caller-supplied and never defaulted or
guessed, and a malformed mnemonic never reaches Bloomberg; (2) the request
Shiori actually sends -- one security, one field, the requested range in
Bloomberg's own ``YYYYMMDD`` form, and the four pinned options that stop
Bloomberg from filling non-trading days in; (3) chronological parsing of a
normal response; (4) missing dates stay missing and a valueless row stays a
visible hole; (5) an empty series is a valid, non-synthetic answer; (6)
every fail-closed condition -- non-finite/malformed value, duplicate date,
out-of-range date, missing/malformed date, securityError, fieldException,
no securityData, two securities in one answer, responseError, timeout,
session/service failure; (7) security/field identity and requested range
are preserved verbatim on the result.

Every number and date below is made up. Nothing here is a Bloomberg value.

No network access, no real ``blpapi``, no system clock: Bloomberg is faked
with the same minimal stand-in objects ``tests/test_bloomberg_option_
discount_curve.py`` already established, injected via
``sys.modules["blpapi"]`` so this loader's lazy ``import blpapi`` picks them
up.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from shiori_pricing_lab.data import bloomberg_bond_yield_history as module
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError
from shiori_pricing_lab.data.bloomberg_bond_yield_history import (
    SOURCE_SYSTEM,
    load_bloomberg_bond_yield_history,
)

_SECURITY = "/isin/XS0000000000"
_FIELD = "SYNTHETIC_TEST_YIELD_FIELD"


# --- Fake blpapi surface -----------------------------------------------------
# Mirrors only the calls the loader actually makes.


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


def _row(observation_date=None, value=None, *, omit_date=False) -> _FakeElement:
    sub = {}
    if not omit_date:
        sub["date"] = _leaf(observation_date)
    if value is not None:
        sub[_FIELD] = _leaf(value)
    return _FakeElement(sub_elements=sub)


def _security_data(
    *,
    security=_SECURITY,
    rows=(),
    omit_security=False,
    omit_field_data=False,
    security_error=None,
    field_exceptions=None,
) -> _FakeElement:
    sub: dict = {}
    if not omit_security:
        sub["security"] = _leaf(security)
    if not omit_field_data:
        sub["fieldData"] = _FakeElement(values=list(rows))
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


def _message(*, security_data=None, response_error=None) -> _FakeElement:
    if response_error is not None:
        return _FakeElement(
            sub_elements={"responseError": _FakeElement(string_value=response_error)}
        )
    return _FakeElement(sub_elements={"securityData": security_data})


def _response(security_data) -> list[_FakeEvent]:
    return [_FakeEvent(_EventType.RESPONSE, [_message(security_data=security_data)])]


def _rows_response(rows) -> list[_FakeEvent]:
    return _response(_security_data(rows=rows))


class _FakeRequest:
    def __init__(self):
        self.securities: list[str] = []
        self.fields: list[str] = []
        self.options: dict[str, str] = {}

    def append(self, name, value):
        if name == "securities":
            self.securities.append(value)
        elif name == "fields":
            self.fields.append(value)
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected append field {name!r}")

    def set(self, name, value):
        self.options[name] = value


class _FakeService:
    def __init__(self, session):
        self._session = session

    def createRequest(self, name):
        assert name == "HistoricalDataRequest"
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


_FIXED_ACQUISITION = datetime(2026, 8, 31, 14, 5, 0, tzinfo=UTC)


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
    monkeypatch.setattr(module, "_acquisition_now", lambda: _FIXED_ACQUISITION)
    return holder


def _load(**overrides):
    kwargs = {
        "identifier": _SECURITY,
        "yield_field": _FIELD,
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
    }
    kwargs.update(overrides)
    return load_bloomberg_bond_yield_history(**kwargs)


# --- caller input validation (no blpapi needed) -------------------------------


@pytest.mark.parametrize("identifier", ["", "   ", None, 7])
def test_rejects_a_blank_identifier(identifier):
    with pytest.raises(ValueError, match="identifier"):
        _load(identifier=identifier)


@pytest.mark.parametrize(
    "malformed_field",
    ["", "   ", "px last", "PX-LAST", "px_last", "PX LAST", "YLD;DROP", None],
)
def test_rejects_a_malformed_yield_field_before_any_bloomberg_request(malformed_field):
    with pytest.raises(ValueError, match="yield_field"):
        _load(yield_field=malformed_field)


def test_yield_field_has_no_default_anywhere_in_the_module():
    """The mnemonic is never guessed: it is a required keyword with no default."""

    with pytest.raises(TypeError):
        load_bloomberg_bond_yield_history(  # type: ignore[call-arg]
            identifier=_SECURITY, start_date="2026-01-01", end_date="2026-01-31"
        )


@pytest.mark.parametrize("malformed_date", ["2026-1-1", "20260101", "", "not-a-date", None])
def test_rejects_a_malformed_date(malformed_date):
    with pytest.raises(ValueError):
        _load(start_date=malformed_date)


def test_rejects_a_datetime_where_a_calendar_date_is_required():
    with pytest.raises(ValueError, match="calendar date"):
        _load(start_date=datetime(2026, 1, 1, 9, 0, 0))


def test_rejects_an_inverted_date_range():
    with pytest.raises(ValueError, match="must not be after"):
        _load(start_date="2026-02-01", end_date="2026-01-01")


def test_rejects_a_blank_provenance_string():
    with pytest.raises(ValueError, match="field_unit"):
        _load(field_unit="   ")


# --- the request Shiori actually sends ----------------------------------------


def test_requests_exactly_one_security_and_one_field_over_the_requested_range(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_rows_response([]))

    _load(start_date="2026-01-05", end_date="2026-03-09")

    request = holder["session"].last_request
    assert request.securities == [_SECURITY]
    assert request.fields == [_FIELD]
    assert request.options["startDate"] == "20260105"
    assert request.options["endDate"] == "20260309"


def test_pins_the_request_so_bloomberg_never_fills_a_non_trading_day(monkeypatch):
    """The four options that keep the returned series raw (Issue #196 §B)."""

    holder = _install_fake_blpapi(monkeypatch, events=_rows_response([]))

    _load()

    options = holder["session"].last_request.options
    assert options["periodicitySelection"] == "DAILY"
    assert options["periodicityAdjustment"] == "ACTUAL"
    assert options["nonTradingDayFillOption"] == "ACTIVE_DAYS_ONLY"
    assert options["nonTradingDayFillMethod"] == "NIL_VALUE"


def test_stops_the_session_on_a_successful_load(monkeypatch):
    holder = _install_fake_blpapi(monkeypatch, events=_rows_response([]))

    _load()

    assert holder["session"].stopped is True


def test_stops_the_session_after_a_failed_load(monkeypatch):
    holder = _install_fake_blpapi(
        monkeypatch,
        events=_response(_security_data(security_error="BAD_SEC")),
    )

    with pytest.raises(BLIBloombergDapiError):
        _load()

    assert holder["session"].stopped is True


# --- normal parsing -----------------------------------------------------------


def test_parses_a_normal_response_chronologically(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=_rows_response(
            [
                _row("2026-01-08", "4.125"),
                _row("2026-01-06", "4.0"),
                _row("2026-01-07", "4.25"),
            ]
        ),
    )

    history = _load()

    assert [o.observation_date for o in history.observations] == [
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
    ]
    assert [o.yield_value for o in history.observations] == [4.0, 4.25, 4.125]


def test_preserves_bloombergs_own_value_string_alongside_the_float(monkeypatch):
    """The table shows every digit Bloomberg sent, not a re-formatted float."""

    _install_fake_blpapi(monkeypatch, events=_rows_response([_row("2026-01-06", "4.1200000")]))

    history = _load()

    assert history.observations[0].raw_value == "4.1200000"
    assert history.observations[0].yield_value == 4.12


def test_preserves_security_field_identity_and_the_requested_range(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_rows_response([_row("2026-01-06", "4.0")]))

    history = _load(field_meaning="Synthetic test meaning", field_unit="percent")

    assert history.requested_identifier == _SECURITY
    assert history.security == _SECURITY
    assert history.yield_field == _FIELD
    assert history.field_meaning == "Synthetic test meaning"
    assert history.field_unit == "percent"
    assert history.requested_start_date == date(2026, 1, 1)
    assert history.requested_end_date == date(2026, 1, 31)
    assert history.source_system == SOURCE_SYSTEM
    assert history.acquired_at == _FIXED_ACQUISITION.isoformat(timespec="seconds")


def test_unconfirmed_field_semantics_stay_unconfirmed(monkeypatch):
    """No unit or meaning is ever inferred from the mnemonic."""

    _install_fake_blpapi(monkeypatch, events=_rows_response([_row("2026-01-06", "4.0")]))

    history = _load()

    assert history.field_meaning is None
    assert history.field_unit is None


def test_records_the_security_bloomberg_itself_resolved(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=_response(
            _security_data(security="SYNTHETIC TEST Corp", rows=[_row("2026-01-06", "4.0")])
        ),
    )

    history = _load()

    assert history.requested_identifier == _SECURITY
    assert history.security == "SYNTHETIC TEST Corp"


def test_joins_a_paginated_partial_response(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[
            _FakeEvent(
                _EventType.PARTIAL_RESPONSE,
                [_message(security_data=_security_data(rows=[_row("2026-01-06", "4.0")]))],
            ),
            _FakeEvent(
                _EventType.RESPONSE,
                [_message(security_data=_security_data(rows=[_row("2026-01-07", "4.25")]))],
            ),
        ],
    )

    history = _load()

    assert [o.observation_date for o in history.observations] == [
        date(2026, 1, 6),
        date(2026, 1, 7),
    ]


# --- gaps are gaps ------------------------------------------------------------


def test_missing_dates_remain_missing(monkeypatch):
    """A day Bloomberg did not answer for is simply absent -- never filled in."""

    _install_fake_blpapi(
        monkeypatch,
        events=_rows_response([_row("2026-01-06", "4.0"), _row("2026-01-09", "4.4")]),
    )

    history = _load()

    assert [o.observation_date for o in history.observations] == [
        date(2026, 1, 6),
        date(2026, 1, 9),
    ]
    assert len(history.observations) == 2


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_returned_row_with_no_value_is_a_visible_hole(monkeypatch, blank):
    _install_fake_blpapi(
        monkeypatch,
        events=_rows_response(
            [
                _row("2026-01-06", "4.0"),
                _row("2026-01-07", blank),
                _row("2026-01-08"),
                _row("2026-01-09", "4.4"),
            ]
        ),
    )

    history = _load()

    assert [o.yield_value for o in history.observations] == [4.0, None, None, 4.4]
    assert [o.raw_value for o in history.observations] == ["4.0", None, None, "4.4"]


def test_an_empty_series_is_a_valid_answer(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_rows_response([]))

    history = _load()

    assert history.observations == ()
    assert history.security == _SECURITY
    assert history.yield_field == _FIELD


def test_an_answer_with_no_field_data_element_is_an_empty_series(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_response(_security_data(omit_field_data=True)))

    history = _load()

    assert history.observations == ()


# --- fail closed --------------------------------------------------------------


@pytest.mark.parametrize("malformed", ["not-a-number", "nan", "inf", "-inf", "4.0.0", "4,0"])
def test_a_malformed_or_non_finite_value_fails_closed(monkeypatch, malformed):
    _install_fake_blpapi(
        monkeypatch,
        events=_rows_response([_row("2026-01-06", "4.0"), _row("2026-01-07", malformed)]),
    )

    with pytest.raises(BLIBloombergDapiError):
        _load()


def test_a_duplicate_observation_date_fails_visibly(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=_rows_response([_row("2026-01-06", "4.0"), _row("2026-01-06", "4.5")]),
    )

    with pytest.raises(BLIBloombergDapiError, match="two observations dated"):
        _load()


def test_a_duplicate_date_across_two_partial_responses_fails_visibly(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[
            _FakeEvent(
                _EventType.PARTIAL_RESPONSE,
                [_message(security_data=_security_data(rows=[_row("2026-01-06", "4.0")]))],
            ),
            _FakeEvent(
                _EventType.RESPONSE,
                [_message(security_data=_security_data(rows=[_row("2026-01-06", "4.5")]))],
            ),
        ],
    )

    with pytest.raises(BLIBloombergDapiError, match="two observations dated"):
        _load()


def test_an_observation_outside_the_requested_range_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_rows_response([_row("2026-02-02", "4.0")]))

    with pytest.raises(BLIBloombergDapiError, match="outside the requested range"):
        _load()


def test_a_row_with_no_date_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_rows_response([_row(value="4.0", omit_date=True)]))

    with pytest.raises(BLIBloombergDapiError, match="no date"):
        _load()


@pytest.mark.parametrize("malformed_date", ["20260106", "06/01/2026", "2026-13-01", ""])
def test_a_malformed_observation_date_fails_closed(monkeypatch, malformed_date):
    _install_fake_blpapi(monkeypatch, events=_rows_response([_row(malformed_date, "4.0")]))

    with pytest.raises(BLIBloombergDapiError, match="observation date"):
        _load()


def test_a_security_error_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_response(_security_data(security_error="BAD_SEC")))

    with pytest.raises(BLIBloombergDapiError, match="securityError"):
        _load()


def test_a_field_exception_fails_closed_rather_than_looking_like_no_history(monkeypatch):
    """An unrecognised or unentitled mnemonic must never read as an empty series."""

    _install_fake_blpapi(
        monkeypatch,
        events=_response(_security_data(rows=[], field_exceptions=["BAD_FLD"])),
    )

    with pytest.raises(BLIBloombergDapiError, match="field exception"):
        _load()


def test_no_security_data_at_all_fails_closed(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[_FakeEvent(_EventType.RESPONSE, [_FakeElement(sub_elements={})])],
    )

    with pytest.raises(BLIBloombergDapiError, match="no securityData"):
        _load()


def test_two_different_securities_in_one_answer_fail_closed(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[
            _FakeEvent(
                _EventType.PARTIAL_RESPONSE,
                [
                    _message(
                        security_data=_security_data(
                            security="A Corp", rows=[_row("2026-01-06", "4.0")]
                        )
                    )
                ],
            ),
            _FakeEvent(
                _EventType.RESPONSE,
                [
                    _message(
                        security_data=_security_data(
                            security="B Corp", rows=[_row("2026-01-07", "4.1")]
                        )
                    )
                ],
            ),
        ],
    )

    with pytest.raises(BLIBloombergDapiError, match="2 different securities"):
        _load()


def test_a_response_error_fails_closed(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[_FakeEvent(_EventType.RESPONSE, [_message(response_error="NOT_ENTITLED")])],
    )

    with pytest.raises(BLIBloombergDapiError, match="responseError"):
        _load()


def test_a_malformed_element_fails_closed(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=_rows_response([_row("2026-01-06", _FakeBlpapiException("not convertible"))]),
    )

    with pytest.raises(BLIBloombergDapiError, match="malformed value"):
        _load()


def test_a_timeout_event_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=[_FakeEvent(_EventType.TIMEOUT)])

    with pytest.raises(BLIBloombergDapiError, match="timed out"):
        _load()


def test_an_expired_deadline_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, events=_rows_response([]))
    clock = iter([0.0, 10_000.0])
    monkeypatch.setattr(module, "_monotonic", lambda: next(clock))

    with pytest.raises(BLIBloombergDapiError, match="timed out"):
        _load()


def test_a_session_that_will_not_start_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, start_result=False)

    with pytest.raises(BLIBloombergDapiError, match="failed to start"):
        _load()


def test_a_service_that_will_not_open_fails_closed(monkeypatch):
    _install_fake_blpapi(monkeypatch, open_service_result=False)

    with pytest.raises(BLIBloombergDapiError, match="failed to open service"):
        _load()


def test_a_missing_blpapi_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules, "blpapi", None)

    with pytest.raises(BLIBloombergDapiError, match="blpapi is not installed"):
        _load()


# --- read-only: nothing downstream of market data is reachable from here ------


def test_the_loader_imports_no_pricing_vol_or_store_module():
    """Issue #196 §D: this is a market-data acquisition slice, and only that.

    A structural check rather than a behavioural one: the loader cannot
    mutate the VCUB store, call the vol resolver, populate PRICE_VOL/
    YIELD_VOL, or touch Forward/Discounting if it never imports any of them.
    """

    source = Path(module.__file__).read_text(encoding="utf-8")
    import_lines = [
        line.replace("shiori_pricing_lab", "")  # the package name is not a subpackage
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) or line.strip().startswith("import blpapi")
    ]
    forbidden = (
        "pricing",
        "vol_surface",
        "vcub",
        "products",
        "valuation",
        "reference_data",
        "journal",
        "app",
    )
    for line in import_lines:
        assert not any(word in line.lower() for word in forbidden), line


def test_the_loader_computes_no_statistic():
    """No standard deviation, annualization, or Yield Change lives in this module."""

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    body = source.split('"""', 2)[2]  # skip the module docstring, which names them to forbid them
    for forbidden in ("stdev", "std_dev", "stdlib.statistics", "annualiz", "sqrt", "variance"):
        assert forbidden not in body
