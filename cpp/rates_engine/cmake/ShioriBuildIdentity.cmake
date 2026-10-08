# =============================================================================================
# Build / version identity (Issue #226 sections 12.3 and 10.5).
#
# ONE source of truth. `shiori_generate_build_identity()` writes a single generated header and every
# identity consumer reads it from there:
#   * `shiori_rates_core` (engine version / method),
#   * `tools/rates_engine_cli` (`engine-identity`, `telemetry`),
#   * the Lane B M4 metadata attached to benchmark output.
#
# Nothing here may be supplied by a hand-written configure line: the workflow invokes presets only,
# and the version stamp comes from `project(... VERSION ...)` plus the vcpkg manifest.
#
# NO TIMESTAMP and NO ADDRESS is recorded. A configure-time clock reading would make the compiled
# artifact non-reproducible, and #226 requires a reproducible build. Source identity is captured by
# `SHIORI_GIT_REVISION` (with an explicit dirty marker) instead.
# =============================================================================================
include_guard(GLOBAL)

function(shiori_generate_build_identity output_path)
  if(NOT DEFINED SHIORI_ENGINE_VERSION OR SHIORI_ENGINE_VERSION STREQUAL "")
    message(FATAL_ERROR "shiori_generate_build_identity: SHIORI_ENGINE_VERSION is not set")
  endif()

  # Acquisition mode and its pins come from the manifest itself, so the reported dependency identity
  # cannot drift from the manifest that was actually consumed.
  set(_vcpkg_baseline "NONE")
  set(_vcpkg_manifest "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../vcpkg.json")
  if(EXISTS "${_vcpkg_manifest}")
    file(READ "${_vcpkg_manifest}" _vcpkg_manifest_content)
    string(REGEX MATCH "\"builtin-baseline\"[ \t\r\n]*:[ \t\r\n]*\"([0-9a-fA-F]+)\""
           _vcpkg_baseline_match "${_vcpkg_manifest_content}")
    if(NOT CMAKE_MATCH_1 STREQUAL "")
      set(_vcpkg_baseline "${CMAKE_MATCH_1}")
    else()
      message(FATAL_ERROR
        "vcpkg.json declares no builtin-baseline; #226 section 3.3 requires a pinned baseline and "
        "this build refuses to report an unpinned dependency identity")
    endif()
  endif()
  if(DEFINED VCPKG_TARGET_TRIPLET AND NOT VCPKG_TARGET_TRIPLET STREQUAL "")
    set(_vcpkg_triplet "${VCPKG_TARGET_TRIPLET}")
  else()
    set(_vcpkg_triplet "NONE")
  endif()

  if(DEFINED CMAKE_BUILD_TYPE AND NOT CMAKE_BUILD_TYPE STREQUAL "")
    set(_build_type "${CMAKE_BUILD_TYPE}")
  else()
    set(_build_type "MULTI_CONFIG")
  endif()

  if(MSVC)
    set(_compiler "MSVC")
  elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    set(_compiler "Clang")
  elseif(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    set(_compiler "GCC")
  else()
    set(_compiler "${CMAKE_CXX_COMPILER_ID}")
  endif()

  set(_sanitizers "NONE")
  if(SHIORI_ENABLE_TSAN)
    set(_sanitizers "THREAD")
  elseif(SHIORI_ENABLE_SANITIZERS)
    set(_sanitizers "ADDRESS+UNDEFINED")
  endif()

  # Source identity: the exact commit, with a dirty marker because a dirty tree is a different
  # artifact from the commit it claims to be.
  set(_git_revision "unknown")
  find_package(Git QUIET)
  if(GIT_FOUND)
    execute_process(COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
      WORKING_DIRECTORY "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../../.."
      OUTPUT_VARIABLE _git_head OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET
      RESULT_VARIABLE _git_head_result)
    if(_git_head_result EQUAL 0 AND NOT _git_head STREQUAL "")
      set(_git_revision "${_git_head}")
      execute_process(COMMAND "${GIT_EXECUTABLE}" status --porcelain
        WORKING_DIRECTORY "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../../.."
        OUTPUT_VARIABLE _git_status OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET
        RESULT_VARIABLE _git_status_result)
      if(_git_status_result EQUAL 0 AND NOT _git_status STREQUAL "")
        string(APPEND _git_revision "-dirty")
      endif()
    endif()
  endif()

  set(_numeric_flags "${SHIORI_NUMERIC_FLAGS}")
  if(_numeric_flags STREQUAL "")
    message(FATAL_ERROR
      "SHIORI_NUMERIC_FLAGS is unset; include(ShioriBuildOptions) before generating build identity")
  endif()

  # CMake strings may not carry a raw double quote into a C++ string literal.
  foreach(_value_name IN ITEMS _build_type _compiler _sanitizers _git_revision _numeric_flags
                                _vcpkg_baseline _vcpkg_triplet)
    string(REPLACE "\\" "/" ${_value_name} "${${_value_name}}")
    string(REPLACE "\"" "'" ${_value_name} "${${_value_name}}")
  endforeach()

  set(_template [=[
#pragma once
// GENERATED FILE - do not edit, and do not commit.
// Written by cpp/rates_engine/cmake/ShioriBuildIdentity.cmake at configure time.
//
// It is the ONLY place this build's version and dependency identity is written down. Every consumer
// reads it from here so the reported identity cannot drift from the configuration that produced the
// binary.

#define SHIORI_ENGINE_VERSION "@_engine_version@"
#define SHIORI_BUILD_TYPE "@_build_type@"
#define SHIORI_COMPILER "@_compiler@"
#define SHIORI_COMPILER_VERSION "@CMAKE_CXX_COMPILER_VERSION@"
#define SHIORI_ACQUISITION_MODE "@SHIORI_DEPENDENCY_MODE@"
#define SHIORI_VCPKG_BASELINE "@_vcpkg_baseline@"
#define SHIORI_VCPKG_TRIPLET "@_vcpkg_triplet@"
#define SHIORI_NUMERIC_FLAGS "@_numeric_flags@"
#define SHIORI_SANITIZERS "@_sanitizers@"
#define SHIORI_GIT_REVISION "@_git_revision@"
]=])

  set(_engine_version "${SHIORI_ENGINE_VERSION}")
  string(CONFIGURE "${_template}" _content @ONLY)

  get_filename_component(_output_dir "${output_path}" DIRECTORY)
  file(MAKE_DIRECTORY "${_output_dir}")
  file(WRITE "${output_path}" "${_content}")
  message(STATUS "SHIORI: wrote build identity to ${output_path}")
endfunction()
