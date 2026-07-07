# 29 BLI QuantLib Bond-Mechanics Adapter Preflight

Status: docs-only preflight. No adapter module, dependency-manifest
change, CI workflow change, curve interpolation change,
discount-factor change, forward-clean-price implementation, coupon
schedule engine, accrued-interest calculation, yield-to-price
conversion, volatility conversion, Black-76, PV, Greeks, or UI is
added by this doc. No source file under `src/` and no test file under
`tests/` is modified. `price_bli_mvp`'s runtime behavior is unchanged.
No frozen BLI v1.3 source spec file (`SPEC_v1.3.md`, `ANNEX_A_v1.3.md`,
`ANNEX_B_v1.3.md`, `ANNEX_C_v1.3.md`) is edited. Issue #38 is
unaffected and remains open.

---

## 1. Current repo state after PR #79

PR #79 (merged, `6f91cf2`) completed the curve-to-discount-factor
dependency chain this MVP has been building since `docs/26`:

```text
pricing/bli_curve_tenor.py               tenor_to_year_fraction
pricing/bli_curve_selector.py            select_curve_points_by_purpose
pricing/bli_zero_curve_nodes.py          build_continuous_zero_curve_nodes
pricing/bli_zero_rate_interpolation.py   interpolate_continuous_zero_rate
pricing/bli_discount_factor.py           continuous_discount_factor
pricing/bli_curve_discount_factor.py     discount_factor_from_continuous_zero_curve
pricing/bli_valuation_time.py            year_fraction_to_expiry /
                                          year_fraction_to_bond_option_expiry
```

`discount_factor_from_continuous_zero_curve(curve_points, *, currency,
curve_purpose, target_year_fraction) -> float` is the single
composition entrypoint: it chains selector → node-builder →
interpolator → discount-factor math, propagates every error each step
already raises, and rejects (does not flat-extrapolate) an
out-of-range target, per the PR #77 Codex review decision recorded in
`interpolate_continuous_zero_rate`'s own docstring. None of these
seven modules parses a coupon schedule, computes accrued interest, or
touches `BondReferenceData` at all — they only ever read
`BLICurvePoint` rows and date strings.

`pricing/bli_pricing_engine.py::price_bli_mvp` is still the docs/25
skeleton: for any valid `BLIMVPInputBundle` it returns
`PricingResult(status=FAILED, errors=[PricingErrorCode.
UNSUPPORTED_PRODUCT])`, unchanged since docs/25. No PR since has
touched it.

`reference_data/bond_reference_data.py::BondReferenceData` (docs/20)
carries `coupon` (a **decimal annual rate**, e.g. `0.0325`),
`coupon_frequency` (`Frequency`), `maturity_date`/`issue_date`/
`first_coupon_date`/`last_coupon_date` (ISO date strings, invariant
`issue_date < first_coupon_date <= last_coupon_date <= maturity_date`
enforced in `__post_init__`), `day_count` (`DayCount`:
`ACT_360`/`ACT_365_FIXED`/`THIRTY_360`/`ACT_ACT_ISDA`),
`business_day_convention`, `redemption_amount`, `yield_convention`
(`BondYieldConvention`), and `ex_dividend_days` (a non-negative `int`).
It validates only that these are individually well-formed static
terms — it does not generate a coupon schedule, does not detect an
irregular stub, and does not compute accrued interest; the module
docstring is explicit that schedule generation is future pricing-engine
work.

`data/bli_snapshot.py::BLIBondQuote` (docs/23) carries the market
observation side: `clean_price_per_100`, `yield_value`, and
`accrued_interest_per_100` (all optional individually, at least one of
price/yield required), each a plain, unconverted, as-observed number —
this schema never performs a yield-to-price or price-to-yield
conversion and never derives one field from another.

`data/bli_mvp_input_bundle.py::BLIMVPInputBundle` (docs/24) is the
single validated seam a future pricing entrypoint would read from:
`bundle_id`, `valuation_date`, `product`
(`BondLinkedStructuredProduct`), `resolved_bond_reference_data`
(`BondReferenceData`), `resolution_status`, `eligibility_reasons`,
`market_data_snapshot` (`BLIMarketDataSnapshot`). It performs no curve
interpolation, no yield/price conversion, and calls no pricing
function itself.

