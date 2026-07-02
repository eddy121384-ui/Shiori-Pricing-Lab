# 09 MVP Core Runbook

A short checkpoint of the Vanilla Rates Core spine after PRs #15, #16, and #17.

This is a runbook, not a design doc. It records what is wired up today, what
must stay true, and where to go next. For design rationale see
`docs/01_system_architecture.md`, `docs/02_data_and_market_snapshots.md`, and
`docs/03_valuation_context.md`.

## 1. Current completed flow

```text
provider → MarketDataSnapshot → ValuationContext → RateCurve → scenario
```

This path is implemented end to end and exercised by tests. The Streamlit
prototype (`src/shiori_pricing_lab/app/streamlit_app.py`) drives exactly this
flow: it loads sample data through a provider, freezes a snapshot for an
explicitly chosen valuation date, builds a context, derives a curve, and applies
a parallel shock.

### Pricing contract seam (PR #23, Issue #10 first slice)

The product-pricing spine now has its stable internal front door:

```text
Product Definition + ValuationContext + MarketDataSnapshot → price(...) → PricingResult
```

`price(product, valuation_context, market_snapshot)` (in
`src/shiori_pricing_lab/pricing/engine.py`) is the single entry point that the
UI, historical-valuation, backtesting, and AI layers will call. **It is
contract-only — this is not yet real pricing.** The current front door is
intentionally conservative: it

- validates the call contract shape (raising on malformed input);
- rejects a malformed context (missing or `None` `market_snapshot`);
- rejects a context snapshot that is a different object from the passed
  `market_snapshot` (`MARKET_SNAPSHOT_MISMATCH`), and a valuation-date mismatch;
- routes by `product.product_type` through a registry;
- returns `FAILED + UNSUPPORTED_PRODUCT` when no engine is registered.

