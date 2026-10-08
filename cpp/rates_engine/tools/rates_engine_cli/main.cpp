// Shiori Rates Engine CLI.
//
// A thin, deterministic transport surface over the two operations #227 implements, plus identity
// reporting. It prices nothing.
//
// Contract (owned by #227, docs/CANONICALIZATION.md):
//   stdin   canonical UTF-8 JSON for the kernel-input operations; unused otherwise
//   stdout  exactly one canonical JSON object + "\n":
//             {"errors":[...],"payload":...,"schema_version":"...","status":"...","warnings":[...]}
//   exit    0 SUCCESS | 2 FAILED (contract refusal) | 3 usage error
//
// `status` is a transport token. A structural refusal token such as WRONG_TYPE or
// FINGERPRINT_MISMATCH is NEVER presented as one of #225 section 16's eleven domain error codes:
// a refused document is not a pricing outcome and must not be mistaken for one.
#include <cstdio>
#include <exception>
#include <iostream>
#include <iterator>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/version.hpp"
#include "shiori_rates/engine/engine.hpp"
#include "shiori_rates/diagnostics/runtime_telemetry.hpp"
#include "shiori_rates/quantlib_adapter/adapter.hpp"

namespace {

using dto::CanonicalItems;
using dto::CanonicalMembers;
using dto::CanonicalValue;

constexpr int kExitSuccess = 0;
constexpr int kExitFailed = 2;
constexpr int kExitUsage = 3;

struct TransportError {
  std::string code;
  std::string pointer;
  std::string message;

  [[nodiscard]] CanonicalValue to_canonical() const {
    CanonicalMembers members;
    members.emplace_back("code", CanonicalValue(code));
    members.emplace_back("message", CanonicalValue(message));
    members.emplace_back("pointer", CanonicalValue(pointer));
    return CanonicalValue::make_object(std::move(members));
  }
};

struct Envelope {
  bool success = false;
  std::string schema_version;
  CanonicalValue payload;  // null when there is no payload
  std::vector<TransportError> errors;
  std::vector<TransportError> warnings;

