# 11 Historical Valuation Loop — Design Preflight

Status: **design preflight only — no code, no loop, nothing implemented.**

This document defines the design for the *first historical valuation loop
skeleton* (Issue #13). The loop values one product across a series of explicit
historical valuation dates by **reusing the existing single-date deterministic
pricing contract**, not by building a second pricing path.

It is intentionally a preflight: it pins down the request shape, how dates and
synthetic snapshots are supplied, how each date reuses `ValuationContext` and the
`price(...)` front door, the result-table shape, per-date failure handling,
required provenance, the no-system-date / no-future-data rules, and the tests the
implementation slice must add. **It implements none of them.**

Required reading before the implementation slice: `AGENTS.md`,
`docs/02_data_and_market_snapshots.md`, `docs/03_valuation_context.md`,
`docs/05_backtesting_engine.md`, `docs/09_mvp_core_runbook.md` (sections 1, 3, 8),
and the existing pricing-contract modules
(`src/shiori_pricing_lab/pricing/engine.py`, `result.py`) plus
`ValuationContext` / `MarketDataSnapshot`.

---

## 1. Purpose

PR #29 registered the first per-product engine (USD-only IRS) behind the stable
single-date front door:

```text
Product Definition + ValuationContext + MarketDataSnapshot → price(...) → PricingResult
```

Issue #13 is the next slice *downstream* of that contract: a minimal loop that
runs the **same** `price(...)` call once per historical valuation date and
collects the results into a stable table.

The loop is deliberately thin. It owns **iteration and collection only**. It does
**not** own pricing, curve building, data loading, or market-data invention —
those already belong to the pricing engine, `ValuationContext`, and the (future)
data layer respectively.

This preflight calculates **no values** and defines **no P&L / return / backtest
analytics**. It stops at "value the same product on each date and record the
structured result".

---

## 2. Scope

### In scope (first slice)

- A minimal in-memory loop over a caller-supplied, ordered list of valuation
  dates for **one product**.
- **Synthetic historical market data only**, supplied by the caller.
- Reuse of `MarketDataSnapshot`, `ValuationContext.from_snapshot`, and the
  `price(...)` front door, unchanged.
- A stable, deterministic **result table** with one row per valuation date,
  including per-date failures.

### Explicitly out of scope (see §12)

- Any backtest analytics (P&L, returns, curves-over-time, charts).
- Persistence, trade journal, UI, AI layer.
- Multiple products / portfolios (one product first).
- Real / Bloomberg / CSV-provider / web data, or any data fetching.
- A second pricing path or any direct engine call that bypasses `price(...)`.

This is the **skeleton** Issue #13 asks for, not full backtesting (Issue #13 must
not expand into `docs/05` backtesting analytics here).

---

## 3. Minimal historical valuation request structure

The future loop takes an explicit request describing *what to value* and *the
market states to value it against*. The shape below is the contract the
implementation slice should honor; the exact type name (dataclass) is decided in
the implementation PR.

Conceptually the request carries:

- **`product`** — one product definition (e.g. an `InterestRateSwap`). Unchanged
  from the single-date path; the loop never mutates it.
- **`dated_snapshots`** — an **ordered** collection of
  `(valuation_date, MarketDataSnapshot)` pairs (see §4 and §5), one per date to
  value. Each snapshot's `valuation_date` must equal its key date.
- **`reporting_currency`** — passed through to each `ValuationContext`
  (default `"USD"`, matching `ValuationContext`).
- optional **`request_metadata`** — a small dict copied verbatim into provenance
  (e.g. `{"run_id": ..., "dataset": "synthetic_usd_irs_v0"}`); metadata only,
  never used in pricing.

The request holds **no system date, no "as of today", no date range that is
expanded by generating calendar dates** — the caller supplies the explicit dates.
Generating a date range from a calendar/holiday rule is out of scope (it needs a
calendar, which the repo does not have yet).

---

