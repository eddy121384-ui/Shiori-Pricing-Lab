# ShioriDependencies.cmake
#
# Dependency provisioning for the Shiori Rates Engine.
# Contract: docs/34_cpp_rates_build_quantlib_concurrency_cache_benchmark_226.md sections 2.6, 2.7, 2.8.
#
# Required, mechanically enforced properties:
#   * vcpkg is the PRIMARY and default acquisition path (manifest mode, pinned baseline, exact versions).
#   * The resolved QuantLib version is asserted to be exactly 1.43 from the pinned headers. It is never
#     floated, never taken from an arbitrary system install, and never "latest compatible".
#   * A FetchContent fallback exists but is (a) opt-in, (b) exact-immutable-version only,
#     (c) archive-hash verified, (d) recorded in Lane B build metadata, (e) network-capable only.
#   * Offline mode never downloads. It consumes already-populated material and otherwise FAILS CLOSED
#     with an explicit provisioning error. It never falls back to a different version, a system
#     package, vendoring, or a weakened integrity check.

include_guard(GLOBAL)

include(FetchContent)

set(SHIORI_DEPENDENCY_MODE "VCPKG" CACHE STRING
  "Native dependency acquisition path: VCPKG (primary) or FETCHCONTENT (sanctioned fallback).")
set_property(CACHE SHIORI_DEPENDENCY_MODE PROPERTY STRINGS VCPKG FETCHCONTENT)

set(SHIORI_DEPENDENCY_OFFLINE OFF CACHE BOOL
  "Forbid any network acquisition. Consume only pre-provisioned material; fail closed otherwise.")

set(SHIORI_DEPENDENCY_PROVISIONING_ERROR
  "SHIORI_DEPENDENCY_PROVISIONING_ERROR")

# --------------------------------------------------------------------------------------------
# Pinned dependency identity. Mirror of cpp/rates_engine/vcpkg.json.
# Changing any value here is a deliberate, reviewable dependency change (Issue #226 section 3.8).
# --------------------------------------------------------------------------------------------
set(SHIORI_VCPKG_BASELINE "2750401336fb7c95f6619657a46a7e798661341c" CACHE INTERNAL "" FORCE)
set(SHIORI_QUANTLIB_VERSION "1.43" CACHE INTERNAL "" FORCE)
set(SHIORI_QUANTLIB_VERSION_MAJOR "1" CACHE INTERNAL "" FORCE)
set(SHIORI_QUANTLIB_VERSION_MINOR "43" CACHE INTERNAL "" FORCE)
set(SHIORI_NLOHMANN_JSON_VERSION "3.12.0" CACHE INTERNAL "" FORCE)
set(SHIORI_GTEST_VERSION "1.18.0" CACHE INTERNAL "" FORCE)
set(SHIORI_BENCHMARK_VERSION "1.9.5" CACHE INTERNAL "" FORCE)

function(shiori_dependency_error message)
  message(FATAL_ERROR "${SHIORI_DEPENDENCY_PROVISIONING_ERROR}: ${message}")
endfunction()

