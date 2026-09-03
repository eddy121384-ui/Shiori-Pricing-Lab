"""Tests for `pricing/treasury_futures_implied_yield.py` (Issue #190).

The canonical calculation path, checked three ways:

1. **Against convention, not against itself.** A bond priced at par on a
   coupon date must yield exactly its coupon; accrued interest must equal the
   hand-computed Actual/Actual (ISMA) proration; the month-end coupon grid
   must land on month ends. These pin the methodology, so a round-trip test
   cannot pass by being consistently wrong.
2. **Round trips**, to Issue #190's own tolerances: price -> yield -> price
   within one minimum tick, and yield -> price -> yield within 0.5 bp.
3. **Fail closed**, wherever a missing or impossible input would otherwise
   produce a plausible-looking number.

Every CTD below is an arbitrary test input chosen to exercise the maths --
never real current market data for any contract.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shiori_pricing_lab.data.treasury_futures_ctd import treasury_futures_ctd_from_manual_entry
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    TreasuryFuturesContractError,
    minimum_tick,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
    _YIELD_SOLVE_LOWER,
    _YIELD_SOLVE_UPPER,
    TREASURY_DAY_COUNT,
    TreasuryFuturesYieldError,
    accrued_interest_per_100,
    clean_price_from_yield,
    converted_clean_price,
    coupon_period_bounds,
    futures_price_from_clean_price,
    futures_price_from_target_yield,
    implied_yield_from_futures_price,
    remaining_coupon_dates,
    yield_from_clean_price,
)

HALF_BASIS_POINT_IN_PERCENT = 0.005


def _ctd(**overrides):
    entry = {
        "contract_code": "ZN",
        "contract_symbol": "TYZ6",
        "ctd_identifier": "US91282CTEST",
        "ctd_coupon_percent": 4.25,
        "ctd_maturity_date": "2034-05-15",
        "conversion_factor": 0.8012,
        "last_delivery_date": "2026-12-31",
        "as_of": "2026-08-25T14:00:00Z",
    }
    entry.update(overrides)
    return treasury_futures_ctd_from_manual_entry(entry)


# Four arbitrary but structurally representative CTD shapes, one per MVP
# contract: a month-end 2-year maturity for ZT (the case a day-of-month
# schedule gets wrong), mid-month 15ths for the rest.
CTD_BY_CONTRACT = {
    "ZT": dict(
        contract_code="ZT",
        contract_symbol="TUZ6",
        ctd_coupon_percent=3.875,
        ctd_maturity_date="2028-08-31",
        conversion_factor=0.9123,
    ),
    "ZF": dict(
        contract_code="ZF",
        contract_symbol="FVZ6",
        ctd_coupon_percent=4.0,
        ctd_maturity_date="2031-02-15",
        conversion_factor=0.8654,
    ),
    "ZN": dict(contract_code="ZN", contract_symbol="TYZ6"),
    "ZB": dict(
        contract_code="ZB",
        contract_symbol="USZ6",
        ctd_coupon_percent=3.0,
        ctd_maturity_date="2049-08-15",
        conversion_factor=0.6421,
    ),
}


# --------------------------------------------------------------------------
# Convention: the coupon grid, accrual, and price/yield identities
# --------------------------------------------------------------------------


def test_the_coupon_grid_is_anchored_on_maturity_not_on_settlement() -> None:
    previous, following = coupon_period_bounds(date(2026, 12, 31), date(2034, 5, 15))
    assert (previous, following) == (date(2026, 11, 15), date(2027, 5, 15))


def test_a_month_end_maturity_puts_every_coupon_on_a_month_end() -> None:
    # A 2-year note maturing 31 Aug pays on 28/29 Feb and 31 Aug -- not on
    # "the 31st, clamped to 28". This is the ZT case a naive day-of-month
    # schedule (PR #9's) gets wrong on most contracts.
    assert remaining_coupon_dates(date(2026, 9, 30), date(2028, 8, 31)) == [
        date(2027, 2, 28),
        date(2027, 8, 31),
        date(2028, 2, 29),
        date(2028, 8, 31),
    ]


def test_the_grid_re_anchors_on_maturity_and_never_drifts_off_it() -> None:
    # A day-of-month anchor that a short month clamps (a 29th anchored grid
    # passing through February) must return to the anchor day, not stay
    # clamped -- forward stepping would leave a spurious extra coupon just
    # before maturity.
    dates = remaining_coupon_dates(date(2026, 12, 31), date(2029, 8, 29))
    assert dates == [
        date(2027, 2, 28),
        date(2027, 8, 29),
        date(2028, 2, 29),
        date(2028, 8, 29),
        date(2029, 2, 28),
        date(2029, 8, 29),
    ]
    assert dates[-1] == date(2029, 8, 29)
    assert len(dates) == len(set(dates))


def test_the_grid_and_the_accrual_period_are_the_same_grid() -> None:
    # Accrued interest is prorated on `coupon_period_bounds`, discounting is
    # exponentiated on `remaining_coupon_dates`. If those two ever came from
    # different schedules the clean/dirty conversion would be inconsistent.
    for settlement, maturity in (
        (date(2026, 12, 31), date(2034, 5, 15)),
        (date(2026, 9, 30), date(2028, 8, 31)),
        (date(2026, 12, 31), date(2049, 8, 15)),
    ):
        _, following = coupon_period_bounds(settlement, maturity)
        assert remaining_coupon_dates(settlement, maturity)[0] == following


def test_settlement_exactly_on_a_coupon_date_accrues_nothing() -> None:
    assert accrued_interest_per_100(date(2026, 11, 15), date(2034, 5, 15), 4.25) == 0.0


def test_accrued_interest_is_the_actual_actual_isma_proration() -> None:
    settlement, maturity = date(2026, 12, 31), date(2034, 5, 15)
    previous, following = coupon_period_bounds(settlement, maturity)
    expected = (
        (4.25 / 2) * (settlement - previous).days / (following - previous).days
    )
    assert accrued_interest_per_100(settlement, maturity, 4.25) == pytest.approx(expected)


def test_the_convention_stamped_on_every_answer_is_the_treasury_one() -> None:
    assert str(TREASURY_DAY_COUNT) == "ACT_ACT_BOND"


def test_a_par_price_on_a_coupon_date_yields_exactly_the_coupon() -> None:
    # The identity that pins the discounting formula: no round trip can pass
    # this by being consistently wrong.
    assert yield_from_clean_price(100.0, date(2026, 5, 15), date(2034, 5, 15), 4.0) == (
        pytest.approx(4.0, abs=1e-9)
    )
    assert clean_price_from_yield(4.0, date(2026, 5, 15), date(2034, 5, 15), 4.0) == (
        pytest.approx(100.0, abs=1e-9)
    )


def test_a_bond_yielding_above_its_coupon_trades_below_par_and_vice_versa() -> None:
    below = clean_price_from_yield(5.0, date(2026, 12, 31), date(2034, 5, 15), 4.25)
    above = clean_price_from_yield(3.5, date(2026, 12, 31), date(2034, 5, 15), 4.25)
    assert below < 100.0 < above


@pytest.mark.parametrize("yield_percent", [0.5, 2.0, 4.25, 6.75, 12.0])
def test_clean_price_and_yield_round_trip(yield_percent) -> None:
    settlement, maturity, coupon = date(2026, 12, 31), date(2034, 5, 15), 4.25
    price = clean_price_from_yield(yield_percent, settlement, maturity, coupon)
    assert yield_from_clean_price(price, settlement, maturity, coupon) == pytest.approx(
        yield_percent, abs=1e-9
    )


# --------------------------------------------------------------------------
# The futures conversion
# --------------------------------------------------------------------------


def test_the_converted_clean_price_is_futures_price_times_conversion_factor() -> None:
    assert converted_clean_price(112.515625, 0.8012) == pytest.approx(112.515625 * 0.8012)
    assert futures_price_from_clean_price(112.515625 * 0.8012, 0.8012) == pytest.approx(
        112.515625
    )


@pytest.mark.parametrize("contract_code", sorted(CTD_BY_CONTRACT))
def test_workflow_a_reports_the_converted_price_the_yield_was_solved_from(
    contract_code,
) -> None:
    ctd = _ctd(**CTD_BY_CONTRACT[contract_code])
    result = implied_yield_from_futures_price(ctd, 110.5)
    assert result.settlement_date == ctd.last_delivery_date
    assert result.converted_clean_price == pytest.approx(110.5 * ctd.conversion_factor)
    assert result.dirty_price == pytest.approx(
        result.converted_clean_price + result.accrued_interest
    )
    assert clean_price_from_yield(
        result.implied_yield_percent,
        result.settlement_date,
        ctd.ctd_maturity_date,
        ctd.ctd_coupon_percent,
    ) == pytest.approx(result.converted_clean_price, abs=1e-9)


@pytest.mark.parametrize("contract_code", sorted(CTD_BY_CONTRACT))
def test_futures_price_to_yield_to_futures_price_stays_inside_one_tick(
    contract_code,
) -> None:
    # Issue #190's own acceptance tolerance for this round trip.
    ctd = _ctd(**CTD_BY_CONTRACT[contract_code])
    for price in (95.0, 102.515625, 110.5, 128.25):
        implied = implied_yield_from_futures_price(ctd, price)
        back = futures_price_from_target_yield(ctd, implied.implied_yield_percent)
        assert abs(back.futures_price - price) < minimum_tick(contract_code)


@pytest.mark.parametrize("contract_code", sorted(CTD_BY_CONTRACT))
def test_target_yield_to_futures_price_to_yield_stays_inside_half_a_basis_point(
    contract_code,
) -> None:
    ctd = _ctd(**CTD_BY_CONTRACT[contract_code])
    for target in (1.5, 3.25, 4.2, 5.875):
        priced = futures_price_from_target_yield(ctd, target)
        back = implied_yield_from_futures_price(ctd, priced.futures_price)
        assert abs(back.implied_yield_percent - target) < HALF_BASIS_POINT_IN_PERCENT


@pytest.mark.parametrize("contract_code", sorted(CTD_BY_CONTRACT))
def test_a_higher_futures_price_always_implies_a_lower_ctd_yield(contract_code) -> None:
    ctd = _ctd(**CTD_BY_CONTRACT[contract_code])
    yields = [
        implied_yield_from_futures_price(ctd, price).implied_yield_percent
        for price in (95.0, 100.0, 105.0, 110.0, 115.0, 120.0)
    ]
    assert yields == sorted(yields, reverse=True)
    assert len(set(yields)) == len(yields)


def test_workflow_b_reports_both_the_raw_price_and_a_tradeable_one() -> None:
    ctd = _ctd()
    result = futures_price_from_target_yield(ctd, 4.2)
    ticks = result.exchange_price / result.minimum_tick
    assert ticks == pytest.approx(round(ticks))  # a real, tradeable price
    assert abs(result.exchange_price - result.futures_price) <= result.minimum_tick / 2
    assert result.minimum_tick == minimum_tick("ZN")


def test_the_yield_is_solved_from_the_exact_price_not_its_tick_rounded_display() -> None:
    # An off-tick hypothetical is the desk's normal case; rounding it before
    # solving would answer a question the trader did not ask.
    ctd = _ctd()
    off_tick = implied_yield_from_futures_price(ctd, 112.5137)
    on_tick = implied_yield_from_futures_price(ctd, 112.515625)
    assert off_tick.quote.on_tick is False
    assert off_tick.implied_yield_percent != on_tick.implied_yield_percent
    assert off_tick.converted_clean_price == pytest.approx(112.5137 * ctd.conversion_factor)


def test_a_quote_string_and_its_decimal_give_the_same_yield() -> None:
    ctd = _ctd()
    assert implied_yield_from_futures_price(ctd, "112-165").implied_yield_percent == (
        implied_yield_from_futures_price(ctd, 112.515625).implied_yield_percent
    )


# --------------------------------------------------------------------------
# The result payloads
# --------------------------------------------------------------------------


def test_every_answer_is_stamped_with_its_methodology_and_carries_no_carry() -> None:
    ctd = _ctd()
    for payload in (
        implied_yield_from_futures_price(ctd, 110.5).as_payload(),
        futures_price_from_target_yield(ctd, 4.2).as_payload(),
    ):
        methodology = payload["methodology"]
        assert methodology["basis"] == "CME_TREASURY_ANALYTICS_CTD_IMPLIED_FORWARD_YIELD"
        assert methodology["settlement_date_rule"] == "FUTURES_CONTRACT_LAST_DELIVERY_DAY"
        assert methodology["day_count"] == "ACT_ACT_BOND"
        assert methodology["coupon_frequency"] == "SEMI_ANNUAL"
        assert methodology["par"] == 100.0
        # No net basis, repo or carry adjustment exists to be applied.
        assert methodology["carry_adjustment"] == "NONE"


def test_every_answer_carries_the_ctd_source_status_beside_it() -> None:
    payload = implied_yield_from_futures_price(_ctd(), 110.5).as_payload()
    assert payload["ctd"]["source"] == "MANUAL_UNCONFIRMED"
    assert payload["ctd"]["is_confirmed_source"] is False


# --------------------------------------------------------------------------
# Fail-closed behavior
# --------------------------------------------------------------------------


def test_a_ctd_tagged_with_an_unsupported_contract_is_refused() -> None:
    ctd = _ctd(contract_code="ZQ")
    with pytest.raises(TreasuryFuturesContractError):
        implied_yield_from_futures_price(ctd, 110.5)
    with pytest.raises(TreasuryFuturesContractError):
        futures_price_from_target_yield(ctd, 4.2)


def test_settlement_inside_the_final_coupon_period_is_refused_not_approximated() -> None:
    # The street convention switches to simple interest for a single
    # remaining coupon; this module does not implement that, so it says so.
    with pytest.raises(TreasuryFuturesYieldError) as exc:
        clean_price_from_yield(4.0, date(2034, 1, 15), date(2034, 5, 15), 4.25)
    assert "final coupon period" in str(exc.value)


def test_settlement_on_or_after_maturity_is_refused() -> None:
    with pytest.raises(TreasuryFuturesYieldError):
        coupon_period_bounds(date(2034, 5, 15), date(2034, 5, 15))
    with pytest.raises(TreasuryFuturesYieldError):
        coupon_period_bounds(date(2035, 1, 1), date(2034, 5, 15))


@pytest.mark.parametrize("conversion_factor", [0.0, -0.5])
def test_a_non_positive_conversion_factor_is_refused(conversion_factor) -> None:
    with pytest.raises(TreasuryFuturesYieldError):
        converted_clean_price(110.5, conversion_factor)
    with pytest.raises(TreasuryFuturesYieldError):
        futures_price_from_clean_price(90.0, conversion_factor)


@pytest.mark.parametrize(
    "bound_percent", [_YIELD_SOLVE_LOWER * 100.0, _YIELD_SOLVE_UPPER * 100.0]
)
def test_a_yield_sitting_exactly_on_a_bracket_endpoint_is_returned_not_bisected_past(
    bound_percent,
) -> None:
    """Codex review, PR #191 (P2), fifth round.

    A zero residual is not greater than zero, so the sign test read a root at
    the lower bound as "same side as the midpoint" and moved past it. The
    observed symptom was that the price produced at -20% solved back to
    +100% -- a wrong answer with no error, which is the failure mode this
    module exists to avoid.
    """

    settlement, maturity, coupon = date(2026, 12, 31), date(2034, 5, 15), 4.25
    price = clean_price_from_yield(bound_percent, settlement, maturity, coupon)
    assert yield_from_clean_price(price, settlement, maturity, coupon) == pytest.approx(
        bound_percent, abs=1e-9
    )


def test_a_price_outside_the_solvable_yield_bracket_is_refused() -> None:
    with pytest.raises(TreasuryFuturesYieldError):
        yield_from_clean_price(1e6, date(2026, 12, 31), date(2034, 5, 15), 4.25)
    with pytest.raises(TreasuryFuturesYieldError):
        yield_from_clean_price(-1.0, date(2026, 12, 31), date(2034, 5, 15), 4.25)


@pytest.mark.parametrize(
    "bad_target", ["4.2", None, True, [4.2], float("nan"), float("inf")]
)
def test_a_non_numeric_target_yield_is_refused(bad_target) -> None:
    with pytest.raises(TreasuryFuturesYieldError):
        futures_price_from_target_yield(_ctd(), bad_target)


# --------------------------------------------------------------------------
# Property sweep over the coupon grid
#
# The worked examples above pin specific known-good answers. This sweeps the
# structural invariants across every maturity day-of-month shape a Treasury
# can have -- the 15th, and the 28th/29th/30th/31st across leap and non-leap
# Februaries -- against settlements spread through a coupon period. It is
# what would have caught the forward-stepping grid drift by construction
# rather than by someone thinking of the case.
# --------------------------------------------------------------------------


def _sweep_maturities() -> list[date]:
    maturities = []
    for year in (2028, 2029, 2030, 2031, 2032):
        for month in (1, 2, 3, 8, 12):
            for day in (15, 28, 29, 30, 31):
                try:
                    maturities.append(date(year, month, day))
                except ValueError:  # 31 Feb and friends
                    continue
    return maturities


_SWEEP_SETTLEMENTS = [
    date(2026, 9, 30) + timedelta(days=offset)
    for offset in (0, 45, 91, 181, 182, 200, 365)
]


def test_the_two_grid_views_never_disagree_about_the_same_schedule() -> None:
    checked = 0
    for maturity in _sweep_maturities():
        for settlement in _SWEEP_SETTLEMENTS:
            if settlement >= maturity:
                continue
            previous, following = coupon_period_bounds(settlement, maturity)
            remaining = remaining_coupon_dates(settlement, maturity)
            checked += 1
            # Accrual is prorated on `coupon_period_bounds`; discounting is
            # exponentiated on `remaining_coupon_dates`. If the two ever came
            # from different schedules, clean <-> dirty would be inconsistent.
            assert remaining[0] == following, (settlement, maturity)
            assert previous <= settlement < following, (settlement, maturity)
            assert remaining[-1] == maturity, (settlement, maturity)
            assert remaining == sorted(remaining), (settlement, maturity)
            assert len(set(remaining)) == len(remaining), (settlement, maturity)
            # Every gap must be a real semiannual period. This is the
            # assertion with teeth: a grid that drifts off its anchor (a
            # 29th/30th/31st schedule clamped in February and stepped
            # forward from there) still starts and ends correctly and is
            # still sorted and unique -- it betrays itself only as a final
            # pair one day apart. 181-184 actual days is the true range of a
            # semiannual period; the bounds here are one day wider on each
            # side and still catch that by a mile.
            gaps = [
                (later - earlier).days
                for earlier, later in zip(remaining, remaining[1:], strict=False)
            ]
            assert all(180 <= gap <= 185 for gap in gaps), (settlement, maturity, gaps)
            # ... and the first gap must be a period from the period start,
            # not from settlement, so the grid is anchored the same way at
            # both ends.
            assert 180 <= (following - previous).days <= 185, (settlement, maturity)
    assert checked > 500  # the sweep is actually sweeping


@pytest.mark.parametrize("coupon_percent", [0.0, 4.25, 9.5])
def test_accrued_interest_never_leaves_its_own_coupon_period(coupon_percent) -> None:
    for maturity in _sweep_maturities():
        for settlement in _SWEEP_SETTLEMENTS:
            if settlement >= maturity:
                continue
            accrued = accrued_interest_per_100(settlement, maturity, coupon_percent)
            # Never negative, and never more than the coupon it is accruing
            # towards -- the invariant that makes clean <-> dirty safe.
            assert 0.0 <= accrued <= coupon_percent / 2, (settlement, maturity)


def test_price_is_strictly_decreasing_in_yield_and_invertible_across_the_sweep() -> None:
    yields = (0.5, 2.0, 4.25, 6.0, 9.0)
    for maturity in _sweep_maturities()[::3]:  # every third shape keeps this quick
        for settlement in _SWEEP_SETTLEMENTS[::2]:
            if settlement >= maturity:
                continue
            try:
                prices = [
                    clean_price_from_yield(y, settlement, maturity, 4.25) for y in yields
                ]
            except TreasuryFuturesYieldError:
                continue  # final coupon period, refused by design
            assert prices == sorted(prices, reverse=True), (settlement, maturity)
            for target, price in zip(yields, prices, strict=True):
                assert yield_from_clean_price(
                    price, settlement, maturity, 4.25
                ) == pytest.approx(target, abs=1e-7), (settlement, maturity, target)


# --------------------------------------------------------------------------
# Regression tests for Codex P2 findings
# --------------------------------------------------------------------------


def test_reverse_conversion_payload_includes_on_tick() -> None:
    """P2 #1: reverse target-yield -> futures-price payload must include on_tick.

    The UI checks payload.on_tick (top-level) to show off-tick correctly.
    Both on-tick and off-tick cases must be reported correctly.
    """
    ctd = _ctd()

    # On-tick price: use the exact tick price from a round-trip
    exact_tick_price = 112.515625  # 112-165 for ZN
    implied = implied_yield_from_futures_price(ctd, exact_tick_price)
    on_tick_result = futures_price_from_target_yield(ctd, implied.implied_yield_percent)
    payload = on_tick_result.as_payload()
    assert "on_tick" in payload
    assert payload["on_tick"] is True

    # Off-tick price (interpolated)
    off_tick = futures_price_from_target_yield(ctd, 4.2001)
    payload = off_tick.as_payload()
    assert "on_tick" in payload
    assert payload["on_tick"] is False


def test_extreme_target_yield_raises_treasury_futures_yield_error_not_overflow() -> None:
    """P2 #2: extreme but finite target yields must raise
    TreasuryFuturesYieldError, not OverflowError.

    1e100 is finite in IEEE 754 but causes numerical overflow in the
    pricing math. The per-direction fail-visible behavior requires this
    to be caught and reported as TreasuryFuturesYieldError, not an
    unhandled OverflowError that would crash the route.

    Note: negative extreme yields like -1e100 hit the domain check
    (period_yield <= -1.0) first and raise a different error message.
    Only positive extreme yields are tested here for the overflow path.
    """
    ctd = _ctd()
    extreme_yields = [1e100, 1e50, 1e30]

    for yield_val in extreme_yields:
        with pytest.raises(TreasuryFuturesYieldError) as exc:
            futures_price_from_target_yield(ctd, yield_val)
        assert "numerical overflow" in str(exc.value).lower()


# --------------------------------------------------------------------------
# Regression tests for latest-head fixes (commit 6cdf252)
# --------------------------------------------------------------------------


def test_reverse_direction_on_tick_with_float_noise_is_true() -> None:
    """A mathematically on-tick reverse result with normal float noise is on_tick=True.

    The exact tick price 112-165 = 112.515625 for ZN may produce a tiny
    floating-point difference after round-tripping through the pricing math.
    The tolerance should absorb normal IEEE 754 roundoff.
    """
    ctd = _ctd()
    # Use the exact tick price 112.515625 (112-165 for ZN)
    # Round-trip through yield and back to price
    implied = implied_yield_from_futures_price(ctd, 112.515625)
    result = futures_price_from_target_yield(ctd, implied.implied_yield_percent)

    # The exchange_price is always an exact tick multiple
    # The raw futures_price may have a tiny float error from the round-trip
    # With tolerance 1e-8 * minimum_tick, this should be reported as on_tick=True
    from shiori_pricing_lab.pricing.treasury_futures_contract import get_contract
    contract = get_contract(ctd.contract_code)
    diff = abs(result.exchange_price - result.futures_price)
    assert diff <= contract.minimum_tick * 1e-8
    assert result.on_tick is True


def test_reverse_direction_genuinely_off_tick_remains_false() -> None:
    """A genuinely off-tick reverse result remains on_tick=False."""
    ctd = _ctd()
    # Use a yield that produces a price clearly between ticks
    result = futures_price_from_target_yield(ctd, 4.2001)
    from shiori_pricing_lab.pricing.treasury_futures_contract import get_contract
    contract = get_contract(ctd.contract_code)
    # The difference should be larger than the tolerance
    diff = abs(result.exchange_price - result.futures_price)
    assert diff > contract.minimum_tick * 1e-8
    assert result.on_tick is False


def test_huge_finite_futures_price_fails_with_treasury_futures_quote_error() -> None:
    """Huge finite futures price (1e308) fails with
    TreasuryFuturesQuoteError, not raw OverflowError."""
    from shiori_pricing_lab.pricing.treasury_futures_contract import (
        TreasuryFuturesQuoteError,
        round_to_tick,
    )

    with pytest.raises(TreasuryFuturesQuoteError) as exc:
        round_to_tick("ZN", 1e308)
    assert "overflow" in str(exc.value).lower()


def test_huge_positive_target_yield_translates_overflow_to_treasury_futures_yield_error() -> None:
    """Huge positive target yield (1e308) translates OverflowError to TreasuryFuturesYieldError."""
    ctd = _ctd()
    with pytest.raises(TreasuryFuturesYieldError) as exc:
        futures_price_from_target_yield(ctd, 1e308)
    assert "numerical overflow" in str(exc.value).lower()


def test_too_negative_yield_domain_error_keeps_original_message_not_relabeled() -> None:
    """A too-negative-yield domain error keeps its original domain message
    and is NOT relabeled overflow.

    The check `period_yield <= -1.0` in `clean_price_from_yield` raises
    TreasuryFuturesYieldError with 'too negative to discount semiannually'.
    This must NOT be caught and relabeled as 'numerical overflow'.
    """
    ctd = _ctd()
    # -200% yield gives period_yield = -1.0, which triggers the domain check
    with pytest.raises(TreasuryFuturesYieldError) as exc:
        futures_price_from_target_yield(ctd, -200.0)
    error_msg = str(exc.value)
    assert "too negative to discount semiannually" in error_msg
    assert "numerical overflow" not in error_msg.lower()