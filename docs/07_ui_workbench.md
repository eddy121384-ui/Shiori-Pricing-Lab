# 07 UI Workbench

## Purpose

The UI workbench should help a rates trader inspect products, market data, valuation results, scenarios, backtests, charts, and AI explanations without hiding pricing assumptions.

The UI is not the pricing engine.

## Design goal

The long-term UI should feel closer to a trading workbench than a generic dashboard.

It should support:

- valuation date selection;
- product selection and editing;
- market snapshot selection;
- curve and vol visualization;
- scenario controls;
- valuation result tables;
- risk breakdown;
- backtest charts;
- trade journal overlays;
- AI-assisted inquiry.

## Suggested information architecture

```text
Left navigation
    Products
    Market Data
    Valuation
    Scenarios
    Backtesting
    Journal
    AI Inquiry

Main workspace
    charts
    tables
    controls
    diagnostics

Right panel
    scenario controls
    model settings
    warnings
    AI explanation
```

## Core screens

### 1. Valuation screen

Purpose: price a selected product under a selected valuation context.

Components:

- product selector;
- valuation date selector;
- market snapshot indicator;
- curve set selector;
- model settings panel;
- PV and risk summary;
- cashflow table;
- warnings and diagnostics.

### 2. Scenario screen

Purpose: compare base valuation against shocks.

Components:

- shock type selector;
- curve shock inputs;
- FX shock inputs;
- vol shock inputs;
- base vs shocked valuation result;
- risk deltas;
- chart-ready scenario output.

### 3. Backtesting screen

Purpose: run and inspect historical valuation or strategy backtests.

Components:

- date range selector;
- strategy or product selector;
- parameter controls;
- PnL / PV chart;
- exposure chart;
- trade event overlay;
- result table;
- export button;
- AI explanation panel.

### 4. Market data screen

Purpose: inspect loaded data and quality.

Components:

- market snapshot selector;
- curve table;
- curve chart;
- vol surface table;
- fixing table;
- source and timestamp metadata;
- missing data warnings.

### 5. AI inquiry screen

Purpose: let the trader ask structured questions in natural language.

Components:

- prompt input;
- parsed request preview;
- validation result;
- execution result;
- explanation;
- generated script preview where relevant.

The parsed request preview is important. The user should see what AI thinks it is about to run.

## UI implementation stages

### Stage 1 — Streamlit prototype

Fastest way to inspect the workflow.

Use Streamlit to validate:

- sample market data loading;
- curve chart;
- simple scenario controls;
- output tables.

### Stage 2 — Structured app modules

Separate UI orchestration from pricing logic.

### Stage 3 — Better charting

Add Plotly or TradingView Lightweight Charts style components for richer interaction.

### Stage 4 — Backtesting interface

Add historical valuation controls and result charts.

### Stage 5 — AI inquiry interface

Add prompt-to-structured-request workflow only after deterministic APIs exist.

## Visual reference handling

If visual references are added, store them under:

```text
docs/ui/references/
```

Also add written interpretation under:

```text
docs/ui/ui-spec.md
docs/ui/design-tokens.md
```

Do not expect AI agents to infer financial behavior from screenshots alone.

Written specs take precedence over images.

## UI rules

1. UI code must not own pricing logic.
2. UI should display valuation date and snapshot identity clearly.
3. Warnings must be visible, not hidden in logs.
4. Scenario assumptions must be shown near outputs.
5. Charts must not fabricate data for visual effect.
6. AI-generated explanations must be grounded in structured outputs.
7. The interface should remain usable on a normal desk laptop.

## MVP direction

For now, the Streamlit prototype is enough.

Do not spend too much time polishing UI until the valuation context and pricing API are stable.
