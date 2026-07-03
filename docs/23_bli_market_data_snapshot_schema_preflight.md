# 23 BLI `MarketDataSnapshot` Schema Preflight

Status: docs-only preflight. No `MarketDataSnapshot` class, MVP input
bundle class, bundle builder, pricing engine, payoff skeleton, cash-flow
generation, schedule engine, yield-to-price calculation, curve
interpolation, volatility surface, credit spread model, Treasury FTP
parser, ingestion, Bloomberg/API connector, QuantLib adapter, UI,
screenshot/OCR capture, product-schema change, or reference-data
resolver change is added by this doc.

## 1. Why this doc exists

`docs/22_bli_market_data_input_bundle_preflight.md` (PR #61) defined the
*conceptual* boundary between product terms, reference data, market
data, and the future MVP input bundle, and recommended (§12) that the
very next slice be a schema preflight for the BLI-scoped
`MarketDataSnapshot` itself — narrower and more concrete than `docs/22`,
but still not code. This doc is that narrowing step, following the same
"preflight before code" pattern already used for `DepositLeg`
(`docs/18`), `BondReferenceData` (`docs/20`), ISIN resolution
(`docs/21`), and `docs/22` itself.

This doc does not implement anything. It exists so the *next* PR can
implement the smallest useful `MarketDataSnapshot` dataclass and a
synthetic fixture, per `docs/22` §12 step 2, without having to make
schema-shape decisions ad hoc while writing code.

---

## 2. Purpose and boundary

A future BLI `MarketDataSnapshot` is:

```text
A frozen, explicit, point-in-time container of market observations and
curve inputs for one BLI valuation context.
```

It must answer:

```text
What market data did we use for this valuation context?
```

It must **not** answer:

```text
What product did we trade?            -- BondOption / DepositLeg /
                                          BondLinkedStructuredProduct
What bond did the issuer promise?     -- BondReferenceData
Is the bond eligible?                 -- resolve_bond_reference_data /
                                          is_mvp_pricing_eligible
What is the price/PV?                 -- a future PricingResult
```

This restates `docs/22` §2's four-layer boundary at the granularity of
a single class: the snapshot is layer 3 only. Nothing in this doc moves
a field into or out of layers 1, 2, or 4 — those boundaries are already
decided and are not re-opened here.

---

## 3. Proposed module location

Three options, evaluated:

### 3.1 Option A — extend `src/shiori_pricing_lab/data/snapshot.py`

Add BLI fields directly onto the existing vanilla-rates-core
`MarketDataSnapshot` (`docs/02`, DataFrame-of-rates-points-based).
**Rejected.** The existing class's shape (`valuation_date`, `source`, a
single `_rates_points` DataFrame, `metadata`) is structurally
unrelated to what a BLI snapshot needs (an explicit bond quote, a small
set of named curves by purpose, an FTP observation, a volatility input,
a credit spread input). Bolting BLI-specific optional fields onto the
existing class would make every non-BLI consumer of
`data.snapshot.MarketDataSnapshot` carry irrelevant fields, and would
violate the same "do not blur an existing, working boundary by adding
one convenience field" caution `docs/22` §4 already applies to product
schemas and reference data.

### 3.2 Option B — new top-level package, `src/shiori_pricing_lab/market_data/`

Mirrors the precedent PR #58 set for `reference_data/`: a new sibling
package when the concept is genuinely distinct from what an existing
package owns. **Considered, not recommended.** Unlike Bond Reference
Data (which is explicitly *not* market data and needed its own
package specifically to avoid being confused with `products/`), a BLI
market-data snapshot *is* market data — and `AGENTS.md` rule 2 already
designates `src/shiori_pricing_lab/data/` as the one place
Bloomberg/API/market-data code may live. Creating a second top-level
package for market data would fragment that existing rule rather than
extend it.

### 3.3 Option C (recommended) — new module inside the existing `data/` package

```text
src/shiori_pricing_lab/data/bli_snapshot.py
```

with a **distinctly named** class, e.g. `BLIMarketDataSnapshot` (not
`MarketDataSnapshot` — see §3.4), so it never collides with or is
mistaken for the existing vanilla-rates-core class. This keeps market
data inside the package `AGENTS.md` rule 2 already designates for it,
follows the same "small, focused module per concept" pattern already
used inside `data/` (`providers.py` vs. `snapshot.py` are already
separate concerns), and avoids inventing a new top-level package for a
concept that already has a natural home.

