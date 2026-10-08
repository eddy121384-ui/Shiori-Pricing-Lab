#pragma once
//
// Canonical JSON value tree + deterministic serialization.
//
// This file implements the *mechanism* that Issue #227 owns:
//   * canonical UTF-8 JSON representation;
//   * deterministic numeric/string serialization rules;
//   * deterministic object key ordering.
//
// Which FIELDS participate in a fingerprint preimage is owned by Issue #225 and is never decided
// here. See cpp/rates_engine/docs/CANONICALIZATION.md.
//
// Determinism rules enforced by this type (and proven by the determinism tests):
//   R1  Object members are ordered by the unsigned byte order of their UTF-8 key. Because UTF-8
//       preserves code-point order, this equals code-point ordering, but it is applied to BYTES.
//       Duplicate keys are refused.
//   R2  Arrays keep the order they were given. Domain ordering rules (for example "pillars sorted
//       strictly ascending by pillar_date") are enforced by the owning DTO, never by this type.
//   R3  Exactly one representation per value: no insignificant whitespace; `,` and `:` separators.
//   R4  Integers serialize as plain decimal integers. Floats serialize through the documented
//       ECMAScript Number::toString rule (canonical_number) so that C++ and Python agree byte for
//       byte. NaN and infinities are not representable and are refused.
//   R5  Strings are escaped minimally: `"`, `\`, and the named control escapes; other code points
//       below U+0020 as \u00XX; every other character is emitted as literal UTF-8. Lone surrogates
//       are not valid UTF-8 and are refused.
//   R6  `null` is a distinct value from "absent". A field that is absent is not serialized at all;
//       a field that is present with no value serializes as null.
//
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

namespace Shiori::rates::dto {

class CanonicalValue;

using CanonicalMember = std::pair<std::string, CanonicalValue>;
using CanonicalMembers = std::vector<CanonicalMember>;
using CanonicalItems = std::vector<CanonicalValue>;

// R4. Canonical textual form of a finite IEEE-754 double.
// Throws ContractViolation(kNumericNotRepresentable) for NaN/Infinity.
[[nodiscard]] std::string canonical_number(double value);

// True when `text` is well-formed UTF-8 with no lone surrogate encodings and no truncation.
[[nodiscard]] bool is_valid_utf8(std::string_view text) noexcept;

// R5. Appends the canonical JSON string literal (including the surrounding quotes) to `out`.
void append_canonical_json_string(std::string& out, std::string_view text);

class CanonicalValue {
 public:
  struct Object;
  struct Array;

  using Storage = std::variant<std::nullptr_t, bool, std::int64_t, double, std::string,
                               std::shared_ptr<const Object>, std::shared_ptr<const Array>>;

  CanonicalValue() noexcept : storage_(nullptr) {}
  CanonicalValue(std::nullptr_t) noexcept : storage_(nullptr) {}
  CanonicalValue(bool value) noexcept : storage_(value) {}
  CanonicalValue(std::int64_t value) noexcept : storage_(value) {}
  CanonicalValue(int value) noexcept : storage_(static_cast<std::int64_t>(value)) {}
  CanonicalValue(std::size_t value) : storage_(static_cast<std::int64_t>(value)) {
    // R4, as for double: refuse a value the canonical form cannot represent, at construction and
    // not by assigning into a default-constructed variant.
    if (value > static_cast<std::size_t>(kMaxInt64)) {
      fail_numeric();
    }
  }
  CanonicalValue(double value) : storage_(value) {
    // R4: refuse unrepresentable numerics at construction time rather than at dump time.
    if (std::isnan(value) || std::isinf(value)) {
      fail_numeric();
    }
  }
  CanonicalValue(std::string value) : storage_(std::move(value)) {}
  CanonicalValue(const char* value) : storage_(std::string(value)) {}

  // R1: sorts members by UTF-8 byte order and refuses duplicate keys.
  [[nodiscard]] static CanonicalValue make_object(CanonicalMembers members);
  // R2: preserves item order.
  [[nodiscard]] static CanonicalValue make_array(CanonicalItems items);

  [[nodiscard]] bool is_null() const noexcept;
  [[nodiscard]] bool is_bool() const noexcept;
  [[nodiscard]] bool is_integer() const noexcept;
  [[nodiscard]] bool is_float() const noexcept;
  [[nodiscard]] bool is_string() const noexcept;
  [[nodiscard]] bool is_object() const noexcept;
  [[nodiscard]] bool is_array() const noexcept;

  [[nodiscard]] const char* type_name() const noexcept;

  [[nodiscard]] bool as_bool() const;
  [[nodiscard]] std::int64_t as_integer() const;
  [[nodiscard]] double as_double() const;
  [[nodiscard]] const std::string& as_string() const;
  [[nodiscard]] const Object& as_object() const;
  [[nodiscard]] const Array& as_array() const;

  // Non-throwing object lookup. Returns nullptr when absent or when this is not an object.
  [[nodiscard]] const CanonicalValue* find(std::string_view key) const noexcept;
  // Requiring lookup: refuses a missing member with kMissingRequiredField.
  [[nodiscard]] const CanonicalValue& at(std::string_view key, const std::string& pointer) const;

  // R3-R6. Deterministic serialization to Canonical UTF-8 JSON.
  [[nodiscard]] std::string dump() const;

 private:
  static constexpr std::int64_t kMaxInt64 = 9223372036854775807LL;
  [[noreturn]] static void fail_numeric();

  Storage storage_;
};

struct CanonicalValue::Object {
  // Invariant: strictly ascending by key in unsigned byte order; no duplicates.
  CanonicalMembers members;
};

struct CanonicalValue::Array {
  CanonicalItems items;
};

// Parses UTF-8 JSON text into a CanonicalValue.
//
// The parser is strict about what the canonical mechanism can faithfully represent:
//   * integers outside signed 64-bit range are refused (kNumericNotRepresentable);
//   * duplicate object keys are refused (kDuplicateKey);
//   * malformed JSON is refused (kWrongType);
//   * invalid UTF-8 is refused (kInvalidUtf8);
//   * integers stay integers and floats stay floats (is_integer()/is_float()).
// No exception escapes as a non-Shiori type.
[[nodiscard]] CanonicalValue parse_canonical_json(std::string_view text);

// Reads a whole file as bytes. Refuses (ContractViolation) when unreadable.
[[nodiscard]] std::string read_text_file(const std::string& path);

}  // namespace Shiori::rates::dto
