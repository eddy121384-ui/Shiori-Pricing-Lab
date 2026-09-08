"""Issue #204: Eurex German government bond futures (FGBS/FGBM/FGBL/FGBX).

Focused coverage for the four Eurex contracts on top of the existing
per-module suites (which pin the six UST contracts):

- registry / market membership, aliases, delivery roots, decimal ticks
- decimal quote parse/format, including 32nds refusal on decimal grids
- DE ISIN acceptance + ISO 6166 check digit; US-ISIN behavior unchanged
- German CUSIP non-reconciliation (display-only, never an identity check)
- German display descriptions (BKO/OBL/DBR kept when coherent, dropped when not)
- in-month delivery-date validation (never the ZT/ZF next-month rule)
- remaining-maturity window boundaries for all four contracts
- cross-contract substitution rejection (Eurex x Eurex via window,
  cross-market via identity)
- full two-stage live loads with the confirmed Issue #204 RED Gate 1 values
- GERMAN_GOVT convention selection on every answer
- price -> yield -> price and yield -> price -> yield round trips
- workbench catalogue and acceptance-CLI exposure (both registry-driven)

No pricing methodology lives here beyond asserting which convention each
answer is stamped with. No live Bloomberg parity values are fabricated: the
only numbers asserted are the confirmed workstation evidence from RED Gate 1
and pure arithmetic on the tick grids. Live numerical parity against
Bloomberg/Eurex analytics (<= 0.5 bp) remains workstation UAT, tracked as
outstanding on the PR -- these tests prove self-consistency only.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from test_treasury_futures_ctd import (
    LIVE_DELIVERY_SYMBOL,
    LIVE_STAGE_TWO,
    _install_fake_blpapi,
    _load_with,
    _response_event,
    _security_data,
    _two_stage_responder,
)

from shiori_pricing_lab.app.standalone_option_workbench_server import (
    treasury_futures_contract_catalogue,
)
from shiori_pricing_lab.data.treasury_futures_ctd import (
    BLOOMBERG_FUTURES_ACTIVE_ALIASES,
    BLOOMBERG_FUTURES_TICKER_ROOTS,
    EUREX_GERMAN_FUTURES_CODES,
    TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS,
    TreasuryFuturesCTDBloombergError,
    TreasuryFuturesCTDError,
    TreasuryFuturesCTDSource,
    _delivery_month_first_day,
    _require_delivery_ticker,
    _require_remaining_maturity_plausible,
    bloomberg_active_contract,
    load_bloomberg_ctd_metadata,
    treasury_futures_ctd_from_manual_entry,
)
from shiori_pricing_lab.pricing.bli_bond_convention_profile import get_convention_profile
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    MARKET_EUREX_GERMAN,
    MARKET_US_TREASURY,
    QUOTE_CONVENTION_32NDS,
    QUOTE_CONVENTION_DECIMAL,
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
    TREASURY_FUTURES_CONTRACTS,
    TreasuryFuturesContract,
    TreasuryFuturesQuoteError,
    format_futures_quote,
    get_contract,
    minimum_tick,
    parse_futures_quote,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
    BOND_CONVENTION_PROFILE_BY_MARKET,
    TreasuryFuturesYieldError,
    _policy_for_market,
    _resolve_pricing_policy,
    accrued_interest_per_100,
    futures_price_from_target_yield,
    implied_yield_from_futures_price,
)
from shiori_pricing_lab.products.enums import DayCount, Frequency

_REPOS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPOS_ROOT / "tools"))
import treasury_futures_implied_yield_acceptance as acceptance  # noqa: E402

EUREX_CODES = ("FGBS", "FGBM", "FGBL", "FGBX")
UST_CODES = ("ZT", "ZF", "ZN", "ZB", "UXY", "WN")

#: On-tick decimal prices used for the round-trip tests (one per grid).
EUREX_ROUND_TRIP_PRICES = {
    "FGBS": 105.125,
    "FGBM": 98.50,
    "FGBL": 101.25,
    "FGBX": 88.10,
}

#: Target yields with a solvable, well-conditioned answer per contract.
EUREX_ROUND_TRIP_YIELDS = {
    "FGBS": 4.0,
    "FGBM": 4.0,
    "FGBL": 4.0,
    "FGBX": 4.0,
}

#: Stage-three live schedule evidence (Eddy's Bloomberg workstation,
#: 2026-09-08): ISSUE_DT is the accrual start, FIRST_CPN_DT the first actual
#: coupon. Same Bloomberg run as the RED Gate 1 stage-two values.
LIVE_SCHEDULE = {
    "FGBS": {"ISSUE_DT": "2026-07-16", "FIRST_CPN_DT": "2027-09-13"},
    "FGBM": {"ISSUE_DT": "2026-07-23", "FIRST_CPN_DT": "2027-10-08"},
    "FGBL": {"ISSUE_DT": "2025-07-04", "FIRST_CPN_DT": "2026-08-15"},
    "FGBX": {"ISSUE_DT": "2024-02-06", "FIRST_CPN_DT": "2025-08-15"},
}


def _three_stage_responder(
    *, active, delivery, bond, active_fields, stage_two_fields, schedule_fields
):
    """Fake DAPI answering all three Eurex stages, including the schedule."""

    two_stage = _two_stage_responder(
        active_fields=active_fields,
        stage_two_fields=stage_two_fields,
        active=active,
        delivery=delivery,
    )

    def _respond(security):
        if security == bond:
            return _response_event([_security_data(security, schedule_fields)])
        return two_stage(security)

    return _respond


def _load_eurex_with(monkeypatch, contract_code, *, stage_two=None, schedule=None):
    """Run the live loader for one Eurex contract with chosen payloads."""

    resolved = LIVE_DELIVERY_SYMBOL[contract_code]
    live = LIVE_STAGE_TWO[contract_code]
    _install_fake_blpapi(
        monkeypatch,
        _three_stage_responder(
            active_fields={"PARSEKYABLE_DES": f"{resolved} Comdty"},
            stage_two_fields=dict(stage_two or live),
            schedule_fields=dict(
                schedule
                or {
                    "ISSUE_DT": LIVE_SCHEDULE[contract_code]["ISSUE_DT"],
                    "FIRST_CPN_DT": LIVE_SCHEDULE[contract_code]["FIRST_CPN_DT"],
                }
            ),
            active=bloomberg_active_contract(contract_code),
            delivery=f"{resolved} Comdty",
            bond=f"/isin/{live['FUT_CTD_ISIN']}",
        ),
    )
    return load_bloomberg_ctd_metadata(contract_code)


# ---------------------------------------------------------------------------
# Registry, market membership, aliases, roots
# ---------------------------------------------------------------------------


def test_all_four_german_contracts_are_registered_in_market_order() -> None:
    assert SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES[-4:] == EUREX_CODES
    assert EUREX_GERMAN_FUTURES_CODES == EUREX_CODES
    for code in EUREX_CODES:
        contract = get_contract(code)
        assert contract.market == MARKET_EUREX_GERMAN
        assert contract.quote_convention == QUOTE_CONVENTION_DECIMAL
    for code in UST_CODES:
        assert get_contract(code).market == MARKET_US_TREASURY
        assert get_contract(code).quote_convention == QUOTE_CONVENTION_32NDS


def test_market_membership_cannot_drift_between_the_two_tables() -> None:
    """The contract table's ``market`` and the CTD loader's Eurex set agree."""

    from_contracts = {
        code
        for code in SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES
        if get_contract(code).market == MARKET_EUREX_GERMAN
    }
    assert from_contracts == set(EUREX_GERMAN_FUTURES_CODES)


def test_active_aliases_are_the_eurex_desk_aliases() -> None:
    assert BLOOMBERG_FUTURES_ACTIVE_ALIASES["FGBS"] == "DUA"
    assert BLOOMBERG_FUTURES_ACTIVE_ALIASES["FGBM"] == "OEA"
    assert BLOOMBERG_FUTURES_ACTIVE_ALIASES["FGBL"] == "RXA"
    assert BLOOMBERG_FUTURES_ACTIVE_ALIASES["FGBX"] == "UBA"
    assert bloomberg_active_contract("FGBS") == "DUA Comdty"
    assert bloomberg_active_contract("FGBM") == "OEA Comdty"
    assert bloomberg_active_contract("FGBL") == "RXA Comdty"
    assert bloomberg_active_contract("FGBX") == "UBA Comdty"


def test_delivery_roots_are_the_eurex_futures_roots() -> None:
    assert BLOOMBERG_FUTURES_TICKER_ROOTS["FGBS"] == "DU"
    assert BLOOMBERG_FUTURES_TICKER_ROOTS["FGBM"] == "OE"
    assert BLOOMBERG_FUTURES_TICKER_ROOTS["FGBL"] == "RX"
    assert BLOOMBERG_FUTURES_TICKER_ROOTS["FGBX"] == "UB"


@pytest.mark.parametrize(
    "contract_code, symbol, ok",
    [
        ("FGBS", "DUZ6 Comdty", True),
        ("FGBM", "OEZ6 Comdty", True),
        ("FGBL", "RXZ6 Comdty", True),
        ("FGBX", "UBZ6 Comdty", True),
        ("FGBS", "TYZ6 Comdty", False),  # ZN's month answered for FGBS
        ("FGBL", "RXZ6 Comdty", True),
        ("FGBL", "DUZ6 Comdty", False),  # Schatz month answered for Bund
        ("FGBS", "DUF7 Comdty", False),  # not a quarterly delivery month
        ("FGBS", "DUZ26 Comdty", True),  # two-digit year form is accepted
    ],
)
def test_explicit_symbol_root_validation(contract_code, symbol, ok) -> None:
    if ok:
        assert (
            _require_delivery_ticker(symbol, contract_code, "test") == symbol.split()[0]
        )
    else:
        with pytest.raises(TreasuryFuturesCTDBloombergError):
            _require_delivery_ticker(symbol, contract_code, "test")


# ---------------------------------------------------------------------------
# Decimal ticks and quote behavior
# ---------------------------------------------------------------------------


def test_decimal_minimum_ticks_are_the_published_eurex_ticks() -> None:
    assert minimum_tick("FGBS") == 0.005
    assert minimum_tick("FGBM") == 0.01
    assert minimum_tick("FGBL") == 0.01
    assert minimum_tick("FGBX") == 0.02
    assert get_contract("FGBS").minimum_tick_label == "0.005 point"
    assert get_contract("FGBM").minimum_tick_label == "0.01 point"
    assert get_contract("FGBL").minimum_tick_label == "0.01 point"
    assert get_contract("FGBX").minimum_tick_label == "0.02 point"
    # Decimal contracts have no 32nds alphabet at all.
    for code in EUREX_CODES:
        assert get_contract(code).sub_32nd_digits == {}
        assert get_contract(code).accepts_half_32nd_suffix is False


@pytest.mark.parametrize(
    "code, raw, expected",
    [
        ("FGBS", "105.125", 105.125),
        ("FGBS", 105.125, 105.125),
        ("FGBM", "98.50", 98.5),
        ("FGBL", "101.25", 101.25),
        ("FGBX", "88.10", 88.1),
        ("FGBS", "105.126", 105.126),  # off-tick decimals are readable, not on tick
    ],
)
def test_decimal_quotes_parse(code, raw, expected) -> None:
    quote = parse_futures_quote(code, raw)
    assert quote.decimal_price == pytest.approx(expected)


def test_off_tick_decimal_is_flagged_not_rounded() -> None:
    quote = parse_futures_quote("FGBS", "105.126")
    assert quote.on_tick is False
    assert quote.exchange_quote == "105.125"
    assert quote.decimal_price == pytest.approx(105.126)


@pytest.mark.parametrize(
    "code, price, expected",
    [
        ("FGBS", 105.125, "105.125"),
        ("FGBM", 98.5, "98.50"),
        ("FGBL", 101.25, "101.25"),
        ("FGBX", 88.1, "88.10"),
    ],
)
def test_decimal_prices_format_at_the_contract_precision(code, price, expected) -> None:
    assert format_futures_quote(code, price) == expected


@pytest.mark.parametrize("code", EUREX_CODES)
@pytest.mark.parametrize("raw", ["105-12", "105-125", "105'125", "105 1/2", "105-12+"])
def test_a_32nds_quote_is_refused_on_a_decimal_grid(code, raw) -> None:
    """A 32nds string is never a price on a decimal grid -- refuse, don't misread."""

    with pytest.raises(TreasuryFuturesQuoteError):
        parse_futures_quote(code, raw)