The first per-product engine is now registered (PR #29): a **USD-only IRS
reference engine** prices a supported USD synthetic IRS to a deterministic PV.
OIS / CCS / FX Swap still return `FAILED + UNSUPPORTED_PRODUCT`, and unsupported
IRS shapes fail explicitly. See section 8 for the engine checkpoint.

## 2. What each layer owns

| Layer | Module | Owns | Must not |
| --- | --- | --- | --- |
| Data providers | `src/shiori_pricing_lab/data/providers.py` | Load + normalize raw rows (CSV / manual) into the rates-points schema; validate minimum fields | Price, build curves, render UI, call AI |
| MarketDataSnapshot | `src/shiori_pricing_lab/data/snapshot.py` | Freeze normalized market data for one explicit valuation date; defensive-copy the data; carry `source` / `metadata` | Import the pricing layer; fetch data; know about curves |
| ValuationContext | `src/shiori_pricing_lab/valuation/context.py` | Bind valuation date + snapshot + reporting currency / model settings; enforce date consistency; orchestrate curve building | Use the system date; mutate the snapshot |
| RateCurve | `src/shiori_pricing_lab/pricing/curve.py` | Represent a simple curve (tenor → rate) from snapshot/normalized points; tenor→years mapping; parallel shock | Read CSV / call providers; invent data |
| Scenario shock | `src/shiori_pricing_lab/pricing/scenario.py` | Deterministic parallel curve shock and `change_bp` output | Call providers; depend on the valuation layer |
| Pricing engine contract | `src/shiori_pricing_lab/pricing/result.py`, `errors.py`, `engine.py` | Define `PricingResult` / messages; the `PricingEngine` Protocol + registry; the front-door `price(...)` that validates inputs, routes by product type, and returns a structured result | Fetch market data; use the system date; import data providers / UI / AI; compute PV / DV01 (no engines registered yet) |

### Normalized rates-points schema

```text
date, ticker, tenor, value, data_type, source
```

`value` is a decimal rate (4.25% = 0.0425). A parallel shock is in basis points
(+1 bp = +0.0001 in decimal rate terms). See docstrings in
`pricing/curve.py` and `data/providers.py`.

## 3. What must stay true

These are load-bearing invariants. Breaking one is a regression even if tests
pass:

1. **`valuation_date` is always explicit.** Snapshot and context both require it
   and have no default; blank/empty is rejected.
2. **No system date in pricing / valuation.** Never `date.today()` in curve,
   scenario, snapshot, or context code. Historical valuation depends on this.
3. **Providers stay adapters.** All raw data access (CSV/manual today) lives in
   `data/providers.py`. Nothing else reads files.
4. **Pricing / scenario do not call providers directly.** They consume snapshot/
   context-derived inputs. The data layer also must not import the pricing layer.
5. **Synthetic data only in the repo.** No real market data, Bloomberg output,
   positions, or secrets. Sample data is clearly labeled `source=synthetic`.

### Do not break these invariants (pricing contract)

These extend the rules above and apply to the pricing engine seam (PR #23):

- **Product schemas must not import data / valuation / pricing.** The `products`
  package stays pure (guarded by a test).
- **Pricing engines must not fetch market data directly** and must consume a
  normalized `MarketDataSnapshot` only — never providers, CSV, Bloomberg, or web
  data.
- **Pricing engines must not use `date.today()` / `datetime.now()`.** The
  valuation date comes only from the passed objects.
- **`ValuationContext.valuation_date` and `MarketDataSnapshot.valuation_date`
  must remain explicit and consistent.**
- **`valuation_context.market_snapshot` and the explicit `market_snapshot`
  argument to `price(...)` must refer to the same object** (else
  `MARKET_SNAPSHOT_MISMATCH`); a missing or `None` context snapshot is a contract
  violation.
- **No `pricing ↔ valuation` runtime import cycle** — the engine references
  context / snapshot / product types only under `TYPE_CHECKING`.
- **The AI layer must call deterministic pricing APIs** and must not invent
  pricing outputs.
- **Unsupported products must fail explicitly** (`FAILED + UNSUPPORTED_PRODUCT`),
  never return a fake zero PV.

## 4. How to run tests

Full deterministic suite:

```bash
python -m pytest -q
```

Lint the core spine and tests:

```bash
python -m ruff check src/shiori_pricing_lab tests
```

Tests run without Bloomberg or any external dependency. Relevant test files:

- `tests/test_data_providers.py` — provider loading + schema validation
  (missing columns, empty frame).
- `tests/test_valuation_context.py` — snapshot/context creation, explicit-date
  behavior, defensive copies, layering.
- `tests/test_curve_and_scenario.py` — curve building, +1 bp / +5 bp shocks.
- `tests/test_spine_flow.py` — end-to-end provider → snapshot → context → curve
  → scenario.

## 5. What Issues #1 and #2 achieved

- **Issue #1 — market data normalization + MarketDataSnapshot workflow.** CSV/
  manual providers normalize into the rates-points schema; `MarketDataSnapshot`
  freezes that data for an explicit valuation date with `source`/`metadata`;
  required-field and empty-input validation is in place and directly tested
  (PR #17). Pricing never reads CSV.
- **Issue #2 — RateCurve, ValuationContext, scenario shock flow.** Curves build
  from synthetic snapshot data; `ValuationContext` carries an explicit valuation
  date and snapshot reference (never the system date); the parallel shock is
  deterministic with verified +1 bp / +5 bp behavior; pricing/scenario do not
  call providers.

Net result: the reusable spine from `docs/00_vision.md`
(`Product Definition + Valuation Context + Market Data Snapshot + Pricing
Engine = Valuation Result`) exists for the rates curve case. The Product
Definition piece now has its first slice too (IRS / OIS schemas, PR #19 —
see section 7); the remaining gap is the pricing engine itself.

## 6. What is intentionally NOT done yet

Deliberately out of scope at this checkpoint:

- Bloomberg or any external market-data adapter.
- Database / persistent storage (SQLite / DuckDB / Parquet).
- AI-native inquiry / chat layer.
- OIS / CCS / FX Swap pricing engines (the pricing *contract* exists as of
  PR #23 and a **USD-only IRS** engine is registered as of PR #29, but OIS / CCS
  / FX Swap still return `FAILED + UNSUPPORTED_PRODUCT`).
- Historical valuation and the backtesting loop.
- Production UI (the Streamlit app is a prototype only).
- Richer snapshot content (FX, vols, fixings, reference data) — rates points only.
- Curve bootstrapping, calendars, day-count conventions.

## 7. Product schema checkpoint (PR #19 + PR #21, Issue #12 complete)

The Product Definition piece of the spine now exists for all four MVP vanilla
rates products, in `src/shiori_pricing_lab/products/`. **Issue #12's product-
schema scope is complete.** This is **schema only — there is still no pricing
engine.**

| Product | Schema status |
| --- | --- |
| IRS (`InterestRateSwap`) | ✅ Defined and validated (PR #19) |
| OIS (`OvernightIndexedSwap`) | ✅ Defined and validated (PR #19) |
| CCS (`CrossCurrencySwap`) | ✅ Defined and validated (PR #21) |
| FX Swap (`FXSwap`) | ✅ Defined and validated (PR #21) |

Supporting types: `FixedLeg`, `FloatingLeg`, `CrossCurrencyLeg`, and the enums
`PayReceive`, `BuySell`, `Currency`, `Frequency`, `DayCount`,
`BusinessDayConvention`, `FloatingIndex`, `CompoundingMethod`. Shared low-level
validation helpers live in `products/_validation.py`. Validated by
`tests/test_products.py` and `tests/test_products_ccs_fxswap.py`
(`python -m pytest -q` → 129 passed; `ruff` clean).

CCS carries **per-leg currency and notional** via `CrossCurrencyLeg` (two
currencies, two notionals); FX Swap is a **flat** schema whose `near_rate` /
`far_rate` are frozen trade terms, not live market data.

What the schema enforces (from Codex review):

- `product_type` is non-overridable (a fixed discriminator), so an IRS always
  serializes as `"IRS"` and an OIS as `"OIS"`.
- Enum-backed fields are coerced/validated at runtime; blanks and unknown
  strings are rejected with clear errors.
- Schedule dates require strict `YYYY-MM-DD`; compact and ISO week-date forms
  are rejected.
- OIS `floating_leg.reset_frequency` may only be `None` or `Frequency.DAILY`.

Load-bearing invariant for this layer (keep it true):

- **Product definitions describe the trade only.** They must not contain market
  data, valuation date, PV, DV01, curves, discount factors, fixings, or any
  pricing result. This applies equally to the CCS and FX Swap schemas. The
  `products` package imports no data/pricing/valuation module (guarded by a
  test).

## 8. Pricing engine contract checkpoint (PR #23, Issue #10 first slice)

The deterministic pricing engine **contract** now exists, in
`src/shiori_pricing_lab/pricing/`. This is the **boundary only — there is still
no per-product pricing.**

| Piece | Status |
| --- | --- |
| `PricingResult` + `PricingStatus` / `PricingMessage` / error & warning codes (`result.py`) | ✅ Defined |
| Raise-path exceptions (`errors.py`) | ✅ Defined |
| `PricingEngine` Protocol, `PricingEngineRegistry`, `register_engine`, front-door `price(...)` (`engine.py`) | ✅ Defined |
| IRS pricing engine (USD-only reference) | ✅ Registered (PR #29, Issue #27) |
| OIS / CCS / FX Swap pricing engines | ❌ Not started (all return `FAILED + UNSUPPORTED_PRODUCT`) |

Validated by `tests/test_pricing_engine.py` (`python -m pytest -q` → 175 passed;
`ruff` clean). The contract does **not** calculate values: `pv`, `dv01`,
`cashflows`, and `scenario_results` exist on `PricingResult` but default to
`None`. Failure handling is hybrid — domain failures return
`PricingResult(status=FAILED, errors=[...])`; contract / programming violations
raise from `pricing/errors.py`. See the contract invariants under section 3.

Issue #10 status: **first slice complete (PR #23); the issue is now closed
(completed).** The first per-product engine (USD-only IRS) exists (see section
8.1); the remaining work is **not** Issue #10 itself but downstream / follow-up
per-product engines (OIS / CCS / FX Swap and deferred extensions).

### 8.1 IRS reference engine checkpoint (PR #29, Issue #27)

The first real per-product engine is registered, in
`src/shiori_pricing_lab/pricing/irs_engine.py` (`IRSReferenceEngine`), wired via
`register_engine("IRS", IRSReferenceEngine())`. It uses a small deterministic
schedule helper, `pricing/schedule.py` (`generate_regular_schedule`).

What it does (deliberately narrow):

- prices a **USD-only** synthetic fixed-vs-floating IRS to a **deterministic
  `pv`** behind the existing `price(...)` contract;
- builds **one** `RateCurve` from the snapshot (via `RateCurve.from_snapshot`)
  and uses it as **both the discount and the forecast curve** — no bootstrapping,
  no calendar, no business-day adjustment;
- supports only `ACT_360` and `ACT_365_FIXED` day counts.

What it returns:

- a supported USD IRS → deterministic `pv`; **`dv01` and `cashflows` stay
  `None`**;
- every unsupported / out-of-scope path → a structured `FAILED` with
  **`pv is None`** (never a fake `0.0`). This includes non-USD **product**
  currency, non-USD **reporting** currency, unsupported floating-leg conventions
  (only a quarterly `USD_SOFR_TERM_3M` leg with reset = payment frequency and no
  compounding is supported), unsupported day count / frequency / non-clean
  schedule (`INVALID_PRODUCT`), and missing / unusable market data
  (`MISSING_MARKET_DATA`).

Validated by `tests/test_irs_reference_engine.py` (`python -m pytest -q` → 190
passed at merge — final PR #29 state after the Claude Code P2 fixes; the earlier
initial Codex run reported 186; `ruff` clean). **Issue #27 and Issue #10 are
both closed (completed).** OIS / CCS / FX Swap remain unsupported; the downstream
historical valuation loop (#13) and AI inquiry contract (#14) are unchanged.

## 9. Recommended next development step

**Design preflight for the first per-product reference engine — not full
valuation.**

The pricing contract is in place, so the next step is **not** to jump into full
IRS / OIS / CCS / FX Swap valuation. It is a short design preflight for the
*first* per-product reference engine, likely the smallest **IRS or OIS**
reference pricing slice. The preflight should define, before any code:

- the required market data and where it comes from (a normalized
  `MarketDataSnapshot` only — no Bloomberg / external data);
- the explicit assumptions the reference engine makes;
- which cases are unsupported and must fail clearly (e.g. missing market data →
  `FAILED + MISSING_MARKET_DATA`);
- the deterministic tests that pin the result.

A possible next implementation slice:

- one product type only;
- a simple deterministic reference engine registered behind `price(...)`;
- explicit missing-market-data failures;
- no Bloomberg / external data, no UI, no AI, no historical loop.

This slots cleanly into the spine documented above without changing any of the
existing layers.

### Preflight written: IRS reference engine

This design preflight now exists as `docs/10_irs_reference_engine_preflight.md`.
It scopes the **first per-product reference engine to IRS only** (vanilla
fixed-vs-floating, **USD-only**, regular schedule, synthetic data — non-USD fails
explicitly because the snapshot/curve layer has no enforceable curve-currency
metadata yet), defines
the market-data, schedule/accrual, day-count, output, and failure behavior, and
lists the tests the implementation slice must add. The implementation slice
described there has since landed (**PR #29, Issue #27**): the USD-only IRS
reference engine is now registered, so a supported USD IRS returns a
deterministic PV instead of `FAILED + UNSUPPORTED_PRODUCT`. See section 8.1 for
the engine checkpoint. **Issue #10 is now closed (completed)**; the remaining
per-product engine work (OIS / CCS / FX Swap and deferred extensions) is
downstream / follow-up.

### Preflight written: historical valuation loop (Issue #13)

`docs/11_historical_valuation_loop_preflight.md` is the design preflight for the
first **historical valuation loop skeleton** (Issue #13). It reuses the existing
single-date `price(...)` contract once per explicit, caller-supplied valuation
date over **synthetic** snapshots, and collects a stable per-date result table
(failures included as rows, never a fake `0.0`). It is **docs only — no loop is
implemented**; it creates no second pricing path, fetches no data, invents no
rates, and does not start Issue #14. Issue #13 remains open.

### Product-priority pivot: Bond Linked Structured Pricer (PR #33)

The near-term product priority has **shifted** from the Vanilla Rates Core /
IRS-first path to the **Bond Linked Structured Pricer (BLI) MVP**. This is a
re-ordering, **not** a teardown: the Rates Core / IRS work stays as the shared
deterministic pricing infrastructure, and the spine
(`Product Definition + ValuationContext + MarketDataSnapshot → price(...) →
PricingResult`) is unchanged — BLI will register behind the same `price(...)`
front door.

PR #33 merged the authoritative BLI **v1.3 reference specs** into
`docs/bond_linked_structured_pricer/` (Annex A = pricing methodology, Annex B =
FTP / market-data file spec, Annex C = UI/UX guidance). Codex review already
found and fixed three Annex A methodology defects there (clean-price tree coupon
handling; price-based put-call parity notional scaling; parity tolerance basis),
which is why authoritative methodology docs must get **quant-style review before
implementation**.

As a result, the **near-term priority is BLI methodology teardown and an
integration preflight**, not the historical valuation loop. **Issue #13 is
deferred / reframed** for later EOD / revaluation / warehouse valuation use, and
**Issue #14 stays deferred.** See `docs/13_bond_linked_pivot_checkpoint.md`; the
next planned PR is
`docs/14_bond_linked_spec_teardown_and_integration_preflight.md`.

### BLI methodology teardown / integration preflight complete (PR #35)

`docs/14_bond_linked_spec_teardown_and_integration_preflight.md` is now merged
(PR #35), **completing** the BLI methodology teardown and integration preflight.
It is the **guide for BLI implementation issue sequencing**: it reviews the
Annex A methodology, assesses market-data readiness (Annex B / SPEC §7), maps BLI
onto the existing spine, and carries a severity-ranked risk list plus a §6
roadmap. The **existing deterministic pricing spine remains the target** — BLI
registers behind the same `price(...)` front door, not a parallel path.

Next work (recorded, **not started here**):

- **Convert the `docs/14` §6 roadmap into concrete GitHub issues.**
- **Do not start pricing engine code yet.** The first implementation slice is
  **prerequisites** — enum gap analysis, product schema, and the market-data
  boundary — **not** the American tree, AI inquiry, UI, Bloomberg implementation,
  or a QuantLib backend.

### BLI controlled-vocabulary enums landed (PR #45, Issue #37)

**PR #45 is merged.** It is the code-level follow-up to the `docs/14` enum-gap
preflight (F-16/A-14) — controlled vocabulary only, no schema, no snapshot, no
pricing engine:

- `Currency` gained `NZD`, `KRW`, `HKD`, `SGD` (Annex A/B markets NZ/KR/HK/SG).
- Five new BLI product enums landed in `products/enums.py`, not yet referenced
  by any schema: `PayoffBasis` (`PRICE`/`YIELD`), `OptionType` (`CALL`/`PUT`),
  `ExerciseStyle` (`EUROPEAN`/`AMERICAN`), `SettlementType`
  (`CASH`/`PHYSICAL`), `Position` (`BUY`/`SELL`).
- `BondYieldConvention` landed (`SEMI_ANNUAL_COMPOUND`, `ANNUAL_COMPOUND`,
  `SIMPLE_YIELD`, `JAPANESE_COMPOUND`, `OTHER`).
- `PricingErrorCode.MISSING_REFERENCE_DATA` landed, for reference/static data
  that is present but carries an unrecognised convention (distinct from
  `MISSING_MARKET_DATA`, a required market observation that is absent).
- Validated by `tests/test_bli_enums.py` (`python -m pytest -q` → 215 passed;
  `ruff check .` clean).

**Deliberately still deferred:**

- **`DayCount` vocabulary** (`ACT_365`, `ACT_365F`, market `ACT/ACT` variants)
  remains explicitly deferred pending a reviewed, Annex-driven decision
  (`docs/14` §5, amendment A-14). The existing `ACT_360` / `ACT_365_FIXED` /
  `THIRTY_360` / `ACT_ACT_ISDA` members are unchanged and not aliased.
- **A Bond Master / jurisdiction enum** (beyond `Currency`) was not added and
  stays deferred unless a future Bond Master or `MarketDataSnapshot` extension
  issue actually needs one.

**Status (precise):** PR #45 completed the **first code-level
controlled-vocabulary slice** (currencies, BLI product enums,
`BondYieldConvention`, `MISSING_REFERENCE_DATA`). **Issue #37 remains open**
because the `DayCount` and market/jurisdiction vocabulary decisions above are
**still deferred** — the enum-gap resolution is not finished until those are
explicitly resolved or deliberately scoped into the next issue. **Issue #38**
(BLI product schemas for `BondOption` / `BondLinkedStructuredProduct`) may be
**prepared** next, but it **must not land product schemas that depend on
unresolved `DayCount` / Bond Master convention assumptions**. Before #38 can be
considered complete, **either** (1) #38 explicitly **excludes** `DayCount` /
Bond Master convention fields and keeps them in the Bond Master / later issues,
**or** (2) the `DayCount` vocabulary decision is made first in a reviewed
prerequisite slice. Do **not** treat #38 as fully unblocked without that
qualifier. **Do not start Issues #39–#42 yet, and do not start Black-76 /
Issue #44 yet.**

### BLI product-schema preflight for Issue #38

`docs/15_bli_product_schema_preflight_issue_38.md` answers, before any #38
code is written, whether `BondOption` / `BondLinkedStructuredProduct` can be
pure deal-term schemas without the still-unresolved `DayCount` / Bond Master
convention decision. **Conclusion:** `BondOption` can be defined as a fully
pure deal-term schema (identity, option terms, strike/payoff-basis
cross-field validation, dates, notional, position) with **no** `day_count`,
`yield_convention`, or `compounding_frequency` field. A **deliberately
minimal** `BondLinkedStructuredProduct` wrapper (deposit
notional/currency/dates recorded but not used for accrual, an embedded
`BondOption`, `participation_ratio`) is also safe. The **full** SPEC §6.1
deposit leg (Day Count, Business Day Convention, Deposit Rate/Yield, Deposit
Curve ID) is **not** safe for #38 and must be split into a later,
separately reviewed issue once the `DayCount` decision (A-14) is made.
`docs/15` §6 lists the acceptance-criteria tests the future #38
implementation PR should add; none of that schema/test code is written yet.
