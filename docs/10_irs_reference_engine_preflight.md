# 10 IRS Reference Engine — Design Preflight

Status: **design preflight only — no code, no pricing maths, nothing registered.**

This document defines the design for the *first real per-product pricing engine*
that will sit behind the existing deterministic pricing contract. It is the next
slice of **Issue #10** after PR #23 established the
`price(product, valuation_context, market_snapshot) -> PricingResult` contract.

It is intentionally a preflight: it pins down scope, market-data needs, the
schedule/accrual boundary, day-count handling, the output shape, failure
behavior, and the tests a future implementation PR must add. **It does not
implement any of them.** No PV / DV01 maths, no cashflow generation, no schedule
engine, no curve bootstrapping, no calendar, and no new code are added in the PR
that introduces this document.

Required reading before the implementation slice: `AGENTS.md`, `docs/00_vision.md`,
`docs/01_system_architecture.md`, `docs/04_product_definition_schema.md`,
`docs/08_performance_engine_backend_strategy.md`, `docs/09_mvp_core_runbook.md`
(sections 3, 8, 9), and the existing pricing-contract modules
(`src/shiori_pricing_lab/pricing/result.py`, `engine.py`, `errors.py`).

---

## 1. Purpose

PR #23 added the **contract and routing seam** of the pricing spine but no
per-product engine, so today every product — IRS / OIS / CCS / FX Swap — routes
to `FAILED + UNSUPPORTED_PRODUCT`.

This preflight defines the **first real product engine** to register behind that
seam:

```text
Product Definition + ValuationContext + MarketDataSnapshot
        → price(...) → [IRS reference engine] → PricingResult
```

The engine will:

- be registered for `product_type == "IRS"` via `register_engine("IRS", ...)`
  (or an explicit registry in tests);
- satisfy the existing `PricingEngine` Protocol
  (`price(product, valuation_context, market_snapshot) -> PricingResult`);
- price **one deliberately narrow IRS shape first**, not the whole rates universe;
- return a structured `PricingResult` for every domain outcome (success or
  explicit failure), and leave contract/programming violations to the front door.

It must **not** become a general swap valuation library in its first slice. The
goal is the smallest engine that produces a *real, deterministic* PV for one
simple synthetic IRS and fails explicitly for everything it does not yet support.

This preflight calculates **no values**. Every number mentioned below is a
description of what the *future* engine will compute, not a result.

---

## 2. Product scope

### First supported IRS shape (MVP)

The first engine supports only:

- a vanilla **fixed-vs-floating** interest rate swap;
- **USD-only for the first implementation slice** — both legs share
  `InterestRateSwap.currency`, and that currency must be `Currency.USD`. A
  non-USD product is out of scope and fails explicitly (see §3 and §7). This is
  deliberately tighter than "single currency": the current snapshot/curve layer
  carries **no enforceable curve-currency metadata** (see §3), so the engine
  cannot safely tell which currency a snapshot's curve represents. Other
  currencies stay out of scope until the snapshot/curve layer carries enforceable
  curve-currency metadata and currency-tagged curve selection (see §9);
- exactly **one fixed leg and one floating leg** (the existing
  `InterestRateSwap` schema shape — `fixed_leg: FixedLeg`,
  `floating_leg: FloatingLeg`);
- **opposite** pay/receive directions (already enforced by the schema:
  `fixed_leg.pay_receive` must differ from `floating_leg.pay_receive`);
- **positive notional** (already enforced by the schema);
- **effective_date < maturity_date** (already enforced by the schema);
- the **existing `InterestRateSwap` schema only** — no new product fields;
- **synthetic market data only**.

Because the `InterestRateSwap` schema already enforces notional > 0,
date order, opposite directions, and a required `floating_leg.reset_frequency`,
the engine can treat a constructed product as structurally valid and focus on
*pricing* support, not re-validating deal terms.

### Explicitly out of scope (first slice)

The engine must **fail explicitly** (see §7), never silently approximate, for:

- OIS (`OvernightIndexedSwap`) — its own engine later;
- CCS (`CrossCurrencySwap`) and FX Swap (`FXSwap`) — their own engines later;
- amortizing / non-constant notional swaps;
- stub periods (front or back), broken / irregular dates;
- forward-starting edge cases beyond what the schema already permits
  (`effective_date` after `valuation_date` is allowed as a plain forward start;
  exotic forward-start handling is out of scope);
- historical fixing lookup / past floating fixings (no fixings in the snapshot
  yet — see §3);
