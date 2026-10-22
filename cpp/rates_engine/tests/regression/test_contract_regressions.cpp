// Regression anchors for the two mechanisms whose exact bytes are contract.
//
// The unit tests above prove behaviour; this file pins the VALUES. SHA-256 is pinned to the FIPS 180-4
// published vectors, and the canonical number form is pinned at every branch boundary of the
// ECMAScript rule. If one of these moves, every fingerprint already stored in this repository (or in a
// peer's store) is invalidated, so the change is a contract change and must not be made quietly.
#include <gtest/gtest.h>

#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/sha256.hpp"

namespace {

using Shiori::rates::dto::canonical_number;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::sha256_hex;

}  // namespace

TEST(Sha256Regression, MatchesPublishedFips180_4Vectors) {
  // FIPS 180-4 published test vectors. These pin the digest algorithm itself; a "small" change to
  // padding or byte order would break every stored fingerprint.
  EXPECT_EQ(sha256_hex(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  EXPECT_EQ(sha256_hex("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  EXPECT_EQ(sha256_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  // A multi-block input exercises the length encoding across a block boundary.
  const std::string million_a(1000000, 'a');
  EXPECT_EQ(sha256_hex(million_a),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
}

TEST(CanonicalNumberRegression, PinsEveryBranchBoundaryOfTheRule) {
  // 1e-6 is the last value rendered in fixed notation; 1e-7 starts scientific notation.
  EXPECT_EQ(canonical_number(0.000001), "0.000001");
  EXPECT_EQ(canonical_number(0.0000001), "1e-7");
  // 1e20 is the last value rendered with all digits; 1e21 switches to scientific with an explicit
  // '+' sign, which is exactly the kind of detail a hand-written formatter gets wrong.
  EXPECT_EQ(canonical_number(1e20), "100000000000000000000");
  EXPECT_EQ(canonical_number(1e21), "1e+21");
  EXPECT_EQ(canonical_number(-1e21), "-1e+21");
  // Integral doubles never gain a trailing ".0".
  EXPECT_EQ(canonical_number(100.0), "100");
  EXPECT_EQ(canonical_number(-0.0), "0");
  // The shortest round-trip rule: these are the shortest forms that restore the same double.
  EXPECT_EQ(canonical_number(0.1), "0.1");
  EXPECT_EQ(canonical_number(0.30000000000000004), "0.30000000000000004");
  EXPECT_EQ(canonical_number(123456789012345680.0), "123456789012345680");
}

TEST(CanonicalRegression, PreservesTheIntegerVersusFloatDistinction) {
  // A document that says 1 and a document that says 1.0 are the same JSON *number*, and the canonical
  // form collapses them -- but only because both round-trip to the same value. What must NOT happen is
  // an integer silently becoming a float (which would print differently in a diagnostic) or a float
  // being truncated.
  EXPECT_EQ(Shiori::rates::dto::parse_canonical_json(R"({"a":1})").dump(), R"({"a":1})");
  EXPECT_EQ(Shiori::rates::dto::parse_canonical_json(R"({"a":1.0})").dump(), R"({"a":1})");
  EXPECT_EQ(Shiori::rates::dto::parse_canonical_json(R"({"a":9007199254740993})").dump(),
            R"({"a":9007199254740993})");
}

TEST(CanonicalRegression, KeyOrderIsByteOrderNotLocaleOrder) {
  // The single most fragile ordering case: a non-ASCII key must sort AFTER every ASCII key because its
  // first byte is >= 0x80. A signed char comparison would place it before "A".
  const CanonicalValue value = CanonicalValue::make_object({
      {"é", CanonicalValue(1)},
      {"A", CanonicalValue(2)},
      {"z", CanonicalValue(3)},
  });
  EXPECT_EQ(value.dump(), R"({"A":2,"z":3,"é":1})");
}

TEST(FingerprintRegression, PreimageIsTheCanonicalBytesAndNothingElse) {
  // The fingerprint must be a function of the canonical bytes only. Whitespace or key order in the
  // INPUT must not reach it: two differently written but logically identical documents must share a
  // fingerprint, or replay verification would depend on formatting.
  const std::string first = R"({"a":1,"b":[1,2]})";
  const std::string second = "{  \"b\" : [ 1, 2 ] ,\n \"a\" : 1 }";
  const CanonicalValue parsed_first = Shiori::rates::dto::parse_canonical_json(first);
  const CanonicalValue parsed_second = Shiori::rates::dto::parse_canonical_json(second);
  EXPECT_EQ(Shiori::rates::dto::fingerprint_of(parsed_first),
            Shiori::rates::dto::fingerprint_of(parsed_second));
  // And a one-character difference must change it.
  EXPECT_NE(Shiori::rates::dto::fingerprint_of(parsed_first),
            Shiori::rates::dto::fingerprint_of(
                Shiori::rates::dto::parse_canonical_json(R"({"a":1,"b":[1,3]})")));
}
