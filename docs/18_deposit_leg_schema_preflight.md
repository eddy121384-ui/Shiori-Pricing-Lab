# 18 DepositLeg Schema Preflight (BLI MVP Slice A)

Status: docs-only preflight. No `DepositLeg` code, no Treasury FTP parser,
no ingestion, no `BondLinkedStructuredProduct`, no pricing engine, and no
tests are added by this doc.

## 0. Why this doc exists

`docs/17_bli_mvp_vertical_slice_preflight.md` §11 names "Slice A: DepositLeg
schema preflight / implementation" as the first future BLI MVP slice, and
§4 of that doc explicitly left the deposit rate/yield source undecided. This
doc is that preflight. It also incorporates the real Treasury FTP rate
matrix format the desk uses (§2), which was not available when `docs/17` was
written, so this doc goes further than a restatement of `docs/17` — it
resolves the shape of the rate-source question in enough detail that a
future implementation slice can be scoped safely.

This doc does not implement `DepositLeg`, a Treasury FTP parser, ingestion,
the wrapper, or a pricing engine. It resolves the minimum economic and
schema boundary so that future code does not have to make these decisions
ad hoc.

---

## 1. What is `DepositLeg` in the BLI MVP?

`DepositLeg` is the deposit / funding / principal-return component of a
Bond Linked Structured Product — the "what does the customer get back, and
under what terms" side of the trade, as distinct from the `BondOption` leg
(the option payoff side). Per SPEC §6.1.1, it carries deposit notional,
currency, dates, a rate/yield, and a principal repayment rule.

It is a **deal-term schema component consumed by the future
`BondLinkedStructuredProduct` wrapper**, not a standalone pricing product.
Nothing in `docs/04` (product definition schema), `docs/14`, or `docs/15`
treats a deposit leg as a tradeable product in its own right — it only has
meaning embedded in the wrapper, the same way `FixedLeg` / `FloatingLeg`
only have meaning embedded in `InterestRateSwap` / `OvernightIndexedSwap`
(`src/shiori_pricing_lab/products/legs.py`). A future `DepositLeg`
implementation should follow that existing repo pattern: a small,
frozen, embeddable dataclass, not a schema with its own `product_type`
discriminator or its own pricing-engine registration.

---

## 2. Treasury FTP rate matrix interpretation

The observed Treasury FTP sheet is a rate matrix:

```text
business_date × currency × tenor × quote_side → rate
```

### 2.1 Minimum normalized fields

```text
business_date
currency
tenor
quote_side
rate_percent
rate_decimal
source_file_name
as_of_timestamp or loaded_at
```

This is a normalized *record shape* for a future parser to target — no
parser is implemented here. `rate_percent` and `rate_decimal` are both
listed deliberately: the sheet's native unit is percent (§2.2), and pricing
code needs decimal, so a normalized record should carry both rather than
force every downstream consumer to convert. `source_file_name` and
`as_of_timestamp`/`loaded_at` exist for the audit trail
(`docs/17` §10) — a rate that entered the system must be traceable back to
which file and when.

