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

### 4.1 Structured optional / unresolved values

When this document writes `ValueOrReason<T>`, the wire contract means the following typed union, not a bare JSON `null` and not prose attached to a null:

```yaml
value_or_reason:
  state: PRESENT | NULL_WITH_REASON
  value: <T | null>                       # required iff state=PRESENT
  reason:                                # required iff state=NULL_WITH_REASON
    category: NOT_APPLICABLE | UNAVAILABLE | UNRESOLVED_METHODOLOGY | MISSING_MARKET_DATA | FAILED_CAPTURE
    code: <string>                        # stable machine-readable reason code
    detail: <string | null>               # optional human detail; never the only semantic carrier
```

Rules:

- `PRESENT` requires a value and forbids a reason.
- `NULL_WITH_REASON` requires `value=null` plus the structured reason; a bare `null` is not equivalent.
- Existing shorthand such as `<T | NULL_WITH_REASON>` means this exact union. A field written only as `<T | NULL>` is a literal nullable field and MUST NOT claim a structured reason in prose.
- `NumericWithUnit` means `{ value: <double>, unit: <unit enum> }`. Economically meaningful numerics whose interpretation can vary by unit use an explicit unit field or this structure; comments and field names are not unit metadata.
- These are serialization primitives only. They do not resolve any RED methodology value and are not a general error framework.

---

## 5. Canonical object graph

```
MarketSnapshot (§6)
 ├── snapshot_id / valuation_date / schema_version / provenance
 ├── CurveSet (§7)
 │     ├── DiscountCurve (§7.2)  [1..n, role-tagged]
 │     └── ForwardCurve  (§7.3)  [0..n, role-tagged, index-associated]
 ├── FixingStore (§8, incl. embedded same_day_rule — RED-225-F1, §8.2/§8.5)
 ├── VolatilityInput (§9)   -- quote-typed vol surface / cube / single quote
 └── content_fingerprint (§15.4, non-recursive preimage)

RatesKernelInput (§5.1)
 ├── ResolvedSwap (#224)                    -- resolved trade input
 ├── MarketSnapshot (§6)                    -- resolved market input only
 ├── CurveSelection                         -- explicit discount/forecast curve ids
 ├── ExerciseTerms (§10)                    -- explicit product terms when applicable
 ├── SettlementTerms (§11)                  -- explicit product terms when applicable
 ├── ModelInput (§9.6 / §14.1)              -- explicit model input when applicable
 └── ValuationContext                       -- valuation_date + reporting_currency + identity
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
- `MarketSnapshot` is the complete resolved MARKET input. It carries no trade economics, no convention rules, no resolved schedules, no exercise/settlement product terms, and no model configuration.
- Exercise, settlement, and model inputs cross the no-I/O kernel boundary explicitly through `RatesKernelInput`; they are never resolved from ids inside `MarketSnapshot` and never defaulted from market data.
- The kernel joins these typed inputs at valuation time. No object duplicates another object's authority.
- `ConventionSet` version (`convention_set_id`) is recorded on `ResolvedSwap`; market construction methodology version (where RED-01 Path B applies) is recorded on the curve objects (§7); both are echoed in result replay metadata (§12–§15) so a result names the exact rulebooks that produced its inputs.

### 5.1 No-I/O kernel invocation composition

The canonical kernel boundary receives one explicit composition object. This is a transport/ownership contract, not pricing implementation:

```yaml
rates_kernel_input:
  schema_version: RATES_KERNEL_INPUT_V1
  valuation_product:
    product_id: <string>                     # identity of the trade/position being valued
    product_type: <enum>                     # VANILLA_USD_SOFR_OIS | EUROPEAN_SWAPTION | ...; vocabulary only
    underlying_product_id: <ValueOrReason<string>> # derivative: PRESENT and equals resolved_swap.product_id; vanilla swap: NOT_APPLICABLE
  resolved_swap: <ResolvedSwap #224>          # embedded resolved swap: valued trade for vanilla swap, underlying trade for derivative
  market_snapshot: <MarketSnapshot §6>        # embedded authoritative market snapshot
  curve_selection:
    discount_curve_id: <curve_id>             # explicit selected discount-capable curve in market_snapshot.curve_set
    forecast_curve_by_index: <map<FloatingIndex, curve_id>> # required floating index -> explicit selected forecast curve
  exercise_terms: <ValueOrReason<ExerciseTerms §10>>   # PRESENT iff product requires exercise terms
  settlement_terms: <ValueOrReason<SettlementTerms §11>> # PRESENT iff product requires settlement terms
  model_input: <ValueOrReason<ModelInput §9.6>>          # PRESENT iff pricing method requires model input
  valuation_context:
    valuation_context_id: <string>            # derived identity of this valuation context; no system-clock default
    valuation_date: <ISO date>                # MUST equal market_snapshot.valuation_date
    reporting_currency: <Currency enum>       # V1 MUST equal resolved_swap.currency; no FX conversion contract exists
  content_fingerprint: <hex>                  # non-recursive preimage rule §15.4
```

Rules:

- `ResolvedSwap` and `MarketSnapshot` are always PRESENT.
- `valuation_product` is the authoritative identity of WHAT is being valued. For a vanilla swap, `valuation_product.product_id == resolved_swap.product_id` and `underlying_product_id = NULL_WITH_REASON(NOT_APPLICABLE)`. For a derivative over that swap, `valuation_product.product_id` identifies the derivative trade/position, `underlying_product_id` MUST be PRESENT and equal `resolved_swap.product_id`, and the derivative id MUST NOT be inferred from or substituted by the underlying id.
- `valuation_product.product_type` controls structural applicability (for example whether exercise/settlement terms are required); product identity and underlying identity are never collapsed.
- `curve_selection` is mandatory. The kernel MUST NOT choose the first eligible curve, infer selection from array position, or derive a preferred curve internally. `discount_curve_id` and every `forecast_curve_by_index` target must exist in the embedded `CurveSet`, have compatible `curve_role`, and (for forecasts) match the stated `index_id`; mismatch fails closed.
- V1 is single-currency at the pricing boundary because no FX input/conversion contract exists. `market_snapshot.curve_set.base_currency`, every selected discount/forecast curve's `currency`, `valuation_context.reporting_currency`, and any PRESENT `settlement_terms.settlement_currency` MUST equal `resolved_swap.currency`. A mismatch fails closed with `CURRENCY_MISMATCH`; the kernel never relabels or converts an amount and never invents FX.
- Reusing one curve id for more than one role is permitted only when that curve's explicit role/methodology contract permits it; this document does not approve a new production single-curve methodology.
- `ExerciseTerms`, `SettlementTerms`, and `ModelInput` are direct payloads when applicable; otherwise their `ValueOrReason` state must be `NULL_WITH_REASON(category=NOT_APPLICABLE)`. An id without payload is never sufficient at the kernel boundary.
- Product applicability is structural, not a market default: e.g. a vanilla swap does not gain exercise/settlement terms merely because a snapshot contains related market data.
- `valuation_context.valuation_date` must equal `market_snapshot.valuation_date`; mismatch fails closed.
- #227 owns concrete C++/JSON type implementation, not the semantics or payload locations above.

---

## 6. MarketSnapshot

### 6.1 Role

`MarketSnapshot` is the single versioned container for everything the kernel needs from the market for one valuation date. It is the Rates analogue of the legacy `MarketDataSnapshot` in spirit only — the legacy shape (`_rates_points` DataFrame, `source` string, free-form `metadata`) is REFERENCE ONLY and is not the canonical Rates contract. The Rates `MarketSnapshot` is fully typed, unit-explicit, provenance-carrying, and replay-capable.

### 6.2 Contract shape

```yaml
market_snapshot:
  schema_version: MARKET_SNAPSHOT_V1        # machine-readable wire version; unknown -> fail closed
  snapshot_id: <string>                     # immutable instance identity; see §6.3 (derived, captured_at participates once)
  valuation_date: <ISO date YYYY-MM-DD>     # the date being valued; never defaulted to system date
  captured_at: <ISO-8601 timestamp+offset>  # when this snapshot instance was assembled (distinct from valuation_date); participates once in snapshot_id preimage (§6.3)
  source: <enum>                            # e.g. BLOOMBERG_DAPI | SCREEN_TRANSCRIPTION | SYNTHETIC_FIXTURE | RESEARCH_ADAPTER; value list owned here as vocabulary, selection per snapshot is data
  source_detail: <string>                   # ticker universe / screen / adapter name; free text, never methodology
  curve_set_ref: <curve_set_id>             # cache/persistence identity index; MUST equal embedded curve_set.curve_set_id
  curve_set: <CurveSet §7>                   # REQUIRED embedded kernel payload
  fixing_store_ref: <fixing_store_id>       # cache/persistence identity index; MUST equal embedded fixing_store.fixing_store_id
  fixing_store: <FixingStore §8>             # REQUIRED embedded kernel payload
  volatility_ref: <ValueOrReason<volatility_input_id>>  # identity index; PRESENT id MUST equal embedded volatility_input id
  volatility_input: <ValueOrReason<VolatilityInput §9>> # embedded payload; NULL_WITH_REASON only when calculation does not require vol
  provenance:
    assembled_by: <string>                  # pipeline / operator identity
    adapter_versions: <map>                 # adapter name -> version for every Python adapter that contributed data
    upstream_ids: <list>                    # CanonicalVolSurface surface_id(s), loader batch ids, fixture ids consumed
    evidence_refs: <list>                   # workstation evidence citations (E-ids) where applicable; empty is data, not a claim
  content_fingerprint: <hex>                # digest of canonical preimage per §15.4 (own fingerprint field excluded); see §15
  diagnostics:
    unresolved_fields: <list>               # identity/coverage gaps carried structurally, never papered over
    warnings: <list[code+message]>          # machine-readable codes; §16
```

### 6.3 Identity rules

Owner decision (round 2, architecture/identity only — not market methodology): `MarketSnapshot.snapshot_id` identifies one immutable assembled snapshot instance.

- `snapshot_id` is derived from the canonical immutable snapshot identity preimage: the canonical serialization of the `MarketSnapshot` identity content per §15.4, EXCLUDING the `snapshot_id` and `content_fingerprint` fields themselves (which would otherwise recurse), and INCLUDING `captured_at` exactly once as an ordinary identity field alongside `valuation_date`, `source`, embedded section identities, and provenance identity fields.
- `captured_at` participates exactly once. It is not appended a second time as a separate disambiguator. A reassembly at a different `captured_at` is a DIFFERENT snapshot instance and may have a different `snapshot_id`, even if market values happen to be numerically identical. Re-serializing the SAME immutable snapshot with the SAME fields must produce the SAME identity.
- No hashing algorithm or numeric-formatting choice is made here: those implementation details remain owned by #227. This rule fixes PREIMAGE semantics only (which fields participate), not the digest function.
- `valuation_date` is required and explicit. There is no default. A snapshot is never valued under a different date: the kernel compares `valuation_date` against the valuation request and fails closed on mismatch (`VALUATION_DATE_MISMATCH`).
- `captured_at` is when this snapshot instance was assembled. It is never a substitute for a market quote timestamp: where a quote timestamp is known it lives on the quote object (§7–§9); where it is unknown the field is `NULL_WITH_REASON`, never inferred.
- No duplicate authoritative same-day fixing rule lives at `MarketSnapshot` level: the single authoritative `same_day_rule` is `FixingStore.same_day_rule` (§8.2/§8.5). `MarketSnapshot` reaches it only through the embedded `FixingStore`.

### 6.4 Embedding vs referencing

- The canonical wire form EMBEDS the `CurveSet`, `FixingStore`, and, when applicable, `VolatilityInput` payloads inside `MarketSnapshot` for replay atomicity. The corresponding `*_ref` fields are identity indexes for caching / persistence (#226), not substitutes for content at the kernel boundary.
- `curve_set_ref == curve_set.curve_set_id` and `fixing_store_ref == fixing_store.fixing_store_id` are mandatory invariants. When volatility is PRESENT, `volatility_ref.value == volatility_input.value.volatility_input_id`. Any mismatch fails closed with `MARKET_SNAPSHOT_MISMATCH`.
- A kernel call that receives only ids without the required embedded content fails closed (`MISSING_MARKET_DATA`). Volatility may be `NULL_WITH_REASON` only when the requested valuation genuinely does not require volatility; the ref and payload states must agree.
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
  curve_set_id: <string>                    # immutable CurveSet instance identity: derived from canonical CurveSet identity preimage per §15.4 (own id + fingerprint excluded; no timestamp double-counted)
  valuation_date: <ISO date>                # must equal MarketSnapshot.valuation_date; mismatch fails closed
  base_currency: <Currency enum>            # e.g. USD; vocabulary from products/enums.py, value is data
  curves:
    - <DiscountCurve §7.2>                  # 1..n, each role-tagged
    - <ForwardCurve §7.3>                   # 0..n, each role-tagged + index-associated
  construction:
    construction_methodology_id: UNRESOLVED  # RED-01: e.g. RESOLVED_CURVE_SUPPLY vs BOOTSTRAP_FROM_INSTRUMENTS; field defined, value open
    construction_methodology_version: UNRESOLVED  # RED-01: version of the above once approved
    construction_inputs_ref: <ValueOrReason<id>> # Path-B instrument inputs ref; NULL_WITH_REASON when not applicable/unapproved
    evidence_refs: <list>                   # E-citations once RED-01 locks; empty until then
  provenance: { sources, adapter_versions, upstream_ids, captured_at, evidence_refs }
  content_fingerprint: <hex>                # digest of canonical CurveSet preimage per §15.4 (own fingerprint excluded)
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
  curve_id: <string>                        # content-derived identity from the FULL canonical curve payload per §15.4, excluding only curve_id and content_fingerprint themselves
  currency: <Currency enum>                 # e.g. USD
  curve_role: DISCOUNT | DISCOUNT_AND_FORECAST_REFERENCE_ONLY
  valuation_date: <ISO date>                # valuation context date; explicit, never system date
  reference_date: <ISO date>                # curve anchor used for curve-time calculations; normally == valuation_date
  reference_date_reason: <ValueOrReason<string>> # NOT_APPLICABLE when equal; PRESENT explanation required when different
  value_type: <enum>                        # one canonical V1 interpretation for all pillars; see §7.4
  value_unit: <unit enum>                   # one canonical V1 unit for all pillars; see §7.4
  pillars:
    - pillar_date: <ISO date>               # canonical coordinate: calendar date, never a bare year fraction
      maturity_label: <ValueOrReason<string>> # original tenor label where applicable; never used as coordinate
      value_state: RESOLVED | NULL_WITH_REASON
      value: <double | NULL>                # numeric only when value_state=RESOLVED; never NaN/sentinel
      unresolved_reason: <StructuredReason | NULL> # REQUIRED iff value_state=NULL_WITH_REASON; forbidden when RESOLVED
      source_column: <ValueOrReason<string>> # exact upstream column/field that produced this pillar where applicable; structured reason otherwise
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
    quote_timestamps: <per-pillar ValueOrReason<ISO-8601>> # quote time or structured reason per pillar; never inferred
    adapter_name: <string>
    adapter_version: <string>
    upstream_ids: <list>
    evidence_refs: <list>                  # E-citations once methodology locks; empty until then
  construction_methodology_id: UNRESOLVED    # RED-01: which approved construction produced these pillars
  construction_methodology_version: UNRESOLVED
  content_fingerprint: <hex>                # digest of canonical curve preimage per §15.4 (own fingerprint excluded)
```

