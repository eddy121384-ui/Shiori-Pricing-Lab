# 14 Bond Linked Structured Pricer — Spec Teardown & Integration Preflight

Status: **docs-only teardown / preflight — no code, no pricing maths, nothing
registered, and no edit to the four BLI source spec files.**

This document tears down the authoritative Bond Linked Structured Pricer (BLI)
v1.3 specifications (landed by PR #33) and maps them onto the existing
deterministic pricing spine, *before* any BLI pricing code is written. It is the
docs-only preflight the pivot checkpoint (`docs/13 (removed, see git history)`, §4) named as the next step
after the product-priority pivot.

It does four things and nothing else:

1. reviews the **financial methodology** in Annex A for internal consistency and
   implementation risk (§2);
2. assesses **market-data implementation readiness** against Annex B and SPEC §7
   (§3);
3. draws the **repo integration map** onto `price(...)`, `PricingResult`,
   `ValuationContext`, `MarketDataSnapshot`, and the product schemas (§4);
4. produces a severity-ranked **risk list** (§5) and a **next-issue roadmap**
   (§6).

Every defect found here is written up as a **proposed targeted amendment to a
future Annex revision**, not as an inline edit to the frozen source specs. The
four source files
(`SPEC_v1.3.md`, `ANNEX_A_v1.3.md`, `ANNEX_B_v1.3.md`, `ANNEX_C_v1.3.md`) are
**not modified** by the PR that introduces this document.

This preflight computes **no values**. Every formula quoted below is quoted from
the spec to reason about it, not to produce a price.

---

## 1. Scope and boundaries

**In scope (this doc):** methodology review, market-data readiness review,
integration mapping, risk list, roadmap.

**Out of scope (this doc and its PR):** source code, tests, CI/workflow, pricing
implementation, Bloomberg / FTP adapters, QuantLib dependency, UI. No edit to the
four BLI source spec files. No implementation issue is opened or modified.

**Required reading before the first BLI implementation slice:** `AGENTS.md`,
`docs/12_pr_review_rubric.md`, the pivot checkpoint (`docs/13_bond_linked_pivot_checkpoint.md`,
removed, see git history), the four
files under `docs/bond_linked_structured_pricer/`, and the existing spine modules
(`src/shiori_pricing_lab/pricing/engine.py`, `result.py`, `errors.py`,
`valuation/context.py`, `data/snapshot.py`, `products/`), plus `docs/02` (market
snapshots) and `docs/04` (product schema).

**Reuse invariant (from `docs/13 (removed, see git history)` §1):** BLI registers behind the **same**
`price(product, valuation_context, market_snapshot) -> PricingResult` front door
as a per-product engine (exactly as the IRS reference engine does today via
`register_engine("IRS", IRSReferenceEngine())` in
`src/shiori_pricing_lab/pricing/__init__.py`). BLI does **not** get a parallel
pricing path.

---

## 2. Financial methodology consistency — Annex A teardown

This section walks Annex A section by section against the checklist the pivot
checkpoint set. Where a section is **consistent**, it is confirmed briefly so the
implementation slice can trust it. Where a section carries a **defect or
ambiguity**, it is recorded as a finding `F-nn` and rolled into the risk list
(§5) with a proposed amendment.

PR #33 already fixed three Annex A items during review (clean-price tree coupon
handling §A.4.2, price-based parity notional scaling §A.13.2, and parity
tolerance basis §A.13.2 — see `docs/13 (removed, see git history)` §3). Those are treated as closed here and
not re-litigated; the findings below are **new**.

### 2.1 Clean vs dirty price, accrued interest, coupon timing

