# 20 BLI Bond Reference Data Preflight

Status: docs-only preflight. No Bond Master dataclass, fixture file,
parser, file import, connector, `MarketDataSnapshot`, MVP input bundle,
pricing, payoff skeleton, QuantLib, or UI is added by this doc.

## 1. Purpose

`docs/17_bli_mvp_vertical_slice_preflight.md` §11 names "Slice B: Bond
reference data minimal schema / fixture boundary" as the next BLI MVP
slice after the wrapper schema (PR #56). `docs/17` §6 already lists a
minimum field set and states the boundary rule — "must not be embedded
inside product schemas" — but does not resolve the schema/fixture shape,
plain-vanilla eligibility rules, or the exact relationship to `BondOption`
and the wrapper. This doc is that resolution.

The underlying bond referenced by a `BondOption` (`underlying_isin`) has
static, slowly-changing contractual terms — coupon, maturity, day count,
yield convention — that are **not** deal terms of the option or deposit
trade, and **not** market data. `docs/15` §2.2 already excluded these
fields from `BondOption` for exactly this reason (they are "Bond Master
reference data," resolved at pricing time, never baked into the option
deal). This doc defines where they actually live.

**Source of the field list:** the required field set below is taken
directly from `docs/bond_linked_structured_pricer/ANNEX_B_v1.3.md` §B.5
"Bond Master File," which is available in this repo as a frozen v1.3
source spec (not edited by this doc). `docs/14` F-08 already flagged one
gap in that spec (no `m`/compounding-frequency field for `yield_convention
= OTHER`); this doc carries that gap forward rather than silently
resolving it. **Any future implementation slice must confirm this field
list against Annex A/B again before writing code** — this preflight
transcribes and organizes the Annex B §B.5 fields as of this writing, but
does not re-derive or re-validate Annex A's methodology dependencies on
these fields (e.g. §A.6's yield-to-price maths), which remain out of scope
for a reference-data preflight.

This doc answers what the future object/fixture must be able to answer:

```text
What is the bond?
How does it accrue?
How does yield convert to price?
Is it eligible for the plain-vanilla MVP?
```

It must not answer:

```text
What is today's bond price?
What is today's yield?
What is today's spread?
What is today's volatility?
What is the valuation date?
What is the PV?
What is the customer return?
```

---

## 2. What Bond Reference Data owns

- **Stable, security-level static terms** of the underlying bond: identity
  (ISIN, issuer, currency), coupon mechanics (rate, frequency, first/last
  coupon date), accrual/yield convention (day count, business day
  convention, yield convention), redemption terms, and structural flags
  (callable, sinkable, bond type) needed to determine plain-vanilla MVP
  eligibility (§5).
- **Nothing else.** It does not own deal terms (those are `BondOption`'s
  and `DepositLeg`'s), market observations (§9), or pricing output.

## 3. What Bond Reference Data must not own

Explicitly excluded, restated in full (mirrors the pattern `docs/18` §8
and `docs/19` §9 already used for `DepositLeg` and the wrapper):

```text
business_date
valuation_date
as_of_timestamp
clean_price
dirty_price
yield
spread
volatility
curve
discount_rate
funding_rate
FTP rate
source_file_name
loaded_at
run_id
PV
option_premium
customer_return
bank_margin
pricing_result
```

Where each belongs instead:

- **Market price / yield / spread / vol / curve** → a future
  `MarketDataSnapshot` / MVP input bundle (`docs/17` §7).
- **Source / as-of / run / audit metadata** → a future audit trail /
  input-bundle provenance layer (`docs/17` §10, the same pattern already
  used for `TreasuryFTPRateSelector`, which excludes `business_date` /
  `source_file_name` / `loaded_at` for the identical reason, `docs/18`
  §2.1/§4.2).
- **PV / customer return / margin** → a future `PricingResult`.
- **Deal-specific terms** (strike, notional, option type, exercise style,
  deposit rate, principal repayment rule) → `BondOption` / `DepositLeg` /
  `BondLinkedStructuredProduct`, already implemented — this doc does not
  duplicate any of them.

---

## 4. Minimal MVP field set

Per Annex B §B.5, with an MVP required/optional/deferred classification
(this doc's judgment call, not a re-statement of Annex B's own required/
optional marking, which does not exist at that granularity in the spec
text):

| Field | Annex B status | MVP classification | Enum / type | Notes |
| --- | --- | --- | --- | --- |
| `isin` | required | **Required** | `str` | Non-blank, same pattern as `BondOption.underlying_isin`. |
| `issuer` | required | **Required** | `str` | Non-blank. No structured issuer registry exists; a plain string for MVP. |
| `currency` | required | **Required** | `Currency` (existing) | Coerced via `coerce_enum`, same as every other product schema. |
| `coupon` | required | **Required** | `float` | Coupon *rate* (per Annex B "coupon"). Decimal form, consistent with `docs/18`'s percent-vs-decimal rule for FTP rates — this doc does not re-litigate that; it simply requires decimal, not percent, matching every other rate field in this repo. **Must be finite and non-negative (`coupon >= 0`); negative coupons are not accepted in the BLI MVP Bond Reference Data fixture.** `coupon == 0` is allowed as valid *reference data* — zero-coupon bonds can be plain-vanilla — but zero-coupon *BLI pricing eligibility* is a separate, explicit decision for the future implementation slice (§5). |
| `coupon_frequency` | required | **Required** | `Frequency` (existing) | Reuse the existing payment-frequency enum — this is exactly the kind of quantity `Frequency` was built for (unlike `TreasuryFTPTenor`, which was deliberately kept separate because it is a lookup tenor, not a payment frequency, `docs/18` §2.3). |
| `maturity_date` | required | **Required** | `str` (`YYYY-MM-DD`) | Strict ISO date, existing `_parse_iso_date` pattern. |
| `issue_date` | required | **Required** | `str` (`YYYY-MM-DD`) | Strict ISO date; must be before `maturity_date`. |
| `day_count` | required | **Required** | `DayCount` (existing) | Coerced via `coerce_enum`. This is the *bond's own* day count — distinct from the still-unresolved deposit-leg/wrapper `DayCount` question (A-14, `docs/18` §7); resolving it *here*, for the bond's own accrual, does not resolve A-14 for the deposit leg (§6 below). |
| `business_day_convention` | required | **Required** | `BusinessDayConvention` (existing) | Coerced via `coerce_enum`. Recorded as a deal-term-style choice only; no calendar resolution here (§6). |
| `redemption_amount` | required | **Required** | `float` | Positive, finite (reuse the `_require_finite_number` pattern from `BondOption`/`DepositLeg`). Typically `100.0` per 100 face for a bullet bond, but stored explicitly rather than assumed. |
| `callable_flag` | required | **Required** | `bool` | Drives eligibility (§5); explicit boolean, not a truthy value (matches `CrossCurrencySwap.initial_exchange`/`final_exchange`'s existing `isinstance(..., bool)` pattern). |
| `sinkable_flag` | required | **Required** | `bool` | Same pattern; drives eligibility. |
| `bond_type` | required | **Required** | `str` (or a future controlled enum) | Annex B does not enumerate `bond_type`'s allowed values in the text available to this preflight. **Open item (§11):** whether `bond_type` needs its own controlled vocabulary (e.g. `FIXED_COUPON`, `FLOATING_RATE_NOTE`, `AMORTIZING`, ...) or can start as a free-text field cross-checked against the eligibility rules in §5 is left to the implementation slice, which must confirm against Annex A/B before choosing. |
| `yield_convention` | required | **Required** | `BondYieldConvention` (existing) | Coerced via `coerce_enum`. `OTHER` is a valid *enum* member but is **not** MVP-eligible by default (§5) — this is an eligibility rule, not a coercion rule; the value still coerces successfully, it is rejected at the eligibility-check layer. |
| `ex_dividend_days` | required | **Required** | `int` | Non-negative integer (reuse `BondOption.settlement_lag_days`'s `isinstance(..., bool)` exclusion + non-negativity pattern). Needed for accrual mechanics even at MVP. |
| `first_coupon_date` | required | **Required** | `str` (`YYYY-MM-DD`) | Annex B marks it required and flags "irregular period → must enter cash flow generation, cannot be ignored" (§B.5 validation note). **Kept required, matching Annex B — not downgraded to optional.** Non-null, strict ISO date via the existing `_parse_iso_date` pattern. A *reference-data schema* only records the date; it does not generate cash flows from it (that remains pricing-engine work, §10/§11). Omitting the field would remove the only signal a future implementation has for detecting an irregular first coupon period, forcing it to silently infer a regular schedule from `issue_date`/`maturity_date`/`coupon_frequency` — which this doc does not allow (§5). |
| `last_coupon_date` | required | **Required** | `str` (`YYYY-MM-DD`) | Same reasoning as `first_coupon_date` — kept required, not downgraded. |
| `status` | required | **Required** | `str` (or a future controlled enum) | Annex B does not enumerate allowed values in the text available here. Same open item as `bond_type` (§11) — a future implementation slice must confirm the vocabulary against Annex A/B, or start with a minimal `ACTIVE`/`INACTIVE`-style pair and expand later. |

**Explicitly not in this table:** `compounding_frequency` / `m`. Per
`docs/14` F-08, Annex B §B.5 has no field for the compounding frequency
`m` that Annex A §A.6.2 needs to convert `yield_convention = OTHER` to a
price. This preflight does **not** resolve F-08/A-08 — it only accepts
Annex B §B.5's `yield_convention` field as-is and defers the `m` gap to
whichever slice implements amendment A-08 (`docs/14` §5), consistent with
how `docs/15` kept `compounding_frequency` off `BondOption` for the same
reason.

---

## 5. Plain-vanilla eligibility rules

Recommended MVP rule, matching Annex B §B.5's own validation section
almost verbatim:

```text
Only fixed-coupon, bullet, non-callable, non-sinkable, plain-vanilla
bonds are eligible.
```

Future code should **reject or mark ineligible**:

```text
callable bonds            (callable_flag == true)
sinkable bonds             (sinkable_flag == true)
floating-rate notes
amortizing bonds
convertibles
inflation-linked bonds
perpetuals
structured notes
unknown / OTHER yield convention, unless explicitly approved
bonds with an irregular first or last coupon period, until a future
  cash-flow generation slice supports them
```

- `callable_flag` / `sinkable_flag` map directly to existing boolean
  fields (§4) — straightforward to check.
- `yield_convention == BondYieldConvention.OTHER` maps directly to the
  existing enum (§4) — Annex B §B.5's own validation text already says
  "reject for MVP pricing pool... unless Trader has supplied `m` and
  `day_count` in Bond Master maintenance," which restates the F-08 gap
  (§4); until that gap is resolved, `OTHER` should be treated as
  ineligible by default for MVP.
- **Floating-rate notes / amortizing bonds / convertibles /
  inflation-linked bonds / perpetuals / structured notes have no direct
  field mapping in the Annex B §B.5 field list available to this
  preflight** (there is no `is_floating`, `is_amortizing`, or
  `is_convertible` flag). **This is recorded as an implementation
  decision, not a silent assumption:** a future implementation slice must
  either (a) rely on `bond_type` (§4, itself an open vocabulary question)
  to encode and check these categories, or (b) determine that the
  existing `callable_flag`/`sinkable_flag`/`yield_convention` fields are
  Annex B's only exclusion signals and that the additional categories
  listed above are out-of-scope-by-construction (i.e., a `bond_type`
  vocabulary that only ever contains "plain vanilla fixed coupon bullet"
  values for MVP, so nothing else can be constructed in the first place).
  Either resolution must be explicit in that slice's PR body, not
  inferred silently from partial field coverage.
- **Irregular first/last coupon period bonds are ineligible for MVP
  pricing until a future cash-flow generation slice supports them.**
  `first_coupon_date` and `last_coupon_date` are now required
  reference-data fields (§4), so a future implementation always has the
  raw dates on hand; whether a given bond's first/last period is actually
  *irregular* (i.e. does not match the regular schedule implied by
  `issue_date`/`maturity_date`/`coupon_frequency`) requires schedule
  logic this preflight does not design. **This does not mean the
  reference-data schema calculates schedules, adds a calendar engine, or
  generates cash flows** — it only means a bond with a known or detected
  irregular stub must be marked ineligible for MVP pricing, not silently
  admitted. If the first implementation slice cannot detect irregularity
  without a schedule engine, the MVP fixture itself must be manually
  reviewed and limited to regular-coupon, no-stub bonds, and any known
  stub/irregular bond is out of MVP pricing scope by construction (not by
  runtime detection) until a future cash-flow generation slice expands
  coverage.
- **Zero-coupon eligibility must be an explicit decision, not left
  ambiguous.** `coupon == 0` is valid *reference data* (§4) — a
  zero-coupon bond can be plain-vanilla in principle — but that does not
  automatically make it MVP-*pricing*-eligible. The future implementation
  slice must explicitly choose one of: (a) treat zero-coupon bonds as
  eligible for MVP pricing like any other fixed-coupon bullet bond, or
  (b) declare zero-coupon bonds out of MVP pricing scope for the first
  implementation (a stricter, also-acceptable choice). **Negative coupon
  is never accepted** — that is a reference-data validation rejection
  (§4/§10), not an eligibility question.

---

## 6. Day count / calendar / yield convention boundary

- `day_count`, `business_day_convention`, and `yield_convention` are
  **recorded as reference-data fields** here (§4) — unlike `DepositLeg`
  and `BondOption`, which deliberately omit them (`docs/15` §2.2, `docs/18`
  §7). This is not a contradiction: those two schemas omit these fields
  because they describe *deal terms*, and the bond's own accrual/yield
  convention is not something the option or deposit trade negotiates — it
  is a property of the bond itself, which is exactly what Bond Reference
  Data exists to hold.
- **This does not resolve the Issue #37 `DayCount` vocabulary decision
  (A-14).** The existing `DayCount` enum
  (`ACT_360`/`ACT_365_FIXED`/`THIRTY_360`/`ACT_ACT_ISDA`) was built for
  vanilla rates legs; using it here for the bond's own accrual convention
  is a **separate application** of the same enum, not a resolution of
  A-14's open question about whether that enum's member set is complete
  for BLI's needs (market `ACT/ACT` variants, `ACT/365`, `ACT/365F`
  remain undecided — `docs/14` §5). A future implementation slice must
  confirm this enum is adequate for actual bond accrual conventions before
  using it, or flag a gap if it is not.
- **No calendar/holiday engine is proposed here.** `business_day_convention`
  is recorded as a static reference-data field only — resolving it against
  an actual holiday calendar (to roll a coupon date, for example) requires
  a calendar engine that does not exist in this repo (`docs/18` §7's
  identical reasoning for the deposit leg). Annex B §B.6 "Calendar /
  Holiday File" is a separate future data source, not part of this
  preflight's scope.

---

## 7. Fixture vs future source system

**For MVP, Bond Reference Data may be supplied as a small, manually
reviewed fixture** (e.g. a synthetic JSON/CSV file with a handful of
plain-vanilla example bonds), consistent with `docs/16`'s "MVP may use
manually supplied, verified inputs" direction and `docs/17` §6's framing.

Explicitly **not** in this slice:

- no generic file import;
- no parser;
- no Bloomberg/API connector;
- no screenshot capture;
- no auto-loading from external systems.

The fixture boundary must be **deterministic and reviewable** — a fixed
set of hand-written records checked into the repo (or a test-only
in-memory structure), not a live-loaded file with unpredictable content.

Future source systems, listed for context only (none implemented here):

- Bloomberg/BQL;
- an internal bond master;
- a vendor security master;
- a manually reviewed fixture (the MVP choice).

**The schema/future object itself should not care where the data came
from.** Source/provenance (which system, when loaded, who reviewed it) is
audit/provenance metadata that lives *around* the fixture (a loader-level
concern, or an audit-trail field per `docs/17` §10), never mixed into the
reference-data fields themselves — the same "no `source_file_name` /
`loaded_at` on the data object itself" pattern already used for
`TreasuryFTPRateSelector` (§3).

---

## 8. Relationship to BondOption and BLI wrapper

- **`BondOption` stores `underlying_isin`, not full bond reference data**
  (`docs/15` §2.1/§2.2, unchanged by this doc) — `underlying_isin` is a
  *reference*, not the bond's data itself.
- **`BondLinkedStructuredProduct` embeds `BondOption` and `DepositLeg`,
  but does not embed full Bond Master terms** (`docs/19` §2/§3, unchanged
  by this doc) — the wrapper's job is the relationship between the two
  deal-term legs, not a copy of the underlying bond's static data.
- **Future pricing will resolve `bond_option.underlying_isin` against the
  Bond Reference Data fixture/lookup** — a lookup step that happens at
  pricing time (inside a future pricing engine or its input-resolution
  layer), not at product-schema construction time. `BondOption` must
  remain constructible without any Bond Reference Data lookup succeeding
  or even existing — consistent with `docs/04`'s "product definitions must
  not fetch market data" rule, since a Bond Master lookup is exactly this
  kind of external resolution.
- **If ISIN is missing from the reference data fixture, future pricing
  must block, not guess.** No default bond terms, no silent
  "assume vanilla," no fallback convention — a missing reference-data
  record is a hard pricing error (the existing
  `PricingErrorCode.MISSING_REFERENCE_DATA` member, added in PR #45
  specifically for this kind of gap, `docs/14` §3.2, is the natural future
  error code for this case, though wiring it up is a future pricing-engine
  concern, not this preflight's).
- **If the bond is not MVP-eligible (§5), future pricing must block, not
  silently downgrade.** A callable bond referenced by a `BondOption` must
  cause pricing to refuse the trade, not silently price it as if it were
  bullet.

---

## 9. Relationship to market data / MVP input bundle

```text
Bond Reference Data = stable security master terms.
Market Data / MVP Input Bundle = valuation-time observed or supplied values.
```

| Bond Reference Data (this doc) | Market Data / MVP Input Bundle (`docs/17` §7, future) |
| --- | --- |
| coupon | valuation date |
| maturity | clean price |
| coupon frequency | yield |
| day count | volatility |
| business day convention | spread |
| redemption amount | discount curve |
| yield convention | Treasury FTP / funding curve rate (if a deposit leg needs it) |
| — | source / as-of of each market observation |

The distinguishing test: Bond Reference Data answers "what did the issuer
promise," largely unchanged for years at a time; the input bundle answers
"what does the market say right now," different on every valuation date.
Nothing in §4's field list changes between valuation runs for the same
bond; everything in the right-hand column does.

---

## 10. Validation rules for future code

None of these are implemented here — the acceptance-criteria-style
checklist a future Bond Reference Data implementation PR should satisfy,
mirroring how `docs/15` §6 and `docs/18` §10 preceded `BondOption` (PR #50)
and `DepositLeg` (PR #54):

```text
isin, issuer must be non-blank strings
currency must coerce to the existing Currency enum
coupon must be a finite number (reuse _require_finite_number) and must be
  non-negative (coupon >= 0) -- negative coupons are rejected outright;
  coupon == 0 is accepted as valid reference data, but zero-coupon MVP
  pricing eligibility is a separate, explicit decision (section 5), not
  automatically implied by accepting the reference-data value
coupon_frequency must coerce to the existing Frequency enum
maturity_date, issue_date must be strict YYYY-MM-DD (_parse_iso_date);
  issue_date must be before maturity_date
day_count must coerce to the existing DayCount enum
business_day_convention must coerce to the existing BusinessDayConvention enum
redemption_amount must be a finite, positive number
callable_flag, sinkable_flag must be real bool, not truthy values
yield_convention must coerce to the existing BondYieldConvention enum
ex_dividend_days must be a non-negative integer, not bool
first_coupon_date, last_coupon_date are REQUIRED (not optional/if-present)
  and must be strict YYYY-MM-DD (_parse_iso_date); missing either field
  is rejected, matching Annex B.5's own required-field marking -- this
  must not be silently downgraded to optional
bond_type, status: vocabulary decision deferred to the implementation
  slice (see section 4/section 5's open items) -- must not silently
  accept arbitrary strings without at least documenting the decision
plain-vanilla eligibility check rejects callable_flag == true,
  sinkable_flag == true, and yield_convention == OTHER by default
  (unless a documented exception path exists)
irregular first/last coupon period handling must be explicit: either the
  implementation detects irregularity (via schedule logic, not a
  calendar engine) and marks such bonds ineligible for MVP pricing, or
  the MVP fixture is manually reviewed and limited to regular-coupon,
  no-stub bonds by construction -- a stub bond must never silently enter
  the MVP pricing pool
no market-data or pricing-output field (section 3's full list) exists
  on the reference-data object -- a dataclass-fields boundary test,
  mirroring tests/test_deposit_leg.py and
  tests/test_bond_linked_structured_product.py
```

---

## 11. Deferred items

Explicitly not decided or built by this doc:

- **`m` / compounding frequency for `yield_convention = OTHER`** (`docs/14`
  F-08/A-08) — carried forward, not resolved.
- **Whether `bond_type` and `status` need their own controlled enums**, or
  can start as validated free text (§4) — left to the implementation
  slice, which must confirm against Annex A/B before choosing.
- **Whether floating-rate/amortizing/convertible/inflation-linked/
  perpetual/structured-note exclusion needs an explicit field beyond
  `callable_flag`/`sinkable_flag`/`yield_convention`, or is achieved by
  construction via a narrow `bond_type` vocabulary** (§5) — an explicit
  open decision, not silently assumed either way.
- **Whether the `DayCount` enum's current member set is adequate for real
  bond accrual conventions** (§6) — a separate, not-yet-confirmed
  application of the still-partially-open Issue #37 / A-14 vocabulary
  question.
- **Irregular first/last coupon period *cash-flow generation*** (Annex B
  §B.5's own validation note) — explicitly out of scope for a
  reference-data schema; belongs to a future pricing/cash-flow-generation
  slice, not this preflight or its recommended next slice. **This is
  narrower than it may first read: the `first_coupon_date` and
  `last_coupon_date` *fields themselves* are not deferred — they are
  required reference-data fields (§4) that must be validated (non-null,
  strict ISO date) in the very next implementation slice. Only the
  *cash-flow math* that would consume those dates to generate an
  irregular coupon schedule is deferred.** Until that future slice
  exists, any bond whose first/last period is irregular must be marked
  ineligible for MVP pricing (§5), not silently priced as if regular.
- **Any lookup/resolution mechanism** (by ISIN, from a fixture or future
  source system) — this doc states the *rule* (block on missing/
  ineligible, §8) but does not design the lookup interface.
- Everything already deferred by `docs/16`/`docs/17`: Treasury FTP
  parser/ingestion, `MarketDataSnapshot`, MVP input bundle, pricing
  engine, QuantLib, UI, Bloomberg/API connector, file upload, screenshot
  capture.

---

## 12. Recommended next implementation slice

**Implement minimal `BondReferenceData` / Bond Master fixture schema
only.** Still no pricing. That slice should:

- add the reference-data dataclass with the required fields from §4,
  resolving the `bond_type`/`status` vocabulary open item explicitly;
- implement required, non-null, strict-ISO-date validation for
  `first_coupon_date` and `last_coupon_date` — these are required
  reference-data fields, not optional (§4);
- reject negative `coupon` values, and explicitly resolve whether
  `coupon == 0` is MVP-pricing-eligible or out of scope (§5);
- add the plain-vanilla eligibility check (§5), resolving the
  floating/amortizing/convertible/etc. exclusion-mechanism open item
  explicitly, **and** explicitly keeping bonds with an irregular
  first/last coupon period out of the MVP pricing pool — either by
  detecting irregularity, or by limiting the MVP fixture to
  regular-coupon, no-stub bonds by construction;
- add a small, deterministic, manually reviewed fixture (§7) — not a
  parser, not a file-import mechanism;
- add tests per §10/§13;
- export the schema from `products/__init__.py` **only if** it is treated
  as part of the products package; if the implementation slice instead
  decides reference data belongs in a separate module/package (e.g.
  `shiori_pricing_lab/reference_data/`), that decision should be made
  explicitly in that PR, not silently defaulted.

That slice must still **not** implement: pricing; a payoff skeleton;
QuantLib; `MarketDataSnapshot`; the MVP input bundle; a Treasury FTP
parser; ingestion; a Bloomberg/API connector; or UI. It must not close
Issue #38 — this slice is downstream of, not a replacement for, the
`BondOption` partial slice (PR #50) that satisfies #38's own narrower
scope.

---

## 13. Acceptance checklist for future code PR

A future Bond Reference Data implementation PR should satisfy:

- Adds a minimal Bond Reference Data schema/fixture boundary only.
- Does not touch existing product schemas (`BondOption`, `DepositLeg`,
  `BondLinkedStructuredProduct`) except an optional import/export
  reference if the implementation slice decides that is needed — no
  restructuring of their existing fields.
- Does not add pricing logic.
- Does not add a `MarketDataSnapshot`.
- Does not add an MVP input bundle.
- Does not add file import / a parser / a connector.
- Uses the existing `Currency`, `Frequency`, `DayCount`,
  `BusinessDayConvention`, and `BondYieldConvention` enums — no new enum
  values added in a docs-only PR, and no new enum values added silently
  in the code PR without being called out.
- Rejects or marks ineligible non-plain-vanilla bonds per §5, with the
  exclusion-mechanism open item resolved explicitly.
- Keeps every field in §3's exclusion list out of the schema — a
  dataclass-fields boundary test asserts this.
- Provides tests for:
  - a valid plain-vanilla bond reference constructs successfully;
  - a callable bond is rejected or marked ineligible;
  - a sinkable bond is rejected or marked ineligible;
  - missing required fields (§4) are rejected, **including missing
    `first_coupon_date` and missing `last_coupon_date`** — these must be
    rejected the same as any other required field, not silently accepted
    as absent;
  - an invalid `first_coupon_date` / `last_coupon_date` format is
    rejected (non-`YYYY-MM-DD` or non-calendar date);
  - a negative `coupon` is rejected;
  - `coupon == 0` is either accepted as valid reference data with the
    zero-coupon MVP-pricing-eligibility decision applied explicitly
    (accepted or marked ineligible, per whichever choice §5 records), or
    rejected — the test must reflect whichever explicit rule that slice
    adopted, not an untested assumption;
  - irregular first/last coupon stub handling is exercised explicitly —
    either a test proving a detected-irregular bond is marked ineligible,
    or (if detection is not implemented) a test/comment documenting that
    the fixture is limited to regular-coupon, no-stub bonds by
    construction;
  - invalid enum values are rejected via the existing `coerce_enum` path;
  - market-data/pricing fields (§3) are absent — dataclass-fields
    boundary test;
  - lookup by ISIN, if a lookup helper is included in that slice.
- Issue #38 remains open and is not referenced as closed by that PR.

---

## 14. Scope boundaries of this PR

Docs only. No Bond Master dataclass, fixture file, parser, file import,
Bloomberg/API connector, `MarketDataSnapshot`, MVP input bundle, pricing,
payoff skeleton, QuantLib, or UI is added. No frozen BLI v1.3 source spec
file (`docs/bond_linked_structured_pricer/`) is edited — this doc only
reads and transcribes Annex B §B.5's field list. Issue #38 is unaffected
and remains open.
