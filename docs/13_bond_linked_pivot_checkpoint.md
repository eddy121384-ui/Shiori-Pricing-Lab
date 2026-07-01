# 13 Bond Linked Structured Pricer — Priority Pivot Checkpoint

Status: **checkpoint record only — no code, no implementation, no new issues opened.**

This document records a **product-priority pivot** and the current repository
status after PR #33, before any new implementation issues are opened. It changes
no code and no pricing behavior; it is a snapshot so future work starts from a
clear baseline.

---

## 1. What changed in priority

The near-term product priority has **shifted** from the original
**Vanilla Rates Core / IRS-first** path to the **Bond Linked Structured Pricer
(BLI) MVP**.

- This is a **priority re-ordering, not a teardown.** The existing Rates Core /
  IRS work is **not discarded**. It remains the shared, deterministic pricing
  infrastructure that BLI and every later product build on.
- The reusable pricing spine is **unchanged and still valid**:

  ```text
  Product Definition + ValuationContext + MarketDataSnapshot → price(...) → PricingResult
  ```

  BLI will register behind the **same** `price(...)` front door as a future
  per-product engine; it does not get its own parallel pricing path.

---

## 2. What PR #33 landed

PR #33 merged the **authoritative BLI v1.3 reference specs** into the repo under
`docs/bond_linked_structured_pricer/`:

| File | Role |
| --- | --- |
| `SPEC_v1.3.md` | The main Bond Linked Structured Pricer specification (v1.3). |
| `ANNEX_A_v1.3.md` | **Authoritative pricing methodology source** for BLI. |
| `ANNEX_B_v1.3.md` | **Reference FTP / market-data file specification.** |
| `ANNEX_C_v1.3.md` | **UI/UX and brand visual guidance** reference. |
| `README.md` | Lists the four authoritative files; states the specs are reference-only. |

These are **reference specifications only** — no pricing implementation, no FTP
adapter, no UI, no Bloomberg / QuantLib code was added.

---

## 3. Methodology defects already found (and fixed) in review

During PR #33, Codex review identified — and the PR fixed — several **Annex A
methodology defects** before any implementation:

1. **Clean-price tree coupon handling (§A.4.2, P1)** — the price-state tree state
   variable is clean price, so clean-price nodes must **not** be shifted down by
   the full coupon; the dirty price drops and accrued interest resets instead.
2. **Price-based put-call parity notional scaling (§A.13.2, P2)** — the
   price-based parity now shows both per-100 and full-PV forms
   (`… × N / 100`), matching the European pricing and yield-based parity.
3. **Parity tolerance basis for full-PV checks (§A.13.2, P2)** — the
   `0.1% per 100 face` tolerance must be compared on a matching unit: normalize a
   full-PV residual back to per-100, or scale the threshold by `N / 100`.

**Lesson recorded:** authoritative methodology documents are not automatically
correct just because they are "authoritative". They must receive **quant-style
review (financial-correctness lens, see `docs/12_pr_review_rubric.md`) before
implementation**, because a methodology defect that reaches code produces wrong
PV / risk, not just a style issue.

---

## 4. Near-term priority

- **Near-term priority is no longer the historical valuation loop.**
- Near-term priority **is BLI methodology teardown and an integration preflight**:
  reading Annex A/B/C carefully, mapping each methodology decision onto the
  existing spine (`price(...)`, `PricingResult`, `ValuationContext`,
  `MarketDataSnapshot`), and identifying what the existing infrastructure already
  provides versus what BLI genuinely needs.

The **next planned PR** is a docs-only teardown/preflight:

```text
docs/14_bond_linked_spec_teardown_and_integration_preflight.md
```

That preflight (a separate PR) will define the integration plan before any BLI
pricing code is written. **This checkpoint does not open or modify any
implementation issue.**

---

## 5. Status of prior in-flight work

| Item | Status after this checkpoint |
| --- | --- |
| Rates Core spine (`provider → snapshot → context → curve → scenario`) | Complete, unchanged, still the shared base. |
| Pricing contract (`price(...)` / `PricingResult`, Issue #10) | Closed; unchanged; BLI will reuse it. |
| USD IRS reference engine (Issue #27, PR #29) | Merged; unchanged; remains the first per-product engine. |
| **Issue #13 — historical valuation loop** | **Deferred / reframed.** Not implemented now. Its preflight (`docs/11`) stays valid, but the loop is reframed for later **EOD / revaluation / warehouse valuation** use rather than an immediate next step. |
| **Issue #14 — AI inquiry contract** | **Deferred** (unchanged). |
| BLI v1.3 reference specs | Landed (PR #33); teardown/integration preflight is next. |

Deferring Issue #13 does **not** delete or invalidate `docs/11`; it only moves
the loop later in the sequence. Both #13 and #14 remain open and untouched by
this PR.

---

## 6. Boundaries this checkpoint preserves

- **Docs only.** No source code, tests, CI/workflow, pricing, FTP, Bloomberg, or
  QuantLib changes.
- **No architecture rewrite** beyond recording this checkpoint.
- **The four BLI spec source files are not edited** here, and their line endings
  / whitespace are not normalized.
- **No implementation issues opened or modified.**
- The spine and all existing invariants (explicit valuation date, no system date
  in pricing, single pricing path, synthetic data only) remain in force.
