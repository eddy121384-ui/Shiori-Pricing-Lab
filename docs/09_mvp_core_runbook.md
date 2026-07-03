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
`yield_convention`, or `compounding_frequency` field, and can proceed in
#38. `BondLinkedStructuredProduct` **should be deferred** unless explicitly
accepted as a **non-economic placeholder** — its deposit leg carries
**contractual economic terms** (deposit rate/yield, principal repayment
rule) that a schema cannot omit and still reproduce the customer's
cashflows, which is a separate concern from (and in addition to) the
`DayCount`/calendar blocker (A-14). If built at all in #38, the wrapper must
be labeled incomplete for valuation and its `participation_ratio` must be
derived from — or validated against — `bond_option.notional /
deposit_notional`, never stored as an independent, freely-set field. A
complete, economic wrapper requires a later, separately reviewed issue that
resolves the deposit-leg economic terms, the funding-curve-vs-fixed-rate
question, and the `DayCount`/calendar decision. `docs/15` §6 lists the
acceptance-criteria tests the future #38 implementation PR should add; none
of that schema/test code is written yet.

### `BondOption` schema landed — Issue #38 partial

`BondOption` (`src/shiori_pricing_lab/products/bond_option.py`) is
implemented as the pure deal-term schema described in `docs/15` §2/§5, using
the controlled vocabulary from PR #45 (`PayoffBasis`, `OptionType`,
`ExerciseStyle`, `SettlementType`, `Position`, `Currency`). It validates the
`payoff_basis` / strike cross-field rule and the `exercise_style` /
`exercise_start_date` rule from `docs/15` §2.3, and carries no `day_count`,
`yield_convention`, `compounding_frequency`, Bond Master, market-data, or
pricing-output field. Tests are in `tests/test_bond_option.py`.

**`BondLinkedStructuredProduct` is still not implemented, not even as a
placeholder.** It remains deferred until the deposit-leg economic terms
(deposit rate/yield source, principal repayment rule), the Treasury FTP /
Funding Curve semantics (`docs/16`), and the `DayCount`/calendar decision
(A-14) are resolved in a separate, reviewed slice. This PR does not
register any pricing engine for `BondOption`, does not touch market-data
ingestion, and does not start Issues #39–#42 or #44.

### BLI MVP vertical-slice checkpoint

- Before starting any `BondLinkedStructuredProduct`, deposit leg, BLI
  pricing engine, or BLI market-data/fixture work, agents must read
  `docs/17_bli_mvp_vertical_slice_preflight.md`.
- The MVP target is one plain-vanilla bond, one deposit leg, one embedded
  `BondOption` leg, European exercise and cash settlement first — not the
  full BLI v1.3 product/connector universe.
- The deposit rate/yield source (fixed term vs. Treasury FTP / Funding
  Curve lookup vs. both under an explicit mode) is an **open decision**;
  do not silently pick one when implementing the deposit leg (`docs/17` §4).
- `participation_ratio` on any future wrapper must be derived from, or
  validated against, `bond_option.notional / deposit_notional` — never a
  freely-set field (`docs/15` §3.3, `docs/17` §5).
- QuantLib, if used, is a computational library only — it must not
  silently define product methodology, and any QuantLib-based result must
  be benchmarked before being treated as production-like (`docs/17` §8).
- Implement in the small slices `docs/17` §11 proposes (A–G); do not fold
  the whole BLI MVP into one PR.

### DepositLeg schema preflight checkpoint (Slice A)

- Before any `DepositLeg`, wrapper, or Treasury FTP rate-parsing work,
  agents must read `docs/18_deposit_leg_schema_preflight.md`.
- The Treasury FTP sheet is a rate matrix (business_date × currency ×
  tenor × quote_side → rate). Rates are quoted as **percent**
  (`3.5500` = `3.5500%`); pricing code must use the decimal form
  (`0.035500`), never the raw percent number.
- Default quote side is `MID` and must be configurable; `BID`/`OFFER`
  usage must never be silently chosen by code. Currencies without a
  bid/mid/offer breakdown are treated as MID-equivalent — do not infer a
  spread the sheet does not provide.
- `deposit_rate_mode` (`FIXED_RATE` / `TREASURY_FTP_REFERENCE` /
  `MANUAL_VERIFIED_RATE`) is the recommended schema boundary — do not
  hard-code a single rate source.