def test_a_non_numeric_quote_is_refused_with_decimal_guidance() -> None:
    with pytest.raises(TreasuryFuturesQuoteError) as exc:
        parse_futures_quote("FGBL", "abc")
    assert "decimal" in str(exc.value)


# ---------------------------------------------------------------------------
# Identity: DE ISIN acceptance, UST behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_confirmed_live_ctd_loads_with_no_synthetic_fallback(monkeypatch, contract_code) -> None:
    ctd = _load_eurex_with(monkeypatch, contract_code)
    live = LIVE_STAGE_TWO[contract_code]
    assert ctd.contract_symbol == LIVE_DELIVERY_SYMBOL[contract_code]
    assert ctd.ctd_identifier == live["FUT_CTD_ISIN"]
    assert ctd.ctd_identifier.startswith("DE")
    assert ctd.ctd_coupon_percent == float(live["FUT_CTD_CPN"])
    assert ctd.ctd_maturity_date == date.fromisoformat(live["FUT_CTD_MTY"])
    assert ctd.conversion_factor == float(live["FUT_CNVS_FACTOR"])
    assert ctd.last_delivery_date == date.fromisoformat(live["FUT_DLV_DT_LAST"])
    assert ctd.source is TreasuryFuturesCTDSource.BLOOMBERG_DAPI
    assert ctd.is_confirmed_source is True


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_three_stage_lookup_resolves_alias_then_ctd_then_schedule(
    monkeypatch, contract_code
) -> None:
    live = LIVE_STAGE_TWO[contract_code]
    resolved = LIVE_DELIVERY_SYMBOL[contract_code]
    active = bloomberg_active_contract(contract_code)
    bond = f"/isin/{live['FUT_CTD_ISIN']}"
    harness = _install_fake_blpapi(
        monkeypatch,
        _three_stage_responder(
            active_fields={"PARSEKYABLE_DES": f"{resolved} Comdty"},
            stage_two_fields=dict(live),
            schedule_fields=dict(LIVE_SCHEDULE[contract_code]),
            active=active,
            delivery=f"{resolved} Comdty",
            bond=bond,
        ),
    )
    ctd = load_bloomberg_ctd_metadata(contract_code)
    assert ctd.contract_symbol == resolved
    assert ctd.first_accrual_start == date.fromisoformat(
        LIVE_SCHEDULE[contract_code]["ISSUE_DT"]
    )
    assert ctd.first_coupon_date == date.fromisoformat(
        LIVE_SCHEDULE[contract_code]["FIRST_CPN_DT"]
    )
    assert [security for security, _ in harness["requests"]] == [
        active,
        f"{resolved} Comdty",
        bond,
    ]