**This normalized record belongs to `MarketDataSnapshot` / an MVP input
bundle / the audit-provenance layer — it does not belong to `DepositLeg`.**
In particular, `business_date` is a **valuation-run-specific** value (which
day's FTP sheet applied), not a fixed deal term, so it must never be frozen
into the immutable product schema. `DepositLeg` may only carry a *stable
selector* (currency/tenor/quote_side, §4.2) that says "look up Treasury FTP
for this shape"; the dated record that answers that lookup for a specific
pricing run is chosen from the market/funding-data snapshot at pricing
time, not stored on the trade.

### 2.2 Percent-vs-decimal rule

- The sheet quotes rates as **percentages**: `USD O/N = 3.5500` means
  `3.5500%`, i.e. `0.035500` in decimal form — **not** `3.5500` used
  directly as a rate.
- **Pricing code must use the decimal form (`0.035500`), never the raw
  percent number.** Any future parser or `DepositLeg` field that stores a
  rate for pricing purposes must store or convert to decimal explicitly,
  with the conversion visible in code (divide by 100), not implicit or
  buried in a formatting layer.
- A normalized record should keep `rate_percent` (as observed in the
  source, for audit/display) and `rate_decimal` (as used for pricing) as
  two explicit fields rather than one ambiguous `rate` field.

### 2.3 Tenor vocabulary observed

```text
O/N, 1W, 2W, 3W, 1M, 2M, 3M, 6M, 9M, 1Y, 2Y, 3Y, demand/savings (if applicable)
```

**As of the controlled-vocabulary slice after this preflight,
`TreasuryFTPTenor` exists in `src/shiori_pricing_lab/products/enums.py`
(alongside `DepositRateMode` and `TreasuryFTPQuoteSide`) and is the
approved FTP tenor vocabulary for the observed labels above.** This closes
the tenor-vocabulary gap **at the vocabulary level only**. `Frequency` in
`enums.py` remains a payment/reset period vocabulary —
`DAILY`/`MONTHLY`/`QUARTERLY`/`SEMI_ANNUAL`/`ANNUAL` — and is not a tenor
label set; it must **not** be reused as FTP tenor vocabulary, since it does
not include `O/N`, `1W`, `2W`, `3W`, `9M`, or `2Y`/`3Y`, and reusing it
would silently misrepresent a Treasury FTP tenor as a payment frequency.
Unsupported variants such as `ON` (missing the slash) versus the canonical
`O/N` must be rejected, not accepted or silently normalized.

**This does not add a `DepositLeg` schema, a Treasury FTP parser, FTP
ingestion, a `MarketDataSnapshot` implementation, or an MVP input-bundle
implementation** — those remain deferred to later slices (§12). Only the
tenor (and quote-side, and deposit-rate-mode) vocabulary a future
`DepositLeg` schema needs to validate against now exists.

### 2.4 Quote side

- Some currencies publish `Offer` / `Mid` / `Bid` rows; some publish only a
  single rate.
- Allowed quote sides: `BID`, `MID`, `OFFER`.
- Default quote side is `MID`; the default must be configurable (§5).
- Where a currency has no bid/mid/offer breakdown, the single available
  rate is treated as **MID-equivalent** for MVP — this is a documented
  normalization, not a silent assumption (§5).
- **Do not infer `BID`/`OFFER` for a currency the sheet does not provide
  them for.** If only a MID-equivalent rate exists, only `MID` is
  available; a future parser must not synthesize a spread.

### 2.5 What is explicitly not built here

No Treasury FTP parser, file reader, or ingestion code is implemented in
this PR. This section only fixes the target record shape and unit
conventions so that a future parser (and a future `DepositLeg` reference
field, §3) has a reviewed shape to build against, consistent with
`docs/16`'s file-minimal / API-first direction — this is still a manual
or MVP-input-bundle-supplied source, not a general file-import system.

---

## 3. Minimum `DepositLeg` fields for MVP

| Field | Classification | Notes |
| --- | --- | --- |
| `deposit_leg_id` | Required for MVP | Non-blank identifier, same pattern as `product_id` (`_require_non_blank`). |
| `deposit_notional` | Required for MVP | Positive, finite number — same pattern as `BondOption.notional` (`_require_finite_number` + positivity, PR #50's Codex-P2 fix). |
| `currency` | Required for MVP | Existing `Currency` enum, coerced via `coerce_enum`. |
| `start_date` | Required for MVP | Strict `YYYY-MM-DD` via existing `_parse_iso_date`. |
| `maturity_date` | Required for MVP | Strict `YYYY-MM-DD`; must be after `start_date`. |
| `tenor` | Optional for MVP | Useful for `TREASURY_FTP_REFERENCE` lookups (§4.2) and audit/display; derivable from `start_date`/`maturity_date` in principle, but storing it explicitly avoids recomputing a tenor label from dates. Depends on the tenor-vocabulary gap in §2.3. |
| `deposit_rate_mode` | Required for MVP | Controlled vocabulary: `FIXED_RATE` / `TREASURY_FTP_REFERENCE` / `MANUAL_VERIFIED_RATE` (§4). |
| `fixed_deposit_rate` | Required only when `deposit_rate_mode == FIXED_RATE` | Decimal rate, finite-number validated; must be `None` otherwise. |
| `ftp_rate_selector` | Required only when `deposit_rate_mode == TREASURY_FTP_REFERENCE` | A **stable selector** (currency/tenor/quote_side, and optionally a `source_policy`/`curve_family` if needed later) — **not** a `business_date` and not the rate itself. See §4.2 and §8. Must be `None` otherwise. |
| `manual_input_reference` | Required only when `deposit_rate_mode == MANUAL_VERIFIED_RATE` | A **reference marker only** (e.g. an identifier pointing at the manual pricing-input record). The actual manual rate and its audit metadata (source, as-of, entered-by, run id) live outside `DepositLeg` — see §4.3. Must be `None` otherwise. |
| `principal_repayment_rule` | Required for MVP | Controlled vocabulary (§6). |
| `day_count` | Deferred | Blocked by the unresolved Issue #37 `DayCount` vocabulary decision (A-14) — see §7. |
| `business_day_convention` | Deferred | Same blocker as `day_count`; also needs a calendar to resolve, which is out of MVP scope. |
| `calendar` | Deferred | No calendar engine exists in the repo; out of MVP scope (§7). |

**Boundary:** `DepositLeg` may store a *stable selector* pointing at the
Treasury FTP rate source (`ftp_rate_selector`: currency/tenor/quote_side,
never `business_date`) but must **not** embed the FTP rate table, a rate
history, a specific dated rate record, or any other market/funding-data
payload. This mirrors the existing `docs/04`/`docs/16` rule that a product
schema may point at a rate *shape* but must not carry the rate matrix, a
specific business-date's value, or any other snapshot-specific data —
those belong to `MarketDataSnapshot` / the MVP input bundle, resolved at
pricing/revaluation time so that replay and scenario pricing can choose a
different as-of date without mutating the immutable trade record.

---

## 4. Deposit rate / yield source decision

Three options, evaluated as requested by `docs/17` §4 and §12:

### 4.1 Option A — `FIXED_RATE`

- **Schema fields needed:** `fixed_deposit_rate` (decimal, finite,
  sign-unconstrained — a funding rate can in principle be negative, same
  reasoning as `BondOption.strike_yield`, PR #50).
- **Validation required:** required iff `deposit_rate_mode == FIXED_RATE`;
  must be `None` for the other two modes (mutual exclusivity, §11).
- **What belongs outside the schema:** nothing — this is, by definition, a
  frozen trade term, so it is fully schema-resident, like
  `FXSwap.near_rate`.
- **Pros:** simplest to validate; no external lookup; fully reproducible
  from the trade record alone.
- **Cons:** does not reflect how Treasury FTP funding actually works if the
  desk's real convention is "rate resolved from the funding curve at
  trade/valuation time," not a bespoke negotiated number.
- **Risk of silent methodology drift:** low — the number is frozen at
  construction, nothing resolves it later.
- **MVP suitability:** good for a synthetic/test MVP example, but may not
  reflect real desk usage if deposit rates are always Treasury-FTP-sourced
  in practice.

### 4.2 Option B — `TREASURY_FTP_REFERENCE`

- **Schema fields needed:** `ftp_rate_selector` — a structured, **stable**
  selector (`currency`, `tenor`, `quote_side`, and optionally
  `source_policy` / `curve_family` if a later design needs to distinguish
  multiple FTP curve families for the same currency/tenor). **No
  `business_date` field.** A `business_date` (or `as_of`) is
  valuation-run-specific, not a trade term: which day's FTP sheet applies
  is a property of a *pricing run* (today's valuation, a historical
  replay, a scenario), not of the deposit leg itself. Freezing a
  `business_date` into `DepositLeg` would make the trade record itself
  change meaning depending on when it was priced, or would require a new
  `DepositLeg` instance per valuation date — both wrong. `business_date`,
  `as_of_timestamp`, `source_file_name`, `loaded_at`, and the resolved
  rate value all belong to `MarketDataSnapshot`, the MVP input bundle, or
  the audit/provenance layer (§2.1), never to the product schema.
- **Validation required:** required iff `deposit_rate_mode ==
  TREASURY_FTP_REFERENCE`; `currency` must match the deposit leg's own
  `currency` (no silent cross-currency lookup); `tenor` must map to a
  controlled FTP tenor vocabulary (§2.3) — **this vocabulary must exist
  before `TREASURY_FTP_REFERENCE` is enabled**, not validated with a
  placeholder check (§12); `quote_side` must be one of `BID`/`MID`/`OFFER`,
  defaulting to `MID` if unspecified (§5).
- **What belongs outside the schema:** the `business_date`, the actual
  rate matrix, and the resolved rate value — all market/funding data,
  resolved at pricing time against an MVP input bundle or (later) a live
  source, per `docs/04`/`docs/16`. Revaluation, replay, and scenario
  pricing choose the applicable FTP business/as-of date from the selected
  market/funding-data snapshot for that run, not from the immutable
  product schema. The product schema only ever says "use Treasury FTP for
  this currency/tenor/side" — never "use the FTP rate as of this date."
- **Pros:** matches the real Treasury FTP sheet format now that it is
  known (§2); keeps the schema honest about "this rate is looked up, not
  agreed," which is closer to actual desk practice for funding rates.
- **Cons:** requires the tenor-vocabulary gap (§2.3) to be closed first;
  introduces a lookup-key/rate-resolution boundary that must be tested for
  "reference exists but rate missing at pricing time" (a `MISSING_REFERENCE_DATA`-style
  error, consistent with the existing `PricingErrorCode.MISSING_REFERENCE_DATA`
  member already added in PR #45).
- **Risk of silent methodology drift:** **higher** if quote-side selection
  is ever hard-coded instead of read from the reference — this is why §5
  requires configurability and audit, not a hard-coded `MID`.
- **MVP suitability:** best long-term fit, but only once §2.3's tenor gap
  and the input-bundle boundary (`docs/17` §7/§11 slice D) are in place.

### 4.3 Option C — `MANUAL_VERIFIED_RATE`

**Boundary correction:** the actual manually supplied rate must **not** be
a `DepositLeg` field, for the same reason `business_date` must not be
(§4.2). A manually verified rate is, by definition, an MVP **pricing
input** supplied for a specific run — not a term the two counterparties
agreed to at trade execution. If a rate is meant to be frozen into the
trade itself, that is `FIXED_RATE` (§4.1), not this mode.

- **Schema fields needed:** `manual_input_reference` only — an opaque
  marker/identifier (e.g. "this deposit leg's rate is manually supplied;
  see the referenced pricing-input record") that lets a pricing run find
  the corresponding manual rate. **No `manual_verified_rate` field, no
  source description, no as-of date, no entered-by/run id on
  `DepositLeg`.**
- **Validation required:** required iff `deposit_rate_mode ==
  MANUAL_VERIFIED_RATE`; `manual_input_reference` non-blank. The actual
  rate value and its audit metadata (source description, as-of date,
  entered-by, run id — reusing the audit-trail field list from `docs/17`
  §10) are validated where they actually live: the MVP input bundle /
  valuation context / `MarketDataSnapshot`-like structure and the
  `PricingResult` audit trail, not on the product schema. A manual rate
  without that provenance is exactly the "silent manual override"
  `docs/16` §1 warns against for Manual Override / Manual Entry, the final
  fallback tier — but the fix is to require the provenance at the
  input/audit layer, not to store it on the immutable trade.
- **What belongs outside the schema:** the manual rate value itself, its
  source description, as-of date, entered-by, and run id — all of it. Only
  the reference marker lives on `DepositLeg`.
- **Naming note:** if implementation finds `MANUAL_VERIFIED_RATE` reads as
  implying the rate itself lives on the schema, a future slice may rename
  it to `MANUAL_VERIFIED_RATE_REFERENCE` for clarity; either name is
  acceptable as long as the field boundary above (reference only, no rate
  value) is preserved.
- **Pros:** unblocks MVP work before either a fixed-rate convention or a
  Treasury FTP reference-resolution path exists; matches `docs/16`'s
  "MVP may use manually supplied, verified inputs" language, without
  compromising the schema/market-data boundary.
- **Cons:** weakest methodology guarantee of the three if the referenced
  input/audit record is ever made optional or unverified; must not become
  the permanent path.
- **Risk of silent methodology drift:** medium — mitigated only if the
  referenced manual pricing input and its audit metadata are mandatory,
  not optional, at the input/audit layer.
- **MVP suitability:** good as a **bridge** mode, not a destination.

### 4.4 Recommendation

**Support all three modes now, as an explicit `deposit_rate_mode` enum,
rather than picking one.** This doc does not eliminate any of the three —
each is legitimate for a different situation (frozen trade term vs.
funding-curve lookup vs. pre-ingestion manual bridge), and picking only one
now would either misrepresent real desk practice (if `FIXED_RATE` only) or
block MVP progress on the unresolved tenor-vocabulary gap (if
`TREASURY_FTP_REFERENCE` only). The controlled vocabulary is:

```text
FIXED_RATE
TREASURY_FTP_REFERENCE
MANUAL_VERIFIED_RATE
```

with **exactly** the fields for the selected mode required, and the other
modes' fields required to be `None` (§11). In every mode, `DepositLeg`
itself carries only trade terms, stable selectors, or reference markers —
never a `business_date`, a resolved market rate, or manual-input audit
metadata (§4.2, §4.3, §8). No implementation is added by this PR — this is
the boundary a future `DepositLeg` implementation slice should follow, per
`docs/15`'s precedent of deciding the boundary before writing schema code.

---

## 5. Quote side policy

- **Default quote side is `MID`.**
- **The default must be configurable** — not hard-coded at the schema or
  parser level. Where this configuration lives (a module-level constant, a
  `ValuationContext` field, an MVP input-bundle setting) is an
  implementation decision for a later slice, not resolved here.
- **A per-pricing override of quote side may be allowed only if it is
  audited** — i.e. if a specific pricing run uses `BID` or `OFFER` instead
  of the configured default, that choice must be recorded in the audit
  trail (`docs/17` §10), not silently applied.
- **`BID`/`OFFER` usage is methodology/policy-sensitive and must not be
  silently chosen by code.** No future parser or pricing path should default
  to `BID` or `OFFER` without an explicit, reviewed reason recorded
  alongside the choice.
- **If no side is available for a currency, treat the rate as
  MID-equivalent and record that normalization** — a future normalized FTP
  record (§2.1) should be able to distinguish "quote_side = MID because the
  sheet said so" from "quote_side = MID because no breakdown existed and we
  normalized it," e.g. via a boolean/flag field or a distinct enum member,
  decided at implementation time.
- **The future pricing result / audit trail should record the selected
  quote side** actually used for a given valuation, alongside the other
  audit fields in `docs/17` §10.

---

## 6. Principal repayment rule

Minimum controlled vocabulary evaluated for MVP:

```text
FULL_PRINCIPAL_AT_MATURITY
PRINCIPAL_PLUS_OPTION_PAYOFF
PRINCIPAL_AFFECTED_BY_OPTION_PAYOFF
PHYSICAL_BOND_DELIVERY
```

**Recommended MVP scope: `FULL_PRINCIPAL_AT_MATURITY` plus a separately
calculated/linked option payoff, i.e. deposit principal repayment is
explicit and unconditional, and the option payoff is computed separately
by the wrapper** (`docs/17` §5's "how option payoff affects final customer
payoff" wrapper responsibility) rather than baked into a single combined
repayment formula on `DepositLeg` itself. This corresponds most closely to
`PRINCIPAL_PLUS_OPTION_PAYOFF` at the wrapper level, with `DepositLeg`
itself only needing to express `FULL_PRINCIPAL_AT_MATURITY` for its own
principal return.

- `PRINCIPAL_AFFECTED_BY_OPTION_PAYOFF` (principal itself reduced/adjusted
  by the option outcome, e.g. principal-protected-minus structures) is
  **deferred** — it changes the deposit leg's own repayment math, not just
  the combined customer payoff, and needs its own reviewed methodology
  before being added to the vocabulary.
- `PHYSICAL_BOND_DELIVERY` is **deferred**, consistent with `docs/17` §2's
  "cash settlement first" MVP scope choice — physical delivery of the
  underlying bond is a custody/settlement concern the MVP does not need.

This is a **recommendation**, not a final decision — the exact enum members
and their precise semantics (e.g. whether `PRINCIPAL_PLUS_OPTION_PAYOFF`
lives as a `DepositLeg` value or is really a wrapper-level concept) should
be confirmed when `DepositLeg` and the wrapper schema are implemented
together, since they are coupled (§9).

---

## 7. DayCount / calendar boundary

`DepositLeg` conceptually needs `day_count`, `business_day_convention`, a
`calendar`, and a payment-adjustment rule to compute an actual accrual
amount from `deposit_rate` and the start/maturity dates. All four remain
**deferred / open**, for the same reason `docs/15` kept them off
`BondOption`:

- `day_count` is blocked by the unresolved Issue #37 `DayCount` vocabulary
  decision (A-14) — the existing `DayCount` enum
  (`ACT_360`/`ACT_365_FIXED`/`THIRTY_360`/`ACT_ACT_ISDA`) was built for
  vanilla rates legs, and reusing it here without a reviewed decision
  repeats the exact "silently coerced to the wrong convention" risk
  `docs/14` F-16 flagged.
- `business_day_convention` (an enum member exists — `BusinessDayConvention`
  — but *resolving* it against a calendar to roll a date does not) and
  `calendar` are out of MVP scope: no calendar/holiday engine exists in this
  repo, and building one is far beyond a "smallest usable MVP" schema
  preflight (`docs/17` §1).
- A payment-adjustment rule is not evaluated further here since it depends
  on the above two being resolved first.

**This is listed as an explicit gap, not invented.** A future `DepositLeg`
implementation slice must either (a) omit these fields entirely for MVP
(matching `BondOption`'s precedent of a schema with no day-count field), or
(b) wait for the A-14 decision. Given `docs/17`'s MVP framing (single
short-tenor deposit, MVP fixture inputs), option (a) — omit for MVP, using
a plain elapsed-time or externally-supplied accrual amount if needed by the
future payoff skeleton — is the more consistent choice with the rest of the
MVP scope, but this doc does not mandate it; it is left for the
implementation slice to confirm.

---

## 8. Schema vs. market/funding data boundary

Restated explicitly for `DepositLeg`, consistent with `docs/04` and
`docs/16`:

- The product schema may carry **trade terms** (`deposit_notional`,
  `currency`, dates, `deposit_rate_mode`, a `fixed_deposit_rate` if that
  mode is chosen) and **stable selectors/references**
  (`ftp_rate_selector`'s currency/tenor/quote_side, or
  `manual_input_reference`'s marker) — never a `business_date`, a resolved
  rate value, or manual-input audit metadata.
- Treasury FTP / Funding Curve **rates are not product terms** unless
  explicitly fixed at trade time (`FIXED_RATE` mode, §4.1) — in
  `TREASURY_FTP_REFERENCE` mode, both the applicable `business_date` and
  the resolved rate are market/funding data, resolved at pricing time from
  the run's `MarketDataSnapshot` / MVP input bundle, never stored on the
  schema. The same applies to `MANUAL_VERIFIED_RATE` mode: the manually
  supplied rate and its audit trail live in the input/audit layer, not on
  `DepositLeg` (§4.3).
- Market/funding data must **not** be embedded silently into the product
  schema — no full FTP rate table, no rate history, no specific
  business-date's rate, no cached resolved value living on `DepositLeg`.
- **No generic file import is implied by this doc.** The Treasury FTP
  sheet format (§2) informs the *shape* of a future normalized record and
  a future MVP input bundle (`docs/17` §7/§11 slice D); it does not require
  or imply a general-purpose file-upload feature, per `docs/16` §4.
- **No Bloomberg/API implementation is implied.** Nothing here requires or
  assumes a live connector; MVP inputs may remain manually supplied and
  verified (`docs/16` §7).

---

## 9. Relationship to the future wrapper

The future `BondLinkedStructuredProduct` wrapper links:

```text
DepositLeg + BondOption + principal_repayment_rule + customer payoff rule
```

- `DepositLeg.deposit_notional` and `BondOption.notional` (already on the
  existing schema, PR #50) together define
  `participation_ratio = bond_option.notional / deposit_notional`.
- **Restated from `docs/15` §3.3 and `docs/17` §5: `participation_ratio`
  must be derived from, or validated against, `bond_option.notional /
  deposit_notional` — it must never be a freely-set, independently-stored
  field on the wrapper.** This doc does not change that rule; it only
  confirms `DepositLeg.deposit_notional` is the other half of the ratio.
- `principal_repayment_rule` (§6) determines how the deposit leg's own
  principal return combines with the `BondOption` payoff to produce the
  customer's final payoff — per §6's MVP recommendation, this is expressed
  as `DepositLeg` returning full principal at maturity, with the option
  payoff calculated and applied separately at the wrapper level, not
  folded into a single combined `DepositLeg` formula.
- The wrapper — not `DepositLeg` alone — is responsible for settlement /
  delivery behavior (cash vs. physical, `docs/17` §2), since that depends
  on both legs together, not the deposit leg in isolation.

No wrapper code is implemented here; this section only states the intended
relationship so a future wrapper preflight/implementation can reference it.

---

## 10. Validation rules recommended for future code

```text
deposit_notional > 0
start_date < maturity_date
currency must use the existing Currency enum
DepositLeg must not accept a business_date / as_of field for TREASURY_FTP_REFERENCE
  (that belongs to MarketDataSnapshot / the MVP input bundle, resolved per pricing run)
ftp_rate_selector.tenor must map to a controlled FTP tenor vocabulary, not the
  existing Frequency enum and not a placeholder/unchecked string
  (e.g. "ON" vs "O/N" must be rejected before construction or reference
  resolution, not silently accepted or silently normalized)
quote_side must be BID / MID / OFFER if a side is available
default quote_side = MID
fixed_deposit_rate required only when deposit_rate_mode is FIXED_RATE
ftp_rate_selector required only when deposit_rate_mode is TREASURY_FTP_REFERENCE
  (selector fields only: currency/tenor/quote_side[/source_policy]; never
  business_date, never a resolved rate)
manual_input_reference required only when deposit_rate_mode is MANUAL_VERIFIED_RATE
  (reference marker only; never the manual rate value or its audit metadata)
do not allow multiple rate sources at the same time (mutual exclusivity across modes)
rate_percent must convert to decimal explicitly (divide by 100, not implicit)
  -- applies to the MarketDataSnapshot / input-bundle record, not a DepositLeg field
rate must reject blank / non-numeric / NaN / infinity
  (reuse the _require_finite_number pattern introduced for
  BondOption.strike_yield, PR #50's Codex P2 fix) -- again at the
  snapshot/input-bundle layer for TREASURY_FTP_REFERENCE and
  MANUAL_VERIFIED_RATE modes, and on DepositLeg.fixed_deposit_rate itself
  for FIXED_RATE mode
principal_repayment_rule must be controlled vocabulary
```

None of these are implemented here — they are the acceptance-criteria-style
checklist a future `DepositLeg` implementation PR should satisfy, mirroring
how `docs/15` §6 listed `BondOption`'s test checklist before PR #50
implemented it.

---

## 11. Explicit exclusions

This PR does not implement:

```text
DepositLeg code
FTP parser
Treasury FTP / Funding Curve ingestion
BondLinkedStructuredProduct
pricing engine
QuantLib
market-data ingestion
Bloomberg/API connector
file upload
screenshot capture
UI
tests
```

---

## 12. Recommended next slice

**Implement `DepositLeg` schema only** — the smallest useful version, per
`docs/17` §11 slice A — is the recommended next step, **conditional on**:

- accepting the three-mode `deposit_rate_mode` design (§4.4) as the
  schema boundary (rather than picking a single mode), and
- accepting the selector/reference-only field boundary for
  `TREASURY_FTP_REFERENCE` (§4.2) and `MANUAL_VERIFIED_RATE` (§4.3) — no
  `business_date`, no resolved rate, no manual-rate audit metadata on
  `DepositLeg` itself.

**`TREASURY_FTP_REFERENCE` must not be enabled with placeholder or
unchecked tenor validation.** A controlled FTP tenor vocabulary — **and
must not reuse the existing `Frequency` enum**, which is a payment/reset
period vocabulary, not a tenor label set (§2.3) — must exist before
`TREASURY_FTP_REFERENCE` mode can be enabled for product construction or
reference resolution. Unsupported or misspelled tenors (e.g. `ON` vs
`O/N`) must be rejected outright, not silently accepted or coerced.

**That vocabulary now exists** (§2.3): `DepositRateMode`,
`TreasuryFTPQuoteSide`, and `TreasuryFTPTenor` landed in
`src/shiori_pricing_lab/products/enums.py` in the controlled-vocabulary
slice that followed this preflight. **After the vocabulary slice lands,
the next slice can implement `DepositLeg` schema only**, using
`DepositRateMode`, `TreasuryFTPQuoteSide`, and `TreasuryFTPTenor`.
`TREASURY_FTP_REFERENCE` mode's `ftp_rate_selector` may now validate its
`tenor` and `quote_side` fields against this controlled vocabulary instead
of a placeholder check. That implementation slice still must not:

- store `business_date`, a resolved rate, `source_file_name`, an
  `as_of_timestamp`, or manual-rate provenance (source, as-of,
  entered-by, run id) on `DepositLeg` itself (§4.2, §4.3, §8);
- implement a Treasury FTP parser, FTP ingestion, `MarketDataSnapshot`,
  an MVP input bundle, a pricing engine, QuantLib, the
  `BondLinkedStructuredProduct` wrapper, or any UI.

Slices B (Bond reference fixture), C (wrapper schema), D (manual MVP input
bundle), E (deterministic payoff skeleton), F (QuantLib benchmark), and G
(MVP runner example) from `docs/17` §11 remain downstream of `DepositLeg`
and are not affected by this doc beyond the clarifications above.

---

## 13. Scope boundaries of this PR

Docs only. No `DepositLeg`, Treasury FTP parser, ingestion,
`BondLinkedStructuredProduct`, pricing engine, QuantLib, connector,
market-data ingestion, file upload, screenshot capture, UI, or test code is
added. `docs/17` is not rewritten — this doc extends it with the Treasury
FTP format detail and the deposit-rate-source analysis it deferred. No
frozen BLI v1.3 source spec file is edited. Issue #38 is unaffected.
