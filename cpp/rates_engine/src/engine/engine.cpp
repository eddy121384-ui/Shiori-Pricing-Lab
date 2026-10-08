#include "shiori_rates/engine/engine.hpp"

#include <string>
#include <string_view>
#include <utility>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/version.hpp"

// Generated at configure time by CMake; it is the only place build/version identity is written down.
// It must never be produced by the workflow file, so that a hand-written command line cannot invent a
// version stamp.
#include "shiori_rates/engine/build_identity_generated.hpp"

namespace Shiori::rates::engine {

namespace {

[[nodiscard]] std::string canonical_or_empty(const dto::CanonicalValue& value) {
  return value.dump();
}

void capture_refusal(dto::OperationOutcome& outcome, const dto::ContractViolation& violation) {
  outcome.success = false;
  outcome.refusal_token = std::string(violation.token());
  outcome.refusal_pointer = violation.pointer();
  outcome.refusal_message = violation.message();
}

[[nodiscard]] dto::RatesKernelInput decode_and_validate(std::string_view canonical_json) {
  const dto::CanonicalValue document = dto::parse_canonical_json(canonical_json);
  return dto::RatesKernelInput::from_canonical(document, "");
}

}  // namespace

std::string_view engine_name() noexcept { return "shiori_rates_engine"; }

std::string_view engine_version() noexcept { return SHIORI_ENGINE_VERSION; }

std::string_view engine_method() noexcept {
  // The honest token for this lane. #227 implements no pricing method, so naming a pricing
  // methodology here would claim behaviour the build does not have.
  return "SCHEMA_VALIDATION_ONLY";
}

dto::EngineIdentity identity() {
  dto::EngineIdentity value;
  value.engine_name = std::string(engine_name());
  value.engine_version = std::string(engine_version());
  value.method = std::string(engine_method());
  return value;
}

const BuildIdentity& build_identity() {
  static const BuildIdentity identity_value = [] {
    BuildIdentity value;
    value.build_type = SHIORI_BUILD_TYPE;
    value.compiler = SHIORI_COMPILER;
    value.compiler_version = SHIORI_COMPILER_VERSION;
    value.acquisition_mode = SHIORI_ACQUISITION_MODE;
    value.vcpkg_baseline = SHIORI_VCPKG_BASELINE;
    value.vcpkg_triplet = SHIORI_VCPKG_TRIPLET;
    value.numeric_flags = SHIORI_NUMERIC_FLAGS;
    value.sanitizers = SHIORI_SANITIZERS;
    value.git_revision = SHIORI_GIT_REVISION;
    return value;
  }();
  return identity_value;
}

dto::OperationOutcome validate_kernel_input(std::string_view canonical_json) {
  dto::OperationOutcome outcome;
  try {
    const dto::RatesKernelInput input = decode_and_validate(canonical_json);
    // The reported facts are identity facts only. No price, no discount factor, no curve value and no
    // volatility is read or produced: the operation validates a document.
    dto::CanonicalMembers members;
    members.emplace_back("content_fingerprint", dto::CanonicalValue(input.content_fingerprint));
    members.emplace_back("product_id",
                         dto::CanonicalValue(input.valuation_product.product_id));
    members.emplace_back("product_type",
                         dto::enum_to_canonical(input.valuation_product.product_type));
    members.emplace_back("resolved_swap_product_id",
                         dto::CanonicalValue(input.resolved_swap.product_id));
    members.emplace_back("schema_version",
                         dto::CanonicalValue(std::string(dto::to_token(
                             dto::SchemaVersion::kRatesKernelInputV1))));
    members.emplace_back("valuation_context_id",
                         dto::CanonicalValue(input.valuation_context.valuation_context_id));
    members.emplace_back(
        "valuation_date",
        input.valuation_context.valuation_date.to_canonical());
    outcome.success = true;
    outcome.payload_json = canonical_or_empty(dto::CanonicalValue::make_object(std::move(members)));
    outcome.fingerprint = input.content_fingerprint;
  } catch (const dto::ContractViolation& violation) {
    capture_refusal(outcome, violation);
  }
  return outcome;
}

dto::OperationOutcome canonicalize_kernel_input(std::string_view canonical_json) {
  dto::OperationOutcome outcome;
  try {
    const dto::RatesKernelInput input = decode_and_validate(canonical_json);
    const dto::CanonicalValue document = input.to_canonical();
    const std::string bytes = document.dump();
    // Self-check: the canonical form of an accepted document must fingerprint to the document's own
    // declared content fingerprint. This makes the operation its own cross-language parity anchor.
    if (dto::fingerprint_of_canonical_bytes(bytes) != input.content_fingerprint) {
      dto::fail(dto::ContractViolationKind::kFingerprintMismatch, "",
                "canonical re-serialization does not reproduce the declared content_fingerprint");
    }
    outcome.success = true;
    outcome.payload_json = bytes;
    outcome.fingerprint = input.content_fingerprint;
  } catch (const dto::ContractViolation& violation) {
    capture_refusal(outcome, violation);
  }
  return outcome;
}

}  // namespace Shiori::rates::engine
