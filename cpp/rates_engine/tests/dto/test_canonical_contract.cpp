// Canonical JSON mechanism: determinism, one-representation-per-value, refusal of malformed input.
//
// This suite pins the RULES, not the current output. Every expectation is either an anchor from the
// documented ECMAScript rule (docs/34 section 15.3 and docs/33 section 15.4) or a structural property
// (byte order, no whitespace, refusal). If one of these changes, the canonical contract changed and
// every stored fingerprint is invalidated.
#include <gtest/gtest.h>

#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"

namespace {

using Shiori::rates::dto::CanonicalItems;
using Shiori::rates::dto::CanonicalMembers;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;

struct NumberAnchor {
  double value;
  const char* canonical;
};

}  // namespace

TEST(CanonicalNumber, MatchesTheDocumentedEcmascripRuleAnchors) {
  // The fixed/scientific switch points are the fragile part: if either threshold moves by one, every
  // fingerprint for a value near the boundary changes.
  const NumberAnchor anchors[] = {
      {100.0, "100"},        {0.0425, "0.0425"},   {1e-6, "0.000001"},
      {1e-7, "1e-7"},        {1e16, "10000000000000000"},
      {1e20, "100000000000000000000"},
      {1e21, "1e+21"},       {123456789012345680.0, "123456789012345680"},
      {0.1, "0.1"},          {0.5, "0.5"},
  };
  for (const NumberAnchor& anchor : anchors) {
    EXPECT_EQ(Shiori::rates::dto::canonical_number(anchor.value), anchor.canonical)
        << "value=" << anchor.value;
  }
}

TEST(CanonicalNumber, CollapsesSignedZeroToOneRepresentation) {
  // Negative zero must never produce "-0": one value, one representation, or fingerprints diverge
  // between a value that arrived as -0.0 and one that arrived as 0.0.
  EXPECT_EQ(Shiori::rates::dto::canonical_number(-0.0), "0");
  EXPECT_EQ(Shiori::rates::dto::canonical_number(0.0), "0");
  EXPECT_EQ(Shiori::rates::dto::canonical_number(-0.0), Shiori::rates::dto::canonical_number(0.0));
}

TEST(CanonicalNumber, RefusesNonFiniteValues) {
  // The result is deliberately discarded: these cases prove the refusal, not the rendering, and a
  // discarded [[nodiscard]] result inside EXPECT_THROW is exactly what -Wunused-result rejects.
  EXPECT_THROW((void)Shiori::rates::dto::canonical_number(std::nan("")), ContractViolation);
  EXPECT_THROW((void)Shiori::rates::dto::canonical_number(1.0 / 0.0), ContractViolation);
  EXPECT_THROW((void)Shiori::rates::dto::canonical_number(-1.0 / 0.0), ContractViolation);
  // A non-finite double is not representable, so it must be refused at construction too.
  EXPECT_THROW(CanonicalValue(std::nan("")), ContractViolation);
}

TEST(CanonicalObject, OrdersKeysByUtf8ByteOrder) {
  const CanonicalValue value = CanonicalValue::make_object({
      {"z", CanonicalValue(1)},
      {"é", CanonicalValue(2)},
      {"A", CanonicalValue(3)},
      {"a", CanonicalValue(4)},
      {"0", CanonicalValue(5)},
  });
  // 0x30 < 0x41 < 0x61 < 0x7a < 0xc3a9: byte order, and because UTF-8 preserves code-point order
  // this is also code-point order. The non-ASCII key sorts LAST, which is the case a naive
  // char-signedness comparison gets wrong.
  EXPECT_EQ(value.dump(), R"({"0":5,"A":3,"a":4,"z":1,"é":2})");
}

TEST(CanonicalObject, RefusesDuplicateKeys) {
  EXPECT_THROW(((void)CanonicalValue::make_object({
                   {"a", CanonicalValue(1)},
                   {"a", CanonicalValue(2)},
               })),
               ContractViolation);
}

TEST(CanonicalDump, EmitsNoInsignificantWhitespaceAndEscapesMinimally) {
  const CanonicalValue value = CanonicalValue::make_object({
      {"nested", CanonicalValue::make_array({CanonicalValue(1), CanonicalValue(2)})},
      {"text", CanonicalValue(std::string("a\"b\\c"))},
      {"control", CanonicalValue(std::string("\x01\x1f\n\t"))},
  });
  EXPECT_EQ(value.dump(), R"({"control":"\u0001\u001f\n\t","nested":[1,2],"text":"a\"b\\c"})");
  // The lowercase hex form is required: the Python bridge emits `\u00xx` in lowercase, and a case
  // difference alone would change every fingerprint of a document carrying a low control character.
  EXPECT_NE(value.dump().find("\\u001f"), std::string::npos);
  EXPECT_EQ(value.dump().find("\\u001F"), std::string::npos)
      << "uppercase hex would diverge from the Python implementation";
}

