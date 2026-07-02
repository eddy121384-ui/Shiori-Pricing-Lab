# 17 BLI MVP Vertical Slice Preflight

Status: docs-only preflight. No `BondLinkedStructuredProduct`, deposit leg,
pricing engine, QuantLib, market-data ingestion, or connector code is added
by this doc.

## 0. Why this doc exists

`BondOption` now exists as a pure deal-term schema (PR #50, Issue #38
partial). `BondLinkedStructuredProduct` is still deferred: `docs/15` (PR #47)
concluded the wrapper cannot be treated as a complete economic schema until
the deposit-leg contractual terms (deposit rate/yield source, principal
repayment rule) and the `DayCount`/calendar decision (A-14) are resolved.
`docs/16` (PR #49) clarified that Shiori Pricing Lab is API-first /
file-minimal, and that Treasury FTP / Funding Curve — not generic
market-data file import — is the first expected MVP manual-upload surface.

This doc does not resolve those open questions. It defines the smallest
complete Bond Linked Structured Product (BLI) MVP vertical slice — one
product, one structure, one end-to-end pricing path — so that future
implementation slices have a bounded target instead of drifting toward the
full BLI platform described in the v1.3 specs. It does not implement
anything and does not close Issue #38.

---

## 1. What counts as "the smallest usable BLI MVP"

MVP means:

```text
one product,
one supported structure,
one end-to-end pricing path,
complete economics,
minimal infrastructure.
```

MVP explicitly does **not** mean:

```text
all products,
all data sources,
all connectors,
full UI,
full market-data platform.
```

"Complete economics" is the binding constraint: the MVP must be able to
reproduce the customer's actual cashflows for the one structure it supports
(deposit return + option payoff), even though everything else about it is
minimal. A schema that looks complete but omits the deposit rate or the
repayment rule (the original `docs/15` "minimal wrapper" mistake) is not an
MVP — it is a shell, and `docs/15` §3 already rejected that as insufficient.

---

## 2. Proposed MVP product scope

```text
single underlying plain-vanilla bond
single deposit leg
single embedded bond option leg (the existing BondOption schema)
European exercise first
cash settlement first
fixed or explicitly supplied deposit/funding rate
no callable/sinkable/exotic underlying bonds
no portfolio/batch pricing
no Bloomberg connector dependency for MVP
no screenshot capture dependency for MVP
```

This matches existing docs, with two choices worth flagging explicitly
rather than treating as automatic:

- **European exercise first.** `BondOption` already supports both
  `EUROPEAN` and `AMERICAN` (PR #50). Nothing in `docs/14` or `docs/15`
  states American must come first; `docs/14` §2.4 flags the American-tree
  methodology (SPEC §A.4) as one of the more involved pieces of Annex A.
  Starting the MVP payoff/pricing slice with `EUROPEAN` only is a scope
  choice made **here**, not a pre-existing repo requirement — record it as
  such, not as inherited fact.
- **Cash settlement first.** Same reasoning: `SettlementType.PHYSICAL`
  already exists on `BondOption`, but physical delivery of the underlying
  bond adds a bond-transfer/custody concern the MVP does not need. Cash
  settlement is chosen here as the narrower path, not because physical
  settlement is unsupported by the schema.

No conflict was found between this proposed scope and `docs/13`, `docs/14`,
or `docs/15` — the BLI product-priority pivot (`docs/13`) and the teardown
(`docs/14`) describe the full v1.3 product universe, and this doc is
choosing the single narrowest slice of that universe, which those docs
anticipate ("MVP slice" language already appears in `docs/14` §6).

---

## 3. Required economic components

| Component | Already done | Needed for MVP | Explicitly deferred |
| --- | --- | --- | --- |
| **Bond option leg** | `BondOption` schema + tests (PR #50) | Nothing further at schema level | A pricing engine for it (Black-76 / Issue #44) |
| **Deposit leg** | Nothing | A `DepositLeg`-equivalent schema carrying notional, currency, start/maturity dates, a resolved rate/yield, and a principal repayment rule | Funding-curve-lookup mode, amortizing/exotic repayment rules beyond bullet |
| **Wrapper relationship** | Nothing (`docs/15` explicitly rejected a placeholder-only wrapper as insufficient) | A schema linking deposit leg + `BondOption`, with `participation_ratio` derived/validated (§6) | Multi-option or multi-deposit-leg wrappers |
| **Market / reference data** | Nothing BLI-specific; existing `MarketDataSnapshot` concept (`docs/02`) | Minimal bond reference fixture (§7) + minimal market inputs (§8) | Bloomberg/BQL connector, generic file import, screenshot capture |
| **Pricing method** | Nothing | A deterministic MVP pricing path definition (§10) — not the engine itself | Actual engine implementation, American tree, vol-surface handling beyond a flat input |
| **Audit trail** | Existing `PricingResult` / `PricingMessage` conventions (`docs/09` §8) | MVP-scoped audit fields (§11) | Full provenance/versioning system beyond the MVP field list |

---

## 4. Deposit leg decisions that must be resolved before wrapper code

At minimum, a `DepositLeg`-equivalent schema needs:

```text
deposit_notional
deposit_start_date
deposit_maturity_date
deposit_rate or deposit_yield source
principal_repayment_rule
deposit currency
deposit day count / calendar treatment
```

**Deposit rate/yield source — not decided here.** `docs/15` §3.2 already
flagged this as ambiguous rather than resolved, and this doc does not
resolve it either. Three options exist and must be chosen (or explicitly
scoped as a configurable mode) in the future `DepositLeg` preflight (slice
A, §12):

1. **Fixed contractual trade term** — the rate is agreed and frozen at
   execution, stored directly on the schema (same pattern as
   `FXSwap.near_rate`). Still needs a day-count convention to turn into an
   accrual amount, which re-opens the blocked `DayCount`/calendar decision
   (A-14).
2. **Supplied Treasury FTP / Funding Curve input** (`docs/16`) — the rate
   is resolved from an internal funding-cost curve at pricing time. This is
   a market/funding-data input and, per `docs/04` and `docs/16`, must never
   live on the product schema itself; the schema would carry a reference
   (e.g. a currency/tenor lookup key), not the rate.
3. **Both, under an explicit mode flag** — the schema records which source
   applies (`FIXED` vs. `FUNDING_CURVE`) and validates that exactly the
   fields for that mode are present. This avoids silently picking one and
   is the option most consistent with `docs/16`'s API-first-but-file-minimal
   framing, but it is more schema surface than options 1 or 2 alone.

**This preflight does not pick one.** Slice A (§12) must decide before any
deposit-leg schema code is written, the same way `docs/15` did for
`BondOption`'s day-count boundary.

`day_count` / `calendar` treatment on the deposit leg remains blocked by the
same unresolved Issue #37 vocabulary decision (A-14) that blocks it on the
wrapper side (`docs/15` §3.2) — this is not a new blocker, just restated
here for completeness.

---

## 5. Wrapper relationship

The wrapper is the layer that links:

```text
Deposit leg + BondOption leg + repayment/payoff rules
```

It should define or validate:

```text
deposit_notional
bond_option.notional
participation_ratio = bond_option.notional / deposit_notional
principal repayment rule
how option payoff affects final customer payoff
settlement / delivery behavior
```

**`participation_ratio` must be derived or validated, not freely set** —
restated from `docs/15` §3.3: a schema that stores `participation_ratio`
independently of `bond_option.notional` and `deposit_notional`, checked only
for positivity, would allow internally contradictory terms (e.g.
`deposit_notional=100`, `bond_option.notional=50`, `participation_ratio=2`,
which implies `0.5`). The future wrapper implementation must either derive
`participation_ratio` at construction time or validate a stored value
against the ratio within an explicit, documented tolerance.

"How option payoff affects final customer payoff" and "settlement /
delivery behavior" are principal-repayment-rule concerns (§4) and are not
decided here — they are listed as required wrapper-schema *fields to
resolve*, not as resolved rules.

---

## 6. Minimal reference data / Bond Master

Minimum bond reference data needed for MVP pricing (not implemented here):

```text
ISIN
issuer
currency
coupon
coupon_frequency
maturity_date
day_count
business_day_convention
yield_convention
calendar
redemption_amount
plain-vanilla eligibility flag
```

For MVP, this may be a **manually supplied fixture / reference input** —
e.g. a small synthetic JSON/CSV fixture used to drive the pricing path — but
it must **not** be embedded inside product schemas (`docs/04`'s
product/market-data separation rule, and the exact reasoning `docs/15` used
to keep `day_count` / `yield_convention` off `BondOption`). A later, separate
slice (§12, slice B) defines this fixture boundary; this doc only lists what
the minimum field set is.

---

## 7. Minimal market data

Minimum inputs needed for MVP pricing:

```text
valuation_date
underlying bond clean price or yield
yield curve / discount curve
volatility input
credit spread if required
Treasury FTP / Funding Curve rate, if the deposit funding mode (§4) requires it
```

Clarifications, consistent with `docs/16`:

- **API-first remains the target.** These inputs should eventually come
  from Bloomberg API/BQL, vendor APIs, or internal service APIs where
  available.
- **MVP may use manually supplied, verified inputs** (e.g. a fixture
  snapshot with a recorded source and as-of date) instead of a live
  connector, to unblock the pricing-path slice without building ingestion
  infrastructure first.
- **Manual generic file import is not required for MVP** and must not be
  built as a side effect of this work (`docs/16` §2/§4).
- **Screenshot-assisted capture is not required for MVP** — it remains a
  future fallback helper only (`docs/16` §1, `docs/future_screenshot_assisted_data_capture.md`).

---

## 8. QuantLib usage policy inside BLI MVP

QuantLib is not banned. It is allowed as a computational library, not as
methodology owner:

```text
QuantLib may be used for bond cashflows, schedules, day count, calendar,
curve, discounting, or option-pricing support where appropriate.

QuantLib must not silently define product methodology.
All methodology choices must be explicit in Shiori specs or in this MVP
preflight.
QuantLib usage must be wrapped behind internal interfaces.
Raw QuantLib objects must not leak into product schemas, market-data
snapshots, or public results.
Engine/model version must be recorded in audit trail.
Any QuantLib-based result must be benchmarked against at least one
controlled example or Excel/reference calculation before being treated as
production-like.
```

This mirrors `docs/08_performance_engine_backend_strategy.md`'s existing
"Python as orchestration layer, compiled backends behind stable interfaces"
rule and AGENTS.md rule 13 — it is not a new architectural principle, just
the BLI-specific application of it. No QuantLib integration is implemented
by this PR.

---

## 9. Minimal pricing path (future work, not implemented here)

```text
BondLinkedStructuredProduct definition
+ ValuationContext
+ MarketDataSnapshot / manually supplied MVP inputs
+ Bond reference data
→ pricing engine
→ PricingResult
→ audit trail
```

This mirrors the existing spine contract
(`Product Definition + ValuationContext + MarketDataSnapshot → price(...) →
PricingResult`, `docs/09` §1) — the BLI MVP is a new product type routed
through the same `price(...)` front door (`docs/09`'s "Product-priority
pivot" note: BLI registers behind the same front door, it does not create a
second pricing path). **The pricing engine itself is future work.** No
engine, no registration, and no payoff/cashflow logic is written in this PR.

---

## 10. MVP audit trail

Minimum audit fields for an MVP `PricingResult`:

```text
deal input version
market data as-of
manual input source
reference data source
valuation date
pricing engine version
model/method version
calculation timestamp
user / run id if available
```

These extend, rather than replace, the existing `PricingResult` /
`PricingMessage` conventions referenced in `docs/09` §8. Exact field names
and where they live (on `PricingResult` vs. a separate audit record) are an
implementation decision for a later slice, not this doc.

---

## 11. Proposed implementation sequence after this preflight

Small, independently reviewable slices — not one large PR:

```text
A. DepositLeg schema preflight / implementation
   - resolve the deposit-rate-source decision (§4) before writing code
B. Bond reference data minimal schema / fixture boundary
   - fixture only; must not be embedded in product schemas
C. BondLinkedStructuredProduct wrapper schema
   - links DepositLeg + BondOption; enforces participation_ratio (§5)
D. Minimal manual MVP input bundle / valuation context boundary
   - manually supplied, verified market inputs; no ingestion infrastructure
E. Simple deterministic BLI payoff / cashflow skeleton
   - the smallest possible pricing logic that reproduces MVP economics
F. QuantLib usage policy / prototype benchmark, if needed
   - only if E's deterministic skeleton needs QuantLib support; benchmarked
     per §8 before being treated as production-like
G. MVP runner / JSON example
   - an end-to-end synthetic example wiring A-F together
```

None of A–G is started in this PR. Each should get its own preflight or
implementation PR, following the same "preflight before code" pattern
`docs/15` used for `BondOption`.

---

## 12. Open MVP decisions (not resolved by this doc)

Restated in one place so a future implementer does not have to re-derive
them from §4/§8:

- Deposit rate/yield source: fixed term, Treasury FTP / Funding Curve
  lookup, or both under an explicit mode (§4).
- The `DayCount` / calendar vocabulary decision (A-14), which blocks both
  the deposit leg's day-count field and any bond-leg accrual math.
- Exact field names/location for the MVP audit trail (§10).
- Whether QuantLib is needed at all for the MVP deterministic skeleton
  (slice E), or only if/when slice F is scoped.

---

## 13. Scope boundaries of this PR

Docs only. No `BondLinkedStructuredProduct`, deposit leg, pricing engine,
QuantLib, Bloomberg/API connector, market-data ingestion, Treasury FTP /
Funding Curve, file upload, or screenshot capture code is added. No test is
added. No frozen BLI v1.3 source spec file is edited. Issue #38 is **not**
closed by this PR — `BondOption` (the #38 partial slice, PR #50) is
unaffected, and `BondLinkedStructuredProduct` remains deferred exactly as
`docs/15` described.
