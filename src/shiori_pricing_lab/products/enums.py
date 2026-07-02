"""Controlled vocabularies for vanilla rates product definitions.

These enums fix the small set of contractual choices a vanilla swap can take.
They are intentionally string-valued (``str`` subclasses) so that a product
definition round-trips cleanly through ``dataclasses.asdict`` and ``json.dumps``
without custom encoders.

Scope notes:

- This is a *small* vocabulary inspired by standard rates market conventions
  (the kind of terms ISDA / FpML / CDM definitions enumerate). It is not a copy
  of any of those standards and is deliberately incomplete: only the values the
  first IRS / OIS schemas need are present. New members can be added later
  without breaking existing definitions.
- Nothing here references market data, curves, calendars, or a valuation date.
  These are pure deal-term labels.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

_E = TypeVar("_E", bound=StrEnum)


def coerce_enum(value: object, enum_cls: type[_E], field_name: str) -> _E:
    """Coerce a raw value into ``enum_cls`` or raise a clear ``ValueError``.

    Dataclass type hints are not enforced at runtime, so a product can be built
    with an arbitrary string in an enum-typed field. This helper closes that gap:
    it accepts an existing enum member as-is, coerces a valid raw string into the
    matching member, and rejects blanks or unknown values with a message that
    lists the allowed options. Keeping coercion explicit (rather than trusting
    the annotation) means a definition always holds real enum members.
    """

    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ValueError(
            f"{field_name} must be one of {{{allowed}}}, got {value!r}"
        ) from exc


class PayReceive(StrEnum):
    """Direction of a leg's cashflows from the trade owner's perspective.

    ``PAY`` means the owner pays the leg away; ``RECEIVE`` means the owner
    receives it. A vanilla swap pairs one paying leg with one receiving leg.
    """

    PAY = "PAY"
    RECEIVE = "RECEIVE"

    def opposite(self) -> PayReceive:
        return PayReceive.RECEIVE if self is PayReceive.PAY else PayReceive.PAY


class Currency(StrEnum):
    """Settlement / notional currency.

    Restricted to a handful of common rates currencies for now. Using an enum
    keeps the currency explicit and rejects free-text typos at construction.
    """

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CHF = "CHF"
    AUD = "AUD"
    CAD = "CAD"
    TWD = "TWD"
    NZD = "NZD"
    KRW = "KRW"
    HKD = "HKD"
    SGD = "SGD"


class BuySell(StrEnum):
    """Direction of the near leg of an FX swap, from the trade owner's view.

    ``BUY`` / ``SELL`` refer to the *base* currency on the near date. The far
    leg is the opposite by construction, so only the near-leg direction is
    stored on the product.
    """

    BUY = "BUY"
    SELL = "SELL"


class Frequency(StrEnum):
    """Period frequency for payments or resets.

    ``DAILY`` exists mainly so an overnight (OIS) floating leg can express a
    daily reset; vanilla term legs use the longer periods.
    """

    DAILY = "DAILY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    ANNUAL = "ANNUAL"


class DayCount(StrEnum):
    """Day-count (accrual) convention.

    A representative set of the conventions seen on vanilla rates legs. The
    schema only records the choice; the accrual maths belongs to a future
    pricing engine, not here.
    """

    ACT_360 = "ACT_360"
    ACT_365_FIXED = "ACT_365_FIXED"
    THIRTY_360 = "THIRTY_360"
    ACT_ACT_ISDA = "ACT_ACT_ISDA"


class BusinessDayConvention(StrEnum):
    """How a payment date that lands on a non-business day is rolled.

    Recorded as a deal term only. Resolving it requires a holiday calendar,
    which is out of scope for this schema.
    """

    FOLLOWING = "FOLLOWING"
    MODIFIED_FOLLOWING = "MODIFIED_FOLLOWING"
    PRECEDING = "PRECEDING"
    MODIFIED_PRECEDING = "MODIFIED_PRECEDING"
    NONE = "NONE"


class FloatingIndex(StrEnum):
    """Floating rate index referenced by a floating leg.

    Includes both overnight risk-free rates (used by OIS floating legs) and a
    couple of term indices (used by IRS floating legs). The enum does not
    enforce which index is "overnight"; that distinction is documented and left
    to the desk's choice of product.
    """

    # Overnight risk-free rates
    USD_SOFR = "USD_SOFR"
    EUR_ESTR = "EUR_ESTR"
    GBP_SONIA = "GBP_SONIA"
    JPY_TONA = "JPY_TONA"
    CHF_SARON = "CHF_SARON"

    # Term indices
    USD_SOFR_TERM_3M = "USD_SOFR_TERM_3M"
    EUR_EURIBOR_3M = "EUR_EURIBOR_3M"
    EUR_EURIBOR_6M = "EUR_EURIBOR_6M"


class CompoundingMethod(StrEnum):
    """How daily overnight fixings are combined over a coupon period.

    ``NONE`` applies to a plain term floating leg (each period uses a single
    fixing). ``DAILY_COMPOUNDED`` and ``AVERAGED`` are the two standard ways an
    OIS floating leg turns a series of overnight fixings into one coupon.
    """

    NONE = "NONE"
    DAILY_COMPOUNDED = "DAILY_COMPOUNDED"
    AVERAGED = "AVERAGED"


# --- Bond-linked (BLI) controlled vocabulary --------------------------------
#
# The following enums are the controlled-vocabulary preflight for Issue #37
# (docs/14 F-16 / A-14, §4.1 / §6.1). They are added ahead of any BLI product
# schema so that a future ``BondOption`` / ``BondLinkedStructuredProduct``
# schema has a reviewed vocabulary to build on. Nothing here is referenced by
# a product schema yet, and no pricing, snapshot, or market-data code is
# added alongside them.


class PayoffBasis(StrEnum):
    """Whether a bond option's payoff is expressed in price or yield space."""

    PRICE = "PRICE"
    YIELD = "YIELD"


