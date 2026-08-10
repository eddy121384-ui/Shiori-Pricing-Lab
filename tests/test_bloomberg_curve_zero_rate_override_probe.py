"""Tests for `tools/bloomberg_curve_zero_rate_override_probe.py` (Issue #165 round 8).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) the security identifier is never derived --
always exactly what the caller/CLI supplied, defaulting to Eddy's own
literal "USD Curncy" (round 7 already proved "S490" is BAD_SEC); (2)
exactly one `ReferenceDataRequest` is sent for `SW174` only (never
`SW173`) with `SW569` sent as an override via the same
`getElement("overrides")`/`appendElement()`/`setElement` pattern
`bloomberg_dapi_probe.probe_fields` already uses; (3) a rejected security
stops with Bloomberg's own error text verbatim, never a guessed fallback;
(4) a field/override error surfaces verbatim regardless of whether
Bloomberg keys it under `SW569` or `SW174`; (5) bulk rows are parsed via
the same generic `Element.elements()` flattening round 7 already built and
tested (reused, not reimplemented), with every row's raw dump always
captured; (6) every extracted/raw text value is sanitized; (7) the CLI's
one-command contract.

No `blpapi` faking needed for the orchestration tests: `run_zero_rate_
override_probe` takes `send_request` as an injectable callable, mirroring
the seams the round 2/4/5/6/7 tools already established.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_zero_rate_override_probe as module  # noqa: E402
from bloomberg_curve_zero_rate_override_probe import (  # noqa: E402
    DEFAULT_OVERRIDE_VALUE,
    DEFAULT_SECURITY,
    FIELD,
    OVERRIDE_FIELD,
    build_report,
    main,
    render_json,
    render_markdown,
    run_zero_rate_override_probe,
    write_report,
)

_REFDATA = "//blp/refdata"


# --- fake ReferenceDataRequest response elements --------------------------------------


class _FakeNamedElement:
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


class _FakeOverrideElement:
    def __init__(self):
        self.values: dict[str, str] = {}

    def setElement(self, name, value):
        self.values[name] = value


class _FakeOverridesElement:
    def __init__(self):
        self.elements_appended: list[_FakeOverrideElement] = []

    def appendElement(self):
        element = _FakeOverrideElement()
        self.elements_appended.append(element)
        return element


class _FakeRequest:
    def __init__(self):
        self.securities: list[str] = []
        self.fields: list[str] = []
        self.overrides = _FakeOverridesElement()

    def append(self, name, value):
        if name == "securities":
            self.securities.append(value)
        elif name == "fields":
            self.fields.append(value)

    def getElement(self, name):
        assert name == "overrides"
        return self.overrides


def _bulk_row(*, raw="<row>", **named_fields) -> _FakeElement:
    elements_list = [_FakeNamedElement(name, value) for name, value in named_fields.items()]
    return _FakeElement(elements_list=elements_list, string_value=raw)


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
        request = _FakeRequest()
        configure(request)
        collect(message)
        return request

    return _fake


# --- input validation ---------------------------------------------------------------


def test_run_rejects_a_blank_security():
    with pytest.raises(ValueError, match="non-blank"):
        run_zero_rate_override_probe("", send_request=lambda **_: None)


def test_run_rejects_a_non_string_security():
    with pytest.raises(ValueError, match="non-blank"):
        run_zero_rate_override_probe(None, send_request=lambda **_: None)


def test_default_security_is_eddys_literal_usd_curncy():
    assert DEFAULT_SECURITY == "USD Curncy"


def test_defaults_match_confirmed_field_and_override():
    assert FIELD == "SW174"
    assert OVERRIDE_FIELD == "SW569"
    assert DEFAULT_OVERRIDE_VALUE == "Y"


# --- request construction -----------------------------------------------------------


def test_run_sends_only_sw174_never_sw173():
    captured = {}

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        captured["fields"] = request.fields
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_zero_rate_override_probe("USD Curncy", send_request=_fake_send_request)

    assert captured["fields"] == ["SW174"]
    assert "SW173" not in captured["fields"]


def test_run_sends_the_sw569_override_with_the_given_value():
    captured = {}

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        captured["overrides"] = [e.values for e in request.overrides.elements_appended]
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_zero_rate_override_probe("USD Curncy", override_value="Y", send_request=_fake_send_request)

    assert captured["overrides"] == [{"fieldId": "SW569", "value": "Y"}]


def test_run_sends_the_security_verbatim_never_a_guessed_variant():
    captured = {}

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        captured["securities"] = request.securities
        collect(
            _message_with_records(
                [_security_data_record(security_error="[BAD_SEC] Unknown/Invalid security")]
            )
        )

    run_zero_rate_override_probe("USD Curncy", send_request=_fake_send_request)

    assert captured["securities"] == ["USD Curncy"]


# --- request/response-envelope handling -----------------------------------------


def test_run_reports_a_request_failure():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        raise RuntimeError("Bloomberg DAPI request timed out")

    report = run_zero_rate_override_probe("USD Curncy", send_request=_fake_send_request)

    assert report.request_status == "error"
    assert "timed out" in report.request_error
    assert "timed out" in report.blocker_note


def test_run_reports_zero_records_as_a_record_count_error():
    report = run_zero_rate_override_probe(
        "USD Curncy", send_request=_send_request_returning(_message_with_records([]))
    )

    assert report.record_count_error is not None
    assert "0 securityData record" in report.record_count_error


def test_run_reports_a_rejected_security_verbatim_with_no_fallback():
    report = run_zero_rate_override_probe(
        "USD Curncy",
        send_request=_send_request_returning(
            _message_with_records(
                [_security_data_record(security_error="<securityError category=BAD_SEC>")]
            )
        ),
    )

    assert report.security_error is not None
    assert "BAD_SEC" in report.security_error
    assert report.field_status is None
    assert "No fallback identifier is guessed" in report.blocker_note


# --- field/override error handling -----------------------------------------------


def test_run_reports_an_override_error_keyed_under_the_override_field():
    report = run_zero_rate_override_probe(
        "USD Curncy",
        send_request=_send_request_returning(
            _message_with_records(
                [
                    _security_data_record(
                        field_data_subs={},
                        field_exceptions=[
                            _field_exception("SW569", "[INVALID_OVERRIDE] value not accepted")
                        ],
                    )
                ]
            )
        ),
    )

    assert report.override_error is not None
    assert "INVALID_OVERRIDE" in report.override_error
    assert report.field_status == "field_exception"
    assert report.rows == ()


def test_run_reports_an_override_error_keyed_under_the_target_field():
    # Bloomberg may key a rejected override under the field it was applied
    # to (SW174) rather than the override's own id -- both are checked.
    report = run_zero_rate_override_probe(
        "USD Curncy",
        send_request=_send_request_returning(
            _message_with_records(
                [
                    _security_data_record(
                        field_data_subs={},
                        field_exceptions=[_field_exception("SW174", "[BAD_FLD] not applicable")],
                    )
                ]
            )
        ),
    )

    assert report.override_error is not None
    assert "BAD_FLD" in report.override_error


def test_run_reports_absent_when_neither_field_nor_override_errors_exist():
    report = run_zero_rate_override_probe(
        "USD Curncy",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs={})])
        ),
    )

    assert report.override_error is None
    assert report.field_status == "absent"


# --- bulk row parsing (reused from round 7) ---------------------------------------


def test_run_parses_bulk_rows_with_generically_flattened_fields():
    field_data_subs = {
        "SW174": _FakeElement(
            values=[
                _bulk_row(Date="2026-08-10", ZeroRate="0.0325", raw="<row 0>"),
                _bulk_row(Date="2027-08-10", ZeroRate="0.0341", raw="<row 1>"),
            ]
        ),
    }
    report = run_zero_rate_override_probe(
        "USD Curncy",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs=field_data_subs)])
        ),
    )

    assert report.field_status == "returned_bulk"
    assert report.row_count == 2
    assert report.rows[0].fields == {"Date": "2026-08-10", "ZeroRate": "0.0325"}
    assert report.rows[1].fields == {"Date": "2027-08-10", "ZeroRate": "0.0341"}
    assert "asserts no match" in report.blocker_note


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
    report = run_zero_rate_override_probe(
        "USD Curncy",
        send_request=_send_request_returning(
            _message_with_records([_security_data_record(field_data_subs=field_data_subs)])
        ),
    )

    row = report.rows[0]
    assert "SYNTH-LEAK" not in row.fields["Note"]
    assert "localhost:8194" not in row.fields["Note"]
    assert "SYNTH-LEAK" not in row.raw_row_dump


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.ZeroRateOverrideProbeReport:
    row = module.BulkFieldRow(
        index=0, fields={"Date": "2026-08-10", "ZeroRate": "0.0325"}, raw_row_dump="<row 0>"
    )
    return module.ZeroRateOverrideProbeReport(
        generated_at="2026-08-10T00:00:00+00:00",
        security="USD Curncy",
        override_field="SW569",
        override_value="Y",
        request_status="sent",
        request_error=None,
        record_count_error=None,
        security_error=None,
        override_error=None,
        field_status="returned_bulk",
        row_count=1,
        rows=(row,),
        scalar_value=None,
        raw_response_dump="<message with securityData>",
        blocker_note="SW174 returned 1 bulk row(s) below.",
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "SW174" in markdown
    assert "SW569" in markdown
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
    def _raise_import_error(security, override_value):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "run_zero_rate_override_probe", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_uses_defaults_when_not_supplied(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(security, override_value):
        seen["security"] = security
        seen["override_value"] = override_value
        return _minimal_report()

    monkeypatch.setattr(module, "run_zero_rate_override_probe", _fake_run)

    main(["--output-dir", str(tmp_path / "out")])

    assert seen["security"] == DEFAULT_SECURITY
    assert seen["override_value"] == DEFAULT_OVERRIDE_VALUE


def test_main_accepts_explicit_overrides(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(security, override_value):
        seen["security"] = security
        seen["override_value"] = override_value
        return _minimal_report()

    monkeypatch.setattr(module, "run_zero_rate_override_probe", _fake_run)

    main(
        [
            "--security",
            "USD Curncy 2",
            "--override-value",
            "N",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert seen["security"] == "USD Curncy 2"
    assert seen["override_value"] == "N"


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        module, "run_zero_rate_override_probe", lambda security, override_value: _minimal_report()
    )

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Field status: returned_bulk (1 rows)" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
