#include "shiori_rates/dto/fingerprint.hpp"

#include <string>
#include <string_view>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/sha256.hpp"

namespace Shiori::rates::dto {

namespace {

// The approved #227 canonical representation spells a digest in LOWERCASE hex only. Uppercase A-F is
// therefore not a valid token: it is refused rather than normalized, so a declared fingerprint is
// compared byte-for-byte against the digest this build recomputes.
bool is_hex_digit(const char character) noexcept {
  return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f');
}

}  // namespace

bool is_valid_fingerprint(const std::string_view text) noexcept {
  if (text.size() != kFingerprintHexLength) {
    return false;
  }
  for (const char character : text) {
    if (!is_hex_digit(character)) {
      return false;
    }
  }
  return true;
}

std::string fingerprint_of_canonical_bytes(const std::string_view canonical_bytes) {
  return sha256_hex(canonical_bytes);
}

std::string fingerprint_of(const CanonicalValue& value) {
  return fingerprint_of_canonical_bytes(value.dump());
}

std::string require_valid_fingerprint(std::string token, const std::string& pointer) {
  // is_valid_fingerprint refuses uppercase, so there is nothing to normalize here: the token that is
  // validated is exactly the token that gets compared with the recomputed digest.
  if (!is_valid_fingerprint(token)) {
    fail(ContractViolationKind::kFingerprintMismatch, pointer,
         "declared fingerprint is not a 64-character lowercase hex SHA-256 token");
  }
  return token;
}

void verify_declared_fingerprint(const CanonicalValue& object, const std::string_view field,
                                 const CanonicalValue& recomputed_preimage,
                                 const std::string& pointer) {
  const CanonicalValue& declared_node = object.at(field, pointer);
  if (!declared_node.is_string()) {
    fail_wrong_type(pointer_child(pointer, field), "string fingerprint",
                    declared_node.type_name());
  }
  const std::string declared =
      require_valid_fingerprint(declared_node.as_string(), pointer_child(pointer, field));
  const std::string recomputed = fingerprint_of(recomputed_preimage);
  if (declared != recomputed) {
    // #226 section 7.4.1: a declared fingerprint that does not match its recomputation is a
    // REFUSAL, never a hit and never a trusted label.
    fail(ContractViolationKind::kFingerprintMismatch, pointer_child(pointer, field),
         "declared fingerprint does not match the recomputed canonical preimage");
  }
}

}  // namespace Shiori::rates::dto
