"""Treasury futures CTD implied-yield live acceptance path (Issue #190).

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production pricing path, and never imported by it. It calls the production
loader and the production calculation directly:

- ``data/treasury_futures_ctd.load_bloomberg_ctd_metadata`` (the confirmed
  two-stage Bloomberg lookup), and
- ``pricing/treasury_futures_implied_yield``'s canonical converter.

**Purpose.** Let Eddy run the real production path once per contract on his
own Bloomberg workstation and see, in one block, every input the calculation
used and every number it produced -- so he can type the *same* CTD inputs
into CME Treasury Analytics (or Bloomberg) and compare. **This script asserts
no such match itself.** It surfaces the values and the round-trip residuals
for Eddy's own acceptance judgment, the same discipline
``bloomberg_usd_sofr_par_rate_curve_acceptance.py`` already established.

**The futures price is always explicit.** This script never fetches or
guesses a market price: no Bloomberg price mnemonic has been confirmed for
these contracts, and for a reconciliation an explicit price is better anyway
-- both sides of the comparison must use the identical input, and a price
fetched a few seconds apart would not be. Pass ``--price`` per contract.

**What it reports, per contract**

- the resolved delivery month and every CTD field, with its source and the
  acquisition timestamp;
- the futures price as entered, its decimal value, and the exchange quote;
- converted clean price, accrued interest, dirty price;
- the **CTD implied forward yield** -- the number to compare;
- the two internal round-trips and their residuals against Issue #190's own
  tolerances (price -> yield -> price within one tick; yield -> price ->
  yield within 0.5 bp). Those prove self-consistency only; they say nothing
  about agreement with CME.

**Deliberately not in this script.** No net-basis, repo or carry adjustment
(the converter has none), no comparison assertion, no benchmark scraping, no
writes to the repository, and no second implementation of any calculation --
every number below comes from the production modules.

**Running it.** On a Bloomberg-networked workstation with ``blpapi``
installed and the Terminal logged in::

    python tools/treasury_futures_implied_yield_acceptance.py \\
        --price ZT=102-16 --price ZF=108-155 --price ZN=112-165 --price ZB=118-16

One contract at a time works too::

    python tools/treasury_futures_implied_yield_acceptance.py --price ZN=112-165

Paste the console output into Issue #190 alongside the CME Treasury Analytics
readings for the same contracts and prices.
"""

from __future__ import annotations

import argparse
import sys

from shiori_pricing_lab.data.treasury_futures_ctd import (
    TreasuryFuturesCTDBloombergError,
    TreasuryFuturesCTDError,
    load_bloomberg_ctd_metadata,
)
from shiori_pricing_lab.pricing.treasury_futures_contract import (
    SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES,
    TreasuryFuturesQuoteError,
    minimum_tick,
)
from shiori_pricing_lab.pricing.treasury_futures_implied_yield import (
    TreasuryFuturesYieldError,
    futures_price_from_target_yield,
    implied_yield_from_futures_price,
)

#: Issue #190's own acceptance tolerances, restated here only to label the
#: residuals this script prints. The external comparison against CME is a
#: human judgment; these two are internal self-consistency.
ROUND_TRIP_YIELD_TOLERANCE_PERCENT = 0.005  # 0.5 bp


def _parse_price_arguments(raw_prices: list[str]) -> dict[str, str]:
    """Parse ``--price ZN=112-165`` pairs, verbatim -- no price is defaulted."""

    prices: dict[str, str] = {}
    for entry in raw_prices:
        if "=" not in entry:
            raise ValueError(
                f"--price must be CONTRACT=PRICE (e.g. ZN=112-165), got {entry!r}"
            )
        contract_code, price = entry.split("=", 1)
        contract_code = contract_code.strip().upper()
        price = price.strip()
        if not contract_code or not price:
            raise ValueError(f"--price must be CONTRACT=PRICE, got {entry!r}")
        if contract_code not in SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES:
            raise ValueError(
                f"unsupported contract {contract_code!r} -- supported: "
                f"{', '.join(SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES)}"
            )
        prices[contract_code] = price
    if not prices:
        raise ValueError("at least one --price CONTRACT=PRICE is required")
    return prices


