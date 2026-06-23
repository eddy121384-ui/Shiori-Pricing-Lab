# 06 AI-native Layer

## Purpose

The AI-native layer allows traders to interact with pricing, valuation, and backtesting workflows through natural language while keeping numeric results grounded in deterministic engines.

AI should make the platform easier to use and extend. It should not replace pricing logic.

## Core rule

> AI may translate, generate, explain, and review. AI must not invent pricing results.

## Allowed AI roles

### 1. Inquiry parser

Convert natural language into structured requests.

Example:

```text
"Price a 5Y IRS receive fixed, valuation date 2024-12-31, shock the curve +10 bp."
```

AI output should become a structured request such as:

```json
{
  "request_type": "valuation",
  "product_type": "IRS",
  "valuation_date": "2024-12-31",
  "scenario": {"type": "parallel_curve_shock", "shock_bp": 10}
}
```

The structured request must still be validated before execution.

### 2. Backtest script assistant

Generate or modify strategy scripts.

Scripts must:

- be saved as code;
- be reviewable;
- run in a controlled environment;
- call approved pricing/backtesting APIs;
- include tests or sample runs when possible.

### 3. Explanation layer

Explain valuation and backtest outputs.

AI can summarize:

- what drove PV changes;
- where curve exposure sits;
- how carry / roll contributed;
- why a backtest performed poorly;
- what diagnostics to inspect next.

It must cite or reference the structured numeric result it is explaining.

### 4. Documentation and spec assistant

AI can draft:

- product specs;
- implementation plans;
- test cases;
- issue descriptions;
- code review notes;
- model assumption summaries.

## Forbidden AI roles

AI must not:

- fabricate market data;
- produce final PV without calling deterministic pricing code;
- claim Bloomberg data was checked unless the tool actually checked it;
- send confidential data to external services without explicit approval;
- change pricing assumptions silently;
- auto-approve its own generated code;
- generate backtest results without running code.

## Natural language inquiry architecture

```text
User question
→ AI parser
→ structured request
→ validation
→ deterministic pricing/backtesting engine
→ structured result
→ AI explanation
→ UI output
```

The validation step is mandatory.

## Data safety

The AI layer must assume that trading data may be sensitive.

Before sending data to any external model, the system must consider:

- whether the data is synthetic, public, internal, client-related, or confidential;
- whether masking or aggregation is required;
- whether company policy allows external model usage;
- whether logs will retain the prompt or output.

## AI-generated backtest scripts

AI-generated scripts should be treated like code from a junior developer with no production permission.

Required workflow:

1. generate script;
2. inspect script;
3. run tests or dry run;
4. check for look-ahead bias;
5. validate output schema;
6. commit only if reviewed.

## Prompting interface direction

The UI may eventually expose prompts such as:

- "Price this IRS under +10 bp parallel shock."
- "Show historical valuation from 2024-01-01 to 2025-12-31."
- "Write a backtest for a 2s10s steepener rule."
- "Explain why PV changed since last close."

Each prompt should be converted into structured requests, not executed as free-form reasoning.

## Testing AI workflows

AI workflows should be tested at the boundary:

- prompt to structured request;
- structured request validation;
- blocked unsafe request;
- deterministic engine call;
- explanation grounded in result fields.

## Development priority

Do not build AI inquiry before the deterministic pricing API is stable enough to call.

A chatbox without a trustworthy pricing core is just a confident spreadsheet ghost.
