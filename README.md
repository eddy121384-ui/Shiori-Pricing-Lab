# Shiori Pricing Lab

Shiori Pricing Lab is a private, AI-native pricing and market analytics workspace for building trader-owned tools.

The project starts with rates and fixed-income workflows, but the architecture is intentionally modular so that future data sources, pricing engines, charting components, and trading journals can be added without rewriting the whole system.

## Core idea

This is not meant to replace Bloomberg, internal systems, or official risk infrastructure. It is a personal research and workflow lab that helps a trader make pricing logic explicit, testable, explainable, and easier for AI coding assistants to maintain.

The long-term goal is to combine:

- market data adapters, starting with CSV/manual data and later Bloomberg where permitted;
- pricing and risk calculations, starting with bonds, curves, DV01, scenario shocks, carry and roll;
- clean visual dashboards and charting;
- trading journal and position notes;
- AI-readable project structure, specs, tests, and documentation.

## First milestone: MVP v0.1

The first version should stay deliberately narrow. It should answer one practical question:

> Can I load rates data, build a simple curve, price a fixed-income instrument, calculate risk, run scenarios, and visualize the result without relying on a fragile spreadsheet?

Initial MVP scope:

- local Python project structure;
- CSV/manual market data provider;
- basic fixed-income calculation skeleton;
- yield curve and scenario chart prototypes;
- local web app prototype;
- simple trade journal storage;
- tests and benchmark hooks.

## Project principles

1. Keep data access separate from pricing logic.
2. Keep pricing functions deterministic and testable.
3. Do not call Bloomberg, APIs, or external AI services directly from model code.
4. Prefer clear modules over magical notebooks.
5. Document every assumption that affects pricing, risk, or scenario output.
6. Treat external data, Bloomberg data, internal positions, and trade records as sensitive.
7. Build small, verify, then expand.

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

## Repository layout

```text
.
├── AGENTS.md                    # Instructions for Codex / AI coding agents
├── README.md                    # Project overview
├── docs/
│   ├── architecture.md          # System architecture
│   ├── roadmap.md               # Development roadmap
│   └── spec_v0.1.md             # First MVP specification
├── examples/
│   └── sample_market_data.csv   # Toy data for development
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

Do not commit credentials, Bloomberg entitlements, raw client data, internal position files, confidential reports, or production secrets.
