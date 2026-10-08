# Phase 0 — contract-to-implementation matrix (Issue #227)

**Scope of #227.** A minimum real C++20 Rates Engine foundation: a reproducible, testable,
Python-callable shell with deterministic DTO/JSON contracts and mechanically enforced QuantLib
isolation. #227 contains **no swap pricing, no risk, no calibration, and no market data**. Success is
defined as: *the engine shell is safe and reproducible enough for #228/#229 to build on.*

**Authority order used while implementing** (highest first): Eddy's latest explicit instruction →
merged contracts (#223–#226) → the live text of Issue #227 → existing repository behaviour → #227
engineering decisions recorded here.

**No new financial methodology was decided.** Everything numeric in this tree is a contract mechanism
(canonical bytes, digests, identity preimages, refusal behaviour). Where #225 leaves a value vocabulary
open, #227 carries it as open text and does not invent a value (see
`CANONICALIZATION.md` section 7).

## 1. Matrix

| # | Requirement (authority) | Implementation | Evidence |
| --- | --- | --- | --- |
| 1 | C++20, extensions off, CMake ≥ 3.25, presets as the single configuration authority (#226 §2) | `CMakeLists.txt`, `CMakePresets.json`, `cmake/ShioriBuildOptions.cmake` | CI `cpp-build` configures and builds through a preset only |
| 2 | Out-of-source builds only | in-tree-build guard in `CMakeLists.txt` | guard fails configuration if `build/` is inside the source tree |
| 3 | Numeric policy: no fast-math, no contraction, warnings-as-errors for Shiori sources (#226 §3.5) | `cmake/ShioriBuildOptions.cmake` (`SHIORI_NUMERIC_FLAGS`) | `unit/test_engine_operations.cpp` asserts the reported flags contain no fast-math; CI builds with `SHIORI_WARNINGS_AS_ERRORS=ON` |
| 4 | Pinned dependencies (vcpkg baseline, QuantLib 1.43, nlohmann-json, GoogleTest, Google Benchmark) (#226 §2.2) | `vcpkg.json` | CI clones vcpkg at the exact baseline commit; `adapter/test_settings_guard.cpp` asserts the QuantLib runtime identity is 1.43 |
| 5 | QuantLib is isolated behind one adapter; `shiori_rates_dto` has zero QuantLib dependency (#226 §3) | target split in `CMakeLists.txt`; `include/shiori_rates/quantlib_adapter/*` | `tools/isolation_guard.py` + CI `cpp-isolation-guard` (self-test, real scan, negative control on the real tree) |
| 6 | Production allow-list is exactly `src/quantlib_adapter/`; test allow-list is `tests/adapter/` + `tests/defaults/`; benchmarks may not include QuantLib (#226 §3.9) | `tools/isolation_guard.py` tier classifier | 12 self-test cases including raw-string and line-continuation traps |
| 7 | No Shiori methodology may come from a QuantLib default; the evaluation date is always explicit and restored exactly (#226 §3.6/§3.7) | `QuantLibSettingsGuard` in `src/quantlib_adapter/adapter.cpp` | `defaults/test_quantlib_default_policy.cpp` observes QuantLib's own `Settings` before/inside/after the guard; raw-null is captured and restored as raw-null |
| 8 | Failed restore poisons the adapter and every later operation fails closed | poison flag in `adapter.cpp`, hooks only in `quantlib_adapter/testing.hpp` | `adapter_poison/test_fail_closed.cpp`; the poison hooks are not reachable from any production header |
| 9 | Serialized QuantLib execution; parallel pricing disabled under the initial posture (#226 §5.2) | `SerializationGate` + `kProductionParallelPricingEnabled = false` | `concurrency/test_serialization_gate.cpp` observes max occupancy 1 across 4 threads × 60 acquisitions |
| 10 | Deterministic cache key with an explicit identity component set; unknown component ⇒ non-reusable (#226 §7.2/§7.6/§8.2) | `dto/immutable.hpp`, `src/dto/immutable.cpp` | `cache/test_cache_key.cpp` (every empty component refused, every meaningful difference changes the digest, tampered digest refused) |
| 11 | Canonical JSON: byte-order keys, no whitespace, minimal escaping, ECMAScript numbers, SHA-256 (#226 §5.2, #225) | `dto/canonical.hpp`, `src/dto/canonical.cpp`, `src/dto/sha256.cpp`, `src/dto/fingerprint.cpp` | `dto/test_canonical_contract.cpp`, `regression/test_contract_regressions.cpp` (FIPS 180-4 vectors, branch boundaries) |
| 12 | Python ↔ C++ canonical parity is a contract, not an assumption | `tests/fixtures/generate_parity_fixtures.py` → tracked `tests/fixtures/canonical_parity_fixtures.json` | `parity/test_canonical_parity.cpp` (1407 number forms + 7 documents, three-way: Python value tree, Python source text, C++ bytes and digest) and the Python side asserts the same file |
| 13 | DTO layer must represent and round-trip the approved V1 shapes (kernel input, market snapshot/curve/fixing identities, valuation context, resolved product references, pricing/risk results, model/calibration envelopes) | `include/shiori_rates/dto/*.hpp`, `src/dto/*.cpp` | `dto/test_decoder_refusals.cpp`, `dto/test_contract_primitives.cpp`, `calibration/test_calibration_envelopes.cpp` |
| 14 | Every document is fenced by its exact version token; unknown version/field/type is refused with a precise pointer | `require_schema_version`, `require_only_keys`, `fail*` helpers, `ContractViolation` | the refusal suites above; refusals are deterministic (`unit/test_engine_operations.cpp`) |
| 15 | Deep immutability of a published request; only request-local views | `FrozenKernelInput`, `PublishedKernelInput` | `determinism/test_canonical_determinism.cpp` (empty publish is refused, not silently empty) |
| 16 | Lane A (`assumptions`/`diagnostics` scalar maps) vs Lane B (out-of-band `RATES_RUNTIME_TELEMETRY_V1`, never a result member, not fingerprint-participating) (#226 §12) | `dto/result.hpp` row policy, `diagnostics/runtime_telemetry.hpp` | telemetry has its own version token and its own derived identity; no result decoder accepts it |
| 17 | CLI envelope `{"status","schema_version","payload","warnings","errors"}` with exit codes 0/2/3; a transport failure never uses a #225 domain code | `tools/rates_engine_cli/main.cpp` | CTest `cli_contract_*` (version, engine identity, QuantLib identity, usage error) |
| 18 | Engine/build identity is reported deterministically and includes no timestamp (reproducibility) | `cmake/ShioriBuildIdentity.cmake` → `build_identity_generated.hpp`; `engine/engine.cpp` | `unit/test_engine_operations.cpp` asserts identity stability and one of the two sanctioned acquisition modes |
| 19 | Benchmarks report M4 metadata; no timing gate (#226 §10.5/§10.8) | `benchmarks/m4_metadata.hpp` (metadata on **stderr**, so `--benchmark_format=json` on stdout stays parseable) | CTest `benchmark_smoke_*` runs each benchmark with confidence intervals but asserts no duration |
| 20 | CI is the reproducibility evidence: preset-driven configure/build/CTest, sanitizer jobs, isolation guard, benchmark smoke; the Python workflow must stay untouched | `.github/workflows/cpp-rates-engine.yml` | workflow validates the pinned baseline, uses separate cache scopes per triplet, and has no `continue-on-error` / `|| true` |

## 2. Placeholder register (documented, not implemented here)

These are **not** stubs that silently return values; they are the documented boundary of #227 and are
owned by successor issues in the #222 tree.

| Placeholder | State in #227 | Owner |
| --- | --- | --- |
| Curve construction, fixing stores, calendars, resolved SOFR schedules | DTO shape + identity only; no schedule/curve materialization | #228 `[FOUNDATION] Curve, fixing, calendar and resolved SOFR schedule primitives` |
| Vanilla USD SOFR swap pricing (leg PV, NPV, par rate, annuity, DV01) | absent by design | #229 `[IRS] C++ vanilla USD SOFR swap kernel` |
| Bloomberg primitive reconciliation for the swap | absent by design | #230 `[UAT]` |
| European swaption engine (Black / shifted Black / Bachelier) | `ExerciseTerms`/`ModelInput` shapes only | #231 `[SWAPTION] European swaption C++ engine` |
| Hull-White 1F calibration | `ModelCalibrationInput`/`Result` envelopes (opaque payload + derived identity) only, no objective/optimizer/tolerance | #233 `[MODEL] Hull-White 1F calibration framework` |
| Callable/range-accrual products | not represented beyond the resolved-product reference shape | #234/#236 |
| Volatility internals (`VolatilityInput` payload), `ResolvedSwap.resolved`, `ModelInput.payload` | opaque payload preserved byte-exactly, identity derived from the envelope | #228/#231/#233 |
| Open value vocabularies (`sign_convention`, `replay.tolerance`, `compounding`, `day_count`, interpolation/extrapolation ids, same-day rule fields, cash-settlement methodology fields, `model_version`, underlying start rule) | carried as open text with the V1 value `UNRESOLVED`; no value invented | #224/#225 follow-ups |
| Real market data | **never** fabricated; no fixture in this tree contains a market quote | — |

## 3. Verification status and disclosure

- **This tree was authored without a local C++ toolchain** (no `cmake`, `ninja`, MSVC, GCC, Clang or
  vcpkg on the authoring machine). The first compile of every C++ file is therefore the exact-head CI
  run, and that is the only accepted build/test evidence for #227. Nothing here is claimed to have been
  built or tested locally.
- What *was* verified locally, with the repository's own Python toolchain:
  - `ruff check` clean on all new Python files;
  - the Python side of the canonical contract: 72 tests pass, including the parity test against the
    tracked fixture;
  - the fixture is byte-deterministic and platform-independent (LF, no newline translation): two
    consecutive generation runs produce the same SHA-256
    `E7FBA69C506591BB221B407CD65047822D5CF664B1EA6A27EF079D9B9F9B67D7`;
  - the isolation guard: `--self-test` passes (12 trap cases + tree-walk controls) and the real scan
    reports 0 findings over 57 C++ files.
- **Known coverage gap, disclosed rather than hidden:** the *positive* path of the deep
  `RatesKernelInput` invariants (OIS vs SWAPTION applicability, single-currency agreement, valuation
  date agreement, curve-role/index resolution, underlying-reference agreement) is **not** positively
  exercised by the test suites. The decoders and `validate_invariants` implement those rules, and the
  refusal paths are tested, but a test that materializes a fully valid kernel input requires
  constructing a complete market snapshot/curve document, which is #228's subject. #227 therefore
  proves the mechanism (refusal, determinism, identity) and leaves positive coverage of the market
  shapes to the issue that owns them.
- The benchmark numbers are **not** correctness evidence and are never gated (#226 §10.8).
