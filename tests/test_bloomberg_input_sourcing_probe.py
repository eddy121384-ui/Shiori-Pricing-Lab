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
    sanitize_external_text,
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
                # long enough that the report has to cap it
                documentation=f"{field} documentation " + "detail " * 100,
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


def _default_report(overrides_by_field=None, user_overrides=(), context=None):
    probe = _fake_probe({"DAY_CNT_DES": "ACT/ACT", "OPT_UNDL_FORWARD_PX": "98.765432"})
    discovery = _discovery(overrides_by_field)
    if context is None:
        context = {**load_option_context_fixture(), **module.FIXED_ROLE_VALUES}
    plan, applied = plan_overrides(discovery, context, user_overrides)
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
    assert describe.calls == [
        list(module._group_mnemonics(OPTION_CONTEXT_GROUP)),
        ["SOME_OVERRIDE_FLD"],
    ]
    assert written["field_metadata_discovery"]["fields"][0]["documented_overrides"] == [
        "SOME_OVERRIDE_FLD"
    ]
    assert [
        field["field"]
        for field in written["field_metadata_discovery"]["override_field_metadata"]["fields"]
    ] == ["SOME_OVERRIDE_FLD"]


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
    # option-context mnemonics are. The second call is the override pass.
    assert describe.calls == [
        list(module._group_mnemonics(OPTION_CONTEXT_GROUP)),
        ["STRIKE_FLD", "EXPIRY_FLD"],
    ]
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


# --- override field metadata: the second FieldInfoRequest -----------------------


def test_discovered_override_ids_are_described_in_the_same_run():
    describe = _fake_describe(
        {"OPT_UNDL_FORWARD_PX": ("OP046", "OP188", "OP131"), "PRICE_VOL": ("OP131",)}
    )

    discovery = discover_option_context_metadata(describe=describe, clock=_clock())

    # One second request, carrying every discovered override id once, in
    # discovery order -- no user argument, no extra command.
    assert describe.calls[1] == ["OP046", "OP188", "OP131"]
    assert discovery.override_fields == ("OP046", "OP188", "OP131")
    assert [item.field for item in discovery.override_descriptions] == ["OP046", "OP188", "OP131"]
    assert discovery.override_requested_at < discovery.override_received_at
    assert discovery.override_requested_at > discovery.received_at


def test_no_second_request_when_no_override_is_documented():
    describe = _fake_describe()

    discovery = discover_option_context_metadata(describe=describe, clock=_clock())

    assert len(describe.calls) == 1
    assert discovery.override_fields == ()
    assert discovery.override_requested_at is None


def test_a_failed_override_metadata_pass_keeps_the_first_pass_and_the_probes():
    calls: list[list[str]] = []

    def _describe(fields):
        calls.append(list(fields))
        if len(calls) == 1:
            return [
                FieldDescription(
                    field=field,
                    status="described",
                    mnemonic=field,
                    overrides=("OP999",) if field == "OPT_UNDL_FORWARD_PX" else (),
                )
                for field in fields
            ]
        raise RuntimeError("Bloomberg DAPI request timed out for field metadata request")

    discovery = discover_option_context_metadata(describe=_describe, clock=_clock())
    plan, applied = plan_overrides(discovery, {}, ())
    report = build_report(
        collect_evidence(("US91282CLJ89",), applied, probe=_fake_probe({}), clock=_clock()),
        discovery,
        plan,
        _GENERATED_AT,
    )

    metadata = report["field_metadata_discovery"]["override_field_metadata"]
    assert "timed out" in metadata["error"]
    assert metadata["requested_fields"] == ["OP999"]
    assert metadata["fields"] == []
    assert report["field_metadata_discovery"]["error"] is None
    assert report["field_metadata_discovery"]["fields"]
    assert report["securities"][0]["requests"][0]["error"] is None
    assert plan[0].status == module.OVERRIDE_ROLE_UNRESOLVED
    assert "no metadata was retrieved" in plan[0].note.lower()


def test_override_metadata_is_recorded_but_never_resolves_the_role():
    discovery = _discovery({"OPT_UNDL_FORWARD_PX": ("OP999",)})

    plan, applied = plan_overrides(discovery, load_option_context_fixture(), ())

    entry = plan[0]
    assert applied == ()
    assert entry.status == module.OVERRIDE_ROLE_UNRESOLVED
    assert entry.metadata is not None
    assert entry.metadata.mnemonic == "OP999"
    assert entry.metadata.datatype == "Price"
    assert "does not by itself establish which Shiori" in entry.note


