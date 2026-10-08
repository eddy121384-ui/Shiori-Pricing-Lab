#include "shiori_rates/dto/codec_traits.hpp"

#include <string>
#include <string_view>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"

namespace Shiori::rates::dto {

bool has_key(const CanonicalValue& node, const std::string_view key) noexcept {
  return node.find(key) != nullptr;
}

void require_only_keys(const CanonicalValue& node, const std::string& pointer,
                       const std::vector<std::string_view>& allowed) {
  if (!node.is_object()) {
    fail_wrong_type(pointer, "object", node.type_name());
  }
  for (const auto& member : node.as_object().members) {
    bool recognised = false;
    for (const std::string_view key : allowed) {
      if (member.first == key) {
        recognised = true;
        break;
      }
    }
    if (!recognised) {
      // The V1 posture is fail-closed. An unrecognised member inside a document that claims a known
      // schema version would otherwise let a producer drift the payload silently.
      fail(ContractViolationKind::kUnknownField, pointer_child(pointer, member.first),
           "field is not part of this schema version's declared shape");
    }
  }
}

void require_absent(const CanonicalValue& node, const std::string& pointer,
                    const std::string_view key) {
  if (has_key(node, key)) {
    fail(ContractViolationKind::kInvalidTaggedUnionState, pointer_child(pointer, key),
         "field must be absent in this state (its presence would contradict the selected state)");
  }
}

}  // namespace Shiori::rates::dto
