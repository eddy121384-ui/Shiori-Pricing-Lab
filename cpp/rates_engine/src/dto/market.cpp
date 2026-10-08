#include "shiori_rates/dto/market.hpp"

#include <string>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"

namespace Shiori::rates::dto {

namespace {

[[nodiscard]] std::string child(const std::string& pointer, const char* key) {
  return pointer_child(pointer, key);
}

[[nodiscard]] std::string require_non_empty_string(const CanonicalValue& node,
                                                   const std::string& pointer,
                                                   const char* what) {
  if (!node.is_string()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
  if (node.as_string().empty()) {
    fail(ContractViolationKind::kInvariantViolation, pointer,
         "an identity/label field must not be empty");
  }
  return node.as_string();
}

[[nodiscard]] std::vector<std::string> require_string_list(const CanonicalValue& node,
                                                           const std::string& pointer,
                                                           const char* what) {
  if (!node.is_array()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
  std::vector<std::string> result;
  const auto& items = node.as_array().items;
  result.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    const CanonicalValue& item = items[index];
    if (!item.is_string()) {
      fail_wrong_type(pointer_index(pointer, index), "string", item.type_name());
    }
    result.push_back(item.as_string());
  }
  return result;
}

[[nodiscard]] CanonicalValue string_list(const std::vector<std::string>& values) {
  CanonicalItems items;
  items.reserve(values.size());
  for (const std::string& value : values) {
    items.emplace_back(CanonicalValue(value));
  }
  return CanonicalValue::make_array(std::move(items));
}

[[nodiscard]] CanonicalValue string_map(const std::map<std::string, std::string>& values) {
  CanonicalMembers members;
  members.reserve(values.size());
  for (const auto& entry : values) {
    members.emplace_back(entry.first, CanonicalValue(entry.second));
  }
  return CanonicalValue::make_object(std::move(members));
}

[[nodiscard]] std::map<std::string, std::string> require_string_map(const CanonicalValue& node,
                                                                    const std::string& pointer,
                                                                    const char* what) {
  if (!node.is_object()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
  std::map<std::string, std::string> result;
  for (const auto& member : node.as_object().members) {
    if (!member.second.is_string()) {
      fail_wrong_type(pointer_child(pointer, member.first), "string", member.second.type_name());
    }
    result.emplace(member.first, member.second.as_string());
  }
  return result;
}

// A required open methodology-text member. #225 defines these fields with UNRESOLVED values, so the
// only structural requirement #227 may enforce is presence and non-emptiness: it must not invent a
// value, and it must not treat "UNRESOLVED" as a resolved methodology.
[[nodiscard]] std::string require_methodology_text(const CanonicalValue& node,
                                                   const std::string& pointer, const char* what) {
  return require_non_empty_string(node, pointer, what);
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// Small shared shapes
// ---------------------------------------------------------------------------------------------
CanonicalValue CodeMessage::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("code", CanonicalValue(code));
  members.emplace_back("message", CanonicalValue(message));
  return CanonicalValue::make_object(std::move(members));
}

CodeMessage CodeMessage::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"code", "message"});
  CodeMessage result;
  result.code = require_non_empty_string(node.at("code", pointer), child(pointer, "code"),
                                         "string warning code");
  result.message = require_non_empty_string(node.at("message", pointer), child(pointer, "message"),
                                            "string warning message");
  return result;
}

CanonicalValue DiagnosticRecord::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("code", CanonicalValue(code));
  members.emplace_back("message", CanonicalValue(message));
  members.emplace_back("detail", detail.has_value() ? CanonicalValue(*detail)
                                                    : CanonicalValue(nullptr));
  return CanonicalValue::make_object(std::move(members));
}

DiagnosticRecord DiagnosticRecord::from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  require_only_keys(node, pointer, {"code", "message", "detail"});
  DiagnosticRecord result;
  result.code = require_non_empty_string(node.at("code", pointer), child(pointer, "code"),
                                         "string diagnostic code");
  result.message = require_non_empty_string(node.at("message", pointer), child(pointer, "message"),
                                            "string diagnostic message");
  const CanonicalValue* detail_node = node.find("detail");
  if (detail_node != nullptr && !detail_node->is_null()) {
    if (!detail_node->is_string()) {
      fail_wrong_type(child(pointer, "detail"), "string or null", detail_node->type_name());
    }
    result.detail = detail_node->as_string();
  }
  return result;
}

CanonicalValue SourceProvenance::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("source", source.has_value() ? enum_to_canonical(*source)
                                                    : CanonicalValue(nullptr));
  members.emplace_back("adapter_name", adapter_name.has_value() ? CanonicalValue(*adapter_name)
                                                                : CanonicalValue(nullptr));
  members.emplace_back("adapter_version", adapter_version.has_value() ? CanonicalValue(*adapter_version)
                                                                     : CanonicalValue(nullptr));
  members.emplace_back("upstream_ids", string_list(upstream_ids));
  members.emplace_back("evidence_refs", string_list(evidence_refs));
  return CanonicalValue::make_object(std::move(members));
}

SourceProvenance SourceProvenance::from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"source", "adapter_name", "adapter_version", "upstream_ids", "evidence_refs"});
  SourceProvenance result;
  const CanonicalValue* source_node = node.find("source");
  if (source_node != nullptr && !source_node->is_null()) {
    result.source = enum_from_canonical<MarketSource>(*source_node, child(pointer, "source"));
  }
  const CanonicalValue* name_node = node.find("adapter_name");
  if (name_node != nullptr && !name_node->is_null()) {
    result.adapter_name = require_non_empty_string(*name_node, child(pointer, "adapter_name"),
                                                   "string adapter name");
  }
  const CanonicalValue* version_node = node.find("adapter_version");
  if (version_node != nullptr && !version_node->is_null()) {
    result.adapter_version = require_non_empty_string(*version_node,
                                                      child(pointer, "adapter_version"),
                                                      "string adapter version");
  }
  result.upstream_ids =
      require_string_list(node.at("upstream_ids", pointer), child(pointer, "upstream_ids"),
                          "array of upstream ids");
  result.evidence_refs =
      require_string_list(node.at("evidence_refs", pointer), child(pointer, "evidence_refs"),
                          "array of evidence refs");
  return result;
}

CanonicalValue MethodSpec::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("method_id", CanonicalValue(method_id));
  members.emplace_back("method_version", CanonicalValue(method_version));
  if (parameters.has_value()) {
    std::map<std::string, NumericWithUnit> ordered;
    for (const auto& entry : *parameters) {
      ordered.emplace(entry.first, entry.second);
    }
    members.emplace_back("parameters",
                         DtoCodec<std::map<std::string, NumericWithUnit>>::encode(ordered));
  } else {
    members.emplace_back("parameters", CanonicalValue(nullptr));
  }
  return CanonicalValue::make_object(std::move(members));
}

