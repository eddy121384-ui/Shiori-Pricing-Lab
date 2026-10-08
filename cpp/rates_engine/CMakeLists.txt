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

# =============================================================================================
# Tests
# =============================================================================================
if(SHIORI_BUILD_TESTS)
  enable_testing()
  include(GoogleTest)

  set(SHIORI_FIXTURE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")

  # group-name -> libraries. The group name is also the test directory name.
  set(SHIORI_TEST_GROUPS
    "dto|shiori_rates_dto"
    "unit|shiori_rates_core"
    "adapter|shiori_rates_quantlib_adapter"
    "adapter_poison|shiori_rates_quantlib_adapter"
    "defaults|shiori_rates_quantlib_adapter"
    "determinism|shiori_rates_dto;shiori_rates_core"
    "cache|shiori_rates_dto;shiori_rates_core"
    "calibration|shiori_rates_dto"
    "concurrency|shiori_rates_quantlib_adapter"
    "parity|shiori_rates_dto;shiori_rates_core"
    "regression|shiori_rates_dto;shiori_rates_core"
  )

  foreach(_group_spec IN LISTS SHIORI_TEST_GROUPS)
    string(REPLACE "|" ";" _group_parts "${_group_spec}")
    list(GET _group_parts 0 _group)
    list(GET _group_parts 1 _group_libraries)
    file(GLOB _group_sources CONFIGURE_DEPENDS
      "${CMAKE_CURRENT_SOURCE_DIR}/tests/${_group}/*.cpp")
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
