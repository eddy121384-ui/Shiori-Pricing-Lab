#pragma once
//
// SHA-256 (FIPS 180-4) — the fingerprint digest chosen by Issue #227.
//
// Rationale (documented in cpp/rates_engine/docs/CANONICALIZATION.md):
//   * #225 section 6.3/15.4 deliberately leaves the digest function to #227;
//   * SHA-256 is collision-resistant enough for content identity, is language-portable, and is
//     directly available in the Python standard library (`hashlib.sha256`), which lets the
//     Python<->C++ parity fixtures be verified against an independent implementation;
//   * it adds no third-party dependency, keeping the dependency footprint minimal.
//
// This is implemented in-tree rather than taken from a crypto library because no crypto library is
// in the pinned dependency set and the dependency list must not grow without a deliberate change.
// Correctness is pinned by the FIPS 180-4 test vectors in tests/dto.
//
#include <array>
#include <cstdint>
#include <string>
#include <string_view>

namespace Shiori::rates::dto {

using Sha256Digest = std::array<std::uint8_t, 32>;

[[nodiscard]] Sha256Digest sha256(std::string_view data);
[[nodiscard]] std::string to_hex(const Sha256Digest& digest);

// Lowercase hex SHA-256 of `data`.
[[nodiscard]] std::string sha256_hex(std::string_view data);

}  // namespace Shiori::rates::dto
