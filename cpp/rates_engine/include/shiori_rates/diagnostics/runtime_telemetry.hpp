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
struct MeasurementMetadata {
  std::string engine_version;
  std::string quantlib_version;
  std::string quantlib_macro_configuration;
  std::string acquisition_mode;
  std::string vcpkg_baseline;
  std::string vcpkg_triplet;
  std::string build_type;
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

[[nodiscard]] std::string telemetry_content_fingerprint(const RuntimeTelemetry& telemetry);
void assign_derived_identities(RuntimeTelemetry& telemetry);

}  // namespace Shiori::rates::diagnostics