class OptionType(StrEnum):
    """Call or put, per SPEC/Annex A yield-and-price option direction rules."""

    CALL = "CALL"
    PUT = "PUT"


class ExerciseStyle(StrEnum):
    """Exercise style of a bond option."""

    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"


class SettlementType(StrEnum):
    """Cash or physical settlement of a bond option at exercise."""

    CASH = "CASH"
    PHYSICAL = "PHYSICAL"


class Position(StrEnum):
    """Buy or sell of a bond option, from the trade owner's perspective.

    Kept distinct from :class:`BuySell` (the FX swap near-leg direction):
    the two vocabularies describe different deal terms and are not
    guaranteed to stay aligned if one evolves independently of the other.
    """

    BUY = "BUY"
    SELL = "SELL"


class BondYieldConvention(StrEnum):
    """Bond Master yield-compounding convention (docs/14 §3.2, F-08).

    Recorded as a deal/reference-data term only; deriving the compounding
    frequency ``m`` from a member (or requiring an explicit ``m`` for
    ``OTHER``) is future pricing-engine work, not this enum's concern.
    """

    SEMI_ANNUAL_COMPOUND = "SEMI_ANNUAL_COMPOUND"
    ANNUAL_COMPOUND = "ANNUAL_COMPOUND"
    SIMPLE_YIELD = "SIMPLE_YIELD"
    JAPANESE_COMPOUND = "JAPANESE_COMPOUND"
    OTHER = "OTHER"


# --- DepositLeg / Treasury FTP controlled vocabulary (docs/18, BLI MVP Slice A) --
#
# These enums are the controlled-vocabulary foundation for a future
# ``DepositLeg`` schema (docs/18_deposit_leg_schema_preflight.md), added
# ahead of that schema so ``TREASURY_FTP_REFERENCE`` mode has a reviewed
# tenor/quote-side vocabulary to validate against instead of a placeholder
# check (docs/18 §12). No ``DepositLeg`` schema, Treasury FTP parser, or
# ingestion code is added alongside them.


class DepositRateMode(StrEnum):
    """How a future ``DepositLeg``'s rate/yield is sourced (docs/18 §4).

    ``FIXED_RATE``: the rate is a fixed contractual trade term and may live
    directly on ``DepositLeg``.

    ``TREASURY_FTP_REFERENCE``: ``DepositLeg`` stores only a stable selector
    (currency/tenor/quote_side); the resolved Treasury FTP rate and its
    business date belong to ``MarketDataSnapshot`` / an MVP input bundle /
    the audit trail, never the product schema (docs/18 §4.2).

    ``MANUAL_VERIFIED_RATE``: ``DepositLeg`` stores only a reference to a
    manually supplied pricing input; the rate value and its provenance
    (source, as-of, entered-by) live outside the product schema
    (docs/18 §4.3).
    """

    FIXED_RATE = "FIXED_RATE"
    TREASURY_FTP_REFERENCE = "TREASURY_FTP_REFERENCE"
    MANUAL_VERIFIED_RATE = "MANUAL_VERIFIED_RATE"


class TreasuryFTPQuoteSide(StrEnum):
    """Quote side of a Treasury FTP rate matrix entry (docs/18 §2.4, §5).

    ``MID`` is the default policy where a currency has no bid/mid/offer
    breakdown, the single available rate is treated as MID-equivalent — a
    documented normalization, not an inference of ``BID``/``OFFER`` the
    source sheet does not provide. This enum defines vocabulary only; no
    quote-side selection policy is implemented here.
    """

    BID = "BID"
    MID = "MID"
    OFFER = "OFFER"


class TreasuryFTPTenor(StrEnum):
    """Tenor labels observed on the Treasury FTP rate sheet (docs/18 §2.3).

    Deliberately separate from :class:`Frequency`: ``Frequency`` is a
    payment/reset period vocabulary for vanilla rates legs
    (``DAILY``/``MONTHLY``/``QUARTERLY``/...), not a tenor label set, and
    does not cover ``O/N``, short weekly tenors, or ``9M``/``2Y``/``3Y``.
    Reusing ``Frequency`` here would silently misrepresent a Treasury FTP
    tenor as a payment frequency (docs/18 §2.3). Canonical string values
    match the sheet's own labels exactly (e.g. ``"O/N"``, ``"1W"``); member
    *names* are valid Python identifiers where the label itself is not.
    """

    OVERNIGHT = "O/N"
    ONE_WEEK = "1W"
    TWO_WEEK = "2W"
    THREE_WEEK = "3W"
    ONE_MONTH = "1M"
    TWO_MONTH = "2M"
    THREE_MONTH = "3M"
    SIX_MONTH = "6M"
    NINE_MONTH = "9M"
    ONE_YEAR = "1Y"
    TWO_YEAR = "2Y"
    THREE_YEAR = "3Y"
    DEMAND_SAVINGS = "DEMAND_SAVINGS"
