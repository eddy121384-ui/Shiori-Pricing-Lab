#include "shiori_rates/dto/calibration.hpp"

#include <string>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"

namespace Shiori::rates::dto {

namespace {

[[nodiscard]] std::string child(const std::string& pointer, const char* key) {
  return pointer_child(pointer, key);
}

[[nodiscard]] std::string require_text(const CanonicalValue& node, const std::string& pointer,
                                       const char* what) {
  if (!node.is_string()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
  if (node.as_string().empty()) {
    fail(ContractViolationKind::kInvariantViolation, pointer, "field must not be empty");
  }
  return node.as_string();
}

[[nodiscard]] CanonicalValue strip_key(const CanonicalValue& object, const std::string_view key) {
  CanonicalMembers members;
  members.reserve(object.as_object().members.size());
  for (const auto& member : object.as_object().members) {
    if (member.first != key) {
      members.push_back(member);
    }
  }
  return CanonicalValue::make_object(std::move(members));
}

[[nodiscard]] std::string require_fingerprint_node(const CanonicalValue& node,
                                                   const std::string& pointer) {
  return require_valid_fingerprint(node.is_string() ? node.as_string() : std::string(), pointer);
}

[[nodiscard]] CanonicalValue require_object(const CanonicalValue& node, const std::string& pointer,
                                            const char* what) {
  if (!node.is_object()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
  return node;
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// ModelCalibrationInput
// ---------------------------------------------------------------------------------------------
CanonicalValue ModelCalibrationInput::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("calibration_input_id", CanonicalValue(calibration_input_id));
  members.emplace_back("payload", payload);
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kModelCalibrationInputV1))));
  return CanonicalValue::make_object(std::move(members));
}

ModelCalibrationInput ModelCalibrationInput::from_canonical(const CanonicalValue& node,
                                                            const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kModelCalibrationInputV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "calibration_input_id", "payload", "provenance",
                     "content_fingerprint"});
  ModelCalibrationInput result;
  result.calibration_input_id = require_text(node.at("calibration_input_id", pointer),
                                             child(pointer, "calibration_input_id"),
                                             "string calibration input id");
  result.payload = require_object(node.at("payload", pointer), child(pointer, "payload"),
                                  "object payload");
  result.provenance = SourceProvenance::from_canonical(node.at("provenance", pointer),
                                                       child(pointer, "provenance"));
  result.content_fingerprint = require_fingerprint_node(node.at("content_fingerprint", pointer),
                                                        child(pointer, "content_fingerprint"));
  verify_calibration_input_identities(result, pointer);
  return result;
}

CanonicalValue calibration_input_identity_preimage(const ModelCalibrationInput& input) {
  return strip_key(strip_key(input.to_canonical(), "calibration_input_id"), "content_fingerprint");
}

CanonicalValue calibration_input_fingerprint_preimage(const ModelCalibrationInput& input,
                                                      const std::string& input_id) {
  ModelCalibrationInput copy = input;
  copy.calibration_input_id = input_id;
  return strip_key(copy.to_canonical(), "content_fingerprint");
}

DerivedIdentity compute_calibration_input_identities(const ModelCalibrationInput& input) {
  DerivedIdentity identity;
  identity.id = fingerprint_of(calibration_input_identity_preimage(input));
  identity.content_fingerprint =
      fingerprint_of(calibration_input_fingerprint_preimage(input, identity.id));
  return identity;
}

void verify_calibration_input_identities(const ModelCalibrationInput& input,
                                         const std::string& pointer) {
  const DerivedIdentity expected = compute_calibration_input_identities(input);
  if (input.calibration_input_id != expected.id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "calibration_input_id"),
         "declared calibration_input_id does not match the recomputed identity");
  }
  if (input.content_fingerprint != expected.content_fingerprint) {
    fail(ContractViolationKind::kFingerprintMismatch, child(pointer, "content_fingerprint"),
         "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(ModelCalibrationInput& input) {
  const DerivedIdentity identity = compute_calibration_input_identities(input);
  input.calibration_input_id = identity.id;
  input.content_fingerprint = identity.content_fingerprint;
}

// ---------------------------------------------------------------------------------------------
// ModelCalibrationResult
// ---------------------------------------------------------------------------------------------
CanonicalValue ModelCalibrationResult::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("calibration_result_id", CanonicalValue(calibration_result_id));
  members.emplace_back("payload", payload);
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back(
      "schema_version",
      CanonicalValue(std::string(to_token(SchemaVersion::kModelCalibrationResultV1))));
  return CanonicalValue::make_object(std::move(members));
}

ModelCalibrationResult ModelCalibrationResult::from_canonical(const CanonicalValue& node,
                                                              const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kModelCalibrationResultV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "calibration_result_id", "payload", "provenance",
                     "content_fingerprint"});
  ModelCalibrationResult result;
  result.calibration_result_id = require_text(node.at("calibration_result_id", pointer),
                                              child(pointer, "calibration_result_id"),
                                              "string calibration result id");
  result.payload = require_object(node.at("payload", pointer), child(pointer, "payload"),
                                  "object payload");
  result.provenance = SourceProvenance::from_canonical(node.at("provenance", pointer),
                                                       child(pointer, "provenance"));
  result.content_fingerprint = require_fingerprint_node(node.at("content_fingerprint", pointer),
                                                        child(pointer, "content_fingerprint"));
  verify_calibration_result_identities(result, pointer);
  return result;
}

CanonicalValue calibration_result_identity_preimage(const ModelCalibrationResult& result) {
  return strip_key(strip_key(result.to_canonical(), "calibration_result_id"),
                   "content_fingerprint");
}

CanonicalValue calibration_result_fingerprint_preimage(const ModelCalibrationResult& result,
                                                       const std::string& result_id) {
  ModelCalibrationResult copy = result;
  copy.calibration_result_id = result_id;
  return strip_key(copy.to_canonical(), "content_fingerprint");
}

DerivedIdentity compute_calibration_result_identities(const ModelCalibrationResult& result) {
  DerivedIdentity identity;
  identity.id = fingerprint_of(calibration_result_identity_preimage(result));
  identity.content_fingerprint =
      fingerprint_of(calibration_result_fingerprint_preimage(result, identity.id));
  return identity;
}

void verify_calibration_result_identities(const ModelCalibrationResult& result,
                                          const std::string& pointer) {
  const DerivedIdentity expected = compute_calibration_result_identities(result);
  if (result.calibration_result_id != expected.id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "calibration_result_id"),
         "declared calibration_result_id does not match the recomputed identity");
  }
  if (result.content_fingerprint != expected.content_fingerprint) {
    fail(ContractViolationKind::kFingerprintMismatch, child(pointer, "content_fingerprint"),
         "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(ModelCalibrationResult& result) {
  const DerivedIdentity identity = compute_calibration_result_identities(result);
  result.calibration_result_id = identity.id;
  result.content_fingerprint = identity.content_fingerprint;
}

}  // namespace Shiori::rates::dto
