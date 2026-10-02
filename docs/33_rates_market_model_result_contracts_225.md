# 33 — Rates market / model / result contracts (Issue #225)

Parent: #222 — C++ Rates Engine Foundation.
This issue: #225 — ARCHITECTURE / SCHEMA ONLY. No pricing implementation.

| Field | Value |
|---|---|
| Issue | #225 |
| Owner | OpenCode (primary repository execution agent) |
| Branch | `arch/225-rates-market-model-result-contracts` |
| Base SHA | `b7f17d08b172a4e37231a2801efea106d98b961c` (main, merge of PR #241) |
| Scope | Model-independent typed market/model/result contracts for the future C++ Rates Engine |
| Non-goals | No pricing, calibration, bootstrapping, Bloomberg adapters, UI, endpoints, QuantLib, build changes |
| Deliverable | This document only (`docs/33_rates_market_model_result_contracts_225.md`) |

Methodology authority: Sophira. Final merge authority: Eddy. Independent reviewer: Codex.
Architecture baseline: #223 as merged (`docs/31_rates_reuse_boundary_223.md`) + #224 as merged (`docs/32_usd_sofr_ois_convention_224.md`, PR #241).
Existing bond-option product line: STABLE — do not refactor, rename, migrate, or rewrite validated behavior in this slice.
Current status: `PENDING SOPHIRA / CODEX REVIEW — DO NOT MERGE`. The final gate `READY TO MERGE — 等待 Eddy 明確批准` applies only after scope, latest HEAD, validation, PR body, and Codex review are all accepted.
AGENTS.md compliance: smallest coherent change (one document), reuse before adding, no fabricated market data or Bloomberg evidence, no methodology guesses, no merge without explicit approval.

---

## 1. Purpose / scope / non-goals

### 1.1 Purpose

Define the model-independent typed contracts consumed and produced by the future C++ Rates Engine, so that #227 / #228 / #229 can later be implemented literally without guessing:

- what exact typed market, model, and result objects cross the Rates Engine boundary;
- what each field means, in what unit, under which methodology version;
- what provenance and version information is mandatory for deterministic replay;
- what conditions must fail closed.

This issue answers: **what crosses the boundary, with what meaning, under what identity — not how it is computed.**

### 1.2 In scope

The minimum complete typed contract shapes and semantics for:

1. `MarketSnapshot`
2. `CurveSet`
3. `DiscountCurve`
4. `ForwardCurve`
5. `FixingStore`
6. swaption volatility inputs / quote representation
7. exercise representation
8. settlement representation
9. model / calibration inputs where architecture requires them
10. `PricingResult` (Rates)
11. `RiskResult` (Rates)
12. `ModelCalibrationResult`
13. source / version / provenance / diagnostics required for deterministic replay

Plus: how these objects relate to the already-approved `SwapTrade -> ConventionSet -> ResolvedSwap` contract from #224.

### 1.3 Non-goals (binding)

This document does NOT:

- implement pricing, calibration, curve bootstrapping, or Bloomberg adapters;
- change UI, endpoints, or existing bond-option behavior;
- touch runtime code, introduce QuantLib, or change the build system;
- select a production Bloomberg curve, bootstrap instrument set, interpolation scheme, extrapolation scheme, day count, compounding rule, vol methodology, model, fixing treatment, settlement methodology, or desk methodology unless already explicitly approved by repo evidence;
- solve #226 (build / QuantLib isolation / concurrency / caching / benchmark methodology);
- solve #227 / #228 / #229 (C++ skeleton, curve/fixing/schedule primitives, swap kernel).

Where #226 concerns arise, this document records the dependency / boundary only and does not solve them.

### 1.4 Stop conditions (binding)

STOP and report to Sophira / Eddy instead of guessing if any decision requires:

- Bloomberg workstation evidence;
- desk convention evidence;
- production curve authority / construction methodology;
- production vol authority / quote methodology;
- model choice / calibration methodology;
- interpolation / extrapolation choice;
- fixing treatment not already approved;
- settlement methodology not already approved;
- product-specific pricing methodology belonging to later issues.

Do not silently adopt QuantLib defaults, market folklore, public examples, Bloomberg defaults, or "standard SOFR practice" as Shiori production methodology. Architecture may define a FIELD capable of carrying such a choice. Architecture may NOT choose an unresolved VALUE.

---

## 2. Existing accepted architecture dependencies

This document depends on — and does not change — the following accepted baseline:

### 2.1 #222 (control epic)

- One Shiori system, one repository, separate pricing engines.
- Python owns UI, orchestration, Bloomberg capture, persistence, research, validation workflows.
- Python must not sit in the production pricing hot path for new Rates products.
- C++ Rates Engine receives explicit, fully resolved inputs and performs no live Bloomberg fetch during a calculation.
- Keep Market Data / Trade Definition / Convention-Trade Resolution / Model Assumptions / Pricing Engine / Risk-Calibration separate.
- RED-01 (curve authority for first production UAT) and RED-02 (USD SOFR workstation convention authority) remain open unless live repo says otherwise. This document touches RED-01 scope (curve representation) but does not close it; RED-02 values remain owned by #224 / workstation evidence.

### 2.2 #223 (reuse map + Python/C++ boundary, `docs/31`)

Preserved without modification:

- Three logically separate C++ Rates module layers:
  1. Convention / Trade Resolution (`SwapTrade + ConventionSet -> ResolvedSwap`);
  2. Market / Curve Resolution (`-> resolved curve input(s)`, via either RED-01 path);
  3. Pricing Kernel (`ResolvedSwap + resolved curve input(s) + Fixings + Model Inputs -> Results`).
- The C++ PRICING KERNEL boundary consumes only resolved inputs. No ticker, field name, `blpapi`, pixel, or SQLite path crosses the kernel boundary.
- Ownership table: acquisition + normalization + persistence stay in Python; trade/convention resolution methodology owned by #224; curve representation and model calibration interfaces are methodology deferred to #225 (this issue); caching / concurrency / benchmarks owned by #226.
- Compatibility boundary: BLI chain is LEAVE UNTOUCHED, legacy IRS schemas are REFERENCE / ADAPTER SOURCE.
- No number of curves, discount-vs-forward mapping, curve identifiers, container structure, field names, cross-curve relationships, bootstrap instruments, construction helpers, interpolation, curve conventions, QuantLib defaults, or USD SOFR convention values were decided in #223. Those curve-representation decisions are owned by #225 (this issue) as CONTRACT SHAPE; their production VALUES remain RED where evidence is missing.
- Versioning / provenance / cache-key field composition details were deferred to owning issues. This document owns the #225 share.

### 2.3 #224 (USD SOFR OIS convention + resolved swap, `docs/32`, PR #241)

Preserved without modification:

- `SwapTrade` = raw trade economics as dealt (identifiers, direction, currency, notional, raw `trade_date` anchor, stated `maturity_date`, fixed rate + spread in canonical `DECIMAL_ANNUAL`, floating index `USD_SOFR`). No derived schedule, no adjusted date, no fixing, no curve, no valuation date. Convention-like labels are NOT V1 trade inputs.
- `ConventionSet` (`USD_SOFR_OIS_V1`, PROPOSED, every value UNRESOLVED — RED-02) = versioned rulebook. Authority R2 RESOLVED: `ConventionSet` authoritative, no per-trade overrides, conflicts fail closed. Contract shape is role-capable for calendar / BDC (spot-lag counting, effective-date adjustment, per-leg schedule / lag-counting / payment-date, observation).
- `ResolvedSwap` = fully deterministic output and complete resolved trade input for the kernel: retained economics (canonical units), resolved `effective_date` with rule recorded, explicit per-leg period schedules, per-period accrual fractions with day-count rule recorded (both boundary sets preserved), compounding / observation / fixing calendars per period with rule recorded, spread treatment recorded once approved, fixed-coupon derivation recorded, per-leg payment ordering provenance, principal-exchange behavior recorded, stubs with rule recorded, full provenance (`schema_version` + `convention_set_id` + per-derivation rule).
- Wire-schema versioning rule RESOLVED for the three #224 contracts: `SWAP_TRADE_V1` / `USD_SOFR_OIS_V1` / `RESOLVED_SWAP_V1`, unknown versions fail closed, methodology versions PROPOSED → LOCKED.
- Owner decisions R1 (trade-date anchor, spot-starting V1 only, forward-starting out of scope and fail-closed) and R2 recorded in `docs/32 §2` are not re-decided here.
- RED-02 dimension values D1–D13 (spot lag, frequencies, day counts, compounding formula, observation mechanics, payment lags, calendars, BDCs, stubs, compounding convention, notional exchange, coupon formula) plus accrual-boundary, trade-date normalization, spread treatment remain UNRESOLVED and are not filled by this document.
- Evidence list E1–E6 remains the authority path for locking V1 values.

### 2.4 Reference sources read (not promoted to methodology)

- `products/enums.py` — controlled vocabularies (`Frequency`, `DayCount`, `BusinessDayConvention`, `FloatingIndex` incl. `USD_SOFR`, `CompoundingMethod`, `Currency`, `PayReceive`; bond-linked `ExerciseStyle`, `SettlementType`). Vocabulary labels may be referenced; no V1 value is selected here.
- `pricing/result.py` — value-type result spirit (status, structured messages, engine provenance, diagnostics). The legacy `PricingResult` shape is REFERENCE ONLY for the new Rates result contracts; §12 defines the Rates shape independently.
- `pricing/irs_engine.py` (`ENGINE_NAME=usd_irs_reference_engine`, `METHOD=single_curve_simple_discount_forecast`, golden `pv==1506.7928142153469`) — regression anchor for legacy term-IRS behavior only; NOT a source of OIS curve, compounding, fixing, or vol values.
- `data/snapshot.py` (`MarketDataSnapshot`), `valuation/context.py` (`ValuationContext`), `pricing/curve.py` (`RateCurve`) — legacy vanilla snapshot / bridge shapes; structurally unrelated to the new Rates `MarketSnapshot` defined here; must not be conflated.
- `data/vol_surface.py` (`CanonicalVolSurface`), `data/vcub_vol_surface_adapter.py`, `data/vcub_normal_vol_resolver.py` (`resolve_vcub_normal_vol`, `RESOLVER_VERSION=IN_GRID_BILINEAR_V1`, `bp=1e-4`, `FAIL_CLOSED` extrapolation) — capture → canonical → resolved-vol precedent. The resolver's fail-closed, stated-unit, stated-model discipline is the pattern this document generalizes; no VCUB value becomes a Rates Engine input without passing through the §9 contract.
- `data/bloomberg_option_discount_curve.py` / `data/bloomberg_usd_sofr_par_rate_curve.py` — adapter-source market data only (ticker grammar, field sets, unit rules, verbatim `MATURITY`); not convention authority.

---

## 3. Ownership boundary (restated, not changed)

```
Python acquisition / normalization / orchestration
        |
        v
explicit versioned integration boundary
(Trade + Convention + Market DTOs; #224 owns trade/convention detail,
 #225 owns market/model/result detail defined here)
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
        |      (curve CONTRACT SHAPE owned by #225 §7; RED-01 VALUE open)
        |
        +-- Pricing Kernel
               ResolvedSwap
               + resolved curve input(s)
               + Fixings (FixingStore §8)
               + Model Inputs (vol §9, exercise §10, settlement §11, calibration §14)
               -> Results (§12-§14)
               (kernel consumes resolved inputs only; no live Bloomberg fetch)
        |
        v
PricingResult / RiskResult / CalibrationResult (typed, auditable, §12-§14)
        |
        v
Python workflow / persistence / UI
(serialize, persist, display, replay, orchestrate resolved objects)
```

Rules (from #223, applied):

1. No live Bloomberg dependency inside the C++ pricing kernel. The kernel consumes explicit, deterministic, versioned inputs.
2. QuantLib may later be used behind an isolation boundary (#226), but QuantLib defaults are NOT Shiori methodology and never cross the DTO boundary as implicit semantics.
3. Python owns Bloomberg capture, normalization / persistence, workflow / UI integration, research evidence, UAT orchestration. Python must not become the authoritative Rates schedule-generation implementation.
4. C++ owns the deterministic Rates pricing hot path (resolution layers + pricing kernels, implemented after Gate A against approved contracts).
5. The MODULE boundary accepts either RED-01 path; the KERNEL boundary consumes only resolved curve inputs in both paths.

---

## 4. Core principles

Applied to every contract in §6–§15:

- **P1 — One economic fact per field.** Prefer separate typed fields over compound strings. Never collapse source + methodology + version into one string. Never encode the word `RESOLVED` where the actual semantic value belongs (§15 lesson from #224): carry the actual value in one field and the resolution / evidence status in a separate field where both are needed.
- **P2 — No naked numbers.** No naked array of doubles whose economic interpretation must be guessed. Every numeric array carries its coordinate meaning, value type, and unit. A naked volatility `double` is forbidden (§9).
- **P3 — Units and meaning are machine-readable.** Every numeric field has an explicit unit field or a unit-suffixed enum value (e.g. `DECIMAL_ANNUAL`, `DISCOUNT_FACTOR_UNIT maps to §7`). Comments never carry critical semantics that disappear when parsed.
- **P4 — Provenance is structural.** Source, methodology identifier + version, schema version, and snapshot identity are fields, not prose. Python must be able to serialize deterministically; C++ must consume without prose-based inference; downstream UI/reporting must interpret without knowing QuantLib internals.
- **P5 — Fail closed.** Missing, unresolved, ambiguous, or version-unknown inputs are refused with a named reason, never silently defaulted, interpolated over, forecast from history, or coerced across versions.
- **P6 — Shape vs value separation.** Architecture defines FIELDS capable of carrying a choice; architecture does not choose an unresolved VALUE. Every field whose value requires RED evidence is marked `UNRESOLVED — RED` with its owner and evidence requirement, following the #224 D/E convention.
- **P7 — Deterministic replay.** Given the same versioned inputs (trade + convention + market snapshot + model inputs), the same engine version must reproduce the same result bit-for-bit within documented tolerance. Replay metadata (§15) makes this auditable.
- **P8 — Minimal future-proofing.** The exercise (§10) and settlement (§11) shapes admit the known #222 product sequence (European swaption now, Bermudan / callable later) without redesigning the contract, but no hypothetical exotic features are overdesigned.

Contract-quality gate (asked of every field before acceptance):

1. What exact fact does this field encode?
2. Is the value machine-readable?
3. Are units explicit?
4. Is economic meaning explicit?
5. Is provenance explicit where required?
6. Is version identity explicit where required?
7. Can Python serialize it deterministically?
8. Can C++ consume it without prose-based inference?
9. Can downstream UI/reporting interpret it without knowing QuantLib internals?
10. Does this field accidentally make a methodology decision that belongs to a later issue?

---

## 5. Canonical object graph

```
MarketSnapshot (§6)
 ├── snapshot_id / valuation_date / schema_version / provenance
 ├── CurveSet (§7)
 │     ├── DiscountCurve (§7.2)  [1..n, role-tagged]
 │     └── ForwardCurve  (§7.3)  [0..n, role-tagged, index-associated]
 ├── FixingStore (§8)
 ├── VolatilityInput (§9)   -- quote-typed vol surface / cube / single quote
 ├── ExerciseTerms (§10)    -- European now; Bermudan-capable shape
 ├── SettlementTerms (§11)
 ├── ModelInput (§9.6 / §14.1) -- model identity + parameters for HW1F etc.
 └── replay_fingerprint (§15)

ResolvedSwap (#224, input alongside MarketSnapshot)
        +
MarketSnapshot (§6: curves + fixings + vol + exercise/settlement refs)
        +
ValuationContext (valuation_date, reporting currency; legacy shape REFERENCE ONLY)
        |
        v
C++ Rates Pricing Kernel (deterministic, no I/O, no clock)
        |
        +---> PricingResult (§12)
        +---> RiskResult (§13)
        +---> ModelCalibrationResult (§14)
```

Relationship to `SwapTrade -> ConventionSet -> ResolvedSwap` (#224):

- `ResolvedSwap` is the complete resolved TRADE input. It carries no market snapshot, no curve, no fixing values, no model config.
- `MarketSnapshot` is the complete resolved MARKET input. It carries no trade economics, no convention rules, no resolved schedules.
- The kernel joins them at valuation time. Neither object duplicates the other's authority. There are no two competing authoritative copies of trade economics or of market data at the pricing boundary.
- `ConventionSet` version (`convention_set_id`) is recorded on `ResolvedSwap`; market construction methodology version (where RED-01 Path B applies) is recorded on the curve objects (§7); both are echoed in result replay metadata (§12–§15) so a result names the exact rulebooks that produced its inputs.

---

## 6. MarketSnapshot

### 6.1 Role

`MarketSnapshot` is the single versioned container for everything the kernel needs from the market for one valuation date. It is the Rates analogue of the legacy `MarketDataSnapshot` in spirit only — the legacy shape (`_rates_points` DataFrame, `source` string, free-form `metadata`) is REFERENCE ONLY and is not the canonical Rates contract. The Rates `MarketSnapshot` is fully typed, unit-explicit, provenance-carrying, and replay-capable.

### 6.2 Contract shape

```yaml
market_snapshot:
  schema_version: MARKET_SNAPSHOT_V1        # machine-readable wire version; unknown -> fail closed
  snapshot_id: <string>                     # content-derived or allocated unique id; see §6.3
  valuation_date: <ISO date YYYY-MM-DD>     # the date being valued; never defaulted to system date
  captured_at: <ISO-8601 timestamp+offset>  # when this snapshot was assembled (distinct from valuation_date)
  source: <enum>                            # e.g. BLOOMBERG_DAPI | SCREEN_TRANSCRIPTION | SYNTHETIC_FIXTURE | RESEARCH_ADAPTER; value list owned here as vocabulary, selection per snapshot is data
  source_detail: <string>                   # ticker universe / screen / adapter name; free text, never methodology
  curve_set_ref: <curve_set_id>             # identity link to the CurveSet in §7 (embedded or by id; see §6.4)
  fixing_store_ref: <fixing_store_id>       # identity link to the FixingStore in §8
  volatility_ref: <volatility_input_id>     # identity link to the VolatilityInput in §9 (or NULL_IF_UNUSED with reason)
  exercise_settlement_refs: <ids>           # links only where valuation requires them; trade-level terms live on the trade, market-level defaults never silently apply
  model_input_ref: <model_input_id | NULL> # link to ModelInput where the kernel requires it (§14.1); NULL only with explicit reason
  provenance:
    assembled_by: <string>                  # pipeline / operator identity
    adapter_versions: <map>                 # adapter name -> version for every Python adapter that contributed data
    upstream_ids: <list>                    # CanonicalVolSurface surface_id(s), loader batch ids, fixture ids consumed
    evidence_refs: <list>                   # workstation evidence citations (E-ids) where applicable; empty is data, not a claim
  content_fingerprint: <hex>                # digest of canonical serialization; see §15
  diagnostics:
    unresolved_fields: <list>               # identity/coverage gaps carried structurally, never papered over
    warnings: <list[code+message]>          # machine-readable codes; §16
```

### 6.3 Identity rules

- `snapshot_id` identifies one immutable observation set. Two captures sharing all market content but assembled at different instants are two snapshots unless the fingerprint rule says otherwise; the rule itself is part of this contract and is fixed here: `snapshot_id = digest(canonical MarketSnapshot content excluding snapshot_id itself) + captured_at disambiguator`. The disambiguator is a separate field, never folded into the digest input, so identical content re-assembled is idempotent and different content never collides.
- `valuation_date` is required and explicit. There is no default. A snapshot is never valued under a different date: the kernel compares `valuation_date` against the valuation request and fails closed on mismatch (`VALUATION_DATE_MISMATCH`).
- `captured_at` is when the snapshot was assembled. It is never a substitute for a market quote timestamp: where a quote timestamp is known it lives on the quote object (§7–§9); where it is unknown the field is `NULL_WITH_REASON`, never inferred.

### 6.4 Embedding vs referencing

- The canonical wire form EMBEDS the `CurveSet`, `FixingStore`, and `VolatilityInput` payloads inside `MarketSnapshot` for replay atomicity. Reference-by-id links (`curve_set_id`, etc.) are the identity index for caching / persistence (#226), not a substitute for content at the kernel boundary. A kernel call that receives ids without embedded content fails closed (`MISSING_MARKET_DATA`).
- Cache-key composition (which fields participate in curve/DF/schedule/fixing reuse) is owned by #226. This contract guarantees only that every field the cache may key on is present and typed.

### 6.5 Fail-closed rules

- Unknown `schema_version` → refuse before reading any other content.
- `valuation_date` blank, unparseable, or mismatched with the valuation request → `VALUATION_DATE_MISMATCH`.
- Any embedded section with unknown schema version → refuse the whole snapshot.
- Any `NULL_WITH_REASON` in a section the requested calculation requires → `MISSING_MARKET_DATA` with the section and reason named.
- No silent date roll, no silent unit coercion, no silent cross-snapshot mixing: a vol section from snapshot B never resolves a query scoped to snapshot A.

---

## 7. Curve contracts

### 7.0 Engine-contract posture (what is and is not decided)

The ENGINE CONTRACT for curves is defined here as SHAPE. No production curve methodology VALUE is chosen:

- Curve identity, currency, role, discount-vs-forward distinction, index association, valuation/reference dates, pillar representation, value-type / quote-type tagging, compounding / rate representation needed to interpret stored values, interpolation / extrapolation metadata needed for deterministic replay, source / provenance, schema version, and construction methodology / version identifiers are all REQUIRED FIELDS.
- The production VALUES for construction methodology (RED-01: already-resolved curve vs bootstrap-from-instruments), bootstrap instrument set, interpolation scheme, extrapolation scheme, day count, compounding rule, and desk methodology are UNRESOLVED — RED unless live repo evidence says otherwise. Where deterministic replay requires a field whose value is not yet approved, the field is defined and the value is marked `UNRESOLVED — RED-01` (or the owning RED).
- No naked array of doubles. Every pillar array carries its date coordinate, value meaning, and unit.

### 7.1 CurveSet

`CurveSet` is the versioned container for the one-or-more resolved curves a valuation consumes. The NUMBER of curves, their roles, and their relationships are explicit data, never inferred from variable names.

```yaml
curve_set:
  schema_version: CURVE_SET_V1
  curve_set_id: <string>                    # content digest + disambiguator, same rule as §6.3
  valuation_date: <ISO date>                # must equal MarketSnapshot.valuation_date; mismatch fails closed
  base_currency: <Currency enum>            # e.g. USD; vocabulary from products/enums.py, value is data
  curves:
    - <DiscountCurve §7.2>                  # 1..n, each role-tagged
    - <ForwardCurve §7.3>                   # 0..n, each role-tagged + index-associated
  construction:
    construction_methodology_id: UNRESOLVED  # RED-01: e.g. RESOLVED_CURVE_SUPPLY vs BOOTSTRAP_FROM_INSTRUMENTS; field defined, value open
    construction_methodology_version: UNRESOLVED  # RED-01: version of the above once approved
    construction_inputs_ref: <id | NULL>    # Path-B instrument inputs ref, only where Path B approved; else NULL_WITH_REASON
    evidence_refs: <list>                   # E-citations once RED-01 locks; empty until then
  provenance: { source, adapter_versions, upstream_ids, captured_at }
  content_fingerprint: <hex>
```

Rules:

- Every curve carries its own `curve_id`, `curve_role` (§7.1.1), and `currency`. Role is never implied by variable name or array position.
- `valuation_date` on `CurveSet` and on each curve must all agree with `MarketSnapshot.valuation_date`; any disagreement fails closed.
- Single-curve vs multi-curve usage is a per-valuation fact recorded in result replay metadata (§12), not a `CurveSet` invariant: a `CurveSet` may hold one discount curve used for both discounting and forecasting (legacy reference behavior) or separate curves, but the kernel records which curve served which role for each calculation.

#### 7.1.1 Curve roles (vocabulary, not methodology)

`curve_role` is a controlled enum defined here as VOCABULARY. Selecting which role a production valuation uses for which purpose is data per valuation, not methodology — but inventing a new role requires its own methodology review.

```yaml
curve_role: DISCOUNT | FORECAST | DISCOUNT_AND_FORECAST_REFERENCE_ONLY
```

- `DISCOUNT` — this curve discounts cashflows.
- `FORECAST` — this curve forecasts floating rates (with `index_id` in §7.3).
- `DISCOUNT_AND_FORECAST_REFERENCE_ONLY` — legacy single-curve reference shape (`irs_engine` behavior). Exists so the reference regression anchor can be expressed; it is NOT a production V1 role and any production use requires explicit RED-01 approval recorded as methodology version, never silent reuse.

### 7.2 DiscountCurve

```yaml
discount_curve:
  schema_version: DISCOUNT_CURVE_V1
  curve_id: <string>                        # stable id: currency + role + construction methodology version + fingerprint
  currency: <Currency enum>                 # e.g. USD
  curve_role: DISCOUNT | DISCOUNT_AND_FORECAST_REFERENCE_ONLY
  valuation_date: <ISO date>                # reference date for T computations; explicit, never system date
  reference_date: <ISO date>                # curve anchor date; normally == valuation_date; any difference is explicit data with reason
  pillars:
    - pillar_date: <ISO date>               # canonical coordinate: calendar date, never a bare year fraction
      maturity_label: <string | NULL>       # original tenor label (e.g. "6M") where applicable; NULL_WITH_REASON otherwise; never used as the coordinate
      value: <double>                       # the stored number; meaningless without value_type + unit below
      value_type: <enum>                    # DISCOUNT_FACTOR | ZERO_RATE_CONTINUOUS | ZERO_RATE_SIMPLE | PAR_RATE_DISPLAY_ONLY_NEVER_PRICE — see §7.4
      value_unit: <unit enum>               # RATIO for DF; DECIMAL_ANNUAL for rates; see §7.4
  rate_representation:                      # required to interpret stored values; VALUES unresolved where methodology
    compounding: UNRESOLVED                 # RED-01/RED-02-adjacent: CONTINUOUS | SIMPLE | ANNUAL | ... ; field defined, value open
    day_count: UNRESOLVED                   # RED: day-count basis for rate pillars; field defined, value open
    accrual_boundary: UNRESOLVED            # RED-02-adjacent: which boundaries the rate applies to; field defined, value open
  interpolation:
    method_id: UNRESOLVED                   # RED: e.g. LINEAR_IN_ZERO | ... ; field defined, value open
    method_version: UNRESOLVED              # RED: version of the interpolation contract once approved
    parameters: <map | NULL>                # method params, typed with units; NULL only if the approved method takes none
  extrapolation:
    method_id: UNRESOLVED                   # RED: field defined, value open; FAIL_CLOSED must be representable
    method_version: UNRESOLVED
    parameters: <map | NULL>
  source_provenance:
    source: <enum>                          # BLOOMBERG_DAPI | SCREEN_TRANSCRIPTION | SYNTHETIC_FIXTURE | RESEARCH_ADAPTER
    quote_timestamps: <per-pillar | NULL>   # quote time where known; NULL_WITH_REASON where unknown, never inferred
    adapter_name: <string>
    adapter_version: <string>
    upstream_ids: <list>
  construction_methodology_id: UNRESOLVED    # RED-01: which approved construction produced these pillars
  construction_methodology_version: UNRESOLVED
  schema_version_ref: DISCOUNT_CURVE_V1
  content_fingerprint: <hex>
```

Constraints:

- `pillars` is non-empty, sorted strictly ascending by `pillar_date`, duplicate dates refused.
- `value_type` + `value_unit` are mandatory per pillar set (one pair per curve, not per pillar, unless the curve genuinely mixes types — mixing requires explicit methodology approval and per-pillar tagging; unapproved mixing fails closed).
- A curve whose `value_type` is `PAR_RATE_DISPLAY_ONLY_NEVER_PRICE` (the `USOSFR*` adapter output) is display-only by contract and can never be consumed as a pricing input; the kernel refuses it as `INVALID_PRODUCT` / `MISSING_MARKET_DATA` with the reason named.
- Continuous (`exp(-r·T)`) vs simple (`1/(1+r·T)`) semantics are never unified silently (`docs/31 §2.4` compounding note preserved): the `compounding` + `value_type` pair decides the formula, and an `UNRESOLVED` pair fails closed.

### 7.3 ForwardCurve

`ForwardCurve` inherits every `DiscountCurve` field plus index association. A forward curve without an index is malformed.

```yaml
forward_curve:
  <<: *discount_curve_fields
  schema_version: FORWARD_CURVE_V1
  curve_role: FORECAST
  index_id: <FloatingIndex enum>            # e.g. USD_SOFR; vocabulary from products/enums.py, value is data
  index_tenor: <string | NULL>             # e.g. "OVERNIGHT" vs "3M"; NULL only where the index has no tenor dimension, with reason
  fixing_calendar_ref: <id | NULL>         # calendar governing observation/fixing dates where approved; NULL_WITH_REASON until RED-02 locks
  observation_rules_ref: <id | NULL>       # link to approved observation mechanics once #224 D5 locks; NULL_WITH_REASON until then
```

Rules:

- The kernel never forecasts a `USD_SOFR` coupon from a curve whose `index_id` is `USD_SOFR_TERM_3M` or vice versa; index mismatch fails closed (`INVALID_PRODUCT` with both ids named).
- Discount-vs-forward role confusion fails closed: a `DISCOUNT`-role curve is never used to forecast, a `FORECAST`-role curve is never used to discount, unless the single `DISCOUNT_AND_FORECAST_REFERENCE_ONLY` reference shape is explicitly invoked with its methodology version recorded.

### 7.4 Value types and units (vocabulary)

`pillar_value_type` enum (VOCABULARY defined here; production selection per curve is data, subject to RED-01 approval):

| Value | Meaning | Unit field | Notes |
|---|---|---|---|
| `DISCOUNT_FACTOR` | DF to payment/anchor date | `RATIO` (unitless, e.g. 0.98) | Never divided by 100; cf. `S0490D` loader rule |
| `ZERO_RATE_CONTINUOUS` | Continuously compounded zero | `DECIMAL_ANNUAL` (4.25% → 0.0425) | Formula `exp(-r·T)` only with matching `compounding=CONTINUOUS` |
| `ZERO_RATE_SIMPLE` | Simply compounded zero | `DECIMAL_ANNUAL` | Formula `1/(1+r·T)` only with matching `compounding=SIMPLE` |
| `PAR_RATE_DISPLAY_ONLY_NEVER_PRICE` | Par-rate quote for display | `PERCENT_RAW_UNCONVERTED` with `never_price=true` | Adapter output only; kernel refuses as pricing input |

- Percent vs decimal ambiguity is closed structurally: the canonical Rates boundary accepts decimal annual rates (`DECIMAL_ANNUAL`) and unitless ratios (`RATIO`) only. Upstream layers normalize before the boundary (cf. #224 `rate_units: DECIMAL_ANNUAL`). Untagged percent / basis-point numerics are refused.
- The `D`-suffix DF vs `Z`-suffix zero distinction from the #490 loader (`D` never `/100`, `Z/100`) is preserved through adapters: the adapter records which source column produced each pillar in `upstream_ids` + per-pillar `source_column` where applicable.

### 7.5 Interpolation / extrapolation metadata (shape, not values)

- Every curve carries `interpolation.{method_id, method_version, parameters}` and `extrapolation.{method_id, method_version, parameters}`. These fields are REQUIRED for deterministic replay even though their VALUES are UNRESOLVED — RED.
- `FAIL_CLOSED` (refuse out-of-range, cf. `VCUB_EXTRAPOLATION_MODE=FAIL_CLOSED` precedent) must be representable as an extrapolation method and is the only extrapolation posture this document pre-approves as a FALLBACK CONTRACT SHAPE — not as a production curve choice. Any other extrapolation value (flat, linear extension, nearest-node) requires RED approval.
- Interpolation over an unreadable / unresolved pillar is forbidden: unresolved pillars are carried as `NULL_WITH_REASON` coordinates that block any bracket reaching across them (cf. `vcub_normal_vol_resolver` unresolved-column block), never interpolated over.

### 7.6 Construction methodology identifiers (shape, not values)

- `construction_methodology_id` + `construction_methodology_version` name the approved rulebook that produced the pillars (Path A supply rule vs Path B bootstrap rule). Both are REQUIRED FIELDS with UNRESOLVED values (RED-01).
- Lifecycle mirrors #224 §2.5: methodology versions are `PROPOSED → LOCKED`; `LOCKED` requires cited workstation evidence plus Sophira acceptance. Unknown methodology ids fail closed.

---

## 8. FixingStore

### 8.1 Mandatory rule

**A required historical fixing must NEVER silently become a forecast.** History and forecast are different facts with different identities, and the contract makes confusing them structurally impossible.

### 8.2 Contract shape

```yaml
fixing_store:
  schema_version: FIXING_STORE_V1
  fixing_store_id: <string>                 # content digest + disambiguator, same rule as §6.3
  valuation_date: <ISO date>                # must equal MarketSnapshot.valuation_date
  entries:
    - index_id: <FloatingIndex enum>        # e.g. USD_SOFR; vocabulary, value is data
      fixing_date: <ISO date>               # observation / publication date for this fixing
      observation_state: <enum>             # HISTORICAL | FORECAST_REQUIRED | PROJECTED | MISSING — see §8.3
      value: <double | NULL>                # decimal rate (DECIMAL_ANNUAL basis) where known; NULL per §8.3 rules
      value_unit: DECIMAL_ANNUAL            # explicit on every entry; no untagged numerics
      day_count_basis: <enum | UNRESOLVED>  # basis the fixing value is quoted on where applicable; UNRESOLVED — RED until evidenced
      source: <enum>                        # BLOOMBERG_DAPI | SCREEN_TRANSCRIPTION | SYNTHETIC_FIXTURE | CURVE_PROJECTION | NULL_WITH_REASON
      quote_timestamp: <ISO-8601 | NULL>    # where known; NULL_WITH_REASON where unknown, never inferred
      version: <string>                     # source version / batch id for this fixing
  provenance: { assembled_by, adapter_versions, upstream_ids }
  content_fingerprint: <hex>
```

### 8.3 Observation states (machine-readable, not prose)

| State | Meaning | `value` | `source` |
|---|---|---|---|
| `HISTORICAL` | Fixing published on/before valuation date and captured | Required decimal rate | Market source (`BLOOMBERG_DAPI`, `SCREEN_TRANSCRIPTION`, `SYNTHETIC_FIXTURE`) |
| `FORECAST_REQUIRED` | Fixing date after valuation date; kernel must project from curve | `NULL` (never a historical number) | `CURVE_PROJECTION` recorded at valuation time, not stored as history |
| `PROJECTED` | A previously computed forecast carried for audit only | Decimal rate + `projection_method_id/version` | `CURVE_PROJECTION` with method recorded; never mistaken for history |
| `MISSING` | Required historical fixing not held | `NULL` | `NULL_WITH_REASON` naming why (not yet published, capture gap, etc.) |

- `HISTORICAL` vs `FORECAST_REQUIRED` vs `PROJECTED` vs `MISSING` is a dedicated enum field. Prose comments never decide it.
- A `FORECAST_REQUIRED` entry never carries a `value`. A `HISTORICAL` entry never carries a null value. A `PROJECTED` value never appears where a `HISTORICAL` is required.
- Index identity (`index_id`) + `fixing_date` is the unique key. Entries for different indices on the same date are different facts.

### 8.4 Deterministic behavior (fail-closed table)

| Case | Rule |
|---|---|
| Fixing date before valuation date, entry `HISTORICAL` with value | Consume the value. |
| Fixing date before valuation date, entry `MISSING` or absent | `FAILED` with `MISSING_MARKET_DATA`, naming `index_id` + `fixing_date` + snapshot id. NEVER forecast, NEVER interpolate a fixing, NEVER carry a neighboring fixing forward. |
| Fixing date on valuation date | AMBIGUOUS BY METHODOLOGY — see §8.5. No universal rule is chosen here. The kernel behavior is selected by an explicit `same_day_rule_id/version` field (UNRESOLVED — RED, §8.5) and fails closed when that field is unresolved. |
| Fixing date after valuation date | Forecast path: entry must be `FORECAST_REQUIRED`; the kernel projects from the approved `ForwardCurve` + approved observation mechanics (#224 D5). A stored `PROJECTED` value is audit only and is recomputed, never trusted as input. |
| Future fixing with a `HISTORICAL` value stored | Malformed snapshot: refuse (`INVALID_PRODUCT` / `MISSING_MARKET_DATA` with reason `FUTURE_FIXING_STORED_AS_HISTORY`). History cannot come from the future. |
| Any fixing with unknown `observation_state` | Refuse before reading `value`. |

### 8.5 Same-day ambiguity (explicit RED, not invented)

Whether a fixing dated exactly on the valuation date is historical (already published and required) or forecast (not yet published, to be projected) depends on publication timing, time zone, and desk convention that this repository has not evidenced. This document does NOT invent a same-day rule. The contract exposes the ambiguity:

```yaml
same_day_rule:
  rule_id: UNRESOLVED                        # RED: e.g. PUBLISHED_BEFORE_CUTOFF_IS_HISTORY vs VALUATION_DATE_IS_FORECAST; field defined, value open
  rule_version: UNRESOLVED
  cutoff_time: UNRESOLVED                    # RED: time-of-day + timezone basis once approved; field defined, value open
  timezone: UNRESOLVED                       # RED: explicit IANA timezone once approved; never assumed
```

Until the rule locks with workstation / desk evidence plus Sophira acceptance, any valuation requiring a same-day fixing fails closed with `MISSING_MARKET_DATA` + `SAME_DAY_FIXING_RULE_UNRESOLVED`.

### 8.6 Provenance

Every `HISTORICAL` entry names its source, quote timestamp where known, and source version / batch. A fixing without provenance is `MISSING` by contract, not history with an unwritten source.

---

## 9. Volatility contracts

### 9.0 Posture: a naked `double` is forbidden

No vol input is a bare number. Every vol number travels with its quote semantics: quote type, unit, shift, coordinates, ATM definition, surface/cube identity, interpolation metadata, source, timestamp, schema version, and methodology identifier. Anything less fails closed.

The existing `CanonicalVolSurface` + `resolve_vcub_normal_vol` precedent (stated unit, stated vol space, stated smile model, `FAIL_CLOSED` extrapolation, unresolved-column block, `bp=1e-4` normalization, `RESOLVER_VERSION` audit) is the discipline this section generalizes to the engine boundary. `CanonicalVolSurface` itself remains the Python capture/canonical store shape; the engine-bound `VolatilityInput` below is the versioned DTO the kernel consumes, produced via explicit adapters — never by relabeling a capture.

### 9.1 VolQuote (single-point semantics)

```yaml
vol_quote:
  schema_version: VOL_QUOTE_V1
  quote_type: NORMAL | LOGNORMAL | SHIFTED_LOGNORMAL   # vocabulary defined here; production selection per quote is data subject to RED-vol approval
  volatility_unit: <unit enum>            # DECIMAL (absolute decimal rate vol) | BASIS_POINTS (1bp=1e-4, normal space only) — explicit per quote
  volatility: <double>                   # the number; meaningless without quote_type + unit + shift below
  shift: <double | NULL>                 # displaced-diffusion shift where quote_type=SHIFTED_LOGNORMAL; NULL elsewhere
  shift_unit: <unit enum | NULL>          # DECIMAL | BASIS_POINTS; required iff shift present; shift without unit fails closed
  expiry: <coordinate>                   # option expiry; ISO date AND tenor label where both known; see §9.3
  underlying_tenor: <coordinate>          # underlying swap tenor; ISO date span AND tenor label; see §9.3
  strike: <strike coordinate>             # absolute strike (DECIMAL_ANNUAL) OR moneyness (see §9.4); one fact per field, never conflated
  atm_definition: <id | NULL>            # which ATM rule this quote's strike/moneyness is measured against; NULL_WITH_REASON where N/A
  forward_ref: <decimal | NULL>          # forward/ATM rate the moneyness is measured from, with unit; NULL where not stated, never invented
  source: <enum>                         # BLOOMBERG_VCUB | BLOOMBERG_DAPI | SYNTHETIC_FIXTURE | RESEARCH_ADAPTER
  quote_timestamp: <ISO-8601 | NULL>     # where known; NULL_WITH_REASON where unknown
  valuation_date: <ISO date>             # must equal MarketSnapshot.valuation_date
  methodology_id: UNRESOLVED             # RED-vol: production vol methodology (Black-76 / shifted-Black / Bachelier selection); field defined, value open
  methodology_version: UNRESOLVED
```

Rules:

- `quote_type` changes formula/model interpretation (Black-76 lognormal vs shifted-lognormal vs Bachelier normal). That semantic is explicit and machine-readable in `quote_type`, never inferred from magnitude, unit, or shift presence. A `LOGNORMAL` quote with zero/negative strike or forward fails closed rather than being reinterpreted as shifted.
- `SHIFTED_LOGNORMAL` without an explicit `shift` + `shift_unit` fails closed. Shift in basis points vs decimal is never guessed: `shift_unit` is mandatory. A shift of `0` with explicit unit is data (equivalent to unshifted under the stated methodology); a missing shift is not zero.
- `NORMAL` quotes in `BASIS_POINTS` normalize at `1bp=1e-4` (resolver precedent). Any other unit pairing (e.g. `LOGNORMAL` in `BASIS_POINTS`) fails closed unless a future approved methodology explicitly permits it with its conversion recorded.
- Negative normal vols fail closed (resolver `NegativeVolatilityError` precedent generalized): a negative absolute normal vol is evidence of wrong capture/spread semantics, not a low vol.
- `ATM` vs spread vs absolute distinction from `CanonicalVolSurface` (`ABSOLUTE_VOL` vs `SPREAD_TO_ATM`) is preserved through adapters: a spread is never stored or transmitted as though it were a vol. The adapter reconstructs absolute vols explicitly with the ATM vol recorded, or refuses.

### 9.2 VolSurface / VolCube containers

```yaml
volatility_input:
  schema_version: VOLATILITY_INPUT_V1
  volatility_input_id: <string>           # digest + disambiguator, same rule as §6.3
  representation: SURFACE | CUBE          # SURFACE = Expiry x Tenor (+ strike); CUBE = Expiry x Tenor x Strike with full third axis
  quote_type: <enum>                      # uniform per container; mixed-type containers require explicit methodology approval, else fail closed
  volatility_unit: <unit enum>            # uniform per container; same mixing rule
  shift_unit: <unit enum | NULL>          # required iff SHIFTED_LOGNORMAL
  expiries: <ordered coordinate list>     # §9.3
  underlying_tenors: <ordered list>       # §9.3
  strikes: <ordered list | NULL>          # §9.4; NULL only for ATM-only surfaces with reason
  quotes: <VolQuote list>                # every node carries §9.1 semantics; unresolved nodes carried as NULL_WITH_REASON, never dropped
  atm_definition_id: <id | UNRESOLVED>    # RED-vol where ATM convention matters; field defined, value open
  smile_model_id: UNRESOLVED              # RED-vol: PWL vs SABR vs ...; field defined, value open (PWL_ADDITIVE_MONEYNESS_NORMAL_V1 precedent is reference only)
  smile_model_version: UNRESOLVED
  interpolation:
    method_id: UNRESOLVED                 # RED-vol: bilinear / PWL / ... ; field defined, value open
    method_version: UNRESOLVED
    parameters: <map | NULL>
  extrapolation:
    method_id: FAIL_CLOSED | UNRESOLVED   # FAIL_CLOSED pre-approved as fallback shape; any other value RED
    method_version: UNRESOLVED
  source_provenance: { source, capture_ids, surface_ids, adapter_name/version, captured_at, confirmed_by/at }
  valuation_date: <ISO date>
  content_fingerprint: <hex>
```

- Surface vs cube is explicit data (`representation`), not a reader inference from column counts.
- The third (strike) axis, where present, uses the `StrikeDimension` vocabulary generalized: `ATM` vs `YIELD_OFFSET_BP` (existing) plus explicitly reserved-but-unresolved `ABSOLUTE_STRIKE` and `LOG_MONEYNESS` members. Reserved members are NOT approved for production use; any use requires RED-vol methodology approval. No other strike convention is added for screens this repository has not observed.
- Unresolved nodes block any bracket reaching across them (resolver precedent). Interpolating over an unreadable column and reporting no fallback is forbidden.

### 9.3 Expiry / tenor coordinates

Each axis entry carries BOTH a human label and a machine coordinate, because screen labels (`"18Mo"`, `"10Yr"`) are text and turning them into year fractions is methodology (`DCF_VCUB` unresolved):

```yaml
axis_entry:
  label: <string>                         # e.g. "18Mo" / "10Y"; verbatim transcription
  coordinate: <double>                    # numeric axis position supplied by explicit caller map (VCUBGridCoordinates precedent)
  coordinate_unit: YEARS_FRACTION         # explicit; no other unit permitted without methodology approval
  coordinate_method_id: UNRESOLVED        # RED-vol: which day-count/axis rule produced the coordinate; field defined, value open
  coordinate_method_version: UNRESOLVED
```

- Coverage must be complete: a label without a coordinate fails closed (partial maps silently drop bracketing nodes).
- Duplicate coordinates across labels fail closed (bracketing ambiguous).
- Out-of-range queries fail closed (`FAIL_CLOSED`); Bloomberg's own flat extrapolation is deliberately not mirrored.

### 9.4 Strike / moneyness coordinates

One economic fact per field. The contract separates:

```yaml
strike_coordinate:
  strike_dimension: ATM | YIELD_OFFSET_BP | ABSOLUTE_STRIKE_RESERVED | LOG_MONEYNESS_RESERVED
  absolute_strike: <decimal | NULL>       # DECIMAL_ANNUAL; present iff dimension is absolute
  yield_offset_bp: <double | NULL>        # additive K-F in bp; present iff YIELD_OFFSET_BP
  log_moneyness: <double | NULL>          # reserved; NULL until approved methodology defines it
  moneyness_unit: BASIS_POINTS | DECIMAL | NULL  # explicit where applicable
```

- `YIELD_OFFSET_BP` is the VCUB-observed additive coordinate (`mu* = K* - F*`, `K_ij = F_ij + mu*`). Its unit is basis points by screen statement, transcribed never inferred.
- Absolute vs offset vs log-moneyness confusion fails closed. A `0bp` offset is not the ATM point (resolver precedent: ATM carries no offset).
- The forward each moneyness is measured from (`forward_ref` in §9.1) is stated per quote or per corner where applicable, never assumed from the query's forward.

### 9.5 Source / timestamp / version

Every `VolatilityInput` names: `source`, per-quote `quote_timestamp` where known, `valuation_date`, `volatility_input_id`, `schema_version`, `methodology_id/version`, `smile_model_id/version`, interpolation / extrapolation versions, and the full upstream chain (`surface_id(s)`, `capture_id(s)`, adapter name/version, confirmer identity). A vol number without this chain is not an engine input.

### 9.6 Model / calibration inputs where architecture requires them

Vol inputs that are MODEL PARAMETERS rather than market quotes (e.g. Hull-White calibration outputs consumed by a later pricing call) cross the boundary as `ModelInput`, not as `VolQuote`:

```yaml
model_input:
  schema_version: MODEL_INPUT_V1
  model_input_id: <string>
  model_id: <enum>                        # e.g. HULL_WHITE_1F | BLACK_76 | BACHELIER — vocabulary, selection is RED-model data
  model_version: <string>                 # version of the model contract once approved; UNRESOLVED until then
  parameters:                             # one fact per parameter, each with unit
    - name: <string>                      # e.g. mean_reversion | volatility | ...
      value: <double>
      unit: <unit enum>                   # explicit; e.g. PER_YEAR | DECIMAL | ...
  calibration_ref: <calibration_result_id | NULL>  # link to ModelCalibrationResult §14 where applicable
  valuation_date: <ISO date>
  provenance: { source, methodology_id/version, upstream_ids }
```

- Model choice itself is RED-model (Phase 3+): defining the `model_id` vocabulary here does not approve any model for production. Hull-White 1F is the #222 Phase-3 target, not an approval.
- Calibration objective, optimizer, tolerance, and convergence semantics are owned by §14. A `ModelInput` without a calibration link where the model requires calibration fails closed.

---

## 10. Exercise representation

### 10.1 Posture

Typed exercise representation suitable for later European swaptions AND future Bermudan / callable products. The architecture distinguishes exercise facts without guessing, with the minimum structure that prevents known #222 products from requiring a fundamental rewrite. No exotic features are overdesigned.

### 10.2 Contract shape

```yaml
exercise_terms:
  schema_version: EXERCISE_TERMS_V1
  exercise_style: EUROPEAN | BERMUDAN     # vocabulary; EUROPEAN usable now, BERMUDAN shape reserved for callable work (Phases 3+)
  exercise_dates: <ISO date list>         # EUROPEAN: exactly 1; BERMUDAN: 1..n sorted ascending, duplicate-free
  notice_dates: <ISO date list | NULL>    # present only where the contract architecture requires notice semantics; NULL_WITH_REASON otherwise
  underlying_reference:
    resolved_swap_ref: <resolved_swap_id> # the underlying swap being entered / cancelled
    underlying_start_rule: <enum>         # e.g. EXERCISE_DATE_IS_UNDERLYING_START vs UNDERLYING_START_PER_SCHEDULE; VALUES UNRESOLVED — RED where methodology-dependent
    underlying_start_rule_version: UNRESOLVED
  calendar_ref: <id | NULL>               # holiday calendar governing exercise-date adjustment where genuinely required; NULL_WITH_REASON otherwise
  business_day_convention: <enum | NULL>  # where genuinely required; NULL_WITH_REASON otherwise
  timezone: <IANA string | NULL>           # only where exercise timing genuinely requires it (e.g. cross-region cutoffs); NULL_WITH_REASON otherwise
  expiry_time: <time+timezone | NULL>     # exercise cutoff time where the product requires it; NULL_WITH_REASON otherwise
```

Rules:

- `EUROPEAN` with anything other than exactly one `exercise_date` fails closed. `BERMUDAN` with an empty or unsorted date list fails closed.
- `notice_dates`, `timezone`, `expiry_time`, `calendar_ref` are present ONLY where the product genuinely requires them. Inventing notice semantics for a product that has none is forbidden; omitting them where the product requires them fails closed.
- The underlying effective/start relationship (`exercise_date` vs underlying swap start) is explicit data (`underlying_start_rule`), never inferred from date equality. Its VALUES are UNRESOLVED — RED where desk/workstation evidence is required.
- American exercise is deliberately NOT in the vocabulary: no #222 product requires it, and adding it would be speculative future-proofing. If a later product needs it, the vocabulary extends under its own methodology review.
- Cash vs physical outcome of exercise is NOT an exercise fact — it lives in SettlementTerms (§11).

---

## 11. Settlement representation

### 11.1 Posture

Settlement is explicit. Distinct concepts are never collapsed into one free-form string. Production values are not invented: where market/desk evidence is required, the field is defined and the value left `UNRESOLVED — RED`.

### 11.2 Contract shape

```yaml
settlement_terms:
  schema_version: SETTLEMENT_TERMS_V1
  settlement_type: CASH | PHYSICAL        # what is delivered: cash amount vs underlying swap
  settlement_method: <enum | UNRESOLVED>  # e.g. COLLATERALIZED_CASH_PRICE vs ... ; field defined, value open — RED-settlement
  settlement_method_version: UNRESOLVED
  cash_settlement_methodology:            # required iff settlement_type=CASH; forbidden otherwise
    methodology_id: UNRESOLVED            # RED-settlement: e.g. ISDA cash-settlement rule, CCP rule, desk rule; field defined, value open
    methodology_version: UNRESOLVED
    settlement_rate_source: UNRESOLVED    # RED: which rate/curve sources the cash amount; field defined, value open
    settlement_date_rule: UNRESOLVED      # RED: timing rule (e.g. T+2 from exercise); field defined, value open
  settlement_date: <ISO date | NULL>      # explicit date where known / required; NULL_WITH_REASON where rule-derived and not yet resolved
  settlement_currency: <Currency enum>    # explicit; never assumed equal to trade currency without a stated rule
  source_methodology_provenance: { source, evidence_refs, methodology_id/version }
```

Rules:

- `settlement_type` (CASH vs PHYSICAL — WHAT is delivered) and `settlement_method` (HOW the cash amount is determined — WHICH rulebook) are separate fields. Collapsing them into one string fails schema validation.
- `CASH` without a cash-settlement methodology fails closed. `PHYSICAL` with a cash-settlement methodology fails closed.
- Settlement date / timing rules (`settlement_date_rule`, `settlement_date`) are explicit. A settlement date derived from an unresolved timing rule is `NULL_WITH_REASON`, never guessed from payment-lag precedent (#224 D6 is a swap-cashflow rule, not a swaption-settlement rule).
- No production settlement value (method, methodology, rate source, date rule) is chosen here. All are `UNRESOLVED — RED-settlement` pending Phase-2 (#232) workstation reconciliation.

---

## 12. PricingResult (Rates)

### 12.1 Role

`PricingResult` is the typed output of one deterministic Rates valuation. Downstream Python / UI must interpret it without reverse-engineering C++ internals and without knowing QuantLib internals. The legacy `pricing/result.py::PricingResult` is REFERENCE ONLY (value-type spirit, status codes, structured messages, engine provenance, diagnostics); the Rates shape below is defined independently so #227+ can implement it literally.

### 12.2 Contract shape

```yaml
rates_pricing_result:
  schema_version: RATES_PRICING_RESULT_V1
  product_id: <string>                    # trade identity; must match ResolvedSwap.product_id
  product_type: <enum>                    # e.g. VANILLA_USD_SOFR_OIS | EUROPEAN_SWAPTION — vocabulary, value is data
  valuation_date: <ISO date>              # the date valued; must equal MarketSnapshot.valuation_date
  valuation_context_id: <string>          # identity of the valuation request (date + reporting currency + snapshot + trade + model refs)
  result_currency: <Currency enum>        # reporting currency; explicit, never assumed
  pv: <double | NULL>                     # present value in result_currency; NULL only on FAILED
  pv_unit: <unit enum>                    # CURRENCY_AMOUNT (e.g. USD amount); explicit on every result carrying a pv
  pv_sign_convention: <enum>              # RECEIVE_MINUS_PAY_FROM_OWNER_PERSPECTIVE | ... ; explicit, never guessed — see §12.3
  component_pvs: <map | NULL>             # e.g. fixed_leg_pv / floating_leg_pv / exercise_value / intrinsic_vs_time split where the engine defines them; each with unit; NULL where the engine has no components
  price_semantics: <enum>                 # PRESENT_VALUE | PREMIUM | PAR_RATE | ANNUITY_PVBP — what the headline number IS
  annuity_pvbp: <double | NULL>           # where computed (swaps/swaptions); with unit CURRENCY_AMOUNT_PER_BASIS_POINT or RATIO as applicable; NULL where N/A
  par_rate: <double | NULL>               # where computed; DECIMAL_ANNUAL; NULL where N/A
  status: SUCCESS | SUCCESS_WITH_WARNINGS | FAILED   # legacy PricingStatus spirit preserved
  warnings: <list[{code, message, detail}]>  # machine-readable codes; §16
  errors: <list[{code, message, detail}]>    # machine-readable codes; §16; FAILED carries >=1
  engine:
    engine_name: <string>                 # e.g. shiori_rates_engine
    engine_version: <string>              # pinned version; part of replay identity
    method: <string>                      # deterministic method token (e.g. RESOLVED_CURVE_DISCOUNT_FORECAST_V1 once approved)
  inputs_identity:
    market_snapshot_id: <string>          # exact MarketSnapshot consumed
    curve_set_id: <string>                # exact CurveSet consumed
    curve_role_map: <map>                 # which curve_id served DISCOUNT vs FORECAST for this calculation
    fixing_store_id: <string>             # exact FixingStore consumed
    volatility_input_id: <string | NULL>  # exact vol input where applicable
    convention_set_id: <string>           # from ResolvedSwap provenance (#224)
    resolved_swap_schema_version: <string># RESOLVED_SWAP_V1 etc.
    model_input_id: <string | NULL>       # exact ModelInput where applicable
    calibration_result_id: <string | NULL>
  assumptions: <map>                      # every material assumption the engine made, as typed data (e.g. calendar_applied=false); never prose-only
  diagnostics: <map>                      # leg PVs, period counts, weights, fallbacks-not-taken; small typed values, never a narrative
  replay:
    content_fingerprint: <hex>            # digest of canonical result + inputs identity
    inputs_fingerprint: <hex>             # digest of canonical inputs (trade + convention + market + model)
    tolerance: <string>                   # documented numeric tolerance for bit-for-bit comparison (e.g. ABS_1E_9)
```

### 12.3 Sign, unit, and price-semantics rules

- Every monetary value carries its currency (`result_currency`) and its unit (`pv_unit`). A PV without a currency is malformed.
- Every PV carries an explicit `pv_sign_convention`. The legacy reference engine's `RECEIVE-positive / PAY-negative from owner perspective` is REFERENCE ONLY term behavior, not a Rates decision: the Rates contract requires the convention to be STATED per result, so a reader never infers sign from engine folklore.
- `price_semantics` states what the headline number is. A swaption premium is not a swap NPV is not a par rate is not an annuity: the UI must branch on this field, never on magnitude or product-type inference.
- Component outputs (`component_pvs`) each carry their own unit and sign convention reference. Components that do not sum to the headline (e.g. intrinsic/time splits) say so in `assumptions`.

### 12.4 Linkage rules

- A result names the exact `market_snapshot_id`, `curve_set_id` (+ role map), `fixing_store_id`, `volatility_input_id` (where applicable), `convention_set_id`, `resolved_swap_schema_version`, and `model_input_id` / `calibration_result_id` (where applicable) it was computed from. A result that cannot name its inputs is not replayable and fails validation.
- `market_data_as_of` (legacy field spirit) is preserved as `valuation_date` + `market_snapshot_id`: the date alone is not identity; the snapshot id is.

---

## 13. RiskResult (Rates)

### 13.1 Role

`RiskResult` carries Greeks / bump sensitivities and any bucketed risk for one valuation. It is separate from `PricingResult` so risk methodology (bump sizes, bucketing, revaluation rules) never leaks into price semantics.

### 13.2 Contract shape

```yaml
rates_risk_result:
  schema_version: RATES_RISK_RESULT_V1
  product_id: <string>
  valuation_date: <ISO date>
  valuation_context_id: <string>
  result_currency: <Currency enum>
  measures:
    - measure_id: <enum>                  # e.g. DV01 | PV01 | DELTA | GAMMA | VEGA | ... — vocabulary defined here, selection per result is data
      value: <double>
      unit: <unit enum>                   # e.g. CURRENCY_AMOUNT_PER_BASIS_POINT for DV01; DECIMAL_SENSITIVITY where applicable; explicit per measure
      bump_spec:                          # methodology that produced this measure; required per measure
        bump_type: <enum>                 # PARALLEL_BP | BUCKETED_TENOR | VOL_POINT | MODEL_PARAM — vocabulary here
        bump_size: <double>
        bump_unit: <unit enum>            # BASIS_POINTS | DECIMAL | ... ; explicit
        revaluation_rule_id: UNRESOLVED   # RED-risk: full revaluation vs analytic; field defined, value open
        revaluation_rule_version: UNRESOLVED
      bucket_coordinate: <coordinate | NULL>  # pillar date / expiry-tenor-strike where bucketed; NULL for parallel measures with reason
      market_snapshot_id: <string>        # snapshot bumped
      model_version: <string | NULL>      # engine/model version used for revaluation
  inputs_identity: { market_snapshot_id, curve_set_id, fixing_store_id, volatility_input_id, convention_set_id, model_input_id }
  status: SUCCESS | SUCCESS_WITH_WARNINGS | FAILED
  warnings / errors: <structured codes; §16>
  engine: { engine_name, engine_version, method }
  diagnostics: <map>
  replay: { content_fingerprint, inputs_fingerprint, tolerance }
```

Rules:

- Every measure names its `measure_id`, `bump_spec` (type + size + unit + revaluation rule), `unit`, and `bucket_coordinate` (or explicit NULL for non-bucketed). A sensitivity without a bump spec is not a result.
- Bump sizes and bucketing rules are per-measure data with explicit units. Production bump conventions (1bp vs 0.5bp, bucket boundaries) are UNRESOLVED — RED-risk where desk methodology is required; the fields exist so the choice is recordable, not so a default is smuggled in.
- `DV01` vs `PV01` vs delta/gamma/vega naming follows the `measure_id` vocabulary here, not QuantLib or Bloomberg naming. A measure id unknown to this vocabulary fails closed rather than being passed through as free text.

---

## 14. ModelCalibrationResult + model inputs

### 14.1 Role

`ModelCalibrationResult` records how a model (e.g. Hull-White 1F in Phase 3) was calibrated against validated instruments, so a later pricing call consuming its `ModelInput` (§9.6) is auditable. Calibration methodology itself is NOT decided here — only the contract capable of carrying it.

### 14.2 Contract shape

```yaml
model_calibration_result:
  schema_version: MODEL_CALIBRATION_RESULT_V1
  calibration_result_id: <string>         # digest + disambiguator, same rule as §6.3
  model_id: <enum>                        # HULL_WHITE_1F | BLACK_76 | BACHELIER | ... — vocabulary, selection is RED-model data
  model_version: UNRESOLVED               # RED-model: version of the approved model contract; field defined, value open
  calibrated_at: <ISO-8601 timestamp+offset>  # when calibration ran; distinct from valuation_date
  valuation_context_id: <string>          # context calibrated under
  market_snapshot_id: <string>            # exact snapshot calibrated against
  instruments:                            # calibration instruments / identifiers
    - instrument_id: <string>             # e.g. swaption quote id / VolatilityInput node id
      instrument_type: <enum>             # EUROPEAN_SWAPTION | VANILLA_SWAP | ...
      weight: <double | NULL>             # calibration weight with unit where applicable; NULL_WITH_REASON otherwise
  parameters:                             # calibrated parameters, one fact per parameter
    - name: <string>
      value: <double>
      unit: <unit enum>                   # PER_YEAR | DECIMAL | ... ; explicit
  objective:
    objective_id: UNRESOLVED              # RED-model: e.g. WEIGHTED_SQUARE_PRICE_ERROR; field defined, value open
    objective_version: UNRESOLVED
    error_value: <double | NULL>          # final objective value with unit; NULL where N/A with reason
    error_unit: <unit enum | NULL>
    per_instrument_errors: <list | NULL>  # instrument_id -> error, with units; NULL_WITH_REASON where not recorded
  convergence:
    status: CONVERGED | NOT_CONVERGED | NOT_APPLICABLE  # machine-readable; never prose
    iterations: <int | NULL>
    tolerance: <string | NULL>            # documented tolerance, e.g. ABS_1E_9
    optimizer_id: UNRESOLVED              # RED-model: optimizer identity; field defined, value open
    optimizer_version: UNRESOLVED
  source_provenance: { source, adapter_versions, upstream_ids, evidence_refs }
  status: SUCCESS | SUCCESS_WITH_WARNINGS | FAILED
  warnings / errors: <structured codes; §16>
  replay: { content_fingerprint, inputs_fingerprint, tolerance }
```

Rules:

- A calibration result without `model_id`, `market_snapshot_id`, `instruments`, `parameters` (with units), `objective` identity, and `convergence.status` is incomplete and fails validation.
- `CONVERGED=false` (or `NOT_CONVERGED`) results are still first-class records: a downstream pricing call consuming a non-converged calibration fails closed unless an explicit override policy (itself RED-model, not defined here) permits it with the override recorded.
- Objective, optimizer, tolerance, and model version VALUES are UNRESOLVED — RED-model. The fields exist so Phase-3 work can record them without redesigning the contract.

---

## 15. Versioning / provenance / replay

### 15.1 Lesson from #224 applied

Do not encode the word `RESOLVED` where the actual semantic value belongs. Every contract in §6–§14 separates:

- the ACTUAL VALUE (the rate, the date, the vol, the strike, the methodology selection);
- the RESOLUTION / EVIDENCE STATUS (which rulebook resolved it, under which version, from which evidence, with what timestamp).

Where both are needed, both are fields. Comments never carry critical semantics that disappear when parsed.

### 15.2 Schema versions

Every top-level contract carries an explicit machine-readable `schema_version`:

- `MARKET_SNAPSHOT_V1`, `CURVE_SET_V1`, `DISCOUNT_CURVE_V1`, `FORWARD_CURVE_V1`, `FIXING_STORE_V1`, `VOL_QUOTE_V1`, `VOLATILITY_INPUT_V1`, `MODEL_INPUT_V1`, `EXERCISE_TERMS_V1`, `SETTLEMENT_TERMS_V1`, `RATES_PRICING_RESULT_V1`, `RATES_RISK_RESULT_V1`, `MODEL_CALIBRATION_RESULT_V1`.
- Pattern `<CONTRACT>_V<n>`. A wire-schema revision ships under a new version string, never by mutating a published shape. Unknown versions fail closed before any other content is read.
- Methodology versions (`construction_methodology_id/version`, `methodology_id/version`, `smile_model_id/version`, `model_id/version`, `same_day_rule_id/version`, `settlement methodology`, `objective/optimizer`) evolve independently from wire-schema versions. Neither implies the other.
- Lifecycle for methodology versions is `PROPOSED → LOCKED`; `LOCKED` requires cited workstation/desk evidence plus Sophira acceptance. No migration framework beyond this rule is designed here (per #225 scope).

### 15.3 Provenance (mandatory per object)

Every market/model object names: `source` (controlled enum), `adapter_name/version` (where adapted), `upstream_ids` (loader batches, `surface_id(s)`, `capture_id(s)`, fixture ids), `captured_at` / `quote_timestamp` / `calibrated_at` (whichever apply, each with explicit offset), `evidence_refs` (E-citations where methodology locked, empty otherwise), and `content_fingerprint`.

- Source + methodology + version are never collapsed into one string. Each is its own field.
- Provenance written only in prose is not provenance. A reader that cannot determine what a number means, what units it uses, where it came from, which methodology/version produced it, which market snapshot it belongs to, and whether it is historical, forecast, calibrated, observed, or unresolved — from FIELDS alone — is reading a contract defect.

### 15.4 Deterministic replay

- Canonical serialization: every contract defines a canonical JSON form (sorted keys, fixed separators, decimal formatting pinned by the owning implementation issue — format details owned by #227, not chosen here). `content_fingerprint` is the digest of that form.
- Replay identity for a result = `inputs_fingerprint` (canonical trade + convention + market + model inputs) + `engine_version` + `method` + `tolerance`. Given identical inputs and engine version, the result must reproduce within `tolerance`.
- `NULL_WITH_REASON` is part of the fingerprint: an unresolved field resolved later is a different input, not the same input clarified.

---

## 16. Failure semantics / fail-closed rules

Status vocabulary (Rates `PricingStatus` spirit preserved from `pricing/result.py`):

- `SUCCESS` — valued, no warnings.
- `SUCCESS_WITH_WARNINGS` — valued with machine-readable `warnings` (e.g. `FORWARD_STARTING` reference precedent, `DATA_QUALITY`).
- `FAILED` — not valued; carries >=1 structured `errors` with `code + message + detail`. Domain failures are return-path results, not exceptions; contract/programming violations raise.

Error-code vocabulary (minimum; extension requires methodology review):

| Code | Meaning |
|---|---|
| `UNSUPPORTED_PRODUCT` | Product / style / role outside approved scope |
| `MISSING_MARKET_DATA` | Required market section / pillar / fixing / vol node absent or unresolved |
| `VALUATION_DATE_MISMATCH` | Valuation date disagrees with snapshot / curve / fixing / vol date |
| `MARKET_SNAPSHOT_MISMATCH` | Snapshot identity disagrees with query scope (e.g. vol from another snapshot) |
| `INVALID_PRODUCT` | Malformed trade / index mismatch / role confusion / display-only curve as pricing input |
| `MISSING_REFERENCE_DATA` | Reference term present but uninterpretable (never silently coerced) |
| `SAME_DAY_FIXING_RULE_UNRESOLVED` | Same-day fixing required but rule open (§8.5) |
| `UNKNOWN_SCHEMA_VERSION` | Unknown wire version encountered |
| `UNKNOWN_METHODOLOGY_VERSION` | Unknown methodology id/version encountered |
| `ENGINE_ERROR` | Internal engine failure (never a methodology guess) |

Global fail-closed rules (in addition to per-section rules in §6–§14):

1. Unknown schema or methodology version → refuse before reading content; never coerce across versions.
2. Missing required historical fixing → `MISSING_MARKET_DATA`; never forecast (§8).
3. Naked vol `double` (quote without `quote_type` + `unit` + coordinates + shift where required) → refuse; never infer NORMAL vs SHIFTED_LOGNORMAL.
4. Curve pillar without `value_type` + `unit` + `compounding` interpretation → refuse; never guess DF vs zero.
5. Role implied only by variable name (discount vs forward, fixed vs floating leg rule) → refuse; role must be a field.
6. Resolved methodology hidden only in comments → schema defect; comments are never inputs.
7. Status strings where actual semantic values belong → schema defect; both value and status are fields (§15.1).
8. Provenance only in prose, or source+methodology+version collapsed → schema defect.
9. Settlement type/method collapsed into one string → schema defect.
10. Expiry / tenor / strike coordinate ambiguity → refuse; coordinates require label + numeric + unit + method.
11. Result values whose units or sign conventions would need guessing → refuse; currency + unit + sign convention + price semantics required.
12. Any hidden dependence on QuantLib defaults or Bloomberg conventions → methodology violation; STOP per §1.4.
13. Extrapolation where the method is not `FAIL_CLOSED` or an approved methodology → refuse.
14. Interpolating over an unresolved pillar / vol node → refuse.
15. Consuming a non-converged calibration without an explicit (RED-approved) override → refuse.

---

## 17. RED decisions / evidence still required

Carried open REDs (not closed by this document):

- **RED-01 — Curve authority for first production UAT** (owner: Sophira/Eddy). Whether the first C++ vanilla-swap UAT consumes an already-resolved curve or bootstraps from SOFR instruments. This document defines the ENGINE CONTRACT fields for either outcome (`construction_methodology_id/version`, `construction_inputs_ref`, pillar + interpolation/extrapolation metadata) but chooses NEITHER value. Working recommendation (consume already-resolved first) remains a recommendation only.
- **RED-02 — USD SOFR workstation convention authority** (owner: Sophira/Eddy). All #224 D1–D13 values plus E1–E6 evidence. Untouched by this document.

New unresolved methodology VALUES defined-as-fields-but-not-chosen here (each `UNRESOLVED — RED`, owner Sophira/Eddy unless noted):

- **RED-225-C1 — Curve construction methodology + version** (§7.1/§7.6). Evidence: RED-01 lock + construction inputs capture.
- **RED-225-C2 — Curve interpolation method + version and parameters** (§7.2/§7.5). Evidence: workstation / desk interpolation authority + versioned method contract.
- **RED-225-C3 — Curve extrapolation method + version** (§7.5). Only `FAIL_CLOSED` pre-approved as fallback shape; any production extrapolation needs evidence.
- **RED-225-C4 — Curve compounding / day-count / accrual-boundary values** (§7.2/§7.4). Evidence: desk/workstation rate-basis authority. Note BLI `exp(-r·T)` vs reference-engine `1/(1+r·T)` are both REFERENCE ONLY and must not be unified silently.
- **RED-225-F1 — Fixing same-day rule + cutoff + timezone** (§8.5). Evidence: publication-timing / desk ruling.
- **RED-225-F2 — Fixing day-count basis values** (§8.2). Evidence: index publication authority.
- **RED-225-V1 — Production swaption vol methodology** (§9.1: Black-76 / shifted-Black / Bachelier selection; §9.2: smile model PWL vs SABR; §9.3: axis-coordinate method; interpolation method). Evidence: Bloomberg VCUB / desk vol authority (#232 scope). The contract is capable of representing the evidence later without redesign.
- **RED-225-V2 — ATM definition / strike-convention values** (§9.1/§9.4). Evidence: workstation strike-convention capture.
- **RED-225-E1 — Underlying start rule values** (§10.2). Evidence: desk/workstation exercise authority where methodology-dependent.
- **RED-225-S1 — Settlement method + cash-settlement methodology + rate source + date rule** (§11.2). Evidence: Phase-2 (#232) settlement reconciliation.
- **RED-225-M1 — Model choice + version** (§9.6/§14: Hull-White 1F target is direction, not approval). Evidence: Phase-3 calibration framework + workstation reconciliation.
- **RED-225-M2 — Calibration objective + optimizer + tolerance + convergence policy** (§14.2). Evidence: Phase-3 methodology approval.
- **RED-225-R1 — Risk bump sizes / bucketing / revaluation rule values** (§13.2). Evidence: desk risk methodology where required.

No stop-condition trigger occurred in writing this document: no Bloomberg/workstation evidence was created, cited, or implied beyond what the repository already records; no production value was filled.

---

## 18. Handoff to #226 / #227 / #228 / #229

- **#226 (build / QuantLib isolation / concurrency / caching / benchmark methodology).** Owns: C++20/CMake skeleton decisions, dependency pinning, QuantLib isolation interface, thread-safety contract, cache-key composition + invalidation (over the identity fields defined here), benchmark methodology, CI. Must not invent DTO field semantics belonging to #225 (this issue) or #224. Dependency recorded here: caching requires every cache-keyable field present and typed (§6.4); concurrency requires immutable snapshot semantics (§6.3) — implementation belongs to #226.
- **#227 (C++20 skeleton + versioned DTO/JSON contract + CI).** Consumes: wire-schema versions (§15.2), canonical serialization rule (§15.4, format details owned by #227), `UNKNOWN_SCHEMA_VERSION` / `UNKNOWN_METHODOLOGY_VERSION` fail-closed behavior (§16). Must refuse unknown versions before content; must implement `NULL_WITH_REASON` as fingerprint-participating (§15.4).
- **#228 (curve, fixing, calendar, resolved SOFR schedule primitives).** Consumes: `CurveSet` / `DiscountCurve` / `ForwardCurve` shapes (§7) with RED-01 values still open — implements the MECHANICS against the contract without choosing production values; `FixingStore` semantics + fail-closed table + same-day RED (§8); calendar/BDC role shape from #224 (values still RED-02). Must not silently close RED-01, RED-02, RED-225-C*, or RED-225-F*.
- **#229 (C++ vanilla USD SOFR swap kernel).** Consumes: `ResolvedSwap` (#224) + `MarketSnapshot` (§6) + `PricingResult` (§12) + `RiskResult` (§13, DV01 scope). Must record `curve_role_map`, inputs identity, assumptions, diagnostics, and replay fingerprints per §12; must implement the §16 fail-closed table literally. Must not invent curve/vol/model methodology to fill RED gaps.
- Later issues (#230 workstation reconciliation, #231–#232 swaption engine + quote/settlement reconciliation, #233–#235 Hull-White/Bermudan, #236–#238 accruals) consume §9–§11 + §14 without contract redesign, filling RED-225-V*/E*/S*/M* values with evidence at their own gates.

---

## 19. Acceptance checklist

- [ ] Document defines all 13 contract families in §1.2 with field-level shapes: MarketSnapshot (§6), CurveSet (§7.1), DiscountCurve (§7.2), ForwardCurve (§7.3), FixingStore (§8), vol quote/surface/cube (§9), exercise (§10), settlement (§11), model/calibration inputs (§9.6/§14), PricingResult (§12), RiskResult (§13), ModelCalibrationResult (§14), provenance/replay (§15).
- [ ] Relation to approved `SwapTrade -> ConventionSet -> ResolvedSwap` (#224) explicit (§5, §12.4); no competing authoritative copies.
- [ ] #223 ownership boundary preserved (§3): Python acquisition/normalization/persistence/UI vs C++ resolution/pricing; no live Bloomberg in kernel; QuantLib behind isolation, defaults never methodology.
- [ ] #224 semantics preserved: R1/R2, wire versioning, D1–D13/E1–E6 untouched; no RED-02 value filled.
- [ ] Curve ENGINE CONTRACT distinguishes identity / currency / role / discount-vs-forward / index / dates / pillars / value-type / compounding / interpolation-extrapolation / provenance / schema / methodology-version (§7); every unapproved VALUE marked `UNRESOLVED — RED`; no naked double arrays.
- [ ] FixingStore distinguishes historical / forecast-required / projected / missing with observation state, index identity, fixing date, source, version (§8.3); same-day ambiguity exposed as RED (§8.5); fail-closed table deterministic (§8.4); no silent history-to-forecast.
- [ ] Vol contract forbids naked `double` (§9.0); quote type NORMAL / LOGNORMAL / SHIFTED_LOGNORMAL explicit with unit + shift + shift-unit + coordinates + ATM + surface/cube + interpolation + source/timestamp/version/methodology (§9.1–§9.5); NORMAL vs SHIFTED_LOGNORMAL formula semantic machine-readable; no production vol methodology chosen (RED-225-V*).
- [ ] Exercise distinguishes style / dates / notice (only where required) / underlying-start rule / calendar-timezone (only where genuinely required) (§10); minimum structure for European now + Bermudan later; no exotic overdesign; unresolved values RED.
- [ ] Settlement separates type / method / physical-vs-cash / cash methodology / date-timing / source-methodology-version (§11); no collapsed strings; no invented values (RED-225-S1).
- [ ] Results carry identity / valuation context / currency / PV-price semantics / units / sign convention / components / model-market-convention linkage / diagnostics / replay (§12–§14); every field has a concrete downstream reason; no generic-library filler fields.
- [ ] Versioning/provenance: actual value vs resolution-status separated (§15.1); machine-readable schema versions everywhere (§15.2); source/methodology/version never collapsed; replay fingerprints defined (§15.4).
- [ ] Fail-closed rules enumerated (§16 + per-section rules); RED list complete with owners (§17); #226–#229 boundaries preserved (§18).
- [ ] Same-defect-family audit performed (§A below): no naked units, no role-by-name, no comment-as-data, no collapsed provenance, no history/forecast ambiguity, no settlement/vol-coordinate ambiguity, no QuantLib/Bloomberg hidden dependence, nothing #227–#229 must infer.
- [ ] Literal-implementer review performed (§B below): every remaining guess classified A (fixed), B (explicit RED), or C (later-issue handoff); no uncategorized guess remains.
- [ ] Validation: full diff vs `main` inspected; `git diff --check` clean; only intended docs file changed; no runtime/schema/build changes; stale-term grep clean.

---

## A. Same-defect-family audit (performed before push)

Searched the new contract for each defect family; outcome per family:

1. Naked numerics with ambiguous units — PASS. Every numeric field pairs with an explicit unit enum (`DECIMAL_ANNUAL`, `RATIO`, `BASIS_POINTS`, `CURRENCY_AMOUNT*`, `PER_YEAR`, `YEARS_FRACTION`) or a unit-suffixed value-type. Percent/bp untagged numerics refused (§7.4, §9.1).
2. Naked `double` vol semantics — PASS. §9.0 forbids; §9.1–§9.2 require quote-type + unit + shift-unit + coordinates + methodology per quote/container.
3. Curve arrays without DF/zero meaning — PASS. §7.2–§7.4 require per-curve `value_type` + `value_unit` + `compounding` interpretation; display-only par rates structurally barred from pricing.
4. Role implied only by variable name — PASS. `curve_role`, per-leg role maps, index ids, calendar/BDC role shape (#224) are fields; §16 refuses name-implied roles.
5. Resolved methodology hidden only in comments — PASS. Methodology ids/versions are fields (§7, §9, §14, §15); comments carry no semantics by P3.
6. Status strings where values belong — PASS. §15.1 separates actual value from resolution/evidence status; `observation_state`, `convergence.status`, `settlement_type/method` each carry values, not statuses.
7. Provenance only in prose — PASS. §15.3 requires source/adapter/upstream/timestamp/evidence/fingerprint FIELDS per object.
8. Source + methodology + version collapsed — PASS. Separate fields everywhere; §16 lists collapsing as schema defect.
9. History vs forecast ambiguity — PASS. §8.3 enum + §8.4 table + §8.5 same-day RED; future-history refused.
10. Settlement type/method ambiguity — PASS. §11.2 separates type/method/methodology/date/currency.
11. NORMAL vs SHIFTED_LOGNORMAL ambiguity — PASS. §9.1 `quote_type` machine-readable; shift/unit mandatory for shifted; cross-type reinterpretation refused.
12. Shift-units ambiguity — PASS. `shift_unit` mandatory with shift; unit-less shifts refused.
13. Expiry/tenor/strike coordinate ambiguity — PASS. §9.3–§9.4 require label + numeric + unit + method per axis; incomplete maps and duplicate coordinates refused.
14. Result units / sign conventions guessable — PASS. §12.3 requires currency + unit + sign convention + price semantics per result.
15. Hidden QuantLib dependence — PASS. No QuantLib symbol, default, or day-count appears as a value; QuantLib confined to #226 isolation per §3.
16. Hidden Bloomberg dependence — PASS. Bloomberg sources are `source` enum VALUES per snapshot/quote (data), never methodology; no Bloomberg default adopted as a value.
17. Anything #227–#229 would have to infer — addressed in §B; remaining items are explicit RED (B) or handoff (C), none uncategorized.

## B. Literal-implementer review (performed before push)

Perspective: "If #227, #228, #229 were implemented literally by an engineer forbidden to ask what I meant, what would they still have to guess?" Remaining guesses classified:

- A. Contract defect → FIXED NOW. (None remaining after audit: every defect found was fixed in-document before push — specifically: sign-convention explicitness §12.3, `NULL_WITH_REASON` fingerprint participation §15.4, mixed-type container refusal §9.2, future-history refusal §8.4, American-exercise exclusion §10.2 rationale.)
- B. Intentionally unresolved methodology → explicit RED in §17. Complete list: RED-01, RED-02 (D1–D13/E1–E6 carried), RED-225-C1/C2/C3/C4, RED-225-F1/F2, RED-225-V1/V2, RED-225-E1, RED-225-S1, RED-225-M1/M2, RED-225-R1. Each names owner (Sophira/Eddy) and evidence requirement. No silent value.
- C. Belongs to a later issue → explicit handoff in §18. Complete list: #226 (cache keys, concurrency, benchmarks, QuantLib isolation, canonical JSON formatting details), #227 (DTO serialization format, CI), #228 (schedule/fixing/calendar/curve mechanics implementation), #229 (swap kernel), #230+ (reconciliation, swaption/callable/accrual methodology fills). No implementation detail deferred without an owner.

No uncategorized guess remains.

---

## C. Validation performed (this round)

- Fetched latest `main`; verified HEAD `b7f17d08b172a4e37231a2801efea106d98b961c` (PR #241 merge) present in ancestry.
- Read live Issue #222, #223 + `docs/31`, #224 + `docs/32`, #225, and repo `AGENTS.md` before writing; did not rely on remembered copies.
- Branch `arch/225-rates-market-model-result-contracts` cut from `main`.
- One docs file added; no production/runtime/schema/build/test/tool/workflow code changed.
- `git diff --check` clean; full diff vs `main` inspected (single file).
- Grep for stale/ambiguous terms (`RESOLVED` as a value, `TODO`, naked `double vol`, `QuantLib`, `Bloomberg` as methodology, `standard practice`) reviewed: no silent methodology adoption; Bloomberg/QuantLib appear only as source vocabulary or explicit non-authority statements.
- #223 ownership boundary, #224 SwapTrade/ConventionSet/ResolvedSwap semantics, RED-01 open status, and #226–#229 issue boundaries confirmed preserved.

---

*End of Issue #225 deliverable. Architecture/schema document only; no production functionality.*
