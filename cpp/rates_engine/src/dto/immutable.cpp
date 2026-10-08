#include "shiori_rates/dto/immutable.hpp"

#include <string>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"

namespace Shiori::rates::dto {

namespace {

[[nodiscard]] std::string require_text(const CanonicalValue& node, const std::string& pointer,
                                       const char* what) {
  if (!node.is_string()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
  return node.as_string();
}

// The exact required-component set of #226 sections 7.2/7.6. An empty component means "unknown
// identity", which section 7.6 makes NON-REUSABLE.
constexpr std::string_view kRequiredComponents[] = {
    "inputs_fingerprint", "engine_version", "methodology_id",  "methodology_version",
    "quantlib_version",   "acquisition_mode", "dependency_baseline_and_triplet",
    "build_numeric_flags"};

}  // namespace

std::shared_ptr<const FrozenKernelInput> freeze_kernel_input(RatesKernelInput value) {
  // Re-derive every nested identity and the top-level fingerprint from the owned value, so a caller
  // cannot publish a payload whose declared identity disagrees with its content.
  assign_derived_identities(value);
  auto frozen = std::make_shared<FrozenKernelInput>();
  frozen->canonical_bytes = value.to_canonical().dump();
  frozen->content_fingerprint = fingerprint_of_canonical_bytes(frozen->canonical_bytes);
  frozen->owning_value = std::move(value);
  return frozen;
}

const FrozenKernelInput& PublishedKernelInput::frozen() const {
  if (frozen_ == nullptr) {
    fail(ContractViolationKind::kInvariantViolation, "",
         "published kernel input is empty; there is no request state to read");
  }
  return *frozen_;
}

CanonicalValue CacheKeyComponents::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("inputs_fingerprint", CanonicalValue(inputs_fingerprint));
  members.emplace_back("engine_version", CanonicalValue(engine_version));
  members.emplace_back("methodology_id", CanonicalValue(methodology_id));
  members.emplace_back("methodology_version", CanonicalValue(methodology_version));
  members.emplace_back("quantlib_version", CanonicalValue(quantlib_version));
  members.emplace_back("quantlib_macro_configuration", CanonicalValue(quantlib_macro_configuration));
  members.emplace_back("acquisition_mode", CanonicalValue(acquisition_mode));
  members.emplace_back("dependency_baseline_and_triplet",
                       CanonicalValue(dependency_baseline_and_triplet));
  members.emplace_back("build_numeric_flags", CanonicalValue(build_numeric_flags));
  return CanonicalValue::make_object(std::move(members));
}

CacheKeyComponents CacheKeyComponents::from_canonical(const CanonicalValue& node,
                                                      const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"inputs_fingerprint", "engine_version", "methodology_id",
                     "methodology_version", "quantlib_version",
                     "quantlib_macro_configuration", "acquisition_mode",
                     "dependency_baseline_and_triplet", "build_numeric_flags"});
  CacheKeyComponents result;
  result.inputs_fingerprint =
      require_text(node.at("inputs_fingerprint", pointer), pointer_child(pointer, "inputs_fingerprint"),
                   "string inputs fingerprint");
  result.engine_version = require_text(node.at("engine_version", pointer),
                                       pointer_child(pointer, "engine_version"),
                                       "string engine version");
  result.methodology_id = require_text(node.at("methodology_id", pointer),
                                       pointer_child(pointer, "methodology_id"),
                                       "string methodology id");
  result.methodology_version = require_text(node.at("methodology_version", pointer),
                                            pointer_child(pointer, "methodology_version"),
                                            "string methodology version");
  result.quantlib_version = require_text(node.at("quantlib_version", pointer),
                                         pointer_child(pointer, "quantlib_version"),
                                         "string quantlib version");
  result.quantlib_macro_configuration =
      require_text(node.at("quantlib_macro_configuration", pointer),
                   pointer_child(pointer, "quantlib_macro_configuration"),
                   "string quantlib macro configuration");
  result.acquisition_mode = require_text(node.at("acquisition_mode", pointer),
                                         pointer_child(pointer, "acquisition_mode"),
                                         "string acquisition mode");
  result.dependency_baseline_and_triplet =
      require_text(node.at("dependency_baseline_and_triplet", pointer),
                   pointer_child(pointer, "dependency_baseline_and_triplet"),
                   "string dependency baseline and triplet");
  result.build_numeric_flags = require_text(node.at("build_numeric_flags", pointer),
                                            pointer_child(pointer, "build_numeric_flags"),
                                            "string build numeric flags");
  return result;
}

std::string CacheKey::compute_digest() const {
  return fingerprint_of(components.to_canonical());
}

CacheKey CacheKey::from_components(CacheKeyComponents components_in, const std::string& pointer) {
  CacheKey key;
  key.components = std::move(components_in);
  key.digest = key.compute_digest();
  if (!key.is_reusable()) {
    // Fail closed rather than return a key that could silently collide across unknown identity.
    fail(ContractViolationKind::kInvariantViolation, pointer,
         "cache key has an empty or unknown required component; #226 section 7.6 makes such a key "
         "non-reusable");
  }
  return key;
}

CanonicalValue CacheKey::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("components", components.to_canonical());
  members.emplace_back("digest", CanonicalValue(digest));
  return CanonicalValue::make_object(std::move(members));
}

CacheKey CacheKey::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"components", "digest"});
  CacheKey key;
  key.components = CacheKeyComponents::from_canonical(node.at("components", pointer),
                                                      pointer_child(pointer, "components"));
  key.digest = require_valid_fingerprint(
      node.at("digest", pointer).is_string() ? node.at("digest", pointer).as_string()
                                             : std::string(),
      pointer_child(pointer, "digest"));
  if (key.digest != key.compute_digest()) {
    fail(ContractViolationKind::kFingerprintMismatch, pointer_child(pointer, "digest"),
         "cache key digest does not match the recomputed canonical component preimage");
  }
  (void)from_components(key.components, pointer);
  return key;
}

bool CacheKey::is_reusable(std::string* refusal_reason) const {
  const CanonicalValue canonical = components.to_canonical();
  for (const std::string_view name : kRequiredComponents) {
    const CanonicalValue& field = canonical.at(name, "");
    if (!field.is_string() || field.as_string().empty()) {
      if (refusal_reason != nullptr) {
        *refusal_reason = std::string("empty required cache-key component: ") + std::string(name);
      }
      return false;
    }
  }
  if (digest != compute_digest()) {
    if (refusal_reason != nullptr) {
      *refusal_reason = "cache-key digest does not match its components";
    }
    return false;
  }
  return true;
}

}  // namespace Shiori::rates::dto
