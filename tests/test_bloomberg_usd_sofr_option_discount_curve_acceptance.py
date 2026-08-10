"""Tests for `tools/bloomberg_usd_sofr_option_discount_curve_acceptance.py`
(Issue #165 production phase).

Standalone workstation diagnostic CLI, not part of the production pricing
path. These tests prove: (1) it calls the production loader with the
loader's own default tenors unless overridden; (2) it reports each node's
zero rate (decimal and recomputed percent), preserved discount-factor
evidence, and maturity, without asserting a match to any prior observation
itself; (3) a `BLIBloombergDapiError`/`ImportError`/`ValueError` from the
loader is reported, never raised past `main`; (4) report rendering/writing
and the CLI's tenor-override contract.

No `blpapi` faking needed: `run_acceptance` takes the production loader as
an injectable `load` callable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_usd_sofr_option_discount_curve_acceptance as module  # noqa: E402
from bloomberg_usd_sofr_option_discount_curve_acceptance import (  # noqa: E402
    DEFAULT_USD_SOFR_TENORS,
    build_report,
    main,
    render_json,
    render_markdown,
    run_acceptance,
    write_report,
)

from shiori_pricing_lab.data.bli_snapshot import (  # noqa: E402
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.data.bloomberg_bond_quote import BLIBloombergDapiError  # noqa: E402
from shiori_pricing_lab.data.bloomberg_option_discount_curve import (  # noqa: E402
    USD_SOFR_CURVE_ID,
    USD_SOFR_CURVE_NAME,
    BloombergDiscountFactorEvidence,
    BloombergUsdSofrOptionDiscountCurveResult,
)
from shiori_pricing_lab.products.enums import Currency  # noqa: E402


def _curve_point(tenor, rate) -> BLICurvePoint:
    return BLICurvePoint(
        curve_id=USD_SOFR_CURVE_ID,
        curve_name=USD_SOFR_CURVE_NAME,
        currency=Currency.USD,
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        tenor=tenor,
        rate=rate,
        rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
        source_system="BLOOMBERG_DAPI",
        status=BLIMarketDataStatus.ACTIVE,
    )


def _evidence(
    tenor, security, raw_last_price, discount_factor, maturity
) -> BloombergDiscountFactorEvidence:
    return BloombergDiscountFactorEvidence(
        tenor=tenor,
        security=security,
        raw_last_price=raw_last_price,
        discount_factor=discount_factor,
        maturity=maturity,
    )


def _success_result() -> BloombergUsdSofrOptionDiscountCurveResult:
    return BloombergUsdSofrOptionDiscountCurveResult(
        curve_points=(_curve_point("1Y", 0.0175), _curve_point("2Y", 0.0325)),
        discount_factor_evidence=(
            _evidence("1Y", "S0490D 1Y BLC2 Curncy", "0.98", 0.98, "2027-08-10"),
            _evidence("2Y", "S0490D 2Y BLC2 Curncy", "0.94", 0.94, "2028-08-10"),
        ),
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


def test_run_reports_zero_rate_decimal_and_recomputed_percent():
    report = run_acceptance(load=lambda *, tenors: _success_result())

    by_tenor = {n["tenor"]: n for n in report.nodes}
    assert by_tenor["1Y"]["zero_rate_decimal"] == pytest.approx(0.0175)
    assert by_tenor["1Y"]["zero_rate_percent_recomputed"] == pytest.approx(1.75)
    assert by_tenor["2Y"]["zero_rate_decimal"] == pytest.approx(0.0325)
    assert by_tenor["2Y"]["zero_rate_percent_recomputed"] == pytest.approx(3.25)


def test_run_reports_preserved_discount_factor_evidence():
    report = run_acceptance(load=lambda *, tenors: _success_result())

    by_tenor = {n["tenor"]: n for n in report.nodes}
    assert by_tenor["1Y"]["discount_factor"] == pytest.approx(0.98)
    assert by_tenor["1Y"]["discount_factor_raw_last_price"] == "0.98"
    assert by_tenor["1Y"]["discount_factor_security"] == "S0490D 1Y BLC2 Curncy"
    assert by_tenor["1Y"]["maturity"] == "2027-08-10"


def test_run_flags_discount_factor_in_zero_one_range_as_a_fact_not_a_verdict():
    report = run_acceptance(load=lambda *, tenors: _success_result())

    by_tenor = {n["tenor"]: n for n in report.nodes}
    assert by_tenor["1Y"]["discount_factor_in_zero_one_range"] is True


def test_run_never_asserts_a_match_in_its_own_data():
    report = run_acceptance(load=lambda *, tenors: _success_result())

    assert report.status == "ok"
    assert report.error is None


# --- run_acceptance: error path ---------------------------------------------------------


def test_run_reports_a_bli_bloomberg_dapi_error_without_raising():
    def _fake_load(*, tenors):
        raise BLIBloombergDapiError(
            "Bloomberg DAPI securityError for 'S0490Z 1Y BLC2 Curncy': BAD_SEC"
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
    assert "0.0175" in markdown
    assert "0.98" in markdown
    assert "1Y" in as_json


def test_write_report_writes_both_files(tmp_path):
    report = run_acceptance(load=lambda *, tenors: _success_result())
    data = build_report(report)

    markdown_path, json_path = write_report(data, tmp_path / "out")

    assert markdown_path.exists()
    assert json_path.exists()
    assert "0.98" in markdown_path.read_text(encoding="utf-8")


# --- CLI -------------------------------------------------------------------------


def test_main_uses_default_tenors_when_not_supplied(monkeypatch, tmp_path):
    seen = {}

    def _fake_run(tenors):
        seen["tenors"] = tenors
        return module.AcceptanceReport(
            generated_at="2026-08-10T00:00:00+00:00",
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
            generated_at="2026-08-10T00:00:00+00:00",
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
            generated_at="2026-08-10T00:00:00+00:00",
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
            generated_at="2026-08-10T00:00:00+00:00",
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
            generated_at="2026-08-10T00:00:00+00:00",
            tenors=tenors,
            status="ok",
            error=None,
            nodes=(
                {
                    "tenor": "1Y",
                    "curve_id": USD_SOFR_CURVE_ID,
                    "rate_basis": "CONTINUOUS_ZERO_RATE",
                    "zero_rate_decimal": 0.0175,
                    "zero_rate_percent_recomputed": 1.75,
                    "discount_factor_security": "S0490D 1Y BLC2 Curncy",
                    "discount_factor": 0.98,
                    "discount_factor_raw_last_price": "0.98",
                    "discount_factor_in_zero_one_range": True,
                    "maturity": "2027-08-10",
                },
            ),
        ),
    )

    exit_code = main(["--output-dir", str(tmp_path / "out")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "tenor 1Y: rate=0.0175" in out
    assert str((tmp_path / "out" / module.MARKDOWN_FILENAME).resolve()) in out
    assert (tmp_path / "out" / module.MARKDOWN_FILENAME).exists()
    assert (tmp_path / "out" / module.JSON_FILENAME).exists()
