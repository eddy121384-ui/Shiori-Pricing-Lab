#include "shiori_rates/dto/canonical.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <utility>

#include <nlohmann/json.hpp>

#include "shiori_rates/dto/error.hpp"

namespace Shiori::rates::dto {

namespace {

constexpr char kHexDigits[] = "0123456789abcdef";

void append_hex4(std::string& out, unsigned value) {
  out += "\\u00";
  out += kHexDigits[(value >> 4) & 0x0FU];
  out += kHexDigits[value & 0x0FU];
}

// True when the byte sequence starting at `index` is a well-formed UTF-8 scalar value.
// Returns the sequence length, or 0 on error. Surrogates are rejected explicitly: a JSON text is
// a Unicode string, and a lone surrogate cannot be represented in UTF-8 at all.
std::size_t utf8_sequence_length(const std::string_view text, std::size_t index) {
  const unsigned char lead = static_cast<unsigned char>(text[index]);
  std::size_t length = 0;
  std::uint32_t code_point = 0;

  if (lead < 0x80U) {
    return 1;
  }
  if ((lead & 0xE0U) == 0xC0U) {
    length = 2;
    code_point = lead & 0x1FU;
  } else if ((lead & 0xF0U) == 0xE0U) {
    length = 3;
    code_point = lead & 0x0FU;
  } else if ((lead & 0xF8U) == 0xF0U) {
    length = 4;
    code_point = lead & 0x07U;
  } else {
    return 0;
  }
  if (index + length > text.size()) {
    return 0;
  }
  for (std::size_t offset = 1; offset < length; ++offset) {
    const unsigned char continuation = static_cast<unsigned char>(text[index + offset]);
    if ((continuation & 0xC0U) != 0x80U) {
      return 0;
    }
    code_point = (code_point << 6U) | (continuation & 0x3FU);
  }
  // Reject overlong encodings, surrogates and out-of-range code points.
  if (length == 2 && code_point < 0x80U) {
    return 0;
  }
  if (length == 3 && code_point < 0x800U) {
    return 0;
  }
  if (length == 4 && code_point < 0x10000U) {
    return 0;
  }
  if (code_point >= 0xD800U && code_point <= 0xDFFFU) {
    return 0;
  }
  if (code_point > 0x10FFFFU) {
    return 0;
  }
  return length;
}

void dump_into(const CanonicalValue& value, std::string& out);

void dump_object(const CanonicalValue::Object& object, std::string& out) {
  out += '{';
  bool first = true;
  for (const auto& member : object.members) {
    if (!first) {
      out += ',';
    }
    first = false;
    append_canonical_json_string(out, member.first);
    out += ':';
    dump_into(member.second, out);
  }
  out += '}';
}

void dump_array(const CanonicalValue::Array& array, std::string& out) {
  out += '[';
  bool first = true;
  for (const auto& item : array.items) {
    if (!first) {
      out += ',';
    }
    first = false;
    dump_into(item, out);
  }
  out += ']';
}

void dump_into(const CanonicalValue& value, std::string& out) {
  if (value.is_null()) {
    out += "null";
  } else if (value.is_bool()) {
    out += value.as_bool() ? "true" : "false";
  } else if (value.is_integer()) {
    out += std::to_string(value.as_integer());
  } else if (value.is_float()) {
    out += canonical_number(value.as_double());
  } else if (value.is_string()) {
    append_canonical_json_string(out, value.as_string());
  } else if (value.is_object()) {
    dump_object(value.as_object(), out);
  } else {
    dump_array(value.as_array(), out);
  }
}

// ---------------------------------------------------------------------------------------------
// nlohmann -> CanonicalValue conversion.
//
// nlohmann is used ONLY as the parser. Canonical OUTPUT never goes through nlohmann's serializer,
// so the output format is owned end-to-end by canonical_number/append_canonical_json_string.
// ---------------------------------------------------------------------------------------------
CanonicalValue convert(const nlohmann::json& input, const std::string& pointer) {
  switch (input.type()) {
    case nlohmann::json::value_t::null:
      return CanonicalValue(nullptr);
    case nlohmann::json::value_t::boolean:
      return CanonicalValue(input.get<bool>());
    case nlohmann::json::value_t::number_integer:
      return CanonicalValue(static_cast<std::int64_t>(input.get<std::int64_t>()));
    case nlohmann::json::value_t::number_unsigned: {
      const auto raw = input.get<std::uint64_t>();
      if (raw > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) {
        fail(ContractViolationKind::kNumericNotRepresentable, pointer,
             "unsigned JSON integer exceeds the signed 64-bit domain");
      }
      return CanonicalValue(static_cast<std::int64_t>(raw));
    }
    case nlohmann::json::value_t::number_float:
      return CanonicalValue(input.get<double>());
    case nlohmann::json::value_t::string: {
      const std::string& text = input.get_ref<const std::string&>();
      if (!is_valid_utf8(text)) {
        fail(ContractViolationKind::kInvalidUtf8, pointer,
             "string is not well-formed UTF-8 (or contains a lone surrogate)");
      }
      return CanonicalValue(text);
    }
    case nlohmann::json::value_t::array: {
      CanonicalItems items;
      const auto& raw_array = input.get_ref<const nlohmann::json::array_t&>();
      items.reserve(raw_array.size());
      for (std::size_t index = 0; index < raw_array.size(); ++index) {
        items.push_back(convert(raw_array[index], pointer_index(pointer, index)));
      }
      return CanonicalValue::make_array(std::move(items));
    }
    case nlohmann::json::value_t::object: {
      CanonicalMembers members;
      const auto& raw_object = input.get_ref<const nlohmann::json::object_t&>();
      members.reserve(raw_object.size());
      for (const auto& entry : raw_object) {
        if (!is_valid_utf8(entry.first)) {
          fail(ContractViolationKind::kInvalidUtf8, pointer_child(pointer, entry.first),
               "object key is not well-formed UTF-8");
        }
        members.emplace_back(entry.first, convert(entry.second, pointer_child(pointer, entry.first)));
      }
      return CanonicalValue::make_object(std::move(members));
    }
    case nlohmann::json::value_t::binary:
    case nlohmann::json::value_t::discarded:
    default:
      fail(ContractViolationKind::kWrongType, pointer,
           "value is not representable in the canonical JSON subset");
  }
}

}  // namespace

bool is_valid_utf8(const std::string_view text) noexcept {
  std::size_t index = 0;
  while (index < text.size()) {
    const std::size_t length = utf8_sequence_length(text, index);
    if (length == 0) {
      return false;
    }
    index += length;
  }
  return true;
}

void append_canonical_json_string(std::string& out, const std::string_view text) {
  if (!is_valid_utf8(text)) {
    fail(ContractViolationKind::kInvalidUtf8, "",
         "cannot serialize a string that is not well-formed UTF-8");
  }
  out += '"';
  for (const char raw : text) {
    const unsigned char byte = static_cast<unsigned char>(raw);
    switch (byte) {
      case '"':
        out += "\\\"";
        break;
      case '\\':
        out += "\\\\";
        break;
      case '\b':
        out += "\\b";
        break;
      case '\f':
        out += "\\f";
        break;
      case '\n':
        out += "\\n";
        break;
      case '\r':
        out += "\\r";
        break;
      case '\t':
        out += "\\t";
        break;
      default:
        if (byte < 0x20U) {
          append_hex4(out, byte);
        } else {
          out += raw;
        }
        break;
    }
  }
  out += '"';
}

std::string canonical_number(double value) {
  if (std::isnan(value) || std::isinf(value)) {
    fail(ContractViolationKind::kNumericNotRepresentable, "",
         "NaN and infinity are not representable in canonical JSON");
  }
  if (value == 0.0) {
    // Covers both +0.0 and -0.0: the canonical form of either is "0".
    return "0";
  }

  const bool negative = std::signbit(value);
  const double magnitude = negative ? -value : value;

  std::array<char, 64> buffer{};
  const auto result = std::to_chars(buffer.data(), buffer.data() + buffer.size(), magnitude,
                                    std::chars_format::scientific);
  if (result.ec != std::errc()) {
    fail(ContractViolationKind::kNumericNotRepresentable, "",
         "std::to_chars could not produce a shortest round-trip representation");
  }

  // `to_chars(scientific)` with no precision yields the shortest round-tripping mantissa in the
  // form `d[.ddd]e±dd`. Decompose it into (digits, n) where value == digits * 10^(n - k) and
  // k == digits.size(), with no leading or trailing zeros in `digits`.
  const std::string_view text(buffer.data(), static_cast<std::size_t>(result.ptr - buffer.data()));
  const std::size_t exponent_pos = text.find('e');
  const std::string_view mantissa = text.substr(0, exponent_pos);
  const std::string_view exponent_text = text.substr(exponent_pos + 1);

  std::string digits;
  digits.reserve(mantissa.size());
  std::size_t integer_digits = 0;
  for (const char character : mantissa) {
    if (character == '.') {
      integer_digits = digits.size();
      continue;
    }
    digits += character;
  }
  if (integer_digits == 0) {
    integer_digits = digits.size();
  }

  int exponent = 0;
  {
    const auto parsed = std::from_chars(exponent_text.data(),
                                        exponent_text.data() + exponent_text.size(), exponent);
    if (parsed.ec != std::errc()) {
      fail(ContractViolationKind::kNumericNotRepresentable, "",
           "std::to_chars produced an unparsable exponent");
    }
  }

  // `digits` is already free of leading zeros (the mantissa always starts with a non-zero digit)
  // and of trailing zeros (the representation is shortest).
  const std::size_t k = digits.size();
  const long long n = static_cast<long long>(exponent) + 1;  // value == digits * 10^(n-k)

  std::string out;
  if (negative) {
    out += '-';
  }
  if (static_cast<long long>(k) <= n && n <= 21) {
    out += digits;
    out.append(static_cast<std::size_t>(n - static_cast<long long>(k)), '0');
  } else if (0 < n && n <= 21) {
    out += digits.substr(0, static_cast<std::size_t>(n));
    out += '.';
    out += digits.substr(static_cast<std::size_t>(n));
  } else if (-6 < n && n <= 0) {
    out += "0.";
    out.append(static_cast<std::size_t>(-n), '0');
    out += digits;
  } else {
    const long long e = n - 1;
    if (k == 1) {
      out += digits;
    } else {
      out += digits.substr(0, 1);
      out += '.';
      out += digits.substr(1);
    }
    out += 'e';
    out += (e >= 0) ? '+' : '-';
    out += std::to_string(e >= 0 ? e : -e);
  }
  return out;
}

CanonicalValue CanonicalValue::make_object(CanonicalMembers members) {
  std::stable_sort(members.begin(), members.end(),
                   [](const CanonicalMember& left, const CanonicalMember& right) {
                     // R1: unsigned byte order of the UTF-8 key. std::string comparison is
                     // implemented with unsigned char semantics (char_traits<char>::compare), so
                     // it is already byte-ordered and matches code-point order for valid UTF-8.
                     return left.first < right.first;
                   });
  for (std::size_t index = 1; index < members.size(); ++index) {
    if (members[index - 1].first == members[index].first) {
      fail(ContractViolationKind::kDuplicateKey,
           pointer_child("", members[index].first),
           "duplicate object key is not representable in canonical JSON");
    }
  }
  auto object = std::make_shared<Object>();
  object->members = std::move(members);
  // Emplace the active alternative instead of assigning into the variant: assignment instantiates
  // every alternative's assignment operator, including the shared_ptr one.
  CanonicalValue value;
  value.storage_.emplace<std::shared_ptr<const Object>>(std::move(object));
  return value;
}

CanonicalValue CanonicalValue::make_array(CanonicalItems items) {
  auto array = std::make_shared<Array>();
  array->items = std::move(items);
  CanonicalValue value;
  value.storage_.emplace<std::shared_ptr<const Array>>(std::move(array));
  return value;
}

bool CanonicalValue::is_null() const noexcept {
  return std::holds_alternative<std::nullptr_t>(storage_);
}
bool CanonicalValue::is_bool() const noexcept { return std::holds_alternative<bool>(storage_); }
bool CanonicalValue::is_integer() const noexcept {
  return std::holds_alternative<std::int64_t>(storage_);
}
bool CanonicalValue::is_float() const noexcept { return std::holds_alternative<double>(storage_); }
bool CanonicalValue::is_string() const noexcept {
  return std::holds_alternative<std::string>(storage_);
}
bool CanonicalValue::is_object() const noexcept {
  return std::holds_alternative<std::shared_ptr<const Object>>(storage_);
}
bool CanonicalValue::is_array() const noexcept {
  return std::holds_alternative<std::shared_ptr<const Array>>(storage_);
}

const char* CanonicalValue::type_name() const noexcept {
  if (is_null()) {
    return "null";
  }
  if (is_bool()) {
    return "boolean";
  }
  if (is_integer()) {
    return "integer";
  }
  if (is_float()) {
    return "number";
  }
  if (is_string()) {
    return "string";
  }
  if (is_object()) {
    return "object";
  }
  return "array";
}

bool CanonicalValue::as_bool() const {
  if (!is_bool()) {
    fail_wrong_type("", "boolean", type_name());
  }
  return std::get<bool>(storage_);
}

std::int64_t CanonicalValue::as_integer() const {
  if (!is_integer()) {
    fail_wrong_type("", "integer", type_name());
  }
  return std::get<std::int64_t>(storage_);
}

double CanonicalValue::as_double() const {
  if (is_integer()) {
    return static_cast<double>(std::get<std::int64_t>(storage_));
  }
  if (!is_float()) {
    fail_wrong_type("", "number", type_name());
  }
  return std::get<double>(storage_);
}

const std::string& CanonicalValue::as_string() const {
  if (!is_string()) {
    fail_wrong_type("", "string", type_name());
  }
  return std::get<std::string>(storage_);
}

const CanonicalValue::Object& CanonicalValue::as_object() const {
  if (!is_object()) {
    fail_wrong_type("", "object", type_name());
  }
  return *std::get<std::shared_ptr<const Object>>(storage_);
}

const CanonicalValue::Array& CanonicalValue::as_array() const {
  if (!is_array()) {
    fail_wrong_type("", "array", type_name());
  }
  return *std::get<std::shared_ptr<const Array>>(storage_);
}

const CanonicalValue* CanonicalValue::find(const std::string_view key) const noexcept {
  if (!is_object()) {
    return nullptr;
  }
  const Object& object = *std::get<std::shared_ptr<const Object>>(storage_);
  const auto iterator =
      std::lower_bound(object.members.begin(), object.members.end(), key,
                       [](const CanonicalMember& member, const std::string_view& probe) {
                         return member.first < probe;
                       });
  if (iterator == object.members.end() || iterator->first != key) {
    return nullptr;
  }
  return &iterator->second;
}

const CanonicalValue& CanonicalValue::at(const std::string_view key,
                                         const std::string& pointer) const {
  if (!is_object()) {
    fail_wrong_type(pointer, "object", type_name());
  }
  const CanonicalValue* found = find(key);
  if (found == nullptr) {
    fail_missing(pointer, key);
  }
  return *found;
}

std::string CanonicalValue::dump() const {
  std::string out;
  dump_into(*this, out);
  return out;
}

void CanonicalValue::fail_numeric() {
  fail(ContractViolationKind::kNumericNotRepresentable, "",
       "NaN and infinity are not representable in canonical JSON");
}

CanonicalValue parse_canonical_json(const std::string_view text) {
  if (!is_valid_utf8(text)) {
    fail(ContractViolationKind::kInvalidUtf8, "",
         "input bytes are not well-formed UTF-8");
  }
  nlohmann::json parsed = nlohmann::json::parse(text.data(), text.data() + text.size(),
                                               /*cb=*/nullptr, /*allow_exceptions=*/false,
                                               /*ignore_comments=*/false);
  if (parsed.is_discarded()) {
    fail(ContractViolationKind::kWrongType, "",
         "input is not well-formed JSON");
  }
  return convert(parsed, "");
}

std::string read_text_file(const std::string& path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) {
    // No violation kind describes an unreadable input file: this is not a document-content violation
    // at all, it is a violated API precondition. kInvariantViolation is the honest fit; inventing a
    // new token would change the refusal vocabulary #225 fixes.
    fail(ContractViolationKind::kInvariantViolation, path, "file could not be opened");
  }
  std::ostringstream buffer;
  buffer << stream.rdbuf();
  return buffer.str();
}

}  // namespace Shiori::rates::dto
