#include "shiori_rates/dto/numeric.hpp"

#include <cstdint>
#include <string>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"

namespace Shiori::rates::dto {

namespace {

// A JSON integer is an exact canonical number form, and a binary64 DTO field must round-trip exactly:
// `CanonicalValue::as_double()` converts an integer without proving the conversion is lossless, so an
// integer above the binary64 exact range (for example 9007199254740993) would be SILENTLY rounded to a
// different number. Identities are recomputed from the rounded in-memory value, so a document carrying
// fingerprints for the rounded number could validate while the canonical field says something else, and
// reserialization would change the request.
//
// Every integer with |v| <= 2^53 is exact. Above that a value is exact exactly when it is a multiple of
// the binary64 spacing at its magnitude: for |v| in [2^b, 2^(b+1)) the spacing is 2^(b-52), so the value
// is representable iff its low (b-52) bits are zero. The check is integer-only - there is no double to
// integer conversion anywhere, so there is no out-of-range/undefined cast near INT64_MIN/INT64_MAX.
[[nodiscard]] bool integer_is_exactly_representable_as_binary64(std::int64_t value) noexcept {
  constexpr std::int64_t kTwoTo53 = 9007199254740992LL;  // 2^53: exact for every integer at or below
  if (value >= -kTwoTo53 && value <= kTwoTo53) {
    return true;
  }
  // INT64_MIN is handled without overflow: -(value + 1) is a legal negation, then the +1 restores the
  // magnitude in the unsigned domain.
  const std::uint64_t magnitude = value < 0
                                      ? static_cast<std::uint64_t>(-(value + 1)) + 1U
                                      : static_cast<std::uint64_t>(value);
  unsigned exponent = 0;  // floor(log2(magnitude)); >= 53 because the early return handles |v| <= 2^53
  for (std::uint64_t probe = magnitude; probe > 1U; probe >>= 1U) {
    ++exponent;
  }
  // The binary64 spacing at magnitude 2^e is 2^(e - 52), so the value is exact exactly when its low
  // (e - 52) bits are zero. e is in [53, 63] here, so the shift is always in range for a 64-bit mask.
  const unsigned dropped_bits = exponent - 52U;
  return (magnitude & ((std::uint64_t{1} << dropped_bits) - 1U)) == 0U;
}

// The one decode rule for double-valued DTO fields: an integer node must be exactly representable, a
// floating node is stored as-is. A non-exact integer is REFUSED at the field's own pointer rather than
// rounded, so no identity is ever derived from a number the document does not contain.
[[nodiscard]] double require_binary64_number(const CanonicalValue& node, const std::string& pointer) {
  if (node.is_integer()) {
    const std::int64_t raw = node.as_integer();
    if (!integer_is_exactly_representable_as_binary64(raw)) {
      fail(ContractViolationKind::kInvariantViolation, pointer,
           "an integer-valued numeric field must be exactly representable as binary64; this integer "
           "would be silently rounded, so the canonical field would not round-trip and every identity "
           "derived from it would describe a different number");
    }
    return static_cast<double>(raw);
  }
  return node.as_double();
}

}  // namespace

CanonicalValue NumericWithUnit::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("unit", enum_to_canonical(unit));
  members.emplace_back("value", CanonicalValue(value));
  return CanonicalValue::make_object(std::move(members));
}

NumericWithUnit NumericWithUnit::from_canonical(const CanonicalValue& node,
                                               const std::string& pointer) {
  require_only_keys(node, pointer, {"value", "unit"});
  NumericWithUnit result;
  const CanonicalValue& value_node = node.at("value", pointer);
  if (!value_node.is_float() && !value_node.is_integer()) {
    fail_wrong_type(pointer_child(pointer, "value"), "number", value_node.type_name());
  }
  result.value = require_binary64_number(value_node, pointer_child(pointer, "value"));
  result.unit = enum_from_canonical<Unit>(node.at("unit", pointer), pointer_child(pointer, "unit"));
  return result;
}

const NumericWithUnit& NumericWithUnit::require_unit(const Unit expected,
                                                     const std::string& pointer) const {
  if (unit != expected) {
    fail(ContractViolationKind::kInvariantViolation, pointer_child(pointer, "unit"),
         "field carries a unit that the owning contract does not permit in this position");
  }
  return *this;
}

}  // namespace Shiori::rates::dto