def test_a_us_isin_on_a_eurex_request_is_refused(monkeypatch) -> None:
    """Cross-market identity runs the market's own country rule, not a window."""

    fields = dict(LIVE_STAGE_TWO["FGBM"], FUT_CTD_ISIN="US91282CRJ26")
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _load_eurex_with(monkeypatch, "FGBM", stage_two=fields)
    assert "non-DE ISIN" in str(exc.value)


def test_a_de_isin_on_a_ust_request_is_refused(monkeypatch) -> None:
    """UST behavior is unchanged: a German ISIN is not a Treasury CTD."""

    fields = dict(LIVE_STAGE_TWO["ZN"], FUT_CTD_ISIN="DE000BU2Z056")
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _load_with(monkeypatch, "ZN", stage_two=fields)
    assert "non-US ISIN" in str(exc.value)


def test_a_de_isin_with_a_bad_check_digit_is_refused(monkeypatch) -> None:
    fields = dict(LIVE_STAGE_TWO["FGBS"], FUT_CTD_ISIN="DE000BU22149")
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _load_eurex_with(monkeypatch, "FGBS", stage_two=fields)
    assert "check digit is invalid" in str(exc.value)


# ---------------------------------------------------------------------------
# CUSIP: display-only for Eurex, never reconciled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_live_eurex_cusip_values_are_kept_as_display_only(monkeypatch, contract_code) -> None:
    """The live FUT_CTD_CUSIP values name a different vendor identifier, not
    the ISIN's characters -- and the load must still succeed with them kept."""

    ctd = _load_eurex_with(monkeypatch, contract_code)
    live = LIVE_STAGE_TWO[contract_code]
    assert live["FUT_CTD_ISIN"][2:11] != live["FUT_CTD_CUSIP"]
    assert ctd.ctd_cusip == live["FUT_CTD_CUSIP"]
    assert ctd.ctd_identifier == live["FUT_CTD_ISIN"]