Constraints:

- `pillars` is non-empty, sorted strictly ascending by `pillar_date`, duplicate dates refused. An unresolved pillar remains present at its original coordinate with `value_state=NULL_WITH_REASON`, `value=null`, and a structured `unresolved_reason`; dropping it, storing NaN, zero-filling it, or inventing a neighboring value is forbidden.
- `curve_id` uses the full canonical curve payload as its identity preimage: currency, role/index fields, valuation/reference dates + reason, value semantics, all pillars, rate representation, interpolation/extrapolation ids/versions/parameters, provenance, and construction methodology all participate. No price-affecting field may be omitted from curve identity. Only `curve_id` and `content_fingerprint` are excluded to avoid self-reference.
- `reference_date == valuation_date` requires `reference_date_reason=NULL_WITH_REASON(category=NOT_APPLICABLE)`. If they differ, `reference_date_reason` MUST be PRESENT and explain the explicit anchor-date difference; differing dates without a reason fail closed. Curve-time calculations use `reference_date`, while `valuation_date` remains the valuation-context identity date.
- A resolved pillar requires `value_state=RESOLVED` and a numeric `value`, and forbids `unresolved_reason`. Its interpretation comes from the enclosing curve-level `value_type` + `value_unit`.
- `value_type` + `value_unit` are REQUIRED exactly once at curve level in V1 and apply uniformly to every pillar, including unresolved pillars. Mixed value types/units inside one V1 curve are NOT representable and fail closed; supporting a genuinely mixed curve would require a future schema version rather than ambiguous per-pillar repetition.
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
  index_tenor: <ValueOrReason<string>>     # PRESENT for tenor-dimensional indices; NULL_WITH_REASON when index has no tenor dimension
  fixing_calendar_ref: <ValueOrReason<id>> # calendar governing observation/fixing dates; NULL_WITH_REASON until RED-02 locks
  observation_rules_ref: <ValueOrReason<id>> # approved observation mechanics ref; NULL_WITH_REASON until #224 D5 locks
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
- The `D`-suffix DF vs `Z`-suffix zero distinction from the #490 loader (`D` never `/100`, `Z/100`) is preserved through adapters: each adapted pillar carries its exact `source_column`; `upstream_ids` records the enclosing source batch/object identity. Synthetic or otherwise column-free pillars use `NULL_WITH_REASON(category=NOT_APPLICABLE)`, never an invented column name.

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
  fixing_store_id: <string>                 # immutable FixingStore instance identity: derived from canonical FixingStore identity preimage per §15.4 (own id + fingerprint excluded; valuation_date + entries + same_day_rule participate)
  valuation_date: <ISO date>                # must equal MarketSnapshot.valuation_date
  same_day_rule:                            # AUTHORITATIVE same-day fixing rule location (RED-225-F1); see §8.5. No duplicate at MarketSnapshot level.
    rule_id: UNRESOLVED                      # RED-225-F1: e.g. PUBLISHED_BEFORE_CUTOFF_IS_HISTORY vs VALUATION_DATE_IS_FORECAST; field defined, value open
    rule_version: UNRESOLVED                 # RED-225-F1: version of the approved rule once locked
    cutoff_time: UNRESOLVED                  # RED-225-F1: time-of-day basis once approved; field defined, value open; no cutoff invented
    cutoff_time_unit: UNRESOLVED             # RED-225-F1: unit/basis of cutoff_time once approved; field defined, value open
    timezone: UNRESOLVED                     # RED-225-F1: explicit IANA timezone once approved; never assumed
  entries:
    - index_id: <FloatingIndex enum>        # e.g. USD_SOFR; vocabulary, value is data
      observation_date: <ISO date>          # economic/reference date of the rate used by the coupon; unique-key date
      publication_date: <ValueOrReason<ISO date>> # date the observation is scheduled/known to become available; never inferred from observation_date
      publication_timestamp: <ValueOrReason<ISO-8601 timestamp+offset>> # exact publication/availability time when known
      observation_state: <enum>             # HISTORICAL | FORECAST_REQUIRED | PROJECTED | MISSING — see §8.3
      value: <ValueOrReason<NumericWithUnit>> # PRESENT fixing value must carry DECIMAL_ANNUAL unit; null state carries structured reason
      day_count_basis: <enum | UNRESOLVED>  # basis the fixing value is quoted on where applicable; UNRESOLVED — RED until evidenced
      source: <ValueOrReason<enum>>         # PRESENT: BLOOMBERG_DAPI | SCREEN_TRANSCRIPTION | SYNTHETIC_FIXTURE | CURVE_PROJECTION; otherwise structured reason
      quote_timestamp: <ValueOrReason<ISO-8601 timestamp+offset>> # known timestamp or structured reason; never inferred
      version: <ValueOrReason<string>>      # source version / batch id or structured reason when no source exists
      projection_method_id: <ValueOrReason<string>>      # state-dependent; see §8.3
      projection_method_version: <ValueOrReason<string>> # state-dependent; see §8.3
      projection_inputs_ref: <ValueOrReason<ProjectionInputsRef>> # exact curve-set/forward-curve/observation-rule inputs used for audit projection
  provenance:
    sources: <list<enum>>                  # exact union of PRESENT entry sources; MAY be empty for forecast-only/MISSING-only store; never invent a market source
    assembled_by: <string>
    adapter_versions: <map>
    upstream_ids: <list>
    captured_at: <ISO-8601 timestamp+offset>
    evidence_refs: <list>                  # E-citations once methodology locks; empty until then
  content_fingerprint: <hex>                # digest of canonical FixingStore preimage per §15.4 (own fingerprint excluded; same_day_rule participates)

projection_inputs_ref_type:
  curve_set_id: <curve_set_id>              # MUST identify sibling MarketSnapshot.curve_set when embedded
  forward_curve_id: <curve_id>              # MUST resolve to FORECAST curve with matching entry.index_id
  observation_rules_ref: <id>               # MUST resolve through the #228-owned observation-rule registry
```

State-dependent projection-field rules (machine-readable, enforced per entry):

- `PROJECTED`: valid only as a complete audit-only forecast record. `value`, `source=CURVE_PROJECTION`, `projection_method_id`, `projection_method_version`, and `projection_inputs_ref` MUST all be PRESENT. The typed reference MUST identify the sibling `CurveSet`, a FORECAST `forward_curve_id` whose `index_id` equals the entry's `index_id`, and the exact observation-rule id resolved by the #228-owned registry; zero-match, mismatch, or opaque/external-only references fail closed. If projection methodology is unknown/unapproved, do NOT serialize the entry as `PROJECTED`; use the applicable non-projected state (normally `FORECAST_REQUIRED`).
- `HISTORICAL`: `value` must be PRESENT with unit `DECIMAL_ANNUAL`; `source` and `version` must be PRESENT; projection fields must be `NULL_WITH_REASON` (reason `NOT_APPLICABLE_HISTORICAL`). A historical entry carrying projection methodology is malformed and fails closed.
- `FORECAST_REQUIRED`: `value` is `NULL_WITH_REASON(TO_BE_PROJECTED_AT_VALUATION)`; projection fields are also structured not-yet-projected states. No projected value is stored as history — future projection is computed by the pricing/resolution path from the approved forward curve + observation mechanics, not read from this entry.
- `MISSING`: `value`, `source`, `quote_timestamp`, and `version` carry structured missing/unavailable reasons as applicable; projection fields are `NULL_WITH_REASON`. No projection methodology may convert `MISSING` into history.

### 8.3 Observation states (machine-readable, not prose)

| State | Meaning | `value` | `source` |
|---|---|---|---|
| `HISTORICAL` | Fixing published on/before valuation date and captured | PRESENT `NumericWithUnit` with `unit=DECIMAL_ANNUAL`; projection fields structured N/A | PRESENT market source (`BLOOMBERG_DAPI`, `SCREEN_TRANSCRIPTION`, `SYNTHETIC_FIXTURE`) |
| `FORECAST_REQUIRED` | Observation not yet available as history at valuation under explicit publication-availability semantics; kernel must project from curve | `NULL_WITH_REASON(TO_BE_PROJECTED_AT_VALUATION)`; no historical number | Projection/source state is not treated as observed history |
| `PROJECTED` | A previously computed forecast carried for audit only | PRESENT rate + PRESENT projection method/version + inputs ref REQUIRED | PRESENT `CURVE_PROJECTION`; never mistaken for history |
| `MISSING` | Required historical fixing not held | Structured missing reason; no numeric value | Structured source/unavailability reason naming why (not yet published, capture gap, etc.) |

- `HISTORICAL` vs `FORECAST_REQUIRED` vs `PROJECTED` vs `MISSING` is a dedicated enum field. Prose comments never decide it.
- A `FORECAST_REQUIRED` entry never carries a PRESENT numeric fixing value. A `HISTORICAL` entry requires a PRESENT fixing value. A `PROJECTED` value never appears where a `HISTORICAL` is required.
- Index identity (`index_id`) + `observation_date` is the unique economic key. Entries for different indices on the same observation date are different facts. For a forecast-path key, `FORECAST_REQUIRED` and `PROJECTED` are alternative states of that single entry, never two coexisting rows.
- `observation_date` identifies WHICH rate the coupon needs. `publication_date` / `publication_timestamp` identify WHEN that observation becomes available. They are never collapsed or inferred from one another; an observation may precede its publication date.

### 8.4 Deterministic behavior (fail-closed table)

| Case | Rule |
|---|---|
| `publication_date < valuation_date`, entry `HISTORICAL` with value | Consume the value. The observation date may be earlier than publication; publication availability, not observation-date position alone, governs historical availability. |
| `publication_date < valuation_date`, entry `MISSING` or absent | `FAILED` with `MISSING_MARKET_DATA`, naming `index_id` + `observation_date` + publication availability + snapshot id. NEVER forecast/interpolate/carry a neighboring fixing merely because the observation date is old. |
| `publication_date == valuation_date` | Same-day publication ambiguity — see §8.5. The embedded `FixingStore.same_day_rule` uses explicit publication timing/cutoff semantics; unresolved rule/timing fails closed with `SAME_DAY_FIXING_RULE_UNRESOLVED`. |
| `publication_date > valuation_date` | Forecast path: entry may be `FORECAST_REQUIRED` OR complete `PROJECTED`. In either state the kernel computes the rate from the approved `ForwardCurve` + approved observation mechanics (#224 D5). A `PROJECTED` value is audit only, ignored as authoritative input, and recomputed. |
| Publication availability required but `publication_date` is `NULL_WITH_REASON` / unknown | Fail closed; do not reinterpret `observation_date` as publication date and do not silently decide history vs forecast. |
| Entry marked `HISTORICAL` while publication availability is after the valuation context | Malformed snapshot: refuse with reason `UNPUBLISHED_FIXING_STORED_AS_HISTORY`. |
| Any fixing with unknown `observation_state` | Refuse before reading `value`. |

### 8.5 Same-day ambiguity (explicit RED, not invented)

Whether an observation whose `publication_date` is the valuation date is already available as history at the valuation moment depends on publication timing, time zone, and desk convention that this repository has not evidenced. The `observation_date` may be earlier and is NOT reused as publication timing. This document does NOT invent a same-day rule. The contract exposes the ambiguity at its single authoritative location:

- Authoritative location: `FixingStore.same_day_rule` (§8.2: `rule_id`, `rule_version`, `cutoff_time`, `cutoff_time_unit`, `timezone`). All values `UNRESOLVED — RED-225-F1`. No cutoff time, no cutoff unit, no timezone, and no same-day behavior is chosen here.
- The deterministic as-of instant for applying a LOCKED same-day rule is `MarketSnapshot.captured_at`, supplied explicitly with offset. The kernel never reads the system clock. If the approved methodology later requires a different valuation-as-of timestamp, that must become an explicit versioned field before use; it is not inferred here.
- Where `publication_timestamp` is PRESENT, it is the fact compared under the approved rule. Where only `publication_date` is PRESENT, a cutoff/timezone rule may determine availability only after RED-225-F1 is LOCKED; until then same-day use fails closed.
- No duplicate authoritative rule exists at `MarketSnapshot` level (§6.3): the snapshot reaches the rule only through its embedded `FixingStore`.
- `cutoff_time_unit` is a separate field from `cutoff_time` per P1 (one economic fact per field): the time and its basis/unit are never conflated.

Until the rule locks with workstation / desk evidence plus Sophira acceptance, any valuation requiring a same-day fixing fails closed with `MISSING_MARKET_DATA` + `SAME_DAY_FIXING_RULE_UNRESOLVED`.

### 8.6 Provenance

Every `HISTORICAL` entry names its source, quote timestamp where known, and source version / batch. A fixing without provenance is `MISSING` by contract, not history with an unwritten source. `FixingStore.provenance.sources` is the deterministic union of PRESENT entry sources and is allowed to be an empty list when all entries are forecast-only / missing; `assembled_by`, adapter versions, upstream ids, captured_at, and evidence refs still identify the store assembly. An empty source union is data, not permission to invent a source.

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
  volatility_state: RESOLVED | NULL_WITH_REASON
  volatility: <double | NULL>             # numeric only when RESOLVED; never NaN/sentinel
  volatility_unresolved_reason: <StructuredReason | NULL> # REQUIRED iff NULL_WITH_REASON
  shift: <double | NULL>                 # displaced-diffusion shift where quote_type=SHIFTED_LOGNORMAL; NULL elsewhere
  shift_unit: <unit enum | NULL>          # DECIMAL | BASIS_POINTS; required iff shift present; shift without unit fails closed
  expiry: <ExpiryAxisEntry>               # exact expiry date + label + derived coordinate where known; see §9.3
  underlying_tenor: <UnderlyingTenorAxisEntry> # exact underlying start/end + label + derived coordinate where known; see §9.3
  strike: <StrikeCoordinate>              # absolute strike / moneyness / ATM; see §9.4
  node_key: <VolNodeKey>                   # exact deterministic key derived from expiry + tenor + strike; see §9.2
  atm_definition: <ValueOrReason<id>>      # ATM rule identity or structured N/A/unresolved reason
  forward_ref: <ValueOrReason<NumericWithUnit>> # forward/ATM rate used for moneyness; value and unit travel together
  source: <enum>                         # BLOOMBERG_VCUB | BLOOMBERG_DAPI | SYNTHETIC_FIXTURE | RESEARCH_ADAPTER
  quote_timestamp: <ValueOrReason<ISO-8601 timestamp+offset>> # known timestamp or structured reason
  valuation_date: <ISO date>             # must equal MarketSnapshot.valuation_date
  methodology_id: UNRESOLVED             # RED-vol: production vol methodology (Black-76 / shifted-Black / Bachelier selection); field defined, value open
  methodology_version: UNRESOLVED
```

