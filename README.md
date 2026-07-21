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

The app opens on the **Standalone Bond Option Workbench**, the current
trader-facing entry point. The two earlier demo pages (Rates Curve Demo,
Bond Option (BLI MVP)) remain available from the sidebar.

Current supported slice: a standalone European, price-based, cash-settled
bond option — the bond-option leg only. The deposit leg and the full
structured-product value are excluded.

### Workbench workflow

The workbench prices one case at a time and shows only values the pricing
engine actually produced. It fabricates no prices, risk sensitivities,
charts, or market data.

1. **Load a case.** Under *Advanced case input*, either edit the bundled
   example or upload your own `.json` file. The bundled example is
   sanitized synthetic market-shaped data — not Bloomberg output and not
   real-market validation. With no uploaded file nothing is priced; invalid
   UTF-8 or JSON is reported as an explicit error and never falls back to
   the example.
2. **Read the instrument header.** Issuer, coupon, maturity, currency,
   underlying ISIN, valuation date, and the case's own quote side and clean
   price are shown read-only, derived from the selected case.
3. **Adjust the seven trader inputs.** Option type, position, strike price,
   notional, forward clean price per 100, forward quote side, and
   volatility are editable directly, prefilled from the case. Exactly those
   seven values are overlaid onto a copy of the case envelope; everything
   else is passed through unchanged.
4. **Choose the run setup.** *Mode* selects price-only or price plus
   benchmark comparison and implied `PRICE_VOL` calibration. *Bond quote
   source* selects the case JSON's own quote or one Bloomberg DAPI quote.
   Bloomberg mode needs an explicit security (Yellow Key included) and an
   explicit quote side — neither has a default — and the case's expected
   ISIN is shown before retrieval. Refreshing replaces only the bond quote
   and pricing timestamp; a verified ISIN appears only after a successful
   retrieval.
5. **Price, then export.** Results show the model fair premium per 100 and
   at total notional, the forward clean price, the Black-76 PV per 100, the
   effective reporting-date discount factor, and the time to expiry, plus
   full provenance and engine detail. A failed run shows the complete
   structured errors and no premium or intermediate numbers. Any real
   result — including a failure — can be exported as current-run JSON or
   Markdown.

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