def test_a_contradictory_cusip_does_not_block_a_eurex_load(monkeypatch) -> None:
    fields = dict(LIVE_STAGE_TWO["FGBL"], FUT_CTD_CUSIP="CONTRADICT")
    ctd = _load_eurex_with(monkeypatch, "FGBL", stage_two=fields)
    assert ctd.ctd_identifier == "DE000BU2Z056"
    assert ctd.ctd_cusip == "CONTRADICT"


# ---------------------------------------------------------------------------
# Description: German pattern kept when coherent, dropped when not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_every_live_german_description_is_kept_because_it_agrees(
    monkeypatch, contract_code
) -> None:
    ctd = _load_eurex_with(monkeypatch, contract_code)
    assert ctd.ctd_description == LIVE_STAGE_TWO[contract_code]["FUT_CTD_TICKER"]


@pytest.mark.parametrize(
    "description",
    [
        "DBR 2.8 09/13/28",  # coupon disagrees with FUT_CTD_CPN
        "BKO 2.7 09/13/29",  # year disagrees with FUT_CTD_MTY
        "T 2.7 09/13/28",  # UST pattern is not applied to German contracts
        "#N/A N/A",
        "GARBAGE",
    ],
)
def test_a_german_description_that_disagrees_is_dropped(monkeypatch, description) -> None:
    fields = dict(LIVE_STAGE_TWO["FGBS"], FUT_CTD_TICKER=description)
    ctd = _load_eurex_with(monkeypatch, "FGBS", stage_two=fields)
    assert ctd.ctd_description is None
    assert ctd.ctd_identifier == "DE000BU22148"  # the priced record is unaffected
    assert ctd.ctd_coupon_percent == 2.7
    assert ctd.ctd_maturity_date == date(2028, 9, 13)


# ---------------------------------------------------------------------------
# Delivery dates: in-month rule, never ZT/ZF next-month
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contract_code, symbol",
    [("FGBS", "DUZ6"), ("FGBM", "OEZ6"), ("FGBL", "RXZ6"), ("FGBX", "UBZ6")],
)
def test_eurex_last_delivery_sits_inside_the_delivery_month(contract_code, symbol) -> None:
    assert (
        _delivery_month_first_day(symbol, contract_code, date(2026, 12, 10))
        == date(2026, 12, 1)
    )