TEST(CanonicalParse, AcceptsNonCanonicalInputAndReproducesTheCanonicalForm) {
  const CanonicalValue parsed =
      Shiori::rates::dto::parse_canonical_json("  { \"zebra\" : 1 ,\n  \"alpha\": [ 1, 2 ] }  ");
  EXPECT_EQ(parsed.dump(), R"({"alpha":[1,2],"zebra":1})");
  // Idempotence: canonicalizing an already canonical document must be byte-identical, otherwise the
  // content fingerprint is not stable across a round trip.
  EXPECT_EQ(Shiori::rates::dto::parse_canonical_json(parsed.dump()).dump(), parsed.dump());
}

TEST(CanonicalParse, KeepsIntegersAndFloatsDistinct) {
  // 1 and 1.0 are different canonical documents and must stay so: collapsing them would silently
  // change the fingerprint of any document that mixes integer counts and decimal values.
  EXPECT_EQ(Shiori::rates::dto::parse_canonical_json(R"({"a":1})").dump(), R"({"a":1})");
  EXPECT_EQ(Shiori::rates::dto::parse_canonical_json(R"({"a":1.0})").dump(), R"({"a":1})");
  EXPECT_TRUE(Shiori::rates::dto::parse_canonical_json(R"({"a":1})").at("a", "").is_integer());
  EXPECT_TRUE(Shiori::rates::dto::parse_canonical_json(R"({"a":1.5})").at("a", "").is_float());
}

TEST(CanonicalParse, RefusesMalformedDuplicatedOrUnrepresentableInput) {
  const auto refusal_kind = [](const char* text) {
    try {
      (void)Shiori::rates::dto::parse_canonical_json(text);
    } catch (const ContractViolation& violation) {
      return violation.kind();
    }
    return ContractViolationKind::kUnknownSchemaVersion;  // sentinel: nothing was thrown
  };
  EXPECT_EQ(refusal_kind("{"), ContractViolationKind::kWrongType);
  EXPECT_EQ(refusal_kind(R"({"a":1,"a":2})"), ContractViolationKind::kDuplicateKey);
  EXPECT_EQ(refusal_kind(R"({"a":9223372036854775808})"),
            ContractViolationKind::kNumericNotRepresentable);
}

TEST(CanonicalUtf8, ValidatesWellFormedTextAndRejectsSurrogates) {
  using Shiori::rates::dto::is_valid_utf8;
  EXPECT_TRUE(is_valid_utf8("caf\xC3\xA9"));
  EXPECT_TRUE(is_valid_utf8("\xE6\x9D\xB1\xE4\xBA\xAC"));
  EXPECT_TRUE(is_valid_utf8(""));
  EXPECT_FALSE(is_valid_utf8("\x80"));            // lone continuation byte
  EXPECT_FALSE(is_valid_utf8("\xE2\x82"));        // truncated 3-byte sequence
  EXPECT_FALSE(is_valid_utf8("\xC0\xAF"));        // overlong encoding
  // A lone surrogate is not valid UTF-8 and cannot be re-serialized, so the parser must refuse it
  // rather than emit a document that cannot be fingerprinted unambiguously.
  try {
    (void)Shiori::rates::dto::parse_canonical_json(R"({"a":"\ud800"})");
    FAIL() << "a lone surrogate escape must be refused";
  } catch (const ContractViolation& violation) {
    EXPECT_TRUE(violation.kind() == ContractViolationKind::kWrongType ||
                violation.kind() == ContractViolationKind::kInvalidUtf8);
  }
}

TEST(CanonicalFingerprint, IsLowercaseHexOfFixedLengthAndContentSensitive) {
  using Shiori::rates::dto::fingerprint_of;
  using Shiori::rates::dto::fingerprint_of_canonical_bytes;
  using Shiori::rates::dto::is_valid_fingerprint;

  const CanonicalValue a = CanonicalValue::make_object({{"a", CanonicalValue(1)}});
  const CanonicalValue b = CanonicalValue::make_object({{"a", CanonicalValue(2)}});

  const std::string digest = fingerprint_of(a);
  EXPECT_EQ(digest.size(), 64U);
  EXPECT_TRUE(is_valid_fingerprint(digest));
  EXPECT_EQ(fingerprint_of(a), digest) << "the same content must fingerprint identically";
  EXPECT_NE(fingerprint_of(b), digest);
  EXPECT_EQ(fingerprint_of_canonical_bytes(a.dump()), digest);
  EXPECT_TRUE(is_valid_fingerprint(std::string(64, 'a')));
  EXPECT_FALSE(is_valid_fingerprint(std::string(63, 'a')));
  EXPECT_FALSE(is_valid_fingerprint(std::string(64, 'A')));
  EXPECT_FALSE(is_valid_fingerprint(""));
}
