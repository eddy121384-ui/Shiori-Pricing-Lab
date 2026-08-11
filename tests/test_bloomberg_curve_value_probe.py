"""Tests for `tools/bloomberg_curve_value_probe.py` (Issue #165 round 7).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) the security identifier is never derived --
it is always exactly what the caller/CLI supplied, defaulting to Eddy's own
literal "S490"; (2) exactly one `ReferenceDataRequest` is sent for
`SW173`/`SW174` against that security, reusing the same
`securityData`/`securityError`/`fieldExceptions` validation shape
`data/bloomberg_bond_quote.py`'s production loaders already use; (3) a
rejected security (`securityError`, e.g. `BAD_SEC`) stops with Bloomberg's
own error text verbatim, never a guessed suffix/yellow-key fallback;
(4) bulk rows are parsed via generic `Element.elements()` flattening, with
each row's raw dump always captured regardless of whether the flatten
succeeds; (5) every extracted/raw text value is sanitized; (6) the CLI's
one-command contract, including its default `--security`.

No `blpapi` faking needed for the orchestration tests: `run_curve_value_
probe` takes `send_request` as an injectable callable, mirroring the seams
the round 2/4/5/6 tools already established.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_value_probe as module  # noqa: E402
from bloomberg_curve_value_probe import (  # noqa: E402
    BULK_FIELDS,
    DEFAULT_SECURITY,
    build_report,
    main,
    render_json,
    render_markdown,
    run_curve_value_probe,
    write_report,
)

_REFDATA = "//blp/refdata"


# --- fake ReferenceDataRequest response elements --------------------------------------


class _FakeNamedElement:
    """A leaf sub-element for generic `Element.elements()` iteration."""

    def __init__(self, name, value):
        self._name = name
        self._value = value

    def name(self):
        return self._name

    def getValueAsString(self):
        return self._value


class _FakeElement:
    def __init__(self, sub_elements=None, values=None, string_value=None, elements_list=None):
        self._sub = sub_elements or {}
        self._values = values
        self._string_value = string_value
        self._elements_list = elements_list

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

    def elements(self):
        if self._elements_list is None:
            raise RuntimeError("this element cannot be iterated generically")
        return iter(self._elements_list)

    def __str__(self):
        return self._string_value or "<element>"


def _bulk_row(*, raw="<row>", **named_fields) -> _FakeElement:
    elements_list = [_FakeNamedElement(name, value) for name, value in named_fields.items()]
    return _FakeElement(elements_list=elements_list, string_value=raw)


def _unflattenable_row(*, raw="<unparseable row>") -> _FakeElement:
    return _FakeElement(string_value=raw)


def _field_exception(field_id, message) -> _FakeElement:
    return _FakeElement(
        sub_elements={
            "fieldId": _FakeElement(string_value=field_id),
            "errorInfo": _FakeElement(string_value=message),
        },
        string_value=f"<fieldException {field_id}>",
    )


def _security_data_record(
    *, security_error=None, field_exceptions=None, field_data_subs=None
) -> _FakeElement:
    sub = {}
    if field_data_subs is not None:
        sub["fieldData"] = _FakeElement(sub_elements=field_data_subs)
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error)
    if field_exceptions is not None:
        sub["fieldExceptions"] = _FakeElement(values=list(field_exceptions))
    return _FakeElement(sub_elements=sub, string_value="<securityData record>")


def _message_with_records(records) -> _FakeElement:
    return _FakeElement(
        sub_elements={"securityData": _FakeElement(values=list(records))},
        string_value="<message with securityData>",
    )


def _send_request_returning(message):
    def _fake(*, service_uri, request_name, configure, collect, context):
        assert service_uri == _REFDATA
        assert request_name == "ReferenceDataRequest"

        class _Request:
            def __init__(self):
                self.securities = []
                self.fields = []

            def append(self, name, value):
                if name == "securities":
                    self.securities.append(value)
                elif name == "fields":
                    self.fields.append(value)

        request = _Request()
        configure(request)
        collect(message)
        return request

    return _fake


# --- input validation ---------------------------------------------------------------


def test_run_rejects_a_blank_security():
    with pytest.raises(ValueError, match="non-blank"):
        run_curve_value_probe("", send_request=lambda **_: None)


def test_run_rejects_a_non_string_security():
    with pytest.raises(ValueError, match="non-blank"):
        run_curve_value_probe(None, send_request=lambda **_: None)


# --- request construction -----------------------------------------------------------


def test_run_sends_the_security_and_both_confirmed_fields_verbatim():
    captured = {}

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        class _Request:
            def __init__(self):
                self.securities = []
                self.fields = []

            def append(self, name, value):
                getattr(self, name).append(value)

        request = _Request()
        configure(request)
        captured["securities"] = request.securities
        captured["fields"] = request.fields
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_curve_value_probe("S490", send_request=_fake_send_request)

    assert captured["securities"] == ["S490"]
    assert captured["fields"] == list(BULK_FIELDS)


def test_default_security_is_eddys_literal_s490():
    assert DEFAULT_SECURITY == "S490"


def test_run_never_retries_with_a_different_security(monkeypatch):
    calls = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        class _Request:
            def append(self, name, value):
                if name == "securities":
                    calls.append(value)

        configure(_Request())
        collect(
            _message_with_records(
                [_security_data_record(security_error="[BAD_SEC] Unknown/Invalid security")]
            )
        )

    run_curve_value_probe("S490", send_request=_fake_send_request)

    assert calls == ["S490"]


# --- request/response-envelope handling -----------------------------------------


def test_run_reports_a_request_failure():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        raise RuntimeError("Bloomberg DAPI request timed out")

    report = run_curve_value_probe("S490", send_request=_fake_send_request)

    assert report.request_status == "error"
    assert "timed out" in report.request_error
    assert "timed out" in report.blocker_note


def test_run_reports_zero_records_as_a_record_count_error():
    report = run_curve_value_probe(
        "S490", send_request=_send_request_returning(_message_with_records([]))
    )

    assert report.record_count_error is not None
    assert "0 securityData record" in report.record_count_error


def test_run_reports_more_than_one_record_as_a_record_count_error():
    records = [_security_data_record(field_data_subs={}), _security_data_record(field_data_subs={})]
    report = run_curve_value_probe(
        "S490", send_request=_send_request_returning(_message_with_records(records))
    )

    assert report.record_count_error is not None
    assert "2 securityData record" in report.record_count_error


def test_run_reports_a_rejected_security_verbatim_and_never_guesses_a_suffix():
    report = run_curve_value_probe(
        "S490",
        send_request=_send_request_returning(
            _message_with_records(
                [_security_data_record(security_error="<securityError category=BAD_SEC>")]
            )
        ),
    )

    assert report.security_error is not None
    assert "BAD_SEC" in report.security_error
    assert report.field_results == ()
    assert "No suffix or yellow-key variant is guessed" in report.blocker_note


# --- field-level outcomes -----------------------------------------------------------


def test_run_reports_a_field_exception_per_field():
    report = run_curve_value_probe(
        "S490",
        send_request=_send_request_returning(
            _message_with_records(
                [
                    _security_data_record(
                        field_data_subs={},
                        field_exceptions=[_field_exception("SW173", "[BAD_FLD] not applicable")],
                    )
                ]
            )
        ),
    )

    by_field = {r.field: r for r in report.field_results}
    assert by_field["SW173"].status == "field_exception"
    assert "BAD_FLD" in by_field["SW173"].detail
    assert by_field["SW174"].status == "absent"


def test_run_reports_bulk_rows_with_generically_flattened_fields():
    field_data_subs = {
        "SW174": _FakeElement(
            values=[
                _bulk_row(Date="2026-08-10", ZeroRate="0.0325", raw="<row 0>"),
                _bulk_row(Date="2027-08-10", ZeroRate="0.0341", raw="<row 1>"),
            ]
        ),
    }
    report = run_curve_value_probe(
        "S490",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs=field_data_subs)])
        ),
    )

    by_field = {r.field: r for r in report.field_results}
    assert by_field["SW174"].status == "returned_bulk"
    assert by_field["SW174"].row_count == 2
    assert by_field["SW174"].rows[0].fields == {"Date": "2026-08-10", "ZeroRate": "0.0325"}
    assert by_field["SW174"].rows[1].fields == {"Date": "2027-08-10", "ZeroRate": "0.0341"}
    assert by_field["SW173"].status == "absent"
    assert "compare Date/Zero Rate/Discount Factor" in report.blocker_note


def test_run_falls_back_to_raw_row_dump_when_generic_flatten_fails():
    field_data_subs = {
        "SW173": _FakeElement(values=[_unflattenable_row(raw="<row that cannot be flattened>")]),
    }
    report = run_curve_value_probe(
        "S490",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs=field_data_subs)])
        ),
    )

    by_field = {r.field: r for r in report.field_results}
    row = by_field["SW173"].rows[0]
    assert row.fields == {}
    assert row.raw_row_dump == "<row that cannot be flattened>"


def test_run_reports_a_present_non_bulk_field_as_a_scalar():
    field_data_subs = {"SW173": _FakeElement(values=[], string_value="unexpectedly scalar")}
    report = run_curve_value_probe(
        "S490",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs=field_data_subs)])
        ),
    )

    by_field = {r.field: r for r in report.field_results}
    assert by_field["SW173"].status == "present_non_bulk_or_empty"
    assert by_field["SW173"].scalar_value == "unexpectedly scalar"


def test_no_bulk_rows_at_all_produces_the_right_blocker():
    report = run_curve_value_probe(
        "S490",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs={})])
        ),
    )

    assert all(r.status == "absent" for r in report.field_results)
    assert "No fallback identifier is guessed" in report.blocker_note


# --- sanitization ---------------------------------------------------------------


def test_row_fields_and_raw_dump_are_sanitized():
    field_data_subs = {
        "SW174": _FakeElement(
            values=[
                _bulk_row(
                    Date="2026-08-10",
                    Note="from clientId=SYNTH-LEAK at localhost:8194",
                    raw="row from clientId=SYNTH-LEAK at localhost:8194",
                )
            ]
        ),
    }
    report = run_curve_value_probe(
        "S490",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs=field_data_subs)])
        ),
    )

    row = report.field_results[1].rows[0]
    assert "SYNTH-LEAK" not in row.fields["Note"]
    assert "localhost:8194" not in row.fields["Note"]
    assert "SYNTH-LEAK" not in row.raw_row_dump
    assert "localhost:8194" not in row.raw_row_dump


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.CurveValueProbeReport:
    row = module.BulkFieldRow(
        index=0, fields={"Date": "2026-08-10", "ZeroRate": "0.0325"}, raw_row_dump="<row 0>"
    )
    result_sw174 = module.BulkFieldResult(
        field="SW174",
        status="returned_bulk",
        row_count=1,
        rows=(row,),
        scalar_value=None,
        detail=None,
    )
    result_sw173 = module.BulkFieldResult(
        field="SW173", status="absent", row_count=0, rows=(), scalar_value=None, detail=None
    )
    return module.CurveValueProbeReport(
        generated_at="2026-08-10T00:00:00+00:00",
        security="S490",
        request_status="sent",
        request_error=None,
        record_count_error=None,
        security_error=None,
        field_results=(result_sw173, result_sw174),
        raw_response_dump="<message with securityData>",
        blocker_note="Bulk rows returned below for SW173/SW174.",
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "SW174" in markdown
    assert "2026-08-10" in markdown
    assert "0.0325" in markdown
    assert "SW174" in as_json


def test_write_report_writes_both_files(tmp_path):
    data = build_report(_minimal_report())

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "SW174" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_rejects_a_blank_security(capsys):
    exit_code = main(["--security", "   "])

    assert exit_code == 2
    assert "non-blank" in capsys.readouterr().err


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(security):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "run_curve_value_probe", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_uses_the_default_security_when_not_supplied(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(security):
        seen["security"] = security
        return _minimal_report()

    monkeypatch.setattr(module, "run_curve_value_probe", _fake_run)

    main(["--output-dir", str(tmp_path / "out")])

    assert seen["security"] == DEFAULT_SECURITY


def test_main_accepts_an_explicit_security_override(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(security):
        seen["security"] = security
        return _minimal_report()

    monkeypatch.setattr(module, "run_curve_value_probe", _fake_run)

    main(["--security", "S490 Curncy", "--output-dir", str(tmp_path / "out")])

    assert seen["security"] == "S490 Curncy"


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(module, "run_curve_value_probe", lambda security: _minimal_report())

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "SW174: returned_bulk (1 rows)" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
