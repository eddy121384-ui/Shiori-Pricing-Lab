"""Tests for `tools/bloomberg_usd_sofr_par_rate_curve_acceptance.py` (Issue #168).

Standalone workstation diagnostic CLI, not part of the production pricing
path. These tests prove: (1) it calls the production loader with the
loader's own default tenors unless overridden; (2) it reports each node's
tenor, security, raw LAST_PRICE, and normalized par_rate_percent, without
asserting a match to any prior observation itself; (3) a
`BLIBloombergDapiError`/`ImportError`/`ValueError` from the loader is
reported, never raised past `main`; (4) report rendering/writing and the
CLI's tenor-override contract.

No `blpapi` faking needed: `run_acceptance` takes the production loader as
an injectable `load` callable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_usd_sofr_par_rate_curve_acceptance as module  # noqa: E402
from bloomberg_usd_sofr_par_rate_curve_acceptance import (  # noqa: E402
    DEFAULT_USD_SOFR_TENORS,
    build_report,
    main,
    render_json,
    render_markdown,
    run_acceptance,
    write_report,
)

from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError  # noqa: E402
from shiori_pricing_lab.data.bloomberg_usd_sofr_par_rate_curve import (  # noqa: E402
    BloombergUsdSofrParRateCurveResult,
    BloombergUsdSofrParRatePoint,
)


def _point(tenor, security, raw_last_price, par_rate_percent) -> BloombergUsdSofrParRatePoint:
    return BloombergUsdSofrParRatePoint(
        tenor=tenor,
        security=security,
        raw_last_price=raw_last_price,
        par_rate_percent=par_rate_percent,
        source_system="BLOOMBERG_DAPI",
    )


def _success_result() -> BloombergUsdSofrParRateCurveResult:
    return BloombergUsdSofrParRateCurveResult(
        points=(
            _point("1Y", "USOSFR1 Curncy", "3.7500", 3.75),
            _point("2Y", "USOSFR2 Curncy", "3.4200", 3.42),
        )
    )


# --- run_acceptance: successful path --------------------------------------------------


def test_run_calls_loader_with_default_tenors_by_default():
    seen = {}

    def _fake_load(*, tenors):
        seen["tenors"] = tenors
        return _success_result()

    run_acceptance(load=_fake_load)

    assert seen["tenors"] == DEFAULT_USD_SOFR_TENORS


def test_run_calls_loader_with_explicit_tenor_override():
    seen = {}

    def _fake_load(*, tenors):
        seen["tenors"] = tenors
        return _success_result()

    run_acceptance(tenors=("1Y", "2Y", "5Y"), load=_fake_load)

    assert seen["tenors"] == ("1Y", "2Y", "5Y")


def test_run_reports_tenor_security_raw_price_and_par_rate_percent():
    report = run_acceptance(load=lambda *, tenors: _success_result())

    by_tenor = {n["tenor"]: n for n in report.nodes}
    assert by_tenor["1Y"]["security"] == "USOSFR1 Curncy"
    assert by_tenor["1Y"]["raw_last_price"] == "3.7500"
    assert by_tenor["1Y"]["par_rate_percent"] == pytest.approx(3.75)
    assert by_tenor["2Y"]["security"] == "USOSFR2 Curncy"
    assert by_tenor["2Y"]["par_rate_percent"] == pytest.approx(3.42)


def test_run_reports_source_system():
    report = run_acceptance(load=lambda *, tenors: _success_result())

    for node in report.nodes:
        assert node["source_system"] == "BLOOMBERG_DAPI"


def test_run_never_asserts_a_match_in_its_own_data():
    report = run_acceptance(load=lambda *, tenors: _success_result())

    assert report.status == "ok"
    assert report.error is None


# --- run_acceptance: error path ---------------------------------------------------------


def test_run_reports_a_bli_bloomberg_dapi_error_without_raising():
    def _fake_load(*, tenors):
        raise BLIBloombergDapiError(
            "Bloomberg DAPI securityError for 'USOSFR1 Curncy': BAD_SEC"
        )

    report = run_acceptance(load=_fake_load)

    assert report.status == "error"
    assert "BAD_SEC" in report.error
    assert report.nodes == ()


def test_run_reports_import_error_without_raising():
    def _fake_load(*, tenors):
        raise ImportError("no module named blpapi")

    report = run_acceptance(load=_fake_load)

    assert report.status == "error"
    assert "blpapi" in report.error


def test_run_reports_value_error_without_raising():
    def _fake_load(*, tenors):
        raise ValueError("tenor must be a non-blank string, got '   '")

    report = run_acceptance(load=_fake_load)

    assert report.status == "error"
    assert "non-blank" in report.error


# --- rendering / report writing ------------------------------------------------------


def test_build_report_and_render_round_trip():
    report = run_acceptance(load=lambda *, tenors: _success_result())
    data = build_report(report)

    markdown = render_markdown(data)
    as_json = render_json(data)

    assert "1Y" in markdown
    assert "3.75" in markdown
    assert "USOSFR1 Curncy" in markdown
    assert "1Y" in as_json


def test_render_markdown_includes_a_compact_full_curve_table():
    report = run_acceptance(load=lambda *, tenors: _success_result())
    data = build_report(report)

    markdown = render_markdown(data)

    assert "## Full curve, compact table" in markdown
    assert "| Tenor | Security | Raw LAST_PRICE | Par rate (percent) |" in markdown
    assert "| 1Y | USOSFR1 Curncy | 3.7500 | 3.75 |" in markdown
    assert "| 2Y | USOSFR2 Curncy | 3.4200 | 3.42 |" in markdown
    # One table row per node, in the same order as report.nodes.
    assert markdown.index("| 1Y |") < markdown.index("| 2Y |")
    assert "does not claim" in markdown.lower()


def test_render_markdown_omits_the_compact_table_when_there_are_no_nodes():
    empty_report = module.AcceptanceReport(
        generated_at="2026-08-11T00:00:00+00:00",
        tenors=("1Y",),
        status="error",
        error="Bloomberg DAPI securityError: BAD_SEC",
        nodes=(),
    )
    data = build_report(empty_report)

    markdown = render_markdown(data)

    assert "## Full curve, compact table" not in markdown


def test_write_report_writes_both_files(tmp_path):
    report = run_acceptance(load=lambda *, tenors: _success_result())
    data = build_report(report)

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "3.75" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_uses_default_tenors_when_not_supplied(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(tenors):
        seen["tenors"] = tenors
        return module.AcceptanceReport(
            generated_at="2026-08-11T00:00:00+00:00",
            tenors=tenors,
            status="ok",
            error=None,
            nodes=(),
        )

    monkeypatch.setattr(module, "run_acceptance", _fake_run)

    main(["--output-dir", str(tmp_path / "out")])

    assert seen["tenors"] == DEFAULT_USD_SOFR_TENORS


def test_main_accepts_a_comma_separated_tenor_override(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(tenors):
        seen["tenors"] = tenors
        return module.AcceptanceReport(
            generated_at="2026-08-11T00:00:00+00:00",
            tenors=tenors,
            status="ok",
            error=None,
            nodes=(),
        )

    monkeypatch.setattr(module, "run_acceptance", _fake_run)

    main(["--tenors", "1Y, 2Y ,5Y", "--output-dir", str(tmp_path / "out")])

    assert seen["tenors"] == ("1Y", "2Y", "5Y")


def test_main_returns_zero_on_success_and_nonzero_on_error(monkeypatch, tmp_path, capsys):
    def _ok(tenors):
        return module.AcceptanceReport(
            generated_at="2026-08-11T00:00:00+00:00",
            tenors=tenors,
            status="ok",
            error=None,
            nodes=(),
        )

    monkeypatch.setattr(module, "run_acceptance", _ok)
    exit_code_ok = main(["--output-dir", str(tmp_path / "out1")])
    assert exit_code_ok == 0

    def _error(tenors):
        return module.AcceptanceReport(
            generated_at="2026-08-11T00:00:00+00:00",
            tenors=tenors,
            status="error",
            error="Bloomberg DAPI securityError: BAD_SEC",
            nodes=(),
        )

    monkeypatch.setattr(module, "run_acceptance", _error)
    exit_code_error = main(["--output-dir", str(tmp_path / "out2")])
    assert exit_code_error == 1
    assert "BAD_SEC" in capsys.readouterr().err


def test_main_writes_report_and_prints_the_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        module,
        "run_acceptance",
        lambda tenors: module.AcceptanceReport(
            generated_at="2026-08-11T00:00:00+00:00",
            tenors=tenors,
            status="ok",
            error=None,
            nodes=(
                {
                    "tenor": "1Y",
                    "security": "USOSFR1 Curncy",
                    "raw_last_price": "3.7500",
                    "par_rate_percent": 3.75,
                    "source_system": "BLOOMBERG_DAPI",
                },
            ),
        ),
    )

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "tenor 1Y (USOSFR1 Curncy): par_rate_percent=3.75" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
