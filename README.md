# Shiori Pricing Lab

Shiori Pricing Lab is a private, AI-native Rates Desk Workbench for building trader-owned pricing, valuation, historical valuation, backtesting, charting, and AI-assisted inquiry tools.

The project starts with vanilla rates and fixed-income workflows, but the architecture is intentionally modular so that future data sources, pricing engines, charting components, trade journals, options products, callable products, and structured products can be added without rewriting the whole system.

## Core idea

This is not meant to replace Bloomberg, internal systems, or official risk infrastructure. It is a personal research and workflow lab that helps a trader make pricing logic explicit, testable, explainable, historically reproducible, and easier for AI coding assistants to maintain.

The long-term goal is to support:

- IRS, OIS, CCS, and FX Swap;
- Swaptions;
- Bond Options;
- Callable Swaps, fixed or floating;
- IR Daily Range Accrual structured products;
- valuation at arbitrary valuation dates;
- historical valuation and backtesting;
- clean visual dashboards and TradingView-like charting over time;
- trading journal and position notes;
- AI-readable project structure, specs, tests, and documentation;
- AI-assisted inquiry, script generation, and explanation around deterministic pricing engines.

## Product thesis

The platform should be built around one reusable flow:

```text
Product Definition
+ Valuation Context
+ Market Data Snapshot
+ Model Settings
+ Pricing Engine
= Valuation Result
```

Backtesting should reuse the same flow through time:

```text
for each historical valuation date:
    load market data snapshot
    build valuation context
    value product or strategy
    store valuation / risk / PnL / diagnostics
```

AI should sit around the deterministic pricing core, not replace it.

```text
Natural language request
→ structured pricing or backtest request
→ validation
→ deterministic engine
→ numeric result
→ AI-assisted explanation
```

## Current milestone

The first durable milestone is Vanilla Rates Core. It should answer:

> Can I load rates data, build a valuation context, represent a curve, price vanilla rates products, calculate risk, run scenarios, and visualize results without relying on a fragile spreadsheet?

Initial MVP scope:

- local Python project structure;
- CSV/manual market data provider;
- market data snapshot concept;
- explicit valuation date and valuation context;
- basic curve representation;
- deterministic scenario shock helper;
- chart-ready outputs;
- local web app prototype;
- simple trade journal schema;
- tests and benchmark hooks.

## Project principles

1. Keep data access separate from pricing logic.
2. Keep pricing functions deterministic and testable.
3. Do not call Bloomberg, APIs, or external AI services directly from model code.
4. Prefer clear modules over magical notebooks.
5. Document every assumption that affects pricing, risk, scenario output, or backtesting.
6. Treat external data, Bloomberg data, internal positions, and trade records as sensitive.
7. Build small, verify, then expand.
8. Do not implement exotic products before the shared rates spine is stable.
9. Do not let AI generate final pricing numbers without deterministic engine calls.

## Suggested stack

The exact stack can evolve, but the current direction is:

- Python 3.11+
- pandas / polars for data handling
- numpy / scipy for numerical work
- QuantLib-Python later for serious fixed-income analytics
- Streamlit for fast local UI prototypes
- FastAPI later if the app becomes a more formal service
- Plotly or TradingView Lightweight Charts for visualization
- SQLite / DuckDB / Parquet for local storage and cache
- pytest for tests

## Architecture documents

Start here for new work:

```text
docs/00_vision.md
docs/01_system_architecture.md
docs/02_data_and_market_snapshots.md
docs/03_valuation_context.md
docs/04_product_definition_schema.md
docs/05_backtesting_engine.md
docs/06_ai_native_layer.md
docs/07_ui_workbench.md
```

Legacy / supporting docs:

```text
docs/spec_v0.1.md
docs/architecture.md
docs/roadmap.md
docs/runbook.md
```

## Repository layout

```text
.
├── AGENTS.md                    # Shared instructions for Codex / AI coding agents
├── CLAUDE.md                    # Claude Code specific entrypoint
├── README.md                    # Project overview
├── docs/
│   ├── 00_vision.md
│   ├── 01_system_architecture.md
│   ├── 02_data_and_market_snapshots.md
│   ├── 03_valuation_context.md
│   ├── 04_product_definition_schema.md
│   ├── 05_backtesting_engine.md
│   ├── 06_ai_native_layer.md
│   ├── 07_ui_workbench.md
│   ├── architecture.md
│   ├── roadmap.md
│   ├── runbook.md
│   └── spec_v0.1.md
├── examples/
│   └── sample_market_data.csv   # Synthetic toy data for development
├── src/
│   └── shiori_pricing_lab/
│       ├── data/                # Data providers and schemas
│       ├── pricing/             # Pricing and risk engines
│       ├── charts/              # Chart preparation helpers
│       ├── journal/             # Trade journal models
│       └── app/                 # Local app entry points
└── tests/                       # Unit tests
```

## Safety and compliance note

This repository is for research, education, prototyping, and trader workflow support. It should not be used as an official booking, risk, valuation, or compliance system unless reviewed and approved under the relevant internal governance process.

Do not commit credentials, Bloomberg entitlements, raw client data, internal position files, confidential reports, production market data, or production secrets.
