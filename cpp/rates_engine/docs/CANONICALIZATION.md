# Canonical JSON and fingerprint contract (C++ side)

Owner issue: **#227** (foundation). Contract authority: `docs/33_..._225.md` (market/model/result
contracts) and `docs/34_..._226.md` (build, isolation, concurrency, caching, benchmark). This file
records how the C++ DTO layer implements those rules; it does not create new ones.

The canonical form is a **cross-language contract**: the Python application
(`src/shiori_pricing_lab/rates_bridge/canonical.py`) and the C++ engine must produce byte-identical
canonical documents and identical SHA-256 digests. A fingerprint stored by one side must be verifiable
by the other.

## 1. Byte rules

1. UTF-8, no BOM, no trailing newline.
2. Object keys are sorted by **UTF-8 byte order** (comparison of `std::string_view`, i.e. unsigned
   byte comparison). `"z"` (0x7A) sorts before `"é"` (0xC3 0xA9); a signed-`char` comparison would
   invert that and silently change every fingerprint of a non-ASCII document.
3. No whitespace anywhere. Separators are exactly `,` and `:`.
4. Duplicate object keys are **refused**. A document that contains the same key twice has no single
   meaning, so it is not canonicalized at all.
5. Members of an **array** keep their input order; members of an **object** never do. Arrays whose
   order is semantic (e.g. `curves`, `fixing_store.entries`, `pillars`) therefore must not be sorted by
   the serializer.

## 2. String escaping

Minimal escaping only:

| input | output |
| --- | --- |
| `"` | `\"` |
| `\` | `\\` |
| 0x08 | `\b` |
| 0x0C | `\f` |
| 0x0A | `\n` |
| 0x0D | `\r` |
| 0x09 | `\t` |
| other < 0x20 | `\u00xx` with **lowercase** hex |
| >= 0x80 | emitted as literal UTF-8 (never `\u`) |
| invalid UTF-8, lone surrogates | refused |

Escaping is reversible: parsing an escaped document and re-serializing it reproduces the same bytes.

## 3. Numbers

- Integers (`int64`) are emitted as plain decimal.
- Floating-point values are emitted with the **ECMAScript `Number::toString`** rule: shortest decimal
  form that round-trips to the same `double`.
- Anchors enforced by tests:

  | value | canonical |
  | --- | --- |
  | `100.0` | `100` |
  | `-0.0` | `0` |
  | `0.0425` | `0.0425` |
  | `0.000001` | `0.000001` |
  | `1e-7` | `1e-7` |
  | `1e20` | `100000000000000000000` |
  | `1e21` | `1e+21` |
  | `123456789012345680.0` | `123456789012345680` |

- `NaN` and `±Infinity` are refused: they have no JSON representation and must never appear in a
  contract document.
- The numeric policy is part of the build: `-ffp-contract=off` (GCC/Clang), `/fp:precise` (MSVC). Fast
  math is forbidden, and `BuildIdentity.numeric_flags` reports what was actually used so a
  non-comparable build is detectable.

## 4. Digests

- Digest = SHA-256, lowercase hex, exactly 64 characters.
- `fingerprint_of(value)` is defined as `SHA-256(canonical_bytes(value))`. It is a function of the
  **bytes**, not of the in-memory representation.
- Document-level identity rules (`*_id` and `content_fingerprint` preimages) are defined by #225 and
  implemented in `src/dto/*_identity` helpers:
  - a document's `*_id` preimage **excludes its own `*_id` and its own `content_fingerprint`**;
  - a document's `content_fingerprint` preimage **excludes only the `content_fingerprint` field** and
    therefore includes the derived id;
  - `ResolvedSwap` and `RatesKernelInput` have **no id**; they are identified by fingerprint only.

## 5. Immutability of a published request

`FrozenKernelInput` owns the validated DTO together with the canonical bytes and the content
fingerprint. `PublishedKernelInput` hands out only immutable access; there is no mutable accessor.
Verification that a stored fingerprint still matches is therefore possible without trusting the
caller, and a request cannot be edited after it was fingerprinted.

## 6. Cache keys

A cache key is a function of explicit identity only: input fingerprint, engine version, methodology id
and version, QuantLib version, QuantLib macro configuration, dependency acquisition mode, dependency
baseline + triplet, and build numeric flags. An empty (unknown) component makes the key
**non-reusable**; it is refused at construction rather than hashed as an empty string. A digest that
does not match its components is refused.

## 7. Open vocabularies (NOT invented by #227)

#225 leaves several value vocabularies open. #227 must not invent their values, so the C++ layer
carries them as open text whose V1 value is `UNRESOLVED` (or as an opaque payload) and refuses an
unknown value where a value is required:

- `sign_convention`, `pv_sign_convention`, `component_pvs[].sign_convention_ref`
- `replay.tolerance`
- `compounding`, `day_count`, `accrual_boundary`
- interpolation / extrapolation method ids
- same-day rule fields
- `revaluation_rule_id` / `revaluation_rule_version`
- cash settlement methodology fields
- `model_version`
- underlying start rule

Volatility internals, `ResolvedSwap.resolved`, `ModelInput.payload` and the model/calibration
envelopes are **documented placeholders**; see `PHASE0_CONTRACT_MATRIX.md`.

## 8. Lane A / Lane B separation

- The row-level vocabulary of `assumptions` and `diagnostics` is **Lane A** and is a map of JSON
  scalars only. **Lane B** runtime telemetry (`RATES_RUNTIME_TELEMETRY_V1`) is out of band: it is
  never a member of `PricingResult`/`RiskResult` and does not participate in any fingerprint.
- QuantLib version, macro configuration and build identity are Lane B (G-2/G-3): they are reported and
  participate in the cache key, never in a document's content fingerprint.
