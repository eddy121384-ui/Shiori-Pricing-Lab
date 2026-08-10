"""Tests for `tools/bloomberg_curve_identity_search_probe.py` (Issue #165 round 4).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) the search step only fires once this run's own
`discover_service` confirms the proven `FieldSearchRequest.searchSpec` pair,
never assumed from a prior round; (2) one request is sent per search term,
using that exact pair; (3) a `fieldData`-shaped response is parsed into
per-record candidates and category-filtered by literal substring match
against Eddy's three category strings -- never a name-based guess; (4) a
response that doesn't parse that way falls back to the whole raw dump
without losing evidence; (5) one term's send failure never blocks another;
(6) category-relevant mnemonics are deduplicated and handed to round 3's
`discover_field_documentation` for expansion, skipped entirely when there
are none; (7) the CLI's one-command contract.

No `blpapi` faking needed for the orchestration tests: `search_curve_
identity_candidates` takes `discover_service_fn`/`send_request`/`describe`
as injectable callables, mirroring the seams the round 2/3 tools already
established.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_identity_search_probe as module  # noqa: E402
from bloomberg_curve_discovery_probe import (  # noqa: E402
    OperationEvidence,
    ServiceEvidence,
)
from bloomberg_curve_identity_search_probe import (  # noqa: E402
    CATEGORY_FILTER_STRINGS,
    DEFAULT_CURVE_IDENTITY_SEARCH_TERMS,
    build_report,
    main,
    render_json,
    render_markdown,
    search_curve_identity_candidates,
    write_report,
)
from bloomberg_dapi_probe import FieldDescription  # noqa: E402

_APIFLDS = "//blp/apiflds"


def _proven_apiflds_evidence(*, opened=True) -> ServiceEvidence:
    return ServiceEvidence(
        service_uri=_APIFLDS,
        opened=opened,
        open_error=None if opened else "could not open",
        full_schema_dump="schema",
        operations=(
            OperationEvidence(
                name="FieldSearchRequest",
                description=None,
                request_schema=None,
                response_schemas=(),
                candidate_string_elements=("searchSpec",),
            ),
        ),
        operation_list_error=None,
    )


def _unproven_apiflds_evidence() -> ServiceEvidence:
    return ServiceEvidence(
        service_uri=_APIFLDS,
        opened=True,
        open_error=None,
        full_schema_dump="schema",
        operations=(
            OperationEvidence(
                name="ReferenceDataRequest",
                description=None,
                request_schema=None,
                response_schemas=(),
                candidate_string_elements=(),
            ),
        ),
        operation_list_error=None,
    )


# --- fake fieldData response elements -----------------------------------------------


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
        return self._string_value or "<record>"


def _field_info_record(field_id, *, mnemonic=None, description=None, category=None) -> _FakeElement:
    info_sub = {}
    if mnemonic is not None:
        info_sub["mnemonic"] = _FakeElement(string_value=mnemonic)
    if description is not None:
        info_sub["description"] = _FakeElement(string_value=description)
    if category is not None:
        info_sub["category"] = _FakeElement(string_value=category)
    return _FakeElement(
        sub_elements={
            "id": _FakeElement(string_value=field_id),
            "fieldInfo": _FakeElement(sub_elements=info_sub, string_value=f"<{field_id}>"),
        },
        string_value=f"<record {field_id}>",
    )


def _field_data_message(records) -> _FakeElement:
    return _FakeElement(
        sub_elements={"fieldData": _FakeElement(values=list(records))},
        string_value="<message with fieldData>",
    )


def _plain_message(text) -> _FakeElement:
    return _FakeElement(string_value=text)


def _fake_describe(fields):
    """A stand-in for `describe_fields` -- avoids the real one's `import blpapi`."""

    return [FieldDescription(field=field, status="described", mnemonic=field) for field in fields]


# --- search_curve_identity_candidates: capability gate --------------------------


