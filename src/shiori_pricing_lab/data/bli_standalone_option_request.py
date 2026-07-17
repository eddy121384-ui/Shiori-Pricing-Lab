"""``BLIStandaloneBondOptionRequest``: standalone bond-option pricing input (Issue #95).

Scope, per Issue #95's approved-with-corrections decision: a frozen,
deterministic, immutable pricing-request boundary for exactly one
standalone European price-based cash-settled bond option, binding only the
inputs the existing reviewed pricing kernel actually consumes -- **without**
requiring an economically irrelevant ``DepositLeg`` /
``BondLinkedStructuredProduct`` wrapper or a Deposit Curve.

This is a *request* object, not a pricing object: like ``BLIMVPInputBundle``
(``data/bli_mvp_input_bundle.py``, docs/24) it binds already-independently-
valid objects **by reference**, performs no pricing math, no curve
interpolation, no yield/price conversion, and calls no pricing function.
The actual pricing lives in ``pricing/bli_pricing_engine.py``
(``price_bli_mvp_standalone_option``), which reaches exactly the same
reviewed guard + forward-clean-price + curve + Black-76 + result
composition as the bundle path.

**Field list is deliberately minimal (Issue #95 correction #2):**

- ``bond_option`` -- a bare ``BondOption`` (its own ``product_id`` /
  ``product_type`` are reused verbatim for the pricing result; no new
  ``"STANDALONE_BOND_OPTION"`` discriminator is introduced).
- ``resolved_bond_reference_data`` -- the resolved Bond Master record.
- ``valuation_date`` -- explicit ISO date; never ``date.today()``.
- ``market_data_snapshot`` -- one ``BLIMarketDataSnapshot``. For the
  OVME-aligned standalone path (Issue #94) its
  ``forward_clean_price_input`` must be present, and that input's
  ``quote_side`` must equal the spot ``bond_quote.quote_side``: the
  forward clean price is supplied explicitly with provenance and is never
  reconstructed from a spot price and a Bond Reference Curve. Consequently
  only ``OPTION_DISCOUNT_CURVE`` is required here (not
  ``BOND_REFERENCE_CURVE``), and a yield-only spot ``bond_quote`` is
  allowed since the forward no longer depends on the spot clean price.

**Timing/date contract (Issue #94 human methodology approval, comment
5001749998): the explicit inputs the approved Phase 1 Bloomberg
methodology requires for the standalone European bond-option path.**
This slice adds deterministic parsing/coherence validation only -- it
does **not** compute ACT/ACT option time, dirty forward/strike, or any
reporting-date discount adjustment, and it is not wired into the
pricing engine.

- ``pricing_timestamp`` / ``expiry_timestamp`` -- full ISO-8601
  datetimes, each requiring an explicit UTC offset (``Z``, ``+00:00``,
  ``+08:00``, ...); naive datetimes and bare dates are rejected.
  ``pricing_timestamp.date()`` must equal ``valuation_date``;
  ``expiry_timestamp.date()`` must equal ``bond_option.expiry_date``;
  ``expiry_timestamp`` must be strictly after ``pricing_timestamp``.
- ``reporting_date`` -- strict ``YYYY-MM-DD``; must be on or after
  ``valuation_date``.
- ``forward_settlement_date`` -- strict ``YYYY-MM-DD``; must be on or
  after ``bond_option.expiry_date``. The bond forward settlement date
  used by the repo/forward replication chain.
- ``option_settlement_date`` -- strict ``YYYY-MM-DD``; must be on or
  after ``bond_option.expiry_date``. The cash option settlement date
  used for option discounting. Deliberately kept distinct from
  ``forward_settlement_date`` -- this contract never requires them to be
  equal, and never derives either one from
  ``bond_option.settlement_lag_days``, Delivery Delay, weekends,
  holidays, or any business-day/calendar convention. Those derivations
  are out of scope for this slice.

Every one of these five fields is preserved exactly as supplied on the
frozen instance -- parsing in ``__post_init__`` is for validation only
and never normalizes or rewrites the stored string.

**No ``resolution_status`` / ``eligibility_reasons`` / ``request_id``**
(Issue #95 correction #2): unlike ``BLIMVPInputBundle``, this request does
not carry the resolver's own status fields. Instead it **re-runs**
``is_mvp_pricing_eligible`` directly on the actual reference-data object --
the single existing source of truth for MVP eligibility
(``reference_data.eligibility``). Structured resolver/builder outcomes are
#96's concern, not this pricing request's.

**Construction rejects cross-object incoherence only (Issue #95 correction
#4).** ``__post_init__`` enforces the same coherence gates as
``BLIMVPInputBundle`` *except* the deposit-leg-specific ones and the
resolution-status gates: invalid valuation date, ineligible reference data,
ISIN mismatch, currency mismatch, valuation-date mismatch, a future as-of
date, or a missing option-leg curve purpose. It deliberately does **not**
narrow a valid general ``BondOption`` down to the supported *pricing* shape:
American exercise, a yield payoff, physical settlement, a ``YIELD_VOL``
volatility basis, and a yield-only bond quote all remain **constructible**
here where their own schemas allow them, and are then rejected by the
standalone pricing guard as explicit ``FAILED`` results -- exactly the same
supported/unsupported boundary the bundle path enforces.

**Reused helpers (no duplicated validation):** ``_parse_iso_date`` and the
mechanically-relocated ``_parse_as_of_calendar_date`` (both
``data/_validation.py``), ``is_mvp_pricing_eligible``
(``reference_data.eligibility``), and ``require_exact_isin_match``
(``data/bli_snapshot.py``) -- all unmodified.

**Hard non-goals (Issue #95 scope cap):** no deposit-leg valuation, no full
structured-product valuation, no curve construction/extrapolation, no
yield-vol conversion, no Bloomberg/provider code, no UI, no persistence, no
new pricing methodology. ``BondOption``, ``BondReferenceData``,
``BLIMarketDataSnapshot``, ``is_mvp_pricing_eligible``, and
``require_exact_isin_match`` are all unmodified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from shiori_pricing_lab.data._validation import _parse_as_of_calendar_date, _parse_iso_date
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePurpose,
    BLIMarketDataSnapshot,
    require_exact_isin_match,
)
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.eligibility import is_mvp_pricing_eligible

# Curve purposes required to price the standalone option leg. Presence
# only, never tenor-node selection or interpolation. Narrowed for the
# OVME-aligned standalone path (Issue #94): the forward clean price is now
# supplied explicitly as market data (BLIForwardCleanPriceInput), so this
# path no longer constructs the forward from a spot price and a Bond
# Reference Curve and no longer requires BOND_REFERENCE_CURVE. Only the
# Option Discount Curve (used for the option-payoff discount factor to the
# option settlement date and the reporting-date factor) is required.
_REQUIRED_STANDALONE_CURVE_PURPOSES: frozenset[BLICurvePurpose] = frozenset(
    {
        BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    }
)


def _parse_offset_aware_datetime(value: object, field_name: str) -> datetime:
    """Parse a full ISO-8601 datetime that must carry an explicit UTC offset.

    Rejects a bare date, a naive datetime, and any malformed string. ``Z``,
    ``+00:00``, ``+08:00``, etc. are all accepted -- any explicit offset is
    fine, not only UTC. This is deliberately a different, stricter helper
    from ``_parse_as_of_calendar_date`` (``data/_validation.py``), which
    accepts a bare date or naive datetime for a different, more permissive
    no-look-ahead check -- the two must not be conflated or reused for each
    other's purpose. Never reads the system clock or timezone.

    **Codex P1 fix:** ``datetime.fromisoformat`` accepts an arbitrary single
    Unicode character (any ASCII separator, a space, or even an emoji) in
    the date/time separator position -- so ``"2026-07-01x16:00:00Z"`` or
    ``"2026-09-29\U0001F40D16:00:00+08:00"`` would otherwise parse
    successfully. The canonical shape is checked explicitly -- position 10
    must be exactly the uppercase ``"T"`` -- *before* delegating the actual
    date/time/offset parsing to ``datetime.fromisoformat``; a lowercase
    ``"t"``, a space, or any other separator is rejected outright.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank ISO-8601 datetime string")
    if len(value) < 11 or value[10] != "T":
        raise ValueError(
            f"{field_name} must use an uppercase 'T' separator immediately after the "
            f"YYYY-MM-DD date (e.g. '2026-07-01T16:00:00Z'), got {value!r}"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be a full ISO-8601 datetime with an explicit UTC offset "
            f"(e.g. 'Z', '+00:00', '+08:00'), got {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"{field_name} must include an explicit UTC offset (e.g. 'Z', '+00:00', "
            f"'+08:00') -- a bare date or naive datetime is not accepted, got {value!r}"
        )
    return parsed


@dataclass(frozen=True)
class BLIStandaloneBondOptionRequest:
    """Standalone bond-option pricing request: option + reference data + market data.

    Every object-typed field is a **reference** to an already-independently-
    valid object -- no field here duplicates a value already owned by
    ``bond_option``, ``resolved_bond_reference_data``, or
    ``market_data_snapshot``. See the module docstring for the exact
    coherence gates enforced in ``__post_init__`` and for why the
    supported-pricing-shape narrowing is deliberately left to the pricing
    guard, not this constructor.
    """

    bond_option: BondOption
    resolved_bond_reference_data: BondReferenceData
    valuation_date: str
    market_data_snapshot: BLIMarketDataSnapshot
    pricing_timestamp: str
    expiry_timestamp: str
    reporting_date: str
    forward_settlement_date: str
    option_settlement_date: str

    def __post_init__(self) -> None:
        # valuation_date is explicit and parsed only for format validation --
        # never date.today()/datetime.now() anywhere in this module.
        valuation_date = _parse_iso_date(self.valuation_date, "valuation_date")

        if not isinstance(self.bond_option, BondOption):
            raise TypeError("bond_option must be a BondOption")
        if not isinstance(self.resolved_bond_reference_data, BondReferenceData):
            raise TypeError("resolved_bond_reference_data must be a BondReferenceData")
        if not isinstance(self.market_data_snapshot, BLIMarketDataSnapshot):
            raise TypeError("market_data_snapshot must be a BLIMarketDataSnapshot")

        # Reference data must be MVP-pricing eligible -- re-run the single
        # existing source of truth (Issue #95 correction #2), never trust a
        # separately-supplied resolver status (this request carries none).
        actual_eligibility = is_mvp_pricing_eligible(self.resolved_bond_reference_data)
        if not actual_eligibility.eligible:
            raise ValueError(
                "resolved_bond_reference_data is not MVP-pricing eligible "
                f"(is_mvp_pricing_eligible reasons: {'; '.join(actual_eligibility.reasons)})"
            )

        # ISIN gates -- plain string equality only, no fuzzy/prefix/
        # case-insensitive matching anywhere (docs/24 §6, docs/21 §4).
        if self.bond_option.underlying_isin != self.resolved_bond_reference_data.isin:
            raise ValueError(
                "bond_option.underlying_isin "
                f"({self.bond_option.underlying_isin!r}) does not exactly match "
                "resolved_bond_reference_data.isin "
                f"({self.resolved_bond_reference_data.isin!r})"
            )
        require_exact_isin_match(
            self.market_data_snapshot, self.resolved_bond_reference_data.isin
        )

        # Currency coherence gates (mirrors BLIMVPInputBundle, docs/24):
        # ISIN identity alone does not prove one coherent currency. No FX
        # conversion is implemented or implied -- a mismatch is always a
        # hard rejection.
        product_currency = self.bond_option.currency
        if self.resolved_bond_reference_data.currency is not product_currency:
            raise ValueError(
                "bond_option currency "
                f"({product_currency.value}) does not match "
                "resolved_bond_reference_data.currency "
                f"({self.resolved_bond_reference_data.currency.value})"
            )
        if self.market_data_snapshot.bond_quote.currency is not product_currency:
            raise ValueError(
                "market_data_snapshot.bond_quote.currency "
                f"({self.market_data_snapshot.bond_quote.currency.value}) does not match "
                f"bond_option currency ({product_currency.value})"
            )

        # Valuation-date coherence -- equality alone (necessary but, per the
        # as-of check below, not sufficient).
        if self.market_data_snapshot.valuation_date != self.valuation_date:
            raise ValueError(
                "market_data_snapshot.valuation_date "
                f"({self.market_data_snapshot.valuation_date!r}) must equal request "
                f"valuation_date ({self.valuation_date!r})"
            )

        # Market-data as-of / no-look-ahead gate (docs/24 §6): same-date
        # as-of is allowed; an as-of date strictly after valuation_date is
        # rejected. Uses the mechanically-relocated shared parser -- policy
        # unchanged from the bundle path.
        as_of_date = _parse_as_of_calendar_date(
            self.market_data_snapshot.as_of_timestamp, "market_data_snapshot.as_of_timestamp"
        )
        if as_of_date > valuation_date:
            raise ValueError(
                "market_data_snapshot.as_of_timestamp "
                f"({self.market_data_snapshot.as_of_timestamp!r}, date {as_of_date.isoformat()}) "
                f"must not be after valuation_date ({self.valuation_date!r}) -- "
                "no-look-ahead: market data may not be dated after the valuation it "
                "is used for"
            )

        self._require_standalone_curve_purposes()
        self._require_forward_clean_price_input()
        self._validate_timing_contract(valuation_date)

    def _require_forward_clean_price_input(self) -> None:
        """Require an explicit forward clean price input, coherent with the spot side.

        OVME-aligned standalone path (Issue #94): the forward clean price is
        supplied explicitly as market data with provenance -- this request
        cannot price without it, and never reconstructs it from a spot price
        and a Bond Reference Curve. Its ``quote_side`` must equal the spot
        ``bond_quote.quote_side`` (one coherent observation side; no silent
        side substitution or blending). The numeric forward is read only from
        the snapshot's ``forward_clean_price_input`` -- it is deliberately not
        duplicated as a separate request field.
        """

        forward_input = self.market_data_snapshot.forward_clean_price_input
        if forward_input is None:
            raise ValueError(
                "market_data_snapshot.forward_clean_price_input must be present -- the "
                "standalone OVME-aligned path prices from an explicit forward clean price "
                "and never reconstructs it from a spot price and a Bond Reference Curve"
            )
        spot_side = self.market_data_snapshot.bond_quote.quote_side
        if forward_input.quote_side is not spot_side:
            raise ValueError(
                "market_data_snapshot.forward_clean_price_input.quote_side "
                f"({forward_input.quote_side.value}) must equal bond_quote.quote_side "
                f"({spot_side.value}) -- one coherent observation side, no substitution"
            )

    def _validate_timing_contract(self, valuation_date: date) -> None:
        """Validate the Phase 1 Bloomberg methodology timing/date contract.

        Issue #94 human methodology approval (comment 5001749998): adds
        deterministic parsing/coherence validation only. Does **not**
        compute ACT/ACT option time, dirty forward/strike, or any
        reporting-date discount adjustment, and does not derive or
        require agreement with ``bond_option.settlement_lag_days``,
        Delivery Delay, weekends, holidays, or any business-day/calendar
        convention -- those derivations are out of scope for this slice.
        Every supplied string is parsed for validation only and preserved
        exactly on the frozen instance.
        """

        pricing_timestamp = _parse_offset_aware_datetime(
            self.pricing_timestamp, "pricing_timestamp"
        )
        expiry_timestamp = _parse_offset_aware_datetime(
            self.expiry_timestamp, "expiry_timestamp"
        )
        expiry_date = _parse_iso_date(self.bond_option.expiry_date, "bond_option.expiry_date")

        if pricing_timestamp.date() != valuation_date:
            raise ValueError(
                f"pricing_timestamp ({self.pricing_timestamp!r}, date "
                f"{pricing_timestamp.date().isoformat()}) must fall on valuation_date "
                f"({self.valuation_date!r})"
            )
        if expiry_timestamp.date() != expiry_date:
            raise ValueError(
                f"expiry_timestamp ({self.expiry_timestamp!r}, date "
                f"{expiry_timestamp.date().isoformat()}) must fall on bond_option.expiry_date "
                f"({self.bond_option.expiry_date!r})"
            )
        if expiry_timestamp <= pricing_timestamp:
            raise ValueError(
                f"expiry_timestamp ({self.expiry_timestamp!r}) must be strictly after "
                f"pricing_timestamp ({self.pricing_timestamp!r})"
            )

        reporting_date = _parse_iso_date(self.reporting_date, "reporting_date")
        forward_settlement_date = _parse_iso_date(
            self.forward_settlement_date, "forward_settlement_date"
        )
        option_settlement_date = _parse_iso_date(
            self.option_settlement_date, "option_settlement_date"
        )

        if reporting_date < valuation_date:
            raise ValueError(
                f"reporting_date ({self.reporting_date!r}) must be on or after "
                f"valuation_date ({self.valuation_date!r})"
            )
        if forward_settlement_date < expiry_date:
            raise ValueError(
                f"forward_settlement_date ({self.forward_settlement_date!r}) must be on or "
                f"after bond_option.expiry_date ({self.bond_option.expiry_date!r})"
            )
        if option_settlement_date < expiry_date:
            raise ValueError(
                f"option_settlement_date ({self.option_settlement_date!r}) must be on or "
                f"after bond_option.expiry_date ({self.bond_option.expiry_date!r})"
            )

    def _require_standalone_curve_purposes(self) -> None:
        """Require presence of both option-leg curve purposes in the option's currency.

        Presence only -- never tenor-node selection, never interpolation.
        A required purpose present only under a *different* currency does
        not satisfy this gate (mirrors BLIMVPInputBundle's currency-coherent
        curve check). No DEPOSIT_CURVE is required: this request prices only
        the bond option leg (Issue #95).
        """

        product_currency = self.bond_option.currency
        present_purposes_in_product_currency = {
            point.curve_purpose
            for point in self.market_data_snapshot.curve_points
            if point.currency is product_currency
        }
        missing_purposes = (
            _REQUIRED_STANDALONE_CURVE_PURPOSES - present_purposes_in_product_currency
        )
        if missing_purposes:
            missing_names = sorted(purpose.value for purpose in missing_purposes)
            raise ValueError(
                "market_data_snapshot.curve_points is missing required curve "
                f"purpose(s) in currency {product_currency.value}: {missing_names}"
            )
