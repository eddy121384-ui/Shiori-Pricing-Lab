"""Tests for `tools/ust_s490_repo_carry_forward_parity.py` (Issue #173).

The tool's report-building and rendering are exercised end to end with the
real, unmodified `build_request_from_standalone_option_case`, over the
repository's own bundled sanitized-synthetic case, with the Bloomberg curve
acquisition replaced by an injected synthetic Curve #490-shaped node set --
the same `load=`/injectable-callable seam
`test_bloomberg_usd_sofr_par_rate_curve_acceptance.py` already uses. No
Bloomberg call, no clock read, and no real OVME number appears anywhere in
this file: the "observed OVME forward" values below are obviously synthetic
inputs used only to prove the residual arithmetic and the report shape.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import ust_s490_repo_carry_forward_parity as parity  # noqa: E402

from shiori_pricing_lab.data.bloomberg_option_discount_curve import (  # noqa: E402
    USD_SOFR_CURVE_ID,
    USD_SOFR_SOURCE_SYSTEM,
)
from shiori_pricing_lab.pricing.bli_quantlib_bond_adapter import (  # noqa: E402
    is_quantlib_available,
)
from shiori_pricing_lab.pricing.bli_s490_funding_resolver import (  # noqa: E402
    S490FundingMethod,
)

requires_quantlib = pytest.mark.skipif(
    not is_quantlib_available(), reason="QuantLib is not installed in this environment"
)

BASE_CASE_PATH = PROJECT_ROOT / "examples" / "standalone_option_case.json"

# The bundled case's own valuation date, and a spot settlement one day
# later. Both are explicit: nothing here derives a settlement date.
SPOT_SETTLEMENT_DATE = "2026-07-02"

# The bundled synthetic bullet pays coupons on 15 June / 15 December, so
# these three horizons are all coupon-free from the spot settlement above.
THREE_HORIZONS = ("2026-08-03", "2026-10-01", "2026-12-01")

_SYNTHETIC_NODES: tuple[tuple[str, int, float], ...] = (
    ("1W", 7, 0.0380),
    ("1M", 31, 0.0378),
    ("3M", 92, 0.0372),
    ("6M", 184, 0.0364),
    ("1Y", 365, 0.0350),
    ("2Y", 730, 0.0340),
)


def _load_base_case() -> dict:
    return json.loads(BASE_CASE_PATH.read_text(encoding="utf-8"))


def _synthetic_curve_point_dicts(valuation_date: str) -> list[dict]:
    """Curve #490-shaped case-JSON rows -- synthetic rates, real row shape."""

    as_of = date.fromisoformat(valuation_date)
    return [
        {
            "curve_id": "SYNTHETIC_CURVE_490_SHAPED_TEST_CURVE",
            "curve_name": "SYNTHETIC_CURVE_490_SHAPED_TEST_CURVE",
            "currency": "USD",
            "curve_purpose": "OPTION_DISCOUNT_CURVE",
            "tenor": tenor,
            "rate": rate,
            "rate_basis": "CONTINUOUS_ZERO_RATE",
            "source_system": "SYNTHETIC_TEST_SOURCE",
            "status": "ACTIVE",
            "maturity_date": (as_of + timedelta(days=days)).isoformat(),
        }
        for tenor, days, rate in _SYNTHETIC_NODES
    ]


def _inject_synthetic_curve(case: dict) -> dict:
    """Stand-in for the Issue #171 live Curve #490 injector."""

    return {**case, "curve_points": _synthetic_curve_point_dicts(case["valuation_date"])}


def _with_production_identity_curve(case: dict) -> dict:
    """The same synthetic rates, stamped with the production curve identity.

    Used only to exercise the production-curve gate's *passing* branch and
    the CLI happy path. The rates are still the obviously-synthetic ones
    above -- this fixture asserts nothing about any real Bloomberg value; it
    only carries the two identity fields the gate checks.
    """

    points = [
        {**point, "curve_id": USD_SOFR_CURVE_ID, "source_system": USD_SOFR_SOURCE_SYSTEM}
        for point in _synthetic_curve_point_dicts(case["valuation_date"])
    ]
    return {**case, "curve_points": points}


