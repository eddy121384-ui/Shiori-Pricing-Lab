# Bloomberg OVME / SWDF source mapping (manually observed)

> **Scope and restrictions (read first)**
>
> - This file records **manually observed** Bloomberg OVME / OVME F / SWDF
>   source semantics.
> - It is **not** a Bloomberg API field specification.
> - Bloomberg **UI labels must not be treated as API mnemonics**.
> - Unknown Bloomberg fields, curve transformations, repo mechanics,
>   timestamps, units, or conventions **must not be guessed**.
> - This document **does not authorize** live Bloomberg connectivity or any
>   change to pricing methodology.

## Purpose

Record verified Bloomberg UI/source semantics that Eddy and Sophira have
manually observed and approved, so that a **later, separately approved**
Bloomberg export or API adapter can map Bloomberg data into Shiori's existing
normalized standalone-option input without guessing.

This advances Issue #94 (`[P1-01][RED][HUMAN GATE]`). It is RED methodology
documentation only. It does not close #94.

The "Existing Shiori destination" column names contracts that already exist on
`main`. They were confirmed against:

- `examples/standalone_option_case.json` (the normalized input envelope);
- `src/shiori_pricing_lab/data/bli_snapshot.py` (`BLICurvePurpose`,
  `BLICurveRateBasis`);
- `src/shiori_pricing_lab/data/bli_benchmark_quote.py` (`BLIBenchmarkQuote`,
  `BLIBenchmarkQuoteSide`, `BLIBenchmarkSourceType`);
- `src/shiori_pricing_lab/data/bli_standalone_option_request.py`.

No field named below is invented; where an observation has no existing
destination, its status says so.

## Status vocabulary

Only these statuses are used:

- **CONFIRMED** — manually observed and agreed; destination already exists.
- **PARTIALLY_CONFIRMED** — meaning observed, but an exact unit, compounding,
  interpolation, date treatment, or API representation is still unverified.
- **UNMAPPED** — observed, but deliberately **not** mapped to any Shiori input
  pending later human approval.
- **NOT_USED** — observed, but not consumed by the current Phase 1 path.
- **FUTURE_API_TBD** — deferred to a later, separately approved Bloomberg
  export/API slice.

## Canonical mapping table

