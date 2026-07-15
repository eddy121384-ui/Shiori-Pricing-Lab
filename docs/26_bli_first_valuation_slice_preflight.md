# 26 BLI First Valuation Slice Preflight

Status: docs-only preflight. No pricing module, valuation math, cash-flow
generation, schedule engine, yield-to-price calculation, curve
interpolation, volatility surface, credit spread model, Treasury FTP
parser, ingestion, Bloomberg/API connector, QuantLib adapter, or UI is
added by this doc. No source file under `src/` and no test file under
`tests/` is modified. `price_bli_mvp`'s runtime behavior is unchanged.
No new `PricingResult`/`PricingStatus`/`PricingErrorCode`/
`BLIPricingResult`/`BLIPricingStatus` is introduced. No frozen BLI v1.3
source spec file (`SPEC_v1.3.md`, `ANNEX_A_v1.3.md`, `ANNEX_B_v1.3.md`,
`ANNEX_C_v1.3.md`) is edited. Issue #38 is unaffected and remains open.

---

## 1. Where this picks up

PR #68 (merged, `445f710`) landed the BLI **pricing engine skeleton**:
`price_bli_mvp(bundle: BLIMVPInputBundle) -> PricingResult`
(`src/shiori_pricing_lab/pricing/bli_pricing_engine.py`). For every valid
bundle it returns a deterministic `PricingResult(status=FAILED,
errors=[PricingErrorCode.UNSUPPORTED_PRODUCT])` — no real valuation math
exists. It reuses `PricingResult`/`PricingStatus`/`PricingErrorCode`
as-is (no `BLIPricingResult`/`BLIPricingStatus`), does not call
`resolve_bond_reference_data` or `build_bli_mvp_input_bundle`, and is not
registered on `PricingEngineRegistry` (whether a future slice also
registers a bundle-unpacking adapter behind the generic `price(...)`
front door remains an explicitly open, undecided question — this doc
does not decide it either).