The eligible synthetic fixture used across this dependency chain
(`reference_data/fixtures.py::_SYNTHETIC_VANILLA_BULLET`, ISIN
`XS0000000001`) is: 3.25% (`coupon=0.0325`) semi-annual, `ACT_ACT_ISDA`
day count, `MODIFIED_FOLLOWING`, issue `2025-06-15`, maturity
`2030-06-15`, coupon dates on 6/15 and 12/15 by manual construction
(first `2025-12-15`, last `2029-12-15`), `redemption_amount=100.0`,
**`ex_dividend_days=1`** (not zero). The synthetic MVP input bundle's
positive path pairs this bond with `valuation_date="2026-07-01"` and a
bond option `expiry_date="2026-09-29"` — no coupon date falls inside
`(2026-07-01, 2026-09-29]`, so this specific fixture's forward-price
math would exercise the "zero coupons before expiry" branch of Annex A
§A.5.2 only. This is restated here because §7 below explicitly does not
rely on this fixture's dates to prove the adapter's coupon-before-expiry
behavior — a second, non-fixture date pair is required for that.

No QuantLib import exists anywhere in `src/` or `tests/` today (grep
-verified). `pyproject.toml` already declares
`[project.optional-dependencies] quant = ["QuantLib>=1.32"]`, unused by
any code yet. `.github/workflows/python-tests.yml` installs
`requirements.txt` + `pip install -e .` only — it does **not** install
the `quant` extra, so if an adapter shipped today its QuantLib-backed
tests would either fail on a missing import or silently skip in every
CI run.

---

## 2. Exact QuantLib adapter boundary

**QuantLib is scoped to bond mechanics only, and only these four
outputs:**

1. Coupon schedule generation — the set of coupon payment dates implied
   by `issue_date` / `first_coupon_date` / `last_coupon_date` /
   `maturity_date` / `coupon_frequency` / `business_day_convention`.
2. Coupon cashflow amounts — the per-100 cash amount of each scheduled
   coupon, from `coupon` (decimal rate) and `coupon_frequency`.
3. Accrued interest at one explicit, caller-supplied date — using
   `day_count` and the schedule from (1).
4. The clean/dirty arithmetic identity at one explicit date — this is
   not new QuantLib logic beyond (3); `dirty(d) = clean(d) +
   accrued_interest(d)` is arithmetic the adapter's caller can do
   itself once it has (3), so this is listed as a boundary output only
   to be explicit that no *other* clean/dirty mechanic (e.g. settlement
   invoicing, §A.7) is in scope.

**Calendar / business-day convention for coupon schedule generation
(Codex review of PR #80):** the first adapter slice generates and
returns **unadjusted** schedule dates only — QuantLib's `Schedule` is
built with `ql.NullCalendar()` (no holiday adjustment), regardless of
`BondReferenceData.business_day_convention`. `business_day_convention`
(`MODIFIED_FOLLOWING`, `FOLLOWING`, ...) is recorded on `BondReferenceData`
as a deal term only — that module's own docstring already states
"resolving it requires a holiday calendar, which is out of scope for
this schema" — and no reviewed holiday-calendar source (which markets'
holidays, which calendar library/data feed, how a currency maps to a
specific trading calendar) exists anywhere in this repo yet. Silently
picking a QuantLib built-in calendar (e.g.
`ql.UnitedStates(ql.UnitedStates.GovernmentBond)`) to honor
`business_day_convention` would fabricate a holiday-calendar decision
this codebase has never reviewed, for a market `currency` alone does
not uniquely determine. **The adapter must not do this.** Coupon
payment dates returned by the first slice are therefore exactly the
schedule's unadjusted calendar dates (`first_coupon_date`, then each
subsequent regular-frequency date, ..., `last_coupon_date`,
`maturity_date`) — the eligible fixture's coupon dates (6/15, 12/15)
happen not to fall on a weekend in this synthetic data, so unadjusted
and calendar-adjusted dates coincide for it *by construction of the
fixture*, not because the adapter applied any calendar logic.
Calendar-adjusted payment dates are out of scope until a separate,
reviewed calendar-source contract exists (e.g. a future preflight
deciding which calendar library/data feed is authoritative and how it
maps `currency`/`issuer` to a specific holiday calendar) — this doc
does not invent one now, and the next implementation PR must not
either.

