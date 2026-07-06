# 25 BLI Pricing Engine Skeleton Preflight

Status: docs-only handoff / preflight. No pricing module, result
dataclass, valuation math, payoff logic, curve interpolation,
yield/price conversion, QuantLib adapter, connector, ingestion, or UI is
added by this doc. No source file under `src/` and no test file under
`tests/` is modified. No frozen BLI v1.3 source spec file (`SPEC_v1.3.md`,
`ANNEX_A_v1.3.md`, `ANNEX_B_v1.3.md`, `ANNEX_C_v1.3.md`) is edited.
Issue #38 is unaffected and remains open.

---

## 1. Current state

After PR #63 (`BLIMarketDataSnapshot`), PR #64/#65 (the MVP input bundle
preflight and its `BLIMVPInputBundle` implementation, including the
Codex P1/P2 fixes for eligibility re-verification, as-of timezone
handling, and currency coherence), and PR #66
(`build_bli_mvp_input_bundle`), the following exist as real, tested
code:

```text
src/shiori_pricing_lab/data/bli_mvp_input_bundle.py
  -- BLIMVPInputBundle: frozen dataclass binding one
     BondLinkedStructuredProduct + one resolved BondReferenceData + one
     BLIMarketDataSnapshot by reference, enforcing every input-readiness
     gate at construction time (see docs/09's "BLI bundle construction:
     the canonical path" checkpoint for the full gate list).

src/shiori_pricing_lab/data/bli_mvp_input_bundle_builder.py
  -- build_bli_mvp_input_bundle(*, bundle_id, valuation_date, product,
     bond_reference_data_universe, market_data_snapshot) ->
     BLIMVPInputBundle: resolves the product's underlying ISIN via the
     existing resolve_bond_reference_data and constructs the bundle from
     the result. This is the only supported construction path.

src/shiori_pricing_lab/data/bli_mvp_input_bundle_fixtures.py
  -- SYNTHETIC_BLI_MVP_INPUT_BUNDLE: one positive fixture, built through
     build_bli_mvp_input_bundle from the three existing synthetic
     fixtures (product, reference data, market data).

tests/test_bli_mvp_input_bundle.py (44 tests)
tests/test_bli_mvp_input_bundle_builder.py (18 tests)
  -- python -m pytest -q -> 651 passed at the time of this doc; ruff
     clean except the 2 pre-existing, unrelated products/bond_option.py
     E501 findings.
```

**No real pricing engine exists yet for BLI.** `BLIMVPInputBundle` has
no `pv`/`dv01`/`cashflows` field and performs no pricing calculation —
it is purely an assembly-with-validation object (`docs/24` §2). Nothing
in this repo yet consumes a `BLIMVPInputBundle` to produce a valuation
result of any kind.

Separately, the **vanilla-rates-core** pricing spine already has a
working, generic contract for a different product family (IRS/OIS/CCS/
FX Swap): `src/shiori_pricing_lab/pricing/result.py`
(`PricingResult`/`PricingStatus`/`PricingErrorCode`/`PricingMessage`),
`pricing/errors.py` (`PricingContractError`/`EngineRegistrationError`,
the raise-path), and `pricing/engine.py` (the `PricingEngine` Protocol,
`PricingEngineRegistry`, and the `price(product, valuation_context,
market_snapshot)` front door, currently routing a registered **USD-only
IRS reference engine**, `pricing/irs_engine.py`). This existing
contract is built around the `Product + ValuationContext +
MarketDataSnapshot` triad, not around `BLIMVPInputBundle` — see §4's
open question for why this preflight does not assume BLI should reuse
it as-is.

---

## 2. Next slice objective

The next implementation slice adds **only a pricing engine skeleton**
for BLI — no real valuation math. It should define:

- a future pricing entrypoint, likely `price_bli_mvp`;
- minimal typed result/status shapes (sketched, not designed in detail,
  in §6);
- deterministic **not-implemented** behavior for every valid input — no
  fake numeric output of any kind.