def test_a_next_month_delivery_date_is_refused_for_eurex() -> None:
    """ZT/ZF's (1, 2) span must never leak onto Eurex: January is not December."""

    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _delivery_month_first_day("DUZ6", "FGBS", date(2027, 1, 5))
    assert "unresolvable" in str(exc.value)


# ---------------------------------------------------------------------------
# Remaining-maturity windows
# ---------------------------------------------------------------------------


def test_window_config_is_the_issue_204_eurex_windows() -> None:
    assert TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS["FGBS"] == (21, 27, True)
    assert TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS["FGBM"] == (54, 66, True)
    assert TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS["FGBL"] == (102, 126, True)
    assert TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS["FGBX"] == (288, 420, True)


@pytest.mark.parametrize(
    "code, symbol, maturity, passes",
    [
        # FGBS window from 2026-12-01: [2028-09-01, 2029-03-01].
        ("FGBS", "DUZ6", date(2028, 9, 1), True),
        ("FGBS", "DUZ6", date(2028, 8, 31), False),
        ("FGBS", "DUZ6", date(2029, 3, 1), True),
        ("FGBS", "DUZ6", date(2029, 3, 2), False),
        # FGBM window from 2026-12-01: [2031-06-01, 2032-06-01].
        ("FGBM", "OEZ6", date(2031, 6, 1), True),
        ("FGBM", "OEZ6", date(2031, 5, 31), False),
        ("FGBM", "OEZ6", date(2032, 6, 1), True),
        ("FGBM", "OEZ6", date(2032, 6, 2), False),
        # FGBL window from 2026-12-01: [2035-06-01, 2037-06-01].
        ("FGBL", "RXZ6", date(2035, 6, 1), True),
        ("FGBL", "RXZ6", date(2035, 5, 31), False),
        ("FGBL", "RXZ6", date(2037, 6, 1), True),
        ("FGBL", "RXZ6", date(2037, 6, 2), False),
        # FGBX window from 2026-12-01: [2050-12-01, 2061-12-01].
        ("FGBX", "UBZ6", date(2050, 12, 1), True),
        ("FGBX", "UBZ6", date(2050, 11, 30), False),
        ("FGBX", "UBZ6", date(2061, 12, 1), True),
        ("FGBX", "UBZ6", date(2061, 12, 2), False),
    ],
)
def test_window_boundaries_are_exact(code, symbol, maturity, passes) -> None:
    if passes:
        _require_remaining_maturity_plausible(code, symbol, maturity, date(2026, 12, 10), "test")
    else:
        with pytest.raises(TreasuryFuturesCTDBloombergError):
            _require_remaining_maturity_plausible(
                code, symbol, maturity, date(2026, 12, 10), "test"
            )


@pytest.mark.parametrize("requested", EUREX_CODES)
@pytest.mark.parametrize("donor", EUREX_CODES)
def test_cross_substituted_eurex_ctds_fail_closed(monkeypatch, requested, donor) -> None:
    """Every off-diagonal Eurex pairing is refused; the diagonal still loads."""

    donor_fields = dict(
        LIVE_STAGE_TWO[donor],
        FUT_DLV_DT_LAST=LIVE_STAGE_TWO[requested]["FUT_DLV_DT_LAST"],
    )
    if requested == donor:
        assert _load_eurex_with(monkeypatch, requested, stage_two=donor_fields) is not None
        return
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _load_eurex_with(monkeypatch, requested, stage_two=donor_fields)
    assert f"{requested}'s remaining-maturity window" in str(exc.value)


@pytest.mark.parametrize(
    "requested, donor",
    [
        ("FGBM", "ZN"),  # UST CTD carries a US ISIN, refused by identity first
        ("FGBL", "ZB"),
        ("ZN", "FGBL"),  # German CTD carries a DE ISIN, refused by identity first
        ("ZB", "FGBX"),
    ],
)
def test_cross_market_ctd_substitution_is_refused(monkeypatch, requested, donor) -> None:
    donor_fields = dict(
        LIVE_STAGE_TWO[donor],
        FUT_DLV_DT_LAST=LIVE_STAGE_TWO[requested]["FUT_DLV_DT_LAST"],
    )
    load = _load_eurex_with if requested in EUREX_CODES else _load_with
    with pytest.raises(TreasuryFuturesCTDBloombergError):
        load(monkeypatch, requested, stage_two=donor_fields)