# --------------------------------------------------------------------------------------------
# QuantLib exact-version assertion.
#
# The pinned QuantLib is verified by compiling against the version macros it actually ships.
# This runs on BOTH acquisition paths and cannot be satisfied by a differently-versioned library.
# --------------------------------------------------------------------------------------------
function(shiori_assert_quantlib_version)
  if(NOT TARGET QuantLib::QuantLib)
    shiori_dependency_error(
      "QuantLib::QuantLib target is absent after package resolution. "
      "Refusing to continue: the Rates engine cannot be built without the pinned QuantLib.")
  endif()

  get_target_property(_ql_inc QuantLib::QuantLib INTERFACE_INCLUDE_DIRECTORIES)
  if(NOT _ql_inc)
    shiori_dependency_error(
      "QuantLib::QuantLib exposes no INTERFACE_INCLUDE_DIRECTORIES; the pinned QuantLib version "
      "cannot be asserted. Refusing to continue rather than accept an unverified dependency.")
  endif()

  include(CheckCXXSourceCompiles)
  set(CMAKE_REQUIRED_INCLUDES "${_ql_inc}")
  set(CMAKE_REQUIRED_QUIET ON)
  check_cxx_source_compiles("
    #include <ql/version.hpp>
    #ifndef QL_VERSION_MAJOR
    #  error QL_VERSION_MAJOR is not defined by ql/version.hpp
    #endif
    static_assert(QL_VERSION_MAJOR == ${SHIORI_QUANTLIB_VERSION_MAJOR}, \"QuantLib major version mismatch\");
    static_assert(QL_VERSION_MINOR == ${SHIORI_QUANTLIB_VERSION_MINOR}, \"QuantLib minor version mismatch\");
    int main() { return 0; }
  " SHIORI_QUANTLIB_VERSION_MATCHES_PIN)

  if(NOT SHIORI_QUANTLIB_VERSION_MATCHES_PIN)
    shiori_dependency_error(
      "the resolved QuantLib does not report version ${SHIORI_QUANTLIB_VERSION}. "
      "QuantLib is pinned to ${SHIORI_QUANTLIB_VERSION} by Issue #226 section 3.2. Refusing to build "
      "against a floating or substituted QuantLib; change the pin deliberately instead.")
  endif()
  message(STATUS "SHIORI: QuantLib ${SHIORI_QUANTLIB_VERSION} pin verified against the resolved headers.")
endfunction()

# --------------------------------------------------------------------------------------------
# vcpkg (primary path)
# --------------------------------------------------------------------------------------------
function(shiori_dependencies_vcpkg)
  if(NOT DEFINED CMAKE_TOOLCHAIN_FILE OR CMAKE_TOOLCHAIN_FILE STREQUAL "")
    shiori_dependency_error(
      "SHIORI_DEPENDENCY_MODE=VCPKG requires the vcpkg toolchain (CMAKE_TOOLCHAIN_FILE). "
      "Configure with a CMake preset, which reads it from the vcpkg environment (\\$env{VCPKG_ROOT}). "
      "No system package substitution is attempted.")
  endif()

  if(SHIORI_DEPENDENCY_OFFLINE)
    # Offline is a consumption mode. vcpkg may only satisfy the manifest from an already-populated
    # binary cache; it must not reach the network to acquire or build anything.
    if(NOT DEFINED VCPKG_BINARY_SOURCES AND NOT DEFINED VCPKG_DEFAULT_BINARY_CACHE AND NOT DEFINED ENV{VCPKG_BINARY_SOURCES})
      shiori_dependency_error(
        "SHIORI_DEPENDENCY_OFFLINE=ON with SHIORI_DEPENDENCY_MODE=VCPKG, but no pre-provisioned vcpkg "
        "binary cache is configured (VCPKG_BINARY_SOURCES / VCPKG_DEFAULT_BINARY_CACHE). "
        "A network-less clean environment requires trusted pre-provisioned material matching the exact "
        "manifest, triplet and configuration. Refusing to build.")
    endif()
    list(APPEND VCPKG_INSTALL_OPTIONS "--only-binarycaching")
    set(VCPKG_INSTALL_OPTIONS "${VCPKG_INSTALL_OPTIONS}" CACHE STRING "" FORCE)
  endif()

  set(SHIORI_DEPENDENCY_ACQUISITION_MODE "VCPKG_MANIFEST_PINNED_BASELINE" CACHE INTERNAL "" FORCE)

  # Exact versions for the direct dependencies that ship a package version file.
  find_package(nlohmann_json ${SHIORI_NLOHMANN_JSON_VERSION} EXACT CONFIG REQUIRED)
  find_package(GTest ${SHIORI_GTEST_VERSION} EXACT CONFIG REQUIRED)
  find_package(benchmark ${SHIORI_BENCHMARK_VERSION} EXACT CONFIG REQUIRED)

  # QuantLib: prefer an EXACT version query; regardless of which query succeeds, the pin is then
  # asserted from the shipped headers so a missing/loose version file cannot let a substitute through.
  find_package(QuantLib ${SHIORI_QUANTLIB_VERSION} EXACT CONFIG QUIET)
  if(NOT QuantLib_FOUND)
    find_package(QuantLib CONFIG REQUIRED)
  endif()
  shiori_assert_quantlib_version()
endfunction()

# --------------------------------------------------------------------------------------------
# FetchContent fallback (sanctioned by Issue #226 section 2.6.2, network-capable environments only)
# --------------------------------------------------------------------------------------------
# Immutable source identity for the fallback. Every entry is either a stable upstream RELEASE ASSET
# with a measured SHA-256, or an exact 40-character commit SHA (content-addressed and therefore
# immutable). GitHub's auto-generated `archive/refs/tags/...` tarballs are deliberately NOT used,
# because their bytes are not guaranteed stable and therefore cannot carry a durable hash pin.
set(SHIORI_NLOHMANN_JSON_TAG "v3.12.0" CACHE INTERNAL "" FORCE)
set(SHIORI_NLOHMANN_JSON_URL
  "https://github.com/nlohmann/json/releases/download/v3.12.0/json.tar.xz" CACHE INTERNAL "" FORCE)
set(SHIORI_NLOHMANN_JSON_SHA256
  "42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa" CACHE INTERNAL "" FORCE)

set(SHIORI_GTEST_TAG "v1.18.0" CACHE INTERNAL "" FORCE)
set(SHIORI_GTEST_URL
  "https://github.com/google/googletest/releases/download/v1.18.0/googletest-1.18.0.tar.gz" CACHE INTERNAL "" FORCE)
set(SHIORI_GTEST_SHA256
  "6e3191c1455468b3fc35a417fb565c1c5071aee1b7e7f85e30cf48a98d37d8b5" CACHE INTERNAL "" FORCE)

# Google Benchmark publishes no release assets, so the fallback pins the exact commit that tag
# v1.9.5 pointed at. A commit SHA is immutable (content-addressed); the archive URL form is not.
set(SHIORI_BENCHMARK_TAG "v1.9.5" CACHE INTERNAL "" FORCE)
set(SHIORI_BENCHMARK_GIT_REPOSITORY
  "https://github.com/google/benchmark.git" CACHE INTERNAL "" FORCE)
set(SHIORI_BENCHMARK_GIT_COMMIT
  "192ef10025eb2c4cdd392bc502f0c852196baa48" CACHE INTERNAL "" FORCE)

set(SHIORI_QUANTLIB_TAG "v1.43" CACHE INTERNAL "" FORCE)
set(SHIORI_QUANTLIB_URL
  "https://github.com/lballabio/QuantLib/releases/download/v1.43/QuantLib-1.43.tar.gz" CACHE INTERNAL "" FORCE)
# Digest published by the upstream v1.43 release (Issue #226 section 3.2 cites the same value).
set(SHIORI_QUANTLIB_ARCHIVE_SHA256
  "b41206ccc4ba39b5e86bdc940d51138222b352f79d2c6fc68649f9c9bbe58701" CACHE INTERNAL "" FORCE)

function(shiori_fetchcontent_require_seed name identity)
  if(SHIORI_DEPENDENCY_OFFLINE)
    # Offline: consume pre-seeded verified source only. Never download.
    set(_seed "${FETCHCONTENT_SOURCE_DIR_${name}}")
    if(_seed AND EXISTS "${_seed}")
      message(STATUS "SHIORI: offline mode consuming pre-seeded source for ${name} at ${_seed}")
    else()
      shiori_dependency_error(
        "offline mode is ON but no pre-seeded verified source is present for '${name}' (${identity}). "
        "Provide -DFETCHCONTENT_SOURCE_DIR_${name}=<path> pointing at the already-verified material, "
        "or use a trusted pre-provisioned dependency cache matching the exact manifest/triplet/"
        "configuration. FETCHCONTENT_FULLY_DISCONNECTED is a consumption mode, not a first-run "
        "provisioning mechanism.")
    endif()
  endif()
endfunction()

function(shiori_fetchcontent_declare_url name url sha256)
  shiori_fetchcontent_require_seed(${name} "${url} sha256:${sha256}")
  FetchContent_Declare(${name}
    URL "${url}"
    URL_HASH "SHA256=${sha256}"
    DOWNLOAD_EXTRACT_TIMESTAMP OFF
  )
endfunction()

function(shiori_fetchcontent_declare_git name repository commit)
  shiori_fetchcontent_require_seed(${name} "${repository}@${commit}")
  FetchContent_Declare(${name}
    GIT_REPOSITORY "${repository}"
    GIT_TAG "${commit}"
    GIT_SHALLOW FALSE
  )
endfunction()

function(shiori_dependencies_fetchcontent)
  if(SHIORI_DEPENDENCY_OFFLINE)
    set(FETCHCONTENT_FULLY_DISCONNECTED ON CACHE BOOL "" FORCE)
  endif()

  set(SHIORI_DEPENDENCY_ACQUISITION_MODE "FETCHCONTENT_HASH_VERIFIED_FALLBACK" CACHE INTERNAL "" FORCE)
  message(WARNING
    "SHIORI: dependency acquisition is running in the FETCHCONTENT fallback. Exact immutable tags and "
    "archive SHA-256 hashes are enforced and the mode is recorded in Lane B build metadata. This is not "
    "the primary vcpkg path.")

  shiori_fetchcontent_declare_url(nlohmann_json
    "${SHIORI_NLOHMANN_JSON_URL}" "${SHIORI_NLOHMANN_JSON_SHA256}")
  shiori_fetchcontent_declare_url(googletest
    "${SHIORI_GTEST_URL}" "${SHIORI_GTEST_SHA256}")
  shiori_fetchcontent_declare_git(benchmark
    "${SHIORI_BENCHMARK_GIT_REPOSITORY}" "${SHIORI_BENCHMARK_GIT_COMMIT}")

  FetchContent_MakeAvailable(nlohmann_json googletest benchmark)
  set(nlohmann_json_FOUND TRUE)

  if(NOT TARGET GTest::gtest)
    shiori_dependency_error(
      "the FetchContent GoogleTest fallback did not produce the GTest::gtest target; "
      "public target/interface behaviour must be identical to the vcpkg path.")
  endif()
  if(NOT TARGET benchmark::benchmark)
    shiori_dependency_error(
      "the FetchContent Google Benchmark fallback did not produce the benchmark::benchmark target; "
      "public target/interface behaviour must be identical to the vcpkg path.")
  endif()

  # QuantLib is acquired through the same hash-verified mechanism, then its exact version is asserted.
  shiori_fetchcontent_declare(quantlib
    "${SHIORI_QUANTLIB_URL}" "${SHIORI_QUANTLIB_ARCHIVE_SHA256}")

  # Boost: never vendored (Issue #226 section 2.6). In the fallback it must be supplied by an
  # explicitly provided, already-verified Boost installation. There is no silent system fallback.
  if(NOT DEFINED SHIORI_BOOST_ROOT OR SHIORI_BOOST_ROOT STREQUAL "")
    shiori_dependency_error(
      "SHIORI_DEPENDENCY_MODE=FETCHCONTENT requires -DSHIORI_BOOST_ROOT=<path> supplying the verified "
      "Boost closure for the pinned QuantLib ${SHIORI_QUANTLIB_VERSION}. Boost is never vendored into "
      "this repository, and no system Boost is used implicitly.")
  endif()
  set(BOOST_ROOT "${SHIORI_BOOST_ROOT}" CACHE PATH "" FORCE)
  find_package(Boost CONFIG REQUIRED)

  FetchContent_MakeAvailable(quantlib)
  shiori_assert_quantlib_version()
endfunction()

# --------------------------------------------------------------------------------------------
# Dispatch. The mode is explicit; there is no automatic downgrade between modes.
# --------------------------------------------------------------------------------------------
if(SHIORI_DEPENDENCY_MODE STREQUAL "VCPKG")
  shiori_dependencies_vcpkg()
elseif(SHIORI_DEPENDENCY_MODE STREQUAL "FETCHCONTENT")
  shiori_dependencies_fetchcontent()
else()
  shiori_dependency_error(
    "unknown SHIORI_DEPENDENCY_MODE='${SHIORI_DEPENDENCY_MODE}'. Expected VCPKG or FETCHCONTENT. "
    "Refusing to guess an acquisition strategy.")
endif()

get_target_property(SHIORI_NLOHMANN_JSON_INCLUDE_DIR nlohmann_json::nlohmann_json INTERFACE_INCLUDE_DIRECTORIES)
set(SHIORI_NLOHMANN_JSON_INCLUDE_DIR "${SHIORI_NLOHMANN_JSON_INCLUDE_DIR}" CACHE INTERNAL "" FORCE)
