# 28 BLI Curve Rate Basis Preflight

Status: docs-only preflight. No curve interpolation, discount-factor
calculation, par-to-zero conversion, source-to-zero conversion,
rate-basis enum, rate-basis field, schema change, fixture change,
forward-price derivation, coupon schedule, accrued interest, volatility
conversion, yield-to-price conversion, Black-76, PV, QuantLib adapter,
Bloomberg/API connector, FTP ingestion, or UI is added by this doc. No
source file under `src/` and no test file under `tests/` is modified.
`price_bli_mvp`'s runtime behavior is unchanged. No frozen BLI v1.3
source spec file (`SPEC_v1.3.md`, `ANNEX_A_v1.3.md`, `ANNEX_B_v1.3.md`,
`ANNEX_C_v1.3.md`) is edited. Issue #38 is unaffected and remains open.

---

## 1. Where this picks up

PR #71 (merged, `b068cdc`) wrote `docs/27_bli_curve_interpolation_preflight.md`,
which already identified the core problem this doc resolves further:
`BLICurvePoint.rate` is validated only as a finite number, and nothing
in the schema proves it is already a continuously-compounded zero rate
— Annex A §A.10.2's required input for interpolation. `docs/27` §6.1
named this the "curve-rate-basis blocker" and explicitly deferred
resolving it, listing four candidate unblocking approaches without
choosing one (docs/27 §6.1's numbered list) and leaving "interpolated
zero rate" and "discount factor" helpers both blocked as a result
(docs/27 §9.2's sequencing).

Since `docs/27`, two further slices landed, each deliberately *not*
touching this blocker:

- PR #72 (merged, `3c68a89`): `pricing/bli_curve_tenor.py`,
  `tenor_to_year_fraction(tenor: str) -> float` — parses the tenor
  *label* (`"3M"`, `"2Y"`) into a year fraction. Never reads `rate`.
- PR #73 (merged, `63cdace`): `pricing/bli_curve_selector.py`,
  `select_curve_points_by_purpose(curve_points, *, currency,
  curve_purpose) -> tuple[BLICurvePoint, ...]` — a purely structural
  filter on `(currency, curve_purpose)`. Never reads `rate`; its own
  docstring explicitly states its output "must not be read as implying"
  the rates are zero rates.

Both are necessary but not sufficient: an interpolation helper still
needs a year fraction (have it), the right curve's rows (have them),
and *rates it can legally treat as zero rates* (still missing). This
doc is the preflight `docs/27` §6.1 deferred — it decides how the
project should make `BLICurvePoint.rate` safe for interpolation,
without implementing any of it.

---

## 2. Current curve-rate input contract

Everything below already exists and requires no new fixture content —
restated from `docs/27` §2.1/§2.2, re-verified directly against
`data/bli_snapshot.py` for this doc.

### 2.1 `BLICurvePoint` fields (`data/bli_snapshot.py`)

```text
curve_id         str   -- non-blank
curve_name       str   -- non-blank
currency         Currency
curve_purpose    BLICurvePurpose  -- BOND_REFERENCE_CURVE / OPTION_DISCOUNT_CURVE /
                                     DEPOSIT_CURVE / FUNDING_CURVE
tenor            str   -- non-blank, bare label (e.g. "2Y", "3M")
rate             float -- any finite number (_require_finite_number); no
                          basis, no sign constraint ("yields/rates may
                          legitimately be signed")
source_system    str   -- non-blank
status           BLIMarketDataStatus -- must be ACTIVE at construction
```

**`rate` is currently finite numeric only.** `BLICurvePoint.__post_init__`
calls `_require_finite_number(self.rate, "rate")` and nothing else on
`rate` — no positivity check, no basis check, no cross-field
consistency check with any other attribute. A finite `float` is the
entire contract `rate` currently carries.

**The current schema does not say whether `rate` is a zero rate, par
rate, swap rate, bond yield, funding rate, or another source-system
quote.** There is no field for this anywhere on `BLICurvePoint`,
`BLIMarketDataSnapshot`, or `BLIMVPInputBundle`. This is not an
oversight this doc is discovering for the first time — `docs/27` §6.1
already named the gap — but this doc restates it precisely because it
is the entire subject of what follows.

**`curve_purpose` describes use, not rate basis.** `BLICurvePurpose`
(`BOND_REFERENCE_CURVE` / `OPTION_DISCOUNT_CURVE` / `DEPOSIT_CURVE` /
`FUNDING_CURVE`) answers "what is this curve *for*" — which leg's PV it
discounts, per Annex A's own mapping (§A.2.2's `DF` for the option leg,
§A.5.3 for bond coupons). It says nothing about whether the `rate`
values attached to that purpose are already zero rates, par rates, or
something else. A `BOND_REFERENCE_CURVE` row could, in principle, carry
a par rate from a source system just as easily as a zero rate — nothing
in the type distinguishes the two cases.

**`curve_name`, `curve_id`, `source_system`, and tenor labels must not
be used to infer zero-rate status.** None of these fields assert a rate
basis:

- `curve_name`/`curve_id` are free-text identifiers (e.g.
  `"USD_BOND_REFERENCE_CURVE"`, `data/bli_snapshot_fixtures.py`) whose
  string content is not validated against any basis vocabulary and could
  say anything a source system chose to name it.
- `source_system` (e.g. `"SYNTHETIC_CURVE_FEED"` on every existing
  `BLICurvePoint` row, and `"SYNTHETIC_TREASURY_FTP_FEED"` on the
  separate FTP deposit-rate observation, both in
  `data/bli_snapshot_fixtures.py`) identifies *where* a value came from,
  not *what basis* it is quoted in — the same source system can
  plausibly emit both zero curves and par curves depending on
  instrument/tenor.
- `tenor` (`"3M"`, `"2Y"`, `"5Y"`) is an x-axis label (`docs/27`'s prior
  slice, PR #72) with zero connection to the y-axis quoting convention.

Treating any of these as an implicit "this is already a zero rate"
signal would be exactly the silent inference `docs/27` §6.1 already
forbade ("Do not silently infer zero-rate status from `curve_purpose`,
`curve_name`, `curve_id`, or `source_system`") — this doc does not
revisit that prohibition, it takes it as settled and builds the
unblocking analysis on top of it.

### 2.2 Existing fixture (`data/bli_snapshot_fixtures.py`)

```text
USD_BOND_REFERENCE_CURVE:  tenor="2Y" rate=0.0362, tenor="5Y" rate=0.0375
USD_OPTION_DISCOUNT_CURVE: tenor="2Y" rate=0.0341, tenor="5Y" rate=0.0353
USD_DEPOSIT_CURVE:         tenor="3M" rate=0.0350                (one row)
```

Every rate is a small, plausible decimal (3.4%–3.75%) consistent with
*either* a zero rate or a par rate at these maturities — the fixture
values themselves cannot resolve the basis question either; they were
chosen to be plausible market levels, not to encode a specific
quoting-convention answer.

---

## 3. Annex A requirement

Restating `docs/27` §6/§6.1's citations, re-verified directly against
`docs/bond_linked_structured_pricer/ANNEX_A_v1.3.md` for this doc:

> **§A.10.2 Yield Curve Interpolation**
> 方法：piecewise linear on **zero rates**（continuously compounded）。
> Day count：ACT/365F 統一在 curve 內部運算，匯入時依 FTP 提供的
> convention 轉換。
> 超出 curve 範圍：flat extrapolation，並標示 fallback flag。

> **§A.10.3 Curve 建構**
> MVP 不自建 bootstrapping engine。
> FTP curve 已是 zero / par curve 任一形式時，依 source 標示處理：
> FTP 直接給 zero curve → 直接使用。
> FTP 給 par curve → MVP 採 piecewise linear par→zero 簡化轉換（不做
> forward-rate smoothing）。

Three things are pinned exactly, and one important thing is **not**:

- **Pinned: interpolation method.** Piecewise linear on zero rates,
  continuously compounded. Not ambiguous, already restated by `docs/27`
  §6.
- **Pinned: day-count handling inside the curve.** ACT/365F uniformly
  internal to curve math; "匯入時依 FTP 提供的 convention 轉換" (convert
  per the FTP-provided convention at import time) — this is Annex A
  *anticipating* that imported curve data may arrive in a different
  day-count convention and need conversion at the import boundary, which
  is itself evidence the "raw import equals ready-to-interpolate" is not
  the general case Annex A assumes.
- **Pinned: par→zero conversion exists as a named case.** §A.10.3
  explicitly distinguishes "FTP directly gives a zero curve → use
  directly" from "FTP gives a par curve → MVP uses a simplified
  piecewise-linear par→zero conversion (no forward-rate smoothing)."
  Annex A **assumes a per-curve determination of which case applies** —
  it says the *conversion method* (simplified piecewise-linear, no
  forward-rate smoothing) for the par-curve case, but does not itself
  specify *how the codebase should record or check, for a given
  `BLICurvePoint` row, which of the two cases (zero vs. par) applies*.
  That determination-and-recording mechanism is exactly this doc's
  subject, and it is **not specified by Annex A** — Annex A describes
  the trading/methodology decision ("if it's a par curve, convert it
  this way"), not a data-contract decision ("how does the system know
  which curves are which"). This doc does not invent an answer to fill
  that gap; §5 below recommends the next step precisely because the gap
  is real.
- **Not explicit: how a discount factor is computed from a zero
  rate.** Neither §A.10.2 nor §A.10.3 states the exact discount-factor
  formula. `docs/27` §6.2 already inferred `DF = exp(-zero_rate × T)`
  from "continuously compounded" being the stated compounding
  convention (the standard definition of a continuously-compounded zero
  rate implies this formula), but this doc flags, as `docs/27` did, that
  this is an inference from the compounding-convention statement, not a
  verbatim formula in Annex A. This doc does not need to resolve that
  gap — it is downstream of the rate-basis question, not part of it —
  but repeats the flag for completeness since a future reader might
  otherwise assume Annex A spells out `exp(-rT)` explicitly. It does
  not.

**Conclusion for this doc:** Annex A requires the interpolation *input*
to be continuously-compounded zero rates, explicitly acknowledges that
real-world curve data may arrive as par curves needing conversion, and
gives the *conversion method* for that case — but gives no *data-model
mechanism* for a codebase to record, check, or trust which case a given
row is in. That mechanism gap is exactly what blocks
`BLICurvePoint.rate` from being used safely today, and is what §4/§5
below evaluate.

---

## 4. Existing implemented helpers and their boundary

Three pure utilities already exist in this dependency chain. None of
them solves the rate-basis question — restated precisely, per file:

### 4.1 `year_fraction_to_expiry` (PR #70, `pricing/bli_valuation_time.py`)

Computes ACT/365F between two ISO date strings
(`valuation_date`/`expiry_date`). **Only handles dates** — it has no
parameter, return value, or internal logic that touches a curve, a
tenor, or a rate at all. It is a time-axis utility for the *option's*
expiry, not the curve's x-axis or y-axis. Reusable later as the `T` in
a discount-factor formula (`docs/27` §6.2), but contributes nothing to
resolving what `BLICurvePoint.rate` means.

### 4.2 `tenor_to_year_fraction` (PR #72, `pricing/bli_curve_tenor.py`)

Converts a `BLICurvePoint.tenor`-shaped string (`"3M"`, `"2Y"`) into a
year fraction. **Only handles the curve's x-axis label** — it takes a
`str` and returns a `float`; it has no parameter for, and never reads,
`BLICurvePoint.rate`. Knowing "`\"2Y\"` means `2.0` years" says nothing
about whether the number sitting in that row's `rate` field is a zero
rate or a par rate.

### 4.3 `select_curve_points_by_purpose` (PR #73, `pricing/bli_curve_selector.py`)

Filters a `BLICurvePoint` collection down to rows matching a requested
`(currency, curve_purpose)` pair, preserving order. **Only filters
rows structurally** — it does not read `point.rate` in its filter
predicate or return value transformation (it returns the same
`BLICurvePoint` instances, unmodified), and its own module docstring
already states its output "must not be read as implying" the selected
rates are zero rates. Narrowing "all curve points" down to "the Option
Discount Curve's USD rows" does not change what basis those rows'
`rate` values are quoted in — it just answers "which rows," not "what
do the numbers in those rows mean."

### 4.4 Why none of these, individually or combined, resolves the blocker

Chaining all three (`select_curve_points_by_purpose` → for each
selected point, `tenor_to_year_fraction` → someday, interpolate against
`rate`) produces exactly the inputs an interpolation function would
need positionally (`(year_fraction, rate)` pairs for one curve) — but
positional readiness is not the same as economic correctness. Nothing
in this chain checks, asserts, or records that the `rate` values being
handed to a future interpolator are continuously-compounded zero rates
as opposed to par rates. The chain is "necessary plumbing," not "proof
the water is the right temperature."

---

## 5. Candidate unblocking paths

### Path A — Add an explicit rate-basis field to `BLICurvePoint`

**Shape:** a new `BLICurveRateBasis` enum (e.g.
`CONTINUOUS_ZERO_RATE` / `SIMPLE_ZERO_RATE` / `PAR_RATE` / `SWAP_RATE`
/ `BOND_YIELD` / `FUNDING_RATE` / `OTHER`) and a new field on
`BLICurvePoint` (e.g. `rate_basis`) carrying it, validated the same way
`curve_purpose` already is (`coerce_enum`).

- **Benefits:** Makes the basis question a first-class, per-row,
  machine-checkable fact — exactly mirroring how `curve_purpose` already
  answers "what is this curve for." A future interpolation helper can
  assert `rate_basis is BLICurveRateBasis.CONTINUOUS_ZERO_RATE` (or
  route through a conversion step for `PAR_RATE`) and raise immediately
  and legibly if the field says anything else. Self-documenting in
  every fixture and every future ingestion path — a reviewer reading a
  `BLICurvePoint(...)` construction site sees the basis, not just the
  number.
- **Risks:** A new enum member set is itself a methodology decision
  (which bases are even worth naming? is `SIMPLE_ZERO_RATE` — i.e.
  non-continuously-compounded — distinct enough from `CONTINUOUS_ZERO_RATE`
  to need its own member, or does that conflate a compounding-convention
  axis with a zero-vs-par axis into one enum?) — getting the taxonomy
  wrong now could require a breaking rename later. Every existing fixture
  construction site (`data/bli_snapshot_fixtures.py`) would need updating
  to supply the new field (schema change, however small).
- **Code impact:** New enum in `data/bli_snapshot.py` (or a sibling
  module), a new dataclass field, updated `__post_init__` validation,
  updated docstrings.
- **Data-model impact:** `BLICurvePoint`'s field set changes — every
  existing and future construction site must supply (or default) the
  new field. This is the only path of the four that changes the schema.
- **Audit impact:** Strongest of the four — the basis is recorded in the
  same object the rate itself lives in, so any audit trail that already
  captures a `BLICurvePoint` automatically captures its claimed basis
  too. No separate document or out-of-band record to keep in sync.
- **Test impact:** New tests for the enum's coercion/validation
  behavior (mirroring `BLICurvePurpose`'s own test pattern), plus every
  existing `BLICurvePoint`-constructing test needs the new field
  supplied (or an explicit default policy).
- **MVP-safe:** Yes, if scoped small (enum + field + validation only,
  no interpolation) — this is the "small explicit contract" option
  §5 below leans toward.
- **Silent-treatment risk:** Lowest of the four *if* the field is
  **required, not optional, with no default** — an optional field with
  a silently-assumed default would reintroduce exactly the risk this
  doc exists to close (a caller who forgets to set it would fall back to
  whatever the default is, which could be wrongly treated as "zero rate"
  by convention). If made required, this path structurally cannot be
  silently skipped — every `BLICurvePoint` construction site must make
  an explicit choice.

### Path B — Document and enforce an upstream normalization contract

**Shape:** no schema change. Instead, a documented, audited guarantee
that by the time a `BLICurvePoint` reaches `BLIMarketDataSnapshot`, its
`rate` is already a continuously-compounded zero rate — enforced by
whatever produces the snapshot (an ingestion pipeline, a resolver, a
manual construction discipline), not by the `BLICurvePoint` type itself.

- **Benefits:** No schema change, no fixture rewrite, no new enum
  taxonomy to get right. Fastest to state.
- **Risks:** This is the path §6.1 of `docs/27` most directly warns
  against if it is not made genuinely explicit and auditable — "the repo
  validates or records that contract somehow" is the operative
  requirement, and today there is no such recording mechanism
  anywhere in this codebase. A contract that lives only in a doc,
  unchecked by any code, degrades over time as new construction sites
  are added by future agents who have not read this doc — exactly the
  "silently inferred from source_system" failure mode `docs/27` §6.1
  already forbade, just moved one level up (from "inferred from a
  field on the object" to "inferred from a document nobody re-reads
  before adding a new fixture").
- **Code impact:** None, by construction — that is the point of this
  path, and also its weakness (no code exists to enforce it).
- **Data-model impact:** None.
- **Audit impact:** Weak unless paired with a real enforcement
  mechanism (e.g. a lint rule, a required code-review checklist item, or
  a runtime assertion somewhere) — a plain doc statement is not
  "auditable" in the sense docs/27 §6.1 required; it is closer to the
  unaudited assumption this whole dependency chain exists to avoid.
- **Test impact:** None directly possible — there is no field or
  object state to assert against in a test; a test could only assert
  "the existing fixture's rates happen to look plausible," which does
  not test the *contract*, only the *fixture's current values*.
  Not MVP-safe.
- **MVP-safe:** No, not as a standalone path — it reintroduces an
  unchecked assumption at the exact seam this entire dependency chain
  (docs/26 → docs/27 → this doc) exists to close. It could become safe
  only if combined with a genuine enforcement mechanism (which would
  then really be a variant of Path A, wearing different clothes).
- **Silent-treatment risk:** Highest of the four as a standalone path.

### Path C — Add a separate par-to-zero / source-to-zero conversion slice

**Shape:** keep raw source quotes exactly as observed (whatever basis a
source system emits), and add a new, separately reviewed conversion
layer (Annex A §A.10.3's "簡化 par→zero conversion, no forward-rate
smoothing") that runs before interpolation and produces zero-rate nodes
from source-curve nodes.

- **Benefits:** Matches Annex A §A.10.3's own methodology most directly
  — Annex A already describes this exact conversion as the answer for
  the "FTP gives a par curve" case. Keeps the raw, as-observed quote
  intact and auditable (nothing is silently overwritten), with the
  conversion as a separate, inspectable step.
- **Risks:** This is the largest of the four paths by a wide margin.
  It requires: (a) first knowing which rows need conversion at all —
  which is *this doc's own open question*, so Path C cannot even start
  until some version of Path A or B answers "is this row already a zero
  rate or not" — and (b) a genuine methodology decision from Trading
  Desk on the conversion formula's edge cases (Annex A's "simplified,
  no forward-rate smoothing" still leaves implementation choices, e.g.
  exact treatment of the boundary tenor, that are not spelled out at the
  level of precision this codebase's other Annex-A-sourced formulas
  have been given, per `docs/26`/`docs/27`'s own "no invented
  methodology" discipline).
- **Code impact:** A new, nontrivial conversion module; likely the
  single largest code addition of the four paths.
- **Data-model impact:** Depends on whether it also needs Path A's field
  to know which rows to convert (likely yes — see Risks above) — in
  practice, Path C is usually **Path A followed by a conversion step**,
  not an alternative to it.
- **Audit impact:** Strong, if paired with Path A (basis is recorded,
  and the conversion step is a visible, separate, testable
  transformation) — weak if attempted without Path A (the conversion
  step would have no principled way to decide which rows to convert).
- **Test impact:** Substantial — conversion-formula tests, boundary
  tests, and (if paired with Path A) the same field-validation tests
  Path A needs.
- **MVP-safe:** Not as the *next* slice — it depends on Path A (or an
  equivalent basis-recording mechanism) existing first, and its own
  methodology (the exact par→zero formula's edge-case behavior) is not
  yet as pinned as Annex A's other MVP-scoped formulas.
- **Silent-treatment risk:** Low, if built on top of Path A; effectively
  the same risk as Path B if attempted standalone (there would be
  nothing checking which rows actually need the conversion).

### Path D — Temporary fixture-only assumption

**Shape:** treat only the existing synthetic test fixture
(`SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`'s curve points) as if they are
already normalized zero-rate nodes, scoped narrowly to tests, with no
claim about production or any other runtime data.

- **Benefits:** Trivial to state, zero code/schema change, unblocks
  *test-writing* for an interpolation helper's happy-path cases sooner.
- **Risks:** Exactly the anti-pattern `docs/27` §6.1 forbade, just
  narrowed to "tests only" rather than "everywhere" — but the risk is
  not really contained by that narrowing: a test suite that asserts
  correct interpolation behavior against fixture rates *assumed* (not
  proven) to be zero rates would pass regardless of whether the
  underlying assumption is true, giving false confidence that a future
  production wiring is safe. Worse, once an interpolation helper exists
  and its tests pass against the fixture under this assumption, the
  pressure to also wire it into `price_bli_mvp` for the same fixture
  increases, without the basis question ever actually having been
  resolved for real inputs — silently converting a "temporary, test-only
  assumption" into a de facto production assumption is a realistic
  failure mode, not a hypothetical one.
- **Code impact:** None to `src/` (by definition, "assumption" not
  "field"); would show up only as a comment/docstring caveat on new
  test code.
- **Data-model impact:** None — this is precisely its appeal and its
  weakness (nothing enforces the boundary between "test fixture, assumed
  normalized" and "any other `BLICurvePoint` data," so the boundary
  exists only as long as every future reader remembers and respects it).
- **Audit impact:** None — an assumption stated only in a test docstring
  is not an auditable contract by any of `docs/27` §6.1's three
  acceptable-unblocking criteria (explicit basis field/contract,
  documented+audited upstream normalization, or a reviewed conversion
  slice) — it satisfies none of them.
- **Test impact:** Enables writing interpolation tests sooner, at the
  cost of those tests not actually proving the code is safe for
  non-fixture input.
- **MVP-safe:** No, not as a standalone path, and not recommended even
  as a stopgap — it is the specific failure mode `docs/27` §6.1's final
  bullet ("do not silently infer zero-rate status ... ") already
  named, merely relocated to "the test fixture," not resolved.
- **Silent-treatment risk:** High — arguably the whole point of naming
  this path is to make explicit why it should not be chosen, not to
  offer it as a real contender.

### 5.x Summary comparison

```text
Path  MVP-safe alone   Silent-treatment risk   Schema change   Code size
A     Yes              Lowest (if required)    Yes (field)     Small
B     No                Highest                No              None
C     No (needs A)      Low (if built on A)     Maybe (via A)   Large
D     No                High                    No              None
```

---

## 6. Recommendation

**Recommended: the next code PR should add a small, explicit
`BLICurveRateBasis` enum and a required `rate_basis` field on
`BLICurvePoint`, with validation and tests — no interpolation, no
conversion logic, no wiring into anything.** This is Path A, scoped to
its smallest form.

**Why this, conservatively, over the alternatives:**

- Path B (documented-only contract) does not satisfy `docs/27` §6.1's
  own bar — "explicitly documented and audited," not merely
  documented — because nothing in this codebase today can check or
  enforce a doc-only contract. A field is the smallest change that
  makes the contract machine-checkable rather than merely asserted.
- Path C (conversion slice) cannot be attempted first — it needs to
  know *which rows* require conversion, which is exactly what a
  `rate_basis` field would answer. Path C is correctly sequenced
  *after* Path A, not instead of it, and its own methodology (the exact
  par→zero formula's edge-case behavior) is not yet pinned precisely
  enough to implement without further Trading Desk input — appropriately
  out of scope for "the smallest safe next slice."
- Path D (fixture-only assumption) is explicitly rejected — it does not
  meet any of `docs/27` §6.1's three acceptable-unblocking criteria and
  risks quietly becoming a de facto production assumption.
- A **required** field (no default) is chosen over an optional one:
  an optional field with an implicit default would recreate the same
  silent-inference risk this entire chain exists to close. Every
  `BLICurvePoint` construction site — including every existing fixture
  — must explicitly state its basis once this lands; there is no
  "leave it unset and assume zero rate" escape hatch.
- This still keeps the slice small: an enum, a field, validation, and
  tests — no interpolation, no conversion math, no new methodology
  decisions about *how* to convert a par rate, only *whether a given
  row's rate is already usable as-is or needs conversion later*.

**This is not a "the project needs a Trading Desk decision before any
code" situation** — naming a `PAR_RATE` vs. `CONTINUOUS_ZERO_RATE`
(etc.) taxonomy and requiring every curve point to declare one is a
data-contract decision this codebase's own AI-agent-authorable
conventions can make (mirroring `BLICurvePurpose`'s existing four-member
enum, itself introduced the same way per docs/23). The *conversion
formula's* precise edge-case behavior (Path C) is the piece that may
still need Trading Desk sign-off later — but that is explicitly not
what this doc recommends implementing next.

---

## 7. Scope for the recommended next code PR (not implemented here)

```text
Suggested branch:     claude/bli-curve-rate-basis-contract
Suggested PR title:   Add BLI curve rate-basis contract
```

**Target files:**

```text
src/shiori_pricing_lab/data/bli_snapshot.py
  -- new BLICurveRateBasis(StrEnum) with members such as
     CONTINUOUS_ZERO_RATE / SIMPLE_ZERO_RATE / PAR_RATE / SWAP_RATE /
     BOND_YIELD / FUNDING_RATE / OTHER (exact member set decided in
     that PR, not here -- naming a taxonomy is itself a small design
     choice that PR should make explicit and justify, not silently
     copy from this doc's illustrative list).
  -- new required field on BLICurvePoint, e.g. rate_basis:
     BLICurveRateBasis, validated via coerce_enum in __post_init__
     (mirroring curve_purpose's existing pattern) -- no default value.

src/shiori_pricing_lab/data/bli_snapshot_fixtures.py
  -- every existing BLICurvePoint(...) construction site updated to
     supply an explicit rate_basis (this is the "migration/fixture
     consideration" -- see below).

tests/test_bli_market_data_snapshot.py (or a new, narrowly-scoped
tests/test_bli_curve_rate_basis.py)
  -- coercion/validation tests for the new enum and field, mirroring
     BLICurvePurpose's existing test pattern.

(development log and runbook entries -- removed, see git history)
```

**Migration/fixture considerations:** every existing `BLICurvePoint`
construction site — the synthetic fixture (§2.2 above) and any test
that hand-builds a `BLICurvePoint` (e.g. the tenor-sort test in
`tests/test_bli_curve_selector.py`) — must be updated to supply the new
required field. Since the field would be required, this is a
compile/construction-time break, not a silent behavior change — every
call site either supplies a value or the test suite fails loudly at
construction, which is the intended, safe failure mode for a required
field with no default.

**Tests:**

```text
- BLICurveRateBasis coerces from both an enum member and a valid raw
  string (mirroring BLICurvePurpose's own coercion test pattern).
- an unsupported/blank rate_basis string raises ValueError, listing
  allowed members (coerce_enum's existing behavior, exercised for the
  new enum).
- BLICurvePoint construction without rate_basis raises (TypeError for
  a missing required dataclass field, or a clear message if a
  transitional default-then-reject pattern is chosen instead -- that
  PR's own decision, not this doc's).
- every existing SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT curve point
  construction still succeeds once rate_basis is supplied for each --
  no new fixture *content* is needed beyond adding this one field's
  value per existing row.
- module/field-boundary test: rate_basis being present does not, by
  itself, imply anything about interpolation -- i.e. this PR still does
  not add or call any interpolation/discount-factor-shaped function
  (mirroring the module-boundary test pattern already used in
  tests/test_bli_curve_tenor.py and tests/test_bli_curve_selector.py).
```

**Explicit non-goals for that future PR:** identical to §8 below, minus
the "no rate-basis enum"/"no rate-basis field" items (which are exactly
what that PR *would* add) -- restated in that PR's own body, not
predicted further here.

**Codex review checklist for that future PR:**

```text
[ ] Is rate_basis required (no default), so no BLICurvePoint can be
    constructed without an explicit basis?
[ ] Does the enum's member set avoid conflating the zero-vs-par axis
    with the compounding-convention axis (e.g. is a continuously-
    compounded zero rate distinguishable from a simple-compounded one,
    if that distinction matters to Annex A elsewhere)?
[ ] Are all existing fixture/test construction sites updated, with no
    silent default slipped in to avoid updating them?
[ ] Does this PR avoid adding any interpolation, discount-factor, or
    par-to-zero conversion logic -- confirmed via a module-boundary
    test mirroring the existing pattern?
[ ] Is price_bli_mvp's return value byte-for-byte unchanged before/after
    this PR for the existing SYNTHETIC_BLI_MVP_INPUT_BUNDLE fixture?
[ ] Do pricing/bli_pricing_engine.py, pricing/bli_valuation_time.py,
    pricing/bli_curve_tenor.py, and pricing/bli_curve_selector.py
    remain unmodified?
[ ] Are the tests deterministic (no randomness, no wall-clock reads)?
```

---

## 8. Error and validation boundaries for future code (not implemented
by this PR)

```text
- Missing rate basis: if Path A is adopted, this becomes structurally
  impossible for a new BLICurvePoint (required field, no default) --
  but any future interpolation helper reading an *already-constructed*
  BLICurvePoint from data it does not fully trust (e.g. a future
  ingestion path outside this repo's own construction sites) must still
  treat an unexpected missing/None rate_basis as a hard error, never a
  silent default to "assume zero rate."
- Unsupported rate basis: an unrecognized string value must raise at
  construction (coerce_enum's existing behavior), never silently map to
  the nearest known member.
- Basis inconsistent with future zero-rate interpolation (e.g.
  rate_basis is PAR_RATE, SWAP_RATE, BOND_YIELD, FUNDING_RATE, or
  OTHER): a future interpolation helper must raise/refuse rather than
  interpolate -- interpolation is only valid once a row's basis is
  CONTINUOUS_ZERO_RATE (or, if a separately reviewed conversion slice
  exists per Path C, only after that conversion has actually run and
  produced a new, explicitly-zero-rate value -- never by interpolating
  a par rate "as if" it were already a zero rate).
- Mixed basis inside one selected curve (e.g. select_curve_points_by_
  purpose returns rows where some carry CONTINUOUS_ZERO_RATE and others
  carry PAR_RATE for the same curve_id): a future interpolation helper
  must raise -- interpolating across rows of different, unreconciled
  bases would silently produce a meaningless blended value. This is a
  new check a future helper needs; existing validation
  (_validate_curve_points, docs/27 §2.2) does not check basis
  consistency because the field does not exist yet.
- Raw string coercion if a basis enum is added: mirrors coerce_enum's
  existing behavior across every other BLI enum (BLICurvePurpose,
  BLIMarketDataStatus, BLIVolatilityBasis, ...) -- accept an existing
  enum member as-is, coerce a valid raw string, reject blanks/unknowns
  with a message listing the allowed options.
- Fixture compatibility: every existing SYNTHETIC_BLI_MARKET_DATA_
  SNAPSHOT curve point needs an explicit rate_basis value supplied by
  that future PR -- this doc does not choose those values (that is a
  real, reviewable claim about the existing fixture's data, not a
  mechanical rename, and belongs in that PR's own body/tests, not
  guessed at here).
- Audit/report implications: once rate_basis exists, any future
  Internal Pricing Report (Annex A §A.12, per docs/26/§27's citations)
  that surfaces which curve/rate was used for a valuation should also
  be able to surface the basis that was assumed -- not implemented by
  this doc or the recommended next PR, but worth the future PR noting
  as a downstream consumer of the new field.
- How price_bli_mvp should behave while basis is not ready: unchanged
  from docs/26 §8 and docs/27 §10 -- keep returning the deterministic
  PricingResult(status=FAILED, errors=[PricingErrorCode.
  UNSUPPORTED_PRODUCT]) for every valid bundle. Adding a rate_basis
  field (even once landed) does not, by itself, give price_bli_mvp
  anything new to dispatch on -- there is still no interpolation, no
  discount factor, and no PV computation behind it.
```

---

## 9. What must remain out of scope for this PR

```text
no modification to src/
no modification to tests/
no rate-basis enum added
no rate-basis field added
no fixture changes
no par-to-zero conversion implementation
no source-to-zero conversion implementation
no curve interpolation implementation
no discount-factor implementation
no forward clean price
no coupon/cash-flow schedule
no accrued interest
no volatility surface / conversion
no yield-to-price / price-to-yield conversion
no Black-76
no PV
no Greeks / DV01 / CS01
no wiring of anything into price_bli_mvp
no change to price_bli_mvp
no QuantLib adapter
no Bloomberg/API connector
no FTP ingestion
no UI / debug viewer
no edits to SPEC_v1.3.md / ANNEX_A_v1.3.md / ANNEX_B_v1.3.md /
  ANNEX_C_v1.3.md
no closing of issue #38
```

---

## 10. Fresh-session handoff

A new Claude Code session picking up the actual next implementation PR
(§7) should read, in this order:

```text
1. This doc (docs/28_bli_curve_rate_basis_preflight.md).
2. docs/27_bli_curve_interpolation_preflight.md §6.1/§9.2 -- the
   original statement of the curve-rate-basis blocker and the four
   candidate paths this doc evaluates in full.
3. src/shiori_pricing_lab/data/bli_snapshot.py -- BLICurvePoint,
   BLICurvePurpose, and the existing coerce_enum-based validation
   pattern this doc's recommended rate_basis field would mirror.
4. src/shiori_pricing_lab/data/bli_snapshot_fixtures.py -- confirm
   exactly which existing construction sites would need a rate_basis
   value supplied.
5. src/shiori_pricing_lab/pricing/bli_curve_tenor.py and
   bli_curve_selector.py -- confirm both remain untouched by the
   rate-basis question and are not expected to change when it lands.
6. docs/bond_linked_structured_pricer/ANNEX_A_v1.3.md §A.10.2/§A.10.3 --
   the exact interpolation-method pin and the par/zero-curve handling
   language this doc's §3 quotes and analyzes.
```

The actual implementation PR described in §7 is **not started by this
doc**. Issue #38 remains open.
