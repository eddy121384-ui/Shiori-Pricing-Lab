# 09 MVP Core Runbook

A short checkpoint of the Vanilla Rates Core spine after PRs #15, #16, and #17.

This is a runbook, not a design doc. It records what is wired up today, what
must stay true, and where to go next. For design rationale see
`docs/01_system_architecture.md`, `docs/02_data_and_market_snapshots.md`, and
`docs/03_valuation_context.md`.

## 1. Current completed flow

```text
provider → MarketDataSnapshot → ValuationContext → RateCurve → scenario
```

This path is implemented end to end and exercised by tests. The Streamlit
prototype (`src/shiori_pricing_lab/app/streamlit_app.py`) drives exactly this
flow: it loads sample data through a provider, freezes a snapshot for an
explicitly chosen valuation date, builds a context, derives a curve, and applies
a parallel shock.

## 2. What each layer owns

| Layer | Module | Owns | Must not |
| --- | --- | --- | --- |
| Data providers | `src/shiori_pricing_lab/data/providers.py` | Load + normalize raw rows (CSV / manual) into the rates-points schema; validate minimum fields | Price, build curves, render UI, call AI |
| MarketDataSnapshot | `src/shiori_pricing_lab/data/snapshot.py` | Freeze normalized market data for one explicit valuation date; defensive-copy the data; carry `source` / `metadata` | Import the pricing layer; fetch data; know about curves |
| ValuationContext | `src/shiori_pricing_lab/valuation/context.py` | Bind valuation date + snapshot + reporting currency / model settings; enforce date consistency; orchestrate curve building | Use the system date; mutate the snapshot |
| RateCurve | `src/shiori_pricing_lab/pricing/curve.py` | Represent a simple curve (tenor → rate) from snapshot/normalized points; tenor→years mapping; parallel shock | Read CSV / call providers; invent data |
| Scenario shock | `src/shiori_pricing_lab/pricing/scenario.py` | Deterministic parallel curve shock and `change_bp` output | Call providers; depend on the valuation layer |

### Normalized rates-points schema

```text
date, ticker, tenor, value, data_type, source
```

`value` is a decimal rate (4.25% = 0.0425). A parallel shock is in basis points
(+1 bp = +0.0001 in decimal rate terms). See docstrings in
`pricing/curve.py` and `data/providers.py`.

## 3. What must stay true

These are load-bearing invariants. Breaking one is a regression even if tests
pass:

1. **`valuation_date` is always explicit.** Snapshot and context both require it
   and have no default; blank/empty is rejected.
2. **No system date in pricing / valuation.** Never `date.today()` in curve,
   scenario, snapshot, or context code. Historical valuation depends on this.
3. **Providers stay adapters.** All raw data access (CSV/manual today) lives in
   `data/providers.py`. Nothing else reads files.
4. **Pricing / scenario do not call providers directly.** They consume snapshot/
   context-derived inputs. The data layer also must not import the pricing layer.
5. **Synthetic data only in the repo.** No real market data, Bloomberg output,
   positions, or secrets. Sample data is clearly labeled `source=synthetic`.

## 4. How to run tests

Full deterministic suite:

```bash
python -m pytest -q
```

Lint the core spine and tests:

```bash
python -m ruff check src/shiori_pricing_lab tests
```

Tests run without Bloomberg or any external dependency. Relevant test files:

- `tests/test_data_providers.py` — provider loading + schema validation
  (missing columns, empty frame).
- `tests/test_valuation_context.py` — snapshot/context creation, explicit-date
  behavior, defensive copies, layering.
- `tests/test_curve_and_scenario.py` — curve building, +1 bp / +5 bp shocks.
- `tests/test_spine_flow.py` — end-to-end provider → snapshot → context → curve
  → scenario.

## 5. What Issues #1 and #2 achieved

