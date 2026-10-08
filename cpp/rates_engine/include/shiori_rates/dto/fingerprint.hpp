#pragma once
//
// Content identity: canonical bytes -> fingerprint.
//
// #227 owns the MECHANISM (canonical bytes + digest function). #225 owns WHICH FIELDS participate:
//
//   * `content_fingerprint` is the digest of the canonical preimage with the object's OWN
//     fingerprint field excluded ("own fingerprint excluded").
//   * An identity-bearing object's `*_id` is derived from the same canonical content with BOTH its
//     own id and its own fingerprint excluded — excluding the id is what makes the identity
//     non-recursive (an id cannot be an input to itself).
//
// ORDER OF DERIVATION (implemented by every identity-bearing DTO, stated once here):
//   1. build the identity preimage  = canonical content minus {own id, own content_fingerprint}
//   2. own id                       = sha256_hex(1)
//   3. build the fingerprint preimage = canonical content minus {own content_fingerprint}
//                                     = (1) plus the just-derived own id
//   4. content_fingerprint           = sha256_hex(3)
//
// This reading follows #225's literal exclusion wording and gives a deterministic, non-recursive
// derivation. It is documented in cpp/rates_engine/docs/CANONICALIZATION.md so a later engine cannot
// change it by accident.
//
// A DECLARED fingerprint is an input, not an authority: a decode that finds a declared fingerprint
// which does not match its recomputation REFUSES (never "trusts the label").
//
#include <cstddef>
#include <string>
#include <string_view>

#include "shiori_rates/dto/canonical.hpp"

namespace Shiori::rates::dto {

inline constexpr std::size_t kFingerprintHexLength = 64;

[[nodiscard]] bool is_valid_fingerprint(std::string_view text) noexcept;

// Fingerprint of already-canonical UTF-8 bytes.
[[nodiscard]] std::string fingerprint_of_canonical_bytes(std::string_view canonical_bytes);

// Canonicalizes, then fingerprints.
[[nodiscard]] std::string fingerprint_of(const CanonicalValue& value);

// Validates a declared fingerprint token, returning the normalized (lowercase) form.
// An empty, wrong-length, or non-hex token is refused.
[[nodiscard]] std::string require_valid_fingerprint(std::string token, const std::string& pointer);

// Verifies a declared fingerprint against recomputed canonical bytes.
// Mismatch -> ContractViolation(kFingerprintMismatch).
void verify_declared_fingerprint(const CanonicalValue& object, std::string_view field,
                                 const CanonicalValue& recomputed_preimage,
                                 const std::string& pointer);

}  // namespace Shiori::rates::dto
