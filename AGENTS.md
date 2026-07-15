# AGENTS.md

This repository is maintained with AI coding agents. Build the current trader-facing workflow with the least code, documentation, infrastructure, and process that safely works.

## Implementation authority

Only these authorize implementation, in order:

1. Eddy’s latest explicit decision.
2. The current Eddy-approved GitHub issue or PR slice.
3. Pricing methodology explicitly invoked by that slice.
4. Existing runtime contracts required by that slice.

Vision, roadmap, architecture, SPEC, annexes, archives, future phases, TODOs, and reviewer suggestions are reference only. They may clarify the approved slice but may not expand it.

## Lean implementation gate

Understand the real execution path, then stop at the first applicable step:

1. Does this need to exist for the current user-visible slice? If not, do not build it.
2. Does it already exist? Reuse it.
3. Can the standard library or platform-native behavior do it? Use that.
4. Can an installed dependency do it safely? Use it.
5. Only then write the smallest complete implementation.

Prefer deletion, reuse, native behavior, mature plumbing, few files, and one vertical slice.

Do not create speculative frameworks, one-implementation interfaces, factories for one product, unused configuration, compatibility or migration layers, schema registries, persistence abstractions, delegating wrappers, or future-phase scaffolding.

“No code change needed” is valid.

## Before editing

- Read the approved issue or PR slice.
- Read the code and tests directly involved.
- Trace callers and the real execution flow.
- Read only methodology or docs needed by that flow.
- Confirm the change is necessary.

Do not load the whole vision, roadmap, or docs tree by default. Fix the smallest shared root cause, not one symptom per caller.

## Risk classification

Classify by actual effect, not by filename or object name.

### RED

RED includes changes that can alter pricing or risk output, curve construction/interpolation/discounting, forward clean price, accrued interest or coupon PV, yield-price or volatility conversion, Black-76/tree/payoff behavior, pricing-engine wiring, pricing fallback/unsupported behavior, externally consumed interfaces, or persisted-data compatibility.

For RED work:

- do not invent missing methodology;
- preserve the approved contract;
- add deterministic tests;
- state scope and exclusions;
- require independent Codex review and Eddy’s merge approval;
- stop when a required RED decision is not authorized.

### Non-RED

Internal replaceable plumbing, UI, tools, formatting, and non-pricing validation are classified by their real impact. A schema, dataclass, validator, or serializer is not automatically RED.

If work unexpectedly affects RED behavior, stop and reclassify it.

## Financial correctness

Pricing results must come from deterministic code, never LLM reasoning.

Do not fabricate market data, prices, risk, Bloomberg/vendor output, benchmark evidence, or model-validation evidence.

Keep pricing logic independent from UI rendering, external data fetching, LLM calls, and persistence side effects. Pricing inputs and valuation dates must be explicit.

Do not silently add coercion, fallback, unsupported behavior, conventions, or methodology changes.

## Tests and reviews

Tests prove current approved behavior, not hypothetical future behavior.

- RED: deterministic boundary/numerical tests, focused tests, full suite, and lint when practical.
- Non-RED: the smallest runnable check that catches the changed behavior.
- Do not build broad fixture frameworks or exhaustive type matrices without a current need.
- Do not lock in implementation trivia or test unreachable future behavior.

A reviewer finding does not authorize scope. Fix only reachable defects, approved-invariant violations, necessary proof gaps, or real security/data-loss/accessibility issues. Defer speculative hardening, future compatibility, generalized frameworks, unrelated cleanup, new methodology, and redesign beyond the slice.

Review the actual diff and path. Say nothing rather than invent findings.

## Pull requests and documentation

Default to one smallest complete, reviewable PR. Split only for materially different risk, genuine reviewability, or an unresolved RED boundary.

Every PR states concisely:

- changed user-visible or runtime behavior;
- tests/checks proving it;
- what remains out of scope.

Maintain one current source of truth per topic. Update or merge existing docs; archive or delete obsolete guidance. Do not add a document when an issue, PR body, test name, docstring, or existing doc is enough. Git history is the default archive.

Docs-only PRs are allowed only when they unlock the next concrete implementation or remove a conflicting source of truth.

Progress means tested usable behavior or verified removal of unnecessary complexity—not more files, abstractions, tests, documents, or PRs.

## Security and merge control

Do not commit credentials, client information, internal positions, restricted Bloomberg data, confidential bank files, or unapproved production market data.

Do not remove trust-boundary validation, data-loss protection, security, or accessibility in the name of simplification.

One issue/branch/PR has one primary implementation owner. Do not run overlapping agents against the same files or core invariant.

Codex reviews; it does not expand scope. No agent may approve, merge, resolve review threads, or close methodology decisions for Eddy.

Final gate:

> READY TO MERGE — awaiting Eddy’s explicit approval.
