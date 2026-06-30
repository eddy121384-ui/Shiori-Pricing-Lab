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
  first engine, this preflight fixes scope (narrow vanilla IRS), required market
  data (one synthetic curve from the snapshot, no providers), the schedule/
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

## Checkpoint summary

- Issues #1 and #2 are closed (PR #18 merged).
- MVP Core (Phase 1, Vanilla Rates Core spine) is complete: the
  `provider → snapshot → context → curve → scenario` flow is wired and tested.
- Issue #12 product-schema scope is **complete**: IRS and OIS (PR #19) plus CCS
  and FX Swap (PR #21) are defined and validated, schema-only.
- Issue #10 first slice is **complete** (PR #23): the deterministic pricing
  engine **contract** exists —
  `Product Definition + ValuationContext + MarketDataSnapshot → price(...) →
  PricingResult`. It is contract-only (no PV / DV01 / cashflows); all products
  currently return `FAILED + UNSUPPORTED_PRODUCT`. Issue #10 remains open.
- Recommended next development step: a design preflight for the **first
  per-product reference engine** (likely the smallest IRS or OIS reference
  pricing slice), not a jump into full valuation. See section 8 of
  `docs/09_mvp_core_runbook.md`.