# ---------------------------------------------------------------------------
# Yield convention: GERMAN_GOVT on every Eurex answer, UST untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_every_eurex_answer_is_stamped_german_govt_annual(monkeypatch, contract_code) -> None:
    ctd = _load_eurex_with(monkeypatch, contract_code)
    for payload in (
        implied_yield_from_futures_price(ctd, EUREX_ROUND_TRIP_PRICES[contract_code]).as_payload(),
        futures_price_from_target_yield(
            ctd, EUREX_ROUND_TRIP_YIELDS[contract_code]
        ).as_payload(),
    ):
        methodology = payload["methodology"]
        assert methodology["basis"] == "SHIORI_EUREX_GERMAN_CTD_IMPLIED_FORWARD_YIELD"
        assert methodology["market"] == "EUREX_DE"
        assert methodology["bond_convention_profile"] == "GERMAN_GOVT"
        assert methodology["coupon_frequency"] == "ANNUAL"
        assert methodology["day_count"] == "ACT_ACT_BOND"
        assert methodology["par"] == 100.0
        assert methodology["carry_adjustment"] == "NONE"
        assert methodology["settlement_date_rule"] == "FUTURES_CONTRACT_LAST_DELIVERY_DAY"


def test_the_german_govt_profile_is_the_registered_annual_act_act_target_profile() -> None:
    profile = get_convention_profile("GERMAN_GOVT")
    assert profile.name == "GERMAN_GOVT"
    assert BOND_CONVENTION_PROFILE_BY_MARKET["EUREX_DE"] == "GERMAN_GOVT"
    assert BOND_CONVENTION_PROFILE_BY_MARKET["UST"] == "UST"


# ---------------------------------------------------------------------------
# Pricing policy: bound to the registered profile, fail-closed (Sophira gate)
# ---------------------------------------------------------------------------


def test_ust_contracts_resolve_to_the_registered_ust_profile() -> None:
    for code in UST_CODES:
        policy = _resolve_pricing_policy(code)
        assert policy.market == "UST"
        assert policy.convention_profile == "UST"
        assert policy.coupon_frequency == Frequency.SEMI_ANNUAL
        assert policy.coupons_per_year == 2
        assert policy.day_count == DayCount.ACT_ACT_BOND


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_eurex_contracts_resolve_to_the_registered_german_govt_profile(
    contract_code,
) -> None:
    policy = _resolve_pricing_policy(contract_code)
    assert policy.market == "EUREX_DE"
    assert policy.convention_profile == "GERMAN_GOVT"
    assert policy.coupon_frequency == Frequency.ANNUAL
    assert policy.coupons_per_year == 1
    assert policy.day_count == DayCount.ACT_ACT_BOND


def test_german_policy_values_come_from_the_registered_profile() -> None:
    """Frequency/day count are read off GERMAN_GOVT, not hard-coded for Eurex."""

    profile = get_convention_profile("GERMAN_GOVT")
    assert profile.coupon_frequencies == (Frequency.ANNUAL,)
    assert profile.day_count == DayCount.ACT_ACT_BOND
    policy = _policy_for_market("EUREX_DE")
    assert policy.convention_profile == profile.name
    assert policy.coupon_frequency == profile.coupon_frequencies[0]
    assert policy.day_count == profile.day_count


def test_an_unrecognized_market_cannot_silently_inherit_ust_behavior() -> None:
    with pytest.raises(TreasuryFuturesYieldError) as exc:
        _policy_for_market("MARS")
    assert "no futures pricing policy is registered" in str(exc.value)


def test_a_contract_on_an_unrecognized_market_cannot_inherit_ust_behavior(
    monkeypatch,
) -> None:
    bogus = TreasuryFuturesContract(
        code="XX",
        name="Bogus futures",
        market="MARS",
        quote_convention=QUOTE_CONVENTION_DECIMAL,
        ticks_per_point=100,
        ticks_per_32nd=None,
        decimal_places=2,
    )
    monkeypatch.setitem(TREASURY_FUTURES_CONTRACTS, "XX", bogus)
    with pytest.raises(TreasuryFuturesYieldError):
        _resolve_pricing_policy("XX")


def test_ust_answers_keep_their_cme_basis_and_semiannual_stamp(monkeypatch) -> None:
    ctd = _load_with(monkeypatch, "ZN")
    methodology = implied_yield_from_futures_price(ctd, 110.5).as_payload()["methodology"]
    assert methodology["basis"] == "CME_TREASURY_ANALYTICS_CTD_IMPLIED_FORWARD_YIELD"
    assert methodology["market"] == "UST"
    assert methodology["bond_convention_profile"] == "UST"
    assert methodology["coupon_frequency"] == "SEMI_ANNUAL"


def test_annual_accrued_uses_one_coupon_over_actual_days() -> None:
    """Schatz CTD: 2.7% annual, period 2026-09-13 -> 2027-09-13, settled 2026-12-10."""

    assert accrued_interest_per_100(
        date(2026, 12, 10), date(2028, 9, 13), 2.7, coupons_per_year=1
    ) == pytest.approx(2.7 * 88 / 365)


