# Shiori Pricing Lab

Shiori Pricing Lab is a private, AI-native workspace for trader-owned pricing and market-data workflows.

It is a research and prototyping tool. It is not an official booking, risk, valuation, accounting, compliance, or regulatory system.

## Core rule

Pricing and risk results must come from deterministic code with explicit inputs. AI may help build, test, review, and explain the system, but it must not invent market data or numeric results.

The basic flow is:

```text
Product terms
+ valuation date
+ market data
+ model inputs
→ deterministic pricing engine
→ result and diagnostics
```

Data access, pricing logic, UI rendering, AI assistance, and persistence should remain separate where the current workflow actually needs those boundaries.

## Development approach

Work directly toward Eddy's current requested outcome using the simplest safe path.
Pricing-method changes require deterministic tests and Eddy's approval.

## Quickstart

```bash
pip install -e ".[dev,quant]"

# focused standalone bond-option workflow test
pytest -q tests/test_standalone_option_workbench.py

# full suite
pytest -q

# launch the trader-facing app
streamlit run src/shiori_pricing_lab/app/streamlit_app.py
```

The current trader-facing entry point is the **Standalone Bond Option
Workbench** page in the launched app.

## Standalone bond-option workbench (local, no setup)

**Windows:** double-click `start_shiori.bat` in the repository root. It
opens the workbench at `http://127.0.0.1:8765/` once ready.

**Manual fallback (any OS):**

```bash
python -m venv .venv
.venv/bin/pip install -e ".[quant]"   # Windows: .venv\Scripts\pip
.venv/bin/python -m shiori_pricing_lab.app.standalone_option_workbench_server
```

First launch needs an internet connection to install dependencies
(including QuantLib) into a repo-local `.venv`. Keep the server window open
while you use the workbench; close it (or press Ctrl+C) to stop the server.

Current supported slice: a standalone European, price-based, cash-settled
bond option (bond-option leg only). Current product objective: enter one
anonymized real-market case and compare it with Bloomberg, tracked in
issue #94.

## Repository layout

```text
src/shiori_pricing_lab/
├── data/       # data adapters and input contracts
├── pricing/    # deterministic pricing and risk logic
├── app/        # local user interfaces and orchestration
├── charts/     # chart-ready transformations
└── journal/    # research and workflow notes

tests/          # deterministic and behavioral tests
docs/           # methodology, architecture, and reference material
```

Read only the documents required by the current task. Historical or future-state documents must not expand the task.

## Safety

Do not commit credentials, client information, internal positions, restricted Bloomberg data, confidential reports, production market data, or other bank-sensitive material.

Use synthetic examples unless explicitly approved otherwise.