**QuantLib is never given:**

- A curve, a discount factor, or any interpolation input/output.
- A volatility, a credit spread, or a strike/expiry.
- `BLIMVPInputBundle`, `BLIMarketDataSnapshot`, `PricingResult`, or
  any Annex-A pricing formula.
- The system clock. Every date the adapter touches is an explicit
  caller-supplied ISO date string; if QuantLib's `Settings.
  evaluationDate` global must be set to make a calculation work, the
  adapter sets it from the caller's argument immediately before use
  and does not read or default from `date.today()`/`datetime.now()`
  anywhere.

**QuantLib does not price the option in MVP, full stop.** Annex A
§A.2.3 is a six-line closed-form Black-76 on forward clean price;
Annex A §A.13 (self-validation: closed-form-vs-bump-and-revalue,
put-call parity) presumes the desk owns that formula outright.
Wrapping `ql.BlackCalculator` or any QuantLib option-pricing engine
would convert the MVP's single most audit-sensitive number into a
library call this desk cannot re-derive by hand, which directly
contradicts `AGENTS.md`'s "pricing results must come from deterministic
pricing engines" framed as *this project's own* engines, and
contradicts §A.13's whole premise (there is no independent Quant team
to sign off on a black-box result). No exception is proposed for any
later MVP stage either — American trees (§A.4), yield-space Black-76
(§A.3), and the option payoff itself all stay project-owned.

---

## 3. Why the existing curve helpers stay custom

Three independent reasons, each sufficient alone:

- **Annex A §A.10.2 already pins a specific, non-default methodology**
  — piecewise-linear interpolation on continuously-compounded zero
  rates, with out-of-range targets requiring a fallback flag rather
  than silent extrapolation. `ql.ZeroCurve`/`ql.LogLinear` interpolants
  default to different conventions and would either require QuantLib
  configuration contortions to match §A.10.2 exactly or would silently
  diverge from it. Rebuilding this chain on QuantLib does not reduce
  methodology work; it hides it inside library configuration instead of
  this repo's own reviewed code.
- **The chain is already implemented, tested, and Codex-reviewed**
  across PRs #72–#79, including a specific, deliberate correction (PR
  #77: reject out-of-range targets instead of flat-extrapolating,
  because a fallback-flag contract does not exist yet). Replacing it
  with a QuantLib curve object would reopen that already-settled
  review decision for no stated benefit.
- **`SPEC_v1.3.md` §5.4.1's "QuantLib 處理 bond cash flow、curve、day
  count、calendar" is a suggested-architecture table, not frozen
  methodology** — the section header itself states "本節為建議架構，實際
  技術棧需由 IT architecture review 確認，不可視為替行內 IT 標準做最終決定"
  (this section is a suggested architecture; the actual stack requires
  IT architecture review and must not be treated as a final decision on
  behalf of internal IT standards). Annex A (the authoritative,
  `authoritative: true` pricing methodology document) never mentions
  QuantLib and instead spells out the interpolation method itself. This
  doc treats Annex A's explicit method as controlling and treats
  SPEC §5.4.1's "curve" cell as superseded by the already-implemented,
  Annex-A-literal chain — a deliberate deviation from that one
  suggested-stack cell, recorded here rather than silently ignored.

The discount-factor resolver (`discount_factor_from_continuous_zero_curve`)
and the not-yet-built Annex A §A.5.2 forward-clean-price composition
follow the same reasoning and also stay custom.

---

## 4. Day-count mapping decision

`products/enums.py::DayCount` has four members: `ACT_360`,
`ACT_365_FIXED`, `THIRTY_360`, `ACT_ACT_ISDA`. QuantLib's day-counter
constructors that a mapping table would need:

```text
DayCount.ACT_360        -> ql.Actual360()
DayCount.ACT_365_FIXED  -> ql.Actual365Fixed()
DayCount.THIRTY_360     -> ql.Thirty360(ql.Thirty360.BondBasis)   # or .USA; pick one, document it
DayCount.ACT_ACT_ISDA   -> ??? (the open question below)
```

**The `ACT_ACT_ISDA` mapping is not free of ambiguity and must be
decided explicitly, not inferred from the enum's name.** QuantLib
exposes `ql.ActualActual(ql.ActualActual.ISDA)` (the ISDA convention,
a fixed rule independent of the bond's own coupon schedule) and
`ql.ActualActual(ql.ActualActual.ISMA)` / `.Bond` (the ICMA/ISMA bond
convention, which requires the bond's `Schedule` object as a
constructor argument and produces different accrued-interest results
for an irregular period). Government-bond accrued interest in most
markets Annex A §A.6.2's table lists (`US Treasury`, `Euro Govt`, `UK
Gilt`) is conventionally computed under the ICMA/bond convention in
practice, even though this repo's enum member is spelled
`ACT_ACT_ISDA`. Two options exist, and this doc does not silently pick
one:

- **Option 1 — take the enum name literally.** Map
  `DayCount.ACT_ACT_ISDA -> ql.ActualActual(ql.ActualActual.ISDA)`
  exactly as spelled. Simple, matches the field's own name, but may
  diverge from Bloomberg/vendor accrued-interest figures for the
  government/corporate bonds Annex A §A.6.2 actually enumerates,
  risking §A.13.4's Bloomberg benchmark check at 2%–5%+ variance for a
  bond where the market convention is genuinely ICMA/bond-basis.
- **Option 2 — treat `ACT_ACT_ISDA` as this repo's placeholder label
  for "actual/actual, bond-schedule-aware" and map it to
  `ql.ActualActual(ql.ActualActual.ISMA, schedule)`.** Matches
  practical bond-market accrual and Annex A §A.6.2's per-market table
  intent more closely, but means the enum member's own name is
  misleading relative to what it actually triggers — a future reader
  grepping for "ISDA" in the adapter would not expect an ISMA/bond-basis
  call underneath it without reading this doc.

**Recommendation (revised, Codex review of PR #80): Option 1 — map
`DayCount.ACT_ACT_ISDA` literally to `ql.ActualActual(ql.ActualActual.
ISDA)`, with no ISMA/bond-basis substitution anywhere in the adapter.**
This doc's earlier draft recommended Option 2; that recommendation is
**withdrawn**. Silently mapping an enum member spelled `ACT_ACT_ISDA`
to QuantLib's `ISMA`/`Bond` variant would hide a different day-count
convention behind a name that says otherwise — exactly the kind of
undocumented, unauditable substitution `docs/27`/`docs/28`'s "do not
silently infer [basis] from [an adjacent field]" discipline already
forbids for curve rate basis, and there is no principled reason to
permit it here merely because the mismatch sits between two
`ActualActual` sub-conventions rather than between a par rate and a
zero rate. The adapter maps the name to what it literally says, and
nothing else.

This is a **real, stated limitation carried forward, not a deferred
convenience**: for a bond where the market convention is genuinely
ICMA/bond-basis actual/actual (as Annex A §A.6.2's per-market table
implies for several listed markets), literal-ISDA accrued interest may
diverge from Bloomberg/vendor figures. That divergence is exactly what
Annex A §A.13.4's Bloomberg/vendor benchmark check exists to catch —
surfacing a real, *measured* discrepancy through the self-validation
framework the desk already has, rather than this doc silently
deciding the "more correct" convention on the desk's behalf with no
benchmark evidence at all. **If and when a benchmark run reveals a
genuine ISDA-vs-ICMA divergence for a specific bond/market, the correct
fix is a new, distinct `BondReferenceData.day_count` enum member (e.g.
`ACT_ACT_ICMA` or `ACT_ACT_BOND_BASIS`), added in its own reviewed
slice — never a silent behavior change under the existing
`ACT_ACT_ISDA` name.** Until that member exists, `ACT_ACT_ISDA` means
ISDA, and only ISDA; no enum rename and no new day-count member is
added by this doc or by the next implementation PR (§8) — introducing
`ACT_ACT_ICMA` is deferred until an actual benchmark run demonstrates
the need, not guessed at here.

---

## 5. Coupon unit convention

`BondReferenceData.coupon` is a **decimal annual coupon rate**
(`0.0325` for the fixture bond, verified against `__post_init__`'s own
`coupon >= 0` check and every fixture value, all of which are small
decimals, not per-100 numbers). Per-period, per-100-face coupon cash is
not stored anywhere and is not computed by any existing code.

**Decision (revised, Codex review of PR #80): the adapter computes
per-period coupon cash on a fixed per-100-face basis, as
`coupon × 100 / periods_per_year`, where `periods_per_year` comes from
`coupon_frequency` (`SEMI_ANNUAL -> 2`, `ANNUAL -> 1`, `QUARTERLY -> 4`,
`MONTHLY -> 12`, `DAILY` -> rejected as an invalid bond coupon
frequency, since no fixture or Annex A citation uses a daily-coupon
bond).** For the fixture bond this is `0.0325 × 100 / 2 = 1.625` per
coupon, per 100 face.

**`redemption_amount` is not used in this formula, and coupon flows
are never scaled by it.** This doc's earlier draft used
`redemption_amount` as the coupon base; that was wrong, for the same
reason Annex A quotes clean price "per 100" throughout (§A.1: "Clean
price per 100 報價慣例") — every curve, discount-factor, and
forward-price helper already built in this dependency chain (§1)
operates on a fixed per-100 convention, and `amount_per_100`'s own
field name (§8) promises a per-100 number, not a per-`redemption_amount`
one. Mixing the two conventions inside one coupon-flow helper would
silently break that convention for any bond whose
`redemption_amount != 100.0` — none of the current fixtures have this,
but nothing in `BondReferenceData.__post_init__` forbids it; it only
requires `redemption_amount > 0`.

`redemption_amount`'s only legitimate use is principal/redemption-leg
logic (the final repayment amount at maturity, and any invoice/
settlement amount computed against it, per Annex A §A.7.2) — a
separate concern this adapter's first slice does not implement at all
(no principal cashflow, no redemption logic, no settlement invoicing).
**If a future bond with `redemption_amount != 100.0` creates a genuine
ambiguity about how per-100 coupon flows and principal-at-
`redemption_amount` should interact, that ambiguity must be gated
(explicitly rejected, not guessed) in whatever future slice adds
principal/redemption logic — it is not solved here by scaling coupon
flows.**

This coupon formula is a **regular-coupon approximation, valid only
when every coupon period is regular.** Every existing fixture bond is
manually constructed with regular first/last coupon periods
(`reference_data/fixtures.py`'s own docstring states this explicitly),
so no existing fixture would expose a stub miscalculation today — but
the adapter's first slice must not silently apply this formula to a
bond it has not verified is regular. **The adapter must detect an
irregular first or last coupon period and raise a documented,
unsupported-stub error rather than approximate it.** Detection compares
`first_coupon_date` against `issue_date` plus one unadjusted
`coupon_frequency` period (per §2's `NullCalendar` decision), and
`last_coupon_date` against `maturity_date` minus one unadjusted
`coupon_frequency` period; any mismatch means an odd-first or odd-last
stub is present, and the adapter raises instead of computing a coupon
amount for that period. This is a hard requirement carried into §8's
test list below, not a "future review" deferral — silently returning a
regular-period coupon amount for a bond with a real stub would be a
wrong number returned with no signal that anything was approximated,
which is worse than refusing outright.

---

## 6. Ex-dividend policy

`BondReferenceData.ex_dividend_days` is a required, non-negative `int`.
**The eligible MVP fixture (`XS0000000001`) has `ex_dividend_days=1`,
not `0`** — so this is not a hypothetical edge case the adapter can
defer; the only bond this MVP currently prices through is already in
the ex-dividend-relevant category. Annex A §A.6.3 states accrued
interest during an ex-coupon period "需考慮負值情境" (must consider the
negative-value scenario) when `ex_dividend_days > 0`.

**Decision: the first adapter slice (§7) explicitly gates on this
rather than silently ignoring it.** `accrued_interest_per_100(bond,
as_of_date)` raises a clear, documented error (not a silent zero, not a
silent positive-only value) whenever `as_of_date` falls inside a bond's
ex-dividend window (i.e. within `ex_dividend_days` calendar days before
a coupon payment date, using the bond's own convention — the exact
window-boundary rule, e.g. calendar days vs. business days, is decided
in the implementation PR, not here). This means the first adapter slice
**cannot compute accrued interest for `XS0000000001` on any date inside
its ex-dividend window** — a real, stated limitation, not a gap this
doc glosses over. Negative-accrued-interest support (the correct,
non-gated behavior Annex A §A.6.3 describes) is deferred to a follow-up
slice once the exact sign/boundary convention is worked out and tested
against a hand-computed example, because guessing that convention now
would risk exactly the kind of silently-wrong number this codebase's
existing conservative-rejection pattern (e.g. `bli_zero_rate_interpolation
.py`'s refusal to flat-extrapolate) is designed to avoid.

---

## 7. Cross-checking computed AI against observed snapshot AI

**Decision: yes, a future consuming slice (not the adapter itself)
should cross-check computed accrued interest against
`BLIBondQuote.accrued_interest_per_100` when both are available, as a
warning-level consistency check, not a construction-time hard error.**
Reasoning:

- The adapter has no visibility into `BLIBondQuote` at all (§2's
  boundary: it takes a `BondReferenceData` and a date, nothing
  market-data-shaped) — so the cross-check cannot live inside the
  adapter module itself without breaking that boundary. It belongs in
  whatever future slice combines adapter output with a
  `BLIMarketDataSnapshot`/`BLIMVPInputBundle` (the same slice likely
  responsible for Annex A §A.5.2's `Spot Dirty Price = Spot Clean Price
  + AI(pricing date)` line).
- A hard construction-time error would mean a single day-count-mapping
  mistake (§4) or a stale/rounded observed quote could permanently block
  pricing for a bond that is otherwise fine — too strong a consequence
  for what is fundamentally a data-quality signal, not a contract
  violation.
- A silent, unreported mismatch would be worse: it hides exactly the
  kind of day-count/mapping bug §4 is worried about. This mirrors the
  project's own established pattern for a closely analogous
  case — Annex A §A.9.5/§A.13.1's "closed-form vs bump-and-revalue"
  Greeks consistency check, which is a **reported, thresholded
  comparison surfaced in the Internal Pricing Report**, not a
  construction-time rejection.
- **Tolerance is not decided by this doc.** A future PR must pick and
  justify a concrete threshold (e.g. an absolute per-100 tolerance, not
  a percentage, since accrued interest can be arbitrarily close to
  zero right after a coupon date, making a percentage-based tolerance
  degenerate) against at least one hand-computed example — this doc
  only decides that the check should exist and where it should live,
  not its numeric threshold.

---

## 8. Proposed next implementation PR

```text
Branch:      claude/bli-quantlib-adapter-schedule-and-accrued
PR title:    Add BLI QuantLib bond-mechanics adapter: schedule, coupon
             cashflows, and accrued interest
