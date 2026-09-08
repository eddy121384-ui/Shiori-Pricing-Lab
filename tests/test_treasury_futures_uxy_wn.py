"""Issue #202: UXY and WN contract-universe expansion.

Focused coverage for the two new contracts on top of the existing
per-module suites (which now also parametrize over all six):

- registry / support detection and tick sizes (1/64 for UXY, 1/32 for WN)
- desk quote parsing / formatting on the new grids
- Bloomberg active-alias mapping and explicit-symbol root validation
- remaining-maturity window lower/upper boundaries
- full two-stage live loads with the confirmed Issue #202 CTD values
- cross-contract substitution rejection against the neighbouring contracts
- workbench catalogue and acceptance-CLI exposure (both registry-driven)

No pricing methodology lives here: round-trip tolerances are covered by
``test_treasury_futures_implied_yield`` through the shared ``CTD_BY_CONTRACT``
fixture. No live Bloomberg parity values are fabricated: the only numbers
asserted are the confirmed workstation evidence quoted in Issue #202 and
pure arithmetic on the tick grids.
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
    _two_stage_responder,
)

from shiori_pricing_lab.app.standalone_option_workbench_server import (
    treasury_futures_contract_catalogue,
)
from shiori_pricing_lab.data.treasury_futures_ctd import (
    BLOOMBERG_FUTURES_ACTIVE_ALIASES,
    BLOOMBERG_FUTURES_TICKER_ROOTS,
    TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS,
    TreasuryFuturesCTDBloombergError,
    TreasuryFuturesCTDSource,
    _require_delivery_ticker,
    _require_remaining_maturity_plausible,
    bloomberg_active_contract,
)
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
    format_futures_quote,
    get_contract,
    minimum_tick,
    parse_futures_quote,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import treasury_futures_implied_yield_acceptance as acceptance  # noqa: E402


def test_uxy_and_wn_are_supported_alongside_the_mvp_four() -> None:
    assert SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES == (
        "ZT",
        "ZF",
        "ZN",
        "ZB",
        "UXY",
        "WN",
        "FGBS",
        "FGBM",
        "FGBL",
        "FGBX",
    )
    assert get_contract("UXY").name == "Ultra 10-Year U.S. Treasury Note futures"
    assert get_contract("WN").name == "Ultra U.S. Treasury Bond futures"


def test_uxy_trades_zn_grid_and_wn_trades_zb_grid() -> None:
    assert minimum_tick("UXY") == minimum_tick("ZN") == 1 / 64
    assert minimum_tick("WN") == minimum_tick("ZB") == 1 / 32
    assert get_contract("UXY").ticks_per_32nd == 2
    assert get_contract("WN").ticks_per_32nd == 1
    assert sorted(get_contract("UXY").sub_32nd_digits) == ["0", "5"]
    assert sorted(get_contract("WN").sub_32nd_digits) == ["0"]


@pytest.mark.parametrize(
    "code, raw, expected",
    [
        ("UXY", "104-08", 104 + 8 / 32),
        ("UXY", "104-085", 104 + 8.5 / 32),
        ("UXY", "104-08+", 104 + 8.5 / 32),
        ("UXY", "104-08 1/2", 104 + 8.5 / 32),
        ("WN", "132-24", 132 + 24 / 32),
        ("WN", "132-240", 132 + 24 / 32),
    ],
)
def test_desk_quotes_parse_on_the_new_grids(code, raw, expected) -> None:
    quote = parse_futures_quote(code, raw)
    assert quote.decimal_price == pytest.approx(expected)
    assert quote.on_tick is True


@pytest.mark.parametrize(
    "code, price, expected",
    [
        ("UXY", 104 + 8.5 / 32, "104-08 1/2"),
        ("WN", 132 + 24 / 32, "132-24"),
    ],
)
def test_prices_format_back_to_the_desk_notation(code, price, expected) -> None:
    assert format_futures_quote(code, price) == expected


def test_active_aliases_are_the_desk_aliases_not_continuation() -> None:
    assert BLOOMBERG_FUTURES_ACTIVE_ALIASES["UXY"] == "UXYA"
    assert BLOOMBERG_FUTURES_ACTIVE_ALIASES["WN"] == "WNA"
    assert bloomberg_active_contract("UXY") == "UXYA Comdty"
    assert bloomberg_active_contract("WN") == "WNA Comdty"


def test_delivery_roots_are_the_contracts_own_roots() -> None:
    assert BLOOMBERG_FUTURES_TICKER_ROOTS["UXY"] == "UXY"
    assert BLOOMBERG_FUTURES_TICKER_ROOTS["WN"] == "WN"


@pytest.mark.parametrize(
    "contract_code, symbol, ok",
    [
        ("UXY", "UXYZ6 Comdty", True),
        ("WN", "WNZ6 Comdty", True),
        ("UXY", "TYZ6 Comdty", False),  # ZN's month answered for UXY
        ("WN", "USZ6 Comdty", False),  # ZB's month answered for WN
        ("ZN", "UXYZ6 Comdty", False),  # UXY's month answered for ZN
        ("ZB", "WNZ6 Comdty", False),  # WN's month answered for ZB
        ("UXY", "UXYF7 Comdty", False),  # not a quarterly delivery month
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


def test_window_config_is_9y5m_to_10y_for_uxy_and_open_ended_25y_up_for_wn() -> None:
    assert TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS["UXY"] == (113, 120, True)
    assert TREASURY_FUTURES_REMAINING_MATURITY_WINDOW_MONTHS["WN"] == (300, None, True)


@pytest.mark.parametrize(
    "code, symbol, maturity, passes",
    [
        # UXY window from 2026-12-01: [2036-05-01, 2036-12-01].
        ("UXY", "UXYZ6", date(2036, 5, 1), True),
        ("UXY", "UXYZ6", date(2036, 4, 30), False),
        ("UXY", "UXYZ6", date(2036, 12, 1), True),
        ("UXY", "UXYZ6", date(2036, 12, 2), False),
        # WN window from 2026-12-01: [2051-12-01, no upper bound).
        ("WN", "WNZ6", date(2051, 12, 1), True),
        ("WN", "WNZ6", date(2051, 11, 30), False),
        ("WN", "WNZ6", date(2056, 12, 1), True),
        ("WN", "WNZ6", date(2076, 12, 1), True),
    ],
)
def test_window_boundaries_are_exact(code, symbol, maturity, passes) -> None:
    if passes:
        _require_remaining_maturity_plausible(
            code, symbol, maturity, date(2026, 12, 31), "test"
        )
    else:
        with pytest.raises(TreasuryFuturesCTDBloombergError):
            _require_remaining_maturity_plausible(
                code, symbol, maturity, date(2026, 12, 31), "test"
            )


@pytest.mark.parametrize("contract_code", ["UXY", "WN"])
def test_confirmed_live_ctd_loads_with_no_synthetic_fallback(
    monkeypatch, contract_code
) -> None:
    ctd = _load_with(monkeypatch, contract_code)
    live = LIVE_STAGE_TWO[contract_code]
    assert ctd.contract_symbol == LIVE_DELIVERY_SYMBOL[contract_code]
    assert ctd.ctd_identifier == live["FUT_CTD_ISIN"]
    assert ctd.ctd_coupon_percent == float(live["FUT_CTD_CPN"])
    assert ctd.ctd_maturity_date == date.fromisoformat(live["FUT_CTD_MTY"])
    assert ctd.conversion_factor == float(live["FUT_CNVS_FACTOR"])
    assert ctd.last_delivery_date == date.fromisoformat(live["FUT_DLV_DT_LAST"])
    assert ctd.source is TreasuryFuturesCTDSource.BLOOMBERG_DAPI
    assert ctd.is_confirmed_source is True


def test_two_stage_lookup_resolves_the_active_alias(monkeypatch) -> None:
    harness = _install_fake_blpapi(
        monkeypatch,
        _two_stage_responder(
            active_fields={"PARSEKYABLE_DES": "UXYZ6 Comdty"},
            stage_two_fields=dict(LIVE_STAGE_TWO["UXY"]),
            active="UXYA Comdty",
            delivery="UXYZ6 Comdty",
        ),
    )
    from shiori_pricing_lab.data.treasury_futures_ctd import (
        load_bloomberg_ctd_metadata,
    )

    ctd = load_bloomberg_ctd_metadata("UXY")
    assert ctd.contract_symbol == "UXYZ6"
    assert [security for security, _ in harness["requests"]] == [
        "UXYA Comdty",
        "UXYZ6 Comdty",
    ]


@pytest.mark.parametrize(
    "requested, donor",
    [
        ("UXY", "ZN"),  # neighbour 10-year CTD is too short for UXY
        ("UXY", "ZB"),  # bond CTD is too long for UXY
        ("UXY", "WN"),  # ultra-bond CTD is far too long for UXY
        ("WN", "ZB"),  # bond CTD is too short for WN
        ("WN", "UXY"),  # ultra-10y CTD is far too short for WN
        ("ZB", "WN"),  # ultra-bond CTD breaks ZB's under-25y cap
        ("ZB", "UXY"),  # ultra-10y CTD is too short for ZB
    ],
)
def test_neighbouring_contract_ctds_are_rejected(monkeypatch, requested, donor) -> None:
    donor_fields = dict(
        LIVE_STAGE_TWO[donor],
        FUT_DLV_DT_LAST=LIVE_STAGE_TWO[requested]["FUT_DLV_DT_LAST"],
    )
    with pytest.raises(TreasuryFuturesCTDBloombergError) as exc:
        _load_with(monkeypatch, requested, stage_two=donor_fields)
    assert f"{requested}'s remaining-maturity window" in str(exc.value)


def test_workbench_catalogue_lists_uxy_and_wn_with_their_own_ticks() -> None:
    catalogue = treasury_futures_contract_catalogue()
    by_code = {contract["code"]: contract for contract in catalogue["contracts"]}
    assert set(("UXY", "WN")) <= set(by_code)
    assert by_code["UXY"]["minimum_tick"] == minimum_tick("UXY") == 1 / 64
    assert by_code["WN"]["minimum_tick"] == minimum_tick("WN") == 1 / 32
    assert by_code["UXY"]["ticks_per_32nd"] == 2
    assert by_code["WN"]["ticks_per_32nd"] == 1


def test_acceptance_cli_reports_uxy_and_wn(monkeypatch, capsys) -> None:
    _install_fake_blpapi(
        monkeypatch,
        _two_stage_responder(
            active_fields={"PARSEKYABLE_DES": "UXYZ6 Comdty"},
            stage_two_fields=dict(LIVE_STAGE_TWO["UXY"]),
            active="UXYA Comdty",
            delivery="UXYZ6 Comdty",
        ),
    )
    assert acceptance.main(["--price", "UXY=104-085"]) == 0
    output = capsys.readouterr().out
    assert "UXYZ6" in output
    assert "US91282CQQ77" in output
    assert "OUT OF TOLERANCE" not in output


def test_acceptance_cli_continues_to_refuse_unknown_contracts(capsys) -> None:
    assert acceptance.main(["--price", "ZQ=100-00"]) == 2
    assert capsys.readouterr().err