MethodSpec MethodSpec::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"method_id", "method_version", "parameters"});
  MethodSpec result;
  result.method_id = require_methodology_text(node.at("method_id", pointer),
                                             child(pointer, "method_id"), "string method id");
  result.method_version = require_methodology_text(node.at("method_version", pointer),
                                                   child(pointer, "method_version"),
                                                   "string method version");
  const CanonicalValue* parameters_node = node.find("parameters");
  if (parameters_node != nullptr && !parameters_node->is_null()) {
    result.parameters = DtoCodec<std::map<std::string, NumericWithUnit>>::decode(
        *parameters_node, child(pointer, "parameters"));
  }
  return result;
}

CanonicalValue RateRepresentation::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("compounding", CanonicalValue(compounding));
  members.emplace_back("day_count", CanonicalValue(day_count));
  members.emplace_back("accrual_boundary", CanonicalValue(accrual_boundary));
  return CanonicalValue::make_object(std::move(members));
}

RateRepresentation RateRepresentation::from_canonical(const CanonicalValue& node,
                                                      const std::string& pointer) {
  require_only_keys(node, pointer, {"compounding", "day_count", "accrual_boundary"});
  RateRepresentation result;
  result.compounding = require_methodology_text(node.at("compounding", pointer),
                                                child(pointer, "compounding"),
                                                "string compounding token");
  result.day_count = require_methodology_text(node.at("day_count", pointer),
                                              child(pointer, "day_count"),
                                              "string day-count token");
  result.accrual_boundary = require_methodology_text(node.at("accrual_boundary", pointer),
                                                     child(pointer, "accrual_boundary"),
                                                     "string accrual-boundary token");
  return result;
}

CanonicalValue CurvePillar::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("pillar_date", pillar_date.to_canonical());
  members.emplace_back("maturity_label", maturity_label.to_canonical());
  members.emplace_back("value_state", enum_to_canonical(value_state));
  members.emplace_back("value", value.has_value() ? CanonicalValue(*value) : CanonicalValue(nullptr));
  members.emplace_back("unresolved_reason", unresolved_reason.has_value()
                                                ? unresolved_reason->to_canonical()
                                                : CanonicalValue(nullptr));
  members.emplace_back("source_column", source_column.to_canonical());
  return CanonicalValue::make_object(std::move(members));
}

CurvePillar CurvePillar::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"pillar_date", "maturity_label", "value_state", "value", "unresolved_reason",
                     "source_column"});
  CurvePillar result;
  result.pillar_date = Date::from_canonical(node.at("pillar_date", pointer),
                                           child(pointer, "pillar_date"));
  result.maturity_label = ValueOrReason<std::string>::from_canonical(
      node.at("maturity_label", pointer), child(pointer, "maturity_label"));
  result.value_state = enum_from_canonical<ValueState>(node.at("value_state", pointer),
                                                       child(pointer, "value_state"));

  const CanonicalValue& value_node = node.at("value", pointer);
  const CanonicalValue* reason_node = node.find("unresolved_reason");

  if (result.value_state == ValueState::kResolved) {
    if (value_node.is_null()) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "value"),
           "value_state=RESOLVED requires a numeric value");
    }
    if (!value_node.is_float() && !value_node.is_integer()) {
      fail_wrong_type(child(pointer, "value"), "number", value_node.type_name());
    }
    result.value = value_node.as_double();
    if (reason_node != nullptr && !reason_node->is_null()) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "unresolved_reason"),
           "value_state=RESOLVED forbids an unresolved reason");
    }
  } else {
    if (!value_node.is_null()) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "value"),
           "value_state=NULL_WITH_REASON requires value=null; an unresolved pillar is never "
           "zero-filled or given a borrowed value");
    }
    if (reason_node == nullptr || reason_node->is_null()) {
      fail_missing(pointer, "unresolved_reason");
    }
    result.unresolved_reason = StructuredReason::from_canonical(
        *reason_node, child(pointer, "unresolved_reason"));
  }
  result.source_column = ValueOrReason<std::string>::from_canonical(
      node.at("source_column", pointer), child(pointer, "source_column"));
  return result;
}

CanonicalValue CurveSourceProvenance::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("source", enum_to_canonical(source));
  CanonicalItems stamps;
  stamps.reserve(quote_timestamps.size());
  for (const auto& entry : quote_timestamps) {
    stamps.push_back(entry.to_canonical());
  }
  members.emplace_back("quote_timestamps", CanonicalValue::make_array(std::move(stamps)));
  members.emplace_back("adapter_name", CanonicalValue(adapter_name));
  members.emplace_back("adapter_version", CanonicalValue(adapter_version));
  members.emplace_back("upstream_ids", string_list(upstream_ids));
  members.emplace_back("evidence_refs", string_list(evidence_refs));
  return CanonicalValue::make_object(std::move(members));
}

CurveSourceProvenance CurveSourceProvenance::from_canonical(const CanonicalValue& node,
                                                            const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"source", "quote_timestamps", "adapter_name", "adapter_version",
                     "upstream_ids", "evidence_refs"});
  CurveSourceProvenance result;
  result.source = enum_from_canonical<MarketSource>(node.at("source", pointer),
                                                   child(pointer, "source"));
  const CanonicalValue& stamps_node = node.at("quote_timestamps", pointer);
  if (!stamps_node.is_array()) {
    fail_wrong_type(child(pointer, "quote_timestamps"), "array", stamps_node.type_name());
  }
  const auto& items = stamps_node.as_array().items;
  result.quote_timestamps.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.quote_timestamps.push_back(ValueOrReason<Timestamp>::from_canonical(
        items[index], pointer_index(child(pointer, "quote_timestamps"), index)));
  }
  result.adapter_name = require_non_empty_string(node.at("adapter_name", pointer),
                                                 child(pointer, "adapter_name"),
                                                 "string adapter name");
  result.adapter_version = require_non_empty_string(node.at("adapter_version", pointer),
                                                    child(pointer, "adapter_version"),
                                                    "string adapter version");
  result.upstream_ids = require_string_list(node.at("upstream_ids", pointer),
                                            child(pointer, "upstream_ids"),
                                            "array of upstream ids");
  result.evidence_refs = require_string_list(node.at("evidence_refs", pointer),
                                             child(pointer, "evidence_refs"),
                                             "array of evidence refs");
  return result;
}

// ---------------------------------------------------------------------------------------------
// Curves
// ---------------------------------------------------------------------------------------------
namespace {

// Shared decoding for DISCOUNT_CURVE_V1 / FORWARD_CURVE_V1 common fields. `allowed` carries the
// exact key set of the concrete schema version so an unexpected key is refused.
CurveCommon curve_common_from_canonical(const CanonicalValue& node, const std::string& pointer,
                                        const std::vector<std::string_view>& allowed) {
  require_only_keys(node, pointer, allowed);
  CurveCommon result;
  result.curve_id = require_non_empty_string(node.at("curve_id", pointer), child(pointer, "curve_id"),
                                             "string curve id");
  result.currency = enum_from_canonical<Currency>(node.at("currency", pointer),
                                                 child(pointer, "currency"));
  result.curve_role = enum_from_canonical<CurveRole>(node.at("curve_role", pointer),
                                                     child(pointer, "curve_role"));
  result.index_id = ValueOrReason<FloatingIndex>::from_canonical(node.at("index_id", pointer),
                                                                 child(pointer, "index_id"));
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                               child(pointer, "valuation_date"));
  result.reference_date = Date::from_canonical(node.at("reference_date", pointer),
                                               child(pointer, "reference_date"));
  result.reference_date_reason = ValueOrReason<std::string>::from_canonical(
      node.at("reference_date_reason", pointer), child(pointer, "reference_date_reason"));
  result.value_type = enum_from_canonical<PillarValueType>(node.at("value_type", pointer),
                                                          child(pointer, "value_type"));
  result.value_unit = enum_from_canonical<Unit>(node.at("value_unit", pointer),
                                                child(pointer, "value_unit"));

