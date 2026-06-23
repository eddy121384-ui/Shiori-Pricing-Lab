# 01 System Architecture

## Architecture goal

The architecture must support simple products first while leaving a clean path toward options, callable products, range accruals, historical valuation, and AI-assisted workflows.

The most important design rule is:

> Pricing engines consume normalized inputs. They do not fetch data, render UI, or call LLMs.

## Top-level layers

```text
External data sources
        ↓
Market Data Adapters
        ↓
Market Data Snapshots
        ↓
Valuation Context
        ↓
Product Definitions + Model Settings
        ↓
Pricing Engines
        ↓
Valuation Results
        ↓
Backtesting / Scenario / UI / AI Explanation
```

## Layer 1 — Market Data Adapters

Responsibilities:

- load raw data from CSV, manual input, Bloomberg, databases, or synthetic examples;
- normalize data into project schemas;
- validate minimum fields;
- isolate provider-specific logic.

Must not:

- price products;
- calculate risk;
- render UI;
- call AI services.

Current package direction:

```text
src/shiori_pricing_lab/data/
```

## Layer 2 — Market Data Snapshots

A market data snapshot is the state of the market for a specific valuation date and data version.

It may include:

- discount curves;
- forecast curves;
- basis curves;
- FX spot and forwards;
- vol surfaces;
- historical fixings;
- bond reference data;
- calendars and holiday sets;
- metadata about data source and timestamp.

Snapshots are required for historical valuation and backtesting.

## Layer 3 — Valuation Context

A valuation context combines valuation date, market snapshot, model settings, and operational assumptions.

It answers:

- what date are we valuing on?
- what market data snapshot is used?
- what curves and vol surfaces are active?
- what fixings are known as of the valuation date?
- what model and convention settings apply?

The valuation date must never be hard-coded to today.

## Layer 4 — Product Definitions

Product definitions describe instruments in machine-readable form.

Examples:

- IRS: legs, fixed rate, floating index, schedule, notional, pay/receive;
- CCS: currencies, notionals, FX reset rules, basis spread, discount curves;
- FX Swap: near/far dates, spot, forward points, currency pair;
- Swaption: underlying swap, expiry, tenor, payer/receiver, strike;
- Callable Swap: underlying swap plus exercise schedule;
- Range Accrual: observation calendar, index, range, coupon formula, accrual rule.

Product definitions must not include live market data. They describe the deal, not the market.

## Layer 5 — Pricing Engines

Pricing engines take product definitions and valuation contexts and return valuation results.

Responsibilities:

- price products;
- calculate risk;
- produce cashflows;
- expose diagnostics;
- keep assumptions visible.

Must not:

- fetch raw data;
- call Bloomberg directly;
- call LLMs;
- render UI;
- write to the trade journal directly.

Current package direction:

```text
src/shiori_pricing_lab/pricing/
```

## Layer 6 — Valuation Results

A valuation result should be structured, not just a number.

It should eventually include:

- PV;
- clean / dirty price where relevant;
- accrued interest where relevant;
- risk measures such as DV01, curve bucket DV01, vega, theta, carry / roll;
- cashflow table;
- scenario output;
- model diagnostics;
- warnings;
- metadata about valuation date and market snapshot.

## Layer 7 — Backtesting and Scenario Layer

Backtesting repeats valuation through time.

Scenario analysis modifies the valuation context or market snapshot and re-runs the same deterministic pricing engine.

This layer must not duplicate product pricing logic.

## Layer 8 — UI Layer

The UI orchestrates workflows and displays results.

It may:

- select products;
- select valuation dates;
- display charts;
- compare scenarios;
- display backtest results;
- annotate trades.

It must not own pricing logic.

## Layer 9 — AI-native Layer

The AI layer sits outside the deterministic pricing core.

It may:

- parse natural language requests into structured pricing requests;
- generate backtest scripts;
- summarize results;
- explain changes in PV, risk, or backtest output;
- suggest checks and diagnostics.

It must not:

- invent pricing results;
- bypass pricing engines;
- fabricate market data;
- send confidential data to external services without explicit approval.

## Current implementation status

The repo currently has early skeletons for:

- CSV/manual market data provider;
- simple curve representation;
- parallel curve shock;
- chart-ready data helpers;
- trade journal schema;
- Streamlit prototype;
- tests.

These are scaffolding, not production pricing engines.

## Design warning

Do not create one separate mini-system per product.

Avoid this pattern:

```text
irs.py
ccs.py
swaption.py
callable_swap.py
range_accrual.py
```

where each file fetches data, builds schedules, prices, formats output, and draws charts independently.

Prefer shared foundations:

```text
market snapshot
valuation context
product definitions
pricing engines
valuation result
backtesting engine
ui adapters
```
