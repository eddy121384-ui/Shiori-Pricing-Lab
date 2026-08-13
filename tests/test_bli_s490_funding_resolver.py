"""Tests for `pricing/bli_s490_funding_resolver.py` (Issue #173).

Every curve in this file is a **local synthetic fixture shaped like** the
production Bloomberg Curve #490 rows (``OPTION_DISCOUNT_CURVE`` /
``CONTINUOUS_ZERO_RATE``, each node carrying its own ``maturity_date``) --
no Bloomberg value, OVME observation, or market data of any kind is
committed here, and no test asserts agreement with any real Bloomberg
number. What is pinned is the *transformation*: which coordinate is read,
which discount factors are used, how the carry factor and the simple
ACT/360 repo rate follow from them, and that both labeled methods fail
closed instead of extrapolating.

Every expected value is recomputed inside the test from the same
already-reviewed curve chain (`discount_factor_from_continuous_zero_curve`)
this module composes, never hand-typed.

QuantLib is not needed anywhere in this file: the funding resolver touches
no bond schedule or accrual.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePoint,
    BLICurvePurpose,
    BLICurveRateBasis,
    BLIMarketDataStatus,
)
from shiori_pricing_lab.pricing.bli_curve_discount_factor import (
    discount_factor_from_continuous_zero_curve,
)
from shiori_pricing_lab.pricing.bli_repo_carry_forward import (
    carry_factor_from_simple_repo_rate,
)
from shiori_pricing_lab.pricing.bli_s490_funding_resolver import (
    CURVE_COORDINATE_DAY_COUNT_CONVENTION,
    DEFAULT_S490_FUNDING_METHOD,
    S490_SOURCE_REPRESENTATION,
    S490FundingMethod,
    resolve_s490_repo_carry_funding,
)

CURVE_AS_OF_DATE = "2026-08-12"

# A Curve #490-shaped synthetic node set: the same tenor labels and node
# shape the production loader returns (week tenors included, each with
# Bloomberg's own maturity date), with obviously synthetic zero rates.
_SYNTHETIC_NODES: tuple[tuple[str, int, float], ...] = (
    ("1W", 7, 0.0380),
    ("1M", 31, 0.0378),
    ("3M", 92, 0.0372),
    ("6M", 184, 0.0364),
    ("1Y", 365, 0.0350),
    ("2Y", 730, 0.0340),
)


def _maturity(days: int) -> str:
    return (date.fromisoformat(CURVE_AS_OF_DATE) + timedelta(days=days)).isoformat()


def _synthetic_s490_curve() -> tuple[BLICurvePoint, ...]:
    return tuple(
        BLICurvePoint(
            curve_id="SYNTHETIC_CURVE_490_SHAPED_TEST_CURVE",
            curve_name="SYNTHETIC_CURVE_490_SHAPED_TEST_CURVE",
            currency="USD",
            curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
            tenor=tenor,
            rate=rate,
            rate_basis=BLICurveRateBasis.CONTINUOUS_ZERO_RATE,
            source_system="SYNTHETIC_TEST_SOURCE",
            status=BLIMarketDataStatus.ACTIVE,
            maturity_date=_maturity(days),
        )
        for tenor, days, rate in _SYNTHETIC_NODES
    )


def _expected_discount_factor(curve, coordinate: float) -> float:
    return discount_factor_from_continuous_zero_curve(
        curve,
        currency="USD",
        curve_purpose=BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        target_year_fraction=coordinate,
        as_of_date=CURVE_AS_OF_DATE,
    )


SPOT_SETTLEMENT_DATE = "2026-08-13"

# Three different horizons, as Issue #173 requires: one landing exactly on
# the curve's own 3M node coordinate, and two that do not.
THREE_HORIZONS = ("2026-09-15", "2026-11-13", "2027-02-15")


def _resolve(forward_settlement_date: str, **kwargs):
    return resolve_s490_repo_carry_funding(
        _synthetic_s490_curve(),
        currency="USD",
        curve_as_of_date=CURVE_AS_OF_DATE,
        spot_settlement_date=SPOT_SETTLEMENT_DATE,
        forward_settlement_date=forward_settlement_date,
        **kwargs,
    )


# --- the default (term-rate) method ------------------------------------------------


def test_the_default_method_is_the_term_rate_reading():
    assert DEFAULT_S490_FUNDING_METHOD is S490FundingMethod.TERM_RATE_FROM_CURVE_AS_OF
    assert _resolve(THREE_HORIZONS[0]).method == DEFAULT_S490_FUNDING_METHOD.value


@pytest.mark.parametrize("forward_settlement_date", THREE_HORIZONS)
def test_the_term_rate_method_reads_the_curve_once_at_the_repo_term_length(
    forward_settlement_date,
):
    funding = _resolve(forward_settlement_date)

    term_days = (
        date.fromisoformat(forward_settlement_date) - date.fromisoformat(SPOT_SETTLEMENT_DATE)
    ).days
    expected_coordinate = term_days / 365.0
    expected_discount_factor = _expected_discount_factor(
        _synthetic_s490_curve(), expected_coordinate
    )

    assert funding.repo_term_days == term_days
    assert funding.repo_term_year_fraction == pytest.approx(term_days / 360.0)
    assert funding.curve_coordinate_day_count_convention == CURVE_COORDINATE_DAY_COUNT_CONVENTION

    assert len(funding.curve_evaluations) == 1
    evaluation = funding.curve_evaluations[0]
    assert evaluation.label == "REPO_TERM_FROM_CURVE_AS_OF"
    # The date read is the term length from the curve as-of date, which is
    # deliberately neither settlement date.
    assert evaluation.target_date == (
        date.fromisoformat(CURVE_AS_OF_DATE) + timedelta(days=term_days)
    ).isoformat()
    assert evaluation.curve_year_fraction == pytest.approx(expected_coordinate)
    assert evaluation.discount_factor == pytest.approx(expected_discount_factor)

    assert funding.carry_factor == pytest.approx(1.0 / expected_discount_factor)


def test_a_horizon_landing_on_a_curve_node_uses_that_node_rate_unchanged():
    # 2026-08-13 -> 2026-11-13 is 92 days, exactly the synthetic 3M node's
    # own maturity offset, so the interpolation step is an identity and the
    # discount factor is the node's own exp(-z*t).
    from math import exp

    funding = _resolve("2026-11-13")
    node_rate = dict((tenor, rate) for tenor, _days, rate in _SYNTHETIC_NODES)["3M"]
    coordinate = 92 / 365.0
    assert funding.curve_evaluations[0].discount_factor == pytest.approx(
        exp(-node_rate * coordinate)
    )


# --- the forward-forward method ----------------------------------------------------


def test_the_forward_forward_method_reads_both_settlement_dates():
    # The spot settlement here is inside the curve's node range, so the
    # forward-forward reading is evaluable; see the short-end test below
    # for the T+1 case that is not.
    spot = "2026-08-23"
    funding = resolve_s490_repo_carry_funding(
        _synthetic_s490_curve(),
        currency="USD",
        curve_as_of_date=CURVE_AS_OF_DATE,
        spot_settlement_date=spot,
        forward_settlement_date="2026-11-23",
        method=S490FundingMethod.FORWARD_FORWARD_DF_RATIO,
    )

    curve = _synthetic_s490_curve()
    spot_coordinate = 11 / 365.0
    forward_coordinate = 103 / 365.0
    expected_spot_df = _expected_discount_factor(curve, spot_coordinate)
    expected_forward_df = _expected_discount_factor(curve, forward_coordinate)

    labels = [evaluation.label for evaluation in funding.curve_evaluations]
    assert labels == ["SPOT_SETTLEMENT", "FORWARD_SETTLEMENT"]
    assert funding.curve_evaluations[0].discount_factor == pytest.approx(expected_spot_df)
    assert funding.curve_evaluations[1].discount_factor == pytest.approx(expected_forward_df)
    assert funding.carry_factor == pytest.approx(expected_spot_df / expected_forward_df)
    assert funding.method == S490FundingMethod.FORWARD_FORWARD_DF_RATIO.value


def test_the_forward_forward_method_fails_closed_at_the_curve_short_end():
    # A T+1 spot settlement is inside the 1W shortest node, and the shared
    # zero-curve chain does not extrapolate. Issue #173's own instruction is
    # to report that, not to invent a short-end rate.
    with pytest.raises(ValueError, match="outside the node range"):
        _resolve(THREE_HORIZONS[0], method=S490FundingMethod.FORWARD_FORWARD_DF_RATIO)


def test_the_two_methods_disagree_and_neither_is_silently_substituted():
    spot = "2026-08-23"
    forward = "2026-11-23"
    term_rate = resolve_s490_repo_carry_funding(
        _synthetic_s490_curve(),
        currency="USD",
        curve_as_of_date=CURVE_AS_OF_DATE,
        spot_settlement_date=spot,
        forward_settlement_date=forward,
        method=S490FundingMethod.TERM_RATE_FROM_CURVE_AS_OF,
    )
    forward_forward = resolve_s490_repo_carry_funding(
        _synthetic_s490_curve(),
        currency="USD",
        curve_as_of_date=CURVE_AS_OF_DATE,
        spot_settlement_date=spot,
        forward_settlement_date=forward,
        method=S490FundingMethod.FORWARD_FORWARD_DF_RATIO,
    )
    assert term_rate.carry_factor != forward_forward.carry_factor
    assert term_rate.method != forward_forward.method


# --- shared step 3, provenance, and failure domains ---------------------------------


@pytest.mark.parametrize("forward_settlement_date", THREE_HORIZONS)
def test_the_derived_rate_reproduces_the_carry_factor_through_the_fpa_formula(
    forward_settlement_date,
):
    # Step 3 is the exact inverse of the FPA primitive's own 1 + r x t, so
    # the reported rate and the reported factor can never describe two
    # different fundings.
    funding = _resolve(forward_settlement_date)
    assert carry_factor_from_simple_repo_rate(
        repo_rate_decimal=funding.derived_repo_rate_decimal,
        repo_term_year_fraction=funding.repo_term_year_fraction,
    ) == pytest.approx(funding.carry_factor)


@pytest.mark.parametrize("method", list(S490FundingMethod))
def test_every_result_carries_its_method_and_provenance(method):
    spot = "2026-08-23"
    funding = resolve_s490_repo_carry_funding(
        _synthetic_s490_curve(),
        currency="USD",
        curve_as_of_date=CURVE_AS_OF_DATE,
        spot_settlement_date=spot,
        forward_settlement_date="2026-11-23",
        method=method,
    )
    assert funding.method == method.value
    assert "PROTOTYPE" in funding.method
    assert funding.source_representation == S490_SOURCE_REPRESENTATION
    assert funding.curve_ids == ("SYNTHETIC_CURVE_490_SHAPED_TEST_CURVE",)
    assert funding.source_systems == ("SYNTHETIC_TEST_SOURCE",)
    assert funding.curve_as_of_date == CURVE_AS_OF_DATE
    assert funding.spot_settlement_date == spot
    assert funding.forward_settlement_date == "2026-11-23"


def test_an_unknown_method_is_refused():
    with pytest.raises(ValueError, match="method must be one of"):
        _resolve(THREE_HORIZONS[0], method="S490_WHATEVER_LOOKS_CLOSEST")


def test_a_horizon_past_the_last_curve_node_is_refused_rather_than_extrapolated():
    with pytest.raises(ValueError, match="outside the node range"):
        _resolve("2029-08-13")


def test_a_forward_date_on_or_before_spot_settlement_is_refused():
    with pytest.raises(ValueError, match="strictly after"):
        _resolve(SPOT_SETTLEMENT_DATE)


def test_a_settlement_date_before_the_curve_as_of_date_is_refused():
    with pytest.raises(ValueError, match="must not be before curve_as_of_date"):
        resolve_s490_repo_carry_funding(
            _synthetic_s490_curve(),
            currency="USD",
            curve_as_of_date=CURVE_AS_OF_DATE,
            spot_settlement_date="2026-08-01",
            forward_settlement_date="2026-11-13",
            method=S490FundingMethod.FORWARD_FORWARD_DF_RATIO,
        )


def test_a_non_continuous_zero_rate_curve_row_is_refused_by_the_existing_chain():
    curve = tuple(
        replace(point, rate_basis=BLICurveRateBasis.SIMPLE_ZERO_RATE)
        for point in _synthetic_s490_curve()
    )
    with pytest.raises(ValueError):
        resolve_s490_repo_carry_funding(
            curve,
            currency="USD",
            curve_as_of_date=CURVE_AS_OF_DATE,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date=THREE_HORIZONS[0],
        )


def test_a_curve_with_no_usd_rows_is_refused_rather_than_falling_back():
    curve = tuple(replace(point, currency="EUR") for point in _synthetic_s490_curve())
    with pytest.raises(ValueError):
        resolve_s490_repo_carry_funding(
            curve,
            currency="USD",
            curve_as_of_date=CURVE_AS_OF_DATE,
            spot_settlement_date=SPOT_SETTLEMENT_DATE,
            forward_settlement_date=THREE_HORIZONS[0],
        )


def test_three_horizons_all_resolve_and_report_their_own_term():
    fundings = [_resolve(forward_date) for forward_date in THREE_HORIZONS]
    assert [funding.repo_term_days for funding in fundings] == [33, 92, 186]
    assert len({funding.derived_repo_rate_decimal for funding in fundings}) == 3
    for funding in fundings:
        assert funding.derived_repo_rate_decimal > 0