  const CanonicalValue& pillars_node = node.at("pillars", pointer);
  if (!pillars_node.is_array()) {
    fail_wrong_type(child(pointer, "pillars"), "array", pillars_node.type_name());
  }
  const auto& pillar_items = pillars_node.as_array().items;
  if (pillar_items.empty()) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "pillars"),
         "a curve requires at least one pillar");
  }
  result.pillars.reserve(pillar_items.size());
  for (std::size_t index = 0; index < pillar_items.size(); ++index) {
    result.pillars.push_back(CurvePillar::from_canonical(
        pillar_items[index], pointer_index(child(pointer, "pillars"), index)));
  }
  // #225 section 7.2: pillars are non-empty, sorted strictly ascending by pillar_date, and
  // duplicate dates are refused. A non-monotonic coordinate list would make every later bracket
  // ambiguous, so this is enforced at decode time.
  for (std::size_t index = 1; index < result.pillars.size(); ++index) {
    const std::string& previous = result.pillars[index - 1].pillar_date.text;
    const std::string& current = result.pillars[index].pillar_date.text;
    if (!(previous < current)) {
      fail(ContractViolationKind::kInvariantViolation,
           pointer_index(child(pointer, "pillars"), index),
           "pillars must be strictly ascending by pillar_date with no duplicate dates");
    }
  }

  result.rate_representation = RateRepresentation::from_canonical(
      node.at("rate_representation", pointer), child(pointer, "rate_representation"));
  result.interpolation = MethodSpec::from_canonical(node.at("interpolation", pointer),
                                                    child(pointer, "interpolation"));
  result.extrapolation = MethodSpec::from_canonical(node.at("extrapolation", pointer),
                                                    child(pointer, "extrapolation"));
  result.source_provenance = CurveSourceProvenance::from_canonical(
      node.at("source_provenance", pointer), child(pointer, "source_provenance"));
  // One quote timestamp per pillar, in pillar order (section 7.2: per-pillar ValueOrReason).
  if (result.source_provenance.quote_timestamps.size() != result.pillars.size()) {
    fail(ContractViolationKind::kInvariantViolation,
         child(child(pointer, "source_provenance"), "quote_timestamps"),
         "quote_timestamps must carry exactly one entry per pillar, in pillar order");
  }
  result.construction_methodology_id = require_methodology_text(
      node.at("construction_methodology_id", pointer), child(pointer, "construction_methodology_id"),
      "string construction methodology id");
  result.construction_methodology_version = require_methodology_text(
      node.at("construction_methodology_version", pointer),
      child(pointer, "construction_methodology_version"),
      "string construction methodology version");
  result.content_fingerprint = require_valid_fingerprint(
      node.at("content_fingerprint", pointer).is_string()
          ? node.at("content_fingerprint", pointer).as_string()
          : std::string(),
      child(pointer, "content_fingerprint"));

  // Role/applicability rules, section 7.2/7.3.
  if (result.curve_role == CurveRole::kDiscount) {
    (void)result.index_id.require_null_with_reason(child(pointer, "index_id"),
                                             ReasonCategory::kNotApplicable);
  } else {
    if (!result.index_id.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "index_id"),
           "a FORECAST or DISCOUNT_AND_FORECAST_REFERENCE_ONLY curve requires a PRESENT index id");
    }
  }
  // Section 7.2: value_type/value_unit pairing is fixed, and the pair decides the formula. An
  // UNRESOLVED pair fails closed downstream; #227 never unifies continuous and simple silently.
  switch (result.value_type) {
    case PillarValueType::kDiscountFactor:
      if (result.value_unit != Unit::kRatio) {
        fail(ContractViolationKind::kInvariantViolation, child(pointer, "value_unit"),
             "DISCOUNT_FACTOR requires the unitless RATIO unit (never divided by 100)");
      }
      break;
    case PillarValueType::kZeroRateContinuous:
    case PillarValueType::kZeroRateSimple:
      if (result.value_unit != Unit::kDecimalAnnual) {
        fail(ContractViolationKind::kInvariantViolation, child(pointer, "value_unit"),
             "rate pillars require DECIMAL_ANNUAL; untagged percent is refused at the boundary");
      }
      break;
    case PillarValueType::kParRateDisplayOnlyNeverPrice:
      if (result.value_unit != Unit::kPercentRawUnconverted) {
        fail(ContractViolationKind::kInvariantViolation, child(pointer, "value_unit"),
             "PAR_RATE_DISPLAY_ONLY_NEVER_PRICE requires PERCENT_RAW_UNCONVERTED");
      }
      break;
  }
  // Section 7.2: reference_date_reason is NOT_APPLICABLE when the dates agree, and PRESENT with an
  // explicit explanation when they differ.
  if (result.reference_date.text == result.valuation_date.text) {
    (void)result.reference_date_reason.require_null_with_reason(
        child(pointer, "reference_date_reason"), ReasonCategory::kNotApplicable);
  } else if (!result.reference_date_reason.present) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "reference_date_reason"),
         "a reference_date different from valuation_date requires a PRESENT explanation");
  }
  return result;
}

CanonicalValue curve_common_to_canonical(const CurveCommon& common) {
  CanonicalMembers members;
  members.emplace_back("curve_id", CanonicalValue(common.curve_id));
  members.emplace_back("currency", enum_to_canonical(common.currency));
  members.emplace_back("curve_role", enum_to_canonical(common.curve_role));
  members.emplace_back("index_id", common.index_id.to_canonical());
  members.emplace_back("valuation_date", common.valuation_date.to_canonical());
  members.emplace_back("reference_date", common.reference_date.to_canonical());
  members.emplace_back("reference_date_reason", common.reference_date_reason.to_canonical());
  members.emplace_back("value_type", enum_to_canonical(common.value_type));
  members.emplace_back("value_unit", enum_to_canonical(common.value_unit));
  CanonicalItems pillars;
  pillars.reserve(common.pillars.size());
  for (const CurvePillar& pillar : common.pillars) {
    pillars.push_back(pillar.to_canonical());
  }
  members.emplace_back("pillars", CanonicalValue::make_array(std::move(pillars)));
  members.emplace_back("rate_representation", common.rate_representation.to_canonical());
  members.emplace_back("interpolation", common.interpolation.to_canonical());
  members.emplace_back("extrapolation", common.extrapolation.to_canonical());
  members.emplace_back("source_provenance", common.source_provenance.to_canonical());
  members.emplace_back("construction_methodology_id", CanonicalValue(common.construction_methodology_id));
  members.emplace_back("construction_methodology_version",
                       CanonicalValue(common.construction_methodology_version));
  members.emplace_back("content_fingerprint", CanonicalValue(common.content_fingerprint));
  return CanonicalValue::make_object(std::move(members));
}

