"""Tests for `tools/bloomberg_curve_discovery_probe.py` (Issue #165 follow-up).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) service/operation schema introspection reports
Bloomberg's own `service.toString()` dump and a best-effort structured
per-operation walk, isolating one operation's introspection failure from the
rest; (2) a top-level STRING request element is only ever reported as a
search candidate when the operation's own schema exposes one -- never a
hardcoded name; (3) the field-search attempt step only fires for a
discovered candidate, records Bloomberg's raw response verbatim, and isolates
one attempt's failure from the others; (4) the "no search capability found"
limitation is reported explicitly rather than silently falling back to a
guessed mnemonic; (5) the CLI writes a report and handles a missing `blpapi`
the same way the sibling probe tools do.

No network access, no real `blpapi` for the service-introspection tests:
Bloomberg is faked with minimal stand-in objects mirroring only the small
slice of the real `blpapi` Session/Service/Operation/SchemaElementDefinition/
SchemaTypeDefinition surface this module calls, injected via
`sys.modules["blpapi"]` -- same technique as `tests/test_bloomberg_dapi_probe.py`,
kept as its own slim copy here. The field-search-attempt tests instead use
`attempt_field_search`'s own `send_request` injection seam, so they need no
`blpapi` fake at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_discovery_probe as module  # noqa: E402
from bloomberg_curve_discovery_probe import (  # noqa: E402
    OperationEvidence,
    ServiceEvidence,
    attempt_field_search,
    build_report,
    discover_service,
    main,
    render_json,
    render_markdown,
    run_discovery,
    write_report,
)

# --- fake blpapi (service/schema introspection only) ------------------------------


class _FakeDataType:
    STRING = "STRING"
    INT32 = "INT32"
    SEQUENCE = "SEQUENCE"


class _FakeSchemaTypeDefinition:
    def __init__(self, datatype, element_defs=()):
        self._datatype = datatype
        self._element_defs = list(element_defs)

    def datatype(self):
        return self._datatype

    def numElementDefinitions(self):
        return len(self._element_defs)

    def getElementDefinition(self, index):
        return self._element_defs[index]

    def __str__(self):
        return f"<type {self._datatype}>"


class _FakeSchemaElementDefinition:
    def __init__(self, name, type_definition, *, raise_on_name=False):
        self._name = name
        self._type_definition = type_definition
        self._raise_on_name = raise_on_name

    def name(self):
        if self._raise_on_name:
            raise RuntimeError("element name unavailable on this blpapi build")
        return self._name

    def typeDefinition(self):
        return self._type_definition

    def __str__(self):
        return f"<element {self._name}>"


class _FakeOperation:
    def __init__(
        self,
        name,
        *,
        description="an operation",
        request_definition=None,
        response_definitions=(),
        raise_on_name=False,
        raise_on_request_definition=False,
    ):
        self._name = name
        self._description = description
        self._request_definition = request_definition
        self._response_definitions = list(response_definitions)
        self._raise_on_name = raise_on_name
        self._raise_on_request_definition = raise_on_request_definition

    def name(self):
        if self._raise_on_name:
            raise RuntimeError("operation name unavailable")
        return self._name

    def description(self):
        return self._description

    def requestDefinition(self):
        if self._raise_on_request_definition:
            raise RuntimeError("request definition unavailable")
        return self._request_definition

    def numResponseDefinitions(self):
        return len(self._response_definitions)

    def getResponseDefinitionAt(self, index):
        return self._response_definitions[index]


class _FakeService:
    def __init__(self, *, operations=(), schema_text="FULL SERVICE SCHEMA"):
        self._operations = list(operations)
        self._schema_text = schema_text

    def numOperations(self):
        return len(self._operations)

    def getOperation(self, index):
        return self._operations[index]

    def __str__(self):
        return self._schema_text


class _FakeSession:
    def __init__(self, options, *, services, start_ok=True):
        self.options = options
        self._services = services
        self.start_ok = start_ok
        self.stopped = False

    def start(self):
        return self.start_ok

    def openService(self, uri):
        return uri in self._services and self._services[uri] is not None

    def getService(self, uri):
        return self._services[uri]

    def stop(self):
        self.stopped = True


class _FakeSessionOptions:
    def setServerHost(self, host):
        pass

    def setServerPort(self, port):
        pass


def _install_fake_blpapi(monkeypatch, *, services, start_ok=True):
    holder = {}

    def _session_factory(options):
        session = _FakeSession(options, services=services, start_ok=start_ok)
        holder["session"] = session
        return session

    fake_module = type(sys)("blpapi")
    fake_module.SessionOptions = _FakeSessionOptions
    fake_module.Session = _session_factory
    fake_module.DataType = _FakeDataType
    monkeypatch.setitem(sys.modules, "blpapi", fake_module)
    return holder


_APIFLDS = "//blp/apiflds"
_REFDATA = "//blp/refdata"


# --- discover_service --------------------------------------------------------------


def test_discover_service_reports_open_failure(monkeypatch):
    _install_fake_blpapi(monkeypatch, services={})

    evidence = discover_service(_APIFLDS)

    assert evidence.opened is False
    assert "failed to open service" in evidence.open_error
    assert evidence.operations == ()


def test_discover_service_reports_session_start_failure(monkeypatch):
    _install_fake_blpapi(monkeypatch, services={_APIFLDS: _FakeService()}, start_ok=False)

    evidence = discover_service(_APIFLDS)

    assert evidence.opened is False
    assert "failed to start" in evidence.open_error


def test_discover_service_captures_full_schema_dump_with_no_operations(monkeypatch):
    _install_fake_blpapi(
        monkeypatch,
        services={_APIFLDS: _FakeService(schema_text="THE FULL SCHEMA")},
    )

    evidence = discover_service(_APIFLDS)

    assert evidence.opened is True
    assert evidence.full_schema_dump == "THE FULL SCHEMA"
    assert evidence.operations == ()
    assert evidence.operation_list_error is None


def test_discover_service_finds_candidate_string_elements_from_the_schema_itself(monkeypatch):
    request_definition = _FakeSchemaElementDefinition(
        "FieldSearchRequest",
        _FakeSchemaTypeDefinition(
            _FakeDataType.SEQUENCE,
            element_defs=[
                _FakeSchemaElementDefinition(
                    "searchSpec", _FakeSchemaTypeDefinition(_FakeDataType.STRING)
                ),
                _FakeSchemaElementDefinition(
                    "maxResults", _FakeSchemaTypeDefinition(_FakeDataType.INT32)
                ),
            ],
        ),
    )
    operation = _FakeOperation("FieldSearchRequest", request_definition=request_definition)
    _install_fake_blpapi(monkeypatch, services={_APIFLDS: _FakeService(operations=[operation])})

    evidence = discover_service(_APIFLDS)

    assert len(evidence.operations) == 1
    op = evidence.operations[0]
    assert op.name == "FieldSearchRequest"
    assert op.candidate_string_elements == ("searchSpec",)
    assert op.introspection_error is None


def test_discover_service_reports_no_candidates_when_no_string_element_exists(monkeypatch):
    request_definition = _FakeSchemaElementDefinition(
        "ReferenceDataRequest",
        _FakeSchemaTypeDefinition(
            _FakeDataType.SEQUENCE,
            element_defs=[
                _FakeSchemaElementDefinition(
                    "count", _FakeSchemaTypeDefinition(_FakeDataType.INT32)
                ),
            ],
        ),
    )
    operation = _FakeOperation("ReferenceDataRequest", request_definition=request_definition)
    _install_fake_blpapi(monkeypatch, services={_REFDATA: _FakeService(operations=[operation])})

    evidence = discover_service(_REFDATA)

    assert evidence.operations[0].candidate_string_elements == ()


def test_discover_service_isolates_one_operations_introspection_failure(monkeypatch):
    good_request_definition = _FakeSchemaElementDefinition(
        "GoodRequest", _FakeSchemaTypeDefinition(_FakeDataType.SEQUENCE, element_defs=[])
    )
    good_operation = _FakeOperation("GoodRequest", request_definition=good_request_definition)
    bad_operation = _FakeOperation("BadRequest", raise_on_name=True)
    _install_fake_blpapi(
        monkeypatch,
        services={_APIFLDS: _FakeService(operations=[bad_operation, good_operation])},
    )

    evidence = discover_service(_APIFLDS)

    assert len(evidence.operations) == 2
    assert evidence.operations[0].name == "<unknown>"
    assert evidence.operations[0].introspection_error is not None
    assert evidence.operations[1].name == "GoodRequest"
    assert evidence.operations[1].introspection_error is None


def test_discover_service_sanitizes_the_schema_dump():
    # sanitize_external_text is reused verbatim, so a host:port leak in an
    # error/schema string would already be scrubbed the same way it is for
    # the other Bloomberg probe tools -- this just proves the wiring calls it.
    from bloomberg_input_sourcing_probe import sanitize_external_text

    sanitized = sanitize_external_text("connected to internal-host:8194")
    assert sanitized == "connected to <redacted host>"


# --- run_discovery: limitation reporting --------------------------------------------


def test_run_discovery_flags_missing_search_capability(monkeypatch):
    request_definition = _FakeSchemaElementDefinition(
        "FieldInfoRequest",
        _FakeSchemaTypeDefinition(
            _FakeDataType.SEQUENCE,
            element_defs=[
                _FakeSchemaElementDefinition(
                    "returnFieldDocumentation", _FakeSchemaTypeDefinition(_FakeDataType.INT32)
                ),
            ],
        ),
    )
    operation = _FakeOperation("FieldInfoRequest", request_definition=request_definition)
    _install_fake_blpapi(
        monkeypatch,
        services={
            _APIFLDS: _FakeService(operations=[operation]),
            _REFDATA: _FakeService(),
        },
    )

    report = run_discovery(("stripped curve",))

    assert report.search_capability_found is False
    assert report.limitation_note is not None
    assert "did not report fieldsearchrequest.searchspec" in report.limitation_note.lower()
    assert report.search_attempts == ()


def test_run_discovery_reports_apiflds_open_failure_as_the_limitation(monkeypatch):
    _install_fake_blpapi(monkeypatch, services={_REFDATA: _FakeService()})

    report = run_discovery(("stripped curve",))

    assert report.apiflds.opened is False
    assert "could not be opened" in report.limitation_note


def test_run_discovery_finds_capability_and_attempts_search(monkeypatch):
    request_definition = _FakeSchemaElementDefinition(
        "FieldSearchRequest",
        _FakeSchemaTypeDefinition(
            _FakeDataType.SEQUENCE,
            element_defs=[
                _FakeSchemaElementDefinition(
                    "searchSpec", _FakeSchemaTypeDefinition(_FakeDataType.STRING)
                ),
            ],
        ),
    )
    operation = _FakeOperation("FieldSearchRequest", request_definition=request_definition)
    apiflds_service = _FakeService(operations=[operation])
    _install_fake_blpapi(
        monkeypatch,
        services={_APIFLDS: apiflds_service, _REFDATA: _FakeService()},
    )
    # discover_service (called first, inside run_discovery) only opens the
    # service and never sends a request, so the plain fake Session above is
    # enough for it. attempt_field_search (called next, on the same
    # discovered evidence) goes through the real `_send_request`, which does
    # send a request and run its event loop -- so this one apiflds service
    # object additionally grows the request-sending surface `_send_request`
    # needs, and the Session factory is swapped for one whose `nextEvent`/
    # `sendRequest` satisfy that loop, matching this test's single fake
    # session/service pair used for both calls.
    module_blpapi = sys.modules["blpapi"]

    class _EventType:
        TIMEOUT = "TIMEOUT"
        PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
        RESPONSE = "RESPONSE"

    class _FakeMessage:
        def hasElement(self, name):
            return False

        def __str__(self):
            return "<candidate field mnemonic evidence>"

    class _FakeEvent:
        def eventType(self):
            return _EventType.RESPONSE

        def __iter__(self):
            return iter([_FakeMessage()])

    class _FakeSearchRequest:
        def __init__(self):
            self.set_calls = {}

        def set(self, name, value):
            self.set_calls[name] = value

    apiflds_service.createRequest = lambda name: _FakeSearchRequest()
    module_blpapi.Event = _EventType

    def _session_factory(options):
        session = _FakeSession(
            options, services={_APIFLDS: apiflds_service, _REFDATA: _FakeService()}
        )
        session.nextEvent = lambda timeout_ms: _FakeEvent()
        session.sendRequest = lambda request: None
        return session

    monkeypatch.setattr(module_blpapi, "Session", _session_factory)

    report = run_discovery(("stripped curve",))

    assert report.search_capability_found is True
    assert len(report.search_attempts) == 1
    attempt = report.search_attempts[0]
    assert attempt.operation_name == "FieldSearchRequest"
    assert attempt.request_element_used == "searchSpec"
    assert attempt.status == "sent"
    assert attempt.raw_response_dump == "<candidate field mnemonic evidence>"


# --- attempt_field_search (send_request injection seam) -----------------------------


def _service_evidence(*operations: OperationEvidence, opened: bool = True) -> ServiceEvidence:
    return ServiceEvidence(
        service_uri=_APIFLDS,
        opened=opened,
        open_error=None,
        full_schema_dump="schema",
        operations=operations,
        operation_list_error=None,
    )


def test_attempt_field_search_skips_when_service_did_not_open():
    evidence = _service_evidence(opened=False)

    attempts = attempt_field_search(evidence, ("stripped curve",), send_request=lambda **_: None)

    assert attempts == ()


def test_attempt_field_search_skips_operations_with_no_candidate_element():
    evidence = _service_evidence(
        OperationEvidence(
            name="ReferenceDataRequest",
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=(),
        )
    )

    attempts = attempt_field_search(evidence, ("stripped curve",), send_request=lambda **_: None)

    assert attempts == ()


# --- Issue #165 round 3: narrowed to the one proven (operation, element) pair ------


def test_attempt_field_search_skips_a_string_element_on_an_unproven_operation_name():
    # A different operation happening to expose a top-level STRING element
    # called "searchSpec" must not be treated as search capability -- round
    # 2's blind "any STRING element" heuristic would have fired here; round
    # 3 requires the operation name itself to be the proven one too.
    evidence = _service_evidence(
        OperationEvidence(
            name="SomeOtherRequest",
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=("searchSpec",),
        )
    )

    attempts = attempt_field_search(evidence, ("stripped curve",), send_request=lambda **_: None)

    assert attempts == ()


def test_attempt_field_search_skips_an_unproven_element_name_on_the_proven_operation():
    # FieldSearchRequest with a different string element name than the
    # proven "searchSpec" must not be attempted either -- both halves of the
    # pair must match what workstation evidence actually confirmed.
    evidence = _service_evidence(
        OperationEvidence(
            name="FieldSearchRequest",
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=("someOtherStringField",),
        )
    )

    attempts = attempt_field_search(evidence, ("stripped curve",), send_request=lambda **_: None)

    assert attempts == ()


def test_run_discovery_search_capability_requires_the_exact_proven_pair(monkeypatch):
    # A schema exposing a STRING element under a different name must not be
    # reported as search_capability_found, even though round 2 would have.
    request_definition = _FakeSchemaElementDefinition(
        "FieldSearchRequest",
        _FakeSchemaTypeDefinition(
            _FakeDataType.SEQUENCE,
            element_defs=[
                _FakeSchemaElementDefinition(
                    "notTheProvenElement", _FakeSchemaTypeDefinition(_FakeDataType.STRING)
                ),
            ],
        ),
    )
    operation = _FakeOperation("FieldSearchRequest", request_definition=request_definition)
    _install_fake_blpapi(
        monkeypatch,
        services={
            _APIFLDS: _FakeService(operations=[operation]),
            _REFDATA: _FakeService(),
        },
    )

    report = run_discovery(("stripped curve",))

    assert report.search_capability_found is False
    assert report.search_attempts == ()


def test_attempt_field_search_uses_the_discovered_element_name(monkeypatch):
    calls = []

    class _Request:
        def __init__(self):
            self.set_calls = {}

        def set(self, name, value):
            self.set_calls[name] = value

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        calls.append((service_uri, request_name, context))
        request = _Request()
        configure(request)
        assert request.set_calls == {"searchSpec": "stripped curve"}
        collect("raw response text")

    evidence = _service_evidence(
        OperationEvidence(
            name="FieldSearchRequest",
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=("searchSpec",),
        )
    )

    attempts = attempt_field_search(evidence, ("stripped curve",), send_request=_fake_send_request)

    assert len(attempts) == 1
    assert attempts[0] == module.SearchAttempt(
        operation_name="FieldSearchRequest",
        request_element_used="searchSpec",
        term="stripped curve",
        status="sent",
        raw_response_dump="raw response text",
        error=None,
    )
    assert calls == [
        (
            module._APIFLDS_SERVICE,
            "FieldSearchRequest",
            "FieldSearchRequest(searchSpec='stripped curve')",
        )
    ]


def test_attempt_field_search_falls_back_to_append_for_a_repeated_element():
    class _Request:
        def __init__(self):
            self.set_should_fail = True
            self.appended = []

        def set(self, name, value):
            raise RuntimeError("element is repeated, use append")

        def append(self, name, value):
            self.appended.append((name, value))

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _Request()
        configure(request)
        assert request.appended == [("searchSpec", "zero curve")]
        collect("ok")

    evidence = _service_evidence(
        OperationEvidence(
            name="FieldSearchRequest",
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=("searchSpec",),
        )
    )

    attempts = attempt_field_search(evidence, ("zero curve",), send_request=_fake_send_request)

    assert attempts[0].status == "sent"


def test_attempt_field_search_records_one_terms_failure_without_stopping_others():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        if "stripped curve" in context:
            raise RuntimeError("Bloomberg DAPI request timed out")
        collect("ok for the other term")

    evidence = _service_evidence(
        OperationEvidence(
            name="FieldSearchRequest",
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=("searchSpec",),
        )
    )

    attempts = attempt_field_search(
        evidence, ("stripped curve", "zero curve"), send_request=_fake_send_request
    )

    assert len(attempts) == 2
    by_term = {attempt.term: attempt for attempt in attempts}
    assert by_term["stripped curve"].status == "error"
    assert "timed out" in by_term["stripped curve"].error
    assert by_term["zero curve"].status == "sent"
    assert by_term["zero curve"].raw_response_dump == "ok for the other term"


def test_attempt_field_search_tries_every_term_for_every_candidate_element():
    seen_terms = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        seen_terms.append(context)
        collect("ok")

    evidence = _service_evidence(
        OperationEvidence(
            name="FieldSearchRequest",
            description=None,
            request_schema=None,
            response_schemas=(),
            candidate_string_elements=("searchSpec",),
        )
    )

    attempts = attempt_field_search(
        evidence, ("stripped curve", "zero curve", "SWDF"), send_request=_fake_send_request
    )

    assert len(attempts) == 3
    assert [attempt.term for attempt in attempts] == ["stripped curve", "zero curve", "SWDF"]


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.DiscoveryReport:
    apiflds = _service_evidence(
        OperationEvidence(
            name="FieldSearchRequest",
            description="searches fields",
            request_schema="request schema text",
            response_schemas=("response schema text",),
            candidate_string_elements=("searchSpec",),
        )
    )
    refdata = ServiceEvidence(
        service_uri=_REFDATA,
        opened=True,
        open_error=None,
        full_schema_dump="refdata schema",
        operations=(),
        operation_list_error=None,
    )
    return module.DiscoveryReport(
        generated_at="2026-08-10T00:00:00+00:00",
        apiflds=apiflds,
        refdata=refdata,
        search_terms=("stripped curve",),
        search_attempts=(
            module.SearchAttempt(
                operation_name="FieldSearchRequest",
                request_element_used="searchSpec",
                term="stripped curve",
                status="sent",
                raw_response_dump="candidate mnemonic evidence",
                error=None,
            ),
        ),
        search_capability_found=True,
        limitation_note=None,
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "FieldSearchRequest" in markdown
    assert "searchSpec" in markdown
    assert "candidate mnemonic evidence" in markdown
    assert "FieldSearchRequest" in as_json


def test_write_report_writes_both_files(tmp_path):
    data = build_report(_minimal_report())

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "FieldSearchRequest" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(search_terms):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "run_discovery", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(module, "run_discovery", lambda search_terms: _minimal_report())

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Field-search capability found: True" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()


def test_main_uses_default_search_terms_when_none_supplied(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _fake_run_discovery(search_terms):
        seen["search_terms"] = search_terms
        return _minimal_report()

    monkeypatch.setattr(module, "run_discovery", _fake_run_discovery)

    main([])

    assert seen["search_terms"] == module.DEFAULT_SEARCH_TERMS


def test_main_accepts_repeated_search_term_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _fake_run_discovery(search_terms):
        seen["search_terms"] = search_terms
        return _minimal_report()

    monkeypatch.setattr(module, "run_discovery", _fake_run_discovery)

    main(["--search-term", "custom curve term", "--search-term", "another term"])

    assert seen["search_terms"] == ("custom curve term", "another term")
