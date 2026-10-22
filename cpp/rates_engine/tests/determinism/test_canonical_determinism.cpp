// Determinism of the canonical mechanism, and the immutability surface of published request state.
//
// The canonical form has to be reproducible from the same logical content regardless of how that
// content was assembled: a map built in one key order and the same map parsed from JSON must produce
// identical bytes and therefore an identical fingerprint. If that is not true, a stored fingerprint
// cannot be recomputed by another process, which invalidates the entire replay contract.
#include <gtest/gtest.h>

#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/immutable.hpp"
#include "shiori_rates/dto/sha256.hpp"

namespace {

using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;

[[nodiscard]] CanonicalValue sample_document(bool reversed_insertion_order) {
  if (reversed_insertion_order) {
    return CanonicalValue::make_object({
        {"zeta", CanonicalValue(1)},
        {"alpha", CanonicalValue::make_array({CanonicalValue(true), CanonicalValue(2),
                                              CanonicalValue(std::string("café"))})},
        {"mid", CanonicalValue(0.0425)},
    });
  }
  return CanonicalValue::make_object({
      {"alpha", CanonicalValue::make_array({CanonicalValue(true), CanonicalValue(2),
                                            CanonicalValue(std::string("café"))})},
      {"mid", CanonicalValue(0.0425)},
      {"zeta", CanonicalValue(1)},
  });
}

}  // namespace

TEST(CanonicalDeterminism, IsIndependentOfAssemblyOrder) {
  const CanonicalValue first = sample_document(false);
  const CanonicalValue second = sample_document(true);
  EXPECT_EQ(first.dump(), second.dump());
  EXPECT_EQ(Shiori::rates::dto::fingerprint_of(first),
            Shiori::rates::dto::fingerprint_of(second));
}

TEST(CanonicalDeterminism, IsStableAcrossRepeatedSerialization) {
  const CanonicalValue document = sample_document(false);
  const std::string expected = document.dump();
  const std::string expected_fingerprint = Shiori::rates::dto::fingerprint_of(document);
  for (int iteration = 0; iteration < 200; ++iteration) {
    const CanonicalValue reparsed = Shiori::rates::dto::parse_canonical_json(expected);
    ASSERT_EQ(reparsed.dump(), expected) << "iteration " << iteration;
    ASSERT_EQ(Shiori::rates::dto::fingerprint_of(reparsed), expected_fingerprint)
        << "iteration " << iteration;
  }
}

TEST(CanonicalDeterminism, FingerprintEqualsTheDigestOfTheCanonicalBytes) {
  // Cross-check the fingerprint helper against SHA-256 applied directly to the canonical bytes: the
  // digest must be a function of the BYTES, not of the in-memory value, because a peer recomputes it
  // from stored bytes.
  const CanonicalValue document = sample_document(false);
  const std::string bytes = document.dump();
  EXPECT_EQ(Shiori::rates::dto::fingerprint_of(document), Shiori::rates::dto::sha256_hex(bytes));
  EXPECT_EQ(Shiori::rates::dto::fingerprint_of_canonical_bytes(bytes),
            Shiori::rates::dto::sha256_hex(bytes));
  EXPECT_EQ(Shiori::rates::dto::fingerprint_of(document),
            Shiori::rates::dto::fingerprint_of_canonical_bytes(bytes));
}

TEST(CanonicalDeterminism, EscapedStringsRoundTripToTheSameValue) {
  // Escaping is minimal and reversible: parsing the escaped form must yield the identical document,
  // so escaping cannot be a lossy step that changes a fingerprint.
  const std::string raw = "quote\" back\\slash \x01\x1f tab\tnl\ncafé 東京";
  std::string escaped;
  Shiori::rates::dto::append_canonical_json_string(escaped, raw);
  std::string again;
  Shiori::rates::dto::append_canonical_json_string(again, raw);
  EXPECT_EQ(escaped, again);

  const CanonicalValue document =
      CanonicalValue::make_object({{"text", CanonicalValue(raw)}});
  const CanonicalValue reparsed = Shiori::rates::dto::parse_canonical_json(document.dump());
  EXPECT_EQ(reparsed.dump(), document.dump());
  EXPECT_NE(document.dump().find("\\u0001"), std::string::npos);
  EXPECT_NE(document.dump().find("\\u001f"), std::string::npos);
  // Non-ASCII is emitted as literal UTF-8, never as \\u escapes: an escape here would still be valid
  // JSON but would change the bytes and therefore every fingerprint of a non-ASCII document.
  EXPECT_NE(document.dump().find("café"), std::string::npos);
  EXPECT_EQ(document.dump().find("\\u00e9"), std::string::npos);
}

TEST(PublishedKernelInput, IsEmptyUntilPublishedAndThenExposesOnlyImmutableState) {
  // The default-constructed published request has no state, and reading it refuses rather than
  // returning an empty document that a caller could mistake for a published request.
  const Shiori::rates::dto::PublishedKernelInput empty;
  EXPECT_FALSE(empty.valid());
  EXPECT_EQ(empty.canonical_size(), 0U);
  EXPECT_THROW((void)empty.frozen(), ContractViolation);
  EXPECT_THROW((void)empty.canonical_view(), ContractViolation);
  EXPECT_THROW((void)empty.content_fingerprint(), ContractViolation);
}