def _horizons(*specs: str) -> tuple[parity.ParityHorizon, ...]:
    return tuple(parity.parse_horizon_argument(spec) for spec in specs)


# --- Treasury fraction display ------------------------------------------------------


@pytest.mark.parametrize(
    "decimal_price, expected",
    [
        (98.5, "98-16"),
        (98.515625, "98-16+"),
        (99.1015625, "99-032"),
        (99.12109375, "99-037"),
        (100.0, "100-00"),
        (99.03125, "99-01"),
    ],
)
def test_a_price_renders_as_the_same_treasury_quote_the_page_already_parses(
    decimal_price, expected
):
    # The exact inverse of script.js::parseTreasuryQuote's own worked
    # examples (Issue #161), so a number Eddy compares against OVME reads
    # the way his screen does.
    assert parity.format_price_as_treasury_fraction(decimal_price) == expected


def test_an_arbitrary_decimal_is_rounded_to_the_nearest_eighth_of_a_32nd():
    # A derived forward is an arbitrary decimal and cannot be an exact
    # 32nd; the display rounds, and every report carries the exact decimal
    # beside it.
    assert parity.format_price_as_treasury_fraction(98.5 + 0.0001) == "98-16"
    assert parity.format_price_as_treasury_fraction(98.5 + 1 / 256) == "98-161"


def test_a_negative_price_keeps_its_sign_on_the_magnitude():
    assert parity.format_price_as_treasury_fraction(-98.5) == "-98-16"


# --- --horizon parsing --------------------------------------------------------------


def test_a_horizon_without_an_ovme_number_asks_for_no_residual():
    horizon = parity.parse_horizon_argument("2026-11-13")
    assert horizon.forward_settlement_date == "2026-11-13"
    assert horizon.observed_ovme_forward_clean_price_per_100 is None


def test_a_horizon_with_an_ovme_number_carries_it_as_a_decimal():
    horizon = parity.parse_horizon_argument(" 2026-11-13 = 99.828125 ")
    assert horizon.forward_settlement_date == "2026-11-13"
    assert horizon.observed_ovme_forward_clean_price_per_100 == 99.828125


def test_a_treasury_quote_string_is_not_silently_accepted_as_an_ovme_number():
    with pytest.raises(ValueError, match="not a decimal price per 100"):
        parity.parse_horizon_argument("2026-11-13=99-16+")


def test_a_horizon_with_no_date_is_refused():
    with pytest.raises(ValueError, match="missing a forward settlement date"):
        parity.parse_horizon_argument("=99.5")


@pytest.mark.parametrize("bad", ["nan", "-nan", "inf", "-inf", "infinity", "1e999"])
def test_a_non_finite_ovme_number_is_refused_before_a_horizon_exists(bad):
    # float() accepts all of these, and they would otherwise produce
    # non-finite residuals and non-standard NaN/Infinity JSON tokens --
    # malformed input masquerading as a supplied Bloomberg observation
    # (Codex P2 review of PR #174).
    with pytest.raises(ValueError, match="not a finite decimal price per 100"):
        parity.parse_horizon_argument(f"2026-11-13={bad}")


# --- the production-curve gate ------------------------------------------------------


def test_only_rows_carrying_the_production_curve_identity_count_as_production():
    from shiori_pricing_lab.app.standalone_option_workbench import (
        build_request_from_standalone_option_case,
    )

    production = build_request_from_standalone_option_case(
        _with_production_identity_curve(_load_base_case())
    )
    assert (
        parity.classify_curve_provenance(production.market_data_snapshot.curve_points)
        == parity.PRODUCTION_CURVE_PROVENANCE
    )

    synthetic = build_request_from_standalone_option_case(
        _inject_synthetic_curve(_load_base_case())
    )
    assert (
        parity.classify_curve_provenance(synthetic.market_data_snapshot.curve_points)
        == parity.NON_PRODUCTION_CURVE_PROVENANCE
    )

    # One foreign row is enough -- there is no majority verdict.
    mixed_case = _with_production_identity_curve(_load_base_case())
    mixed_case["curve_points"][2] = {
        **mixed_case["curve_points"][2],
        "source_system": "SHIORI_MANUAL_OPTION_DISCOUNT_CURVE",
    }
    mixed = build_request_from_standalone_option_case(mixed_case)
    assert (
        parity.classify_curve_provenance(mixed.market_data_snapshot.curve_points)
        == parity.NON_PRODUCTION_CURVE_PROVENANCE
    )