### 3.4 Naming: why not reuse the name `MarketDataSnapshot`

`src/shiori_pricing_lab/data/snapshot.py` already exports a class named
`MarketDataSnapshot`. Reusing that exact name for a structurally
different BLI-scoped object in a different module is legal Python (two
modules can each define a same-named class) but is a foreseeable source
of import confusion (`from shiori_pricing_lab.data.snapshot import
MarketDataSnapshot` vs. `from shiori_pricing_lab.data.bli_snapshot
import MarketDataSnapshot` silently importing the wrong one). This doc
recommends a distinct name — `BLIMarketDataSnapshot` — for the future
class, the same way `BondResolutionStatus` was kept distinct from
`PricingStatus` (`docs/21`) and `Position` was kept distinct from
`BuySell` (`docs/14`/PR #45) even though each pair could plausibly share
a name. **This is a naming recommendation only; the implementation
slice may pick a different name if it states a reason**, but must not
silently reuse `MarketDataSnapshot` for a different shape.

**No module is created by this doc.**

---

## 4. Minimal conceptual fields

Grounded in `docs/22` §3/§6 and the frozen Annex B §B.1/§B.2 field
lists, organized by sub-observation. **No dataclass is implemented
here** — field names below are a proposal for the next implementation
slice to confirm, not a binding schema.

### 4.1 Snapshot-level fields

```text
business_date / valuation_date / pricing_date
as_of_timestamp
source_system
snapshot_id or source_fixture_name / audit label
status / data_quality_status
```

### 4.2 Bond quote

```text
isin
currency
clean_price_per_100 and/or yield
accrued_interest_per_100, if available/required
price_type / quote_side
bond_quote_status
bond_quote_source_system
```

### 4.3 Curves

```text
curve_id
curve_name
currency
curve_type / curve_purpose
tenor
rate
day_count
compounding
interpolation_method
curve_source_system
curve_status
```

A snapshot conceptually carries **a small collection of curve records**,
one or more per curve purpose (§7), not a single curve — `docs/22` §6.2
already requires the Bond Reference Curve, Option Discount Curve,
Deposit Curve, and (if mapped) Funding Curve to be distinguishable, so
the future dataclass needs a keyed or list-of-records shape here, not
one flat `curve_rate` field.

### 4.4 Deposit / FTP observation

```text
currency
tenor
quote_side
ftp_rate_percent_value
ftp_rate_decimal_value (or an explicit conversion rule, §5)
ftp_source_system
ftp_status
```

### 4.5 Option volatility

```text
volatility
volatility_basis
vol_source_system
vol_status
vol_override_or_fallback_audit, if any
```

### 4.6 Credit spread

```text
credit_spread
credit_spread_basis
spread_source_system
spread_status
spread_treatment
spread_override_or_fallback_audit, if any
```

---

## 5. Percent vs. decimal policy

Restated from `docs/18` §2.2 and `docs/22` §9, made concrete for the
future schema's field shape:

```text
Treasury FTP source values are percentages: 3.5500 means 3.5500%,
  decimal 0.0355 -- never treat 3.5500 as decimal 3.55.
```

**Recommendation:** store **both** the raw observed value and the
normalized decimal value as two explicit fields
(`ftp_rate_percent_value` / `ftp_rate_decimal_value`, §4.4), rather than
one ambiguous `rate` field plus an implicit conversion buried in a
formatting layer. This matches `docs/18` §2.1's own recommendation for
the normalized FTP record shape ("`rate_percent` and `rate_decimal` are
both listed deliberately... a normalized record should carry both
rather than force every downstream consumer to convert") and is
preferred over the alternative (one canonical decimal field plus a raw
audit string) because it keeps the conversion **visible in the schema
itself** — a reviewer or test can compare the two fields directly
(`percent / 100 == decimal`) rather than trusting an opaque audit blob.
**This is a recommendation for the next implementation slice to adopt
or explicitly override, not a binding decision.**

---

## 6. Quote side and price type

Restated from `docs/18` §5 and `docs/22` §7, as concrete expectations on
the future schema:

```text
Quote side must be explicit -- never silently chosen.
Do not silently choose BID / OFFER / MID.
MID default may exist only if explicitly configured and recorded.
A single-rate FTP observation may be treated as MID-equivalent only if
  that normalization is explicitly documented (docs/18 §2.4), never
  silently assumed.
Bond price/yield price_type must be explicit (per Annex B §B.1's own
  price_type field).
Source and status must be recorded for every sub-observation (§4.2-4.6
  each carry their own *_source_system / *_status fields, deliberately
  not one snapshot-wide field, since a bond quote, a curve, an FTP
  rate, a vol input, and a spread input can each come from a different
  system with a different status).
```

---

## 7. Curve purpose separation

Restated and operationalized from `docs/22` §6.2 (itself grounded in
frozen SPEC §3.5/§7.3):

```text
Future snapshot / curve representation must keep separate:
  Bond Reference Curve
  Option Discount Curve
  Deposit Curve
  Funding Curve, if applicable
```

Rules, restated verbatim from the frozen spec (not invented here):

- **Option Discount Curve and Bond Reference Curve must not be mixed**
  (SPEC §3.5).
- **The deposit leg must not silently reuse the Option Discount Curve**
  unless an explicit mapping rule says so (SPEC §3.5).
- **Curve purpose must be carried explicitly on each curve record**
  (the `curve_type` / `curve_purpose` field, §4.3) — never inferred from
  currency alone. Two curves in the same currency serving different
  purposes (e.g. USD Bond Reference Curve vs. USD Option Discount
  Curve) are different records, not the same record reused.
- **Missing curve data or invalid curve data blocks future bundle
  creation** (SPEC §7.3, restated in `docs/22` §5.1/§10) — this is a
  bundle-layer gate, not a snapshot-construction-time rule; the
  snapshot itself may legitimately be missing a curve purpose it was
  never given (e.g. a snapshot built before Funding Curve mapping was
  resolved), and it is the future bundle builder's job to check
  "does this snapshot have what this specific product needs," not the
  snapshot's job to know in advance what every possible product needs.

---

## 8. Volatility policy

Restated from `docs/22` §6.5 (itself grounded in frozen SPEC
§§3.2/3.3/7.4):

```text
Volatility input / used volatility must be explicit.
Volatility basis must be explicit (YIELD_VOL / PRICE_VOL /
  EQUIVALENT_PRICE_VOL, SPEC §7.4's vocabulary, transcribed for
  context here, not re-derived or extended).
No invented volatility.
No silent default volatility.
No silent flat-vol fallback (SPEC §3.3 point 5).
Any manual override or fallback must be audit-recorded
  (vol_override_or_fallback_audit, §4.5).
If a yield-vol-to-price-vol conversion is used later, it must be
  recorded as a conversion (basis, mode, formula version) -- SPEC §3.3
  point 4 -- never silently substituted as if it were an observed
  price vol.
Missing required volatility blocks future bundle construction
  (docs/22 §5.1/§10 gate 8) -- not a snapshot-construction-time
  requirement, for the same "snapshot may legitimately not have
  everything every product needs" reasoning as §7 above.
```

**No volatility surface, vol interpolation, or yield-vol-to-price-vol
conversion is designed or implemented by this doc.** §4.5's field list
is a proposal for a single scalar volatility observation (plus its
basis and audit trail) sufficient for the MVP's synthetic fixture (§10)
— a real vol surface (by expiry tenor and strike/moneyness, per Annex B
§B.3) is out of scope for the MVP snapshot and is not designed here.

---

## 9. Credit spread policy

Restated from `docs/22` §6.6 (itself grounded in frozen SPEC §7.5):

```text
Credit spread must not silently default to zero.
If credit spread is required by the selected mapping/methodology and
  missing, future bundle construction must block (not a
  snapshot-construction-time requirement, same reasoning as §7/§8).
If spread is already embedded in a selected bond quote or curve
  methodology (so no separate spread input is needed), that must be an
  explicit statement made by the future implementation slice that
  designs the actual bundle/snapshot -- this doc does not assume
  embedding either way.
"Not required / embedded / not applicable" must be an audited decision
  (spread_treatment + spread_override_or_fallback_audit, §4.6), never a
  silent assumption.
Manual override or proxy fallback (down SPEC §7.5's priority chain:
  bond-specific -> issuer -> rating/sector proxy -> manual override)
  must be audit-recorded, never applied silently.
```

**No spread model, spread mapping table, or spread-to-price adjustment
is designed or implemented by this doc.** §4.6's field list is a
proposal for a single scalar spread observation (plus its basis,
treatment, and audit trail) sufficient for the MVP's synthetic fixture
— a full spread mapping table (by rating/sector/issuer, per Annex B
§B.4) is out of scope for the MVP snapshot and is not designed here.

---

## 10. Status vocabulary

`docs/22` §10 gate 11 and §14 explicitly left "the acceptable-status
vocabulary" as an open item for this doc to at least frame, if not
finalize. This doc frames it, without inventing a broad production
status system:

**Possible minimal statuses**, modeled on the existing `BondStatus`
(`ACTIVE`/`INACTIVE`, `docs/20`) precedent of "small, MVP-sufficient,
extend later":

```text
ACTIVE
STALE
INVALID
MISSING
MANUAL_VERIFIED
```

`ACTIVE` is the only status that should permit bundle construction
(§14 leaves the exact rule to the implementation slice, but the default
posture — consistent with `docs/22` §5.1/§10's "no stale/inactive/
invalid data" rule — is that `STALE`/`INVALID`/`MISSING` block, and
`MANUAL_VERIFIED` is treated as acceptable only if it carries the
manual-rate audit metadata `docs/18` §4.3 already requires for that
mode).

**What must be decided in the implementation PR, not here:**

```text
whether this five-value list is the final vocabulary, or whether Annex
  B's own per-file status fields (§B.1-§B.4, not transcribed in this
  doc) imply a different or larger set;
whether status is one snapshot-wide field or (per §4.2-4.6's
  recommendation above) a separate field per sub-observation -- this
  doc recommends per-sub-observation status, consistent with each
  sub-observation also carrying its own source_system;
whether MANUAL_VERIFIED is a status value or a separate boolean/flag
  alongside an ACTIVE-equivalent status -- both are defensible, this
  doc does not pick one.
```

**Non-acceptable status blocks future bundle creation** — this
principle is fixed by `docs/22` §5.1/§10; only the exact vocabulary is
open.

---

## 11. Synthetic fixture scope (for the next implementation PR)

Per `docs/22` §12 step 2 and the existing `BondReferenceData` /
`SYNTHETIC_BOND_FIXTURES` precedent (PR #58): the next implementation
PR should add a small, manually reviewed, deterministic synthetic
fixture — not a parser, not ingestion.

### 11.1 Minimal positive fixture (one complete, eligible BLI path)

```text
one valuation date
one resolved eligible ISIN                (reuse an existing
                                            SYNTHETIC_BOND_FIXTURES
                                            entry, e.g. the eligible
                                            FIXED_COUPON_BULLET bond,
                                            docs/20/PR #58)
one bond quote for that ISIN
one Bond Reference Curve
one Option Discount Curve
one Deposit Curve                          (Codex P2 review of PR #62:
                                            required regardless of
                                            deposit_rate_mode, for the
                                            deposit leg's own
                                            discounting/funding
                                            calculation when the future
                                            methodology requires it --
                                            see the note below; not a
                                            substitute for the
                                            deposit-rate input below,
                                            and not substituted by it)
plus one deposit-rate input matching the synthetic DepositLeg's
  own deposit_rate_mode (docs/18 §4) -- exactly one of:
    - FIXED_RATE: the rate is already on DepositLeg itself; no separate
      market-data input is needed for the rate value (it is a deal
      term, docs/18 §4.1), but the Deposit Curve above is still
      required for discounting.
    - TREASURY_FTP_REFERENCE: one matching FTP observation resolving
      the leg's ftp_rate_selector (currency/tenor/quote_side, docs/18
      §4.2) -- this FTP observation resolves the *rate*, it does not
      replace the Deposit Curve, which is a separate discounting input.
    - MANUAL_VERIFIED_RATE: one manual verified rate audit record
      resolving the leg's manual_input_reference (docs/18 §4.3).
one explicit volatility input
one explicit credit spread treatment       (either a value, or an
                                            explicit audited
                                            not-required/embedded
                                            decision, §9)
source/status fields on every sub-observation, all ACTIVE
```

### 11.2 Negative fixture concepts (for future tests, not built here)

```text
missing bond quote
missing curve (any one of the required purposes)
missing FTP/deposit rate
missing volatility
missing credit spread where required
stale/invalid status (on any sub-observation)
ambiguous quote side
```

**No fixture is created by this doc.** This section only scopes what
the next implementation PR's fixture module should contain, mirroring
how `docs/20` §7 scoped `SYNTHETIC_BOND_FIXTURES` before PR #58 built
it.

---

## 12. Validation rules for future implementation

Acceptance-criteria-style checklist for the next implementation PR,
mirroring how `docs/18` §10 and `docs/20` §10 preceded `DepositLeg` and
`BondReferenceData`. **None of these are implemented here.**

```text
required dates/timestamps (business_date/valuation_date, as_of_timestamp)
  must be non-blank, explicit strings -- reuse the existing
  _parse_iso_date pattern where a calendar date is expected.
no system date fallback -- never date.today()/datetime.now() anywhere
  in this module, per the existing repo-wide invariant (docs/09 §3).
curve records are tenor/rate rows, not one row per curve (Codex P2
  review of PR #62 -- corrected from an earlier, too-broad version of
  this rule): Annex B §B.2 models a curve as multiple tenor nodes (e.g.
  1Y/2Y/5Y/10Y) sharing the same `currency` + `curve_purpose`, so
  repeated `currency` + `curve_purpose` values across records are
  **expected and valid**, not duplicates. Duplicate detection must
  instead be keyed at the curve-node level -- conceptually
  `business_date`/`valuation_date` + `curve_id`/`curve_name` +
  `currency` + `curve_purpose` + `tenor` (+ `source_system`/version if
  more than one source or version could otherwise collide). Future
  implementation must reject:
    - a duplicate tenor row within the same curve identity (two rows
      claiming the same curve_id/curve_name + tenor for the same
      valuation context);
    - conflicting rates for the same curve identity + tenor + valuation
      context (two rows agreeing on identity and tenor but disagreeing
      on rate);
    - ambiguous multiple curve IDs claiming the same curve_purpose for
      the same currency/valuation context without an explicit mapping
      rule to choose between them (§7's "curve purpose must be carried
      explicitly... never inferred from currency alone" already
      implies this, restated here as a validation rule).
  None of these three cases may be silently resolved by "use whichever
  row is first/last" (same no-silent-first/last-match principle as the
  duplicate-ISIN rejection already implemented in
  resolve_bond_reference_data, docs/21 §4, PR #60) -- reject outright or
  require an explicit selection rule.
no negative clean price (reuse _require_finite_number + positivity,
  same pattern as BondOption.notional / BondReferenceData.
  redemption_amount).
finite rates / volatility / spread (reuse _require_finite_number;
  yield and spread may be signed, same reasoning already applied to
  BondOption.strike_yield and DepositLeg.fixed_deposit_rate -- no blanket
  sign constraint on every numeric field).
FTP percent-to-decimal conversion must be explicit and internally
  consistent if both fields are stored (§5) -- e.g. validate
  abs(percent / 100 - decimal) is within a small tolerance, rejecting a
  record where the two disagree, rather than silently trusting one over
  the other.
exact ISIN matching with the resolved bond -- the snapshot's bond-quote
  isin must equal the isin resolve_bond_reference_data resolved, not a
  fuzzy or prefix match (same exact-match-only rule as docs/21 §4).
quote side explicit for every quoted sub-observation that has one
  (bond quote, FTP observation) -- never silently defaulted at
  construction without recording which side.
source and status non-blank for every sub-observation present.
no stale/invalid data accepted at construction unless a future,
  explicit policy allows it (§10) -- what "stale" means (an age
  threshold vs. a status flag) is left to the implementation slice.
no silent volatility or credit-spread fallback -- if an override/
  fallback value is stored, its corresponding audit field
  (vol_override_or_fallback_audit / spread_override_or_fallback_audit,
  §4.5/§4.6) must be non-blank; a non-blank override value with a blank
  audit field must be rejected, not silently accepted.
```

---

## 13. Relationship to the input bundle

Restated from `docs/22` §5, because this doc's narrower focus on the
snapshot alone could otherwise read as if the snapshot *is* the bundle:

```text
MarketDataSnapshot (this doc's subject) is NOT the MVP input bundle.
The snapshot contains market observations for one valuation context.
```

The future input bundle combines:

```text
one BondLinkedStructuredProduct
resolved BondReferenceData
resolver / eligibility result
one BLIMarketDataSnapshot                  (this doc's subject)
explicit curve mappings                    (which curve_id serves which
                                            purpose for which product --
                                            a mapping-table concern, not
                                            a snapshot-content concern)
explicit assumptions / validation results
```

**This PR does not design the input bundle class in detail** — `docs/22`
§5/§10 already covers the bundle's own validation gates at the
conceptual level, and this doc does not restate or expand that design.
This doc's scope is the snapshot only.

---

## 14. Recommended future implementation sequence

Restated from `docs/22` §12, unchanged, since this doc is a narrowing of
step 1, not a replacement for the sequence:

```text
1. Minimal BLI MarketDataSnapshot dataclass + synthetic fixture.
   -- this doc's direct follow-up: confirm §3's module location and
   naming, §4's field list, and §12's validation rules against Annex B
   / SPEC one more time while writing the actual dataclass, then add
   the fixture scoped in §11.
2. MVP input bundle docs/code preflight.
3. MVP input bundle dataclass.
4. Bundle builder combining product + resolver + synthetic snapshot.
5. Pricing engine skeleton.
```

None of steps 1-5 is started by this PR.

---

## 15. Relationship to prior docs (no re-opening)

- `docs/02_data_and_market_snapshots.md`: the existing vanilla-rates-core
  `MarketDataSnapshot` and its "snapshot identity" field list
  (`snapshot_id`, `valuation_date`, timestamp, `source`, `creation
  timestamp`, `data version`, `quality flags`, `notes`) is the precedent
  §4.1's snapshot-level fields draw from; this doc does not modify that
  existing module (§3.1 explains why extending it directly is rejected).
- `docs/22` §3/§5/§6/§7/§8/§9/§10: every field list, policy, and gate
  this doc restates is unchanged from `docs/22` — this doc only narrows
  "what a future implementation must confirm" into a more concrete
  schema shape and module-location recommendation. Where `docs/22` used
  a rule (e.g. "no silent flat-vol fallback"), this doc restates it as
  a schema-field consequence (e.g. "a non-blank override value with a
  blank audit field must be rejected"), not a new rule.
- `docs/18`, `docs/20`, `docs/21`: the percent-vs-decimal rule, the
  exact-ISIN-match precedent, and the duplicate-detection precedent
  this doc reuses for §5/§12 are unchanged, not re-derived.

---

## 16. Deferred items

Explicitly not decided or built by this doc:

- **The `BLIMarketDataSnapshot` class itself** — exact fields, whether
  curves are a list/dict/named-tuple-of-purposes, and validation are
  choices for implementation slice 1 (§14), not fixed here beyond §4's
  conceptual list and §12's checklist.
- **The final module name and location**, if the implementation slice
  finds a reason to deviate from §3.3/§3.4's recommendation.
- **The acceptable-status vocabulary**, final form (§10) — this doc
  proposes a five-value starting set and frames the open questions, but
  does not finalize it.
- **Whether status lives per-sub-observation or snapshot-wide** (§10) —
  this doc recommends per-sub-observation but leaves it open.
- **The concrete volatility-basis vocabulary and vol-hierarchy
  fallback-selection logic**, and **the concrete credit-spread mapping
  priority-chain logic** — restated from `docs/22` §14, still not
  designed here; §4.5/§4.6/§8/§9 only fix the field shape and the
  no-silent-fallback rule.
- **The MVP input bundle class and its bundle builder** — `docs/22`
  §5/§10, slices 3/4 (§14).
- **The `DayCount` vocabulary decision (A-14)** and **`docs/14` F-08**
  (`m`/compounding-frequency gap) — unrelated to this doc, carried
  forward unresolved.

---

## 17. Scope boundaries of this PR

Docs only. No `MarketDataSnapshot`/`BLIMarketDataSnapshot` class, MVP
input bundle class, bundle builder, pricing engine, payoff skeleton,
cash-flow generation, schedule engine, yield-to-price calculation, curve
interpolation, volatility surface, credit spread model, Treasury FTP
parser, ingestion, Bloomberg/API connector, QuantLib adapter, UI,
screenshot/OCR capture, product-schema change, or reference-data
resolver change is added. `BondOption`, `DepositLeg`,
`BondLinkedStructuredProduct`, `BondReferenceData`, and
`resolve_bond_reference_data` are all unmodified. No frozen BLI v1.3
source spec file is edited — this doc only reads and transcribes Annex B
§B.1-§B.4 and SPEC §§3.2/3.3/3.5/7.3/7.4/7.5, already transcribed once
before by `docs/18`/`docs/20`/`docs/22`. Issue #38 is unaffected and
remains open.