```

**Target files:**

```text
src/shiori_pricing_lab/pricing/bli_quantlib_bond_adapter.py   (new)
tests/test_bli_quantlib_bond_adapter.py                        (new)
.github/workflows/python-tests.yml                             (install the
                                                                 quant extra)
```

**Exact adapter API (decided in that PR, this is the shape this doc
recommends it start from):**

```python
@dataclass(frozen=True)
class BLIBondCouponFlow:
    payment_date: str          # ISO YYYY-MM-DD
    amount_per_100: float      # fixed per-100 face, never redemption_amount; see §5

def is_quantlib_available() -> bool: ...

def coupon_flows_before(
    bond: BondReferenceData,
    *,
    after_date: str,     # exclusive
    on_or_before_date: str,  # inclusive
) -> tuple[BLIBondCouponFlow, ...]: ...

def accrued_interest_per_100(
    bond: BondReferenceData,
    *,
    as_of_date: str,
) -> float: ...
```

Only `BondReferenceData` and plain ISO date strings/floats cross the
boundary in either direction — no `BLIMarketDataSnapshot`,
`BLIMVPInputBundle`, curve, or QuantLib object appears in any
signature. `coupon_flows_before`'s `(after_date, on_or_before_date]`
argument shape mirrors Annex A §A.5.2's own `coupon_date_i ∈ (pricing
date, expiry date]` window exactly, so a caller does not need to
reinterpret inclusivity.

**Calendar note (§2):** `coupon_flows_before`'s and
`accrued_interest_per_100`'s internal schedule construction uses
`ql.NullCalendar()` only — no calendar parameter is exposed on this
API, and none should be added until a reviewed calendar-source contract
exists. **Stub note (§5):** both functions raise a documented
unsupported-stub error (e.g. a dedicated exception or a `ValueError`
naming the bond and the irregular period) if `bond`'s first or last
coupon period is irregular per §5's detection rule — neither function
silently falls back to the regular-period formula for such a bond.

**Deterministic tests to add:**

```text
- Availability: importing the module without QuantLib installed does
  not raise; calling any function without QuantLib installed raises a
  clear, documented error; is_quantlib_available() reflects the
  environment (pytest.importorskip("QuantLib") gates the functional
  tests below).