This is intentionally the smallest possible next step: a callable seam
a future PR can register real valuation logic behind, exactly the same
"contract before methodology" pattern already used for the vanilla
IRS engine (`docs/10_irs_reference_engine_preflight.md` → PR #23's
contract-only slice → PR #29's real IRS engine).

---

## 3. Required input boundary

The pricing skeleton **must accept only `BLIMVPInputBundle`**.

It must **not** accept, as a parameter to the pricing entrypoint:

```text
a raw BondLinkedStructuredProduct
a raw BondReferenceData
a raw BLIMarketDataSnapshot
a raw ISIN string
raw curve points / a raw curve collection
a raw deposit-rate observation
```

It must **not call**, anywhere in its own implementation:

```text
resolve_bond_reference_data
build_bli_mvp_input_bundle
```

Rationale: every input-readiness gate (ISIN identity, eligibility,
currency coherence, valuation-date/as-of coherence, deposit-rate
consistency, curve-purpose presence) already lives in
`BLIMVPInputBundle.__post_init__` (docs/24 §6, PR #65) and its builder
(docs/24 §12 step 5, PR #66). A pricing engine that accepted raw inputs
or called the resolver/builder itself would either (a) duplicate those
gates, risking drift, or (b) skip them, which is exactly the boundary
violation `docs/24` §4.3's ownership table was written to prevent. The
skeleton's *only* job is to prove the seam exists and to fail
predictably until real pricing is implemented — not to re-derive
"is this bundle valid," which by construction it already is.

---

## 4. Proposed module shape

Recommended, but **not implemented by this doc**:

```text
src/shiori_pricing_lab/pricing/bli_pricing_engine.py
  -- new module inside the existing pricing/ package (no new
     src/shiori_pricing_lab/pricing/__init__.py needed -- the package
     already exists and already exports the vanilla-rates-core pricing
     contract modules listed in §1).

entrypoint: price_bli_mvp(bundle: BLIMVPInputBundle) -> BLIPricingResult
```

**Naming rationale (for the implementation slice to confirm or
override, stating why, per the same "recommendation only" caveat
`docs/23` §3.4 and `docs/24` §3.1 already used):** `price_bli_mvp`
mirrors `is_mvp_pricing_eligible`'s "MVP" labeling and keeps this
function visibly distinct from the generic `pricing.engine.price(...)`
front door, which takes a different argument shape entirely (see the
open question below).

**Open question this doc does not resolve — the implementation slice
must decide explicitly, not silently pick one:**

```text
Should the future BLI pricing engine reuse the existing generic
  pricing/result.py (PricingResult / PricingStatus / PricingErrorCode /
  PricingMessage) and pricing/engine.py (PricingEngine Protocol /
  PricingEngineRegistry) contract already used by the IRS reference
  engine -- or does BLI need its own distinct BLIPricingResult /
  BLIPricingStatus (§6), given that BLIMVPInputBundle is structurally
  incompatible with the existing PricingEngine.price(product,
  valuation_context, market_snapshot) signature?

If BLI defines its own contract (this doc's working assumption, per
  the task that produced it), is price_bli_mvp ever expected to also
  be reachable through the shared pricing.engine.price(...) front door
  (e.g. via a future adapter that wraps a BLIMVPInputBundle-consuming
  engine to satisfy the generic PricingEngine Protocol) -- or does BLI
  stay on a deliberately separate entrypoint indefinitely, given its
  fundamentally different input shape (one bundle object vs. three
  separate arguments)?
```

Read `src/shiori_pricing_lab/pricing/result.py`, `errors.py`, and
`engine.py` before writing the skeleton, specifically to answer this
question with a stated reason — do not silently default to "obviously
BLI needs its own contract" without at least confirming the existing
one truly cannot fit (or deliberately should not, to keep the two
product families decoupled).

---

## 5. Proposed result shape

Recommended, but **not implemented by this doc** — a sketch only, for
the implementation slice to confirm or amend:

```text
BLIPricingStatus (StrEnum, sketch):
  NOT_IMPLEMENTED   -- the only value this skeleton slice needs.
  (SUCCESS / FAILED / etc., if ever added, are future-slice work once
  real pricing exists -- do not pre-invent a full status vocabulary for
  outcomes nothing can produce yet.)

BLIPricingResult (frozen dataclass, sketch):
  bundle_id            str   (identity: which bundle this result is for)
  product_id            str   (product.product_id, for audit convenience)
  valuation_date         str   (bundle.valuation_date, restated for
                                convenience -- not a new source of truth)
  status                BLIPricingStatus
  engine_name            str
  engine_version          str
  message                str   (human-readable "not implemented yet" note)

  # Numeric pricing fields, if introduced in this sketch at all, must be
  # optional and None until real pricing exists -- mirroring the
  # existing pricing.result.PricingResult precedent (pv/dv01/cashflows
  # default to None "in the contract now, computed later"):
  pv                    float | None = None
  dv01                  float | None = None
  cashflows             tuple | None = None
```

**State clearly, as a hard constraint on the next implementation
slice:**

```text
no fake PV
no dummy 0.0 anywhere a real number would eventually go
no fake option value
no fake bond value
no fake deposit value
no DV01 / Greeks of any kind
any numeric pricing field that is introduced must be optional and
  default to None until real pricing logic actually computes it
```

This mirrors the existing `pricing/result.py` precedent exactly (§1):
`PricingResult.pv`/`dv01`/`cashflows` already default to `None` "part of
the contract from day one but... default to `None` because no product
pricing exists yet." The BLI skeleton must follow the same discipline,
whichever result shape the implementation slice ultimately picks.

---

## 6. Expected behavior for next implementation

For a valid `BLIMVPInputBundle`, the skeleton should **either**:

```text
(a) return a deterministic BLIPricingResult with status
    NOT_IMPLEMENTED; or
(b) raise a named BLIPricingNotImplementedError.
```

**Choose one in the next implementation, based on repo conventions** —
do not implement both, and do not leave the choice ambiguous. Relevant
precedent to weigh: the existing vanilla-rates-core spine uses a hybrid
(`docs/09` §3's "Failure handling is hybrid" model) — domain failures
return a `FAILED` `PricingResult`, while contract/programming
violations raise. "Not implemented yet" for a *valid* bundle is neither
a domain failure (nothing about the bundle is wrong) nor a programming
violation (the caller did nothing wrong) — it is a statement about the
engine's own current capability. The implementation slice should decide
which of (a)/(b) fits that better and state why, rather than silently
copying whichever pattern is easiest to write.

**Wrong input type must raise `TypeError`** — e.g. calling
`price_bli_mvp` with a raw `BondLinkedStructuredProduct`,
`BondReferenceData`, or `BLIMarketDataSnapshot` instead of a
`BLIMVPInputBundle` is a contract violation, not a "not implemented"
outcome, and must fail immediately and clearly (mirroring
`BLIMVPInputBundle.__post_init__`'s own `isinstance` checks, and the
existing `PricingContractError` raise-path precedent in
`pricing/errors.py`, §1).

---

## 7. Explicit non-goals for next implementation

Restated from this doc's own hard boundary, repeated here as the
acceptance checklist for the next implementation PR:

```text
no real valuation math
no payoff formula
no bond pricing
no option pricing
no deposit payoff calculation
no cash-flow generation
no schedule engine
no yield-to-price or price-to-yield conversion
no curve interpolation
no curve construction
no volatility surface
no credit spread model
no Treasury FTP parser
no ingestion
no Bloomberg/API connector
no QuantLib adapter
no UI / debug viewer
no scenario engine
no hedge / Greeks / DV01
```

`BLIMVPInputBundle`, `build_bli_mvp_input_bundle`, `BondOption`,
`DepositLeg`, `BondLinkedStructuredProduct`, `BondReferenceData`,
`resolve_bond_reference_data`, `is_mvp_pricing_eligible`, and
`BLIMarketDataSnapshot` (and all their component classes) must remain
unmodified by the next implementation slice. The existing
vanilla-rates-core `pricing/result.py`/`errors.py`/`engine.py`/
`irs_engine.py` must also remain unmodified unless the open question in
§4 is explicitly resolved in favor of extending them (in which case
that extension — not a rewrite — is the only change those files should
see).

---

## 8. Test expectations for next implementation

Acceptance-criteria-style list, for the next implementation PR to turn
into real tests. **None of these are written by this doc.**

```text
1. accepts SYNTHETIC_BLI_MVP_INPUT_BUNDLE without error (whichever of
   §6's (a)/(b) behaviors was chosen still "succeeds" in the sense of
   not raising for a bad reason).
2. returns (or raises) the chosen deterministic not-implemented
   behavior -- consistently, not flakily, across repeated calls with
   the same bundle.
3. if a result object is used (§6(a)), it carries the expected identity
   fields (bundle_id / product_id / valuation_date) matching the input
   bundle.
4. wrong input type (a raw BondLinkedStructuredProduct,
   BondReferenceData, or BLIMarketDataSnapshot instead of a
   BLIMVPInputBundle) raises TypeError.
5. the entrypoint does not mutate the bundle passed to it (bundle
   identity/equality unchanged after the call -- BLIMVPInputBundle is
   already frozen, so this should be true by construction, but the test
   documents the expectation explicitly).
6. the entrypoint does not call resolve_bond_reference_data or
   build_bli_mvp_input_bundle anywhere in its own code path (e.g. a
   test that monkeypatches or spies on both and asserts neither is
   invoked, or a static check of the module's imports).
7. no fake numeric pricing output is ever produced -- if a result
   object with pv/dv01/cashflows-style fields is used, assert they are
   None for the NOT_IMPLEMENTED case.
8. no pricing/interpolation/schedule/connector dependency is introduced
   -- e.g. a dataclass-fields / module-attribute boundary test similar
   to the ones already used in tests/test_bli_mvp_input_bundle.py and
   tests/test_bli_mvp_input_bundle_builder.py (asserting forbidden
   names like "interpolate", "generate_cashflows", "build_schedule" are
   absent from the new module).
```

---

## 9. Fresh-session handoff

A new Claude Code session picking up the next slice (the actual pricing
engine skeleton implementation) should read, in this order:

```text
1. This doc (docs/25_bli_pricing_engine_skeleton_preflight.md).
2. docs/09_mvp_core_runbook.md -- specifically the "BLI bundle
   construction: the canonical path (post-PR #66)" checkpoint and the
   BLIMVPInputBundle / build_bli_mvp_input_bundle checkpoints above it.
3. docs/00_development_log.md -- the PR #65/#66/this-doc entries, for
   the "why" behind each design decision already made.
4. src/shiori_pricing_lab/data/bli_mvp_input_bundle.py -- the exact
   validation gates already enforced (nothing here needs re-checking
   by the pricing skeleton).
5. src/shiori_pricing_lab/data/bli_mvp_input_bundle_builder.py -- the
   only supported construction path (never called by pricing code
   itself, but a future test fixture may still use it to build the
   bundle a pricing test then feeds into price_bli_mvp).
6. src/shiori_pricing_lab/data/bli_mvp_input_bundle_fixtures.py --
   SYNTHETIC_BLI_MVP_INPUT_BUNDLE, the fixture the next slice's happy-
   path test should reuse rather than duplicate.
7. src/shiori_pricing_lab/pricing/result.py, errors.py, engine.py, and
   irs_engine.py -- the existing generic pricing contract and its one
   registered engine, needed to answer §4's open question about whether
   BLI reuses or diverges from it.
```

The next slice should then implement the pricing engine skeleton
described in §2-§8 above **in a separate code PR** — this preflight
adds no code itself. That implementation PR should:

- confirm or amend §4's module/naming/contract-reuse decision, stating
  a reason either way;
- confirm or amend §5's result-shape sketch;
- pick exactly one of §6's two not-implemented behaviors, stating why;
- add the tests scoped in §8;
- update `docs/00_development_log.md` and `docs/09_mvp_core_runbook.md`
  with what actually landed, the same way every prior BLI slice has.

---

## 10. Scope boundaries of this PR

Docs only. No pricing module, result dataclass, valuation math, payoff
logic, cash-flow generation, schedule engine, yield-to-price or
price-to-yield conversion, curve interpolation, curve construction,
volatility surface, credit spread model, Treasury FTP parser,
ingestion, Bloomberg/API connector, QuantLib adapter, UI, or debug
viewer is added. No file under `src/` or `tests/` is modified. No
frozen BLI v1.3 source spec file is edited. `BLIMVPInputBundle`,
`build_bli_mvp_input_bundle`, `BondOption`, `DepositLeg`,
`BondLinkedStructuredProduct`, `BondReferenceData`,
`resolve_bond_reference_data`, `is_mvp_pricing_eligible`,
`BLIMarketDataSnapshot`, and the existing vanilla-rates-core pricing
contract modules are all unmodified. Issue #38 is unaffected and
remains open.