def test_an_empty_curve_is_never_classified_as_production():
    assert parity.classify_curve_provenance(()) == parity.NON_PRODUCTION_CURVE_PROVENANCE


@requires_quantlib
def test_a_manual_or_fixture_curve_blocks_the_whole_run_by_default():
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(*THREE_HORIZONS),
        inject_curve=_inject_synthetic_curve,
    )
    assert report.status == "error"
    assert "not the production Bloomberg Curve #490 acquisition" in report.error
    assert USD_SOFR_CURVE_ID in report.error
    assert report.horizons == ()


@requires_quantlib
def test_a_production_identity_curve_passes_the_gate_and_is_stamped_as_such():
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(THREE_HORIZONS[1]),
        inject_curve=_with_production_identity_curve,
    )
    assert report.status == "ok"
    assert report.case["curve_provenance"] == parity.PRODUCTION_CURVE_PROVENANCE
    assert report.horizons[0]["status"] == "ok", report.horizons[0]["error"]


@requires_quantlib
def test_a_gate_disabled_run_is_stamped_and_warned_about_as_non_evidence():
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(THREE_HORIZONS[1]),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )
    assert report.status == "ok"
    assert report.case["curve_provenance"] == parity.NON_PRODUCTION_CURVE_PROVENANCE

    markdown = parity.render_markdown(parity.build_report(report))
    assert parity.NON_PRODUCTION_CURVE_PROVENANCE in markdown
    assert "not S490 acceptance evidence" in markdown


def test_the_production_curve_gate_is_on_by_default_and_is_not_a_cli_option():
    # There must be no command-line way to produce a parity report from a
    # non-production curve; the seam exists for tests only.
    import inspect

    signature = inspect.signature(parity.run_parity)
    assert signature.parameters["require_production_curve"].default is True

    source = Path(parity.__file__).read_text(encoding="utf-8")
    assert "add_argument" in source
    assert "require-production-curve" not in source
    assert "allow-non-production-curve" not in source


# --- the parity run -----------------------------------------------------------------


@requires_quantlib
def test_three_horizons_each_re_derive_funding_and_a_forward():
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(*THREE_HORIZONS),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )

    assert report.status == "ok"
    assert report.error is None
    assert len(report.horizons) == 3

    terms = []
    for row, forward_date in zip(report.horizons, THREE_HORIZONS, strict=True):
        assert row["status"] == "ok", row["error"]
        assert row["forward_settlement_date"] == forward_date
        assert row["funding"]["method"] == S490FundingMethod.TERM_RATE_FROM_CURVE_AS_OF.value
        assert row["funding"]["derived_repo_rate_decimal"] > 0
        assert row["forward"]["spot_settlement_date"] == SPOT_SETTLEMENT_DATE
        assert row["forward_clean_price_per_100"] > 0
        assert row["forward_clean_price_treasury_fraction"]
        # No OVME number supplied -> no residual invented.
        assert row["residual_decimal_per_100"] is None
        assert row["residual_treasury_ticks_32nds"] is None
        terms.append(row["funding"]["repo_term_days"])

    # Changing the expiry really does change the horizon and the funding.
    assert terms == sorted(terms) and len(set(terms)) == 3
    rates = {row["funding"]["derived_repo_rate_decimal"] for row in report.horizons}
    assert len(rates) == 3


@requires_quantlib
def test_the_residual_is_the_derived_forward_minus_the_supplied_ovme_forward():
    observed = 101.0  # synthetic stand-in for an OVME F reading, not real evidence
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(f"{THREE_HORIZONS[1]}={observed}"),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )

    row = report.horizons[0]
    assert row["status"] == "ok", row["error"]
    assert row["observed_ovme_forward_clean_price_per_100"] == observed
    assert row["residual_decimal_per_100"] == pytest.approx(
        row["forward_clean_price_per_100"] - observed
    )
    assert row["residual_treasury_ticks_32nds"] == pytest.approx(
        row["residual_decimal_per_100"] * 32
    )


