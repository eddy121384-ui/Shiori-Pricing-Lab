#include "shiori_rates/dto/numeric.hpp"

#include <string>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"

namespace Shiori::rates::dto {

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
  result.value = value_node.as_double();
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
