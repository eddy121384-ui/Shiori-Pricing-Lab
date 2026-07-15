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

## Development model

Implementation scope is defined one approved GitHub issue or PR slice at a time.

Long-term vision, roadmaps, architecture documents, product specifications, annexes, and future phases are reference material. They do not authorize implementation by themselves.

Agents must follow [`AGENTS.md`](AGENTS.md), including the lean implementation gate:

1. Do not build what the current slice does not need.
2. Reuse existing behavior.
3. Prefer the standard library, platform-native features, and installed dependencies.
4. Write only the smallest complete implementation that remains financially correct and testable.

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
docs/           # methodology, architecture, runbooks, and reference material
```

Read only the documents required by the current approved slice. Historical or future-state documents must not expand the task.

## Safety

Do not commit credentials, client information, internal positions, restricted Bloomberg data, confidential reports, production market data, or other bank-sensitive material.

Use synthetic examples unless explicitly approved otherwise.
