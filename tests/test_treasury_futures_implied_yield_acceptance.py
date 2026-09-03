"""Tests for `tools/treasury_futures_implied_yield_acceptance.py` (Issue #190).

A workstation diagnostic CLI, not part of the pricing or workbench path.
These tests prove the things that matter about an acceptance script:

- it runs the **production** loader and the **production** calculation, so
  the numbers Eddy pastes into the issue are the ones the desk tool gives;
- it prints every CTD input the benchmark side needs, so the comparison uses
  identical inputs on both sides;
- it never fetches or defaults a futures price;
- it asserts no benchmark agreement itself, and says so;
- a failed live load is reported, never papered over.

Bloomberg is faked with the same stand-ins `test_treasury_futures_ctd` uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_treasury_futures_ctd import (
    ACTIVE_ZN,
    DELIVERY_ZN,
    _install_fake_blpapi,
    _two_stage_responder,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))
import treasury_futures_implied_yield_acceptance as module  # noqa: E402

from shiori_pricing_lab.data.treasury_futures_ctd import (  # noqa: E402
    load_bloomberg_ctd_metadata,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (  # noqa: E402
    implied_yield_from_futures_price,
)


def test_a_price_is_required_and_never_defaulted(capsys) -> None:
    assert module.main([]) == 2
    assert "at least one --price" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad", ["ZN", "ZN=", "=112-165", "ZQ=112-165", "ZN:112-165"]
)
def test_a_malformed_or_unsupported_price_argument_is_refused(capsys, bad) -> None:
    assert module.main(["--price", bad]) == 2
    assert capsys.readouterr().err


def test_the_reported_yield_is_the_production_calculation(monkeypatch, capsys) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    assert module.main(["--price", "ZN=112-165"]) == 0
    output = capsys.readouterr().out

    # Recompute through the production path directly and require the printed
    # number to be that one -- an acceptance script that reported anything
    # else would be reconciling the wrong thing.
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    expected = implied_yield_from_futures_price(
        load_bloomberg_ctd_metadata("ZN"), "112-165"
    ).implied_yield_percent
    assert f"{expected:.6f}" in output


def test_every_ctd_input_the_benchmark_needs_is_printed(monkeypatch, capsys) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112-165"])
    output = capsys.readouterr().out
    for fragment in (
        "TYZ6",                # contract symbol
        "US91282CRJ26",        # CTD ISIN
        "91282CRJ2",           # CTD CUSIP
        "T 4.5 08/31/33",      # CTD description
        "4.5",                 # coupon
        "2033-08-31",          # maturity
        "0.9202",              # conversion factor
        "2026-12-31",          # last delivery / settlement
        "BLOOMBERG_DAPI",      # source
    ):
        assert fragment in output, fragment


def test_it_states_that_it_asserts_no_benchmark_agreement(monkeypatch, capsys) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112-165"])
    output = capsys.readouterr().out
    assert "asserts nothing" in output
    assert "NOT benchmark agreement" in output
    assert "0.5 bp" in output


def test_the_internal_round_trips_are_reported_and_pass(monkeypatch, capsys) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112-165"])
    output = capsys.readouterr().out
    assert "price->yield->price" in output
    assert "yield->price->yield" in output
    assert "OUT OF TOLERANCE" not in output


def test_it_uses_the_confirmed_two_stage_lookup(monkeypatch, capsys) -> None:
    harness = _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112-165"])
    assert [security for security, _ in harness["requests"]] == [ACTIVE_ZN, DELIVERY_ZN]


def test_an_off_tick_price_is_reported_as_off_tick_not_silently_rounded(
    monkeypatch, capsys
) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112.5137"])
    output = capsys.readouterr().out
    assert "off-tick" in output


def test_the_benchmark_is_told_the_price_the_yield_was_actually_computed_from(
    monkeypatch, capsys
) -> None:
    """Codex review, PR #191 (P2).

    The reported yield comes from the entered decimal, so the benchmark must be
    set to that same decimal. Naming the rounded exchange quote here compared
    two yields from different inputs -- 0.03 bp apart on this price, before the
    benchmark is even read -- which breaks the identical-input rule this script
    states for itself and quietly spends part of the 0.5 bp budget.
    """

    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112.5137"])
    output = capsys.readouterr().out

    instruction = next(line for line in output.splitlines() if "and read its Yield" in line)
    assert "112.5137" in instruction
    # The exchange quote is now included but clearly labeled as "nearest exchange quote"
    assert "nearest exchange quote" in instruction
    assert "'112-165'" in instruction
    # They must not be displayed as an equality (e.g., "112-165 = 112.5137" is forbidden)
    assert "112-165 = 112.5137" not in instruction
    # And the mismatch is called out rather than left for the reader to notice.
    assert "not an exchange-tradable level" in output


def test_an_on_tick_price_needs_no_off_tick_warning(monkeypatch, capsys) -> None:
    """The guard above must not fire on the normal case."""

    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112-165"])
    output = capsys.readouterr().out
    assert "not an exchange-tradable level" not in output
    instruction = next(line for line in output.splitlines() if "and read its Yield" in line)
    assert "112.515625" in instruction


def test_off_tick_display_shows_entered_decimal_and_exchange_quote_separately(
    monkeypatch, capsys
) -> None:
    """P2 #1: off-tick display must show entered decimal and exchange quote as distinct values.

    The acceptance script's printed instruction must show the exact entered decimal
    as the priced input, and the exchange quote separately as the nearest tradable
    quote, without implying equality between them.
    """

    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112.5137"])
    output = capsys.readouterr().out

    instruction = next(line for line in output.splitlines() if "and read its Yield" in line)
    # The priced input is the entered decimal
    assert "112.5137" in instruction
    # The rounded exchange quote is shown separately
    assert "112-165" in instruction
    # They must not be displayed as an equality (e.g., "112-165 = 112.5137" is forbidden)
    assert "112-165 = 112.5137" not in instruction
    # Off-tick must be explicitly called out
    assert "off-tick" in output.lower()
    assert "nearest" in output.lower()


def test_a_failed_live_load_is_reported_and_exits_non_zero(capsys) -> None:
    # No fake installed: the live fetch cannot succeed here.
    assert module.main(["--price", "ZN=112-165"]) == 1
    assert "CTD LOAD FAILED" in capsys.readouterr().out


def test_a_contract_that_fails_still_lets_the_others_report(monkeypatch, capsys) -> None:
    """A four-contract UAT run must not lose three good readings to one bad
    one -- the exit code carries the failure, the output carries the rest."""

    def _respond(security):
        if security in (ACTIVE_ZN, DELIVERY_ZN):
            return _two_stage_responder()(security)
        raise RuntimeError(f"no fake response for {security!r}")

    _install_fake_blpapi(monkeypatch, _respond)
    # ZT is asked for first and blows up; ZN still reports.
    with pytest.raises(RuntimeError):
        module.main(["--price", "ZT=102-16", "--price", "ZN=112-165"])


def test_contracts_are_reported_in_a_stable_order(monkeypatch, capsys) -> None:
    _install_fake_blpapi(monkeypatch, _two_stage_responder())
    module.main(["--price", "ZN=112-165"])
    output = capsys.readouterr().out
    assert output.count("IMPLIED YIELD") == 1