def test_an_unusable_override_metadata_status_is_reported_honestly():
    def _describe(fields):
        if fields == ["OP999"]:
            return [FieldDescription(field="OP999", status="field_error", detail="[BAD_FLD]")]
        return [
            FieldDescription(field=field, status="described", mnemonic=field, overrides=("OP999",))
            for field in fields
        ]

    discovery = discover_option_context_metadata(describe=_describe, clock=_clock())

    plan, _ = plan_overrides(discovery, {}, ())

    assert plan[0].status == module.OVERRIDE_ROLE_UNRESOLVED
    assert plan[0].metadata.status == "field_error"
    assert "came back 'field_error'" in plan[0].note


def test_override_metadata_reaches_the_json_and_markdown_redacted():
    report = _default_report({"OPT_UNDL_FORWARD_PX": ("OP999",)})

    metadata = report["field_metadata_discovery"]["override_field_metadata"]
    assert metadata["requested_at"] and metadata["received_at"]
    assert metadata["fields"][0]["mnemonic"] == "OP999"
    assert metadata["fields"][0]["documentation_excerpt"].endswith("<truncated>")
    assert report["override_plan"][0]["metadata"]["field"] == "OP999"

    markdown = render_markdown(report)
    assert "### Override field metadata (second `FieldInfoRequest`)" in markdown
    assert "| `OP999` | `described` | OP999 | Price |" in markdown
    assert "`OP999` documentation:" in markdown


# --- workstation-confirmed override semantics (OP046 / OP188 / OP131) -----------


def _forward_discovery():
    return _discovery({"OPT_UNDL_FORWARD_PX": ("OP046", "OP188", "OP131")})


def test_op188_is_sent_automatically_as_the_shiori_valuation_date():
    plan, applied = plan_overrides(
        _forward_discovery(), load_option_context_fixture(), ()
    )

    entry = next(item for item in plan if item.override_field == "OP188")
    assert entry.status == module.OVERRIDE_APPLIED
    assert entry.role == "valuation_date"
    assert entry.source == "fixture:valuation_date"
    sent = {item.field: item for item in applied}
    assert sent["OP188"].value == load_option_context_fixture()["valuation_date"]


def test_op046_is_sent_as_the_documented_black_bond_option_model():
    context = {**load_option_context_fixture(), **module.FIXED_ROLE_VALUES}

    plan, applied = plan_overrides(_forward_discovery(), context, ())

    entry = next(item for item in plan if item.override_field == "OP046")
    assert entry.status == module.OVERRIDE_APPLIED
    assert entry.role == "pricing_model"
    assert entry.source == "fixed:pricing_model"
    sent = {item.field: item for item in applied}
    assert set(sent) == {"OP046", "OP188"}
    # the value Bloomberg's documentation lists for a bond option, verbatim
    assert module.FIXED_ROLE_VALUES["pricing_model"] == "Black"
    assert sent["OP046"].value == "Black"


def test_op046_is_withheld_if_its_documented_value_is_ever_unrecorded(monkeypatch):
    monkeypatch.delitem(module.FIXED_ROLE_VALUES, "pricing_model")

    plan, applied = plan_overrides(_forward_discovery(), load_option_context_fixture(), ())

    entry = next(item for item in plan if item.override_field == "OP046")
    assert entry.status == module.OVERRIDE_VALUE_UNCONFIRMED
    assert entry.role == "pricing_model"
    assert "will not guess" in entry.note
    # the other confirmed override still goes out
    assert [item.field for item in applied] == ["OP188"]


def test_op131_is_never_mapped_and_never_sent_even_via_override():
    plan, applied = plan_overrides(
        _forward_discovery(), load_option_context_fixture(), (("OP131", "0.02"),)
    )

    entry = next(item for item in plan if item.override_field == "OP131")
    assert entry.status == module.OVERRIDE_NOT_APPLICABLE
    assert entry.role is None
    assert "dividend yield" in entry.note
    assert "--override for it was ignored" in entry.note
    assert "OP131" not in {item.field for item in applied}


