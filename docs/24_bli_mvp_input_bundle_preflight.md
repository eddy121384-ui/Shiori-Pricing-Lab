# 24 BLI MVP Input Bundle Preflight

Status: docs-only preflight. No MVP input bundle class, bundle builder,
pricing engine, payoff skeleton, cash-flow generation, schedule engine,
yield-to-price calculation, curve interpolation, volatility surface,
credit spread model, Treasury FTP parser, ingestion, Bloomberg/API
connector, QuantLib adapter, or UI is added by this doc. `BondOption`,
`DepositLeg`, `BondLinkedStructuredProduct`, `BondReferenceData`,
`resolve_bond_reference_data`, `is_mvp_pricing_eligible`, and
`BLIMarketDataSnapshot` are all unmodified. No frozen BLI v1.3 source
spec file (`SPEC_v1.3.md`, `ANNEX_A_v1.3.md`, `ANNEX_B_v1.3.md`,
`ANNEX_C_v1.3.md`) is edited. Package exports are unchanged. Issue #38
is unaffected and remains open.

## 1. Why this doc exists

`docs/23_bli_market_data_snapshot_schema_preflight.md` (PR #62) and its
implementation slice (PR #63, merged) completed the layer-3 half of
`docs/22_bli_market_data_input_bundle_preflight.md`'s four-layer
boundary: `BLIMarketDataSnapshot` and its component objects
(`BLIBondQuote`, `BLICurvePoint`, `BLIDepositRateObservation`,
`BLIVolatilityInput`, `BLICreditSpreadInput`) now exist as real, tested
code in `src/shiori_pricing_lab/data/bli_snapshot.py`, with a synthetic
fixture (`data/bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`)
and 96 tests (`tests/test_bli_market_data_snapshot.py`).

`docs/22` §12 already recommended the next step as slice 3, "MVP input
bundle docs/code" — but `docs/22` itself was written *before* the
snapshot existed, so its §3/§5/§6 field lists were necessarily
conceptual placeholders. This doc is the concrete follow-up: it
re-derives the bundle boundary against the **actual** implemented
classes (not the pre-implementation field list), following the same
"preflight before code" pattern used for every prior BLI slice
(`docs/18`, `docs/19`, `docs/20`, `docs/21`, `docs/23`).

This doc does not implement anything. It exists so the next PR can
implement the smallest useful MVP input bundle dataclass without making
boundary or naming decisions ad hoc while writing code.

---

## 2. What is the MVP input bundle?

Restated and narrowed from `docs/22` §5, now that layer 3 is concrete:

```text
The MVP input bundle is a deterministic, immutable valuation context
for exactly one BondLinkedStructuredProduct, for exactly one valuation
date, produced by binding together:

  one already-validated BondLinkedStructuredProduct  (product terms)
  one resolved, MVP-pricing-eligible BondReferenceData (reference data)
  one internally-valid BLIMarketDataSnapshot           (market data)

plus the cross-checks that only make sense once all three are present
together (ISIN identity across all three, product-specific market-data
presence per §6, valuation-date and as-of/no-look-ahead coherence
per §6).
```

**Internal validity is necessary but not sufficient** (Codex P2 review
of PR #64): `BLIMarketDataSnapshot.__post_init__` only proves the
snapshot is well-formed *in isolation* (non-empty `curve_points`, no
duplicate/conflicting/ambiguous curve nodes, FTP percent/decimal
consistency, `ACTIVE`-only status) — it has no notion of "which
product is this snapshot for," so it cannot and does not check that
the snapshot actually carries what *this specific product* needs (a
matching deposit-rate observation, the required curve purposes, etc.).
§6 states this as an explicit, additional bundle-construction gate, not
something an `isinstance` check on `BLIMarketDataSnapshot` already
covers.

It answers exactly one question:

```text
Given this product, this resolved bond, and this market snapshot, do
we have a complete, internally consistent, valuation-ready input for
one BLI pricing run?
```

It does **not** answer:

```text
What is the PV / premium / customer return?    -- a future PricingResult
Which curve interpolation method applies?      -- a future pricing engine
What is the option's payoff?                   -- a future payoff engine
```

**It must not price anything.** Restated from `docs/22` §5/§13: the
bundle is the single object a future pricing engine consumes — it is
not itself part of the pricing calculation, has no `pv`/`dv01`/
`cashflows` field, and performs no interpolation, conversion, or
numerical methodology of any kind. Its only job is *assembly with
validation*, mirroring the existing spine contract already documented
in `docs/09 (removed, see git history)` §1 (`Product Definition + ValuationContext +
MarketDataSnapshot → price(...) → PricingResult`) — the BLI bundle is
the BLI-specific instantiation of "everything `price(...)` needs to
actually price this product," not a second, parallel contract.

---

## 3. Proposed naming and location

### 3.1 Class name

**Recommended: `BLIMVPInputBundle`.**

Considered alternatives and why they are not recommended:

- `BLIValuationInputBundle` — reasonable, but "valuation input" doesn't
  signal that this is specifically the *MVP-scoped* bundle (a future,
  richer bundle covering callable bonds, physical settlement, or
  multi-curve funding mappings might one day need a different or
  extended shape). `BLIMVPInputBundle` keeps the "this is the MVP slice's
  shape, not necessarily the final one" caveat visible in the name
  itself, consistent with `is_mvp_pricing_eligible` and
  `SYNTHETIC_BOND_FIXTURES`'s own "MVP" labeling elsewhere in this
  codebase.
- `BLIInputBundle` (no "MVP") — drops that signal; not recommended for
  the same reason.
- `BLIPricingInputBundle` — reads as if it already touches pricing
  methodology, which risks blurring exactly the boundary §2 draws
  ("it must not price anything"). Rejected to avoid that implication.
- Reusing `MarketDataSnapshot` or a `Bundle`-suffixed variant of the
  existing vanilla-rates-core name — rejected for the same reason
  `docs/23` §3.4 rejected reusing `MarketDataSnapshot` for
  `BLIMarketDataSnapshot`: the vanilla-rates-core `ValuationContext`
  (`valuation/context.py`) already occupies the "binds a valuation date
  + snapshot + settings" role for the IRS/OIS/CCS/FX-Swap path, and it
  has a structurally different shape (reporting currency, curve
  building) that the BLI bundle does not share. A distinct name avoids
  a reader assuming the two are interchangeable.

**This is a naming recommendation only** — like `docs/23` §3.4, the
implementation slice may pick a different name if it states a reason,
but must not silently reuse an existing, structurally-different class
name.

### 3.2 Module location

**Recommended: a new module inside the existing `data/` package,**
`src/shiori_pricing_lab/data/bli_mvp_input_bundle.py`, mirroring
`docs/23` §3.3's reasoning for `bli_snapshot.py`: the bundle binds
market data (already homed in `data/`) with product/reference-data
*references*, not new product or reference-data content, so it is
closer to a market-data-consuming assembly step than a new top-level
concept. This keeps the growing family of BLI-specific `data/` modules
(`bli_snapshot.py`, and now the bundle) discoverable in one place,
matching the existing "small, focused module per concept" pattern
already used inside `data/` (`providers.py` vs. `snapshot.py` vs.
`bli_snapshot.py` are already separate concerns).

**Considered, not recommended:** a new top-level package,
`shiori_pricing_lab.bundle/` or `shiori_pricing_lab.valuation_context/`.
Rejected for the same reason `docs/23` §3.2 rejected a new top-level
`market_data/` package: the bundle is not a genuinely new *kind* of
concern the way `reference_data/` was (Bond Reference Data needed its
own package specifically because it is *not* market data and *not* a
product — the bundle is neither of those; it is an assembly of
already-homed concepts). A second top-level package would fragment
discoverability rather than clarify it.

**No module is created by this doc.**

---

## 4. Boundary and ownership

### 4.1 What belongs in the bundle

```text
a reference to exactly one BondLinkedStructuredProduct (the already-
  validated product object itself, not a copy of its fields)
a reference to exactly one resolved BondReferenceData (the record
  resolve_bond_reference_data returned, not a copy)
the BondResolutionStatus / EligibilityResult.reasons from that
  resolution (audit context: how eligibility was established)
a reference to exactly one BLIMarketDataSnapshot (the whole frozen
  object, not a flattened copy of its fields)
the bundle's own valuation_date (redundant with, and validated against,
  both the snapshot's valuation_date and the resolution's implicit
  as-of context -- see §4.3 and §6)
a small set of cross-object validation results (e.g. "isin match: OK",
  recorded for audit, not re-derived by a future pricing engine)
```

### 4.2 What does not belong in the bundle

Restated and extended from `docs/22` §4's exclusion lists, now that
concrete classes exist to check against:

```text
no duplicated product field (notional, strike, expiry_date, etc.) --
  the bundle references the BondLinkedStructuredProduct, it does not
  copy its fields out
no duplicated reference-data field (coupon, maturity_date, day_count,
  etc.) -- same reasoning, reference the BondReferenceData
no duplicated market-data field (clean_price_per_100, curve rates,
  volatility, credit_spread, etc.) -- reference the
  BLIMarketDataSnapshot, do not flatten its contents into new bundle-
  level fields
no curve interpolation result, no bootstrapped discount factor
no yield-to-price or price-to-yield conversion result
no computed volatility (e.g. an interpolated or converted vol) --
  only the BLIVolatilityInput as observed
no computed credit spread adjustment
no pv / dv01 / cashflows / customer_return / bank_margin / any pricing
  output field
no curve selection *logic* (e.g. "which curve_id wins if two are
  eligible") -- curve identity ambiguity is already rejected at
  BLIMarketDataSnapshot construction time (docs/23 §12's ambiguous-
  curve_id-set rule); the bundle only checks presence, per §4.4/§6
  below, not tie-breaking logic
```

### 4.3 Which layer owns which check

Restated from `docs/22` §2's four-layer boundary, made concrete against
the classes that now exist:

| Check | Owner (unchanged by this doc) |
| --- | --- |
| Product terms are individually valid (dates, notionals, enums) | `BondOption.__post_init__`, `DepositLeg.__post_init__` |
| Wrapper-level product relationship (currency match, settlement-date guardrail, CASH-only settlement) | `BondLinkedStructuredProduct.__post_init__` |
| Is the referenced bond plain-vanilla enough to price at all | `is_mvp_pricing_eligible` |
| Does an ISIN resolve to a reference-data record, and is it eligible | `resolve_bond_reference_data` |
| Are the market-data sub-observations individually well-formed (finite numbers, non-blank strings, FTP percent/decimal consistency, curve-node duplicate/conflict/ambiguity, `ACTIVE`-only status) | `BLIBondQuote` / `BLICurvePoint` / `BLIDepositRateObservation` / `BLIVolatilityInput` / `BLICreditSpreadInput` / `BLIMarketDataSnapshot.__post_init__` |
| Does the snapshot's bond quote ISIN exactly match an expected ISIN | `require_exact_isin_match` (already exists, `data/bli_snapshot.py`) |
| **New, this doc scopes it:** does the *product's* bond option ISIN match the *resolved reference data's* ISIN | future bundle construction (§6) |
| **New:** does the *resolved reference data's* ISIN match the *snapshot's* bond quote ISIN | future bundle construction (§6), reusing `require_exact_isin_match` |
| **New:** is the resolved reference data actually eligible (not just found) | future bundle construction (§6), reusing the existing `BondResolutionStatus`/`EligibilityResult` the resolver already returns |
| **New:** do the product, reference data, and snapshot agree on a coherent valuation context | future bundle construction (§6/§8) |
| Curve interpolation, discounting, yield-to-price conversion, payoff calculation, PV | **nobody yet** — future pricing engine, out of scope for the bundle |

The bundle's job is narrow: it is the layer that notices *disagreement
between* already-individually-valid objects (an ISIN mismatch, an
ineligible bond, an incoherent valuation date) — it does not re-validate
what each object already validates about itself.

### 4.4 What the bundle does not re-validate

To avoid duplicating checks that already exist and could drift out of
sync:

```text
the bundle does not re-check that BondOption / DepositLeg /
  BondLinkedStructuredProduct fields are individually well-formed --
  that already happened at their own construction, which is
  unconditionally already true for any BondLinkedStructuredProduct
  instance the bundle is handed (dataclasses cannot exist half-
  constructed)
the bundle does not re-check BLIBondQuote / BLICurvePoint / etc.
  individual field validity, FTP percent/decimal consistency, or
  curve-node duplicate/conflict/ambiguity -- BLIMarketDataSnapshot
  already enforces all of that at its own construction (docs/23 §12)
the bundle does not re-implement is_mvp_pricing_eligible's rules
  (callable/sinkable/zero-coupon/OTHER-yield-convention/non-vanilla-
  bond_type/inactive-status) -- it only checks that the resolver's
  returned status was FOUND_ELIGIBLE, per §6
```

---

## 5. Data-flow diagram (text)

```text
BondOption + DepositLeg
        |
        v
BondLinkedStructuredProduct           (already validated at
        |                              construction, docs/19)
        |
        |     BondReferenceData fixtures (or a future real source)
        |             |
        |             v
        +----> resolve_bond_reference_data(underlying_isin, fixtures)
        |             |
        |             v
        |      BondReferenceResolutionResult
        |      (status, bond_reference_data, eligibility_reasons)
        |             |
        |  BLIMarketDataSnapshot     |
        |  (already validated at    |
        |   construction, docs/23)  |
        |             |             |
        v             v             v
      +-------------------------------------+
      |     future BLIMVPInputBundle         |
      |  (this doc scopes, does not build)   |
      |                                      |
      |  cross-checks only:                  |
      |   - product ISIN == reference ISIN   |
      |   - reference ISIN == snapshot ISIN  |
      |   - resolution status FOUND_ELIGIBLE |
      |   - valuation-date coherence         |
      +-------------------------------------+
                      |
                      v
        future pricing engine (not built here)
        consumes ONE BLIMVPInputBundle,
        performs curve interpolation, yield/price
        conversion, payoff calculation, PV -- none
        of that logic lives upstream of this point
```

The product schema, the resolver, and the snapshot are each already
independently constructed and already independently valid *before* the
bundle ever sees them (docs/15/18/19/20/21/23) — the bundle's arrows in
the diagram above represent "handed a reference to," not "constructs."

---

## 6. Required validation rules for the future implementation

Acceptance-criteria-style checklist, mirroring `docs/18` §10 / `docs/20`
§10 / `docs/23` §12's format. **None of these are implemented here.**

```text
product must be a BondLinkedStructuredProduct instance (isinstance
  check; a bare BondOption or DepositLeg alone is not sufficient input
  for the MVP bundle, which prices the wrapper, not a standalone leg).

product must contain exactly the bond option and deposit leg shapes
  the MVP wrapper already enforces -- this is not a new rule to invent;
  it is BondLinkedStructuredProduct.__post_init__'s existing CASH-only-
  settlement, FULL_PRINCIPAL_AT_MATURITY, and effective-settlement-date
  guardrails (docs/19), which are unconditionally already true for any
  instance the bundle is handed. The bundle only needs an isinstance
  check, not a re-implementation of docs/19's rules.

bond_option.underlying_isin must exactly match the resolved
  BondReferenceData.isin -- reuse plain string equality (the same
  exact-match-only convention already used by resolve_bond_reference_data,
  docs/21 §4, and require_exact_isin_match, docs/23 §12). This is
  almost always true by construction if the resolver was called with
  bond_option.underlying_isin in the first place, but the bundle
  constructor should not *assume* the caller passed a resolution result
  for the right ISIN -- it should check.

BLIMarketDataSnapshot.bond_quote.isin must exactly match the resolved
  BondReferenceData.isin -- reuse the existing require_exact_isin_match
  helper (data/bli_snapshot.py) rather than re-implementing the
  comparison; do not accept a snapshot for a different bond.

reference data must be MVP-pricing eligible -- the bundle constructor
  requires BondResolutionStatus.FOUND_ELIGIBLE; FOUND_INELIGIBLE and
  NOT_FOUND both block bundle construction outright (restated from
  docs/22 §5.1/§10 gate 3, now concrete: the resolver's own result type
  already carries this status, so the bundle only branches on it, it
  does not re-derive eligibility).

market data snapshot must be internally valid before bundling -- this
  is automatically true for any BLIMarketDataSnapshot instance (its own
  __post_init__ already enforces internal well-formedness, docs/23
  §12: non-empty curve_points, no duplicate/conflicting/ambiguous curve
  nodes, FTP percent/decimal consistency, ACTIVE-only status). An
  isinstance check confirms this much, but **this is necessary, not
  sufficient** (Codex P2 review of PR #64): internal well-formedness
  says nothing about whether the snapshot carries what *this specific
  product* needs -- BLIMarketDataSnapshot's own validation has no
  concept of "which product is this for," so a product-specific gate
  can only live at the bundle layer, never be delegated back to the
  snapshot. The bundle constructor must additionally check, beyond
  isinstance:

    bond quote for the resolved ISIN -- market_data_snapshot.bond_quote
      is a required (non-Optional) field on every BLIMarketDataSnapshot,
      so this gate reduces to the ISIN-match rule already listed above
      (BLIMarketDataSnapshot.bond_quote.isin ==
      bond_reference_data.isin); restated here only so the
      product-specific gate list is complete, not because it needs new
      logic beyond that ISIN check.

    deposit-rate observation, product-conditional on
      DepositLeg.deposit_rate_mode (docs/18 §4) -- this is the concrete
      gap Codex flagged: BLIMarketDataSnapshot.deposit_rate_observation
      is optional and unconditionally allowed to be None (docs/23), so
      nothing today stops a TREASURY_FTP_REFERENCE-mode DepositLeg from
      being bundled with a snapshot that carries no matching FTP
      observation. The bundle must require:
        - DepositRateMode.TREASURY_FTP_REFERENCE: market_data_snapshot.
          deposit_rate_observation must be present (non-None), and its
          currency/tenor/quote_side must match
          product.deposit_leg.ftp_rate_selector -- presence and
          selector consistency only; the bundle does not re-resolve,
          parse, or recompute the rate itself.
        - DepositRateMode.FIXED_RATE: the rate is already a deal term
          on DepositLeg (docs/18 §4.1); no deposit_rate_observation is
          required for the *rate* itself, though the Deposit Curve
          (below) may still be required for discounting.
        - DepositRateMode.MANUAL_VERIFIED_RATE: a manual-verified-rate
          audit record is required per docs/18 §4.3 -- not yet
          representable anywhere in BLIMarketDataSnapshot today; this
          is an open item (§11), not resolved by this doc.

    required MVP curve-purpose presence -- see the dedicated gate list
      immediately below; a non-empty curve_points is not the same as
      "the required curves are present."

  All of these are **presence and consistency checks only** (Codex P2
  review of PR #64's explicit boundary): the bundle confirms *that* a
  required component exists and matches the product's own selector, and
  performs no curve interpolation, no yield/price conversion, no
  Treasury FTP parsing, no pricing, and no fallback construction of a
  missing value.

required MVP curve-purpose gates -- restated as its own explicit gate
  because `BLIMarketDataSnapshot.curve_points` being non-empty (docs/23
  §12) only proves *some* curve data exists, not that the *right*
  curve purposes exist for MVP BLI valuation (Codex P2 review of
  PR #64: a snapshot carrying only, say, a lone
  BLICurvePurpose.FUNDING_CURVE row is internally valid today but
  useless for the MVP pricing path). Using the actual enum members
  already defined on BLICurvePurpose (data/bli_snapshot.py), the bundle
  must require, by presence only:

    at least one curve_points row with
      curve_purpose == BLICurvePurpose.BOND_REFERENCE_CURVE -- required
      for the bond leg / bond option's own bond-side valuation context
      (SPEC §3.5, docs/22 §6.2).
    at least one curve_points row with
      curve_purpose == BLICurvePurpose.OPTION_DISCOUNT_CURVE -- required
      for discounting the option-side valuation (SPEC §3.5, docs/22
      §6.2). Restated, unchanged: the Option Discount Curve and Bond
      Reference Curve are separate concepts and must never be treated
      as interchangeable (docs/23 §7, SPEC §3.5) -- the bundle checks
      for both purposes independently, never accepting one curve_id as
      satisfying both gates.
    at least one curve_points row with
      curve_purpose == BLICurvePurpose.DEPOSIT_CURVE -- required for
      the deposit leg's own discounting/funding calculation (docs/22
      §6.2, docs/23 §11.1) regardless of deposit_rate_mode. This is a
      separate discounting input from the deposit-rate observation
      above; neither substitutes for the other (docs/23 §11.1's
      Codex-P2-fixed rule, restated, not re-opened).
    curve_purpose == BLICurvePurpose.FUNDING_CURVE -- required only if
      the product/mapping explicitly calls for a funding adjustment;
      this doc does not decide when that mapping applies (docs/22
      §6.2/§14 already leaves the curve-mapping-table shape open,
      restated in §11, not re-opened here).

  The bundle checks **presence of at least one row for each required
  purpose only** -- never which specific tenor node a future pricing
  engine should read, and never how to interpolate between tenors.
  Selecting a tenor node and interpolating between tenors remain future
  pricing-engine work, entirely out of scope for the bundle (same "no
  curve interpolation inside the bundle" rule stated below, restated
  here for emphasis since this is exactly the boundary Codex's review
  asked to keep intact).

valuation date / as-of timestamp handling must be explicit, and
  **valuation-date equality alone is not sufficient** (Codex P2 review
  of PR #64, correcting an earlier version of this doc that treated
  `bundle.valuation_date == market_data_snapshot.valuation_date` as
  enough valuation-context coherence):

    the bundle's own valuation_date must be a required, explicit,
      non-defaulted field (same "no date.today()" invariant already
      enforced everywhere else in this repo, docs/09 (removed, see git history) §3).
    market_data_snapshot.valuation_date must equal the bundle's
      valuation_date (unchanged from the earlier version of this doc).
    market_data_snapshot.as_of_timestamp must ALSO be validated under
      an explicit no-look-ahead rule, not merely checked for
      non-blankness. Today, BLIMarketDataSnapshot.__post_init__ only
      requires as_of_timestamp to be a non-blank string (docs/23) -- it
      is never parsed as a timestamp or compared against
      valuation_date, so nothing currently stops a snapshot whose
      as_of_timestamp is, in substance, *after* the valuation date it
      claims to represent. For a historical-valuation bundle, market
      data whose as_of_timestamp falls after the valuation date's
      allowed market-data cutoff must be rejected -- a bundle must
      never be built from market data that "looked ahead" of the
      valuation date it represents (restated from docs/22 §8's
      no-look-ahead principle, now made concrete as a bundle-
      construction gate rather than a general policy statement).
    the exact cutoff rule (e.g. "as_of_timestamp's calendar date must
      be <= valuation_date," vs. an intraday cutoff time, vs. a
      settlement-aware T+0/T+1 rule) is **not decided by this doc** --
      this is an explicit, required policy decision the implementation
      slice must make and document, not something a future pricing
      engine may silently interpret differently on each call. Recorded
      as an open item in §11, not resolved here.
    whether the bundle also needs a reference-data "as of" concept
      remains open (unchanged from the earlier version of this doc) --
      BondReferenceData / resolve_bond_reference_data carry no
      valuation-date field of their own today (docs/21 §7.1's
      conclusion: point-in-time correctness of the *fixtures iterable*
      supplied to the resolver is the caller's job, not the resolver's
      or the bundle's), so there is no second date to reconcile against
      yet, only the caller's obligation to supply an already-as-of-
      correct fixtures iterable in the first place.

no fuzzy ISIN matching anywhere in the bundle -- every ISIN comparison
  in this doc is plain string equality, never prefix, case-insensitive,
  or check-digit-corrected matching (restated from docs/21 §4, docs/23
  §12, unchanged, not re-opened).

no silent fallback of any kind -- a missing or mismatched required
  input blocks bundle construction outright; the bundle never
  substitutes a default, a "latest known," or a proxy value for a
  missing product/reference/market-data component (restated from
  docs/22 §5.1, unchanged).

no yield-to-price or price-to-yield conversion inside the bundle --
  the bundle passes through whichever of BLIBondQuote.
  clean_price_per_100 / yield_value were actually observed (both may be
  present per docs/23's Codex-P2 fix); it must not compute the missing
  one from the other.

no curve interpolation inside the bundle -- the bundle passes through
  BLIMarketDataSnapshot.curve_points as-is; tenor-to-tenor interpolation
  is future pricing-engine work, not scoped or started here.

no pricing / PV calculation inside the bundle -- restated from §2; the
  bundle has no pv/dv01/cashflows field and calls no pricing function.
```

---

## 7. Proposed future dataclass shape (sketch only, not code)

```text
BLIMVPInputBundle (frozen, tentative field list):

  bundle_id                       str, non-blank, audit label
  valuation_date                  str, explicit YYYY-MM-DD, required
                                   (validated equal to
                                   market_data_snapshot.valuation_date)
  product                         BondLinkedStructuredProduct
                                   (a reference, not copied fields)
  bond_reference_data             BondReferenceData
                                   (the resolver's matched record,
                                   a reference, not copied fields)
  resolution_status               BondResolutionStatus
                                   (must equal FOUND_ELIGIBLE at
                                   construction, else construction
                                   raises -- kept as a field anyway so
                                   the accepted bundle still carries an
                                   explicit audit trail of how
                                   eligibility was established, not
                                   just an implicit "it must have
                                   passed")
  eligibility_reasons             tuple[str, ...]
                                   (expected empty when
                                   resolution_status is FOUND_ELIGIBLE;
                                   kept for symmetry with
                                   BondReferenceResolutionResult, not a
                                   new concept)
  market_data_snapshot            BLIMarketDataSnapshot
                                   (a reference, not copied fields)
  source_fixture_name             str
                                   (audit-only label for which
                                   reference-data source resolved this
                                   bundle, mirroring
                                   BondReferenceResolutionResult.
                                   source_fixture_name -- not a new
                                   concept, just carried through)

Deliberately NOT included (see §4.2):
  no duplicated product/reference/market-data field
  no curve mapping selection field (an open question, §9)
  no pv / dv01 / cashflows / pricing_result field
  no computed / interpolated / converted value of any kind
```

This is a sketch for the next implementation slice to confirm or amend
against Annex A/B/SPEC one more time while writing the actual dataclass
— exactly the same caveat `docs/23` §16 applied to its own field list
before implementation.

---

## 8. Fixture plan

### 8.1 What already exists and can be reused directly

```text
reference_data.fixtures.SYNTHETIC_BOND_FIXTURES
  -- specifically the eligible FIXED_COUPON_BULLET bond, isin
  "XS0000000001" (docs/20/PR #58) -- reused as-is, no change needed.

data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT
  -- already built against bond_quote.isin == "XS0000000001" (docs/23/
  PR #63) -- reused as-is, no change needed.
```

### 8.2 What does not yet exist

**There is currently no synthetic `BondLinkedStructuredProduct` fixture
module.** `tests/test_bond_linked_structured_product.py` builds one
inline via local `_bond_option()` / `_deposit_leg()` helper functions
(not a shared, importable fixture), and — noted here as a concrete,
verified gap this doc found while inspecting the repo — that test's
`_bond_option()` helper uses `underlying_isin="US912828ZZ11"`, which
does **not** match the `"XS0000000001"` ISIN the reference-data and
market-data fixtures already use. There is today no single synthetic
`BondLinkedStructuredProduct` whose `bond_option.underlying_isin` lines
up with both existing fixtures.

### 8.3 What the next implementation slice needs to add

```text
A new, small synthetic BondLinkedStructuredProduct fixture (module
  location TBD by that slice -- e.g. products/fixtures.py, or inline in
  a future bundle fixtures module) whose bond_option.underlying_isin is
  exactly "XS0000000001", so all three existing fixtures
  (product / reference data / market data) can be combined into one
  positive BLIMVPInputBundle fixture without any of them needing to
  change. This is new fixture *content*, not a change to
  BondLinkedStructuredProduct / BondOption / DepositLeg schema code.

One positive SYNTHETIC_BLI_MVP_INPUT_BUNDLE fixture combining the three
  above, once the bundle dataclass exists (out of scope for this doc).

Negative fixture concepts for future tests (not built here, mirroring
  docs/23 §11.2's pattern):
    product referencing a different ISIN than the reference data
    reference data resolved as FOUND_INELIGIBLE (e.g. reusing the
      existing callable/zero-coupon/FRN fixtures already in
      SYNTHETIC_BOND_FIXTURES for this purpose)
    reference data resolved as NOT_FOUND
    snapshot bond_quote referencing a different ISIN than the
      reference data
    mismatched valuation_date between the bundle and the snapshot
    a snapshot variant with one required curve_purpose row removed
      (e.g. no DEPOSIT_CURVE row) -- needed for §9 test 10
    a TREASURY_FTP_REFERENCE-mode product paired with a snapshot whose
      deposit_rate_observation is None -- needed for §9 test 11
    a snapshot whose as_of_timestamp violates whatever no-look-ahead
      cutoff rule the implementation slice decides (§11) -- needed for
      §9 test 12, and cannot be built until that rule is decided
```

---

## 9. Test plan for the future implementation

Acceptance-criteria-style, for the next implementation PR to turn into
real tests — mirroring the format `docs/23` §11's test list used before
its own implementation slice.

```text
1. happy-path: bundle constructs successfully from the three existing
   fixtures + the new product fixture (§8.3).
2. exact ISIN match passes: bond_option.underlying_isin ==
   bond_reference_data.isin == market_data_snapshot.bond_quote.isin.
3. mismatched product/reference ISIN rejects: bond_option.underlying_isin
   != bond_reference_data.isin.
4. mismatched market-data/reference ISIN rejects:
   market_data_snapshot.bond_quote.isin != bond_reference_data.isin.
5. ineligible reference data rejects: resolution_status is
   FOUND_INELIGIBLE (reuse an existing ineligible fixture bond --
   callable / zero-coupon / FRN -- rather than inventing a new one).
6. not-found reference data rejects: resolution_status is NOT_FOUND.
7. product is not a BondLinkedStructuredProduct instance rejects (a
   bare BondOption or DepositLeg alone is insufficient input).
8. market data snapshot is not a BLIMarketDataSnapshot instance rejects.
9. mismatched valuation_date rejects: bundle.valuation_date !=
   market_data_snapshot.valuation_date.
10. missing required curve-purpose row rejects (§6's new gate): a
    snapshot whose curve_points omits BLICurvePurpose.DEPOSIT_CURVE (or
    BOND_REFERENCE_CURVE, or OPTION_DISCOUNT_CURVE) must block bundle
    construction, even though that same snapshot is perfectly
    internally valid on its own.
11. missing deposit-rate observation for TREASURY_FTP_REFERENCE mode
    rejects (§6's new gate): a product whose DepositLeg.deposit_rate_mode
    is TREASURY_FTP_REFERENCE, bundled with a snapshot whose
    deposit_rate_observation is None, must be rejected. A FIXED_RATE
    DepositLeg bundled with deposit_rate_observation=None must still be
    accepted (no separate rate observation is required for that mode).
12. market-data as-of look-ahead violation rejects (§6's new gate) --
    this test cannot be fully specified until the implementation slice
    decides the exact cutoff rule (§11); at minimum, once that rule is
    chosen, add a case where as_of_timestamp is after the allowed
    cutoff for valuation_date and assert construction is rejected.
13. no pricing engine invoked anywhere in these tests -- assert the
    bundle has no pv/dv01/cashflows attribute (a dataclass-fields
    boundary test, mirroring the existing pattern in
    tests/test_deposit_leg.py / tests/test_bond_reference_data.py /
    tests/test_bond_linked_structured_product.py).

Explicitly NOT duplicated by bundle tests (already covered elsewhere,
per §4.4):
    stale/invalid/missing status rejection -- already exhaustively
      tested in tests/test_bli_market_data_snapshot.py at the snapshot/
      sub-observation level; the bundle test suite should not re-test
      every BLIMarketDataStatus permutation, only that an invalid
      snapshot cannot be constructed in the first place (which is
      already guaranteed by BLIMarketDataSnapshot's own __post_init__,
      so there is nothing further for the bundle to test here beyond
      "an already-invalid snapshot object cannot exist to pass in").
    individual product-field validation (blank strings, non-finite
      numbers, date ordering) -- already covered by
      tests/test_bond_option.py, tests/test_deposit_leg.py,
      tests/test_bond_linked_structured_product.py.
    individual reference-data-field validation -- already covered by
      tests/test_bond_reference_data.py.
    resolver behavior itself (exact match, duplicate-ISIN handling) --
      already covered by tests/test_bond_reference_resolution.py.
```

---

## 10. Non-goals

Explicitly not decided or built by this doc:

```text
the MVP input bundle dataclass implementation
a bundle builder / construction helper function
a pricing engine of any kind
a payoff skeleton
cash-flow generation
a schedule engine
yield-to-price or price-to-yield calculation
curve interpolation
a volatility surface
a credit spread model
a Treasury FTP parser
market-data ingestion of any kind
a Bloomberg/API connector
a QuantLib adapter
a UI, debug viewer, or fixture viewer
```

**No frozen BLI v1.3 source spec file is edited.** `BondOption`,
`DepositLeg`, `BondLinkedStructuredProduct`, `BondReferenceData`,
`resolve_bond_reference_data`, `is_mvp_pricing_eligible`, and
`BLIMarketDataSnapshot` (and its component classes) are all unmodified.
Package exports (`products/__init__.py`, `reference_data/__init__.py`,
`data/__init__.py`) are unchanged. Issue #38 is unaffected and remains
open.

---

## 11. Open questions / implementation risks

Flagged here, not resolved, per this slice's docs-only scope — several
of these surfaced while inspecting the current code and are concrete
enough that the next implementation slice should read them before
writing the dataclass, rather than re-discovering them mid-implementation.

```text
- Fixture ISIN mismatch (verified, §8.2): the existing
  tests/test_bond_linked_structured_product.py helper builds a
  BondOption with underlying_isin "US912828ZZ11", which does not match
  the "XS0000000001" ISIN already used by both
  reference_data.fixtures.SYNTHETIC_BOND_FIXTURES and
  data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT. This is
  not a bug in either existing module (each was built independently,
  correctly, for its own slice's purpose) -- it only becomes a problem
  once something tries to combine all three, which is exactly what the
  future bundle needs. The fix is new fixture content in the next
  implementation slice (§8.3), not a change to any existing schema or
  fixture file; this doc does not make that change itself since it is
  code, not documentation.

- No product-level synthetic fixture module exists today (verified,
  §8.2): unlike reference_data.fixtures and data.bli_snapshot_fixtures,
  there is no products.fixtures (or similar) module exporting a shared,
  importable BondLinkedStructuredProduct instance -- every existing
  product test builds its own local instance inline. The next
  implementation slice will need to decide where a shared product
  fixture (if any) should live; this doc does not decide that, since it
  is a code-location choice, not a boundary decision.

- Valuation-date coherence with reference data (restated from docs/22
  §8, still open): BondReferenceData and resolve_bond_reference_data
  carry no valuation-date field of their own today (docs/21 §7.1
  concluded that point-in-time correctness of the *fixtures iterable*
  supplied to the resolver is the caller's responsibility, not the
  resolver's). This means the future bundle can validate
  bundle.valuation_date against market_data_snapshot.valuation_date
  (both exist and are explicit), but it cannot today validate
  "reference data as of this valuation date" against anything, because
  no such field exists anywhere in the reference-data layer. Whether a
  future Bond Master versioning concept is ever needed is out of scope
  here and not decided.

- Curve mapping selection (restated from docs/22 §14, still open, and
  now explicitly distinguished from §6's new curve-purpose *presence*
  gates, Codex P2 review of PR #64): §6 requires that at least one
  curve_points row of each required curve_purpose is *present*, which
  is a narrower, cheaper check than curve *selection*. This doc does
  not decide *how* a future bundle or pricing engine picks "the" one
  curve_id to actually use for a purpose when more than one otherwise-
  valid curve_id could serve it (today's fixture happens to have
  exactly one curve_id per purpose, and BLIMarketDataSnapshot already
  rejects an *ambiguous* set of different curve_ids sharing one
  currency+curve_purpose at construction time, docs/23 §12 -- but a
  real multi-source scenario with, say, two independently-valid
  currency/purpose combinations resolved through different explicit
  mappings would still need a mapping rule this doc does not design).
  This is unchanged from docs/22 §6.2/§14 and is not resolved by this
  doc.

- Market-data as-of / no-look-ahead cutoff rule (new, Codex P2 review
  of PR #64, §6): this doc now requires that a bundle reject market
  data whose as_of_timestamp violates a no-look-ahead rule relative to
  the bundle's valuation_date, but it deliberately does **not** decide
  what that rule is -- whether the check is a plain calendar-date
  comparison (as_of_timestamp's date <= valuation_date), an intraday
  cutoff time (e.g. "as of 17:00 valuation-date local time"), or a
  settlement-aware T+0/T+1 rule tied to a specific market's
  conventions. `BLIMarketDataSnapshot.as_of_timestamp` is stored as a
  plain non-blank string today (docs/23) with no required format beyond
  that, so the implementation slice must also decide (or further defer,
  explicitly) what timestamp format/parsing the bundle can rely on
  before it can even compare as_of_timestamp against valuation_date.
  This is a required policy decision for that slice to make and
  document explicitly -- not a detail a future pricing engine may
  silently interpret differently each time it runs.

- Whether resolution_status/eligibility_reasons belong on the bundle at
  all, vs. being re-derivable on demand: §7's sketch keeps them as
  fields for audit-trail symmetry with BondReferenceResolutionResult,
  but an implementation slice could reasonably decide the bundle should
  just store the BondReferenceResolutionResult object directly instead
  of unpacking it into two separate fields. Not decided here.

- Error/audit shape for a failed bundle construction (restated from
  docs/22 §11, still open): this doc's §6 states which cases block
  construction, but does not decide whether bundle construction should
  raise (mirroring resolve_bond_reference_data's
  DuplicateBondReferenceDataError-style raise-on-integrity-violation
  precedent) or return a structured result object (mirroring
  BondReferenceResolutionResult's found/not-found-without-raising
  precedent). Both patterns already coexist in this codebase for
  different reasons; the next implementation slice must pick one
  explicitly and state why, not silently default to whichever is
  easiest to write first.
```

No code change was made to resolve any of the above — they are
recorded here as the next implementation slice's starting checklist,
per this doc's docs-only scope.

---

## 12. Recommended future implementation sequence

Restated and narrowed from `docs/22` §12 / `docs/23` §14, unchanged in
overall shape, now that step 1/2 (the snapshot) is complete:

```text
1. Minimal BLI MarketDataSnapshot dataclass + synthetic fixture.
   -- COMPLETE (PR #63).
2. MVP input bundle docs/code preflight.
   -- this doc.
3. MVP input bundle dataclass.
   -- confirm §3's naming/location, §6's validation rules, and §7's
   field sketch against Annex A/B/SPEC one more time while writing the
   actual dataclass, resolving the open questions in §11 explicitly
   (state a decision and why) rather than silently picking one.
4. Add a synthetic positive bundle fixture.
   -- requires the new product fixture content scoped in §8.3 first
   (a BondLinkedStructuredProduct whose bond_option.underlying_isin is
   "XS0000000001", matching the two fixtures that already exist).
5. Add a bundle builder / construction helper.
   -- the function/class that actually runs the gates in §6 and either
   returns a valid bundle or a structured block result (§11's open
   error-shape question must be resolved here); tests only, still no
   pricing.
6. Only then: pricing engine skeleton.
   -- consumes the bundle from step 5; out of scope for every slice
   this doc recommends.
7. Only after the bundle can construct: a debug viewer / fixture viewer
   sanity-check UI.
   -- explicitly sequenced last, after step 5, not before -- a UI that
   renders a bundle before the bundle itself is trustworthy would
   invite exactly the kind of premature, ungrounded display `AGENTS.md`
   rule 4 and the "LLMs must not fabricate ... production valuations"
   rule already warn against for pricing output; the same caution
   applies to displaying an unvalidated intermediate structure as if it
   were meaningful.
```

None of steps 3-7 is started by this doc.

---

## 13. Relationship to prior docs (no re-opening)

- `docs/22` §2/§4/§5/§6/§7/§8/§9/§10: the four-layer boundary, the
  product/reference-data/market-data exclusion lists, the MVP bundle's
  conceptual field list, the quote-side/point-in-time/Treasury-FTP
  policies, and the bundle validation gate list are all restated here
  against the now-concrete `BLIMarketDataSnapshot` classes, not
  redesigned. Where `docs/22` used a placeholder ("a future
  MarketDataSnapshot"), this doc uses the real class name and its
  already-implemented validation.
- `docs/23` §2/§3/§10/§12/§16: the snapshot's scope, naming rationale,
  status vocabulary, and validation checklist are unchanged and are the
  layer-3 half of this doc's boundary — this doc does not modify
  `BLIMarketDataSnapshot` or re-decide any of its open items (e.g. the
  final status vocabulary, per-sub-observation vs. snapshot-wide status)
  remain exactly as `docs/23` left them.
- `docs/19` §3/§6/§7/§8/§9: the wrapper's ownership boundary,
  derived-only `participation_ratio`, and exclusion list are restated in
  §4.3/§4.4 above, not changed.
- `docs/20` §8, `docs/21` §5/§6/§7.1: the "missing/ineligible bond must
  block, never guess" rule and the point-in-time boundary are restated
  in §6/§9/§11 above, not changed or re-opened.
- `docs/09 (removed, see git history)` §1: the existing spine contract
  (`Product Definition + ValuationContext + MarketDataSnapshot →
  price(...) → PricingResult`) is the pattern this doc's bundle mirrors
  for BLI, restated in §2, not replaced.

---

## 14. Scope boundaries of this PR

Docs only. No `BLIMVPInputBundle` (or any other bundle name) class,
bundle builder, pricing engine, payoff skeleton, cash-flow generation,
schedule engine, yield-to-price calculation, curve interpolation,
volatility surface, credit spread model, Treasury FTP parser,
ingestion, Bloomberg/API connector, QuantLib adapter, or UI is added.
`BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
`BondReferenceData`, `resolve_bond_reference_data`,
`is_mvp_pricing_eligible`, `BLIMarketDataSnapshot`, and its component
classes are all unmodified. No frozen BLI v1.3 source spec file is
edited — this doc only reads and references Annex A/B/SPEC sections
already transcribed by `docs/17`–`docs/23`. Package exports are
unchanged. No test file is added or modified by this doc. Issue #38 is
unaffected and remains open.
