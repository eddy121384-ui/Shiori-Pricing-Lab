"""Tests for `tools/ovme_vcub_dcf_convention_experiment.py` (Issue #192).

Deterministic and offline. Every volatility here is **synthetic** and is
labelled as such: no Bloomberg observation is pinned as regression truth,
and no test asserts that any candidate convention is the one Bloomberg
uses -- that remains RED per Annex A §A.8.5.

Every expected year fraction is written as the arithmetic a reader can
redo by hand (`59 / 366`, `128 / 365 + 1 + 365 / 366`), never a number copied out
of the implementation.

The six deterministic cases Issue #192 §B asks for are covered by:

1. `test_ordinary_non_leap_period_*` -- ordinary non-leap period;
2. `test_period_crossing_feb_29_*` -- period crossing Feb 29;
3. `test_exact_one_year_boundary_*` -- exact one-year-like boundary;
4. `test_multi_year_period_*` -- multi-year period;
5. `test_leap_period_separates_act_act_from_act_365f` -- a case where the
   two conventions differ by a non-zero amount;
6. `test_total_variance_equality_holds_for_a_controlled_fixture` -- the
   §A.8.5 total-variance equality itself.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

from ovme_vcub_dcf_convention_experiment import (  # noqa: E402
    CANDIDATE_DAY_COUNTS,
    CandidatePair,
    DateRole,
    DisplayedVol,
    DisplayRounding,
    build_date_roles,
    candidate_pairs,
    candidate_year_fractions,
    identical_multiplier_groups,
    implied_ratio_interval,
    is_consistent,
    main,
    pairs_without_guaranteed_separation,
    predicted_yield_vol_interval,
    render_report,
    separation,
    surviving_candidates,
)

NEAREST = DisplayRounding.NEAREST

ACT_ACT = CANDIDATE_DAY_COUNTS["ACT/ACT ISDA"]
ACT_365F = CANDIDATE_DAY_COUNTS["ACT/365F"]
ACT_360 = CANDIDATE_DAY_COUNTS["ACT/360"]


# --------------------------------------------------------------------------
# 1. ordinary non-leap period
# --------------------------------------------------------------------------


def test_ordinary_non_leap_period_year_fractions():
    start, end = date(2026, 8, 26), date(2026, 11, 26)  # 92 calendar days, no Feb 29

    assert ACT_365F(start, end) == pytest.approx(92 / 365, rel=0, abs=1e-15)
    assert ACT_360(start, end) == pytest.approx(92 / 360, rel=0, abs=1e-15)
    # 2026 is not a leap year and the period stays inside it, so ACT/ACT ISDA
    # collapses onto ACT/365F exactly.
    assert ACT_ACT(start, end) == pytest.approx(92 / 365, rel=0, abs=1e-15)


def test_ordinary_non_leap_period_cannot_separate_act_act_from_act_365f():
    """A short present-day expiry has *zero* discriminating power here.

    This is the experiment-design result, not an implementation detail: an
    observation on a period containing no Feb 29 can never distinguish
    ACT/ACT ISDA from ACT/365F, however precise the screen is.
    """

    role = DateRole("t0->TE", date(2026, 8, 26), date(2026, 11, 26))
    candidates = candidate_year_fractions([role])
    act_act_over_365f = CandidatePair(
        "t0->TE ACT/ACT ISDA",
        "t0->TE ACT/365F",
        candidates["t0->TE ACT/ACT ISDA"],
        candidates["t0->TE ACT/365F"],
    )
    assert act_act_over_365f.ratio == 1.0
    assert act_act_over_365f.vol_multiplier == 1.0


# --------------------------------------------------------------------------
# 2. period crossing Feb 29
# --------------------------------------------------------------------------


def test_period_crossing_feb_29_inside_one_leap_year():
    start, end = date(2028, 2, 1), date(2028, 3, 31)  # 29 + 30 = 59 days, all in 2028

    assert (end - start).days == 59
    assert ACT_ACT(start, end) == pytest.approx(59 / 366, rel=0, abs=1e-15)
    assert ACT_365F(start, end) == pytest.approx(59 / 365, rel=0, abs=1e-15)


def test_period_crossing_feb_29_across_a_year_boundary():
    start, end = date(2027, 12, 31), date(2028, 3, 1)  # 1 day in 2027, 60 days in 2028

    assert (end - start).days == 61
    assert ACT_ACT(start, end) == pytest.approx(1 / 365 + 60 / 366, rel=0, abs=1e-15)
    assert ACT_365F(start, end) == pytest.approx(61 / 365, rel=0, abs=1e-15)


# --------------------------------------------------------------------------
# 3. exact one-year-like boundary
# --------------------------------------------------------------------------


def test_exact_one_year_boundary_in_non_leap_years():
    start, end = date(2026, 8, 26), date(2027, 8, 26)  # 128 days of 2026 + 237 of 2027

    assert (end - start).days == 365
    assert ACT_365F(start, end) == 1.0
    assert ACT_ACT(start, end) == pytest.approx(128 / 365 + 237 / 365, rel=0, abs=1e-15)
    assert ACT_ACT(start, end) == pytest.approx(1.0, rel=0, abs=1e-15)
    assert ACT_360(start, end) == pytest.approx(365 / 360, rel=0, abs=1e-15)


def test_exact_one_year_boundary_spanning_feb_29_is_not_one_act_act_year():
    start, end = date(2028, 2, 1), date(2029, 2, 1)  # 366 days, 335 of them in 2028

    assert (end - start).days == 366
    assert ACT_ACT(start, end) == pytest.approx(335 / 366 + 31 / 365, rel=0, abs=1e-15)
    assert ACT_365F(start, end) == pytest.approx(366 / 365, rel=0, abs=1e-15)


# --------------------------------------------------------------------------
# 4. multi-year period
# --------------------------------------------------------------------------


def test_multi_year_period_year_fractions():
    # 128 days of 2026, all 365 days of 2027, and 365 of leap-year 2028's 366.
    start, end = date(2026, 8, 26), date(2028, 12, 31)

    assert (end - start).days == 858
    assert ACT_ACT(start, end) == pytest.approx(128 / 365 + 365 / 365 + 365 / 366, abs=1e-15)
    assert ACT_365F(start, end) == pytest.approx(858 / 365, rel=0, abs=1e-15)
    assert ACT_360(start, end) == pytest.approx(858 / 360, rel=0, abs=1e-15)


# --------------------------------------------------------------------------
# 5. a case where the two conventions differ by a non-zero amount
# --------------------------------------------------------------------------


def test_leap_period_separates_act_act_from_act_365f():
    """Inside a leap year the pair is at its widest: `sqrt(366/365)`."""

    start, end = date(2028, 2, 1), date(2028, 3, 31)
    pair = CandidatePair(
        "VCUB ACT/365F", "BondVol ACT/ACT ISDA", ACT_365F(start, end), ACT_ACT(start, end)
    )

    assert pair.ratio == pytest.approx(366 / 365, rel=0, abs=1e-15)
    assert pair.vol_multiplier == pytest.approx((366 / 365) ** 0.5, rel=0, abs=1e-15)
    # The whole effect is 13.7 bp per 100 bp of vol -- the ceiling on what any
    # ACT/ACT-vs-ACT/365F discrimination can ever see.
    assert pair.vol_multiplier - 1.0 == pytest.approx(0.001369, abs=1e-6)


def test_multi_year_leap_separation_is_smaller_than_the_in_leap_year_ceiling():
    start, end = date(2026, 8, 26), date(2028, 12, 31)
    pair = CandidatePair(
        "VCUB ACT/365F", "BondVol ACT/ACT ISDA", ACT_365F(start, end), ACT_ACT(start, end)
    )

    # 858 days holding one Feb 29: 5.8e-4, well under the 1.37e-3 ceiling above.
    assert pair.vol_multiplier - 1.0 == pytest.approx(0.00058, rel=1e-2)


def test_act_360_against_act_365f_is_an_order_of_magnitude_larger_effect():
    start, end = date(2026, 8, 26), date(2026, 11, 26)
    pair = CandidatePair(
        "VCUB ACT/360", "BondVol ACT/365F", ACT_360(start, end), ACT_365F(start, end)
    )

    assert pair.ratio == pytest.approx(365 / 360, rel=0, abs=1e-15)
    assert pair.vol_multiplier - 1.0 == pytest.approx(0.006920, abs=1e-6)


# --------------------------------------------------------------------------
# 6. the §A.8.5 total-variance equality on a controlled fixture
# --------------------------------------------------------------------------


def test_total_variance_equality_holds_for_a_controlled_fixture():
    """`(σ_Y^N)^2 × DCF_BondVol == (λ σ_vcub)^2 × DCF_VCUB`.

    All inputs are synthetic: a round 100.0 vol in whatever unit the caller
    displays, and a hypothesis that `DCF_VCUB` is ACT/365F while
    `DCF_BondVol` is ACT/ACT ISDA over one leap-year period. The fixture
    proves the arithmetic of the bridge, **not** that this hypothesis is
    Bloomberg's convention.
    """

    dcf_vcub = 59 / 365  # ACT/365F over 2028-02-01 -> 2028-03-31
    dcf_bondvol = 59 / 366  # ACT/ACT ISDA over the same leap-year period
    lambda_vcub = 1.0
    sigma_vcub = 100.0  # synthetic

    pair = CandidatePair("VCUB ACT/365F", "BondVol ACT/ACT ISDA", dcf_vcub, dcf_bondvol)
    sigma_yield = lambda_vcub * sigma_vcub * pair.vol_multiplier

    assert sigma_yield**2 * dcf_bondvol == pytest.approx(
        (lambda_vcub * sigma_vcub) ** 2 * dcf_vcub, rel=1e-15
    )
    # The same equality, stated the way the experiment reads it back.
    assert (sigma_yield / (lambda_vcub * sigma_vcub)) ** 2 == pytest.approx(
        dcf_vcub / dcf_bondvol, rel=1e-15
    )


# --------------------------------------------------------------------------
# candidate construction
# --------------------------------------------------------------------------


def test_candidate_year_fractions_labels_every_role_and_convention():
    roles = build_date_roles(
        pricing_date=date(2026, 8, 26),
        expiry_date=date(2028, 12, 31),
        forward_settlement_date=date(2029, 1, 2),
    )
    candidates = candidate_year_fractions(roles)

    assert set(candidates) == {
        "t0->TE ACT/ACT ISDA",
        "t0->TE ACT/365F",
        "t0->TE ACT/360",
        "t0->TF ACT/ACT ISDA",
        "t0->TF ACT/365F",
        "t0->TF ACT/360",
    }
    assert candidates["t0->TF ACT/365F"] == pytest.approx(860 / 365, rel=0, abs=1e-15)


def test_build_date_roles_adds_only_the_roles_the_supplied_dates_support():
    only_expiry = build_date_roles(pricing_date=date(2026, 8, 26), expiry_date=date(2027, 8, 26))
    assert [role.name for role in only_expiry] == ["t0->TE"]

    every_role = build_date_roles(
        pricing_date=date(2026, 8, 26),
        expiry_date=date(2027, 8, 26),
        forward_settlement_date=date(2027, 8, 27),
        spot_settlement_date=date(2026, 8, 27),
    )
    assert [role.name for role in every_role] == ["t0->TE", "t0->TF", "spot->TE", "spot->TF"]


def test_a_date_role_must_end_after_it_starts():
    with pytest.raises(ValueError, match="strictly after"):
        DateRole("t0->TE", date(2026, 8, 26), date(2026, 8, 26))


def test_duplicate_role_names_are_refused():
    role = DateRole("t0->TE", date(2026, 8, 26), date(2027, 8, 26))
    other = DateRole("t0->TE", date(2026, 8, 26), date(2028, 8, 26))

    with pytest.raises(ValueError, match="duplicate date-role name"):
        candidate_year_fractions([role, other])


def test_candidate_pairs_crosses_both_legs_and_refuses_an_empty_leg():
    candidates = {"a": 1.0, "b": 2.0}
    pairs = candidate_pairs(candidates, candidates)

    assert len(pairs) == 4
    assert {(pair.vcub_label, pair.bondvol_label) for pair in pairs} == {
        ("a", "a"),
        ("a", "b"),
        ("b", "a"),
        ("b", "b"),
    }
    with pytest.raises(ValueError, match="at least one candidate"):
        candidate_pairs({}, candidates)


def test_candidate_pairs_refuses_a_non_positive_year_fraction():
    with pytest.raises(ValueError, match="strictly positive"):
        candidate_pairs({"a": 0.0}, {"b": 1.0})


# --------------------------------------------------------------------------
# display precision, implied ratio, survivors
# --------------------------------------------------------------------------


def test_displayed_vol_rounding_interval_follows_the_stated_display_rule():
    """A truncating screen and a rounding screen mean different intervals."""

    assert DisplayedVol(100.0, 0.01, NEAREST).interval == (99.995, 100.005)
    # Truncation: the digits shown are a floor, so the true value is at or
    # above them -- an interval a nearest-rounding assumption would exclude.
    assert DisplayedVol(100.0, 0.01, DisplayRounding.TRUNCATED).interval == (100.0, 100.01)
    # Unconfirmed: the union, which can only ever fail to exclude.
    assert DisplayedVol(100.0, 0.01, DisplayRounding.UNKNOWN).interval == (99.995, 100.01)


def test_an_unconfirmed_display_rule_never_excludes_what_either_rule_allows():
    pair = CandidatePair("v", "b", 59 / 365, 59 / 366)
    sigma_vcub = DisplayedVol(100.0, 0.01, DisplayRounding.UNKNOWN)
    truncation_only_case = DisplayedVol(100.15, 0.01, DisplayRounding.UNKNOWN)

    # Under a nearest-rounding assumption this observation would exclude the
    # candidate; with the rule unconfirmed it must not.
    assert not is_consistent(
        pair,
        sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
        sigma_yield=DisplayedVol(100.15, 0.01, NEAREST),
        lambda_vcub=1.0,
    )
    assert is_consistent(
        pair, sigma_vcub=sigma_vcub, sigma_yield=truncation_only_case, lambda_vcub=1.0
    )


def test_the_display_rounding_rule_must_be_stated():
    with pytest.raises(ValueError, match="not a default"):
        DisplayedVol(100.0, 0.01, "nearest")


@pytest.mark.parametrize(
    ("value", "quantum"),
    [(0.0, 0.01), (-1.0, 0.01), (float("nan"), 0.01), (100.0, 0.0), (100.0, -0.01)],
)
def test_displayed_vol_refuses_a_value_or_quantum_that_is_not_positive_and_finite(value, quantum):
    with pytest.raises(ValueError):
        DisplayedVol(value, quantum, NEAREST)


def test_displayed_vol_refuses_a_quantum_that_swallows_the_value():
    with pytest.raises(ValueError, match="too coarse"):
        DisplayedVol(0.004, 0.01, NEAREST)


def test_implied_ratio_interval_is_widest_high_yield_over_low_vcub():
    sigma_vcub = DisplayedVol(100.0, 0.01, NEAREST)
    sigma_yield = DisplayedVol(100.0, 0.01, NEAREST)

    low, high = implied_ratio_interval(
        sigma_vcub=sigma_vcub, sigma_yield=sigma_yield, lambda_vcub=1.0
    )

    assert low == pytest.approx((99.995 / 100.005) ** 2, rel=0, abs=1e-15)
    assert high == pytest.approx((100.005 / 99.995) ** 2, rel=0, abs=1e-15)
    assert low < 1.0 < high


def test_implied_ratio_interval_scales_with_lambda():
    sigma_vcub = DisplayedVol(50.0, 0.01, NEAREST)
    sigma_yield = DisplayedVol(100.0, 0.01, NEAREST)

    low, high = implied_ratio_interval(
        sigma_vcub=sigma_vcub, sigma_yield=sigma_yield, lambda_vcub=2.0
    )

    assert low == pytest.approx((99.995 / (2.0 * 50.005)) ** 2, rel=0, abs=1e-15)
    assert high == pytest.approx((100.005 / (2.0 * 49.995)) ** 2, rel=0, abs=1e-15)


@pytest.mark.parametrize("lambda_vcub", [0.0, -1.0, float("inf")])
def test_a_non_positive_lambda_is_refused(lambda_vcub):
    with pytest.raises(ValueError, match="lambda_vcub"):
        implied_ratio_interval(
            sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
            sigma_yield=DisplayedVol(100.0, 0.01, NEAREST),
            lambda_vcub=lambda_vcub,
        )


def test_predicted_yield_vol_interval_carries_the_vcub_display_width():
    pair = CandidatePair("v", "b", 59 / 365, 59 / 366)
    low, high = predicted_yield_vol_interval(
        pair, sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST), lambda_vcub=1.0
    )

    multiplier = (366 / 365) ** 0.5
    assert low == pytest.approx(99.995 * multiplier, rel=0, abs=1e-12)
    assert high == pytest.approx(100.005 * multiplier, rel=0, abs=1e-12)


def test_a_candidate_survives_only_when_the_two_intervals_meet():
    pair = CandidatePair("v", "b", 59 / 365, 59 / 366)  # multiplier 1.0013690...
    sigma_vcub = DisplayedVol(100.0, 0.01, NEAREST)

    on_top_of_the_prediction = DisplayedVol(100.137, 0.01, NEAREST)
    a_long_way_off = DisplayedVol(100.0, 0.01, NEAREST)

    assert is_consistent(
        pair, sigma_vcub=sigma_vcub, sigma_yield=on_top_of_the_prediction, lambda_vcub=1.0
    )
    assert not is_consistent(
        pair, sigma_vcub=sigma_vcub, sigma_yield=a_long_way_off, lambda_vcub=1.0
    )


def test_surviving_candidates_keeps_only_the_consistent_pairs():
    equal_conventions = CandidatePair("v", "b", 59 / 365, 59 / 365)
    leap_mismatch = CandidatePair("v", "b2", 59 / 365, 59 / 366)
    survivors = surviving_candidates(
        [equal_conventions, leap_mismatch],
        sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
        sigma_yield=DisplayedVol(100.0, 0.01, NEAREST),
        lambda_vcub=1.0,
    )

    assert survivors == (equal_conventions,)


# --------------------------------------------------------------------------
# separation: what the display could ever discriminate
# --------------------------------------------------------------------------


def test_separation_is_guaranteed_only_beyond_the_display_quantum():
    ratio_one = CandidatePair("v", "b", 59 / 365, 59 / 365)
    leap_mismatch = CandidatePair("v", "b2", 59 / 365, 59 / 366)
    sigma_vcub = DisplayedVol(100.0, 0.01, NEAREST)

    # 100 x (multiplier - 1) = 0.1369 apart, minus the two half-widths (0.01).
    fine_screen = separation(
        ratio_one,
        leap_mismatch,
        sigma_vcub=sigma_vcub,
        sigma_yield_quantum=0.01,
        lambda_vcub=1.0,
    )
    assert fine_screen.centre_gap == pytest.approx(0.13690, rel=1e-3)
    assert fine_screen.clear_gap == pytest.approx(0.13690 - 0.01, rel=1e-2)
    assert fine_screen.guaranteed_separable

    coarse_screen = separation(
        ratio_one,
        leap_mismatch,
        sigma_vcub=sigma_vcub,
        sigma_yield_quantum=1.0,
        lambda_vcub=1.0,
    )
    assert not coarse_screen.guaranteed_separable


def test_separation_refuses_a_non_positive_quantum():
    pair = CandidatePair("v", "b", 1.0, 1.0)
    with pytest.raises(ValueError, match="sigma_yield_quantum"):
        separation(
            pair,
            pair,
            sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
            sigma_yield_quantum=0.0,
            lambda_vcub=1.0,
        )


def test_candidates_sharing_a_multiplier_are_reported_as_permanently_ambiguous():
    """Same multiplier, different conventions: no capture can ever separate them.

    ACT/360 on both legs and ACT/365F on both legs are distinct convention
    hypotheses that produce the same `σ_Y^N` at every precision. They must
    be reported as a permanent ambiguity, not dropped and not counted as a
    precision problem.
    """

    left = CandidatePair("t0->TE ACT/365F", "t0->TE ACT/365F", 2.0, 2.0)
    right = CandidatePair("t0->TE ACT/360", "t0->TE ACT/360", 3.0, 3.0)

    assert left.vol_multiplier == right.vol_multiplier
    assert identical_multiplier_groups([left, right]) == ((left, right),)
    # ... and they are not double-counted as a display-precision ambiguity.
    assert (
        pairs_without_guaranteed_separation(
            [left, right],
            sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
            sigma_yield_quantum=0.01,
            lambda_vcub=1.0,
        )
        == ()
    )


def test_a_multiplier_reached_by_only_one_candidate_is_not_a_permanent_ambiguity():
    only_one = CandidatePair("t0->TE ACT/365F", "t0->TE ACT/ACT ISDA", 59 / 365, 59 / 366)

    assert identical_multiplier_groups([only_one]) == ()


def test_a_present_day_two_year_expiry_cannot_separate_act_act_from_act_365f_at_one_bp_display():
    """The experiment-design result Issue #192 §4 asks to be quantified.

    From a 2026 pricing date, the widest ACT/ACT-vs-ACT/365F effect
    reachable at a ~2.3-year expiry is ~0.058 bp per 100 bp of vol, so a
    screen showing `σ_Y^N` to 0.1 bp cannot separate them.
    """

    roles = build_date_roles(pricing_date=date(2026, 8, 26), expiry_date=date(2028, 12, 31))
    candidates = candidate_year_fractions(roles)
    act_365f_over_act_act = CandidatePair(
        "t0->TE ACT/365F",
        "t0->TE ACT/ACT ISDA",
        candidates["t0->TE ACT/365F"],
        candidates["t0->TE ACT/ACT ISDA"],
    )
    ratio_one = CandidatePair(
        "t0->TE ACT/365F",
        "t0->TE ACT/365F",
        candidates["t0->TE ACT/365F"],
        candidates["t0->TE ACT/365F"],
    )
    sigma_vcub = DisplayedVol(100.0, 0.01, NEAREST)  # synthetic 100 bp

    coarse = separation(
        act_365f_over_act_act,
        ratio_one,
        sigma_vcub=sigma_vcub,
        sigma_yield_quantum=0.1,
        lambda_vcub=1.0,
    )
    assert coarse.centre_gap == pytest.approx(0.0582, rel=1e-2)
    assert not coarse.guaranteed_separable

    fine = separation(
        act_365f_over_act_act,
        ratio_one,
        sigma_vcub=sigma_vcub,
        sigma_yield_quantum=0.01,
        lambda_vcub=1.0,
    )
    assert fine.guaranteed_separable


def test_a_one_day_end_date_shift_is_worth_as_much_as_the_whole_leap_effect():
    """Why the date role is part of the candidate, not a fixed input."""

    roles = build_date_roles(
        pricing_date=date(2026, 8, 26),
        expiry_date=date(2028, 12, 31),
        forward_settlement_date=date(2029, 1, 1),
    )
    candidates = candidate_year_fractions(roles)
    same_convention_different_end_date = CandidatePair(
        "t0->TF ACT/365F",
        "t0->TE ACT/365F",
        candidates["t0->TF ACT/365F"],
        candidates["t0->TE ACT/365F"],
    )
    same_end_date_different_convention = CandidatePair(
        "t0->TE ACT/365F",
        "t0->TE ACT/ACT ISDA",
        candidates["t0->TE ACT/365F"],
        candidates["t0->TE ACT/ACT ISDA"],
    )

    assert same_convention_different_end_date.vol_multiplier == pytest.approx(
        same_end_date_different_convention.vol_multiplier, rel=1e-3
    )


# --------------------------------------------------------------------------
# report and CLI
# --------------------------------------------------------------------------


def test_report_states_that_it_pins_nothing_and_lists_the_survivors():
    roles = build_date_roles(pricing_date=date(2026, 8, 26), expiry_date=date(2028, 12, 31))
    candidates = candidate_year_fractions(roles)
    report = render_report(
        roles=roles,
        pairs=candidate_pairs(candidates, candidates),
        sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
        lambda_vcub=1.0,
        sigma_yield=DisplayedVol(100.0, 0.01, NEAREST),
    )

    assert "pins nothing" in report
    assert "calendar days = 858" in report
    assert "implied ratio interval" in report
    assert "Surviving candidates:" in report
    assert "Do not pick one from this list." in report


def test_a_design_run_reports_separability_only_for_a_stated_yield_precision():
    roles = build_date_roles(pricing_date=date(2026, 8, 26), expiry_date=date(2028, 12, 31))
    pairs = candidate_pairs(*(candidate_year_fractions(roles),) * 2)

    without_precision = render_report(
        roles=roles,
        pairs=pairs,
        sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
        lambda_vcub=1.0,
        sigma_yield=None,
    )
    assert "design run" in without_precision
    assert "Surviving candidates:" not in without_precision
    # sigma_vcub's own quantum is never borrowed to stand in for the screen
    # precision of a vol this run has not seen.
    assert "not\nguaranteed" not in without_precision
    assert "is not guaranteed to separate" not in without_precision
    assert "Separability not reported" in without_precision

    with_precision = render_report(
        roles=roles,
        pairs=pairs,
        sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
        lambda_vcub=1.0,
        sigma_yield=None,
        sigma_yield_quantum=0.1,
    )
    assert "sigma_Y^N quantum 0.1" in with_precision
    assert "is not guaranteed to separate" in with_precision


def test_an_observed_yield_vol_and_a_design_quantum_cannot_both_be_supplied():
    roles = build_date_roles(pricing_date=date(2026, 8, 26), expiry_date=date(2028, 12, 31))
    pairs = candidate_pairs(*(candidate_year_fractions(roles),) * 2)

    with pytest.raises(ValueError, match="design run only"):
        render_report(
            roles=roles,
            pairs=pairs,
            sigma_vcub=DisplayedVol(100.0, 0.01, NEAREST),
            lambda_vcub=1.0,
            sigma_yield=DisplayedVol(100.0, 0.01, NEAREST),
            sigma_yield_quantum=0.01,
        )


def test_cli_runs_a_design_pass(capsys):
    exit_code = main(
        [
            "--pricing-date",
            "2026-08-26",
            "--expiry-date",
            "2028-12-31",
            "--sigma-vcub",
            "100.0",
            "--sigma-vcub-quantum",
            "0.01",
            "--display-rounding",
            "unknown",
            "--lambda-vcub",
            "1.0",
        ]
    )

    assert exit_code == 0
    assert "design run" in capsys.readouterr().out


def test_cli_accepts_a_design_quantum_without_an_observation(capsys):
    exit_code = main(
        [
            "--pricing-date",
            "2026-08-26",
            "--expiry-date",
            "2028-12-31",
            "--sigma-vcub",
            "100.0",
            "--sigma-vcub-quantum",
            "0.01",
            "--sigma-yield-quantum",
            "0.1",
            "--display-rounding",
            "unknown",
            "--lambda-vcub",
            "1.0",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "design run" in output
    assert "sigma_Y^N quantum 0.1" in output


def test_cli_requires_the_observed_vol_and_its_quantum_together():
    with pytest.raises(SystemExit):
        main(
            [
                "--pricing-date",
                "2026-08-26",
                "--expiry-date",
                "2028-12-31",
                "--sigma-vcub",
                "100.0",
                "--sigma-vcub-quantum",
                "0.01",
                "--sigma-yield",
                "100.1",
                "--lambda-vcub",
                "1.0",
            ]
        )


def test_cli_reports_an_invalid_input_as_an_error_not_a_traceback(capsys):
    exit_code = main(
        [
            "--pricing-date",
            "2026-08-26",
            "--expiry-date",
            "2026-08-26",
            "--sigma-vcub",
            "100.0",
            "--sigma-vcub-quantum",
            "0.01",
            "--display-rounding",
            "unknown",
            "--lambda-vcub",
            "1.0",
        ]
    )

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err
