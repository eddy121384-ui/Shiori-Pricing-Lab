# 00 Vision — Shiori Pricing Lab

## One-line vision

Shiori Pricing Lab is an AI-native Rates Desk Workbench for trader-owned pricing, valuation, backtesting, charting, and AI-assisted inquiry.

## Why this exists

A rates desk needs tools that can answer pricing and risk questions quickly without turning every workflow into a fragile spreadsheet maze.

The project exists to make pricing logic:

- explicit;
- testable;
- modular;
- historically reproducible;
- readable by AI coding agents;
- usable by a trader during real workflow exploration.

## What this is

This is a private research and workflow platform for a rates trading desk.

It should support, over time:

- IRS;
- CCS;
- FX Swap;
- Swaptions;
- Bond Options;
- Callable Swaps, fixed or floating;
- IR Daily Range Accrual structured products;
- valuation at arbitrary valuation dates;
- historical valuation;
- backtesting;
- TradingView-like charting and visualization;
- AI-assisted inquiry and backtest scripting.

## What this is not

This is not a Bloomberg replacement.

This is not an official booking, risk, valuation, accounting, compliance, or regulatory system.

This is not an execution system.

This is not a platform where LLMs directly invent pricing results.

This is not a place to store credentials, client data, real internal position files, or confidential downloaded market data.

## Core product thesis

The platform should be built around one reusable flow:

```text
Product Definition
+ Valuation Context
+ Market Data Snapshot
+ Model Settings
+ Pricing Engine
= Valuation Result
```

Backtesting is the same flow repeated through historical valuation dates:

```text
for each historical valuation date:
    load market data snapshot
    build valuation context
    value product or strategy
    store valuation / risk / PnL / diagnostics
```

AI is useful only when it sits around this deterministic core:

```text
Natural language request
→ structured pricing or backtest request
→ deterministic engine
→ numeric result
→ AI-assisted explanation
```

## Product development sequence

### Phase 1 — Vanilla Rates Core

Build the shared foundations for IRS, OIS, CCS, and FX Swap.

This includes curves, schedules, day-count conventions, valuation date, fixing lookup, market snapshots, PV, DV01, scenario analysis, and basic UI.

### Phase 2 — Historical Valuation and Backtesting

Make valuation date first-class and reproducible. Add historical snapshots, strategy scripts, result storage, and chartable outputs.

### Phase 3 — Vanilla Options Core

Add Swaptions after the vanilla rates core is stable. Introduce vol surfaces, option conventions, payer / receiver logic, and option scenario risk.

### Phase 4 — Callable and Bond Optionality

Add Bond Options and Callable Swaps only after the option and curve foundations are stable.

### Phase 5 — Structured Product Framework

Add IR Daily Range Accrual and other path-dependent structured products after product definition schemas and historical fixing infrastructure are robust.

## Project survival principle

Build the spine before the monsters.

Do not start with exotic products. Start with the shared infrastructure that every exotic product will later depend on.
