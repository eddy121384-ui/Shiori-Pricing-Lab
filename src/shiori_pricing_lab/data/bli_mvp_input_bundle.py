"""``BLIMVPInputBundle``: the MVP input bundle (docs/24 implementation slice).

Scope, per `docs/24_bli_mvp_input_bundle_preflight.md`: a frozen,
deterministic, immutable valuation context for exactly one
``BondLinkedStructuredProduct``, binding one already-validated product +
one resolved, MVP-pricing-eligible ``BondReferenceData`` + one
``BLIMarketDataSnapshot`` **by reference** -- never by copying fields out
of any of them. This is an assembly-with-validation object, not a
pricing object: it has no ``pv``/``dv01``/``cashflows`` field, performs no
curve interpolation, no yield-to-price or price-to-yield conversion, no
Treasury FTP parsing, and calls no pricing function.

**Naming/location** follow `docs/24` §3 exactly: ``BLIMVPInputBundle``
inside the existing ``data/`` package, deliberately distinct from the
vanilla-rates-core ``ValuationContext`` (``valuation/context.py``) and
from ``BLIMarketDataSnapshot`` (``data/bli_snapshot.py``).

**Open questions from `docs/24` §11, resolved here (implementation
decisions, not re-litigated):**

- **Field name `resolved_bond_reference_data`** (not `docs/24` §7's
  sketched ``bond_reference_data``): the ``resolved_`` prefix makes it
  explicit that this is the resolver's matched record, not a raw/
  unresolved reference-data input -- there is no other reference-data
  field on this bundle to disambiguate it from.
- **`resolution_status` / `eligibility_reasons` are kept as two plain
  fields**, not the whole ``BondReferenceResolutionResult`` object:
  ``BondReferenceResolutionResult`` also carries ``requested_isin``
  (redundant with ``resolved_bond_reference_data.isin`` /
  ``product.bond_option.underlying_isin``) and ``block_reason`` (always
  ``None`` once construction has succeeded, since ``FOUND_ELIGIBLE`` is
  required), so unpacking only the two fields that carry real
  information keeps the bundle's own field list minimal.
- **No ``source_fixture_name`` field**: `docs/24` §7 sketched one for
  audit-trail symmetry with ``BondReferenceResolutionResult``, but this
  slice narrows the field list to what the required validation gates
  (`docs/24` §6) actually need, per this doc's own hard-stop guidance
  against widening scope. A future slice can add it if a concrete audit
  consumer needs it.
- **Bundle construction raises, it does not return a structured
  found/not-found result** (`docs/24` §11's open error-shape question):
  every other frozen dataclass in this codebase
  (``BondOption``/``DepositLeg``/``BondLinkedStructuredProduct``/
  ``BLIBondQuote``/etc.) raises ``ValueError``/``TypeError`` from
  ``__post_init__`` on an invalid combination of already-otherwise-valid
  inputs; ``BondReferenceResolutionResult``'s found/not-found pattern
  exists because ``resolve_bond_reference_data`` is a *lookup function*
  over a whole fixture iterable, a fundamentally different shape from a
  dataclass constructor being handed already-resolved objects. A future
  *bundle builder* (`docs/24` §12 step 5, not built here) remains free to
  catch this ``ValueError`` and translate it into its own structured
  result -- that is a separate, later decision.
- **``DepositRateMode.MANUAL_VERIFIED_RATE`` is rejected outright**, with
  a clear "not yet supported" error, rather than silently accepted: no
  manual-verified-rate audit record is representable anywhere in
  ``BLIMarketDataSnapshot`` today (`docs/24` §6/§11), so there is nothing
  this bundle could check for that mode yet.
- **Market-data as-of / no-look-ahead policy is a minimal, deterministic
  calendar-date comparison**, not an intraday or settlement-aware rule:
  ``market_data_snapshot.as_of_timestamp`` is parsed with
  ``datetime.fromisoformat`` and only its **calendar date** is compared
  against ``valuation_date``; an as-of date strictly after
  ``valuation_date`` is rejected, an as-of date on or before
  ``valuation_date`` is accepted. No current-time lookup
  (``date.today()``/``datetime.now()``) is ever used -- both dates being
  compared come from the objects the caller supplied. **This is a
  narrower policy than a real no-look-ahead rule would eventually need**
  (no intraday cutoff time, no settlement-aware T+0/T+1 handling) --
  recorded as a still-open limitation, not silently treated as final.
  **Only three timestamp shapes are accepted (Codex P1 review of
  PR #65):** a bare date (``"2026-07-01"``), a naive datetime with no
  timezone offset (``"2026-07-01T16:00:00"``), or a UTC datetime whose
  ``utcoffset()`` is exactly zero (``"2026-07-01T16:00:00Z"`` or the
  equivalent ``"...+00:00"`` spelling). Any *other* timezone offset
  (e.g. ``"2026-07-01T23:30:00-05:00"``, ``"2026-07-02T08:00:00+08:00"``)
  is rejected outright, never silently accepted: ``fromisoformat(...)
  .date()`` on an offset-aware datetime returns that offset's *local*
  calendar date, which can legitimately differ from the UTC calendar
  date the no-look-ahead gate needs (``"2026-07-01T23:30:00-05:00"`` is
  already ``"2026-07-02"`` in UTC) -- correctly normalizing an arbitrary
  offset is future work, not guessed at here.
- **``BLICurvePurpose.FUNDING_CURVE`` is not required**: `docs/24` §6
  only requires it "if the product/mapping explicitly calls for a
  funding adjustment," and no such mapping exists in this codebase yet.
- **Reference-data eligibility is re-verified from the actual
  ``BondReferenceData``, not only trusted from the supplied
  ``resolution_status``/``eligibility_reasons`` (Codex P1 review of
  PR #65):** the original implementation accepted any
  ``BondReferenceData`` as long as the caller separately claimed
  ``resolution_status=FOUND_ELIGIBLE`` with empty ``eligibility_reasons``
  -- stale or hand-assembled resolver metadata could therefore
  contradict the reference data it was supposedly describing (e.g. a
  callable bond bundled with a manually overridden "eligible" status).
  ``__post_init__`` now also calls the existing
  ``is_mvp_pricing_eligible(resolved_bond_reference_data)`` directly and
  rejects construction if it disagrees, regardless of what the supplied
  ``resolution_status``/``eligibility_reasons`` claim. The
  ``resolution_status``/``eligibility_reasons`` gate is kept alongside
  this, unchanged -- both must agree, neither is trusted alone.
- **Currency coherence gates (Codex P2 review of PR #65):** ISIN
  identity alone does not prove the bundle's inputs describe one
  coherent instrument in one coherent currency. ``__post_init__`` now
  also requires ``product.bond_option.currency`` (already, by
  ``BondLinkedStructuredProduct``'s own construction, equal to
  ``product.deposit_leg.currency``) to equal
  ``resolved_bond_reference_data.currency``, requires
  ``market_data_snapshot.bond_quote.currency`` to equal that same
  currency, and requires each required MVP curve purpose (see below) to
  have at least one ``curve_points`` row in that currency specifically
  -- a same-purpose row in a different currency does not satisfy the
  gate. ``deposit_rate_observation.currency`` was already required to
  match ``ftp_rate_selector.currency`` (which ``DepositLeg`` itself
  already requires to equal ``deposit_leg.currency``, i.e. the same
  product currency), so no separate deposit-rate currency check was
  needed. No FX conversion is implemented or implied by any of this --
  a currency mismatch is always a hard rejection, never a fallback.

**Hard non-goals (unchanged from `docs/24` §10):** no bundle builder, no
pricing engine, no payoff skeleton, no cash-flow generation, no schedule
engine, no yield-to-price/price-to-yield calculation, no curve
interpolation, no volatility surface, no credit spread model, no
Treasury FTP parser, no ingestion, no Bloomberg/API connector, no
QuantLib adapter, no UI. ``BondOption``, ``DepositLeg``,
``BondLinkedStructuredProduct``, ``BondReferenceData``,
``resolve_bond_reference_data``, ``is_mvp_pricing_eligible``, and
``BLIMarketDataSnapshot`` (and its component classes) are all
unmodified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from shiori_pricing_lab.data._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.data.bli_snapshot import (
    BLICurvePurpose,
    BLIMarketDataSnapshot,
    require_exact_isin_match,
)
from shiori_pricing_lab.products.bond_linked_structured_product import (
    BondLinkedStructuredProduct,
)
from shiori_pricing_lab.products.enums import DepositRateMode, coerce_enum
from shiori_pricing_lab.reference_data.bond_reference_data import BondReferenceData
from shiori_pricing_lab.reference_data.eligibility import is_mvp_pricing_eligible
from shiori_pricing_lab.reference_data.resolution import BondResolutionStatus

# Curve purposes required for the MVP BLI pricing path (docs/24 §6):
# presence only, never tenor-node selection or interpolation.
# BLICurvePurpose.FUNDING_CURVE is deliberately excluded -- required only
# if a future mapping calls for a funding adjustment, which none does yet.
_REQUIRED_MVP_CURVE_PURPOSES: frozenset[BLICurvePurpose] = frozenset(
    {
        BLICurvePurpose.BOND_REFERENCE_CURVE,
        BLICurvePurpose.OPTION_DISCOUNT_CURVE,
        BLICurvePurpose.DEPOSIT_CURVE,
    }
)


def _parse_as_of_calendar_date(as_of_timestamp: str, field_name: str) -> date:
    """Parse ``as_of_timestamp`` and return its calendar date only.

    Minimal, deterministic ISO-8601 parsing (docs/24 §6/§11, Codex P1
    review of PR #65) -- accepts a bare date (``"2026-07-01"``), a naive
    datetime with no timezone offset (``"2026-07-01T16:00:00"``), or a UTC
    datetime whose ``utcoffset()`` is exactly zero (``"2026-07-01T16:00:00Z"``
    or the equivalent ``"...+00:00"`` spelling, matching the existing
    synthetic fixture's shape). Any other timezone offset is rejected
    outright -- ``fromisoformat(...).date()`` on an offset-aware datetime
    returns that offset's *local* calendar date, which can silently differ
    from the UTC calendar date this no-look-ahead check needs (e.g.
    ``"2026-07-01T23:30:00-05:00"`` is already ``"2026-07-02"`` in UTC).
    Never falls back to the current date/time; a value that cannot be
    parsed, or that carries an unsupported non-UTC offset, is rejected
    outright rather than silently accepted or misread.
    """

    try:
        parsed = datetime.fromisoformat(as_of_timestamp)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO-8601 date, a naive datetime, or a UTC "
            f"('Z'-suffixed) datetime for the no-look-ahead check, got {as_of_timestamp!r}"
        ) from exc

    if parsed.tzinfo is not None and parsed.utcoffset() != timedelta(0):
        raise ValueError(
            f"{field_name} must be a bare date, a naive datetime, or a UTC "
            "('Z'-suffixed) timestamp for the no-look-ahead check -- non-UTC timezone "
            f"offsets are not supported yet, got {as_of_timestamp!r}"
        )
    return parsed.date()


@dataclass(frozen=True)
class BLIMVPInputBundle:
    """The MVP input bundle: product + resolved reference data + market data.

    Every object-typed field is a **reference** to an already-independently-
    valid object (docs/24 §4.1/§4.2) -- no field here duplicates a value
    already owned by ``product``, ``resolved_bond_reference_data``, or
    ``market_data_snapshot``. See the module docstring for the exact
    validation gates enforced in ``__post_init__`` and for the open
    questions this implementation slice resolved.
    """

    bundle_id: str
    valuation_date: str
    product: BondLinkedStructuredProduct
    resolved_bond_reference_data: BondReferenceData
    resolution_status: BondResolutionStatus
    eligibility_reasons: tuple[str, ...]
    market_data_snapshot: BLIMarketDataSnapshot

    def __post_init__(self) -> None:
        _require_non_blank(self.bundle_id, "bundle_id")

        # valuation_date is explicit and parsed only for format validation --
        # never date.today()/datetime.now() anywhere in this module (docs/09 §3).
        valuation_date = _parse_iso_date(self.valuation_date, "valuation_date")

        if not isinstance(self.product, BondLinkedStructuredProduct):
            raise TypeError("product must be a BondLinkedStructuredProduct")
        if not isinstance(self.resolved_bond_reference_data, BondReferenceData):
            raise TypeError(
                "resolved_bond_reference_data must be a BondReferenceData"
            )
        if not isinstance(self.market_data_snapshot, BLIMarketDataSnapshot):
            raise TypeError("market_data_snapshot must be a BLIMarketDataSnapshot")

        object.__setattr__(
            self,
            "resolution_status",
            coerce_enum(self.resolution_status, BondResolutionStatus, "resolution_status"),
        )
        object.__setattr__(self, "eligibility_reasons", tuple(self.eligibility_reasons))

        # Reference data must be MVP-pricing eligible -- FOUND_INELIGIBLE and
        # NOT_FOUND both block bundle construction outright (docs/24 §6,
        # docs/22 §5.1/§10 gate 3). The bundle only branches on the
        # resolver's own status; it never re-derives eligibility.
        if self.resolution_status is not BondResolutionStatus.FOUND_ELIGIBLE:
            raise ValueError(
                "resolution_status must be FOUND_ELIGIBLE, got "
                f"{self.resolution_status.value} "
                f"(eligibility_reasons={self.eligibility_reasons!r})"
            )
        if self.eligibility_reasons:
            raise ValueError(
                "eligibility_reasons must be empty when resolution_status is "
                f"FOUND_ELIGIBLE, got {self.eligibility_reasons!r}"
            )

        # Re-verify eligibility directly from the reference data itself
        # (Codex P1 review of PR #65) -- the checks above only trust
        # whatever resolution_status/eligibility_reasons the caller
        # supplied, which a stale or hand-assembled resolver result could
        # contradict (e.g. a callable bond bundled with a manually
        # overridden "eligible" status). is_mvp_pricing_eligible is the
        # single existing source of truth for MVP eligibility
        # (reference_data.eligibility, docs/20 §5); it must agree with the
        # supplied status, never be bypassed by it.
        actual_eligibility = is_mvp_pricing_eligible(self.resolved_bond_reference_data)
        if not actual_eligibility.eligible:
            raise ValueError(
                "resolved_bond_reference_data is not actually MVP-pricing eligible "
                f"(is_mvp_pricing_eligible reasons: {'; '.join(actual_eligibility.reasons)}), "
                f"despite supplied resolution_status={self.resolution_status.value}"
            )

        # ISIN gates -- plain string equality only, no fuzzy/prefix/
        # case-insensitive matching anywhere (docs/24 §6, docs/21 §4).
        if self.product.bond_option.underlying_isin != self.resolved_bond_reference_data.isin:
            raise ValueError(
                "product.bond_option.underlying_isin "
                f"({self.product.bond_option.underlying_isin!r}) does not exactly match "
                "resolved_bond_reference_data.isin "
                f"({self.resolved_bond_reference_data.isin!r})"
            )
        require_exact_isin_match(self.market_data_snapshot, self.resolved_bond_reference_data.isin)

        # Currency coherence gates (Codex P2 review of PR #65): ISIN
        # identity alone does not prove the bundle's inputs share one
        # coherent currency. BondLinkedStructuredProduct.__post_init__
        # already enforces bond_option.currency == deposit_leg.currency
        # (docs/19), so bond_option.currency is used as the single
        # "product currency" reference point here. No FX conversion is
        # implemented or implied -- a mismatch is always a hard rejection.
        product_currency = self.product.bond_option.currency
        if self.resolved_bond_reference_data.currency is not product_currency:
            raise ValueError(
                "product currency "
                f"({product_currency.value}) does not match "
                "resolved_bond_reference_data.currency "
                f"({self.resolved_bond_reference_data.currency.value})"
            )
        if self.market_data_snapshot.bond_quote.currency is not product_currency:
            raise ValueError(
                "market_data_snapshot.bond_quote.currency "
                f"({self.market_data_snapshot.bond_quote.currency.value}) does not match "
                f"product currency ({product_currency.value})"
            )

        # Valuation-date coherence -- equality alone, restated from docs/24
        # §6: it is necessary but (per the as-of check below) not sufficient.
        if self.market_data_snapshot.valuation_date != self.valuation_date:
            raise ValueError(
                "market_data_snapshot.valuation_date "
                f"({self.market_data_snapshot.valuation_date!r}) must equal bundle "
                f"valuation_date ({self.valuation_date!r})"
            )

        # Market-data as-of / no-look-ahead gate (docs/24 §6): valuation-date
        # equality alone is not sufficient. Same-date as-of is allowed;
        # an as-of date strictly after valuation_date is rejected.
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

        self._require_product_specific_market_data()
        self._require_mvp_curve_purposes()

    def _require_product_specific_market_data(self) -> None:
        """Product-specific market-data presence gates (docs/24 §6).

        An ``isinstance`` check on ``BLIMarketDataSnapshot`` only proves
        internal well-formedness -- it has no notion of which product it
        is for. This method is the bundle-layer gate that checks what
        ``product.deposit_leg.deposit_rate_mode`` specifically requires.
        Presence and selector consistency only -- never re-resolving,
        parsing, or recomputing a rate.
        """

        deposit_leg = self.product.deposit_leg
        deposit_rate_observation = self.market_data_snapshot.deposit_rate_observation

        if deposit_leg.deposit_rate_mode is DepositRateMode.TREASURY_FTP_REFERENCE:
            if deposit_rate_observation is None:
                raise ValueError(
                    "market_data_snapshot.deposit_rate_observation is required when "
                    "product.deposit_leg.deposit_rate_mode is TREASURY_FTP_REFERENCE"
                )
            selector = deposit_leg.ftp_rate_selector
            assert selector is not None  # guaranteed by DepositLeg's own validation
            if deposit_rate_observation.currency is not selector.currency:
                raise ValueError(
                    "market_data_snapshot.deposit_rate_observation.currency "
                    f"({deposit_rate_observation.currency.value}) must match "
                    f"product.deposit_leg.ftp_rate_selector.currency ({selector.currency.value})"
                )
            if deposit_rate_observation.tenor is not selector.tenor:
                raise ValueError(
                    "market_data_snapshot.deposit_rate_observation.tenor "
                    f"({deposit_rate_observation.tenor.value}) must match "
                    f"product.deposit_leg.ftp_rate_selector.tenor ({selector.tenor.value})"
                )
            if deposit_rate_observation.quote_side is not selector.quote_side:
                raise ValueError(
                    "market_data_snapshot.deposit_rate_observation.quote_side "
                    f"({deposit_rate_observation.quote_side.value}) must match "
                    "product.deposit_leg.ftp_rate_selector.quote_side "
                    f"({selector.quote_side.value})"
                )
        elif deposit_leg.deposit_rate_mode is DepositRateMode.FIXED_RATE:
            # The rate is already a deal term on DepositLeg (docs/18 §4.1);
            # no separate deposit_rate_observation is required for the rate
            # itself, present or not.
            pass
        else:  # DepositRateMode.MANUAL_VERIFIED_RATE
            raise ValueError(
                "product.deposit_leg.deposit_rate_mode MANUAL_VERIFIED_RATE is not "
                "supported by BLIMVPInputBundle yet -- no manual-verified-rate audit "
                "record is representable in BLIMarketDataSnapshot today (docs/24 §6/§11); "
                "this requires a future, explicit audit-policy decision, not a silent "
                "acceptance"
            )

    def _require_mvp_curve_purposes(self) -> None:
        """Require presence of every MVP-required curve purpose (docs/24 §6).

        Presence only -- never tenor-node selection, never interpolation.
        A non-empty ``curve_points`` (already guaranteed by
        ``BLIMarketDataSnapshot`` itself) is not the same as "the required
        purposes are present." A required purpose present only under a
        *different* currency does not satisfy this gate either (Codex P2
        review of PR #65, currency-coherence gates): a USD Deposit Curve
        row does not make an EUR product's Deposit Curve requirement
        satisfied.
        """

        product_currency = self.product.bond_option.currency
        present_purposes_in_product_currency = {
            point.curve_purpose
            for point in self.market_data_snapshot.curve_points
            if point.currency is product_currency
        }
        missing_purposes = _REQUIRED_MVP_CURVE_PURPOSES - present_purposes_in_product_currency
        if missing_purposes:
            missing_names = sorted(purpose.value for purpose in missing_purposes)
            raise ValueError(
                "market_data_snapshot.curve_points is missing required curve "
                f"purpose(s) in currency {product_currency.value}: {missing_names}"
            )
