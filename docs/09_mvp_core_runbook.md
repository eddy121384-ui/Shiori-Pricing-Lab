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
Engine = Valuation Result`) exists for the rates curve case, minus the product
definition piece (next step).

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

## 7. Recommended next development step

**Issue #12 — define vanilla rates product schemas (schema only).**

Add machine-readable product definitions for vanilla rates instruments (start
with IRS / OIS, then CCS / FX Swap) as plain data structures: legs, schedule
inputs, notional, fixed rate, floating index, pay/receive, currency.

Constraints for that step:

- **Schema only — no pricing engine yet.** Define and validate the structures;
  do not price them.
- Product definitions describe the deal, not the market: no embedded market
  data, no valuation date.
- Keep it small, explicit, and additive; mirror the existing dataclass style.
- Tests for construction and validation, synthetic only.

This slots cleanly into the spine: a future pricing engine will take a product
definition plus a `ValuationContext` and return a valuation result, without
changing any of the layers documented above.