constexpr std::string_view kDiscountCurveKeys[] = {
    "schema_version",           "curve_id",
    "currency",                 "curve_role",
    "index_id",                 "valuation_date",
    "reference_date",           "reference_date_reason",
    "value_type",               "value_unit",
    "pillars",                  "rate_representation",
    "interpolation",            "extrapolation",
    "source_provenance",        "construction_methodology_id",
    "construction_methodology_version", "content_fingerprint"};

constexpr std::string_view kForwardCurveExtraKeys[] = {"index_tenor", "fixing_calendar_ref",
                                                       "observation_rules_ref"};

[[nodiscard]] std::vector<std::string_view> discount_curve_keys() {
  return {std::begin(kDiscountCurveKeys), std::end(kDiscountCurveKeys)};
}

[[nodiscard]] std::vector<std::string_view> forward_curve_keys() {
  std::vector<std::string_view> keys = discount_curve_keys();
  for (const std::string_view key : kForwardCurveExtraKeys) {
    keys.push_back(key);
  }
  return keys;
}

}  // namespace

CanonicalValue curve_full_canonical(const CurveCommon& common, const SchemaVersion schema_version,
                                    const ForwardCurve* forward) {
  CanonicalValue payload = curve_common_to_canonical(common);
  CanonicalMembers members(payload.as_object().members.begin(), payload.as_object().members.end());
  if (forward != nullptr) {
    members.emplace_back("index_tenor", forward->index_tenor.to_canonical());
    members.emplace_back("fixing_calendar_ref", forward->fixing_calendar_ref.to_canonical());
    members.emplace_back("observation_rules_ref", forward->observation_rules_ref.to_canonical());
  }
  members.emplace_back("schema_version", CanonicalValue(std::string(to_token(schema_version))));
  return CanonicalValue::make_object(std::move(members));
}

const CurveCommon& Curve::common() const {
  if (std::holds_alternative<DiscountCurve>(body)) {
    return std::get<DiscountCurve>(body).common;
  }
  return std::get<ForwardCurve>(body).common;
}

std::string_view Curve::curve_id() const { return common().curve_id; }

CurveRole Curve::curve_role() const { return common().curve_role; }

CanonicalValue Curve::to_canonical() const {
  if (std::holds_alternative<ForwardCurve>(body)) {
    const ForwardCurve& forward = std::get<ForwardCurve>(body);
    return curve_full_canonical(forward.common, SchemaVersion::kForwardCurveV1, &forward);
  }
  return curve_full_canonical(std::get<DiscountCurve>(body).common,
                              SchemaVersion::kDiscountCurveV1, nullptr);
}

Curve Curve::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  // The curve's own schema_version decides which shape is read; it is validated before any other
  // content so an unknown or mismatched version fails closed first.
  const CanonicalValue* version_node = node.find("schema_version");
  if (version_node == nullptr || !version_node->is_string()) {
    fail_missing(pointer, "schema_version");
  }
  const std::string& version_token = version_node->as_string();
  if (version_token == to_token(SchemaVersion::kDiscountCurveV1)) {
    (void)require_schema_version(node, SchemaVersion::kDiscountCurveV1, pointer);
    DiscountCurve curve;
    curve.common = curve_common_from_canonical(node, pointer, discount_curve_keys());
    Curve result{std::move(curve)};
    verify_curve_identities(result, pointer);
    return result;
  }
  if (version_token == to_token(SchemaVersion::kForwardCurveV1)) {
    (void)require_schema_version(node, SchemaVersion::kForwardCurveV1, pointer);
    ForwardCurve curve;
    curve.common = curve_common_from_canonical(node, pointer, forward_curve_keys());
    curve.index_tenor = ValueOrReason<std::string>::from_canonical(
        node.at("index_tenor", pointer), child(pointer, "index_tenor"));
    curve.fixing_calendar_ref = ValueOrReason<std::string>::from_canonical(
        node.at("fixing_calendar_ref", pointer), child(pointer, "fixing_calendar_ref"));
    curve.observation_rules_ref = ValueOrReason<std::string>::from_canonical(
        node.at("observation_rules_ref", pointer), child(pointer, "observation_rules_ref"));
    Curve result{std::move(curve)};
    verify_curve_identities(result, pointer);
    return result;
  }
  fail(ContractViolationKind::kUnknownSchemaVersion, child(pointer, "schema_version"),
       "a curve entry must be DISCOUNT_CURVE_V1 or FORWARD_CURVE_V1");
}

// ---------------------------------------------------------------------------------------------
// CurveSet
// ---------------------------------------------------------------------------------------------
CanonicalValue CurveSetConstruction::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("construction_methodology_id", CanonicalValue(construction_methodology_id));
  members.emplace_back("construction_methodology_version",
                       CanonicalValue(construction_methodology_version));
  members.emplace_back("construction_inputs_ref", construction_inputs_ref.to_canonical());
  members.emplace_back("evidence_refs", string_list(evidence_refs));
  return CanonicalValue::make_object(std::move(members));
}

CurveSetConstruction CurveSetConstruction::from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer) {
  require_only_keys(node, pointer, {"construction_methodology_id",
                                    "construction_methodology_version",
                                    "construction_inputs_ref", "evidence_refs"});
  CurveSetConstruction result;
  result.construction_methodology_id =
      require_methodology_text(node.at("construction_methodology_id", pointer),
                               child(pointer, "construction_methodology_id"),
                               "string construction methodology id");
  result.construction_methodology_version =
      require_methodology_text(node.at("construction_methodology_version", pointer),
                               child(pointer, "construction_methodology_version"),
                               "string construction methodology version");
  result.construction_inputs_ref = ValueOrReason<std::string>::from_canonical(
      node.at("construction_inputs_ref", pointer), child(pointer, "construction_inputs_ref"));
  result.evidence_refs = require_string_list(node.at("evidence_refs", pointer),
                                             child(pointer, "evidence_refs"),
                                             "array of evidence refs");
  return result;
}

CanonicalValue CurveSetProvenance::to_canonical() const {
  CanonicalMembers members;
  CanonicalItems source_items;
  source_items.reserve(sources.size());
  for (const MarketSource entry : sources) {
    source_items.push_back(enum_to_canonical(entry));
  }
  members.emplace_back("sources", CanonicalValue::make_array(std::move(source_items)));
  members.emplace_back("adapter_versions", string_map(adapter_versions));
  members.emplace_back("upstream_ids", string_list(upstream_ids));
  members.emplace_back("captured_at", captured_at.to_canonical());
  members.emplace_back("evidence_refs", string_list(evidence_refs));
  return CanonicalValue::make_object(std::move(members));
}