Rules:

- `quote_type` changes formula/model interpretation (Black-76 lognormal vs shifted-lognormal vs Bachelier normal). That semantic is explicit and machine-readable in `quote_type`, never inferred from magnitude, unit, or shift presence. A `LOGNORMAL` quote with zero/negative strike or forward fails closed rather than being reinterpreted as shifted.
- A resolved node requires `volatility_state=RESOLVED`, numeric `volatility`, and no unresolved reason. An unreadable node remains in the grid with `volatility_state=NULL_WITH_REASON`, `volatility=null`, its full coordinates/quote semantics, and a structured reason. Dropping the node, zero-filling it, using NaN, or borrowing a neighbor is forbidden.
- `SHIFTED_LOGNORMAL` without an explicit `shift` + `shift_unit` fails closed. Shift in basis points vs decimal is never guessed: `shift_unit` is mandatory. A shift of `0` with explicit unit is data (equivalent to unshifted under the stated methodology); a missing shift is not zero.
- `NORMAL` quotes in `BASIS_POINTS` normalize at `1bp=1e-4` (resolver precedent). Any other unit pairing (e.g. `LOGNORMAL` in `BASIS_POINTS`) fails closed unless a future approved methodology explicitly permits it with its conversion recorded.
- Negative normal vols fail closed (resolver `NegativeVolatilityError` precedent generalized): a negative absolute normal vol is evidence of wrong capture/spread semantics, not a low vol.
- `ATM` vs spread vs absolute distinction from `CanonicalVolSurface` (`ABSOLUTE_VOL` vs `SPREAD_TO_ATM`) is preserved through adapters: a spread is never stored or transmitted as though it were a vol. The adapter reconstructs absolute vols explicitly with the ATM vol recorded, or refuses.

### 9.2 VolSurface / VolCube containers

```yaml
volatility_input:
  schema_version: VOLATILITY_INPUT_V1
  volatility_input_id: <string>           # immutable VolatilityInput instance identity: derived from canonical vol identity preimage per §15.4 (own id + fingerprint excluded; no invented timestamp)
  representation: SURFACE | CUBE          # structural invariant defined below; token alone never changes dimensionality
  quote_type: <enum>                      # uniform per container
  volatility_unit: <unit enum>            # uniform per container
  shift_unit: <unit enum | NULL>          # required iff SHIFTED_LOGNORMAL
  methodology_id: UNRESOLVED              # authoritative container methodology — RED-225-V1
  methodology_version: UNRESOLVED
  expiries: <ordered ExpiryAxisEntry list>          # §9.3
  underlying_tenors: <ordered UnderlyingTenorAxisEntry list> # §9.3
  strikes: <ValueOrReason<ordered StrikeCoordinate list>> # SURFACE: N/A for ATM-only or PRESENT exactly 1; CUBE: PRESENT with >=2 distinct strikes
  quotes: <VolQuote list>                 # canonically ordered by node_key; duplicate node_key forbidden
  atm_definition_id: <ValueOrReason<id>>  # PRESENT iff methodology requires ATM anchor; N/A when genuinely irrelevant; unresolved only when applicable but unlocked
  smile_model_id: UNRESOLVED              # RED-vol: PWL vs SABR vs ...; field defined, value open (PWL_ADDITIVE_MONEYNESS_NORMAL_V1 precedent is reference only)
  smile_model_version: UNRESOLVED
  interpolation:
    method_id: UNRESOLVED                 # RED-vol: bilinear / PWL / ... ; field defined, value open
    method_version: UNRESOLVED
    parameters: <map | NULL>
  extrapolation:
    method_id: FAIL_CLOSED | UNRESOLVED   # FAIL_CLOSED pre-approved as fallback shape; any other value RED
    method_version: UNRESOLVED
  source_provenance: { source, capture_ids, surface_ids, adapter_name/version, captured_at, confirmed_by/at, evidence_refs }
  valuation_date: <ISO date>
  content_fingerprint: <hex>                # digest of canonical vol preimage per §15.4 (own fingerprint excluded)
```

- `representation` has a structural invariant, not just a label:
  - `SURFACE` is exactly one strike slice across Expiry × Tenor. ATM-only surface: `strikes=NULL_WITH_REASON(NOT_APPLICABLE)` and every quote uses `strike_dimension=ATM`. Non-ATM surface: `strikes=PRESENT` with exactly one StrikeCoordinate and every quote uses that coordinate.
  - `CUBE` is Expiry × Tenor × Strike with `strikes=PRESENT` containing at least two distinct StrikeCoordinates; every quoted strike must be one of them.
  - Any payload that can satisfy both or neither shape fails closed; changing only the representation token can never turn one structure into the other.
- `atm_definition_id` is PRESENT exactly when the approved container methodology requires an ATM anchor. A genuinely ATM-independent absolute-strike methodology uses `NULL_WITH_REASON(category=NOT_APPLICABLE)`; if ATM is required but its convention is not yet locked, use structured `UNRESOLVED_METHODOLOGY` and fail closed rather than fabricating an id.
- `VolatilityInput.methodology_id/version` is authoritative for the container. Every embedded `VolQuote` MUST carry matching `methodology_id/version`, `quote_type`, `volatility_unit`, applicable `shift_unit`, and an `atm_definition` ValueOrReason state/value exactly equal to container `atm_definition_id`. A quote cannot introduce a second ATM authority; any disagreement (including PRESENT vs NOT_APPLICABLE vs UNRESOLVED state) fails closed as malformed mixed semantics.
- Every `VolQuote.expiry` MUST exactly equal one entry in `VolatilityInput.expiries`, and every `VolQuote.underlying_tenor` MUST exactly equal one entry in `underlying_tenors`; its strike must satisfy the SURFACE/CUBE invariant above. Embedded quote coordinates are echoes for node self-description, not competing authorities. Mismatch fails closed.
- `VolNodeKey` is the canonical typed tuple `{expiry_coordinate, underlying_tenor_coordinate, strike}`, where the two coordinates equal the authoritative axis-entry numeric coordinates and `strike` is the canonical StrikeCoordinate object. Each `VolQuote.node_key` MUST equal the tuple derived from that quote's embedded fields. Duplicate node keys are forbidden.
- `VolatilityInput.quotes` is canonically sorted ascending by canonical serialization of `node_key`; source capture order never affects the wire payload or fingerprint.
- The third (strike) axis, where present, uses one canonical `StrikeDimension` vocabulary: `ATM | YIELD_OFFSET_BP | ABSOLUTE_STRIKE | LOG_MONEYNESS`. The existence of a vocabulary token does NOT approve its production methodology: `ABSOLUTE_STRIKE` and `LOG_MONEYNESS` remain UNRESOLVED for production use and require RED-225-V2 approval/evidence. Approval status is carried by methodology/version state, never encoded by changing the enum token name.
- Unresolved nodes block any bracket reaching across them (resolver precedent). Interpolating over an unreadable column and reporting no fallback is forbidden.

### 9.3 Expiry / tenor coordinates

Axis contracts preserve BOTH exact calendar facts (when known) and the derived numeric coordinate used for interpolation. Exact dates are never reconstructed later from a tenor label or year fraction.

```yaml
expiry_axis_entry:
  label: <string>                         # e.g. "18Mo"; verbatim/source label
  expiry_date: <ValueOrReason<ISO date>> # exact option expiry date when source/resolved instrument provides it
  coordinate: <double>                    # derived interpolation coordinate
  coordinate_unit: YEARS_FRACTION
  coordinate_method_id: UNRESOLVED        # RED-vol: rule producing the coordinate
  coordinate_method_version: UNRESOLVED

underlying_tenor_axis_entry:
  label: <string>                         # e.g. "10Y"; verbatim/source label
  underlying_start_date: <ValueOrReason<ISO date>> # exact start date when known
  underlying_end_date: <ValueOrReason<ISO date>>   # exact maturity/end date when known
  coordinate: <double>                    # derived tenor interpolation coordinate
  coordinate_unit: YEARS_FRACTION
  coordinate_method_id: UNRESOLVED
  coordinate_method_version: UNRESOLVED
```

- When exact expiry/start/end dates are known from the source or resolved instrument, they MUST be PRESENT and preserved. An adapter must not discard them and require a downstream consumer to reconstruct dates from `coordinate`.
- When an exact date is genuinely unavailable from the source, the field is `NULL_WITH_REASON`; the numeric coordinate may still be present only if an explicit coordinate methodology produced it from authoritative inputs. No reverse derivation from coordinate to calendar date is allowed.
- Coverage must be complete: a label without a required interpolation coordinate fails closed.
- Duplicate coordinates across labels fail closed (bracketing ambiguous).
- Out-of-range queries fail closed (`FAIL_CLOSED`); Bloomberg's own flat extrapolation is deliberately not mirrored.

### 9.4 Strike / moneyness coordinates

One economic fact per field. The contract separates:

```yaml
strike_coordinate:
  strike_dimension: ATM | YIELD_OFFSET_BP | ABSOLUTE_STRIKE | LOG_MONEYNESS
  absolute_strike: <ValueOrReason<NumericWithUnit>> # PRESENT iff absolute; unit carried in payload (canonical rate unit is explicit, not comment-only)
  yield_offset: <ValueOrReason<NumericWithUnit>>    # PRESENT iff YIELD_OFFSET_BP; unit must be BASIS_POINTS for the observed VCUB coordinate
  log_moneyness: <ValueOrReason<NumericWithUnit>>   # reserved; PRESENT only after approved methodology defines value+unit
```

- `YIELD_OFFSET_BP` is the VCUB-observed additive coordinate (`mu* = K* - F*`, `K_ij = F_ij + mu*`). Its serialized `yield_offset.value.unit` must be `BASIS_POINTS`; the unit is data, not inferred from the field name or a comment.
- Absolute vs offset vs log-moneyness confusion fails closed. The active strike member is PRESENT and the inactive members carry `NULL_WITH_REASON`; a `0bp` offset is not the ATM point (resolver precedent: ATM carries no offset).
- `absolute_strike` and `forward_ref` each carry value + unit in the payload. A parser must not infer decimal-annual vs basis-point representation from magnitude, field name, product type, or comments.
- The forward each moneyness is measured from (`forward_ref` in §9.1) is stated per quote or per corner where applicable, never assumed from the query's forward.

### 9.5 Source / timestamp / version

Every `VolatilityInput` names: `source`, per-quote `quote_timestamp` where known, `valuation_date`, `volatility_input_id`, `schema_version`, `methodology_id/version`, `smile_model_id/version`, interpolation / extrapolation versions, and the full upstream chain (`surface_id(s)`, `capture_id(s)`, adapter name/version, confirmer identity). A vol number without this chain is not an engine input.

### 9.6 Model / calibration inputs where architecture requires them

Vol inputs that are MODEL PARAMETERS rather than market quotes (e.g. Hull-White calibration outputs consumed by a later pricing call) cross the boundary as `ModelInput`, not as `VolQuote`:

