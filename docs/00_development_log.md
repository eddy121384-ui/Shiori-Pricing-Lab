# 00 Development Log

A concise record of how the project reached the current MVP Core checkpoint:
what happened and why.

This complements `docs/09_mvp_core_runbook.md`:

- **Development log (this file)** — what changed over time and why decisions
  were made.
- **Runbook (`docs/09`)** — how the current system should be operated and
  maintained.

For architecture rationale, see `docs/01`–`docs/03`; this log does not repeat it.

## Timeline

### PR #15 — MarketDataSnapshot and ValuationContext skeleton

- **What changed:** Added `src/shiori_pricing_lab/data/snapshot.py`
  (`MarketDataSnapshot`) and `src/shiori_pricing_lab/valuation/context.py`
  (`ValuationContext`), plus `RateCurve.from_snapshot`.
- **Why it mattered:** Introduced valuation date and market snapshot as
  first-class objects so pricing consumes a frozen, explicit market state
  instead of loose values.
- **Intentionally not done:** No pricing engines, no richer snapshot content
  (FX/vols/fixings), no provider rewrite.
- **Review / validation:** Codex review raised a P1 (data layer depending on
  pricing) and P2s (DataFrame mutability, blank valuation date). Fixed:
  `MarketDataSnapshot` became a pure data object with defensive copies and
  blank-date rejection. Tests green.

### PR #16 — Connect the valuation spine

- **What changed:** Added `ValuationContext.from_snapshot(...)` and rewired the
  Streamlit prototype to the full path
  `provider → MarketDataSnapshot → ValuationContext → RateCurve → scenario`.
  Added `tests/test_spine_flow.py`.
- **Why it mattered:** The spine objects existed but the working flow still
  bypassed them; this made the spine the actual path, with an explicitly chosen
  valuation date (never the system date).
- **Intentionally not done:** Scenario stayed curve-based — no context-aware
  scenario helper in the pricing layer (would invert layering); no new products,
  no UI rewrite.
- **Review / validation:** `pytest` and `ruff` clean.

### PR #17 — Validation raise-path tests for Issue #1

- **What changed:** Added direct tests in `tests/test_data_providers.py` for the
  two previously untested validation paths in `validate_rates_points_frame`:
  missing required columns and empty input frame, including surfacing through a
  provider and through `MarketDataSnapshot.from_rates_points`.
- **Why it mattered:** A closure audit found these raise-paths were the only
  remaining gap for Issue #1.
- **Intentionally not done:** Tests only — no feature, architecture, or
  interface changes.
- **Review / validation:** 33 tests pass; `ruff` clean.

### PR #18 — MVP Core runbook and development log

- **What changed:** Added `docs/09_mvp_core_runbook.md` and this development log
  (`docs/00_development_log.md`).
- **Why it mattered:** Records the checkpoint state and history before the next
  feature begins, so future work starts from a clear baseline.
- **Intentionally not done:** Documentation only — no code changes.

### PR #19 — IRS / OIS product schemas (Issue #12, first slice)

- **What changed:** Added the `src/shiori_pricing_lab/products/` package with
  schema-only product definitions: enums (`PayReceive`, `Currency`, `Frequency`,
  `DayCount`, `BusinessDayConvention`, `FloatingIndex`, `CompoundingMethod`),
  legs (`FixedLeg`, `FloatingLeg`), and products (`InterestRateSwap`,
  `OvernightIndexedSwap`), plus `tests/test_products.py`.
- **Why it mattered:** Supplies the **Product Definition** piece of the spine
  (`Product Definition + Valuation Context + Market Data Snapshot + Pricing
  Engine = Valuation Result`). This is **schema only — not a pricing engine**:
  it defines and validates the trade terms a future engine will consume.
- **Validation fixes from Codex review:**
  - `product_type` is non-overridable — a `field(init=False)` discriminator, so
    an IRS always serializes as `"IRS"` and an OIS as `"OIS"`.
  - Enum-backed fields have runtime coercion/validation (`coerce_enum`): valid
    raw strings coerce to members; blanks and unknown values are rejected with a
    clear error, instead of relying on type hints alone.
  - Schedule dates require strict `YYYY-MM-DD`; compact (`20260701`) and ISO
    week (`2026-W27-3`) forms are rejected so dates round-trip unchanged.
  - OIS `floating_leg.reset_frequency` allows only `None` or `Frequency.DAILY`
    (the reset is daily / implicit); longer resets are rejected.
- **Intentionally not done:** No pricing, PV, DV01, cashflows, schedules,
  calendars, or day-count maths; no market data, valuation date, or curves on
  products; no CCS or FX Swap schemas yet.
- **Review / validation:** `python -m pytest -q` → 74 passed; `ruff` clean.
  Issue #12 is only **partially complete** — IRS and OIS landed; **CCS and FX
  Swap schemas remain.**

### PR #21 — CCS / FX Swap product schemas (Issue #12, second slice)

- **What changed:** Added the CCS and FX Swap product schemas, completing the
  vanilla-rates product-schema set:
  - `CrossCurrencyLeg` — wraps a per-leg `currency` + `notional` around a reused
    `FixedLeg` / `FloatingLeg`, so a CCS carries **per-leg currency and
    notional** (its defining two-currency / two-notional shape) without changing
    the single-currency legs.
  - `CrossCurrencySwap` — `product_type = "CCS"`; holds `leg_1` / `leg_2`,
    explicit `initial_exchange` / `final_exchange` booleans, schedule dates, and
    business day convention. The basis spread reuses `FloatingLeg.spread`; there
    is no separate `basis_spread` field.
  - `FXSwap` — a **flat** schema (not built from `FixedLeg` / `FloatingLeg`) with
    `near_date` / `far_date`, `base_notional`, `near_action`, and `near_rate` /
    `far_rate` treated as **frozen trade terms, not live market data** (named
    `near_rate`, not `spot`; forward points are not stored).
  - `BuySell` enum (`BUY` / `SELL`) for the FX swap near-leg direction.
  - `TWD` added to `Currency`.
  - `_validation.py` now holds the shared low-level product validation helpers
    (strict `YYYY-MM-DD` parsing, non-blank checks) reused by IRS / OIS / CCS /
    FX Swap; the per-product validators stay product-specific.
- **Why it mattered:** Completes the **Product Definition** piece of the spine
  for all four MVP vanilla-rates products. This is still **schema only — not a
  pricing engine**: no pricing, PV, DV01, cashflows, schedules, calendars,
  curves, market data, or valuation logic was added.
- **Intentionally not done:** No pricing engine or valuation outputs; no
  cashflow/schedule/calendar generation; no curve bootstrapping, market data, or
  product lifecycle events.
- **Review / validation:** `python -m pytest -q` → **129 passed**; `ruff`
  clean. Codex reviewed the PR and found no major issues. **Issue #12's
  product-schema scope is now complete** (IRS, OIS, CCS, FX Swap).

### PR #23 — Deterministic pricing engine contract first slice

- **What changed:** Added the first deterministic pricing engine **boundary** for
  Issue #10 — the contract and routing seam only, no pricing maths.
  - New modules: `src/shiori_pricing_lab/pricing/result.py`,
    `src/shiori_pricing_lab/pricing/errors.py`,
    `src/shiori_pricing_lab/pricing/engine.py`.
  - Updated: `src/shiori_pricing_lab/pricing/__init__.py` and
    `tests/test_pricing_engine.py`.
  - Result contract: `PricingResult`, `PricingStatus`, `PricingMessage`,
    `PricingErrorCode`, `PricingWarningCode`.
  - Engine seam: `PricingEngine` Protocol, `PricingEngineRegistry`,
    `register_engine(...)`, and the front-door
    `price(product, valuation_context, market_snapshot)`.
- **Why it mattered:** Supplies the **Pricing Engine** seam of the spine
  (`Product Definition + ValuationContext + MarketDataSnapshot → price(...) →
  PricingResult`). It is **contract only — it does not calculate values.** No
  real product engines are registered yet, so all current products
  (IRS / OIS / CCS / FX Swap) return `FAILED + UNSUPPORTED_PRODUCT`. The future
  fields `pv`, `dv01`, `cashflows`, `scenario_results` exist on `PricingResult`
  but default to `None`.
- **Hybrid failure model:**
  - Expected *domain* failures return `PricingResult(status=FAILED, errors=[...])`.
  - *Contract / programming* violations raise pricing exceptions from
    `pricing/errors.py`.
- **Guardrails (from the design + Codex review):**
  - No system date usage (`date.today()` / `datetime.now()`), no market-data
    fetching inside pricing, no data-provider imports, and no UI / AI /
    historical-loop dependency.
  - No `pricing ↔ valuation` runtime import cycle (the engine references
    context / snapshot / product types only under `TYPE_CHECKING`).
  - `valuation_context.market_snapshot` must exist and must not be `None`
    (contract violation otherwise).
  - Same-date but different snapshot objects are rejected with
    `MARKET_SNAPSHOT_MISMATCH`.
  - `PricingMessage.code` must be a known `PricingErrorCode` or
    `PricingWarningCode`.
  - `PricingResult.warnings` and `errors` normalize to tuples of
    `PricingMessage` (bare strings rejected).
- **Intentionally not done:** No product pricing, PV, DV01, cashflows, curve
  bootstrapping, calendars, data adapters, UI, AI, or historical valuation loop.
- **Review / validation:** `python -m pytest -q` → **175 passed**;
  `python -m ruff check src/shiori_pricing_lab tests` → **All checks passed**.
  Codex reviewed across several rounds; all raised P2s were addressed.
- **Issue #10 status:** **first slice complete; the issue remains open /
  partially complete.** The remaining work is the per-product deterministic
  pricing engines (one registered per product type).

### PR (this) — IRS reference engine design preflight (Issue #10)

- **What changed:** Added `docs/10_irs_reference_engine_preflight.md`, the design
  preflight for the **first per-product deterministic reference engine (IRS
  only)** behind the existing `price(...)` contract. Lightly noted the preflight
  in `docs/09_mvp_core_runbook.md` (section 9) and in this log.
- **Why it mattered:** PR #23 landed the pricing **contract** but no engine, so
  every product still returns `FAILED + UNSUPPORTED_PRODUCT`. Before writing the
  first engine, this preflight fixes scope (narrow vanilla IRS, **USD-only** —
  non-USD fails explicitly because the snapshot/curve layer has no enforceable
  curve-currency metadata yet), required market data (one synthetic curve from
  the snapshot, no providers), the schedule/
  accrual boundary (regular periods, no calendar, clean-division-or-fail),
  safe day counts (`ACT_360` / `ACT_365_FIXED`; others fail explicitly), the
  `PricingResult` output shape, explicit failure/warning behavior, and the tests
  the implementation slice must add.
- **Intentionally not done:** **Docs only — no code.** No pricing engine, PV,
  DV01, cashflow generation, schedule/calendar engine, curve bootstrapping, data
  adapter, UI, AI, or historical loop. No new error codes implemented (some are
  *proposed* for the implementation slice). Issue #10 is **not** closed.
- **Review / validation:** Documentation-only change; no test or lint impact.
  Existing suite remains green from PR #23 (`python -m pytest -q` → 175 passed).
  Codex P2 addressed in a follow-up commit: the preflight no longer claims a
  wrong-currency curve can be detected via `MISSING_MARKET_DATA` (the current
  `rates_points` schema has no enforceable curve-currency field); the first slice
  is now **USD-only** with non-USD products failing explicitly before curve
  construction, and multi-currency curve selection is documented as future work.

### PR #29 — First USD-only IRS reference engine (Issue #27)

- **What changed:** Implemented the **first real per-product pricing engine**
  behind the existing `price(product, valuation_context, market_snapshot)`
  contract, per the preflight (`docs/10`).
  - New `src/shiori_pricing_lab/pricing/irs_engine.py` (`IRSReferenceEngine`),
    registered for `product_type == "IRS"` via
    `register_engine("IRS", IRSReferenceEngine())` in
    `pricing/__init__.py`.
  - New deterministic regular-period schedule helper
    `src/shiori_pricing_lab/pricing/schedule.py`
    (`generate_regular_schedule`) — clean monthly / quarterly / semi-annual /
    annual schedules only; stubs and unsupported frequencies raise.
  - Tests in `tests/test_irs_reference_engine.py`, plus a small update to
    `tests/test_pricing_engine.py` to reflect the one registered engine.