CurveSetProvenance CurveSetProvenance::from_canonical(const CanonicalValue& node,
                                                      const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"sources", "adapter_versions", "upstream_ids", "captured_at", "evidence_refs"});
  CurveSetProvenance result;
  const CanonicalValue& sources_node = node.at("sources", pointer);
  if (!sources_node.is_array()) {
    fail_wrong_type(child(pointer, "sources"), "array", sources_node.type_name());
  }
  const auto& items = sources_node.as_array().items;
  result.sources.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.sources.push_back(enum_from_canonical<MarketSource>(
        items[index], pointer_index(child(pointer, "sources"), index)));
  }
  result.adapter_versions = require_string_map(node.at("adapter_versions", pointer),
                                               child(pointer, "adapter_versions"),
                                               "object of adapter versions");
  result.upstream_ids = require_string_list(node.at("upstream_ids", pointer),
                                            child(pointer, "upstream_ids"),
                                            "array of upstream ids");
  result.captured_at = Timestamp::from_canonical(node.at("captured_at", pointer),
                                                 child(pointer, "captured_at"));
  result.evidence_refs = require_string_list(node.at("evidence_refs", pointer),
                                             child(pointer, "evidence_refs"),
                                             "array of evidence refs");
  // Section 7.1 / 8.4: `sources` is the deterministic union of the contributing sources. A store or
  // set must not claim a source that no child actually carries.
  return result;
}

CanonicalValue CurveSet::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("curve_set_id", CanonicalValue(curve_set_id));
  members.emplace_back("valuation_date", valuation_date.to_canonical());
  members.emplace_back("base_currency", enum_to_canonical(base_currency));
  CanonicalItems curve_items;
  curve_items.reserve(curves.size());
  for (const Curve& curve : curves) {
    curve_items.push_back(curve.to_canonical());
  }
  members.emplace_back("curves", CanonicalValue::make_array(std::move(curve_items)));
  members.emplace_back("construction", construction.to_canonical());
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kCurveSetV1))));
  return CanonicalValue::make_object(std::move(members));
}

CurveSet CurveSet::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kCurveSetV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "curve_set_id", "valuation_date", "base_currency", "curves",
                     "construction", "provenance", "content_fingerprint"});
  CurveSet result;
  result.curve_set_id = require_non_empty_string(node.at("curve_set_id", pointer),
                                                 child(pointer, "curve_set_id"),
                                                 "string curve set id");
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                               child(pointer, "valuation_date"));
  result.base_currency = enum_from_canonical<Currency>(node.at("base_currency", pointer),
                                                       child(pointer, "base_currency"));
  const CanonicalValue& curves_node = node.at("curves", pointer);
  if (!curves_node.is_array()) {
    fail_wrong_type(child(pointer, "curves"), "array", curves_node.type_name());
  }
  const auto& items = curves_node.as_array().items;
  if (items.empty()) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "curves"),
         "a CurveSet requires at least one curve");
  }
  result.curves.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.curves.push_back(
        Curve::from_canonical(items[index], pointer_index(child(pointer, "curves"), index)));
  }
  result.construction = CurveSetConstruction::from_canonical(node.at("construction", pointer),
                                                             child(pointer, "construction"));
  result.provenance = CurveSetProvenance::from_canonical(node.at("provenance", pointer),
                                                         child(pointer, "provenance"));
  result.content_fingerprint = require_valid_fingerprint(
      node.at("content_fingerprint", pointer).is_string()
          ? node.at("content_fingerprint", pointer).as_string()
          : std::string(),
      child(pointer, "content_fingerprint"));

  // Section 7.2: construction_methodology_{id,version} are a REQUIRED exact echo of the enclosing
  // CurveSet values. A curve that disagrees with its own container is malformed.
  for (std::size_t index = 0; index < result.curves.size(); ++index) {
    const CurveCommon& common = result.curves[index].common();
    const std::string curve_pointer = pointer_index(child(pointer, "curves"), index);
    if (common.construction_methodology_id != result.construction.construction_methodology_id ||
        common.construction_methodology_version !=
            result.construction.construction_methodology_version) {
      fail(ContractViolationKind::kInvariantViolation, curve_pointer,
           "curve construction methodology must exactly echo the enclosing CurveSet");
    }
    if (common.valuation_date.text != result.valuation_date.text) {
      fail(ContractViolationKind::kInvariantViolation, curve_pointer,
           "every curve valuation_date must equal the enclosing CurveSet valuation_date");
    }
  }
  // Section 7.3: a forward curve requires a PRESENT index id; a DISCOUNT_AND_FORECAST_REFERENCE_ONLY
  // curve must carry one too, so any forecast use can be validated deterministically.
  for (std::size_t index = 0; index < result.curves.size(); ++index) {
    if (!result.curves[index].is_forward()) {
      continue;
    }
    const ForwardCurve& forward = std::get<ForwardCurve>(result.curves[index].body);
    if (!forward.common.index_id.present) {
      fail(ContractViolationKind::kInvariantViolation,
           pointer_index(child(pointer, "curves"), index),
           "a forward curve without a PRESENT index id is malformed");
    }
  }
  verify_curve_set_identities(result, pointer);
  return result;
}

// ---------------------------------------------------------------------------------------------
// FixingStore
// ---------------------------------------------------------------------------------------------
int SameDayRule::count_explicitly_unresolved() const noexcept {
  int count = 0;
  if (is_unresolved_methodology_token(rule_id)) {
    ++count;
  }
  if (is_unresolved_methodology_token(rule_version)) {
    ++count;
  }
  if (is_unresolved_methodology_token(cutoff_time)) {
    ++count;
  }
  if (is_unresolved_methodology_token(cutoff_time_unit)) {
    ++count;
  }
  if (is_unresolved_methodology_token(timezone)) {
    ++count;
  }
  return count;
}

CanonicalValue SameDayRule::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("rule_id", CanonicalValue(rule_id));
  members.emplace_back("rule_version", CanonicalValue(rule_version));
  members.emplace_back("cutoff_time", CanonicalValue(cutoff_time));
  members.emplace_back("cutoff_time_unit", CanonicalValue(cutoff_time_unit));
  members.emplace_back("timezone", CanonicalValue(timezone));
  return CanonicalValue::make_object(std::move(members));
}

SameDayRule SameDayRule::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"rule_id", "rule_version", "cutoff_time", "cutoff_time_unit", "timezone"});
  SameDayRule result;
  result.rule_id = require_methodology_text(node.at("rule_id", pointer), child(pointer, "rule_id"),
                                            "string same-day rule id");
  result.rule_version = require_methodology_text(node.at("rule_version", pointer),
                                                 child(pointer, "rule_version"),
                                                 "string same-day rule version");
  result.cutoff_time = require_methodology_text(node.at("cutoff_time", pointer),
                                               child(pointer, "cutoff_time"),
                                               "string cutoff time basis");
  result.cutoff_time_unit = require_methodology_text(node.at("cutoff_time_unit", pointer),
                                                     child(pointer, "cutoff_time_unit"),
                                                     "string cutoff time unit");
  result.timezone = require_methodology_text(node.at("timezone", pointer), child(pointer, "timezone"),
                                             "string timezone");
  return result;
}

