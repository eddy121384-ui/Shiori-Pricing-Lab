# Toolchain waivers (temporary, owner-authorized)

Scope rule: a waiver in this project may only keep a diagnostic **visible** while making one known
diagnostic non-fatal for **one translation unit on one compiler major version**. Disabling a warning
class, or removing `-Werror`, is not a waiver and is not used here. Every waiver needs an owner
authorization, an exact diagnostic location, and a removal trigger.

## W-001 — GCC 13 `-Wmaybe-uninitialized` while compiling `src/dto/value_or_reason.cpp`

| | |
|---|---|
| Authorization | Issue #227, Sophira owner decision comment `6115796337` (option a) |
| Applied in | `cpp/rates_engine/CMakeLists.txt`, source-file option for `src/dto/value_or_reason.cpp` |
| Option | `-Wno-error=maybe-uninitialized` — the warning stays visible, it is not disabled |
| Condition | `CMAKE_CXX_COMPILER_ID == GNU` **and** major version 13 only: not Clang, not MSVC, not GCC 12/14+, not any other source file |
| CI evidence | job `build (gcc-release)`, ubuntu-24.04, runner image `20260927.320.1` |

Exact diagnostic (one class of message, inside libstdc++, from the pinned GCC 13 / `/usr/include/c++/13`):

- `/usr/include/c++/13/bits/shared_ptr_base.h:1099:32` — the body of `__shared_count::operator=`,
  `_Sp_counted_base<_Lp>* __tmp = __r._M_pi;`, reported as a possibly-uninitialized read of the
  `<unnamed>` union member;
- further instances at `/usr/include/c++/13/variant:224:11` (`std::nullptr_t`, `bool`, `long int`,
  `double`, … alternatives);
- inlined chain: `CanonicalValue::CanonicalValue(CanonicalValue&&)` <- `std::pair<std::string,
  CanonicalValue>` <- `std::construct_at` <- `std::allocator_traits::construct` <-
  `std::vector<...>::emplace_back` <- `StructuredReason::to_canonical()` at
  `src/dto/value_or_reason.cpp:19`.

Why this is treated as a compiler diagnostic and not a runtime defect: Clang and MSVC do not report it
on the same sources, and the ASan/UBSan and TSan jobs compile the same code with **0 compile errors**
and report no invalid access or uninitialized read (their failures are the functional test failures
tracked separately in #227, none of which is a sanitizer report). This does not assert that the code
is free of GNU-diagnosed uninitialized use in general, and it is **not** a claim of a clean GCC warning
profile: one known diagnostic is non-fatal here, and a different GCC uninitialized diagnostic is a new
finding that is not covered by this waiver.

Removal trigger: whichever of these happens first — a change of the GCC major version, a change of the
libstdc++ implementation or version, a change of the pinned CI compiler image, or any redesign of
`CanonicalValue`'s recursive storage. Remove the CMake block and re-evaluate this entry.

## W-002, W-003, W-004 — the same GCC 13 diagnostic in three further translation units

W-001 above was authorized for exactly one file. Once it was in place, the **benchmark** configuration
surfaced the same diagnostic class in three more translation units: the CI release configuration
(`Release`) is green, while the benchmark preset (`RelWithDebInfo` with `SHIORI_BUILD_BENCHMARKS=ON`)
makes GCC 13's optimizer reach the same libstdc++ inlined path. Each is a separate, individually
removable waiver, and all three are additionally narrowed to `RelWithDebInfo`.

| | |
|---|---|
| Authorization | Issue #227, Sophira owner decision comment `6118664190`, Decision 1 |
| Applied in | `cpp/rates_engine/CMakeLists.txt`, three separate source-file options, after the targets that own the files |
| Option | `-Wno-error=maybe-uninitialized` — the warning stays visible, it is not disabled |
| Condition | `CMAKE_CXX_COMPILER_ID == GNU` **and** major version 13 only **and** `CMAKE_BUILD_TYPE == RelWithDebInfo`, and exactly one named file per waiver — never a target, directory or global scope |
| CI evidence | job `benchmark smoke`, ubuntu-24.04, at HEAD `ea85c568a2cb7c610e7f0a56acf052dacdd5b646` (14 diagnostics, 0 of them in the W-001 file) |

| Waiver | Translation unit | Owning target |
|---|---|---|
| W-002 | `src/dto/numeric.cpp` | `shiori_rates_dto` |
| W-003 | `src/diagnostics/runtime_telemetry.cpp` | `shiori_rates_diagnostics` |
| W-004 | `tools/rates_engine_cli/main.cpp` | `shiori_rates_cli` |

Exact diagnostic: the same message class as W-001, from the pinned GCC 13.3 / `/usr/include/c++/13` —
`variant:224:11` and `bits/shared_ptr_base.h:1099:32` reporting possibly-uninitialized reads of the
`std::variant` / `std::__shared_count` union members while inlining `CanonicalValue`'s construction.

Why this is still treated as a compiler diagnostic and not a runtime defect: the same sources compile
with 0 errors under `Release` (job `build (gcc-release)`), under Clang and under MSVC, and the
ASan/UBSan and TSan jobs report no invalid access or uninitialized read. It remains **not** a claim of a
clean GCC warning profile: three known diagnostics are non-fatal in one configuration, and a different
GCC uninitialized diagnostic — another class, or any further translation unit — is a new finding that is
not covered here and must be escalated rather than waived silently.

Removal trigger (each waiver independently): whichever of these happens first — a change of the GCC
major version, a change of the libstdc++ implementation or version, a change of the pinned CI compiler
image, a change of the benchmark build type, or any redesign of `CanonicalValue`'s storage. Remove the
corresponding `set_source_files_properties` call and re-evaluate this entry.
