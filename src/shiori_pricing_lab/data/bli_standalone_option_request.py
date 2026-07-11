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
- ``market_data_snapshot`` -- one ``BLIMarketDataSnapshot``.

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

from shiori_pricing_lab.data._validation import _parse_as_of_calendar_date, _parse_iso_date
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePurpose,
    BLIMarketDataSnapshot,
    require_exact_isin_match,
)
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.eligibility import is_mvp_pricing_eligible

# Curve purposes required to price the standalone option leg (docs/26 §3,
# §5): presence only, never tenor-node selection or interpolation.
# Deliberately narrower than BLIMVPInputBundle's own
# _REQUIRED_MVP_CURVE_PURPOSES -- no DEPOSIT_CURVE, because this request
# prices only the bond option leg, never a deposit leg (Issue #95).
_REQUIRED_STANDALONE_CURVE_PURPOSES: frozenset[BLICurvePurpose] = frozenset(
    {
        BLICurvePurpose.BOND_REFERENCE_CURVE,
        BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    }
)


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

    def __post_init__(self) -> None:
        # valuation_date is explicit and parsed only for format validation --
        # never date.today()/datetime.now() anywhere in this module (docs/09 §3).
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
