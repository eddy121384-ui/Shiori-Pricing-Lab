#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/units.hpp"

namespace Shiori::rates::diagnostics {

namespace {

using dto::CanonicalMembers;
using dto::CanonicalValue;

[[nodiscard]] std::string require_text(const CanonicalValue& node, const std::string& pointer,
                                       const char* what) {
  if (!node.is_string()) {
    dto::fail_wrong_type(pointer, what, node.type_name());
  }
  if (node.as_string().empty()) {
    dto::fail(dto::ContractViolationKind::kInvariantViolation, pointer,
              "field must not be empty");
  }
  return node.as_string();
}

[[nodiscard]] bool require_bool(const CanonicalValue& node, const std::string& pointer) {
  if (!node.is_bool()) {
    dto::fail_wrong_type(pointer, "boolean", node.type_name());
  }
  return node.as_bool();
}

[[nodiscard]] std::int64_t require_int(const CanonicalValue& node, const std::string& pointer) {
  if (!node.is_integer()) {
    dto::fail_wrong_type(pointer, "integer", node.type_name());
  }
  return node.as_integer();
}

[[nodiscard]] const char* cache_state_token(dto::CacheState state) {
  return dto::enum_to_token(state);
}

}  // namespace

CanonicalValue CacheLayerObservation::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("layer_id", CanonicalValue(layer_id));
  members.emplace_back("state", CanonicalValue(std::string(cache_state_token(state))));
  members.emplace_back("key_digest", CanonicalValue(key_digest));
  members.emplace_back("lookups", CanonicalValue(lookups));
  members.emplace_back("hits", CanonicalValue(hits));
  members.emplace_back("misses", CanonicalValue(misses));
  return CanonicalValue::make_object(std::move(members));
}

CanonicalValue ExecutionPathObservation::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("path_id", CanonicalValue(path_id));
  members.emplace_back("served_from_cache", CanonicalValue(served_from_cache));
  return CanonicalValue::make_object(std::move(members));
}

CanonicalValue GateObservation::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("mode", CanonicalValue(mode));
  members.emplace_back("gate_id", CanonicalValue(gate_id));
  members.emplace_back("acquisitions", CanonicalValue(acquisitions));
  members.emplace_back("waits", CanonicalValue(waits));
  members.emplace_back("reentrant_refusals", CanonicalValue(reentrant_refusals));
  return CanonicalValue::make_object(std::move(members));
}

CanonicalValue PhaseTiming::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("phase_id", CanonicalValue(phase_id));
  members.emplace_back("milliseconds", CanonicalValue(milliseconds));
  return CanonicalValue::make_object(std::move(members));
}

CanonicalValue MeasurementMetadata::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("engine_version", CanonicalValue(engine_version));
  members.emplace_back("quantlib_version", CanonicalValue(quantlib_version));
  members.emplace_back("quantlib_macro_configuration",
                       CanonicalValue(quantlib_macro_configuration));
  members.emplace_back("acquisition_mode", CanonicalValue(acquisition_mode));
  members.emplace_back("vcpkg_baseline", CanonicalValue(vcpkg_baseline));
  members.emplace_back("vcpkg_triplet", CanonicalValue(vcpkg_triplet));
  members.emplace_back("build_type", CanonicalValue(build_type));
  members.emplace_back("compiler", CanonicalValue(compiler));
  members.emplace_back("numeric_flags", CanonicalValue(numeric_flags));
  members.emplace_back("benchmark_name", CanonicalValue(benchmark_name));
  members.emplace_back("fixture_id", CanonicalValue(fixture_id));
  return CanonicalValue::make_object(std::move(members));
}

CanonicalValue RuntimeTelemetry::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("telemetry_version", CanonicalValue(telemetry_version));
  members.emplace_back("telemetry_id", CanonicalValue(telemetry_id));
  members.emplace_back("correlation_result_fingerprint",
                       CanonicalValue(correlation_result_fingerprint));
  members.emplace_back("correlation_inputs_fingerprint",
                       CanonicalValue(correlation_inputs_fingerprint));
  members.emplace_back("metadata", metadata.to_canonical());
  dto::CanonicalItems layers;
  layers.reserve(cache_layers.size());
  for (const CacheLayerObservation& layer : cache_layers) {
    layers.push_back(layer.to_canonical());
  }
  members.emplace_back("cache_layers", CanonicalValue::make_array(std::move(layers)));
  members.emplace_back("execution_path", execution_path.to_canonical());
  members.emplace_back("gate", gate.to_canonical());
  dto::CanonicalItems phase_items;
  phase_items.reserve(phases.size());
  for (const PhaseTiming& phase : phases) {
    phase_items.push_back(phase.to_canonical());
  }
  members.emplace_back("phases", CanonicalValue::make_array(std::move(phase_items)));
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  return CanonicalValue::make_object(std::move(members));
}

