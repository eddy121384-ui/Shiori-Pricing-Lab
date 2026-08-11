"""Tests for `tools/bloomberg_curve_list_probe.py` (Issue #165 round 6).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) `curveListRequest` is re-confirmed via this
run's own `discover_service` introspection before anything is sent -- never
assumed from a prior round; (2) exactly Eddy's schema-confirmed filter
(`currencyCode=USD`, `type=IRS`, `subtype=OIS`, `maxResults=0`) is sent,
nothing else; (3) each returned `CurveRecord` is parsed into its nine named
fields, with the wrapping results array's own element name discovered
structurally from a candidate list, never asserted, and the raw response
always captured as a fallback; (4) a record is flagged as a SOFR candidate
only via its own `curve`/`description` text, never a name-based guess;
(5) a record's own `curveid` literally equalling "490" is the only thing
that ever marks a Bloomberg-supplied tie to terminal Curve #490 -- never
inferred any other way; (6) zero, one, and multiple SOFR candidates each
produce the right `identity_conclusion` and never promote a guess; (7) the
CLI's one-command contract.

No `blpapi` faking needed for the orchestration tests:
`run_curve_list_probe` takes `discover_service_fn`/`send_request` as
injectable callables, mirroring the seams the round 2/4/5 tools already
established.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_list_probe as module  # noqa: E402
from bloomberg_curve_discovery_probe import (  # noqa: E402
    OperationEvidence,
    ServiceEvidence,
)
from bloomberg_curve_list_probe import (  # noqa: E402
    CURVE_RECORD_FIELDS,
    build_report,
    main,
    render_json,
    render_markdown,
    run_curve_list_probe,
    write_report,
)

_INSTRUMENTS = "//blp/instruments"


def _service(*, opened=True, open_error=None, operation_confirmed=True) -> ServiceEvidence:
    operations = (
        (OperationEvidence("curveListRequest", None, None, (), ()),)
        if operation_confirmed
        else (OperationEvidence("SomeOtherRequest", None, None, (), ()),)
    )
    return ServiceEvidence(
        service_uri=_INSTRUMENTS,
        opened=opened,
        open_error=open_error,
        full_schema_dump="schema",
        operations=operations if opened else (),
        operation_list_error=None,
    )


# --- fake fieldData-shaped response elements -----------------------------------------


class _FakeElement:
    def __init__(self, sub_elements=None, values=None, string_value=None):
        self._sub = sub_elements or {}
        self._values = values
        self._string_value = string_value

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
        return self._string_value or "<element>"


def _curve_record(**fields) -> _FakeElement:
    sub = {
        name: _FakeElement(string_value=value)
        for name, value in fields.items()
        if value is not None
    }
    return _FakeElement(sub_elements=sub, string_value=f"<CurveRecord {fields.get('curve')}>")


def _results_message(array_element_name, records) -> _FakeElement:
    return _FakeElement(
        sub_elements={array_element_name: _FakeElement(values=list(records))},
        string_value="<message with results>",
    )


def _plain_message(text) -> _FakeElement:
    return _FakeElement(string_value=text)


class _Request:
    def __init__(self):
        self.set_calls: dict[str, object] = {}

    def set(self, name, value):
        self.set_calls[name] = value


# --- capability gate --------------------------------------------------------------


def test_run_reports_blocker_when_service_not_opened():
    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(opened=False, open_error="could not open"),
        send_request=lambda **_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert report.operation_confirmed is False
    assert report.request_status == "not_attempted"
    assert report.identity_conclusion == "SERVICE_NOT_OPENED"
    assert "could not open" in report.blocker_note


def test_run_reports_blocker_when_operation_not_confirmed():
    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(operation_confirmed=False),
        send_request=lambda **_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert report.operation_confirmed is False
    assert report.identity_conclusion == "OPERATION_NOT_CONFIRMED"
    assert "curveListRequest" in report.blocker_note


# --- request construction -----------------------------------------------------------


def test_run_sends_exactly_the_confirmed_filter():
    captured = {}

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        assert service_uri == _INSTRUMENTS
        assert request_name == "curveListRequest"
        request = _Request()
        configure(request)
        captured["set_calls"] = request.set_calls
        collect(_results_message("results", []))

    run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert captured["set_calls"] == {
        "currencyCode": "USD",
        "type": "IRS",
        "subtype": "OIS",
        "maxResults": 0,
    }


# --- parsing: candidate array names, structural field extraction --------------------


def test_run_parses_records_from_a_matching_candidate_array_name():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(
            _results_message(
                "curveData",
                [
                    _curve_record(
                        curve="USD SOFR OIS",
                        description="USD SOFR Overnight Index Swap Curve",
                        country="US",
                        currency="USD",
                        curveid="490",
                        type="IRS",
                        subtype="OIS",
                        publisher="BFIX",
                        bbgid="BBG000001",
                    )
                ],
            )
        )

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert report.results_array_element_name == "curveData"
    assert len(report.records) == 1
    record = report.records[0]
    assert record.curve == "USD SOFR OIS"
    assert record.curveid == "490"
    assert record.mentions_sofr is True
    assert record.curveid_matches_terminal_490 is True


def test_every_candidate_array_name_is_tried():
    for candidate_name in module._CANDIDATE_RESULTS_ELEMENT_NAMES:

        def _fake_send_request(
            *, service_uri, request_name, configure, collect, context, _name=candidate_name
        ):
            configure(_Request())
            collect(_results_message(_name, [_curve_record(curve="X", description="Y")]))

        report = run_curve_list_probe(
            discover_service_fn=lambda uri: _service(),
            send_request=_fake_send_request,
        )

        assert report.results_array_element_name == candidate_name, candidate_name
        assert len(report.records) == 1


def test_run_extracts_all_nine_confirmed_fields():
    fields = {name: f"value_{name}" for name in CURVE_RECORD_FIELDS}

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(_results_message("results", [_curve_record(**fields)]))

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    record = report.records[0]
    for name in CURVE_RECORD_FIELDS:
        assert getattr(record, name) == f"value_{name}"


def test_run_falls_back_to_raw_dump_when_no_candidate_array_name_matches():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(_plain_message("totally unexpected response shape, no known array here"))

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert report.results_array_element_name is None
    assert report.records == ()
    assert report.identity_conclusion == "NO_RECORDS_PARSED"
    assert "totally unexpected response shape" in report.raw_response_dump


# --- SOFR candidate flagging and identity conclusions --------------------------------


def test_sofr_flag_checks_curve_and_description_only():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(
            _results_message(
                "results",
                [
                    _curve_record(curve="USD SOFR", description="d1"),
                    _curve_record(curve="c2", description="mentions SOFR here"),
                    _curve_record(curve="c3", description="d3", publisher="SOFR Publishing"),
                ],
            )
        )

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    sofr_curves = {r.curve for r in report.sofr_candidates}
    assert sofr_curves == {"USD SOFR", "c2"}


def test_single_sofr_candidate_without_490_match():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(
            _results_message(
                "results",
                [_curve_record(curve="USD SOFR OIS", description="d", curveid="123")],
            )
        )

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert report.identity_conclusion == "SINGLE_SOFR_CANDIDATE_UNCONFIRMED_490"
    assert len(report.sofr_candidates) == 1
    assert report.sofr_candidates[0].curveid_matches_terminal_490 is False
    assert "does NOT assert" in report.blocker_note


def test_single_sofr_candidate_with_490_match():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(
            _results_message(
                "results",
                [_curve_record(curve="USD SOFR OIS", description="d", curveid="490")],
            )
        )

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert report.identity_conclusion == "SINGLE_SOFR_CANDIDATE_CURVEID_490_MATCH"
    assert "Bloomberg's own response ties" in report.blocker_note


def test_multiple_sofr_candidates_are_all_reported_and_none_promoted():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(
            _results_message(
                "results",
                [
                    _curve_record(curve="USD SOFR OIS A", description="d", curveid="111"),
                    _curve_record(curve="USD SOFR OIS B", description="d", curveid="222"),
                ],
            )
        )

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert report.identity_conclusion == "MULTIPLE_SOFR_CANDIDATES"
    assert len(report.sofr_candidates) == 2
    assert "none promoted" in report.blocker_note


def test_no_sofr_candidates_among_returned_records():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(_results_message("results", [_curve_record(curve="USD LIBOR", description="d")]))

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert report.identity_conclusion == "NO_SOFR_CANDIDATES"
    assert report.sofr_candidates == ()
    assert len(report.records) == 1


def test_request_failure_is_reported_without_records():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        raise RuntimeError("Bloomberg DAPI request timed out")

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert report.request_status == "error"
    assert report.identity_conclusion == "REQUEST_FAILED"
    assert "timed out" in report.request_error


def test_raw_dump_and_record_fields_are_sanitized():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(_Request())
        collect(
            _results_message(
                "results",
                [
                    _curve_record(
                        curve="USD SOFR OIS",
                        description="served from clientId=SYNTH-LEAK at localhost:8194",
                    )
                ],
            )
        )

    report = run_curve_list_probe(
        discover_service_fn=lambda uri: _service(),
        send_request=_fake_send_request,
    )

    assert "SYNTH-LEAK" not in report.records[0].description
    assert "localhost:8194" not in report.records[0].description


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.CurveListReport:
    record = module.CurveRecord(
        curve="USD SOFR OIS",
        description="USD SOFR Overnight Index Swap Curve",
        country="US",
        currency="USD",
        curveid="490",
        type="IRS",
        subtype="OIS",
        publisher="BFIX",
        bbgid="BBG000001",
        raw_record_dump="<CurveRecord USD SOFR OIS>",
        mentions_sofr=True,
        curveid_matches_terminal_490=True,
    )
    return module.CurveListReport(
        generated_at="2026-08-10T00:00:00+00:00",
        operation_confirmed=True,
        service_open_error=None,
        request_status="sent",
        request_error=None,
        results_array_element_name="results",
        records=(record,),
        raw_response_dump="<message with results>",
        sofr_candidates=(record,),
        identity_conclusion="SINGLE_SOFR_CANDIDATE_CURVEID_490_MATCH",
        blocker_note="Bloomberg's own response ties this candidate to terminal Curve #490.",
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "USD SOFR OIS" in markdown
    assert "curveid == terminal 490" in markdown
    assert "SINGLE_SOFR_CANDIDATE_CURVEID_490_MATCH" in markdown
    assert "USD SOFR OIS" in as_json


def test_write_report_writes_both_files(tmp_path):
    data = build_report(_minimal_report())

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "USD SOFR OIS" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error():
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "run_curve_list_probe", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(module, "run_curve_list_probe", lambda: _minimal_report())

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Identity conclusion: SINGLE_SOFR_CANDIDATE_CURVEID_490_MATCH" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