- **Consistent:** the clean/dirty split is coherent across §A.5.2 (forward),
  §A.6.1 (yield-to-price), §A.7 (physical invoice), and SPEC §6.3.3. Spot dirty =
  spot clean + AI(pricing date); forward clean = forward dirty − AI(expiry);
  payoff comparison is always on **clean** price; the physical invoice alone uses
  **dirty** price at **settlement** date (§A.7.2). The §A.4.2 clean-price-tree
  coupon rule (PR #33 fix) is consistent with this.
- **F-01 (P2) — coupon exactly on expiry, and ex-coupon AI sign, are
  under-specified.** §A.5.2 discounts `coupons_before_expiry` over the half-open
  interval `(pricing date, expiry date]`, i.e. it **includes** a coupon paid
  exactly on the expiry date. Whether the expiry-date forward is cum- or
  ex-coupon in that case, and how that interacts with the `ex_dividend_days > 0`
  negative-AI case flagged in §A.6.3, is not stated. Two implementers can diverge
  on a coupon-on-expiry bond. See amendment A-01.

### 2.2 Yield-option direction and Black-76 units

- **Consistent:** the direction convention (Yield Call ≡ yield-up payoff ≡ Price
  Put in clean-price space) is identical in §A.3.1 and SPEC §6.3.2, and the doc
  explicitly requires UI/report/audit to preserve it — no sign flip. Black-76
  price-option units in §A.2 are internally consistent: PV per 100 discounted by
  the Option Discount `DF`, then scaled `× N / 100`.
- **F-02 (P1) — §A.3 MODE_A does not define the unit basis of the price-delta
  conversion, so the reported price delta can be off by a fixed scale factor.**
  This is **not** merely a `DV01_expiry` vs `DV01_underlying` naming/date
  question. In MODE_A the `Yield Delta` has *already* been scaled by
  `10000 × DV01_expiry × N / 100` (it is a full-PV, per-notional yield delta),
  while `DV01_expiry` itself is defined "per 1bp, per 100 face". The stated
  conversion `Price Delta = Yield Delta × (-1 / DV01_underlying)` divides that
  fully-scaled yield delta by a bare, undefined `DV01_underlying` and so does
  **not** reproduce a clean-price delta in any coherent unit — the `10000`
  factor, the `N / 100` notional/per-100 basis, and the bp-vs-decimal basis are
  all left dangling. A developer who implements the formula literally gets a
  price delta wrong by a factor of `10000` and/or `N / 100` whenever the Greek is
  reported. Annex A must state, before MODE_A Greeks are reported: (a) whether the
  reported price delta is a **per-100 clean-price delta** or a **full-PV delta**;
  (b) exactly how a `Yield Delta` already carrying `10000 × DV01_expiry × N / 100`
  converts back to that clean-price basis; and (c) a denominator that includes the
  matching `10000 / DV01 / notional / per-100` factors, or a single normalized
  DV01 term that already bundles those units. This is a pre-implementation
  methodology amendment (see A-02), required before any MODE_A price delta is
  surfaced.

### 2.3 Price vs yield vol, and equivalent-price-vol conversion (§A.8)

- **Consistent:** the vol basis policy is coherent — yield-based options take
  yield vol; price-based options take price vol or an equivalent price vol
  converted from yield vol via `PRICE_VOL_CONVERSION_MODE`; no silent flat-vol
  fallback (§A.8.4, SPEC §3.3). The MODE_1 first-order relation
  `σ_P ≈ σ_Y × Y × ModDur` is dimensionally sound (absolute yield vol `σ_Y·Y`
  times modified duration gives a relative price vol suitable for lognormal
  Black-76 on clean price). Vega chaining (§A.9.4: bump `σ_Y`, let `σ_P` follow)
  is consistent.
- **F-03 (P2) — MODE_2 convexity term (§A.8.3) does not pin the definition/units
  of `Convexity`.** `σ_P ≈ σ_Y·Y·ModDur·(1 − 0.5·Convexity·σ_Y²·Y²·T)` is only
  dimensionless if `Convexity` is the **annualized yield convexity** in the same
  `1/yield²` units as `ModDur` is in `1/yield`. Whether the engine should use
  price convexity, per-100 convexity, or `d²P/dy²`-style convexity, and its
  annualization, is not stated; a wrong convexity unit silently distorts `σ_P`
  and therefore every MODE_2 price-option PV. See amendment A-03.

### 2.4 American tree assumptions (§A.4)

- **Consistent:** state-variable choice (clean price for price-based, yield for
  yield-based), payoff-on-clean-price rule, Option-Discount-Curve discounting,
  and the §A.4.2 coupon rule (PR #33 fix) are coherent. `CRR_STEPS` defaults
  (`HIGH(500)`) and the convergence checks in §A.4.3 are sensible.
- **F-04 (P1) — the CRR tree's forward/drift calibration is not specified.**
  §A.4.1 fixes the state variable and the vol but never states that the
  price-state tree must be calibrated so its risk-neutral expectation reproduces
  the §A.5 **forward clean price** (which sits *below* a naive `spot × e^{rT}`
  because of pull-to-par and intervening coupons), nor the analogous requirement
  that the yield-state tree's expectation reproduce the forward yield `YF`. A
  developer who builds a textbook CRR tree with a `spot × e^{rT}` drift on clean
  price will produce a **wrong American PV** and can spuriously pass or fail the
  §A.13.3 American ≥ European check. Because American price call/put cash cases
  are in the MVP UAT set (SPEC §20.3), this must be resolved before the American
  engine. This is the price-vol-on-a-mean-reverting-clean-price concern SPEC §3.3
  raises for time-decay, made concrete for the tree. See amendment A-04.

### 2.5 Forward derivation, curve separation, physical delivery

- **Consistent:** §A.5 (Bond Reference Curve for the forward, no repo/specialness
  in MVP, assumption explicitly stated), §3.5 (Option Discount vs Bond Reference
  vs Deposit vs Funding curves must not be mixed), and §A.7 physical settlement
  (payoff on clean at exercise; invoice on dirty at settlement; AI recomputed at
  settlement when the lag crosses a coupon) are internally coherent and match
  SPEC §3.4–§3.6.
- **F-14 (P2) — the repo/specialness "use Trader override" escape hatch (§A.5.1)
  has no override field defined anywhere in the spec.** §A.5.1 states that in
  specialness-significant bonds (on-the-run treasuries, squeeze-period issues) the
  MVP forward may deviate materially and "if necessary, use trader override" to
  reconcile against a Bloomberg/vendor benchmark. But the SPEC §7.2 override
  whitelist admits **only** bond clean price/yield, volatility, credit spread,
  settlement lag, and shifted-Black epsilon — **not** repo, forward price, or a
  specialness/carry adjustment. So an implementer facing exactly the case §A.5.1
  names has **no deterministic, audited field** to reproduce the benchmark
  discrepancy; the only way to force the forward is to abuse an unrelated input
  (e.g. distort the bond clean price or credit spread), which corrupts DV01/CS01
  provenance and the self-validation checks. This is a concrete boundary
  contradiction between §A.5.1 and §7.2, not a "no finding". See amendment A-12.

### 2.6 DV01 / CS01 scaling and notional scaling (§A.9)

- **Consistent:** DV01 / CS01 are bump-and-revalue ±1bp central difference with a
  clear bump target per option family (§A.9.2–§A.9.3); price-based DV01 correctly
  re-derives the forward clean price through the Bond Reference Curve on the bump.
  Notional scaling is uniform `× N / 100` after the PR #33 parity fix. No new
  finding beyond F-02 (the MODE_A delta-conversion unit basis).

### 2.7 Put-call parity and self-validation (§A.13)

- **Consistent:** §A.13.2 unit handling (per-100 vs full-PV, and the matching
  tolerance normalization) is correct after the PR #33 fix.
- **F-05 (P2) — §A.13.2 gives no parity check for European yield options priced
  under MODE_B.** The yield-based parity identity in §A.13.2 is written only for
  the MODE_A DV01 form
  (`C_yield − P_yield = DF·(YF − YK)·10000·DV01_expiry·N/100`). Under MODE_B
  (numerical), the numerical `C − P` will **not** equal that DV01-linearized
  right-hand side, so applying the MODE_A formula as the MODE_B self-check would
  false-fail. The doc should state that MODE_B parity compares numerical `C − P`
  against the **discounted forward intrinsic in clean-price space**, or exempt
  MODE_B from A.13.2 and rely on A.13.4. See amendment A-05.
- **F-06 (P2) — §A.13.3 (American ≥ European) contradicts §A.4.3 on severity and
  omits a comparison basis/tolerance.** §A.4.3 treats an American < European
  result as a **warning + "raise one step"**; §A.13.3 treats the *same* condition
  as a **critical error + pricing blocked**. Two sections, one condition, two
  outcomes. Separately, §A.13.3 uses an exact `≥` with no tolerance and does not
  say whether the European leg is the **closed-form** value or a **same-tree**
  European value — comparing a CRR American against a closed-form European can
  breach `≥` by pure discretization noise at low step counts. See amendment A-06.

### 2.8 Vol-conversion modes, shifted Black, interpolation (§A.8, §A.10–§A.12)

- **Consistent:** the mode-switch governance (§A.12) is the spec's core idea —
  every "two correct ways" decision is a recorded switch written into the pricing
  run — and it maps cleanly onto the reuse invariant (§4.4). Shifted Black
  (§A.11) is off by default and gated behind an explicit flag with an audited
  epsilon. Interpolation defaults (§A.10) are stated.
- See F-11 (§3.4) for the **FTP-vs-mode-switch precedence** ambiguity that
  straddles Annex A §A.10/§A.12 and Annex B §B.2/§B.3; it is filed under
  market-data readiness because it is triggered by a feed field.

### 2.9 Tolerance basis summary

The self-validation tolerances live in **different units** and must be reported
with their unit, not as bare percentages:

| Check | Section | Threshold | Unit |
| --- | --- | --- | --- |
| Closed-form vs bump Greeks | A.13.1 | 5% | % of the Greek |
| Put-call parity | A.13.2 | 0.1% per 100 face | per-100 (normalize full-PV by `N/100`) |
| American ≥ European | A.13.3 | exact `≥` (see F-06) | price, per 100 face |
| Bloomberg / vendor | A.13.4 | <2% pass / 2–5% warn / >5% fail | % per 100 face |

**F-07 (P3):** the Internal Pricing Report must print each tolerance **with its
unit** (per-100 vs % vs Greek-%); a bare "0.1%" invites the exact unit-mixing bug
PR #33 already fixed once in §A.13.2. See amendment A-07.

---

## 3. Market-data implementation readiness — Annex B / SPEC §7

The BLI market-data surface is far wider than the spine's current single
rates-points frame. This section checks each feed for readiness and boundary
correctness.

### 3.1 Feed-by-feed readiness

| Feed | Source (Annex B / SPEC) | Readiness | Note |
| --- | --- | --- | --- |
| Bond price / yield | §B.1, SPEC §7.1 | Fields defined; filename/cut-off TBD | Carries `clean_price_per_100`, `yield`, `accrued_interest_per_100`. |
| Yield curve | §B.2 | Fields defined; TBD patterns | Carries `curve_type`, `compounding`, `interpolation_method` (see F-11). |
| Volatility | §B.3 | Fields defined | Carries `vol_basis` (YIELD/PRICE/EQUIVALENT) — supports §A.8; also carries `interpolation_method` (see F-11). |
| Credit spread | §B.4 | Fields defined | `spread_type`, `tenor`, ISIN/issuer hierarchy — supports §A.9.3 CS01 bump. |
| Bond master | §B.5 | Fields defined; **F-08 gap** | Reference/static data; drives cash-flow generation and yield convention. |
| Calendar / holiday | §B.6 | Fields defined | Needed for settlement-lag / AI recomputation (§A.7.3). |
| FTP batch control | §B.7 | Complete | Batch id, status vocabulary, reject counts — good audit spine. |
| Downstream payload | §B.8 | Placeholder only (Phase 3) | Correctly reserved; out of MVP. |

> **Terminology note (forward-looking).** The original BLI v1.3 source specs
> (Annex B / SPEC §7) use "FTP" ambiguously to describe generic market-data
> file ingestion. In the user's business context, "Treasury FTP" instead means
> Funds Transfer Pricing / internal funding-cost curve — a different concept.
> This teardown is **not** being rewritten and its findings are not being
> reinterpreted; future implementation of any market-data ingestion, Bond
> Master, or funding-curve work should follow the disambiguated terms in
> `docs/16_market_data_ingestion_terminology.md`.

### 3.2 Bond Master — the yield-convention gap

- **Consistent:** §B.5 rejects callable / sinkable / non-plain-vanilla / missing
  `yield_convention` at import, matching the §A.1 product universe. Good — the
  MVP pricing pool is filtered at the data boundary, not inside the engine.
- **F-08 (P2) — Bond Master (§B.5) has no field to store the compounding
  frequency `m` that §A.6.2 requires.** §A.6.1 yield-to-price needs `m` (2 for
  semi-annual, 1 for annual, etc.). §A.6.2 derives `m` from a hard-coded
  `yield_convention → m` table, and for `yield_convention = OTHER` it instructs
  the Trader to "supply `m` and `day_count` in Bond Master maintenance" — but
  §B.5's field list has **no `m` / `compounding_frequency` column** to hold that
  value. Also note `coupon_frequency` (how often coupons pay) is a **distinct**
  quantity from the yield compounding `m`; they coincide for US Treasuries but not
  for, e.g., an annual-compounded EUR govvie with semi-annual coupons. The schema
  must carry `m` explicitly (or a documented rule that `m` is always derived and
  `OTHER` is simply un-priceable). See amendment A-08.

### 3.3 Override, blocking, and manual-data boundaries

- **Consistent and correct:** override discipline (SPEC §3.8, §7.2) — overrides
  never overwrite FTP source data, always require a reason, are append-only
  `OverrideRecord`s, and only apply to the current run. The Market-Data-Blocking
  matrix (§7.6) is coherent: missing **yield curve / curve mapping** blocks
  pricing; missing **bond price / vol / spread** is *warned* so the Trader can
  supply an override or a configured fallback.
- **F-15 (P1) — "overridable" is not the same as "resolved"; an unresolved
  required input must never yield a success result.** SPEC §7.6 marks missing
  bond price / vol / spread as a **warning the Trader *can* override** — it does
  **not** say the engine may price without a value. A BLI option PV cannot be
  computed without a spot bond price/yield, a vol, and (for credit-sensitive
  forwards) a spread; if none of override, an explicitly-configured fallback, or a
  feed value has supplied one, the engine has **no deterministic input**. The
  correct mapping onto the result contract is therefore three-way, and the engine
  **must not fabricate** a price, vol, spread, curve, or fallback to fill the gap:

  | Input state at pricing time | Result mapping |
  | --- | --- |
  | Present from feed | price normally (`SUCCESS`) |
  | Missing, but a **resolved override or explicitly-configured fallback** supplies a deterministic value | `SUCCESS_WITH_WARNINGS` + `DATA_QUALITY` / `OVERRIDE_APPLIED` / `VOL_FALLBACK`, with provenance echoed |
  | Missing **and unresolved** (no override, no configured fallback) | **block before pricing, or return `FAILED + MISSING_MARKET_DATA`** — never `SUCCESS_WITH_WARNINGS`, never a fabricated `0.0` |
  | Yield curve / curve mapping missing (§7.6 hard block) | `FAILED + MISSING_MARKET_DATA` |

  Returning `SUCCESS_WITH_WARNINGS` for an *unresolved* required input would
  expose a PV that looks valid despite missing market data, which violates the
  AGENTS.md / `docs/12` rule that missing market data must fail explicitly and
  never be fabricated or hidden as a warning. See amendment A-13; §4.3 is updated
  to carry this three-way mapping.
- **F-09 (P2) — "override does not overwrite FTP" is a service-layer invariant the
  pricing engine cannot enforce, and the boundary is not drawn.** The engine
  receives a resolved snapshot; whether a value in it is FTP-origin or an
  applied override must be **carried in the snapshot/provenance**, not
  reconstructed by the engine. The preflight fixes the boundary in §4.6: override
  capture, persistence, and the "no write-back to FTP" rule live **outside** the
  engine; the engine only consumes a resolved, provenance-tagged snapshot and
  echoes provenance into `PricingResult`. See amendment A-09 (documentation of
  the boundary, not a maths change).

### 3.4 Snapshot replay and interpolation precedence

- **Consistent:** replay (NFR §5.4.3, ≤5s) is achievable because every mode
  switch is stored on the pricing result (§A.12, SPEC `ModeSwitchSnapshot`) and
  the market snapshot is immutable and dated — the same design the spine already
  uses for rates points.
- **F-10 (P2) — snapshot replay requires the BLI snapshot to freeze *all* feeds
  (bond price, curves, vol, spread, bond master, calendar) plus the mode
  switches; today's `MarketDataSnapshot` freezes only rates points.** Not a
  defect in the spec, but a concrete build requirement recorded here so replay is
  designed in from the first slice (see §4.2).
- **F-11 (P2) — precedence between the per-feed `interpolation_method` (§B.2
  curve, §B.3 vol) and the `CURVE_INTERP` / `VOL_INTERP_*` mode switches
  (§A.10, §A.12) is unspecified.** Both the FTP file and the mode-switch table
  claim to choose interpolation. If a feed says `interpolation_method = CUBIC`
  but `CURVE_INTERP = LINEAR_ZERO`, which wins? This is a determinism/audit
  question: the run must record *one* effective method. Recommended rule: mode
  switches are authoritative for the pricing run and the feed's
  `interpolation_method` is descriptive metadata only — but the spec must say so.
  See amendment A-10.

---

## 4. Repo integration map

This section maps BLI onto the existing spine types, reusing what exists and
naming only what genuinely must be added. Field names below reference the actual
modules (`pricing/result.py`, `valuation/context.py`, `data/snapshot.py`,
`pricing/engine.py`, `products/`).

### 4.1 What maps to `ProductDefinition` (deal terms only)

BLI needs **two new product schemas**, built like the existing frozen dataclass
products (`product_id: str`, `product_type: str = field(init=False, ...)`,
enum-backed terms, schema-level validation only, no market data):

- `BondOption` — `product_type = "BOND_OPTION"` (standalone tool, SPEC §6.2).
- `BondLinkedStructuredProduct` — `product_type = "BOND_LINKED_STRUCTURED"`
  (deposit leg + sold bond option, SPEC §6.1).

New controlled vocabularies (mirroring `products/enums.py` style, English
canonical codes per SPEC §22.3):

- `PayoffBasis` = `PRICE | YIELD`
- `OptionType` = `CALL | PUT`
- `ExerciseStyle` = `EUROPEAN | AMERICAN`
- `SettlementType` = `CASH | PHYSICAL`
- `Position` = `BUY | SELL`
- reuse the existing **enum style and validation pattern** (`StrEnum` +
  `coerce_enum`, per `products/enums.py`) where possible — but the existing
  `Currency` / `DayCount` / `Frequency` / `BusinessDayConvention` members must
  **not** be assumed sufficient. **F-16 (P2): a BLI enum gap analysis is required
  before schema work.** Annex A/B reach beyond the current rates-focused
  vocabularies: markets/currencies include NZ, KR, HK, SG (§A.6.2, §A.7.3) which
  are not all in the current `Currency` enum, and day-count conventions include
  `ACT/365`, `ACT/365F`, and market `ACT/ACT` variants (§A.6.2) that the current
  `DayCount` set (`ACT_360`, `ACT_365_FIXED`, `THIRTY_360`, `ACT_ACT_ISDA`) does
  not cleanly cover. If the first schema slice reuses these enums literally,
  supported Bond Master records are either **rejected** or **silently coerced to
  the wrong convention**, which corrupts yield-to-price (§A.6) and every
  downstream Greek/AI result. The gap analysis must (a) extend the enums to the
  Annex A/B markets and day counts actually in scope, and (b) require that any
  unsupported value fail **explicitly** — `FAILED + INVALID_PRODUCT` for an
  out-of-scope deal term, or a proposed `MISSING_REFERENCE_DATA` failure code
  (new to `PricingErrorCode`) for an unrecognised Bond Master convention — and is
  **never silently coerced**. See amendment A-14 and the §6.1 prerequisites.

**Boundary decision (important):** the product carries the **bond identity**
(ISIN) plus the **option terms** (strike, expiry, exercise style, settlement type
and lag, payoff basis, notional, participation ratio). The bond's **static
attributes** (coupon, coupon schedule, `day_count`, `yield_convention`, maturity)
are **reference/market data** delivered by the Bond Master feed (§B.5) and
resolved in the snapshot — **not** deal terms. This keeps the product a pure deal
description (per `docs/04`) and prevents a stale hand-typed coupon schedule from
overriding the governed Bond Master.

### 4.2 What maps to `MarketDataSnapshot` (and what must be added)

`data/snapshot.py` today carries only `valuation_date`, `source`, `_rates_points`,
`metadata`. Its **pattern is the right one** (frozen, explicit valuation date,
defensive deep copy, `from_*` constructors) and BLI should extend it, not fork
it. New immutable sub-structures the BLI snapshot must freeze (aligning with the
categories `docs/02` already anticipates):

- bond price/yield points (§B.1);
- a curve set: Option Discount, Bond Reference, Deposit, optional Funding (§3.5,
  §B.2);
- a vol surface with `vol_basis` (§B.3);
- a credit-spread curve (§B.4);
- Bond Master reference records (§B.5) — resolved by ISIN;
- calendar / holidays (§B.6);
- provenance flags marking FTP-origin vs applied override per value (F-09).

This is the F-10 replay requirement made concrete. Each is added as its own
category with the same defensive-copy discipline as `rates_points`.

### 4.3 What maps to `PricingResult`

`PricingResult` is **reused as-is for the contract shape**; its existing fields
cover most of BLI's needs and its `pv` / `dv01` slots already exist:

- `pv` ← option fair value / structured-product fair value;
- `dv01` ← DV01 (§A.9.2); other Greeks (Gamma, Vega, Theta, CS01, yield/price
  delta) and the self-validation block do **not** have first-class fields.
- `status` / `errors` / `warnings` ← the **three-way** market-data outcome from
  §3.3 / F-15, not a binary: `SUCCESS` when every required input is present from
  the feed; `SUCCESS_WITH_WARNINGS` **only** when a missing input was *resolved*
  by an override or an explicitly-configured fallback (with provenance echoed);
  and `FAILED + MISSING_MARKET_DATA` when a required input (bond price/yield, vol,
  spread, or a curve/curve-mapping) is **missing and unresolved** — the engine
  blocks rather than fabricating a value or hiding the gap behind a warning.
- `assumptions` ← surfaced methodology assumptions (no-repo forward §A.5.1,
  price-vol-on-clean-price note, JGB semi approximation §A.6.2), per AGENTS.md
  "assumptions are surfaced".
- `method` / `engine_name` / `engine_version` ← model provenance (SPEC §21).

**New structured payloads needed** (carried as structured objects, not loose
floats — mirror how `scenario_results: object | None` is already reserved):

- a `BondOptionGreeks` structure (price/yield delta, gamma, vega, theta, DV01,
  CS01) with closed-form and bump values side by side for §A.13.1;
- a `SelfValidationResult` structure (parity, consistency, American≥European,
  benchmark) with each check's unit and pass/warn/critical outcome (§A.13, §2.9);
- a vol-conversion transparency block (original/pricing vol basis, mode, `σ_Y`,
  `Y`, `ModDur`, convexity, derived `σ_P`) per §A.8.4;
- the effective mode-switch snapshot (§A.12).

**Recommendation:** keep the `PricingResult` **core fields frozen and stable**
(other consumers — historical valuation, AI, UI — depend on them) and attach the
BLI-specific structures via the existing `scenario_results`/`diagnostics`
extension points or one new optional `product_detail: object | None` field, added
deliberately as a contract change rather than by widening the numeric core.
Likely **new warning codes**: `OVERRIDE_APPLIED`, `EQUIVALENT_PRICE_VOL_USED`,
`VOL_FALLBACK`, `SELF_VALIDATION_WARNING` (add to `PricingWarningCode` only when a
concrete engine needs each — the module's stated policy).

### 4.4 What maps to `ValuationContext`

Reused **unchanged**. It already provides exactly what BLI governance needs:

- `valuation_date` explicit and required, enforced equal to
  `market_snapshot.valuation_date` — satisfies "no system date in pricing"
  (§A.0 requires every run be reproducible; AGENTS.md rule 11);
- `model_settings: dict` ← the **Annex A mode switches** (`YIELD_OPTION_MODE`,
  `PRICE_VOL_CONVERSION_MODE`, `CRR_STEPS`, `ENABLE_SHIFTED_BLACK`,
  `SHIFTED_BLACK_EPSILON`, `VOL_INTERP_*`, `CURVE_INTERP`,
  `AMERICAN_GREEKS_TREE_STEPS`) ride here and get echoed into the result;
- `reporting_currency` ← settlement currency defaults to bond currency (§A.7.3);
- `scenario` ← reserved slot for the §15 scenario engine (Phase 2).

### 4.5 What can reuse the existing `price(...)` front door

All of it. A BLI engine is a `PricingEngine` Protocol implementer
(`price(product, valuation_context, market_snapshot) -> PricingResult`) registered
per product type, e.g. `register_engine("BOND_OPTION", BondOptionEngine())`,
identical to the IRS pattern. The front door already gives BLI, for free:
`None`/shape guards, the valuation-date-mismatch guard, the
market-snapshot-identity guard, `UNSUPPORTED_PRODUCT` routing, and the
`ENGINE_ERROR` wrapper. No front-door change is required.

### 4.6 What must stay **outside** pricing engines

Per AGENTS.md engineering rules and `docs/13 (removed, see git history)` §6, the engine consumes a resolved
product + context + snapshot and returns a `PricingResult`. It must **not** own:

- FTP / Bloomberg / file ingestion (lives in `data/` only; AGENTS.md rule 2);
- override **capture, persistence, and the no-write-back-to-FTP rule** (F-09) —
  the engine only reads a provenance-tagged snapshot;
- Quote / QuoteVersion / Deal / Warehouse persistence and lifecycle (SPEC §8–§13);
- Internal Pricing Report / termsheet rendering and audit writes (SPEC §9, §18);
- mode-switch **selection UI** (Annex C, SPEC §6.1) — the UI orchestrates and
  passes the chosen switches through `model_settings`; it never prices.

The engine must also never call `date.today()` / `datetime.now()` (valuation date
is explicit) and never fabricate a missing input as `0.0` (§7.6 blocking →
structured `FAILED`), consistent with the review rubric's P0/P1 rules.

---

## 5. Risk list (severity-ranked)

Severity per the task and `docs/12`: **P1** = methodology defect that could cause
wrong PV / risk / misleading report / fake pricing; **P2** = ambiguity that could
cause implementation divergence or missing auditability; **P3** = wording,
naming, report clarity, UI hint, or later cleanup.

| ID | Sev | Section | Finding | Proposed amendment |
| --- | --- | --- | --- | --- |
| F-15 | **P1** | SPEC §7.6, §4.3 | Unresolved missing required input (bond price/vol/spread) must **block or `FAILED + MISSING_MARKET_DATA`**, not `SUCCESS_WITH_WARNINGS`; only a resolved override/configured fallback may succeed-with-warning; no fabricated price/vol/spread/curve/fallback. | A-13 |
| F-04 | **P1** | §A.4.1/§A.4.3 | CRR tree forward/drift calibration unspecified; a naive `spot·e^{rT}` drift on clean price (or on yield) gives wrong American PV and corrupts the A.13.3 check. | A-04 |
| F-02 | **P1** | §A.3 MODE_A | Price-delta conversion has no defined unit basis: `Yield Delta` already carries `10000 × DV01_expiry × N / 100`, so dividing by a bare `DV01_underlying` yields a delta wrong by a `10000` / notional / per-100 factor. Must define the per-100-vs-full-PV basis and the matching denominator before MODE_A Greeks are reported. | A-02 |
| F-01 | P2 | §A.5.2/§A.6.3 | Coupon exactly on expiry (half-open interval) and ex-coupon negative-AI sign under-specified. | A-01 |
| F-03 | P2 | §A.8.3 | MODE_2 convexity term's definition/units of `Convexity` not pinned; wrong units distort `σ_P`. | A-03 |
| F-05 | P2 | §A.13.2 | No put-call parity check defined for European yield options under MODE_B; MODE_A DV01 form would false-fail. | A-05 |
| F-06 | P2 | §A.13.3 vs §A.4.3 | Same American<European condition is "warning + raise step" in one section, "critical + blocked" in another; no tolerance/comparison basis. | A-06 |
| F-08 | P2 | §B.5 vs §A.6.2 | Bond Master has no `m`/compounding field for `yield_convention = OTHER`; `coupon_frequency ≠ m` conflation. | A-08 |
| F-09 | P2 | SPEC §3.8/§7.2 | "Override never overwrites FTP" is a service-layer invariant; engine boundary and provenance carrying not drawn. | A-09 |
| F-10 | P2 | §5.4.3/§B | Snapshot replay needs all feeds + mode switches frozen; current snapshot freezes only rates points. | (build req, §4.2) |
| F-11 | P2 | §B.2/§B.3 vs §A.10/§A.12 | Precedence between feed `interpolation_method` and `CURVE_INTERP`/`VOL_INTERP_*` mode switches unspecified (determinism/audit). | A-10 |
| F-14 | P2 | §A.5.1 vs §7.2 | Repo/specialness forward may need Trader override, but §7.2 whitelist has no repo/forward/specialness field; only workaround is abusing another input, corrupting DV01/CS01 provenance. | A-12 |
| F-16 | P2 | §4.1, §A.6.2/§A.7.3 | BLI enum gap: Annex A/B markets (NZ/KR/HK/SG) and day counts (`ACT/365`, `ACT/365F`, market `ACT/ACT`) exceed current `Currency`/`DayCount` enums; reuse-as-is would reject or silently mis-map records. Extend enums and fail unsupported values explicitly, never coerce. | A-14 |
| F-07 | P3 | §A.13 | Tolerances span three unit bases; report must print each with its unit. | A-07 |
| F-12 | P3 | §A.6.2 | JGB "SEMI 近似" and other documented approximations must be surfaced in `assumptions`/report, not silent. | A-11 |
| F-13 | P3 | §A.2.5/§14.3 | Vega unit reporting ("per 1.00 vol unit, UI ÷100" vs "±1 vol point") must be stated consistently in the report. | A-07 |

**Proposed targeted amendments (for a future Annex A/B revision — not applied
here):**

- **A-01:** In §A.5.2, state whether a coupon paid on the expiry date is included
  in `PV(coupons before expiry)` and whether the expiry forward is ex-coupon;
  cross-reference the `ex_dividend_days` negative-AI case in §A.6.3.
- **A-02 (pre-implementation, before MODE_A Greeks are reported):** In §A.3
  MODE_A, define the **unit basis** of the price-delta conversion, not just the
  symbol name. Specifically: (a) state whether the reported price delta is a
  per-100 clean-price delta or a full-PV delta; (b) show explicitly how a
  `Yield Delta` already scaled by `10000 × DV01_expiry × N / 100` converts back to
  that clean-price basis; and (c) give a denominator carrying the matching
  `10000` / DV01 / notional / per-100 factors, or define a single normalized DV01
  term that already bundles those units. Renaming `DV01_underlying` alone is
  insufficient and would leave a factor-of-`10000`/notional error in the reported
  delta.
- **A-03:** In §A.8.3, define `Convexity` (annualized yield convexity, `1/yield²`
  units, per-100 basis) and show the dimensional check so `σ_P` is unambiguous.
- **A-04:** In §A.4.1, require the CRR tree (price-state and yield-state) to be
  calibrated so its risk-neutral expectation reproduces the §A.5 forward clean
  price / forward yield, discounting on the Option Discount Curve; state the
  per-step drift explicitly.
- **A-05:** In §A.13.2, add the MODE_B parity rule (numerical `C − P` vs
  discounted forward intrinsic in clean-price space) or explicitly exempt MODE_B
  and defer to §A.13.4.
- **A-06:** Reconcile §A.13.3 and §A.4.3 to one severity for American<European,
  add a tolerance, and state whether European is the closed-form or same-tree
  value.
- **A-07:** In §9.1 / §A.13, require every tolerance and every Greek/vega figure
  in the Internal Pricing Report to be printed **with its unit**.
- **A-08:** In §B.5, add an explicit `compounding_frequency` (`m`) field (or state
  `m` is always table-derived and `OTHER` is un-priceable), and note
  `coupon_frequency ≠ m`.
- **A-09:** Add a short note (SPEC §3.8 or a new methodology-boundary appendix)
  that override provenance travels in the snapshot and the pricing engine never
  writes back to FTP.
- **A-10:** In §A.10/§A.12, state that mode switches are authoritative for the run
  and a feed's `interpolation_method` is descriptive metadata only (or vice
  versa) — one effective method per run, recorded.
- **A-11:** In §A.6.2, require documented approximations (JGB semi, par→zero
  simplification §A.10.3) to appear in the result `assumptions` / report.
- **A-12:** In §A.5.1 / §7.2, resolve the specialness override contradiction —
  **either** state explicitly that repo/specialness is unsupported and that a
  materially-special bond is out-of-scope / blocking for MVP (removing the vague
  "use trader override"), **or** add a controlled, whitelisted forward /
  specialness override field with its own reason, provenance, and audit, so the
  §A.5.1 case is reproducible without abusing another market-data input.
- **A-13 (pre-implementation, gates the first engine):** In SPEC §7.6 / a
  methodology-boundary note, state the three-way market-data rule — feed value →
  `SUCCESS`; **resolved** override/configured fallback → `SUCCESS_WITH_WARNINGS`
  with provenance; **unresolved** missing required input → block before pricing
  or `FAILED + MISSING_MARKET_DATA` — and that no engine may fabricate a price,
  vol, spread, curve, or fallback.
- **A-14 (first schema slice):** Require a BLI enum gap analysis before schema
  work: extend `Currency` / `DayCount` (and any market vocabulary) to the Annex
  A/B markets and conventions actually in scope, and require unsupported values to
  fail explicitly (`FAILED + INVALID_PRODUCT`, or a new `MISSING_REFERENCE_DATA`
  code for unrecognised Bond Master conventions) rather than being silently
  coerced.

None of these are applied to the frozen source files in this PR; each is a
proposal for a future, separately reviewed Annex revision.

---

## 6. Proposed next-issue roadmap

### 6.1 What must be done **before** the first BLI pricing engine

1. **BLI enum gap analysis, then product schemas + enums** (§4.1, F-16/A-14):
   *first* run the enum gap analysis — reconcile the Annex A/B markets
   (NZ/KR/HK/SG …) and day counts (`ACT/365`, `ACT/365F`, market `ACT/ACT`)
   against the current `Currency`/`DayCount` enums and extend them, requiring any
   unsupported value to fail explicitly (`INVALID_PRODUCT` / `MISSING_REFERENCE_DATA`)
   rather than be silently coerced. *Then* add `BondOption`,
   `BondLinkedStructuredProduct`, and the five new enums. Schema-level validation
   and tests only — no maths. (Mirrors the Issue #12 product-schema slice.)
2. **Snapshot extension** (§4.2): add the BLI market-data categories to
   `MarketDataSnapshot` with the same immutability discipline. Bond price, curve
   set, vol surface, credit spread, Bond Master reference, calendar, provenance.
3. **Deterministic market-data primitives** (no option maths yet): accrued
   interest and cash-flow generation from Bond Master + calendar; yield-to-price
   conversion (§A.6) honouring `yield_convention`/`m`/`day_count`; forward clean
   price (§A.5). These are the shared, testable building blocks every BLI option
   family reuses.
4. **Pin the market-data resolution rule** (F-15/A-13) as a shared engine
   pre-check *before* any engine prices: feed value → success; resolved
   override/configured fallback → success-with-warning + provenance; unresolved
   required input → block / `FAILED + MISSING_MARKET_DATA`; never fabricate.
5. **Resolve the P1/P2 methodology amendments** that touch the first engines —
   at minimum **A-04** (tree drift, before any American work) and **A-02/A-03**
   (before yield MODE_A Greeks and MODE_2) — as reviewed Annex amendments.

### 6.2 Implementation order (smallest useful first)

1. **European price-based, cash-settled, Black-76 (§A.2)** — closed form,
   closed-form Greeks, self-validation A.13.1 + A.13.2 (per-100). Smallest engine
   that yields a real deterministic PV; matches the "smallest useful version"
   rule and the IRS-engine precedent.
2. **European yield-based MODE_A (§A.3)** — reuses the yield-to-price primitive;
   adds F-02/A-02 resolution.
3. **Equivalent price vol MODE_1 (§A.8.2)** feeding engine (1); MODE_2 later
   (needs A-03).
4. **Physical-delivery invoice calc (§A.7)** — reuses AI-at-settlement primitive;
   in MVP UAT scope.
5. **American CRR (§A.4)** — **only after A-04**; price-state first, then
   yield-state forced MODE_B.
6. Structured product wrapper (deposit leg PV + sold option) composing (1).

### 6.3 What must stay **out of MVP**

Warehouse position, EOD / on-demand revaluation, full Greeks for American,
scenario engine, portfolio aggregation, risk limits/alerts, downstream interface,
Japanese UI/termsheet, and full brand visuals — all Phase 2/3 per SPEC §1A. MODE_2
convexity vol, shifted Black (off by default, §A.11), and MAX(2000) tree steps are
available switches but not MVP defaults.

### 6.4 Existing issues — defer / reframe / leave alone

- **Issue #13 (historical valuation loop):** stays **deferred / reframed** for
  later EOD-revaluation use (`docs/13 (removed, see git history)` §5); its preflight `docs/11` remains valid.
  Do not implement now.
- **Issue #14 (AI inquiry contract):** stays **deferred** and untouched. AI must
  still not bypass the deterministic `price(...)` API when it does land.
- **IRS reference engine (Issue #27 / PR #29):** **leave alone** — it is the
  shared per-product precedent BLI copies; no change.
- **Pricing contract (Issue #10):** **leave alone**; reused as the front door.

No implementation issue is opened or modified by this document; the roadmap above
is a proposal for the maintainer to convert into issues.

---

## 7. Boundaries this preflight preserves

- **Docs only.** No source, tests, CI/workflow, pricing, FTP, Bloomberg,
  QuantLib, or UI changes.
- **The four BLI source spec files are not edited**; every defect is a proposed
  amendment (§5), not an inline rewrite.
- The spine invariants hold: explicit valuation date, no system date in pricing,
  single `price(...)` path, structured `PricingResult`, synthetic data only.
