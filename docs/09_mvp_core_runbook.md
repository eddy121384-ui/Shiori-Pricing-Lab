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
  Reference Curve, one Option Discount Curve, **one Deposit Curve plus
  one separate deposit-rate input matching `DepositLeg.
  deposit_rate_mode`** — the Deposit Curve is the deposit leg's own
  discounting/funding curve input, distinct from the deposit-rate input
  itself: for `FIXED_RATE` the rate is already on `DepositLeg` (but the
  Deposit Curve may still be required for discounting); for
  `TREASURY_FTP_REFERENCE` a matching FTP observation resolves the rate
  (it does not replace the Deposit Curve); for `MANUAL_VERIFIED_RATE` a
  manual verified rate audit record is required — plus one explicit
  volatility input and one explicit credit-spread treatment, so the
  positive fixture is complete enough for the future `docs/22` bundle
  gates. Also lists negative-fixture concepts for future tests (missing
  quote/curve/FTP-rate/volatility/spread, stale/invalid status,
  ambiguous quote side).
- Lists a validation-rules checklist for the future dataclass, including
  **curve duplicate detection keyed at the curve-node level, not merely
  `currency + curve_purpose`**: Annex B models a curve as multiple
  tenor/rate rows, so a normal curve has several rows sharing the same
  currency and curve purpose across different tenors — that is expected
  and valid, not a duplicate. The duplicate key is conceptually
  `business_date`/`valuation_date` + `curve_id`/`curve_name` +
  `currency` + `curve_purpose` + `tenor` (+ `source_system`/version if
  relevant); future implementation must reject a duplicate or
  conflicting row for the same curve identity + tenor + valuation
  context, never by silently picking the first/last row. The checklist
  also covers no system date, exact ISIN match with the resolver's
  result, explicit FTP percent/decimal consistency, and no silent
  override without an audit field — none implemented here.
- No `BLIMarketDataSnapshot` class, MVP input bundle, bundle builder,
  pricing engine, or any code change exists yet. `BondOption`,
  `DepositLeg`, `BondLinkedStructuredProduct`, `BondReferenceData`, and
  `resolve_bond_reference_data` are all unmodified. Issue #38 remains
  open.

### `BLIMarketDataSnapshot` schema and synthetic fixture landed

`BLIMarketDataSnapshot` (`src/shiori_pricing_lab/data/bli_snapshot.py`)
is implemented as the minimal BLI-scoped market-data schema `docs/23`
described. **Module and class name follow `docs/23` §3.3/§3.4 exactly:**
a new module inside the existing `data/` package, with a class name
deliberately distinct from the existing vanilla-rates-core
`MarketDataSnapshot` (`data/snapshot.py`) — the two remain unrelated and
unmodified by each other.

Component objects: `BLIBondQuote`, `BLICurvePoint`,
`BLIDepositRateObservation`, `BLIVolatilityInput`,
`BLICreditSpreadInput` — all frozen dataclasses, all required to carry
their own `source_system` and per-sub-observation `status`
(`BLIMarketDataStatus`: `ACTIVE`/`STALE`/`INVALID`/`MISSING`/
`MANUAL_VERIFIED`, docs/23 §10's proposed minimal vocabulary, not
finalized). `BLICurvePurpose` (`BOND_REFERENCE_CURVE`/
`OPTION_DISCOUNT_CURVE`/`DEPOSIT_CURVE`/`FUNDING_CURVE`) is carried
explicitly on every curve record, never inferred from currency alone.
`BLIVolatilityBasis`, `BLICreditSpreadTreatment`, and `BLIQuoteBasis`
(bond quote price/yield basis, **`data`-local, Codex P3 review of
PR #63** — deliberately not `products.enums.PayoffBasis`, which
documents an unrelated bond-option-payoff concept) are new controlled
vocabularies local to this module; `TreasuryFTPQuoteSide` and
`TreasuryFTPTenor` are reused from `products.enums`.