- **Why it mattered:** PR #23 landed the contract but no engine, so every
  product returned `FAILED + UNSUPPORTED_PRODUCT`. This turns a **supported USD
  synthetic IRS** into a real, deterministic `SUCCESS` PV, while everything
  out of scope keeps failing explicitly.
- **Supported shape (deliberately narrow):** USD-only, synthetic data, one
  `MarketDataSnapshot`-derived `RateCurve` used as **both** discount and forecast
  curve (single curve, no bootstrapping, no calendar, no business-day
  adjustment). PV is deterministic (linear zero-rate interpolation, discount
  factor `1 / (1 + zero_rate * year_fraction)`); only `ACT_360` and
  `ACT_365_FIXED` day counts are supported.
- **Output:** a supported USD IRS returns a deterministic `pv`; `dv01` and
  `cashflows` remain `None`. Unsupported / out-of-scope paths return a structured
  `FAILED` with `pv is None` — never a fake `0.0`. Specifically: non-USD product
  currency and non-USD reporting currency fail explicitly (`INVALID_PRODUCT`);
  unsupported floating-leg conventions (anything other than a quarterly
  `USD_SOFR_TERM_3M` leg with reset = payment frequency and no compounding) fail
  explicitly; missing / unusable market data returns `MISSING_MARKET_DATA`.
- **Intentionally not done:** OIS / CCS / FX Swap engines remain unsupported.
  No calendars, business-day adjustment, stubs, bootstrapping, multi-curve,
  currency-tagged curves, FX conversion, historical fixings, DV01, cashflows,
  external data, UI, or AI layer. Issue #13 (historical valuation loop) and
  Issue #14 (AI inquiry contract) remain downstream and unchanged.
- **Review / validation:** `python -m pytest -q` → **190 passed** at merge
  (final PR #29 state after the Claude Code P2 fixes; the earlier initial Codex
  run reported 186); `ruff` clean. Codex review comments were addressed.
  **Issue #27 and Issue #10 are both closed (completed).** IRS is the first of
  the per-product engines; the remaining work is **not** Issue #10 itself but
  downstream / follow-up engine work (OIS / CCS / FX Swap and deferred
  extensions).

### PR (this) — Historical valuation loop design preflight (Issue #13)

- **What changed:** Added `docs/11_historical_valuation_loop_preflight.md`, the
  design preflight for the **first historical valuation loop skeleton**
  (Issue #13), plus a short pointer in `docs/09_mvp_core_runbook.md` (section 9)
  and this log entry.
- **Why it mattered:** With the single-date `price(...)` contract and the first
  engine (USD IRS) in place, the loop is the next downstream slice. The preflight
  fixes, before any code: the minimal request shape, explicit `YYYY-MM-DD`
  caller-supplied dates, synthetic per-date `MarketDataSnapshot` supply, per-date
  reuse of `ValuationContext.from_snapshot` + the `price(...)` front door (same
  snapshot object, so no `MARKET_SNAPSHOT_MISMATCH`), a stable per-date result
  table, per-date failure rows, required provenance, the no-system-date /
  no-future-data / no-external-data rules, and the deterministic tests the
  implementation slice must add.
- **Boundaries recorded:** one pricing path only (reuse `price(...)`, never call
  engines directly), no market-data fetching, no invented rates, no second/toy
  valuation path, synthetic data only; failed dates are structured `FAILED` rows
  (`pv is None`), never a fake `0.0`.
- **Intentionally not done:** **Docs only — no code.** No loop, no backtest
  analytics (P&L / returns / charts), no persistence, no UI, no AI layer.
  Issue #14 is not started; Issue #13 is not closed.
- **Review / validation:** Documentation-only change; no test or lint impact.

### PR #33 — Bond Linked Structured Pricer v1.3 reference specs (product pivot)

- **What changed:** Merged the **authoritative BLI v1.3 reference specs** into
  `docs/bond_linked_structured_pricer/` (`SPEC_v1.3.md`, `ANNEX_A_v1.3.md`,
  `ANNEX_B_v1.3.md`, `ANNEX_C_v1.3.md`, `README.md`). Reference specs only — no
  pricing, FTP, UI, Bloomberg, or QuantLib code.
- **Why it mattered:** Records a **product-priority pivot**: the near-term
  priority moves from the original **Vanilla Rates Core / IRS-first** path to the
  **Bond Linked Structured Pricer (BLI) MVP**. This is a re-ordering, **not** a
  teardown — the Rates Core / IRS work stays as the shared deterministic pricing
  infrastructure, and the spine
  (`Product Definition + ValuationContext + MarketDataSnapshot → price(...) →
  PricingResult`) is unchanged. Annex A is the authoritative BLI pricing
  methodology source; Annex B the reference FTP / market-data file spec; Annex C
  the UI/UX and visual guidance reference.
- **Review found real methodology defects (fixed in-PR):** Codex review caught
  and PR #33 fixed three Annex A issues before any implementation — clean-price
  tree coupon handling (§A.4.2), price-based put-call parity notional scaling
  (§A.13.2), and the parity tolerance basis for full-PV checks (§A.13.2). Lesson
  recorded: **authoritative methodology docs must get quant-style review before
  implementation** (`docs/12_pr_review_rubric.md`), because a methodology defect
  in code is wrong PV/risk, not a style nit.
- **Priority re-sequencing:** Near-term priority is **no longer** the historical
  valuation loop. Issue #13 is **deferred / reframed** for later EOD /
  revaluation / warehouse valuation use (its `docs/11` preflight stays valid);
  Issue #14 (AI inquiry) remains deferred. The near-term priority is **BLI
  methodology teardown and integration preflight** — the next planned PR is
  `docs/14_bond_linked_spec_teardown_and_integration_preflight.md`.
- **Checkpoint doc:** `docs/13_bond_linked_pivot_checkpoint.md` records this pivot
  and the current repo status.
- **Intentionally not done:** **Docs only.** No code, tests, CI, pricing, FTP,
  Bloomberg, QuantLib, or architecture rewrite; no implementation issues opened or
  modified; the four BLI spec source files are not edited and their line endings /
  whitespace are not normalized.

### PR #35 — BLI v1.3 methodology teardown / integration preflight (complete)

- **What changed:** Merged `docs/14_bond_linked_spec_teardown_and_integration_preflight.md`,
  the docs-only teardown of the BLI v1.3 methodology (Annex A) plus a market-data
  readiness review (Annex B / SPEC §7) and an integration map onto the existing
  pricing spine (`price(...)`, `PricingResult`, `ValuationContext`,
  `MarketDataSnapshot`, product schemas). This **completes the BLI methodology
  teardown and integration preflight** the pivot checkpoint (`docs/13` §4) named
  as the next step after PR #33/#34.
- **Why it mattered:** `docs/14` is now the **guide for BLI implementation issue
  sequencing** — it carries the severity-ranked risk list (P1/P2/P3 findings with
  proposed targeted Annex amendments) and the §6 roadmap. The existing
  deterministic pricing spine remains the target: BLI will register behind the
  **same** `price(...)` front door as a per-product engine, not a parallel path.
- **Review found and fixed real issues (Codex round 1):** two P1s were sharpened
  before merge — unresolved missing required market data must **block or
  `FAILED + MISSING_MARKET_DATA`**, never `SUCCESS_WITH_WARNINGS` and never a
  fabricated value (F-15); and the MODE_A price-delta conversion needs an explicit
  per-100-vs-full-PV **unit basis**, not just a symbol rename (F-02). Also added a
  repo/specialness override-path finding (F-14) and a BLI enum-gap finding (F-16).
- **Next work (no code yet):** convert the `docs/14` §6 roadmap into concrete
  GitHub issues. The **first implementation slice is prerequisites** — enum gap
  analysis, product schema, and the market-data boundary — **not** the American
  tree, AI inquiry, UI, Bloomberg, or a QuantLib backend. **Do not start pricing
  engine code yet.**
- **Intentionally not done:** **Docs only.** No source, tests, CI, pricing, FTP,
  Bloomberg, QuantLib, or UI; the four BLI source spec files are not edited; no
  implementation issue opened yet.

### PR #45 — BLI controlled-vocabulary enums (Issue #37 code slice)

- **What changed:** Merged the code-level follow-up to the `docs/14` enum-gap
  preflight (F-16/A-14): `Currency` gained `NZD`, `KRW`, `HKD`, `SGD`; five new
  standalone BLI enums landed (`PayoffBasis`, `OptionType`, `ExerciseStyle`,
  `SettlementType`, `Position`) plus `BondYieldConvention`
  (`SEMI_ANNUAL_COMPOUND`, `ANNUAL_COMPOUND`, `SIMPLE_YIELD`,
  `JAPANESE_COMPOUND`, `OTHER`); `PricingErrorCode.MISSING_REFERENCE_DATA` was
  added for reference/static data that is present but carries an unrecognised
  convention (distinct from `MISSING_MARKET_DATA`, a required observation that
  is absent). `tests/test_bli_enums.py` covers all of the above plus unknown
  values still failing through the existing `coerce_enum` path.
- **Why it mattered:** This is the smallest useful slice the `docs/14`
  preflight named as prerequisite work: the enums a future `BondOption` /
  `BondLinkedStructuredProduct` schema needs now exist and are reviewed, ahead
  of any schema, snapshot, or engine code.
- **Deliberately deferred, not silently decided:**
  - **`DayCount` is untouched.** `ACT_365`, `ACT_365F`, `ACT_ACT`, and
    `ACT_ACT_ICMA` are not added, and `ACT_365_FIXED` / `ACT_ACT_ISDA` are not
    aliased or renamed. These conventions diverge in ways that matter for
    yield-to-price (fixed-vs-actual year length; ISDA vs ICMA `ACT/ACT`
    variants), so the naming decision is deferred pending a reviewed,
    Annex-driven amendment (`docs/14` §5, A-14) rather than guessed here.
  - **No Bond Master / jurisdiction enum was added.** A market/jurisdiction
    vocabulary (beyond `Currency`) stays deferred unless a future Bond Master
    or `MarketDataSnapshot` extension issue actually needs one.
- **Intentionally not done:** No `BondOption` / `BondLinkedStructuredProduct` /
  Bond Master schema, no `MarketDataSnapshot` extension or market-data
  resolver, no accrued interest / cashflows / yield-to-price / forward clean
  price / Black-76 / American tree, no QuantLib, Bloomberg/FTP, UI, or AI
  inquiry code.
- **Review / validation:** `python -m pytest -q` → **215 passed**; `ruff check
  .` → all checks passed; `git diff --check` → clean. Issue #37 was **not**
  closed by this PR.
- **Status (precise):** PR #45 completed the **first code-level
  controlled-vocabulary slice** — currencies (`NZD`/`KRW`/`HKD`/`SGD`), the five
  BLI product enums, `BondYieldConvention`, and
  `PricingErrorCode.MISSING_REFERENCE_DATA`. **Issue #37 remains open** because
  the **`DayCount` and market/jurisdiction vocabulary decisions are still
  deferred** (see `docs/14` §5, A-14) — the enum-gap resolution is not finished
  until those are explicitly resolved or deliberately scoped into the next
  issue. **Issue #38** (BLI product schemas for `BondOption` /
  `BondLinkedStructuredProduct`) **may be prepared next, but it must not land
  product schemas that depend on unresolved `DayCount` / Bond Master convention
  assumptions.** Before #38 can be considered complete, **either** (1) #38
  explicitly **excludes** `DayCount` / Bond Master convention fields and keeps
  them in the Bond Master / later issues, **or** (2) the `DayCount` vocabulary
  decision is made first in a reviewed prerequisite slice. Do **not** treat #38
  as fully unblocked without that qualifier. Issues **#39–#42 and #44
  (Black-76) are not started** and should not be started before #38 lands.

