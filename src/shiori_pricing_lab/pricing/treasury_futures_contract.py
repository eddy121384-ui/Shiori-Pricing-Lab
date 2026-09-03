"""U.S. Treasury futures contract metadata, quote parsing and quote formatting (Issue #190).

Scope: the *quote notation* half of the desk's futures <-> CTD implied-yield
converter. Nothing here prices anything, reads a clock, or touches market
data -- it turns a trader-entered futures quote into an exact decimal price
per 100 par, and a decimal price back into the exchange's own display for
that specific contract.

**Why this is contract-driven and not one generic 32nds parser.** The four
MVP contracts do not share a minimum price increment, so a single parser
cannot be correct for all of them (Issue #190's explicit rejection of PR
#9's generic "third digit is tenths of a 32nd" reading). Each contract's
outright minimum tick, from the CME contract specifications quoted in Issue
#190 and cross-checked against each contract's published tick value:

===== ==================================== ================= ===========
Code  Contract                              Minimum tick      Tick value
===== ==================================== ================= ===========
ZT    2-Year U.S. Treasury Note futures     1/8 of 1/32       $7.8125
ZF    5-Year U.S. Treasury Note futures     1/4 of 1/32       $7.8125
ZN    10-Year U.S. Treasury Note futures    1/2 of 1/32       $15.625
ZB    U.S. Treasury Bond futures            1/32              $31.25
===== ==================================== ================= ===========

(ZT is on a $200,000 contract, the other three on $100,000, which is why ZT
and ZF share a tick value at different tick sizes.)

**The sub-32nd digit.** CBOT Treasury futures are displayed as
``<handle>-<32nds><sub>``, where ``<sub>`` is a single digit encoding the
fraction of a 32nd -- and that digit is the fraction's own leading decimal
digit, not a count of eighths::

    sub digit = int(ticks_into_the_32nd * 10 / ticks_per_32nd)

That one rule reproduces every contract's published alphabet exactly:

- ZB (whole 32nds): ``0`` only -- ``118-16``.
- ZN (halves): ``0``, ``5`` -- ``110-165`` is 110 + 16.5/32.
- ZF (quarters): ``0``, ``2``, ``5``, ``7`` -- ``110-167`` is 110 + 16.75/32.
- ZT (eighths): ``0``, ``1``, ``2``, ``3``, ``5``, ``6``, ``7``, ``8`` --
  ``110-166`` is 110 + 16.625/32. ``4`` and ``9`` never appear.

``ZF``'s ``7`` for three quarters is the tell that decides this: a
count-of-eighths reading could never produce ``7`` on a quarter-tick
contract. The alphabets are derived from the rule above in
:func:`_sub_32nd_digits` and pinned as explicit literals in this module's
tests, so a future edit cannot silently move them.

**Deliberately not the same notation as this repository's cash Treasury
formatter.** ``pricing/bli_treasury_price_format.format_price_as_treasury_fraction``
renders *cash* Treasury quotes, where the third character counts eighths
directly (``99-032`` is 99 + 3.25/32 there, and ``+`` is four eighths).
Futures use the leading-decimal-digit rule above, so ``99-032`` means a
different price on a futures contract than it does on a cash bond. The two
notations are never interchanged, and neither module calls the other.

**Off-tick input.** A *fractional* quote is an exchange quote by
construction, so one that is not on the selected contract's tick (``118-16+``
on ZB, which has no half-32nd) is rejected as a typo. A *decimal* price is
accepted exactly as entered, on-tick or not: the desk's whole point is
asking "if 10Y futures were at X, where is the yield?", and X is often a
hypothetical level rather than a tradeable one. Every parse result carries
both the exact decimal the trader entered and the nearest valid exchange
price/quote, so a caller can show what would actually trade without ever
silently rounding the number the calculation used.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

TREASURY_FUTURES_32NDS_PER_POINT = 32

# Trader shorthand for half a 32nd, accepted on input for any contract whose
# tick actually divides 1/64. Never emitted: `format_futures_quote` always
# writes the exchange's own sub-digit form.
TREASURY_FUTURES_HALF_32ND_SUFFIX = "+"

_QUOTE_SEPARATORS = "-'"
_FRACTIONAL_QUOTE_PATTERN = re.compile(
    rf"^(?P<handle>\d+)[{_QUOTE_SEPARATORS}](?P<thirty_seconds>\d{{2}})"
    rf"(?P<sub>\d|\{TREASURY_FUTURES_HALF_32ND_SUFFIX})?$"
)


class TreasuryFuturesContractError(ValueError):
    """An unknown futures contract code was requested."""


class TreasuryFuturesQuoteError(ValueError):
    """A futures quote could not be read as a price for the selected contract.

    Covers a blank/non-string input, an unparseable string, a 32nds component
    outside ``00``-``31``, a sub-32nd digit that is not in the selected
    contract's alphabet, a ``+`` on a contract with no half-32nd tick, and a
    non-positive or non-finite price.
    """


@dataclass(frozen=True)
class TreasuryFuturesContract:
    """One CBOT Treasury futures contract's quote convention.

    ``ticks_per_32nd`` is the whole story: it fixes the minimum tick
    (``1 / (32 * ticks_per_32nd)`` of a point), the legal sub-32nd digits,
    and whether ``+`` is a legal shorthand.

    There is deliberately no market-data vendor ticker here. The CTD probe
    tool in ``tools/`` keeps its own root-to-contract table instead: this
    package is guarded against vendor plumbing (``test_irs_reference_engine``
    asserts every ``pricing/*.py`` is free of it), and a quote convention has
    no business knowing a vendor symbology.
    """

    code: str
    name: str
    ticks_per_32nd: int

    @property
    def minimum_tick(self) -> float:
        """Minimum outright price increment, in points per 100 par."""

        return 1.0 / (TREASURY_FUTURES_32NDS_PER_POINT * self.ticks_per_32nd)

    @property
    def ticks_per_point(self) -> int:
        return TREASURY_FUTURES_32NDS_PER_POINT * self.ticks_per_32nd

    @property
    def minimum_tick_label(self) -> str:
        """The tick as a trader reads it, e.g. ``"1/64 point"``.

        Rendered here rather than in a consumer so no display layer has to do
        arithmetic on ``minimum_tick`` to say what the tick is.
        """

        return f"1/{self.ticks_per_point} point"

    @property
    def sub_32nd_digits(self) -> dict[str, int]:
        """Legal sub-32nd display digit -> number of ticks into the 32nd."""

        return _sub_32nd_digits(self.ticks_per_32nd)

    @property
    def accepts_half_32nd_suffix(self) -> bool:
        """Whether ``"110-16+"`` is a price this contract can actually trade."""

        return self.ticks_per_32nd % 2 == 0


def _sub_32nd_digits(ticks_per_32nd: int) -> dict[str, int]:
    """Return the contract's display-digit alphabet, derived, never listed by hand.

    See the module docstring: the digit is the leading decimal digit of the
    fraction of a 32nd. The uniqueness assertion is what makes the derivation
    safe to trust for a tick count this module has not shipped before.
    """

    digits = {str(ticks * 10 // ticks_per_32nd): ticks for ticks in range(ticks_per_32nd)}
    if len(digits) != ticks_per_32nd:
        raise TreasuryFuturesContractError(
            f"{ticks_per_32nd} ticks per 32nd cannot be displayed as one digit each"
        )
    return digits


# The four MVP contracts of Issue #190. Adding 3-Year / Ultra 10-Year / Ultra
# Bond later is one row each -- but each row asserts that contract's real
# exchange tick, so none is added here without its own confirmation.
TREASURY_FUTURES_CONTRACTS: dict[str, TreasuryFuturesContract] = {
    contract.code: contract
    for contract in (
        TreasuryFuturesContract(
            code="ZT",
            name="2-Year U.S. Treasury Note futures",
            ticks_per_32nd=8,
        ),
        TreasuryFuturesContract(
            code="ZF",
            name="5-Year U.S. Treasury Note futures",
            ticks_per_32nd=4,
        ),
        TreasuryFuturesContract(
            code="ZN",
            name="10-Year U.S. Treasury Note futures",
            ticks_per_32nd=2,
        ),
        TreasuryFuturesContract(
            code="ZB",
            name="U.S. Treasury Bond futures",
            ticks_per_32nd=1,
        ),
    )
}

SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES = tuple(TREASURY_FUTURES_CONTRACTS)


@dataclass(frozen=True)
class TreasuryFuturesQuote:
    """One parsed futures quote.

    ``decimal_price`` is exactly what the trader asked about and is the only
    value any calculation uses. ``exchange_price``/``exchange_quote`` are the
    nearest price this contract could actually trade at, for display; they
    are equal to ``decimal_price`` whenever ``on_tick`` is true.
    """

    contract_code: str
    decimal_price: float
    exchange_price: float
    exchange_quote: str
    on_tick: bool
    minimum_tick: float


def get_contract(contract_code: str) -> TreasuryFuturesContract:
    """Return the contract metadata for ``contract_code`` (e.g. ``"ZN"``)."""

    if not isinstance(contract_code, str) or not contract_code.strip():
        raise TreasuryFuturesContractError("contract code must be a non-empty string")
    contract = TREASURY_FUTURES_CONTRACTS.get(contract_code.strip().upper())
    if contract is None:
        raise TreasuryFuturesContractError(
            f"unsupported Treasury futures contract {contract_code!r} -- supported: "
            f"{', '.join(SUPPORTED_TREASURY_FUTURES_CONTRACT_CODES)}"
        )
    return contract


def minimum_tick(contract_code: str) -> float:
    """Minimum outright price increment for ``contract_code``, in points per 100."""

    return get_contract(contract_code).minimum_tick


def round_to_tick(contract_code: str, price: float) -> float:
    """Round ``price`` to the nearest valid exchange price for ``contract_code``.

    Ties round up (away from zero), never to even: a half-tick residual is a
    display decision, and "always the same direction" is the one a trader can
    reproduce by hand.
    """

    contract = get_contract(contract_code)
    price = _require_positive_finite_price(price)
    try:
        return math.floor(price * contract.ticks_per_point + 0.5) / contract.ticks_per_point
    except OverflowError as exc:
        raise TreasuryFuturesQuoteError(
            f"futures price {price} causes numerical overflow in tick rounding"
        ) from exc


def parse_futures_quote(contract_code: str, raw: str | int | float) -> TreasuryFuturesQuote:
    """Read a trader-entered futures quote for ``contract_code``.

    Accepts a decimal price (``110.515625``, exact, on-tick or not) or a
    CBOT fractional quote for this contract (``"110-16"``, ``"110-165"``,
    ``"110'165"``, ``"110-16+"`` where a half 32nd is a real tick). An
    off-tick fractional quote is rejected -- see the module docstring.
    """

    contract = get_contract(contract_code)

    if isinstance(raw, bool):  # bool is an int subclass; never a price
        raise TreasuryFuturesQuoteError("futures price must be a number or a quote string")
    if isinstance(raw, (int, float)):
        return _build_quote(contract, _require_positive_finite_price(float(raw)))
    if not isinstance(raw, str):
        raise TreasuryFuturesQuoteError("futures price must be a number or a quote string")

    text = raw.strip()
    if not text:
        raise TreasuryFuturesQuoteError("futures price must not be blank")

    if not any(separator in text for separator in _QUOTE_SEPARATORS):
        try:
            decimal_price = float(text)
        except ValueError as exc:
            raise TreasuryFuturesQuoteError(
                f"{text!r} is neither a decimal price nor a {contract.code} quote "
                "(expected e.g. '110-16', '110-165' or 110.515625)"
            ) from exc
        return _build_quote(contract, _require_positive_finite_price(decimal_price))

    return _build_quote(contract, _parse_fractional_quote(contract, text))


def _parse_fractional_quote(contract: TreasuryFuturesContract, text: str) -> float:
    match = _FRACTIONAL_QUOTE_PATTERN.match(text)
    if match is None:
        raise TreasuryFuturesQuoteError(
            f"{text!r} is not a valid {contract.code} quote -- expected a handle, a "
            "separator, exactly two 32nds digits, and an optional sub-32nd digit "
            "(e.g. '110-16', '110-165')"
        )

    thirty_seconds = int(match.group("thirty_seconds"))
    if thirty_seconds >= TREASURY_FUTURES_32NDS_PER_POINT:
        raise TreasuryFuturesQuoteError(
            f"{text!r} is not a valid {contract.code} quote -- the 32nds component must "
            f"be 00-31, got {match.group('thirty_seconds')!r}"
        )

    sub = match.group("sub")
    if sub is None:
        sub_ticks = 0
    elif sub == TREASURY_FUTURES_HALF_32ND_SUFFIX:
        if not contract.accepts_half_32nd_suffix:
            raise TreasuryFuturesQuoteError(
                f"{text!r} is not a valid {contract.code} quote -- {contract.code} trades "
                f"in whole 32nds, so half a 32nd ('+') is not a valid tick"
            )
        sub_ticks = contract.ticks_per_32nd // 2
    else:
        digits = contract.sub_32nd_digits
        if sub not in digits:
            raise TreasuryFuturesQuoteError(
                f"{text!r} is not a valid {contract.code} quote -- {contract.code}'s "
                f"sub-32nd digit must be one of {', '.join(sorted(digits))}, got {sub!r}"
            )
        sub_ticks = digits[sub]

    ticks = (
        int(match.group("handle")) * contract.ticks_per_point
        + thirty_seconds * contract.ticks_per_32nd
        + sub_ticks
    )
    return _require_positive_finite_price(ticks / contract.ticks_per_point)


def format_futures_quote(contract_code: str, price: float) -> str:
    """Render ``price`` as ``contract_code``'s exchange quote, rounded to its tick."""

    contract = get_contract(contract_code)
    price = _require_positive_finite_price(price)

    total_ticks = math.floor(price * contract.ticks_per_point + 0.5)
    handle, remainder = divmod(total_ticks, contract.ticks_per_point)
    thirty_seconds, sub_ticks = divmod(remainder, contract.ticks_per_32nd)
    if sub_ticks == 0:
        return f"{handle}-{thirty_seconds:02d}"
    digit = next(
        digit for digit, ticks in contract.sub_32nd_digits.items() if ticks == sub_ticks
    )
    return f"{handle}-{thirty_seconds:02d}{digit}"


def _build_quote(contract: TreasuryFuturesContract, decimal_price: float) -> TreasuryFuturesQuote:
    exchange_price = round_to_tick(contract.code, decimal_price)
    # Use a very small tolerance for floating-point roundoff when checking if
    # the price is on a valid tick. The contract's minimum tick is the
    # economic threshold; here we only account for IEEE 754 roundoff error.
    tick = contract.minimum_tick
    on_tick = abs(exchange_price - decimal_price) <= tick * 1e-8
    return TreasuryFuturesQuote(
        contract_code=contract.code,
        decimal_price=decimal_price,
        exchange_price=exchange_price,
        exchange_quote=format_futures_quote(contract.code, decimal_price),
        # An exact tick multiple survives the round-trip bit for bit; anything
        # the trader typed off-tick does not. A tiny tolerance is applied
        # here to account for floating-point roundoff, so a mathematically
        # on-tick price with normal float noise is correctly reported as
        # on_tick=True. The contract tick itself remains the economic
        # threshold for what is tradeable.
        on_tick=on_tick,
        minimum_tick=contract.minimum_tick,
    )


def _require_positive_finite_price(price: float) -> float:
    price = float(price)
    if not math.isfinite(price):
        raise TreasuryFuturesQuoteError("futures price must be a finite number")
    if price <= 0:
        raise TreasuryFuturesQuoteError(f"futures price must be positive, got {price}")
    return price
