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
- `effective_date` / `maturity_date` as stated deal terms (ISO dates, order-validated only).
- leg terms as stated: fixed rate, floating index (`USD_SOFR`), spread, payment frequencies, day-count labels, compounding-method label, reset label, BDC label.
- Explicitly NOT included: any derived schedule, any adjusted date, any accrued fraction, any fixing, any forward, any curve, any valuation date.

`SwapTrade` never contains a date that was computed from a lag, a calendar, or a convention. If a date was derived, it belongs in `ResolvedSwap` with its derivation recorded, not in `SwapTrade`.

Two contract gaps are recorded here as RED-02 and are NOT decided by this issue — until Eddy/Sophira decide both, `SwapTrade + ConventionSet -> ResolvedSwap` is not yet a deterministic contract and #228 must not implement it. First, `SwapTrade` carries no raw `trade_date`: the `ConventionSet` spot-lag rule (trade date → effective date) therefore cannot be evaluated or audited from the stated inputs, and this issue does not choose between adding a `trade_date` input, redefining spot lag as validation-only / out of scope, assigning effective-date authority to the stated `effective_date`, or any forward-start semantics. Second, convention-like trade terms (payment frequencies, day-count labels, compounding-method label, reset label, BDC label) overlap `ConventionSet` authority: this issue does not choose whether the trade overrides the convention, the convention supplies defaults, or a mismatch is rejected.

### 2.2 ConventionSet (`USD_SOFR_OIS_V1`) — "under which approved rules is the trade resolved?"

`ConventionSet` is the versioned, workstation-reconciled rulebook that turns a `SwapTrade` into a `ResolvedSwap`. Version `USD_SOFR_OIS_V1` is PROPOSED in §6 with every value UNRESOLVED (RED-02). A future locked version may only fill a value with supporting repository/workstation evidence cited in this document's §3–§5 terms.

`ConventionSet` owns: spot-lag rule (trade date → effective date), fixed-leg frequency/day-count authority, floating-leg compounding mechanics + formula, fixing/observation semantics (lookback/shift/lockout/cutoff), payment-lag rule, calendar authority, BDC resolution rule, stub rule, overnight compounding convention. It owns NO market data, NO curves, NO fixings, NO model.

### 2.3 ResolvedSwap — "what exactly will the kernel price?"

`ResolvedSwap` is the fully deterministic output of `SwapTrade + ConventionSet` resolution:

- explicit period schedules per leg (start/end dates, all calendar/BDC adjustments applied and recorded).
- per-period accrual fractions with the day-count rule recorded.
- compounding / observation / fixing calendar per period (observation dates, weights, lockout/cutoff application) with the rule recorded.
- payment dates with lag application recorded.
- stub periods, if the approved stub rule permits them, with the rule recorded.
- full provenance: which `ConventionSet` version resolved it, and which rule produced each derived date/number.

Resolution is total and fail-closed: anything the approved `ConventionSet` cannot resolve (e.g. a non-clean schedule under a no-stub rule) is refused with a named reason, never silently filled. `ResolvedSwap` carries no market snapshot, no curve, no fixing values, no model config — those arrive separately at the kernel boundary per #223 (`ResolvedSwap + resolved curve input(s) + Fixings + Model Inputs -> Results`).

### 2.4 Authority rule (from #223, restated, not changed)

The C++ Rates module owns deterministic Convention / Trade Resolution; #228 implements the primitives approved here. Python may serialize, persist, display, replay, and orchestrate `ResolvedSwap` objects. Python must NOT become the authoritative Rates schedule-generation implementation for new Rates products. The legacy `pricing/schedule.py::generate_regular_schedule` (regular, no-calendar, stub-refusing) is REFERENCE ONLY (§3) and is not the V1 resolution engine.

---

## 3. Convention dimension audit (verified against the repository)

Status vocabulary: CONFIRMED = value fixed by repository/workstation evidence cited below. REFERENCE ONLY = existing behavior usable as regression/adapter context but NOT V1 methodology. CONFLICT = competing evidences requiring owner resolution (none found). UNRESOLVED — RED-02 = open methodology decision owned by Sophira/Eddy; #224 must not fill it.

