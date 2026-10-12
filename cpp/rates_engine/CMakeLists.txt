# =============================================================================================
# Shiori Pricing Lab — Rates Engine (Issue #227; foundation for #228/#229)
#
# Contract: docs/34_cpp_rates_build_quantlib_concurrency_cache_benchmark_226.md sections 2 and 3.
#
# This file owns target boundaries only. Toolchain, dependency acquisition, numeric flags and warning
# policy live in cmake/, and the CONFIGURATION AUTHORITY is CMakePresets.json alone: there is no
# supported hand-written configure line, and no flag may be supplied ad hoc by a workflow.
#
# Target boundaries (locked by #226 section 2.3):
#   shiori_rates_dto               no QuantLib, direct or transitive
#   shiori_rates_quantlib_adapter  the ONLY production QuantLib containment boundary
#   shiori_rates_core              never includes QuantLib; consumes the adapter only through its
#                                  QuantLib-free public header
#   shiori_rates_diagnostics       Lane B; no QuantLib
#   shiori_rates_cli               the transport surface; no QuantLib headers through the adapter
#
# The include graph is enforced mechanically by cpp/rates_engine/tools/isolation_guard.py, not by
# convention.
# =============================================================================================
cmake_minimum_required(VERSION 3.25)

project(shiori_rates_engine
  VERSION 0.1.0
  DESCRIPTION "Shiori Pricing Lab Rates Engine (Issue #227 skeleton)"
  LANGUAGES CXX)

# Out-of-source only (#226 section 2.2). An in-source configure would place build output inside the
# repository and make the evidence ambiguous.
if(CMAKE_BINARY_DIR STREQUAL CMAKE_SOURCE_DIR)
  message(FATAL_ERROR
    "In-source configuration is not supported. Configure through a preset in "
    "cpp/rates_engine/CMakePresets.json, which places binaries under cpp/rates_engine/build/<preset>/.")
endif()

set(SHIORI_ENGINE_VERSION "${PROJECT_VERSION}")
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")

include(ShioriBuildOptions)   # options, numeric-flags record, shiori_configure_target()
include(ShioriDependencies)   # explicit acquisition mode; no automatic downgrade
include(ShioriBuildIdentity)

shiori_generate_build_identity(
  "${CMAKE_CURRENT_BINARY_DIR}/generated/shiori_rates/engine/build_identity_generated.hpp")

# =============================================================================================
# shiori_rates_dto — the versioned DTO / JSON contract. NO QuantLib, ever.
# =============================================================================================
add_library(shiori_rates_dto STATIC
  src/dto/calibration.cpp
  src/dto/canonical.cpp
  src/dto/codec_traits.cpp
  src/dto/error.cpp
  src/dto/fingerprint.cpp
  src/dto/immutable.cpp
  src/dto/kernel_input.cpp
  src/dto/market.cpp
  src/dto/market_identity.cpp
  src/dto/numeric.cpp
  src/dto/result.cpp
  src/dto/sha256.cpp
  src/dto/time.cpp
  src/dto/value_or_reason.cpp
  src/dto/version.cpp
)
add_library(Shiori::Rates::Dto ALIAS shiori_rates_dto)
shiori_configure_target(shiori_rates_dto)
target_include_directories(shiori_rates_dto PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
# nlohmann is used as a PARSER ONLY, inside canonical.cpp. It is not part of the DTO's public
# interface, so the dependency stays PRIVATE.
target_link_libraries(shiori_rates_dto PRIVATE nlohmann_json::nlohmann_json)

# ---------------------------------------------------------------------------------------------
# TEMPORARY TOOLCHAIN WAIVER — GCC 13 only, one translation unit, diagnostic stays visible.
#
# GCC 13's -Wmaybe-uninitialized reports a libstdc++-internal inlined path while compiling
# src/dto/value_or_reason.cpp: /usr/include/c++/13/bits/shared_ptr_base.h:1099
# (`__shared_count::operator=` reading `__r._M_pi`, i.e. `_Sp_counted_base<_Lp>* __tmp = __r._M_pi;`)
# and /usr/include/c++/13/variant:224, reached from
# CanonicalValue::CanonicalValue(CanonicalValue&&) <- std::pair<std::string, CanonicalValue> <-
# std::construct_at <- std::vector<...>::emplace_back <- StructuredReason::to_canonical().
# Clang and MSVC do not diagnose this, and the ASan/UBSan and TSan runs report no invalid access or
# uninitialized read, so it is recorded as a known non-fatal toolchain diagnostic, not as a defect
# that may be hidden.
#
# Scope: GNU C++ major version 13 and this single source file only. -Wno-error=... keeps the warning
# VISIBLE; it does not disable it (that would be -Wno-maybe-uninitialized, which is NOT authorized).
# Every other warning in this and every other file remains fatal under the project's -Werror, and a
# different GCC uninitialized diagnostic is a new finding that is not waived.
#
# Owner authorization: issue #227, Sophira owner decision comment 6115796337, option (a).
# REMOVAL TRIGGER: any change of the GCC major version, the libstdc++ implementation/version, the
# pinned CI compiler image, or any redesign of CanonicalValue's storage — whichever comes first.
# ---------------------------------------------------------------------------------------------
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU"
   AND CMAKE_CXX_COMPILER_VERSION VERSION_GREATER_EQUAL 13.0
   AND CMAKE_CXX_COMPILER_VERSION VERSION_LESS 14.0)
  set_source_files_properties(src/dto/value_or_reason.cpp PROPERTIES
                              COMPILE_OPTIONS "-Wno-error=maybe-uninitialized")
  message(STATUS
          "shiori_rates_dto: temporary GCC ${CMAKE_CXX_COMPILER_VERSION} waiver for "
          "src/dto/value_or_reason.cpp (-Wno-error=maybe-uninitialized; warning remains visible)")