def test_the_forward_probe_carries_the_confirmed_overrides_for_both_securities():
    probe = _fake_probe({"OPT_UNDL_FORWARD_PX": "98.765432"})
    discovery = _forward_discovery()
    _, applied = plan_overrides(
        discovery, {**load_option_context_fixture(), **module.FIXED_ROLE_VALUES}, ()
    )

    evidence = collect_evidence(
        module.DEFAULT_IDENTIFIERS, applied, probe=probe, clock=_clock()
    )

    option_calls = [call for call in probe.calls if "OPT_UNDL_FORWARD_PX" in call["fields"]]
    assert len(option_calls) == 2
    for call in option_calls:
        assert [field for field, _ in call["overrides"]] == ["OP046", "OP188"]
    forward = [
        group.results["OPT_UNDL_FORWARD_PX"].status
        for security in evidence
        for group in security.groups
        if "OPT_UNDL_FORWARD_PX" in group.results
    ]
    assert forward == ["returned", "returned"]


def test_a_forward_that_still_fails_is_reported_per_security():
    def _probe(identifier, fields, overrides=None):
        if identifier == "/isin/GB00BFX0ZL78" and "OPT_UNDL_FORWARD_PX" in fields:
            return [
                ProbeFieldResult(
                    field=field,
                    status="field_exception",
                    detail="[BAD_FLD] not applicable",
                )
                for field in fields
            ]
        return [
            ProbeFieldResult(field=field, status="returned", value="98.765432")
            if field == "OPT_UNDL_FORWARD_PX"
            else ProbeFieldResult(field=field, status="absent")
            for field in fields
        ]

    discovery = _forward_discovery()
    plan, applied = plan_overrides(
        discovery, {**load_option_context_fixture(), **module.FIXED_ROLE_VALUES}, ()
    )
    report = build_report(
        collect_evidence(module.DEFAULT_IDENTIFIERS, applied, probe=_probe, clock=_clock()),
        discovery,
        plan,
        _GENERATED_AT,
    )

    row = next(
        item
        for item in report["inputs"]
        if item["input_id"] == "pricing_inputs.forward_clean_price"
    )
    outcomes = {
        result["security"]: result["classification"]
        for candidate in row["candidates"]
        for result in candidate["results"]
    }
    assert outcomes == {"US91282CLJ89": DISPLAY_ONLY, "GB00BFX0ZL78": BAD_FLD}


def test_the_confirmed_and_excluded_overrides_reach_the_markdown():
    report = _default_report({"OPT_UNDL_FORWARD_PX": ("OP046", "OP188", "OP131")})

    markdown = render_markdown(report)
    assert "`OP188` | `OPT_UNDL_FORWARD_PX` | `OVERRIDE_APPLIED` | valuation_date" in markdown
    assert (
        "`OP046` | `OPT_UNDL_FORWARD_PX` | `OVERRIDE_APPLIED` | pricing_model | "
        "fixed:pricing_model" in markdown
    )
    # the documented token itself is still redacted like every override value
    # ("Black" also occurs legitimately in "Black-76" prose, so assert on the
    # override rendering itself)
    assert "(fixed:pricing_model) = <redacted text: 5 chars>" in markdown
    assert "= Black" not in markdown
    assert "`OP131` | `OPT_UNDL_FORWARD_PX` | `OVERRIDE_NOT_APPLICABLE`" in markdown
    assert "dividend yield" in markdown


# --- Codex review follow-ups (PR #151) ------------------------------------------


def test_an_override_value_echoed_in_a_request_error_is_scrubbed():
    def _probe(identifier, fields, overrides=None):
        raise RuntimeError(
            "Bloomberg DAPI responseError: invalid override OP046 = Black for this security"
        )

    applied = (AppliedOverride(field="OP046", value="Black", source="fixed:pricing_model"),)

    evidence = collect_evidence(("US91282CLJ89",), applied, probe=_probe, clock=_clock())

    option_group = evidence[0].groups[1]
    assert "Black" not in option_group.error
    assert "<redacted override value>" in option_group.error
    # the failure itself is still legible
    assert "responseError" in option_group.error and "OP046" in option_group.error


def test_an_override_value_echoed_in_a_field_exception_is_scrubbed():
    def _probe(identifier, fields, overrides=None):
        return [
            ProbeFieldResult(
                field=field,
                status="field_exception",
                detail=f"[BAD_FLD] override OP046=Black rejected for {field}",
            )
            for field in fields
        ]

    applied = (AppliedOverride(field="OP046", value="Black", source="fixed:pricing_model"),)

    evidence = collect_evidence(("US91282CLJ89",), applied, probe=_probe, clock=_clock())

    detail = evidence[0].groups[1].results["OPT_UNDL_FORWARD_PX"].detail
    assert "Black" not in detail
    assert "<redacted override value>" in detail


