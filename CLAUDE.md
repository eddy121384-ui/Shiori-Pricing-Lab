# Claude Code Instructions

@AGENTS.md

Claude Code should treat `AGENTS.md` as the shared repository constitution. This file only adds Claude-specific workflow guidance.

## Working model

Shiori Pricing Lab is an AI-native Rates Desk Workbench, not a one-off pricing script.

The long-term scope includes IRS, CCS, FX Swap, Swaptions, Bond Options, Callable Swaps, IR Daily Range Accrual products, valuation, historical valuation, backtesting, charting, and AI-assisted inquiry.

Do not attempt to implement the entire vision at once.

## Before coding

Read the smallest relevant set of documents before editing code:

1. `AGENTS.md`
2. `docs/00_vision.md`
3. `docs/01_system_architecture.md`
4. The relevant domain document, such as valuation context, market snapshots, product schema, backtesting, AI layer, or UI workbench.

For pricing or risk changes, also read the relevant product spec once product specs exist under `docs/products/`.

## Execution workflow

For non-trivial changes:

1. Restate the target issue or spec section.
2. Propose a short implementation plan.
3. Identify the modules that will change.
4. Implement the smallest useful version.
5. Add or update deterministic tests.
6. Run the test suite if the environment supports it.
7. Summarize changed files, assumptions, test results, and remaining risks.

## Financial correctness rules

Pricing results must come from deterministic pricing engines, not LLM reasoning.

LLMs may help with:

- natural-language inquiry parsing;
- writing backtest scripts;
- explaining valuation output;
- generating documentation;
- proposing tests;
- reviewing architecture.

LLMs must not fabricate market data, production valuations, risk results, Bloomberg outputs, or model validation evidence.

## Implementation boundaries

Do not let the UI directly price products.

Do not let product engines directly fetch Bloomberg, CSV, database, or web data.

Do not let AI inquiry code bypass the deterministic pricing API.

Do not commit secrets, real client data, internal position files, Bloomberg entitlement details, or downloaded production market data.

## Preferred output style for Claude Code

When responding after code changes, include:

- what changed;
- why it changed;
- how to run or test it;
- what assumptions were made;
- what remains unfinished.

Be explicit and boring. In this repo, boring code is a feature.

Always summarize what changed in GitHub itself, not only in chat.

## GitHub PR execution report requirement

After opening or updating any pull request, Claude Code must leave a top-level GitHub PR comment with a self-contained execution report.

Do not rely only on the chat reply. The GitHub PR conversation should contain the audit trail.

The PR comment must include:

1. PR / Branch
   - PR number
   - Branch name
   - Base branch
   - Related issue number, if any

2. Intent
   - What this PR is trying to accomplish
   - Why the change exists

3. Files changed
   - Main files changed
   - One short note per file

4. What changed
   - Specific implementation or documentation changes

5. What intentionally did not change
   - Explicit scope boundaries
   - Anything deferred or intentionally left untouched

6. Tests / checks
   - Exact commands run
   - Exact results
   - If tests were not run, explain why

7. Review status
   - Whether Codex review was requested
   - Whether prior Codex findings were addressed
   - Whether human review is still needed

8. Issue status
   - Related issue status after this PR
   - Whether the issue should remain open or can be closed

9. Follow-up work
   - What should happen next
   - What remains out of scope

The report should be understandable from GitHub alone, without requiring external chat context.
