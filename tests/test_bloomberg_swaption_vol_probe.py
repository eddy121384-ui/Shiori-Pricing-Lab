"""Tests for `tools/bloomberg_swaption_vol_probe.py` (Market Data
Reconnection Phase 1).

This is a standalone diagnostic CLI, not part of the production pricing,
vol, ingestion or workbench path. These tests prove four things and nothing
else:

1. the probe's own stages report what they were given, and a failure in one
   stage never turns into a fabricated answer in another;
2. **no ticker, field mnemonic or override value is ever defaulted** -- a run
   with no operator-supplied candidate requests no value, and the report says
   so rather than inventing one;
3. the ten report items keep their evidence classes apart -- an operator's
   declaration is never presented as Bloomberg evidence, and a returned
   number never establishes its own vol type or unit;
4. the existing no-leak guard is applied to external text, including an
   override value echoed back inside a Bloomberg error message.

No network access and no real `blpapi`. Bloomberg is faked with stand-in
objects mirroring only the slice of the real Session/Service/Request/Event/
Element surface the *reused* helpers actually call, injected via
`sys.modules["blpapi"]` so the lazy `import blpapi` picks them up -- the
same technique `tests/test_bloomberg_dapi_probe.py` uses, kept as its own
slim copy here rather than shared, consistent with this repository's
practice of not coupling test harnesses across independent modules.

The reused helpers themselves (`discover_service`, `probe_fields`,
`hunt_vol_fields`) are covered by their own test modules; this file proves
only that this probe wires them correctly, so the field-search stage is
driven through an injected stand-in rather than re-proving that helper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import bloomberg_swaption_vol_probe as module  # noqa: E402
from bloomberg_dapi_probe import ProbeFieldResult  # noqa: E402
from bloomberg_swaption_vol_probe import (  # noqa: E402
    AuthorizationStatus,
    EvidenceClass,
    ServiceOutcome,
    SessionReport,
    TargetCoordinate,
    ValueProbeRecord,
    build_report,
    build_report_items,
    main,
    probe_session_services,
    run_value_probes,
)

_IDENTIFIER = "OPERATOR-SUPPLIED-SECURITY"
_FIELD = "OPERATOR_SUPPLIED_FIELD"
_REFDATA = "//blp/refdata"
_APIFLDS = "//blp/apiflds"


# --------------------------------------------------------------------------
# A minimal fake blpapi
# --------------------------------------------------------------------------


class _FakeBlpapiException(Exception):
    pass


class _FakeBlpapiExceptionNamespace:
    Exception = _FakeBlpapiException


class _FakeDataType:
    STRING = "STRING"


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

    def setElement(self, name, value):
        self._sub[name] = _FakeElement(string_value=value)

    def getElementAsString(self, name):
        return self._sub[name].getValueAsString()

    def getValueAsString(self, index=None):
        if self._raise_on_value is not None:
            raise self._raise_on_value
        if index is not None and self._values is not None:
            return self._values[index]
        return self._string_value

    def numValues(self):
        return len(self._values or [])

    def getValueAsElement(self, index):
        return self._values[index]

    def __str__(self):
        return self._string_value or ""


class _FakeElementDefinition:
    def __init__(self, name, datatype):
        self._name = name
        self._datatype = datatype

    def name(self):
        return self._name

    def typeDefinition(self):
        return self


class _FakeTypeDefinition:
    def __init__(self, element_names=(), datatype="STRING"):
        self._element_names = element_names
        self._datatype = datatype

    def numElementDefinitions(self):
        return len(self._element_names)

    def getElementDefinition(self, index):
        return _FakeElementDefinition(self._element_names[index], _FakeDataType.STRING)

    def datatype(self):
        return self._datatype


class _FakeRequestDefinition:
    def __init__(self, element_names):
        self._element_names = element_names

    def typeDefinition(self):
        return _FakeTypeDefinition(self._element_names)

    def __str__(self):
        return f"<request definition {list(self._element_names)}>"


class _FakeOperation:
    def __init__(self, name, element_names=("searchSpec",), description="op"):
        self._name = name
        self._element_names = element_names
        self._description = description

    def name(self):
        return self._name

    def description(self):
        return self._description

    def requestDefinition(self):
        return _FakeRequestDefinition(self._element_names)

    def numResponseDefinitions(self):
        return 0

    def getResponseDefinitionAt(self, index):  # pragma: no cover - never called with 0
        raise IndexError(index)


class _FakeService:
    def __init__(self, name, operations):
        self._name = name
        self._operations = operations

    def numOperations(self):
        return len(self._operations)

    def getOperation(self, index):
        return self._operations[index]

    def createRequest(self, request_name):
        self._request_name = request_name
        return _FakeRequest()

    def __str__(self):
        return f"<service {self._name}>"


class _FakeRequest:
    def __init__(self):
        self.fields = []
        self.securities = []
        self.overrides = []

    def append(self, name, value):
        if name == "fields":
            self.fields.append(value)
        elif name == "securities":
            self.securities.append(value)

    def set(self, name, value):
        self._set = (name, value)

    def getElement(self, name):
        if name == "overrides":
            holder = _FakeElement(values=[])

            def append_element():
                element = _FakeElement(sub_elements={})
                self.overrides.append(element)
                return element

            holder.appendElement = append_element
            return holder
        raise KeyError(name)


class _FakeSessionOptions:
    def setServerHost(self, host):
        self.host = host

    def setServerPort(self, port):
        self.port = port


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


class _FakeSession:
    def __init__(self, options=None, *, start=True, open_services=(_REFDATA, _APIFLDS),
                 events=None, behaviours=None):
        self._start = start
        self._open_services = set(open_services)
        self._events = list(events or [])
        self._behaviours = dict(behaviours or {})
        self.stopped = False

    def start(self):
        return self._start

    def openService(self, uri):
        return uri in self._open_services

    def getService(self, uri):
        operations = (_FakeOperation("FieldInfoRequest", ("id",)),)
        if uri == _APIFLDS:
            operations = (_FakeOperation("FieldSearchRequest", ("searchSpec",)),)
        return _FakeService(uri, operations)

    def sendRequest(self, request):
        self._sent = request
        behaviour = self._behaviours.get("events")
        self._events = list(behaviour) if behaviour is not None else self._events
        if not self._events:
            self._events = [_FakeEvent(_EventType.RESPONSE, [])]

    def nextEvent(self, timeout_ms):
        if not self._events:
            return _FakeEvent(_EventType.TIMEOUT, [])
        return self._events.pop(0)

    def stop(self):
        self.stopped = True


class _FakeSessionFactory:
    """Stands in for `blpapi.Session`, handing out one configured fake."""

    def __init__(self, **session_kwargs):
        self._kwargs = session_kwargs
        self.created: list[_FakeSession] = []

    def __call__(self, options=None):
        session = _FakeSession(options, **self._kwargs)
        self.created.append(session)
        return session


def _install_fake_blpapi(monkeypatch, **session_kwargs):
    factory = _FakeSessionFactory(**session_kwargs)
    fake = type(
        "blpapi",
        (),
        {
            "SessionOptions": _FakeSessionOptions,
            "Session": factory,
            "Event": _EventType,
            "DataType": _FakeDataType,
            "exception": _FakeBlpapiExceptionNamespace,
        },
    )
    monkeypatch.setitem(sys.modules, "blpapi", fake)
    return factory


def _security_data_message(*, fields, security=_IDENTIFIER, security_error=None,
                           field_exceptions=None):
    """One `ReferenceDataRequest` response message, shaped as `probe_fields` reads it."""

    field_data = _FakeElement(
        sub_elements={name: _FakeElement(string_value=value) for name, value in fields.items()}
    )
    sub = {"fieldData": field_data, "security": _FakeElement(string_value=security)}
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error)
    if field_exceptions is not None:
        sub["fieldExceptions"] = _FakeElement(values=field_exceptions)
    records = _FakeElement(values=[_FakeElement(sub_elements=sub)])
    return _FakeElement(sub_elements={"securityData": records})


# --------------------------------------------------------------------------
# Stage 1 -- connectivity / service authorisation
# --------------------------------------------------------------------------


def test_probe_session_services_reports_each_service(monkeypatch):
    _install_fake_blpapi(monkeypatch)

    session = probe_session_services((_REFDATA, _APIFLDS))

    assert [outcome.service_uri for outcome in session.outcomes] == [_REFDATA, _APIFLDS]
    assert all(outcome.opened for outcome in session.outcomes)
    assert all(outcome.open_error is None for outcome in session.outcomes)
    assert session.apiflds_open_error is None


def test_probe_session_services_reports_a_refused_service_without_crashing(monkeypatch):
    _install_fake_blpapi(monkeypatch, open_services=(_REFDATA,))

    session = probe_session_services((_REFDATA, _APIFLDS))

    by_uri = {outcome.service_uri: outcome for outcome in session.outcomes}
    assert by_uri[_REFDATA].opened is True
    assert by_uri[_APIFLDS].opened is False
    assert "//blp/apiflds" in (by_uri[_APIFLDS].open_error or "")
    assert session.apiflds_open_error is not None


def test_probe_session_services_reports_session_start_failure(monkeypatch):
    _install_fake_blpapi(monkeypatch, start=False)

    session = probe_session_services((_REFDATA,))

    assert session.outcomes[0].opened is False
    assert "session failed to start" in (session.outcomes[0].open_error or "")


def test_probe_session_services_propagates_a_missing_blpapi(monkeypatch):
    """A missing SDK is a usage/install error (exit 2), never a fake result."""

    monkeypatch.setitem(sys.modules, "blpapi", None)
    with pytest.raises(ImportError):
        probe_session_services((_REFDATA,))


# --------------------------------------------------------------------------
# Stage 3 -- the value probe never invents a candidate
# --------------------------------------------------------------------------


def test_run_value_probes_returns_nothing_without_candidates():
    assert run_value_probes((), ()) == ()
    assert run_value_probes((_IDENTIFIER,), ()) == ()
    assert run_value_probes((), (_FIELD,)) == ()


def test_run_value_probes_records_every_outcome_without_aborting():
    def fake_probe(security, fields, overrides):
        return [
            ProbeFieldResult(field="A", status="returned", value="89.15"),
            ProbeFieldResult(field="B", status="absent"),
            ProbeFieldResult(field="C", status="field_exception", detail="permission denied"),
        ]

    records = run_value_probes(
        (_IDENTIFIER,), ("A", "B", "C"), (), probe=fake_probe, clock=lambda: "2026-01-01T00:00:00Z"
    )

    assert [record.status for record in records] == ["returned", "absent", "field_exception"]
    assert records[0].value == "89.15"
    assert records[2].detail == "permission denied"
    assert {record.fetched_at for record in records} == {"2026-01-01T00:00:00Z"}


def test_run_value_probes_records_a_request_error_per_field():
    def exploding_probe(security, fields, overrides):
        raise RuntimeError("session died")

    records = run_value_probes((_IDENTIFIER,), ("A", "B"), (), probe=exploding_probe)

    assert [record.status for record in records] == ["request_error", "request_error"]
    assert all("session died" in (record.detail or "") for record in records)


def test_value_probe_end_to_end_through_the_reused_probe_fields(monkeypatch):
    """The real `probe_fields` path, driven by the fake session, returns the fake value."""

    message = _security_data_message(fields={_FIELD: "89.15"})
    _install_fake_blpapi(
        monkeypatch, behaviours={"events": [_FakeEvent(_EventType.RESPONSE, [message])]}
    )

    records = run_value_probes((_IDENTIFIER,), (_FIELD,), ())

    assert len(records) == 1
    assert records[0].status == "returned"
    assert records[0].value == "89.15"
    assert records[0].field == _FIELD


def test_a_bloomberg_field_exception_is_reported_not_raised(monkeypatch):
    message = _security_data_message(
        fields={},
        field_exceptions=[
            _FakeElement(
                sub_elements={
                    "fieldId": _FakeElement(string_value=_FIELD),
                    "errorInfo": _FakeElement(string_value="not entitled"),
                }
            )
        ],
    )
    _install_fake_blpapi(
        monkeypatch, behaviours={"events": [_FakeEvent(_EventType.RESPONSE, [message])]}
    )

    records = run_value_probes((_IDENTIFIER,), (_FIELD,), ())

    assert records[0].status == "field_exception"
    assert "not entitled" in (records[0].detail or "")


def test_a_bloomberg_security_error_becomes_a_request_error_per_field(monkeypatch):
    message = _security_data_message(fields={}, security_error="INVALID_SECURITY")
    _install_fake_blpapi(
        monkeypatch, behaviours={"events": [_FakeEvent(_EventType.RESPONSE, [message])]}
    )

    records = run_value_probes((_IDENTIFIER,), (_FIELD,))

    assert records[0].status == "request_error"
    assert "INVALID_SECURITY" in (records[0].detail or "")


def test_override_values_are_scrubbed_from_echoed_error_text():
    def echoing_probe(security, fields, overrides):
        return [
            ProbeFieldResult(
                field="A",
                status="field_exception",
                detail="override OPERATOR_SUPPLIED_OVERRIDE=SECRET-VALUE was rejected",
            )
        ]

    records = run_value_probes(
        (_IDENTIFIER,),
        ("A",),
        (("OPERATOR_SUPPLIED_OVERRIDE", "SECRET-VALUE"),),
        probe=echoing_probe,
    )

    assert "SECRET-VALUE" not in (records[0].detail or "")
    assert "<redacted override value>" in (records[0].detail or "")


# --------------------------------------------------------------------------
# Stage 4 -- the report contract and its evidence classes
# --------------------------------------------------------------------------


def _session(*, opened=True) -> SessionReport:
    return SessionReport(
        host="localhost",
        port=8194,
        attempted_service_uris=(_REFDATA, _APIFLDS),
        outcomes=(
            ServiceOutcome(
                service_uri=_REFDATA,
                opened=opened,
                open_error=None if opened else "refused",
                operation_count=1,
                operation_list_error=None,
            ),
        ),
        apiflds_open_error=None,
    )


def _record(status="returned", value="89.15", detail=None) -> ValueProbeRecord:
    return ValueProbeRecord(
        security=_IDENTIFIER,
        field=_FIELD,
        overrides=(),
        status=status,
        value=value if status == "returned" else None,
        detail=detail,
        fetched_at="2026-01-01T00:00:00Z",
    )


def _items_by_name(items):
    return {item.item: item for item in items}


def test_report_items_cover_every_question_this_phase_asks():
    items = build_report_items(
        _session(), TargetCoordinate(), (), (), fetched_at="2026-01-01T00:00:00Z"
    )

    assert set(_items_by_name(items)) >= {
        "connection",
        "authorization",
        "value",
        "vol_type",
        "vol_unit",
        "expiry",
        "tenor",
        "quote_side",
        "data_date",
        "fetched_at",
    }


def test_a_returned_value_never_establishes_its_own_vol_type_or_unit():
    """The core guard: magnitude is not evidence of meaning (Annex A.8.1)."""

    items = _items_by_name(
        build_report_items(
            _session(),
            TargetCoordinate(),
            (_record(),),
            (),
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    assert items["value"].evidence_class is EvidenceClass.VENDOR_RETURNED
    for item_name in ("vol_type", "vol_unit", "expiry", "tenor", "quote_side", "data_date"):
        assert items[item_name].evidence_class is EvidenceClass.NOT_ESTABLISHED
        assert "never defaults it" in items[item_name].detail


def test_operator_declarations_are_labelled_as_his_and_never_as_bloomberg_evidence():
    items = _items_by_name(
        build_report_items(
            _session(),
            TargetCoordinate(
                expiry="1Y",
                tenor="5Y",
                strike="ATM",
                quote_side="MID",
                vol_type="Normal",
                vol_unit="bp",
                data_as_of="2026-01-01",
            ),
            (),
            (),
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    for item_name in ("vol_type", "vol_unit", "expiry", "tenor", "quote_side", "data_date"):
        assert items[item_name].evidence_class is EvidenceClass.OPERATOR_DECLARED
        assert "his assertion" in items[item_name].detail
    assert items["expiry"].detail.count("1Y") >= 1
    assert items["tenor"].detail.count("5Y") >= 1


def test_connection_and_authorization_evidence_follow_the_session_evidence():
    opened = _items_by_name(
        build_report_items(_session(), TargetCoordinate(), (), (),
                           fetched_at="2026-01-01T00:00:00Z")
    )
    refused = _items_by_name(
        build_report_items(_session(opened=False), TargetCoordinate(), (), (),
                           fetched_at="2026-01-01T00:00:00Z")
    )

    assert opened["connection"].evidence_class is EvidenceClass.PROBE_OBSERVED
    assert opened["authorization"].evidence_class is EvidenceClass.VENDOR_RETURNED
    assert "SERVICE_LEVEL_ONLY" in opened["authorization"].detail

    assert refused["connection"].evidence_class is EvidenceClass.NOT_ESTABLISHED
    assert refused["authorization"].evidence_class is EvidenceClass.NOT_ESTABLISHED
    assert "NOT_ESTABLISHED" in refused["authorization"].detail


def test_authorization_reaches_field_level_only_once_a_field_answered():
    items = _items_by_name(
        build_report_items(
            _session(), TargetCoordinate(), (_record(),), (),
            fetched_at="2026-01-01T00:00:00Z",
        )
    )
    assert "FIELD_LEVEL_OBSERVED" in items["authorization"].detail


def test_documented_overrides_are_reported_as_bloomberg_evidence():
    items = _items_by_name(
        build_report_items(
            _session(), TargetCoordinate(), (), ("OPERATOR_SUPPLIED_OVERRIDE",),
            fetched_at="2026-01-01T00:00:00Z",
        )
    )

    assert items["bloomberg_documented_overrides"].evidence_class is (
        EvidenceClass.VENDOR_RETURNED
    )


def test_built_report_declares_itself_read_only_and_assigns_no_verdict():
    report = build_report(
        _session(), TargetCoordinate(), None, (), generated_at="2026-01-01T00:00:00Z"
    )

    assert report["read_only"] is True
    assert report["production_paths_touched"] == []
    assert report["verdict"] == "UNRESOLVED"
    assert report["phase"] == "MARKET_DATA_RECONNECTION_PHASE_1"


def test_report_is_json_serializable():
    report = build_report(
        _session(),
        TargetCoordinate(expiry="1Y"),
        None,
        (_record(),),
        generated_at="2026-01-01T00:00:00Z",
    )
    json.dumps(report)


# --------------------------------------------------------------------------
# CLI behaviour
# --------------------------------------------------------------------------


def test_the_cli_starts_without_an_editable_install():
    """Guards the bootstrap at the top of the probe.

    `bloomberg_dapi_probe` (reused) imports `shiori_pricing_lab` at module
    level and has no bootstrap of its own. Without the probe's own `src/`
    bootstrap, running the CLI in a checkout that was never `pip install -e`'d
    dies on an ImportError traceback with exit 1, instead of reaching the
    diagnostics this file exists to print. `PYTHONPATH` is scrubbed so a
    developer's own environment cannot make this pass spuriously.
    """

    environment = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    completed = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "tools" / "bloomberg_swaption_vol_probe.py"),
            "--help",
        ],
        cwd=str(_REPO_ROOT),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    assert completed.returncode == 0


def test_main_reports_an_install_error_when_blpapi_is_missing(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "blpapi", None)

    assert main(["--session-only"]) == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_fails_closed_when_no_service_opens(monkeypatch, tmp_path, capsys):
    _install_fake_blpapi(monkeypatch, start=False)

    assert main(["--session-only", "--output-dir", str(tmp_path)]) == 1
    assert "no Bloomberg DAPI service could be opened" in capsys.readouterr().err


def test_session_only_writes_a_report_and_sends_no_value_request(monkeypatch, tmp_path, capsys):
    factory = _install_fake_blpapi(monkeypatch)

    exit_code = main(["--session-only", "--output-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "no --security/--field supplied" in captured.out
    assert (tmp_path / "bloomberg_swaption_vol_probe.json").exists()
    assert (tmp_path / "bloomberg_swaption_vol_probe.md").exists()
    # Only the service-introspection sessions ran; no ReferenceDataRequest was sent.
    assert all(not hasattr(session, "_sent") for session in factory.created)


def test_main_with_no_candidate_reports_that_it_requested_no_value(monkeypatch, tmp_path, capsys):
    _install_fake_blpapi(monkeypatch)

    exit_code = main(
        ["--skip-discovery", "--output-dir", str(tmp_path)]
    )

    assert exit_code == 0
    assert "never invents a ticker or a mnemonic" in capsys.readouterr().out
    report = json.loads((tmp_path / "bloomberg_swaption_vol_probe.json").read_text("utf-8"))
    assert report["value_probes"] == []
    assert report["authorization_status"] == AuthorizationStatus.SERVICE_LEVEL_ONLY.value


def test_discovery_failure_is_surfaced_as_a_warning_not_a_result(monkeypatch, tmp_path, capsys):
    _install_fake_blpapi(monkeypatch)
    monkeypatch.setattr(
        module,
        "hunt_vol_fields",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("apiflds unavailable")),
    )

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert "field discovery failed" in capsys.readouterr().err
    report = json.loads((tmp_path / "bloomberg_swaption_vol_probe.json").read_text("utf-8"))
    assert report["field_discovery"] is None
