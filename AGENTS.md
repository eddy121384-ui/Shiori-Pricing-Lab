# AGENTS.md

This repository is designed to be maintained with AI coding agents such as Codex, Claude Code, or other assistants. Follow these rules when editing the project.

## Project identity

Project name: Shiori Pricing Lab

Purpose: build a private, AI-readable Rates Desk Workbench for trader-owned pricing, valuation, historical valuation, backtesting, charting, and AI-assisted inquiry.

The project starts with vanilla rates workflows, but the long-term scope includes IRS, CCS, FX Swap, Swaptions, Bond Options, Callable Swaps, and IR Daily Range Accrual structured products.

Do not turn the early project into a full Bloomberg clone, TradingView clone, or generic quant platform. Build the shared pricing spine first.

## Source of truth

GitHub is the source of truth for executable specs, code, issues, tests, and architecture decisions that affect implementation.

Notion may be used as a discussion hub, draft space, and cross-AI handoff layer, but Notion drafts must be converted into GitHub docs or issues before implementation.

## Required reading map

Before coding, read the smallest relevant set of files.

Always start with:

1. `README.md`
2. `docs/00_vision.md`
3. `docs/01_system_architecture.md`

Then read the relevant domain document:

- Market data or historical data: `docs/02_data_and_market_snapshots.md`
- Valuation date, context, or scenario assumptions: `docs/03_valuation_context.md`
- Product representation: `docs/04_product_definition_schema.md`
- Backtesting: `docs/05_backtesting_engine.md`
- AI inquiry or script generation: `docs/06_ai_native_layer.md`
- UI or charting: `docs/07_ui_workbench.md`
- Performance or pricing backend design: `docs/08_performance_engine_backend_strategy.md`

Legacy docs still exist:

- `docs/spec_v0.1.md`
- `docs/architecture.md`
- `docs/roadmap.md`
- `docs/runbook.md`

Prefer the numbered architecture documents for new design decisions.

## Current priority

The current priority is not exotic-product pricing.

The first durable milestone is Vanilla Rates Core:

- explicit valuation date;
- market data snapshot concept;
- valuation context;
- curve framework;
- IRS / OIS / CCS / FX Swap foundations;
- deterministic PV / DV01 / scenario output;
- tests;
- simple local UI.

## Engineering rules

1. Keep pricing logic independent from data sources.
2. Bloomberg/API calls must only live in `src/shiori_pricing_lab/data/`.
3. Pricing modules must accept explicit inputs and return explicit outputs.
4. Do not hide pricing, model, or convention assumptions inside UI code.
5. Do not add external services, credentials, or cloud calls unless explicitly requested.
6. Do not commit real Bloomberg data, internal bank data, client information, real positions, or secrets.
7. Prefer simple readable code over clever abstractions.
8. Add or update tests when adding calculation logic.
9. Keep examples synthetic unless the user explicitly provides public data.
10. Write comments to explain financial assumptions, not obvious Python syntax.
11. Do not use system date inside pricing engines. Valuation date must be explicit.
12. Do not let AI inquiry code bypass deterministic pricing APIs.
13. Keep Python as the orchestration layer and allow optimized or compiled pricing backends behind stable interfaces.
14. Do not write large pure Python Monte Carlo or portfolio repricing loops without profiling and an explicit performance plan.
15. Do not claim performance improvement without a benchmark and reference-result comparison.

## Financial correctness rules

Pricing results must come from deterministic pricing engines, not LLM reasoning.

LLMs may assist with:

- parsing natural language into structured requests;
- writing backtest scripts;
- explaining outputs;
- drafting specs;
- proposing tests;
- reviewing architecture.

LLMs must not fabricate market data, PV, risk, backtest results, Bloomberg output, or model validation evidence.

## Suggested workflow for agents

Before coding:

1. Restate the target issue or spec section.
2. Identify the relevant architecture document.
3. Identify which module owns the change.
4. State financial assumptions that may affect pricing or risk.

When implementing:

1. Start with the smallest useful version.
2. Keep data adapters, valuation context, pricing engines, UI, and AI layers separate.
3. Add tests for deterministic calculations.
4. Keep imports lightweight.
5. Avoid broad refactors unless requested.

When proposing larger changes:

1. Create or update an issue first.
2. State the financial assumption affected.
3. State expected user-visible behavior.
4. State test coverage.
5. State remaining risks.

When opening or updating a pull request:

Leave a self-contained GitHub PR conversation comment that is understandable without reading the agent private chat session. Include the target issue / scope, changed files, implementation summary, tests and lint commands run with results, known limitations, explicitly deferred work, and any assumptions or financial-model choices that affect pricing or risk behavior.

## Style preference

The codebase should be boring, explicit, and easy for another AI to edit. This is a feature, not a weakness.
