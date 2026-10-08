# ShioriBuildOptions.cmake
#
# Numeric-determinism and warning policy for the Shiori Rates Engine.
# Contract: docs/34_cpp_rates_build_quantlib_concurrency_cache_benchmark_226.md section 2.4.
#
# Locked invariants implemented here:
#   * C++20, extensions OFF (set by the top-level CMakeLists.txt).
#   * Fast-math is forbidden in EVERY configuration: no -ffast-math, no -Ofast, no /fp:fast.
#   * Floating-point contraction is explicitly disabled: -ffp-contract=off on GCC/Clang,
#     /fp:precise on MSVC. Rationale (Issue #226 section 2.4): contraction/reassociation would
#     make results compiler- and flag-dependent and silently break the bit-level regression
#     anchors required by docs/31 section 3.1.
#   * Warnings-as-errors apply to the project's OWN sources only, never to third-party targets.

include_guard(GLOBAL)

option(SHIORI_WARNINGS_AS_ERRORS "Treat warnings in Shiori sources as errors" ON)
option(SHIORI_ENABLE_SANITIZERS "Enable ASan+UBSan on supported compilers" OFF)
option(SHIORI_ENABLE_TSAN "Enable ThreadSanitizer (Linux/Clang only; needs an instrumented dependency)" OFF)
option(SHIORI_BUILD_TESTS "Build the GoogleTest suite" ON)
option(SHIORI_BUILD_BENCHMARKS "Build the Google Benchmark targets" OFF)
option(SHIORI_EVIDENCE_RUNS "Run quarantined concurrency-evidence tests (Issue #226 section 9.8 Q0)" OFF)
option(SHIORI_FETCHCONTENT_FULLY_DISCONNECTED "Consume only already-populated FetchContent material" OFF)

if(MSVC AND (SHIORI_ENABLE_SANITIZERS OR SHIORI_ENABLE_TSAN))
  message(WARNING
    "SHIORI: sanitizers were requested but this is the MSVC toolchain; "
    "sanitizer coverage is delivered by the Linux/Clang CI job (Issue #226 section 11.3). "
    "The requested flags are NOT applied here.")
  set(SHIORI_ENABLE_SANITIZERS OFF)
  set(SHIORI_ENABLE_TSAN OFF)
endif()

if(SHIORI_ENABLE_TSAN AND SHIORI_ENABLE_SANITIZERS)
  message(FATAL_ERROR
    "SHIORI_CONFIG_ERROR: SHIORI_ENABLE_TSAN and SHIORI_ENABLE_SANITIZERS are mutually exclusive. "
    "TSan cannot be combined with ASan/UBSan in one translation unit.")
endif()

# ---------------------------------------------------------------------------------------------
# Project-local interface target carrying every numeric/warning rule.
# ---------------------------------------------------------------------------------------------
add_library(shiori_rates_build_options INTERFACE)

target_compile_features(shiori_rates_build_options INTERFACE cxx_std_20)

if(MSVC)
  # /fp:precise is MSVC's default, but it is stated explicitly so a later default change or an
  # inherited /fp:fast from a parent project cannot silently alter numerics.
  target_compile_options(shiori_rates_build_options INTERFACE
    /fp:precise
    /permissive-
    /Zc:__cplusplus
    /Zc:preprocessor
    /W4
  )
  if(SHIORI_WARNINGS_AS_ERRORS)
    target_compile_options(shiori_rates_build_options INTERFACE /WX)
  endif()
else()
  target_compile_options(shiori_rates_build_options INTERFACE
    -ffp-contract=off
    -fno-fast-math
    -fno-unsafe-math-optimizations
    -Wall
    -Wextra
    -Wpedantic
  )
  if(SHIORI_WARNINGS_AS_ERRORS)
    target_compile_options(shiori_rates_build_options INTERFACE -Werror)
  endif()
endif()

# ---------------------------------------------------------------------------------------------
# Sanitizers (Linux/Clang only, per Issue #226 section 11.3). Applied to Shiori sources and to the
# test/benchmark executables. They are NEVER applied to third-party targets.
# ---------------------------------------------------------------------------------------------
add_library(shiori_rates_sanitizers INTERFACE)
if(SHIORI_ENABLE_SANITIZERS)
  target_compile_options(shiori_rates_sanitizers INTERFACE -fsanitize=address,undefined -fno-omit-frame-pointer)
  target_link_options(shiori_rates_sanitizers INTERFACE -fsanitize=address,undefined)
endif()
if(SHIORI_ENABLE_TSAN)
  target_compile_options(shiori_rates_sanitizers INTERFACE -fsanitize=thread -fno-omit-frame-pointer)
  target_link_options(shiori_rates_sanitizers INTERFACE -fsanitize=thread)
endif()

# ---------------------------------------------------------------------------------------------
# A Shiori-owned target: applies standard level, numeric rules, warnings and (optionally)
# sanitizers. Third-party imported targets never go through this function.
# ---------------------------------------------------------------------------------------------
function(shiori_configure_target target)
  target_link_libraries(${target} PUBLIC shiori_rates_build_options)
  if(SHIORI_ENABLE_SANITIZERS OR SHIORI_ENABLE_TSAN)
    target_link_libraries(${target} PRIVATE shiori_rates_sanitizers)
  endif()
  set_target_properties(${target} PROPERTIES
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS OFF
  )
endfunction()

# Describes the numeric flags in effect, for Lane B / benchmark metadata (Issue #226 M3).
# The description must not itself contain a fast-math FLAG token: it is reported by the engine and
# asserted by the unit tests, and a description that mentions the forbidden flag textually would make
# that assertion meaningless.
if(MSVC)
  set(SHIORI_NUMERIC_FLAGS "/fp:precise (fast-math forbidden)" CACHE INTERNAL "" FORCE)
else()
  set(SHIORI_NUMERIC_FLAGS
      "-ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations (fast-math forbidden)"
      CACHE INTERNAL "" FORCE)
endif()
