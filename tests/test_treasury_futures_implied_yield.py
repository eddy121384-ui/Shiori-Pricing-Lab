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

from datetime import date

import pytest

from shiori_pricing_lab.data.treasury_futures_ctd import treasury_futures_ctd_from_manual_entry
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    TreasuryFuturesContractError,
    minimum_tick,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
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
