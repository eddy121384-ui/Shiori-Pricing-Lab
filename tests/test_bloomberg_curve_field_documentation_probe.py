"""Tests for `tools/bloomberg_curve_field_documentation_probe.py` (Issue #165 round 3).

Standalone diagnostic CLI, not part of the production pricing or ingestion
path. These tests prove: (1) the two-pass `//blp/apiflds` `FieldInfoRequest`
documentation expansion -- primary fields, then every override field id
their own documentation names -- composes only the already-reviewed
`bloomberg_dapi_probe.describe_fields`, sending no security-level request at
all; (2) a pass-1 failure still returns cleanly with nothing to expand in
pass 2, and a pass-2 failure never discards pass-1 results; (3) every
Bloomberg-authored text field goes through `sanitize_external_text`; (4) the
CLI's one-command contract, including its default field list and its
`blpapi`-missing handling.

No network access, no real `blpapi`: `discover_field_documentation` takes
`describe` as an injectable callable (mirroring
`bloomberg_input_sourcing_probe.discover_option_context_metadata`'s own
`describe=None -> describe = describe or describe_fields` pattern), so these
tests fake `describe_fields` directly rather than faking `blpapi` itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_curve_field_documentation_probe as module  # noqa: E402
from bloomberg_curve_field_documentation_probe import (  # noqa: E402
    ROUND_2_CONFIRMED_FIELDS,
    build_report,
    discover_field_documentation,
    main,
    render_json,
    render_markdown,
    write_report,
)
from bloomberg_dapi_probe import FieldDescription  # noqa: E402


def _description(field, *, status="described", overrides=(), **kwargs) -> FieldDescription:
    return FieldDescription(field=field, status=status, overrides=overrides, **kwargs)


# --- discover_field_documentation ---------------------------------------------------


def test_discover_field_documentation_expands_primary_fields():
    def _describe(fields):
        assert fields == list(ROUND_2_CONFIRMED_FIELDS)
        return [_description(field, mnemonic=field, datatype="BulkFormat") for field in fields]

    report = discover_field_documentation(describe=_describe)

    assert report.primary_fields == ROUND_2_CONFIRMED_FIELDS
    assert [d.field for d in report.primary_descriptions] == list(ROUND_2_CONFIRMED_FIELDS)
    assert report.primary_error is None
    assert report.override_fields == ()
    assert report.override_descriptions == ()


def test_discover_field_documentation_accepts_a_custom_field_list():
    calls = []

    def _describe(fields):
        calls.append(list(fields))
        return [_description(field) for field in fields]

    report = discover_field_documentation(("SW174", "SW173"), describe=_describe)

    assert report.primary_fields == ("SW174", "SW173")
    assert calls[0] == ["SW174", "SW173"]


def test_discover_field_documentation_expands_discovered_overrides_in_a_second_pass():
    calls = []

    def _describe(fields):
        calls.append(list(fields))
        if len(calls) == 1:
            return [
                _description("SW174", overrides=("CRV_ID", "CRV_DATE")),
                _description("SW173", overrides=("CRV_ID",)),
            ]
        return [_description(field, description="override doc") for field in fields]

    report = discover_field_documentation(("SW174", "SW173"), describe=_describe)

    assert calls[1] == ["CRV_ID", "CRV_DATE"]
    assert report.override_fields == ("CRV_ID", "CRV_DATE")
    assert [d.field for d in report.override_descriptions] == ["CRV_ID", "CRV_DATE"]


def test_discover_field_documentation_deduplicates_overrides_across_fields():
    def _describe(fields):
        if fields == ["SW174", "SW173"]:
            return [
                _description("SW174", overrides=("CRV_ID",)),
                _description("SW173", overrides=("CRV_ID",)),
            ]
        return [_description(field) for field in fields]

    report = discover_field_documentation(("SW174", "SW173"), describe=_describe)

    assert report.override_fields == ("CRV_ID",)


def test_discover_field_documentation_pass_1_failure_leaves_pass_2_empty():
    def _describe(fields):
        raise RuntimeError("Bloomberg DAPI failed to open service //blp/apiflds")

    report = discover_field_documentation(describe=_describe)

    assert report.primary_descriptions == ()
    assert "failed to open service" in report.primary_error
    assert report.override_fields == ()
    assert report.override_descriptions == ()
    assert report.override_error is None


def test_discover_field_documentation_pass_2_failure_keeps_pass_1_results():
    calls = []

    def _describe(fields):
        calls.append(list(fields))
        if len(calls) == 1:
            return [_description("SW174", overrides=("CRV_ID",))]
        raise RuntimeError("Bloomberg DAPI request timed out")

    report = discover_field_documentation(("SW174",), describe=_describe)

    assert report.primary_error is None
    assert [d.field for d in report.primary_descriptions] == ["SW174"]
    assert report.override_fields == ("CRV_ID",)
    assert report.override_descriptions == ()
    assert "timed out" in report.override_error


def test_discover_field_documentation_sanitizes_documentation_text():
    def _describe(fields):
        return [
            _description(
                field,
                description="host:8194 leaked in description",
                documentation="uuid=12345678 leaked in documentation",
                detail="C:\\Users\\eddy\\leak.log",
            )
            for field in fields
        ]

    report = discover_field_documentation(("SW174",), describe=_describe)

    described = report.primary_descriptions[0]
    assert "host:8194" not in described.description
    assert "12345678" not in described.documentation
    assert "C:\\Users\\eddy" not in described.detail
    assert "<redacted host>" in described.description


# --- rendering / report writing ------------------------------------------------------


def test_build_report_and_render_round_trip():
    def _describe(fields):
        if fields == list(ROUND_2_CONFIRMED_FIELDS):
            return [
                _description(
                    field,
                    mnemonic=field,
                    datatype="BulkFormat",
                    description="a description",
                    overrides=("CRV_ID",) if field == "SW174" else (),
                    documentation="full documentation text",
                )
                for field in fields
            ]
        return [_description(field, description="override description") for field in fields]

    report = discover_field_documentation(describe=_describe)
    data = build_report(report)

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "SW174" in markdown
    assert "BulkFormat" in markdown
    assert "full documentation text" in markdown
    assert "CRV_ID" in markdown
    assert "round 2 workstation" in markdown.lower()
    assert "SW174" in as_json


def test_write_report_writes_both_files(tmp_path):
    def _describe(fields):
        return [_description(field) for field in fields]

    report = discover_field_documentation(describe=_describe)
    data = build_report(report)

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "SW174" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_reports_blpapi_not_installed(monkeypatch, capsys):
    def _raise_import_error(fields):
        raise ImportError("no module named blpapi")

    monkeypatch.setattr(module, "describe_fields", _raise_import_error)

    exit_code = main([])

    assert exit_code == 2
    assert "blpapi is not installed" in capsys.readouterr().err


def test_main_uses_the_default_confirmed_field_list(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _describe(fields):
        seen["fields"] = list(fields)
        return [_description(field) for field in fields]

    monkeypatch.setattr(module, "describe_fields", _describe)

    main([])

    assert seen["fields"] == list(ROUND_2_CONFIRMED_FIELDS)


def test_main_accepts_repeated_field_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def _describe(fields):
        seen.setdefault("calls", []).append(list(fields))
        return [_description(field) for field in fields]

    monkeypatch.setattr(module, "describe_fields", _describe)

    main(["--field", "SW174", "--field", "SW173"])

    assert seen["calls"][0] == ["SW174", "SW173"]


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    def _describe(fields):
        return [_description(field, mnemonic=field, overrides=("CRV_ID",)) for field in fields]

    monkeypatch.setattr(module, "describe_fields", _describe)

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "SW174" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
