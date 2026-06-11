# AGENTS.md

This repository is designed to be maintained with AI coding agents such as Codex, Claude Code, or other assistants. Follow these rules when editing the project.

## Project identity

Project name: Shiori Pricing Lab

Purpose: build a private, AI-readable pricing and market analytics workspace for trader-owned fixed-income and rates tools.

The project should begin narrow and reliable. Do not turn it into a full Bloomberg clone, TradingView clone, or generic quant platform in early versions.

## Current priority

MVP v0.1 focuses on:

- project skeleton;
- clean data-provider abstraction;
- CSV/manual market data loading;
- basic curve representation;
- simple bond/rates analytics skeleton;
- scenario shock calculation;
- chart-ready data objects;
- local app prototype;
- tests.

## Engineering rules

1. Keep pricing logic independent from data sources.
2. Bloomberg/API calls must only live in `src/shiori_pricing_lab/data/`.
3. Pricing modules must accept explicit inputs and return explicit outputs.
4. Do not hide assumptions inside UI code.
5. Do not add external services, credentials, or cloud calls unless explicitly requested.
6. Do not commit real Bloomberg data, internal bank data, client information, real positions, or secrets.
7. Prefer simple readable code over clever abstractions.
8. Add or update tests when adding calculation logic.
9. Keep examples synthetic unless the user explicitly provides public data.
10. Write comments to explain financial assumptions, not obvious Python syntax.

## Suggested workflow for agents

Before coding:

1. Read `README.md`.
2. Read `docs/spec_v0.1.md`.
3. Read `docs/architecture.md`.
4. Identify which module owns the change.

When implementing:

1. Start with the smallest useful version.
2. Add tests for deterministic calculations.
3. Keep imports lightweight.
4. Avoid broad refactors unless requested.

When proposing larger changes:

1. Create or update an issue first.
2. State the financial assumption affected.
3. State expected user-visible behavior.
4. State test coverage.

## Style preference

The codebase should be boring, explicit, and easy for another AI to edit. This is a feature, not a weakness.
