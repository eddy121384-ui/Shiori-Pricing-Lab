# 12 PR Review Rubric (for AI reviewers)

This rubric tells an AI reviewer (Codex, Claude, or any assistant) how to review a
pull request in Shiori Pricing Lab. The goal is **useful, diff-grounded review**,
not roleplay, not generic commentary, and not verbosity.

It complements `AGENTS.md` (the repository constitution) and the numbered
architecture docs. When a PR touches pricing or risk, the financial-correctness
rules in `AGENTS.md` and the relevant product/valuation docs win over anything
here.

---

## 1. How to review

- **Be diff-grounded.** Every finding must point at a line the PR actually
  changes (or a direct, concrete consequence of it). Do not review unchanged
  code, do not restate the whole file, and do not invent hypothetical problems.
- **Lenses, not personas.** The four lenses below (§3) are *angles to check*, not
  characters to play. Never write "as a quant, I would…" or "pretend you are a
  trader". No persona narration.
- **Only the relevant lenses.** Apply a lens only if the diff actually touches
  that concern. A docs-only PR usually needs the design/readability lens and
  little else; a day-count change needs the quant lens. Do not force all four
  lenses to speak on every PR.
- **Concrete over generic.** Prefer naming a specific blocker, edge case, missing
  test, broken contract, or misleading output over general advice ("add more
  tests", "consider performance"). If you cannot tie advice to a concrete risk in
  this diff, leave it out.
- **Short by default.** A clean PR gets a short approval. Length is not a proxy
  for rigor. Say nothing rather than pad.
- **Respect scope.** This repo values small, boring changes. Do not ask a PR to
  do more than its issue; flag *over*-reach (see the IT lens) as readily as
  under-reach.

Each finding should carry a severity (§2), a one-line statement of the problem,
and — where useful — a concrete failing case or the specific line.

---

## 2. Severity scale

| Severity | Use for |
| --- | --- |
| **P0 / P1** | Wrong PV or risk; market-data leakage / look-ahead / future-data use; a break of the deterministic pricing contract (`price(...)` / `PricingResult`); fabricated results (fake `0.0`, invented rates, made-up market data); or a safety / security issue. These block merge. |
| **P2** | A missing edge case; unclear or wrong failure behavior; a missing test for new calculation or contract logic; a **likely** performance or maintenance risk. Should be addressed or explicitly deferred. |
| **P3** | Naming, doc clarity, small cleanups, and non-blocking design/readability nits. Optional. |

### Where unnecessary complexity lands

Unnecessary complexity (see the IT lens, §3.2) is **P2 only if** it creates a
**likely** performance, maintenance, testability, or future-editing risk.
Otherwise it is **P3**. Do not inflate a stylistic preference into a blocker.

### Financial / determinism issues are never "just style"

Anything that changes a number, hides a pricing assumption, uses the system date
in pricing, mixes market states, or lets AI/UI bypass the deterministic pricing
API is at least P1 — never downgrade it to a readability nit.

---

## 3. Review lenses

Apply the lenses that fit the diff. Most PRs need one or two.

### 3.1 Quant / financial correctness lens

Use when the diff touches pricing, curves, schedules, day counts, discounting,
risk, valuation dates, market data, or product schemas.

Check for:

- **Wrong or approximated math** — day-count/accrual errors, discount/forecast
  formula mistakes, sign or pay/receive errors, silent approximation of an
  unsupported convention instead of an explicit failure.
- **Determinism** — same inputs must give the same outputs; no reliance on
  iteration order, dict ordering, or floating-point nondeterminism in results.
- **No system date in pricing** — valuation date must come from explicit inputs,
  never `date.today()` / `datetime.now()`.
- **No future data / look-ahead** — valuing date `T` must not read a later date's
  data (critical for historical valuation and backtesting).
- **No fabricated results** — missing market data must fail explicitly
  (`MISSING_MARKET_DATA`), never a fake `0.0`, invented curve, or back-filled
  rate.
- **Assumptions are surfaced** — single-curve, no-BDC, no-calendar, currency
  scope, etc. are recorded (e.g. in `assumptions`) and not hidden in code.
- **Contract fidelity** — results use `PricingResult` correctly (`pv is None` on
  failure; structured error/warning codes; correct status).

### 3.2 IT / engineering lens

Use for almost any code diff. Covers correctness-adjacent engineering and,
importantly, **unnecessary code weight**.

Check for:

- **Layering** — data adapters, valuation context, pricing engines, UI, and AI
  stay separate; no `pricing → data.providers` import, no UI pricing directly, no
  AI bypassing `price(...)`.
- **Broken contracts / regressions** — public function shapes, `PricingResult`
  fields, and existing invariants stay intact; new failures are structured, not
  raised where a `FAILED` result is expected (and vice-versa).
- **Tests** — new calculation or contract logic has deterministic tests; failure
  paths are tested, not just the happy path.
- **Unnecessary code weight** (flag these explicitly):
  - unnecessary abstractions, wrappers, factories, managers, adapters, or helper
    layers added before there is a real second caller;
  - duplicated logic, or needless data conversions / re-parsing / format
    round-trips;
  - broad refactors not required by the issue (scope creep in a small PR);
  - generic, framework-like code built for hypothetical future use cases that do
    not exist yet;
  - hidden performance costs — work inside loops that could be hoisted, repeated
    parsing, repeated object construction, avoidable copies of frames/arrays;
  - code that makes the repo **harder for a future agent or human to understand
    without improving correctness, safety, or user-visible behavior**.
- **Severity for the above** — apply the §2 rule: P2 if it creates a likely
  performance, maintenance, testability, or future-editing risk; otherwise P3.
  Prefer suggesting the *smaller* version over demanding a rewrite.

### 3.3 Trader / workflow lens

Use when the diff affects what a desk user sees or does: valuation output,
result tables, error messages, statuses, provenance, or the flow a user drives.

Check for:

- **Misleading output** — a result that looks valid but is not (e.g. a `0.0` that
  is really a failure, a PV with no indication of the currency or assumptions,
  a success status hiding a data-quality problem).
- **Actionable failures** — when something fails, can the user tell *why* and
  *what to do*? Structured codes and a clear message beat a bare exception.
- **Provenance / auditability** — rows/results carry enough context (valuation
  date, market-data-as-of, source, engine) to be trusted and reproduced.
- **Workflow fit** — does the change match how the desk actually values,
  compares, or reviews trades, or does it add friction/steps without benefit?

Do not invent product requirements here; tie comments to the diff's user-visible
behavior.

### 3.4 Design / readability lens

Use for most PRs, but keep it P3 unless it impairs correctness or future edits.

Check for:

- **Boring, explicit code** — matches the repo's preference for readable,
  obvious code over clever abstractions.
- **Naming and comments** — names say what things are; comments explain financial
  or design assumptions, not obvious syntax.
- **Docs accuracy** — docs/comments match the code the PR ships; status notes,
  issue references, and examples are correct.
- **Consistency** — new code reads like the surrounding code (structure, naming,
  error handling).

---

## 4. What not to do

- Do not roleplay a persona or force every lens to comment.
- Do not review code the PR does not touch, or block on pre-existing issues
  unrelated to the diff (mention them briefly at most).
- Do not pad with generic advice, restated code, or speculative "could someday"
  concerns.
- Do not downgrade a financial-correctness, determinism, data-leakage, or safety
  issue to a style nit.
- Do not demand broad refactors, new abstractions, or extra generality the issue
  did not ask for — the repo prefers the smallest correct change.

---

## 5. Suggested review output shape

Keep it short and skimmable:

1. **One-line verdict** — approve / approve-with-nits / request-changes, and why.
2. **Findings** — each as `severity — file:line — problem (+ concrete case or
   fix)`, most severe first. Omit the section if there are none.
3. **Optional nits** — P3 items, clearly marked optional.

A clean PR can be a single approving line. Reserve length for PRs that earn it.