- Schedule/coupon dates for XS0000000001 match a hand-written literal
  list: 2025-12-15, 2026-06-15, 2026-12-15, 2027-06-15, 2027-12-15,
  2028-06-15, 2028-12-15, 2029-06-15, 2029-12-15 (first_coupon_date
  through last_coupon_date inclusive, per the fixture's own manually
  constructed regular schedule) -- expected values computed by hand in
  the test, not accepted from whatever QuantLib returns. **These are
  asserted as unadjusted dates (§2): the test build must use
  ql.NullCalendar() and must not pass any other calendar to the
  Schedule construction**, and the test docstring/comment says so
  explicitly rather than leaving the calendar choice implicit.
- coupon_flows_before(bond, after_date="2026-07-01",
  on_or_before_date="2026-09-29") returns () for XS0000000001 -- this
  is the existing MVP fixture's own window, and must return empty per
  §1's restated observation, proving the current positive-path fixture
  exercises the zero-coupon branch.
- coupon_flows_before(bond, after_date="2026-07-01",
  on_or_before_date="2027-01-15") returns exactly one flow:
  ("2026-12-15", 1.625) -- a second, non-fixture date pair, added
  specifically because the existing fixture cannot exercise the
  non-empty branch (per §1).
- Boundary: a coupon exactly on after_date is excluded; a coupon
  exactly on on_or_before_date is included.