**The gap this doc addresses:** nothing yet says what the *first* real
line of BLI valuation math should be, or in what order the still-missing
methodology pieces (forward price, curve interpolation, coupon schedule,
accrued interest, vol conversion, yield/price conversion, physical
settlement) should land. Annex A v1.3 (§A.0–§A.15) defines the full BLI
methodology in one document; attempting all of it in one PR is exactly
the failure mode this doc exists to prevent — mirroring the same
"contract before methodology" discipline already used for the vanilla
IRS engine (`docs/10_irs_reference_engine_preflight.md` → PR #23's
contract-only slice → PR #29's real engine) and for every BLI slice
before this one (`docs/17`–`docs/25`).

---

## 2. What is the first real valuation slice? (required question 1)

**Chosen: the narrowest model Annex A defines at all — §A.2's European,
price-based, cash-settled bond option, priced with Black-76 on forward
clean price — computed only for the bond option leg already embedded in
the existing `BLIMVPInputBundle.product` (a `BondLinkedStructuredProduct`
wrapper). The deposit leg is not priced in this slice.**

### 2.1 Why this is the narrowest available slice

Every one of the task's "strongly prefer" narrowing rules is already the
*only* option Annex A defines at this level of simplicity, not an
arbitrary restriction this doc is inventing:

| Narrowing rule | Annex A / SPEC basis |
| --- | --- |
| European only | §A.2 has no early-exercise logic; American requires the CRR tree (§A.4), a materially larger, still-undesigned-for-BLI seam. |
| Price-based only | §A.2 needs only `F`/`K`/`σ`/`T`/`DF` — no yield-to-price conversion, no DV01-based mode switch (§A.3's `YIELD_OPTION_MODE`). On vol: `BLIVolatilityBasis.PRICE_VOL` and `EQUIVALENT_PRICE_VOL` can both be used directly as `σ` in §A.2's formula as-is (a `PRICE_VOL` observation needs no conversion; an `EQUIVALENT_PRICE_VOL` value is, by definition, already in the right units if one has already been supplied upstream). `YIELD_VOL` is the one basis that is **not** directly usable here — Annex A §A.8's equivalent-price-vol conversion (MODE_1/MODE_2) would first need to turn it into a price vol, and that conversion is dependency 6 (§4), not implemented. Since no conversion exists yet, this slice's destination must require an already price-compatible vol input (`PRICE_VOL` or a pre-supplied `EQUIVALENT_PRICE_VOL`) and leave `YIELD_VOL` inputs blocked until dependency 6 lands — it does not implement any vol conversion itself. |
| Cash-settled only | §A.7 (physical delivery invoice, settlement-date accrued interest) is a separate, additive concern (SPEC §3.6) not needed to compute an option PV. |
| Deterministic happy-path fixture only | `SYNTHETIC_BLI_MVP_INPUT_BUNDLE` (`data/bli_mvp_input_bundle_fixtures.py`) already exists, is `PayoffBasis.PRICE` (via `products/fixtures.py`'s `SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT` — see §3), and requires no new fixture content. |
| No American exercise | Same as "European only" above. |
| No yield-based option | Same as "price-based only" above. |
| No physical delivery | Same as "cash-settled only" above. |
| No deposit-leg economics | See §2.2. |

### 2.2 Why "standalone bond option only" means "price only the embedded
leg," not "change the input contract"

The task's suggested framing is "standalone BLI bond option only, not
full structured product." Taken literally as an *input-schema* change,
this is not available without inventing a new bundle type:
`BLIMVPInputBundle.product` is typed `BondLinkedStructuredProduct`
(`data/bli_mvp_input_bundle.py`), and `BondLinkedStructuredProduct`
**requires** exactly one embedded `DepositLeg` at construction
(`products/bond_linked_structured_product.py::__post_init__`) — there is
no standalone-`BondOption`-only bundle contract anywhere in this
codebase, and inventing one would be new code, out of scope for a
docs-only preflight, and would fork the input contract PR #68's
`price_bli_mvp` already committed to (`bundle: BLIMVPInputBundle`).

**Resolution: "standalone bond option" is a *valuation-scope* boundary,
not an input-schema boundary.** The next code slice keeps accepting
`BLIMVPInputBundle` exactly as `price_bli_mvp` already does, and computes
a PV only for `bundle.product.bond_option` — the deposit leg stays
structurally present (it must be; the bundle cannot exist without it)
but its economics (deposit PV, customer return, participation-ratio
combination with the option payoff) are explicitly **not** computed by
this slice. This is directly sanctioned by SPEC §6.2, "Bond Option
Standalone Pricing Tool," which the spec itself scopes as an MVP-level
feature independent of the structured-product wrapper's full Quote
workflow ("Trader 對價... Warehousing 之前的單筆 option valuation").
Pricing only the option leg of an already-validated wrapper is the
smallest step toward that spec-sanctioned standalone tool, without
touching the bundle's input contract at all.

### 2.3 This is still not one PR

Even "compute a Black-76 PV for a European, price-based, cash-settled
bond option leg" is too large for one PR — it needs forward-price
derivation, curve discounting, and a coupon/accrued-interest model
first (§4). §5 picks the single smallest piece of *that* list as the
actual next implementation PR. This section only answers "what is the
destination the next several slices are walking toward," not "what
lands next."

---

## 3. What existing inputs are already available from `BLIMVPInputBundle`? (required question 2)

Everything below already exists, is already validated, and requires no
new fixture content. Nothing in this list is invented for this doc.

### 3.1 Bundle-level

```text
bundle.bundle_id            str
bundle.valuation_date        str, YYYY-MM-DD
```

### 3.2 `bundle.product` (`BondLinkedStructuredProduct`)

```text
product.product_id           str
product.product_type          "BOND_LINKED_STRUCTURED_PRODUCT" (fixed)
product.participation_ratio    float property (bond_option.notional / deposit_leg.deposit_notional)
```

**`product.bond_option` (`BondOption`) — the leg this slice prices:**

```text
bond_option.product_id            str
bond_option.underlying_isin        str
bond_option.currency              Currency
bond_option.payoff_basis           PayoffBasis (PRICE / YIELD)
bond_option.option_type            OptionType (CALL / PUT)
bond_option.exercise_style         ExerciseStyle (EUROPEAN / AMERICAN)
bond_option.settlement_type        SettlementType (CASH / PHYSICAL)
bond_option.settlement_lag_days     int
bond_option.expiry_date            str, YYYY-MM-DD
bond_option.notional               float  -- Bond Option Notional (SPEC §3.7)
bond_option.position               Position (BUY / SELL)
bond_option.strike_price           float | None  -- set iff payoff_basis is PRICE
bond_option.strike_yield           float | None  -- set iff payoff_basis is YIELD
bond_option.exercise_start_date     str | None  -- set iff exercise_style is AMERICAN
bond_option.product_type           "BOND_OPTION" (fixed)
```

**`product.deposit_leg` (`DepositLeg`) — present but not priced this
slice:**

```text
deposit_leg.deposit_leg_id, deposit_notional, currency, start_date,
maturity_date, deposit_rate_mode, principal_repayment_rule, tenor,
fixed_deposit_rate | ftp_rate_selector | manual_input_reference
(exactly one populated, per deposit_rate_mode), leg_type
```

### 3.3 `bundle.resolved_bond_reference_data` (`BondReferenceData`)

```text
isin, issuer, currency, coupon, coupon_frequency, maturity_date,
issue_date, day_count, business_day_convention, redemption_amount,
callable_flag, sinkable_flag, bond_type, yield_convention,
ex_dividend_days, first_coupon_date, last_coupon_date, status
```

Plus the resolver audit fields kept on the bundle directly:
`bundle.resolution_status` (always `FOUND_ELIGIBLE`, re-verified) and
`bundle.eligibility_reasons` (always `()`).

### 3.4 `bundle.market_data_snapshot` (`BLIMarketDataSnapshot`)

```text
valuation_date, as_of_timestamp, source_system, snapshot_id, status
```

**`bond_quote` (`BLIBondQuote`)** — the spot observation:

```text
isin, currency, price_type (BLIQuoteBasis: PRICE / YIELD), quote_side,
source_system, status, clean_price_per_100 | None,
yield_value | None (at least one of the two is present),
accrued_interest_per_100 | None  -- an OBSERVED value if present; never
                                     computed by anything in this repo today
```

**`curve_points` (`tuple[BLICurvePoint, ...]`)** — one row per
tenor/curve, already guaranteed (by `BLIMVPInputBundle.__post_init__`,
docs/24 §6) to include at least one row, in the product's own currency,
for each of:

```text
BLICurvePurpose.BOND_REFERENCE_CURVE
BLICurvePurpose.OPTION_DISCOUNT_CURVE
BLICurvePurpose.DEPOSIT_CURVE
```

(`FUNDING_CURVE` only if a future mapping calls for it — none does
today.) Each row: `curve_id, curve_name, currency, curve_purpose, tenor
(a bare string, e.g. "2Y"), rate, source_system, status`.

**`volatility_input` (`BLIVolatilityInput`):**

```text
volatility, volatility_basis (BLIVolatilityBasis: YIELD_VOL / PRICE_VOL /
EQUIVALENT_PRICE_VOL), source_system, status, override_or_fallback_audit | None
```

**`credit_spread_input` (`BLICreditSpreadInput`):**

```text
spread_treatment (OBSERVED / OVERRIDE / FALLBACK / EMBEDDED /
NOT_REQUIRED), credit_spread | None, credit_spread_basis | None,
source_system, status, override_or_fallback_audit | None
```

**`deposit_rate_observation` (`BLIDepositRateObservation | None`)** —
only relevant to deposit-leg economics, out of scope this slice (§2.2).

### 3.5 What is **not** in this list (do not invent)

No coupon/cashflow list, no computed accrued interest, no discount
factor, no forward clean price, no time-to-expiry year fraction, no
interpolated curve rate, and no yield-to-price/price-to-yield result
exist anywhere in this codebase today. `BLIBondQuote.
accrued_interest_per_100` is the *only* accrued-interest-shaped field
that exists, and it is an optional, directly observed input — nothing
computes it from `coupon`/`day_count`/`first_coupon_date`.

---

## 4. What dependencies are still missing before real pricing math can be implemented? (required question 3)

Each of these is genuinely absent — not "exists elsewhere and needs
wiring." None is implemented by this doc.

```text
1. Time-to-expiry year fraction (Annex A §A.2.2: T, ACT/365F, from
   pricing/valuation date to option expiry date). No BLI code computes
   this today.

2. Curve interpolation / discount-factor access from a BLICurvePoint
   collection (Annex A §A.10.2: piecewise linear on zero rates,
   continuously compounded; flat extrapolation outside range). Needed
   for both the Option Discount Curve (discounting the option PV) and
   the Bond Reference Curve (discounting coupons for the forward price,
   §A.5.3). BLICurvePoint stores tenor as a bare string ("2Y") with no
   parser or interpolation function anywhere. The existing
   pricing/curve.py::RateCurve is NOT reusable as-is: it is built from
   the unrelated vanilla-rates-core data.snapshot.MarketDataSnapshot
   (a pandas DataFrame of rates points), not from BLICurvePoint objects
   -- a structurally different shape, per docs/23 §3.4's own reasoning
   for why BLIMarketDataSnapshot is a distinct class in the first place.

3. Coupon / cash-flow schedule generation for the underlying bond
   (Annex A §A.5.2/§A.6.1: coupon dates between first_coupon_date and
   maturity_date at coupon_frequency). No schedule engine exists for
   BondReferenceData. pricing/schedule.py::generate_regular_schedule is
   IRS-leg-specific (drives off a swap leg's effective_date/
   payment_frequency) and is not wired to, or validated against,
   BondReferenceData's fields.

4. Accrued interest calculation (Annex A §A.6.3: day_count- and
   coupon-schedule-aware; ex-coupon negative-AI handling if
   ex_dividend_days > 0). Not implemented anywhere. Depends on (3).

5. Forward clean price derivation (Annex A §A.5.2, SPEC §3.4): combines
   (2) [Bond Reference Curve discounting] + (3) [which coupons fall
   before expiry] + (4) [accrued interest at both pricing date and
   expiry date]. Not implemented; this is the single largest remaining
   dependency and is explicitly NOT the next slice (§5).

6. Volatility selection / equivalent-price-vol conversion (Annex A §A.8,
   SPEC §3.3): BLIVolatilityInput.volatility_basis already distinguishes
   YIELD_VOL / PRICE_VOL / EQUIVALENT_PRICE_VOL, but nothing branches on
   it or performs the MODE_1/MODE_2 conversion described in §A.8.2/§A.8.3.

7. Yield-to-price / price-to-yield conversion (Annex A §A.6). Needed
   only for yield-based options (out of scope, §2.1) or a future
   equivalent-price-vol conversion's ModDur/Convexity inputs (item 6).
   Listed for completeness; not required for the price-based-only path
   this doc scopes toward.

8. Settlement / physical delivery invoice logic (Annex A §A.7, SPEC
   §3.6). Not required for the cash-settled-only path this doc scopes
   toward (§2.1); listed for completeness only.
```

---

## 5. Which missing dependency should be implemented first? (required question 4)

**Chosen: dependency 1 — the time-to-expiry year-fraction calculation
(Annex A §A.2.2's `T`, ACT/365F).**

### 5.1 Why this one, and why it is small enough

- **Zero market-data dependency.** It is pure date arithmetic:
  `T = (expiry_date - valuation_date).days / 365.0`. It needs no curve,
  no vol, no bond reference data, no coupon schedule — nothing from §4's
  items 2–8.
- **Zero design ambiguity.** Annex A §A.2.2 already pins the convention
  (`ACT/365F`) precisely; there is no "which of two reasonable
  approaches" choice to make, unlike almost every other item in §4
  (curve interpolation method, forward-price cost-of-carry assumption,
  vol conversion mode).
- **Already has a reviewed precedent in this codebase.** ACT/365F is
  exactly `DayCount.ACT_365_FIXED`, and
  `pricing/irs_engine.py::_year_fraction` already implements this exact
  formula (`days / 365.0`) for the IRS reference engine, reviewed and
  merged in PR #29. The BLI version is a small, mechanical adaptation of
  an already-proven pattern, not new methodology — this materially lowers
  review risk relative to every other §4 item.
- **Maximum future leverage for minimum code.** `T` is a direct input to
  the Black-76 `d1`/`d2` formula (§A.2.3), to the discount-factor horizon
  needed for item 2, and to the vol surface's maturity axis (§A.10.1) —
  essentially every later BLI valuation computation needs a time-to-expiry
  value, so this is the single dependency with the broadest downstream
  reuse for the smallest amount of new code.
- **Trivially, exhaustively testable.** A fixed table of
  (valuation_date, expiry_date) → expected `T` pairs, including the
  boundary case `expiry_date <= valuation_date` (which Annex A §A.2.4
  requires to **block** pricing — `T > 0` "否則 pricing blocked" — so the
  function must raise, not silently return zero or a negative value).

### 5.2 What this slice deliberately does NOT do

It does not compute a discount factor, a forward price, or a PV. It does
not read `curve_points`, `volatility_input`, `credit_spread_input`, or
`resolved_bond_reference_data` at all. It does not change
`price_bli_mvp`'s behavior (§7). It is a single, standalone, pure
function plus its tests — nothing else.

---

## 6. What must remain out of scope

Explicitly excluded from the next implementation PR (§7), restated as an
acceptance-criteria checklist:

```text
no full structured product pricing (deposit leg + option leg combined)
no deposit leg PV of any kind
no American option pricing / CRR tree
no yield-based option pricing / YIELD_OPTION_MODE
no physical delivery invoice logic
no full cash-flow engine
no broad/general schedule engine
no curve construction
no curve interpolation framework (dependency 2, deliberately deferred
  to a later slice, not this one)
no volatility surface framework
no equivalent-price-vol conversion (dependency 6, deferred)
no Greeks / DV01 / CS01
no scenario engine
no QuantLib adapter
no Bloomberg/API connector
no FTP ingestion
no UI / debug viewer
no fake numeric outputs of any kind
```

---

## 7. What should the next implementation PR look like?

```text
Suggested branch:     claude/bli-time-to-expiry-year-fraction
Suggested PR title:   Add BLI time-to-expiry year-fraction utility
```

**Target files:**

```text
src/shiori_pricing_lab/pricing/bli_valuation_time.py   (new)
  -- one pure function, e.g.
     year_fraction_act365f(start_date: str, end_date: str) -> float
  -- optionally, a second, equally tiny convenience function that reads
     the two relevant dates off a BLIMVPInputBundle (bundle.valuation_date,
     bundle.product.bond_option.expiry_date) and calls the function
     above -- still not wired into price_bli_mvp (see §7's acceptance
     criteria).

tests/test_bli_valuation_time.py                        (new)
```

**Expected tests:**

```text
- same-day pair (valuation_date == expiry_date) -> raises ValueError
  (Annex A §A.2.4 requires T > 0 strictly; T == 0 is exactly as blocked
  as a negative T, since the option has already expired by the time it
  would be priced -- there is no "boundary but not yet blocked" case).
- expired option (expiry_date < valuation_date) -> raises ValueError
  (same §A.2.4 rule).
- only a strictly future expiry_date (expiry_date > valuation_date)
  returns a value, and only then is it an ACT/365F year fraction --
  one-day, one-year, and a leap-year-spanning pair -> exact expected
  ACT/365F fraction (days / 365.0), matching
  pricing/irs_engine.py::_year_fraction's existing ACT_365_FIXED
  behavior for the same day-count convention.
- non-ISO / malformed date string -> raises (mirroring the existing
  _parse_iso_date-style rejection used throughout the repo).
- pure function performs no I/O, reads no curve/vol/credit-spread/bond-
  reference data, and never calls date.today()/datetime.now().
- if the bundle-level convenience function is added: it reads only
  bundle.valuation_date and bundle.product.bond_option.expiry_date, does
  not mutate the bundle, and works against the existing
  SYNTHETIC_BLI_MVP_INPUT_BUNDLE fixture without any new fixture content.
- module-boundary test (mirroring tests/test_bli_pricing_engine.py's
  pattern): asserts no curve/discount/forward-price/yield-conversion/
  QuantLib-shaped name exists anywhere in the new module.
```

**Acceptance criteria:**

```text
- the function's output matches Annex A §A.2.2's ACT/365F definition
  exactly for every test case.
- expiry_date <= valuation_date raises (never returns 0.0 or a negative
  T silently) -- Annex A §A.2.4's blocking rule.
- no date.today() / datetime.now() anywhere.
- price_bli_mvp is untouched and still returns the same deterministic
  PricingResult(status=FAILED, errors=[PricingErrorCode.UNSUPPORTED_PRODUCT])
  for every valid bundle -- this slice adds an unwired utility only.
- no new PricingResult / PricingStatus / PricingErrorCode /
  BLIPricingResult / BLIPricingStatus member or type is added.
- no curve, volatility, forward-price, or yield-conversion logic is
  introduced, even incidentally.
- tests reuse the existing SYNTHETIC_BLI_MVP_INPUT_BUNDLE /
  SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT fixtures rather than
  fabricating new ad hoc product/bundle instances, except where a
  boundary/error case genuinely needs a different date pair (in which
  case construct only the minimal date-pair input the pure function
  needs, not a new bundle).
```

**Explicit non-goals:** identical to §6's list, restated in the PR body
per this repo's standing PR-description convention.

**Codex review checklist for that PR:**

```text
[ ] Does the function compute days/365.0 exactly, with no off-by-one or
    inclusive/exclusive boundary error, matching irs_engine.py's
    existing ACT_365_FIXED precedent?
[ ] Is expiry_date <= valuation_date rejected (raise), never silently
    clamped to 0 or allowed to go negative?
[ ] Is date.today()/datetime.now() absent from the new module and its
    tests (no system-date use in pricing, docs/09 (removed, see git history) §3, AGENTS.md rule 11)?
[ ] Is price_bli_mvp's return value byte-for-byte unchanged before/after
    this PR for the existing SYNTHETIC_BLI_MVP_INPUT_BUNDLE fixture?
[ ] Does pricing/result.py / pricing/errors.py / pricing/engine.py /
    bli_pricing_engine.py remain unmodified?
[ ] Does the new module import nothing from curve/vol/credit-spread/
    schedule/QuantLib/Bloomberg/UI code?
[ ] Are the tests deterministic (no randomness, no wall-clock reads)?
```

---

## 8. How should `price_bli_mvp` behave until real math is ready? (required question 7)

**Keep returning the deterministic `PricingResult(status=FAILED,
errors=[PricingErrorCode.UNSUPPORTED_PRODUCT])` unchanged. Do not
introduce a narrower internal dispatch or dependency-gated result in the
next implementation PR (§7), and not in this preflight either.**

Reasoning: the next slice (§5/§7) is a single, pure, unwired utility
function. It does not touch `bli_pricing_engine.py` at all, so there is
nothing yet for `price_bli_mvp` to dispatch on. Introducing a narrower
"is this bundle's option European + price-based + cash-settled?" gate
now, before the actual PV computation exists, would add branching logic
with no real behavior behind it — exactly the kind of premature
structure `AGENTS.md`'s "prefer simple readable code over clever
abstractions" rule and this repo's "contract before methodology"
discipline (§1) warn against.

**When should that change?** Only once enough of §4's dependencies exist
to actually attempt a real PV for the narrow §2 slice (time-to-expiry +
curve discount-factor access + forward-clean-price derivation + vol
selection, in that rough order per §4's numbering) — at that point, a
future PR should introduce the dispatch that routes a *supported* bundle
shape (European, price-based, cash-settled) to a real Black-76
computation, while everything else (American, yield-based, physical
delivery, or any bundle still missing a required dependency) continues
to return `FAILED` with an explicit, structured reason — never a fake
number. **This doc does not decide that future PR's exact gate shape or
error code choice; it only confirms that no such change belongs in the
next implementation slice.**

---

## 9. Fresh-session handoff

A new Claude Code session picking up the actual next implementation PR
(§7) should read, in this order:

```text
1. This doc (docs/26_bli_first_valuation_slice_preflight.md).
2. docs/25_bli_pricing_engine_skeleton_preflight.md and the "BLI pricing
   engine skeleton landed" checkpoint in the MVP runbook (removed, see git
   history) -- what price_bli_mvp already does and does not do.
3. src/shiori_pricing_lab/pricing/bli_pricing_engine.py -- confirm it is
   not touched by the time-to-expiry slice.
4. src/shiori_pricing_lab/pricing/irs_engine.py's _year_fraction helper
   -- the existing ACT_365_FIXED precedent this slice mechanically
   mirrors for BLI.
5. src/shiori_pricing_lab/data/bli_mvp_input_bundle.py and
   bli_mvp_input_bundle_fixtures.py -- confirm bundle.valuation_date and
   bundle.product.bond_option.expiry_date are the only two fields this
   slice needs.
6. docs/bond_linked_structured_pricer/ANNEX_A_v1.3.md §A.2.2/§A.2.4 --
   the exact T definition and the "T > 0 else blocked" rule.
```

The actual implementation PR described in §7 is **not started by this
doc**. Issue #38 remains open.