CanonicalValue ProjectionInputsRef::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("curve_set_id", CanonicalValue(curve_set_id));
  members.emplace_back("forecast_curve_id", CanonicalValue(forecast_curve_id));
  members.emplace_back("observation_rule_id", CanonicalValue(observation_rule_id));
  members.emplace_back("observation_rule_version", CanonicalValue(observation_rule_version));
  return CanonicalValue::make_object(std::move(members));
}

ProjectionInputsRef ProjectionInputsRef::from_canonical(const CanonicalValue& node,
                                                        const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"curve_set_id", "forecast_curve_id", "observation_rule_id",
                     "observation_rule_version"});
  ProjectionInputsRef result;
  result.curve_set_id = require_non_empty_string(node.at("curve_set_id", pointer),
                                                 child(pointer, "curve_set_id"),
                                                 "string curve set id");
  result.forecast_curve_id = require_non_empty_string(node.at("forecast_curve_id", pointer),
                                                      child(pointer, "forecast_curve_id"),
                                                      "string forecast curve id");
  result.observation_rule_id = require_non_empty_string(node.at("observation_rule_id", pointer),
                                                        child(pointer, "observation_rule_id"),
                                                        "string observation rule id");
  result.observation_rule_version =
      require_non_empty_string(node.at("observation_rule_version", pointer),
                               child(pointer, "observation_rule_version"),
                               "string observation rule version");
  return result;
}

CanonicalValue FixingEntry::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("index_id", enum_to_canonical(index_id));
  members.emplace_back("observation_date", observation_date.to_canonical());
  members.emplace_back("publication_date", publication_date.to_canonical());
  members.emplace_back("publication_timestamp", publication_timestamp.to_canonical());
  members.emplace_back("observation_state", enum_to_canonical(observation_state));
  members.emplace_back("value", value.to_canonical());
  members.emplace_back("day_count_basis", CanonicalValue(day_count_basis));
  members.emplace_back("source", source.to_canonical());
  members.emplace_back("quote_timestamp", quote_timestamp.to_canonical());
  members.emplace_back("version", version.to_canonical());
  members.emplace_back("projection_method_id", projection_method_id.to_canonical());
  members.emplace_back("projection_method_version", projection_method_version.to_canonical());
  members.emplace_back("projection_inputs_ref", projection_inputs_ref.to_canonical());
  return CanonicalValue::make_object(std::move(members));
}

FixingEntry FixingEntry::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"index_id", "observation_date", "publication_date", "publication_timestamp",
                     "observation_state", "value", "day_count_basis", "source", "quote_timestamp",
                     "version", "projection_method_id", "projection_method_version",
                     "projection_inputs_ref"});
  FixingEntry result;
  result.index_id = enum_from_canonical<FloatingIndex>(node.at("index_id", pointer),
                                                       child(pointer, "index_id"));
  result.observation_date = Date::from_canonical(node.at("observation_date", pointer),
                                                 child(pointer, "observation_date"));
  result.publication_date = ValueOrReason<Date>::from_canonical(node.at("publication_date", pointer),
                                                                child(pointer, "publication_date"));
  result.publication_timestamp = ValueOrReason<Timestamp>::from_canonical(
      node.at("publication_timestamp", pointer), child(pointer, "publication_timestamp"));
  result.observation_state = enum_from_canonical<ObservationState>(
      node.at("observation_state", pointer), child(pointer, "observation_state"));
  result.value = ValueOrReason<NumericWithUnit>::from_canonical(node.at("value", pointer),
                                                                child(pointer, "value"));
  result.day_count_basis = require_methodology_text(node.at("day_count_basis", pointer),
                                                    child(pointer, "day_count_basis"),
                                                    "string day-count basis");
  result.source = ValueOrReason<MarketSource>::from_canonical(node.at("source", pointer),
                                                              child(pointer, "source"));
  result.quote_timestamp = ValueOrReason<Timestamp>::from_canonical(
      node.at("quote_timestamp", pointer), child(pointer, "quote_timestamp"));
  result.version = ValueOrReason<std::string>::from_canonical(node.at("version", pointer),
                                                              child(pointer, "version"));
  result.projection_method_id = ValueOrReason<std::string>::from_canonical(
      node.at("projection_method_id", pointer), child(pointer, "projection_method_id"));
  result.projection_method_version = ValueOrReason<std::string>::from_canonical(
      node.at("projection_method_version", pointer), child(pointer, "projection_method_version"));
  result.projection_inputs_ref = ValueOrReason<ProjectionInputsRef>::from_canonical(
      node.at("projection_inputs_ref", pointer), child(pointer, "projection_inputs_ref"));

  // Section 8.2: a PRESENT fixing value must carry DECIMAL_ANNUAL, and the mountain of
  // state-dependent fields must agree with `observation_state`.
  if (result.value.present) {
    (void)result.value.value.require_unit(Unit::kDecimalAnnual, child(pointer, "value"));
    if (result.observation_state == ObservationState::kMissing) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "observation_state"),
           "a MISSING observation must not carry a PRESENT value");
    }
  }
  if (!result.value.present &&
      (result.observation_state == ObservationState::kHistorical ||
       result.observation_state == ObservationState::kProjected)) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "value"),
         "a HISTORICAL or PROJECTED observation requires a PRESENT value with a structured reason "
         "only where the contract permits it");
  }
  if (result.observation_state == ObservationState::kForecastRequired) {
    // Section 8.3: a forecast carries the explicit projection methodology that produced it.
    if (!result.projection_method_id.present || !result.projection_method_version.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "projection_method_id"),
           "FORECAST_REQUIRED requires PRESENT projection method id and version");
    }
    if (!result.value.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "value"),
           "FORECAST_REQUIRED requires the projected value");
    }
  } else if (result.projection_method_id.present || result.projection_method_version.present) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "projection_method_id"),
         "projection methodology is meaningful only for a forecast observation");
  }
  return result;
}

CanonicalValue FixingStoreProvenance::to_canonical() const {
  CanonicalMembers members;
  CanonicalItems source_items;
  source_items.reserve(sources.size());
  for (const MarketSource entry : sources) {
    source_items.push_back(enum_to_canonical(entry));
  }
  members.emplace_back("sources", CanonicalValue::make_array(std::move(source_items)));
  members.emplace_back("assembled_by", CanonicalValue(assembled_by));
  members.emplace_back("adapter_versions", string_map(adapter_versions));
  members.emplace_back("upstream_ids", string_list(upstream_ids));
  members.emplace_back("captured_at", captured_at.to_canonical());
  members.emplace_back("evidence_refs", string_list(evidence_refs));
  return CanonicalValue::make_object(std::move(members));
}