# ---------------------------------------------------------------------------
# Round trips (self-consistency; benchmark parity remains workstation UAT)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_price_yield_price_residual_is_below_one_tick(monkeypatch, contract_code) -> None:
    ctd = _load_eurex_with(monkeypatch, contract_code)
    price = EUREX_ROUND_TRIP_PRICES[contract_code]
    forward = implied_yield_from_futures_price(ctd, price)
    back = futures_price_from_target_yield(ctd, forward.implied_yield_percent)
    assert abs(back.futures_price - price) < minimum_tick(contract_code)


@pytest.mark.parametrize("contract_code", EUREX_CODES)
def test_yield_price_yield_residual_is_within_half_a_basis_point(
    monkeypatch, contract_code
) -> None:
    ctd = _load_eurex_with(monkeypatch, contract_code)
    target = EUREX_ROUND_TRIP_YIELDS[contract_code]
    price_leg = futures_price_from_target_yield(ctd, target)
    round_trip = implied_yield_from_futures_price(ctd, price_leg.futures_price)
    assert abs(round_trip.implied_yield_percent - target) <= 0.005


# ---------------------------------------------------------------------------
# RED regression: Bloomberg YAS exact case (Sophira gate)
# ---------------------------------------------------------------------------


def _fgbs_yas_ctd():
    return treasury_futures_ctd_from_manual_entry(
        {
            "contract_code": "FGBS",
            "contract_symbol": "DUZ6",
            "ctd_identifier": "DE000BU22148",
            "ctd_coupon_percent": 2.7,
            "ctd_maturity_date": "2028-09-13",
            "conversion_factor": 0.946091,
            "last_delivery_date": "2026-12-10",
            "as_of": "2026-09-08T00:00:00Z",
            "first_accrual_start": "2026-07-16",
            "first_coupon_date": "2027-09-13",
        }
    )


def test_yas_red_case_accrued_matches_bloomberg_to_displayed_precision() -> None:
    from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
        IrregularFirstCoupon,
        accrued_interest_per_100,
    )

    assert accrued_interest_per_100(
        date(2026, 12, 10),
        date(2028, 9, 13),
        2.7,
        coupons_per_year=1,
        schedule=IrregularFirstCoupon(date(2026, 7, 16), date(2027, 9, 13)),
    ) == pytest.approx(1.08739726, abs=1e-8)


def test_yas_red_case_yield_matches_bloomberg() -> None:
    fwd = implied_yield_from_futures_price(_fgbs_yas_ctd(), 105.065)
    assert fwd.converted_clean_price == pytest.approx(99.401051, abs=1e-6)
    assert fwd.accrued_interest == pytest.approx(1.08739726, abs=1e-8)
    assert fwd.implied_yield_percent == pytest.approx(3.044683, abs=1e-6)


def test_fgbm_same_mechanism_matches_its_independent_check() -> None:
    ctd = treasury_futures_ctd_from_manual_entry(
        {
            "contract_code": "FGBM",
            "contract_symbol": "OEZ6",
            "ctd_identifier": "DE000BU25075",
            "ctd_coupon_percent": 2.9,
            "ctd_maturity_date": "2031-10-08",
            "conversion_factor": 0.872911,
            "last_delivery_date": "2026-12-10",
            "as_of": "2026-09-08T00:00:00Z",
            "first_accrual_start": "2026-07-23",
            "first_coupon_date": "2027-10-08",
        }
    )
    fwd = implied_yield_from_futures_price(ctd, 113.24)
    # Sophira independent check: 3.155945% vs BB 3.1559% => 0.0045 bp
    assert fwd.implied_yield_percent == pytest.approx(3.155945, abs=1e-5)


def test_schedule_ignored_once_past_the_first_coupon_fgbl_fgbx_identical() -> None:
    # FGBL seasoned past its first coupon (2026-08-15) => identical
    base = {
        "contract_code": "FGBL",
        "contract_symbol": "RXZ6",
        "ctd_identifier": "DE000BU2Z056",
        "ctd_coupon_percent": 2.6,
        "ctd_maturity_date": "2035-08-15",
        "conversion_factor": 0.774902,
        "last_delivery_date": "2026-12-10",
        "as_of": "2026-09-08T00:00:00Z",
    }
    with_sched = dict(base, first_accrual_start="2025-07-04", first_coupon_date="2026-08-15")
    a = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(base), 101.25
    )
    b = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(with_sched), 101.25
    )
    assert a.implied_yield_percent == b.implied_yield_percent
    assert a.accrued_interest == b.accrued_interest
    assert a.converted_clean_price == b.converted_clean_price


