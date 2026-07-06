# 27 BLI Curve Interpolation Preflight

Status: docs-only preflight. No curve interpolation, discount-factor
calculation, tenor parser, curve-point selector, forward-price
derivation, coupon schedule, accrued interest, volatility conversion,
yield-to-price conversion, Black-76, PV, QuantLib adapter, Bloomberg/API
connector, FTP ingestion, or UI is added by this doc. No source file
under `src/` and no test file under `tests/` is modified.
`year_fraction_to_expiry`/`year_fraction_to_bond_option_expiry`
(`pricing/bli_valuation_time.py`, PR #70) are not wired into anything by
this doc, and `price_bli_mvp`'s runtime behavior is unchanged. No frozen
BLI v1.3 source spec file (`SPEC_v1.3.md`, `ANNEX_A_v1.3.md`,
`ANNEX_B_v1.3.md`, `ANNEX_C_v1.3.md`) is edited. Issue #38 is unaffected
and remains open.

---

## 1. Where this picks up

PR #70 (merged, `4f99b61`) landed `pricing/bli_valuation_time.py`:
`year_fraction_to_expiry(valuation_date: str, expiry_date: str) -> float`,
a pure ACT/365F helper (Annex A §A.2.2), plus a bundle-reading
convenience wrapper `year_fraction_to_bond_option_expiry`. It resolved
`docs/26`'s dependency 1 (time-to-expiry). It is not wired into
`price_bli_mvp`, and `bli_pricing_engine.py` is untouched.

`docs/26_bli_first_valuation_slice_preflight.md` §4 lists dependency 2
as the next genuinely-missing piece: **curve interpolation /
discount-factor access from a `BLICurvePoint` collection** (Annex A
§A.10.2), needed for both the Option Discount Curve (discounting the
option PV, §A.2.2's `DF`) and the Bond Reference Curve (discounting
coupons for the forward price, §A.5.3). `docs/26` §4 item 2 already
flagged that `pricing/curve.py::RateCurve` is not built from
`BLICurvePoint` objects and is a structurally different shape — but did
not decide the exact reuse/adapter question, the tenor-format scope, the
interpolation-vs-discount-factor split, or the error/validation
boundary. This doc answers those questions for BLI curve access, without
implementing any of them, mirroring `docs/26`'s own "contract before
methodology" discipline for this next dependency.

---

## 2. Existing BLI curve inputs (required question 1)

Everything below already exists, is already validated by
`BLICurvePoint.__post_init__` / `BLIMarketDataSnapshot.__post_init__` /
`BLIMVPInputBundle.__post_init__`, and requires no new fixture content.
Nothing in this section is invented.

### 2.1 `BLICurvePoint` (`data/bli_snapshot.py`)

One tenor/rate row of a named curve:

```text
curve_id         str   -- non-blank (data/_validation._require_non_blank)
curve_name       str   -- non-blank
currency         Currency
curve_purpose    BLICurvePurpose  -- BOND_REFERENCE_CURVE / OPTION_DISCOUNT_CURVE /
                                     DEPOSIT_CURVE / FUNDING_CURVE
tenor            str   -- non-blank, a bare label (e.g. "2Y", "3M"); no
                          parser, no enum -- unlike TreasuryFTPTenor (an
                          enum used only for deposit-rate observations),
                          BLICurvePoint.tenor is a free-form string
rate             float -- any finite number, sign unconstrained ("yields/
                          rates may legitimately be signed" -- see
                          `_require_finite_number` call in
                          `BLICurvePoint.__post_init__`); no positivity
                          check exists today
source_system    str   -- non-blank
status           BLIMarketDataStatus -- must be ACTIVE at construction
```

A curve is a **collection** of these rows sharing `curve_id`, one row
per tenor (docs/23 §12) — not one row per curve. Repeated
`currency`/`curve_purpose` across rows with different `tenor` is
expected and valid.

### 2.2 `BLIMarketDataSnapshot.curve_points` validation already performed
(`data/bli_snapshot.py::_validate_curve_points`, called from
`BLIMarketDataSnapshot.__post_init__`)

```text
- curve_points must not be empty (non-empty tuple guaranteed).
- every element must be a BLICurvePoint instance.
- duplicate node rejection, keyed by (curve_id, tenor): the same
  (curve_id, tenor) pair appearing twice raises ValueError -- whether
  the repeated rate agrees (flagged as "duplicate curve node") or
  conflicts (flagged as "conflicting rate").
- ambiguous curve identity rejection: two different curve_ids claiming
  the same (currency, curve_purpose) pair raises ValueError ("no
  explicit mapping rule to choose between them"). A single curve_id may
  freely have many (tenor, rate) rows under one (currency, curve_purpose).
```

**What this does *not* already guarantee:** tenor rows within one
`curve_id` are not required to be sorted, not required to be unique in
count (a curve could legally have only one tenor row), and are not
checked for parseability into a year fraction — `tenor` is validated
only as a non-blank string.

### 2.3 `BLIMVPInputBundle` curve-purpose presence gate
(`data/bli_mvp_input_bundle.py::_require_mvp_curve_purposes`)

```text
_REQUIRED_MVP_CURVE_PURPOSES = {
    BLICurvePurpose.BOND_REFERENCE_CURVE,
    BLICurvePurpose.OPTION_DISCOUNT_CURVE,
    BLICurvePurpose.DEPOSIT_CURVE,
}  # FUNDING_CURVE excluded -- required only if a future mapping calls
   # for it, which none does yet (docs/24 §6).
```

For each required purpose, at least one `curve_points` row must exist
**in the product's own currency** (`product.bond_option.currency`) —
a same-purpose row in a different currency does not satisfy the gate.
This is **presence only** — "at least one row of this purpose in this
currency exists somewhere in `curve_points`" — never tenor-node
selection, never interpolation, never uniqueness-of-`curve_id`-per-
purpose beyond the ambiguity check in §2.2. A single `curve_id` can
supply all required purposes' rows, or three different `curve_id`s can,
as long as no two different `curve_id`s claim the same
`(currency, curve_purpose)` pair (§2.2's ambiguity rule already forbids
that).

### 2.4 Bundle-level dates already available

```text
bundle.valuation_date                         str, YYYY-MM-DD
bundle.market_data_snapshot.as_of_timestamp    str (bare date / naive
                                                     datetime / UTC datetime)
```

`year_fraction_to_expiry` (PR #70) already computes ACT/365F between any
two ISO date strings — including, mechanically, `valuation_date` and a
future bond-option expiry date. Nothing new is needed here for a "time
to a curve tenor" calculation once a tenor is converted to a year
fraction; the open question is only how a bare tenor string like `"2Y"`
maps to that year fraction (see §5).

### 2.5 Existing synthetic fixture curve shape
(`data/bli_snapshot_fixtures.py`)

```text
USD_BOND_REFERENCE_CURVE:  tenor="2Y" rate=0.0362, tenor="5Y" rate=0.0375
USD_OPTION_DISCOUNT_CURVE: tenor="2Y" rate=0.0341, tenor="5Y" rate=0.0353
USD_DEPOSIT_CURVE:         tenor="3M" rate=0.0350                (one row only)
```

Two of the three required MVP curve purposes have exactly two tenor
rows each (`"2Y"`/`"5Y"`); the Deposit Curve has exactly **one** tenor
row (`"3M"`). This matters for §5/§6 below: any interpolation
implementation must have a defined behavior for a curve with only one
tenor point (there is nothing to interpolate *between*), not just for
curves with two or more points — and the existing fixture already
exercises that single-point case for `DEPOSIT_CURVE`, so a future test
suite does not need to invent a new fixture to cover it.

### 2.6 What is **not** in this list (do not invent)

No tenor parser, no year-fraction-from-tenor conversion, no curve-point
selector-by-purpose helper, no interpolation function, no discount
factor, and no BLI-specific `RateCurve` equivalent exist anywhere in
this codebase today. `BLICurvePoint.tenor` is validated only as a
non-blank string; nothing parses it into a year fraction, and nothing
sorts or dedupes tenor rows by year fraction.

---

## 3. Existing reusable code (required question 2)

**`pricing/curve.py::RateCurve` is not directly reusable without an
adapter — its two most relevant pieces are shaped for a different
input, but its `tenor_to_years` helper's *tenor label vocabulary* is a
useful reference, not a drop-in dependency.**

### 3.1 `RateCurve` itself

`RateCurve.from_rates_points`/`from_snapshot` build from a
`pandas.DataFrame` of rows with `date`/`tenor`/`value`/`source` columns,
sourced from the unrelated vanilla-rates-core
`data.snapshot.MarketDataSnapshot`. `BLICurvePoint` is a frozen
dataclass, not a DataFrame row, and `BLIMarketDataSnapshot.curve_points`
is a `tuple[BLICurvePoint, ...]`, not a DataFrame. Converting a
`BLICurvePoint` tuple into the DataFrame shape `RateCurve` expects
(`date`, `tenor`, `value`, `source` columns, one `date` value shared by
all rows) would require inventing new field-mapping/DataFrame-
construction code purely to satisfy `RateCurve`'s constructor shape —
that adapter code would itself be new curve-adjacent logic, not
"reusing `RateCurve` directly." **Conclusion: not directly reusable
without an adapter; the mismatch is the input container type (DataFrame
rows vs. `BLICurvePoint` dataclass instances) and the fact that
`RateCurve` has no notion of `curve_purpose` at all** — a BLI curve
selection needs to filter by `(currency, curve_purpose)` first, which
`RateCurve` has no field for.

### 3.2 `RateCurve.shocked_parallel` / curve construction from a `date`
+ points argument

Not relevant to interpolation lookup itself (it is a scenario-shock
helper); not evaluated further here.

### 3.3 `tenor_to_years` / `_TENOR_TO_YEARS` (module-level in
`pricing/curve.py`, not a `RateCurve` method)

```python
_TENOR_TO_YEARS = {
    "1M": 1/12, "3M": 0.25, "6M": 0.5, "1Y": 1.0, "2Y": 2.0, "3Y": 3.0,
    "5Y": 5.0, "7Y": 7.0, "10Y": 10.0, "20Y": 20.0, "30Y": 30.0,
}

def tenor_to_years(tenor: str) -> float:
    if tenor not in _TENOR_TO_YEARS:
        raise ValueError(f"Unsupported tenor: {tenor}")
    return _TENOR_TO_YEARS[tenor]
```

This is a plain `str -> float` function with **no dependency on
`RateCurve`, no dependency on the vanilla-rates-core
`MarketDataSnapshot`, and no dependency on a DataFrame** — its input
shape (`"2Y"`, `"3M"`, ...) is exactly `BLICurvePoint.tenor`'s shape,
and its supported label set already covers every tenor the existing BLI
fixture uses (`"3M"`, `"2Y"`, `"5Y"`). **This function is a strong
candidate for direct reuse *by call*, not by inheritance or DataFrame
coercion** — a future BLI tenor-parsing slice could import and call
`pricing.curve.tenor_to_years` directly instead of redefining an
equivalent mapping. Whether to actually do that (vs. defining a
BLI-local copy to keep `pricing/bli_valuation_time.py`'s sibling module
free of any import from the vanilla-rates-core curve module) is **left
as an open implementation-time choice for the next PR**, not decided
here — both are small, and this doc does not need to force the answer
before seeing the tenor list the next slice actually needs (§5 may need
tenor labels `tenor_to_years` does not have, e.g. if a future fixture
adds a `"9M"` row; `TreasuryFTPTenor`, a *different, enum-typed* tenor
vocabulary used only for deposit-rate observations, already has
`"9M"`/`"O/N"`/`"1W"`/`"2W"`/`"3W"` labels `tenor_to_years` lacks).

### 3.4 `irs_engine.py::_discount_factor`/`_zero_rate`

`irs_engine.py` already has a working discount-factor-from-curve-points
pattern (`_discount_factor(years, curve_points)` /
`_zero_rate(years, curve_points)`), but it is **not the same convention
Annex A requires**: `irs_engine.py::_discount_factor` computes
`1.0 / (1.0 + rate * years)` (simple, non-continuous compounding) over
`curve_points: tuple[tuple[float, float], ...]` (bare `(years, rate)`
pairs, no `curve_purpose`/`currency`/`tenor`-label concept at all).
Annex A §A.10.2 requires **piecewise linear interpolation on zero rates,
continuously compounded** for BLI. Reusing `irs_engine.py`'s discount
formula as-is for BLI would silently apply the wrong compounding
convention — this is exactly the kind of "two existing, structurally
similar but methodologically different patterns" mismatch this doc
exists to flag before code is written, not a "just reuse it" case. Its
*interpolation shape* (linear between the two nearest `(years, rate)`
points, flat beyond the ends — see `_zero_rate`'s clamping at
`curve_points[0]`) is a useful structural reference for the linear-
interpolation slice in §5, but its discount-factor formula is not reused
for BLI without a compounding-convention fix, which is exactly why §6
below keeps discount-factor computation out of the next implementation
slice.

---

## 4. Curve purpose boundary (required question 3)

**Decided (docs-only; no code changes here):**

- **Discounting curve purpose selection:** Annex A ties each purpose to
  a specific use — `OPTION_DISCOUNT_CURVE` discounts the option PV
  (§A.2.2's `DF`), `BOND_REFERENCE_CURVE` discounts coupons for the
  forward clean price (§A.5.3, not this slice), `DEPOSIT_CURVE`
  discounts/rates the deposit leg (out of scope, §2.2 of `docs/26`).
  The future curve-point-selector implementation must take an explicit
  `curve_purpose` (and `currency`) argument — it must never guess or
  default to a purpose, and must never silently substitute one purpose's
  points for another's (`docs/23`'s own rule: "Option Discount Curve and
  Bond Reference Curve must never be mixed").
- **One unambiguous curve-purpose subset required:** yes. A selector
  must filter `curve_points` down to exactly the rows matching the
  requested `(currency, curve_purpose)` pair before doing anything else
  (tenor parsing, sorting, interpolation). `BLIMVPInputBundle`'s
  existing ambiguity check (§2.2 above) already guarantees at most one
  `curve_id` claims a given `(currency, curve_purpose)` pair inside one
  bundle, so a selector filtering by that pair can safely treat its
  result as "one curve's rows," not "rows from several competing
  curves" — but the selector itself must still perform the filter; it
  must not assume it is only ever called with pre-filtered input.
- **Missing required curve-purpose points:** must raise, never return an
  empty result or fall back to a different purpose/currency. (Today,
  `BLIMVPInputBundle.__post_init__` already blocks bundle construction
  if a required purpose/currency is entirely absent — see §2.3 — so a
  selector operating on an already-valid bundle should only see this
  case if it is asked for a purpose the bundle does not require, e.g.
  `FUNDING_CURVE`; the selector must still raise rather than assume the
  bundle-level gate makes every possible query safe.)
- **Duplicate tenor / duplicate curve-purpose points:** already rejected
  at `BLIMarketDataSnapshot` construction (§2.2's duplicate-node and
  ambiguous-curve-identity checks) — a future selector operating on an
  already-constructed, already-valid snapshot does not need to
  re-implement that rejection, but should not assume it is safe to skip
  validating its own narrower input either (defensive re-checking vs.
  trusting the bundle's prior gate is an implementation-time choice, not
  decided here).
- **Multiple curve purposes present:** normal and expected — one
  snapshot's `curve_points` holds rows for all required purposes
  together (see the fixture in §2.5). A selector's job is precisely to
  narrow "all curve points in the snapshot" down to "the rows for one
  requested purpose," not to reject a snapshot for containing more than
  one purpose.

None of this selection/filtering logic is implemented by this PR.

---

## 5. Tenor parsing boundary (required question 4)

**Decided (docs-only):**

- **Minimal supported formats for the first implementation slice:** `M`
  (months) and `Y` (years) suffixed integer tenors only — e.g. `"1M"`,
  `"3M"`, `"6M"`, `"1Y"`, `"2Y"`, `"5Y"`, `"10Y"`. This already covers
  every tenor label the existing synthetic fixture uses (`"3M"`, `"2Y"`,
  `"5Y"`, §2.5) and matches `pricing/curve.py::tenor_to_years`'s existing
  label vocabulary (§3.3), so no new tenor label needs inventing to
  exercise the first slice against real fixture data.
- **Parse tenor to year fraction via a tiny helper:** yes — a pure
  `str -> float` function, structurally: match a leading integer plus a
  single trailing `M` or `Y` character, then divide/multiply
  accordingly (`months / 12.0`, `years * 1.0`). No calendar, no
  day-count convention inside the tenor parser itself (day-count only
  matters once a rate is looked up against an actual valuation-date-to-
  maturity year fraction, which is the interpolation step in §6, not the
  tenor-label parse itself).
- **Explicitly out of scope for the first tenor-parsing slice:**
  business-day calendars, holiday calendars, actual settlement dates,
  IMM dates, week-based tenors (`"1W"`/`"2W"`/`"3W"`, part of the
  separate `TreasuryFTPTenor` deposit-rate vocabulary, not
  `BLICurvePoint.tenor`), overnight (`"O/N"`), and any tenor label not
  matching the strict `M`/`Y` shape (e.g. a malformed `"2 years"` or a
  numeric-only `"24"`) — all of these must raise a clear error, never be
  silently guessed at or rounded to the nearest supported label.
- **Reject clearly:** an unsupported tenor shape must raise `ValueError`
  with the offending string in the message — mirroring
  `pricing/curve.py::tenor_to_years`'s existing "Unsupported tenor: …"
  precedent and `pricing/bli_valuation_time.py::_parse_iso_date`'s
  strict-shape-then-raise pattern (PR #70's Codex P2 fix) rather than
  returning `0.0`, `None`, or silently falling back to a nearby tenor.

No tenor parser is implemented by this PR.

---

## 6. Interpolation method boundary (required question 5)

**Decided (docs-only):**

- **Smallest interpolation method for the first slice: linear
  interpolation on tenor year fractions, over zero rates.** Annex A
  §A.10.2 pins this exactly: "piecewise linear on zero rates
  (continuously compounded)." There is no "which of two reasonable
  approaches" ambiguity here the way there was for, e.g., forward-price
  cost-of-carry — Annex A already names the method.
- **Explicitly excluded from the first interpolation slice:**
  - spline interpolation (not what Annex A specifies — would be a
    silent methodology deviation, not a simplification);
  - bootstrapping / curve construction (Annex A §A.10.3: MVP does not
    build its own bootstrapping engine; a `BLICurvePoint` row is already
    assumed to be a usable zero-rate node, not a par-curve input needing
    par→zero conversion — that par→zero conversion, if ever needed, is
    separately scoped in §A.10.3 and not part of this dependency);
  - a multi-curve framework (one purpose's curve is interpolated in
    isolation; no cross-curve basis-spread logic);
  - **extrapolation beyond the curve's tenor range: explicitly decided
    to defer, not silently included.** Annex A §A.10.2 does pin flat
    extrapolation with a fallback flag ("超出 curve 範圍：flat
    extrapolation，並標示 fallback flag") — but "flag fallback" implies a
    result/audit shape (a flag alongside the interpolated value) that
    does not exist anywhere in this codebase's BLI types yet, and the
    existing `DEPOSIT_CURVE` fixture (§2.5) has only **one** tenor row,
    so *any* target maturity other than exactly `"3M"` is technically
    "outside the curve's range" for that purpose today. The first
    implementation slice should therefore explicitly decide (not
    silently assume) whether flat-extrapolation-without-a-flag is
    acceptable for its own first cut, or whether it should raise for any
    target outside `[min tenor, max tenor]` until a flagging mechanism
    exists — **this doc recommends raising** for out-of-range targets in
    the first slice (see §7), leaving flat extrapolation + fallback
    flagging for a following slice once there is a real caller
    (forward-price derivation) that needs a value rather than an error;
  - day-count variants beyond ACT/365F (Annex A §A.10.2 already pins
    ACT/365F for curve-internal year-fraction calculations — no other
    convention is in scope);
  - compounding variants: Annex A already pins continuous compounding
    for the zero rate itself; nothing else is needed for interpolating
    the rate value (compounding only becomes relevant again once a
    discount factor is computed from the interpolated rate — see §6.1
    below, deferred).

### 6.1 Discount-factor boundary (required question 6)

**Decided: the next implementation slice should compute only the
interpolated zero rate — not a discount factor.**

Reasoning for being conservative here even though Annex A §A.10.2 does
name continuous compounding for the zero rate:

- Interpolation (tenor parsing + curve-point selection by purpose +
  linear interpolation between two zero-rate nodes) is already a full,
  reviewable slice on its own — bundling a discount-factor formula on
  top would repeat `docs/26`'s own lesson about not combining multiple
  still-separate methodology pieces into one PR.
- This codebase already has **two structurally similar but
  methodologically different** discount-factor precedents in the same
  repository: `irs_engine.py::_discount_factor` uses simple compounding
  (`1 / (1 + r·T)`, §3.4 above), while Annex A requires continuous
  compounding (`exp(-r·T)`) for BLI. Landing a BLI discount-factor
  function in the same PR as the interpolation helper raises the risk
  of the two ever being confused or accidentally shared — keeping them
  in separate, sequential slices makes the compounding-convention
  distinction an explicit, single-purpose PR's entire subject, not a
  side effect of a larger change.
- If a following PR does implement the BLI discount factor, the formula
  to review is already unambiguous from Annex A §A.10.2 + §A.2.2:
  `DF = exp(-zero_rate × T)`, where `zero_rate` is the interpolated
  continuously-compounded zero rate from this slice's helper and `T` is
  the ACT/365F year fraction from `pricing/bli_valuation_time.py`'s
  existing `year_fraction_to_expiry` (already landed, PR #70) — no new
  design decision would be needed for that follow-up PR's formula
  itself, only its own tests and error boundary.

No interpolation and no discount factor is implemented by this PR.

---

## 7. Error and validation boundaries (required question 7)

Decided future behavior, not implemented by this PR:

```text
- Empty curve points: cannot occur for an already-valid
  BLIMarketDataSnapshot (BLIMarketDataSnapshot.__post_init__ already
  requires curve_points to be non-empty, §2.2) -- but a future
  curve-point-selector filtering by (currency, curve_purpose) can still
  produce an empty *filtered* result (e.g. a purpose/currency the
  snapshot's rows do not cover). That must raise ValueError, never
  return an empty/None interpolation result.
- Missing target tenor/maturity exactly on the curve: not an error by
  itself -- interpolation (or, at the boundary, flat use of the nearest
  node) is expected to handle a target that does not exactly match an
  existing tenor node. Only a target outside the curve's tenor range is
  the open question resolved in §6 (recommend: raise in the first
  slice).
- Unsupported tenor format (on either a curve-point tenor label or, if
  ever needed, a caller-supplied target maturity string): raise
  ValueError with the offending string in the message (§5).
- Duplicate tenor: already rejected at BLIMarketDataSnapshot
  construction (§2.2); a selector need not re-detect this for an
  already-valid snapshot, though it may choose to defensively re-check
  its own narrower filtered subset (implementation-time choice, §4).
- Target maturity outside curve range: raise ValueError in the first
  slice (§6), rather than silently flat-extrapolating without a fallback
  flag mechanism. Flat extrapolation + fallback flagging is explicitly
  deferred to a later slice once a flagging shape exists.
- Mixed curve purposes: not an error at the snapshot level (expected,
  §4) -- a selector's job is to narrow to one purpose; mixing is only an
  error if a selector is ever asked to interpolate *across* rows from
  two different curve_purpose values without narrowing first, which
  should never be reachable if the selector always filters by purpose
  before interpolating.
- Non-numeric rate: cannot occur for an already-valid BLICurvePoint --
  BLICurvePoint.__post_init__ already requires rate to be a finite
  number via _require_finite_number (§2.1).
- Negative rates: already allowed at the BLICurvePoint level today (no
  sign constraint exists, §2.1, matching the existing comment "yields/
  rates may legitimately be signed"). A future interpolation helper must
  not introduce a new positivity check that the underlying type does not
  itself enforce -- rejecting a negative rate at the interpolation layer
  when BLICurvePoint itself allows it would be a silent, undocumented
  narrowing of an already-decided contract.
- Valuation date / market snapshot mismatch: already fully guarded by
  BLIMVPInputBundle.__post_init__ before a bundle can exist (currency
  coherence, curve-purpose presence, as-of/valuation-date no-look-ahead
  check -- docs/24 §6, restated in this bundle's own module docstring).
  A future curve-interpolation helper operating on an already-valid
  bundle's market_data_snapshot does not need to re-implement any of
  that; it only needs its own tenor-shape and range checks (above).
```

None of these errors are implemented by this PR.

---

## 8. What must remain out of scope

Restated as an acceptance-criteria checklist for the next implementation
PR (§9):

```text
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
no scenario engine
no bootstrapping / curve construction (Annex A §A.10.3 territory)
no multi-curve basis-spread framework
no flat-extrapolation-with-fallback-flag mechanism (deferred, §6)
no wiring of year_fraction_to_expiry into price_bli_mvp or anything else
no change to price_bli_mvp
no QuantLib adapter
no Bloomberg/API connector
no FTP ingestion
no UI / debug viewer
no fake numeric outputs of any kind
```

---

## 9. Which missing dependency should be implemented first, and what
should the next implementation PR look like?

**Chosen: the `BLICurvePoint` tenor parser only** — a pure
`str -> float` helper converting a bare tenor label (`"1M"`/`"3M"`/
`"6M"`/`"1Y"`/`"2Y"`/`"5Y"`/`"10Y"`, per §5) into a year fraction,
mirroring `pricing/curve.py::tenor_to_years`'s existing shape and label
set, but scoped to this BLI-specific module rather than importing from
the unrelated vanilla-rates-core curve module (or, alternatively,
calling `pricing.curve.tenor_to_years` directly — an open, small
implementation-time choice per §3.3, not decided by this doc).

### 9.1 Why this one, and why it is small enough

- **Zero design ambiguity beyond the label set already decided in §5.**
  There is no interpolation-method choice, no curve-purpose-selection
  logic, and no discount-factor formula involved — just a label-to-
  fraction mapping.
- **Already has a reviewed precedent in this codebase**:
  `pricing/curve.py::tenor_to_years` implements this exact pattern
  (dict lookup, raise on unsupported label) for the unrelated vanilla-
  rates-core curve module. The BLI version is a small, mechanical
  adaptation, not new methodology.
- **Directly exercisable against the existing fixture** — no new
  fixture content needed (§2.5's `"3M"`/`"2Y"`/`"5Y"` tenors are already
  in the required label set).
- **Necessary but not sufficient input for every later step in this
  dependency chain** (curve-point selection by purpose, linear
  interpolation, discount factor) — landing it first, alone, keeps each
  later PR reviewable on its own single subject, mirroring `docs/26`'s
  own sequencing discipline.

### 9.2 Suggested next implementation PR

```text
Suggested branch:     claude/bli-curve-tenor-parser
Suggested PR title:   Add BLI curve tenor year-fraction parser
```

**Target files:**

```text
src/shiori_pricing_lab/pricing/bli_curve_tenor.py   (new)
  -- one pure function, e.g.
     tenor_to_year_fraction(tenor: str) -> float
  -- supports only strict "<int>M" / "<int>Y" shapes (§5); raises
     ValueError with the offending string for anything else (week
     tenors, "O/N", malformed strings, non-string input, zero/negative
     integers if that boundary is judged worth pinning explicitly).

tests/test_bli_curve_tenor.py                        (new)
```

**Expected tests:**

```text
- "1M" -> 1/12.0, "3M" -> 0.25, "6M" -> 0.5 (matching the existing
  DEPOSIT_CURVE fixture's "3M" tenor, §2.5).
- "1Y" -> 1.0, "2Y" -> 2.0, "5Y" -> 5.0, "10Y" -> 10.0 (matching the
  existing BOND_REFERENCE_CURVE/OPTION_DISCOUNT_CURVE fixture's "2Y"/
  "5Y" tenors, §2.5).
- unsupported forms raise ValueError: week tenors ("1W"/"2W"/"3W"),
  overnight ("O/N"), non-M/Y suffix, malformed strings ("2 years",
  "24"), non-string input.
- pure function performs no I/O, reads no BLICurvePoint/
  BLIMarketDataSnapshot/BLIMVPInputBundle, and never calls
  date.today()/datetime.now() (no date arithmetic at all -- this is a
  label parser, not a date calculation).
- module-boundary test (mirroring tests/test_bli_valuation_time.py's
  pattern): asserts no curve-selection/interpolation/discount-factor/
  forward-price-shaped name exists anywhere in the new module.
- optional: the function is exercised directly against every tenor
  label present in SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT.curve_points
  (`data/bli_snapshot_fixtures.py`) without constructing any new
  fixture content, proving it covers every tenor label the existing BLI
  fixture actually uses.
```

**Acceptance criteria:**

```text
- output matches the M/Y label set decided in §5 exactly, for every
  test case.
- unsupported tenor shapes raise (never return 0.0 or a guessed
  nearby value).
- no date.today()/datetime.now() anywhere (there is no date arithmetic
  in this slice at all).
- price_bli_mvp is untouched and still returns the same deterministic
  PricingResult(status=FAILED, errors=[PricingErrorCode.UNSUPPORTED_PRODUCT])
  for every valid bundle.
- pricing/bli_valuation_time.py is untouched (no wiring between the two
  utilities in this slice).
- no curve-point selection by (currency, curve_purpose), no
  interpolation, and no discount factor is introduced, even incidentally.
- tests reuse the existing SYNTHETIC_BLI_MVP_INPUT_BUNDLE /
  SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT fixtures for their tenor labels
  rather than fabricating new ad hoc curve-point instances, except where
  an error case genuinely needs an unsupported label (in which case
  construct only the minimal string input the pure function needs, not
  a new bundle/snapshot).
```

**Explicit non-goals:** identical to §8's list, restated in the PR body
per this repo's standing PR-description convention.

**Codex review checklist for that PR:**

```text
[ ] Does the function support exactly the M/Y tenor label set decided
    in §5, with no silent extension to week/overnight/other forms?
[ ] Are unsupported tenor shapes rejected (raise), never silently
    mapped to 0.0, None, or a nearby supported label?
[ ] Is date.today()/datetime.now() absent (trivially -- confirm no date
    arithmetic was added at all in this slice)?
[ ] Is price_bli_mvp's return value byte-for-byte unchanged before/after
    this PR for the existing SYNTHETIC_BLI_MVP_INPUT_BUNDLE fixture?
[ ] Does pricing/bli_pricing_engine.py / pricing/bli_valuation_time.py /
    pricing/result.py / pricing/errors.py / pricing/engine.py remain
    unmodified?
[ ] Does the new module import nothing from curve-selection/
    interpolation/discount-factor/forward-price/QuantLib/Bloomberg/UI
    code, and (if it does not call pricing.curve.tenor_to_years
    directly) does it avoid importing pricing/curve.py at all, keeping
    the BLI tenor vocabulary decision (§3.3) visibly self-contained?
[ ] Are the tests deterministic (no randomness, no wall-clock reads)?
[ ] Does the module-boundary test correctly reject curve-selection/
    interpolation/discount-factor-shaped names, mirroring
    tests/test_bli_valuation_time.py's existing pattern?
```

---

## 10. How should `price_bli_mvp` behave until real math is ready?

Unchanged from `docs/26` §8: keep returning the deterministic
`PricingResult(status=FAILED, errors=[PricingErrorCode.
UNSUPPORTED_PRODUCT])`. The next implementation slice (§9) is a single,
pure, unwired tenor-parsing utility — it does not touch
`bli_pricing_engine.py` at all, so there is nothing yet for
`price_bli_mvp` to dispatch on. A narrower dependency-gated dispatch
remains deferred until enough of `docs/26` §4's dependencies exist to
attempt a real PV.

---

## 11. Fresh-session handoff

A new Claude Code session picking up the actual next implementation PR
(§9) should read, in this order:

```text
1. This doc (docs/27_bli_curve_interpolation_preflight.md).
2. docs/26_bli_first_valuation_slice_preflight.md -- the full dependency
   list this doc's §1 continues from, and price_bli_mvp's unchanged
   "not implemented" behavior.
3. src/shiori_pricing_lab/pricing/bli_valuation_time.py -- confirm the
   existing, reviewed time-to-expiry utility this dependency chain
   already landed (PR #70), and its strict-parse-then-raise pattern
   (Codex P2 fix) this doc's §5/§9 mirrors for tenor labels.
4. src/shiori_pricing_lab/data/bli_snapshot.py -- BLICurvePoint,
   BLICurvePurpose, and _validate_curve_points -- confirm exactly what
   is already validated (§2) before designing any new selector logic.
5. src/shiori_pricing_lab/data/bli_mvp_input_bundle.py --
   _require_mvp_curve_purposes -- confirm the existing presence-only
   curve-purpose gate (§2.3/§4) before assuming a future selector needs
   to re-implement any of it.
6. src/shiori_pricing_lab/pricing/curve.py -- RateCurve and
   tenor_to_years -- confirm the reuse assessment in §3 (tenor_to_years
   is a candidate for direct reuse by call; RateCurve itself is not
   directly reusable without an adapter).
7. src/shiori_pricing_lab/pricing/irs_engine.py's _discount_factor/
   _zero_rate -- confirm the compounding-convention mismatch (§3.4/§6.1)
   that is the specific reason discount-factor computation is deferred
   past the next PR.
8. docs/bond_linked_structured_pricer/ANNEX_A_v1.3.md §A.10.2 -- the
   exact interpolation-method and extrapolation-convention pins this doc
   cites in §6.
```

The actual implementation PR described in §9 is **not started by this
doc**. Issue #38 remains open.