- multi-curve bootstrapping (separate discount vs forecast curves);
- collateral / CSA / discounting choices;
- real holiday calendars and business-day adjustment;
- real Bloomberg conventions or production-grade valuation;
- DV01 / scenario results **unless** their methodology is made explicit first
  (see §6).

If the desk hands the engine a product outside this shape, the answer is a
structured `FAILED` result, not a guess.

---

## 3. Required market data

The engine consumes a **normalized `MarketDataSnapshot` only**. It must never
call a provider, CSV loader, manual loader, Bloomberg, or any web/data adapter —
that boundary already holds for the contract (PR #23) and must hold for the
engine.

### Minimal market-data assumption (MVP)

- **One normalized rate curve** built from the snapshot's rates points
  (`MarketDataSnapshot.rates_points`, schema `date, ticker, tenor, value,
  data_type, source`, `value` in decimal terms where 4.25% = 0.0425).
- The engine derives that curve via the existing pricing-layer helper
  `RateCurve.from_snapshot(snapshot)` (in `pricing/curve.py`), which already
  pins the curve to the snapshot's explicit `valuation_date` and maps tenor
  labels to year fractions via `tenor_to_years`.
- **One curve is used as both the discount and the forecast curve** for the MVP.
  Separate discount/forecast curves and proper multi-curve bootstrapping are
  deferred. This single-curve simplification must be recorded in
  `PricingResult.assumptions` (see §6).
- **No bootstrapping yet** — the curve is the simple tenor→rate representation
  `RateCurve` already provides. The engine does not solve for zero rates from
  par instruments in the first slice; if interpolation/zero-rate derivation is
  needed it must be a documented, deterministic rule, not a hidden one.
- **No external data fetching** and **no provider calls inside pricing**.

### Hard boundary (must stay true)

> The IRS reference engine consumes `MarketDataSnapshot`. It must **not** call
> CSV / manual / Bloomberg / web providers, and must **not** import the
> `data.providers` layer. All market state arrives through the passed snapshot
> (and the context's curve-building helpers) only.

If the snapshot lacks the rates points needed to build a usable curve (empty
snapshot, no points, unmappable tenors), the engine returns
`FAILED + MISSING_MARKET_DATA` (see §7) — it never invents a curve.

### What about currency? (USD-only, no curve-currency detection)

> **Current `MarketDataSnapshot.rates_points` does not carry an enforceable curve
> currency field.** Its schema is `date, ticker, tenor, value, data_type,
> source`, and `RateCurve.from_snapshot(...)` simply builds one curve from those
> points with no notion of which currency it represents. Therefore the first IRS
> engine must **not** attempt multi-currency curve selection, and it must **not**
> claim it can detect a wrong-currency curve from the snapshot. It supports only
> **USD synthetic data**; non-USD products **fail explicitly before any curve is
> built** (see §7).

Concretely, the engine checks `product.currency == Currency.USD` **before**
calling `RateCurve.from_snapshot(...)`. If the product is non-USD it returns a
structured `FAILED` result (see §7) rather than building the single synthetic
curve and silently treating it as that currency. A genuinely missing/empty
USD curve still surfaces as `MISSING_MARKET_DATA` (see §7) — but a
*currency mismatch* is a product-side check, not something the engine can infer
from curve data it cannot tag.

Recording the product currency in `assumptions` is still useful for audit, but
it is metadata only; it does not and cannot prove the snapshot's curve is in that
currency. Multi-currency curve selection is deferred to future work (see §9).

---

## 4. Schedule / accrual boundary

This is the most dangerous part of the design, because a sloppy schedule rule
silently produces wrong cashflows. The first implementation must use a
**deliberately simple, deterministic** rule and fail loudly on anything it does
not handle.

### MVP schedule rule

- Generate **regular periods** from `effective_date` to `maturity_date`.
- The period length comes from the **leg schema frequency**
  (`FixedLeg.payment_frequency`, `FloatingLeg.payment_frequency`); each leg has
  its own schedule.
- **No business-day adjustment** in the first implementation. The schema records
  a `business_day_convention`, but resolving it needs a holiday calendar, which
  is out of scope. The first engine treats the schedule as unadjusted calendar
  dates and records `business_day_adjustment_applied = False` in `assumptions`.
- **No holiday calendar.**
- **Clean division required.** If `effective_date → maturity_date` does **not**
  divide into a whole number of periods at the leg frequency (i.e. a stub would
  be needed), the engine returns a structured failure
  (`FAILED + INVALID_PRODUCT`, or a proposed `BAD_SCHEDULE` code — see §7),
  **never** an invented stub period.

### Where schedule generation lives

Decision for the design: schedule generation should live in a **small, reusable,
deterministic schedule helper** (for example
`src/shiori_pricing_lab/pricing/schedule.py`), *not* be buried inside the IRS
engine module — because OIS / CCS will need the same regular-period logic later,
and burying it in the IRS engine would force a copy-paste when the next engine
arrives. However, to keep the *first* implementation slice minimal, it is
acceptable for the helper to start as a tiny function used only by the IRS engine,
as long as it:

- takes explicit inputs (start date, end date, frequency) and returns explicit
  period boundaries;
- uses **no system date** and **no calendar**;
- is independently unit-tested;
- returns/raises an explicit signal on non-clean division that the engine maps to
  a structured `FAILED` result (it must not fabricate a stub).

**No schedule code is written in this preflight PR.** The above only fixes the
contract the future helper/engine must honor.

---

## 5. Day count / accrual assumptions

The engine computes accrual fractions from the leg `DayCount`. The current enum
(`products/enums.py`) is:

| `DayCount` value | First-slice support | Reason |
| --- | --- | --- |
| `ACT_360` | ✅ supported | actual days / 360 — trivially safe and unambiguous |
| `ACT_365_FIXED` | ✅ supported | actual days / 365 — trivially safe and unambiguous |
| `THIRTY_360` | ⚠️ candidate / may defer | needs the 30/360 day-adjustment rules; correct but more rules, easy to get subtly wrong |
| `ACT_ACT_ISDA` | ❌ fail explicitly first | needs per-calendar-year leap-day splitting; highest risk of a subtle error |

### Rules

- Support **only the day counts that can be calculated safely and unambiguously**
  in the first slice. The minimum safe set is **`ACT_360` and `ACT_365_FIXED`**
  (actual day difference over a fixed denominator).
- `THIRTY_360` may be included **only if** the exact 30/360 convention (e.g. the
  end-of-month / 31st handling) is written down and tested; otherwise it is
  deferred and fails explicitly. The implementation slice decides this, but it
  must not ship an *approximate* 30/360.
- `ACT_ACT_ISDA` is **deferred** in the first slice and must **fail explicitly**.
- **Never silently approximate** an unsupported convention. An unsupported
  day count returns a structured `FAILED` result (see §7), never a "close enough"
  number.

Each supported convention's exact formula must be documented in the
implementation PR and pinned by a deterministic test.

---

## 6. Pricing output shape

The future engine populates `PricingResult` (see `pricing/result.py`). This
preflight defines *what* fields to fill, **not their values** — no number here
is a computed result.

| Field | First-slice plan |
| --- | --- |
| `status` | `SUCCESS`, `SUCCESS_WITH_WARNINGS`, or `FAILED` (the only three) |
| `pv` | net present value in `result_currency` on success; `None` on failure |
| `dv01` | populated **only if** the bump methodology is explicitly defined (see below); otherwise stays `None` |
| `cashflows` | a minimal, immutable per-period structure **only if** its shape is defined (see below); otherwise `None` |
| `method` | a short, stable label, e.g. `"irs_single_curve_v0"` |
| `engine_name` / `engine_version` | engine provenance, e.g. `"irs_reference"` / a version string |
| `assumptions` | the explicit simplifications: single curve = discount = forecast, no BDC, day-count used, interpolation rule, forward-start note |
| `warnings` | structured `PricingMessage`s (e.g. `TRADE_MATURED`, `FORWARD_STARTING`, `DATA_QUALITY`) |
| `errors` | structured `PricingMessage`s on `FAILED` |
| `diagnostics` | small free-form engine notes (period counts, curve tenors used) — never load-bearing |

### PV

PV is the net of the two legs from the trade owner's perspective (received minus
paid), discounted on the single MVP curve. The exact discounting and
forecasting formula is defined in the implementation PR, recorded in `method`
and `assumptions`, and pinned by a deterministic test against a hand-checked
synthetic case.

### DV01 (conditional)

`dv01` is populated **only if** the implementation slice explicitly defines:

- the bump size and direction (e.g. +1 bp parallel — note `RateCurve` already
  has `shocked_parallel`);
- whether it is a one-sided or central difference;
- that the bump reuses the same deterministic curve, with no system date and no
  re-fetch.

If that methodology is not defined in the implementation slice, `dv01` stays
`None`. A `None` DV01 is acceptable; a fabricated one is not.

### Cashflows (conditional)

`cashflows` is populated **only if** a minimal immutable structure is defined
(e.g. a tuple of frozen per-period records: leg, start, end, accrual fraction,
rate, amount). Until that structure is specified and tested, `cashflows` stays
`None`. The field is a tuple-or-`None` by contract, preserving the frozen
result's immutability.

> This preflight does **not** calculate `pv`, `dv01`, or any cashflow. It only
> fixes which fields the future engine is responsible for.

---

## 7. Error / warning behavior

The engine reuses the existing `PricingResult` / `PricingMessage` / error-code
model. **Domain failures return** `PricingResult(status=FAILED, errors=[...])`;
**contract / programming violations raise** from `pricing/errors.py`. The engine
never raises for an expected market/deal outcome.

### Already handled by the front door (not the engine's job)

These are checked in `price(...)` *before* the engine is reached and must stay
there:

- `None` product / context / snapshot, or missing required attributes →
  **raise** `PricingContractError`;
- `valuation_context.valuation_date` vs `market_snapshot.valuation_date`
  mismatch → `FAILED + VALUATION_DATE_MISMATCH`;
- context snapshot vs passed snapshot identity mismatch →
  `FAILED + MARKET_SNAPSHOT_MISMATCH`;
- a missing / `None` `valuation_context.market_snapshot` → **raise**.

The IRS engine assumes these already passed and does **not** re-implement them.

### Engine-level failure cases (return a structured `FAILED`)

| Case | Code (existing unless noted) |
| --- | --- |
| Wrong product type routed in (defensive) | `UNSUPPORTED_PRODUCT` |
| Out-of-scope IRS shape (amortizing, stub, etc.) | `INVALID_PRODUCT` (or proposed `UNSUPPORTED_STRUCTURE`) |
| Non-USD IRS (first slice is USD-only) — checked **before** curve construction | `INVALID_PRODUCT` with `detail={"unsupported_currency": <ccy>}`, or a future `UNSUPPORTED_CURRENCY` code if added |
| Empty snapshot / no rates points / no usable curve (USD) | `MISSING_MARKET_DATA` |
| Curve cannot be built (unmappable tenors, empty frame) | `MISSING_MARKET_DATA` |
| Unsupported day count (e.g. `ACT_ACT_ISDA` in first slice) | proposed `UNSUPPORTED_DAY_COUNT`, else `INVALID_PRODUCT` |
| Unsupported frequency for a regular schedule | proposed `UNSUPPORTED_FREQUENCY`, else `INVALID_PRODUCT` |
| Dates/frequency do not divide cleanly (stub needed) | proposed `BAD_SCHEDULE`, else `INVALID_PRODUCT` |
| Unexpected internal error | `ENGINE_ERROR` (the front door already wraps a raising engine into this) |

### Warning cases (return `SUCCESS_WITH_WARNINGS`)

- trade already matured at the valuation date → `TRADE_MATURED`;
- forward-starting trade (effective after valuation date) → `FORWARD_STARTING`;
- thin/low-quality curve input that is still usable → `DATA_QUALITY`.

### Proposed new codes (do **not** implement in this PR)

The current `PricingErrorCode` set is `UNSUPPORTED_PRODUCT`,
`MISSING_MARKET_DATA`, `VALUATION_DATE_MISMATCH`, `MARKET_SNAPSHOT_MISMATCH`,
`INVALID_PRODUCT`, `ENGINE_ERROR`. The cases above can all be expressed with the
existing codes (mainly `INVALID_PRODUCT` plus a `detail` dict). For sharper
machine-branching, the implementation slice **may propose** adding:

- `UNSUPPORTED_CURRENCY`
- `UNSUPPORTED_DAY_COUNT`
- `UNSUPPORTED_FREQUENCY`
- `BAD_SCHEDULE` (or `UNSUPPORTED_STRUCTURE`)

These are **proposals only**. They are not added in this preflight PR. If the
implementation slice does not add them, it must use `INVALID_PRODUCT` with a
descriptive `message` and a `detail` dict (e.g. `{"day_count": "ACT_ACT_ISDA"}`
or `{"unsupported_currency": "EUR"}`) so the reason is still machine-inspectable.

---

## 8. Tests for the future implementation

The **implementation** PR (not this docs-only PR) must add deterministic tests,
likely in `tests/test_pricing_irs_engine.py`, using **synthetic data only**:

1. **Happy path** — a fixed synthetic single-currency IRS with a synthetic curve
   returns `SUCCESS` and a `pv` that is not `None`.
2. **Deterministic result** — the same synthetic input always yields the same
   `pv` (and `dv01` if implemented), pinned to a hand-checked expected value.
3. **Unsupported product still fails** — routing a non-IRS (OIS / CCS / FX Swap)
   to the IRS engine, or the IRS engine seeing a wrong `product_type`, fails
   structurally; the rest of the world stays `UNSUPPORTED_PRODUCT`.
4. **Missing market data** — empty snapshot / no usable curve returns
   `FAILED + MISSING_MARKET_DATA`, `pv is None`.
5. **Non-USD IRS fails explicitly** — a non-USD `InterestRateSwap.currency`
   (e.g. EUR / TWD / JPY) returns a structured `FAILED`
   (`INVALID_PRODUCT` with `detail={"unsupported_currency": ...}`, or a future
   `UNSUPPORTED_CURRENCY` code), `pv is None`, **and the engine does not build or
   use the USD synthetic curve** (assert `RateCurve.from_snapshot` is not reached
   / the curve is never consulted for a non-USD product).
6. **Unsupported day count** returns a structured `FAILED` (proposed
   `UNSUPPORTED_DAY_COUNT`, else `INVALID_PRODUCT` with `detail`), `pv is None`.
7. **Unsupported frequency / non-clean schedule** returns a structured `FAILED`
   (proposed `UNSUPPORTED_FREQUENCY` / `BAD_SCHEDULE`, else `INVALID_PRODUCT`).
8. **No system-date usage** — the engine module(s) contain no `date.today(` /
   `datetime.now(` (mirror the existing guard in
   `tests/test_pricing_engine.py::test_pricing_engine_modules_have_no_system_date`).
9. **No provider imports** — importing the engine pulls in no
   `shiori_pricing_lab.data` provider / CSV / web module (extend the existing
   layering guard).
10. **No mutation** — the engine does not mutate the product, context, or
    snapshot (assert equality / identity of inputs before and after `price`).
11. **No fake zero PV** — every unsupported / failed path returns `pv is None`,
    never a misleading `0.0`.

Do **not** add these tests in this docs-only preflight PR. (There is no existing
docs-only test/check convention to satisfy, so this PR adds documentation only.)

---

## 9. Recommended follow-up implementation slice

The smallest next PR after this preflight should be **one IRS reference engine
only**:

- a single deterministic IRS reference engine registered via
  `register_engine("IRS", ...)` (or an explicit test registry);
- the narrow product shape from §2 (vanilla fixed-vs-floating, **USD-only**,
  regular schedule, positive notional);
- minimal schedule/accrual support from §4 (regular periods, no BDC, no
  calendar, clean-division-or-fail);
- the safe day-count set from §5 (`ACT_360`, `ACT_365_FIXED`; others fail
  explicitly);
- a real, deterministic `pv` on the happy path (DV01 / cashflows only if their
  methodology is explicitly defined, else left `None`);
- explicit structured failures for every unsupported path (§7);
- synthetic-data deterministic tests (§8);
- **no external data, no UI, no AI layer, no historical valuation loop.**

That slice slots into the spine without touching the data, valuation, products,
or UI layers, and turns IRS from `UNSUPPORTED_PRODUCT` into a real (if minimal)
priced product.

### Deferred: multi-currency support

Lifting the USD-only constraint is a **separate, later** slice, because the
current snapshot/curve layer cannot tell curves apart by currency. It requires,
at minimum:

- **currency-tagged curves or snapshot metadata** — an enforceable curve-currency
  field on `MarketDataSnapshot` (or its rates points), or named/identified curves,
  so a curve's currency is known rather than assumed;
- **curve selection by product currency** — the engine picking the correct curve
  for `product.currency` instead of using "the" single curve;
- **dedicated tests proving EUR / TWD / JPY products cannot accidentally use a USD
  synthetic curve** — i.e. a non-USD product either selects its own currency's
  curve or fails, but never silently prices off the USD curve.

Until that lands, non-USD IRS products must keep failing explicitly (see §3, §7).
This deferral does **not** touch `MarketDataSnapshot`, `RateCurve`, or the product
schemas in this preflight.

---

## 10. Relationship to existing docs

This preflight extends — and does not replace — the runbook and development log:

- `docs/09_mvp_core_runbook.md` §8–§9 already names "design preflight for the
  first per-product reference engine" as the recommended next step; this document
  is that preflight, narrowed to **IRS first**.
- `docs/00_development_log.md` records the PR #23 contract checkpoint; this
  preflight is the design step before the first engine implementation.

Issue #10 remains **open** — its first slice (the contract) is done; the
remaining work is the per-product engines, of which this IRS engine is the first.
This document does not close Issue #10.
