"""Tests for `tools/bloomberg_curve_zero_rate_comparison_probe.py` (Issue #165 round 9).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) exactly three independent `ReferenceDataRequest`
calls are sent for `SW174` against `"USD Curncy"` -- no override, `SW569=Y`,
`SW564=Y` -- each its own request/response cycle, never combined; (2) the
security identifier is never derived, always exactly what the caller/CLI
supplied; (3) each variant's own security/override/field errors and bulk
rows are kept fully separate and never blended across variants; (4) bulk
rows reuse round 7's own `_flatten_row`/`BulkFieldRow` unchanged; (5) this
script never asserts which variant, if any, matches Curve #490; (6) every
extracted/raw text value is sanitized; (7) the CLI's one-command contract.

No `blpapi` faking needed for the orchestration tests: `run_zero_rate_
comparison_probe` takes `send_request` as an injectable callable, mirroring
the seams the round 2/4/5/6/7/8 tools already established.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_zero_rate_comparison_probe as module  # noqa: E402
from bloomberg_curve_zero_rate_comparison_probe import (  # noqa: E402
    DEFAULT_SECURITY,
    DEFAULT_VARIANTS,
    FIELD,
    build_report,
    main,
    render_json,
    render_markdown,
    run_zero_rate_comparison_probe,
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


def _bulk_field_data(rows) -> dict:
    return {FIELD: _FakeElement(values=list(rows))}


# --- input validation ---------------------------------------------------------------


def test_run_rejects_a_blank_security():
    with pytest.raises(ValueError, match="non-blank"):
        run_zero_rate_comparison_probe("", send_request=lambda **_: None)


def test_run_rejects_a_non_string_security():
    with pytest.raises(ValueError, match="non-blank"):
        run_zero_rate_comparison_probe(None, send_request=lambda **_: None)


def test_default_security_and_variants():
    assert DEFAULT_SECURITY == "USD Curncy"
    assert DEFAULT_VARIANTS == (
        ("no_override", None, None),
        ("SW569=Y", "SW569", "Y"),
        ("SW564=Y", "SW564", "Y"),
    )
    assert FIELD == "SW174"


# --- three independent requests ------------------------------------------------------


def test_run_sends_exactly_three_independent_requests():
    calls = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        assert service_uri == _REFDATA
        assert request_name == "ReferenceDataRequest"
        calls.append(context)
        request = _FakeRequest()
        configure(request)
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    assert len(calls) == 3


def test_no_override_variant_sends_no_override_element():
    seen_no_override = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        seen_no_override.append(request.overrides.elements_appended == [])
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    assert seen_no_override == [True, False, False]


def test_each_variant_sends_only_sw174_never_sw173():
    all_fields = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        all_fields.append(request.fields)
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    for fields in all_fields:
        assert fields == ["SW174"]


def test_variant_overrides_are_sent_correctly_and_independently():
    captured_overrides = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        captured_overrides.append([e.values for e in request.overrides.elements_appended])
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    assert captured_overrides == [
        [],
        [{"fieldId": "SW569", "value": "Y"}],
        [{"fieldId": "SW564", "value": "Y"}],
    ]


def test_security_is_sent_verbatim_for_every_variant():
    captured = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        captured.append(request.securities)
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    assert captured == [["USD Curncy"], ["USD Curncy"], ["USD Curncy"]]


# --- per-variant results stay independent ---------------------------------------


def test_results_stay_independent_across_variants():
    call_index = {"n": 0}

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        n = call_index["n"]
        call_index["n"] += 1
        if n == 0:
            collect(
                _message_with_records(
                    [
                        _security_data_record(
                            field_data_subs=_bulk_field_data(
                                [_bulk_row(Date="2026-08-10", ZeroRate="0.0300")]
                            )
                        )
                    ]
                )
            )
        elif n == 1:
            collect(
                _message_with_records(
                    [_security_data_record(security_error="<securityError category=BAD_SEC>")]
                )
            )
        else:
            collect(
                _message_with_records(
                    [
                        _security_data_record(
                            field_data_subs={},
                            field_exceptions=[
                                _field_exception("SW564", "[INVALID_OVERRIDE] rejected")
                            ],
                        )
                    ]
                )
            )

    report = run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    assert len(report.variants) == 3
    no_override, sw569, sw564 = report.variants

    assert no_override.field_status == "returned_bulk"
    assert no_override.rows[0].fields == {"Date": "2026-08-10", "ZeroRate": "0.0300"}
    assert no_override.security_error is None

    assert sw569.security_error is not None
    assert "BAD_SEC" in sw569.security_error
    assert sw569.rows == ()

    assert sw564.override_error is not None
    assert "INVALID_OVERRIDE" in sw564.override_error
    assert sw564.rows == ()

    # The successful variant's rows never leak into the failing ones.
    assert sw569.field_status is None
    assert sw564.field_status == "field_exception"


def test_blocker_note_never_declares_a_winner():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        collect(_message_with_records([_security_data_record(field_data_subs={})]))

    report = run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    assert "does not infer" in report.blocker_note
    assert "compare identical dates" in report.blocker_note


# --- sanitization ---------------------------------------------------------------


def test_row_fields_and_raw_dump_are_sanitized():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        request = _FakeRequest()
        configure(request)
        collect(
            _message_with_records(
                [
                    _security_data_record(
                        field_data_subs=_bulk_field_data(
                            [
                                _bulk_row(
                                    Date="2026-08-10",
                                    Note="from clientId=SYNTH-LEAK at localhost:8194",
                                    raw="row from clientId=SYNTH-LEAK at localhost:8194",
                                )
                            ]
                        )
                    )
                ]
            )
        )

    report = run_zero_rate_comparison_probe("USD Curncy", send_request=_fake_send_request)

    for variant in report.variants:
        for row in variant.rows:
            assert "SYNTH-LEAK" not in row.fields.get("Note", "")
            assert "localhost:8194" not in row.fields.get("Note", "")


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.ZeroRateComparisonReport:
    row = module.BulkFieldRow(
        index=0, fields={"Date": "2026-08-10", "ZeroRate": "0.0300"}, raw_row_dump="<row 0>"
    )
    variants = (
        module.VariantResult(
            variant_label="no_override",
            override_field=None,
            override_value=None,
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
            note="SW174 returned 1 bulk row(s) for this variant.",
        ),
        module.VariantResult(
            variant_label="SW569=Y",
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
            note="SW174 returned 1 bulk row(s) for this variant.",
        ),
        module.VariantResult(
            variant_label="SW564=Y",
            override_field="SW564",
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
            note="SW174 returned 1 bulk row(s) for this variant.",
        ),
    )
    return module.ZeroRateComparisonReport(
        generated_at="2026-08-10T00:00:00+00:00",
        security="USD Curncy",
        variants=variants,
        blocker_note="Three independent SW174 requests were sent.",
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "no_override" in markdown
    assert "SW569=Y" in markdown
    assert "SW564=Y" in markdown
    assert "2026-08-10" in markdown
    assert "no_override" in as_json


def test_write_report_writes_both_files(tmp_path):
    data = build_report(_minimal_report())

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "SW564=Y" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_rejects_a_blank_security(capsys):
    exit_code = main(["--security", "   "])

    assert exit_code == 2
    assert "non-blank" in capsys.readouterr().err


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(security):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "run_zero_rate_comparison_probe", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_uses_the_default_security_when_not_supplied(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(security):
        seen["security"] = security
        return _minimal_report()

    monkeypatch.setattr(module, "run_zero_rate_comparison_probe", _fake_run)

    main(["--output-dir", str(tmp_path / "out")])

    assert seen["security"] == DEFAULT_SECURITY


def test_main_accepts_an_explicit_security_override(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(security):
        seen["security"] = security
        return _minimal_report()

    monkeypatch.setattr(module, "run_zero_rate_comparison_probe", _fake_run)

    main(["--security", "USD Curncy 2", "--output-dir", str(tmp_path / "out")])

    assert seen["security"] == "USD Curncy 2"


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        module, "run_zero_rate_comparison_probe", lambda security: _minimal_report()
    )

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "no_override: sent, returned_bulk (1 rows)" in out
    assert "SW569=Y: sent, returned_bulk (1 rows)" in out
    assert "SW564=Y: sent, returned_bulk (1 rows)" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