- `DepositLeg` must never carry a `business_date`/`as_of` field, a
  resolved market rate, or manual-rate audit metadata (source, as-of,
  entered-by). `TREASURY_FTP_REFERENCE` stores only a stable
  `ftp_rate_selector` (currency/tenor/quote_side); `MANUAL_VERIFIED_RATE`
  stores only a `manual_input_reference` marker. The dated rate, its
  resolution, and its provenance live in `MarketDataSnapshot` / the MVP
  input bundle / the audit trail (`docs/18` §4.2, §4.3, §8).
- `day_count`, `business_day_convention`, and `calendar` remain deferred
  on the deposit leg, same reasoning as `BondOption` and the still-open
  A-14 decision.

### DepositLeg / Treasury FTP controlled vocabulary landed

`DepositRateMode`, `TreasuryFTPQuoteSide`, and `TreasuryFTPTenor`
(`src/shiori_pricing_lab/products/enums.py`, tested in
`tests/test_deposit_leg_vocab.py`) are the enum foundation `docs/18` §12
required before `TREASURY_FTP_REFERENCE` mode can be enabled. **The
tenor-vocabulary gap the checkpoint above used to flag is now closed at
the vocabulary level:** `TreasuryFTPTenor` covers `O/N` through `3Y` plus
`DEMAND_SAVINGS`, is deliberately separate from `Frequency` (a
payment/reset period vocabulary, not a tenor label set — tests assert the
two enums' value sets are disjoint), and rejects unsupported/ambiguous
spellings (`ON`, `O_N`, `1WK`, `12M`, whitespace, blank) through the
existing `coerce_enum` path.

**This does not mean `TREASURY_FTP_REFERENCE` mode or a `DepositLeg`
schema exists yet.** No `DepositLeg` schema, Treasury FTP parser,
ingestion, or market-data code was added — only the controlled
vocabulary a future `DepositLeg` implementation must validate against
(`docs/18` §12's "controlled FTP tenor vocabulary must land first"
condition is now satisfied; the schema implementation itself is still a
separate future slice).

### `DepositLeg` schema landed — BLI MVP Slice A

`DepositLeg` and `TreasuryFTPRateSelector`
(`src/shiori_pricing_lab/products/deposit_leg.py`) are implemented as the
schema `docs/18` §3/§4/§8 described, using `DepositRateMode`,
`TreasuryFTPQuoteSide`, `TreasuryFTPTenor`, and a new, narrow
`PrincipalRepaymentRule` enum (`FULL_PRINCIPAL_AT_MATURITY` only). Exactly
one rate-source field (`fixed_deposit_rate` / `ftp_rate_selector` /
`manual_input_reference`) is populated per `deposit_rate_mode`, and the
other two must be `None`. `DepositLeg` carries a `leg_type` discriminator
(not `product_type`), consistent with it being a leg component consumed by
a future wrapper, not a standalone product.

**`TREASURY_FTP_REFERENCE` mode still stores only a selector
(currency/tenor/quote_side) — no `business_date`, `as_of_timestamp`,
`source_file_name`, `loaded_at`, or resolved rate.** **`MANUAL_VERIFIED_RATE`
mode still stores only a `manual_input_reference` marker — no manual rate
value or its audit metadata.** Both remain resolved at pricing time from a
future `MarketDataSnapshot` / MVP input bundle / audit-provenance layer, not
from `DepositLeg` itself. Tests are in `tests/test_deposit_leg.py`,
including a dataclass-fields boundary test asserting no market-data or
pricing-run field exists on either `DepositLeg` or
`TreasuryFTPRateSelector`.

**No Treasury FTP parser, ingestion, `MarketDataSnapshot` implementation,
MVP input bundle implementation, pricing engine, QuantLib, or
`BondLinkedStructuredProduct` wrapper was added.** Issue #38 is unaffected.
Before starting any of those, agents must still read `docs/18` and `docs/17`.

### BLI wrapper schema preflight checkpoint

- Before implementing `BondLinkedStructuredProduct`, agents must read
  `docs/19_bli_wrapper_schema_preflight.md`.
- The wrapper binds exactly one `DepositLeg` and exactly one `BondOption`
  as **embedded objects** (not reference IDs) — no registry/persistence
  layer exists yet.
- `participation_ratio`: recommended as a **derived property** from
  `bond_option.notional / deposit_leg.deposit_notional`, not a freely-set
  input field, unless a concrete consumer needs the optional-validated
  design (`docs/19` §6). Either way, the canonical mismatch case
  (inconsistent stored ratio) must be rejected, never silently accepted.
- Required cross-component checks: `deposit_leg.currency ==
  bond_option.currency`; the option's **effective settlement date**
  (`bond_option.expiry_date + settlement_lag_days` calendar days, a
  calendar-day approximation — no calendar engine) must be on or before
  `deposit_leg.maturity_date`. A bare `expiry_date <= maturity_date`
  check that ignores `settlement_lag_days` is **not** sufficient
  (`docs/19` §7). Whether `bond_option.expiry_date` must also be on/after
  `deposit_leg.start_date` is a **separate open question** the
  implementation slice must decide explicitly, not silently choose
  (`docs/19` §7).
- **`bond_option.settlement_type` must be `SettlementType.CASH`** for the
  MVP wrapper — construction must raise otherwise. Physical delivery is
  out of MVP scope and deferred to a later custody/settlement slice;
  `BondOption` itself stays general, the wrapper narrows it (`docs/19`
  §8).
- `DepositLeg.principal_repayment_rule` stays `FULL_PRINCIPAL_AT_MATURITY`;
  option payoff is computed separately at the wrapper/pricing level, never
  folded into `DepositLeg`. Do not add a payoff-linkage enum yet
  (`docs/19` §8).
- The wrapper must never carry market data, resolved rates, or pricing
  output (`docs/19` §9's full exclusion list) — same boundary already
  enforced on `DepositLeg` and `BondOption`, restated for the wrapper.
- No wrapper code, pricing engine, QuantLib, payoff skeleton,
  `MarketDataSnapshot`, MVP input bundle, or UI exists yet. Issue #38
  remains open.

### `BondLinkedStructuredProduct` wrapper schema landed

`BondLinkedStructuredProduct`
(`src/shiori_pricing_lab/products/bond_linked_structured_product.py`) is
implemented as the wrapper schema `docs/19` described — **wrapper schema
only**, per the checkpoint above. It binds exactly one embedded
`DepositLeg` and one embedded `BondOption`; `participation_ratio` is a
**derived-only property**, not a constructor field. Validation enforces
currency consistency, `SettlementType.CASH`-only settlement,
`PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY`, `bond_option.
expiry_date >= deposit_leg.start_date` (this implementation's resolution
of the open question above), and the mandatory effective-settlement-date
guardrail (`expiry_date + settlement_lag_days` calendar days `<=
deposit_leg.maturity_date`). Tests are in
`tests/test_bond_linked_structured_product.py`, including a
dataclass-fields boundary test asserting no duplicated component field or
market-data/pricing field exists on the wrapper.

**Still no pricing engine, payoff skeleton, QuantLib,
`MarketDataSnapshot`, MVP input bundle, Treasury FTP parser, ingestion, or
UI.** Those remain future slices per `docs/17` §11 (B–G). Issue #38 is
unaffected and remains open.

### BLI bond reference data preflight checkpoint (Slice B)

- Before implementing a Bond Reference Data / Bond Master schema or
  fixture, agents must read
  `docs/20_bli_bond_reference_data_preflight.md`.
- The required field list is transcribed from
  `docs/bond_linked_structured_pricer/ANNEX_B_v1.3.md` §B.5 — a future
  implementation slice must confirm it against Annex A/B again before
  writing code, not just trust this doc's transcription.
- Bond Reference Data holds the bond's own static terms (coupon,
  maturity, day count, business day convention, yield convention,
  redemption amount, callable/sinkable flags) — it must never carry
  market observations, a valuation date, or pricing output (`docs/20` §3),
  same exclusion pattern already used for `DepositLeg` and the wrapper.
- MVP plain-vanilla eligibility rejects callable, sinkable, and (by
  default) `OTHER`-yield-convention bonds; how floating-rate/amortizing/
  convertible/etc. bonds are excluded (no direct Annex B field exists for
  them) is an **open item** the implementation slice must resolve
  explicitly, not silently assume (`docs/20` §5).
- For MVP, reference data is a small, manually reviewed fixture — no
  parser, no generic file import, no Bloomberg/API connector in this
  slice (`docs/20` §7).
- Future pricing resolves `bond_option.underlying_isin` against this
  fixture and must **block** — not guess or silently downgrade — on a
  missing or ineligible bond (`docs/20` §8).
- Carries forward, without resolving, `docs/14` F-08 (no
  `m`/compounding-frequency field for `yield_convention = OTHER`) and the
  still-open `DayCount` vocabulary question (A-14) as applied to the
  bond's own accrual convention (`docs/20` §6, §11).
- No Bond Master code, fixture, parser, pricing, `MarketDataSnapshot`,
  MVP input bundle, QuantLib, or UI exists yet. Issue #38 remains open.

### `BondReferenceData` schema landed — BLI MVP Slice B

`BondReferenceData` (`src/shiori_pricing_lab/reference_data/`) is
implemented as the Bond Master reference-data schema `docs/20` described.
**Package decision (explicit):** it lives in a new top-level package,
`shiori_pricing_lab.reference_data`, a sibling to `products`, not part of
it — nothing is exported from `products/__init__.py`, and no existing
product schema is touched. It carries every required Annex B §B.5 field
(`docs/20` §4), reusing the existing `Currency` / `Frequency` / `DayCount`
/ `BusinessDayConvention` / `BondYieldConvention` enums, plus two new
enums this slice adds: `BondType` (only `FIXED_COUPON_BULLET` is
MVP-pricing-eligible; the other members exist so non-vanilla bonds are
still representable as reference data) and `BondStatus`
(`ACTIVE`/`INACTIVE`).

`coupon >= 0` is enforced at construction (`coupon == 0` is accepted as
valid reference data); `first_coupon_date` / `last_coupon_date` are
required constructor arguments, non-null, strict `YYYY-MM-DD`.
**MVP pricing eligibility is a separate function**
(`reference_data.eligibility.is_mvp_pricing_eligible`), not part of
construction validation — a callable, sinkable, zero-coupon, or
`OTHER`-yield-convention bond all construct successfully but are marked
ineligible with an explicit reason. Zero-coupon bonds are explicitly
**valid-but-ineligible** for this slice (the stricter of `docs/20` §5's
two allowed choices). Irregular first/last coupon stub detection is
**not** implemented (no schedule engine exists); instead the small,
manually reviewed synthetic fixture (`reference_data/fixtures.py`, four
bonds covering eligible-plain-vanilla, zero-coupon, callable, and
floating-rate-note cases) is limited to regular-coupon, no-stub bonds by
construction. No lookup-by-ISIN helper, pricing, cash-flow generation,
schedule engine, QuantLib, `MarketDataSnapshot`, MVP input bundle, file
parser, ingestion, or Bloomberg/API connector was added. Tests are in
`tests/test_bond_reference_data.py`. Issue #38 remains open.

### BLI ISIN resolution preflight checkpoint

- Before implementing any resolver that maps `BondOption.underlying_isin`
  to a `BondReferenceData` record, agents must read
  `docs/21_bli_isin_resolution_preflight.md`.
- The MVP resolution source is `SYNTHETIC_BOND_FIXTURES`
  (`shiori_pricing_lab.reference_data.fixtures`) only — no Bloomberg/API
  connector, file parser, database, generic ingestion, or
  screenshot/OCR capture in this slice.
- Matching is **exact ISIN string match only** — no fuzzy/partial
  matching, no check-digit correction.
- A missing ISIN is a **not-found** result; a duplicate ISIN across
  fixture records is a **fixture data-integrity error**, not a normal
  lookup outcome, and must fail explicitly rather than silently
  returning the first match.
- **Eligibility is not re-implemented at the resolver layer.** A future
  resolver calls the existing `reference_data.eligibility.
  is_mvp_pricing_eligible(bond)` once per found record and reports all
  of its reasons — callable, sinkable, zero-coupon, `OTHER` yield
  convention, non-`FIXED_COUPON_BULLET` `bond_type`, and inactive status
  are all already covered there (`docs/20`, PR #58).
- A missing or ineligible bond must **block** explicitly — no guessing,
  no fallback bond, no silent downgrade, no partial pricing (`docs/20`
  §8, restated and detailed in `docs/21` §5).
- The resolver only answers found/not-found, the record, eligible/
  ineligible, and the blocking reason — it must never compute PV, DV01,
  cashflows, or a coupon schedule, and it must never carry
  `business_date`, `valuation_date`, a resolved rate, or any other
  market-data field (`docs/21` §6/§7).
- `docs/21` §8 sketches (non-bindingly) a
  `resolve_bond_reference_data(underlying_isin, fixtures)` function as
  the smallest next coding slice — **not implemented yet**. No resolver
  code, pricing, `MarketDataSnapshot`, MVP input bundle, or product
  schema change exists. Issue #38 remains open.

### ISIN resolver landed — BLI resolution slice

`resolve_bond_reference_data` and `BondReferenceResolutionResult`
(`src/shiori_pricing_lab/reference_data/resolution.py`) implement the
minimal resolver `docs/21` §8 recommended. Matching is exact-ISIN-string
only; `fixtures` is a plain parameter defaulting to
`SYNTHETIC_BOND_FIXTURES`. A single match calls the existing
`is_mvp_pricing_eligible` once and returns `FOUND_ELIGIBLE` or
`FOUND_INELIGIBLE` (with every eligibility reason preserved, joined into
`block_reason`); no match returns `NOT_FOUND` (never an exception); more
than one match for the same `isin` raises
`DuplicateBondReferenceDataError` — a fixture data-integrity bug, never
resolved by picking the first or last record. The result carries
`requested_isin`, `status`, `bond_reference_data`, `eligibility_reasons`,
`block_reason`, and an audit-only `source_fixture_name` — no
`business_date`, `valuation_date`, `as_of_timestamp`, or other
market-data field. `BondOption`, `DepositLeg`, and
`BondLinkedStructuredProduct` are unmodified; the resolver is not yet
wired into any pricing engine. Tests are in
`tests/test_bond_reference_resolution.py`. Issue #38 remains open.

### BLI market data / MVP input bundle preflight checkpoint

- Before implementing any `MarketDataSnapshot` (BLI-scoped), MVP input
  bundle, or bundle builder, agents must read
  `docs/22_bli_market_data_input_bundle_preflight.md`.
- Defines the four-layer boundary: product terms
  (`BondLinkedStructuredProduct`/`DepositLeg`/`BondOption`) → reference
  data (`BondReferenceData`/`resolve_bond_reference_data`/
  `is_mvp_pricing_eligible`) → market data (bond price/yield, yield
  curves, deposit/FTP rate observations, curve mapping, pricing date,
  source/status) → the future MVP input bundle a pricing engine
  consumes. None of the first three layers is modified by `docs/22`.
- Grounds the required market-data field lists in the frozen
  `docs/bond_linked_structured_pricer/ANNEX_B_v1.3.md` §B.1 (Bond
  Price/Yield File) and §B.2 (Yield Curve File), and the frozen
  `SPEC_v1.3.md` §3.5/§7.3 curve-purpose rules: **Option Discount Curve
  and Bond Reference Curve must never be mixed**, and **the deposit leg
  must not silently reuse the Option Discount Curve** unless an explicit
  mapping rule says so. Missing curve mapping or missing curve data
  blocks pricing (SPEC §7.3), restated as a hard block on future bundle
  construction, not a warning.
- The future MVP input bundle must not be constructed if: product
  validation fails; the ISIN is not found or resolves ineligible
  (`docs/21`); required bond price/yield, curve mapping, or deposit/FTP
  rate data is missing; quote side is ambiguous; or data status is
  inactive/stale/invalid. A bundle either exists complete and valid, or
  does not exist — no partial-bundle concept.
- Restates and extends the `docs/21` §7.1 point-in-time boundary (the
  Codex P2 fix from PR #59) one layer up: `MarketDataSnapshot` is
  point-in-time; market data and reference data must share a coherent
  valuation context; a future bundle builder must not mix "latest"
  reference data with historical market data — no look-ahead bias.
- Restates the Treasury FTP percent-vs-decimal rule (`3.5500` means
  `3.5500%` = decimal `0.035500`) and the quote-side policy (`docs/18`
  §2.4/§5) — no silent quote-side choice, no silent BID→MID conversion,
  no silent use of latest/stale data.
- Recommends five future implementation slices (MarketDataSnapshot
  preflight → minimal MarketDataSnapshot dataclass with a synthetic
  fixture → MVP input bundle → bundle builder → pricing engine skeleton)
  — none started here.
- No `MarketDataSnapshot`, MVP input bundle, bundle builder, pricing
  engine, or any code change exists yet. `BondOption`, `DepositLeg`,
  `BondLinkedStructuredProduct`, `BondReferenceData`, and
  `resolve_bond_reference_data` are all unmodified. Issue #38 remains
  open.

### BLI `MarketDataSnapshot` schema preflight checkpoint

- Before implementing the BLI-scoped `MarketDataSnapshot` class or its
  synthetic fixture, agents must read
  `docs/23_bli_market_data_snapshot_schema_preflight.md`.
- **Recommended module location:** a new module inside the existing
  `data/` package — `src/shiori_pricing_lab/data/bli_snapshot.py` — not
  a new top-level package, and not fields bolted onto the existing
  vanilla-rates-core `MarketDataSnapshot` (`data/snapshot.py`). Market
  data already has a designated home (`AGENTS.md` rule 2); the BLI
  snapshot needs its own module because its shape (bond quote, curves by
  purpose, FTP observation, volatility, credit spread) is structurally
  unrelated to the existing DataFrame-of-rates-points class.
- **Recommended class name:** `BLIMarketDataSnapshot`, not
  `MarketDataSnapshot` — deliberately distinct from the existing class
  in `data/snapshot.py` to avoid import confusion between two
  same-named but structurally different classes. Either recommendation
  may be overridden by the implementation slice if it states a reason.
- Narrows `docs/22`'s conceptual field list into per-sub-observation
  groups (snapshot-level; bond quote; curves; deposit/FTP; volatility;
  credit spread) with a proposed field name for each, and recommends
  storing both `ftp_rate_percent_value` and `ftp_rate_decimal_value`
  explicitly (matching `docs/18` §2.1's own recommendation) rather than
  one ambiguous rate field.
- Curve purpose (Bond Reference Curve / Option Discount Curve / Deposit
  Curve / Funding Curve) must be carried explicitly per curve record —
  restates that the Option Discount Curve and Bond Reference Curve must
  never be mixed and that the deposit leg must not silently reuse the
  Option Discount Curve without an explicit mapping rule (SPEC §3.5).
- Volatility and credit spread must each be explicit fields with an
  audit trail for any override/fallback — no invented value, no silent
  flat-vol fallback, no silent zero-spread default (restated from
  `docs/22` §6.5/§6.6, SPEC §§3.2/3.3/7.4/7.5).
- Proposes a minimal five-value status vocabulary
  (`ACTIVE`/`STALE`/`INVALID`/`MISSING`/`MANUAL_VERIFIED`) as a starting
  point, explicitly not finalized — the implementation slice must
  confirm or replace it.
- Scopes (but does not build) a minimal synthetic-fixture shape: one
  valuation date, one resolved eligible ISIN, one bond quote, one Bond
  Reference Curve, one Option Discount Curve, one Deposit Curve/FTP
  observation, one explicit volatility input, one explicit credit-spread
  treatment — plus a list of negative-fixture concepts for future tests
  (missing quote/curve/FTP-rate/volatility/spread, stale/invalid status,
  ambiguous quote side).
- Lists a validation-rules checklist for the future dataclass (no system
  date, no duplicate curve purpose without explicit handling, exact ISIN
  match with the resolver's result, explicit FTP percent/decimal
  consistency, no silent override without an audit field) — none
  implemented here.
- No `BLIMarketDataSnapshot` class, MVP input bundle, bundle builder,
  pricing engine, or any code change exists yet. `BondOption`,
  `DepositLeg`, `BondLinkedStructuredProduct`, `BondReferenceData`, and
  `resolve_bond_reference_data` are all unmodified. Issue #38 remains
  open.

### Market-data ingestion terminology checkpoint

- Before any future market-data ingestion, funding-curve, deposit-leg, or
  Bloomberg connector issue, agents must read
  `docs/16_market_data_ingestion_terminology.md`.
- Do not build generic FTP market-data import unless explicitly scoped.
- Do not confuse FTP/SFTP transport with Treasury FTP / Funding Curve.
- Do not start broad ingestion work while working on Issue #38
  BondOption-only schema.
