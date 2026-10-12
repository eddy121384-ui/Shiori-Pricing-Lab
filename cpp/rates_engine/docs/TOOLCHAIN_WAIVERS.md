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
