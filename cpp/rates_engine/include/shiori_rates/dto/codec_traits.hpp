#pragma once
//
// Uniform DTO <-> CanonicalValue dispatch.
//
// One `if constexpr` chain per direction keeps serialization behaviour identical for every DTO in
// the library, which is what makes "same logical input -> same canonical bytes" checkable rather
// than aspirational. A DTO type participates by providing:
//
//     CanonicalValue to_canonical() const;
//     static T from_canonical(const CanonicalValue& node, const std::string& pointer);
//
// Scalars, enums, `std::vector`, `std::optional` and `std::map<std::string, V>` are handled here.
// Anything else is a compile-time error rather than a silent fallback.
//
#include <cstdint>
#include <limits>
#include <map>
#include <optional>
#include <string>
#include <type_traits>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/enum_tokens.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/time.hpp"

namespace Shiori::rates::dto {

template <class>
inline constexpr bool kAlwaysFalse = false;

template <class T>
struct IsVector : std::false_type {};
template <class Item, class Allocator>
struct IsVector<std::vector<Item, Allocator>> : std::true_type {};

template <class T>
struct IsOptional : std::false_type {};
template <class Item>
struct IsOptional<std::optional<Item>> : std::true_type {};

template <class T>
struct IsStringMap : std::false_type {};
template <class Value, class Compare, class Allocator>
struct IsStringMap<std::map<std::string, Value, Compare, Allocator>> : std::true_type {};

template <class T>
inline constexpr bool kIsVector = IsVector<T>::value;
template <class T>
inline constexpr bool kIsOptional = IsOptional<T>::value;
template <class T>
inline constexpr bool kIsStringMap = IsStringMap<T>::value;

template <class T>
struct DtoCodec {
  static CanonicalValue encode(const T& value) {
    if constexpr (std::is_same_v<T, CanonicalValue>) {
      // Used only for a byte-exact, already-canonical child payload (see the documented
      // STRUCTURAL PLACEHOLDER envelopes in docs/CANONICALIZATION.md).
      return value;
    } else if constexpr (requires { value.to_canonical(); }) {
      return value.to_canonical();
    } else if constexpr (std::is_same_v<T, std::string>) {
      return CanonicalValue(value);
    } else if constexpr (std::is_same_v<T, bool>) {
      return CanonicalValue(value);
    } else if constexpr (std::is_integral_v<T>) {
      return CanonicalValue(static_cast<std::int64_t>(value));
    } else if constexpr (std::is_floating_point_v<T>) {
      return CanonicalValue(static_cast<double>(value));
    } else if constexpr (std::is_enum_v<T>) {
      return enum_to_canonical(value);
    } else if constexpr (kIsVector<T>) {
      CanonicalItems items;
      items.reserve(value.size());
      for (const auto& item : value) {
        using Item = typename T::value_type;
        items.push_back(DtoCodec<Item>::encode(item));
      }
      return CanonicalValue::make_array(std::move(items));
    } else if constexpr (kIsOptional<T>) {
      if (!value.has_value()) {
        return CanonicalValue(nullptr);
      }
      using Item = typename T::value_type;
      return DtoCodec<Item>::encode(*value);
    } else if constexpr (kIsStringMap<T>) {
      CanonicalMembers members;
      members.reserve(value.size());
      for (const auto& entry : value) {
        using Item = typename T::mapped_type;
        members.emplace_back(entry.first, DtoCodec<Item>::encode(entry.second));
      }
      return CanonicalValue::make_object(std::move(members));
    } else {
      static_assert(kAlwaysFalse<T>, "no canonical encoder exists for this DTO type");
    }
  }

  static T decode(const CanonicalValue& node, const std::string& pointer) {
    if constexpr (std::is_same_v<T, CanonicalValue>) {
      return node;
    } else if constexpr (requires { T::from_canonical(node, pointer); }) {
      return T::from_canonical(node, pointer);
    } else if constexpr (std::is_same_v<T, std::string>) {
      if (!node.is_string()) {
        fail_wrong_type(pointer, "string", node.type_name());
      }
      return node.as_string();
    } else if constexpr (std::is_same_v<T, bool>) {
      if (!node.is_bool()) {
        fail_wrong_type(pointer, "boolean", node.type_name());
      }
      return node.as_bool();
    } else if constexpr (std::is_integral_v<T>) {
      if (!node.is_integer()) {
        fail_wrong_type(pointer, "integer", node.type_name());
      }
      const std::int64_t raw = node.as_integer();
      if (raw < static_cast<std::int64_t>(std::numeric_limits<T>::min()) ||
          raw > static_cast<std::int64_t>(std::numeric_limits<T>::max())) {
        fail(ContractViolationKind::kNumericNotRepresentable, pointer,
             "integer is outside the target DTO range");
      }
      return static_cast<T>(raw);
    } else if constexpr (std::is_floating_point_v<T>) {
      if (!node.is_float() && !node.is_integer()) {
        fail_wrong_type(pointer, "number", node.type_name());
      }
      return static_cast<T>(node.as_double());
    } else if constexpr (std::is_enum_v<T>) {
      return enum_from_canonical<T>(node, pointer);
    } else if constexpr (kIsVector<T>) {
      if (!node.is_array()) {
        fail_wrong_type(pointer, "array", node.type_name());
      }
      using Item = typename T::value_type;
      T result;
      const auto& items = node.as_array().items;
      result.reserve(items.size());
      for (std::size_t index = 0; index < items.size(); ++index) {
        result.push_back(DtoCodec<Item>::decode(items[index], pointer_index(pointer, index)));
      }
      return result;
    } else if constexpr (kIsOptional<T>) {
      if (node.is_null()) {
        return std::nullopt;
      }
      using Item = typename T::value_type;
      return DtoCodec<Item>::decode(node, pointer);
    } else if constexpr (kIsStringMap<T>) {
      if (!node.is_object()) {
        fail_wrong_type(pointer, "object", node.type_name());
      }
      using Item = typename T::mapped_type;
      T result;
      for (const auto& member : node.as_object().members) {
        result.emplace(member.first,
                       DtoCodec<Item>::decode(member.second, pointer_child(pointer, member.first)));
      }
      return result;
    } else {
      static_assert(kAlwaysFalse<T>, "no canonical decoder exists for this DTO type");
    }
  }
};

// Rejects any member key that the declared contract shape does not define.
// The V1 posture is fail-closed: an unrecognised field is refused rather than ignored, so a
// producer cannot silently drift the payload while still claiming the same schema version.
void require_only_keys(const CanonicalValue& node, const std::string& pointer,
                       const std::vector<std::string_view>& allowed);

// Rejects a member key whose presence is forbidden in this state (used for the exact
// applicability rules of #225 sections 5.1, 7.2, 12.3 and 13.2).
void require_absent(const CanonicalValue& node, const std::string& pointer,
                    std::string_view key);

[[nodiscard]] bool has_key(const CanonicalValue& node, std::string_view key) noexcept;

}  // namespace Shiori::rates::dto