def test_a_scrubbed_error_never_reaches_the_json_markdown_or_console(monkeypatch, tmp_path, capsys):
    def _probe(identifier, fields, overrides=None):
        if overrides:
            raise RuntimeError("responseError: bad override value Black")
        return [ProbeFieldResult(field=field, status="absent") for field in fields]

    monkeypatch.setattr(module, "probe_fields", _probe)
    monkeypatch.setattr(
        module, "describe_fields", _fake_describe({"OPT_UNDL_FORWARD_PX": ("OP046",)})
    )

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    json_text = (tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8")
    markdown_text = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for surface in (json_text, markdown_text, console):
        assert "value Black" not in surface
    assert "<redacted override value>" in json_text


def test_a_cli_override_cannot_replace_a_confirmed_role_value():
    context = {**load_option_context_fixture(), **module.FIXED_ROLE_VALUES}

    plan, applied = plan_overrides(_forward_discovery(), context, (("OP046", "SomethingElse"),))

    entry = next(item for item in plan if item.override_field == "OP046")
    assert entry.status == module.OVERRIDE_APPLIED
    assert entry.role == "pricing_model"
    assert entry.source == "fixed:pricing_model"
    assert "was rejected" in entry.note
    # the confirmed value went out, not the CLI one
    sent = {item.field: item.value for item in applied}
    assert sent["OP046"] == module.FIXED_ROLE_VALUES["pricing_model"]
    assert "SomethingElse" not in sent.values()


def test_a_cli_override_cannot_replace_the_confirmed_valuation_date():
    context = {**load_option_context_fixture(), **module.FIXED_ROLE_VALUES}

    plan, applied = plan_overrides(_forward_discovery(), context, (("OP188", "2020-01-01"),))

    entry = next(item for item in plan if item.override_field == "OP188")
    assert entry.status == module.OVERRIDE_APPLIED
    assert entry.source == "fixture:valuation_date"
    sent = {item.field: item.value for item in applied}
    assert sent["OP188"] == load_option_context_fixture()["valuation_date"]


def test_main_rejects_a_cli_replacement_of_a_confirmed_override(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(module, "probe_fields", lambda *a, **k: calls.append((a, k)) or [])
    monkeypatch.setattr(module, "describe_fields", lambda *a, **k: calls.append((a, k)) or [])

    for field, value in (("OP046", "SomethingElse"), ("OP188", "2020-01-01")):
        assert main([f"--override={field}={value}", "--output-dir", str(tmp_path)]) == 2

    # rejected before any Bloomberg request, and the value is never echoed
    assert calls == []
    err = capsys.readouterr().err
    assert "OP046 was rejected" in err and "OP188 was rejected" in err
    assert "SomethingElse" not in err and "2020-01-01" not in err


def test_main_accepts_a_cli_override_that_repeats_the_approved_value(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "probe_fields", _fake_probe({}))
    monkeypatch.setattr(
        module, "describe_fields", _fake_describe({"OPT_UNDL_FORWARD_PX": ("OP046",)})
    )
    approved = module.FIXED_ROLE_VALUES["pricing_model"]

    exit_code = main([f"--override=OP046={approved}", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    written = json.loads((tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8"))
    entry = next(
        item for item in written["override_plan"] if item["override_field"] == "OP046"
    )
    assert entry["status"] == module.OVERRIDE_APPLIED
    assert entry["source"] == "fixed:pricing_model"


def test_a_cli_override_of_an_unconfirmed_field_still_reports_applied():
    plan, _ = plan_overrides(
        _discovery({"OPT_UNDL_FORWARD_PX": ("OP999",)}), {}, (("OP999", "1.0"),)
    )

    entry = next(item for item in plan if item.override_field == "OP999")
    assert entry.status == module.OVERRIDE_APPLIED
    assert entry.role is None


def test_op131_is_still_only_ignored_not_rejected(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(module, "probe_fields", _fake_probe({}))
    monkeypatch.setattr(
        module, "describe_fields", _fake_describe({"OPT_UNDL_FORWARD_PX": ("OP131",)})
    )

    exit_code = main(["--override", "OP131=0.02", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    console = capsys.readouterr().out
    assert "not applicable to a bond option, not sent" in console
    markdown = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    assert "OVERRIDE_NOT_APPLICABLE" in markdown
    assert "0.02" not in markdown


# --- external-text sanitization -------------------------------------------------


def test_sanitizer_removes_host_path_and_credential_shaped_detail():
    text = (
        "session failed against localhost:8194 while reading "
        "C:\\Users\\eddy\\bloomberg\\session.log for uuid=12345678 token: abcdef "
        "(/home/eddy/.blpapi/cache)"
    )

    sanitized = sanitize_external_text(text)

    assert "localhost:8194" not in sanitized
    assert "C:\\Users\\eddy" not in sanitized
    assert "/home/eddy" not in sanitized
    assert "12345678" not in sanitized
    assert "abcdef" not in sanitized
    assert "<redacted host>" in sanitized
    assert "<redacted path>" in sanitized
    assert "<redacted>" in sanitized
    # the failure itself stays legible
    assert "session failed against" in sanitized


def test_sanitizer_removes_applied_override_values_alongside_the_patterns():
    applied = (AppliedOverride(field="OP046", value="Black", source="fixed:pricing_model"),)

    sanitized = sanitize_external_text(
        "responseError from host:8194 -- override OP046=Black is invalid", applied
    )

    assert "Black" not in sanitized
    assert "<redacted override value>" in sanitized
    assert "<redacted host>" in sanitized
    assert "OP046" in sanitized and "responseError" in sanitized


def test_sanitizer_passes_ordinary_bloomberg_text_through():
    assert sanitize_external_text("[BAD_FLD] field not applicable to this security") == (
        "[BAD_FLD] field not applicable to this security"
    )
    assert sanitize_external_text(None) is None
    assert sanitize_external_text("") == ""


def test_connection_failures_are_sanitized_in_every_output(monkeypatch, tmp_path, capsys):
    def _probe(identifier, fields, overrides=None):
        raise RuntimeError(
            "Bloomberg DAPI session failed to start against localhost:8194 for "
            "uuid=87654321 (C:\\Users\\eddy\\terminal.log)"
        )

    monkeypatch.setattr(module, "probe_fields", _probe)
    monkeypatch.setattr(module, "describe_fields", _fake_describe())

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    json_text = (tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8")
    markdown_text = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for surface in (json_text, markdown_text, console):
        assert "localhost:8194" not in surface
        assert "87654321" not in surface
        assert "eddy\\terminal.log" not in surface
    assert "session failed to start" in json_text


def test_a_first_pass_metadata_failure_is_sanitized_in_every_output(monkeypatch, tmp_path, capsys):
    def _describe(fields):
        raise RuntimeError(
            "Bloomberg DAPI failed to open service //blp/apiflds from localhost:8194 "
            "for uuid=13572468 (C:\\Users\\eddy\\apiflds.log)"
        )

    monkeypatch.setattr(module, "probe_fields", _fake_probe({}))
    monkeypatch.setattr(module, "describe_fields", _describe)

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    json_text = (tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8")
    markdown_text = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for surface in (json_text, markdown_text, console):
        assert "localhost:8194" not in surface
        assert "13572468" not in surface
        assert "eddy\\apiflds.log" not in surface
    # the failure is still reported and still diagnosable
    assert "failed to open service" in json_text
    assert "<redacted host>" in json_text


def test_a_second_pass_metadata_failure_is_sanitized_in_every_output(monkeypatch, tmp_path, capsys):
    calls: list[list[str]] = []

    def _describe(fields):
        calls.append(list(fields))
        if len(calls) == 1:
            return [
                FieldDescription(
                    field=field,
                    status="described",
                    mnemonic=field,
                    overrides=("OP999",) if field == "OPT_UNDL_FORWARD_PX" else (),
                )
                for field in fields
            ]
        raise RuntimeError(
            "Bloomberg DAPI request timed out for field metadata request on localhost:8194 "
            "session=ABCD1234 (/home/eddy/.blpapi/log)"
        )

    monkeypatch.setattr(module, "probe_fields", _fake_probe({}))
    monkeypatch.setattr(module, "describe_fields", _describe)

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    json_text = (tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8")
    markdown_text = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for surface in (json_text, markdown_text, console):
        assert "localhost:8194" not in surface
        assert "ABCD1234" not in surface
        assert "/home/eddy" not in surface
    written = json.loads(json_text)
    override_metadata = written["field_metadata_discovery"]["override_field_metadata"]
    assert "timed out" in override_metadata["error"]
    assert "<redacted host>" in override_metadata["error"]
    # the first pass and the value probes survived the second pass failing
    assert written["field_metadata_discovery"]["error"] is None
    assert written["field_metadata_discovery"]["fields"]
    assert written["securities"][0]["requests"][0]["error"] is None


def test_a_one_character_override_value_is_scrubbed_on_error_paths():
    applied = (AppliedOverride(field="OP999", value="1", source="user_supplied"),)

    sanitized = sanitize_external_text("responseError: OP999=1", applied)

    assert sanitized == "responseError: OP999=<redacted override value>"


def test_scrubbing_matches_whole_tokens_only():
    one_char = (AppliedOverride(field="OP999", value="1", source="user_supplied"),)
    word = (AppliedOverride(field="OP046", value="Black", source="fixed:pricing_model"),)

    # an unrelated substring must survive
    assert sanitize_external_text("codes 10 and 21 unaffected", one_char) == (
        "codes 10 and 21 unaffected"
    )
    assert sanitize_external_text("Blackrock unaffected", word) == "Blackrock unaffected"
    # the delimited value itself is still removed
    assert "<redacted override value>" in sanitize_external_text("value = 1 rejected", one_char)


def test_a_one_character_override_never_reaches_any_output(monkeypatch, tmp_path, capsys):
    def _probe(identifier, fields, overrides=None):
        return [
            ProbeFieldResult(
                field=field,
                status="field_exception",
                detail=f"[BAD_FLD] override OP999=1 rejected for {field}",
            )
            for field in fields
        ]

    monkeypatch.setattr(module, "probe_fields", _probe)
    monkeypatch.setattr(
        module, "describe_fields", _fake_describe({"OPT_UNDL_FORWARD_PX": ("OP999",)})
    )

    exit_code = main(["--override", "OP999=1", "--output-dir", str(tmp_path)])

    assert exit_code == 0
    json_text = (tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8")
    markdown_text = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for surface in (json_text, markdown_text, console):
        assert "OP999=1" not in surface
    assert "<redacted override value>" in json_text


def test_a_per_field_metadata_error_is_sanitized(monkeypatch, tmp_path, capsys):
    def _describe(fields):
        return [
            FieldDescription(
                field=field,
                status="field_error",
                detail=(
                    f"[BAD_FLD] {field} unavailable from localhost:8194 session=ZZZZ9999 "
                    "(/home/eddy/.blpapi/log)"
                ),
            )
            for field in fields
        ]

    monkeypatch.setattr(module, "probe_fields", _fake_probe({}))
    monkeypatch.setattr(module, "describe_fields", _describe)

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    json_text = (tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8")
    markdown_text = (tmp_path / module.MARKDOWN_FILENAME).read_text(encoding="utf-8")
    console = capsys.readouterr().out
    for surface in (json_text, markdown_text, console):
        assert "localhost:8194" not in surface
        assert "ZZZZ9999" not in surface
        assert "/home/eddy" not in surface
    written = json.loads(json_text)
    detail = written["field_metadata_discovery"]["fields"][0]["detail"]
    assert "[BAD_FLD]" in detail and "<redacted host>" in detail


def test_metadata_description_and_documentation_are_sanitized_too(monkeypatch, tmp_path):
    def _describe(fields):
        return [
            FieldDescription(
                field=field,
                status="described",
                mnemonic=field,
                description="served from localhost:8194",
                datatype="Price",
                documentation="see /home/eddy/docs for uuid=24681357",
            )
            for field in fields
        ]

    monkeypatch.setattr(module, "probe_fields", _fake_probe({}))
    monkeypatch.setattr(module, "describe_fields", _describe)

    main(["--output-dir", str(tmp_path)])

    written = json.loads((tmp_path / module.JSON_FILENAME).read_text(encoding="utf-8"))
    field = written["field_metadata_discovery"]["fields"][0]
    assert field["description"] == "served from <redacted host>"
    assert "24681357" not in field["documentation_excerpt"]
    assert "/home/eddy" not in field["documentation_excerpt"]