**Validation landed, per `docs/23` §12 (as corrected by Codex P2 review
of PR #63):** frozen dataclasses throughout; no
`date.today()`/`datetime.now()` anywhere (`valuation_date` is parsed
only for `YYYY-MM-DD` format validity); required string fields reject
blank/whitespace; numeric fields reject NaN/infinity; **`BLIBondQuote`
requires at least one of `clean_price_per_100`/`yield_value` — both may
be present, since docs/23 §4.2 describes the field as "and/or" and a
real feed may report both for one observation; each field is validated
independently when present, and `price_type` no longer forces the other
field to be absent — no yield-to-price or price-to-yield conversion is
ever performed**; the Treasury FTP percent/decimal pair
(`ftp_rate_percent_value`/`ftp_rate_decimal_value`) must agree within a
small tolerance (`3.5500` ⇔ `0.0355`) or construction is rejected;
curve-node duplicate/conflict detection is keyed at `curve_id` + `tenor`
(so multiple tenor rows sharing one `currency` + `curve_purpose` are
expected and pass — not a duplicate-detection false positive), while a
duplicate or conflicting tenor row, or an ambiguous set of different
`curve_id`s claiming the same `currency` + `curve_purpose`, is rejected;
`BLIVolatilityInput`/`BLICreditSpreadInput`'s
`override_or_fallback_audit` must be non-blank whenever populated, and
credit-spread `EMBEDDED`/`NOT_REQUIRED` treatments require a non-blank
audit explanation — no silent zero/default spread, no silent flat-vol
fallback. **Only `ACTIVE` status is accepted at construction, for the
snapshot and every nested observation** — `STALE`/`INVALID`/`MISSING`
are rejected outright, and `MANUAL_VERIFIED` is also rejected for now
(with its own distinct error message) because the audit policy that
would make it acceptable (docs/23 §10) is not implemented in this
slice. `require_exact_isin_match(snapshot, expected_isin)` is a small
module-level helper for exact (never fuzzy/prefix) ISIN comparison
against a future resolver result.

**Synthetic fixture:**
`src/shiori_pricing_lab/data/bli_snapshot_fixtures.SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT`
reuses the existing eligible `XS0000000001` bond from
`reference_data.fixtures.SYNTHETIC_BOND_FIXTURES` and carries one bond
quote, one Bond Reference Curve and one Option Discount Curve (each
with two tenor rows), one Deposit Curve **plus** a separate
`TREASURY_FTP_REFERENCE`-style deposit-rate observation (the Deposit
Curve is never a substitute for the FTP observation, or vice versa,
per docs/23 §11.1's Codex-P2-fixed rule), one explicit volatility
input, and one explicit (`OBSERVED`) credit-spread treatment. Every
sub-observation in the fixture is `ACTIVE`, consistent with the
construction-time status gating above.

Tests are in `tests/test_bli_market_data_snapshot.py` (`python -m
pytest -q` → 589 passed; `ruff check` on the new files → clean).

**Explicitly not built here (`docs/23` §17):** no MVP input bundle,
bundle builder, pricing engine, payoff skeleton, cash-flow generation,
schedule engine, yield-to-price calculation, curve interpolation,
volatility surface, credit spread model, Treasury FTP parser,
ingestion, Bloomberg/API connector, QuantLib adapter, or UI. `BondOption`,
`DepositLeg`, `BondLinkedStructuredProduct`, `BondReferenceData`, and
`resolve_bond_reference_data` are all unmodified. **Issue #38 remains
open.**

### BLI MVP input bundle preflight checkpoint

- Before implementing an MVP input bundle class or bundle builder,
  agents must read `docs/24_bli_mvp_input_bundle_preflight.md`.
- **Recommended class/module:** `BLIMVPInputBundle` in a new
  `src/shiori_pricing_lab/data/bli_mvp_input_bundle.py` — same
  `data/`-package-location reasoning `docs/23` §3.3 used for the
  snapshot; the "MVP" in the name signals this is the MVP slice's
  shape, not necessarily a final one.
- The bundle binds one already-validated `BondLinkedStructuredProduct`,
  one resolved `BondReferenceData` (via `resolve_bond_reference_data`),
  and one `BLIMarketDataSnapshot` — by **reference**, never by copying
  fields out of any of them. Its only job is cross-checking what the
  three individually-valid objects agree or disagree on: ISIN identity
  across all three, `BondResolutionStatus.FOUND_ELIGIBLE`, and
  valuation-date coherence between the bundle and the snapshot. It must
  not price anything, must not interpolate curves, and must not convert
  yield to price or vice versa.
- **Strengthened after Codex P2 review of PR #64:** an `isinstance`
  check on `BLIMarketDataSnapshot` alone is **not** sufficient —
  `BLIMarketDataSnapshot.__post_init__` only proves internal
  well-formedness, it has no notion of which product it is for. The
  bundle must additionally gate on:
  - **product-specific market-data presence** — if
    `DepositLeg.deposit_rate_mode` is `TREASURY_FTP_REFERENCE`, the
    bundle requires a matching `deposit_rate_observation` (present, and
    consistent with `ftp_rate_selector`); `FIXED_RATE` needs no separate
    rate observation; `MANUAL_VERIFIED_RATE` needs an audit record not
    yet representable in `BLIMarketDataSnapshot` (an open item);
  - **required MVP curve-purpose gates** — at least one `curve_points`
    row for each of `BLICurvePurpose.BOND_REFERENCE_CURVE`,
    `OPTION_DISCOUNT_CURVE`, and `DEPOSIT_CURVE` must be present
    (`FUNDING_CURVE` only if mapped); presence only, no tenor selection
    or interpolation;
  - **market-data as-of / no-look-ahead policy** — `valuation_date`
    equality between the bundle and the snapshot is **not** enough;
    `as_of_timestamp` must also be validated against an explicit
    no-look-ahead cutoff rule (today only checked for non-blankness by
    `BLIMarketDataSnapshot`), with the exact cutoff rule left as a
    required policy decision for the implementation slice, not
    something a future pricing engine may silently interpret
    differently each time.
  All of these remain presence/consistency checks only — no curve
  interpolation, no yield/price conversion, no FTP parsing, no pricing,
  no silent fallback.
- **Concrete fixture gap found (not fixed, docs-only):**
  `tests/test_bond_linked_structured_product.py`'s inline `BondOption`
  helper uses ISIN `"US912828ZZ11"`, which does not match the
  `"XS0000000001"` ISIN both `reference_data.fixtures.
  SYNTHETIC_BOND_FIXTURES` and `data.bli_snapshot_fixtures.
  SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT` already use. The next
  implementation slice must add new synthetic
  `BondLinkedStructuredProduct` fixture content using
  `"XS0000000001"` before a combined positive bundle fixture is
  possible — this is new fixture content, not a schema change.
- No `BLIMVPInputBundle` class, bundle builder, pricing engine, or any
  code change exists yet. `BondOption`, `DepositLeg`,
  `BondLinkedStructuredProduct`, `BondReferenceData`,
  `resolve_bond_reference_data`, `is_mvp_pricing_eligible`, and
  `BLIMarketDataSnapshot` are all unmodified. Issue #38 remains open.

### `BLIMVPInputBundle` dataclass landed — BLI MVP input bundle implementation

`BLIMVPInputBundle` (`src/shiori_pricing_lab/data/bli_mvp_input_bundle.py`)
is implemented as the minimal MVP input bundle `docs/24` described,
following the recommended naming/location exactly. It binds one
`BondLinkedStructuredProduct`, one resolved `BondReferenceData`, and one
`BLIMarketDataSnapshot` **by reference only** — no field duplicates a
value already owned by any of the three.

**Fields:** `bundle_id`, `valuation_date`, `product`,
`resolved_bond_reference_data`, `resolution_status`,
`eligibility_reasons`, `market_data_snapshot`. The field name
`resolved_bond_reference_data` (not `docs/24` §7's sketched
`bond_reference_data`) and the decision to keep `resolution_status`/
`eligibility_reasons` as two plain fields rather than the whole
`BondReferenceResolutionResult` object are both explicit implementation
decisions, documented in the module docstring — not silent departures
from the preflight.

**Validation gates implemented, per `docs/24` §6:**

- `product` must be a `BondLinkedStructuredProduct`; `resolved_bond_
  reference_data` must be a `BondReferenceData`; `market_data_snapshot`
  must be a `BLIMarketDataSnapshot` — `isinstance` checks only, since
  each object type already fully validates itself at its own
  construction (docs/24 §4.4).
- `resolution_status` must be `BondResolutionStatus.FOUND_ELIGIBLE`
  (`FOUND_INELIGIBLE`/`NOT_FOUND` both reject); `eligibility_reasons`
  must be empty when `FOUND_ELIGIBLE`.
- **Reference-data eligibility is independently re-verified, not only
  trusted from the caller's supplied status (Codex P1 fix, PR #65):**
  `__post_init__` also calls `is_mvp_pricing_eligible(resolved_bond_
  reference_data)` directly and rejects construction if it disagrees —
  a stale or hand-assembled `resolution_status=FOUND_ELIGIBLE` /
  `eligibility_reasons=()` can no longer bundle an actually-ineligible
  bond (e.g. a callable bond) just by asserting the "right" status.
  Both checks must agree; neither is trusted alone.
- `product.bond_option.underlying_isin` must exactly match
  `resolved_bond_reference_data.isin`; `market_data_snapshot.bond_quote.
  isin` must exactly match it too (reusing the existing
  `require_exact_isin_match` helper) — plain string equality only, no
  fuzzy/prefix matching anywhere.
- **Currency coherence gates (Codex P2 fix, PR #65):**
  `product.bond_option.currency` must equal `resolved_bond_reference_
  data.currency`; `market_data_snapshot.bond_quote.currency` must equal
  that same currency; each required MVP curve purpose must have at
  least one `curve_points` row in that currency specifically (a
  same-purpose row in a different currency does not satisfy the gate).
  No FX conversion is implemented or implied — any mismatch is a hard
  rejection.
- `market_data_snapshot.valuation_date` must equal the bundle's own
  `valuation_date`.
- **Market-data as-of / no-look-ahead gate:** `market_data_snapshot.
  as_of_timestamp` is parsed with `datetime.fromisoformat` and only its
  **calendar date** is compared against `valuation_date` — an as-of
  date strictly after `valuation_date` is rejected, on-or-before is
  accepted, never a current-time lookup. This is a deliberately minimal
  policy (no intraday cutoff, no settlement-aware T+0/T+1 rule) —
  documented as a still-open limitation, not a final answer. **Timezone
  handling fixed (Codex P1 fix, PR #65):** only a bare date, a naive
  datetime, or a UTC datetime (`utcoffset()` exactly zero — `"Z"` or
  explicit `"+00:00"`) are accepted; any other timezone offset (e.g.
  `"...-05:00"`, `"...+08:00"`) is now **rejected outright**, since
  taking `.date()` of an offset-aware datetime returns that offset's
  *local* calendar date, which can legitimately differ from the UTC
  calendar date the no-look-ahead check actually needs.
- **Product-specific deposit-rate gate:** if `product.deposit_leg.
  deposit_rate_mode` is `TREASURY_FTP_REFERENCE`, `market_data_snapshot.
  deposit_rate_observation` must be present and its
  currency/tenor/quote_side must match `deposit_leg.ftp_rate_selector`;
  `FIXED_RATE` requires no separate observation; `MANUAL_VERIFIED_RATE`
  is **rejected outright** with a clear "not supported yet" error (no
  audit-record concept exists in `BLIMarketDataSnapshot` today).
- **Required MVP curve-purpose gate:** at least one `curve_points` row
  for each of `BLICurvePurpose.BOND_REFERENCE_CURVE`,
  `OPTION_DISCOUNT_CURVE`, and `DEPOSIT_CURVE`, **in the product's own
  currency**, must be present — presence only, no tenor-node selection,
  no interpolation. `FUNDING_CURVE` is not required (no mapping calls
  for it yet).
- Bundle construction **raises** (`ValueError`/`TypeError`) on any
  failed gate, matching every other frozen dataclass in this codebase —
  this resolves `docs/24` §11's open "raise vs. structured result"
  question for the dataclass itself.

**Fixture gap resolved:** `src/shiori_pricing_lab/products/fixtures.py`
adds `SYNTHETIC_BOND_LINKED_STRUCTURED_PRODUCT`, a synthetic
`BondLinkedStructuredProduct` whose `bond_option.underlying_isin` is
`"XS0000000001"` — the ISIN both `reference_data.fixtures.
SYNTHETIC_BOND_FIXTURES` and `data.bli_snapshot_fixtures.
SYNTHETIC_BLI_MARKET_DATA_SNAPSHOT` already used, closing the mismatch
`docs/24` §8.2/§11 found (`tests/test_bond_linked_structured_product.py`'s
own inline helper still uses its original, unrelated ISIN and is
unchanged). Its `DepositLeg` uses `TREASURY_FTP_REFERENCE` mode with an
`ftp_rate_selector` matching the snapshot's `deposit_rate_observation`
exactly, so the positive bundle fixture exercises the new
deposit-rate-observation gate meaningfully.

`src/shiori_pricing_lab/data/bli_mvp_input_bundle_fixtures.py` adds
`SYNTHETIC_BLI_MVP_INPUT_BUNDLE`, combining the three existing fixtures.
**(Updated below to build via `build_bli_mvp_input_bundle` once the
builder landed — see the checkpoint after this one.)**

Tests are in `tests/test_bli_mvp_input_bundle.py` (44 tests, 9 added by
the Codex-review fixes below; `python -m pytest -q` → 633 passed; `ruff
check` on the new files → clean).

**Explicitly not built here (`docs/24` §10, unchanged after the Codex
fixes below):** a pricing engine, payoff skeleton, cash-flow generation,
schedule engine, yield-to-price calculation, curve interpolation,
volatility surface, credit spread model, Treasury FTP parser, ingestion,
Bloomberg/API connector, QuantLib adapter, or UI. **A bundle builder /
construction helper is no longer future work — see the next checkpoint.**
`BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
`BondReferenceData`, `resolve_bond_reference_data`,
`is_mvp_pricing_eligible`, and `BLIMarketDataSnapshot` (and its
component classes) remain unmodified. Package exports
(`products/__init__.py`, `reference_data/__init__.py`,
`data/__init__.py`) are unchanged — the new fixture/bundle modules are
imported directly from their submodules, matching the existing `data/`
package convention. **Issue #38 remains open.**

**Fixed after Codex P1/P2 review of PR #65** (three findings, all in
`data/bli_mvp_input_bundle.py`, narrow validation-only fixes — no
builder, pricing, or scope change): (1) **P1 — eligibility was only
trusted from the caller-supplied `resolution_status`/
`eligibility_reasons`**, so a stale or hand-assembled resolver result
could bundle an actually-ineligible bond by asserting `FOUND_ELIGIBLE`.
Fixed by independently re-verifying `is_mvp_pricing_eligible(resolved_
bond_reference_data)` and rejecting on disagreement, alongside
(not instead of) the existing status/reasons gate. (2) **P1 —
`datetime.fromisoformat(as_of_timestamp).date()` silently used the
*local* calendar date for timezone-offset-aware timestamps**, so a
negative-offset instant already past `valuation_date` in UTC (e.g.
`"2026-07-01T23:30:00-05:00"`, which is `2026-07-02` in UTC) could pass
the no-look-ahead gate. Fixed: only a bare date, a naive datetime, or a
UTC datetime (`utcoffset()` exactly zero) are accepted; every other
offset is now rejected outright. (3) **P2 — no currency-coherence
gate existed**, so ISIN identity alone let a caller combine a
different-currency product/reference-data/market-data trio (e.g. an
EUR product against a USD-resolved bond and USD market data). Fixed by
adding explicit currency-equality checks (product ↔ reference data,
bond quote ↔ product currency, and per-required-curve-purpose currency
presence) — no FX conversion, no cross-currency fallback, any mismatch
is a hard rejection.

### `build_bli_mvp_input_bundle` builder landed — BLI MVP input bundle builder

`build_bli_mvp_input_bundle`
(`src/shiori_pricing_lab/data/bli_mvp_input_bundle_builder.py`) is
implemented as `docs/24` §12 step 5's minimal construction helper — the
first normal, callable path into `BLIMVPInputBundle`, replacing the
hand-wired `resolve_bond_reference_data` call +
`BLIMVPInputBundle(...)` unpacking a caller previously had to write out
by hand.

**Signature:** keyword-only `bundle_id`, `valuation_date`, `product`,
`bond_reference_data_universe` (an `Iterable[BondReferenceData]`, passed
straight through as `resolve_bond_reference_data`'s `fixtures`
argument — no filtering, sorting, or default assumed), and
`market_data_snapshot`.

**What it does:**

1. Checks `product` is a `BondLinkedStructuredProduct` (`TypeError`
   otherwise) — done *before* reading `product.bond_option.
   underlying_isin`, so a wrong-type `product` fails clearly rather than
   with an `AttributeError` from inside the resolver call.
2. Extracts `product.bond_option.underlying_isin` and calls the existing
   `resolve_bond_reference_data(underlying_isin, bond_reference_data_
   universe)` — no ISIN matching or eligibility logic is reimplemented.
3. If the resolver does not return `BondResolutionStatus.FOUND_ELIGIBLE`
   (covering both `NOT_FOUND` and `FOUND_INELIGIBLE`), raises `ValueError`
   including the resolver's own status and `block_reason` — never
   silently returns `None`, never builds a partial bundle, never coerces
   an ineligible/missing result into an eligible one.
4. Otherwise constructs and returns `BLIMVPInputBundle(...)` from the
   resolver's `bond_reference_data`/`status`/`eligibility_reasons` plus
   the caller's `bundle_id`/`valuation_date`/`product`/
   `market_data_snapshot`.

**What it deliberately does not do:** re-validate any of
`BLIMVPInputBundle`'s own gates (ISIN cross-checks, currency coherence,
valuation-date / as-of no-look-ahead, product-specific deposit-rate
consistency, required MVP curve-purpose presence, all from PR #65 and
its Codex-review fixes) — those raise directly from
`BLIMVPInputBundle.__post_init__` and are **not** caught or re-wrapped
by the builder. This resolves `docs/24` §11's "raise vs. structured
result" open question for the builder the same way PR #65 resolved it
for the dataclass: raise, do not return a structured found/not-found
object. `reference_data.resolution.DuplicateBondReferenceDataError` is
likewise propagated unchanged if `bond_reference_data_universe`
contains a duplicate ISIN.

**Fixture updated to use the builder:**
`src/shiori_pricing_lab/data/bli_mvp_input_bundle_fixtures.py`'s
`SYNTHETIC_BLI_MVP_INPUT_BUNDLE` now calls `build_bli_mvp_input_bundle`
directly instead of hand-wiring `resolve_bond_reference_data` +
`BLIMVPInputBundle(...)` — no circular-import or side-effect risk was
found, so the fixture was updated rather than duplicated. It remains
exactly one hand-picked positive case, not a general fixture factory.

Tests are in `tests/test_bli_mvp_input_bundle_builder.py` (18 tests;
`python -m pytest -q` → 651 passed; `ruff check` on the new files →
clean). Covers the happy path (via both a direct call and the updated
fixture), the wrong-type-`product` gate, resolver-failure handling
(unknown ISIN → `NOT_FOUND`, ineligible bond → `FOUND_INELIGIBLE`, both
raising clearly and never fabricating an eligible result), propagation
of `BLIMVPInputBundle`'s own gates (ISIN, currency, as-of, curve-purpose,
deposit-rate-observation mismatches all still reject through the
builder), and scope boundary checks (no pricing/interpolation/schedule
function defined anywhere in the builder module, no extra field on the
built bundle).

**Explicitly not built here (`docs/24` §10, unchanged):** a pricing
engine, payoff skeleton, cash-flow generation, schedule engine,
yield-to-price calculation, curve interpolation, curve *selection*
methodology beyond the existing bundle gates, volatility surface,
credit spread model, Treasury FTP parser, ingestion, Bloomberg/API
connector, QuantLib adapter, a debug viewer, or any other UI.
`BondOption`, `DepositLeg`, `BondLinkedStructuredProduct`,
`BondReferenceData`, `resolve_bond_reference_data`,
`is_mvp_pricing_eligible`, `BLIMarketDataSnapshot`, and
`BLIMVPInputBundle` (and its component classes) remain unmodified.
**Issue #38 remains open.**

### BLI bundle construction: the canonical path (post-PR #66)

With PR #65 (`BLIMVPInputBundle`) and PR #66
(`build_bli_mvp_input_bundle`) both merged, the canonical, only-supported
way to obtain a valuation-ready BLI input is now:

```text
product (BondLinkedStructuredProduct)
  + bond_reference_data_universe (an Iterable[BondReferenceData])
  + market_data_snapshot (BLIMarketDataSnapshot)
        │
        ▼
build_bli_mvp_input_bundle(...)   -- data/bli_mvp_input_bundle_builder.py
        │
        ▼
BLIMVPInputBundle                 -- data/bli_mvp_input_bundle.py
```

**Upstream orchestration code, test setup, fixtures, or future
application-layer callers must use `build_bli_mvp_input_bundle` to
obtain a `BLIMVPInputBundle` before invoking pricing** — never call
`resolve_bond_reference_data` directly, and never hand-construct a
`BLIMVPInputBundle` by unpacking a resolver result themselves. The
builder is the only place that maps a raw
`BondReferenceResolutionResult` onto the bundle's fields; duplicating
that mapping anywhere else risks the two drifting apart.

**The pricing engine itself is explicitly excluded from the sentence
above (Codex P2 review of PR #67):** it must not call the builder or
the resolver — it receives the already-validated bundle as its sole
input, built by whatever upstream code is invoking it. See the next
paragraph.

**Every input-readiness gate already lives in the bundle/dataclass
layer, not in any future consumer:**

- resolver status / eligibility (`resolution_status` must be
  `FOUND_ELIGIBLE`, independently re-verified against
  `is_mvp_pricing_eligible` — a stale or hand-assembled resolver result
  cannot override this);
- exact ISIN match across `product`, `resolved_bond_reference_data`,
  and `market_data_snapshot` (no fuzzy/prefix matching);
- currency coherence across all three, including per-required-curve-
  purpose currency;
- valuation-date equality between the bundle and the snapshot;
- the market-data as-of / no-look-ahead policy (calendar-date
  comparison; non-UTC timezone offsets rejected);
- the Treasury-FTP-reference deposit-rate-observation presence/
  consistency gate (`FIXED_RATE` needs none; `MANUAL_VERIFIED_RATE` is
  rejected outright, pending a future audit policy);
- required MVP curve-purpose presence (`BOND_REFERENCE_CURVE`,
  `OPTION_DISCOUNT_CURVE`, `DEPOSIT_CURVE`, in the product's own
  currency).

**A future pricing engine must accept only an already-validated
`BLIMVPInputBundle` as its sole input.** It must not call
`resolve_bond_reference_data`, must not call
`build_bli_mvp_input_bundle`, must not hand-construct a
`BLIMVPInputBundle` by any other means, and must not re-implement or
second-guess any of the gates above — by the time pricing code sees a
`BLIMVPInputBundle` instance, every one of those checks has already
passed (a `BLIMVPInputBundle` cannot exist in an invalid state; its
`__post_init__` would have raised). Bundle construction is entirely the
calling code's responsibility, not the pricing engine's. **All real
valuation math — PV, payoff, cash-flow generation, schedule generation,
yield/price conversion, curve interpolation, volatility, credit
spread — remains future work**, scoped next by
`docs/25_bli_pricing_engine_skeleton_preflight.md`.

### Market-data ingestion terminology checkpoint

- Before any future market-data ingestion, funding-curve, deposit-leg, or
  Bloomberg connector issue, agents must read
  `docs/16_market_data_ingestion_terminology.md`.
- Do not build generic FTP market-data import unless explicitly scoped.
- Do not confuse FTP/SFTP transport with Treasury FTP / Funding Curve.
- Do not start broad ingestion work while working on Issue #38
  BondOption-only schema.

### BLI pricing engine skeleton landed

`price_bli_mvp` (`src/shiori_pricing_lab/pricing/bli_pricing_engine.py`)
is implemented as the pricing-engine **skeleton**
`docs/25_bli_pricing_engine_skeleton_preflight.md` scoped — the first
callable seam a future PR can register real BLI valuation logic behind.
**No real valuation math exists yet.**

**Input boundary:** accepts only an already-validated `BLIMVPInputBundle`
— never a raw `BondLinkedStructuredProduct` / `BondReferenceData` /
`BLIMarketDataSnapshot` / ISIN / curve / deposit observation. It calls
neither `resolve_bond_reference_data` nor `build_bli_mvp_input_bundle`:
every input-readiness gate (ISIN identity, eligibility, currency
coherence, valuation-date/as-of coherence, deposit-rate consistency,
curve-purpose presence) already lives in `BLIMVPInputBundle.__post_init__`
and its builder (see the checkpoint above), so this module re-derives
none of them. Bundle construction remains entirely the calling code's
responsibility, per the checkpoint above — nothing changes about that.

**Shared pricing-spine reuse decision (`docs/25` §4's three required
questions, answered explicitly, not silently picked):**

1. **Can `PricingResult`/`PricingStatus`/`PricingErrorCode` be reused
   as-is? Yes — with zero changes to `pricing/result.py`,
   `pricing/errors.py`, or `pricing/engine.py`.** Every field the
   skeleton needs already has a direct source on the bundle
   (`product.product_id`/`product.product_type`, `bundle.valuation_date`,
   `product.bond_option.currency`,
   `bundle.market_data_snapshot.as_of_timestamp`). The existing
   `PricingErrorCode.UNSUPPORTED_PRODUCT` already means exactly this
   case — it is the same code the front door returns today for OIS / CCS
   / FX Swap, which also have no real per-product engine yet (section 8
   above). No `BLIPricingResult`/`BLIPricingStatus` was introduced.
2. **Can a `BLIMVPInputBundle`-based entrypoint adapt to the existing
   `PricingEngine.price(product, valuation_context, market_snapshot)`
   Protocol shape? No, not without fabricating structure that does not
   exist.** `BLIMVPInputBundle` deliberately merges product + resolved
   reference data + market data into **one** pre-validated object
   (`docs/24` §2); there is no BLI equivalent of the vanilla-rates-core
   `ValuationContext` (reporting currency, model settings, a
   `.market_snapshot` back-reference) for the front door's
   `valuation_context` parameter to bind to. Building a synthetic shim
   purely to satisfy the Protocol's three-argument arity would invent a
   second, parallel object with no real BLI use. This skeleton is
   therefore **not** registered on `PricingEngineRegistry` and does not
   implement the `PricingEngine` Protocol; `pricing/engine.py` is
   unmodified.
3. **Is the separate `price_bli_mvp` entrypoint a temporary stopgap or a
   deliberate, permanent path?** `price_bli_mvp` is **the explicit,
   direct bundle-based entrypoint for the BLI MVP path** — this PR does
   not register it on `PricingEngineRegistry` and does not implement the
   `PricingEngine` Protocol for BLI. This does **not** reopen `docs/14`
   §4.5's or `docs/24` §2's shared-spine assumption: the shared *result
   value type* is still reused unchanged — only the entrypoint's routing
   differs, because BLI's natural caller already holds a validated
   `BLIMVPInputBundle`, not a bare `(product, valuation_context,
   market_snapshot)` triple. **Whether a future slice registers a
   bundle-unpacking adapter behind `price(...)` for BLI remains an open
   design decision** — this skeleton PR takes no position on it either
   way and does not foreclose it.

**Not-implemented behavior chosen:** for a valid bundle, `price_bli_mvp`
returns a deterministic `PricingResult(status=FAILED,
errors=[PricingErrorCode.UNSUPPORTED_PRODUCT])` — it never raises for a
valid bundle, since "not implemented yet" is a statement about the
engine's own current capability, not a contract/programming violation.
**Wrong input type raises `TypeError`** (a raw
`BondLinkedStructuredProduct` / `BondReferenceData` /
`BLIMarketDataSnapshot` instead of a `BLIMVPInputBundle`), mirroring
`BLIMVPInputBundle.__post_init__`'s own `isinstance` checks. `pv`,
`dv01`, `cashflows`, and `scenario_results` all stay `None` on every
call — no fake numeric output of any kind.

Tests are in `tests/test_bli_pricing_engine.py` (16 tests; `python -m
pytest -q` → 667 passed; `ruff check src/shiori_pricing_lab tests` →
only the same 2 pre-existing, unrelated `products/bond_option.py`
`E501` findings remain).

**Explicitly not built here (`docs/25` §7, unchanged):** real valuation
math, payoff formula, bond pricing, option pricing, deposit payoff
calculation, cash-flow generation, schedule engine, yield-to-price or
price-to-yield conversion, curve interpolation, curve construction,
volatility surface, credit spread model, Treasury FTP parser, ingestion,
Bloomberg/API connector, QuantLib adapter, UI, debug viewer, scenario
engine, or hedge/Greeks/DV01. `BLIMVPInputBundle`,
`build_bli_mvp_input_bundle`, `BondOption`, `DepositLeg`,
`BondLinkedStructuredProduct`, `BondReferenceData`,
`resolve_bond_reference_data`, `is_mvp_pricing_eligible`,
`BLIMarketDataSnapshot`, and the existing vanilla-rates-core
`pricing/result.py`/`errors.py`/`engine.py`/`irs_engine.py` are all
unmodified. **Issue #38 remains open** — this skeleton does not price
anything; real BLI valuation math is future work.

### BLI first valuation slice preflight checkpoint

- Before implementing any real BLI valuation math, agents must read
  `docs/26_bli_first_valuation_slice_preflight.md`.
- **First real valuation slice scoped (not implemented):** Annex A
  §A.2's European, price-based, cash-settled bond option (Black-76 on
  forward clean price), priced only for `bundle.product.bond_option` —
  the deposit leg stays structurally present on the bundle but its
  economics are not computed. This is a *valuation-scope* boundary, not
  an input-schema change: `BLIMVPInputBundle.product` stays typed
  `BondLinkedStructuredProduct` (which requires a `DepositLeg`), matching
  SPEC §6.2's "Bond Option Standalone Pricing Tool."
- **Eight missing dependencies identified, none implemented:**
  time-to-expiry year fraction; curve interpolation / discount-factor
  access (confirmed **not** served by the existing
  `pricing/curve.py::RateCurve`, which is built from the unrelated
  vanilla-rates-core `MarketDataSnapshot` DataFrame shape, not
  `BLICurvePoint`); coupon/cash-flow schedule generation; accrued
  interest; forward clean price derivation; volatility selection /
  equivalent-price-vol conversion; yield-to-price conversion;
  settlement / physical delivery invoice logic.
- **Next implementation slice chosen: the time-to-expiry year-fraction
  utility only** (Annex A §A.2.2, ACT/365F) — zero market-data
  dependency, zero design ambiguity, and a mechanical adaptation of the
  already-reviewed `pricing/irs_engine.py::_year_fraction` ACT_365_FIXED
  precedent (PR #29). `docs/26` §7 sketches that PR's suggested branch
  (`claude/bli-time-to-expiry-year-fraction`), target files, expected
  tests, acceptance criteria, and a Codex review checklist — none of it
  implemented by the preflight.
- **`price_bli_mvp` keeps its current behavior unchanged**: deterministic
  `PricingResult(status=FAILED, errors=[PricingErrorCode.
  UNSUPPORTED_PRODUCT])` for every valid bundle. The time-to-expiry
  slice is a pure, unwired utility that does not touch
  `bli_pricing_engine.py`; a narrower dependency-gated dispatch is
  deferred to a later, undecided PR once enough dependencies exist to
  attempt a real PV for the narrow §2 slice.
- No pricing module, valuation math, curve interpolation, cash-flow
  generation, schedule engine, yield/price conversion, QuantLib
  adapter, Bloomberg/API connector, or UI exists yet from this doc.
  `price_bli_mvp` and every existing BLI/vanilla-rates-core module are
  unmodified. **Issue #38 remains open.**

### BLI time-to-expiry ACT/365F utility landed (unwired)

- `docs/26`'s chosen next slice is implemented in
  `src/shiori_pricing_lab/pricing/bli_valuation_time.py` (new module):
  `year_fraction_to_expiry(valuation_date: str, expiry_date: str) ->
  float` computes Annex A §A.2.2's `T = days / 365.0` (ACT/365F),
  mirroring `pricing/irs_engine.py::_year_fraction`'s existing
  `ACT_365_FIXED` behavior. Per Annex A §A.2.4, `expiry_date <=
  valuation_date` raises `ValueError` (same-day expiry and expired
  options are both blocked, never silently `0.0` or negative);
  malformed / non-ISO date strings also raise `ValueError`.
- A convenience wrapper, `year_fraction_to_bond_option_expiry(bundle:
  BLIMVPInputBundle) -> float`, reads only `bundle.valuation_date` and
  `bundle.product.bond_option.expiry_date`, does not mutate the bundle,
  and calls no curve/vol/credit-spread/bond-reference/deposit/pricing
  logic.
- **This is only the time-to-expiry utility — it remains unwired to
  `price_bli_mvp`.** `bli_pricing_engine.py` is unmodified;
  `price_bli_mvp` still returns its existing deterministic
  `PricingResult(status=FAILED,
  errors=[PricingErrorCode.UNSUPPORTED_PRODUCT])` for every valid
  bundle. No `PricingResult`/`PricingStatus`/`PricingErrorCode`/
  `BLIPricingResult`/`BLIPricingStatus` was added or changed. No curve
  interpolation, discount factor, forward clean price, coupon schedule,
  accrued interest, volatility conversion, yield-to-price conversion,
  Black-76, PV, or Greeks logic was added — those remain future,
  separate slices per `docs/26` §4/§6.
- Tests: `tests/test_bli_valuation_time.py` (ACT/365F happy path
  including a leap-year-spanning pair, the same-day/expired blocking
  rule, malformed-date rejection, a no-system-date-use check, a
  module-boundary check against curve/discount/forward-price/vol-shaped
  names, and the bundle convenience function checked against the
  existing `SYNTHETIC_BLI_MVP_INPUT_BUNDLE` fixture with no new fixture
  content). `python -m pytest -q` → 681 passed; `ruff check
  src/shiori_pricing_lab tests` → only the same 2 pre-existing,
  unrelated `products/bond_option.py` `E501` findings remain.
- **Issue #38 remains open.**