endif()

# =============================================================================================
# shiori_rates_quantlib_adapter — the sole production QuantLib containment boundary.
# =============================================================================================
add_library(shiori_rates_quantlib_adapter STATIC
  src/quantlib_adapter/adapter.cpp
)
add_library(Shiori::Rates::QuantLibAdapter ALIAS shiori_rates_quantlib_adapter)
shiori_configure_target(shiori_rates_quantlib_adapter)
target_include_directories(shiori_rates_quantlib_adapter PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
target_link_libraries(shiori_rates_quantlib_adapter
  PUBLIC shiori_rates_dto
  PRIVATE QuantLib::QuantLib)

# =============================================================================================
# shiori_rates_core — engine identity and the two schema operations #227 implements.
# =============================================================================================
add_library(shiori_rates_core STATIC
  src/engine/engine.cpp
)
add_library(Shiori::Rates::Core ALIAS shiori_rates_core)
shiori_configure_target(shiori_rates_core)
target_include_directories(shiori_rates_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
# Generated include dir carries only the version/build identity header.
target_include_directories(shiori_rates_core PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(shiori_rates_core PUBLIC shiori_rates_dto)

# =============================================================================================
# shiori_rates_diagnostics — Lane B `RATES_RUNTIME_TELEMETRY_V1`.
# =============================================================================================
add_library(shiori_rates_diagnostics STATIC
  src/diagnostics/runtime_telemetry.cpp
)
add_library(Shiori::Rates::Diagnostics ALIAS shiori_rates_diagnostics)
shiori_configure_target(shiori_rates_diagnostics)
target_include_directories(shiori_rates_diagnostics PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")
# Lane B is intentionally free of the adapter: telemetry receives already-computed strings, so the
# diagnostics target cannot become a second QuantLib entry point.
target_link_libraries(shiori_rates_diagnostics PUBLIC shiori_rates_dto)

# =============================================================================================
# shiori_rates_cli — deterministic stdin/stdout transport.
# =============================================================================================
add_executable(shiori_rates_cli tools/rates_engine_cli/main.cpp)
add_executable(Shiori::Rates::Cli ALIAS shiori_rates_cli)
shiori_configure_target(shiori_rates_cli)
target_link_libraries(shiori_rates_cli
  PRIVATE shiori_rates_core shiori_rates_diagnostics shiori_rates_quantlib_adapter)

# ---------------------------------------------------------------------------------------------
# TEMPORARY TOOLCHAIN WAIVERS W-002 .. W-004 — GCC 13 + the RelWithDebInfo benchmark configuration,
# one option per named translation unit, diagnostic stays visible.
#
# The benchmark preset (RelWithDebInfo, SHIORI_BUILD_BENCHMARKS=ON) makes GCC 13's
# -Wmaybe-uninitialized report the same libstdc++-internal inlined std::variant / std::shared_ptr
# artifact already recorded as W-001, in three further translation units:
#   W-002  src/dto/numeric.cpp                    (shiori_rates_dto)
#   W-003  src/diagnostics/runtime_telemetry.cpp  (shiori_rates_diagnostics)
#   W-004  tools/rates_engine_cli/main.cpp        (shiori_rates_cli)
# The Release configuration of the same sources is clean (CI `build (gcc-release)` is green), so these
# three are additionally narrowed to RelWithDebInfo. Clang, MSVC and the sanitizer configurations do
# not diagnose it. This block is placed after every target that owns these files so the property can
# never be lost to target-definition ordering.
#
# Scope: GNU C++ major version 13, RelWithDebInfo, and exactly those three files, each separately
# documented as W-002/W-003/W-004 in docs/TOOLCHAIN_WAIVERS.md and independently removable.
# -Wno-error=... keeps the warning VISIBLE; it does not disable it (that would be
# -Wno-maybe-uninitialized, which is NOT authorized, just as a target-wide/global -Wno-error or
# dropping -Werror is not). Every other warning in these and every other file stays fatal under the
# project's -Werror, and a NEW GCC uninitialized diagnostic - another class, or a further translation
# unit - is a new finding that is not waived.
#
# Owner authorization: issue #227, Sophira owner decision comment 6118664190, Decision 1.
# REMOVAL TRIGGER: any change of the GCC major version, the libstdc++ implementation/version, the
# pinned CI compiler image, the benchmark build type, or any redesign of CanonicalValue's storage -
# whichever comes first.
# ---------------------------------------------------------------------------------------------
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU"
   AND CMAKE_CXX_COMPILER_VERSION VERSION_GREATER_EQUAL 13.0
   AND CMAKE_CXX_COMPILER_VERSION VERSION_LESS 14.0
   AND CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo")
  # Deliberately three separate calls, not a loop over a list: each waiver can be dropped on its own
  # without touching the others or W-001.
  set_source_files_properties(src/dto/numeric.cpp PROPERTIES
                              COMPILE_OPTIONS "-Wno-error=maybe-uninitialized")
  set_source_files_properties(src/diagnostics/runtime_telemetry.cpp PROPERTIES
                              COMPILE_OPTIONS "-Wno-error=maybe-uninitialized")
  set_source_files_properties(tools/rates_engine_cli/main.cpp PROPERTIES
                              COMPILE_OPTIONS "-Wno-error=maybe-uninitialized")
  message(STATUS
          "shiori: temporary GCC ${CMAKE_CXX_COMPILER_VERSION} RelWithDebInfo waivers W-002..W-004 for "
          "src/dto/numeric.cpp, src/diagnostics/runtime_telemetry.cpp and "
          "tools/rates_engine_cli/main.cpp (-Wno-error=maybe-uninitialized; warnings remain visible)")
endif()

# =============================================================================================
# Tests
# =============================================================================================
if(SHIORI_BUILD_TESTS)
  enable_testing()
  include(GoogleTest)

  set(SHIORI_FIXTURE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")

  # group-name -> libraries. The group name is also the test directory name.
  # A group's libraries are comma-separated: a SEMICOLON here would be a CMake list separator, so
  # "determinism|a;b" would split into two list elements and silently produce the bogus group "b".
  set(SHIORI_TEST_GROUPS
    "dto|shiori_rates_dto"
    "unit|shiori_rates_core"
    "adapter|shiori_rates_quantlib_adapter"
    "adapter_poison|shiori_rates_quantlib_adapter"
    "defaults|shiori_rates_quantlib_adapter"
    "determinism|shiori_rates_dto,shiori_rates_core"
    "diagnostics|shiori_rates_diagnostics"
    "cache|shiori_rates_dto,shiori_rates_core"
    "calibration|shiori_rates_dto"
    "concurrency|shiori_rates_quantlib_adapter"
    "parity|shiori_rates_dto,shiori_rates_core"
    "regression|shiori_rates_dto,shiori_rates_core"
  )

  foreach(_group_spec IN LISTS SHIORI_TEST_GROUPS)
    string(REPLACE "|" ";" _group_parts "${_group_spec}")
    list(GET _group_parts 0 _group)
    list(GET _group_parts 1 _group_libraries)
    string(REPLACE "," ";" _group_libraries "${_group_libraries}")
    file(GLOB _group_sources CONFIGURE_DEPENDS
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/${_group}/*.cpp")
    # The mandatory #226 section 5.7 T4 forced-restoration-failure test latches PERMANENT adapter
    # poison, so it must never share a process with the other adapter tests: a shared binary would make
    # their outcome depend on test order. The file stays in tests/adapter/ to remain inside the approved
    # QuantLib test include allow-list, and is built into the isolated target below instead.
    if(_group STREQUAL "adapter")
      list(FILTER _group_sources EXCLUDE REGEX "/t4_forced_restoration_failure\\.cpp$")
    endif()
    if(NOT _group_sources)
      message(FATAL_ERROR
        "test group '${_group}' has no sources. A silently empty test target would make the suite "
        "look green while proving nothing; add the tests or remove the group.")
    endif()
    set(_target "shiori_rates_tests_${_group}")
    add_executable(${_target} ${_group_sources})
    shiori_configure_target(${_target})
    target_link_libraries(${_target}
      PRIVATE ${_group_libraries} GTest::gtest_main)
    target_compile_definitions(${_target} PRIVATE
      "SHIORI_FIXTURE_DIR=\"${SHIORI_FIXTURE_DIR}\"")
    if(SHIORI_EVIDENCE_RUNS)
      target_compile_definitions(${_target} PRIVATE SHIORI_EVIDENCE_RUNS=1)
    endif()
    gtest_discover_tests(${_target}
      DISCOVERY_TIMEOUT 60
      TEST_PREFIX "${_group}/"
      PROPERTIES LABELS "${_group}" TIMEOUT 300)
  endforeach()

  # Isolated #226 section 5.7 T4 forced-restoration-failure executable. It is a REQUIRED test, not an
  # optional extra: it proves that a restoration-time fault (a throwing QuantLib observer) latches
  # permanent adapter poison before the single-process gate can be released, so the process is never
  # left usable in a polluted state and a later request is refused promptly instead of deadlocking.
  # A missing source is a configuration error - the leg may not be dropped silently.
  set(_shiori_t4_source
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/adapter/t4_forced_restoration_failure.cpp")
  if(NOT EXISTS "${_shiori_t4_source}")
    message(FATAL_ERROR
      "#226 section 5.7 T4 is mandatory for #227: ${_shiori_t4_source} is missing. The "
      "forced-restoration-failure leg may not be dropped silently; add the test or remove the "
      "requirement explicitly.")
  endif()
  add_executable(shiori_rates_tests_adapter_t4 "${_shiori_t4_source}")
  shiori_configure_target(shiori_rates_tests_adapter_t4)
  target_link_libraries(shiori_rates_tests_adapter_t4
    PRIVATE shiori_rates_quantlib_adapter GTest::gtest_main)
  target_compile_definitions(shiori_rates_tests_adapter_t4 PRIVATE
    "SHIORI_FIXTURE_DIR=\"${SHIORI_FIXTURE_DIR}\"")
  if(SHIORI_EVIDENCE_RUNS)
    target_compile_definitions(shiori_rates_tests_adapter_t4 PRIVATE SHIORI_EVIDENCE_RUNS=1)
  endif()
  gtest_discover_tests(shiori_rates_tests_adapter_t4
    DISCOVERY_TIMEOUT 60
    TEST_PREFIX "adapter_t4/"
    PROPERTIES LABELS "adapter_t4" TIMEOUT 300)

  # The isolation guard is a test, not a convention (#226 section 3.9).
  find_package(Python3 COMPONENTS Interpreter QUIET)
  if(Python3_Interpreter_FOUND)
    add_test(NAME isolation_guard_self_test
      COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/isolation_guard.py"
              --root "${CMAKE_CURRENT_SOURCE_DIR}/../.." --self-test)
    add_test(NAME isolation_guard_scan
      COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/isolation_guard.py"
              --root "${CMAKE_CURRENT_SOURCE_DIR}/../..")
  else()
    message(STATUS
      "SHIORI: no Python interpreter found; the isolation guard runs in its own CI job "
      "(cpp-isolation-guard). It is not skipped silently: the job is required.")
  endif()

  # The CLI envelope is part of the #227 contract (exit 0 on success, exit 2 for a usage error), so it
  # is exercised here rather than only by hand. These operations need no stdin.
  add_test(NAME cli_contract_version
    COMMAND $<TARGET_FILE:shiori_rates_cli> --operation version)
  set_tests_properties(cli_contract_version PROPERTIES
    LABELS "cli" TIMEOUT 60
    PASS_REGULAR_EXPRESSION "\"status\":\"SUCCESS\"")
  add_test(NAME cli_contract_engine_identity
    COMMAND $<TARGET_FILE:shiori_rates_cli> --operation engine-identity)
  set_tests_properties(cli_contract_engine_identity PROPERTIES
    LABELS "cli" TIMEOUT 60
    PASS_REGULAR_EXPRESSION "\"schema_version\":\"ENGINE_IDENTITY_V1\"")
  add_test(NAME cli_contract_quantlib_identity
    COMMAND $<TARGET_FILE:shiori_rates_cli> --operation quantlib-identity)
  set_tests_properties(cli_contract_quantlib_identity PROPERTIES
    LABELS "cli" TIMEOUT 60
    PASS_REGULAR_EXPRESSION "\"schema_version\":\"QUANTLIB_IDENTITY_V1\"")
  # An unknown operation is a usage error, not an internal failure: the process must exit non-zero and
  # must not claim success in its envelope.
  add_test(NAME cli_contract_usage_error
    COMMAND $<TARGET_FILE:shiori_rates_cli> --operation definitely-not-an-operation)
  set_tests_properties(cli_contract_usage_error PROPERTIES
    LABELS "cli" TIMEOUT 60
    WILL_FAIL TRUE
    PASS_REGULAR_EXPRESSION "\"status\":\"FAILED\"")
endif()

# =============================================================================================
# Benchmarks (#226 section 10). Every benchmark prints its M4 metadata so a number can never be
# compared across an unknown configuration.
# =============================================================================================
if(SHIORI_BUILD_BENCHMARKS)
  set(SHIORI_BENCH_GROUPS
    "startup|shiori_rates_core"
    "dto|shiori_rates_dto"
    "materialize|shiori_rates_dto;shiori_rates_core"
  )
  foreach(_group_spec IN LISTS SHIORI_BENCH_GROUPS)
    string(REPLACE "|" ";" _group_parts "${_group_spec}")
    list(GET _group_parts 0 _group)
    list(GET _group_parts 1 _group_libraries)
    file(GLOB _group_sources CONFIGURE_DEPENDS
      "${CMAKE_CURRENT_SOURCE_DIR}/benchmarks/${_group}/*.cpp")
    if(NOT _group_sources)
      message(FATAL_ERROR "benchmark group '${_group}' has no sources.")
    endif()
    set(_target "shiori_rates_bench_${_group}")
    add_executable(${_target} ${_group_sources})
    shiori_configure_target(${_target})
    # The benchmarks need the same version stamp the engine reports.
    target_include_directories(${_target} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
    target_include_directories(${_target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/benchmarks")
    target_link_libraries(${_target}
      PRIVATE ${_group_libraries} shiori_rates_quantlib_adapter benchmark::benchmark)
    if(SHIORI_BUILD_TESTS)
      # A smoke test asserts only that the benchmark RAN and emitted its metadata. #226 section 10.8
      # forbids an absolute-time pass/fail gate, so no timing threshold is asserted here.
      add_test(NAME "benchmark_smoke_${_group}"
        COMMAND ${_target} --benchmark_min_time=0.01s --benchmark_format=json)
      set_tests_properties("benchmark_smoke_${_group}" PROPERTIES TIMEOUT 300 LABELS benchmark)
    endif()
  endforeach()
endif()

message(STATUS "SHIORI: ${PROJECT_NAME} ${PROJECT_VERSION}")
message(STATUS "SHIORI: dependency mode ${SHIORI_DEPENDENCY_MODE}")
message(STATUS "SHIORI: numeric flags: ${SHIORI_NUMERIC_FLAGS}")
message(STATUS "SHIORI: tests=${SHIORI_BUILD_TESTS} benchmarks=${SHIORI_BUILD_BENCHMARKS} "
               "sanitizers=${SHIORI_ENABLE_SANITIZERS} tsan=${SHIORI_ENABLE_TSAN}")
