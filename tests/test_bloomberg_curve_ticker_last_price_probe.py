"""Tests for `tools/bloomberg_curve_ticker_last_price_probe.py` (Issue #165 round 11).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) both security identifiers are never derived --
they are always exactly what the caller/CLI supplied, defaulting to Eddy's
own literal Excel-formula strings; (2) exactly one `ReferenceDataRequest` is
sent for both securities and `LAST_PRICE` only; (3) each security's own
result is matched by its own returned `security` identifier, never by
response-array order; (4) a rejected security (`securityError`) is reported
per-security and never blocks the other security's own result; (5) the
discount-factor ticker's own parsed value is checked against `(0, 1]` as a
factual range check, never a "matches Curve #490" claim; (6) every
extracted/raw text value is sanitized; (7) the CLI's one-command contract,
including its two default securities.

No `blpapi` faking needed for the orchestration tests: `run_ticker_last_
price_probe` takes `send_request` as an injectable callable, mirroring the
seam established in round 7's `bloomberg_curve_value_probe.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_ticker_last_price_probe as module  # noqa: E402
from bloomberg_curve_ticker_last_price_probe import (  # noqa: E402
    DEFAULT_DISCOUNT_FACTOR_SECURITY,
    DEFAULT_ZERO_RATE_SECURITY,
    DISCOUNT_FACTOR_ROLE,
    FIELD,
    ZERO_RATE_ROLE,
    build_report,
    main,
    render_json,
    render_markdown,
    run_ticker_last_price_probe,
    write_report,
)

_REFDATA = "//blp/refdata"


# --- fake ReferenceDataRequest response elements --------------------------------------


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


def _field_exception(field_id, message) -> _FakeElement:
    return _FakeElement(
        sub_elements={
            "fieldId": _FakeElement(string_value=field_id),
            "errorInfo": _FakeElement(string_value=message),
        },
        string_value=f"<fieldException {field_id}>",
    )


def _security_data_record(
    *, security, security_error=None, field_exceptions=None, last_price=None
) -> _FakeElement:
    sub = {"security": _FakeElement(string_value=security)}
    if last_price is not None:
        sub["fieldData"] = _FakeElement(
            sub_elements={"LAST_PRICE": _FakeElement(string_value=last_price)}
        )
    else:
        sub["fieldData"] = _FakeElement(sub_elements={})
    if security_error is not None:
        sub["securityError"] = _FakeElement(string_value=security_error)
    if field_exceptions is not None:
        sub["fieldExceptions"] = _FakeElement(values=list(field_exceptions))
    return _FakeElement(sub_elements=sub, string_value=f"<securityData record {security}>")


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


def _both_returned(zero_rate_value="0.0325", discount_factor_value="0.9678"):
    return _message_with_records(
        [
            _security_data_record(security=DEFAULT_ZERO_RATE_SECURITY, last_price=zero_rate_value),
            _security_data_record(
                security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price=discount_factor_value
            ),
        ]
    )


# --- input validation ---------------------------------------------------------------


def test_run_rejects_a_blank_zero_rate_security():
    with pytest.raises(ValueError, match="zero_rate_security"):
        run_ticker_last_price_probe("", "S0490D 1Y BLC2 Curncy", send_request=lambda **_: None)


def test_run_rejects_a_blank_discount_factor_security():
    with pytest.raises(ValueError, match="discount_factor_security"):
        run_ticker_last_price_probe("S0490Z 2Y BLC2 Curncy", "", send_request=lambda **_: None)


def test_run_rejects_non_string_securities():
    with pytest.raises(ValueError, match="zero_rate_security"):
        run_ticker_last_price_probe(None, "S0490D 1Y BLC2 Curncy", send_request=lambda **_: None)


# --- defaults are Eddy's literal Excel-formula strings -------------------------------


def test_default_securities_are_eddys_literal_excel_formula_strings():
    assert DEFAULT_ZERO_RATE_SECURITY == "S0490Z 2Y BLC2 Curncy"
    assert DEFAULT_DISCOUNT_FACTOR_SECURITY == "S0490D 1Y BLC2 Curncy"
    assert FIELD == "LAST_PRICE"


# --- request construction -----------------------------------------------------------


def test_run_sends_both_securities_and_only_last_price():
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
        collect(_both_returned())

    run_ticker_last_price_probe(send_request=_fake_send_request)

    assert captured["securities"] == [DEFAULT_ZERO_RATE_SECURITY, DEFAULT_DISCOUNT_FACTOR_SECURITY]
    assert captured["fields"] == [FIELD]


def test_run_never_tries_a_different_security_on_rejection():
    calls = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        class _Request:
            def append(self, name, value):
                if name == "securities":
                    calls.append(value)

        configure(_Request())
        collect(
            _message_with_records(
                [
                    _security_data_record(
                        security=DEFAULT_ZERO_RATE_SECURITY,
                        security_error="[BAD_SEC] Unknown/Invalid security",
                    ),
                    _security_data_record(
                        security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price="0.9678"
                    ),
                ]
            )
        )

    run_ticker_last_price_probe(send_request=_fake_send_request)

    assert calls == [DEFAULT_ZERO_RATE_SECURITY, DEFAULT_DISCOUNT_FACTOR_SECURITY]


# --- request/response-envelope handling -----------------------------------------


def test_run_reports_a_request_failure():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        raise RuntimeError("Bloomberg DAPI request timed out")

    report = run_ticker_last_price_probe(send_request=_fake_send_request)

    assert report.request_status == "error"
    assert "timed out" in report.request_error
    assert "timed out" in report.blocker_note
    assert report.results == ()


# --- per-security identity matching --------------------------------------------------


def test_each_security_is_matched_by_its_own_returned_identifier_not_array_order():
    # Deliberately returned out of request order.
    message = _message_with_records(
        [
            _security_data_record(security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price="0.9678"),
            _security_data_record(security=DEFAULT_ZERO_RATE_SECURITY, last_price="0.0325"),
        ]
    )

    report = run_ticker_last_price_probe(send_request=_send_request_returning(message))

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].security == DEFAULT_ZERO_RATE_SECURITY
    assert by_role[ZERO_RATE_ROLE].raw_value == "0.0325"
    assert by_role[DISCOUNT_FACTOR_ROLE].security == DEFAULT_DISCOUNT_FACTOR_SECURITY
    assert by_role[DISCOUNT_FACTOR_ROLE].raw_value == "0.9678"


def test_missing_security_is_a_record_count_error_and_does_not_block_the_other():
    message = _message_with_records(
        [_security_data_record(security=DEFAULT_ZERO_RATE_SECURITY, last_price="0.0325")]
    )

    report = run_ticker_last_price_probe(send_request=_send_request_returning(message))

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].status == "returned"
    assert by_role[DISCOUNT_FACTOR_ROLE].status == "record_count_error"
    assert "0 securityData record" in by_role[DISCOUNT_FACTOR_ROLE].detail


def test_duplicate_security_records_are_a_record_count_error():
    message = _message_with_records(
        [
            _security_data_record(security=DEFAULT_ZERO_RATE_SECURITY, last_price="0.0325"),
            _security_data_record(security=DEFAULT_ZERO_RATE_SECURITY, last_price="0.0326"),
            _security_data_record(security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price="0.9678"),
        ]
    )

    report = run_ticker_last_price_probe(send_request=_send_request_returning(message))

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].status == "record_count_error"
    assert "2 securityData record" in by_role[ZERO_RATE_ROLE].detail


# --- security-level and field-level outcomes -----------------------------------------


def test_security_error_is_reported_verbatim_per_security():
    message = _message_with_records(
        [
            _security_data_record(
                security=DEFAULT_ZERO_RATE_SECURITY,
                security_error="<securityError category=BAD_SEC>",
            ),
            _security_data_record(security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price="0.9678"),
        ]
    )

    report = run_ticker_last_price_probe(send_request=_send_request_returning(message))

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].status == "security_error"
    assert "BAD_SEC" in by_role[ZERO_RATE_ROLE].detail
    assert by_role[DISCOUNT_FACTOR_ROLE].status == "returned"


def test_field_exception_is_reported_for_last_price():
    message = _message_with_records(
        [
            _security_data_record(
                security=DEFAULT_ZERO_RATE_SECURITY,
                field_exceptions=[_field_exception("LAST_PRICE", "[BAD_FLD] not applicable")],
            ),
            _security_data_record(security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price="0.9678"),
        ]
    )

    report = run_ticker_last_price_probe(send_request=_send_request_returning(message))

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].status == "field_exception"
    assert "BAD_FLD" in by_role[ZERO_RATE_ROLE].detail


def test_absent_field_with_no_exception_is_reported_as_absent():
    message = _message_with_records(
        [
            _security_data_record(security=DEFAULT_ZERO_RATE_SECURITY),
            _security_data_record(security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price="0.9678"),
        ]
    )

    report = run_ticker_last_price_probe(send_request=_send_request_returning(message))

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].status == "absent"


def test_returned_value_is_parsed_as_a_float():
    report = run_ticker_last_price_probe(
        send_request=_send_request_returning(_both_returned(zero_rate_value="3.25"))
    )

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].raw_value == "3.25"
    assert by_role[ZERO_RATE_ROLE].parsed_value == 3.25


def test_non_numeric_value_leaves_parsed_value_none_but_keeps_raw():
    report = run_ticker_last_price_probe(
        send_request=_send_request_returning(_both_returned(zero_rate_value="N.A."))
    )

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].raw_value == "N.A."
    assert by_role[ZERO_RATE_ROLE].parsed_value is None


# --- discount-factor plausibility is a factual range check, not a verdict ------------


def test_discount_factor_in_range_is_flagged_plausible():
    report = run_ticker_last_price_probe(
        send_request=_send_request_returning(_both_returned(discount_factor_value="0.9678"))
    )

    by_role = {r.role: r for r in report.results}
    assert by_role[DISCOUNT_FACTOR_ROLE].plausible_discount_factor is True


def test_discount_factor_above_one_is_flagged_implausible():
    report = run_ticker_last_price_probe(
        send_request=_send_request_returning(_both_returned(discount_factor_value="1.4"))
    )

    by_role = {r.role: r for r in report.results}
    assert by_role[DISCOUNT_FACTOR_ROLE].plausible_discount_factor is False


def test_discount_factor_zero_or_negative_is_flagged_implausible():
    report = run_ticker_last_price_probe(
        send_request=_send_request_returning(_both_returned(discount_factor_value="0"))
    )

    by_role = {r.role: r for r in report.results}
    assert by_role[DISCOUNT_FACTOR_ROLE].plausible_discount_factor is False


def test_discount_factor_plausibility_is_none_when_no_value_parsed():
    report = run_ticker_last_price_probe(
        send_request=_send_request_returning(_both_returned(discount_factor_value="N.A."))
    )

    by_role = {r.role: r for r in report.results}
    assert by_role[DISCOUNT_FACTOR_ROLE].plausible_discount_factor is None


def test_zero_rate_ticker_never_gets_a_discount_factor_plausibility_flag():
    report = run_ticker_last_price_probe(send_request=_send_request_returning(_both_returned()))

    by_role = {r.role: r for r in report.results}
    assert by_role[ZERO_RATE_ROLE].plausible_discount_factor is None


def test_blocker_note_never_asserts_a_match_to_curve_490():
    report = run_ticker_last_price_probe(send_request=_send_request_returning(_both_returned()))

    lowered = report.blocker_note.lower()
    assert "eddy's own manual judgment" in lowered
    assert "matches curve #490" not in lowered
    assert "confirms" not in lowered


# --- sanitization ---------------------------------------------------------------


def test_raw_value_and_detail_are_sanitized():
    message = _message_with_records(
        [
            _security_data_record(
                security=DEFAULT_ZERO_RATE_SECURITY,
                security_error="rejected: clientId=SYNTH-LEAK at localhost:8194",
            ),
            _security_data_record(security=DEFAULT_DISCOUNT_FACTOR_SECURITY, last_price="0.9678"),
        ]
    )

    report = run_ticker_last_price_probe(send_request=_send_request_returning(message))

    by_role = {r.role: r for r in report.results}
    assert "SYNTH-LEAK" not in by_role[ZERO_RATE_ROLE].detail
    assert "localhost:8194" not in by_role[ZERO_RATE_ROLE].detail


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.TickerLastPriceReport:
    result_zero_rate = module.SecurityLastPriceResult(
        role=ZERO_RATE_ROLE,
        security=DEFAULT_ZERO_RATE_SECURITY,
        status="returned",
        raw_value="0.0325",
        parsed_value=0.0325,
        detail=None,
        plausible_discount_factor=None,
    )
    result_discount_factor = module.SecurityLastPriceResult(
        role=DISCOUNT_FACTOR_ROLE,
        security=DEFAULT_DISCOUNT_FACTOR_SECURITY,
        status="returned",
        raw_value="0.9678",
        parsed_value=0.9678,
        detail=None,
        plausible_discount_factor=True,
    )
    return module.TickerLastPriceReport(
        generated_at="2026-08-10T00:00:00+00:00",
        request_status="sent",
        request_error=None,
        results=(result_zero_rate, result_discount_factor),
        raw_response_dump="<message with securityData>",
        blocker_note="LAST_PRICE returned for both tickers.",
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert DEFAULT_ZERO_RATE_SECURITY in markdown
    assert "0.0325" in markdown
    assert DEFAULT_DISCOUNT_FACTOR_SECURITY in markdown
    assert "0.9678" in markdown
    assert DEFAULT_ZERO_RATE_SECURITY in as_json


def test_write_report_writes_both_files(tmp_path):
    data = build_report(_minimal_report())

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "0.9678" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_rejects_a_blank_zero_rate_security(capsys):
    exit_code = main(["--zero-rate-security", "   "])

    assert exit_code == 2
    assert "zero_rate_security" in capsys.readouterr().err


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(zero_rate_security, discount_factor_security):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "run_ticker_last_price_probe", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_uses_the_default_securities_when_not_supplied(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(zero_rate_security, discount_factor_security):
        seen["zero_rate_security"] = zero_rate_security
        seen["discount_factor_security"] = discount_factor_security
        return _minimal_report()

    monkeypatch.setattr(module, "run_ticker_last_price_probe", _fake_run)

    main(["--output-dir", str(tmp_path / "out")])

    assert seen["zero_rate_security"] == DEFAULT_ZERO_RATE_SECURITY
    assert seen["discount_factor_security"] == DEFAULT_DISCOUNT_FACTOR_SECURITY


def test_main_accepts_explicit_security_overrides(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(zero_rate_security, discount_factor_security):
        seen["zero_rate_security"] = zero_rate_security
        seen["discount_factor_security"] = discount_factor_security
        return _minimal_report()

    monkeypatch.setattr(module, "run_ticker_last_price_probe", _fake_run)

    main(
        [
            "--zero-rate-security",
            "S0490Z 3Y BLC2 Curncy",
            "--discount-factor-security",
            "S0490D 2Y BLC2 Curncy",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert seen["zero_rate_security"] == "S0490Z 3Y BLC2 Curncy"
    assert seen["discount_factor_security"] == "S0490D 2Y BLC2 Curncy"


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        module,
        "run_ticker_last_price_probe",
        lambda zero_rate_security, discount_factor_security: _minimal_report(),
    )

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"{ZERO_RATE_ROLE} ({DEFAULT_ZERO_RATE_SECURITY}): returned" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
