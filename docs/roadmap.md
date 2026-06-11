# Roadmap

## v0.1 — Foundation MVP

Goal: create an AI-readable foundation that can load sample rates data, represent a curve, apply shocks, and produce chart-ready outputs.

Main tasks:

- initialize repo structure;
- define data schema;
- add CSV provider;
- add simple curve model;
- add scenario shock helper;
- add chart data helpers;
- add test skeleton;
- add local app prototype.

Definition of done:

- `pytest` can run;
- sample data can be loaded;
- simple scenario output can be produced;
- README/spec/architecture are present;
- AI agents have clear instructions.

## v0.2 — Rates analytics prototype

Goal: make the tool useful for basic fixed-income/rates checks.

Possible tasks:

- Treasury / bond pricing helper;
- clean price / dirty price / accrued interest assumptions;
- duration / convexity / DV01 prototype;
- carry and roll calculation skeleton;
- scenario table output;
- Streamlit UI.

## v0.3 — Storage and journal

Goal: connect analytics with trader workflow.

Possible tasks:

- SQLite trade journal;
- trade entry / exit records;
- rationale, regime, stop-loss, target, review notes;
- link trades to market snapshots;
- basic PnL attribution notes.

## v0.4 — Bloomberg adapter prototype

Goal: add Bloomberg support behind a strict provider interface.

Possible tasks:

- define Bloomberg provider interface;
- use mock Bloomberg provider for tests;
- document environment requirements;
- prevent credentials or live data from being committed;
- add sample mapping file for tickers and fields.

## v0.5 — Better visualization

Goal: improve charting and dashboard quality.

Possible tasks:

- Plotly curve chart;
- scenario heatmap;
- historical time-series panels;
- trade annotation overlay;
- evaluate TradingView Lightweight Charts integration.

## v1.0 — Usable personal rates workbench

Goal: a stable local tool for research and personal pricing checks.

Possible features:

- robust local data cache;
- curve building workflow;
- fixed-income pricing helpers;
- scenario engine;
- journal integration;
- dashboard;
- documented assumptions;
- test coverage for core calculations.
