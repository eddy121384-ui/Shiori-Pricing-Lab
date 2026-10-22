#pragma once
//
// `ValueOrReason<T>` and `StructuredReason` — #225 section 4.1.
//
// The wire contract is a TYPED UNION, not a bare JSON null with prose attached:
//
//   value_or_reason:
//     state: PRESENT | NULL_WITH_REASON
//     value: <T | null>      # required iff state=PRESENT
//     reason:                # required iff state=NULL_WITH_REASON
//       category: NOT_APPLICABLE | UNAVAILABLE | UNRESOLVED_METHODOLOGY | MISSING_MARKET_DATA | FAILED_CAPTURE
//       code: <string>
//       detail: <string | null>
//
// Enforced rules (all fail closed):
//   * PRESENT requires a value and FORBIDS a reason.
//   * NULL_WITH_REASON requires value=null plus the structured reason; a bare null is never
//     equivalent and is refused.
//   * `reason.category` and `reason.code` are mandatory; `detail` is optional and is never the only
//     semantic carrier.
//
// #225 section 15.4 decides where a reason participates in a preimage. #227 preserves the reason in
// canonical form, so it participates exactly wherever the owning contract includes the field.
//
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/units.hpp"

namespace Shiori::rates::dto {

struct StructuredReason {
  ReasonCategory category = ReasonCategory::kNotApplicable;
  std::string code;
  std::optional<std::string> detail;

  StructuredReason() = default;
  StructuredReason(ReasonCategory category_value, std::string code_value)
      : category(category_value), code(std::move(code_value)) {}
  StructuredReason(ReasonCategory category_value, std::string code_value, std::string detail_value)
      : category(category_value), code(std::move(code_value)), detail(std::move(detail_value)) {}

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static StructuredReason from_canonical(const CanonicalValue& node,
                                                       const std::string& pointer);
};

// Convenience constructors for the recurring #225 states. They name the contract's own terms and
// choose no methodology.
[[nodiscard]] StructuredReason not_applicable_reason();
[[nodiscard]] StructuredReason unresolved_methodology_reason(std::string code);

template <class T>
struct ValueOrReason {
  bool present = false;
  T value{};
  StructuredReason reason{};

  ValueOrReason() = default;

  [[nodiscard]] static ValueOrReason of(T value_in) {
    ValueOrReason result;
    result.present = true;
    result.value = std::move(value_in);
    return result;
  }

  [[nodiscard]] static ValueOrReason none(StructuredReason reason_in) {
    ValueOrReason result;
    result.present = false;
    result.reason = std::move(reason_in);
    return result;
  }

  [[nodiscard]] static ValueOrReason not_applicable() {
    return none(not_applicable_reason());
  }

  [[nodiscard]] CanonicalValue to_canonical() const {
    CanonicalMembers members;
    if (present) {
      members.emplace_back("state", CanonicalValue(std::string("PRESENT")));
      members.emplace_back("value", DtoCodec<T>::encode(value));
    } else {
      members.emplace_back("state", CanonicalValue(std::string("NULL_WITH_REASON")));
      members.emplace_back("value", CanonicalValue(nullptr));
      members.emplace_back("reason", reason.to_canonical());
    }
    return CanonicalValue::make_object(std::move(members));
  }

  [[nodiscard]] static ValueOrReason from_canonical(const CanonicalValue& node,
                                                    const std::string& pointer) {
    require_only_keys(node, pointer, {"state", "value", "reason"});
    const CanonicalValue& state = node.at("state", pointer);
    if (!state.is_string()) {
      fail_wrong_type(pointer_child(pointer, "state"), "string state token", state.type_name());
    }
    const std::string& token = state.as_string();

    if (token == "PRESENT") {
      const CanonicalValue* value_node = node.find("value");
      if (value_node == nullptr) {
        fail_missing(pointer, "value");
      }
      require_absent(node, pointer, "reason");
      ValueOrReason result;
      result.present = true;
      result.value = DtoCodec<T>::decode(*value_node, pointer_child(pointer, "value"));
      return result;
    }
    if (token == "NULL_WITH_REASON") {
      const CanonicalValue* value_node = node.find("value");
      if (value_node == nullptr) {
        fail_missing(pointer, "value");
      }
      if (!value_node->is_null()) {
        fail(ContractViolationKind::kInvalidTaggedUnionState, pointer_child(pointer, "value"),
             "NULL_WITH_REASON requires value=null");
      }
      const CanonicalValue* reason_node = node.find("reason");
      if (reason_node == nullptr) {
        fail_missing(pointer, "reason");
      }
      ValueOrReason result;
      result.present = false;
      result.reason = StructuredReason::from_canonical(*reason_node, pointer_child(pointer, "reason"));
      return result;
    }
    fail(ContractViolationKind::kInvalidTaggedUnionState, pointer_child(pointer, "state"),
         "state must be exactly PRESENT or NULL_WITH_REASON");
  }

  // Fails closed when the value is not PRESENT. Used by every applicability rule that requires a
  // concrete value (for example: a `DISCOUNT`-role curve must carry a NOT_APPLICABLE index reason).
  [[nodiscard]] const T& require_present(const std::string& pointer) const {
    if (!present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, pointer,
           "a value is required here but the union is NULL_WITH_REASON");
    }
    return value;
  }

  // Fails closed when the union is PRESENT. Used by every "must be NOT_APPLICABLE" rule.
  [[nodiscard]] const StructuredReason& require_null_with_reason(
      const std::string& pointer, ReasonCategory expected) const {
    if (present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, pointer,
           "this field must be NULL_WITH_REASON in this state");
    }
    if (reason.category != expected) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, pointer,
           "structured reason category does not match the applicability contract");
    }
    return reason;
  }

  friend bool operator==(const ValueOrReason& left, const ValueOrReason& right) {
    if (left.present != right.present) {
      return false;
    }
    if (left.present) {
      if constexpr (requires(const T& a, const T& b) { a == b; }) {
        return left.value == right.value;
      } else {
        // Types that carry an opaque canonical payload have no equality operator; compare their
        // canonical form instead so the comparison is still exact and deterministic.
        return DtoCodec<T>::encode(left.value).dump() == DtoCodec<T>::encode(right.value).dump();
      }
    }
    return left.reason.category == right.reason.category && left.reason.code == right.reason.code &&
           left.reason.detail == right.reason.detail;
  }
};

}  // namespace Shiori::rates::dto