FixingStoreProvenance FixingStoreProvenance::from_canonical(const CanonicalValue& node,
                                                            const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"sources", "assembled_by", "adapter_versions", "upstream_ids", "captured_at",
                     "evidence_refs"});
  FixingStoreProvenance result;
  const CanonicalValue& sources_node = node.at("sources", pointer);
  if (!sources_node.is_array()) {
    fail_wrong_type(child(pointer, "sources"), "array", sources_node.type_name());
  }
  const auto& items = sources_node.as_array().items;
  result.sources.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.sources.push_back(enum_from_canonical<MarketSource>(
        items[index], pointer_index(child(pointer, "sources"), index)));
  }
  result.assembled_by = require_non_empty_string(node.at("assembled_by", pointer),
                                                 child(pointer, "assembled_by"),
                                                 "string assembled-by identity");
  result.adapter_versions = require_string_map(node.at("adapter_versions", pointer),
                                               child(pointer, "adapter_versions"),
                                               "object of adapter versions");
  result.upstream_ids = require_string_list(node.at("upstream_ids", pointer),
                                            child(pointer, "upstream_ids"),
                                            "array of upstream ids");
  result.captured_at = Timestamp::from_canonical(node.at("captured_at", pointer),
                                                 child(pointer, "captured_at"));
  result.evidence_refs = require_string_list(node.at("evidence_refs", pointer),
                                             child(pointer, "evidence_refs"),
                                             "array of evidence refs");
  return result;
}

CanonicalValue FixingStore::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("fixing_store_id", CanonicalValue(fixing_store_id));
  members.emplace_back("valuation_date", valuation_date.to_canonical());
  members.emplace_back("same_day_rule", same_day_rule.to_canonical());
  CanonicalItems entry_items;
  entry_items.reserve(entries.size());
  for (const FixingEntry& entry : entries) {
    entry_items.push_back(entry.to_canonical());
  }
  members.emplace_back("entries", CanonicalValue::make_array(std::move(entry_items)));
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kFixingStoreV1))));
  return CanonicalValue::make_object(std::move(members));
}

FixingStore FixingStore::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kFixingStoreV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "fixing_store_id", "valuation_date", "same_day_rule",
                     "entries", "provenance", "content_fingerprint"});
  FixingStore result;
  result.fixing_store_id = require_non_empty_string(node.at("fixing_store_id", pointer),
                                                    child(pointer, "fixing_store_id"),
                                                    "string fixing store id");
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                              child(pointer, "valuation_date"));
  result.same_day_rule = SameDayRule::from_canonical(node.at("same_day_rule", pointer),
                                                     child(pointer, "same_day_rule"));
  const CanonicalValue& entries_node = node.at("entries", pointer);
  if (!entries_node.is_array()) {
    fail_wrong_type(child(pointer, "entries"), "array", entries_node.type_name());
  }
  const auto& items = entries_node.as_array().items;
  result.entries.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.entries.push_back(
        FixingEntry::from_canonical(items[index], pointer_index(child(pointer, "entries"), index)));
  }
  result.provenance = FixingStoreProvenance::from_canonical(node.at("provenance", pointer),
                                                           child(pointer, "provenance"));
  result.content_fingerprint = require_valid_fingerprint(
      node.at("content_fingerprint", pointer).is_string()
          ? node.at("content_fingerprint", pointer).as_string()
          : std::string(),
      child(pointer, "content_fingerprint"));

  // Section 8.2: (index_id, observation_date) is the unique key of an entry.
  for (std::size_t index = 0; index < result.entries.size(); ++index) {
    for (std::size_t other = index + 1; other < result.entries.size(); ++other) {
      if (result.entries[index].index_id == result.entries[other].index_id &&
          result.entries[index].observation_date.text ==
              result.entries[other].observation_date.text) {
        fail(ContractViolationKind::kInvariantViolation,
             pointer_index(child(pointer, "entries"), other),
             "duplicate fixing (index_id, observation_date) key");
      }
    }
  }
  // Section 8.4: provenance.sources is the deterministic union of the PRESENT entry sources.
  std::vector<MarketSource> union_sources;
  for (const FixingEntry& entry : result.entries) {
    if (!entry.source.present) {
      continue;
    }
    bool seen = false;
    for (const MarketSource present : union_sources) {
      if (present == entry.source.value) {
        seen = true;
        break;
      }
    }
    if (!seen) {
      union_sources.push_back(entry.source.value);
    }
  }
  if (union_sources.size() != result.provenance.sources.size()) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "provenance"),
         "provenance.sources must be the exact union of PRESENT entry sources");
  }
  for (const MarketSource declared : result.provenance.sources) {
    bool found = false;
    for (const MarketSource present : union_sources) {
      if (present == declared) {
        found = true;
        break;
      }
    }
    if (!found) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "provenance"),
           "provenance.sources must not claim a source that no entry carries");
    }
  }
  verify_fixing_store_identities(result, pointer);
  return result;
}

// ---------------------------------------------------------------------------------------------
// Volatility boundary (STRUCTURAL PLACEHOLDER — see docs/CANONICALIZATION.md)
// ---------------------------------------------------------------------------------------------
CanonicalValue VolatilityInput::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("volatility_input_id", CanonicalValue(volatility_input_id));
  members.emplace_back("valuation_date", valuation_date.to_canonical());
  members.emplace_back("payload", payload);
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kVolatilityInputV1))));
  return CanonicalValue::make_object(std::move(members));
}

VolatilityInput VolatilityInput::from_canonical(const CanonicalValue& node,
                                                const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kVolatilityInputV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "volatility_input_id", "valuation_date", "payload",
                     "provenance", "content_fingerprint"});
  VolatilityInput result;
  result.volatility_input_id = require_non_empty_string(node.at("volatility_input_id", pointer),
                                                        child(pointer, "volatility_input_id"),
                                                        "string volatility input id");
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                               child(pointer, "valuation_date"));
  const CanonicalValue& payload_node = node.at("payload", pointer);
  if (!payload_node.is_object()) {
    fail_wrong_type(child(pointer, "payload"), "object", payload_node.type_name());
  }
  result.payload = payload_node;
  result.provenance = SourceProvenance::from_canonical(node.at("provenance", pointer),
                                                       child(pointer, "provenance"));
  result.content_fingerprint = require_valid_fingerprint(
      node.at("content_fingerprint", pointer).is_string()
          ? node.at("content_fingerprint", pointer).as_string()
          : std::string(),
      child(pointer, "content_fingerprint"));
  verify_volatility_identities(result, pointer);
  return result;
}

// ---------------------------------------------------------------------------------------------
// MarketSnapshot
// ---------------------------------------------------------------------------------------------
CanonicalValue SnapshotProvenance::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("assembled_by", CanonicalValue(assembled_by));
  members.emplace_back("adapter_versions", string_map(adapter_versions));
  members.emplace_back("upstream_ids", string_list(upstream_ids));
  members.emplace_back("evidence_refs", string_list(evidence_refs));
  return CanonicalValue::make_object(std::move(members));
}

SnapshotProvenance SnapshotProvenance::from_canonical(const CanonicalValue& node,
                                                      const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"assembled_by", "adapter_versions", "upstream_ids", "evidence_refs"});
  SnapshotProvenance result;
  result.assembled_by = require_non_empty_string(node.at("assembled_by", pointer),
                                                 child(pointer, "assembled_by"),
                                                 "string assembled-by identity");
  result.adapter_versions = require_string_map(node.at("adapter_versions", pointer),
                                               child(pointer, "adapter_versions"),
                                               "object of adapter versions");
  result.upstream_ids = require_string_list(node.at("upstream_ids", pointer),
                                            child(pointer, "upstream_ids"),
                                            "array of upstream ids");
  result.evidence_refs = require_string_list(node.at("evidence_refs", pointer),
                                             child(pointer, "evidence_refs"),
                                             "array of evidence refs");
  return result;
}

