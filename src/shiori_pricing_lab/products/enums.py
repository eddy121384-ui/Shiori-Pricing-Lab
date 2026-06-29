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
