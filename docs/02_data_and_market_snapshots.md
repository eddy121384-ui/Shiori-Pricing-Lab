# 02 Data and Market Snapshots

## Purpose

Market data must be handled as versioned snapshots, not loose values floating around the UI or pricing code.

A market snapshot represents the market state available for a given valuation date.

This is required for:

- valuation at arbitrary valuation dates;
- historical valuation;
- backtesting;
- scenario replay;
- auditability of pricing assumptions;
- AI-assisted explanation without data ambiguity.

## Core concept

```text
MarketDataAdapter
→ normalized raw points
→ MarketDataSnapshot
→ ValuationContext
→ PricingEngine
```

Adapters load data. Snapshots freeze the data state. Pricing engines consume snapshots through valuation contexts.

## Snapshot identity

Every market snapshot should eventually have:

- snapshot id;
- valuation date;
- market close or intraday timestamp where relevant;
- source, such as manual, CSV, Bloomberg, internal database, synthetic;
- creation timestamp;
- data version;
- quality flags;
- notes.

Example shape:

```python
MarketDataSnapshot(
    valuation_date="2026-06-23",
    source="synthetic",
    curves={...},
    fx={...},
    vols={...},
    fixings={...},
    metadata={...},
)
```

## Data categories

### Curves

Possible curve types:

- discount curve;
- forecast curve;
- OIS curve;
- IRS curve;
- cross-currency basis curve;
- FX forward curve;
- issuer or bond curve where relevant.

Curve records should preserve:

- curve name;
- currency;
- curve type;
- tenor;
- instrument ticker or source instrument;
- quote type;
- quote value;
- source;
- valuation date.

### FX data

FX data may include:

- spot;
- forward points;
- outright forwards;
- basis inputs;
- settlement conventions;
- holidays.

### Volatility data

Volatility data may include:

- swaption normal vols;
- swaption Black vols;
- expiry / tenor grid;
- strike or moneyness dimension;
- SABR parameters later;
- vol source and timestamp.

### Fixings

Fixings are critical for historical valuation and path-dependent products.

Fixings may include:

- floating-rate index fixings;
- FX fixings;
- range accrual daily observations;
- holiday-adjusted observation dates;
- known / unknown flags as of valuation date.

### Reference data

Reference data may include:

- bond coupon;
- maturity;
- call schedule;
- calendars;
- day-count conventions;
- payment frequency;
- index definitions.

## Current v0.1 data schema

The current sample market data uses this minimal schema:

```text
date,ticker,tenor,value,data_type,source
```

This is enough for early curve and scenario prototypes, but not enough for production valuation.

## Future normalized schema direction

A richer rates point schema may include:

```text
valuation_date
as_of_time
source
market
currency
curve_name
curve_type
instrument_type
ticker
tenor
quote_type
quote_value
quote_unit
calendar
convention
quality_flag
```

## Storage direction

Short term:

- synthetic CSV examples;
- manual provider;
- small local data files for tests.

Medium term:

- SQLite for journal and metadata;
- DuckDB for local analytical queries;
- Parquet for historical market snapshots.

Long term:

- provider-specific adapters such as Bloomberg;
- versioned local cache;
- clear separation between public/synthetic data and sensitive data.

## Snapshot rules

1. Pricing engines must not fetch market data directly.
2. Market data must be normalized before reaching pricing engines.
3. Historical valuation must use the snapshot valid for the requested valuation date.
4. Backtests must record the data source and snapshot identity used.
5. Tests must run without Bloomberg or proprietary data.
6. Synthetic sample data must be clearly labeled as synthetic.

## Bloomberg boundary

Bloomberg integration, when added, must stay inside data adapters.

It should not leak into:

- pricing engines;
- product definitions;
- backtest strategy scripts;
- UI components;
- AI inquiry layer.

Tests should use mock providers so CI can run without Bloomberg.
