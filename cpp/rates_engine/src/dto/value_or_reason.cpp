#include <string>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"

namespace Shiori::rates::dto {

CanonicalValue StructuredReason::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("category", enum_to_canonical(category));
  members.emplace_back("code", CanonicalValue(code));
  if (detail.has_value()) {
    members.emplace_back("detail", CanonicalValue(*detail));
  } else {
    // #225 section 4.1 declares `detail: <string | null>` as optional. The canonical form states
    // the absence explicitly so a reader never has to distinguish "key missing" from "no detail".
    members.emplace_back("detail", CanonicalValue(nullptr));
  }
  return CanonicalValue::make_object(std::move(members));
}

StructuredReason StructuredReason::from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  require_only_keys(node, pointer, {"category", "code", "detail"});
  StructuredReason reason;
  reason.category = enum_from_canonical<ReasonCategory>(
      node.at("category", pointer), pointer_child(pointer, "category"));

  const CanonicalValue& code_node = node.at("code", pointer);
  if (!code_node.is_string()) {
    fail_wrong_type(pointer_child(pointer, "code"), "string reason code", code_node.type_name());
  }
  if (code_node.as_string().empty()) {
    fail(ContractViolationKind::kInvalidTaggedUnionState, pointer_child(pointer, "code"),
         "a structured reason requires a stable machine-readable code");
  }
  reason.code = code_node.as_string();

  const CanonicalValue* detail_node = node.find("detail");
  if (detail_node != nullptr && !detail_node->is_null()) {
    if (!detail_node->is_string()) {
      fail_wrong_type(pointer_child(pointer, "detail"), "string or null", detail_node->type_name());
    }
    reason.detail = detail_node->as_string();
  }
  return reason;
}

StructuredReason not_applicable_reason() {
  return StructuredReason(ReasonCategory::kNotApplicable, "NOT_APPLICABLE");
}

StructuredReason unresolved_methodology_reason(std::string code) {
  return StructuredReason(ReasonCategory::kUnresolvedMethodology, std::move(code));
}

}  // namespace Shiori::rates::dto