@requires_quantlib
def test_the_forward_is_exactly_the_two_reviewed_primitives_composed():
    from shiori_pricing_lab.app.standalone_option_workbench import (
        build_request_from_standalone_option_case,
    )
    from shiori_pricing_lab.pricing.bli_repo_carry_forward import (
        repo_carry_forward_clean_price,
    )
    from shiori_pricing_lab.pricing.bli_s490_funding_resolver import (
        resolve_s490_repo_carry_funding,
    )

    case = _inject_synthetic_curve(_load_base_case())
    request = build_request_from_standalone_option_case(case)
    funding = resolve_s490_repo_carry_funding(
        request.market_data_snapshot.curve_points,
        currency=request.bond_option.currency,
        curve_as_of_date=request.valuation_date,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=THREE_HORIZONS[1],
    )
    expected = repo_carry_forward_clean_price(
        bond=request.resolved_bond_reference_data,
        spot_clean_price_per_100=request.market_data_snapshot.bond_quote.clean_price_per_100,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=THREE_HORIZONS[1],
        repo_rate_decimal=funding.derived_repo_rate_decimal,
    )

    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(THREE_HORIZONS[1]),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )
    assert report.horizons[0]["forward_clean_price_per_100"] == pytest.approx(
        expected.forward_clean_price_per_100
    )


@requires_quantlib
def test_the_selected_funding_method_is_passed_through_and_reported():
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date="2026-07-12",  # inside the curve, so both methods evaluate
        horizons=_horizons(THREE_HORIZONS[2]),
        method=S490FundingMethod.FORWARD_FORWARD_DF_RATIO,
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )
    assert report.case["s490_funding_method"] == S490FundingMethod.FORWARD_FORWARD_DF_RATIO.value
    row = report.horizons[0]
    assert row["status"] == "ok", row["error"]
    assert row["funding"]["method"] == S490FundingMethod.FORWARD_FORWARD_DF_RATIO.value
    assert [evaluation["label"] for evaluation in row["funding"]["curve_evaluations"]] == [
        "SPOT_SETTLEMENT",
        "FORWARD_SETTLEMENT",
    ]


@requires_quantlib
def test_one_bad_horizon_is_reported_on_its_own_row_and_the_others_still_price():
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(THREE_HORIZONS[0], "2032-07-01", THREE_HORIZONS[1]),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )

    assert report.status == "ok"
    assert [row["status"] for row in report.horizons] == ["ok", "error", "ok"]
    assert "outside the node range" in report.horizons[1]["error"]
    assert report.horizons[1]["forward_clean_price_per_100"] is None


@requires_quantlib
def test_an_interim_coupon_horizon_is_reported_not_priced():
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons("2026-12-20"),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )
    row = report.horizons[0]
    assert row["status"] == "error"
    assert "RepoCarryInterimCouponUnsupportedError" in row["error"]


def test_a_failed_curve_acquisition_fails_the_whole_run_with_no_horizons():
    def _fail(case: dict) -> dict:
        raise RuntimeError("Bloomberg DAPI session failed to start")

    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(*THREE_HORIZONS),
        inject_curve=_fail,
    )

    assert report.status == "error"
    assert "Bloomberg DAPI session failed to start" in report.error
    assert report.horizons == ()
    assert report.case == {}


def test_a_yield_only_quote_blocks_the_run_rather_than_inventing_a_spot_price():
    case = _load_base_case()
    case["bond_quote"] = {**case["bond_quote"], "clean_price_per_100": None, "yield_value": 0.031}

    report = parity.run_parity(
        case=case,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(THREE_HORIZONS[0]),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )
    assert report.status == "error"
    assert "clean_price_per_100" in report.error


# --- rendering ----------------------------------------------------------------------


