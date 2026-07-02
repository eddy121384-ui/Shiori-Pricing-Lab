# 19 BLI Wrapper Schema Preflight

Status: docs-only preflight. No `BondLinkedStructuredProduct` code, wrapper
schema code, pricing engine, QuantLib, payoff skeleton, or tests are added
by this doc.

## 1. Purpose

`BondOption` (PR #50) and `DepositLeg` (PR #54) now exist as reviewed,
tested leg/component schemas, alongside the controlled vocabulary
(`DepositRateMode`, `TreasuryFTPQuoteSide`, `TreasuryFTPTenor`,
`PrincipalRepaymentRule`, PR #53/#54) they depend on. The next risky
boundary is not another standalone leg — it is the **wrapper
relationship**:

```text
BondLinkedStructuredProduct
= DepositLeg
+ BondOption
+ relationship rules
```

`docs/15` §3 already rejected a "minimal wrapper" (deposit
notional/currency/dates + embedded option + freely-set
`participation_ratio`) as economically incomplete. `docs/17` §5 and
`docs/18` §9 both restate that `participation_ratio` must be derived or
validated, never freely set. This doc is the concrete preflight that turns
those restated rules into a specific, implementable wrapper schema
boundary — the first **product-level** object in the BLI slice, as
distinct from the two leg/component schemas that precede it.

This doc does not implement `BondLinkedStructuredProduct`, a pricing
engine, or any payoff logic. It decides what the future wrapper schema
owns, what it must not own, and what remains an open, explicitly-flagged
decision.

---

## 2. What the wrapper owns

The wrapper is the layer that expresses "this `BondOption` is sold as part
of a structured note funded by this `DepositLeg`." Concretely, it owns:

- **The relationship between exactly one `DepositLeg` and exactly one
  `BondOption`** for MVP (§4) — not a portfolio, not multiple legs per
  side.
- **A product-level identity** (`product_id`) and discriminator
  (`product_type`), since — unlike `DepositLeg` (a `leg_type` component,
  docs/18 §1) — the wrapper *is* the tradeable product; `DepositLeg` and
  `BondOption` are its components.
- **Cross-component consistency**: currency match, notional-derived
  `participation_ratio` (§6), and date ordering across the two components
  (§7) — none of which either component can check on its own, since each
  only knows about itself.
- **The relationship between `principal_repayment_rule` and the option
  payoff** at the level of "how do these two components combine into what
  the customer receives" (§8) — not the payoff calculation itself, only
  which components participate in it.

## 3. What the wrapper must not own

- **Anything already owned by `DepositLeg` or `BondOption`.** The wrapper
  does not duplicate `deposit_notional`, `bond_option.notional`,
  `deposit_rate_mode`, `payoff_basis`, dates, or any other field that
  already lives on the embedded components — duplicating a field creates a
  second source of truth that can silently drift from the first.
- **Market data, resolved rates, or pricing-run inputs** — restated fully
  in §9; none of these live on any product schema, wrapper included.
- **Pricing output** — PV, option value, margin, customer return, a
  scenario table (§10).
- **Payoff/accrual calculation logic** — the wrapper records *which*
  components combine and *what rule* governs the combination; it does not
  compute the combination's numeric result (§10).

---

## 4. MVP wrapper shape

```python
@dataclass(frozen=True)
class BondLinkedStructuredProduct:
    product_id: str
    deposit_leg: DepositLeg
    bond_option: BondOption
    participation_ratio: float | None = None
    product_type: str = field(init=False, default="BOND_LINKED_STRUCTURED_PRODUCT")
```

This is **illustrative only** — no code is added by this doc. It binds
**exactly one `DepositLeg` and exactly one `BondOption`** for MVP; a
multi-leg or multi-option wrapper is out of scope (`docs/17` §2's "single
deposit leg / single embedded bond option leg" MVP scope). Whether
`participation_ratio` is even a constructor field, and if so how it is
validated, is the open question §6 resolves.

---

## 5. Embedded objects vs references

Two shapes were considered:

**Option A — embedded objects** (`deposit_leg: DepositLeg`, `bond_option:
BondOption`), matching the illustrative shape above and the existing
`CrossCurrencySwap.leg_1: CrossCurrencyLeg` pattern.

**Option B — reference IDs** (`deposit_leg_id: str`, `bond_option_id:
str`), which would require a registry or lookup layer to resolve at
construction or validation time.

**Recommendation: embedded objects (Option A) for MVP.** No database or
registry layer exists yet anywhere in this repo — every existing product
schema (`CrossCurrencySwap`, `InterestRateSwap`) embeds its components
directly, never by ID. Embedding keeps validation deterministic and
testable in isolation (a wrapper test can construct a `DepositLeg` and a
`BondOption` inline and assert on the combination, with no fixture/lookup
setup), matching every other schema-level test in this repo. A future
persistence layer, if one is ever built, can normalize embedded objects
into a reference/ID scheme at that layer without changing this schema's
economic meaning — normalization is a storage concern, not a schema
concern (`docs/04`'s existing product/market-data separation reasoning
applies by analogy here: don't let a future infrastructure need leak into
today's schema).

---

## 6. Participation ratio derivation / validation

```text
derived_participation_ratio = bond_option.notional / deposit_leg.deposit_notional
```

Two options, per `docs/15` §3.3 / `docs/17` §5 / `docs/18` §9's restated
rule that `participation_ratio` must never be a freely-set, independently
stored field:

**Option A — derived-only (no input field).** `participation_ratio` is
exposed as a `@property` computed from `bond_option.notional /
deposit_leg.deposit_notional`, never accepted as a constructor argument.

- **Pros:** eliminates duplicated truth entirely — there is exactly one
  number, computed the same way every time, and no invalid combination can
  ever be constructed. Simplest to validate (nothing to validate; it is
  arithmetic). Matches how `FXSwap` deliberately does *not* store forward
  points separately from `near_rate`/`far_rate`, "to avoid a contradictory
  second source of truth" (existing `fx_swap.py` docstring reasoning).
- **Cons:** a term sheet or Excel model that quotes `participation_ratio`
  as an explicit deal term has no matching schema field to round-trip
  against; a caller must recompute it from the two notionals every time
  they want to display or log it.

**Option B — optional validated input field.** `participation_ratio: float
| None = None`; if provided, the wrapper validates it equals
`bond_option.notional / deposit_leg.deposit_notional` within a tiny,
explicit tolerance, and rejects mismatches.

- **Pros:** lets the schema carry a user-visible deal term that matches how
  a trader or term sheet actually expresses the trade, useful for
  reconciliation against an external Excel/term-sheet source. `docs/15`
  §3.3 already accepted this as one of the two safe designs (the other
  being derive-only).
- **Cons:** duplication risk is real even with validation — a tolerance
  band, however tiny, is still a second source of truth with a margin of
  disagreement; the validation logic itself is another thing that must be
  implemented correctly and tested, whereas Option A has no such logic to
  get wrong.

**Silent mismatch must never be allowed under either option.** The
canonical failure case both options must reject:

```text
deposit_leg.deposit_notional = 100
bond_option.notional = 50
participation_ratio = 2.0   # derived ratio is 0.5, not 2.0 -- must raise
```

**Recommendation: Option A (derived-only property), unless a concrete,
already-known consumer needs to round-trip an explicit
`participation_ratio` deal term.** No such consumer is known at this point
in the MVP (no term-sheet ingestion, no Excel reconciliation tool, no UI
yet) — every value that would populate the field is always recomputable
from `deposit_leg.deposit_notional` and `bond_option.notional`, both of
which are already schema fields. Following `docs/17`'s "smallest usable
MVP" framing (§1), the schema with strictly fewer moving parts and zero
possible-mismatch surface is the safer default; Option B can be added
later, additively, as an optional field with tolerance validation, if a
real consumer needs it — moving from A to B is a strict widening (adding
an optional field with validation), while moving from B to A would be a
narrowing that could break any caller relying on the field. **This
recommendation is not final** — the implementation slice (§11) should
confirm it against any concrete requirement that emerges before writing
code, but absent such a requirement, start with Option A.

---

## 7. Currency, notional, and date consistency checks

Required future validation, none of which either `DepositLeg` or
`BondOption` can check alone:

- **Component types:** `deposit_leg` must be a `DepositLeg` instance;
  `bond_option` must be a `BondOption` instance — `TypeError` on mismatch,
  matching the existing `CrossCurrencyLeg`/`CrossCurrencySwap` type-check
  pattern (`isinstance` check, not a duck-typed attribute check).
- **Currency consistency:** `deposit_leg.currency == bond_option.currency`
  — **no silent cross-currency BLI wrapper in MVP.** A structured note
  funded in one currency with an option leg struck in another is a real
  possible future structure, but it introduces an FX/quanto dimension that
  is out of scope for the "smallest usable MVP" (`docs/17` §1); reject it
  explicitly rather than silently allowing a mismatched pair to construct.
- **Notional consistency:** both `deposit_leg.deposit_notional > 0` and
  `bond_option.notional > 0` are already enforced by each component's own
  `__post_init__` — the wrapper does not need to re-check positivity, only
  that both notionals are available to derive/validate
  `participation_ratio` (§6).
- **Date consistency — `product_id` non-blank** and, for the two
  component objects' own dates:
  - `deposit_leg.start_date < deposit_leg.maturity_date` — already
    enforced by `DepositLeg.__post_init__`; not re-checked by the wrapper.
  - `bond_option.expiry_date <= deposit_leg.maturity_date` — **required.**
    An option that expires after the deposit that funds it has already
    matured cannot be settled against that deposit within the structured
    note's own term; the deposit leg's maturity is the outer boundary of
    the structure's life.
  - If `bond_option.exercise_style == AMERICAN`,
    `bond_option.exercise_start_date` must remain strictly before
    `bond_option.expiry_date` — already enforced by `BondOption`'s own
    `__post_init__`; not re-checked by the wrapper.
  - **Whether `bond_option.expiry_date` must also be on/after
    `deposit_leg.start_date` is an open question, not decided here.** A
    plausible argument exists either way: requiring it prevents an option
    that "expires" before the funding deposit even begins (which would be
    economically meaningless for this structure); *not* requiring it could
    matter if the option and deposit are negotiated with slightly
    different effective dates in practice, or if a future structure wants
    the option leg struck before the deposit is funded. **This preflight
    does not decide it — the future wrapper implementation slice (§11)
    must make this decision explicitly (as a validation rule, one way or
    the other) rather than silently choosing an answer inside the code.**

---

## 8. Principal repayment and payoff boundary

- **MVP remains cash-settlement first**, per `docs/17` §2 — the wrapper
  does not need to validate `bond_option.settlement_type` beyond what
  `BondOption` already enforces; it is not re-derived or re-checked by the
  wrapper for MVP. (A future slice could add a wrapper-level check that
  `bond_option.settlement_type == SettlementType.CASH` if MVP wants to
  reject physical delivery at the wrapper level, but that decision is
  deferred here since `docs/17` §2 already frames cash-first as the MVP
  choice, not a hard prohibition on the schema.)
- **`DepositLeg.principal_repayment_rule` should remain
  `FULL_PRINCIPAL_AT_MATURITY`** — the only member `PrincipalRepaymentRule`
  currently defines (PR #54). The wrapper does not add new
  `PrincipalRepaymentRule` members and does not require the embedded
  `DepositLeg` to use a different one.
- **Option payoff is a wrapper-level relationship / pricing-result
  concern, not a `DepositLeg` concern.** `docs/18` §9 already states this:
  the deposit leg returns full principal at maturity; the option payoff is
  "calculated and applied separately at the wrapper level," never folded
  into `DepositLeg`'s own repayment formula. This doc does not change that
  boundary — it only confirms the wrapper is where that combination is
  *expressed as a relationship*, not *computed as a number* (§3, §10).
- **Wrapper-level payoff linkage enum: not added yet.** Values like
  `OPTION_PAYOFF_PAID_SEPARATELY` / `OPTION_PAYOFF_ADDED_TO_REDEMPTION`
  were considered. **Recommendation: do not add a payoff linkage enum in
  the wrapper schema slice.** Nothing in the MVP schema needs to
  distinguish these cases yet — no pricing or payoff code exists to
  consume the distinction, and adding an enum with no consumer risks
  guessing at a vocabulary that a future payoff/pricing slice would need
  to revisit anyway. Wrapper payoff linkage / customer payoff presentation
  belongs to a **future payoff/pricing slice** (`docs/17` §11 slice E),
  once there is an actual deterministic payoff skeleton that needs the
  distinction to be meaningful.

---

## 9. Market-data / pricing boundary

Restated in full so the future wrapper implementation has a single
checklist (all of these already excluded from `DepositLeg` and
`BondOption` individually; restated here because a wrapper is where a
"combine everything" instinct is most likely to accidentally reintroduce
one of them):

```text
FTP business date
resolved FTP rate
rate_percent
rate_decimal
manual verified rate
manual rate source / as-of / entered_by / run_id
bond clean price
bond yield
volatility
curve
spread
pricing result
PV
option premium
customer return
bank margin
```

All of these belong to a future MVP input bundle / `MarketDataSnapshot` /
`PricingResult` / audit trail (`docs/16`, `docs/17` §7/§10, `docs/18` §8) —
**never the wrapper product schema.** The wrapper's job is to say *which*
`DepositLeg` and *which* `BondOption` are linked and *how* they are meant
to combine (`participation_ratio`, date/currency consistency,
`principal_repayment_rule`) — not to carry or compute any number that
requires market data or a pricing engine to produce.

**The wrapper must not implement pricing.** It must not calculate option
payoff, deposit accrual, option value, PV, margin, a scenario table, or
customer return. Those are exclusively future pricing-engine
responsibilities, consumed through the existing `price(...)` front door
(`docs/09` §1, `docs/17` §9) — the wrapper is a `Product Definition`, the
left-hand side of that contract, never the pricing logic itself.

---

## 10. Deferred items

Explicitly not decided or built by this doc:

- Whether `participation_ratio` is Option A or Option B (§6) —
  recommendation given, not finalized until the implementation slice
  confirms no concrete consumer needs Option B.
- Whether `bond_option.expiry_date` must be on/after
  `deposit_leg.start_date` (§7) — open question, must be decided
  explicitly in the implementation slice, not silently chosen.
- Whether the wrapper should validate `bond_option.settlement_type ==
  CASH` at the wrapper level (§8) — not required by this doc; MVP cash-
  first is a scope choice from `docs/17` §2, not enforced here as a hard
  schema rule.
- A wrapper-level payoff linkage enum (§8) — explicitly deferred to a
  future payoff/pricing slice.
- Multi-leg / multi-option wrappers, portfolio-level BLI structures — out
  of MVP scope entirely (`docs/17` §2).
- Everything already deferred by `docs/17` and `docs/18`: Treasury FTP
  parser/ingestion, `MarketDataSnapshot`, MVP input bundle, Bond Master
  fixture, pricing engine, QuantLib, UI, Bloomberg/API connector, file
  upload, screenshot capture.

---

## 11. Recommended next implementation slice

**Implement `BondLinkedStructuredProduct` wrapper schema only.** That
slice should:

- add the wrapper dataclass (§4's illustrative shape, refined per the
  decisions this doc leaves open);
- validate the embedded `DepositLeg` and `BondOption` (type checks, §7);
- derive or validate `participation_ratio` per the confirmed choice
  between Option A and Option B (§6);
- enforce currency consistency and the date-consistency rules in §7,
  explicitly deciding the open `expiry_date`-vs-`start_date` question
  rather than leaving it unhandled;
- add tests (valid construction, currency mismatch rejection, date
  mismatch rejection, participation-ratio consistency/mismatch rejection,
  a dataclass-fields boundary test mirroring `tests/test_deposit_leg.py`'s
  "no market-data or pricing-run field" test);
- export the wrapper from `products/__init__.py`.

That slice must still **not** implement: pricing; a payoff skeleton;
QuantLib; `MarketDataSnapshot`; the MVP input bundle; a Treasury FTP
parser; ingestion; or UI. It must not close Issue #38 — the wrapper slice
is downstream of, not a replacement for, the `BondOption` partial slice
(PR #50) that satisfies #38's own narrower scope.

---

## 12. Acceptance checklist for future code PR

A future wrapper-implementation PR should satisfy:

- `BondLinkedStructuredProduct` binds exactly one `DepositLeg` and exactly
  one `BondOption`, both type-checked (`TypeError` on mismatch).
- `product_id` non-blank; `product_type` is a fixed
  `"BOND_LINKED_STRUCTURED_PRODUCT"` discriminator (`field(init=False)`),
  matching the existing `FXSwap`/`InterestRateSwap`/`BondOption` pattern.
- `deposit_leg.currency == bond_option.currency`, or construction raises.
- `bond_option.expiry_date <= deposit_leg.maturity_date`, or construction
  raises.
- The `expiry_date`-vs-`start_date` question (§7) is resolved one way or
  the other, with the choice documented in the implementation, not left
  implicit.
- `participation_ratio` is either a derived `@property` (Option A) or an
  optional field validated against `bond_option.notional /
  deposit_leg.deposit_notional` within an explicit, documented tolerance
  (Option B) — never a freely-set, unvalidated field.
- The canonical mismatch case (`deposit_notional=100`,
  `bond_option.notional=50`, an inconsistent stored `participation_ratio`
  such as `2.0`) is rejected, not silently constructed.
- No market-data, pricing-run, or pricing-output field (§9's full list)
  exists on the wrapper — a dataclass-fields boundary test asserts this,
  mirroring `tests/test_deposit_leg.py`.
- No payoff, accrual, PV, or margin calculation logic exists anywhere in
  the wrapper module.
- The wrapper is exported from `products/__init__.py` and covered by
  tests for valid construction, each rejection rule above, and the
  boundary test.
- Issue #38 remains open and is not referenced as closed by that PR.
