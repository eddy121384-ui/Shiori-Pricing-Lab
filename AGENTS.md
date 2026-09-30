# AGENTS.md

Rules for AI coding agents working in this repository:

1. Eddy's latest explicit request is authoritative.
2. Solve the current issue with the smallest correct change. Read the relevant code, tests, and contracts first; reuse or delete before adding. Do not perform unrelated cleanup or speculative future-proofing.
3. Agents own ordinary engineering decisions and should continue until the stated acceptance criteria are satisfied. Do not stop for routine implementation choices. Use parallel/subagents only for genuinely independent work.
4. Pricing and risk must be deterministic and use explicit inputs. Never fabricate market data, Bloomberg evidence, pricing results, or missing assumptions.
5. A new financial methodology, pricing, canonical-schema, validation, or fallback decision not already fixed by the issue or approved docs is **RED**: stop and return the decision to Eddy/Sophira. Do not guess.
6. Run the smallest relevant checks that prove the changed behavior. Pricing-method changes require deterministic tests and Eddy's approval.
7. Work attached to an existing PR stays on that issue branch and PR unless Eddy explicitly says otherwise.
8. End each implementation/amendment round with a remote-HEAD-confirmed PR summary. When a trusted workflow owns publication, the implementation agent produces an uncommitted patch, validation runs in an isolated job without model credentials, and a separate trusted publisher alone commits and pushes the validated patch.
9. Human or interactive review requests use a separate PR comment containing exactly `@codex review`. Automated publishers may rely only on a configured supported automatic-review trigger (for example, Codex Automatic review on push); do not assume bot-authored mentions trigger review.
10. Handle current-HEAD findings as a batch: P0/P1 = BLOCKER, P2 = MATERIAL, P3 = MINOR. Address valid P0-P2 on the same branch/PR, then validate, publish, summarize, and re-review. Do not churn on P3 unless it affects correctness or Eddy asks.
11. Automation may relay current-HEAD Codex P0-P2 only on approved same-repository owner PRs. Model execution, validation, and publication must remain separate trust domains; automated agents must not modify their own workflow/governance/dependency control plane. Stop on stale HEAD, RED, `ESCALATE`, validation failure, or three completed correction rounds.
12. No agent or automation may merge. Codex-clear means no unresolved current-HEAD P0/P1/P2; that is necessary, not sufficient. The final gate is:
`READY TO MERGE — 等待 Eddy 明確批准`
Merge still requires Eddy's explicit approval.
