"""Tests for `tools/bloomberg_dapi_probe.py` (PR #141 second revision).

This is a standalone diagnostic CLI, not part of the production pricing or
workbench path -- these tests only prove `probe_fields`/`main` report each
field's raw outcome (returned / absent / field_exception) correctly and
never let one bad field abort the whole probe, plus that the CLI reuses the
workbench's own bounded identifier parser rather than a second one.

No network access, no real `blpapi`: Bloomberg is faked with minimal
stand-in objects mirroring only the small slice of the real `blpapi`
Session/Service/Request/Event/Element surface `probe_fields` calls,
injected via `sys.modules["blpapi"]` so the lazy `import blpapi` picks
them up -- same technique as `tests/test_bloomberg_bond_quote.py`, kept as
its own slim copy here rather than shared, consistent with this repo's
practice of not coupling test harnesses across independent modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import bloomberg_dapi_probe as module  # noqa: E402
from bloomberg_dapi_probe import ProbeFieldResult, main, probe_fields  # noqa: E402

_IDENTIFIER = "/isin/US91282CLJ89"


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
        return self._string_value or ""


def _field_data(fields: dict) -> _FakeElement:
    sub = {name: _FakeElement(string_value=value) for name, value in fields.items()}
    return _FakeElement(sub_elements=sub)


def _field_exception(field_id: str, message: str) -> _FakeElement:
    return _FakeElement(
        sub_elements={
            "fieldId": _FakeElement(string_value=field_id),
            "errorInfo": _FakeElement(string_value=message),
        }
    )


def _security_data(
    *, security=_IDENTIFIER, fields=None, security_error=None, field_exceptions=None
) -> _FakeElement:
    sub = {"fieldData": _field_data(fields or {}), "security": _FakeElement(string_value=security)}
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error)
    if field_exceptions is not None:
        sub["fieldExceptions"] = _FakeElement(values=field_exceptions)
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


def _response_event(security_data) -> _FakeEvent:
    security_data_array = _FakeElement(values=[security_data])
    message = _FakeElement(sub_elements={"securityData": security_data_array})
    return _FakeEvent(_EventType.RESPONSE, [message])


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
    def __init__(self, options, *, events):
        self.options = options
        self._events = list(events)
        self.stopped = False
        self.last_request = None

    def start(self):
        return True

    def openService(self, uri):
        return True

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
        pass

    def setServerPort(self, port):
        pass


def _install_fake_blpapi(monkeypatch, *, events=()):
    holder = {}

    def _session_factory(options):
        session = _FakeSession(options, events=events)
        holder["session"] = session
        return session

    fake_module = type(sys)("blpapi")
    fake_module.SessionOptions = _FakeSessionOptions
    fake_module.Session = _session_factory
    fake_module.Event = _EventType
    fake_module.exception = _FakeBlpapiExceptionNamespace
    monkeypatch.setitem(sys.modules, "blpapi", fake_module)
    return holder


# --- probe_fields --------------------------------------------------------------


def test_probe_fields_reports_a_returned_value(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[_response_event(_security_data(fields={"CPN": "4.125"}))],
    )

    results = probe_fields(_IDENTIFIER, ["CPN"])

    assert results == [ProbeFieldResult(field="CPN", status="returned", value="4.125")]


def test_probe_fields_reports_absent_when_not_returned(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[_response_event(_security_data(fields={}))],
    )

    results = probe_fields(_IDENTIFIER, ["CPN"])

    assert results == [ProbeFieldResult(field="CPN", status="absent")]


def test_probe_fields_reports_field_exception_detail(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[
            _response_event(
                _security_data(
                    fields={},
                    field_exceptions=[_field_exception("CPN", "[BAD_FLD] not applicable")],
                )
            )
        ],
    )

    results = probe_fields(_IDENTIFIER, ["CPN"])

    assert results == [
        ProbeFieldResult(field="CPN", status="field_exception", detail="[BAD_FLD] not applicable")
    ]


def test_probe_fields_one_bad_field_never_affects_another(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[
            _response_event(
                _security_data(
                    fields={"CRNCY": "USD"},
                    field_exceptions=[_field_exception("CPN", "[BAD_FLD] not applicable")],
                )
            )
        ],
    )

    results = probe_fields(_IDENTIFIER, ["CRNCY", "CPN"])

    assert results == [
        ProbeFieldResult(field="CRNCY", status="returned", value="USD"),
        ProbeFieldResult(field="CPN", status="field_exception", detail="[BAD_FLD] not applicable"),
    ]


def test_probe_fields_sends_the_identifier_and_all_requested_fields_verbatim(monkeypatch):
    holder = _install_fake_blpapi(
        monkeypatch,
        events=[_response_event(_security_data(fields={"CPN": "4.125"}))],
    )

    probe_fields(_IDENTIFIER, ["CPN"])

    assert holder["session"].last_request.securities == [_IDENTIFIER]
    assert holder["session"].last_request.fields == ["CPN"]


def test_probe_fields_raises_on_security_error(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        events=[_response_event(_security_data(security_error="UNKNOWN_SECURITY"))],
    )

    with pytest.raises(RuntimeError, match="securityError"):
        probe_fields(_IDENTIFIER, ["CPN"])


def test_probe_fields_session_stopped_after_success(monkeypatch):
    holder = _install_fake_blpapi(
        monkeypatch,
        events=[_response_event(_security_data(fields={"CPN": "4.125"}))],
    )

    probe_fields(_IDENTIFIER, ["CPN"])

    assert holder["session"].stopped is True


def test_probe_fields_session_stopped_after_failure(monkeypatch):
    holder = _install_fake_blpapi(
        monkeypatch,
        events=[_response_event(_security_data(security_error="UNKNOWN_SECURITY"))],
    )

    with pytest.raises(RuntimeError):
        probe_fields(_IDENTIFIER, ["CPN"])

    assert holder["session"].stopped is True


# --- CLI -----------------------------------------------------------------------


def test_main_rejects_an_invalid_identifier_without_calling_bloomberg(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(module, "probe_fields", lambda *a, **k: calls.append((a, k)))

    exit_code = main(["--identifier", "TOOSHORT"])

    assert exit_code == 2
    assert calls == []
    assert "ISIN" in capsys.readouterr().err


def test_main_qualifies_a_plain_isin_via_the_shared_parser(monkeypatch, capsys):
    seen = {}

    def _fake_probe_fields(identifier, fields):
        seen["identifier"] = identifier
        seen["fields"] = fields
        return [ProbeFieldResult(field="CPN", status="absent")]

    monkeypatch.setattr(module, "probe_fields", _fake_probe_fields)

    exit_code = main(["--identifier", "us91282clj89", "--fields", "CPN"])

    assert exit_code == 0
    assert seen["identifier"] == "/isin/US91282CLJ89"
    assert seen["fields"] == ["CPN"]


def test_main_defaults_to_the_candidate_bond_master_field_list(monkeypatch):
    seen = {}

    def _fake_probe_fields(identifier, fields):
        seen["fields"] = fields
        return []

    monkeypatch.setattr(module, "probe_fields", _fake_probe_fields)

    main(["--identifier", "US91282CLJ89"])

    assert seen["fields"] == list(module._CANDIDATE_BOND_MASTER_FIELDS)


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(identifier, fields):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "probe_fields", _raise_import_error)

    exit_code = main(["--identifier", "US91282CLJ89", "--fields", "CPN"])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err
