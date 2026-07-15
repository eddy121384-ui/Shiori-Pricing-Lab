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
- **Issue #37 remains open.** The development log and runbook
  (checkpoint after PR #46; both removed, see git history) both recorded that the
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
scope is actually achievable for **both** proposed schemas. It is achievable
for `BondOption` (§2). For the structured wrapper, the answer is more
nuanced than a single `DayCount` blocker: the deposit leg also carries
**contractual economic terms** (deposit rate/yield, principal repayment
rule) that a schema cannot simply omit and still call itself a complete,
valuation-meaningful `BondLinkedStructuredProduct` — see §3.

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

> **Revised per Codex P2 review.** The first version of this preflight
> described a "minimal wrapper" (deposit notional/currency/dates, embedded
> `BondOption`, `participation_ratio`) as **safe for #38**. That was too
> optimistic. `Deposit Rate/Yield` and `Principal Repayment Rule` are
> **contractual deposit-leg terms that determine the customer's return and
> are needed to reproduce the deposit leg's cashflows** — they are not
> optional decoration on top of a shell. A wrapper that omits them cannot be
> called a complete, valuation-meaningful `BondLinkedStructuredProduct`; it
> can, at most, be a clearly-labeled **non-economic relationship shell**.
> §3.1–§3.3 below replace the original analysis.

### 3.1 Why a "minimal wrapper" is not a safe economic schema

SPEC §6.1.1 "Deposit Leg" lists: Deposit Notional, Deposit Currency, Start
Date, Maturity Date, Tenor, **Deposit Rate / Yield**, **Day Count**,
**Business Day Convention**, **Principal Repayment Rule**, **Deposit Curve
ID**. Of these, `Deposit Rate/Yield` and `Principal Repayment Rule` are not
market data and not calendar/day-count mechanics — they are the terms that
define what the customer actually receives back. A structured-product
schema that carries `deposit_notional` and `deposit_currency` but not the
rate/yield or the repayment rule cannot reproduce the deposit leg's
cashflows, so it is not an economic representation of the trade — it is a
container that happens to reference a `BondOption` and hold a notional.

Two consequences follow, kept separate because they have different fixes:

1. **`Deposit Rate/Yield` and `Principal Repayment Rule` are contractual
   economic terms, not "market data to exclude."** They must not be
   silently dropped from the schema and forgotten — they must be
   **explicitly deferred** until their source and mechanics are decided
   (see §3.2).
2. Separately, **`Day Count` and `Business Day Convention`** remain blocked
   by the unresolved Issue #37 vocabulary decision (A-14), independent of
   whether the deposit rate is ever added.

### 3.2 Field-by-field classification for the structured wrapper

| SPEC §6.1 field | Classification | Reasoning |
| --- | --- | --- |
| `deposit_notional`, `deposit_currency` | Safe deal term | Plain identifiers/amounts, no accrual math, no market-data lookup. |
| `start_date` / `maturity_date` | Safe deal term, **only if recorded and not used for accrual** | Storing the dates is fine; computing a day-count fraction from them at schema level is not (that requires the blocked `day_count` decision). |
| `bond_option` (embedded) | Safe — composition | Mirrors the existing `CrossCurrencyLeg(leg: FixedLeg | FloatingLeg)` pattern: reference a frozen `BondOption`, no duplicated fields. |
| **`deposit_rate` / `deposit_yield`** | **Ambiguous — must be resolved before a complete wrapper lands, not treated as simple "market data."** | Two distinct cases exist and the spec does not say which applies: (a) a rate **fixed at trade time** as a genuine contractual term (in which case it belongs on the schema, like `FXSwap.near_rate`, but still needs a day-count convention to turn into an accrual amount — blocked by A-14); or (b) a rate **resolved from an internal funding/deposit curve** at pricing time (in which case it is a market/funding-data input and must never live on the product schema, per `docs/04`). Until this ambiguity is resolved, the field cannot be safely added either way. |
| **`principal_repayment_rule`** | **Contractual economic term — defer from #38, not optional decoration.** | Needed to reproduce how/when principal is returned (bullet vs. amortizing vs. linked-to-option-outcome). Previously mischaracterized as "cashflow-generation logic to exclude"; it is a deal term the trade cannot be economically reproduced without. Deferring it is correct, but it must be tracked as a **known gap in a non-economic wrapper**, not silently omitted. |
| `day_count`, `business_day_convention` (deposit leg) | **Still blocked by the unresolved `DayCount`/calendar convention decision (A-14).** | Unchanged from the original analysis — reusing the existing rates-core `DayCount` enum here would repeat the exact "silently coerced to the wrong convention" failure Issue #37 exists to prevent (`docs/14` F-16). |
| `deposit_curve_id` / any funding-curve reference | **Not a product-schema field unless a later market-data/funding-curve design explicitly models an internal funding/deposit curve reference.** | Pure market-data/funding-data reference (SPEC §6.1 "Market Data" section); must never live in a product schema per `docs/04`, and no such funding-curve design exists yet. |
| `participation_ratio` | Safe **only if enforced as a derived/consistency-checked quantity**, not a freely-set field | See §3.3 — storing it independently of `bond_option.notional` and `deposit_notional` would allow silently contradictory terms. |

### 3.3 `participation_ratio` must be enforced, not merely positive

SPEC §6.1 defines `participation_ratio = Bond Option Notional / Deposit
Notional`. A schema that stores `participation_ratio` as an independent
field and only checks that it is positive would allow, for example,
`deposit_notional=100`, `bond_option.notional=50`, `participation_ratio=2` —
three internally contradictory numbers that all pass a naive positivity
check. That is not a safe schema, even inside a non-economic shell. A future
implementation must do **one** of:

- **derive** `participation_ratio` from `bond_option.notional /
  deposit_notional` (no independent field at all), or
- **validate** that a stored `participation_ratio` equals
  `bond_option.notional / deposit_notional` within an explicit, documented
  tolerance, rejecting mismatches.

### 3.4 Revised conclusion for `BondLinkedStructuredProduct`

**`BondLinkedStructuredProduct` should not be described as safe for #38 as
a complete economic schema.** A wrapper containing only deposit
notional/currency/dates, an embedded `BondOption`, and a
consistency-enforced `participation_ratio` may be documented as a
**non-economic container / relationship shell** — useful for expressing
"this option is sold as part of a structured note against this deposit,"
but explicitly **not sufficient** for valuation, because it cannot
reproduce the customer's actual return (no deposit rate/yield, no principal
repayment rule).