def _report_contract(contract_code: str, futures_price: str) -> int:
    print("=" * 78)
    print(f"{contract_code}  --  futures price as entered: {futures_price!r}")
    print("=" * 78)

    try:
        ctd = load_bloomberg_ctd_metadata(contract_code)
    except (TreasuryFuturesCTDBloombergError, TreasuryFuturesCTDError) as exc:
        print(f"  CTD LOAD FAILED: {exc}")
        return 1

    print("  CTD inputs (use these exact values on the benchmark side)")
    print(f"    contract symbol      {ctd.contract_symbol}")
    print(f"    CTD ISIN             {ctd.ctd_identifier}")
    print(f"    CTD CUSIP            {ctd.ctd_cusip or '-'}")
    print(f"    CTD description      {ctd.ctd_description or '-'}")
    print(f"    coupon (%)           {ctd.ctd_coupon_percent}")
    print(f"    maturity             {ctd.ctd_maturity_date.isoformat()}")
    print(f"    conversion factor    {ctd.conversion_factor}")
    print(f"    last delivery date   {ctd.last_delivery_date.isoformat()}  <- settlement date")
    print(f"    source               {ctd.source}")
    print(f"    acquired at          {ctd.as_of}")
    print()

    try:
        forward = implied_yield_from_futures_price(ctd, futures_price)
    except (TreasuryFuturesQuoteError, TreasuryFuturesYieldError) as exc:
        print(f"  CONVERSION FAILED: {exc}")
        return 1

    print("  Shiori answer")
    print(f"    futures price        {forward.quote.decimal_price!r}")
    print(f"    exchange quote       {forward.quote.exchange_quote}"
          f"{'' if forward.quote.on_tick else '   (entered price is off-tick)'}")
    print(f"    minimum tick         {forward.quote.minimum_tick}")
    print(f"    converted clean px   {forward.converted_clean_price:.8f}")
    print(f"    accrued interest     {forward.accrued_interest:.8f}")
    print(f"    dirty price          {forward.dirty_price:.8f}")
    print(f"    IMPLIED YIELD (%)    {forward.implied_yield_percent:.6f}   <- compare this")
    print()

    # Internal round trips. Self-consistency only -- they say nothing about
    # whether the number above agrees with CME.
    back = futures_price_from_target_yield(ctd, forward.implied_yield_percent)
    price_residual = abs(back.futures_price - forward.quote.decimal_price)
    tick = minimum_tick(contract_code)

    round_trip = implied_yield_from_futures_price(ctd, back.futures_price)
    yield_residual = abs(round_trip.implied_yield_percent - forward.implied_yield_percent)

    print("  Internal round trips (self-consistency, NOT benchmark agreement)")
    print(
        f"    price->yield->price  residual {price_residual:.10f} "
        f"vs one tick {tick}   {'OK' if price_residual < tick else 'OUT OF TOLERANCE'}"
    )
    print(
        f"    yield->price->yield  residual {yield_residual:.10f}% "
        f"vs 0.5 bp {ROUND_TRIP_YIELD_TOLERANCE_PERCENT}   "
        f"{'OK' if yield_residual < ROUND_TRIP_YIELD_TOLERANCE_PERCENT else 'OUT OF TOLERANCE'}"
    )
    print()
    print("  Benchmark comparison to run by hand")
    print(f"    On CME Treasury Analytics / Bloomberg, set contract {ctd.contract_symbol},")
    print(f"    CTD {ctd.ctd_identifier} (CF {ctd.conversion_factor}), settlement "
          f"{ctd.last_delivery_date.isoformat()},")
    # The yield above is computed from decimal_price, so the benchmark must be
    # set to decimal_price. Naming exchange_quote here would have compared two
    # yields from different inputs whenever the entered price is off-tick,
    # breaking this script's own identical-input rule (Codex review, PR #191).
    if forward.quote.on_tick:
        print(f"    futures price {forward.quote.decimal_price!r}, and read its Yield.")
    else:
        print(f"    futures price {forward.quote.decimal_price!r} (entered),"
              f"nearest exchange quote {forward.quote.exchange_quote!r}, and read its Yield.")
        print(f"    NOTE: {forward.quote.decimal_price!r} is off-tick for {contract_code} "
          f"(minimum tick {forward.quote.minimum_tick}).")
        print(f"    It is not an exchange-tradable level, and it is NOT "
              f"{forward.quote.exchange_quote}.")
        print("    Use the decimal above on both sides, or re-run on an on-tick price.")
    print(f"    Issue #190 acceptance: |benchmark - {forward.implied_yield_percent:.6f}| "
          "<= 0.5 bp (0.005%).")
    print()
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the production Bloomberg CTD lookup and implied-yield calculation once "
            "per contract, and print every input and output for a manual comparison "
            "against CME Treasury Analytics (Issue #190)."
        )
    )
    parser.add_argument(
        "--price",
        action="append",
        default=[],
        metavar="CONTRACT=PRICE",
        help=(
            "Futures price per contract, e.g. --price ZN=112-165. Repeatable. Never "
            "defaulted or fetched: both sides of the comparison must use the identical "
            "input."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        prices = _parse_price_arguments(args.price)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("Treasury futures CTD implied-yield acceptance (Issue #190)")
    print("Every number below comes from the production modules; this script asserts nothing.")
    print()

    exit_code = 0
    for contract_code in SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES:
        if contract_code not in prices:
            continue
        exit_code = _report_contract(contract_code, prices[contract_code]) or exit_code
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
