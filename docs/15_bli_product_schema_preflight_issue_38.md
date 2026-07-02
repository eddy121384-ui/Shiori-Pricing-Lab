# 15 BLI Product Schema Preflight (Issue #38)

Status: **docs-only preflight — no `BondOption` / `BondLinkedStructuredProduct`
schema code, no tests, no schema registration/export changes.**

This document answers one question before any Issue #38 code is written:

> Can `BondOption` and `BondLinkedStructuredProduct` be defined as pure
> deal-term schemas **without** including unresolved `DayCount` / Bond Master
> convention fields?

It does not implement the schemas. It decides whether Issue #38 can safely
*start* as a narrow schema slice, and if so, exactly where its boundary must
sit so it does not quietly turn into a Bond Master schema, a market-data
snapshot, or a pricing engine.

---

## 1. Current-state summary

- **PR #45** landed only the **first code-level controlled-vocabulary slice**
  for Issue #37: `Currency.NZD/KRW/HKD/SGD`, the five BLI product enums
  (`PayoffBasis`, `OptionType`, `ExerciseStyle`, `SettlementType`, `Position`),
  `BondYieldConvention`, `PricingErrorCode.MISSING_REFERENCE_DATA`, and their
  tests (`tests/test_bli_enums.py`). No schema, no snapshot, no pricing engine
  was added.