- If maintainers want `BondLinkedStructuredProduct` in #38 at all, it must
  be built and labeled as this **non-economic placeholder**, with an
  explicit code comment and test asserting it is incomplete for valuation.
- A **real, economic** `BondLinkedStructuredProduct` — one that can
  reproduce customer cashflows — must be **deferred** until the deposit-leg
  boundary is resolved: the deposit rate/yield source (fixed term vs.
  funding-curve lookup), the principal repayment rule, and the
  `DayCount`/calendar convention decision (A-14). That resolution belongs in
  a separate, reviewed slice — not folded into #38 by omission.

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
| `deposit_notional`, `deposit_currency` | **Safe for #38** — pure deal-term identifiers/amounts, no accrual |
| `bond option currency` (if fixed as a deal term rather than defaulted from the bond) | **Safe for #38**, with a documented assumption that "defaults to bond currency" (SPEC §6.1) is a market-data-resolution rule applied later, not schema logic |
| `coupon`, `coupon_frequency`, `bond maturity_date`, `issue_date` | **Must be excluded from #38** — Bond Master static data, later issue |
| `yield_convention`, `compounding_frequency` (`m`) | **Requires the `DayCount` / Bond Master convention decision first** — do not add to #38 even as an optional field |
| `day_count` (bond leg or deposit leg) | **Requires the `DayCount` / Bond Master convention decision first** — the exact field this preflight exists to keep out of #38 |
| `business_day_convention` for calendar-resolved settlement / deposit accrual | **Requires the `DayCount` / Bond Master convention decision first** (entangled with day-count/calendar prerequisites) |
| `deposit_rate` / `deposit_yield` | **Ambiguous — resolve before a complete wrapper lands.** May be a fixed contractual term (blocked by A-14 to compute an accrual) or a funding-curve lookup (market data, never schema). Not simply "market data" in all cases — do not classify it as either safe or excluded without first resolving which case applies (§3.2). |
| `principal_repayment_rule` | **Contractual economic term — defer from #38.** Required to reproduce the deposit leg's cashflows; not optional decoration and not blocked by `DayCount` specifically, but still out of #38's safe scope (§3.2). |
| `participation_ratio` (wrapper) | **Safe only if derived from `bond_option.notional / deposit_notional`, or validated to equal it within a documented tolerance** — a freely-set, independently-stored value is unsafe (§3.3) |
| `deposit_curve_id` / any funding-curve reference | **Market data — must never live in product schema**, unless a later market-data/funding-curve design explicitly models it as such a reference (still not a product-schema field even then) |
| `accrued_interest`, `clean_price`/`dirty_price`, `bond_yield` | **Market data — must never live in product schema** |
| `vol`, `credit_spread` | **Market data — must never live in product schema** |
| any `curve_id` (yield curve, option discount curve, bond reference curve) | **Market data — must never live in product schema** |
| `market_data_snapshot_id` / snapshot reference | **Market data — must never live in product schema** (a product must not point back at a specific market state) |
| pricing outputs (`pv`, Greeks, self-validation results) | **Market data / pricing output — must never live in product schema** |
| **complete, valuation-meaningful `BondLinkedStructuredProduct`** (deposit rate/yield + principal repayment rule + day count/calendar all resolved) | **Defer until the deposit-leg contractual terms and the `DayCount`/funding-curve boundary are resolved in a separate, reviewed slice** |
| **non-economic `BondLinkedStructuredProduct` relationship shell** (deposit notional/currency/dates, embedded `BondOption`, enforced `participation_ratio`, no rate/repayment terms) | **Possible for #38 only if explicitly labeled incomplete / non-economic / not sufficient for valuation**, and only if maintainers accept that limitation |