```yaml
model_input:
  schema_version: MODEL_INPUT_V1
  model_input_id: <string>                # immutable ModelInput instance identity: derived from canonical ModelInput identity preimage per §15.4 (own id + fingerprint excluded; no invented timestamp)
  model_id: <enum>                        # e.g. HULL_WHITE_1F | BLACK_76 | BACHELIER — vocabulary, selection is RED-model data
  model_version: <string>                 # version of the model contract once approved; UNRESOLVED until then
  parameters:                             # one fact per parameter, each with unit
    - name: <string>                      # e.g. mean_reversion | volatility | ...
      value: <double>
      unit: <unit enum>                   # explicit; e.g. PER_YEAR | DECIMAL | ...
  calibration_ref: <ValueOrReason<calibration_result_id>> # identity index; PRESENT iff calibration_result payload is PRESENT
  calibration_result: <ValueOrReason<ModelCalibrationResult §14.3>> # embedded no-I/O evidence; PRESENT iff calibration-derived
  calibration_timestamp: <ValueOrReason<ISO-8601 timestamp+offset>> # MUST equal embedded calibration_result.calibrated_at; NOT_CALIBRATED otherwise
  valuation_date: <ISO date>
  source: <enum>                          # BLOOMBERG_DAPI | SCREEN_TRANSCRIPTION | SYNTHETIC_FIXTURE | RESEARCH_ADAPTER | CALIBRATION_OUTPUT
  methodology_id: <string | UNRESOLVED>   # model methodology identity once approved; UNRESOLVED — RED-225-M1 until then
  methodology_version: <string | UNRESOLVED>
  adapter_name: <string | NULL_WITH_REASON>     # Python adapter that produced this input where applicable
  adapter_version: <string | NULL_WITH_REASON>
  upstream_ids: <list>                    # loader / surface / calibration ids consumed; empty list is data (no upstream), never an omission
  evidence_refs: <list>                   # E-citations once methodology locks; empty until then
  applicable_timestamp: <ISO-8601 timestamp+offset | NULL_WITH_REASON>  # when this input was assembled / calibrated output produced; NULL_WITH_REASON where N/A, never a calibration timestamp for non-calibrated input
  content_fingerprint: <hex>              # digest of canonical ModelInput preimage per §15.4 (own fingerprint excluded)
```

- Model choice itself is RED-model (Phase 3+): defining the `model_id` vocabulary here does not approve any model for production. Hull-White 1F is the #222 Phase-3 target, not an approval.
- Calibration objective, optimizer, tolerance, and convergence semantics are owned by §14. For a calibration-derived input, `calibration_ref` and embedded `calibration_result` MUST both be PRESENT; the ref must equal `calibration_result.calibration_result_id`, `calibration_timestamp` must equal `calibration_result.calibrated_at`, `model_id/model_version` must match, and `ModelInput.parameters` must exactly equal the embedded result's calibrated `parameters`. Any mismatch fails closed.
- The no-I/O kernel MUST inspect the embedded calibration evidence. V1 permits consumption only when `calibration_result.status` is SUCCESS or SUCCESS_WITH_WARNINGS AND `calibration_result.convergence.status=CONVERGED`. FAILED / NOT_CONVERGED / NOT_APPLICABLE calibration evidence is not consumable. V1 intentionally has no calibration-override carrier; any future override requires a new versioned contract plus RED-225-M2 approval rather than an implicit bypass.
- For a non-calibrated ModelInput, `calibration_ref`, `calibration_result`, and `calibration_timestamp` are all structured `NULL_WITH_REASON(category=NOT_APPLICABLE, code=NOT_CALIBRATED)`.
- `ModelInput` satisfies the §15.3 common provenance contract (source, methodology_id/version, adapter_name/version, upstream_ids, evidence_refs, applicable timestamp, content_fingerprint) rather than weakening it.

---

## 10. Exercise representation

### 10.1 Posture

Typed exercise representation suitable for later European swaptions AND future Bermudan / callable products. The architecture distinguishes exercise facts without guessing, with the minimum structure that prevents known #222 products from requiring a fundamental rewrite. No exotic features are overdesigned.

### 10.2 Contract shape

```yaml
exercise_terms:
  schema_version: EXERCISE_TERMS_V1
  exercise_terms_id: <string>             # content-derived immutable identity; own id/fingerprint excluded from preimage §15.4
  exercise_style: EUROPEAN | BERMUDAN     # vocabulary; EUROPEAN usable now, BERMUDAN shape reserved for callable work (Phases 3+)
  exercise_dates: <ISO date list>         # EUROPEAN: exactly 1; BERMUDAN: 1..n sorted ascending, duplicate-free
  notice_dates: <ValueOrReason<ISO date list>> # PRESENT only when notice semantics apply
  underlying_reference:
    underlying_product_id: <product_id>   # MUST equal RatesKernelInput.valuation_product.underlying_product_id and resolved_swap.product_id for derivative valuations
    underlying_start_rule: <enum>         # e.g. EXERCISE_DATE_IS_UNDERLYING_START vs UNDERLYING_START_PER_SCHEDULE; VALUES UNRESOLVED — RED where methodology-dependent
    underlying_start_rule_version: UNRESOLVED
  calendar_ref: <ValueOrReason<id>>       # holiday calendar or structured N/A/unresolved reason
  business_day_convention: <ValueOrReason<enum>> # exercise-date BDC or structured N/A/unresolved reason
  timezone: <ValueOrReason<IANA string>>   # only where exercise timing genuinely requires it
  expiry_time: <ValueOrReason<time+timezone>> # exercise cutoff or structured N/A/unresolved reason
  content_fingerprint: <hex>              # non-recursive preimage §15.4
```

Rules:

- `EUROPEAN` with anything other than exactly one `exercise_date` fails closed. `BERMUDAN` with an empty or unsorted date list fails closed.
- `notice_dates`, `timezone`, `expiry_time`, `calendar_ref` are present ONLY where the product genuinely requires them. Inventing notice semantics for a product that has none is forbidden; omitting them where the product requires them fails closed.
- For derivative valuations, `underlying_reference.underlying_product_id` MUST equal both `RatesKernelInput.valuation_product.underlying_product_id.value` and embedded `RatesKernelInput.resolved_swap.product_id`; mismatch fails closed. For vanilla swaps, ExerciseTerms is NOT_APPLICABLE. There is no invented `resolved_swap_id` field because #224 does not define one.
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
  settlement_terms_id: <string>           # content-derived immutable identity; own id/fingerprint excluded from preimage §15.4
  settlement_type: CASH | PHYSICAL        # what is delivered: cash amount vs underlying swap
  settlement_method: <ValueOrReason<enum>> # CASH: PRESENT once approved; PHYSICAL: NULL_WITH_REASON(NOT_APPLICABLE)
  settlement_method_version: <ValueOrReason<string>> # same applicability as settlement_method
  cash_settlement_methodology: <ValueOrReason<CashSettlementMethodology>> # CASH: PRESENT object shaped below; PHYSICAL: structured N/A
  settlement_date: <ValueOrReason<ISO date>> # explicit date or structured unresolved/not-applicable reason
  settlement_currency: <Currency enum>    # explicit; never assumed equal to trade currency without a stated rule
  source_methodology_provenance: { source, evidence_refs, methodology_id/version }
  content_fingerprint: <hex>              # non-recursive preimage §15.4

cash_settlement_methodology_type:
  methodology_id: UNRESOLVED              # RED-225-S1
  methodology_version: UNRESOLVED
  settlement_rate_source: UNRESOLVED
  settlement_date_rule: UNRESOLVED
```

Rules:

- `settlement_type` (CASH vs PHYSICAL — WHAT is delivered) and `settlement_method` (cash amount determination rulebook, applicable only to CASH) are separate fields. Collapsing them into one string fails schema validation.
- For `CASH`: `settlement_method`, `settlement_method_version`, and `cash_settlement_methodology` MUST be PRESENT; unresolved methodology values remain RED-225-S1 and therefore fail closed until locked.
- For `PHYSICAL`: `settlement_method`, `settlement_method_version`, and `cash_settlement_methodology` MUST each be `NULL_WITH_REASON(category=NOT_APPLICABLE)`. A physical settlement must not carry an eternally-UNRESOLVED cash-only method.
- `CashSettlementMethodology` is the typed value carried inside the PRESENT `cash_settlement_methodology` union; the type declaration is not a second instance/authority.
- Settlement date / timing rules (`settlement_date_rule`, `settlement_date`) are explicit. A settlement date derived from an unresolved timing rule is `NULL_WITH_REASON`, never guessed from payment-lag precedent (#224 D6 is a swap-cashflow rule, not a swaption-settlement rule).
- No production CASH settlement value (method, methodology, rate source, date rule) is chosen here. All such values remain `UNRESOLVED — RED-225-S1` pending Phase-2 (#232) workstation reconciliation.

---

## 12. PricingResult (Rates)

### 12.1 Role

`PricingResult` is the typed output of one deterministic Rates valuation. Downstream Python / UI must interpret it without reverse-engineering C++ internals and without knowing QuantLib internals. The legacy `pricing/result.py::PricingResult` is REFERENCE ONLY (value-type spirit, status codes, structured messages, engine provenance, diagnostics); the Rates shape below is defined independently so #227+ can implement it literally.

### 12.2 Contract shape

```yaml
rates_pricing_result:
  schema_version: RATES_PRICING_RESULT_V1
  product_id: <string>                    # MUST equal RatesKernelInput.valuation_product.product_id
  product_type: <enum>                    # MUST equal RatesKernelInput.valuation_product.product_type
  valuation_date: <ISO date>              # the date valued; must equal MarketSnapshot.valuation_date
  valuation_context_id: <string>          # identity of the valuation request (date + reporting currency + snapshot + trade + model refs)
  result_currency: <Currency enum>        # MUST equal RatesKernelInput.valuation_context.reporting_currency == resolved_swap.currency in V1
  headline: <ValueOrReason<HeadlineValue>> # SUCCESS: PRESENT; FAILED: structured UNAVAILABLE(PRICING_FAILED)
  pv: <ValueOrReason<NumericWithUnit>>     # supplemental PV metric; unit=CURRENCY_AMOUNT when PRESENT
  pv_sign_convention: <ValueOrReason<enum>>
  component_pvs: <ValueOrReason<list[{component_id, value, unit, sign_convention_ref}]>> # each component serializes its unit/sign semantics
  annuity_pvbp: <ValueOrReason<NumericWithUnit>> # supplemental metric where computed
  par_rate: <ValueOrReason<NumericWithUnit>>     # supplemental metric; unit=DECIMAL_ANNUAL when PRESENT
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
    curve_role_map: <map>                 # exact echo of RatesKernelInput.curve_selection; result never invents selection
    fixing_store_id: <string>             # exact FixingStore consumed
    volatility_input_id: <ValueOrReason<string>> # exact vol input where applicable
    exercise_terms_id: <ValueOrReason<string>>    # exact ExerciseTerms payload when applicable
    settlement_terms_id: <ValueOrReason<string>>  # exact SettlementTerms payload when applicable
    resolved_swap_product_id: <string>    # exact embedded ResolvedSwap.product_id; underlying id for derivative valuation
    convention_set_id: <string>           # from ResolvedSwap provenance (#224)
    resolved_swap_schema_version: <string># RESOLVED_SWAP_V1 etc.
    model_input_id: <ValueOrReason<string>>       # exact ModelInput where applicable
    calibration_result_id: <ValueOrReason<string>>
  assumptions: <map>                      # every material assumption the engine made, as typed data (e.g. calendar_applied=false); never prose-only
  diagnostics: <map>                      # leg PVs, period counts, weights, fallbacks-not-taken; small typed values, never a narrative
  replay:
    content_fingerprint: <hex>            # digest of canonical result preimage per §15.4 (own fingerprint excluded) + inputs identity
    inputs_fingerprint: <hex>             # MUST equal the consumed RatesKernelInput.content_fingerprint
    tolerance: <string>                   # documented numeric tolerance for bit-for-bit comparison (e.g. ABS_1E_9)

headline_value_type:
  semantics: PRESENT_VALUE | PREMIUM | PAR_RATE | ANNUITY_PVBP
  value: <double>
  unit: <unit enum>
  currency: <ValueOrReason<Currency enum>> # PRESENT for currency-denominated headline; structured N/A otherwise
  sign_convention: <ValueOrReason<enum>>   # PRESENT when signed; structured N/A otherwise
