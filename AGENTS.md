# AGENTS.md

Rules for AI coding agents working in this repository:

1. Eddy's latest explicit request is authoritative.
2. Solve the current task directly with the smallest correct change.
3. Read the relevant existing code before editing. Reuse or delete before adding new code.
4. Do not change unrelated behavior or create speculative abstractions, documents, issues, or process.
5. Agents may choose ordinary implementation details independently.
6. Pricing and risk results must come from deterministic code. Do not fabricate market data or Bloomberg evidence.
7. Do not guess financial methodology or data meaning. Pricing-method changes require deterministic tests and Eddy's approval.
8. Run the smallest relevant checks that prove the changed behavior.
9. Do not merge without Eddy's explicit approval.

## Pull-request execution protocol

10. When work is attached to an existing pull request, remain on that issue branch and PR unless Eddy explicitly requests otherwise.

11. At the end of every implementation or amendment round, complete relevant validation, commit, push, confirm the remote PR head, and post an execution summary in the PR Conversation. The summary must include the issue, branch, latest HEAD, files changed, validation, RED/methodology status, and next action.

12. After an implementation agent has pushed a reviewable round, request independent Codex review on the new HEAD by posting a separate PR comment containing exactly:
`@codex review`
If the agent lacks permission to post the comment, report that the Codex review request remains pending.

13. Codex review priorities map to repository handling categories as follows: P0/P1 = BLOCKER, P2 = MATERIAL, P3 = MINOR. Implementation agents must address valid BLOCKER or MATERIAL findings on the same branch and PR, then validate, push, post a new round summary, and request Codex review again. Do not perform unrelated cleanup while addressing review findings.

14. MINOR-only (P3) review findings do not require automatic churn unless they affect correctness or Eddy explicitly requests the change. Accepted P3 findings may remain unresolved.

15. A RED methodology, pricing, schema, validation, or fallback decision must stop the review loop and return control to Eddy/Sophira. Agents must not guess the decision.

16. If the same substantive review finding survives two attempted fixes, or three implementation-review correction rounds occur on the same PR, stop and escalate instead of continuing the loop indefinitely.

17. No agent may merge automatically. For merge-gate purposes, a Codex review is considered clear when there are no unresolved P0, P1, or P2 findings; accepted P3 findings may remain. This is necessary but not sufficient for merge. The final merge gate is:
`READY TO MERGE — 等待 Eddy 明確批准`
and merge still requires Eddy's explicit approval.