### PR (this) — BLI product schema preflight (Issue #38)

- **What changed:** Added
  `docs/15_bli_product_schema_preflight_issue_38.md`, a docs-only preflight
  answering whether `BondOption` and `BondLinkedStructuredProduct` can be
  defined as pure deal-term schemas without the still-unresolved `DayCount` /
  Bond Master convention decision (Issue #37 remains open per PR #46).
- **Conclusion:** **`BondOption` can proceed; `BondLinkedStructuredProduct`
  should be deferred.** `BondOption` can be a fully pure deal-term schema
  (identity, option terms, strike/payoff-basis cross-field validation, dates,
  notional, position) with no `day_count`, `yield_convention`, or
  `compounding_frequency` field — those are Bond Master reference data.
  `BondLinkedStructuredProduct` is **not** safe to describe as a complete,
  valuation-meaningful schema for #38: its deposit leg carries **contractual
  economic terms** (deposit rate/yield, principal repayment rule) that a
  schema cannot omit and still reproduce the customer's cashflows — this is
  distinct from, and in addition to, the `DayCount`/calendar blocker. A
  wrapper may be built **only** as an explicitly-labeled **non-economic
  relationship shell** (deposit notional/currency/dates, an embedded
  `BondOption`, and a `participation_ratio` that is derived from — or
  validated against — `bond_option.notional / deposit_notional`, never
  freely set). A real economic wrapper requires a later, separately reviewed
  slice that resolves the deposit-leg economic terms, the funding-curve vs.
  fixed-rate question, and the `DayCount`/calendar decision (A-14).
- **Why it mattered:** Prevents #38 from (a) silently reusing the existing
  rates-core `DayCount` enum on a deposit leg that was never reconciled
  against Annex A/B — exactly the "silently coerced to the wrong convention"
  failure Issue #37 exists to prevent (`docs/14` F-16) — and (b) shipping a
  structured-product schema that looks complete but silently omits the
  deposit rate/yield and repayment terms needed to reproduce customer
  cashflows (Codex P2 review of this PR).
- **Intentionally not done:** **Docs only.** No `BondOption` /
  `BondLinkedStructuredProduct` code, no tests, no schema
  registration/export changes, no pricing engine, no `MarketDataSnapshot`
  change, no Bond Master schema, no `DayCount` enum decision (documented as
  required, not made). Issues #39–#42 and #44 (Black-76) are not started.
- **Review / validation:** Documentation-only change; `git diff --check` →
  clean. No test or lint impact (no source changed).

## Checkpoint summary

- Issues #1 and #2 are closed (PR #18 merged).
- MVP Core (Phase 1, Vanilla Rates Core spine) is complete: the
  `provider → snapshot → context → curve → scenario` flow is wired and tested.
- Issue #12 product-schema scope is **complete**: IRS and OIS (PR #19) plus CCS
  and FX Swap (PR #21) are defined and validated, schema-only.
- Issue #10 first slice is **complete** (PR #23): the deterministic pricing
  engine **contract** exists —
  `Product Definition + ValuationContext + MarketDataSnapshot → price(...) →
  PricingResult`. **Issue #10 is now closed (completed)** — the per-product
  engine work it tracked is downstream / follow-up (OIS / CCS / FX Swap and
  deferred extensions), not Issue #10 itself.
- Issue #27 is **closed** (PR #29): the **first per-product reference engine**
  (USD-only IRS) is now registered behind `price(...)`. A supported USD IRS
  returns a deterministic PV; `dv01` and `cashflows` stay `None`; every
  out-of-scope path returns a structured `FAILED` with `pv is None`. OIS / CCS /
  FX Swap remain unsupported.
- **Near-term priority pivoted to the Bond Linked Structured Pricer (BLI).** The
  BLI v1.3 reference specs landed (PR #33), the pivot was recorded
  (`docs/13`, PR #34), and the **methodology teardown / integration preflight is
  now complete** (`docs/14`, PR #35). `docs/14` is the guide for BLI
  implementation issue sequencing.
- Recommended next development step: **convert the `docs/14` §6 roadmap into
  concrete GitHub issues** — no pricing engine code yet. The **first slice is
  prerequisites** (enum gap analysis, product schema, market-data boundary), not
  the American tree, AI inquiry, UI, Bloomberg, or a QuantLib backend. The IRS
  reference engine and the rest of the deterministic spine remain the shared
  target; OIS / CCS / FX Swap engines, the historical valuation loop (#13), and
  the AI inquiry contract (#14) stay downstream / deferred.
- **Issue #37's first controlled-vocabulary code slice landed (PR #45).** New
  BLI currencies (`NZD`, `KRW`, `HKD`, `SGD`), the five BLI product enums, and
  `BondYieldConvention` exist and are tested; `PricingErrorCode` now carries
  `MISSING_REFERENCE_DATA`. **Issue #37 remains open**: the `DayCount`
  vocabulary and market/jurisdiction vocabulary decisions are **still deferred**
  pending a reviewed Annex-driven decision (`docs/14` §5, A-14), and no Bond
  Master / jurisdiction enum was added. **Issue #38** (BLI product schemas for
  `BondOption` / `BondLinkedStructuredProduct`) may be **prepared** next, but it
  **must not land product schemas that depend on unresolved `DayCount` / Bond
  Master convention assumptions**. Before #38 can be considered complete,
  **either** (1) #38 explicitly **excludes** `DayCount` / Bond Master convention
  fields (keeping them in the Bond Master / later issues), **or** (2) the
  `DayCount` vocabulary decision is made first in a reviewed prerequisite slice.
  **Do not start Issues #39–#42 or #44 (Black-76) yet.**
- **BLI product-schema preflight for Issue #38 is complete
  (`docs/15_bli_product_schema_preflight_issue_38.md`).** Conclusion:
  `BondOption` can proceed as a pure deal-term schema with no `DayCount` /
  Bond Master convention fields. `BondLinkedStructuredProduct` should be
  **deferred** unless explicitly built and labeled as a **non-economic
  placeholder** — its deposit leg carries contractual economic terms
  (deposit rate/yield, principal repayment rule) that a schema cannot omit
  and still reproduce customer cashflows, on top of the still-unresolved
  `DayCount`/calendar decision (A-14). A complete, economic wrapper requires
  a later, separately reviewed slice. `docs/15` §6 lists the
  acceptance-criteria tests the future #38 implementation PR should add,
  including deriving/validating `participation_ratio` against
  `bond_option.notional / deposit_notional` rather than storing it freely.
- **Market-data ingestion terminology clarified, docs-only
  (`docs/16_market_data_ingestion_terminology.md`).** Older "FTP file"
  language inherited from the BLI v1.3 Annex B / SPEC specs should be
  understood as legacy / ambiguous terminology, not a design decision.
  Future docs and issues should use the disambiguated terms:
  "FTP/SFTP transport," "Market Data Ingestion," "API Connector,"
  "File-based Import," and "Treasury FTP" / "Funding Curve" (the latter is a
  business funding-cost input, currency × tenor × rate — not generic
  market-data file transport). System direction is **API-first /
  file-minimal**: external market data should prefer API-based ingestion,
  and **Treasury FTP / Funding Curve is the first likely MVP
  manual-upload surface**, not bond price/yield, curves, vol, spread, Bond
  Master, or calendar. Screenshot-assisted capture remains a future
  fallback only. No code or implementation was changed.
- **`BondOption` product schema implemented — Issue #38 partial
  (`src/shiori_pricing_lab/products/bond_option.py`,
  `tests/test_bond_option.py`).** Following `docs/15` §2/§5, `BondOption` is
  a pure deal-term schema (`product_id`, `underlying_isin`, `currency`,
  `payoff_basis`, `option_type`, `exercise_style`, `settlement_type`,
  `settlement_lag_days`, `strike_price`/`strike_yield`, `expiry_date`,
  `exercise_start_date`, `notional`, `position`) using the controlled
  vocabulary landed in PR #45. It carries no `day_count`,
  `yield_convention`, `compounding_frequency`, Bond Master, market-data, or
  pricing-output field. **`BondLinkedStructuredProduct` remains deferred** —
  not implemented, not even as a placeholder — pending the deposit-leg
  economic terms (deposit rate/yield source, principal repayment rule), the
  Treasury FTP / Funding Curve semantics (`docs/16`), and the
  `DayCount`/calendar boundary (A-14). No pricing engine, market-data
  ingestion, or UI code was touched by this slice.
- **BLI MVP vertical-slice preflight written, docs-only
  (`docs/17_bli_mvp_vertical_slice_preflight.md`).** Defines the smallest
  complete Bond Linked Structured Product MVP — one plain-vanilla bond, one
  deposit leg, one embedded `BondOption` leg, European exercise and cash
  settlement first — as a target for future implementation slices, not as
  an implementation itself. It does not resolve the deposit rate/yield
  source (fixed term vs. Treasury FTP / Funding Curve lookup vs. both under
  an explicit mode) or the `DayCount`/calendar decision (A-14); both remain
  open MVP decisions for the future `DepositLeg` preflight. It restates
  that `participation_ratio` must be derived/validated, not freely set, and
  adds a QuantLib usage policy (allowed as a computational library, never
  as an unreviewed methodology owner). Proposes seven small future slices
  (A–G: deposit leg, bond reference fixture, wrapper schema, manual MVP
  input bundle, deterministic payoff skeleton, QuantLib benchmark if
  needed, MVP runner example) — none started here. Issue #38 is not
  closed; `BondLinkedStructuredProduct` remains deferred.
- **DepositLeg schema preflight written (Slice A), docs-only
  (`docs/18_deposit_leg_schema_preflight.md`).** Incorporates the real
  Treasury FTP rate matrix format (business_date × currency × tenor ×
  quote_side → rate; percent-quoted, e.g. `3.5500` means `3.5500%` and must
  convert to decimal `0.035500` for pricing; default quote side `MID`,
  configurable; currencies without a bid/mid/offer breakdown are treated as
  MID-equivalent, not inferred). Recommends an explicit `deposit_rate_mode`
  vocabulary (`FIXED_RATE` / `TREASURY_FTP_REFERENCE` /
  `MANUAL_VERIFIED_RATE`) rather than picking one source, with exactly the
  matching fields required per mode. Recommends the narrowest MVP principal
  repayment rule (`FULL_PRINCIPAL_AT_MATURITY` on the deposit leg, option
  payoff calculated separately at the wrapper level); defers
  `PRINCIPAL_AFFECTED_BY_OPTION_PAYOFF` and `PHYSICAL_BOND_DELIVERY`. Flags
  a still-open gap: no controlled tenor vocabulary exists yet for FTP
  tenors (`O/N`, `1W`...`3Y`), which blocks safe `TREASURY_FTP_REFERENCE`
  validation until resolved. `day_count`/`business_day_convention`/
  `calendar` remain deferred, same as `BondOption`. Restates
  `participation_ratio` must be derived/validated against
  `bond_option.notional / deposit_notional`. No `DepositLeg` code, FTP
  parser, ingestion, wrapper, pricing engine, or tests were added.
- **DepositLeg preflight tightened after Codex P2 review, docs-only
  (`docs/18` update).** Three schema/market-data boundary issues fixed:
  (1) `TREASURY_FTP_REFERENCE` no longer carries a `business_date` field —
  renamed to `ftp_rate_selector` (currency/tenor/quote_side only); the
  applicable business date is chosen from the pricing run's
  `MarketDataSnapshot` / MVP input bundle, never frozen into the immutable
  `DepositLeg`. (2) `MANUAL_VERIFIED_RATE` no longer carries the manual
  rate or its audit metadata directly — renamed to
  `manual_input_reference` (a reference marker only); the actual rate,
  source, as-of date, and entered-by/run id live in the input-bundle /
  audit-provenance layer, not on the product schema. A rate meant to be a
  frozen trade term is `FIXED_RATE`, not this mode. (3) The "implement
  with placeholder tenor validation" option for `TREASURY_FTP_REFERENCE`
  was removed from the recommended next slice — a controlled FTP tenor
  vocabulary (not the existing `Frequency` enum) must exist before that
  mode can be enabled; the preferred sequencing now adds the tenor /
  quote-side / deposit-rate-mode vocabularies first. No code, tests, or
  other doc besides `docs/18`/`docs/09` was touched.
- **DepositLeg / Treasury FTP controlled vocabulary landed
  (`src/shiori_pricing_lab/products/enums.py`,
  `tests/test_deposit_leg_vocab.py`).** Adds `DepositRateMode`
  (`FIXED_RATE`/`TREASURY_FTP_REFERENCE`/`MANUAL_VERIFIED_RATE`),
  `TreasuryFTPQuoteSide` (`BID`/`MID`/`OFFER`), and `TreasuryFTPTenor`
  (`O/N` through `3Y`, plus `DEMAND_SAVINGS`) — the enum foundation
  `docs/18` §12 required before `TREASURY_FTP_REFERENCE` mode can be
  enabled. `TreasuryFTPTenor` is a deliberately separate vocabulary from
  the existing `Frequency` enum (a payment/reset period vocabulary, not a
  tenor label set); tests assert the two enums' value sets are disjoint.
  Unsupported/ambiguous tenor spellings (`ON`, `O_N`, `1WK`, `12M`,
  whitespace variants, blank) are rejected through the existing
  `coerce_enum` path, matching the case-sensitive, exact-value coercion
  convention already used for every other product enum — no new
  coercion policy was introduced. **This PR adds vocabulary only:** no
  `DepositLeg` schema, Treasury FTP parser, ingestion,
  `BondLinkedStructuredProduct`, pricing engine, or market-data code was
  added. `TREASURY_FTP_REFERENCE` still does not imply an FTP parser or
  ingestion exists — only that the mode's controlled vocabulary is now
  available for a future `DepositLeg` implementation to validate against.
- **`DepositLeg` product schema implemented — BLI MVP Slice A
  (`src/shiori_pricing_lab/products/deposit_leg.py`,
  `tests/test_deposit_leg.py`).** Following `docs/18` §3/§4/§8, `DepositLeg`
  is a pure deal-term/rate-source-selector schema
  (`deposit_leg_id`, `deposit_notional`, `currency`, `start_date`,
  `maturity_date`, `deposit_rate_mode`, `principal_repayment_rule`, plus an
  optional `tenor`), consumed as a leg component by a future
  `BondLinkedStructuredProduct` wrapper — not a standalone product; it
  carries a `leg_type` discriminator (`"DEPOSIT_LEG"`), not a
  `product_type`. It uses the controlled vocabulary landed in the prior
  slice: `DepositRateMode`, and a new, deliberately narrow
  `PrincipalRepaymentRule` enum with only `FULL_PRINCIPAL_AT_MATURITY`
  (`PRINCIPAL_PLUS_OPTION_PAYOFF` / `PRINCIPAL_AFFECTED_BY_OPTION_PAYOFF` /
  `PHYSICAL_BOND_DELIVERY` remain deferred to the future wrapper, per
  `docs/18` §6). Exactly one of `fixed_deposit_rate` /
  `ftp_rate_selector` / `manual_input_reference` is required and the other
  two must be `None`, matching `deposit_rate_mode`. `TREASURY_FTP_REFERENCE`
  mode uses a new `TreasuryFTPRateSelector` value object
  (currency/tenor/quote_side only, validated against `Currency` /
  `TreasuryFTPTenor` / `TreasuryFTPQuoteSide`) — **it carries no
  `business_date`, `as_of_timestamp`, `source_file_name`, `loaded_at`, or
  resolved rate**; those remain deferred to a future `MarketDataSnapshot` /
  MVP input bundle. `MANUAL_VERIFIED_RATE` mode stores only a
  `manual_input_reference` marker — **no manual rate value or its audit
  metadata (source, as-of, entered-by, run id) lives on `DepositLeg`**; that
  provenance remains deferred to the input-bundle/audit-provenance layer.
  `day_count`, `business_day_convention`, and `calendar` remain absent, same
  as `BondOption`'s precedent, pending the A-14 decision. **No Treasury FTP
  parser, ingestion, `MarketDataSnapshot`, pricing engine, QuantLib, or
  `BondLinkedStructuredProduct` wrapper was added.** Issue #38 is
  unaffected.
- **BLI wrapper schema preflight written, docs-only
  (`docs/19_bli_wrapper_schema_preflight.md`).** Defines the future
  `BondLinkedStructuredProduct` wrapper boundary: binds exactly one
  `DepositLeg` and exactly one `BondOption` (embedded objects, not
  reference IDs — no registry layer exists yet); recommends deriving
  `participation_ratio` as a computed property from `bond_option.notional /
  deposit_leg.deposit_notional` rather than accepting it as an input field,
  unless a concrete consumer needs the optional-validated-input design
  `docs/15` §3.3 also allows; requires `deposit_leg.currency ==
  bond_option.currency` and `bond_option.expiry_date <=
  deposit_leg.maturity_date`; explicitly flags (rather than silently
  decides) whether `bond_option.expiry_date` must also be on/after
  `deposit_leg.start_date`; keeps `DepositLeg.principal_repayment_rule` at
  `FULL_PRINCIPAL_AT_MATURITY` with option payoff computed separately at
  the wrapper/pricing level; recommends not adding a payoff-linkage enum
  yet. Restates the full market-data/pricing-output exclusion list (FTP
  business date, resolved rates, bond price/yield/vol/spread, PV, premium,
  margin, customer return, ...) must never live on the wrapper. Recommends
  the next slice — wrapper schema implementation only — with an acceptance
  checklist. No wrapper code, pricing, or tests were added. Issue #38
  remains open.
- **BLI wrapper preflight tightened after Codex P2 review, docs-only
  (`docs/19` update).** Two date/settlement boundary issues fixed: (1) the
  date-consistency rule was `bond_option.expiry_date <=
  deposit_leg.maturity_date`, which ignored `BondOption.settlement_lag_days`
  — an option that expires exactly at deposit maturity but has a positive
  settlement lag would actually settle after the deposit's life ends. Now
  requires the option's **effective settlement date**
  (`expiry_date + settlement_lag_days` calendar days, a documented
  calendar-day approximation, no calendar engine) to be on or before
  `deposit_leg.maturity_date`. (2) the doc left `bond_option.settlement_type
  == CASH` as an optional, undecided wrapper check, which would have
  silently allowed a physical-delivery BLI wrapper to construct despite
  MVP being cash-settlement-first. Now **required**: construction must
  raise unless `settlement_type` is `CASH`; physical delivery is deferred
  to a later custody/settlement slice. `docs/09`'s wrapper checkpoint was
  updated to match. No code, tests, or other doc besides `docs/19`/`docs/09`
  was touched.
- **`BondLinkedStructuredProduct` wrapper schema implemented — wrapper
  schema only
  (`src/shiori_pricing_lab/products/bond_linked_structured_product.py`,
  `tests/test_bond_linked_structured_product.py`).** Following `docs/19`,
  the wrapper binds exactly one `DepositLeg` and exactly one `BondOption`
  as embedded objects, with a fixed `product_type =
  "BOND_LINKED_STRUCTURED_PRODUCT"` discriminator. `participation_ratio`
  is a **derived-only property** (`bond_option.notional /
  deposit_leg.deposit_notional`) — not a constructor field, so no
  independently stored value can ever contradict the two notionals it is
  computed from. Validation enforces: `deposit_leg.currency ==
  bond_option.currency` (no cross-currency BLI wrapper); `bond_option.
  settlement_type == SettlementType.CASH` (physical delivery rejected at
  the wrapper level, deferred to a later custody/settlement slice);
  `deposit_leg.principal_repayment_rule ==
  PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY`; `bond_option.
  expiry_date >= deposit_leg.start_date` (the open question `docs/19` §7
  left for this implementation slice, resolved conservatively); and the
  mandatory **effective settlement date guardrail** —
  `bond_option.expiry_date + settlement_lag_days` calendar days must be
  on or before `deposit_leg.maturity_date`, not just the bare expiry date
  (a plain calendar-day approximation, no business-day rolling, no
  holiday calendar, no calendar engine). **No pricing, payoff calculation,
  QuantLib, `MarketDataSnapshot`, MVP input bundle, Treasury FTP parser,
  ingestion, or UI was added.** Issue #38 remains open.
- **BLI bond reference data preflight written (Slice B), docs-only
  (`docs/20_bli_bond_reference_data_preflight.md`).** Defines the minimal
  Bond Reference Data / Bond Master fixture boundary for the underlying
  bond a `BondOption` references, transcribing the required field list
  from `docs/bond_linked_structured_pricer/ANNEX_B_v1.3.md` §B.5 (isin,
  issuer, currency, coupon, coupon_frequency, maturity_date, issue_date,
  day_count, business_day_convention, redemption_amount, callable_flag,
  sinkable_flag, bond_type, yield_convention, ex_dividend_days,
  first/last_coupon_date, status) and classifying each as MVP required/
  optional. Recommends the MVP plain-vanilla eligibility rule (reject
  callable, sinkable, and default-ineligible `OTHER` yield convention),
  explicitly flagging that floating-rate/amortizing/convertible/
  inflation-linked/perpetual/structured-note exclusion has no direct
  Annex B field mapping and must be resolved explicitly by the
  implementation slice (via `bond_type` vocabulary or construction-time
  narrowing), not silently assumed. Recommends a small, manually reviewed
  fixture for MVP — no parser, no file import, no connector. States that
  future pricing must resolve `bond_option.underlying_isin` against the
  fixture and **block** (not guess or silently downgrade) on a missing or
  ineligible bond. Restates the market-data/pricing exclusion list and the
  reference-data-vs-input-bundle distinction. Carries forward, without
  resolving, `docs/14` F-08 (no `m`/compounding-frequency field for
  `yield_convention = OTHER`) and the still-open `DayCount` vocabulary
  question (A-14) as it applies to the bond's own accrual convention.
  Recommends the next slice — minimal `BondReferenceData` schema/fixture
  implementation only, still no pricing. No code, fixture, parser, or
  tests were added. No frozen BLI v1.3 source spec file was edited. Issue
  #38 remains open.
- **BLI bond reference data preflight tightened after Codex P2 review,
  docs-only (`docs/20` update).** Three findings fixed: (1)
  `first_coupon_date` / `last_coupon_date` were mis-classified as
  "Optional for MVP" despite Annex B §B.5 listing both as required — now
  **Required**, with non-null strict-ISO-date validation, matching Annex
  B and preventing a future implementation from losing its only signal
  for detecting an irregular first/last coupon period. (2) Added an
  explicit MVP eligibility rule that bonds with an irregular first/last
  coupon period must be marked ineligible for MVP pricing until a future
  cash-flow generation slice supports them — the reference-data schema
  still does not calculate schedules or add a calendar engine; it only
  prevents a stub bond from silently entering the MVP pricing pool,
  either via irregularity detection or by limiting the MVP fixture to
  regular-coupon, no-stub bonds by construction. (3) `coupon` validation
  now requires `coupon >= 0` (negative coupons rejected outright);
  `coupon == 0` remains valid reference data, but zero-coupon MVP-pricing
  eligibility must be an explicit decision recorded by the implementation
  slice, not left ambiguous. Propagated through the field table, the
  eligibility section, the validation checklist, the deferred-items
  section, the next-slice recommendation, and the acceptance checklist.
- **`BondReferenceData` schema implemented — BLI MVP Slice B
  (`src/shiori_pricing_lab/reference_data/`,
  `tests/test_bond_reference_data.py`).** Following `docs/20`, adds a new
  top-level package, **sibling to `products`, not part of it** — the
  explicit package decision `docs/20` §7/§11/§12 left open, made because
  Bond Reference Data describes the underlying bond's own static terms
  (what the issuer promised), not a traded deal's terms and not market
  data. No existing product schema (`BondOption`, `DepositLeg`,
  `BondLinkedStructuredProduct`) is modified, and nothing is exported from
  `products/__init__.py`.
  - `BondReferenceData` carries every required field from Annex B §B.5 /
    `docs/20` §4 (`isin`, `issuer`, `currency`, `coupon`,
    `coupon_frequency`, `maturity_date`, `issue_date`, `day_count`,
    `business_day_convention`, `redemption_amount`, `callable_flag`,
    `sinkable_flag`, `bond_type`, `yield_convention`, `ex_dividend_days`,
    `first_coupon_date`, `last_coupon_date`, `status`), reusing the
    existing `Currency` / `Frequency` / `DayCount` /
    `BusinessDayConvention` / `BondYieldConvention` enums — no new members
    added to any of them.
  - Two new controlled-vocabulary enums resolve the open items `docs/20`
    §4/§11 left to this slice: `BondType` (`FIXED_COUPON_BULLET`,
    `FLOATING_RATE_NOTE`, `AMORTIZING`, `CONVERTIBLE`,
    `INFLATION_LINKED`, `PERPETUAL`, `STRUCTURED_NOTE`) and `BondStatus`
    (`ACTIVE`, `INACTIVE`).
  - `coupon >= 0` is enforced at construction (negative rejected);
    `coupon == 0` constructs successfully as valid reference data.
    `first_coupon_date` / `last_coupon_date` are required constructor
    arguments (omitting either raises `TypeError`) and must be non-null,
    strict `YYYY-MM-DD` dates.
  - **MVP pricing eligibility is a separate function**,
    `reference_data.eligibility.is_mvp_pricing_eligible`, deliberately not
    part of `__post_init__` — a callable, sinkable, zero-coupon, or
    `OTHER`-yield-convention bond all construct successfully as reference
    data but are marked ineligible with an explicit reason. This resolves
    three open decisions explicitly: (1) floating-rate/amortizing/
    convertible/inflation-linked/perpetual/structured-note exclusion uses
    `bond_type` as the signal (only `FIXED_COUPON_BULLET` is eligible);
    (2) zero-coupon bonds are **valid-but-ineligible** for this
    implementation slice (the stricter of `docs/20` §5's two allowed
    choices); (3) irregular first/last coupon stub detection is **not**
    implemented (no schedule engine exists, and a wrong heuristic would be
    worse than none) — instead the small, manually reviewed synthetic
    fixture (`reference_data/fixtures.py`, four bonds: one eligible
    plain-vanilla bullet, one zero-coupon, one callable, one floating-rate
    note) is limited to regular-coupon, no-stub bonds by construction, and
    a test documents that limitation.
  - No lookup-by-ISIN helper was added (`docs/20` §11 explicitly defers
    designing a lookup/resolution mechanism to a future pricing-engine
    slice). No pricing, cash-flow generation, schedule engine, QuantLib,
    `MarketDataSnapshot`, MVP input bundle, file parser, ingestion, or
    Bloomberg/API connector was added. Issue #38 is unaffected and
    **remains open**.
  - **Review / validation:** `python -m pytest -q` → 455 passed;
    `python -m ruff check src/shiori_pricing_lab tests` → clean for every
    file this slice touches (2 pre-existing `E501` findings in
    `products/bond_option.py`, untouched by this slice, remain).
  No code, fixture, or tests were added.
- **`BondReferenceData` / `is_mvp_pricing_eligible` fixed after Codex P2
  review of PR #58.** Three gaps closed, no scope added: (1)
  `is_mvp_pricing_eligible` did not check `status`, so a `BondStatus.
  INACTIVE` bond could be marked MVP-pricing-eligible — it now adds an
  explicit "status INACTIVE is not MVP-pricing-eligible" reason, matching
  the existing valid-but-ineligible pattern used for callable/sinkable/
  zero-coupon/non-vanilla bonds. (2) `BondReferenceData` only checked
  each coupon date's format, not its ordering, so impossible static
  records could construct (`first_coupon_date` on/before `issue_date`,
  `last_coupon_date` after `maturity_date`, or `first_coupon_date` after
  `last_coupon_date`) — three new checks enforce
  `issue_date < first_coupon_date <= last_coupon_date <= maturity_date`.
  This is explicitly **not** schedule generation, stub detection, or
  business-day rolling — only a static-date-order sanity check; the
  zero-coupon fixture bond (`first_coupon_date == last_coupon_date ==
  maturity_date`) still satisfies the invariant and still constructs. (3)
  Because those two gaps are fixed, an impossible or inactive record can
  no longer be reported eligible. Six new tests cover both fixes. **No
  pricing, schedule engine, product-schema change, or lookup mechanism
  was added; Issue #38 remains open.**
  - **Review / validation:** `python -m pytest -q` → 461 passed;
    `python -m ruff check src/shiori_pricing_lab tests` → the 2
    pre-existing `E501` findings in `products/bond_option.py` (untouched)
    remain the only findings.
- **BLI ISIN resolution preflight written, docs-only
  (`docs/21_bli_isin_resolution_preflight.md`).** Defines the boundary
  for the next BLI MVP slice: resolving `BondOption.underlying_isin`
  against `BondReferenceData` — the lookup/resolution mechanism `docs/20`
  §11 explicitly deferred. States that product schemas must not embed
  `BondReferenceData` and that `BondReferenceData` stays reference data,
  not product or market data (both restated, not re-opened). Fixes the
  MVP resolution source at `SYNTHETIC_BOND_FIXTURES` only — no Bloomberg/
  API connector, file parser, database, generic ingestion, or
  screenshot/OCR capture. Defines exact-match-only lookup behavior for
  every required case (exact match, missing ISIN, duplicate ISIN in the
  fixture, inactive bond, valid-but-ineligible bond, unsupported
  `bond_type`, callable/sinkable, zero-coupon, `yield_convention ==
  OTHER`) — critically, a resolver must call the existing
  `is_mvp_pricing_eligible` once per found record rather than
  re-implementing any eligibility rule itself, and a duplicate ISIN is a
  fixture data-integrity error that must fail explicitly, never resolved
  by "return the first match." Restates and details the blocking rule
  (`docs/20` §8): a missing or ineligible bond must block, with no
  guessing, no fallback bond, no silent downgrade, no partial pricing,
  and no fuzzy ISIN matching. Separates resolution from both pricing (the
  resolver only answers found/not-found, the record, eligible/ineligible,
  and the blocking reason — never PV/DV01/cashflows/a schedule) and
  market data (no `business_date`, `valuation_date`, resolved rate, or
  any of `docs/20` §3's exclusion-list fields on a resolution result).
  Sketches, non-bindingly, a `resolve_bond_reference_data(underlying_isin,
  fixtures)` function and a conceptual result shape (requested ISIN,
  resolution status, matched record, eligibility reasons, block reason,
  source fixture name — no market-data field) as the smallest next coding
  slice. **No resolver code, pricing, payoff skeleton, cash-flow
  generation, schedule engine, `MarketDataSnapshot`, MVP input bundle,
  Treasury FTP parser, ingestion, Bloomberg/API connector, QuantLib, UI,
  screenshot capture, or product-schema change was added.** No frozen BLI
  v1.3 source spec file was edited. Issue #38 remains open.
  - **Review / validation:** Documentation-only change; no source file
    changed, so `python -m pytest -q` and `ruff` are unaffected by this
    PR (last known state: 461 passed, only the 2 pre-existing
    `products/bond_option.py` `E501` findings).
- **ISIN resolver implemented — BLI resolution slice
  (`src/shiori_pricing_lab/reference_data/resolution.py`,
  `tests/test_bond_reference_resolution.py`).** Implements the minimal
  resolver `docs/21` §8 recommended: `resolve_bond_reference_data(
  underlying_isin, fixtures=SYNTHETIC_BOND_FIXTURES,
  *, source_fixture_name=...)` does an exact-ISIN-string-match scan
  (no fuzzy/partial/case-insensitive matching) and returns a frozen
  `BondReferenceResolutionResult` (`requested_isin`, `status`,
  `bond_reference_data`, `eligibility_reasons`, `block_reason`,
  `source_fixture_name`) with a three-value `BondResolutionStatus`
  (`FOUND_ELIGIBLE` / `FOUND_INELIGIBLE` / `NOT_FOUND`). A single match
  calls the existing `is_mvp_pricing_eligible` **once** — the resolver
  does not re-implement any callable/sinkable/zero-coupon/`OTHER`/
  `bond_type`/inactive-status rule — and preserves every eligibility
  reason (joined into `block_reason` when ineligible, never collapsed to
  the first one). A missing ISIN returns `NOT_FOUND` (never raises); more
  than one record sharing the requested `isin` raises a new local
  `DuplicateBondReferenceDataError` (a fixture data-integrity bug, not
  resolved by picking the first or last match) — defined locally in
  `resolution.py` rather than imported from `pricing/errors.py`, keeping
  `reference_data` independent of the `pricing` package. No
  `business_date`, `valuation_date`, `as_of_timestamp`, or other
  market-data field exists anywhere in the resolver's inputs or result
  (`docs/21` §7); the resolver never chooses "the latest" reference data
  and never reasons about a valuation date (`docs/21` §7.1) — `fixtures`
  is a plain caller-supplied parameter. `BondOption`, `DepositLeg`, and
  `BondLinkedStructuredProduct` are unmodified, and the resolver is not
  wired into any pricing engine by this slice. **No pricing, payoff
  skeleton, cash-flow generation, schedule engine, `MarketDataSnapshot`,
  MVP input bundle, Treasury FTP parser, ingestion, Bloomberg/API
  connector, QuantLib, UI, screenshot capture, or product-schema change
  was added.** Issue #38 remains open.
  - **Review / validation:** `python -m pytest -q` → 482 passed (461
    previous + 21 new); `python -m ruff check src/shiori_pricing_lab
    tests` → the 2 pre-existing `E501` findings in
    `products/bond_option.py` (untouched) remain the only findings; the
    new resolver module and test file are clean.
- **ISIN resolver fixed after Codex review of PR #60 (2 P2, 1 P3).**
  (1) `source_fixture_name` no longer defaults to the literal
  `"SYNTHETIC_BOND_FIXTURES"` string regardless of what `fixtures` was
  passed — it now defaults to `None`, and a new `_resolve_source_fixture_name`
  helper resolves it to `"SYNTHETIC_BOND_FIXTURES"` only when `fixtures`
  is genuinely (by identity) the module-level default, or to the generic
  `"caller_supplied_fixtures"` for any other unlabeled iterable, so a
  caller resolving against a custom or future point-in-time-versioned
  source is never mislabeled; the resolved label is used consistently in
  the result, the not-found `block_reason`, and the duplicate-ISIN error
  message. (2) `BondReferenceResolutionResult`, a public directly
  constructible type, now validates its own invariant in
  `__post_init__` so a hand-built result can never contradict
  `status`/`eligibility_reasons` (e.g. `FOUND_INELIGIBLE` with a
  `block_reason` that silently drops a reason, or a status/
  `bond_reference_data`/`eligibility_reasons` combination that cannot
  occur from `resolve_bond_reference_data` itself) — raises `ValueError`
  on a bad direct construction; results the resolver itself builds are
  unaffected. (3) `reference_data/__init__.py`'s docstring no longer
  says ISIN resolution is deferred — it now says the resolver lives in
  this package while pricing-engine wiring remains future work. 11 new
  tests added. **No pricing, product-schema change, or new architecture
  was added; scope stays the minimal resolver.** Issue #38 remains open.
  - **Review / validation:** `python -m pytest -q` → 493 passed (482
    previous + 11 new); `python -m ruff check src/shiori_pricing_lab
    tests` → the same 2 pre-existing `E501` findings in
    `products/bond_option.py` remain the only findings; the modified
    resolver module and test file are clean.
- **BLI market data / MVP input bundle preflight written, docs-only
  (`docs/22_bli_market_data_input_bundle_preflight.md`).** Defines the
  boundary for the next BLI MVP slices: a BLI-scoped `MarketDataSnapshot`
  and the MVP input bundle a future pricing engine will consume, built
  from one `BondLinkedStructuredProduct` + resolved `BondReferenceData` +
  resolver/eligibility status + one point-in-time market snapshot +
  explicit curve mappings + explicit assumptions/validation results.
  States the four-layer boundary (product terms / reference data /
  market data / input bundle) and restates, without changing, the
  existing product-schema and reference-data exclusion lists (`docs/15`,
  `docs/18` §8, `docs/19` §9, `docs/20` §3) plus a new market-data
  exclusion: market data must never rewrite a bond's static terms
  (coupon, maturity, first/last coupon date, bond type, callable/
  sinkable flags) or a product's deal terms (notional, strike,
  settlement rules) — any mismatch is a blocking validation error, never
  a silent overwrite. Grounds the required market-data field lists in
  the frozen `ANNEX_B_v1.3.md` §B.1 (Bond Price/Yield File) and §B.2
  (Yield Curve File), and restates the frozen `SPEC_v1.3.md` §3.5/§7.3
  curve-purpose rules verbatim: Option Discount Curve and Bond Reference
  Curve must never be mixed; the deposit leg must not silently reuse the
  Option Discount Curve unless an explicit mapping rule says so; missing
  curve mapping or invalid curve data blocks pricing. Lists ten
  conceptual bundle-validation gates (product valid, wrapper currency
  consistent, resolver status `FOUND_ELIGIBLE`, market snapshot present
  and coherent, bond price/yield available for the exact resolved ISIN,
  curve mapping available per purpose, deposit/FTP rate or manual-rate
  audit present, quote side explicit, source/status acceptable, no
  stale/inactive data) — any single failed gate blocks bundle creation
  entirely, no partial-bundle concept. Extends the `docs/21` §7.1
  point-in-time boundary (the Codex P2 fix from PR #59) one layer up:
  market data and reference data must share a coherent valuation
  context, and a future bundle builder must not mix "latest" reference
  data with historical market data (no look-ahead bias). Restates the
  Treasury FTP percent-vs-decimal rule and quote-side policy (`docs/18`
  §2.2/§2.4/§5) without changing them. Sketches conceptual error/audit
  categories (mapping some onto the existing `PricingErrorCode.
  MISSING_REFERENCE_DATA`/`MISSING_MARKET_DATA` members, leaving open
  whether more granular codes are needed) and recommends five future
  implementation slices (`MarketDataSnapshot` preflight → minimal
  dataclass with a synthetic fixture → MVP input bundle → bundle builder
  → pricing engine skeleton). **No `MarketDataSnapshot`, MVP input
  bundle, bundle builder, pricing engine, payoff skeleton, cash-flow
  generation, schedule engine, yield-to-price calculation, curve
  interpolation, Treasury FTP parser, ingestion, Bloomberg/API
  connector, QuantLib adapter, UI, or screenshot capture was added.**
  `BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
  `BondReferenceData`, and `resolve_bond_reference_data` are all
  unmodified. No frozen BLI v1.3 source spec file was edited. Issue #38
  remains open.
  - **Review / validation:** Documentation-only change; no source file
    changed, so `python -m pytest -q` and `ruff` are unaffected by this
    PR (last known state: 493 passed, only the 2 pre-existing
    `products/bond_option.py` `E501` findings).
- **`docs/22` fixed after Codex P2 review of PR #61: added missing
  option-volatility and credit-spread input categories.** The original
  version's required market-data list (bond price/yield, yield curves,
  deposit/FTP rate, manual-rate audit input) omitted two pricing inputs
  the frozen spec already requires (SPEC §§3.2/3.3/7.4 for volatility,
  §7.5 for credit spread) — a gap that could let a future bundle builder
  pass without either input, forcing a later pricing skeleton to either
  fail on a "valid" bundle or fabricate a fallback. Added new §6.5
  (Option volatility input) and §6.6 (Credit spread / spread
  adjustment), restating the frozen spec's own rules verbatim: no silent
  vol fallback to flat vol (SPEC §3.3), no silent zero-spread default
  (SPEC §7.5), any override/fallback must be an audited assumption, and
  bundle construction must block if either is required and missing.
  Propagated through §3's conceptual field list, §5.1's
  must-not-construct conditions, §10's validation gates (renumbered to
  12, adding explicit vol/spread gates and a no-silent-fallback gate),
  §11's error/audit categories (missing volatility input, ambiguous
  volatility basis, missing credit spread, ambiguous credit spread
  treatment, unauthorized silent fallback/default), §12's recommended
  sequence, §13's prior-docs relationships, and §14's deferred items.
  **Still docs-only: no `MarketDataSnapshot`, MVP input bundle, bundle
  builder, pricing engine, volatility surface, or credit-spread model
  was added; `BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
  `BondReferenceData`, and `resolve_bond_reference_data` remain
  unmodified.** Issue #38 remains open.
  - **Review / validation:** Documentation-only change; `python -m
    pytest -q` → 493 passed (unchanged); `ruff` → the same 2
    pre-existing `products/bond_option.py` `E501` findings remain the
    only findings.
- **BLI `MarketDataSnapshot` schema preflight written, docs-only
  (`docs/23_bli_market_data_snapshot_schema_preflight.md`).** Narrows
  `docs/22`'s conceptual boundary into an implementable schema shape for
  the next PR. **Recommends a new module,
  `src/shiori_pricing_lab/data/bli_snapshot.py`, inside the existing
  `data/` package** (not a new top-level package, and not fields added
  to the existing vanilla-rates-core `MarketDataSnapshot` in
  `data/snapshot.py`, whose DataFrame-of-rates-points shape is
  structurally unrelated) — reasoning: `AGENTS.md` rule 2 already
  designates `data/` as market data's home, so a new sibling package
  would fragment that rule the way `reference_data/` correctly did NOT
  need to when it split off `products/` for a genuinely non-market-data
  concept. **Recommends a distinct class name, `BLIMarketDataSnapshot`**,
  to avoid import confusion with the existing `MarketDataSnapshot`.
  Breaks `docs/22` §3/§6's field list into six proposed groups
  (snapshot-level; bond quote; curves; deposit/FTP; volatility; credit
  spread), recommending `ftp_rate_percent_value` +
  `ftp_rate_decimal_value` as two explicit fields (matching `docs/18`
  §2.1's own recommendation) rather than one ambiguous rate field.
  Restates the curve-purpose-separation, volatility, and credit-spread
  rules from `docs/22` §6.5/§6.6/§7 as concrete field-level
  consequences (e.g. "a non-blank override value with a blank audit
  field must be rejected"). Proposes a minimal five-value status
  vocabulary (`ACTIVE`/`STALE`/`INVALID`/`MISSING`/`MANUAL_VERIFIED`),
  explicitly not finalized. Scopes a minimal positive synthetic-fixture
  shape (one valuation date, one resolved eligible ISIN, one bond quote,
  one Bond Reference Curve, one Option Discount Curve, one Deposit
  Curve/FTP observation, one volatility input, one credit-spread
  treatment) plus negative-fixture concepts for future tests. Lists a
  validation-rules checklist (no system date, no duplicate curve purpose
  without explicit handling, exact-ISIN match with the resolver's
  result, explicit FTP percent/decimal consistency, no silent
  override/fallback without an audit field) mirroring `docs/18`
  §10/`docs/20` §10's acceptance-criteria style. Restates that this
  snapshot is not the MVP input bundle (`docs/22` §5) and does not
  design the bundle in detail. **No `BLIMarketDataSnapshot` class, MVP
  input bundle, bundle builder, pricing engine, payoff skeleton,
  cash-flow generation, schedule engine, yield-to-price calculation,
  curve interpolation, volatility surface, credit spread model, Treasury
  FTP parser, ingestion, Bloomberg/API connector, QuantLib adapter, UI,
  or screenshot/OCR capture was added.** `BondOption`, `DepositLeg`,
  `BondLinkedStructuredProduct`, `BondReferenceData`, and
  `resolve_bond_reference_data` are all unmodified. No frozen BLI v1.3
  source spec file was edited. Issue #38 remains open.
  - **Review / validation:** Documentation-only change; no source file
    changed, so `python -m pytest -q` and `ruff` are unaffected by this
    PR (last known state: 493 passed, only the 2 pre-existing
    `products/bond_option.py` `E501` findings).
- **`docs/23` fixed after Codex P2 review of PR #62.** Two findings:
  (1) the §12 validation checklist's "no duplicate curve purpose"
  rule was too broad — Annex B §B.2 models a curve as multiple tenor
  rows sharing the same `currency` + `curve_purpose` (e.g. 1Y/2Y/5Y/10Y
  under one Option Discount Curve), so repeated `currency` +
  `curve_purpose` values are expected, not duplicates. Corrected to key
  duplicate detection at the curve-node level (valuation context +
  curve identity + currency + curve_purpose + tenor), rejecting a
  duplicate tenor row within one curve identity, conflicting rates for
  the same node, or an ambiguous unmapped multiple-curve-ID case — never
  resolved by picking the first/last row. (2) The §11.1 positive
  synthetic fixture said "one Deposit Curve or FTP observation," treating
  the two as substitutes; they are not — `docs/22` already separates
  the Deposit Curve (a discounting input) from the deposit-rate input
  (fixed rate / FTP observation / manual-verified-rate audit, per
  `docs/18` §4's three modes). Corrected to require the Deposit Curve
  unconditionally, plus a separate deposit-rate input matching whichever
  `deposit_rate_mode` the synthetic `DepositLeg` fixture uses. **Still
  docs-only: no class, fixture, or code of any kind was added.** Issue
  #38 remains open.
  - **Review / validation:** Documentation-only change; `python -m
    pytest -q` → 493 passed (unchanged); `ruff` → the same 2
    pre-existing `products/bond_option.py` `E501` findings remain the
    only findings.
- **`BLIMarketDataSnapshot` schema and synthetic fixture landed
  (`docs/23` implementation slice).** Added
  `src/shiori_pricing_lab/data/bli_snapshot.py`: frozen dataclasses
  `BLIBondQuote`, `BLICurvePoint`, `BLIDepositRateObservation`,
  `BLIVolatilityInput`, `BLICreditSpreadInput`, and
  `BLIMarketDataSnapshot`, plus the controlled vocabularies
  `BLIMarketDataStatus` (`ACTIVE`/`STALE`/`INVALID`/`MISSING`/
  `MANUAL_VERIFIED`), `BLICurvePurpose`, `BLIVolatilityBasis`, and
  `BLICreditSpreadTreatment`. Reuses the existing `PayoffBasis` (bond
  quote price/yield), `TreasuryFTPQuoteSide`, and `TreasuryFTPTenor`
  enums from `products.enums` rather than inventing parallel
  vocabulary. Added `src/shiori_pricing_lab/data/_validation.py`
  (duplicated small helpers, matching the existing
  `products`/`reference_data` sibling-package convention) and
  `src/shiori_pricing_lab/data/bli_snapshot_fixtures.py`
  (`SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`) — one valuation date, one bond
  quote for the existing eligible `XS0000000001` fixture ISIN
  (`reference_data.fixtures.SYNTHETIC_BOND_FIXTURES`), one Bond
  Reference Curve and one Option Discount Curve (each with two tenor
  rows), one Deposit Curve, one separate `TREASURY_FTP_REFERENCE`-style
  deposit-rate observation, one explicit volatility input, and one
  explicit (`OBSERVED`) credit-spread treatment. Also added
  `require_exact_isin_match(snapshot, expected_isin)`, a small helper
  for exact (never fuzzy/prefix) ISIN comparison against a future
  resolver result.
  - **Validation implemented:** frozen dataclasses throughout; no
    `date.today()`/`datetime.now()` anywhere (`valuation_date` is
    parsed only for `YYYY-MM-DD` format validation); required string
    fields reject blank/whitespace; numeric fields reject
    NaN/infinity via a shared finite-number check; `clean_price_per_100`
    / `yield_value` on `BLIBondQuote` follow the same "exactly one,
    matching the explicit discriminator" pattern as
    `BondOption.strike_price`/`strike_yield`; FTP
    `ftp_rate_percent_value` / `ftp_rate_decimal_value` must agree
    within a small tolerance (`3.5500` ⇔ `0.0355`), rejecting an
    inconsistent pair; curve duplicate/conflict detection is keyed at
    the curve-node level (`curve_id` + `tenor`) so multi-tenor curves
    sharing one `currency` + `curve_purpose` pass, while a duplicate or
    conflicting tenor row, or an ambiguous set of different `curve_id`s
    claiming the same `currency` + `curve_purpose`, is rejected;
    `BLIVolatilityInput.override_or_fallback_audit` and
    `BLICreditSpreadInput.override_or_fallback_audit` must be non-blank
    whenever populated, and credit-spread `EMBEDDED`/`NOT_REQUIRED`
    treatments require a non-blank audit explanation (never a silent
    zero/default).
  - **Explicit non-goals (unchanged from `docs/23` §17):** no MVP input
    bundle, bundle builder, pricing engine, payoff skeleton, cash-flow
    generation, schedule engine, yield-to-price calculation, curve
    interpolation, volatility surface, credit spread model, Treasury
    FTP parser, ingestion, Bloomberg/API connector, QuantLib adapter,
    or UI was added. `BondOption`, `DepositLeg`,
    `BondLinkedStructuredProduct`, `BondReferenceData`, and
    `resolve_bond_reference_data` remain unmodified. **Issue #38
    remains open.**
  - **Review / validation:** `python -m pytest -q` → 559 passed
    (493 prior + 66 new in `tests/test_bli_market_data_snapshot.py`);
    `ruff check` on the new/changed files → clean (the 2 pre-existing
    `products/bond_option.py` `E501` findings are unrelated and
    untouched by this PR).
- **`BLIMarketDataSnapshot` fixed after Codex P2/P3 review of PR #63.**
  Three findings, all in `src/shiori_pricing_lab/data/bli_snapshot.py`:
  (1) **P2 — `BLIBondQuote` wrongly required exactly one of
  `clean_price_per_100`/`yield_value`.** `docs/23` §4.2 describes the
  field as "clean_price_per_100 and/or yield", and a real bond
  price/yield feed may validly report both for the same observation;
  the old "exactly one, matching `price_type`" rule would have
  discarded an observed value. Fixed: at least one of the two is now
  required, both may be present, each is validated independently when
  present (`clean_price_per_100` finite and positive, `yield_value`
  finite, signed allowed), and `price_type` no longer gates which
  field may be populated — it only records which basis was primarily
  reported. No yield-to-price or price-to-yield conversion is
  performed anywhere; the snapshot still only preserves what was
  observed. (2) **P2 — `STALE`/`INVALID`/`MISSING` statuses
  constructed successfully.** `docs/23` §12 expects stale/invalid data
  not to be accepted at construction absent an explicit policy; the
  original implementation only coerced the enum and never gated on it,
  so a frozen snapshot could carry a stale/invalid/missing nested
  observation that a future bundle layer might wrongly trust. Fixed: a
  shared `_require_active_status` check now runs in every dataclass's
  `__post_init__` (the snapshot and all five nested observation types)
  and rejects anything other than `ACTIVE`. `MANUAL_VERIFIED` is also
  rejected for now, with its own distinct error message, because the
  audit policy (docs/23 §10) that would make it acceptable is not
  implemented in this slice — accepting it would require its own
  reviewed audit-metadata design, which is out of scope here. The
  `BLIMarketDataStatus` enum itself is unchanged (still five members);
  only construction-time acceptance is narrowed. (3) **P3 —
  `BLIBondQuote.price_type` was typed as `products.enums.PayoffBasis`.**
  `PayoffBasis` documents a bond *option's payoff* basis (a product/
  methodology concept), not a market-data quote's basis; reusing it
  coupled this market-data schema to an unrelated product enum. Fixed:
  added a small, `data`-package-local `BLIQuoteBasis` enum
  (`PRICE`/`YIELD`) and switched `BLIBondQuote.price_type` to it;
  `products.enums.PayoffBasis` is untouched. Updated the synthetic
  fixture and all affected tests accordingly (30 new tests added,
  covering price-only/yield-only/both/neither for the bond quote, and
  `STALE`/`INVALID`/`MISSING`/`MANUAL_VERIFIED` rejection for the
  snapshot and every nested observation type). **No MVP input bundle,
  bundle builder, pricing engine, or any other out-of-scope surface was
  touched.** `BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
  `BondReferenceData`, and `resolve_bond_reference_data` remain
  unmodified. Issue #38 remains open.
  - **Review / validation:** `python -m pytest -q` → 589 passed (559
    prior + 30 net new in `tests/test_bli_market_data_snapshot.py`);
    `ruff check src/shiori_pricing_lab tests` → only the same 2
    pre-existing, unrelated `products/bond_option.py` `E501` findings
    remain.
- **BLI MVP input bundle preflight written, docs-only
  (`docs/24_bli_mvp_input_bundle_preflight.md`).** The concrete
  follow-up to `docs/22` §12 step 3, now re-derived against the actual
  implemented `BLIMarketDataSnapshot` classes (PR #63) instead of
  `docs/22`'s pre-implementation placeholder field list. Defines the
  MVP input bundle as a deterministic, immutable valuation context for
  exactly one `BondLinkedStructuredProduct` binding: the product,
  a resolved `BondReferenceData` (via `resolve_bond_reference_data`),
  and a `BLIMarketDataSnapshot` — with **cross-checks only** (ISIN
  identity across all three; `FOUND_ELIGIBLE` resolution status;
  valuation-date coherence), since each input is already
  independently valid at its own construction. **Recommends a new
  class, `BLIMVPInputBundle`, inside a new module,
  `src/shiori_pricing_lab/data/bli_mvp_input_bundle.py`** (same
  `data/`-package-location reasoning `docs/23` §3.3 used for the
  snapshot). Sketches a tentative field list (bundle-level
  `valuation_date`, references to the product/reference-data/snapshot
  objects, the resolver's status/eligibility audit trail — no
  duplicated field from any of the three, no pv/dv01/cashflows).
  **Found and recorded, but did not fix (docs-only slice), a concrete
  fixture gap:** `tests/test_bond_linked_structured_product.py`'s
  inline `BondOption` helper uses ISIN `"US912828ZZ11"`, which does not
  match the `"XS0000000001"` ISIN both `SYNTHETIC_BOND_FIXTURES` and
  `SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT` already use — flagged in §11 as
  the first thing the next implementation slice must add (new fixture
  content, not a schema change). Lists a validation-rules checklist
  (§6), a data-flow diagram (§5), a fixture plan (§8), a test plan (§9),
  and an "open questions / implementation risks" section (§11) covering
  curve-mapping selection, reference-data valuation-date coherence, and
  the bundle's raise-vs-structured-result error shape — none resolved,
  all left for the implementation slice. **No `BLIMVPInputBundle` class,
  bundle builder, pricing engine, payoff skeleton, cash-flow generation,
  schedule engine, yield-to-price calculation, curve interpolation,
  volatility surface, credit spread model, Treasury FTP parser,
  ingestion, Bloomberg/API connector, QuantLib adapter, or UI was
  added.** `BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
  `BondReferenceData`, `resolve_bond_reference_data`,
  `is_mvp_pricing_eligible`, and `BLIMarketDataSnapshot` are all
  unmodified. No frozen BLI v1.3 source spec file was edited. Package
  exports are unchanged. Issue #38 remains open.
  - **Review / validation:** Documentation-only change; no source or
    test file changed, so `python -m pytest -q` and `ruff` are
    unaffected by this PR (last known state: 589 passed, only the 2
    pre-existing `products/bond_option.py` `E501` findings).
- **PR #64 revised after Codex P2 review — three findings, all in
  `docs/24_bli_mvp_input_bundle_preflight.md`, strengthening the
  bundle's future validation requirements (still docs-only, no
  implementation added).** (1) The "market data snapshot must be
  internally valid" bullet in §6 read as if an `isinstance` check on
  `BLIMarketDataSnapshot` were sufficient; `BLIMarketDataSnapshot.
  __post_init__` only proves internal well-formedness and has no
  concept of which product it is for, so a `TREASURY_FTP_REFERENCE`-
  mode `DepositLeg` could have been bundled with a snapshot carrying no
  matching `deposit_rate_observation`. Fixed: §6 now requires explicit
  product-specific market-data presence gates — a matching deposit-rate
  observation when `deposit_rate_mode` is `TREASURY_FTP_REFERENCE`
  (present, and consistent with `ftp_rate_selector`), with `FIXED_RATE`
  needing no separate rate observation and `MANUAL_VERIFIED_RATE`'s
  audit record flagged as not yet representable (an open item). (2) §6
  also let a snapshot with non-empty but wrong-purpose `curve_points`
  (e.g. only `FUNDING_CURVE`) count as valuation-ready. Fixed: §6 now
  requires an explicit required-MVP-curve-purpose gate — at least one
  `curve_points` row for each of `BLICurvePurpose.BOND_REFERENCE_CURVE`,
  `OPTION_DISCOUNT_CURVE`, and `DEPOSIT_CURVE` (`FUNDING_CURVE` only if
  mapped), presence only, no tenor selection or interpolation. (3) §6's
  valuation-date rule treated `bundle.valuation_date ==
  market_data_snapshot.valuation_date` as sufficient coherence, but
  `BLIMarketDataSnapshot.as_of_timestamp` is only checked for
  non-blankness today, so a historical bundle could carry market data
  whose as-of timestamp is after the valuation date. Fixed: §6 now
  states an explicit market-data as-of / no-look-ahead policy —
  `as_of_timestamp` must also be validated under a no-look-ahead cutoff
  rule, with the exact rule (calendar-date vs. intraday vs.
  settlement-aware) left as a required, explicit policy decision for
  the implementation slice (§11), not something a future pricing engine
  may silently interpret differently. All three fixes remain
  presence/consistency checks only — no curve interpolation, no
  yield/price conversion, no FTP parsing, no pricing, no silent
  fallback; the bundle's deterministic/immutable/by-reference/
  cross-check-only/no-duplicated-fields/no-pricing design and the
  documented (not fixed) fixture-ISIN-mismatch gap are all unchanged.
  §9's test plan and §8.3's negative-fixture list were extended to
  match the three new gates. `docs/09` gained a corresponding summary
  under its BLI MVP input bundle preflight checkpoint.
  - **Review / validation:** Documentation-only change; `python -m
    pytest -q` → 589 passed (unchanged); `ruff check
    src/shiori_pricing_lab tests` → only the same 2 pre-existing,
    unrelated `products/bond_option.py` `E501` findings remain.
- **`BLIMVPInputBundle` dataclass landed (`docs/24` implementation
  slice).** Added `src/shiori_pricing_lab/data/bli_mvp_input_bundle.py`:
  the frozen `BLIMVPInputBundle` dataclass binding one
  `BondLinkedStructuredProduct`, one resolved `BondReferenceData`, and
  one `BLIMarketDataSnapshot` **by reference only**. Fields:
  `bundle_id`, `valuation_date`, `product`,
  `resolved_bond_reference_data` (an explicit naming departure from
  `docs/24` §7's sketched `bond_reference_data`, documented in the
  module docstring), `resolution_status`, `eligibility_reasons` (kept
  as two plain fields rather than the whole
  `BondReferenceResolutionResult` object — another explicit,
  documented departure), and `market_data_snapshot`.
  - **Validation gates implemented (`docs/24` §6):** `isinstance` checks
    on `product`/`resolved_bond_reference_data`/`market_data_snapshot`
    (each already fully self-validates at its own construction, so no
    re-validation is needed); `resolution_status` must be
    `FOUND_ELIGIBLE` with empty `eligibility_reasons`; exact-string-only
    ISIN matching between `product.bond_option.underlying_isin`,
    `resolved_bond_reference_data.isin`, and
    `market_data_snapshot.bond_quote.isin` (reusing the existing
    `require_exact_isin_match` helper for the latter); valuation-date
    equality between the bundle and the snapshot; a **market-data as-of
    / no-look-ahead gate** (`as_of_timestamp` parsed via
    `datetime.fromisoformat`, comparing only the calendar date against
    `valuation_date` — same-date and earlier accepted, after rejected;
    no current-time lookup; documented as a deliberately minimal policy,
    not a final intraday/settlement-aware rule); a
    **product-specific deposit-rate gate**
    (`TREASURY_FTP_REFERENCE` requires a matching, selector-consistent
    `deposit_rate_observation`; `FIXED_RATE` requires none;
    `MANUAL_VERIFIED_RATE` is rejected outright with a clear
    "not supported yet" error); and a **required MVP curve-purpose
    gate** (at least one `curve_points` row for each of
    `BOND_REFERENCE_CURVE`/`OPTION_DISCOUNT_CURVE`/`DEPOSIT_CURVE`,
    presence only, `FUNDING_CURVE` not required). Construction raises
    `ValueError`/`TypeError` on any failed gate, matching every other
    frozen dataclass in this codebase — resolving `docs/24` §11's open
    "raise vs. structured result" question for the dataclass itself.
  - **Fixture gap resolved:** added
    `src/shiori_pricing_lab/products/fixtures.py`
    (`SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT`), a synthetic
    `BondLinkedStructuredProduct` whose `bond_option.underlying_isin` is
    `"XS0000000001"` — matching the ISIN both
    `reference_data.fixtures.SYNTHETIC_BOND_FIXTURES` and
    `data.bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`
    already used, closing the mismatch `docs/24` §8.2/§11 found.
    `tests/test_bond_linked_structured_product.py`'s own inline helper
    (ISIN `"US912828ZZ11"`) is unchanged. Added
    `src/shiori_pricing_lab/data/bli_mvp_input_bundle_fixtures.py`
    (`SYNTHETIC_BLI_MVP_INPUT_BUNDLE`), combining all three fixtures by
    calling `resolve_bond_reference_data` directly at fixture-definition
    time — not a reusable bundle-builder function.
  - **Explicit non-goals (unchanged from `docs/24` §10):** no bundle
    builder / construction helper, pricing engine, payoff skeleton,
    cash-flow generation, schedule engine, yield-to-price calculation,
    curve interpolation, volatility surface, credit spread model,
    Treasury FTP parser, ingestion, Bloomberg/API connector, QuantLib
    adapter, or UI was added. `BondOption`, `DepositLeg`,
    `BondLinkedStructuredProduct`, `BondReferenceData`,
    `resolve_bond_reference_data`, `is_mvp_pricing_eligible`, and
    `BLIMarketDataSnapshot` (and its component classes) remain
    unmodified. Package exports (`products/__init__.py`,
    `reference_data/__init__.py`, `data/__init__.py`) are unchanged —
    the new modules are imported directly from their submodules,
    matching the existing `data/` package convention. **Issue #38
    remains open.**
  - **Review / validation:** `python -m pytest -q` → 624 passed (589
    prior + 35 new in `tests/test_bli_mvp_input_bundle.py`); `ruff check
    src/shiori_pricing_lab tests` → only the same 2 pre-existing,
    unrelated `products/bond_option.py` `E501` findings remain.
- **`BLIMVPInputBundle` fixed after Codex P1/P2 review of PR #65.**
  Three findings, all in `src/shiori_pricing_lab/data/bli_mvp_input_bundle.py`,
  fixed narrowly with no builder/pricing/scope change: (1) **P1 —
  reference-data eligibility was only trusted from the caller-supplied
  `resolution_status`/`eligibility_reasons`, not verified against the
  actual `BondReferenceData`.** A stale or hand-assembled resolver
  result (e.g. `resolution_status=FOUND_ELIGIBLE`, `eligibility_reasons
  =()`) could therefore bundle an actually-ineligible bond (a callable
  fixture bond, in the added test). Fixed: `__post_init__` now also
  calls the existing `is_mvp_pricing_eligible(resolved_bond_reference_
  data)` directly and rejects construction if it disagrees with the
  supplied status — both checks must now agree, neither is trusted
  alone. (2) **P1 — the no-look-ahead gate's
  `datetime.fromisoformat(as_of_timestamp).date()` silently used the
  timestamp's *local* calendar date for timezone-offset-aware inputs**,
  so `"2026-07-01T23:30:00-05:00"` (already `"2026-07-02"` in UTC) could
  incorrectly pass a `valuation_date` of `"2026-07-01"`. Fixed: only a
  bare date, a naive datetime, or a UTC datetime (`utcoffset()` exactly
  zero — `"Z"` or explicit `"+00:00"`) are now accepted; any other
  timezone offset is rejected outright with a clear error, rather than
  silently misread. (3) **P2 — no currency-coherence gate existed**, so
  ISIN identity alone let a caller combine a different-currency
  product/reference-data/market-data trio (e.g. an EUR product against
  a USD-resolved bond and USD market data) and have it accepted as
  valuation-ready. Fixed: added explicit currency-equality checks —
  `product.bond_option.currency` must equal `resolved_bond_reference_
  data.currency`; `market_data_snapshot.bond_quote.currency` must equal
  that same currency; each required MVP curve purpose must have at
  least one `curve_points` row in that currency specifically. No FX
  conversion or cross-currency fallback is implemented or implied — any
  mismatch is a hard rejection. All three fixes preserve the bundle's
  existing scope and behavior unchanged: exact ISIN matching,
  valuation-date equality, the product-specific deposit-rate gate, the
  required MVP curve-purpose gate, `MANUAL_VERIFIED_RATE` rejection, no
  builder, no pricing, no curve interpolation, no yield/price
  conversion, no connector/UI. Added 9 new tests (35 → 44 in
  `tests/test_bli_mvp_input_bundle.py`), covering the ineligible-bond
  override case, UTC-offset/naive/non-UTC-offset timestamp variants,
  and each new currency-coherence gate (plus confirming the existing
  synthetic bundle fixture still passes all of them). Updated the
  module docstring, `docs/09`'s checkpoint, and this entry accordingly.
  - **Review / validation:** `python -m pytest -q` → 633 passed (624
    prior + 9 net new in `tests/test_bli_mvp_input_bundle.py`); `ruff
    check src/shiori_pricing_lab tests` → only the same 2 pre-existing,
    unrelated `products/bond_option.py` `E501` findings remain.
- **`build_bli_mvp_input_bundle` builder landed (`docs/24` §12 step 5
  implementation slice).** Added
  `src/shiori_pricing_lab/data/bli_mvp_input_bundle_builder.py`: a
  keyword-only function (`bundle_id`, `valuation_date`, `product`,
  `bond_reference_data_universe`, `market_data_snapshot`) that extracts
  `product.bond_option.underlying_isin`, calls the existing
  `resolve_bond_reference_data` against the supplied universe, and
  constructs `BLIMVPInputBundle` from the result — the first normal,
  callable construction path into the bundle, replacing what was
  previously hand-wired resolver-call-plus-field-unpacking. Checks
  `product` is a `BondLinkedStructuredProduct` before reading its ISIN
  (clear `TypeError`, not an `AttributeError` from inside the resolver
  call); raises `ValueError` (including the resolver's own status and
  `block_reason`) whenever resolution is not `FOUND_ELIGIBLE` — covering
  both `NOT_FOUND` and `FOUND_INELIGIBLE` — never silently returning
  `None`, never building a partial bundle, never coercing an
  ineligible/missing result into an eligible one, never bypassing
  `is_mvp_pricing_eligible` or any of `BLIMVPInputBundle`'s own gates.
  **Deliberately does not re-validate any `BLIMVPInputBundle` gate**
  (ISIN, currency, valuation-date/as-of no-look-ahead, deposit-rate
  consistency, curve-purpose presence, all from PR #65) — those raise
  directly from `BLIMVPInputBundle.__post_init__`, uncaught and
  un-rewrapped by the builder, resolving `docs/24` §11's "raise vs.
  structured result" open question for the builder the same way PR #65
  resolved it for the dataclass itself (raise, not a structured
  found/not-found object). `DuplicateBondReferenceDataError` likewise
  propagates unchanged.
  - **Fixture updated:**
    `src/shiori_pricing_lab/data/bli_mvp_input_bundle_fixtures.py`'s
    `SYNTHETIC_BLI_MVP_INPUT_BUNDLE` now calls
    `build_bli_mvp_input_bundle` directly instead of hand-wiring
    `resolve_bond_reference_data` + `BLIMVPInputBundle(...)` — no
    circular-import or side-effect risk was found (the builder module
    only imports from `data.bli_mvp_input_bundle`,
    `products.bond_linked_structured_product`, `data.bli_snapshot`, and
    `reference_data`, none of which import back), so the fixture was
    updated in place rather than duplicated. It remains exactly one
    hand-picked positive case, not a general fixture factory.
  - **Explicit non-goals (unchanged from `docs/24` §10):** no pricing
    engine, payoff skeleton, cash-flow generation, schedule engine,
    yield-to-price calculation, curve interpolation, curve *selection*
    methodology beyond the existing bundle gates, volatility surface,
    credit spread model, Treasury FTP parser, ingestion, Bloomberg/API
    connector, QuantLib adapter, debug viewer, or any other UI was
    added. `BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
    `BondReferenceData`, `resolve_bond_reference_data`,
    `is_mvp_pricing_eligible`, `BLIMarketDataSnapshot`, and
    `BLIMVPInputBundle` (and its component classes) remain unmodified.
    Package exports are unchanged. **Issue #38 remains open.**
  - **Review / validation:** `python -m pytest -q` → 651 passed (633
    prior + 18 new in `tests/test_bli_mvp_input_bundle_builder.py`);
    `ruff check src/shiori_pricing_lab tests` → only the same 2
    pre-existing, unrelated `products/bond_option.py` `E501` findings
    remain.
- **BLI pricing engine skeleton preflight written, docs-only
  (`docs/25_bli_pricing_engine_skeleton_preflight.md`).** Checkpoint
  after PR #65 (`BLIMVPInputBundle`) and PR #66
  (`build_bli_mvp_input_bundle`), both merged. The current verified
  construction path is: `product + reference-data universe +
  market-data snapshot → build_bli_mvp_input_bundle → BLIMVPInputBundle`.
  The bundle/dataclass layer now owns every input-readiness gate: resolver
  status / eligibility (re-verified directly via
  `is_mvp_pricing_eligible`, not only trusted from supplied metadata),
  exact ISIN match across product/reference-data/market-data, currency
  coherence, valuation-date equality, the market-data as-of /
  no-look-ahead policy, the Treasury FTP deposit-rate-observation gate,
  and required MVP curve-purpose presence. Scopes (but does not
  implement) the **next** slice — a pricing engine **skeleton** only:
  a future `price_bli_mvp(bundle: BLIMVPInputBundle) -> BLIPricingResult`
  entrypoint in a new `src/shiori_pricing_lab/pricing/
  bli_pricing_engine.py` module, accepting only an already-validated
  `BLIMVPInputBundle` (never a raw product/reference-data/snapshot/ISIN,
  never calling `resolve_bond_reference_data` or
  `build_bli_mvp_input_bundle` itself), returning a deterministic
  "not implemented" result or raising a named not-implemented
  exception — the implementation slice picks one. Flags, as an explicit
  open question for that slice, whether the future BLI engine should
  reuse the existing generic `pricing/result.py`
  (`PricingResult`/`PricingStatus`/`PricingErrorCode`) /
  `pricing/engine.py` (`PricingEngine` Protocol / registry) contract
  already used by the IRS reference engine, or define its own
  `BLIPricingResult`/`BLIPricingStatus` — not decided here. **No pricing
  module, result dataclass, valuation math, payoff logic, curve
  interpolation, yield/price conversion, QuantLib, connector, ingestion,
  or UI was added.** No source or test file was changed. Issue #38
  remains open.
  - **Review / validation:** Documentation-only change; `python -m
    pytest -q` → 651 passed (unchanged); `ruff check
    src/shiori_pricing_lab tests` → only the same 2 pre-existing,
    unrelated `products/bond_option.py` `E501` findings remain.
