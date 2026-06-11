# Shiori Pricing Lab Specification v0.1

## 1. Purpose

Shiori Pricing Lab v0.1 is the first minimal version of a private trader-owned pricing and analytics workspace.

The goal is not to build a complete production-grade system. The goal is to create a clean, testable foundation that can gradually replace fragile spreadsheet workflows for research, scenario analysis, and personal pricing checks.

## 2. Target user

Primary user: a rates / fixed-income trader who wants a personal tool that can be maintained with AI coding assistants.

Secondary user: future AI coding agents that need to understand the project structure without relying on tribal knowledge.

## 3. MVP problem statement

The first version should answer:

> Given simple rates market data, can the tool load data, represent a curve, calculate basic risk/scenario output, and display chart-ready results in a local workflow?

## 4. In scope for v0.1

### 4.1 Data

- Synthetic sample market data.
- CSV-based market data provider.
- Manual data provider skeleton.
- Stable schema for rates points.

Minimum market data fields:

- `date`
- `ticker`
- `tenor`
- `value`
- `data_type`
- `source`

### 4.2 Pricing and analytics

- Basic rate point and curve objects.
- Simple linear interpolation prototype.
- Parallel curve shock function.
- DV01-style placeholder function with clearly documented assumptions.
- Bond pricing skeleton for future expansion.

### 4.3 Charts

- Convert curve data into chart-ready table format.
- Convert scenario output into chart-ready table format.
- No need to implement full TradingView-like UI in v0.1.

### 4.4 App

- Local app prototype entry point.
- The first app may be Streamlit or a simple CLI depending on implementation speed.
- The app should use sample data by default.

### 4.5 Journal

- Define trade journal schema.
- v0.1 does not need full journal UI.

### 4.6 Tests

- Unit tests for data loading.
- Unit tests for curve shock behavior.
- Unit tests for deterministic pricing helpers once implemented.

## 5. Explicitly out of scope for v0.1

- Real Bloomberg integration.
- External AI API calls.
- Real position import.
- Production valuation.
- Multi-user permissions.
- Official risk reporting.
- Live trading.
- Order execution.
- Full TradingView clone.
- Complex derivatives models.

## 6. Architecture constraints

### 6.1 Data layer isolation

Data providers must live under:

```text
src/shiori_pricing_lab/data/
```

Pricing modules must not directly call Bloomberg, yfinance, web APIs, files, or databases.

### 6.2 Deterministic calculation logic

Pricing and risk functions should be deterministic. Given the same input, they should return the same output.

### 6.3 Assumption visibility

Any pricing assumption must be visible in code, docs, or function parameters. Hidden assumptions are treated as bugs.

### 6.4 Sensitive data handling

Do not commit:

- Bloomberg credentials;
- Bloomberg entitlement data;
- real internal bank data;
- real client data;
- real position files;
- confidential reports;
- API keys;
- passwords.

## 7. Success criteria

v0.1 is considered successful when:

1. The project installs locally.
2. Sample market data can be loaded.
3. A simple curve can be represented.
4. A parallel shock can be applied.
5. Chart-ready data can be produced.
6. At least a small test suite passes.
7. AI agents can understand the repo by reading README, AGENTS, and docs.

## 8. Naming conventions

- Repository: `Shiori-Pricing-Lab`
- Python package: `shiori_pricing_lab`
- Specs: `docs/spec_v*.md`
- Issues should be small and implementation-oriented.

## 9. Future direction after v0.1

Possible v0.2 topics:

- QuantLib integration;
- Treasury bond pricing;
- IRS valuation skeleton;
- curve bootstrap prototype;
- local SQLite or DuckDB storage;
- Streamlit dashboard;
- trade journal CRUD;
- Bloomberg provider stub with mock tests.
