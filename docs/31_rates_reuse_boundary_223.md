# 31 — Rates infrastructure reuse map + Python/C++ integration boundary (Issue #223)

Parent: #222 — C++ Rates Engine Foundation.
This issue: #223 — ARCHITECTURE / REPOSITORY RESEARCH ONLY.

| Field | Value |
|---|---|
| Issue | #223 |
| Owner | OpenCode (primary repository execution agent) |
| Branch | `arch/223-rates-reuse-boundary` |
| Base SHA | `3217311a53a2b72f671e46caecb789643563574a` (main, squash merge of PR #221) |
| Scope | Reuse audit + integration boundary only |
| Non-goals | No production C++ pricing, no C++ skeleton, no Bloomberg plumbing rewrite, no methodology changes |

Methodology authority: Sophira (architecture/methodology scope, RED decisions, review interpretation).
Final merge authority: Eddy. Current status: `PENDING SOPHIRA / CODEX REVIEW — DO NOT MERGE`. The final gate `READY TO MERGE — 等待 Eddy 明確批准` applies only after scope, latest HEAD, validation, PR body, and Codex review are all accepted.
Independent reviewer: Codex.
Existing bond-option product line: STABLE — do not refactor, rename, migrate, or rewrite validated behavior in this slice.

AGENTS.md compliance: smallest coherent change (one document), reuse before adding, no fabricated market data or Bloomberg evidence, no methodology guesses, no merge without explicit approval.

---

## 1. Repo-state delta (feasibility check required by task)

Supplied assumptions were verified against `origin/main` at base SHA above:

1. Repository access: confirmed (clone + fetch succeed).
2. Branch/HEAD: `main` = `3217311a53a2b72f671e46caecb789643563574a`, matching the expected HEAD exactly. `git log` confirms the tip is PR #221 ("Refs #218: US corporate D_B … 30/360 BondBasis").
3. `cpp/` does not exist. `arch/` does not exist. No `CMakeLists.txt` anywhere. Pure-Python repo (`src/`, `tests/`, `tools/`, `docs/`, `pyproject.toml`).
4. All Issue #222–named source files exist at the stated paths (`data/bloomberg_option_discount_curve.py`, `data/bloomberg_usd_sofr_par_rate_curve.py`, `data/bloomberg_vcub_capture.py`, `data/bloomberg_vcub_atm_template.py`, `data/bloomberg_vcub_otm_capture.py`, `data/vcub_normal_vol_resolver.py`, `data/vcub_vol_surface_adapter.py`, `data/vol_surface.py`, `pricing/irs_engine.py`, `tests/test_irs_reference_engine.py`, Bloomberg curve/VCUB acceptance tooling under `tools/`).
5. Bond-option line is at the post-#221 state described (US corporate direct-vol + 30/360 duration work present in history: #220, #221).

Conclusion: **no material delta. Proceed with #223 as scoped.** No planning round needed.

---

## 2. Inventory — what exists today (verified by reading)

Concern legend: A = acquisition (live Bloomberg / pixels), N = normalization (validation, transcription, canonical shapes), P = pricing math (deterministic), S = persistence, O = orchestration/routing, V = presentation.

### 2.1 Bloomberg curve acquisition (A+N, Python-only, keep)

| Module | What it does | Key contract | Bloomberg surface |
|---|---|---|---|
| `src/shiori_pricing_lab/data/bloomberg_option_discount_curve.py` (`load_bloomberg_usd_sofr_option_discount_curve`) | Production Issue #165 ingestion of Curve #490 USD SOFR option-discount curve | Input: tenor labels validated against exact 32-label universe before any request. Output: `tuple[BLICurvePoint,…]` (`curve_id=USD_SOFR_OPTION_DISCOUNT_CURVE`, `currency=USD`, `purpose=OPTION_DISCOUNT_CURVE`, `rate_basis=CONTINUOUS_ZERO_RATE`, `rate=percent/100`, `maturity_date`=raw `MATURITY` verbatim) + parallel `discount_factor_evidence` (never written into the point). Sorted by canonical order | `S0490Z <tenor> BLC2 Curncy` + `S0490D <tenor> BLC2 Curncy`, fields `LAST_PRICE` + `MATURITY` in one `ReferenceDataRequest`. `Z/100`, `D` never `/100`, `Z/D` maturity equality + duplicate/non-increasing maturity fail-closed |
| `src/shiori_pricing_lab/data/bloomberg_usd_sofr_par_rate_curve.py` (`load_bloomberg_usd_sofr_par_rate_curve`) | Issue #168 display-only USD SOFR OIS par-rate acquisition; explicitly never wired into #165 loader or pricing | Output: standalone `BloombergUsdSofrParRatePoint` (`par_rate_percent` = raw `LAST_PRICE` unconverted). Deliberately not `BLICurvePoint` | `USOSFR*` tickers (32-entry verbatim map), field `LAST_PRICE` only, no `MATURITY` |
| Shared DAPI plumbing | Reused, not reimplemented | `_DAPI_HOST/_PORT/_REFDATA_SERVICE/_REQUEST_TIMEOUT_MS`, `BLIBloombergDapiError`, `_get_element_as_string`, `_parse_finite_float` live in `data/bloomberg_bond_quote.py` and are imported by both loaders. `DEFAULT_USD_SOFR_TENORS` defined once in the #490 loader and imported read-only by the par-rate loader with an import-time drift guard | — |

No interpolation, bootstrap, DF-from-zero, pricing, or persistence in either loader (stated "Not in this slice").

### 2.2 VCUB visual-capture stack (N + review gate, Python-only, keep)

| Module | What it does | Key contract |
|---|---|---|
| `data/bloomberg_vcub_capture.py` | Issue #181 typed shapes + confirm/reject state machine for VCUB ATM capture. No image/OCR/geometry/pricing | `VCUBTextToken` pixel boxes, `VCUBATMCapture` (`PENDING_REVIEW` → `CONFIRMED`/`REJECTED`); `accepted_grid` returns a grid only if `CONFIRMED`. `None` cell = unresolved, never zero |
| `data/bloomberg_vcub_atm_template.py` (`parse_vcub_atm_tokens`) | Template reconstruction of ATM `Expiry x Tenor` matrix from tokens; anchor-relative bands, always returns `PENDING_REVIEW`; fail-closed topology | Grid or `None` + `blocking_errors` (ambiguous band, outside rows/cols, duplicates, non-numeric, pitch irregularity) |
| `data/bloomberg_vcub_otm_capture.py` | Issue #185 typed records for OTM Swaptions/SABR multi-screenshot capture (91 `Term x Tenor` rows × 9 strikes, ATM column absolute + rest spreads) | `VCUBOTMCapture.can_confirm` requires `table.is_complete` (exactly 91×9); duplicate image-SHA refused |
| `data/bloomberg_vcub_otm_template.py` | OTM template parser + multi-slice merge (consumed via `accepted_table`) | Overlap/conflict/coverage-gap fail-closed |
| `data/bloomberg_vcub_screen_reader.py`, `data/bloomberg_vcub_ocr.py` | Shared geometry/numeric/tenor helpers; pure `tokens_from_detections` conversion + pixel sign evidence | No pricing |
| `data/vcub_vol_surface_adapter.py` (`canonical_surface_from_confirmed_*`) | Issues #183/#185 one-way bridge: confirmed capture → `CanonicalVolSurface`. Vendor mapping lives only here | Reads `accepted_grid/accepted_table` only, never raw grids. ATM points `ATM/ABSOLUTE_VOL`; OTM ATM column `ATM/ABSOLUTE_VOL`, rest `YIELD_OFFSET_BP/SPREAD_TO_ATM`. Unit `bp` iff stated normal type else `None` |
| `data/vol_surface.py` | Issue #183 canonical vendor-neutral model (+ #185 strike coordinate) | `CanonicalVolSurface` (identity, provenance, points, `volatility_unit`), `surface_id` digest incl. `capture_id`, `content_fingerprint`, conditional serialization for fingerprint stability. No interpolation/conversion/Black-76 |
| `data/vol_surface_store.py` | Issue #183 local SQLite store; only DB opener in repo | `SCHEMA_VERSION=3`, `save_confirmed_surface` (`SAVED`/`ALREADY_SAVED` vs `VolSurfaceConflictError` per fingerprint), fingerprint-verified reads, `list_surfaces` newest-first. Transactional, reads never write |
| `data/vol_surface_grid.py` | Issue #194 reshape stored ATM surface → `Expiry x Swap Tenor` grid for Markets view | Pure permutation, stored order, `None` preserved; refuses non-ATM/spread/holes |
| `data/vcub_normal_vol_resolver.py` (`resolve_vcub_normal_vol`) | Issue #188: one confirmed surface → in-grid normal vol `sigma_vcub`. **Stops at `sigma_vcub`**; imports nothing from `pricing`/`products` by construction | Inputs: surface + `VCUBVolQuery` + caller-supplied `VCUBGridCoordinates`; unit from stated `volatility_unit` (`1bp=1e-4`), space from stated `vol_type`, smile model stated (`PWL` implemented, `SABR`/`None` refused), `ATM+spread` reconstruction, PWL smile + bilinear, `FAIL_CLOSED` extrapolation, unresolved-column block |

### 2.3 Vanilla IRS reference core (P, deterministic — REFERENCE / ADAPTER SOURCE, not canonical)

| Module | What it does | Key contract |
|---|---|---|
| `pricing/irs_engine.py` (`IRSReferenceEngine.price`, `ENGINE_NAME=usd_irs_reference_engine`, `METHOD=single_curve_simple_discount_forecast`) | Minimal USD-only IRS reference MVP: single `RateCurve` for discount+forecast; no bootstrap/calendars/BDC/fixings/DV01 | USD only; floating leg must be `USD_SOFR_TERM_3M/QUARTERLY/reset==pay/COMP.NONE`; legs `ACT_360/ACT_365_FIXED` only; spot or forward-starting only. Output `PricingResult` (`pv=fixed+forecast floating`, `forward=(df_start/df_end−1)/accrual`, payment-time `ACT_365_FIXED`). Formula pins: `DF=1/(1+r·T)`, linear-in-years zero interpolation with flat ends |
| `pricing/curve.py` (`RateCurve`, `tenor_to_years` over 11 labels) | Prototype curve helper | Decimal rates; **do not confuse with `bli_curve_tenor.tenor_to_year_fraction`** (explicitly forbidden reuse) |
| `pricing/schedule.py` (`generate_regular_schedule`) | Regular schedule, no calendars/holidays/system date; stub/month-end raises | MONTHLY/QUARTERLY/SEMI_ANNUAL/ANNUAL |
| `pricing/engine.py` (`price` front door, `PricingEngine` Protocol, registry, `ENGINE_CONTRACT_VERSION=0.1.0`) | Routing only; runtime imports nothing from data/valuation/UI/products | `VALUATION_DATE_MISMATCH` / `MARKET_SNAPSHOT_MISMATCH` / `UNSUPPORTED_PRODUCT` / `ENGINE_ERROR` contract |
| `pricing/result.py` (`PricingResult`, status/error/warning codes) | Value-type result contract; zero layer imports | — |
| `products/swaps.py` (`InterestRateSwap`, `OvernightIndexedSwap`), `products/legs.py` (`FixedLeg`, `FloatingLeg`), `products/deposit_leg.py` (`DepositLeg` future BLI wrapper) | Product schemas only; no curves/fixings/PV | IRS requires `reset_frequency`; OIS requires daily-compounded/averaged + `None`/`DAILY` reset |
| `data/snapshot.py` (`MarketDataSnapshot`), `valuation/context.py` (`ValuationContext`) | Legacy vanilla snapshot (DataFrame-of-rates) + the one allowed data↔pricing bridge (`build_curve`) | Structurally unrelated to `BLIMarketDataSnapshot` — must not be conflated |

### 2.4 BLI shared curve chain (P, pure — LEAVE UNTOUCHED / COMPATIBILITY BOUNDARY, not canonical for new Rates)

`pricing/bli_curve_selector.py::select_curve_points_by_purpose` (structural `(currency, purpose)` filter, never reads rates) → `pricing/bli_zero_curve_nodes.py::build_continuous_zero_curve_nodes` (`CONTINUOUS_ZERO_RATE` gate, per-row date-vs-tenor coordinate, no-fallback without `as_of_date`) → `pricing/bli_zero_rate_interpolation.py::interpolate_continuous_zero_rate` (piecewise-linear, in-range only) → `pricing/bli_discount_factor.py::continuous_discount_factor` (`exp(−r·T)`) → composed by `pricing/bli_curve_discount_factor.py::discount_factor_from_continuous_zero_curve`. Tenor parsing only via `pricing/bli_curve_tenor.py::tenor_to_year_fraction`; valuation-time only via `pricing/bli_valuation_time.py::year_fraction_to_expiry`. Only the two `OPTION_DISCOUNT_CURVE` paths pass `as_of_date=valuation_date`; `bli_forward_clean_price.py` (`BOND_REFERENCE_CURVE`) is deliberately not wired.

Critical compounding note: BLI uses continuous `exp(−r·T)`; `irs_engine` uses simple `1/(1+r·T)`. Any C++ port must decide explicitly (see §6 RED boundary) — never silently unify.

---

## 3. Authoritative tests and acceptance tools

### 3.1 Authoritative (port + gate CI — deterministic, synthetic, no live values)

| Test | What it pins (regression anchor for C++) |
|---|---|
| `tests/test_irs_reference_engine.py` | **Primary anchor.** Synthetic 2-point curve (`6M 0.04`, `1Y 0.042`), `IRS-USD-1Y` fixture; `pv==1506.7928142153469 (abs 1e-9)`, `SUCCESS_WITH_WARNINGS+FORWARD_STARTING`, period counts 2/4, determinism, full fail-closed error table (`UNSUPPORTED_PRODUCT`/`MISSING_MARKET_DATA`/`INVALID_PRODUCT`), input-immutability, no-provider-import hygiene |
| Loader-contract halves of `tests/test_bloomberg_usd_sofr_par_rate_curve.py` + `tests/test_bloomberg_option_discount_curve.py` | Ticker universes/grammars (`S0490Z/D … BLC2 Curncy`, `USOSFR*`), field sets (`LAST_PRICE`±`MATURITY`), unit rules (`Z/100`, `D` untouched, par-rate never `/100`), verbatim-`MATURITY` node dates, canonical ordering, fail-closed table (fake `blpapi`, synthetic `"1.75"/"0.98"` values, `stop()` both paths). Live values never pinned |
| `tests/test_vol_surface.py`, `tests/test_vol_surface_store.py`, `tests/test_vol_surface_store_otm_dimension.py`, `tests/test_vcub_normal_vol_resolver.py` | Surface identity/fingerprint, `bp` gating, store conflict/idempotency/durability (tmp sqlite3), resolver math (hand-computed bilinear e.g. `94.75bp=0.009475`), `bp→decimal`, fail-closed wings/missing/unresolved/negative/unit/space/model rules, structural ban on `pricing`/`products` imports |
| `tests/test_bloomberg_vcub_atm_template.py`, `tests/test_bloomberg_vcub_otm_template.py`, `tests/test_bloomberg_vcub_capture.py` | Token-geometry parsing, axis order, merge/conflict/coverage rules, provenance/confirm invariants (needed only if C++ reimplements the OCR layer; otherwise Python-owned) |

### 3.2 Probes and acceptance tools (do NOT port as truth — reuse the pattern)

Live-Bloomberg probes assert no parity: `tests/test_bloomberg_curve_*_probe*`, `test_bloomberg_dapi/input_sourcing/bond_yield/treasury_ctd*`, `test_bloomberg_vcub_field_hunter*`, `test_ovme_vcub_dcf_convention_experiment*`, OCR e2e, and all `tools/*probe*`, `tools/*acceptance*`, `tools/bloomberg_vcub_field_hunter.py` (recorded verdict: `NO NON-VISUAL ROUTE FOUND`), `tools/bloomberg_vcub_otm_sign_diagnostic.py`, `tools/ovme_vcub_dcf_convention_experiment.py`.

Acceptance CLIs to reuse as a pattern for the future C++ harness (injectable `load`, markdown+JSON, exit codes, never assert a match): `tools/bloomberg_usd_sofr_par_rate_curve_acceptance.py`, `tools/bloomberg_usd_sofr_option_discount_curve_acceptance.py` (run production loader once with default 32; manual Curve #490/SWDF check by Eddy).

Non-Rates tools present but out of scope: `bloomberg_bond_yield_*`, `bloomberg_treasury_futures_ctd_probe`, `treasury_futures_implied_yield_acceptance`, `ust_s490_repo_carry_forward_parity`.

---

## 4. Reuse map — what the C++ engine reuses vs owns

Principle (from #222 + `docs/08`): Python owns UI, orchestration, Bloomberg acquisition, normalization, persistence, research, validation/UAT tooling. The C++ Rates MODULE owns deterministic trade/convention resolution, optional future market/curve resolution, and pricing math. The C++ PRICING KERNEL consumes fully resolved inputs only. No live Bloomberg fetch inside pricing.

The C++ Rates module contains three logically separate layers:

1. Convention / Trade Resolution (`SwapTrade + ConventionSet -> ResolvedSwap`)
2. Market / Curve Resolution (`-> resolved curve input(s)`, via either RED-01 path below)
3. Pricing Kernel (`ResolvedSwap + resolved curve input(s) + Fixings + Model Inputs -> Results`)

Python must not become the authoritative schedule-generation implementation for new Rates products. Python may serialize, persist, display, replay, and orchestrate `ResolvedSwap` objects. #224 owns the methodology contract for `SwapTrade -> ResolvedSwap`; #228 later implements the approved deterministic schedule/fixing/calendar primitives. No USD SOFR convention values are decided in this issue; RED-02 remains open.

RED-01 curve-resolution paths (both explicitly permitted, neither chosen): the C++ Rates MODULE boundary accepts EITHER Path A (Python supplies already-resolved curve input(s)) OR Path B (Python supplies normalized market/instrument inputs plus an approved curve-construction contract, and a deterministic C++ curve-resolution layer produces the resolved curve input(s)). The C++ PRICING KERNEL boundary itself consumes only resolved curve inputs in both paths — one or more resolved curves in whatever form pricing requires. The exact representation, names, container shape, discount/forward distinction, and relationships are NOT decided by #223; those remain owned by #225. No bootstrap instruments, construction helpers, interpolation, curve conventions, or QuantLib defaults are defined in this issue; RED-01 remains open. The working recommendation for the first UAT (consume already-resolved curve input(s) first) remains a recommendation only, not methodology.

| Category | Decision | Items |
|---|---|---|
| LEAVE UNTOUCHED / COMPATIBILITY BOUNDARY (validated BLI contracts — not canonical for new Rates) | Expose data through adapters where appropriate; never modify or lock new Rates DTOs to these | `BLIMarketDataSnapshot` / `BLICurvePoint` semantics, bond-option product/request contracts (`BondOption`, `BLIStandaloneBondOptionRequest`), BLI curve-chain behavior (`bli_curve_selector`, `bli_zero_curve_nodes`, `bli_zero_rate_interpolation`, `bli_discount_factor`, `bli_curve_discount_factor`). See §7 |
| REFERENCE / ADAPTER SOURCE (legacy IRS schemas — not canonical unless proven suitable unchanged) | Read and adapt; do not lock new Rates DTOs to these in #223 | Legacy `MarketDataSnapshot`, `RateCurve`, `InterestRateSwap` / `OvernightIndexedSwap`, `FixedLeg` / `FloatingLeg`, `DepositLeg`, `ValuationContext`, `pricing/schedule.py`, `pricing/engine.py` front-door concept, `pricing/result.py` value-type concept. Authoritative new Rates contracts remain owned by #224 (`SwapTrade` / `ConventionSet` / `ResolvedSwap`) and #225 (market/model/result contracts) |
| REUSE AS-IS (Python, no C++ equivalent) | Bloomberg acquisition, transcription, canonical models, persistence, UI | Both curve loaders (§2.1) incl. shared DAPI helpers + 32-tenor universe; entire VCUB capture/template/OCR/screen-reader chain; `vcub_vol_surface_adapter`; `vol_surface` canonical model; `vol_surface_store` (SQLite); `vol_surface_grid`; acceptance/probe tools |
| REUSE VIA ADAPTER (existing Python output → versioned DTO → C++) | Curve/vol outputs cross the boundary as data, never as code | #490 `BLICurvePoint` rows (with verbatim `maturity_date`) → resolved zero/DF nodes via adapter (not as the canonical new Rates curve contract); par-rate points → display only, never a pricing input; `CanonicalVolSurface` + `resolve_vcub_normal_vol` outputs (`sigma_vcub` + audit record) → vol DTO via adapter; IRS reference fixtures (`1506.79…` golden value, error table) → C++ regression gates |
| OWN IN C++ (new, isolated, deterministic) | Resolution layers + pricing kernels, implemented after Gate A against approved contracts | C++ Convention / Trade Resolution: `SwapTrade + ConventionSet -> ResolvedSwap` per the #224 contract (implemented in #228). C++ Market / Curve Resolution: `-> resolved curve input(s)` via either RED-01 path (already-resolved curve input(s) from Python, or normalized construction inputs + approved construction contract resolved deterministically in C++); curve contracts owned by #225, RED-01 open. C++ Pricing Kernel on resolved inputs only: discount/forward math (compounding decided explicitly per §6); leg PV / NPV / par rate / annuity-PVBP / DV01; Black-76 / shifted-Black / Bachelier kernels (Phase 2+); Hull-White 1F calibration + Bermudan exercise (Phase 3+); range-accrual observation/barrier/fixing-probability kernels (Phase 4+). Pure helpers with no I/O are the only port candidates — each pinned by its Python test before porting |
| KEEP PYTHON-ONLY (must not move to C++) | Anything touching network, pixels, files, UI, or live credentials | `import blpapi` session/request/event handling; OCR/pixel geometry; SQLite open/read/write; Streamlit/workbench server; `//blp/*` discovery; DAPI host/port/service constants as live config |

Anti-duplication rule: any proposal to duplicate an existing validated data contract (ticker grammar, field set, unit conversion, `BLICurvePoint` semantics, surface identity/fingerprint, store conflict semantics, resolver fail-closed rules) that is not strictly necessary **stops for review** per this issue's stop condition. This document proposes no such duplication.

---

## 5. Python ↔ C++ integration boundary (DTO contract direction)

Authoritative detail (field-level schemas, curve/vol/trade/model/result representations) is deferred to #224 (conventions/resolved swap) and #225 (market/model/result contracts) and #226 (build/concurrency/caching/benchmarks). This issue locks only the boundary shape:

```text
Python acquisition / normalization / orchestration
        |
        v
explicit versioned integration boundary
(Trade + Convention + Market DTOs; field detail owned by #224/#225)
        |
        v
C++ Rates Module
        |
        +-- Convention / Trade Resolution
        |      SwapTrade + ConventionSet -> ResolvedSwap
        |      (contract owned by #224, implemented in #228)
        |
        +-- Market / Curve Resolution
        |      EITHER already-resolved curve input(s) (Path A)
        |      OR approved construction inputs + contract (Path B)
        |      -> resolved curve input(s)
        |      (curve contracts owned by #225; RED-01 open, neither path chosen)
        |
        +-- Pricing Kernel
               ResolvedSwap
               + resolved curve input(s)
               + Fixings
               + Model Inputs
               -> Results
               (kernel consumes resolved curve inputs only; no live Bloomberg fetch)
        |
        v
PricingResult / RiskResult / CalibrationResult (typed, auditable)
        |
        v
Python workflow / persistence / UI
(serialize, persist, display, replay, orchestrate resolved objects)
```

Boundary rules:

1. **Module vs kernel boundary (RED-01 open).** The C++ Rates MODULE boundary accepts either RED-01 outcome — Path A: Python supplies already-resolved curve input(s); Path B: Python supplies normalized market/instrument inputs plus an approved curve-construction contract, and a deterministic C++ curve-resolution layer produces the resolved curve input(s). The C++ PRICING KERNEL boundary itself consumes only resolved curve inputs (`ResolvedSwap + resolved curve input(s) + Fixings + Model Inputs`): explicit schedules, stated fixings/observations, curve inputs already resolved into the form required for pricing (one or more resolved curves), stated vol numbers with stated unit/space/model, and explicit exercise/settlement terms. No ticker, no field name, no `blpapi`, no pixel, no SQLite path crosses the boundary. No number of curves, discount-vs-forward mapping, curve identifiers, container structure, field names, cross-curve relationships, bootstrap instruments, construction helpers, interpolation, curve conventions, QuantLib defaults, QuantLib representation, or USD SOFR convention values are decided here — curve representation remains owned by #225.
2. **Explicit, stable, auditable, versioned boundary.** Per parent #222, the Python/C++ integration boundary must be explicit, stable, auditable, and versioned (DTO/JSON). Field-level canonical contract decisions are NOT taken in this issue: which DTO contains which version field, whether every DTO has a version field, exact version-field names, exact schema-evolution policy, exact provenance field placement, exact fingerprint fields, and cache-key field composition remain with their owning issues (#224 for trade/convention/resolved-swap contracts, #225 for market/curve/fixing/vol/exercise/settlement/result contracts, #226 for caching implementation concerns).
3. **Ownership split.** Acquisition + normalization + persistence stay in Python (§4). Trade/convention resolution methodology (`SwapTrade -> ResolvedSwap`) is owned by #224 and implemented in #228 — Python must not become its authoritative implementation. Curve representation choice and model calibration interfaces are methodology — they are named here as deferred decisions (§6), not decided here.
4. **Result discipline.** C++ returns values + assumptions + diagnostics + warnings/errors in the existing `PricingResult` spirit (status codes, no invented data, fail-closed on out-of-range/unresolved/missing). `docs/08` rule applies: every accelerated backend matches its Python reference within documented tolerance before acceptance.
5. **No QuantLib leakage across the boundary.** QuantLib (C++ side, future) is a computational library behind the engine's internal interface, as it is on the Python side (`bli_quantlib_bond_adapter` precedent: schedule/accrual only, no raw `ql.*` in schemas). QuantLib defaults must never silently become Shiori methodology (#222 core principle).
6. **Performance posture.** Caching (curve/DF/schedule/fixing reuse), concurrency (thread-safety contract), and benchmark methodology are #226's scope; this boundary requires DTOs that support batching and caching, with exact cache-key composition owned by #226.

---

## 6. Ownership of acquisition, normalization, pricing, persistence

| Concern | Owner | Notes |
|---|---|---|
| Acquisition (Bloomberg DAPI, tickers, fields, pixels/OCR) | Python (`data/bloomberg_*`, `tools/*`) | Lazy `blpapi`, session lifecycle, fail-closed validation. C++ never acquires |
| Normalization (tenor validation, percent→decimal, `MATURITY`-verbatim dates, capture→canonical, store) | Python (`data/*`, `pricing/bli_*` pure chain for node prep) | Pure node-prep helpers are port candidates for C++ kernels, but the authoritative normalization stays Python |
| Trade definition + convention/trade resolution (schedules, day counts, calendars, stubs, fixings, `SwapTrade -> ResolvedSwap`) | C++ Rates module (resolution layer) per the #224 contract, implemented in #228; Python serializes/persists/displays/replays/orchestrates `ResolvedSwap` | Python must not become the authoritative schedule-generation implementation for new Rates products. No convention values decided here; RED-02 open |
| Market / curve resolution (`-> resolved curve input(s)`; RED-01 open, neither path chosen) | C++ Rates module (resolution layer) under an approved construction contract; curve representation (CurveSet / DiscountCurve / ForwardCurve semantics) owned by #225 | Module accepts either Path A (already-resolved curve input(s) from Python) or Path B (normalized construction inputs + approved contract resolved deterministically in C++). Kernel consumes resolved curve inputs only. No instruments, helpers, interpolation, conventions, or QuantLib defaults defined here |
| Model assumptions (vol space/model, smile, calibration objective) | Stated inputs; methodology owned by Sophira/Eddy | Resolver precedent applies: unstated unit/space/model fails closed. #225 owns the contracts |
| Pricing (PV/NPV/par/annuity/Greeks/calibration/exercise) | C++ (future), validated against Python references | Deterministic only (AGENTS.md rule 6). No system clock, no I/O in kernels |
| Persistence (SQLite store, run exports, snapshots) | Python | Store conflict/idempotency semantics (§2.2) are preserved, not reimplemented |
| UI / orchestration / research / UAT | Python | `docs/08` cockpit rule; C++ behind stable interfaces |

RED boundary (no decisions taken in this issue): #222 RED-01 (curve authority for first UAT: resolved zero/DF curve vs bootstrap-from-instruments) and RED-02 (`USD_SOFR_OIS_V1` workstation convention authority: spot lag, frequencies, day counts, lags, calendars, BDC, stubs, compounding) remain **open and undecided, owned by Sophira/Eddy**. Neither resolved-curve-first nor internal bootstrap is chosen as final methodology in this issue; the working recommendation for the first UAT (consume already-resolved curve input(s) first) remains a recommendation only. No actual USD SOFR convention values are decided in this issue. This document's three-layer posture (trade resolution per #224 contract, curve resolution via either RED-01 path, kernel on resolved inputs only) is compatible with either RED-01 outcome and does not pre-decide it. Any real new pricing/schema/validation/fallback methodology decision encountered downstream = STOP and ask Eddy.

---

## 7. Compatibility boundary protecting the bond-option line

The validated bond-option line is stable. Rates work must not regress it. Frozen (read-only for Rates) — classified LEAVE UNTOUCHED / COMPATIBILITY BOUNDARY, not canonical for new Rates:

- Product contract: `products/bond_option.py` (`BondOption` frozen fields, PRICE/YIELD mutual exclusion, EUROPEAN/AMERICAN exercise rules).
- Engine entry points + result contract: `pricing/bli_pricing_engine.py` (`price_bli_mvp`, `price_bli_mvp_standalone_option`, engine names/versions, `method` tokens, `pv=per_100×notional/100`, position never flips `pv`, Greeks-on-standalone-success only), `pricing/engine.py`, `pricing/result.py`.
- Pure Black-76: `pricing/bli_black76_price_option.py` (per-100 only, `math.erf/exp`, zero cross-imports).
- Curve chain semantics: `bli_curve_selector` / `bli_zero_curve_nodes` / `bli_zero_rate_interpolation` / `bli_discount_factor` / `bli_curve_discount_factor` — no extrapolation/fallback additions, no `CONTINUOUS_ZERO_RATE` gate relaxation, no `as_of_date` auto-defaulting, no purpose-mixing (`BOND_REFERENCE` vs `OPTION_DISCOUNT` vs `DEPOSIT`/`FUNDING` never blended; `OPTION_DISCOUNT` never silently reused for Rates discounting).
- QuantLib adapter: `pricing/bli_quantlib_bond_adapter.py` (schedule/accrual only, `NullCalendar`, optional import, `BLIQuantLibNotAvailableError` propagates).
- Data schemas: `data/bli_snapshot.py` (`BLICurvePoint`, purposes, bases, ACTIVE-only construction, node-level dedupe/ambiguity), `data/bli_standalone_option_request.py` (frozen refs, ISIN/currency/date/no-look-ahead gates).
- Build: core never requires QuantLib (`pyproject.toml`: QuantLib in `quant` extra only; CI installs `[quant]`, local minimal installs may not — the missing-QuantLib error must propagate, never be caught into `FAILED`).

Shared-module rule for Rates: the BLI curve chain and QuantLib adapter may be **read** (and their pure math ported with pinned tests), never **modified** in a way that changes bond-option behavior. Existing BLI contracts may expose data through adapters where appropriate, but they are not the canonical new Rates contract — that contract is owned by #224 (`SwapTrade` / `ConventionSet` / `ResolvedSwap`) and #225 (market/model/result contracts). Legacy IRS schemas are REFERENCE / ADAPTER SOURCE unless repository evidence proves them suitable unchanged. Any change with observable bond-option impact requires its own issue, deterministic tests, and Eddy's approval (AGENTS.md rules 6–7).

---

## 8. Proposed in-repo module layout (tentative — not created in this issue)

Per #222 direction; exact path/name not locked here (locked no later than #227). No directory is created by this issue:

```text
Shiori-Pricing-Lab/
├── src/shiori_pricing_lab/        # existing Python application/data/bond-option line (stable)
├── tests/                         # existing Python tests (anchors in §3.1 stay green)
├── cpp/
│   └── rates_engine/              # NEW (first slice in #227, after Gate A)
│       ├── include/shiori_rates/  # public DTO + engine interfaces (contracts owned by #224/#225)
│       ├── src/                   # resolution/ (SwapTrade->ResolvedSwap per #224, impl #228) + kernels/ (swaps, curves, legs, swaption, HW1F, accrual)
│       ├── tests/                 # GoogleTest/Catch2 ports of §3.1 anchors
│       ├── benchmarks/            # Google Benchmark baselines (#226 methodology)
│       └── CMakeLists.txt         # C++20, pinned deps (QuantLib, nlohmann::json, gtest/catch2)
├── docs/                          # this document + #224/#225/#226 follow-ups
└── tools/                         # existing acceptance tooling + future C++ harness pattern reuse
```

Target stack (from #222, unchanged): C++20, CMake, QuantLib, `nlohmann::json`, GoogleTest or Catch2, Google Benchmark. Bloomberg C++ BLPAPI only later if justified — the first architecture must not duplicate the working Python acquisition path without concrete need.

---

## 9. Validation performed (this issue)

- Verified `origin/main` HEAD = base SHA; branch cut from it; no other branches touched.
- Read (full or large-part): both SOFR loaders, all five VCUB capture/template modules, vol adapter, vol trio, normal-vol resolver, `irs_engine`, `curve`/`schedule`/`engine`/`result`, `swaps`/`legs`/`deposit_leg`, `bli_snapshot`/`snapshot`, `valuation/context`, BLI curve chain (`selector`/`nodes`/`tenor`/`discount`), `pyproject.toml`, `docs/08/01/02/03/04/10`, `tools/` listing, and the §3 test set headers/cases (via primary research + three parallel codebase audits).
- Confirmed no `cpp/`, `arch/`, `CMakeLists.txt`, `pybind`, or extension module exists — nothing to reconcile against.
- This change adds one docs file only: no production code, no schema, no test, no tool, no workflow change. `git diff --check` clean; `git status` shows only the new document.
- No Bloomberg or workstation evidence created, cited, or implied beyond what the repository already records.

---

## 10. Next actions and execution recommendation (out of scope for this issue, for sequencing only)

- #224 — USD SOFR OIS convention contract + resolved swap representation (owns RED-02-adjacent convention content and the `SwapTrade` / `ConventionSet` / `ResolvedSwap` contract).
- #225 — Rates market/model/result contracts (owns MarketSnapshot / CurveSet / DiscountCurve / ForwardCurve / FixingStore / vol / exercise / settlement / result contracts; waits for #224 per the recommendation below).
- #226 — C++ build / QuantLib isolation / concurrency / caching / benchmark methodology.
- Gate A exit: #222 architecture accepted; RED-01/RED-02 resolved or explicitly deferred with owner/evidence requirement.
- First implementation slice after Gate A: M1 C++ vanilla-swap foundation (#227+) reusing this boundary.

Execution recommendation after #223 is accepted:

- #224 and #226 MAY PROCEED IN PARALLEL.
- #225 SHOULD WAIT FOR #224.

Reason: #224 owns USD SOFR convention, fixing/observation, schedule, and `ResolvedSwap` semantics. #225 includes FixingStore and historical-vs-forecast behavior, so opening it before #224 finishes risks overlapping the same convention contract. #226 is largely orthogonal (C++20/CMake, QuantLib isolation, test framework, thread-safety, caching architecture, benchmark methodology, CI) and may proceed in parallel with #224 provided it does not invent DTO field semantics that belong to #225. Do not split #225 into a partial issue merely to increase parallelism.

---

## 11. RED findings

No new RED methodology decisions are taken in this issue. No actual USD SOFR convention values are decided here. Neither RED-01 path (resolved-curve-first vs internal bootstrap) is chosen. Known open REDs carried from #222 (RED-01 curve authority, RED-02 SOFR convention authority) remain explicitly open and undecided. No stop-condition trigger occurred: this document duplicates no validated data contract and changes no methodology.

---

*End of Issue #223 deliverable. Single architecture/reuse document; no production functionality.*