CanonicalValue SnapshotDiagnostics::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("unresolved_fields", string_list(unresolved_fields));
  CanonicalItems warning_items;
  warning_items.reserve(warnings.size());
  for (const CodeMessage& warning : warnings) {
    warning_items.push_back(warning.to_canonical());
  }
  members.emplace_back("warnings", CanonicalValue::make_array(std::move(warning_items)));
  return CanonicalValue::make_object(std::move(members));
}

SnapshotDiagnostics SnapshotDiagnostics::from_canonical(const CanonicalValue& node,
                                                        const std::string& pointer) {
  require_only_keys(node, pointer, {"unresolved_fields", "warnings"});
  SnapshotDiagnostics result;
  result.unresolved_fields = require_string_list(node.at("unresolved_fields", pointer),
                                                child(pointer, "unresolved_fields"),
                                                "array of unresolved field names");
  const CanonicalValue& warnings_node = node.at("warnings", pointer);
  if (!warnings_node.is_array()) {
    fail_wrong_type(child(pointer, "warnings"), "array", warnings_node.type_name());
  }
  const auto& items = warnings_node.as_array().items;
  result.warnings.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.warnings.push_back(
        CodeMessage::from_canonical(items[index], pointer_index(child(pointer, "warnings"), index)));
  }
  return result;
}

CanonicalValue MarketSnapshot::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("snapshot_id", CanonicalValue(snapshot_id));
  members.emplace_back("valuation_date", valuation_date.to_canonical());
  members.emplace_back("captured_at", captured_at.to_canonical());
  members.emplace_back("source", enum_to_canonical(source));
  members.emplace_back("source_detail", CanonicalValue(source_detail));
  members.emplace_back("curve_set_ref", CanonicalValue(curve_set_ref));
  members.emplace_back("curve_set", curve_set.to_canonical());
  members.emplace_back("fixing_store_ref", CanonicalValue(fixing_store_ref));
  members.emplace_back("fixing_store", fixing_store.to_canonical());
  members.emplace_back("volatility_ref", volatility_ref.to_canonical());
  members.emplace_back("volatility_input", volatility_input.to_canonical());
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("diagnostics", diagnostics.to_canonical());
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kMarketSnapshotV1))));
  return CanonicalValue::make_object(std::move(members));
}

MarketSnapshot MarketSnapshot::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kMarketSnapshotV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "snapshot_id", "valuation_date", "captured_at", "source",
                     "source_detail", "curve_set_ref", "curve_set", "fixing_store_ref",
                     "fixing_store", "volatility_ref", "volatility_input", "provenance",
                     "content_fingerprint", "diagnostics"});
  MarketSnapshot result;
  result.snapshot_id = require_non_empty_string(node.at("snapshot_id", pointer),
                                                child(pointer, "snapshot_id"), "string snapshot id");
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                              child(pointer, "valuation_date"));
  result.captured_at = Timestamp::from_canonical(node.at("captured_at", pointer),
                                                child(pointer, "captured_at"));
  result.source = enum_from_canonical<MarketSource>(node.at("source", pointer),
                                                   child(pointer, "source"));
  result.source_detail = require_non_empty_string(node.at("source_detail", pointer),
                                                  child(pointer, "source_detail"),
                                                  "string source detail");
  result.curve_set_ref = require_non_empty_string(node.at("curve_set_ref", pointer),
                                                  child(pointer, "curve_set_ref"),
                                                  "string curve set ref");
  result.curve_set = CurveSet::from_canonical(node.at("curve_set", pointer),
                                             child(pointer, "curve_set"));
  result.fixing_store_ref = require_non_empty_string(node.at("fixing_store_ref", pointer),
                                                     child(pointer, "fixing_store_ref"),
                                                     "string fixing store ref");
  result.fixing_store = FixingStore::from_canonical(node.at("fixing_store", pointer),
                                                   child(pointer, "fixing_store"));
  result.volatility_ref = ValueOrReason<std::string>::from_canonical(
      node.at("volatility_ref", pointer), child(pointer, "volatility_ref"));
  result.volatility_input = ValueOrReason<VolatilityInput>::from_canonical(
      node.at("volatility_input", pointer), child(pointer, "volatility_input"));
  result.provenance = SnapshotProvenance::from_canonical(node.at("provenance", pointer),
                                                        child(pointer, "provenance"));
  result.content_fingerprint = require_valid_fingerprint(
      node.at("content_fingerprint", pointer).is_string()
          ? node.at("content_fingerprint", pointer).as_string()
          : std::string(),
      child(pointer, "content_fingerprint"));
  result.diagnostics = SnapshotDiagnostics::from_canonical(node.at("diagnostics", pointer),
                                                          child(pointer, "diagnostics"));
  result.validate_invariants(pointer);
  verify_snapshot_identities(result, pointer);
  return result;
}

void MarketSnapshot::validate_invariants(const std::string& pointer) const {
  // Section 6.4: the identity indexes MUST equal the embedded payload identities.
  if (curve_set_ref != curve_set.curve_set_id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "curve_set_ref"),
         "curve_set_ref must equal the embedded curve_set.curve_set_id");
  }
  if (fixing_store_ref != fixing_store.fixing_store_id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "fixing_store_ref"),
         "fixing_store_ref must equal the embedded fixing_store.fixing_store_id");
  }
  // Section 6.2: volatility_ref PRESENT id MUST equal the embedded volatility_input id, and the two
  // ValueOrReason states must agree (a reference without a payload, or a payload without a
  // reference, is a mismatch rather than something to be repaired).
  if (volatility_ref.present != volatility_input.present) {
    fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "volatility_ref"),
         "volatility_ref and volatility_input must agree on PRESENT vs NULL_WITH_REASON");
  }
  if (volatility_ref.present && volatility_ref.value != volatility_input.value.volatility_input_id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "volatility_ref"),
         "volatility_ref must equal the embedded volatility_input.volatility_input_id");
  }
  // Section 6.5: valuation_date agreement across the snapshot, the curve set, every curve and the
  // fixing store. The valuation date is never defaulted and never silently reconciled.
  if (curve_set.valuation_date.text != valuation_date.text) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "curve_set"),
         "curve_set.valuation_date must equal MarketSnapshot.valuation_date");
  }
  if (fixing_store.valuation_date.text != valuation_date.text) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "fixing_store"),
         "fixing_store.valuation_date must equal MarketSnapshot.valuation_date");
  }
  if (volatility_input.present &&
      volatility_input.value.valuation_date.text != valuation_date.text) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "volatility_input"),
         "volatility_input.valuation_date must equal MarketSnapshot.valuation_date");
  }
}

}  // namespace Shiori::rates::dto
