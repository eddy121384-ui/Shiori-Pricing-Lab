# 12 PR Review Rubric

For AI reviewers of Shiori Pricing Lab pull requests.

This file complements `AGENTS.md`. It defines how to review; it does not add product scope or override approved methodology.

## 1. Review objective

Review the **current PR HEAD** for concrete risks introduced by the diff.

- Be diff-grounded. Read unchanged code/docs only when needed to understand a changed contract or consequence.
- Respect the issue scope. Do not demand unrelated refactors, abstractions, future-proofing, or cleanup.
- Ordinary prose/docs usually need light review. **Methodology, schema, architecture, and implementation-contract docs are executable contracts**: ambiguity that forces downstream code to invent behavior is substantive.
- Short is good. A clean PR can receive a one-line clear review.

### Completeness rule

Before submitting a review, make one completeness pass and report **all reasonably discoverable P0/P1/P2 findings together**.

A follow-up review should primarily:
1. verify prior material findings against the new HEAD;
2. check regressions introduced by the fix; and
3. report newly discoverable material issues.

Do not intentionally drip-feed material findings that were reasonably discoverable in the previous review.

If the **same substantive P0/P1/P2 defect** is still present after two completed correction attempts, do not request another automated fix. Include a separate line containing exactly:

`ESCALATE`

That signal means human/Sophira review is required. Judge substantive sameness semantically; do not rely on wording or title identity.

For the automated relay, count a completed correction attempt only from a current-PR commit carrying the exact trailer `Shiori-Automation: opencode-codex-relay`; use its `Codex-Review-ID:` trailer to associate the reviewed round. Failed/aborted runs and PR comments do not count.

## 2. Severity

| Priority | Meaning |
| --- | --- |
| **P0 / P1 — BLOCKER** | Wrong pricing/risk or units; look-ahead/data leakage; fabricated market data/results; unsafe fail-open behavior; broken deterministic pricing contract; security/safety issue; or a methodology/schema/architecture ambiguity that makes the next implementation invent material behavior. |
| **P2 — MATERIAL** | Missing edge/failure case, deterministic test, contract field/ownership rule, or a likely performance/maintenance/testability problem that should be fixed or explicitly deferred. |
| **P3 — MINOR** | Naming, wording, readability, optional cleanup, or non-blocking design preference. |

Do not inflate style preferences into P1/P2. A changed numeric result is not automatically a blocker if the change is intentional, deterministic, tested, and methodologically approved.

## 3. Relevant review lenses

Use only the lenses the diff needs.

### Financial correctness

Check:
- formula, sign, scaling, units, day count, calendars, dates, discount/forecast semantics;
- explicit valuation/as-of dates; no hidden system clock;
- no future-data use or market-state mixing;
- missing/stale/unsupported inputs fail visibly rather than becoming fake values;
- assumptions and methodology authority are explicit;
- deterministic outputs and deterministic regression tests where calculations change.

### Contract / architecture

Check:
- ownership and precedence are unambiguous;
- required inputs, normalized units, lifecycle/state, outputs, and failure behavior are complete;
- implementation boundaries do not force the next issue to guess;
- docs, schemas, code, and examples describe the same contract;
- existing validated compatibility boundaries remain intact.

A contract may deliberately leave a **value** unresolved when the owner/evidence requirement is explicit. It may not call the **shape** complete while leaving the implementation unable to represent the eventual evidence.

### Engineering

Check:
- existing public behavior and error contracts do not regress;
- new calculation/contract logic has focused deterministic tests;
- data, pricing, UI, persistence, and AI responsibilities stay separated where the repo requires it;
- the diff is no larger than the issue needs;
- avoid speculative wrappers/factories/frameworks and repeated parsing/copying/work inside hot loops;
- for automation/workflow changes, verify authorization and concurrency, secret/write-token separation, immutable handoff between model/validation/publisher, stale-HEAD checks, exact publication provenance, and fail-closed handling of self-modifying control-plane changes.

### Trader / audit workflow

When user-visible or persisted results change, check:
- failures cannot look like valid prices;
- units/currency/assumptions are understandable;
- provenance is sufficient to reproduce the result;
- the change does not add unnecessary desk workflow friction.

## 4. Review discipline

- Review the commit/HEAD actually requested. Treat findings tied only to superseded lines as outdated unless the issue still exists.
- Prefer one precise finding over generic advice. State the concrete consequence.
- Do not review unrelated pre-existing defects as blockers for this PR.
- Do not prescribe a broad redesign when a smaller correct fix exists.
- Do not downgrade financial correctness, determinism, data leakage, security, or a material unresolved contract decision to a readability nit.
- P3-only findings may be omitted when they add no useful signal.

## 5. Output

For each material finding use:

`P0|P1|P2 — file:line — problem; concrete consequence; bounded fix direction if useful.`

List the most severe first. Keep optional P3 items clearly separate.

If escalation is required, put `ESCALATE` on its own line.

If there are no P0/P1/P2 findings, say so concisely. Do not pad a clean review.
