// Python <-> C++ canonicalization parity, driven by a tracked fixture file.
//
// WHY A FIXTURE AND NOT TWO INDEPENDENT EXPECTATIONS: the canonical form is a CROSS-LANGUAGE contract.
// The Python application writes canonical documents and the C++ engine must reproduce the identical
// bytes and the identical SHA-256 digest, or a fingerprint stored by one side cannot be verified by
// the other. The fixture is generated from the Python implementation (itself verified against V8's
// `String(number)`), so this suite transfers an independent oracle into the C++ build instead of
// comparing C++ with itself.
//
// A failure here means the two implementations disagree; it never means "refresh the fixture".
#include <gtest/gtest.h>

#include <cstdint>
#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/fingerprint.hpp"

namespace {

using Shiori::rates::dto::CanonicalValue;

#ifndef SHIORI_FIXTURE_DIR
#error "SHIORI_FIXTURE_DIR must be defined by the build for the parity suite"
#endif

[[nodiscard]] CanonicalValue load_fixture_root() {
  const std::string path = std::string(SHIORI_FIXTURE_DIR) + "/canonical_parity_fixtures.json";
  return Shiori::rates::dto::parse_canonical_json(Shiori::rates::dto::read_text_file(path));
}

[[nodiscard]] std::string string_member(const CanonicalValue& node, const char* key) {
  const CanonicalValue* member = node.find(key);
  EXPECT_NE(member, nullptr) << key;
  return member != nullptr && member->is_string() ? member->as_string() : std::string();
}

[[nodiscard]] double number_member(const CanonicalValue& node, const char* key) {
  const CanonicalValue* member = node.find(key);
  EXPECT_NE(member, nullptr) << key;
  if (member == nullptr) {
    return 0.0;
  }
  if (member->is_float()) {
    return member->as_double();
  }
  return static_cast<double>(member->as_integer());
}

}  // namespace

TEST(CanonicalParity, FixtureIsPresentAndCarriesRealExpectations) {
  const CanonicalValue root = load_fixture_root();
  EXPECT_EQ(string_member(root, "schema_version"), "SHIORI_CANONICAL_PARITY_V1");
  const CanonicalValue* numbers = root.find("numbers");
  const CanonicalValue* documents = root.find("documents");
  ASSERT_NE(numbers, nullptr);
  ASSERT_NE(documents, nullptr);
  ASSERT_TRUE(numbers->is_array());
  ASSERT_TRUE(documents->is_array());
  EXPECT_GT(numbers->as_array().items.size(), 500U);
  EXPECT_GT(documents->as_array().items.size(), 4U);
}

TEST(CanonicalParity, CppReproducesEveryPythonNumberForm) {
  const CanonicalValue root = load_fixture_root();
  const CanonicalValue& numbers = *root.find("numbers");
  std::size_t checked = 0;
  for (const CanonicalValue& entry : numbers.as_array().items) {
    const double value = number_member(entry, "value");
    EXPECT_EQ(Shiori::rates::dto::canonical_number(value), string_member(entry, "canonical"))
        << "value=" << value;
    ++checked;
  }
  EXPECT_EQ(checked, numbers.as_array().items.size());
}

TEST(CanonicalParity, CppReproducesEveryPythonDocumentAndFingerprint) {
  const CanonicalValue root = load_fixture_root();
  const CanonicalValue& documents = *root.find("documents");
  std::size_t checked = 0;
  for (const CanonicalValue& entry : documents.as_array().items) {
    const CanonicalValue* value_tree = entry.find("json");
    const std::string input_text = string_member(entry, "json_text");
    const std::string expected_canonical = string_member(entry, "canonical");
    const std::string expected_fingerprint = string_member(entry, "fingerprint");
    ASSERT_NE(value_tree, nullptr) << string_member(entry, "name");

    // (1) Canonicalizing the VALUE TREE must reproduce the Python bytes. This mirrors the Python
    //     assertion `dumps(entry["json"]) == entry["canonical"]` exactly, so the two languages are
    //     compared through the same input shape.
    EXPECT_EQ(value_tree->dump(), expected_canonical) << string_member(entry, "name");
    // (2) Parsing the deliberately NON-canonical SOURCE TEXT must produce the same bytes, which proves
    //     whitespace removal and key ordering agree across languages.
    const CanonicalValue parsed = Shiori::rates::dto::parse_canonical_json(input_text);
    EXPECT_EQ(parsed.dump(), expected_canonical) << string_member(entry, "name");
    // (3) The fingerprint is the digest of the canonical BYTES, so both sides must land on the same
    //     digest for the same document.
    EXPECT_EQ(Shiori::rates::dto::fingerprint_of_canonical_bytes(expected_canonical),
              expected_fingerprint)
        << string_member(entry, "name");
    ++checked;
  }
  EXPECT_EQ(checked, documents.as_array().items.size());
}
