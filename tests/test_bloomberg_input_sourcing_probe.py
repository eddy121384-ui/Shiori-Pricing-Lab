"""Offline tests for `tools/bloomberg_input_sourcing_probe.py` (Issue #149).

No network access and no real `blpapi`: every test injects a fake probe
function in place of `tools/bloomberg_dapi_probe.probe_fields`, so these
prove only what the sourcing probe itself decides -- classification,
redaction, failure isolation, disposition aggregation, deterministic
output, and the CLI's one-command contract. They assert nothing about
Bloomberg's real behavior; that is exactly what the workstation run this
tool exists for has to establish.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_input_sourcing_probe as module  # noqa: E402
from bloomberg_dapi_probe import FieldDescription, ProbeFieldResult  # noqa: E402
from bloomberg_input_sourcing_probe import (  # noqa: E402
    ADVANCED_OVERRIDE_REQUIRED,
    APPROVED_PROFILE_REQUIRED,
    BAD_FLD,
    BLOOMBERG_AUTO,
    CONFIRMED,
    DISPLAY_ONLY,
    ENTITLEMENT_BLOCKED,
    MARKET_LEVEL,
    NOT_REQUIRED_FOR_STANDALONE,
    OPTION_CONTEXT_GROUP,
    RESPONSE_ERROR,
    SEMANTIC,
    SHIORI_DERIVED_CANDIDATE,
    STATIC_GROUP,
    UNMAPPED,
    UNRESOLVED,
    AppliedOverride,
    Candidate,
    InputRow,
    build_report,
    classify,
    collect_evidence,
    discover_option_context_metadata,
    load_option_context_fixture,
    main,
    plan_overrides,
    redact,
    redact_override_value,
    render_json,
    render_markdown,
)

_GENERATED_AT = "2026-07-28T00:00:00+00:00"
_DISPOSITIONS = {
    BLOOMBERG_AUTO,
    SHIORI_DERIVED_CANDIDATE,
    APPROVED_PROFILE_REQUIRED,
    ADVANCED_OVERRIDE_REQUIRED,
    UNRESOLVED,
    NOT_REQUIRED_FOR_STANDALONE,
}
_RECOMMENDATIONS = {
    module.REMOVE_FROM_MAIN_SCREEN,
    module.AUTO_SOURCED_READ_ONLY,
    module.MOVE_TO_ADVANCED,
    module.KEEP_AS_TRADE_INPUT,
    module.BLOCK_WITH_UNRESOLVED_MESSAGE,
}


def _clock():
    """Deterministic UTC-shaped clock: one distinct timestamp per call."""

    counter = {"n": 0}

    def _now():
        counter["n"] += 1
        return f"2026-07-28T00:00:{counter['n']:02d}+00:00"

    return _now


def _fake_describe(overrides_by_field: dict[str, tuple[str, ...]] | None = None, *, fail=False):
    calls: list[list[str]] = []

    def _describe(fields):
        calls.append(list(fields))
        if fail:
            raise RuntimeError("Bloomberg DAPI failed to open service //blp/apiflds")
        return [
            FieldDescription(
                field=field,
                status="described",
                mnemonic=field,
                description=f"{field} description",
                datatype="Price",
                overrides=(overrides_by_field or {}).get(field, ()),
                documentation=f"{field} documentation",
            )
            for field in fields
        ]

    _describe.calls = calls
    return _describe


def _discovery(overrides_by_field=None, *, fail=False):
    return discover_option_context_metadata(
        describe=_fake_describe(overrides_by_field, fail=fail), clock=_clock()
    )


def _candidate(**kwargs) -> Candidate:
    defaults = dict(
        mnemonic="TEST_FLD",
        provenance=module.PROBE_PROPOSED,
        group=STATIC_GROUP,
        disclosure=SEMANTIC,
        typed_mapping_safe=False,
        note="test candidate",
    )
    defaults.update(kwargs)
    return Candidate(**defaults)


def _fake_probe(values_by_field: dict[str, str], *, fail: set[str] = frozenset()):
    """Return a probe stand-in: `values_by_field` returns, everything else absent."""

    calls: list[dict] = []

    def _probe(identifier, fields, overrides=None):
        calls.append({"identifier": identifier, "fields": list(fields), "overrides": overrides})
        if identifier in fail:
            raise RuntimeError(f"Bloomberg DAPI request timed out for {identifier!r}")
        results = []
        for field in fields:
            if field in values_by_field:
                results.append(
                    ProbeFieldResult(field=field, status="returned", value=values_by_field[field])
                )
            else:
                results.append(ProbeFieldResult(field=field, status="absent"))
        return results

    _probe.calls = calls
    return _probe


# --- classification ------------------------------------------------------------


def test_returned_value_is_confirmed_only_when_typed_mapping_is_safe():
    result = ProbeFieldResult(field="TEST_FLD", status="returned", value="ACT/ACT")

    safe, safe_evidence = classify(_candidate(typed_mapping_safe=True), result, None)
    unsafe, unsafe_evidence = classify(_candidate(typed_mapping_safe=False), result, None)

    assert safe == CONFIRMED
    assert unsafe == DISPLAY_ONLY
    assert safe_evidence == unsafe_evidence == "ACT/ACT"


def test_absent_and_empty_values_are_unmapped():
    absent = ProbeFieldResult(field="TEST_FLD", status="absent")
    blank = ProbeFieldResult(field="TEST_FLD", status="returned", value="   ")

    assert classify(_candidate(), absent, None)[0] == UNMAPPED
    assert classify(_candidate(), blank, None)[0] == UNMAPPED
    assert classify(_candidate(), None, None)[0] == UNMAPPED


def test_field_exception_details_split_bad_fld_entitlement_and_other_errors():
    def _exception(detail):
        return ProbeFieldResult(field="TEST_FLD", status="field_exception", detail=detail)

    assert classify(_candidate(), _exception("[BAD_FLD] not applicable"), None)[0] == BAD_FLD
    assert (
        classify(_candidate(), _exception("NOT_ENTITLED to this field"), None)[0]
        == ENTITLEMENT_BLOCKED
    )
    other = classify(_candidate(), _exception("something else went wrong"), None)
    assert other[0] == RESPONSE_ERROR


def test_request_level_failure_classifies_the_candidate_as_response_error():
    classification, evidence = classify(_candidate(), None, "request timed out")

    assert classification == RESPONSE_ERROR
    assert "request timed out" in evidence


# --- redaction -----------------------------------------------------------------


def test_market_level_values_are_reduced_to_a_shape_and_never_carry_the_number():
    redacted = redact("101.2345", MARKET_LEVEL)

    assert "101" not in redacted
    assert "2345" not in redacted
    assert redacted == "<redacted numeric: positive, 3 integer digits, 4 decimal places>"


def test_market_level_non_numeric_and_negative_values_are_still_redacted():
    assert redact("-0.5", MARKET_LEVEL).startswith("<redacted numeric: negative,")
    assert redact("N.A.", MARKET_LEVEL) == "<redacted non-numeric: 4 chars>"


def test_semantic_values_are_collapsed_and_length_capped():
    assert redact("  ACT/ACT\n  ", SEMANTIC) == "ACT/ACT"
    assert redact("x" * 200, SEMANTIC).endswith("<truncated>")


# --- evidence collection -------------------------------------------------------


def test_collect_evidence_qualifies_identifiers_and_probes_both_request_groups():
    probe = _fake_probe({})

    evidence = collect_evidence(("US91282CLJ89",), (), probe=probe)

    assert evidence[0].qualified_identifier == "/isin/US91282CLJ89"
    assert [group.group for group in evidence[0].groups] == [STATIC_GROUP, OPTION_CONTEXT_GROUP]
    assert [call["identifier"] for call in probe.calls] == [
        "/isin/US91282CLJ89",
        "/isin/US91282CLJ89",
    ]


def test_collect_evidence_sends_overrides_only_with_the_option_context_request():
    probe = _fake_probe({})
    applied = (AppliedOverride(field="SOME_FIELD", value="SOME_VALUE", source="user_supplied"),)

    collect_evidence(("US91282CLJ89",), applied, probe=probe)

    static_call, option_call = probe.calls
    assert static_call["overrides"] == []
    assert option_call["overrides"] == [("SOME_FIELD", "SOME_VALUE")]


def test_every_dapi_request_records_its_own_acquisition_window():
    probe = _fake_probe({})

    evidence = collect_evidence(("US91282CLJ89", "GB00BFX0ZL78"), (), probe=probe, clock=_clock())

    stamps = [
        (group.requested_at, group.received_at)
        for security in evidence
        for group in security.groups
    ]
    assert len(stamps) == 4
    assert all(requested < received for requested, received in stamps)
    assert len(set(stamps)) == 4


def test_a_failed_request_still_records_its_acquisition_window():
    probe = _fake_probe({}, fail={"/isin/US91282CLJ89"})

    evidence = collect_evidence(("US91282CLJ89",), (), probe=probe, clock=_clock())

    group = evidence[0].groups[0]
    assert group.error
    assert group.requested_at and group.received_at
    assert group.requested_at < group.received_at


def test_one_failed_security_never_stops_the_other_investigations():
    probe = _fake_probe({"DAY_CNT_DES": "ACT/ACT"}, fail={"/isin/GB00BFX0ZL78"})

    evidence = collect_evidence(("US91282CLJ89", "GB00BFX0ZL78"), (), probe=probe)

    assert [group.error for group in evidence[0].groups] == [None, None]
    assert all("timed out" in group.error for group in evidence[1].groups)
    assert evidence[0].groups[0].results["DAY_CNT_DES"].value == "ACT/ACT"


def test_a_failed_request_still_produces_every_report_row():
    probe = _fake_probe({}, fail={"/isin/US91282CLJ89", "/isin/GB00BFX0ZL78"})

    report = build_report(
        collect_evidence(("US91282CLJ89", "GB00BFX0ZL78"), (), probe=probe, clock=_clock()),
        _discovery(),
        (),
        _GENERATED_AT,
    )

    assert len(report["inputs"]) == len(module.INPUT_ROWS)
    probed = [row for row in report["inputs"] if row["candidates"]]
    assert probed, "expected at least one probed row"
    for row in probed:
        for candidate in row["candidates"]:
            assert {result["classification"] for result in candidate["results"]} == {RESPONSE_ERROR}


# --- disposition aggregation ---------------------------------------------------


def _row_with(candidate: Candidate, **kwargs) -> InputRow:
    defaults = dict(
        input_id="test.input",
        section="test",
        question="test?",
        candidates=(candidate,),
        fallback_disposition=APPROVED_PROFILE_REQUIRED,
        reason="test reason",
    )
    defaults.update(kwargs)
    return InputRow(**defaults)


def _evidence(results_by_security: dict[str, dict[str, ProbeFieldResult]]):
    """Hand-built evidence for a synthetic (non-catalogued) candidate."""

    return tuple(
        module.SecurityEvidence(
            identifier=identifier,
            qualified_identifier=f"/isin/{identifier}",
            groups=(
                module.GroupEvidence(
                    group=STATIC_GROUP,
                    mnemonics=tuple(results),
                    overrides=(),
                    results=results,
                    error=None,
                    requested_at="2026-07-28T00:00:01+00:00",
                    received_at="2026-07-28T00:00:02+00:00",
                ),
                module.GroupEvidence(
                    group=OPTION_CONTEXT_GROUP,
                    mnemonics=(),
                    overrides=(),
                    results={},
                    error=None,
                    requested_at="2026-07-28T00:00:03+00:00",
                    received_at="2026-07-28T00:00:04+00:00",
                ),
            ),
        )
        for identifier, results in results_by_security.items()
    )


def _returned(value: str) -> dict[str, ProbeFieldResult]:
    return {"TEST_FLD": ProbeFieldResult(field="TEST_FLD", status="returned", value=value)}


def _absent() -> dict[str, ProbeFieldResult]:
    return {"TEST_FLD": ProbeFieldResult(field="TEST_FLD", status="absent")}


def test_a_candidate_confirmed_on_every_security_makes_the_input_bloomberg_auto():
    evidence = _evidence({"US91282CLJ89": _returned("2026-01-01"), "GB00BFX0ZL78": _returned("X")})

    result = module._row_result(_row_with(_candidate(typed_mapping_safe=True)), evidence)

    assert result["disposition"] == BLOOMBERG_AUTO
    assert result["main_screen_recommendation"] == module.AUTO_SOURCED_READ_ONLY


def test_a_candidate_confirmed_on_only_one_security_falls_back_instead():
    evidence = _evidence({"US91282CLJ89": _returned("2026-01-01"), "GB00BFX0ZL78": _absent()})

    result = module._row_result(_row_with(_candidate(typed_mapping_safe=True)), evidence)

    assert result["disposition"] == APPROVED_PROFILE_REQUIRED


def test_a_returned_but_unsafe_candidate_never_reaches_bloomberg_auto():
    evidence = _evidence({"US91282CLJ89": _returned("ACT/ACT")})

    result = module._row_result(_row_with(_candidate(typed_mapping_safe=False)), evidence)

    assert result["disposition"] == APPROVED_PROFILE_REQUIRED
    assert result["candidates"][0]["results"][0]["classification"] == DISPLAY_ONLY


def test_a_declared_disposition_ignores_probe_evidence():
    evidence = _evidence({"US91282CLJ89": _returned("2026-01-01")})
    row = _row_with(
        _candidate(typed_mapping_safe=True),
        declared_disposition=NOT_REQUIRED_FOR_STANDALONE,
    )

    result = module._row_result(row, evidence)

    assert result["disposition"] == NOT_REQUIRED_FOR_STANDALONE
    assert result["main_screen_recommendation"] == module.REMOVE_FROM_MAIN_SCREEN


# --- report contract -----------------------------------------------------------


def _default_report(overrides_by_field=None, user_overrides=(), fixture=None):
    probe = _fake_probe({"DAY_CNT_DES": "ACT/ACT", "OPT_UNDL_FORWARD_PX": "98.765432"})
    discovery = _discovery(overrides_by_field)
    plan, applied = plan_overrides(discovery, fixture or {}, user_overrides)
    evidence = collect_evidence(
        module.DEFAULT_IDENTIFIERS, applied, probe=probe, clock=_clock()
    )
    return build_report(evidence, discovery, plan, _GENERATED_AT)


def test_every_catalogued_input_gets_a_vocabulary_disposition_and_recommendation():
    report = _default_report()

    assert [row["input_id"] for row in report["inputs"]] == [
        row.input_id for row in module.INPUT_ROWS
    ]
    for row in report["inputs"]:
        assert row["disposition"] in _DISPOSITIONS
        assert row["main_screen_recommendation"] in _RECOMMENDATIONS
        assert row["reason"]


def test_report_covers_the_five_investigation_sections_and_both_default_securities():
    report = _default_report()

    assert {row["section"] for row in report["inputs"]} == {
        module.SECTION_STATIC,
        module.SECTION_TIMING,
        module.SECTION_FORWARD,
        module.SECTION_VOL,
        module.SECTION_DISCOUNTING,
    }
    assert [security["identifier"] for security in report["securities"]] == list(
        module.DEFAULT_IDENTIFIERS
    )


def test_summary_partitions_every_input_and_lists_owner_decisions():
    report = _default_report()
    summary = report["summary"]

    partitioned = (
        summary["bloomberg_can_supply"]
        + summary["shiori_may_derive"]
        + summary["needs_profile_or_owner_decision"]
        + summary["advanced_override_required"]
        + summary["unresolved"]
        + summary["not_required_for_standalone"]
    )
    assert sorted(partitioned) == sorted(row["input_id"] for row in report["inputs"])
    assert summary["owner_decisions_required"]
    assert report["next_delivery_issue"]["deliverable"]


def test_market_level_values_never_reach_the_json_or_markdown_output():
    report = _default_report()

    assert "98.765432" not in render_json(report)
    assert "98.765432" not in render_markdown(report)
    assert "<redacted numeric:" in render_markdown(report)


def test_json_and_markdown_are_deterministic_for_the_same_evidence():
    first = _default_report()
    second = _default_report()

    assert render_json(first) == render_json(second)
    assert render_markdown(first) == render_markdown(second)
    assert json.loads(render_json(first))["inputs"][0]["input_id"] == module.INPUT_ROWS[0].input_id


def test_markdown_restates_the_prohibited_routes_and_the_read_only_scope():
    markdown = render_markdown(_default_report())

    assert "Read-only bounded probe" in markdown
    for prohibition in module.PROHIBITED_ROUTES:
        assert prohibition in markdown


# --- CLI -----------------------------------------------------------------------


def test_main_runs_with_no_arguments_and_writes_both_reports(monkeypatch, tmp_path, capsys):
    probe = _fake_probe({"DAY_CNT_DES": "ACT/ACT"})
    describe = _fake_describe({"OPT_UNDL_FORWARD_PX": ("SOME_OVERRIDE_FLD",)})
    monkeypatch.setattr(module, "probe_fields", probe)
    monkeypatch.setattr(module, "describe_fields", describe)

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    markdown_path = tmp_path / module.MARKDOWN_FILENAME
    json_path = tmp_path / module.JSON_FILENAME
    assert markdown_path.exists() and json_path.exists()
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert [security["identifier"] for security in written["securities"]] == list(
        module.DEFAULT_IDENTIFIERS
    )
    out = capsys.readouterr().out
    assert str(markdown_path.resolve()) in out
    assert str(json_path.resolve()) in out
    # discovery ran inside the same single command, with no user argument
    assert describe.calls == [list(module._group_mnemonics(OPTION_CONTEXT_GROUP))]
    assert written["field_metadata_discovery"]["fields"][0]["documented_overrides"] == [
        "SOME_OVERRIDE_FLD"
    ]


def test_main_reports_request_failures_without_failing_the_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(module, "probe_fields", _fake_probe({}, fail={"/isin/GB00BFX0ZL78"}))
    monkeypatch.setattr(module, "describe_fields", _fake_describe())

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    assert "Request-level failures" in capsys.readouterr().out


def test_main_rejects_a_bad_identifier_or_override_before_calling_bloomberg(
    monkeypatch, tmp_path, capsys
):
    calls = []
    monkeypatch.setattr(module, "probe_fields", lambda *a, **k: calls.append((a, k)) or [])
    monkeypatch.setattr(module, "describe_fields", lambda *a, **k: calls.append((a, k)) or [])

    assert main(["--identifier", "TOOSHORT", "--output-dir", str(tmp_path)]) == 2
    assert main(["--override", "NOEQUALS", "--output-dir", str(tmp_path)]) == 2
    assert calls == []
    assert "error:" in capsys.readouterr().err


def test_main_reports_missing_blpapi_clearly(monkeypatch, tmp_path, capsys):
    def _raise_import_error(*args, **kwargs):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "probe_fields", _raise_import_error)

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


# --- override discovery, fixture and redaction (Issue #149 acceptance) ----------


def test_discovery_runs_inside_the_run_and_records_documented_overrides():
    describe = _fake_describe({"PRICE_VOL": ("STRIKE_FLD", "EXPIRY_FLD")})

    discovery = discover_option_context_metadata(describe=describe, clock=_clock())

    # No user argument decides what to describe: the catalogue's own
    # option-context mnemonics are.
    assert describe.calls == [list(module._group_mnemonics(OPTION_CONTEXT_GROUP))]
    described = {item.field: item for item in discovery.descriptions}
    assert described["PRICE_VOL"].overrides == ("STRIKE_FLD", "EXPIRY_FLD")
    assert discovery.requested_at < discovery.received_at


def test_a_failed_discovery_is_recorded_and_never_stops_the_value_probes():
    discovery = _discovery(fail=True)
    probe = _fake_probe({"DAY_CNT_DES": "ACT/ACT"})

    plan, applied = plan_overrides(discovery, {}, ())
    report = build_report(
        collect_evidence(("US91282CLJ89",), applied, probe=probe, clock=_clock()),
        discovery,
        plan,
        _GENERATED_AT,
    )

    assert "//blp/apiflds" in report["field_metadata_discovery"]["error"]
    assert report["field_metadata_discovery"]["fields"] == []
    assert len(report["inputs"]) == len(module.INPUT_ROWS)
    assert report["securities"][0]["requests"][0]["error"] is None


def test_an_unapproved_override_role_is_reported_not_handed_back_to_the_user():
    discovery = _discovery({"OPT_UNDL_FORWARD_PX": ("SOME_OVERRIDE_FLD",)})

    plan, applied = plan_overrides(discovery, load_option_context_fixture(), ())

    assert applied == ()
    entry = plan[0]
    assert entry.override_field == "SOME_OVERRIDE_FLD"
    assert entry.status == module.OVERRIDE_ROLE_UNRESOLVED
    assert entry.required_by == ("OPT_UNDL_FORWARD_PX",)
    assert "APPROVED_OVERRIDE_ROLES" in entry.note


def test_an_approved_override_role_is_sent_automatically_from_the_fixture(monkeypatch):
    monkeypatch.setitem(module.APPROVED_OVERRIDE_ROLES, "SOME_OVERRIDE_FLD", "strike_price")
    discovery = _discovery({"OPT_UNDL_FORWARD_PX": ("SOME_OVERRIDE_FLD",)})
    fixture = load_option_context_fixture()

    plan, applied = plan_overrides(discovery, fixture, ())

    assert applied == (
        AppliedOverride(
            field="SOME_OVERRIDE_FLD",
            value=fixture["strike_price"],
            source="fixture:strike_price",
        ),
    )
    assert plan[0].status == module.OVERRIDE_APPLIED
    assert plan[0].role == "strike_price"


def test_an_approved_role_missing_from_the_fixture_sends_nothing(monkeypatch):
    monkeypatch.setitem(module.APPROVED_OVERRIDE_ROLES, "SOME_OVERRIDE_FLD", "strike_price")
    discovery = _discovery({"OPT_UNDL_FORWARD_PX": ("SOME_OVERRIDE_FLD",)})

    plan, applied = plan_overrides(discovery, {}, ())

    assert applied == ()
    assert plan[0].status == module.OVERRIDE_FIXTURE_MISSING


def test_the_committed_option_case_supplies_the_option_context_roles():
    fixture = load_option_context_fixture()

    assert fixture["strike_price"] and fixture["expiry_date"] and fixture["option_type"]
    assert load_option_context_fixture(Path("/nonexistent/case.json")) == {}


def test_a_user_supplied_override_still_wins_for_a_documented_field():
    discovery = _discovery({"OPT_UNDL_FORWARD_PX": ("SOME_OVERRIDE_FLD",)})

    plan, applied = plan_overrides(discovery, {}, (("SOME_OVERRIDE_FLD", "99.5"),))

    assert applied[0].source == "user_supplied"
    assert plan[0].status == module.OVERRIDE_APPLIED


def test_override_values_are_redacted_by_data_nature():
    assert redact_override_value("99.5") == (
        "<redacted numeric: positive, 2 integer digits, 1 decimal places>"
    )
    assert redact_override_value("2026-09-29") == "<redacted date: ISO YYYY-MM-DD>"
    # an all-digit value is reported by shape, never guessed to be a compact date
    assert redact_override_value("20260929") == (
        "<redacted numeric: positive, 8 integer digits, 0 decimal places>"
    )
    assert redact_override_value("2026-09-29T16:00:00+01:00") == "<redacted timestamp: ISO 8601>"
    assert redact_override_value("CALL") == "<redacted text: 4 chars>"


def test_no_override_value_reaches_the_json_markdown_or_console(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(module.APPROVED_OVERRIDE_ROLES, "STRIKE_OVERRIDE_FLD", "strike_price")
    monkeypatch.setattr(module, "probe_fields", _fake_probe({}))
    monkeypatch.setattr(
        module,
        "describe_fields",
        _fake_describe({"OPT_UNDL_FORWARD_PX": ("STRIKE_OVERRIDE_FLD",)}),
    )
    fixture_strike = load_option_context_fixture()["strike_price"]

    exit_code = main(["--override", "USER_FLD=123.456", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    json_text = (tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8")
    markdown_text = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for surface in (json_text, markdown_text, console):
        assert "123.456" not in surface
        assert fixture_strike not in surface
        # the field ids themselves stay auditable
        assert "STRIKE_OVERRIDE_FLD" in surface
        assert "USER_FLD" in surface
    assert "fixture:strike_price" in json_text
    assert "<redacted numeric:" in markdown_text


def test_acquisition_timestamps_reach_the_json_and_markdown():
    report = _default_report()

    discovery = report["field_metadata_discovery"]
    assert discovery["requested_at"] and discovery["received_at"]
    windows = [
        (request["requested_at"], request["received_at"])
        for security in report["securities"]
        for request in security["requests"]
    ]
    assert len(windows) == 4
    assert all(requested and received for requested, received in windows)
    assert len(set(windows)) == 4

    markdown = render_markdown(report)
    assert markdown.count("acquisition window:") == len(windows)
    for requested, received in windows:
        assert f"{requested} → {received}" in markdown


def test_unresolved_override_roles_are_listed_in_the_next_delivery_issue():
    report = _default_report({"PRICE_VOL": ("SOME_OVERRIDE_FLD",)})

    assert report["next_delivery_issue"]["override_roles_awaiting_owner_confirmation"] == [
        "SOME_OVERRIDE_FLD"
    ]
