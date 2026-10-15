#pragma once
//
// Lane B runtime telemetry — `RATES_RUNTIME_TELEMETRY_V1` (docs/34 Issue #226 sections 12.2-12.6).
//
// Lane B is a SEPARATE versioned channel. It is never a member of `PricingResult` or `RiskResult`,
// and it never enters a result content fingerprint. Conversely, #225's result diagnostics are never
// moved into it or dropped in favour of it: Lane A stays authoritative inside the result DTOs.
//
// The fields below are the G-1..G-7 Lane B facts of docs/34 section 12.3 plus the M4 metadata of
// section 10.5. G-2 (dependency provenance) has no approved result-contract home, so it lives here,
// in the cache key composed by the DTO layer, and in the M4 metadata — never in a result document.
//
// This header must not include QuantLib: telemetry receives already-computed strings.
//
#include <cstdint>
#include <map>
#include <string>
#include <string_view>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/units.hpp"

namespace Shiori::rates::diagnostics {

// Per-layer cache observation (G-4). The key is hashed, so keys are comparable without leaking the
// fingerprinted payload.
struct CacheLayerObservation {
  std::string layer_id;
  dto::CacheState state = dto::CacheState::kDisabled;
  std::string key_digest;  // hashed; never the raw components
  std::int64_t lookups = 0;
  std::int64_t hits = 0;
  std::int64_t misses = 0;

  [[nodiscard]] dto::CanonicalValue to_canonical() const;
};

struct ExecutionPathObservation {
  std::string path_id;  // for example VALIDATE_ONLY | CANONICALIZE | CACHE_SERVED | COMPUTED
  bool served_from_cache = false;

  [[nodiscard]] dto::CanonicalValue to_canonical() const;
};

// Concurrency mode and gate contention (G-6). Under the initial production posture (docs/34 section
// 5.2) parallel pricing is DISABLED, so the honest value here is SERIALIZED with contentions 0.
struct GateObservation {
  std::string mode;  // exactly "SERIALIZED" in the initial production posture
  std::string gate_id;
  std::int64_t acquisitions = 0;
  std::int64_t waits = 0;
  std::int64_t reentrant_refusals = 0;

  [[nodiscard]] dto::CanonicalValue to_canonical() const;
};

// Timing breakdown hooks (G-7). Recorded, never gated on: #226 section 10.8 forbids an absolute-time
// pass/fail gate, so these values are observations only.
struct PhaseTiming {
  std::string phase_id;
  double milliseconds = 0.0;

  [[nodiscard]] dto::CanonicalValue to_canonical() const;
};

// M4 metadata (docs/34 section 10.5): the dependency/configuration identity that must accompany
// every measurement so a number cannot be compared across an unknown configuration.
//
// Two DISTINCT instrumentation facts are carried, because conflating them would invent evidence
// (docs/34 sections 5.7 T3 and 11.3: a sanitizer run over an UNINSTRUMENTED dependency is not race
// evidence, and M4 requires the dependency's instrumentation state precisely so an instrumented run
// cannot be mistaken for ordinary evidence):
//   * `build_sanitizers` is the SHIORI build's own sanitizer mode, exactly as reported by
//     `BuildIdentity.sanitizers` (NONE | THREAD | ADDRESS+UNDEFINED). It says nothing about the
//     dependency closure.
//   * `dependency_instrumentation` is the dependency/QuantLib closure's instrumentation state, named only
//     when the PINNED vcpkg triplet source backs it: `triplets/x64-linux-shiori-tsan.cmake` sets
//     `-fsanitize=thread` in VCPKG_CXX_FLAGS/VCPKG_C_FLAGS/VCPKG_LINKER_FLAGS for the whole closure and
//     `triplets/x64-linux-shiori-asan.cmake` does the same for address+undefined, each with its own
//     binary-cache identity (docs/34 line 375, section 5.7 T3 / section 11.3). Any other triplet - and
//     every non-vcpkg acquisition path - reports `UNVERIFIED`, which means "this build carries no
//     source-backed evidence", never "the dependency is uninstrumented". It is NEVER derived from
//     `build_sanitizers`, so a Shiori-only sanitizer build is not readable as an instrumented
//     dependency. The triplet token itself is already reported as `vcpkg_triplet`, so the mapping is
//     auditable.
struct MeasurementMetadata {
  std::string engine_version;
  std::string quantlib_version;
  std::string quantlib_macro_configuration;
  std::string acquisition_mode;
  std::string vcpkg_baseline;
  std::string vcpkg_triplet;
  std::string dependency_instrumentation;
  std::string build_type;
  std::string build_sanitizers;
  std::string compiler;
  std::string numeric_flags;
  std::string benchmark_name;
  std::string fixture_id;

  [[nodiscard]] dto::CanonicalValue to_canonical() const;
};

struct RuntimeTelemetry {
  std::string telemetry_version;  // RATES_RUNTIME_TELEMETRY_V1
  std::string telemetry_id;
  std::string correlation_result_fingerprint;  // echoed stable id; never enters a result preimage
  std::string correlation_inputs_fingerprint;  // echoed stable id
  MeasurementMetadata metadata;
  std::vector<CacheLayerObservation> cache_layers;
  ExecutionPathObservation execution_path;
  GateObservation gate;
  std::vector<PhaseTiming> phases;
  std::string content_fingerprint;

  [[nodiscard]] dto::CanonicalValue to_canonical() const;
  [[nodiscard]] static RuntimeTelemetry from_canonical(const dto::CanonicalValue& node,
                                                       const std::string& pointer);
};

// The one implementation of the `dependency_instrumentation` mapping documented above: it names the
// dependency closure's instrumentation state from the pinned vcpkg triplet token ALONE - never from the
// Shiori sanitizer mode, the compiler, or the absence of evidence - and returns the explicit
// `UNVERIFIED` state when no source-backed triplet evidence exists. Kept here (rather than spelled at
// the emission site) so the producer and its tests share exactly one rule.
[[nodiscard]] std::string dependency_instrumentation_for_triplet(const std::string& vcpkg_triplet);

// Locked non-recursive Lane B self-identity (Sophira owner decision 6117299766 section B):
//   * `telemetry_content_fingerprint` is the digest of the canonical content with BOTH the document's
//     own `content_fingerprint` AND its own derived `telemetry_id` excluded. `telemetry_id` is
//     derived from this fingerprint, so it must not be an input to it. Every other participating
//     measurement/metadata/correlation field stays in the preimage.
//   * `telemetry_id = fingerprint_of("telemetry_version:content_fingerprint")`, the same derivation on
//     encode (`assign_derived_identities`) and on decode (`from_canonical`), and never a
//     timestamp/pointer/counter.
// A decode independently verifies both declared labels and refuses a mismatch; it never repairs one.
// This is the Lane B channel only: it changes no #225 `PricingResult`/`RiskResult` preimage.
[[nodiscard]] std::string telemetry_content_fingerprint(const RuntimeTelemetry& telemetry);
void assign_derived_identities(RuntimeTelemetry& telemetry);

}  // namespace Shiori::rates::diagnostics