- **Issue #37 remains open.** `docs/00_development_log.md` and
  `docs/09_mvp_core_runbook.md` (checkpoint after PR #46) both record that the
  `DayCount` vocabulary (`ACT/365`, `ACT/365F`, market `ACT/ACT` variants) and
  the broader market/jurisdiction vocabulary are **still deferred**, pending a
  reviewed Annex-driven decision (`docs/14` §5, amendment A-14). `DayCount`
  itself (`ACT_360`, `ACT_365_FIXED`, `THIRTY_360`, `ACT_ACT_ISDA`) is
  unchanged since PR #21.
- **Issue #38 cannot be treated as fully unblocked** by PR #45/#46 alone.
  Per the corrected checkpoint wording, #38 may be *prepared*, but only if it
  does not land a schema that depends on the unresolved `DayCount` / Bond
  Master convention decision. This preflight exists to make that boundary
  concrete before any schema code is written, rather than discovering it
  mid-implementation.

Issue #38's own body already anticipates this: its "out of scope" section
lists "no bond coupon, coupon schedule, day count, yield convention,
maturity, accrued interest, or cashflow generation inside product
definitions." The question this preflight answers is whether that stated
scope is actually achievable for **both** proposed schemas, or whether one of
them (the structured wrapper) pulls `DayCount` back in through its deposit
leg.

---

## 2. Product-schema boundary analysis — `BondOption`

### 2.1 What `BondOption` should carry (deal terms only)

Cross-referencing Issue #38's field list, `docs/14` §4.1, and SPEC §6.1's
"Bond Option Leg" section, the following are pure deal terms — they describe
what was traded, not the market state or the bond's static reference data:

| Field | Type / enum | Notes |
| --- | --- | --- |
| `product_id` | `str` | non-blank, per existing `_require_non_blank` pattern |
| `underlying_isin` (or equivalent bond identity) | `str` | a *reference*, not the bond's static data itself |
| `currency` | `Currency` | the option/settlement currency as a **deal term**, if the desk fixes it at execution (SPEC §6.1 "預設 = bond currency" is a market-data-resolution default, not a deal-term rule — see §2.2) |
| `payoff_basis` | `PayoffBasis` | `PRICE` or `YIELD` (PR #45) |
| `option_type` | `OptionType` | `CALL` or `PUT` (PR #45) |
| `exercise_style` | `ExerciseStyle` | `EUROPEAN` or `AMERICAN` (PR #45) |
| `settlement_type` | `SettlementType` | `CASH` or `PHYSICAL` (PR #45) |
| `settlement_lag_days` (or equivalent explicit lag term) | `int` | Issue #38 explicitly lists "settlement lag" as in-scope; recorded as a **plain integer deal term** (a trader-adjustable T+n), not resolved against a calendar here — SPEC §6.1 confirms Settlement Lag is trader-adjustable with an audited reason, which is a deal-term / override concern, not a market-data one |
| `strike_price` and/or `strike_yield` | `float`, mutually exclusive by `payoff_basis` | see §2.3 below — one is required, the other must be absent, based on `payoff_basis` |
| `expiry_date` | `str` (`YYYY-MM-DD`) | strict ISO date, per existing `_parse_iso_date` |
| `exercise_start_date` | `str \| None` | only meaningful for `ExerciseStyle.AMERICAN`; `None` for `EUROPEAN` |
| `notional` (bond option notional / face amount) | `float` | must be positive, per existing pattern |
| `position` | `Position` | `BUY` or `SELL` (PR #45) |

This list is deliberately schema-level only: no calendar resolution, no
settlement-date computation, no accrued interest — those are pricing-engine
or market-data concerns, consistent with `docs/04`'s "product definitions
must not fetch market data" and "must not contain live curves or vols" rules.

### 2.2 What `BondOption` must **not** include

Directly from Issue #38's "out of scope" list, `docs/14` §3.2/§4.1 (the
yield-convention gap, F-08/F-16), and the schema design rules in `docs/04`:

- `coupon`, `coupon_frequency` — bond static data, lives in Bond Master.
- `maturity_date` (of the underlying bond) — bond static data.
- `issue_date` — bond static data.
- `day_count` — **the exact field this preflight exists to keep out.** A bond
  option's *own* payoff/settlement mechanics do not require a day-count
  convention at the schema level (unlike an IRS leg, where day count is
  itself a first-class deal term). Day count belongs to the underlying
  bond's accrual and is Bond Master reference data (SPEC §B.5), resolved at
  pricing time — never a `BondOption` field.
- `business_day_convention` **as a Bond Master / calendar-resolution
  concept** — a settlement *calendar* (needed to roll `settlement_lag_days`
  into an actual date) is explicitly out of scope per Issue #38's "no
  cashflow generation" boundary; only the raw lag count is a deal term.
- `yield_convention` (`BondYieldConvention`) — this is Bond Master reference
  data (`docs/14` F-08), not a term the option buyer/seller negotiates. It
  belongs on the bond's static record, resolved through the snapshot at
  pricing time, not baked into the deal.
- `compounding_frequency` (`m`) — same reasoning as `yield_convention`;
  derived from Bond Master data, not a deal term.
- `accrued_interest`, `cashflows`, `clean_price` / `dirty_price`, `bond_yield`
  — all market/valuation outputs or Bond Master-derived quantities, never
  deal terms.
- `vol`, `credit_spread` — market data (SPEC §B.3/§B.4).
- `curve_id` / `option_discount_curve_id` / `bond_reference_curve_id` /
  `deposit_curve_id` — market-data references (SPEC §6.1 "Market Data"
  section), never product-schema fields, exactly like the existing
  `FXSwap.near_rate` design note ("frozen trade terms, not live market
  data") but the reverse case: these are *not* even frozen deal terms, they
  are live lookups.
- `market_data_snapshot_id` or any snapshot reference — a product definition
  must never carry a pointer back to a specific market state (`docs/04`,
  "Keep [product definition and market snapshot] separate").
- Any pricing output or Greek (`pv`, `dv01`, `gamma`, `vega`, ...).

### 2.3 Payoff-basis / strike cross-field rule (schema-level only)

Issue #38 requires "valid expiry / settlement relationship" and "supported
payoff / exercise / settlement combinations" to be validated. The one
concrete cross-field rule identified here, following the existing
`_validate_common` pattern (e.g. `InterestRateSwap`'s
"opposite pay/receive directions" check), is:

- If `payoff_basis is PayoffBasis.PRICE`: `strike_price` must be present
  (not `None`, positive) and `strike_yield` must be `None`.
- If `payoff_basis is PayoffBasis.YIELD`: `strike_yield` must be present
  (a real number; unlike a price, a yield may legitimately be negative or
  zero, so no positivity check) and `strike_price` must be `None`.
- `exercise_start_date` must be `None` when `exercise_style is
  ExerciseStyle.EUROPEAN`, and a valid `YYYY-MM-DD` strictly before
  `expiry_date` when `exercise_style is ExerciseStyle.AMERICAN`.

None of this requires `day_count`, a calendar, or market data — it is pure
enum/date/sign cross-checking, the same category of validation the existing
schemas already do (`docs/04` schema design rule 6: "strict enough for
pricing but readable enough for AI agents").

**Conclusion for `BondOption` alone: yes, it can be defined as a pure
deal-term schema with no unresolved `DayCount` / Bond Master convention
field.** Nothing in its natural field list (§2.1) requires one; the fields
that would (`day_count`, `yield_convention`, `compounding_frequency`) are all
Bond Master reference data and are already correctly out of scope per
Issue #38's own text.

---

## 3. Structured wrapper boundary analysis — `BondLinkedStructuredProduct`

### 3.1 Can it be a minimal wrapper?

SPEC §6.1.1 "Deposit Leg" lists: Deposit Notional, Deposit Currency, Start
Date, Maturity Date, Tenor, **Deposit Rate / Yield**, **Day Count**,
**Business Day Convention**, Principal Repayment Rule, **Deposit Curve ID**.

A **minimal** wrapper that stays inside the resolved boundary (§2) can carry:

| Field | Type / enum | Notes |
| --- | --- | --- |
| `product_id` | `str` | non-blank |
| `deposit_notional` | `float` | positive |
| `deposit_currency` | `Currency` | deal term, matches the existing `Currency` pattern |
| `start_date` / `maturity_date` | `str` (`YYYY-MM-DD`) | **only** if the wrapper does not need to compute a day-count fraction from them — i.e. they are recorded as trade-defining dates, not fed into an accrual formula at schema level. This is safe as long as no schema-level validation attempts a day-count computation (see §3.2). |
| `bond_option` | embedded `BondOption` | mirrors the `CrossCurrencyLeg(leg: FixedLeg | FloatingLeg)` composition pattern already used by `CrossCurrencySwap` — the wrapper references/embeds a frozen `BondOption`, not a duplicate copy of its fields |
| `participation_ratio` | `float` | `= Bond Option Notional / Deposit Notional` per SPEC §6.1; can be validated for consistency against the embedded `bond_option.notional` and `deposit_notional`, or simply stored and cross-checked — either way this is schema-level arithmetic, not pricing |
| `position` (structured-product-level, if distinct from the embedded option's `Position`) | — | SPEC's "sold bond option" pattern (`docs/13`/`docs/14` reuse-invariant: "deposit leg + **sold** bond option") suggests the wrapper's embedded `bond_option.position` is fixed to `SELL` from the structured product's perspective; this can be a `__post_init__` check, not a new field |

### 3.2 What must be excluded from the wrapper (flagged explicitly)

The following SPEC §6.1 "Deposit Leg" fields **cannot** be added to the #38
wrapper without pulling in the unresolved `DayCount` decision or live market
data, and must be **explicitly flagged and deferred**, not silently added or
silently reusing the existing rates-core `DayCount` enum:

| SPEC §6.1 field | Why it cannot land in #38 |
| --- | --- |
| **Day Count** (deposit leg accrual convention) | This is exactly the unresolved vocabulary decision (`docs/14` A-14 / Issue #37). Reusing the existing `DayCount` enum here would be the "silent coercion" AGENTS.md/Issue #37 explicitly forbids — the deposit leg's accrual convention has not been reconciled against Annex A/B any more than the bond leg's has. |
| **Business Day Convention** (deposit leg) | Depends on a settlement calendar to resolve, which Issue #38 already excludes ("no cashflow generation"); also entangled with the same day-count/calendar prerequisite. |
| **Deposit Rate / Yield** | This is either a live market-data lookup (deposit curve) or, if entered as a fixed trade term, still requires a day-count convention to turn into an accrual amount — either path is out of scope until Day Count is resolved or the field is explicitly scoped as "recorded but not used for computation here" (which would be a confusing half-field, not recommended). |
| **Deposit Curve ID** | Pure market-data reference (SPEC §6.1 "Market Data" section) — must never live in a product schema, per `docs/04`. |
| **Principal Repayment Rule** | Not inherently blocked by `DayCount`, but it is deposit-leg cashflow-generation logic (how/when principal is returned), which Issue #38's "no cashflow generation" scope excludes. Worth flagging as a separate future field, not a `DayCount`-blocked one. |

**If a full deposit-leg schema (with real accrual mechanics) is wanted, it
must be split into a later prerequisite or later issue** — it must not be
silently added to #38 by reusing the existing rates-core `DayCount` enum
just because it happens to type-check. That would repeat exactly the
"silently coerced to the wrong convention" failure mode Issue #37 was opened
to prevent (`docs/14` F-16).

**Conclusion for `BondLinkedStructuredProduct`: a genuinely minimal wrapper
(§3.1) is safe for #38.** A full SPEC §6.1-shaped deposit leg is **not**
safe for #38 as currently unblocked, because its Day Count / Business Day
Convention / Deposit Curve fields hit the same unresolved decision. The
wrapper's dates (`start_date` / `maturity_date`) may be recorded, but no
schema-level day-count arithmetic may be attached to them in this issue.

---

## 4. Decision matrix

| Field | Outcome |
| --- | --- |
| `product_id`, `underlying_isin` | **Safe for #38** — pure identifiers |
| `payoff_basis`, `option_type`, `exercise_style`, `settlement_type`, `position` | **Safe for #38** — enums already landed in PR #45 |
| `strike_price` / `strike_yield` (cross-validated against `payoff_basis`) | **Safe for #38** — schema-level cross-field check, no market data |
| `expiry_date`, `exercise_start_date` | **Safe for #38** — strict `YYYY-MM-DD`, schema-level date ordering only |
| `settlement_lag_days` (raw integer) | **Safe for #38** — recorded as a plain trade term, not resolved against a calendar |
| `notional` (bond option face amount) | **Safe for #38** — positive-number check, same pattern as existing products |
| `deposit_notional`, `deposit_currency`, `participation_ratio` (wrapper) | **Safe for #38** — pure deal-term arithmetic/identifiers, no accrual |
| `bond option currency` (if fixed as a deal term rather than defaulted from the bond) | **Safe for #38**, with a documented assumption that "defaults to bond currency" (SPEC §6.1) is a market-data-resolution rule applied later, not schema logic |
| `coupon`, `coupon_frequency`, `bond maturity_date`, `issue_date` | **Must be excluded from #38** — Bond Master static data, later issue |
| `yield_convention`, `compounding_frequency` (`m`) | **Requires the `DayCount` / Bond Master convention decision first** — do not add to #38 even as an optional field |
| `day_count` (bond leg or deposit leg) | **Requires the `DayCount` / Bond Master convention decision first** — the exact field this preflight exists to keep out of #38 |
| `business_day_convention` for calendar-resolved settlement / deposit accrual | **Requires the `DayCount` / Bond Master convention decision first** (entangled with day-count/calendar prerequisites) |
| `deposit rate/yield`, `principal repayment rule` (full deposit-leg accrual) | **Requires the `DayCount` / Bond Master convention decision first**, or must be deferred to a later issue regardless |
| `accrued_interest`, `clean_price`/`dirty_price`, `bond_yield` | **Market data — must never live in product schema** |
| `vol`, `credit_spread` | **Market data — must never live in product schema** |
| any `curve_id` (yield curve, option discount curve, bond reference curve, deposit curve) | **Market data — must never live in product schema** |
| `market_data_snapshot_id` / snapshot reference | **Market data — must never live in product schema** (a product must not point back at a specific market state) |
| pricing outputs (`pv`, Greeks, self-validation results) | **Market data / pricing output — must never live in product schema** |

---

## 5. Recommended implementation path

**#38 is safe to proceed, narrowly scoped.** The recommended smallest
follow-up code slice for the Issue #38 implementation PR:

1. **`BondOption` schema first** — the full field list in §2.1, with the
   cross-field validation in §2.3. This alone satisfies most of Issue #38's
   stated acceptance criteria (valid European cash-settled price-based
   schema; valid physical-delivery schema if represented at schema level;
   invalid enums rejected; product schema rejects market-data fields).
2. **A deliberately minimal `BondLinkedStructuredProduct` wrapper**, using
   only the fields in §3.1 (deposit notional/currency, start/maturity dates
   recorded but not used for accrual, an embedded frozen `BondOption`,
   `participation_ratio`). This wrapper must **not** attempt a real
   deposit-leg accrual schema (§3.2) — no `day_count`, no
   `business_day_convention` tied to calendar resolution, no `deposit
   rate/yield`, no `deposit curve_id`.
3. If the desk/maintainer wants the **full** SPEC §6.1 deposit leg (with real
   Day Count / Business Day Convention / accrual), that must be **split into
   a new, later issue** that depends on the `DayCount` vocabulary decision
   (A-14) being made first — it is not part of #38's safe scope.

**Be explicit in the #38 implementation PR:** this must not become a stealth
Bond Master schema (no coupon/yield-convention/compounding fields), a
market-data snapshot change (no curve/vol/spread/snapshot-id fields), or a
pricing engine (no PV/Greeks/self-validation fields). If the minimal wrapper
in step 2 turns out, during implementation, to need *any* field from the
"requires DayCount decision first" or "market data" rows of §4, that field
must be dropped from the PR and recorded as deferred — not added and
rationalized after the fact.

---

## 6. Proposed acceptance criteria for the later #38 implementation PR

Not written yet — recorded here as the test list a future implementation
slice should satisfy, mirroring the existing `tests/test_products.py` /
`tests/test_products_ccs_fxswap.py` style (`coerce_enum` rejection pattern,
strict `YYYY-MM-DD`, positive-notional checks):

- Valid **European, cash-settled, price-based** bond option schema
  constructs successfully (`payoff_basis=PRICE`, `settlement_type=CASH`,
  `exercise_style=EUROPEAN`, `strike_price` set, `strike_yield=None`,
  `exercise_start_date=None`).
- Valid **yield-based** bond option schema constructs successfully
  (`payoff_basis=YIELD`, `strike_yield` set, `strike_price=None`).
- Valid **physical-delivery** option schema constructs successfully, if
  represented at schema level (`settlement_type=PHYSICAL`).
- Invalid enum strings for `payoff_basis` / `option_type` / `exercise_style`
  / `settlement_type` / `position` are rejected through the existing
  `coerce_enum` path (same error-message pattern as
  `test_invalid_currency_string_rejected` etc.).
- Positive `notional` (and `deposit_notional` for the wrapper) is required;
  zero/negative rejected.
- Strict `YYYY-MM-DD` dates for `expiry_date`, `exercise_start_date`,
  `start_date`, `maturity_date` (compact and ISO-week forms rejected, per
  the existing `_parse_iso_date` tests).
- `expiry_date` / `exercise_start_date` / settlement relationship validated
  **only at schema level**: `exercise_start_date` must be `None` for
  `EUROPEAN` and strictly before `expiry_date` for `AMERICAN`; no calendar
  or market-data lookup involved.
- `strike_price` is **required** when `payoff_basis is PayoffBasis.PRICE`
  (and `strike_yield` must be absent).
- `strike_yield` is **required** when `payoff_basis is PayoffBasis.YIELD`
  (and `strike_price` must be absent).
- Product schema **rejects or does not expose** any market-data field
  (no constructor parameter for `clean_price`, `bond_yield`, `vol`,
  `credit_spread`, any `curve_id`, or a snapshot reference).
- Product schema **rejects or does not expose** any Bond Master static
  field (no constructor parameter for `coupon`, `coupon_frequency`, bond
  `maturity_date`, `issue_date`, `yield_convention`, or `compounding
  frequency`).
- **No `DayCount` or Bond Master convention field appears anywhere in either
  schema** unless the prerequisite `DayCount` decision has already landed
  (it has not, as of this preflight) — this should be a standing assertion
  in the test suite (mirroring the "`test_act_365_variants_are_not_added_to_
  day_count`" style guard already added in `tests/test_bli_enums.py` for the
  enum layer), not a one-time check.
- `BondLinkedStructuredProduct` wrapper: constructing with a non-`BondOption`
  value for its embedded option field raises `TypeError` (mirrors
  `CrossCurrencyLeg`'s `leg` type check); `participation_ratio` is
  positive; the wrapper does not accept a `day_count`, `business_day_
  convention`, `deposit_rate`, or `deposit_curve_id` constructor argument.

---

## 7. Files updated by this PR

- Added `docs/15_bli_product_schema_preflight_issue_38.md` (this file).
- Added a short checkpoint entry to `docs/00_development_log.md`.
- Added a short pointer under `docs/09_mvp_core_runbook.md` §9.
- The four frozen BLI source spec files under
  `docs/bond_linked_structured_pricer/` are **not** edited.
- No source code or tests are added or modified in this PR.

---

## 8. Scope boundaries of this PR

Docs only. No product schema code, no tests, no schema registration/export
changes, no pricing engine, no market-data snapshot, no Bond Master schema.
No `DayCount` enum decision is made here — §3.2/§4 document that the
decision is still required before a full deposit-leg schema can land, but
this PR does not make that decision or implement the split. Issues #39–#42
are not started. Issue #44 (Black-76) is not started.
