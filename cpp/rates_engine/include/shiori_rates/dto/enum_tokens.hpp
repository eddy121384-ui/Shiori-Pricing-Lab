#pragma once
//
// Uniform enum <-> wire-token mapping.
//
// #225 principle P3 requires units and meaning to be machine-readable, and #225 section 16 rule 5
// refuses a role that is implied only by a variable name. Every enum that crosses the wire is
// therefore mapped to an explicit token with a named closed vocabulary, and an unrecognised token
// is a fail-closed refusal (never coerced to a default member).
//
#include <cstddef>
#include <iterator>
#include <span>
#include <string>
#include <string_view>
#include <utility>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"

namespace Shiori::rates::dto {

// Specialised per enum type. A valid specialisation provides:
//   static constexpr std::string_view vocabulary();
//   static std::span<const std::pair<Enum, const char*>> entries();
template <class Enum>
struct EnumTokens;

// Declares a closed vocabulary mapping for `EnumType`. The list is deliberately exhaustive and
// closed: an unknown token is refused, not defaulted.
#define SHIORI_ENUM_TOKENS(EnumType, VocabularyName, ...)                                 \
  template <>                                                                            \
  struct EnumTokens<EnumType> {                                                          \
    static constexpr std::string_view vocabulary() { return VocabularyName; }            \
    static std::span<const std::pair<EnumType, const char*>> entries() {                 \
      static constexpr std::pair<EnumType, const char*> kEntries[] = {__VA_ARGS__};      \
      return std::span<const std::pair<EnumType, const char*>>(kEntries,                 \
                                                              std::size(kEntries));      \
    }                                                                                    \
  };

template <class Enum>
[[nodiscard]] std::string_view enum_vocabulary() {
  return EnumTokens<Enum>::vocabulary();
}

template <class Enum>
[[nodiscard]] const char* enum_to_token(Enum value) {
  for (const auto& entry : EnumTokens<Enum>::entries()) {
    if (entry.first == value) {
      return entry.second;
    }
  }
  fail(ContractViolationKind::kInvariantViolation, "",
       "enum value is outside its declared closed vocabulary");
}

template <class Enum>
[[nodiscard]] Enum enum_from_token(const std::string_view token, const std::string& pointer) {
  for (const auto& entry : EnumTokens<Enum>::entries()) {
    if (token == entry.second) {
      return entry.first;
    }
  }
  fail_unknown_enum(pointer, token, enum_vocabulary<Enum>());
}

template <class Enum>
[[nodiscard]] CanonicalValue enum_to_canonical(Enum value) {
  return CanonicalValue(std::string(enum_to_token<Enum>(value)));
}

template <class Enum>
[[nodiscard]] Enum enum_from_canonical(const CanonicalValue& node, const std::string& pointer) {
  if (!node.is_string()) {
    fail_wrong_type(pointer, "string enum token", node.type_name());
  }
  return enum_from_token<Enum>(node.as_string(), pointer);
}

}  // namespace Shiori::rates::dto