RuntimeTelemetry RuntimeTelemetry::from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  dto::require_only_keys(node, pointer,
                         {"telemetry_version", "telemetry_id", "correlation_result_fingerprint",
                          "correlation_inputs_fingerprint", "metadata", "cache_layers",
                          "execution_path", "gate", "phases", "content_fingerprint"});
  RuntimeTelemetry result;
  result.telemetry_version = require_text(node.at("telemetry_version", pointer),
                                          dto::pointer_child(pointer, "telemetry_version"),
                                          "string telemetry version");
  if (result.telemetry_version != "RATES_RUNTIME_TELEMETRY_V1") {
    dto::fail(dto::ContractViolationKind::kUnknownSchemaVersion,
              dto::pointer_child(pointer, "telemetry_version"),
              "unknown telemetry version; Lane B is versioned independently of the result "
              "documents");
  }
  result.telemetry_id = require_text(node.at("telemetry_id", pointer),
                                     dto::pointer_child(pointer, "telemetry_id"),
                                     "string telemetry id");
  result.correlation_result_fingerprint =
      require_text(node.at("correlation_result_fingerprint", pointer),
                   dto::pointer_child(pointer, "correlation_result_fingerprint"),
                   "string result fingerprint correlation");
  result.correlation_inputs_fingerprint =
      require_text(node.at("correlation_inputs_fingerprint", pointer),
                   dto::pointer_child(pointer, "correlation_inputs_fingerprint"),
                   "string inputs fingerprint correlation");

  const CanonicalValue& metadata_node = node.at("metadata", pointer);
  dto::require_only_keys(metadata_node, dto::pointer_child(pointer, "metadata"),
                         {"engine_version", "quantlib_version",
                          "quantlib_macro_configuration", "acquisition_mode", "vcpkg_baseline",
                          "vcpkg_triplet", "build_type", "compiler", "numeric_flags",
                          "benchmark_name", "fixture_id"});
  result.metadata.engine_version =
      require_text(metadata_node.at("engine_version", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "engine_version"),
                   "string engine version");
  result.metadata.quantlib_version =
      require_text(metadata_node.at("quantlib_version", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "quantlib_version"),
                   "string quantlib version");
  result.metadata.quantlib_macro_configuration =
      require_text(metadata_node.at("quantlib_macro_configuration",
                                    dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"),
                                      "quantlib_macro_configuration"),
                   "string quantlib macro configuration");
  result.metadata.acquisition_mode =
      require_text(metadata_node.at("acquisition_mode", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"),
                                      "acquisition_mode"),
                   "string acquisition mode");
  result.metadata.vcpkg_baseline =
      require_text(metadata_node.at("vcpkg_baseline", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "vcpkg_baseline"),
                   "string vcpkg baseline");
  result.metadata.vcpkg_triplet =
      require_text(metadata_node.at("vcpkg_triplet", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "vcpkg_triplet"),
                   "string vcpkg triplet");
  result.metadata.build_type =
      require_text(metadata_node.at("build_type", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "build_type"),
                   "string build type");
  result.metadata.compiler =
      require_text(metadata_node.at("compiler", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "compiler"),
                   "string compiler");
  result.metadata.numeric_flags =
      require_text(metadata_node.at("numeric_flags", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "numeric_flags"),
                   "string numeric flags");
  result.metadata.benchmark_name =
      require_text(metadata_node.at("benchmark_name", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "benchmark_name"),
                   "string benchmark name");
  result.metadata.fixture_id =
      require_text(metadata_node.at("fixture_id", dto::pointer_child(pointer, "metadata")),
                   dto::pointer_child(dto::pointer_child(pointer, "metadata"), "fixture_id"),
                   "string fixture id");

  const CanonicalValue& layers_node = node.at("cache_layers", pointer);
  if (!layers_node.is_array()) {
    dto::fail_wrong_type(dto::pointer_child(pointer, "cache_layers"), "array",
                         layers_node.type_name());
  }
  for (std::size_t index = 0; index < layers_node.as_array().items.size(); ++index) {
    const std::string layer_pointer =
        dto::pointer_index(dto::pointer_child(pointer, "cache_layers"), index);
    const CanonicalValue& layer_node = layers_node.as_array().items[index];
    dto::require_only_keys(layer_node, layer_pointer,
                           {"layer_id", "state", "key_digest", "lookups", "hits", "misses"});
    CacheLayerObservation layer;
    layer.layer_id = require_text(layer_node.at("layer_id", layer_pointer),
                                  dto::pointer_child(layer_pointer, "layer_id"),
                                  "string layer id");
    layer.state = dto::enum_from_canonical<dto::CacheState>(
        layer_node.at("state", layer_pointer), dto::pointer_child(layer_pointer, "state"));
    layer.key_digest = require_text(layer_node.at("key_digest", layer_pointer),
                                    dto::pointer_child(layer_pointer, "key_digest"),
                                    "string key digest");
    layer.lookups = require_int(layer_node.at("lookups", layer_pointer),
                                dto::pointer_child(layer_pointer, "lookups"));
    layer.hits = require_int(layer_node.at("hits", layer_pointer),
                             dto::pointer_child(layer_pointer, "hits"));
    layer.misses = require_int(layer_node.at("misses", layer_pointer),
                               dto::pointer_child(layer_pointer, "misses"));
    result.cache_layers.push_back(std::move(layer));
  }

  const CanonicalValue& path_node = node.at("execution_path", pointer);
  const std::string path_pointer = dto::pointer_child(pointer, "execution_path");
  dto::require_only_keys(path_node, path_pointer, {"path_id", "served_from_cache"});
  result.execution_path.path_id = require_text(path_node.at("path_id", path_pointer),
                                               dto::pointer_child(path_pointer, "path_id"),
                                               "string execution path id");
  result.execution_path.served_from_cache = require_bool(
      path_node.at("served_from_cache", path_pointer),
      dto::pointer_child(path_pointer, "served_from_cache"));

  const CanonicalValue& gate_node = node.at("gate", pointer);
  const std::string gate_pointer = dto::pointer_child(pointer, "gate");
  dto::require_only_keys(gate_node, gate_pointer,
                         {"mode", "gate_id", "acquisitions", "waits", "reentrant_refusals"});
  result.gate.mode = require_text(gate_node.at("mode", gate_pointer),
                                  dto::pointer_child(gate_pointer, "mode"), "string gate mode");
  result.gate.gate_id = require_text(gate_node.at("gate_id", gate_pointer),
                                     dto::pointer_child(gate_pointer, "gate_id"),
                                     "string gate id");
  result.gate.acquisitions = require_int(gate_node.at("acquisitions", gate_pointer),
                                         dto::pointer_child(gate_pointer, "acquisitions"));
  result.gate.waits = require_int(gate_node.at("waits", gate_pointer),
                                  dto::pointer_child(gate_pointer, "waits"));
  result.gate.reentrant_refusals =
      require_int(gate_node.at("reentrant_refusals", gate_pointer),
                  dto::pointer_child(gate_pointer, "reentrant_refusals"));

  const CanonicalValue& phases_node = node.at("phases", pointer);
  if (!phases_node.is_array()) {
    dto::fail_wrong_type(dto::pointer_child(pointer, "phases"), "array", phases_node.type_name());
  }
  for (std::size_t index = 0; index < phases_node.as_array().items.size(); ++index) {
    const std::string phase_pointer =
        dto::pointer_index(dto::pointer_child(pointer, "phases"), index);
    const CanonicalValue& phase_node = phases_node.as_array().items[index];
    dto::require_only_keys(phase_node, phase_pointer, {"phase_id", "milliseconds"});
    PhaseTiming phase;
    phase.phase_id = require_text(phase_node.at("phase_id", phase_pointer),
                                  dto::pointer_child(phase_pointer, "phase_id"),
                                  "string phase id");
    const CanonicalValue& ms_node = phase_node.at("milliseconds", phase_pointer);
    if (!ms_node.is_float() && !ms_node.is_integer()) {
      dto::fail_wrong_type(dto::pointer_child(phase_pointer, "milliseconds"), "number",
                           ms_node.type_name());
    }
    phase.milliseconds = ms_node.is_float() ? ms_node.as_double()
                                            : static_cast<double>(ms_node.as_integer());
    result.phases.push_back(std::move(phase));
  }

  result.content_fingerprint = require_text(node.at("content_fingerprint", pointer),
                                           dto::pointer_child(pointer, "content_fingerprint"),
                                           "string content fingerprint");
  if (telemetry_content_fingerprint(result) != result.content_fingerprint) {
    dto::fail(dto::ContractViolationKind::kFingerprintMismatch,
              dto::pointer_child(pointer, "content_fingerprint"),
              "declared content_fingerprint does not match the recomputed canonical preimage");
  }
  return result;
}

std::string telemetry_content_fingerprint(const RuntimeTelemetry& telemetry) {
  CanonicalValue document = telemetry.to_canonical();
  dto::CanonicalMembers members;
  members.reserve(document.as_object().members.size());
  for (const auto& member : document.as_object().members) {
    if (member.first != "content_fingerprint") {
      members.push_back(member);
    }
  }
  return dto::fingerprint_of(dto::CanonicalValue::make_object(std::move(members)));
}

void assign_derived_identities(RuntimeTelemetry& telemetry) {
  telemetry.telemetry_version = "RATES_RUNTIME_TELEMETRY_V1";
  telemetry.content_fingerprint = telemetry_content_fingerprint(telemetry);
  // `telemetry_id` is derived from the version + fingerprint so it is deterministic and carries no
  // wall-clock or address identity.
  telemetry.telemetry_id = dto::fingerprint_of(dto::CanonicalValue(
      telemetry.telemetry_version + ":" + telemetry.content_fingerprint));
}

}  // namespace Shiori::rates::diagnostics