| # | Dimension | Status | Evidence |
|---|---|---|---|
| D1 | Effective date / spot lag (e.g. T+2 from trade date) | UNRESOLVED — RED-02 | No `spot_lag` / `trade_date` field on `products/swaps.py` (`InterestRateSwap` / `OvernightIndexedSwap`: `product_id, effective_date, maturity_date, currency, notional, fixed_leg, floating_leg, business_day_convention`; `swaps.py:82-168`); `effective_date` is a raw ISO string with order-only validation (`_validate_common`, `swaps.py:53-79`). `pricing/irs_engine.py:124-134` accepts spot or forward-starting (`valuation_date <= effective`) but encodes no lag. Only mention of spot lag is the RED-02 open list itself (`docs/31`). |
| D2 | Fixed-leg frequency (annual vs semi-annual) | UNRESOLVED — RED-02 | Vocabulary `Frequency` (`enums.py:98-109`) is CONFIRMED as a label set; no V1 value selected. Fixture frequencies (`SEMI_ANNUAL` in `tests/test_irs_reference_engine.py`, `ANNUAL` in `tests/test_products.py` OIS example) are synthetic examples, not workstation evidence. |
| D3 | Fixed-leg day count | UNRESOLVED — RED-02 | Vocabulary `DayCount` (`enums.py:112-133`) CONFIRMED as labels. `irs_engine` safe subset (`ACT_360` / `ACT_365_FIXED`, `irs_engine.py:136-152`) is legacy term-IRS behavior — REFERENCE ONLY, not an OIS fixed-leg decision. `REPO_DAY_COUNT=ACT/360` (`bli_repo_carry_forward`) is repo-accrual prototype scope only; the VCUB DCF experiment's `ACT/360` candidate is vol-annualization scope only — neither is swap evidence (both modules say so explicitly). |
| D4 | Floating-leg compounding mechanics + formula (compounded in arrears; `DAILY_COMPOUNDED` vs `AVERAGED` semantics; exact formula) | UNRESOLVED — RED-02 | Labels `DAILY_COMPOUNDED` / `AVERAGED` (`enums.py:172-182`: "the two standard ways an OIS floating leg turns a series of overnight fixings into one coupon") are CONFIRMED vocabulary with NO formula. `OvernightIndexedSwap` enforces compounding ≠ `NONE` and reset ∈ {`None`, `DAILY`} (`swaps.py:160-168`) — structural shape only. `irs_engine` REJECTS compounding and prices one simple forward per period (`(df_start/df_end − 1)/accrual`, simple DF `1/(1+r·T)`, `irs_engine.py:78-109,230-240,325-327`) — REFERENCE ONLY term-IRS behavior; must not be read as an OIS formula (`docs/31 §4` forbids unifying with BLI `exp(−r·T)` either). |
| D5 | Fixing / observation semantics (observation shift vs lag, lookback, lockout, cutoff) | UNRESOLVED — RED-02 | Zero fields for observation/shift/lookback/lockout/cutoff in swap context (repo-wide grep: no hits; `docs/04` mentions `observation_calendar/observation_index/fixing_rules` only under Range Accrual, `docs/04:200-201`). `enums.py` "daily fixings combined" is description only. `irs_engine` supports no fixings / no in-progress swaps (`irs_engine.py:5,126-134`). `FixingStore` and historical-vs-forecast behavior are owned by #225, which waits for #224 (`docs/31 §10`). |
| D6 | Payment lag (e.g. 2 business days) | UNRESOLVED — RED-02 | Zero fields, zero hits in swap context. Only the RED-02 word "lags" (`docs/31`). All repo/forward settlement dates are explicit caller inputs that derive nothing from lags. |
| D7 | Calendar (which holiday calendar resolves dates) | UNRESOLVED — RED-02 | No calendar field on swaps; `schedule.py:1-6` knows no calendars; `calendar_applied=False` (`irs_engine.py:230-240`). `ql.NullCalendar()` + `Unadjusted` in `bli_quantlib_bond_adapter.py` and `CALENDAR_US_SIFMA` / `CALENDAR_TARGET` in `bli_bond_convention_profile.py` are BOND-only precedent with explicit out-of-scope wording — NOT swap authority (§5.3). |
| D8 | Business-day convention (value for V1) | UNRESOLVED — RED-02 (vocabulary CONFIRMED) | Members `FOLLOWING / MODIFIED_FOLLOWING / PRECEDING / MODIFIED_PRECEDING / NONE` (`enums.py:136-147`) CONFIRMED as recordable labels; stored on the swap (`swaps.py:101,145`) but never resolved ("Resolving it requires a holiday calendar, which is out of scope"). `business_day_adjustment_applied=False` in the reference engine. Test `MODIFIED_FOLLOWING` uses are synthetic fixtures. |
| D9 | Stub rules (front/back, long/short, EOM) | UNRESOLVED — RED-02 (fail-closed behavior REFERENCE ONLY) | `docs/04:94` `stub_rule` is an unimplemented placeholder. `schedule.py:62-72` + `irs_engine` + `docs/10` fail closed on non-clean division — legacy guard behavior, not a stub convention. |
| D10 | Overnight compounding convention (formula + day-count basis for the compounded coupon) | UNRESOLVED — RED-02 | Same as D4/D3 for the float coupon: no formula, no basis decided. S490 compounding explicitly unverified (`docs/bloomberg_ovme_source_mapping.md:63-68`: interpolation, compounding, date treatment remain unverified; UI rate must not be treated as continuous-zero without the separately approved RED decision — which Issue #165 supplied only for the `S0490Z` zero-rate route, not for swap-coupon compounding). |
| D11 | Tenor universe / curve identity (reference facts, not V1 values) | CONFIRMED (facts only) | 32-tenor `USOSFR*` verbatim map (`bloomberg_usd_sofr_par_rate_curve.py:138-171`, import-time drift guard against `DEFAULT_USD_SOFR_TENORS`); `S0490Z/D <tenor> BLC2 Curncy` grammar with `LAST_PRICE` (+`MATURITY` for Z/D), `Z/100`, `D` untouched (`bloomberg_option_discount_curve.py`); Curve #490 = USD SOFR option-discount provenance (`bloomberg_ovme_source_mapping.md:63-68`, PARTIALLY_CONFIRMED with compounding unverified). These fix ticker grammar and tenor list — NOT any D1–D10 convention value. `USOSFR*` par rates are display-only, never pricing inputs. |

CONFLICT count: **zero**. Fixture differences (e.g. `SEMI_ANNUAL` vs `ANNUAL`, `ACT_365_FIXED` vs `THIRTY_360` across synthetic tests) are not competing conventions — all are synthetic examples with no workstation claim.

---

## 4. What is CONFIRMED vs REFERENCE ONLY (so #224 does not over-claim)

CONFIRMED (may be relied upon, none of it fixes a V1 value):

- Controlled vocabularies: `Frequency`, `DayCount`, `BusinessDayConvention`, `FloatingIndex` (incl. `USD_SOFR` overnight vs `USD_SOFR_TERM_3M` term distinction as documented desk choice), `CompoundingMethod`, `Currency`, `PayReceive` (`products/enums.py`).
- Swap product shapes: `InterestRateSwap` (IRS reset required) vs `OvernightIndexedSwap` (compounding required, reset `None`/`DAILY`) with shared order/notional/direction validation (`products/swaps.py`, `products/legs.py`).
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
| E1 | SOFR swap fixed-leg frequency + day count as configured for the USD SOFR curve | SWDF S490 curve-settings / stripped-curve convention screen; else `USOSFR*` security `DES` / `FLDS` fixed-leg fields via DAPI `ReferenceDataRequest` (record field names + values) | D2, D3 |
| E2 | SOFR floating-leg compounding definition: in-arrears formula, `DAILY_COMPOUNDED` vs `AVERAGED` selection, observation shift/lag, lookback / lockout / cutoff days, rate cut-off rule | SWDF floating-leg definition / SWAP terms screen; else `USOSFR*` `FLDS` compounding + lockout/lookback fields (record field names + values) | D4, D5, D10 |
| E3 | Spot-lag rule (trade date → effective date) and payment-lag rule (accrual end → payment), with lag-day counts and the calendar they count on | SWDF effective/spot-date rule + payment-lag field/screen; DAPI equivalent fields if exposed, else transcribed screenshot | D1, D6 |
| E4 | Holiday calendar selector + BDC selector verbatim (e.g. which calendar, which BDC) | SWDF calendar/BDC fields or convention screen verbatim | D7, D8 |
| E5 | Stub convention (are stubs permitted; front/back, long/short, EOM) or confirmation that the traded V1 scope excludes stubs (fail-closed) | SWDF stub-tenor handling screen or desk ruling recorded as owner policy (same standing as the #217-dated owner-policy precedent for settlement lags: an owner policy, not a Bloomberg claim) | D9 |
| E6 | One golden vanilla USD SOFR OIS quote set (par rate + full identifying terms for the same trade) for future #230-style reconciliation | `USOSFR*` `LAST_PRICE` capture + the instrument-terms capture from E1–E4 for the same tenor, same timestamp | All (reconciliation anchor) |

Notes: E1–E4 prefer DAPI fields where Bloomberg exposes them (units/values auditable, fake-`blpapi` testable like the existing loaders); screenshot transcription is acceptable where no field exists, following the VCUB confirm/reject precedent. E5 explicitly allows an Eddy owner-policy decision recorded as policy (not as a Bloomberg claim) if the desk scopes V1 to no-stub trades. No V1 value may be filled from QuantLib defaults, public convention, or legacy IRS fixtures.

---

## 6. Proposed USD_SOFR_OIS_V1 contract (shape only — all values UNRESOLVED)

`USD_SOFR_OIS_V1` is a PROPOSED version identifier for the `ConventionSet` rulebook. Its dimension list is fixed by this issue; every value is UNRESOLVED — RED-02 until workstation evidence (§5) plus Sophira methodology acceptance fills it. A future revision fills values one dimension at a time with evidence citations; no value becomes production methodology without both.

```yaml
convention_set_id: USD_SOFR_OIS_V1
status: PROPOSED — UNRESOLVED (RED-02)
currency: USD  # CONFIRMED (V1 scope; enum products/enums.py)
floating_index: USD_SOFR  # CONFIRMED (V1 scope; overnight index label)
effective_date_rule:
  spot_lag: UNRESOLVED  # D1
  trade_date_anchor: UNRESOLVED  # RED-02 contract gap: SwapTrade carries no raw trade_date, so the spot-lag rule is unevaluable until Eddy/Sophira add the input, redefine spot lag as validation-only/out-of-scope, or assign effective-date authority (forward-start semantics likewise undecided)
fixed_leg:
  frequency: UNRESOLVED  # D2
  day_count: UNRESOLVED  # D3
floating_leg:
  compounding_method: UNRESOLVED  # D4 (label set CONFIRMED; selection + formula open)
  compounding_formula: UNRESOLVED  # D4/D10
  observation: UNRESOLVED  # D5 (shift/lag/lookback/lockout/cutoff)
  day_count: UNRESOLVED  # D10
payment_lag: UNRESOLVED  # D6
calendar: UNRESOLVED  # D7
business_day_convention: UNRESOLVED  # D8 (label set CONFIRMED; selection open)
stub_rule: UNRESOLVED  # D9 (or owner-policy no-stub scope per E5)
resolution_rules:
  schedule_generation: C++ Rates module per approved contract, implemented in #228
  fail_closed: Refuse anything the approved set cannot resolve; never silent-fill
  term_precedence: UNRESOLVED  # RED-02 contract gap: where convention-like SwapTrade terms overlap ConventionSet authority, Eddy/Sophira must choose trade-overrides-convention, convention-supplies-defaults, or mismatch-rejected before #228 can resolve deterministically
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

RED-02 is OPEN. Unresolved production items: D1 spot lag; D2 fixed frequency; D3 fixed day count; D4 compounding mechanics + formula; D5 observation semantics; D6 payment lag; D7 calendar; D8 BDC selection; D9 stub rule; D10 overnight compounding convention; plus two contract-shape decisions — (R1) the missing raw `trade_date` anchor for the spot-lag rule (add the input, validation-only/out-of-scope, or effective-date authority with forward-start semantics) and (R2) override precedence where convention-like `SwapTrade` terms overlap `ConventionSet` authority (trade-overrides, convention-defaults, or mismatch-rejected). Evidence required: E1–E6 in §5. RED-01 (curve authority) is separately open and untouched by this issue. Any workstation mismatch or inferred value = STOP and ask Eddy.

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