---

## 5. Recommended implementation path

**`BondOption` is safe to proceed, narrowly scoped. `BondLinkedStructuredProduct`
is not, unless explicitly scoped as a non-economic placeholder.** The
recommended #38 implementation PR:

1. **Implement `BondOption` first** — the full field list in §2.1, with the
   cross-field validation in §2.3. This alone satisfies most of Issue #38's
   stated acceptance criteria (valid European cash-settled price-based
   schema; valid physical-delivery schema if represented at schema level;
   invalid enums rejected; product schema rejects market-data fields).
2. **Do not implement `BondLinkedStructuredProduct` in #38** unless
   maintainers explicitly accept it as a **non-economic placeholder** —
   deposit notional/currency/dates, an embedded frozen `BondOption`, and a
   consistency-enforced `participation_ratio` (§3.3), with no attempt at
   `deposit_rate`/`deposit_yield`, `principal_repayment_rule`, `day_count`,
   `business_day_convention`, or `deposit_curve_id` (§3.2). If built, the PR
   must label it in code and tests as **incomplete for valuation**, not
   a finished structured-product schema.
3. **Prefer deferring `BondLinkedStructuredProduct` entirely** to a later
   issue, after resolving:
   - the deposit-leg economic terms (deposit rate/yield source, principal
     repayment rule);
   - the Treasury FTP / funding-curve vs. file-import-FTP terminology
     (i.e., whether a deposit rate is a funding-desk input or a trade-level
     fixed term — this ambiguity is unresolved, see §3.2);
   - the `DayCount` / calendar convention decision (A-14).

**Be explicit in the #38 implementation PR:** this must not become a stealth
Bond Master schema (no coupon/yield-convention/compounding fields), a
market-data snapshot change (no curve/vol/spread/snapshot-id fields), a
pricing engine (no PV/Greeks/self-validation fields), or an unlabeled
economic structured-product schema that silently omits the deposit rate and
repayment terms it would need to be valuation-meaningful. If a wrapper is
built and turns out, during implementation, to need *any* field from the
"requires DayCount decision first," "ambiguous," or "market data" rows of
§4, that field must be dropped from the PR and recorded as deferred — not
added and rationalized after the fact.

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
- **If `BondLinkedStructuredProduct` is implemented at all**, it must be
  built and tested as the **non-economic placeholder** described in §3.4,
  not a complete economic schema:
  - constructing with a non-`BondOption` value for its embedded option
    field raises `TypeError` (mirrors `CrossCurrencyLeg`'s `leg` type
    check);
  - the wrapper does not accept a `day_count`, `business_day_convention`,
    `deposit_rate`, `deposit_yield`, `principal_repayment_rule`, or
    `deposit_curve_id` constructor argument;
  - **`participation_ratio` enforcement (§3.3):** either the field does not
    exist and is derived as `bond_option.notional / deposit_notional` at
    construction time, or a stored `participation_ratio` is validated to
    equal that ratio within an explicit, documented tolerance — a bare
    positivity check is not sufficient;
  - a test **rejects contradictory terms**, e.g.
    `deposit_notional=100`, `bond_option.notional=50`,
    `participation_ratio=2` (which implies a ratio of `0.5`, not `2`) must
    raise, not construct silently;
  - a standing test/comment asserts the wrapper is **incomplete for
    valuation** (no deposit rate/yield, no principal repayment rule) — a
    complete economic structured-product schema must preserve all
    contractual terms required to reproduce customer cashflows, or else be
    explicitly labeled incomplete / non-economic; this assertion should not
    be silently dropped if the wrapper is later extended.

---

## 7. Files updated by this PR

- Added `docs/15_bli_product_schema_preflight_issue_38.md` (this file).
- Added a short checkpoint entry to the development log (removed, see git history).
- Added a short pointer under the runbook §9 (removed, see git history).
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