- **Issue #1 — market data normalization + MarketDataSnapshot workflow.** CSV/
  manual providers normalize into the rates-points schema; `MarketDataSnapshot`
  freezes that data for an explicit valuation date with `source`/`metadata`;
  required-field and empty-input validation is in place and directly tested
  (PR #17). Pricing never reads CSV.
- **Issue #2 — RateCurve, ValuationContext, scenario shock flow.** Curves build
  from synthetic snapshot data; `ValuationContext` carries an explicit valuation
  date and snapshot reference (never the system date); the parallel shock is
  deterministic with verified +1 bp / +5 bp behavior; pricing/scenario do not
  call providers.

Net result: the reusable spine from `docs/00_vision.md`
(`Product Definition + Valuation Context + Market Data Snapshot + Pricing
Engine = Valuation Result`) exists for the rates curve case. The Product
Definition piece now has its first slice too (IRS / OIS schemas, PR #19 —
see section 7); the remaining gap is the pricing engine itself.

## 6. What is intentionally NOT done yet

Deliberately out of scope at this checkpoint:

- Bloomberg or any external market-data adapter.
- Database / persistent storage (SQLite / DuckDB / Parquet).
- AI-native inquiry / chat layer.
- IRS / OIS / CCS / FX Swap pricing engines.
- Historical valuation and the backtesting loop.
- Production UI (the Streamlit app is a prototype only).
- Richer snapshot content (FX, vols, fixings, reference data) — rates points only.
- Curve bootstrapping, calendars, day-count conventions.

## 7. Product schema checkpoint (PR #19, Issue #12 first slice)

The Product Definition piece of the spine now exists for vanilla swaps, in
`src/shiori_pricing_lab/products/`. This is **schema only — there is still no
pricing engine.**

| Product | Schema status |
| --- | --- |
| IRS (`InterestRateSwap`) | ✅ Defined and validated |
| OIS (`OvernightIndexedSwap`) | ✅ Defined and validated |
| CCS | ❌ Not started |
| FX Swap | ❌ Not started |

Supporting types: `FixedLeg`, `FloatingLeg`, and the enums `PayReceive`,
`Currency`, `Frequency`, `DayCount`, `BusinessDayConvention`, `FloatingIndex`,
`CompoundingMethod`. Validated by `tests/test_products.py`
(`python -m pytest -q` → 74 passed; `ruff` clean).

What the schema enforces (from Codex review):

- `product_type` is non-overridable (a fixed discriminator), so an IRS always
  serializes as `"IRS"` and an OIS as `"OIS"`.
- Enum-backed fields are coerced/validated at runtime; blanks and unknown
  strings are rejected with clear errors.
- Schedule dates require strict `YYYY-MM-DD`; compact and ISO week-date forms
  are rejected.
- OIS `floating_leg.reset_frequency` may only be `None` or `Frequency.DAILY`.

Load-bearing invariant for this layer (keep it true):

- **Product definitions describe the trade only.** They must not contain market
  data, valuation date, PV, DV01, curves, discount factors, or any pricing
  result. The `products` package imports no data/pricing/valuation module
  (guarded by a test).

## 8. Recommended next development step

**Design preflight for the CCS / FX Swap product schema (still schema only).**

Issue #12 is partially complete: IRS and OIS are done; CCS and FX Swap remain.
Before writing CCS / FX Swap code, do a short design preflight that reuses the
IRS/OIS pattern:

- Confirm which existing enums/legs are reusable and what genuinely new terms
  CCS (two currencies, two notionals, FX exchanges, basis spread) and FX Swap
  (near/far dates and amounts) require — see `docs/04_product_definition_schema.md`.
- Decide how multi-currency notionals and FX exchange flags are represented.
- Keep the same boundaries: **schema only — no pricing engine yet**; no market
  data, valuation date, or pricing results on products; small, explicit,
  additive; tests for construction and validation, synthetic only.

This slots cleanly into the spine: a future pricing engine will take a product
definition plus a `ValuationContext` and return a valuation result, without
changing any of the layers documented above.
