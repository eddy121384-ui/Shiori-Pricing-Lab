# 32 — USD SOFR OIS convention contract + resolved swap representation (Issue #224)

Parent: #222 — C++ Rates Engine Foundation.
This issue: #224 — METHODOLOGY / EVIDENCE. No pricing implementation.

| Field | Value |
|---|---|
| Issue | #224 |
| Owner | OpenCode (primary repository execution agent) |
| Branch | `arch/224-usd-sofr-ois-convention` |
| Base SHA | `7af4cbb3bba6ceeff00968c5f9ac53967502bd20` (main, includes merged #223 + governance #240) |
| Scope | Convention audit + `SwapTrade` / `ConventionSet` / `ResolvedSwap` methodology boundary + RED-02 evidence list |
| Non-goals | No pricing implementation, no C++ code, no Bloomberg plumbing changes, no #225/#228 implementation |

Methodology authority: Sophira. Final merge authority: Eddy. Independent reviewer: Codex.
Architecture baseline: #223 as merged (`docs/31_rates_reuse_boundary_223.md`).
AGENTS.md compliance: smallest coherent change (one document), reuse before adding, no fabricated market data or Bloomberg evidence, no methodology guesses, deterministic-tests + Eddy approval required for any pricing-method change, no merge without explicit approval.

---

## 1. Goal and stop conditions

Goal: define the proposed `USD_SOFR_OIS_V1` convention contract shape and the methodology boundary for `SwapTrade + ConventionSet -> ResolvedSwap`, so that #228 can later implement the approved deterministic resolution and the C++ Pricing Kernel can consume resolved inputs.

Scope (per #224): trade economics vs resolved trade separation; effective date / spot lag; fixed-leg frequency and day count; SOFR floating-leg compounding mechanics; fixing / observation semantics; payment lag; calendars / BDC; stub handling; schedule and cash-flow resolution rules; required Bloomberg/workstation evidence for production convention lock.

Stop conditions (binding): any unresolved production convention requiring a methodology choice = STOP; any conflict between existing Shiori evidence and Bloomberg/workstation behavior = STOP; any temptation to infer missing values from QuantLib defaults, public-market convention, or legacy IRS behavior = STOP and ask Eddy. This document therefore locks NO convention value. Every production-relevant dimension below is classified CONFIRMED, REFERENCE ONLY, CONFLICT, or UNRESOLVED — RED-02.

---

## 2. Conceptual responsibilities: SwapTrade vs ConventionSet vs ResolvedSwap

These are methodology contracts owned by #224 (this issue). Field-level schemas for market/curve/fixing/vol/exercise/settlement/results remain owned by #225. Deterministic schedule/fixing/calendar primitives implementing the approved contract belong to #228. Nothing in this section is implemented here.

### 2.1 SwapTrade — "what did the desk trade?"

`SwapTrade` records raw trade economics as dealt, before any convention resolution:

- identifiers (`product_id`), direction (pay/receive fixed), currency (`USD` for V1), notional.
- raw `trade_date` (ISO date) — the anchor for spot-start resolution (owner decision R1, RESOLVED).
- `maturity_date` as a stated deal term (ISO date, order-validated only).
- leg economics as stated: fixed rate, floating index (`USD_SOFR`), spread. Canonical units (owner decision, RESOLVED): `fixed_rate` is a decimal annual rate (4.25% → 0.0425, consistent with the existing `FixedLeg` contract `products/legs.py:30-34`); `spread` is likewise a decimal annual rate (1 bp → 0.0001, consistent with the existing `FloatingLeg.spread` "decimal spread" contract `products/legs.py:70-71`). Python/Bloomberg/UI layers may originate or display rates in other units, but they must normalize to decimal before the canonical Python/C++ Rates boundary. The canonical Rates contract must not accept ambiguous untagged percent or basis-point numeric representations.
- Explicitly NOT included: `effective_date` (resolved output for V1 — see below), any derived schedule, any adjusted date, any accrued fraction, any fixing, any forward, any curve, any valuation date.

`SwapTrade` never contains a date that was computed from a lag, a calendar, or a convention. If a date was derived, it belongs in `ResolvedSwap` with its derivation recorded, not in `SwapTrade`. `SwapTrade` contains actual trade economics, not duplicated convention defaults: convention-like labels (payment frequencies, day-count labels, compounding-method label, reset label, BDC label) are NOT V1 trade inputs — `ConventionSet` is authoritative for standard production convention terms (owner decision R2, RESOLVED).

Owner decisions R1/R2 (Eddy/Sophira, RESOLVED for V1 — AGENTS.md rule 15 no longer blocks these two): R1 — V1 supports standard spot-starting USD SOFR OIS only; `trade_date + approved ConventionSet -> resolved effective date`, so the effective date is produced by convention resolution and belongs in `ResolvedSwap`; forward-starting swaps are explicitly OUT OF SCOPE for V1, must fail closed, and their final semantics are not designed in this issue. R2 — `ConventionSet` is authoritative for standard production convention terms (fixed-leg and floating-leg payment frequencies, day counts, calendar, business-day convention, spot lag, payment lag, SOFR compounding/observation rules, stub policy); V1 does NOT support arbitrary per-trade convention overrides; incoming or legacy-adapted trade data containing convention-like values that conflict with the approved `ConventionSet` must fail closed; trade data must never silently override `ConventionSet`. Canonical rate units are likewise RESOLVED: `fixed_rate` and `spread` are decimal annual rates at the canonical boundary. With R1/R2 decided, `SwapTrade + ConventionSet -> ResolvedSwap` is deterministic for the V1 scope (all remaining UNRESOLVED items are convention VALUES in §3/§6, not contract-shape decisions).

### 2.2 ConventionSet (`USD_SOFR_OIS_V1`) — "under which approved rules is the trade resolved?"

`ConventionSet` is the versioned, workstation-reconciled rulebook that turns a `SwapTrade` into a `ResolvedSwap`. Version `USD_SOFR_OIS_V1` is PROPOSED in §6 with every value UNRESOLVED (RED-02). A future locked version may only fill a value with supporting repository/workstation evidence cited in this document's §3–§5 terms.

`ConventionSet` owns: spot-lag rule (trade date → effective date), fixed-leg frequency/day-count/coupon-formula authority, floating-leg payment-frequency authority, floating-leg compounding mechanics + formula, fixing/observation semantics (lookback/shift/lockout/cutoff), per-leg payment-lag rules, notional/principal-exchange behavior, calendar authority, BDC resolution rule, stub rule, overnight compounding convention. It owns NO market data, NO curves, NO fixings, NO model.

Authority (owner decision R2, RESOLVED for V1): `ConventionSet` is authoritative for all standard production convention terms above. Calendar / BDC authority is role-capable by contract shape (owner decision, RESOLVED as shape): the contract must be able to represent resolution rules by role — at minimum spot / effective-date resolution, fixed-leg schedule resolution, floating-leg schedule resolution, fixed-leg payment-lag counting, floating-leg payment-lag counting, fixed-leg payment-date resolution, floating-leg payment-date resolution, and SOFR observation / fixing-date resolution — without assuming any two roles share the same calendar or adjustment rule. Actual per-role calendar / BDC VALUES remain UNRESOLVED — RED-02 (D7/D8). V1 does NOT support arbitrary per-trade convention overrides. If incoming or legacy-adapted trade data contains convention-like values that conflict with the approved `ConventionSet`, the resolver must fail closed. Trade data must never silently override `ConventionSet`.

### 2.3 ResolvedSwap — "what exactly will the kernel price?"

`ResolvedSwap` is the fully deterministic output of `SwapTrade + ConventionSet` resolution — and, per owner decision (RESOLVED), the complete resolved trade input for the pricing kernel:

- retained immutable normalized trade economics (canonical units per §2.1): product identity (`product_id`), currency, notional, pay/receive direction, fixed rate, spread, floating index. The kernel receives these from `ResolvedSwap` and does NOT need the original raw `SwapTrade` alongside it — there are no two competing authoritative copies of the trade at the pricing boundary.

- resolved `effective_date` produced from raw `trade_date` + the approved spot-lag rule, with the rule recorded (owner decision R1, RESOLVED for V1).
- explicit period schedules per leg (start/end dates, all calendar/BDC adjustments applied and recorded).
- per-period accrual fractions with the day-count rule recorded, including which boundaries were used. Whether accrual day-count uses unadjusted schedule boundaries or adjusted dates is UNRESOLVED — RED-02 (part of D3) and is not chosen here; `ResolvedSwap` must therefore preserve both the unadjusted schedule boundaries and the adjusted dates for every period of each leg, so the eventually approved rule is replayable and auditable from the resolved output alone.
- compounding / observation / fixing calendar per period (observation dates, weights, lockout/cutoff application) with the rule recorded; each observation mechanics item (shift/lag mechanism, lookback, lockout, cutoff) preserved with its value and unit/semantics independently.
- floating-coupon spread treatment recorded explicitly once approved; the treatment itself is UNRESOLVED — RED-02 (part of D4): spread added outside compounding vs compounded inside the daily factors vs accrued separately. No formula is chosen here.
- fixed-leg coupon formula resolved per the approved rule, with each coupon's derivation recorded. Whether the coupon is simple accrual, compounded, or another evidenced convention is UNRESOLVED — RED-02 (new dimension D13) and is not chosen here; the reference engine computes fixed coupons as `notional × fixed_rate × accrual` today, but that is REFERENCE ONLY term behavior, not a V1 decision.
- payment dates per leg with full ordering provenance recorded: for each leg, the lag base date, accrual-end adjustment, payment-lag application, and payment-date business-day adjustment, in the approved sequence. That sequence — including which date each leg's lag counts from — is UNRESOLVED — RED-02 (part of D6) and is not chosen here; the two legs' payment rules are independent unless later evidence shows otherwise, and `ResolvedSwap` must represent each leg's eventual resolved ordering explicitly so #228 cannot silently invent schedule/accrual semantics.
- principal-exchange behavior resolved per the approved rule, with any resulting principal cashflows recorded with provenance. Whether principal is exchanged at the effective date, at maturity, both, or neither is UNRESOLVED — RED-02 (new dimension D12) and is not chosen here.
- stub periods, if the approved stub rule permits them, with the rule recorded.
- full provenance: which `ConventionSet` version resolved it, and which rule produced each derived date/number.

Resolution is total and fail-closed: anything the approved `ConventionSet` cannot resolve (e.g. a non-clean schedule under a no-stub rule) is refused with a named reason, never silently filled. Forward-starting swaps are OUT OF SCOPE for V1 and any forward-starting input must fail closed (owner decision R1; final forward-start semantics are not designed here). Conflicting convention-like trade input must fail closed and never silently override `ConventionSet` (owner decision R2). `ResolvedSwap` carries no market snapshot, no curve, no fixing values, no model config — those arrive separately at the kernel boundary per #223 (`ResolvedSwap + resolved curve input(s) + Fixings + Model Inputs -> Results`).

### 2.4 Authority rule (from #223, restated, not changed)

The C++ Rates module owns deterministic Convention / Trade Resolution; #228 implements the primitives approved here. Python may serialize, persist, display, replay, and orchestrate `ResolvedSwap` objects. Python must NOT become the authoritative Rates schedule-generation implementation for new Rates products. The legacy `pricing/schedule.py::generate_regular_schedule` (regular, no-calendar, stub-refusing) is REFERENCE ONLY (§3) and is not the V1 resolution engine.

---

## 3. Convention dimension audit (verified against the repository)

Status vocabulary: CONFIRMED = value fixed by repository/workstation evidence cited below. REFERENCE ONLY = existing behavior usable as regression/adapter context but NOT V1 methodology. CONFLICT = competing evidences requiring owner resolution (none found). UNRESOLVED — RED-02 = open methodology decision owned by Sophira/Eddy; #224 must not fill it.

| # | Dimension | Status | Evidence |
|---|---|---|---|
| D1 | Effective date / spot lag (e.g. T+2 from trade date) | VALUE UNRESOLVED — RED-02; mechanism RESOLVED (R1) | Mechanism: V1 is standard spot-starting OIS only; raw `trade_date` is the anchor and the effective date is resolved output; forward-starting is out of scope and fails closed (owner decision R1). Note: existing `products/swaps.py` carries `effective_date` with no `trade_date` — the V1 methodology contract differs from the legacy schema, which remains REFERENCE ONLY. Value: no `spot_lag` length evidenced anywhere; `pricing/irs_engine.py:124-134` accepts spot or forward-starting (`valuation_date <= effective`) but encodes no lag. Only other mention of spot lag is the RED-02 open list itself (`docs/31`). |
| D2 | Fixed-leg frequency (annual vs semi-annual) | UNRESOLVED — RED-02 | Vocabulary `Frequency` (`enums.py:98-109`) is CONFIRMED as a label set; no V1 value selected. Fixture frequencies (`SEMI_ANNUAL` in `tests/test_irs_reference_engine.py`, `ANNUAL` in `tests/test_products.py` OIS example) are synthetic examples, not workstation evidence. |
| D2F | Floating-leg payment frequency | UNRESOLVED — RED-02 | Vocabulary `Frequency` (`enums.py:98-109`) is CONFIRMED as a label set; no V1 value selected and no repo evidence constrains it (`OvernightIndexedSwap` constrains reset/compounding shape only, `swaps.py:160-168`). Added for contract completeness — the value must come from Bloomberg/desk evidence (E1), not from term-IRS quarterly precedent or any guessed value. |
| D3 | Fixed-leg day count | UNRESOLVED — RED-02 | Vocabulary `DayCount` (`enums.py:112-133`) CONFIRMED as labels. `irs_engine` safe subset (`ACT_360` / `ACT_365_FIXED`, `irs_engine.py:136-152`) is legacy term-IRS behavior — REFERENCE ONLY, not an OIS fixed-leg decision. Whether accrual day-count uses unadjusted schedule boundaries or adjusted dates is itself UNRESOLVED — RED-02 with no rule chosen; `ResolvedSwap` preserves both boundary sets so the approved rule stays replayable (§2.3). `REPO_DAY_COUNT=ACT/360` (`bli_repo_carry_forward`) is repo-accrual prototype scope only; the VCUB DCF experiment's `ACT/360` candidate is vol-annualization scope only — neither is swap evidence (both modules say so explicitly). |
| D4 | Floating-leg compounding mechanics + formula (compounded in arrears; `DAILY_COMPOUNDED` vs `AVERAGED` semantics; exact formula) | UNRESOLVED — RED-02 | Labels `DAILY_COMPOUNDED` / `AVERAGED` (`enums.py:172-182`: "the two standard ways an OIS floating leg turns a series of overnight fixings into one coupon") are CONFIRMED vocabulary with NO formula. `OvernightIndexedSwap` enforces compounding ≠ `NONE` and reset ∈ {`None`, `DAILY`} (`swaps.py:160-168`) — structural shape only. `irs_engine` REJECTS compounding and prices one simple forward per period (`(df_start/df_end − 1)/accrual`, simple DF `1/(1+r·T)`, `irs_engine.py:78-109,230-240,325-327`) — REFERENCE ONLY term-IRS behavior; must not be read as an OIS formula (`docs/31 §4` forbids unifying with BLI `exp(−r·T)` either). How spread enters the compounded coupon — added outside compounding, compounded inside the daily factors, or accrued separately — is likewise UNRESOLVED — RED-02 with no formula chosen; E2 must reconcile the alternatives before methodology freeze. Shift/lag mechanism, lookback, lockout, and cutoff are independent mechanics represented as separate YAML leaves (§6), each with value and unit/semantics open. |
| D5 | Fixing / observation semantics (observation shift vs lag, lookback, lockout, cutoff) | UNRESOLVED — RED-02 | Zero fields for observation/shift/lookback/lockout/cutoff in swap context (repo-wide grep: no hits; `docs/04` mentions `observation_calendar/observation_index/fixing_rules` only under Range Accrual, `docs/04:200-201`). `enums.py` "daily fixings combined" is description only. `irs_engine` supports no fixings / no in-progress swaps (`irs_engine.py:5,126-134`). `FixingStore` and historical-vs-forecast behavior are owned by #225, which waits for #224 (`docs/31 §10`). |
| D6 | Payment lag, per leg (e.g. 2 business days) | UNRESOLVED — RED-02, per leg | Zero fields, zero hits in swap context. Only the RED-02 word "lags" (`docs/31`). All repo/forward settlement dates are explicit caller inputs that derive nothing from lags. The lag length, lag base date, and the ordering between accrual-end adjustment, payment-lag application, and payment-date business-day adjustment are UNRESOLVED — RED-02 per leg with no ordering chosen and no equality assumed between the legs' rules; E3 must capture each leg's evidenced sequence separately. |
| D7 | Calendar (which holiday calendar resolves dates) | VALUES UNRESOLVED — RED-02, per role (shape RESOLVED) | No calendar field on swaps; `schedule.py:1-6` knows no calendars; `calendar_applied=False` (`irs_engine.py:230-240`). Contract shape must represent calendar rules by role (spot, fixed schedule, floating schedule, fixed-leg lag counting, floating-leg lag counting, fixed-leg payment, floating-leg payment, observation) with no equality assumed between roles; every per-role value awaits E4 evidence. `ql.NullCalendar()` + `Unadjusted` in `bli_quantlib_bond_adapter.py` and `CALENDAR_US_SIFMA` / `CALENDAR_TARGET` in `bli_bond_convention_profile.py` are BOND-only precedent with explicit out-of-scope wording — NOT swap authority (§5.3). |
| D8 | Business-day convention (value for V1) | VALUES UNRESOLVED — RED-02, per role (vocabulary CONFIRMED; shape RESOLVED) | Members `FOLLOWING / MODIFIED_FOLLOWING / PRECEDING / MODIFIED_PRECEDING / NONE` (`enums.py:136-147`) CONFIRMED as recordable labels; stored on the swap (`swaps.py:101,145`) but never resolved ("Resolving it requires a holiday calendar, which is out of scope"). Like D7, the contract shape must represent BDC rules by role (spot, fixed schedule, floating schedule, fixed-leg payment, floating-leg payment, observation) with no equality assumed; every per-role selection awaits E4 evidence. `business_day_adjustment_applied=False` in the reference engine. Test `MODIFIED_FOLLOWING` uses are synthetic fixtures. |
| D9 | Stub rules (front/back, long/short, EOM) | UNRESOLVED — RED-02 (fail-closed behavior REFERENCE ONLY) | `docs/04:94` `stub_rule` is an unimplemented placeholder. `schedule.py:62-72` + `irs_engine` + `docs/10` fail closed on non-clean division — legacy guard behavior, not a stub convention. Permitted/excluded, position, length, and EOM are independent facts represented as separate YAML leaves (§6), all UNRESOLVED — RED-02. |
| D10 | Overnight compounding convention (formula + day-count basis for the compounded coupon) | UNRESOLVED — RED-02 | Same as D4/D3 for the float coupon: no formula, no basis decided. S490 compounding explicitly unverified (`docs/bloomberg_ovme_source_mapping.md:63-68`: interpolation, compounding, date treatment remain unverified; UI rate must not be treated as continuous-zero without the separately approved RED decision — which Issue #165 supplied only for the `S0490Z` zero-rate route, not for swap-coupon compounding). |
| D11 | Tenor universe / curve identity (reference facts, not V1 values) | CONFIRMED (facts only) | 32-tenor `USOSFR*` verbatim map (`bloomberg_usd_sofr_par_rate_curve.py:138-171`, import-time drift guard against `DEFAULT_USD_SOFR_TENORS`); `S0490Z/D <tenor> BLC2 Curncy` grammar with `LAST_PRICE` (+`MATURITY` for Z/D), `Z/100`, `D` untouched (`bloomberg_option_discount_curve.py`); Curve #490 = USD SOFR option-discount provenance (`bloomberg_ovme_source_mapping.md:63-68`, PARTIALLY_CONFIRMED with compounding unverified). These fix ticker grammar and tenor list — NOT any D1–D10 or D12 convention value. `USOSFR*` par rates are display-only, never pricing inputs. |
| D12 | Notional/principal exchange (at effective date, at maturity, both, or neither) | UNRESOLVED — RED-02 | No principal-exchange behavior exists for single-currency swaps: `notional` is an amount/scaling term only (`products/swaps.py`, `pricing/irs_engine.py` multiplies cashflows by it, never exchanges it). The only in-repo exchange booleans are `initial_exchange` / `final_exchange` on cross-currency swaps (`products/cross_currency.py:69-81,127-129`) — a shape precedent only ("not every CCS exchanges principal"), not SOFR OIS values. Whether V1 exchanges principal at either end is not chosen here; E1 must evidence it. |
| D13 | Fixed-leg coupon formula (how the annual `fixed_rate` becomes each coupon) | UNRESOLVED — RED-02 | No approved V1 formula: the reference engine's fixed branch computes `notional × fixed_rate × accrual` (`pricing/irs_engine.py` `_leg_pv` fixed-rate path) as legacy term behavior — REFERENCE ONLY, not an OIS decision. Whether the V1 coupon is simple accrual, compounded, or another evidenced convention is not chosen here; E1 must evidence the formula so #228 never infers it. |

CONFLICT count: **zero**. Fixture differences (e.g. `SEMI_ANNUAL` vs `ANNUAL`, `ACT_365_FIXED` vs `THIRTY_360` across synthetic tests) are not competing conventions — all are synthetic examples with no workstation claim.

---

## 4. What is CONFIRMED vs REFERENCE ONLY (so #224 does not over-claim)

CONFIRMED (may be relied upon, none of it fixes a V1 value):

- Controlled vocabularies: `Frequency`, `DayCount`, `BusinessDayConvention`, `FloatingIndex` (incl. `USD_SOFR` overnight vs `USD_SOFR_TERM_3M` term distinction as documented desk choice), `CompoundingMethod`, `Currency`, `PayReceive` (`products/enums.py`).
- Swap product shapes: `InterestRateSwap` (IRS reset required) vs `OvernightIndexedSwap` (compounding required, reset `None`/`DAILY`) with shared order/notional/direction validation (`products/swaps.py`, `products/legs.py`).
- Canonical rate units (RESOLVED owner decision, consistent with existing schema docstrings): `fixed_rate` is a decimal annual rate (`FixedLeg`, `products/legs.py:30-34`); `spread` is a decimal annual rate (`FloatingLeg.spread` "decimal spread", `products/legs.py:70-71`).
- Curve-identity facts: Curve #490 grammar/tenors/fields/units (D1–D11 table, D11 row).
- #223 architecture: Python vs C++ ownership, BLI compatibility boundary, legacy reference-source classification, sequencing (#224 + #226 parallel after #223; #225 waits for #224).

REFERENCE ONLY (regression/adapter context; must NOT be promoted to V1 methodology):

- `pricing/irs_engine.py` behavior: USD-only single-curve term-IRS MVP, simple DF, linear-in-years interpolation with flat ends, `ACT_360`/`ACT_365_FIXED` gate, quarterly `USD_SOFR_TERM_3M` reset==pay shape, spot-or-forward-starting acceptance, fail-closed error table, golden `pv==1506.7928142153469` fixture. It is the C++ port's regression anchor for those behaviors — not a source of OIS convention values.
- `pricing/schedule.py` behavior: regular unadjusted schedules, stub/month-end refusal. Not the V1 resolution engine.
- `products` fixtures in `tests/test_products.py` / `tests/test_irs_reference_engine.py`: synthetic examples only.
- `USOSFR*` par-rate points and `S0490Z/D` zero/DF evidence: adapter-source market data, not convention authority.

NOT AUTHORITY (must never be cited for V1 values):

- QuantLib defaults or `bli_quantlib_bond_adapter.py` mechanics (`NullCalendar`/`Unadjusted`, bond day-count mapping): bond scope only, compatibility boundary per `docs/31 §7`.
- Generic public-market convention knowledge (e.g. "SOFR OIS is usually annual ACT/360 T+2 with 2-day lockout"): research context only; Shiori methodology authority is Bloomberg/desk UAT per #222 RED-02.
- Legacy IRS term conventions as OIS conventions (D4 trap): explicitly refused.

---

## 5. Workstation evidence Eddy must collect before USD_SOFR_OIS_V1 can be locked

Nothing in §3 resolves without the following named captures. For each item record the exact Bloomberg screen/field/function, the verbatim value, and the capture date; screenshots must be transcribed through the same confirm/reject discipline the repo already uses for VCUB (never hand-typed silently).

| # | Evidence required | Suggested source (to confirm, not to assume) | Locks dimensions |
|---|---|---|---|
| E1 | SOFR swap fixed-leg frequency + day count + coupon formula, floating-leg payment frequency, and notional/principal-exchange terms as configured for the USD SOFR curve | SWDF S490 curve-settings / stripped-curve convention screen; else `USOSFR*` security `DES` / `FLDS` fixed-leg, floating-leg, and principal-exchange fields via DAPI `ReferenceDataRequest` (record field names + values) | D2, D2F, D3, D12, D13 |
| E2 | SOFR floating-leg compounding definition: in-arrears formula, `DAILY_COMPOUNDED` vs `AVERAGED` selection, observation shift/lag, lookback / lockout / cutoff days, rate cut-off rule, spread application rule (outside compounding vs inside daily factors vs separately accrued — evidence must distinguish the alternatives before methodology freeze) | SWDF floating-leg definition / SWAP terms screen; else `USOSFR*` `FLDS` compounding + lockout/lookback/spread fields (record field names + values) | D4, D5, D10 |
| E3 | Spot-lag rule (trade date → effective date) and per-leg payment-lag rules, with lag-day counts, each leg's lag base date, the lag-counting calendar each leg counts on (the lag-counting roles, independent from the payment-date adjustment calendar), and each leg's full adjustment sequence as evidenced (accrual-end adjustment, lag application, payment-date BDC adjustment — record each leg's evidenced order separately without assuming one, and without assuming the legs match) | SWDF effective/spot-date rule + per-leg payment-lag fields/screens; DAPI equivalent fields if exposed, else transcribed screenshot | D1, D6 |
| E4 | Holiday calendar selector + BDC selector verbatim PER ROLE — spot/effective-date, fixed-leg schedule, floating-leg schedule, fixed-leg payment-lag counting, floating-leg payment-lag counting, fixed-leg payment-date, floating-leg payment-date, and SOFR observation/fixing-date resolution (record each role separately; do not assume roles share a calendar or rule; lag-counting roles are calendar-only while payment-date roles pair a calendar with a BDC role) | SWDF calendar/BDC fields or convention screen verbatim, per role | D7, D8 |
| E5 | Stub convention (are stubs permitted; front/back, long/short, EOM) or confirmation that the traded V1 scope excludes stubs (fail-closed) | SWDF stub-tenor handling screen or desk ruling recorded as owner policy (same standing as the #217-dated owner-policy precedent for settlement lags: an owner policy, not a Bloomberg claim) | D9 |
| E6 | One golden vanilla USD SOFR OIS quote set (par rate + full identifying terms for the same trade) for future #230-style reconciliation. The golden set must include everything needed to reconcile per-period accrual fractions under the approved boundary rule and the payment-date ordering once RED-02 decides those rules | `USOSFR*` `LAST_PRICE` capture + the instrument-terms capture from E1–E4 for the same tenor, same timestamp | All (reconciliation anchor) |

Notes: E1–E4 prefer DAPI fields where Bloomberg exposes them (units/values auditable, fake-`blpapi` testable like the existing loaders); screenshot transcription is acceptable where no field exists, following the VCUB confirm/reject precedent. E5 explicitly allows an Eddy owner-policy decision recorded as policy (not as a Bloomberg claim) if the desk scopes V1 to no-stub trades. No V1 value may be filled from QuantLib defaults, public convention, or legacy IRS fixtures.

---

## 6. Proposed USD_SOFR_OIS_V1 contract (shape only — all values UNRESOLVED)

`USD_SOFR_OIS_V1` is a PROPOSED version identifier for the `ConventionSet` rulebook. Its dimension list is fixed by this issue; every convention VALUE is UNRESOLVED — RED-02 until workstation evidence (§5) plus Sophira methodology acceptance fills it. Contract-shape decisions R1 (trade-date anchor, effective-date output, forward-start exclusion) and R2 (ConventionSet authority, no per-trade overrides, conflicts fail closed) are RESOLVED per Eddy/Sophira owner decisions recorded in §2. A future revision fills values one dimension at a time with evidence citations; no value becomes production methodology without both.

```yaml
convention_set_id: USD_SOFR_OIS_V1
status: PROPOSED — UNRESOLVED (RED-02)
currency: USD  # CONFIRMED (V1 scope; enum products/enums.py)
floating_index: USD_SOFR  # CONFIRMED (V1 scope; overnight index label)
rate_units: RESOLVED  # owner decision: fixed_rate and spread are decimal annual rates at the canonical boundary (4.25% -> 0.0425; 1 bp -> 0.0001); upstream layers normalize before the boundary; no untagged percent/bp accepted
effective_date_rule:
  spot_lag: UNRESOLVED  # D1 value (lag length needs E3 evidence)
  trade_date_anchor: RESOLVED  # R1: raw trade_date is the V1 input; effective date is resolved output; forward-starting out of scope and fails closed
fixed_leg:
  frequency: UNRESOLVED  # D2
  day_count: UNRESOLVED  # D3 value open; accrual-boundary rule (unadjusted schedule boundaries vs adjusted dates) likewise open
  coupon_formula: UNRESOLVED  # D13: simple accrual vs compounded vs another evidenced convention; no formula chosen; E1 must evidence so #228 never infers it
floating_leg:
  payment_frequency: UNRESOLVED  # D2F (owned by ConventionSet; value needs E1 evidence)
  compounding_method: UNRESOLVED  # D4 (label set CONFIRMED; selection + formula open)
  compounding_formula: UNRESOLVED  # D4/D10
  spread_treatment: UNRESOLVED  # D4: outside compounding vs inside daily factors vs separately accrued; no formula chosen; E2 must reconcile before freeze
  observation:  # D5 shape: independent mechanics; every leaf UNRESOLVED — RED-02 with value and unit/semantics open; needs E2 evidence
    shift_lag_mechanism: UNRESOLVED
    lookback: UNRESOLVED
    lockout: UNRESOLVED
    cutoff_rate_cutoff: UNRESOLVED
  day_count: UNRESOLVED  # D10 value open; accrual-boundary rule likewise open
notional_exchange:  # D12 shape: independent booleans covering effective/maturity/both/neither; every leaf UNRESOLVED — RED-02, needs E1 evidence
  at_effective_date: UNRESOLVED
  at_maturity: UNRESOLVED
payment_lag_by_leg:  # D6 shape: per-leg rules with no equality assumed; every leaf UNRESOLVED — RED-02; E3 must capture each leg's sequence
  fixed_leg:
    lag_days: UNRESOLVED
    base_date: UNRESOLVED
    ordering: UNRESOLVED
  floating_leg:
    lag_days: UNRESOLVED
    base_date: UNRESOLVED
    ordering: UNRESOLVED
calendar_by_role:  # D7 shape: per-role rules with no equality assumed; lag-counting roles are calendar-only, payment-date roles pair a calendar with a BDC role; every value UNRESOLVED — RED-02, needs E4 evidence
  spot_effective_date: UNRESOLVED
  fixed_leg_schedule: UNRESOLVED
  floating_leg_schedule: UNRESOLVED
  fixed_leg_payment_lag_counting: UNRESOLVED
  floating_leg_payment_lag_counting: UNRESOLVED
  fixed_leg_payment_date: UNRESOLVED
  floating_leg_payment_date: UNRESOLVED
  observation_fixing_date: UNRESOLVED
business_day_convention_by_role:  # D8 shape: adjustment roles only (BDC rolls dates; lag counting is calendar-only); every selection UNRESOLVED — RED-02, needs E4 evidence
  spot_effective_date: UNRESOLVED
  fixed_leg_schedule: UNRESOLVED
  floating_leg_schedule: UNRESOLVED
  fixed_leg_payment_date: UNRESOLVED
  floating_leg_payment_date: UNRESOLVED
  observation_fixing_date: UNRESOLVED
stub_rule:  # D9 shape: independent facts (E5 enumerates permitted/position/length/EOM); every leaf UNRESOLVED — RED-02 unless owner-policy no-stub scope per E5; needs E5 evidence
  permitted: UNRESOLVED
  position: UNRESOLVED
  length: UNRESOLVED
  end_of_month: UNRESOLVED
resolution_rules:
  schedule_generation: "C++ Rates module per approved contract, implemented in #228"
  fail_closed: Refuse anything the approved set cannot resolve; never silent-fill
  term_precedence: RESOLVED  # R2: ConventionSet authoritative; V1 supports no per-trade convention overrides; conflicting convention-like trade input fails closed, never silently overrides
```

Out of contract scope (owned elsewhere): market snapshots, curve construction/representation (#225, RED-01 open); fixing values/history (#225 `FixingStore`); vol/exercise/settlement/model (#225); build/concurrency/caching/benchmarks (#226).

---

## 7. Ownership and sequencing (unchanged from #223, applied)

- #224 (this issue): `SwapTrade` / `ConventionSet` / `ResolvedSwap` methodology + SOFR schedule/observation/fixing methodology + RED-02 evidence list. No implementation.
- #225: MarketSnapshot / CurveSet / DiscountCurve / ForwardCurve / FixingStore / vol / exercise / settlement / result contracts. Waits for #224 (FixingStore overlaps convention semantics).
- #226: build / QuantLib isolation / concurrency / caching / benchmark / CI. May parallel #224 provided it invents no DTO field semantics belonging to #225.
- #228: implements the approved deterministic schedule/fixing/calendar primitives producing `ResolvedSwap`. Must not start before Gate A accepts the #224 contract.
- Python does not become the authoritative Rates schedule engine at any point. BLI stays compatibility boundary; legacy IRS stays reference/adapter source.

---

## 8. RED-02 unresolved item list (owner: Sophira/Eddy)

RED-02 is OPEN on convention VALUES. Unresolved production values: D1 spot-lag length; D2 fixed frequency; D2F floating-leg payment frequency; D3 fixed day count (incl. accrual-boundary rule); D4 compounding mechanics + formula (incl. spread treatment); D5 observation mechanics leaves (shift/lag, lookback, lockout, cutoff); D6 payment-lag leaves per leg (days, base date, ordering); D7 calendar values per role (incl. per-leg lag-counting and payment roles); D8 BDC selections per role (incl. per-leg payment roles); D9 stub leaves (permitted, position, length, EOM); D10 overnight compounding convention (incl. accrual-boundary rule); D12 notional exchange (effective/maturity); D13 fixed-leg coupon formula. RESOLVED contract-shape decisions (Eddy/Sophira, no longer blocking): (R1) raw `trade_date` anchor with effective date as resolved output, V1 standard spot-starting only, forward-starting out of scope and fail-closed; (R2) `ConventionSet` authority over standard terms, no V1 per-trade overrides, conflicting trade input fails closed. Evidence required for the open values: E1–E6 in §5. RED-01 (curve authority) is separately open and untouched by this issue. Any workstation mismatch or inferred value = STOP and ask Eddy.

---

## 9. Validation performed (this issue)

- Branch cut from current `origin/main` (`7af4cbb`, includes merged #223); base verified by fetch.
- Read in full/large part: `products/enums.py` (all swap vocabularies + docstrings), `products/swaps.py` + `legs.py` (fields + validation), `pricing/schedule.py` + `irs_engine.py` (constraints + assumptions), `docs/04/10/18` (schema/preflight scope statements), `bloomberg_usd_sofr_par_rate_curve.py` (ticker table + display-only boundary), `bloomberg_option_discount_curve.py` (construction-export evidence wording), `bloomberg_ovme_source_mapping.md` (S490 rows), VCUB DCF experiment scope, `tools/` inventory (no swap-convention probe exists), `docs/evidence/` (template only), `bli_quantlib_bond_adapter.py` (bond-only calendar/day-count), plus two parallel codebase audits and repo-wide convention-term greps.
- Wrote one docs file only. No production code, tests, tools, schemas, CMake, C++ implementation, or Bloomberg infrastructure modified.
- No Bloomberg/workstation evidence created, cited, or implied beyond what the repository already records.

---

## 10. Next actions

- Sophira methodology review of this document; Codex independent review.
- Eddy workstation capture round E1–E6 (§5) to unblock RED-02 dimension-by-dimension.
- After Gate A acceptance: #225 (waits for #224) and #226 (may parallel #224) proceed; #228 implements the approved resolution.

---

*End of Issue #224 deliverable. Methodology/research document; no production functionality.*
