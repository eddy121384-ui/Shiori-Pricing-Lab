# UST OVME Case A — Phase 2 reconciliation

**Issue:** #153 (Phase 2, Case A only)  
**Status:** blocked deterministic replay; **not** a methodology approval and **not** completion of #153.

## Evidence and redaction

The committed artifact is `docs/evidence/ust_ovme_case_a_redacted.json`. It preserves the
manual labels and values supplied by Eddy, but replaces the underlying with
`UST_CASE_A_UNDERLYING_REDACTED_V1`. The screenshot, displayed security description,
terminal/user metadata and raw Bloomberg payload are not committed. `Price (Unit)` remains null
because its verbatim fraction was not present in the supplied text; reading a missing crop by
telepathy would be a particularly poor data adapter.

The observation is `2026-08-04T10:37:00+08:00`; trade settlement is `2026-08-05`; expiry is
`2026-10-04T06:20:00+08:00`; and the displayed delivery rule is one business day after expiry.
No approved business-calendar contract is present, so the exact option-settlement date is
`UNRESOLVED`.

## Workstation DAPI capture

Cloud DAPI was not attempted: this environment is not Eddy's logged-in Bloomberg workstation.
Run the following from the repository root on that workstation (the output directory is ignored):

```bash
python tools/ust_case_a_reference_capture.py \
  --identifier '<REAL_ISIN_OR_CUSIP>' \
  --output shiori_probe_output/ust_case_a_reference_capture.json
```

The helper sends one read-only `ReferenceDataRequest` for exactly the six PR #141-confirmed
mnemonics: `CPN`, `CPN_FREQ`, `ISSUE_DT`, `MATURITY`, `FIRST_CPN_DT`, and `DAY_CNT_DES`. It writes
no identifier or raw response and records every field's mnemonic, existing adapter symbol,
retrieval timestamp, redacted source key, normalized value, and independent
`RETURNED`/`ABSENT`/`FIELD_EXCEPTION` status. `DAY_CNT_DES` is display-only and is not coerced to a
typed day count. No approved mnemonic currently exists for last coupon, settlement convention,
business-day convention or typed day count; `REDEMPTION_VALUE` was previously confirmed
`BAD_FLD`, so none is broad-probed or guessed.

## Coupon schedule and qualification

Complete `BondReferenceData` cannot yet be constructed. Consequently the existing QuantLib bond
adapter cannot formally generate the coupon schedule or accrued interest. Coupon dates in
`(2026-08-04, 2026-10-04]`, valuation-date AI, expiry AI and forward-settlement AI all remain
`UNRESOLVED`. The case is only a no-coupon *candidate*:

`CASE_A_NO_COUPON_BEFORE_EXPIRY = UNRESOLVED`

After the local capture, missing typed day count, last coupon, redemption, and settlement/calendar
terms still require an approved source or owner decision before using the existing schedule/accrual
implementation. A bond description is not a coupon schedule.

## Deterministic Shiori replay

The prospective reviewed standalone inputs are Call/Buy, clean spot and strike `98.578125`,
explicit clean forward `98.515625`, and direct decimal annual `PRICE_VOL = 0.03942`. The valuation,
pricing and expiry roles are explicit in the evidence. Normal Yield Vol `87.000 bp` and Lognormal
Yield Vol `19.60%` are retained as observation-only values; MODE_1/MODE_2 is not enabled.

No normalized request was constructed and no engine was invoked. The minimum blockers are:

1. exact option settlement cannot be obtained without an approved business calendar;
2. reporting date is not evidenced;
3. no approved `OPTION_DISCOUNT_CURVE` input exists; and
4. pending complete terms prevent the forward-settlement accrued-interest input.

The displayed `MMkt 3.704%` is not renamed or converted into
`CONTINUOUS_ZERO_RATE`. Therefore pricing status is `BLOCKED`, both replay hashes and Shiori
premium outputs are null, and no test-only curve sensitivity is represented as true OVME
reconciliation.

## Premium reconciliation

| Output | OVME observation | Shiori output | Unit mapping status | Difference | Conclusion |
|---|---:|---:|---|---:|---|
| Price (Unit) | not supplied | blocked | `DISPLAY_BUCKET_UNRESOLVED` | N/A | Verbatim crop value required; not guessed |
| Price (%) | 0.6159 | blocked | `UNIT_MAPPING_UNRESOLVED` | N/A | No formula or notional convention proved |
| Price (Total) | 6.07 USD | blocked | `POSITION_SCALING_UNRESOLVED` | N/A | Position display `1.00` does not prove scaling |
| Shiori premium per 100 | N/A until mapped | blocked | `UNIT_MAPPING_UNRESOLVED` | N/A | No successful run |
| Shiori total premium | N/A until mapped | blocked | `POSITION_SCALING_UNRESOLVED` | N/A | No successful run |

## Disposition

| Topic | Finding | Classification |
|---|---|---|
| Forward | The explicit observed clean forward is normalized and ready as an input, but the blocked request has not replayed it. Annex reconstruction lacks complete terms and a Bond Reference Curve. Repo `3.77837%` is evidence only. | `DATA_ROUTE_MISSING`; repo methodology is `OWNER_DECISION_REQUIRED` |
| Vol | Direct `PRICE_VOL` is unambiguously normalized to `0.03942`, but the full replay is blocked upstream. Yield-vol values are not converted. | direct-input contract `ALREADY_ALIGNED`; replay `UNRESOLVED`; conversion `OWNER_DECISION_REQUIRED` |
| Discounting | `MMkt 3.704%` is observed/unmapped. Shiori standalone currently needs pricing→option-settlement divided by pricing→reporting DFs, while Annex specifies the expiry horizon. Calendar, curve semantics and governing horizon are unproved. | `OWNER_DECISION_REQUIRED` |

### Owner decisions required

1. Approved option-settlement calendar and the exact settlement date.
2. Case A reporting-date role.
3. Approved Option Discount Curve source/transformation and whether expiry or
   option-settlement/reporting is authoritative for this UST standalone slice.
4. Approved routes/mappings for any still-missing typed bond terms; no Bloomberg semantic is
   promoted merely because a display field exists.

Case B is deliberately not started. Issue #153 must remain open for Case B and the decisions above.