- accrued_interest_per_100 at a hand-computed mid-period date under
  ACT_ACT_ISDA (the mapping chosen in §4, with the expected value
  computed by hand against that specific day-counter, not against
  QuantLib's own output); at a coupon date itself (expected 0.0); the
  day immediately after a coupon date (expected a small, hand-computed
  positive value).
- accrued_interest_per_100 for XS0000000001 at a date inside its
  ex-dividend window (1 calendar day before a coupon date, per
  ex_dividend_days=1) raises the documented error from §6 -- proving
  the gate is real, not just described.
- Irregular stub rejection: a hand-built BondReferenceData with an
  odd-first coupon period (first_coupon_date earlier than issue_date
  plus one unadjusted coupon_frequency period) and a separate
  hand-built bond with an odd-last coupon period (last_coupon_date
  later than maturity_date minus one unadjusted coupon_frequency
  period) each raise the documented unsupported-stub error from §5
  when passed to coupon_flows_before and to accrued_interest_per_100
  -- proving the regular-coupon approximation is never silently
  applied to an irregular bond. The test asserts an exception is
  raised, not that some fallback/approximate number is returned; no
  simple coupon/frequency approximation is computed for either case.
- Clean/dirty identity: for a hand-picked clean price, dirty(d) -
  clean(d) == accrued_interest_per_100(bond, as_of_date=d) at both a
  valuation-date-shaped and an expiry-date-shaped input.
- No system date: monkeypatch date.today()/datetime.now() (or simply
  assert output is identical across two calls made at different wall-
  clock times with identical arguments) and confirm no adapter output
  changes; assert the module's source does not call date.today() or
  datetime.now() directly.
- Module-boundary guards, mirroring the existing pattern in
  tests/test_bli_curve_selector.py / tests/test_bli_valuation_time.py:
  the adapter module does not import BLIMVPInputBundle,
  BLIMarketDataSnapshot, PricingResult, or any curve/discount-factor
  helper from §1's chain; pricing/bli_pricing_engine.py's source does
  not import this new adapter module; price_bli_mvp's return value for
  the existing SYNTHETIC_BLI_MVP_INPUT_BUNDLE fixture is byte-for-byte
  unchanged before/after this PR; no function in the module returns or
  computes anything PV/forward-price/discount-factor/Black-76-shaped.
```

**Explicit non-goals for that PR:**

```text
no curve interpolation or discount-factor change
no forward clean price (Annex A §A.5.2)
no PV(coupons before expiry) -- that is Annex-A-curve composition,
  a separate future slice combining this adapter's coupon_flows_before
  with the existing discount_factor_from_continuous_zero_curve
no yield-to-price / price-to-yield conversion
no Black-76, no option PV, no Greeks
no wiring into price_bli_mvp
no negative-accrued-interest / ex-dividend-window support (§6 defers
  this; the PR gates/raises instead)
no calendar-adjusted coupon/payment dates -- unadjusted / NullCalendar
  only (§2); no calendar parameter is added to the adapter API
no irregular-stub coupon-amount approximation -- a stub bond must
  raise a documented error, never approximate a coupon amount for the
  irregular period (§5)
no principal/redemption cashflow or settlement-invoice logic, and no
  use of redemption_amount anywhere in coupon-flow generation (§5)
no new BondReferenceData.day_count enum member (e.g. ACT_ACT_ICMA) --
  ACT_ACT_ISDA maps literally to QuantLib ISDA only (§4); a new member
  is deferred until a Bloomberg/vendor benchmark run demonstrates the
  need
no cross-check against BLIBondQuote.accrued_interest_per_100 (§7
  defers this to a later slice that has visibility into market data)
no change to BondReferenceData, BLIBondQuote, BLIMarketDataSnapshot,
  or BLIMVPInputBundle schemas
no change to the DayCount enum member names -- ACT_ACT_ISDA's mapping
  to ql.ActualActual(ISDA) is a mapping-table decision inside the
  adapter (§4), not a schema rename or a new enum member
no required dependency change -- QuantLib stays under
  [project.optional-dependencies].quant
no edits to SPEC_v1.3.md / ANNEX_A_v1.3.md / ANNEX_B_v1.3.md /
  ANNEX_C_v1.3.md
no closing of issue #38
```

**CI change required in that same PR:** `.github/workflows/
python-tests.yml`'s `pip install -e .` step must become `pip install
-e ".[quant]"` (or an equivalent extra install step) in the same PR
that adds the first QuantLib-gated tests — otherwise those tests would
silently skip in CI forever via `pytest.importorskip`, which is worse
than not having them, since a reviewer would see "tests added, CI
green" with no signal that the new tests never actually ran against
QuantLib. This doc does not add that workflow change itself (docs-only
constraint); it only records that the implementation PR must not omit
it.

---

## 9. What must remain out of scope for this PR

```text
no modification to src/
no modification to tests/
no modification to .github/workflows/
no QuantLib adapter module added
no dependency-manifest change
no curve interpolation implementation
no discount-factor implementation
no forward clean price
no coupon/cash-flow schedule implementation
no accrued interest implementation
no volatility surface / conversion
no yield-to-price / price-to-yield conversion
no Black-76
no PV
no Greeks / DV01 / CS01
no wiring of anything into price_bli_mvp
no change to price_bli_mvp
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
(§8) should read, in this order:

```text
1. This doc (docs/29_bli_quantlib_bond_adapter_preflight.md).
2. docs/28_bli_curve_rate_basis_preflight.md -- the immediately prior
   preflight, and the reasoning style (candidate paths, explicit
   recommendation, non-goals list) this doc follows.
3. src/shiori_pricing_lab/reference_data/bond_reference_data.py and
   src/shiori_pricing_lab/reference_data/fixtures.py -- confirm the
   exact field values (coupon, day_count, ex_dividend_days, coupon
   dates) this doc's §4/§5/§6/§8 analysis depends on.
4. src/shiori_pricing_lab/pricing/bli_curve_discount_factor.py and the
   six modules it composes -- confirm none of them changes, and that
   the new adapter has zero import relationship with any of them.
5. docs/bond_linked_structured_pricer/ANNEX_A_v1.3.md §A.5.2/§A.6/
   §A.9.5/§A.13.1 -- the forward-clean-price formula, yield-to-price
   conversion, and Greeks-consistency-check precedent this doc's §7
   analysis reasons from.
6. pyproject.toml's [project.optional-dependencies].quant entry and
   .github/workflows/python-tests.yml -- confirm the CI gap this doc's
   §8 flags is still unresolved before writing the implementation PR.
```

The actual implementation PR described in §8 is **not started by this
doc**. Issue #38 remains open.
