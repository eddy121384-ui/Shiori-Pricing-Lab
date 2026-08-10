"""Tests for `tools/bloomberg_curve_instruments_service_probe.py` (Issue #165 round 5).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) `//blp/instruments` is explored via the
already-reviewed, unmodified `discover_service` -- no new schema
introspection implementation; (2) an operation only counts as
curve-related when Bloomberg's own description/request/response schema
text literally contains "curve", never from the operation's name; (3) a
search is only attempted using a request element `discover_service`
itself reported as a top-level STRING element on that operation -- never a
guessed element name; (4) every response is raw-captured with no assumed
structure (unlike round 4's `//blp/apiflds`-specific parse); (5) a service
that won't open, or opens with no curve-related capability, or has
curve-related operations with no searchable element, all produce a
precise blocker and never a guessed operation/ticker; (6) the CLI's
one-command contract.

No `blpapi` faking needed for the orchestration tests:
`explore_instruments_service` takes `discover_service_fn`/`send_request`
as injectable callables, mirroring the seams the round 2/4 tools already
established.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_instruments_service_probe as module  # noqa: E402
from bloomberg_curve_discovery_probe import (  # noqa: E402
    OperationEvidence,
    ServiceEvidence,
)
from bloomberg_curve_instruments_service_probe import (  # noqa: E402
    DEFAULT_INSTRUMENT_SEARCH_TERMS,
    build_report,
    explore_instruments_service,
    main,
    render_json,
    render_markdown,
    write_report,
)

_INSTRUMENTS = "//blp/instruments"


def _service(
    *,
    opened=True,
    open_error=None,
    operations=(),
    full_schema_dump="schema",
) -> ServiceEvidence:
    return ServiceEvidence(
        service_uri=_INSTRUMENTS,
        opened=opened,
        open_error=open_error,
        full_schema_dump=full_schema_dump,
        operations=operations,
        operation_list_error=None,
    )


def _operation(
    name,
    *,
    description=None,
    request_schema=None,
    response_schemas=(),
    candidate_string_elements=(),
) -> OperationEvidence:
    return OperationEvidence(
        name=name,
        description=description,
        request_schema=request_schema,
        response_schemas=response_schemas,
        candidate_string_elements=candidate_string_elements,
    )


# --- capability gates --------------------------------------------------------------


def test_explore_reports_blocker_when_service_does_not_open():
    report = explore_instruments_service(
        discover_service_fn=lambda uri: _service(opened=False, open_error="could not open"),
        send_request=lambda **_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert report.service.opened is False
    assert report.curve_related_operation_names == ()
    assert report.search_attempts == ()
    assert "could not be opened" in report.blocker_note
    assert "could not open" in report.blocker_note


def test_explore_reports_blocker_when_no_operation_mentions_curve():
    report = explore_instruments_service(
        discover_service_fn=lambda uri: _service(
            operations=(_operation("InstrumentListRequest", description="lists securities"),)
        ),
        send_request=lambda **_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert report.curve_related_operation_names == ()
    assert report.search_attempts == ()
    assert "none of its" in report.blocker_note
    assert "curve" in report.blocker_note.lower()


# --- curve-mention detection: description / request / response schema ---------------


def test_operation_counts_as_curve_related_via_description():
    report = explore_instruments_service(
        discover_service_fn=lambda uri: _service(
            operations=(_operation("SomeRequest", description="Search for Curve securities"),)
        ),
        send_request=lambda **_: None,
    )

    assert report.curve_related_operation_names == ("SomeRequest",)


def test_operation_counts_as_curve_related_via_request_schema_not_name():
    # The operation's *name* has no "curve" in it at all -- only its own
    # request schema text does. Proves the check is text-based, not a
    # name-pattern guess.
    report = explore_instruments_service(
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "SecuritySearchRequest",
                    description="generic instrument lookup",
                    request_schema="element curveIdentifier : string",
                ),
            )
        ),
        send_request=lambda **_: None,
    )

    assert report.curve_related_operation_names == ("SecuritySearchRequest",)


def test_operation_counts_as_curve_related_via_response_schema():
    report = explore_instruments_service(
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "LookupRequest",
                    description="generic",
                    response_schemas=("sequence { curveMembers : string[] }",),
                ),
            )
        ),
        send_request=lambda **_: None,
    )

    assert report.curve_related_operation_names == ("LookupRequest",)


def test_an_unrelated_operation_is_never_marked_curve_related():
    report = explore_instruments_service(
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "BondSearchRequest",
                    description="search for bonds",
                    request_schema="element isin : string",
                ),
            )
        ),
        send_request=lambda **_: None,
    )

    assert report.curve_related_operation_names == ()


# --- searching: proven element usage only, raw capture only -------------------------


def test_explore_reports_blocker_when_curve_related_operation_has_no_string_element():
    report = explore_instruments_service(
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "CurveListRequest",
                    description="list curves",
                    candidate_string_elements=(),
                ),
            )
        ),
        send_request=lambda **_: (_ for _ in ()).throw(AssertionError("must not send")),
    )

    assert report.curve_related_operation_names == ("CurveListRequest",)
    assert report.search_attempts == ()
    assert "none exposed a top-level STRING request element" in report.blocker_note


def test_explore_sends_one_search_per_term_using_the_discovered_element():
    calls = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        calls.append((service_uri, request_name, context))

        class _Request:
            def set(self, name, value):
                calls.append(("set", name, value))

        configure(_Request())
        collect("raw curve list response")

    report = explore_instruments_service(
        ("SOFR", "490"),
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "CurveListRequest",
                    description="list curves",
                    candidate_string_elements=("searchText",),
                ),
            )
        ),
        send_request=_fake_send_request,
    )

    assert [c[1] for c in calls if c[0] == _INSTRUMENTS] == ["CurveListRequest", "CurveListRequest"]
    assert ("set", "searchText", "SOFR") in calls
    assert ("set", "searchText", "490") in calls
    assert len(report.search_attempts) == 2
    assert report.search_attempts[0].raw_response_dump == "raw curve list response"
    assert "Search attempted" in report.blocker_note


def test_explore_falls_back_to_append_for_a_repeated_element():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        class _Request:
            def set(self, name, value):
                raise RuntimeError("repeated element")

            def append(self, name, value):
                assert (name, value) == ("searchText", "SOFR")

        configure(_Request())
        collect("ok")

    report = explore_instruments_service(
        ("SOFR",),
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "CurveListRequest",
                    description="list curves",
                    candidate_string_elements=("searchText",),
                ),
            )
        ),
        send_request=_fake_send_request,
    )

    assert report.search_attempts[0].status == "sent"


def test_explore_records_one_terms_error_without_stopping_others():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        if "SOFR" in context:
            raise RuntimeError("Bloomberg DAPI request timed out")
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect("ok for 490")

    report = explore_instruments_service(
        ("SOFR", "490"),
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "CurveListRequest",
                    description="list curves",
                    candidate_string_elements=("searchText",),
                ),
            )
        ),
        send_request=_fake_send_request,
    )

    by_term = {a.term: a for a in report.search_attempts}
    assert by_term["SOFR"].status == "error"
    assert "timed out" in by_term["SOFR"].error
    assert by_term["490"].status == "sent"


def test_explore_uses_all_default_terms_when_none_supplied():
    seen = []

    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        seen.append(context)
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect("ok")

    explore_instruments_service(
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "CurveListRequest",
                    description="list curves",
                    candidate_string_elements=("searchText",),
                ),
            )
        ),
        send_request=_fake_send_request,
    )

    assert len(seen) == len(DEFAULT_INSTRUMENT_SEARCH_TERMS)


def test_explore_raw_captures_without_assuming_any_structure():
    # No fieldData-style parsing is attempted here -- only the whole raw
    # message text is kept, unlike round 4's //blp/apiflds-specific parse.
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect("<totally unknown response shape>USD SOFR CURVE 490 XYZ123</...>")

    report = explore_instruments_service(
        ("SOFR",),
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "CurveListRequest",
                    description="list curves",
                    candidate_string_elements=("searchText",),
                ),
            )
        ),
        send_request=_fake_send_request,
    )

    assert "XYZ123" in report.search_attempts[0].raw_response_dump


def test_explore_sanitizes_the_raw_response_dump():
    def _fake_send_request(*, service_uri, request_name, configure, collect, context):
        configure(type("R", (), {"set": lambda self, n, v: None})())
        collect("response from clientId=SYNTH-LEAK-1 at localhost:8194")

    report = explore_instruments_service(
        ("SOFR",),
        discover_service_fn=lambda uri: _service(
            operations=(
                _operation(
                    "CurveListRequest",
                    description="list curves",
                    candidate_string_elements=("searchText",),
                ),
            )
        ),
        send_request=_fake_send_request,
    )

    assert "SYNTH-LEAK-1" not in report.search_attempts[0].raw_response_dump
    assert "localhost:8194" not in report.search_attempts[0].raw_response_dump


# --- rendering / report writing ------------------------------------------------------


def _minimal_report() -> module.InstrumentsExplorationReport:
    service = _service(
        operations=(
            _operation(
                "CurveListRequest",
                description="list curves",
                request_schema="element searchText : string",
                candidate_string_elements=("searchText",),
            ),
        )
    )
    return module.InstrumentsExplorationReport(
        generated_at="2026-08-10T00:00:00+00:00",
        service=service,
        curve_related_operation_names=("CurveListRequest",),
        search_terms=("SOFR",),
        search_attempts=(
            module.InstrumentSearchAttempt(
                operation_name="CurveListRequest",
                request_element_used="searchText",
                term="SOFR",
                status="sent",
                raw_response_dump="raw curve list response",
                error=None,
            ),
        ),
        blocker_note="Search attempted.",
    )


def test_build_report_and_render_round_trip():
    data = build_report(_minimal_report())

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "CurveListRequest" in markdown
    assert "raw curve list response" in markdown
    assert "CURVE-RELATED" in markdown
    assert "CurveListRequest" in as_json


def test_write_report_writes_both_files(tmp_path):
    data = build_report(_minimal_report())

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "CurveListRequest" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(search_terms):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "explore_instruments_service", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_uses_default_search_terms_when_none_supplied(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _fake_explore(search_terms):
        seen["search_terms"] = search_terms
        return _minimal_report()

    monkeypatch.setattr(module, "explore_instruments_service", _fake_explore)

    main([])

    assert seen["search_terms"] == DEFAULT_INSTRUMENT_SEARCH_TERMS


def test_main_accepts_repeated_search_term_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _fake_explore(search_terms):
        seen["search_terms"] = search_terms
        return _minimal_report()

    monkeypatch.setattr(module, "explore_instruments_service", _fake_explore)

    main(["--search-term", "EUR ESTR", "--search-term", "491"])

    assert seen["search_terms"] == ("EUR ESTR", "491")


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        module, "explore_instruments_service", lambda search_terms: _minimal_report()
    )

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Curve-related operations: CurveListRequest" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
