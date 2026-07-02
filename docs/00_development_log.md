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