## 4. How valuation dates are represented

- Valuation dates are **explicit strings in `YYYY-MM-DD` form**, exactly as
  `MarketDataSnapshot.valuation_date` and `ValuationContext.valuation_date`
  already require. No `date.today()`, no implicit "now".
- The loop processes dates **in the caller-supplied order** and must not reorder,
  deduplicate silently, or infer missing dates. Ordering is the caller's
  responsibility; the result table preserves input order (see §7).
- Each date is **independent**: valuing date `T` uses only the snapshot supplied
  for `T`. The loop never lets one date read another date's snapshot (no
  look-ahead — see §10).

---

## 5. How synthetic historical snapshots are supplied

- The caller builds one `MarketDataSnapshot` **per valuation date** and hands
  them to the loop. The loop does **not** construct market data from a provider,
  CSV, Bloomberg, or the web — that boundary already holds for pricing and must
  hold for the loop.
- Two acceptable synthetic-supply patterns, both already supported today:
  1. **Pre-built snapshots** — the caller constructs each
     `MarketDataSnapshot.from_rates_points(frame_for_date, valuation_date, source="synthetic")`
     and passes the `(date, snapshot)` pairs in.
  2. **One multi-date synthetic frame** — the caller passes a single normalized
     rates-points frame containing several dates plus the ordered date list, and
     a thin helper slices it **per date** via the existing
     `MarketDataSnapshot.from_rates_points(frame, valuation_date, source=...)`,
     which already filters the frame to that date and raises if the date has no
     rows. The loop still values one date at a time against its own snapshot.
- **The loop must not invent missing rates.** If a date has no usable market
  data, that surfaces as a structured failure row (see §8) — the loop never
  fabricates a curve or back-fills from a neighbouring date.
- `source` on each snapshot must be a synthetic label (e.g. `"synthetic"`); it is
  carried into provenance (see §9).

---

## 6. How each date builds/reuses `ValuationContext` and calls `price(...)`

For each `(valuation_date, snapshot)` the loop performs exactly the standard
single-date flow — no shortcut, no toy path:

1. Build the context from the snapshot so the dates cannot disagree:
   `context = ValuationContext.from_snapshot(snapshot, reporting_currency=...)`.
   Deriving the date from the snapshot guarantees
   `context.valuation_date == snapshot.valuation_date`.
2. Call the **existing front door**:
   `result = price(product, context, snapshot)`.
   The loop passes the **same snapshot object** it used to build the context, so
   the front door's snapshot-identity check passes (a different object would fail
   with `MARKET_SNAPSHOT_MISMATCH`). This is a load-bearing detail: build the
   snapshot once per date, reuse that exact object for both the context and the
   `price(...)` argument.
3. Record the returned `PricingResult` as one row (see §7), whatever its status.

The loop **must not** call an engine's `price` method directly, look up the
registry itself, or re-implement any front-door guard. It only iterates and
collects. Registering engines is out of scope — whatever is registered globally
(today: USD IRS) is what runs; everything else comes back as a structured
`FAILED` row, which the loop records like any other outcome.

---

## 7. Stable result table shape

The loop returns a **deterministic, ordered** table: exactly one row per input
date, in input order, regardless of success or failure. No row is dropped on
failure. The row shape below is a projection of `PricingResult` plus provenance;
the concrete container (list of frozen rows / DataFrame) is decided in the
implementation PR, but the columns are fixed here:

| Column | Source | Notes |
| --- | --- | --- |
| `valuation_date` | `PricingResult.valuation_date` | the explicit date valued |
| `status` | `PricingResult.status` | `SUCCESS` / `SUCCESS_WITH_WARNINGS` / `FAILED` |
| `is_success` | `PricingResult.is_success` | convenience; `False` only for `FAILED` |
| `pv` | `PricingResult.pv` | `None` on failure (never a fake `0.0`) |
| `result_currency` | `PricingResult.result_currency` | reporting currency |
| `market_data_as_of` | `PricingResult.market_data_as_of` | snapshot date actually used |
| `engine_name` / `engine_version` / `method` | `PricingResult` | engine provenance |
| `error_codes` | `PricingResult.errors` | tuple of machine codes, empty on success |
| `warning_codes` | `PricingResult.warnings` | tuple of machine codes |
| `source` | snapshot `source` | e.g. `"synthetic"` (see §9) |
| `run_metadata` | request metadata | copied verbatim; audit only |

The table must be **serializable and stable**: the same request produces an
identical table every run (see §11). The loop adds **no derived analytics
columns** (no P&L, no diffs between dates) — that is backtesting, out of scope.

---

## 8. How failures are represented per date

Failure handling reuses the pricing contract; the loop invents no new failure
model.

- A per-date **domain failure** (unsupported product, non-USD IRS, missing market
  data, unsupported convention, etc.) comes back from `price(...)` as a
  `PricingResult` with `status == FAILED`, `pv is None`, and structured
  `errors`. The loop records it as a normal row with `is_success == False`;
  it **does not** raise, skip, or halt the loop.
- One bad date **must not** abort the run. The loop continues to the next date so
  a single missing-data date does not lose the whole table.
- The loop must **never** substitute a fake `0.0` PV, a previous date's PV, or an
  interpolated value for a failed date. `pv` stays `None` and the error codes
  explain why (`MISSING_MARKET_DATA`, `UNSUPPORTED_PRODUCT`, `INVALID_PRODUCT`,
  …).
- **Contract violations still raise.** If the caller hands the loop a malformed
  input (e.g. a `None` product, or a snapshot whose date disagrees with its key),
  that is a programming error and should surface as an exception — consistent with
  the front door raising `PricingContractError` — not a silent `FAILED` row. The
  loop should fail fast on a malformed *request*, but record per-date *domain*
  failures as rows.

The three pricing statuses are the only per-date outcomes; there is no
loop-specific status enum.

---

## 9. Provenance / source metadata required in each row

Every row must be auditable on its own, per `AGENTS.md` (synthetic-only,
explicit, boring):

- **`valuation_date`** and **`market_data_as_of`** — the date valued and the
  snapshot date actually used (equal on the happy path; both recorded so a
  mismatch is visible).
- **`source`** — the snapshot's `source` label (must be synthetic in this slice,
  e.g. `"synthetic"`).
- **`engine_name` / `engine_version` / `method`** — which engine produced the row
  (e.g. `usd_irs_reference_engine` / contract version / method label), taken
  straight from `PricingResult`.
- **`run_metadata`** — the request's metadata dict, copied verbatim (e.g.
  `run_id`, `dataset`). Metadata only — it must never influence pricing.

No provenance field may be filled from the system clock or an external lookup;
all of it comes from the request and the returned `PricingResult`.

---

## 10. No system date / no future data / no external data rules

Load-bearing invariants for the loop (extending `docs/09` §3):

1. **No system date.** The loop contains no `date.today()` / `datetime.now()`.
   Every valuation date is explicit and caller-supplied. (A test mirrors the
   existing no-system-date guard — see §11.)
2. **No future data / no look-ahead.** Valuing date `T` uses only the snapshot
   supplied for `T`. The loop must not let date `T` read a later date's snapshot
   or rates, and must not carry state forward that leaks a future observation.
   Each iteration is independent.
3. **No data fetching / no external data.** The loop imports no
   `data.providers` / CSV / Bloomberg / web module and makes no network call. All
   market state arrives as pre-built synthetic `MarketDataSnapshot` objects.
4. **No invented rates.** Missing market data for a date becomes a
   `MISSING_MARKET_DATA` failure row, never a fabricated curve.
5. **Single pricing path.** The loop calls `price(...)` only; it never registers,
   imports, or calls an engine directly, and never re-implements pricing.

---