@requires_quantlib
def test_the_markdown_report_shows_both_residual_units_and_names_the_method(tmp_path):
    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(f"{THREE_HORIZONS[0]}=101.0", THREE_HORIZONS[1]),
        require_production_curve=False,
        inject_curve=_inject_synthetic_curve,
    )
    data = parity.build_report(report)
    markdown = parity.render_markdown(data)

    assert "Residual (ticks)" in markdown
    assert S490FundingMethod.TERM_RATE_FROM_CURVE_AS_OF.value in markdown
    assert "asserts no Bloomberg parity itself" in markdown
    assert "OVME F forward: not supplied -- no residual computed" in markdown

    markdown_path, json_path = parity.write_report(data, tmp_path / "out")
    assert markdown_path.read_text(encoding="utf-8") == markdown
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "ok"


def test_a_failed_run_still_renders_a_report_naming_the_error():
    def _fail(case: dict) -> dict:
        raise RuntimeError("no Bloomberg terminal here")

    report = parity.run_parity(
        case=_load_base_case(),
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        horizons=_horizons(THREE_HORIZONS[0]),
        inject_curve=_fail,
    )
    markdown = parity.render_markdown(parity.build_report(report))
    assert "Status: error" in markdown
    assert "no Bloomberg terminal here" in markdown


# --- CLI ----------------------------------------------------------------------------


def test_a_malformed_horizon_argument_exits_before_any_case_is_read(tmp_path):
    exit_code = parity.main(
        [
            "--case",
            str(tmp_path / "does-not-exist.json"),
            "--spot-settlement-date",
            SPOT_SETTLEMENT_DATE,
            "--horizon",
            "2026-11-13=not-a-price",
        ]
    )
    assert exit_code == 2


def test_an_unreadable_case_file_exits_without_calling_bloomberg(tmp_path, monkeypatch):
    def _must_not_run(**kwargs):
        raise AssertionError("run_parity must not be reached for an unreadable case file")

    monkeypatch.setattr(parity, "run_parity", _must_not_run)
    exit_code = parity.main(
        [
            "--case",
            str(tmp_path / "missing.json"),
            "--spot-settlement-date",
            SPOT_SETTLEMENT_DATE,
            "--horizon",
            THREE_HORIZONS[0],
        ]
    )
    assert exit_code == 2


def test_the_live_curve_injector_is_what_the_run_reaches_for_by_default():
    # Proves the Issue #171 production injector is the wiring, without
    # making a Bloomberg call to do it.
    import inspect

    default = inspect.signature(parity.run_parity).parameters["inject_curve"].default
    assert default is parity.inject_live_option_discount_curve_if_absent


@requires_quantlib
def test_the_cli_refuses_a_case_priced_from_manual_or_fixture_curve_rates(tmp_path, capsys):
    # The bundled example's own curve nodes are sample data, so the real
    # injector leaves them untouched and the production-curve gate stops the
    # run before a single "S490" number is reported (Codex P1 review).
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_load_base_case()), encoding="utf-8")

    exit_code = parity.main(
        [
            "--case",
            str(case_path),
            "--spot-settlement-date",
            SPOT_SETTLEMENT_DATE,
            "--horizon",
            THREE_HORIZONS[1],
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 1
    written = json.loads((tmp_path / "out" / parity.JSON_FILENAME).read_text(encoding="utf-8"))
    assert written["status"] == "error"
    assert "not the production Bloomberg Curve #490 acquisition" in written["error"]
    assert written["horizons"] == []


@requires_quantlib
def test_the_cli_writes_both_reports_and_exits_zero(tmp_path, capsys):
    # A case whose rows carry the production curve identity passes the gate,
    # so the real injector leaves it untouched and no Bloomberg call is
    # made -- the same "a case with explicit nodes is used exactly as
    # supplied" contract Issue #171 established.
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(_with_production_identity_curve(_load_base_case())), encoding="utf-8"
    )

    exit_code = parity.main(
        [
            "--case",
            str(case_path),
            "--spot-settlement-date",
            SPOT_SETTLEMENT_DATE,
            "--horizon",
            THREE_HORIZONS[0],
            "--horizon",
            f"{THREE_HORIZONS[1]}=101.0",
            "--horizon",
            THREE_HORIZONS[2],
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    written = json.loads(
        (tmp_path / "out" / parity.JSON_FILENAME).read_text(encoding="utf-8")
    )
    assert written["status"] == "ok"
    assert len(written["horizons"]) == 3
    assert "UST S490 repo-carry forward vs OVME F parity" in capsys.readouterr().out