def test_regular_first_coupon_collapses_to_the_plain_grid() -> None:
    base = {
        "contract_code": "FGBS",
        "contract_symbol": "DUZ6",
        "ctd_identifier": "DE000BU22148",
        "ctd_coupon_percent": 2.7,
        "ctd_maturity_date": "2028-09-13",
        "conversion_factor": 0.946091,
        "last_delivery_date": "2026-12-10",
        "as_of": "2026-09-08T00:00:00Z",
    }
    # accrual_start == quasi date => factor 1, stub 0
    with_regular = dict(
        base, first_accrual_start="2026-09-13", first_coupon_date="2027-09-13"
    )
    a = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(base), 105.065
    )
    b = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(with_regular), 105.065
    )
    assert a.implied_yield_percent == b.implied_yield_percent


def test_ust_with_a_past_schedule_is_untouched() -> None:
    base = {
        "contract_code": "ZN",
        "contract_symbol": "TYZ6",
        "ctd_identifier": "US91282CRJ26",
        "ctd_coupon_percent": 4.5,
        "ctd_maturity_date": "2033-08-31",
        "conversion_factor": 0.9202,
        "last_delivery_date": "2026-12-31",
        "as_of": "2026-09-08T00:00:00Z",
    }
    with_past = dict(
        base, first_accrual_start="2020-03-15", first_coupon_date="2020-09-15"
    )
    a = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(base), 112.515625
    )
    b = implied_yield_from_futures_price(
        treasury_futures_ctd_from_manual_entry(with_past), 112.515625
    )
    assert a.implied_yield_percent == b.implied_yield_percent


def test_stage_three_missing_first_coupon_fails_closed(monkeypatch) -> None:
    with pytest.raises(TreasuryFuturesCTDBloombergError):
        _load_eurex_with(
            monkeypatch, "FGBS", schedule={"ISSUE_DT": "2026-07-16"}
        )


def test_manual_half_schedule_is_refused() -> None:
    with pytest.raises(TreasuryFuturesCTDError):
        treasury_futures_ctd_from_manual_entry(
            {
                "contract_code": "FGBS",
                "contract_symbol": "DUZ6",
                "ctd_identifier": "DE000BU22148",
                "ctd_coupon_percent": 2.7,
                "ctd_maturity_date": "2028-09-13",
                "conversion_factor": 0.946091,
                "last_delivery_date": "2026-12-10",
                "as_of": "2026-09-08T00:00:00Z",
                "first_accrual_start": "2026-07-16",
            }
        )


def test_settlement_before_accrual_start_is_refused() -> None:
    from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
        IrregularFirstCoupon,
        accrued_interest_per_100,
    )

    with pytest.raises(TreasuryFuturesYieldError):
        accrued_interest_per_100(
            date(2026, 7, 1),
            date(2028, 9, 13),
            2.7,
            coupons_per_year=1,
            schedule=IrregularFirstCoupon(date(2026, 7, 16), date(2027, 9, 13)),
        )


# ---------------------------------------------------------------------------
# Registry exposure: workbench catalogue and acceptance CLI
# ---------------------------------------------------------------------------


def test_workbench_catalogue_lists_the_german_market_with_decimal_ticks() -> None:
    catalogue = treasury_futures_contract_catalogue()
    by_code = {contract["code"]: contract for contract in catalogue["contracts"]}
    assert [c["code"] for c in catalogue["contracts"]][-4:] == list(EUREX_CODES)
    assert by_code["FGBS"]["market"] == "EUREX_DE"
    assert by_code["FGBS"]["market_label"] == "German Government Bond Futures (Eurex)"
    assert by_code["FGBS"]["quote_convention"] == "DECIMAL"
    assert by_code["FGBS"]["minimum_tick"] == 0.005
    assert by_code["FGBX"]["minimum_tick"] == 0.02
    assert "Annual" in by_code["FGBL"]["methodology_note"]
    assert "Semiannual" in by_code["ZN"]["methodology_note"]


@pytest.mark.parametrize(
    "contract_code, price",
    [("FGBS", "105.125"), ("FGBM", "98.50"), ("FGBL", "101.25"), ("FGBX", "88.10")],
)
def test_acceptance_cli_reports_the_german_contracts(
    monkeypatch, capsys, contract_code, price
) -> None:
    resolved = LIVE_DELIVERY_SYMBOL[contract_code]
    live = LIVE_STAGE_TWO[contract_code]
    _install_fake_blpapi(
        monkeypatch,
        _three_stage_responder(
            active_fields={"PARSEKYABLE_DES": f"{resolved} Comdty"},
            stage_two_fields=dict(live),
            schedule_fields=dict(LIVE_SCHEDULE[contract_code]),
            active=bloomberg_active_contract(contract_code),
            delivery=f"{resolved} Comdty",
            bond=f"/isin/{live['FUT_CTD_ISIN']}",
        ),
    )
    assert acceptance.main(["--price", f"{contract_code}={price}"]) == 0
    output = capsys.readouterr().out
    assert resolved in output
    assert live["FUT_CTD_ISIN"] in output
    assert "OUT OF TOLERANCE" not in output
