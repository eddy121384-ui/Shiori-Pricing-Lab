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

## Checkpoint summary

- Issues #1 and #2 are closed (PR #18 merged).
- MVP Core (Phase 1, Vanilla Rates Core spine) is complete: the
  `provider → snapshot → context → curve → scenario` flow is wired and tested.
- Issue #12 product-schema scope is **complete**: IRS and OIS (PR #19) plus CCS
  and FX Swap (PR #21) are defined and validated, schema-only.
- Recommended next development step: a design preflight for the **deterministic
  pricing engine interface** —
  `Product Definition + ValuationContext + MarketDataSnapshot → Pricing Result`.
  See section 8 of `docs/09_mvp_core_runbook.md`.