```

### 12.3 Sign, unit, and price-semantics rules

- `headline` is the single authoritative generic-display value. A SUCCESS / SUCCESS_WITH_WARNINGS result MUST carry `PRESENT(HeadlineValue)`; FAILED MUST carry `NULL_WITH_REASON(category=UNAVAILABLE, code=PRICING_FAILED)`, never bare null, omission, or zero.
- `headline.semantics` tells the consumer exactly what `headline.value` means, and `headline.unit` travels with that value. A consumer never chooses among `pv`, `par_rate`, or `annuity_pvbp` based on product type or magnitude.
- Currency applicability is explicit: currency-denominated headline units require `headline.currency=PRESENT(result_currency)`; dimensionless/rate headlines use structured NOT_APPLICABLE unless their approved unit explicitly includes currency. Signed headline values require an explicit sign convention.
- `pv`, `par_rate`, and `annuity_pvbp` are supplemental typed metrics. When `headline.semantics` designates one of them, the corresponding supplemental metric MUST be PRESENT and exactly equal the headline value+unit (and currency/sign semantics where applicable); disagreement fails closed. A successful PAR_RATE/ANNUITY headline does not require a fabricated PV.
- Every PRESENT PV uses `CURRENCY_AMOUNT`, `result_currency`, and PRESENT `pv_sign_convention`. The legacy receive/pay sign rule remains REFERENCE ONLY; the actual sign convention must be stated.
- Component outputs (`component_pvs`) each carry `component_id + value + unit + sign_convention_ref` as fields. Components that do not sum to the headline say so in `assumptions`.

### 12.4 Linkage rules

- A result's `product_id/product_type` MUST echo `RatesKernelInput.valuation_product`. `inputs_identity.resolved_swap_product_id` MUST equal the embedded `RatesKernelInput.resolved_swap.product_id` (the same as top-level product_id for a vanilla swap; the underlying id for a derivative). Input identity also names the exact market/curve/fixing and optional vol/exercise/settlement/model/calibration identities plus convention/schema versions. A result that cannot name every PRESENT kernel input is not replayable and fails validation.
- `market_data_as_of` (legacy field spirit) is preserved as `valuation_date` + `market_snapshot_id`: the date alone is not identity; the snapshot id is.

---

## 13. RiskResult (Rates)

### 13.1 Role

`RiskResult` carries Greeks / bump sensitivities and any bucketed risk for one valuation. It is separate from `PricingResult` so risk methodology (bump sizes, bucketing, revaluation rules) never leaks into price semantics.

### 13.2 Contract shape

```yaml
rates_risk_result:
  schema_version: RATES_RISK_RESULT_V1
  product_id: <string>                    # MUST equal RatesKernelInput.valuation_product.product_id
  product_type: <enum>                    # MUST equal RatesKernelInput.valuation_product.product_type
  valuation_date: <ISO date>
  valuation_context_id: <string>
  result_currency: <Currency enum>        # MUST equal RatesKernelInput.valuation_context.reporting_currency == resolved_swap.currency in V1
  measures:
    - measure_id: DV01 | PV01 | DELTA | GAMMA | VEGA # exact RATES_RISK_RESULT_V1 vocabulary; no open-ended members
      value: <double>
      unit: <unit enum>                   # e.g. CURRENCY_AMOUNT_PER_BASIS_POINT for DV01; DECIMAL_SENSITIVITY where applicable; explicit per measure
      bump_spec:                          # methodology that produced this measure; required per measure
        bump_type: <enum>                 # PARALLEL_BP | BUCKETED_TENOR | VOL_POINT | MODEL_PARAM — vocabulary here
        bump_size: <double>
        bump_unit: <unit enum>            # BASIS_POINTS | DECIMAL | ... ; explicit
        revaluation_rule_id: UNRESOLVED   # RED-risk: full revaluation vs analytic; field defined, value open
        revaluation_rule_version: UNRESOLVED
      bucket_coordinate: <ValueOrReason<coordinate>> # PRESENT for bucketed measure; NULL_WITH_REASON for parallel/non-bucketed measure
      market_snapshot_id: <string>        # snapshot bumped
      model_version: <string | NULL>      # engine/model version used for revaluation
  inputs_identity:
    market_snapshot_id: <string>
    curve_set_id: <string>
    curve_role_map: <map>                 # exact echo of RatesKernelInput.curve_selection
    fixing_store_id: <string>
    volatility_input_id: <ValueOrReason<string>>
    exercise_terms_id: <ValueOrReason<string>>
    settlement_terms_id: <ValueOrReason<string>>
    resolved_swap_product_id: <string>    # exact embedded ResolvedSwap.product_id
    convention_set_id: <string>
    resolved_swap_schema_version: <string>
    model_input_id: <ValueOrReason<string>>
    calibration_result_id: <ValueOrReason<string>>
  status: SUCCESS | SUCCESS_WITH_WARNINGS | FAILED
  warnings: <list[{code, message, detail}]>
  errors: <list[{code, message, detail}>>   # FAILED carries >=1
  engine: { engine_name, engine_version, method }
  diagnostics: <map>
  replay: { content_fingerprint, inputs_fingerprint, tolerance }  # inputs_fingerprint = consumed RatesKernelInput.content_fingerprint; content fingerprint per §15.4
```

Rules:

- `product_id/product_type` MUST echo `RatesKernelInput.valuation_product`, and `inputs_identity.resolved_swap_product_id` MUST equal the embedded `ResolvedSwap.product_id`. Every optional input identity MUST echo the corresponding `RatesKernelInput` applicability state: PRESENT carries the exact consumed object's id; NOT_APPLICABLE remains structured `NULL_WITH_REASON(NOT_APPLICABLE)`. Omission, bare null, or fabricated ids are malformed.
- Every measure names its `measure_id`, `bump_spec` (type + size + unit + revaluation rule), `unit`, and structured `bucket_coordinate`. Bucketed measures require PRESENT coordinates; parallel/non-bucketed measures require `NULL_WITH_REASON(category=NOT_APPLICABLE)`. A sensitivity without a bump spec is not a result.
- Bump sizes and bucketing rules are per-measure data with explicit units. Production bump conventions (1bp vs 0.5bp, bucket boundaries) are UNRESOLVED — RED-risk where desk methodology is required; the fields exist so the choice is recordable, not so a default is smuggled in.
- RATES_RISK_RESULT_V1 measure vocabulary is EXACTLY `DV01 | PV01 | DELTA | GAMMA | VEGA`. Unknown identifiers (including RHO, CS01, or product-specific aliases) fail closed; adding a measure requires a future schema-version vocabulary extension, not free text. #229 consumes only DV01 scope; defining the other tokens does not approve their production methodology.

---

## 14. ModelCalibrationResult + model inputs

### 14.1 Role

`ModelCalibrationResult` records how a model (e.g. Hull-White 1F in Phase 3) was calibrated against validated instruments, so a later pricing call consuming its `ModelInput` (§9.6) is auditable. Calibration methodology itself is NOT decided here — only the contract capable of carrying it.

### 14.2 ModelCalibrationInput — canonical calibration invocation

Calibration is a distinct deterministic invocation shape from pricing. This issue defines the exact replay preimage without choosing model/calibration methodology values or concrete future product schemas.

```yaml
model_calibration_input:
  schema_version: MODEL_CALIBRATION_INPUT_V1
  calibration_input_id: <string>          # content-derived immutable identity; own id/fingerprint excluded from preimage §15.4
  valuation_context:
    valuation_context_id: <string>
    valuation_date: <ISO date>            # MUST equal market_snapshot.valuation_date
  market_snapshot: <MarketSnapshot §6>    # exact embedded market payload calibrated against
  model_id: <enum>                        # selection remains RED-225-M1
  model_version: UNRESOLVED
  calibration_instruments: <ordered list>
    - instrument_id: <string>             # stable id within this calibration set
      instrument_type: <enum>             # e.g. EUROPEAN_SWAPTION | VANILLA_SWAP
      instrument_terms:                   # executable immutable terms payload; concrete schema owned/versioned by the product issue
        schema_version: <string>           # exact owning product contract version
        payload: <typed canonical object> # full product terms needed to construct the calibration instrument; never fingerprint-only
        content_fingerprint: <hex>         # digest of this exact schema_version + payload under §15.4 non-recursive rules
      market_target: <CalibrationMarketTarget> # exact resolvable embedded-market target
      weight: <ValueOrReason<NumericWithUnit>>
  initial_parameters: <ValueOrReason<ordered list[{name, value, unit}]>> # PRESENT when optimizer/model requires seed; structured N/A otherwise
  objective:
    objective_id: UNRESOLVED              # RED-225-M2
    objective_version: UNRESOLVED
    parameters: <map<string, NumericWithUnit | enum | string>> # exact typed objective settings; empty only if approved objective has none
  optimizer:
    optimizer_id: UNRESOLVED              # RED-225-M2
    optimizer_version: UNRESOLVED
    parameters: <map<string, NumericWithUnit | enum | string>> # exact typed optimizer settings
  convergence_policy:
    tolerance: <ValueOrReason<NumericWithUnit>>
    max_iterations: <ValueOrReason<int>>
    policy_id: UNRESOLVED                 # RED-225-M2
    policy_version: UNRESOLVED
  content_fingerprint: <hex>              # digest of this full canonical input preimage; own id/fingerprint excluded

calibration_market_target_type:
  kind: VOL_QUOTE_NODE_V1                 # only resolvable V1 target kind; later kinds require schema revision
  volatility_input_id: <string>           # MUST equal embedded market_snapshot.volatility_input id
  node_key: <VolNodeKey>                  # MUST resolve uniquely to exactly one embedded VolQuote
```

Canonical calibration-input rules:

- `ModelCalibrationInput.content_fingerprint` fingerprints ALL fields above in canonical serialization semantics: valuation context, full MarketSnapshot content, model id/version, ordered calibration-instrument records (including each full `instrument_terms.schema_version + payload + content_fingerprint`, typed market target, and weights), initial parameters, objective id/version/settings, optimizer id/version/settings, and convergence policy.
- Each `market_target.kind=VOL_QUOTE_NODE_V1` MUST name the embedded `VolatilityInput` actually present in `market_snapshot` and a `VolNodeKey` that resolves to exactly one embedded quote; missing, zero-match, or duplicate-match targets fail closed. A free-form string or external lookup is not a V1 market target. Curve/fixing target kinds are not invented here and require a later schema revision if needed.
- Each calibration instrument MUST carry the full immutable executable `instrument_terms.payload` defined by the owning later product contract, not merely a digest or external id. Its `instrument_terms.content_fingerprint` MUST match the embedded `schema_version + payload`; mismatch fails closed.
- #225 does not invent European swaption/callable instrument economics before those issues define them. The envelope above fixes payload LOCATION / replay semantics only; the concrete typed payload schema and economic fields remain owned by the relevant later product issue.
- Instrument order is canonical and deterministic (ascending `instrument_id` unless a later LOCKED methodology explicitly requires another ordering and records that version). Duplicate `instrument_id` values fail closed.
- No default initial parameter, objective, optimizer, tolerance, max iterations, or model is selected here. UNRESOLVED methodology values remain RED-225-M1/M2 and cannot be made production inputs until locked.
- Calibration may be orchestrated outside the pricing kernel, but replay identity is still deterministic and no implementation may choose a different input preimage while claiming `MODEL_CALIBRATION_INPUT_V1`.

### 14.3 ModelCalibrationResult contract

```yaml
model_calibration_result:
  schema_version: MODEL_CALIBRATION_RESULT_V1
  calibration_result_id: <string>         # immutable calibration instance identity: derived from canonical calibration identity preimage per §15.4 (own id + fingerprint excluded; calibrated_at participates once as the instance timestamp)
  calibration_input_id: <string>           # exact ModelCalibrationInput consumed
  model_id: <enum>                        # HULL_WHITE_1F | BLACK_76 | BACHELIER | ... — vocabulary, selection is RED-model data
  model_version: UNRESOLVED               # RED-model: version of the approved model contract; field defined, value open
  calibrated_at: <ISO-8601 timestamp+offset>  # when calibration ran; distinct from valuation_date
  valuation_context_id: <string>          # context calibrated under
  market_snapshot_id: <string>            # exact snapshot calibrated against
  instruments:                            # calibration instruments / identifiers
    - instrument_id: <string>
      instrument_type: <enum>             # EUROPEAN_SWAPTION | VANILLA_SWAP | ...
      market_target: <CalibrationMarketTarget> # exact echo of consumed ModelCalibrationInput target
      weight: <ValueOrReason<NumericWithUnit>> # exact echo of input weight
  parameters:                             # calibrated parameters, one fact per parameter
    - name: <string>
      value: <double>
      unit: <unit enum>                   # PER_YEAR | DECIMAL | ... ; explicit
  objective:
    objective_id: UNRESOLVED              # RED-model: e.g. WEIGHTED_SQUARE_PRICE_ERROR; field defined, value open
    objective_version: UNRESOLVED
    error_value: <double | NULL>          # paired with error_unit below; NULL where N/A
    error_unit: <unit enum | NULL>
    per_instrument_errors: <ValueOrReason<list[{instrument_id, error_value, error_unit}]>> # each recorded error carries unit
  convergence:
    status: CONVERGED | NOT_CONVERGED | NOT_APPLICABLE  # machine-readable; never prose
    iterations: <int | NULL>
    tolerance: <ValueOrReason<NumericWithUnit>> # exact stopping tolerance echoed from ModelCalibrationInput.convergence_policy when applicable
    optimizer_id: UNRESOLVED              # RED-model: optimizer identity; field defined, value open
    optimizer_version: UNRESOLVED
  source_provenance: { source, adapter_versions, upstream_ids, evidence_refs }
  status: SUCCESS | SUCCESS_WITH_WARNINGS | FAILED
  warnings: <list[{code, message, detail}]>
  errors: <list[{code, message, detail}>>   # FAILED carries >=1
  replay: { content_fingerprint, inputs_fingerprint, tolerance }  # inputs_fingerprint MUST equal consumed ModelCalibrationInput.content_fingerprint; content fingerprint per §15.4
```

Rules:

- A calibration result without `calibration_input_id`, `model_id`, `market_snapshot_id`, `instruments`, `parameters` (with units), `objective` identity, and `convergence.status` is incomplete and fails validation.
- `ModelCalibrationResult.replay.inputs_fingerprint` MUST equal the consumed `ModelCalibrationInput.content_fingerprint`; `calibration_input_id` MUST equal that input's identity. Implementations may not hash an ad-hoc subset.
- Every result field that echoes calibration input identity MUST match it exactly: `model_id/model_version`, `valuation_context_id`, `market_snapshot_id == input.market_snapshot.snapshot_id`, ordered `instruments[*].{instrument_id,instrument_type,market_target,weight}`, `objective.{objective_id,objective_version}`, and `convergence.{optimizer_id,optimizer_version}`. The authoritative instrument economics remain the embedded `ModelCalibrationInput.calibration_instruments[*].instrument_terms`; result rows do not redefine them. Any mismatch is a malformed result, not a second authority.
- When `convergence.tolerance` is applicable, it MUST echo the exact `ModelCalibrationInput.convergence_policy.tolerance` value+unit. Output-only facts are `calibrated_at`, calibrated `parameters`, objective error values, iteration count, convergence status, warnings/errors, and provenance.
- `NOT_CONVERGED` results are still first-class audit records, but RATES_KERNEL_INPUT_V1 cannot consume them through ModelInput. This V1 schema has no override carrier; a future override path requires an explicit schema revision and RED-225-M2 approval.
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

- `RATES_KERNEL_INPUT_V1`, `MARKET_SNAPSHOT_V1`, `CURVE_SET_V1`, `DISCOUNT_CURVE_V1`, `FORWARD_CURVE_V1`, `FIXING_STORE_V1`, `VOL_QUOTE_V1`, `VOLATILITY_INPUT_V1`, `MODEL_INPUT_V1`, `EXERCISE_TERMS_V1`, `SETTLEMENT_TERMS_V1`, `RATES_PRICING_RESULT_V1`, `RATES_RISK_RESULT_V1`, `MODEL_CALIBRATION_INPUT_V1`, `MODEL_CALIBRATION_RESULT_V1`.
- Pattern `<CONTRACT>_V<n>`. A wire-schema revision ships under a new version string, never by mutating a published shape. Unknown versions fail closed before any other content is read.
- Methodology versions (`construction_methodology_id/version`, `methodology_id/version`, `smile_model_id/version`, `model_id/version`, `same_day_rule_id/version`, `settlement methodology`, `objective/optimizer`) evolve independently from wire-schema versions. Neither implies the other.
- Lifecycle for methodology versions is `PROPOSED → LOCKED`; `LOCKED` requires cited workstation/desk evidence plus Sophira acceptance. No migration framework beyond this rule is designed here (per #225 scope).

### 15.3 Provenance (mandatory by identity level)

Every independently identity-bearing MARKET / MODEL object (`MarketSnapshot`, `CurveSet`, `DiscountCurve`, `ForwardCurve`, `FixingStore`, `VolatilityInput`, `ModelInput`, `ModelCalibrationResult`) carries a complete structural provenance envelope:

- `source` for a single-source object OR explicit `sources` for a composite container; for a composite with no PRESENT sourced child facts (e.g. forecast-only FixingStore), the explicit sources list may be empty and MUST NOT be padded with an invented market source; never infer source from a parent;
- `adapter_name/version` or `adapter_versions` where adapted;
- `upstream_ids` (loader batches, `surface_id(s)`, `capture_id(s)`, fixture ids);
- an applicable timestamp (`captured_at`, per-fact `quote_timestamp`, `applicable_timestamp`, or `calibrated_at`) with explicit offset;
- `evidence_refs` (E-citations where methodology locked, empty otherwise);
- `content_fingerprint`.

Embedded leaf facts (curve pillars, fixing entries, VolQuote nodes) need not duplicate the entire container envelope, but MUST carry any fact-level source/timestamp/unit/status fields required to distinguish their own semantics. Their authoritative adapter/upstream/evidence chain is the containing identity-bearing object's explicit envelope plus the deterministic child path; no prose or parent guessing is permitted.

Source + methodology + version are never collapsed into one string. Provenance written only in prose is not provenance. A reader that cannot determine what a number means, what units it uses, where it came from, which methodology/version produced it, which market snapshot it belongs to, and whether it is historical, forecast, calibrated, observed, or unresolved — from FIELDS alone — is reading a contract defect.

### 15.4 Deterministic replay

- Canonical serialization: every contract defines a canonical JSON form (sorted keys, fixed separators, decimal formatting pinned by the owning implementation issue — format details owned by #227, not chosen here).
- Fingerprint-relevant arrays have schema-defined canonical order; source/acquisition order is never allowed to change a fingerprint:
  - `CurveSet.curves`: ascending canonical tuple `(curve_role, index_id_sort_key, curve_id)`; for DiscountCurve (no `index_id` field), `index_id_sort_key` is exactly the empty UTF-8 string `""`; for ForwardCurve it is the canonical enum token. Duplicate `curve_id` forbidden.
  - `DiscountCurve/ForwardCurve.pillars`: strictly ascending `pillar_date`; duplicate dates forbidden (§7.2).
  - `FixingStore.entries`: ascending `(index_id, observation_date)`; duplicate economic keys forbidden (§8).
  - Vol axes: ascending numeric `coordinate`; duplicate coordinates forbidden. `strikes`: ascending by StrikeDimension enum order `ATM < YIELD_OFFSET_BP < ABSOLUTE_STRIKE < LOG_MONEYNESS`, then lexicographic canonical JSON serialization of the active `NumericWithUnit` payload (or the full StrikeCoordinate when no active numeric exists). `quotes`: ascending canonical `VolNodeKey`; duplicate node keys forbidden (§9.2–§9.4).
  - `exercise_dates`: ascending date, duplicate-free (§10). `calibration_instruments`: ascending `instrument_id`, duplicate-free (§14.2).
  - Named parameter lists (`ModelInput`, calibration input/result parameters) are ascending `name` with duplicate names forbidden. `per_instrument_errors` follows calibration-instrument order and permits at most one row per `instrument_id`.
  - `RiskResult.measures`: ascending `(measure_id, canonical bucket_coordinate serialization)`; duplicate exact measure/bucket keys forbidden.
  - Set-semantic provenance lists such as `upstream_ids`, `evidence_refs`, capture/surface ids are lexicographically sorted and duplicate-free before serialization.
  - `MarketSnapshot.diagnostics.unresolved_fields` is lexicographically sorted by canonical field-path string and duplicate-free before serialization.
  - Lists whose order is itself an economic fact retain their explicitly defined domain order; warnings/errors/diagnostics other than the input-assembled `unresolved_fields` set are output records and must be emitted deterministically by the engine.
- NON-RECURSIVE FINGERPRINT PREIMAGE (contract rule, not implementation convention). For any object carrying a `content_fingerprint` (RatesKernelInput, MarketSnapshot, CurveSet, DiscountCurve, ForwardCurve, FixingStore, VolatilityInput and applicable VolQuote objects, ModelInput, ExerciseTerms, SettlementTerms, Rates PricingResult / RiskResult / ModelCalibrationInput / ModelCalibrationResult, and any nested `replay.content_fingerprint`):
  - the object's own `content_fingerprint` field is EXCLUDED from its canonical fingerprint preimage;
  - the object's own identity field naming itself (`snapshot_id`, `curve_set_id`, `curve_id`, `fixing_store_id`, `volatility_input_id`, `model_input_id`, `calibration_result_id`, and equivalent result identity fields) is likewise EXCLUDED from its own preimage where including it would recurse, while all other identity content (including `captured_at` / `calibrated_at` / `valuation_date` where they are ordinary identity fields per §6.3/§14) participates normally;
  - the fingerprint field being computed never participates in its own preimage;
  - independent child / input fingerprints (e.g. a result's `inputs_fingerprint` referencing `market_snapshot_id` digests, or a `ModelInput.calibration_ref` target id) remain ordinary referenced input fields where the owning contract says they are part of the object — only self-reference is excluded.
- No hashing algorithm or canonical numeric formatting is invented here: those details remain owned by #227. This fix is about PREIMAGE semantics (which fields participate), not implementation choice.
- For `PricingResult` and `RiskResult`, `inputs_fingerprint` MUST equal the exact `RatesKernelInput.content_fingerprint` consumed by the call. Every direct kernel fact therefore participates automatically: valuation_product, ResolvedSwap, MarketSnapshot, curve_selection, ExerciseTerms, SettlementTerms, ModelInput, and ValuationContext.
- Pricing/risk replay identity = `RatesKernelInput.content_fingerprint + engine_version + method + tolerance`. Given identical kernel input and engine version, the result must reproduce within `tolerance`. A nested `replay.content_fingerprint` likewise excludes its own field from its preimage.
- `ModelCalibrationResult.replay.inputs_fingerprint` MUST equal the consumed `ModelCalibrationInput.content_fingerprint` defined in §14.2 and is not silently equated to a pricing `RatesKernelInput`; calibration and pricing are distinct invocation shapes.
- `ValueOrReason<T>` / `NULL_WITH_REASON` state, including the structured reason category/code/detail, is part of the fingerprint: an unresolved field resolved later is a different input, not the same input clarified.

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
| `CURRENCY_MISMATCH` | Reporting / selected-curve / settlement currency disagrees with ResolvedSwap currency where V1 has no FX contract |
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

Owner decision (round 2, identifier-governance only — not methodology): keep the issue-scoped `RED-225-*` namespace. Do NOT renumber into global `RED-03` / `RED-04` / etc. The namespace makes ownership and provenance explicit and avoids future collision as #226+ introduce their own unresolved items. This decision does NOT resolve any RED methodology VALUE.

Carried open REDs (not closed by this document):

- **RED-01 — Curve authority for first production UAT** (owner: Sophira/Eddy). Whether the first C++ vanilla-swap UAT consumes an already-resolved curve or bootstraps from SOFR instruments. This document defines the ENGINE CONTRACT fields for either outcome (`construction_methodology_id/version`, `construction_inputs_ref`, pillar + interpolation/extrapolation metadata) but chooses NEITHER value. Working recommendation (consume already-resolved first) remains a recommendation only.
- **RED-02 — USD SOFR workstation convention authority** (owner: Sophira/Eddy). All #224 D1–D13 values plus E1–E6 evidence. Untouched by this document.

New unresolved methodology VALUES defined-as-fields-but-not-chosen here (each `UNRESOLVED — RED`, owner Sophira/Eddy unless noted):

- **RED-225-C1 — Curve construction methodology + version** (§7.1/§7.6). Evidence: RED-01 lock + construction inputs capture.
- **RED-225-C2 — Curve interpolation method + version and parameters** (§7.2/§7.5). Evidence: workstation / desk interpolation authority + versioned method contract.
- **RED-225-C3 — Curve extrapolation method + version** (§7.5). Only `FAIL_CLOSED` pre-approved as fallback shape; any production extrapolation needs evidence.
- **RED-225-C4 — Curve compounding / day-count / accrual-boundary values** (§7.2/§7.4). Evidence: desk/workstation rate-basis authority. Note BLI `exp(-r·T)` vs reference-engine `1/(1+r·T)` are both REFERENCE ONLY and must not be unified silently.
- **RED-225-F1 — Fixing publication-availability / same-day rule + cutoff + unit + timezone** (§8.2 embedded `FixingStore.same_day_rule` / §8.4–§8.5; observation date remains separate from publication date/time). Evidence: publication-timing / desk ruling.
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
- **#227 (C++20 skeleton + versioned DTO/JSON contract + CI).** Consumes: `RatesKernelInput` boundary composition (§5.1), wire-schema versions (§15.2), canonical serialization + NON-RECURSIVE preimage rule (§15.4, format/hash details owned by #227), `UNKNOWN_SCHEMA_VERSION` / `UNKNOWN_METHODOLOGY_VERSION` fail-closed behavior (§16). Must refuse unknown versions before content; must implement `NULL_WITH_REASON` as fingerprint-participating (§15.4); must exclude each object's own fingerprint/identity field from its own preimage while keeping child/input fingerprints as ordinary fields.
- **#228 (curve, fixing, calendar, resolved SOFR schedule primitives).** Consumes: `CurveSet` / `DiscountCurve` / `ForwardCurve` shapes (§7) with RED-01 values still open — implements the MECHANICS against the contract without choosing production values; `FixingStore` semantics + embedded `same_day_rule` (§8.2/§8.5) + state-dependent projection fields (§8.2/§8.3) + fail-closed table (§8.4); calendar/BDC role shape from #224 (values still RED-02). #228 owns the versioned target registries / DTO targets that make `construction_inputs_ref`, `fixing_calendar_ref`, and `observation_rules_ref` resolvable; until those target schemas exist, these refs remain structured unresolved/not-applicable values and MUST NOT trigger external guessing. Must not silently close RED-01, RED-02, RED-225-C*, or RED-225-F*.
- **#229 (C++ vanilla USD SOFR swap kernel).** Consumes: `RatesKernelInput` (§5.1), whose vanilla-swap invocation carries `ResolvedSwap` + `MarketSnapshot` and structured NOT_APPLICABLE exercise/settlement/model terms as appropriate, plus `PricingResult` (§12) + `RiskResult` (§13, DV01 scope). Must record `curve_role_map`, inputs identity, assumptions, diagnostics, and replay fingerprints per §12; must implement the §16 fail-closed table literally. Must not invent curve/vol/model methodology to fill RED gaps.
- Later issues (#230 workstation reconciliation, #231–#232 swaption engine + quote/settlement reconciliation, #233–#235 Hull-White/Bermudan, #236–#238 accruals) consume §9–§11 + §14 without contract redesign, filling RED-225-V*/E*/S*/M* values with evidence at their own gates.

---

## 19. Acceptance checklist

- [ ] Document defines the required contract families with field-level shapes: MarketSnapshot (§6), CurveSet/curves (§7), FixingStore (§8), vol quote/surface/cube (§9), ModelInput (§9.6), exercise (§10), settlement (§11), PricingResult (§12), RiskResult (§13), ModelCalibrationInput + ModelCalibrationResult (§14), provenance/replay (§15).
- [ ] Relation to approved `SwapTrade -> ConventionSet -> ResolvedSwap` (#224) explicit (§5, §12.4); no competing authoritative copies.
- [ ] #223 ownership boundary preserved (§3): Python acquisition/normalization/persistence/UI vs C++ resolution/pricing; no live Bloomberg in kernel; QuantLib behind isolation, defaults never methodology.
- [ ] #224 semantics preserved: R1/R2, wire versioning, D1–D13/E1–E6 untouched; no RED-02 value filled.
- [ ] `RatesKernelInput` (§5.1) explicitly separates valuation_product identity from underlying ResolvedSwap identity, carries all direct payloads, enforces V1 single-currency compatibility (no hidden FX), and can validate embedded calibration convergence evidence without I/O.
- [ ] MarketSnapshot canonical wire shape contains typed embedded CurveSet/FixingStore/VolatilityInput payloads plus matching identity refs (§6.2/§6.4); ids without required content and ref/payload identity mismatches fail closed.
- [ ] Curve ENGINE CONTRACT has exactly one wire schema version authority; reference_date differences carry a serialized reason; identity / currency / role / dates / pillars / value semantics / provenance / methodology are explicit (§7); unresolved pillars remain structurally present; no naked double arrays.
- [ ] FixingStore distinguishes observation/publication dates and typed state (§8.2–§8.5); PROJECTED audit rows carry resolvable typed ProjectionInputsRef (curve set + forward curve + observation rule); no opaque projection ids or silent history-to-forecast.
- [ ] Vol contract forbids naked/ambiguous vol semantics (§9.0); SURFACE/CUBE are disjoint; container ATM definition is the single authority and every quote exactly echoes its ValueOrReason state/value; VolNodeKey identity/order is deterministic; no production vol methodology chosen (RED-225-V*).
- [ ] Exercise distinguishes style / dates / notice / underlying-start rule / calendar-timezone (§10); derivative underlying_product_id binds to both RatesKernelInput.valuation_product.underlying_product_id and embedded ResolvedSwap.product_id; no invented ResolvedSwap identity field; unresolved methodology values remain RED.
- [ ] Settlement separates type / cash-only method / physical-vs-cash / cash methodology / date-timing / provenance (§11); PHYSICAL carries structured NOT_APPLICABLE for cash-only method fields rather than unresolved fake values; no invented values (RED-225-S1).
- [ ] PricingResult/RiskResult/ModelCalibrationResult have literal separate warnings + errors fields; RiskResult V1 measure vocabulary is closed; ModelInput embeds calibration result evidence so convergence is enforceable without external lookup (§12–§14).
- [ ] Versioning/provenance: actual value vs resolution-status separated (§15.1); machine-readable schema versions everywhere incl. `RATES_KERNEL_INPUT_V1` (§15.2); independently identity-bearing market/model objects satisfy the complete provenance envelope (§15.3); source/methodology/version never collapsed; replay fingerprints defined with NON-RECURSIVE preimage (§15.4); snapshot identity includes `captured_at` exactly once; ExerciseTerms/SettlementTerms carry identities + fingerprints.
- [ ] Fail-closed rules enumerated (§16 + per-section rules); RED list complete with owners (§17); #226–#229 boundaries preserved (§18).
- [ ] Same-defect-family audit performed (§A below): no naked units, no role-by-name, no comment-as-data, no collapsed provenance, no history/forecast ambiguity, no settlement/vol-coordinate ambiguity, no QuantLib/Bloomberg hidden dependence, nothing #227–#229 must infer.
- [ ] Literal-implementer review performed (§B below): every remaining guess classified A (fixed), B (explicit RED), or C (later-issue handoff); no uncategorized guess remains.
- [ ] Validation: full diff vs `main` inspected; `git diff --check` clean; only intended docs file changed; no runtime/schema/build changes; stale-term grep clean.

---

## A. Same-defect-family audit (performed before push; round 2 re-audit included)

Searched the new contract for each defect family; outcome per family:

1. Naked numerics with ambiguous units — PASS. Every numeric field pairs with an explicit unit enum (`DECIMAL_ANNUAL`, `RATIO`, `BASIS_POINTS`, `CURRENCY_AMOUNT*`, `PER_YEAR`, `YEARS_FRACTION`) or a unit-suffixed value-type. Percent/bp untagged numerics refused (§7.4, §9.1). Round 2: `cutoff_time` / `cutoff_time_unit` separated per P1 (§8.2).
2. Naked `double` vol semantics — PASS. §9.0 forbids; §9.1–§9.2 require quote-type + unit + shift-unit + coordinates + methodology per quote/container.
3. Curve arrays without DF/zero meaning — PASS. §7.2–§7.4 require per-curve `value_type` + `value_unit` + `compounding` interpretation; display-only par rates structurally barred from pricing.
4. Role implied only by variable name — PASS. `curve_role`, per-leg role maps, index ids, calendar/BDC role shape (#224) are fields; §16 refuses name-implied roles.
5. Resolved methodology hidden only in comments — PASS. Methodology ids/versions are fields (§7, §9, §14, §15); comments carry no semantics by P3. Round 2: `same_day_rule` (§8.2), projection fields (§8.2/§8.3), ModelInput provenance (§9.6) are all fields, not prose.
6. Status strings where values belong — PASS. §15.1 separates actual value from resolution/evidence status; `observation_state`, `convergence.status`, `settlement_type/method` each carry values, not statuses.
7. Provenance only in prose — PASS. §15.3 requires source/adapter/upstream/timestamp/evidence/fingerprint FIELDS per object. Round 2: ModelInput now satisfies §15.3 (§9.6) instead of weakening it.
8. Source + methodology + version collapsed — PASS. Separate fields everywhere; §16 lists collapsing as schema defect.
9. History vs forecast ambiguity — PASS. §8.3 enum + §8.4 table + §8.5 same-day RED; future-history refused. Round 2: `PROJECTED` audit metadata is now shaped (§8.2 projection fields with state-dependent rules); `HISTORICAL` with projection methodology and `MISSING` converted by projection both fail closed.
10. Settlement type/method ambiguity — PASS. §11.2 separates type/method/methodology/date/currency.
11. NORMAL vs SHIFTED_LOGNORMAL ambiguity — PASS. §9.1 `quote_type` machine-readable; shift/unit mandatory for shifted; cross-type reinterpretation refused.
12. Shift-units ambiguity — PASS. `shift_unit` mandatory with shift; unit-less shifts refused.
13. Expiry/tenor/strike coordinate ambiguity — PASS. §9.3–§9.4 require label + numeric + unit + method per axis; incomplete maps and duplicate coordinates refused.
14. Result units / sign conventions guessable — PASS. §12.3 requires currency + unit + sign convention + price semantics per result.
15. Hidden QuantLib dependence — PASS. No QuantLib symbol, default, or day-count appears as a value; QuantLib confined to #226 isolation per §3. No production value chosen while repairing representation (round 2 verified).
16. Hidden Bloomberg dependence — PASS. Bloomberg sources are `source` enum VALUES per snapshot/quote (data), never methodology; no Bloomberg default adopted as a value. No cutoff/timezone invented in round 2 (§8.2/§8.5 all UNRESOLVED).
17. Anything #227–#229 would have to infer — addressed in §B; remaining items are explicit RED (B) or handoff (C), none uncategorized.
18. Round-2 families: (a) prose-described rule missing from YAML shape — FIXED (§8.2 same_day_rule embedded; §8.2 projection fields; §9.6 ModelInput provenance); (b) conditionally-required fields absent — FIXED (projection state rules; ModelInput calibration timestamp rule); (c) recursive fingerprint preimage — FIXED (§15.4 non-recursive rule applied to all fingerprinted objects); (d) double-counted timestamp identity — FIXED (§6.3 captured_at once; stale `digest + disambiguator` / `same rule as §6.3` language removed); (e) false idempotence claim — FIXED (reassembly at new captured_at is a new instance); (f) duplicate authoritative location — FIXED (single FixingStore.same_day_rule; §6.3/§8.5 state no snapshot duplicate); (g) result replay self-hash — FIXED (§15.4 + replay comments).
19. Round-3 shape-vs-prose/unit families — FIXED: (a) MarketSnapshot now has actual embedded CurveSet/FixingStore/VolatilityInput fields plus ref/payload equality rules (§6.2/§6.4); (b) unresolved curve pillars serialize as value_state + nullable value + StructuredReason (§7.2); (c) unresolved vol nodes serialize equivalently without fabricated numerics (§9.1/§9.2); (d) strike/forward units are payload data via NumericWithUnit (§9.1/§9.4); (e) annuity PVBP + par rate + component PV units are explicit (§12.2/§12.3); (f) calibration weights and per-instrument errors carry units (§14.2); (g) every remaining NULL_WITH_REASON shorthand is governed by the typed ValueOrReason<T> union (§4.1), so reason semantics are serializable rather than comment-only.
20. Round-4 contract-consistency families — FIXED: (a) exercise/settlement/model payload location made explicit via RatesKernelInput; MarketSnapshot returned to market-only authority; ExerciseTerms/SettlementTerms gain identity+fingerprint; (b) curve value_type/value_unit canonicalized exactly once at curve level and V1 mixed semantics forbidden; (c) PROJECTED fixing is either complete audit-only state or malformed, with future date-state path allowing FORECAST_REQUIRED or PROJECTED as alternatives; (d) VolatilityInput owns methodology_id/version and embedded quote equality is mandatory; (e) CurveSet/DiscountCurve/FixingStore/VolatilityInput provenance envelopes completed; §15.3 narrowed explicitly by identity level to avoid meaningless leaf duplication while forbidding provenance inference.
21. Round-5 deterministic-input/date families — FIXED: (a) RatesKernelInput carries explicit curve_selection and PricingResult curve_role_map echoes it; (b) pricing/risk inputs_fingerprint equals the entire RatesKernelInput.content_fingerprint, covering every PRESENT direct input; (c) FixingStore separates observation_date from publication_date/publication_timestamp and publication availability drives history-vs-forecast; (d) vol axes preserve exact expiry/underlying start/end dates when known instead of reconstructing them from year-fraction coordinates.
22. Round-6 representation/replay families — FIXED: (a) reason-bearing nullable fields identified by review (index_tenor, VolatilityInput.strikes, RiskResult.bucket_coordinate) use ValueOrReason; (b) each curve pillar carries source_column or structured N/A; (c) forecast-only FixingStore source union may be explicitly empty; (d) ExerciseTerms binds to actual #224 ResolvedSwap.product_id; (e) ModelCalibrationInput defines the exact deterministic calibration replay preimage and ModelCalibrationResult binds its inputs_fingerprint to that input.
23. Round-7 executable-payload/vocabulary/applicability families — FIXED: (a) every calibration instrument embeds its full versioned executable instrument_terms payload plus matching fingerprint, so replay does not depend on digest-only/external reconstruction; (b) StrikeDimension has one canonical token vocabulary and methodology approval is separate state; (c) cash-only settlement method fields are PRESENT only for CASH and structured NOT_APPLICABLE for PHYSICAL.
24. Round-8 identity/headline/result-applicability families — FIXED: (a) RatesKernelInput.valuation_product separates derivative trade identity from underlying ResolvedSwap.product_id and results echo the valued product identity; (b) PricingResult headline is a tagged authoritative value, while PV/par-rate/annuity are supplemental ValueOrReason metrics with equality rules when designated as headline; (c) RiskResult optional input identities use the same ValueOrReason applicability states as PricingResult/inputs.
25. Round-9 OC/Codex audit families — FIXED: (a) removed duplicate curve schema_version_ref; (b) reference-date divergence has a serialized reason and unambiguous anchor role; (c) SURFACE/CUBE are structurally disjoint by strike cardinality; (d) container ATM definition supports structured NOT_APPLICABLE; (e) VolQuote node keys are deterministic/resolvable and calibration market targets use them; (f) fingerprint-relevant input lists have explicit canonical ordering/dedup; (g) #228 owns registries for forward id references; (h) calibration result rows echo market targets.
26. Round-10 OC P3 determinism cleanup — FIXED: (a) DiscountCurve's absent index sort key is exactly empty UTF-8 string; (b) strike secondary ordering uses canonical JSON serialization, not implementation-defined unit comparison; (c) CalibrationMarketTarget has an explicit named type declaration; (d) MarketSnapshot.diagnostics.unresolved_fields is lexicographically ordered and duplicate-free.
27. Round-11 final Codex consistency families — FIXED: (a) embedded calibration_result makes convergence/parameter linkage enforceable by the no-I/O kernel and V1 forbids non-converged override; (b) reporting/selected-curve/settlement currencies must equal ResolvedSwap currency because V1 has no FX contract; (c) VolQuote atm_definition exactly echoes container authority; (d) RiskResult V1 measure vocabulary is closed; (e) ProjectionInputsRef is typed/resolvable; (f) RiskResult and ModelCalibrationResult serialize separate warnings/errors; (g) curve_id derives from the full canonical curve payload, excluding only self id/fingerprint.

## B. Literal-implementer review (performed before push; round 2 repeated with focus areas)

Perspective: "If #227, #228, #229 were implemented literally by an engineer forbidden to ask what I meant, what would they still have to guess?" Remaining guesses classified. Round-2 and round-3 focus areas checked explicitly:

- Receiving same-day fixing methodology — NO GUESS: `FixingStore.same_day_rule` (§8.2) is the single authoritative typed location with rule_id/version/cutoff/cutoff-unit/timezone; unresolved → `SAME_DAY_FIXING_RULE_UNRESOLVED` (§8.4/§8.5). No snapshot-level duplicate to choose between.
- Computing/verifying fingerprints — NO GUESS on preimage: §15.4 excludes each object's own fingerprint/identity field from its own preimage while keeping child/input fingerprints as ordinary fields; hash/format details explicitly C (#227).
- Snapshot identity — NO GUESS: §6.3 derives `snapshot_id` from the canonical identity preimage including `captured_at` once, excluding self-referential fields; reassembly semantics explicit.
- PROJECTED fixing audit metadata — NO GUESS: §8.2/§8.3 state-dependent projection rules name exactly which fields are REQUIRED vs `NULL_WITH_REASON` per observation state, including `projection_inputs_ref` minimum replay link.
- ModelInput replay/provenance — NO GUESS: §9.6 lists every §15.3 provenance field plus `calibration_timestamp` linkage rule (equal to calibration `calibrated_at` when derived; `NULL_WITH_REASON` otherwise).
- MarketSnapshot physical payload location — NO GUESS: §6.2 has typed embedded `curve_set`, `fixing_store`, and `volatility_input` fields; §6.4 requires identity-ref equality and rejects id-only kernel requests.
- Unresolved curve/vol serialization — NO GUESS: §7.2 and §9.1 retain coordinates/semantics while carrying null numeric values plus structured reasons; NaN/sentinels/drop-and-bridge are forbidden.
- Economic numeric units — NO GUESS for audited fields: strike/forward, annuity PVBP, par rate, component PVs, calibration weights/errors, risk values, curve/fixing/vol values all carry unit fields/structures.
- NULL_WITH_REASON serialization — NO GUESS: §4.1 defines the wire union and structured reason categories; shorthand elsewhere refers to that exact representation.
- Exercise/settlement/model payload location — NO GUESS: §5.1 `RatesKernelInput` carries direct typed payloads; MarketSnapshot no longer holds dangling refs.
- Curve value semantics location — NO GUESS: §7.2 stores one `value_type` + `value_unit` at curve level; every V1 pillar shares them.
- PROJECTED fixing validity — NO GUESS: §8.2/§8.4 define complete PROJECTED as audit-only future/forecast alternative; incomplete/unapproved PROJECTED is rejected rather than half-null.
- Vol methodology authority — NO GUESS: §9.2 container methodology is authoritative and embedded quotes must match.
- Provenance scope — NO GUESS: §15.3 names the identity-bearing objects that require full envelopes and the exact leaf-fact inheritance rule.
- Curve selection — NO GUESS: §5.1 carries explicit discount/forecast curve ids and validates role/index compatibility; §12 echoes the same mapping.
- Pricing/risk input fingerprint — NO GUESS: §15.4 binds it to the exact RatesKernelInput.content_fingerprint, covering every PRESENT direct input.
- Same-day as-of instant — NO GUESS: §8.5 uses explicit `MarketSnapshot.captured_at`; kernel never reads system clock.
- Fixing observation vs publication — NO GUESS: §8 carries separate economic observation date and publication availability date/time; history/forecast rules use publication availability.
- Vol axis exact dates/authority — NO GUESS: §9.2–§9.3 preserve exact expiry and underlying start/end dates when known, and each quote coordinate must equal an authoritative container-axis entry.
- Reason-bearing optional values — NO GUESS: reviewed fields use ValueOrReason rather than bare nulls.
- Curve pillar source audit — NO GUESS: §7.2 carries source_column per pillar plus structured N/A.
- Forecast-only fixing provenance — NO GUESS: §8.6 permits an explicit empty PRESENT-source union without fabricating source data.
- Exercise underlying binding — NO GUESS: §10 binds underlying_product_id to the actual #224 ResolvedSwap.product_id.
- Calibration replay preimage — NO GUESS: §14.2 defines ModelCalibrationInput and §14.3 binds result inputs_fingerprint to its full content fingerprint.
- Calibration instrument executability — NO GUESS: each instrument embeds its owning versioned typed terms payload plus matching content fingerprint; digest alone is never executable input.
- Strike vocabulary — NO GUESS: one canonical enum token set; approval status lives in methodology state, not alternate token names.
- Settlement applicability — NO GUESS: cash-only method/methodology fields are structured NOT_APPLICABLE for PHYSICAL and required PRESENT for CASH.
- Valued-product vs underlying identity — NO GUESS: §5.1 carries valuation_product separately; derivative underlying_product_id binds to ResolvedSwap.product_id; Pricing/Risk results echo valuation_product.
- Headline result semantics — NO GUESS: §12.2 tagged headline contains semantics+value+unit(+currency/sign applicability); supplemental metrics cannot disagree.
- Risk optional identities — NO GUESS: §13.2 uses ValueOrReason for vol/exercise/settlement/model/calibration ids, mirroring kernel applicability.
- Curve schema/anchor semantics — NO GUESS: one schema_version authority; reference_date carries explicit reason when differing from valuation_date.
- Vol dimensionality/node identity — NO GUESS: SURFACE/CUBE cardinality is disjoint; VolNodeKey canonically identifies each quote; duplicate keys fail.
- ATM applicability — NO GUESS: container atm_definition_id uses ValueOrReason and can be genuinely NOT_APPLICABLE.
- Calibration market target — NO GUESS: typed VOL_QUOTE_NODE_V1 target resolves only inside embedded VolatilityInput; result echoes it.
- Fingerprint list ordering — NO GUESS: §15.4 defines canonical ordering/dedup for every fingerprint-relevant input-list family.
- Forward refs — NO GUESS on ownership: #228 owns versioned target registries for construction/calendar/observation refs.
- Canonical sort sentinels — NO GUESS: DiscountCurve missing index uses exact empty-string sort key; strike secondary order is canonical-JSON lexicographic.
- Calibration target DTO type — NO GUESS: CalibrationMarketTarget has one named declaration shared by input/result.
- Snapshot unresolved-field order — NO GUESS: canonical field-path lexicographic order, duplicate-free.
- Calibration convergence — NO GUESS: calibration-derived ModelInput embeds the exact ModelCalibrationResult; params/ids/timestamp must match and only CONVERGED is consumable in V1.
- Currency compatibility — NO GUESS: V1 has no FX path; reporting, selected curves, CurveSet base, and settlement currency must equal ResolvedSwap currency.
- ATM authority — NO GUESS: per-quote atm_definition is an exact echo of container atm_definition_id.
- Risk vocabulary — NO GUESS: exact V1 tokens are DV01/PV01/DELTA/GAMMA/VEGA.
- Projection replay target — NO GUESS: typed ProjectionInputsRef resolves sibling CurveSet/ForwardCurve plus #228 observation-rule registry.
- Result warning/error wire shape — NO GUESS: separate fields in Pricing/Risk/Calibration results.
- Curve identity — NO GUESS: full canonical price-affecting payload participates in curve_id.

Classification:

- A. Contract defect → FIXED NOW. Round-2 fixes: (1) same_day_rule embedded in FixingStore with §8.4/§8.5/object-graph/checklist/handoff consistency; (2) §15.4 non-recursive preimage + per-object fingerprint comments + replay comments; (3) §6.3 identity rewrite + removal of `digest + disambiguator` / `same rule as §6.3` / `content-derived or allocated` staleness across CurveSet/FixingStore/VolatilityInput/ModelInput/calibration ids; (4) §8.2/§8.3 projection fields + state rules + minimum `projection_inputs_ref`; (5) §9.6 ModelInput §15.3 completion + calibration-timestamp rule.
- B. Intentionally unresolved methodology → explicit RED in §17. Complete list: RED-01, RED-02 (D1–D13/E1–E6 carried), RED-225-C1/C2/C3/C4, RED-225-F1/F2, RED-225-V1/V2, RED-225-E1, RED-225-S1, RED-225-M1/M2, RED-225-R1. Each names owner (Sophira/Eddy) and evidence requirement. Round-2 owner decision recorded: keep `RED-225-*` namespace (§17). No silent value; no cutoff/timezone/projection/model choice made.
- C. Belongs to a later issue → explicit handoff in §18. Complete list: #226 (cache keys, concurrency, benchmarks, QuantLib isolation, canonical JSON formatting + hash details), #227 (DTO serialization format incl. preimage implementation, CI), #228 (schedule/fixing/calendar/curve mechanics incl. same_day_rule + projection-field consumption), #229 (swap kernel), #230+ (reconciliation, swaption/callable/accrual methodology fills). No implementation detail deferred without an owner.

No uncategorized guess remains.

---

## C. Validation performed (this round; round 2 correction round)

Round 1:

- Fetched latest `main`; verified HEAD `b7f17d08b172a4e37231a2801efea106d98b961c` (PR #241 merge) present in ancestry.
- Read live Issue #222, #223 + `docs/31`, #224 + `docs/32`, #225, and repo `AGENTS.md` before writing; did not rely on remembered copies.
- Branch `arch/225-rates-market-model-result-contracts` cut from `main`.
- One docs file added; no production/runtime/schema/build/test/tool/workflow code changed.
- `git diff --check` clean; full diff vs `main` inspected (single file).
- Grep for stale/ambiguous terms (`RESOLVED` as a value, `TODO`, naked `double vol`, `QuantLib`, `Bloomberg` as methodology, `standard practice`) reviewed: no silent methodology adoption; Bloomberg/QuantLib appear only as source vocabulary or explicit non-authority statements.
- #223 ownership boundary, #224 SwapTrade/ConventionSet/ResolvedSwap semantics, RED-01 open status, and #226–#229 issue boundaries confirmed preserved.

Round 2 (this correction, on the existing branch — no new branch):

- Started from reviewed HEAD `2fcfe7f5572d463ac0723d2d55b799479f3a7ec2` (PR #248); modified ONLY `docs/33_rates_market_model_result_contracts_225.md`.
- Applied all 5 accepted Codex P2 findings as representation/consistency fixes: (1) same_day_rule embedded in FixingStore; (2) §15.4 non-recursive preimage; (3) §6.3 identity coherence + stale-identity-language audit; (4) projection fields + state rules; (5) ModelInput §15.3 completion. No production methodology value chosen; RED-01, RED-02, all RED-225-* remain open; only new owner decisions are the `RED-225-*` namespace retention and snapshot-identity-includes-captured_at-once rule.
- Grep for stale: `digest + disambiguator`, contradictory `idempotent`, detached `same_day_rule`, mentioned-but-not-shaped projection fields, recursive fingerprint wording, `same rule as §6.3`, incomplete ModelInput provenance — all clean after correction.
- Re-ran same-defect-family audit (§A) and literal-implementer review (§B) with round-2 focus areas; no uncategorized guess remains.
- Full diff against PR HEAD and against main inspected; `git diff --check` clean; only the intended docs file changed; #223/#224 and #226–#229 boundaries preserved.

Round 3 (Sophira takeover after OpenCode session failure; same existing branch):

- Started from Codex-reviewed HEAD `d1a22fa7cf440b5caf6bd5f3cf25dc2da81ee02c`; modified ONLY this document.
- Applied all five accepted round-3 P2 representation fixes plus same-family audit: embedded market payload fields; representable unresolved curve/vol nodes; strike/forward unit payloads; annuity PVBP unit; additional component/par-rate/calibration unit gaps; typed `ValueOrReason<T>` semantics for structured nulls.
- No production methodology value resolved. RED-01, RED-02, RED-225-* remain open; #223/#224 and #226–#229 ownership boundaries unchanged.
- Because this correction was written through the GitHub connector rather than a local checkout, validation used exact-head/blob guards, one-file replacement, duplicate-replacement assertions, and a diff-equivalent whitespace scan; no runtime/build/test file was touched. Fresh CI/Codex review is required after push.

Round 4 (Sophira, six Codex P2 findings on `b630a34d...`):

- Accepted all six as contract defects and fixed them in this document only: explicit RatesKernelInput payload composition; curve-level value metadata; internally consistent PROJECTED state + date path; VolatilityInput methodology authority; complete provenance envelopes.
- Same-family audit also removed the dangling MarketSnapshot `model_input_ref` pattern before Codex had to report it, and gave ExerciseTerms / SettlementTerms content identities for result replay.
- No production methodology value resolved; RED-01, RED-02, RED-225-* remain open.

Round 5 (Sophira, four Codex P2 findings on `95e81abf...`):

- Accepted all four as valid contract defects: missing kernel curve selection, incomplete pricing/risk input fingerprint coverage, collapsed fixing observation/publication date semantics, and vol axes dropping exact calendar dates.
- Fixed all four in this document only and ran same-family audit against output linkage/replay/RED wording/acceptance text.
- No curve methodology, publication schedule/cutoff, or vol coordinate methodology VALUE was chosen; only the fields/ownership needed to carry future approved values were made explicit.

Round 6 (Sophira, five Codex P2 findings on `b2b5a5ce...`):

- Accepted all five: reason-bearing bare-null fields, missing per-pillar source_column, forecast-only FixingStore source union, invalid invented resolved_swap_id reference, and undefined calibration replay preimage.
- Added `ModelCalibrationInput` as a minimal deterministic replay contract without selecting model/objective/optimizer/tolerance values or inventing future swaption economics.
- No production methodology value resolved; all RED-01/RED-02/RED-225-* remain open.

Round 7 (Sophira, three Codex P2 findings on `77013c3a...`):

- Accepted all three: calibration instrument fingerprint without executable terms payload; inconsistent strike-dimension token names; cash-only settlement method mandatory for PHYSICAL.
- Fixed payload location, canonical token vocabulary, and CASH-vs-PHYSICAL applicability only. No product economics, strike methodology, or cash settlement methodology value was selected.
- Same-family audit confirms no second calibration instrument authority and no reserved-suffix strike tokens remain.

Round 8 (Sophira, three Codex P2 findings on `837e3bb6...`):

- Accepted all three: derivative trade identity collapsed into underlying ResolvedSwap.product_id; ambiguous PricingResult headline semantics; RiskResult optional identities lacking applicability encoding.
- Added valuation_product identity at the kernel boundary, tagged authoritative headline semantics, and structured RiskResult input identity applicability. No product pricing methodology or market value was chosen.

Round 9 (Sophira using independent OC audit of HEAD `691e919b...` plus Codex review):

- Accepted all five Codex findings and OC's SF1–SF3 same-family findings; SF4 (CurveSet assembled_by provenance style asymmetry) is explicitly non-blocking and left unchanged to avoid scope churn.
- Changes are representation/determinism only: one curve schema version, serialized anchor reason, disjoint SURFACE/CUBE shape, ATM N/A state, typed VolNodeKey calibration target, canonical list ordering/dedup, #228 ref-registry ownership, calibration-result target echo.
- No RED value, market convention, interpolation/smile/model/calibration methodology, or product economics was selected.

Round 10 (Sophira, OC re-audit P3 cleanup on `ec5c84de...`):

- OC reported no P1/P2 and four deterministic-wire P3s.
- Closed all four without methodology changes: explicit DiscountCurve sort sentinel, canonical JSON strike sub-order, named CalibrationMarketTarget type, deterministic unresolved_fields ordering.
- No additional architecture scope added.

Round 11 (Sophira, reconciled overlapping Codex reviews on `ec5c84de...` and `5acdef2c...`):

- Timing overlap exposed eight unresolved P2 findings, not four; all eight were independently verified against current HEAD and fixed together.
- Fixes are contract-completeness/fail-closed only: no FX methodology, calibration override policy, risk bump convention, curve interpolation methodology, or market convention was invented.
- V1 deliberately chooses fail-closed constraints where supporting a missing capability would require a new input contract.

---

*End of Issue #225 deliverable. Architecture/schema document only; no production functionality.*
