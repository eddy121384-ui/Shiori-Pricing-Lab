# Bloomberg bond profile probe evidence

This note records Eddy's Bloomberg workstation results for Issue #161 / Draft
PR #163. It is evidence for deterministic, fail-closed mappings; it is not
fabricated market data and does not authorize issuer inference.

## Approved exact-value mappings

| Bloomberg field | Only accepted value | Evidence established |
| --- | --- | --- |
| `CPN_TYP` | `FIXED` | plain fixed coupon |
| `INFLATION_LINKED_INDICATOR` | `N` | not inflation linked |
| `CONVERTIBLE` | `N` | not convertible |

Every other value, missing/blank field, absent response, or field exception
fails closed. `DAY_CNT_DES` must equal the manually selected profile's token:
`US_CORPORATE` uses `30/360`; `GERMAN_GOVT` and `UST` use `ACT/ACT`.

## Evidence that must not be promoted

`SECURITY_TYP` returned `US GOVERNMENT` for the UST, `GLOBAL` for the corporate
bond, and `EURO-ZONE` for the German government bond. Those strings do not
safely classify the issuer, so profile selection remains a trader input.

`IS_AMORTIZING`, `AMORT_TYP`, `REDEMP_TYP`, and `SCHED_TYP` all returned
`BAD_FLD`. `MTG_TYP` returned `NOT_APPLICABLE` for the two probed bonds.
`PRINCIPAL_FACTOR` returned `1.000000` for both, which establishes only that
principal has not yet been reduced; it does not establish that no future
amortization schedule exists. Consequently `PRINCIPAL_FACTOR = 1` is never
bullet evidence, non-UST profiles remain blocked without independent
bullet/amortizing evidence, and no additional Bloomberg mnemonic is guessed.