def test_search_reports_a_blocker_when_capability_not_confirmed():
    report = search_curve_identity_candidates(
        discover_service_fn=lambda uri: _unproven_apiflds_evidence(),
        send_request=lambda **_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert report.search_capability_confirmed is False
    assert report.term_outcomes == ()
    assert report.category_relevant_mnemonics == ()
    assert "was not confirmed" in report.blocker_note


def test_search_reports_a_blocker_when_apiflds_did_not_open():
    report = search_curve_identity_candidates(
        discover_service_fn=lambda uri: _proven_apiflds_evidence(opened=False),
        send_request=lambda **_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert report.search_capability_confirmed is False
    assert report.apiflds_open_error == "could not open"


# --- search_curve_identity_candidates: sending -----------------------------------


def test_search_sends_one_request_per_term_using_the_proven_pair():
    calls = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        calls.append((service_uri, request_name, context))

        class _Request:
            def set(self, name, value):
                calls.append(("set", name, value))

        configure(_Request())
        collect(_field_data_message([]))

    report = search_curve_identity_candidates(
        ("curve number", "curve id"),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
    )

    assert [c[1] for c in calls if c[0] == module._APIFLDS_SERVICE] == [
        "FieldSearchRequest",
        "FieldSearchRequest",
    ]
    assert ("set", "searchSpec", "curve number") in calls
    assert ("set", "searchSpec", "curve id") in calls
    assert len(report.term_outcomes) == 2


def test_search_falls_back_to_append_for_a_repeated_search_spec_element():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        class _Request:
            def set(self, name, value):
                raise RuntimeError("repeated element")

            def append(self, name, value):
                assert (name, value) == ("searchSpec", "curve id")

        configure(_Request())
        collect(_field_data_message([]))

    report = search_curve_identity_candidates(
        ("curve id",),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
    )

    assert report.term_outcomes[0].status == "sent"


# --- search_curve_identity_candidates: parsing and category filtering -----------


def test_search_parses_fielddata_and_flags_category_relevant_records():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(
            _field_data_message(
                [
                    _field_info_record(
                        "CRV1",
                        mnemonic="CRV1",
                        description="a curve identity field",
                        category="Security Specific/Curves",
                    ),
                    _field_info_record(
                        "UNRELATED1",
                        mnemonic="UNRELATED1",
                        description="something else",
                        category="Analysis/Unrelated",
                    ),
                ]
            )
        )

    report = search_curve_identity_candidates(
        ("curve number",),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
        describe=_fake_describe,
    )

    outcome = report.term_outcomes[0]
    assert outcome.structured_parse_ok is True
    by_mnemonic = {r.mnemonic: r for r in outcome.records}
    assert by_mnemonic["CRV1"].category_relevant is True
    assert by_mnemonic["UNRELATED1"].category_relevant is False
    assert report.category_relevant_mnemonics == ("CRV1",)


def test_search_flags_category_relevant_via_raw_dump_when_no_structured_category():
    record = _field_info_record("CRV2", mnemonic="CRV2", description="d")
    # No structured category element on this record; the raw dump is the
    # only thing that can carry the category phrase in this synthetic case.
    record._string_value = "record for CRV2, category=Analysis/Spread/Curves/Swap"

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(_field_data_message([record]))

    report = search_curve_identity_candidates(
        ("swap curve id",),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
        describe=_fake_describe,
    )

    assert report.category_relevant_mnemonics == ("CRV2",)


def test_search_deduplicates_category_relevant_mnemonics_across_terms():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(
            _field_data_message(
                [
                    _field_info_record(
                        "CRV1",
                        mnemonic="CRV1",
                        description="d",
                        category="Security Specific/Curves",
                    )
                ]
            )
        )

    report = search_curve_identity_candidates(
        ("curve number", "curve id"),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
        describe=_fake_describe,
    )

    assert report.category_relevant_mnemonics == ("CRV1",)


def test_search_falls_back_to_raw_dump_when_response_is_not_fielddata_shaped():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(_plain_message("some unexpected response shape"))

    report = search_curve_identity_candidates(
        ("default curve",),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
    )

    outcome = report.term_outcomes[0]
    assert outcome.structured_parse_ok is False
    assert outcome.records == ()
    assert "some unexpected response shape" in outcome.raw_response_dump


def test_search_records_one_terms_send_failure_without_stopping_others():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        if "curve number" in context:
            raise RuntimeError("Bloomberg DAPI request timed out")
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(_field_data_message([]))

    report = search_curve_identity_candidates(
        ("curve number", "curve id"),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
    )

    by_term = {o.term: o for o in report.term_outcomes}
    assert by_term["curve number"].status == "error"
    assert "timed out" in by_term["curve number"].error
    assert by_term["curve id"].status == "sent"


def test_search_uses_every_default_term_when_none_supplied():
    seen_terms = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        seen_terms.append(context)
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(_field_data_message([]))

    search_curve_identity_candidates(
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
    )

    assert len(seen_terms) == len(DEFAULT_CURVE_IDENTITY_SEARCH_TERMS)


# --- documentation expansion reuse -----------------------------------------------


def test_search_expands_documentation_for_category_relevant_mnemonics():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(
            _field_data_message(
                [
                    _field_info_record(
                        "CRV1",
                        mnemonic="CRV1",
                        description="d",
                        category="Security Specific/Curves",
                    )
                ]
            )
        )

    def _fake_describe(fields):
        assert fields == ["CRV1"]
        return [FieldDescription(field="CRV1", status="described", mnemonic="CRV1")]

    report = search_curve_identity_candidates(
        ("curve number",),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
        describe=_fake_describe,
    )

    assert report.documentation_expansion is not None
    assert report.documentation_expansion["primary_fields"] == ["CRV1"]
    assert "Category-relevant candidates" in report.blocker_note


def test_search_skips_documentation_expansion_with_no_candidates():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect(_field_data_message([]))

    report = search_curve_identity_candidates(
        ("curve number",),
        discover_service_fn=lambda uri: _proven_apiflds_evidence(),
        send_request=_fake_send_request,
        describe=lambda fields: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    assert report.documentation_expansion is None
    assert "still unresolved" in report.blocker_note


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.CurveIdentitySearchReport:
    return module.CurveIdentitySearchReport(
        generated_at="2026-08-10T00:00:00+00:00",
        search_capability_confirmed=True,
        apiflds_open_error=None,
        search_terms=("curve number",),
        term_outcomes=(
            module.SearchTermOutcome(
                term="curve number",
                status="sent",
                error=None,
                structured_parse_ok=True,
                raw_response_dump="raw dump",
                records=(
                    module.SearchResultRecord(
                        search_term="curve number",
                        field_id="CRV1",
                        mnemonic="CRV1",
                        description="a curve identity field",
                        category="Security Specific/Curves",
                        raw_record_dump="<record CRV1>",
                        category_relevant=True,
                    ),
                ),
            ),
        ),
        category_relevant_mnemonics=("CRV1",),
        documentation_expansion={"primary_fields": ["CRV1"]},
        blocker_note="Category-relevant candidates were found.",
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "CRV1" in markdown
    assert "Security Specific/Curves" in markdown
    assert "RELEVANT" in markdown
    assert "CRV1" in as_json


def test_write_report_writes_both_files(tmp_path):
    data = build_report(_minimal_report())

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "CRV1" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(search_terms):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "search_curve_identity_candidates", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_uses_default_search_terms_when_none_supplied(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _fake_search(search_terms):
        seen["search_terms"] = search_terms
        return _minimal_report()

    monkeypatch.setattr(module, "search_curve_identity_candidates", _fake_search)

    main([])

    assert seen["search_terms"] == DEFAULT_CURVE_IDENTITY_SEARCH_TERMS


def test_main_accepts_repeated_search_term_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _fake_search(search_terms):
        seen["search_terms"] = search_terms
        return _minimal_report()

    monkeypatch.setattr(module, "search_curve_identity_candidates", _fake_search)

    main(["--search-term", "curve reference", "--search-term", "curve ticker"])

    assert seen["search_terms"] == ("curve reference", "curve ticker")


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        module, "search_curve_identity_candidates", lambda search_terms: _minimal_report()
    )

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Category-relevant mnemonics: CRV1" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()


def test_category_filter_strings_match_eddys_exact_wording():
    assert CATEGORY_FILTER_STRINGS == (
        "Security Specific/Curves",
        "Analysis/Spread/Curves/Swap",
        "Analysis/Market Data",
    )