  [[nodiscard]] CanonicalValue to_canonical() const {
    CanonicalItems error_items;
    error_items.reserve(errors.size());
    for (const TransportError& error : errors) {
      error_items.push_back(error.to_canonical());
    }
    CanonicalItems warning_items;
    warning_items.reserve(warnings.size());
    for (const TransportError& warning : warnings) {
      warning_items.push_back(warning.to_canonical());
    }
    CanonicalMembers members;
    members.emplace_back("errors", CanonicalValue::make_array(std::move(error_items)));
    members.emplace_back("payload", payload);
    members.emplace_back("schema_version", CanonicalValue(schema_version));
    members.emplace_back("status", CanonicalValue(std::string(success ? "SUCCESS" : "FAILED")));
    members.emplace_back("warnings", CanonicalValue::make_array(std::move(warning_items)));
    return CanonicalValue::make_object(std::move(members));
  }
};

[[nodiscard]] std::string read_stdin() {
  return std::string(std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>());
}

void print_envelope(const Envelope& envelope) {
  const std::string text = envelope.to_canonical().dump();
  std::fwrite(text.data(), 1, text.size(), stdout);
  std::fputc('\n', stdout);
}

int usage(std::string_view message) {
  std::fprintf(stderr, "rates_engine_cli: %.*s\n", static_cast<int>(message.size()),
               message.data());
  std::fprintf(stderr,
               "usage: rates_engine_cli --operation "
               "validate-kernel-input|canonicalize-kernel-input|engine-identity|telemetry|"
               "quantlib-identity|version\n");
  return kExitUsage;
}

[[nodiscard]] int emit_operation_result(const std::string& operation,
                                        const engine::OperationOutcome& outcome) {
  Envelope envelope;
  envelope.success = outcome.success;
  envelope.schema_version = "RATES_KERNEL_INPUT_V1";
  if (outcome.success) {
    envelope.payload = dto::parse_canonical_json(outcome.payload_json);
  }
  if (!outcome.success) {
    TransportError error;
    error.code = outcome.refusal_token;
    error.pointer = outcome.refusal_pointer;
    error.message = outcome.refusal_message;
    envelope.errors.push_back(std::move(error));
  }
  print_envelope(envelope);
  (void)operation;
  return outcome.success ? kExitSuccess : kExitFailed;
}

[[nodiscard]] int emit_engine_identity() {
  Envelope envelope;
  envelope.success = true;
  envelope.schema_version = "ENGINE_IDENTITY_V1";
  const dto::EngineIdentity identity = engine::identity();
  const engine::BuildIdentity& build = engine::build_identity();
  CanonicalMembers build_members;
  build_members.emplace_back("acquisition_mode", CanonicalValue(build.acquisition_mode));
  build_members.emplace_back("build_type", CanonicalValue(build.build_type));
  build_members.emplace_back("compiler", CanonicalValue(build.compiler));
  build_members.emplace_back("compiler_version", CanonicalValue(build.compiler_version));
  build_members.emplace_back("git_revision", CanonicalValue(build.git_revision));
  build_members.emplace_back("numeric_flags", CanonicalValue(build.numeric_flags));
  build_members.emplace_back("sanitizers", CanonicalValue(build.sanitizers));
  build_members.emplace_back("vcpkg_baseline", CanonicalValue(build.vcpkg_baseline));
  build_members.emplace_back("vcpkg_triplet", CanonicalValue(build.vcpkg_triplet));
  CanonicalMembers members;
  members.emplace_back("build_identity", CanonicalValue::make_object(std::move(build_members)));
  members.emplace_back("engine_name", CanonicalValue(identity.engine_name));
  members.emplace_back("engine_version", CanonicalValue(identity.engine_version));
  members.emplace_back("method", CanonicalValue(identity.method));
  envelope.payload = CanonicalValue::make_object(std::move(members));
  print_envelope(envelope);
  return kExitSuccess;
}

[[nodiscard]] int emit_quantlib_identity() {
  Envelope envelope;
  const adapter::QuantLibIdentity& identity = adapter::quantlib_identity();
  envelope.success = identity.complete() && adapter::quantlib_linked();
  envelope.schema_version = "QUANTLIB_IDENTITY_V1";
  CanonicalMembers members;
  members.emplace_back("build_platform", CanonicalValue(identity.build_platform));
  members.emplace_back("global_settings_shape",
                       CanonicalValue(identity.global_settings_quantifiable));
  members.emplace_back("linked", CanonicalValue(adapter::quantlib_linked()));
  members.emplace_back("macro_configuration", CanonicalValue(identity.macro_configuration));
  members.emplace_back("poisoned", CanonicalValue(adapter::adapter_poisoned()));
  members.emplace_back("version", CanonicalValue(identity.version));
  members.emplace_back("version_string", CanonicalValue(identity.version_string));
  envelope.payload = CanonicalValue::make_object(std::move(members));
  if (!envelope.success) {
    TransportError error;
    error.code = "DEPENDENCY_IDENTITY_INCOMPLETE";
    error.pointer = "";
    error.message =
        "QuantLib identity is incomplete or the library is not linked; the adapter fails closed "
        "rather than reporting reusable identity";
    envelope.errors.push_back(std::move(error));
    if (adapter::adapter_poisoned()) {
      TransportError poison;
      poison.code = "ADAPTER_POISONED";
      poison.pointer = "";
      poison.message = adapter::adapter_poison_reason();
      envelope.errors.push_back(std::move(poison));
    }
  }
  print_envelope(envelope);
  return envelope.success ? kExitSuccess : kExitFailed;
}

[[nodiscard]] int emit_telemetry() {
  using diagnostics::CacheLayerObservation;
  using diagnostics::ExecutionPathObservation;
  using diagnostics::GateObservation;
  using diagnostics::MeasurementMetadata;
  using diagnostics::RuntimeTelemetry;

  RuntimeTelemetry telemetry;
  telemetry.telemetry_version = "RATES_RUNTIME_TELEMETRY_V1";
  telemetry.correlation_result_fingerprint = "unavailable-for-this-operation";
  telemetry.correlation_inputs_fingerprint = "unavailable-for-this-operation";

  const engine::BuildIdentity& build = engine::build_identity();
  const adapter::QuantLibIdentity& quantlib = adapter::quantlib_identity();
  telemetry.metadata.engine_version = std::string(engine::engine_version());
  telemetry.metadata.quantlib_version = quantlib.version;
  telemetry.metadata.quantlib_macro_configuration = quantlib.macro_configuration;
  telemetry.metadata.acquisition_mode = build.acquisition_mode;
  telemetry.metadata.vcpkg_baseline = build.vcpkg_baseline;
  telemetry.metadata.vcpkg_triplet = build.vcpkg_triplet;
  telemetry.metadata.build_type = build.build_type;
  telemetry.metadata.compiler = build.compiler + "/" + build.compiler_version;
  telemetry.metadata.numeric_flags = build.numeric_flags;
  telemetry.metadata.benchmark_name = "none";
  telemetry.metadata.fixture_id = "none";

  // G-4: the cache is not implemented in #227, so the honest observation is DISABLED with zero
  // activity. Reporting a fabricated hit rate would be inventing evidence.
  CacheLayerObservation layer;
  layer.layer_id = "kernel_input";
  layer.state = dto::CacheState::kDisabled;
  layer.key_digest = "none";
  telemetry.cache_layers.push_back(std::move(layer));

  telemetry.execution_path.path_id = "VALIDATE_ONLY";
  telemetry.execution_path.served_from_cache = false;

  const adapter::SerializationGate::Counters counters = adapter::SerializationGate::counters();
  telemetry.gate.mode = adapter::kProductionParallelPricingEnabled ? "PARALLEL" : "SERIALIZED";
  telemetry.gate.gate_id = "shiori_rates_quantlib_gate";
  telemetry.gate.acquisitions = counters.acquisitions;
  telemetry.gate.waits = counters.waits;
  telemetry.gate.reentrant_refusals = counters.reentrant_refusals;

  diagnostics::assign_derived_identities(telemetry);

  Envelope envelope;
  envelope.success = true;
  envelope.schema_version = "RATES_RUNTIME_TELEMETRY_V1";
  envelope.payload = telemetry.to_canonical();
  print_envelope(envelope);
  return kExitSuccess;
}

}  // namespace