## 11. Test plan (implementation slice)

The **implementation** PR (not this docs-only PR) must add deterministic tests,
likely in `tests/test_historical_valuation_loop.py`, **synthetic data only**:

1. **Multi-date happy path** — a USD IRS over several synthetic dates returns one
   row per date, in input order, each `SUCCESS` with a non-`None` `pv`.
2. **Deterministic repeated runs** — running the same request twice yields
   identical tables (same rows, order, `pv`s, statuses, provenance). This is the
   core Issue #13 acceptance test.
3. **Per-date failure is a row, not a crash** — a date with an empty / unusable
   snapshot yields a `FAILED + MISSING_MARKET_DATA` row with `pv is None`, and the
   loop still values the remaining dates.
4. **Unsupported product still fails per date** — a non-IRS (or non-USD IRS)
   comes back as a structured `FAILED` row on every date; no fake `0.0`.
5. **No look-ahead** — date `T`'s row depends only on `T`'s snapshot; changing a
   *later* date's snapshot does not change an earlier date's row.
6. **No system-date usage** — the loop module contains no `date.today(` /
   `datetime.now(` (mirror
   `tests/test_pricing_engine.py::test_pricing_engine_modules_have_no_system_date`).
7. **No provider imports** — importing the loop pulls in no
   `shiori_pricing_lab.data` provider / CSV / web module (extend the layering
   guard).
8. **No mutation** — the loop does not mutate the product or any supplied
   snapshot (assert equality/identity before and after the run).
9. **Provenance present** — every row carries `valuation_date`,
   `market_data_as_of`, `source`, engine provenance, and `run_metadata`.

Do **not** add these tests in this docs-only preflight PR. (There is no existing
docs-only test/check convention; this PR adds documentation only.)

---

## 12. Explicit out-of-scope items

Deliberately deferred (do not implement under Issue #13's skeleton):

- **Backtest analytics** — P&L, returns, time-series diffs, curve-over-time,
  charts (that is `docs/05`, a later issue).
- **Multiple products / portfolios** — one product first.
- **Persistence / trade journal / storage** — the table is in-memory only.
- **UI and AI layers** — no rendering, no AI inquiry (Issue #14 is **not**
  started here).
- **Real / external data** — no Bloomberg, CSV-provider changes, web, or network;
  synthetic only.
- **Date generation from calendars** — no holiday calendar, no business-day date
  ranges; the caller supplies explicit dates.
- **DV01 / scenario columns** — the row records whatever `PricingResult` provides
  (`dv01` stays `None` today); the loop adds no scenario analytics.
- **Parallel / concurrent execution and performance work** — a simple
  deterministic sequential loop first (see `AGENTS.md` performance rules).

---

## 13. Boundaries this preserves

Restating the load-bearing boundaries the implementation must not break:

- **One pricing path only** — the loop reuses `price(...)`; it does not create a
  second/toy valuation path and does not call engines directly.
- **No market-data fetching / no invented rates** — snapshots come in pre-built
  and synthetic; missing data fails explicitly.
- **No system date, no future data** — explicit caller-supplied dates, each date
  independent.
- **No new external dependencies** — no Bloomberg / web / CSV-provider changes,
  no UI, no AI, no persistence.

---

## 14. Relationship to existing docs

- `docs/09_mvp_core_runbook.md` §1/§8 documents the single-date `price(...)`
  contract and the registered USD IRS engine; this loop sits **on top of** that
  contract and changes none of it.
- `docs/10_irs_reference_engine_preflight.md` is the design step for the engine
  the loop will exercise; this document is the analogous design step for the
  historical loop.
- `docs/05_backtesting_engine.md` describes the eventual backtesting engine; this
  skeleton is the first, deliberately narrow step toward it and does **not**
  implement its analytics.

Issue #13 remains **open**; this preflight is the design step before its first
implementation slice and does not close it. Issue #14 (AI inquiry) is untouched.