| Bloomberg function / source | Bloomberg UI label or observation | Observed meaning | Existing Shiori destination | Unit / transformation | Status | Evidence / limitations |
|---|---|---|---|---|---|---|
| OVME | Underlying identifier | The cash bond underlying the option | `bond_option.underlying_isin`, and the matching `bond_reference_data_universe[].isin`; benchmark `BLIBenchmarkQuote.underlying_id` | Identifier only. A committed golden case may replace the real identifier with one stable anonymized identifier | CONFIRMED | Identifier semantics only; no bond static terms are implied by this row |
| OVME | Und. Price | Underlying bond clean price | `bond_quote.clean_price_per_100` | Treasury fractional price (32nds / sub-32nds) must be converted to decimal price per 100 | CONFIRMED | Conversion is a display→decimal transcription, not a model transformation |
| OVME | Underlying quote side = Mid | The side of the **underlying** price input | `bond_quote.quote_side = MID` | Enumerated side | CONFIRMED | This is the **underlying input side**, not automatically an executable option MID quote. Bond-quote side uses the existing Treasury-FTP quote-side enum; it is distinct from `BLIBenchmarkQuoteSide` |
| OVME | Option terms: European, Call/Put, Buy/Sell, strike, expiry, position/notional, delivery delay, currency | Standalone option economic terms | `bond_option.exercise_style = EUROPEAN`, `option_type` (CALL/PUT), `position` (BUY/SELL), `strike_price`, `expiry_date`, `notional`, `settlement_lag_days`, `currency`; `payoff_basis = PRICE`, `settlement_type = CASH` for the supported slice | Direct field-to-field; no unit change | CONFIRMED | Maps **only** to already-existing standalone request fields. No new field is invented. `position` is informational for the current absolute fair-premium path |
| OVME | Price Volatility | Direct price volatility input | `volatility_input.volatility`, with `volatility_input.volatility_basis = PRICE_VOL` | Bloomberg percentage display → decimal (e.g. `x%` → `x / 100`) | CONFIRMED | Direct `PRICE_VOL` route only; no yield-vol conversion is in scope |
| OVME | Price (Unit) | Benchmark option premium, per unit | `BLIBenchmarkQuote.premium_per_100` | Treasury fractional premium → decimal per 100 | CONFIRMED | Benchmark premium, not a model output |
| OVME | Price (Total) | Benchmark option premium, total | `BLIBenchmarkQuote.total_premium` | Observed-case position convention: **Position 1.00 represented face amount 1,000** in the observed Treasury case | PARTIALLY_CONFIRMED | The 1.00 → 1,000 face convention is **observed-case evidence, not a universal API contract**. The comparison contract does not derive total from per-100 or reconcile the notional relationship |
| OVME | Price (%) | Premium relative to underlying clean price | — (none) | — | NOT_USED | The observed `%` display represented premium relative to underlying clean price and is **not** Shiori `premium_per_100`. Do not map it there |
| OVME / OVME F | Forward | Reconciliation / evidence forward level | — (evidence only; not a normalized market-data input) | — | CONFIRMED | Confirmed as a reconciliation output / evidence field, **not** an input. OVME F experiments showed **zero forward-contract value when Contract Rate equaled the displayed Forward**. Delivery delay shifts the relevant bond delivery settlement date |
| OVME | USD Rate / MMkt | Discount rate source for the option leg | Provenance of `curve_points` where `curve_purpose = OPTION_DISCOUNT_CURVE` | — | PARTIALLY_CONFIRMED | Source curve confirmed as SWDF curve **S490: USD SOFR (vs. FIXED RATE)**. Exact Bloomberg interpolation, compounding, date treatment, and API representation remain unverified. **Do not** claim the UI rate itself is already a continuous-zero node |
| SWDF S490 | Stripped Curve — Zero Rate | Zero-rate nodes of the option discount curve | Candidate/provenance source for a future `OPTION_DISCOUNT_CURVE` mapping; no direct normalized `BLICurvePoint` mapping is approved yet | Bloomberg percent display → decimal (transcription only; the target normalized basis is not yet decided) | PARTIALLY_CONFIRMED | Curve identity, MID side, displayed zero-rate nodes, and intended option-discount provenance were observed, but the representation/compounding and required transformation remain unverified. The Bloomberg displayed Zero Rate **must not** be treated as Shiori `CONTINUOUS_ZERO_RATE` without a separately approved RED basis/conversion decision. **No executable golden-case JSON may consume these rates yet** |
| SWDF S490 | Stripped Curve — Discount | Discount factors for the same curve | — (cross-check evidence only) | — | PARTIALLY_CONFIRMED | Recorded as cross-check evidence for the same option discount curve. **Do not** create a second competing curve-input contract |
| SWDF | Curve Side = Mid | Curve provenance side | — (provenance metadata; no curve-side field exists) | — | CONFIRMED | Provenance metadata only. Explicitly **distinct** from the active option benchmark side (`BLIBenchmarkQuote.quote_side`). The curve-point contract has no side field today |
| SWDF | Interpolation = Step Forward (Cont) | Bloomberg curve-construction method | — (observed metadata; no equivalence claimed) | — | CONFIRMED | Recorded as observed curve-construction metadata. **Do not** change Shiori interpolation or claim equivalence without a separately approved RED methodology decision |
| OVME | Repo | Repo rate driving the forward | — (deliberately none) | — | UNMAPPED | Repo Rate Source was observed as SWDF with **no explicit Bond Override** present. Controlled experiments showed the displayed repo rate and forward changed when the USD rate was changed. Exact curve, spread, security adjustment, compounding, and transformation remain unknown. **Prohibited** from mapping into `credit_spread_input`, `OPTION_DISCOUNT_CURVE`, or `BOND_REFERENCE_CURVE` without later human approval |
| GOVY | Carry / Roll / C+R | Carry and roll analytics | — (none) | — | NOT_USED | Analytics outputs in basis points; **not** the OVME annualized repo rate |
| OVME | Normal Yield Vol / Lognormal Yield Vol | Yield-basis volatility | — (none for Phase 1) | — | NOT_USED | Not used for the first direct `PRICE_VOL` golden case. **Do not** add yield-vol conversion scope |
| Bloomberg | API / Excel mnemonics | Programmatic field names | — | — | FUTURE_API_TBD | **Do not** infer API fields from UI labels |
| OVME / SWDF | Timestamps | Distinct time concepts | `BLIBenchmarkQuote.source_as_of` and `retrieved_at` (kept separate); snapshot `as_of_timestamp` | — | PARTIALLY_CONFIRMED | Keep `source_as_of` and `retrieved_at` separate. OVME trade/calculation time, live header observation time, curve settle date, and bond settlement date are **distinct** concepts. **Do not** fabricate one from another |

## Known open gaps

Before an executable, sanitized golden-case JSON can be created, the following
remain unresolved and require separately approved human/RED decisions:

- exact Bloomberg API/export field names (no UI-to-API inference);
- exact S490 zero-rate representation and compounding basis expected by Shiori;
- exact repo-rate derivation and any security-specific adjustment;
- the approved **Bond Reference Curve** / financing mapping for this golden
  case (the option discount curve source is partially confirmed as SWDF S490;
  the bond reference curve / repo-forward financing mapping is not);
- remaining bond static terms and timestamp evidence needed before creating an
  executable golden-case JSON.

## Not authorized by this document

- no Bloomberg Desktop/API adapter;
- no CSV parser;
- no schema or validation changes;
- no pricing-engine change;
- no repo curve implementation;
- no yield-vol conversion;
- no example or fixture claiming real-market validation;
- no reopening PR #113 or Issue #100.