int main(int argc, char** argv) {
  std::string operation;
  for (int index = 1; index < argc; ++index) {
    const std::string_view argument = argv[index];
    if (argument == "--operation") {
      if (index + 1 >= argc) {
        return usage("--operation requires a value");
      }
      operation = argv[++index];
    } else if (argument == "--help" || argument == "-h") {
      return usage("help requested");
    } else {
      return usage("unknown argument");
    }
  }
  if (operation.empty()) {
    return usage("--operation is required");
  }

  try {
    if (operation == "validate-kernel-input") {
      return emit_operation_result(operation, engine::validate_kernel_input(read_stdin()));
    }
    if (operation == "canonicalize-kernel-input") {
      return emit_operation_result(operation, engine::canonicalize_kernel_input(read_stdin()));
    }
    if (operation == "engine-identity") {
      return emit_engine_identity();
    }
    if (operation == "quantlib-identity") {
      return emit_quantlib_identity();
    }
    if (operation == "telemetry") {
      return emit_telemetry();
    }
    if (operation == "version") {
      Envelope envelope;
      envelope.success = true;
      envelope.schema_version = "ENGINE_IDENTITY_V1";
      envelope.payload =
          CanonicalValue(std::string(engine::engine_name()) + " " + std::string(engine::engine_version()));
      print_envelope(envelope);
      return kExitSuccess;
    }
    return usage("unknown operation");
  } catch (const dto::ContractViolation& violation) {
    // The transport itself failed to build an envelope (for example unreadable stdin). This is
    // reported fail-closed with a structural token and no payload.
    Envelope envelope;
    envelope.success = false;
    envelope.schema_version = "RATES_ENGINE_CLI_V1";
    TransportError error;
    error.code = std::string(violation.token());
    error.pointer = violation.pointer();
    error.message = violation.message();
    envelope.errors.push_back(std::move(error));
    print_envelope(envelope);
    return kExitFailed;
  } catch (const std::exception& error) {
    Envelope envelope;
    envelope.success = false;
    envelope.schema_version = "RATES_ENGINE_CLI_V1";
    TransportError transport_error;
    // Deliberately NOT `ENGINE_ERROR`: that is a #225 section 16 DOMAIN code and is reserved for a
    // returned pricing outcome. A transport failure is a structural refusal with its own token.
    transport_error.code = "CLI_INTERNAL_ERROR";
    transport_error.pointer = "";
    transport_error.message = error.what();
    envelope.errors.push_back(std::move(transport_error));
    print_envelope(envelope);
    return kExitFailed;
  }
}
