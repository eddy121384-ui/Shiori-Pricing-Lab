# Architecture

## 1. Design goal

Shiori Pricing Lab should be modular enough to swap data sources, pricing engines, charting layers, and storage backends without rewriting the whole project.

The most important design boundary is:

> Data providers fetch or load market data. Pricing engines consume normalized market data. UI components display results. These layers should not leak into each other.

## 2. Layered architecture

```text
External data / local files
        ↓
data providers
        ↓
normalized market data objects
        ↓
pricing and risk engines
        ↓
chart-ready outputs / journal records
        ↓
local app / dashboard / reports
```

## 3. Main modules

### 3.1 `data/`

Responsibilities:

- define market data schemas;
- load sample data;
- load CSV data;
- provide future Bloomberg adapter interface;
- handle cache/storage integration later.

Must not:

- price instruments;
- calculate risk;
- render UI.

### 3.2 `pricing/`

Responsibilities:

- represent curves;
- run interpolation;
- apply shocks;
- price instruments;
- calculate risk measures.

Must not:

- read raw files directly;
- call Bloomberg or external APIs;
- render UI.

### 3.3 `charts/`

Responsibilities:

- convert analytics output into chart-ready data;
- keep UI-independent chart data formatting;
- prepare series for Plotly or future charting engines.

Must not:

- perform pricing calculations;
- fetch market data.

### 3.4 `journal/`

Responsibilities:

- define trade journal schema;
- store trade records later;
- connect trade records to market state, scenario, and notes.

### 3.5 `app/`

Responsibilities:

- provide local app entry points;
- orchestrate data loading, pricing, charts, and journal views;
- keep business logic minimal.

## 4. Data provider pattern

All data providers should share a simple interface:

```python
class MarketDataProvider:
    def load_rates_points(self):
        ...
```

Possible implementations:

- `CSVMarketDataProvider`
- `ManualMarketDataProvider`
- `BloombergMarketDataProvider` later
- `MockMarketDataProvider` for tests

The pricing layer should not know which provider was used.

## 5. Storage direction

v0.1 may rely on CSV examples only.

Future versions may use:

- SQLite for trade journal and settings;
- DuckDB for local analytics queries;
- Parquet for time-series storage and cache.

## 6. UI direction

The first UI can be simple. It should prove the workflow before optimizing appearance.

Possible stages:

1. CLI / notebook-like script.
2. Streamlit local dashboard.
3. FastAPI backend + frontend.
4. TradingView Lightweight Charts integration.

## 7. Testing strategy

The most important test type is deterministic calculation testing.

Examples:

- Given a curve, applying +1 bp shock should increase each rate by 0.0001 if rates are represented in decimal terms.
- Given sample CSV data, loader should return the expected number of rows.
- Given a simple price/yield helper, outputs should match known examples.

## 8. Compliance and secrecy boundary

The architecture should assume that real data may be sensitive.

Therefore:

- no credentials in code;
- no real internal data in examples;
- no external AI calls with raw positions;
- Bloomberg integration must be isolated and optional;
- mock providers should be available for tests and demos.
