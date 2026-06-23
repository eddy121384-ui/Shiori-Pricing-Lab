# 05 Backtesting Engine

## Purpose

The backtesting engine should turn pricing and valuation infrastructure into a historical research workflow.

Backtesting is not a separate toy module. It should reuse the same product definitions, valuation contexts, market snapshots, and pricing engines used for valuation.

## Core loop

```python
for valuation_date in historical_dates:
    snapshot = load_market_snapshot(valuation_date)
    context = build_valuation_context(valuation_date, snapshot, settings)
    result = pricing_engine.value(product_or_strategy, context)
    store(result)
```

## Backtesting types

### Product historical valuation

Value the same product through time.

Examples:

- historical PV of an IRS;
- historical DV01 of a CCS;
- historical swaption value under vol surface changes.

### Strategy backtesting

Run a rule-based strategy through historical market states.

Examples:

- curve steepener strategy;
- carry / roll filter;
- swap spread strategy;
- volatility entry / exit rule;
- trade journal overlay.

### Scenario replay

Apply the same scenario rule through history.

Examples:

- +10 bp parallel shock every day;
- 2s10s steepener shock;
- basis widening;
- vol surface shock.

## Backtest input

Backtest requests should include:

```text
backtest_id
start_date
end_date
frequency
product_definition or strategy_definition
market_data_source
valuation_settings
rebalance_rules
entry_exit_rules optional
cost_assumptions optional
output_metrics
```

## Strategy scripts

AI may help generate strategy scripts, but scripts must be executable and inspectable.

A script should define:

- required inputs;
- signal calculation;
- trade generation;
- valuation call;
- output metrics.

Strategy scripts should not bypass the pricing engine.

## Backtest output

A backtest result should include:

- time series of valuation or PnL;
- positions;
- risk measures;
- trade events;
- market snapshots used;
- warnings and missing data flags;
- summary metrics;
- chart-ready data.

Possible metrics:

- total PnL;
- annualized return where meaningful;
- volatility;
- Sharpe-like ratio where meaningful;
- max drawdown;
- hit rate;
- average carry / roll;
- average DV01 or exposure;
- scenario loss.

## Data integrity rules

Backtests must avoid look-ahead bias.

This means:

- do not use future fixings;
- do not use revised data unless explicitly marked;
- do not use future curves for historical dates;
- do not let AI fill missing market data silently;
- record missing data handling.

## UI needs

The backtesting UI should eventually support:

- date range selection;
- product or strategy selection;
- parameter controls;
- chart panels;
- result table;
- trade event overlay;
- export of results;
- AI-generated explanation of results.

## MVP direction

First backtesting MVP should be deliberately simple:

- one synthetic historical curve dataset;
- one simple strategy or historical valuation;
- one deterministic pricing helper;
- one result table;
- one chart.

Do not start with exotic product backtesting.

## Test requirements

Backtesting code should include tests for:

- date loop behavior;
- no future data access;
- deterministic repeated runs;
- missing data handling;
- output schema stability.
