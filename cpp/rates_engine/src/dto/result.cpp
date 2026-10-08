#include "shiori_rates/dto/result.hpp"

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

[[nodiscard]] std::string require_text(const CanonicalValue& node, const std::string& pointer,
                                       const char* what) {
  if (!node.is_string()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
  if (node.as_string().empty()) {
    fail(ContractViolationKind::kInvariantViolation, pointer, "field must not be empty");
  }
  return node.as_string();
}

[[nodiscard]] CanonicalValue strip_key(const CanonicalValue& object, const std::string_view key) {
  CanonicalMembers members;
  members.reserve(object.as_object().members.size());
  for (const auto& member : object.as_object().members) {
    if (member.first != key) {
      members.push_back(member);
    }
  }
  return CanonicalValue::make_object(std::move(members));
}

// Removes the single leaf `replay.content_fingerprint` from an already-serialized result document.
// Only that one leaf is excluded: every other replay field (inputs_fingerprint, tolerance) is part
// of the preimage, because #225 section 12.2 makes them result content.
[[nodiscard]] CanonicalValue result_preimage(const CanonicalValue& document) {
  CanonicalMembers members;
  members.reserve(document.as_object().members.size());
  for (const auto& member : document.as_object().members) {
    if (member.first == "replay") {
      members.emplace_back("replay", strip_key(member.second, "content_fingerprint"));
    } else {
      members.push_back(member);
    }
  }
  return CanonicalValue::make_object(std::move(members));
}

// `diagnostics` / `assumptions` payload: JSON scalars only. Refusing objects and nested arrays is
// what makes "Lane B telemetry must not become result-contract content" (docs/34 section 12)
// structurally checkable instead of merely documented.
[[nodiscard]] ScalarMap scalar_map_from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  if (!node.is_object()) {
    fail_wrong_type(pointer, "object of scalar values", node.type_name());
  }
  ScalarMap result;
  for (const auto& member : node.as_object().members) {
    const std::string member_pointer = pointer_child(pointer, member.first);
    const CanonicalValue& value = member.second;
    if (value.is_object()) {
      fail(ContractViolationKind::kUnknownField, member_pointer,
           "a nested object is not a typed Lane A scalar; Lane B telemetry belongs to "
           "RATES_RUNTIME_TELEMETRY_V1 and must never enter a result document");
    }
    if (value.is_null()) {
      fail(ContractViolationKind::kWrongType, member_pointer,
           "a null entry carries no typed fact; omit the key or use a scalar");
    }
    if (value.is_array()) {
      for (const CanonicalValue& item : value.as_array().items) {
        if (item.is_object() || item.is_array() || item.is_null()) {
          fail(ContractViolationKind::kWrongType, member_pointer,
               "an array entry must contain scalars only");
        }
      }
    }
    result.emplace(member.first, value);
  }
  return result;
}

[[nodiscard]] CanonicalValue scalar_map_to_canonical(const ScalarMap& values) {
  CanonicalMembers members;
  members.reserve(values.size());
  for (const auto& entry : values) {
    members.emplace_back(entry.first, entry.second);
  }
  return CanonicalValue::make_object(std::move(members));
}

void require_diagnostics(const CanonicalValue& node, const std::string& pointer,
                         const char* what) {
  if (!node.is_array()) {
    fail_wrong_type(pointer, what, node.type_name());
  }
}

[[nodiscard]] std::vector<DiagnosticRecord> diagnostics_from_canonical(const CanonicalValue& node,
                                                                      const std::string& pointer,
                                                                      const char* what) {
  require_diagnostics(node, pointer, what);
  std::vector<DiagnosticRecord> result;
  const auto& items = node.as_array().items;
  result.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.push_back(DiagnosticRecord::from_canonical(items[index], pointer_index(pointer, index)));
  }
  return result;
}

[[nodiscard]] CanonicalValue diagnostics_to_canonical(
    const std::vector<DiagnosticRecord>& records) {
  CanonicalItems items;
  items.reserve(records.size());
  for (const DiagnosticRecord& record : records) {
    items.push_back(record.to_canonical());
  }
  return CanonicalValue::make_array(std::move(items));
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// Shared pieces
// ---------------------------------------------------------------------------------------------
CanonicalValue CurveRoleMap::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("discount_curve_id", discount_curve_id.has_value()
                                                ? CanonicalValue(*discount_curve_id)
                                                : CanonicalValue(nullptr));
  members.emplace_back("forecast_curve_by_index",
                       DtoCodec<std::map<std::string, std::string>>::encode(forecast_curve_by_index));
  return CanonicalValue::make_object(std::move(members));
}

CurveRoleMap CurveRoleMap::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"discount_curve_id", "forecast_curve_by_index"});
  CurveRoleMap result;
  const CanonicalValue* discount_node = node.find("discount_curve_id");
  if (discount_node != nullptr && !discount_node->is_null()) {
    result.discount_curve_id =
        require_text(*discount_node, child(pointer, "discount_curve_id"), "string curve id");
  }
  const CanonicalValue& forecast_node = node.at("forecast_curve_by_index", pointer);
  if (!forecast_node.is_object()) {
    fail_wrong_type(child(pointer, "forecast_curve_by_index"), "object", forecast_node.type_name());
  }
  for (const auto& member : forecast_node.as_object().members) {
    // The key is a FloatingIndex token: a free-text key would silently create a second vocabulary.
    (void)enum_from_token<FloatingIndex>(member.first,
                                        child(child(pointer, "forecast_curve_by_index"),
                                              member.first.c_str()));
    if (!member.second.is_string() || member.second.as_string().empty()) {
      fail_wrong_type(pointer_child(child(pointer, "forecast_curve_by_index"), member.first),
                      "non-empty curve id string", member.second.type_name());
    }
    result.forecast_curve_by_index.emplace(member.first, member.second.as_string());
  }
  if (!result.discount_curve_id.has_value() && result.forecast_curve_by_index.empty()) {
    fail(ContractViolationKind::kInvariantViolation, pointer,
         "curve_role_map must select at least one curve role");
  }
  return result;
}

bool operator==(const InputsIdentity& left, const InputsIdentity& right) {
  return left.market_snapshot_id == right.market_snapshot_id &&
         left.curve_set_id == right.curve_set_id && left.curve_role_map == right.curve_role_map &&
         left.fixing_store_id == right.fixing_store_id &&
         left.volatility_input_id == right.volatility_input_id &&
         left.exercise_terms_id == right.exercise_terms_id &&
         left.settlement_terms_id == right.settlement_terms_id &&
         left.resolved_swap_product_id == right.resolved_swap_product_id &&
         left.convention_set_id == right.convention_set_id &&
         left.resolved_swap_schema_version == right.resolved_swap_schema_version &&
         left.model_input_id == right.model_input_id &&
         left.calibration_result_id == right.calibration_result_id;
}

CanonicalValue EngineIdentity::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("engine_name", CanonicalValue(engine_name));
  members.emplace_back("engine_version", CanonicalValue(engine_version));
  members.emplace_back("method", CanonicalValue(method));
  return CanonicalValue::make_object(std::move(members));
}

EngineIdentity EngineIdentity::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"engine_name", "engine_version", "method"});
  EngineIdentity result;
  result.engine_name = require_text(node.at("engine_name", pointer), child(pointer, "engine_name"),
                                    "string engine name");
  result.engine_version = require_text(node.at("engine_version", pointer),
                                       child(pointer, "engine_version"),
                                       "string engine version");
  result.method = require_text(node.at("method", pointer), child(pointer, "method"),
                               "string method token");
  return result;
}

CanonicalValue InputsIdentity::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("market_snapshot_id", CanonicalValue(market_snapshot_id));
  members.emplace_back("curve_set_id", CanonicalValue(curve_set_id));
  members.emplace_back("curve_role_map", curve_role_map.to_canonical());
  members.emplace_back("fixing_store_id", CanonicalValue(fixing_store_id));
  members.emplace_back("volatility_input_id", volatility_input_id.to_canonical());
  members.emplace_back("exercise_terms_id", exercise_terms_id.to_canonical());
  members.emplace_back("settlement_terms_id", settlement_terms_id.to_canonical());
  members.emplace_back("resolved_swap_product_id", CanonicalValue(resolved_swap_product_id));
  members.emplace_back("convention_set_id", CanonicalValue(convention_set_id));
  members.emplace_back("resolved_swap_schema_version", CanonicalValue(resolved_swap_schema_version));
  members.emplace_back("model_input_id", model_input_id.to_canonical());
  members.emplace_back("calibration_result_id", calibration_result_id.to_canonical());
  return CanonicalValue::make_object(std::move(members));
}

InputsIdentity InputsIdentity::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"market_snapshot_id", "curve_set_id", "curve_role_map", "fixing_store_id",
                     "volatility_input_id", "exercise_terms_id", "settlement_terms_id",
                     "resolved_swap_product_id", "convention_set_id",
                     "resolved_swap_schema_version", "model_input_id", "calibration_result_id"});
  InputsIdentity result;
  result.market_snapshot_id = require_text(node.at("market_snapshot_id", pointer),
                                           child(pointer, "market_snapshot_id"),
                                           "string market snapshot id");
  result.curve_set_id = require_text(node.at("curve_set_id", pointer), child(pointer, "curve_set_id"),
                                     "string curve set id");
  result.curve_role_map = CurveRoleMap::from_canonical(node.at("curve_role_map", pointer),
                                                       child(pointer, "curve_role_map"));
  result.fixing_store_id = require_text(node.at("fixing_store_id", pointer),
                                        child(pointer, "fixing_store_id"),
                                        "string fixing store id");
  result.volatility_input_id = ValueOrReason<std::string>::from_canonical(
      node.at("volatility_input_id", pointer), child(pointer, "volatility_input_id"));
  result.exercise_terms_id = ValueOrReason<std::string>::from_canonical(
      node.at("exercise_terms_id", pointer), child(pointer, "exercise_terms_id"));
  result.settlement_terms_id = ValueOrReason<std::string>::from_canonical(
      node.at("settlement_terms_id", pointer), child(pointer, "settlement_terms_id"));
  result.resolved_swap_product_id = require_text(node.at("resolved_swap_product_id", pointer),
                                                 child(pointer, "resolved_swap_product_id"),
                                                 "string resolved swap product id");
  result.convention_set_id = require_text(node.at("convention_set_id", pointer),
                                          child(pointer, "convention_set_id"),
                                          "string convention set id");
  result.resolved_swap_schema_version =
      require_text(node.at("resolved_swap_schema_version", pointer),
                   child(pointer, "resolved_swap_schema_version"), "string schema version token");
  SchemaVersion parsed{};
  if (!schema_version_from_token(result.resolved_swap_schema_version, parsed)) {
    fail(ContractViolationKind::kUnknownSchemaVersion,
         child(pointer, "resolved_swap_schema_version"),
         "inputs_identity must name a schema version this build knows");
  }
  result.model_input_id = ValueOrReason<std::string>::from_canonical(
      node.at("model_input_id", pointer), child(pointer, "model_input_id"));
  result.calibration_result_id = ValueOrReason<std::string>::from_canonical(
      node.at("calibration_result_id", pointer), child(pointer, "calibration_result_id"));
  return result;
}

CanonicalValue ReplayIdentity::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("inputs_fingerprint", CanonicalValue(inputs_fingerprint));
  members.emplace_back("tolerance", CanonicalValue(tolerance));
  return CanonicalValue::make_object(std::move(members));
}

ReplayIdentity ReplayIdentity::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"content_fingerprint", "inputs_fingerprint", "tolerance"});
  ReplayIdentity result;
  result.content_fingerprint = require_valid_fingerprint(
      node.at("content_fingerprint", pointer).is_string()
          ? node.at("content_fingerprint", pointer).as_string()
          : std::string(),
      child(pointer, "content_fingerprint"));
  result.inputs_fingerprint = require_valid_fingerprint(
      node.at("inputs_fingerprint", pointer).is_string()
          ? node.at("inputs_fingerprint", pointer).as_string()
          : std::string(),
      child(pointer, "inputs_fingerprint"));
  result.tolerance = require_text(node.at("tolerance", pointer), child(pointer, "tolerance"),
                                  "string tolerance token");
  return result;
}

CanonicalValue HeadlineValue::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("semantics", enum_to_canonical(semantics));
  members.emplace_back("value", CanonicalValue(value));
  members.emplace_back("unit", enum_to_canonical(unit));
  members.emplace_back("currency", currency.to_canonical());
  members.emplace_back("sign_convention", sign_convention.to_canonical());
  return CanonicalValue::make_object(std::move(members));
}

HeadlineValue HeadlineValue::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"semantics", "value", "unit", "currency", "sign_convention"});
  HeadlineValue result;
  result.semantics = enum_from_canonical<HeadlineSemantics>(node.at("semantics", pointer),
                                                            child(pointer, "semantics"));
  const CanonicalValue& value_node = node.at("value", pointer);
  if (!value_node.is_float() && !value_node.is_integer()) {
    fail_wrong_type(child(pointer, "value"), "number", value_node.type_name());
  }
  result.value = value_node.as_double();
  result.unit = enum_from_canonical<Unit>(node.at("unit", pointer), child(pointer, "unit"));
  result.currency = ValueOrReason<Currency>::from_canonical(node.at("currency", pointer),
                                                           child(pointer, "currency"));
  result.sign_convention = ValueOrReason<std::string>::from_canonical(
      node.at("sign_convention", pointer), child(pointer, "sign_convention"));
  // Section 12.3: currency-denominated units require PRESENT(result_currency); dimensionless/rate
  // headlines use structured NOT_APPLICABLE unless the approved unit explicitly includes currency.
  const bool currency_denominated =
      result.unit == Unit::kCurrencyAmount || result.unit == Unit::kCurrencyAmountPerBasisPoint;
  if (currency_denominated && !result.currency.present) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "currency"),
         "a currency-denominated headline unit requires PRESENT currency");
  }
  return result;
}

CanonicalValue ComponentPv::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("component_id", CanonicalValue(component_id));
  members.emplace_back("value", CanonicalValue(value));
  members.emplace_back("unit", enum_to_canonical(unit));
  members.emplace_back("sign_convention_ref", sign_convention_ref.to_canonical());
  return CanonicalValue::make_object(std::move(members));
}

ComponentPv ComponentPv::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"component_id", "value", "unit", "sign_convention_ref"});
  ComponentPv result;
  result.component_id = require_text(node.at("component_id", pointer), child(pointer, "component_id"),
                                     "string component id");
  const CanonicalValue& value_node = node.at("value", pointer);
  if (!value_node.is_float() && !value_node.is_integer()) {
    fail_wrong_type(child(pointer, "value"), "number", value_node.type_name());
  }
  result.value = value_node.as_double();
  result.unit = enum_from_canonical<Unit>(node.at("unit", pointer), child(pointer, "unit"));
  result.sign_convention_ref = ValueOrReason<std::string>::from_canonical(
      node.at("sign_convention_ref", pointer), child(pointer, "sign_convention_ref"));
  return result;
}

// ---------------------------------------------------------------------------------------------
// PricingResult
// ---------------------------------------------------------------------------------------------
CanonicalValue PricingResult::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("product_id", CanonicalValue(product_id));
  members.emplace_back("product_type", enum_to_canonical(product_type));
  members.emplace_back("valuation_date", valuation_date.to_canonical());
  members.emplace_back("valuation_context_id", CanonicalValue(valuation_context_id));
  members.emplace_back("result_currency", enum_to_canonical(result_currency));
  members.emplace_back("headline", headline.to_canonical());
  members.emplace_back("pv", pv.to_canonical());
  members.emplace_back("pv_sign_convention", pv_sign_convention.to_canonical());
  members.emplace_back("component_pvs", component_pvs.to_canonical());
  members.emplace_back("annuity_pvbp", annuity_pvbp.to_canonical());
  members.emplace_back("par_rate", par_rate.to_canonical());
  members.emplace_back("status", enum_to_canonical(status));
  members.emplace_back("warnings", diagnostics_to_canonical(warnings));
  members.emplace_back("errors", diagnostics_to_canonical(errors));
  members.emplace_back("engine", engine.to_canonical());
  members.emplace_back("inputs_identity", inputs_identity.to_canonical());
  members.emplace_back("assumptions", scalar_map_to_canonical(assumptions));
  members.emplace_back("diagnostics", scalar_map_to_canonical(diagnostics));
  members.emplace_back("replay", replay.to_canonical());
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kRatesPricingResultV1))));
  return CanonicalValue::make_object(std::move(members));
}

PricingResult PricingResult::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kRatesPricingResultV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "product_id", "product_type", "valuation_date",
                     "valuation_context_id", "result_currency", "headline", "pv",
                     "pv_sign_convention", "component_pvs", "annuity_pvbp", "par_rate", "status",
                     "warnings", "errors", "engine", "inputs_identity", "assumptions",
                     "diagnostics", "replay"});
  PricingResult result;
  result.product_id = require_text(node.at("product_id", pointer), child(pointer, "product_id"),
                                   "string product id");
  result.product_type = enum_from_canonical<ProductType>(node.at("product_type", pointer),
                                                         child(pointer, "product_type"));
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                              child(pointer, "valuation_date"));
  result.valuation_context_id = require_text(node.at("valuation_context_id", pointer),
                                             child(pointer, "valuation_context_id"),
                                             "string valuation context id");
  result.result_currency = enum_from_canonical<Currency>(node.at("result_currency", pointer),
                                                         child(pointer, "result_currency"));
  result.headline = ValueOrReason<HeadlineValue>::from_canonical(node.at("headline", pointer),
                                                                 child(pointer, "headline"));
  result.pv = ValueOrReason<NumericWithUnit>::from_canonical(node.at("pv", pointer),
                                                             child(pointer, "pv"));
  result.pv_sign_convention = ValueOrReason<std::string>::from_canonical(
      node.at("pv_sign_convention", pointer), child(pointer, "pv_sign_convention"));
  result.component_pvs = ValueOrReason<std::vector<ComponentPv>>::from_canonical(
      node.at("component_pvs", pointer), child(pointer, "component_pvs"));
  result.annuity_pvbp = ValueOrReason<NumericWithUnit>::from_canonical(
      node.at("annuity_pvbp", pointer), child(pointer, "annuity_pvbp"));
  result.par_rate = ValueOrReason<NumericWithUnit>::from_canonical(node.at("par_rate", pointer),
                                                                   child(pointer, "par_rate"));
  result.status = enum_from_canonical<PricingStatus>(node.at("status", pointer),
                                                     child(pointer, "status"));
  result.warnings = diagnostics_from_canonical(node.at("warnings", pointer),
                                               child(pointer, "warnings"), "array of warnings");
  result.errors = diagnostics_from_canonical(node.at("errors", pointer), child(pointer, "errors"),
                                             "array of errors");
  result.engine = EngineIdentity::from_canonical(node.at("engine", pointer),
                                                 child(pointer, "engine"));
  result.inputs_identity = InputsIdentity::from_canonical(node.at("inputs_identity", pointer),
                                                          child(pointer, "inputs_identity"));
  result.assumptions = scalar_map_from_canonical(node.at("assumptions", pointer),
                                                 child(pointer, "assumptions"));
  result.diagnostics = scalar_map_from_canonical(node.at("diagnostics", pointer),
                                                 child(pointer, "diagnostics"));
  result.replay = ReplayIdentity::from_canonical(node.at("replay", pointer),
                                                child(pointer, "replay"));
  result.validate_rules(pointer);
  const std::string expected = pricing_result_content_fingerprint(result);
  if (expected != result.replay.content_fingerprint) {
    fail(ContractViolationKind::kFingerprintMismatch, child(child(pointer, "replay"),
                                                            "content_fingerprint"),
         "replay.content_fingerprint does not match the recomputed canonical result preimage");
  }
  return result;
}

void PricingResult::validate_rules(const std::string& pointer) const {
  // Section 12.3: SUCCESS / SUCCESS_WITH_WARNINGS requires PRESENT(HeadlineValue); FAILED requires
  // NULL_WITH_REASON(UNAVAILABLE, PRICING_FAILED) and at least one error.
  const bool success = status == PricingStatus::kSuccess ||
                       status == PricingStatus::kSuccessWithWarnings;
  if (success) {
    if (!headline.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "headline"),
           "a successful result must carry a PRESENT headline value");
    }
    if (!errors.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "errors"),
           "a successful result must not carry errors");
    }
  } else {
    if (headline.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "headline"),
           "a FAILED result must carry NULL_WITH_REASON, never a value, a bare null or a zero");
    }
    if (headline.reason.category != ReasonCategory::kUnavailable) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "headline"),
           "a FAILED result requires headline reason category UNAVAILABLE");
    }
    if (errors.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "errors"),
           "a FAILED result must carry at least one error");
    }
  }
  // Section 12.3: every PRESENT PV uses CURRENCY_AMOUNT plus result_currency plus a PRESENT sign
  // convention.
  if (pv.present) {
    if (pv.value.unit != Unit::kCurrencyAmount) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "pv"),
           "a PRESENT pv requires unit=CURRENCY_AMOUNT");
    }
    if (!pv_sign_convention.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "pv_sign_convention"),
           "a PRESENT pv requires an explicit sign convention; the legacy rule is reference only");
    }
  }
  if (par_rate.present && par_rate.value.unit != Unit::kDecimalAnnual) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "par_rate"),
         "a PRESENT par_rate requires unit=DECIMAL_ANNUAL");
  }
  // Section 12.3: when the headline designates a supplemental metric, that metric must be PRESENT
  // and exactly equal the headline value + unit.
  if (headline.present) {
    const HeadlineValue& value = headline.value;
    struct SupplementalMetric {
      HeadlineSemantics semantics;
      const char* field;
      const ValueOrReason<NumericWithUnit>* metric;
    };
    const SupplementalMetric pairs[] = {
        {HeadlineSemantics::kPresentValue, "pv", &pv},
        {HeadlineSemantics::kParRate, "par_rate", &par_rate},
        {HeadlineSemantics::kAnnuityPvbp, "annuity_pvbp", &annuity_pvbp}};
    for (const auto& pair : pairs) {
      if (value.semantics != pair.semantics) {
        continue;
      }
      if (!pair.metric->present) {
        fail(ContractViolationKind::kInvariantViolation, child(pointer, pair.field),
             "the headline designates this supplemental metric, so it must be PRESENT");
      }
      if (pair.metric->value.value != value.value || pair.metric->value.unit != value.unit) {
        fail(ContractViolationKind::kInvariantViolation, child(pointer, pair.field),
             "the designated supplemental metric must exactly equal the headline value and unit");
      }
    }
  }
}

std::string pricing_result_content_fingerprint(const PricingResult& result) {
  // The own `replay.content_fingerprint` leaf is excluded and nothing else, so the preimage still
  // contains every other replay field (docs/33 section 15.4 non-recursive rule).
  return fingerprint_of(result_preimage(result.to_canonical()));
}

// ---------------------------------------------------------------------------------------------
// RiskResult
// ---------------------------------------------------------------------------------------------
CanonicalValue TenorBucketCoordinate::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("kind", CanonicalValue(std::string("TENOR")));
  members.emplace_back("curve_id", CanonicalValue(curve_id));
  members.emplace_back("curve_usage_role", enum_to_canonical(curve_usage_role));
  members.emplace_back("index_id", index_id.to_canonical());
  members.emplace_back("coordinate", coordinate.to_canonical());
  members.emplace_back("label", label.to_canonical());
  return CanonicalValue::make_object(std::move(members));
}

TenorBucketCoordinate TenorBucketCoordinate::from_canonical(const CanonicalValue& node,
                                                            const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"kind", "curve_id", "curve_usage_role", "index_id", "coordinate", "label"});
  TenorBucketCoordinate result;
  if (require_text(node.at("kind", pointer), child(pointer, "kind"), "string kind") != "TENOR") {
    fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "kind"),
         "kind must be TENOR for a tenor bucket coordinate");
  }
  result.curve_id = require_text(node.at("curve_id", pointer), child(pointer, "curve_id"),
                                 "string curve id");
  result.curve_usage_role = enum_from_canonical<CurveUsageRole>(node.at("curve_usage_role", pointer),
                                                                child(pointer, "curve_usage_role"));
  result.index_id = ValueOrReason<FloatingIndex>::from_canonical(node.at("index_id", pointer),
                                                                child(pointer, "index_id"));
  result.coordinate = NumericWithUnit::from_canonical(node.at("coordinate", pointer),
                                                      child(pointer, "coordinate"));
  // Section 13.2: the tenor coordinate is the authoritative machine coordinate and must be a year
  // fraction; a label can never change identity.
  (void)result.coordinate.require_unit(Unit::kYearsFraction, child(pointer, "coordinate"));
  result.label = ValueOrReason<std::string>::from_canonical(node.at("label", pointer),
                                                            child(pointer, "label"));
  if (result.curve_usage_role == CurveUsageRole::kDiscount) {
    (void)result.index_id.require_null_with_reason(child(pointer, "index_id"),
                                             ReasonCategory::kNotApplicable);
  } else if (!result.index_id.present) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "index_id"),
         "a FORECAST tenor bucket requires a PRESENT index id");
  }
  return result;
}

CanonicalValue VolNodeBucketCoordinate::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("kind", CanonicalValue(std::string("VOL_NODE")));
  members.emplace_back("volatility_input_id", CanonicalValue(volatility_input_id));
  members.emplace_back("node_key", node_key);
  return CanonicalValue::make_object(std::move(members));
}

VolNodeBucketCoordinate VolNodeBucketCoordinate::from_canonical(const CanonicalValue& node,
                                                                const std::string& pointer) {
  require_only_keys(node, pointer, {"kind", "volatility_input_id", "node_key"});
  VolNodeBucketCoordinate result;
  if (require_text(node.at("kind", pointer), child(pointer, "kind"), "string kind") != "VOL_NODE") {
    fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "kind"),
         "kind must be VOL_NODE");
  }
  result.volatility_input_id = require_text(node.at("volatility_input_id", pointer),
                                            child(pointer, "volatility_input_id"),
                                            "string volatility input id");
  const CanonicalValue& node_key = node.at("node_key", pointer);
  if (!node_key.is_object()) {
    fail_wrong_type(child(pointer, "node_key"), "object", node_key.type_name());
  }
  result.node_key = node_key;
  return result;
}

CanonicalValue ModelParameterBucketCoordinate::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("kind", CanonicalValue(std::string("MODEL_PARAMETER")));
  members.emplace_back("model_input_id", CanonicalValue(model_input_id));
  members.emplace_back("parameter_name", CanonicalValue(parameter_name));
  return CanonicalValue::make_object(std::move(members));
}

ModelParameterBucketCoordinate ModelParameterBucketCoordinate::from_canonical(
    const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer, {"kind", "model_input_id", "parameter_name"});
  ModelParameterBucketCoordinate result;
  if (require_text(node.at("kind", pointer), child(pointer, "kind"), "string kind") !=
      "MODEL_PARAMETER") {
    fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "kind"),
         "kind must be MODEL_PARAMETER");
  }
  result.model_input_id = require_text(node.at("model_input_id", pointer),
                                       child(pointer, "model_input_id"),
                                       "string model input id");
  result.parameter_name = require_text(node.at("parameter_name", pointer),
                                       child(pointer, "parameter_name"),
                                       "string parameter name");
  return result;
}

RiskBucketKind RiskBucketCoordinate::kind() const {
  if (std::holds_alternative<TenorBucketCoordinate>(body)) {
    return RiskBucketKind::kTenor;
  }
  if (std::holds_alternative<VolNodeBucketCoordinate>(body)) {
    return RiskBucketKind::kVolNode;
  }
  return RiskBucketKind::kModelParameter;
}

CanonicalValue RiskBucketCoordinate::to_canonical() const {
  if (std::holds_alternative<TenorBucketCoordinate>(body)) {
    return std::get<TenorBucketCoordinate>(body).to_canonical();
  }
  if (std::holds_alternative<VolNodeBucketCoordinate>(body)) {
    return std::get<VolNodeBucketCoordinate>(body).to_canonical();
  }
  return std::get<ModelParameterBucketCoordinate>(body).to_canonical();
}

RiskBucketCoordinate RiskBucketCoordinate::from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer) {
  if (!node.is_object()) {
    fail_wrong_type(pointer, "object tagged union", node.type_name());
  }
  const CanonicalValue* kind_node = node.find("kind");
  if (kind_node == nullptr || !kind_node->is_string()) {
    fail_missing(pointer, "kind");
  }
  const std::string& kind_token = kind_node->as_string();
  if (kind_token == "TENOR") {
    return RiskBucketCoordinate{TenorBucketCoordinate::from_canonical(node, pointer)};
  }
  if (kind_token == "VOL_NODE") {
    return RiskBucketCoordinate{VolNodeBucketCoordinate::from_canonical(node, pointer)};
  }
  if (kind_token == "MODEL_PARAMETER") {
    return RiskBucketCoordinate{ModelParameterBucketCoordinate::from_canonical(node, pointer)};
  }
  fail(ContractViolationKind::kUnknownEnumToken, child(pointer, "kind"),
       "bucket coordinate kind must be TENOR, VOL_NODE or MODEL_PARAMETER");
}

CanonicalValue AllSelectedCurvesTarget::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("kind", CanonicalValue(std::string("ALL_SELECTED_CURVES")));
  return CanonicalValue::make_object(std::move(members));
}

CanonicalValue DiscountCurveTarget::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("kind", CanonicalValue(std::string("DISCOUNT_CURVE")));
  members.emplace_back("curve_id", CanonicalValue(curve_id));
  return CanonicalValue::make_object(std::move(members));
}

DiscountCurveTarget DiscountCurveTarget::from_canonical(const CanonicalValue& node,
                                                        const std::string& pointer) {
  require_only_keys(node, pointer, {"kind", "curve_id"});
  DiscountCurveTarget result;
  if (require_text(node.at("kind", pointer), child(pointer, "kind"), "string kind") !=
      "DISCOUNT_CURVE") {
    fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "kind"),
         "kind must be DISCOUNT_CURVE");
  }
  result.curve_id = require_text(node.at("curve_id", pointer), child(pointer, "curve_id"),
                                 "string curve id");
  return result;
}

CanonicalValue ForecastCurveTarget::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("kind", CanonicalValue(std::string("FORECAST_CURVE")));
  members.emplace_back("curve_id", CanonicalValue(curve_id));
  members.emplace_back("index_id", enum_to_canonical(index_id));
  return CanonicalValue::make_object(std::move(members));
}

ForecastCurveTarget ForecastCurveTarget::from_canonical(const CanonicalValue& node,
                                                        const std::string& pointer) {
  require_only_keys(node, pointer, {"kind", "curve_id", "index_id"});
  ForecastCurveTarget result;
  if (require_text(node.at("kind", pointer), child(pointer, "kind"), "string kind") !=
      "FORECAST_CURVE") {
    fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "kind"),
         "kind must be FORECAST_CURVE");
  }
  result.curve_id = require_text(node.at("curve_id", pointer), child(pointer, "curve_id"),
                                 "string curve id");
  result.index_id = enum_from_canonical<FloatingIndex>(node.at("index_id", pointer),
                                                      child(pointer, "index_id"));
  return result;
}

RiskBumpTargetKind RiskBumpTarget::kind() const {
  if (std::holds_alternative<AllSelectedCurvesTarget>(body)) {
    return RiskBumpTargetKind::kAllSelectedCurves;
  }
  if (std::holds_alternative<DiscountCurveTarget>(body)) {
    return RiskBumpTargetKind::kDiscountCurve;
  }
  return RiskBumpTargetKind::kForecastCurve;
}

CanonicalValue RiskBumpTarget::to_canonical() const {
  if (std::holds_alternative<AllSelectedCurvesTarget>(body)) {
    return std::get<AllSelectedCurvesTarget>(body).to_canonical();
  }
  if (std::holds_alternative<DiscountCurveTarget>(body)) {
    return std::get<DiscountCurveTarget>(body).to_canonical();
  }
  return std::get<ForecastCurveTarget>(body).to_canonical();
}

RiskBumpTarget RiskBumpTarget::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  if (!node.is_object()) {
    fail_wrong_type(pointer, "object tagged union", node.type_name());
  }
  const CanonicalValue* kind_node = node.find("kind");
  if (kind_node == nullptr || !kind_node->is_string()) {
    fail_missing(pointer, "kind");
  }
  const std::string& kind_token = kind_node->as_string();
  if (kind_token == "ALL_SELECTED_CURVES") {
    require_only_keys(node, pointer, {"kind"});
    return RiskBumpTarget{AllSelectedCurvesTarget{}};
  }
  if (kind_token == "DISCOUNT_CURVE") {
    return RiskBumpTarget{DiscountCurveTarget::from_canonical(node, pointer)};
  }
  if (kind_token == "FORECAST_CURVE") {
    return RiskBumpTarget{ForecastCurveTarget::from_canonical(node, pointer)};
  }
  fail(ContractViolationKind::kUnknownEnumToken, child(pointer, "kind"),
       "bump target kind must be ALL_SELECTED_CURVES, DISCOUNT_CURVE or FORECAST_CURVE");
}

CanonicalValue RiskBumpSpec::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("bump_type", enum_to_canonical(bump_type));
  members.emplace_back("bump_size", CanonicalValue(bump_size));
  members.emplace_back("bump_unit", enum_to_canonical(bump_unit));
  members.emplace_back("bump_target", bump_target.to_canonical());
  members.emplace_back("revaluation_rule_id", CanonicalValue(revaluation_rule_id));
  members.emplace_back("revaluation_rule_version", CanonicalValue(revaluation_rule_version));
  return CanonicalValue::make_object(std::move(members));
}

RiskBumpSpec RiskBumpSpec::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"bump_type", "bump_size", "bump_unit", "bump_target", "revaluation_rule_id",
                     "revaluation_rule_version"});
  RiskBumpSpec result;
  result.bump_type = enum_from_canonical<RiskBumpType>(node.at("bump_type", pointer),
                                                       child(pointer, "bump_type"));
  const CanonicalValue& size_node = node.at("bump_size", pointer);
  if (!size_node.is_float() && !size_node.is_integer()) {
    fail_wrong_type(child(pointer, "bump_size"), "number", size_node.type_name());
  }
  result.bump_size = size_node.as_double();
  result.bump_unit = enum_from_canonical<Unit>(node.at("bump_unit", pointer),
                                              child(pointer, "bump_unit"));
  result.bump_target = ValueOrReason<RiskBumpTarget>::from_canonical(
      node.at("bump_target", pointer), child(pointer, "bump_target"));
  result.revaluation_rule_id = require_text(node.at("revaluation_rule_id", pointer),
                                            child(pointer, "revaluation_rule_id"),
                                            "string revaluation rule id");
  result.revaluation_rule_version = require_text(node.at("revaluation_rule_version", pointer),
                                                 child(pointer, "revaluation_rule_version"),
                                                 "string revaluation rule version");
  return result;
}

CanonicalValue RiskMeasure::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("measure_id", enum_to_canonical(measure_id));
  members.emplace_back("value", CanonicalValue(value));
  members.emplace_back("unit", enum_to_canonical(unit));
  members.emplace_back("bump_spec", bump_spec.to_canonical());
  members.emplace_back("bucket_coordinate", bucket_coordinate.to_canonical());
  members.emplace_back("market_snapshot_id", CanonicalValue(market_snapshot_id));
  members.emplace_back("model_version", model_version.has_value() ? CanonicalValue(*model_version)
                                                                  : CanonicalValue(nullptr));
  return CanonicalValue::make_object(std::move(members));
}

RiskMeasure RiskMeasure::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"measure_id", "value", "unit", "bump_spec", "bucket_coordinate",
                     "market_snapshot_id", "model_version"});
  RiskMeasure result;
  result.measure_id = enum_from_canonical<RiskMeasureId>(node.at("measure_id", pointer),
                                                         child(pointer, "measure_id"));
  const CanonicalValue& value_node = node.at("value", pointer);
  if (!value_node.is_float() && !value_node.is_integer()) {
    fail_wrong_type(child(pointer, "value"), "number", value_node.type_name());
  }
  result.value = value_node.as_double();
  result.unit = enum_from_canonical<Unit>(node.at("unit", pointer), child(pointer, "unit"));
  result.bump_spec = RiskBumpSpec::from_canonical(node.at("bump_spec", pointer),
                                                  child(pointer, "bump_spec"));
  result.bucket_coordinate = ValueOrReason<RiskBucketCoordinate>::from_canonical(
      node.at("bucket_coordinate", pointer), child(pointer, "bucket_coordinate"));
  result.market_snapshot_id = require_text(node.at("market_snapshot_id", pointer),
                                           child(pointer, "market_snapshot_id"),
                                           "string market snapshot id");
  const CanonicalValue* model_node = node.find("model_version");
  if (model_node != nullptr && !model_node->is_null()) {
    result.model_version = require_text(*model_node, child(pointer, "model_version"),
                                        "string model version");
  }

  // Section 13.2: bucket_coordinate and bump_target applicability are exact by bump_type.
  const RiskBumpType bump_type = result.bump_spec.bump_type;
  if (bump_type == RiskBumpType::kParallelBp) {
    (void)result.bucket_coordinate.require_null_with_reason(child(pointer, "bucket_coordinate"),
                                                      ReasonCategory::kNotApplicable);
    if (!result.bump_spec.bump_target.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "bump_spec"),
           "PARALLEL_BP requires a PRESENT bump target: it is the only identity that "
           "distinguishes the bumped scope");
    }
  } else {
    if (!result.bucket_coordinate.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "bucket_coordinate"),
           "a bucketed bump type requires a PRESENT bucket coordinate");
    }
    (void)result.bump_spec.bump_target.require_null_with_reason(
        child(child(pointer, "bump_spec"), "bump_target"), ReasonCategory::kNotApplicable);
    const RiskBucketKind expected =
        bump_type == RiskBumpType::kBucketedTenor
            ? RiskBucketKind::kTenor
            : (bump_type == RiskBumpType::kVolPoint ? RiskBucketKind::kVolNode
                                                    : RiskBucketKind::kModelParameter);
    if (result.bucket_coordinate.value.kind() != expected) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "bucket_coordinate"),
           "bucket coordinate variant does not match bump_type");
    }
  }
  return result;
}

CanonicalValue RiskResult::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("product_id", CanonicalValue(product_id));
  members.emplace_back("product_type", enum_to_canonical(product_type));
  members.emplace_back("valuation_date", valuation_date.to_canonical());
  members.emplace_back("valuation_context_id", CanonicalValue(valuation_context_id));
  members.emplace_back("result_currency", enum_to_canonical(result_currency));
  CanonicalItems measure_items;
  measure_items.reserve(measures.size());
  for (const RiskMeasure& measure : measures) {
    measure_items.push_back(measure.to_canonical());
  }
  members.emplace_back("measures", CanonicalValue::make_array(std::move(measure_items)));
  members.emplace_back("inputs_identity", inputs_identity.to_canonical());
  members.emplace_back("status", enum_to_canonical(status));
  members.emplace_back("warnings", diagnostics_to_canonical(warnings));
  members.emplace_back("errors", diagnostics_to_canonical(errors));
  members.emplace_back("engine", engine.to_canonical());
  members.emplace_back("diagnostics", scalar_map_to_canonical(diagnostics));
  members.emplace_back("replay", replay.to_canonical());
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kRatesRiskResultV1))));
  return CanonicalValue::make_object(std::move(members));
}

RiskResult RiskResult::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  (void)require_schema_version(node, SchemaVersion::kRatesRiskResultV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "product_id", "product_type", "valuation_date",
                     "valuation_context_id", "result_currency", "measures", "inputs_identity",
                     "status", "warnings", "errors", "engine", "diagnostics", "replay"});
  RiskResult result;
  result.product_id = require_text(node.at("product_id", pointer), child(pointer, "product_id"),
                                   "string product id");
  result.product_type = enum_from_canonical<ProductType>(node.at("product_type", pointer),
                                                         child(pointer, "product_type"));
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                              child(pointer, "valuation_date"));
  result.valuation_context_id = require_text(node.at("valuation_context_id", pointer),
                                             child(pointer, "valuation_context_id"),
                                             "string valuation context id");
  result.result_currency = enum_from_canonical<Currency>(node.at("result_currency", pointer),
                                                         child(pointer, "result_currency"));
  const CanonicalValue& measures_node = node.at("measures", pointer);
  if (!measures_node.is_array()) {
    fail_wrong_type(child(pointer, "measures"), "array", measures_node.type_name());
  }
  const auto& measure_items = measures_node.as_array().items;
  result.measures.reserve(measure_items.size());
  for (std::size_t index = 0; index < measure_items.size(); ++index) {
    result.measures.push_back(
        RiskMeasure::from_canonical(measure_items[index], pointer_index(child(pointer, "measures"),
                                                                        index)));
  }
  result.inputs_identity = InputsIdentity::from_canonical(node.at("inputs_identity", pointer),
                                                          child(pointer, "inputs_identity"));
  result.status = enum_from_canonical<PricingStatus>(node.at("status", pointer),
                                                     child(pointer, "status"));
  result.warnings = diagnostics_from_canonical(node.at("warnings", pointer),
                                               child(pointer, "warnings"), "array of warnings");
  result.errors = diagnostics_from_canonical(node.at("errors", pointer), child(pointer, "errors"),
                                             "array of errors");
  result.engine = EngineIdentity::from_canonical(node.at("engine", pointer),
                                                 child(pointer, "engine"));
  result.diagnostics = scalar_map_from_canonical(node.at("diagnostics", pointer),
                                                 child(pointer, "diagnostics"));
  result.replay = ReplayIdentity::from_canonical(node.at("replay", pointer),
                                                child(pointer, "replay"));
  result.validate_rules(pointer);
  const std::string expected = risk_result_content_fingerprint(result);
  if (expected != result.replay.content_fingerprint) {
    fail(ContractViolationKind::kFingerprintMismatch,
         child(child(pointer, "replay"), "content_fingerprint"),
         "replay.content_fingerprint does not match the recomputed canonical result preimage");
  }
  return result;
}

void RiskResult::validate_rules(const std::string& pointer) const {
  const bool success = status == PricingStatus::kSuccess ||
                       status == PricingStatus::kSuccessWithWarnings;
  if (success) {
    if (measures.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "measures"),
           "a successful risk result must carry at least one measure");
    }
    if (!errors.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "errors"),
           "a successful risk result must not carry errors");
    }
  } else if (errors.empty()) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "errors"),
         "a FAILED risk result must carry at least one error");
  }
  // Section 13.2: bucket coordinates and bump targets must agree with the selected role map.
  for (std::size_t index = 0; index < measures.size(); ++index) {
    const RiskMeasure& measure = measures[index];
    const std::string measure_pointer = pointer_index(child(pointer, "measures"), index);
    if (measure.market_snapshot_id != inputs_identity.market_snapshot_id) {
      fail(ContractViolationKind::kIdentityMismatch, child(measure_pointer, "market_snapshot_id"),
           "a measure must name the snapshot that inputs_identity names");
    }
    const std::string& map_discount = inputs_identity.curve_role_map.discount_curve_id.value_or("");
    if (measure.bump_spec.bump_target.present) {
      const RiskBumpTarget& target = measure.bump_spec.bump_target.value;
      if (target.kind() == RiskBumpTargetKind::kDiscountCurve) {
        const auto& discount = std::get<DiscountCurveTarget>(target.body);
        if (discount.curve_id != map_discount) {
          fail(ContractViolationKind::kIdentityMismatch,
               child(child(measure_pointer, "bump_spec"), "bump_target"),
               "DISCOUNT_CURVE.curve_id must equal curve_role_map.discount_curve_id");
        }
      } else if (target.kind() == RiskBumpTargetKind::kForecastCurve) {
        const auto& forecast = std::get<ForecastCurveTarget>(target.body);
        const auto entry = inputs_identity.curve_role_map.forecast_curve_by_index.find(
            std::string(enum_to_token(forecast.index_id)));
        if (entry == inputs_identity.curve_role_map.forecast_curve_by_index.end() ||
            entry->second != forecast.curve_id) {
          fail(ContractViolationKind::kIdentityMismatch,
               child(child(measure_pointer, "bump_spec"), "bump_target"),
               "FORECAST_CURVE must match one curve_role_map.forecast_curve_by_index entry");
        }
      }
    }
    if (measure.bucket_coordinate.present &&
        measure.bucket_coordinate.value.kind() == RiskBucketKind::kTenor) {
      const auto& tenor = std::get<TenorBucketCoordinate>(measure.bucket_coordinate.value.body);
      if (tenor.curve_usage_role == CurveUsageRole::kDiscount) {
        if (tenor.curve_id != map_discount) {
          fail(ContractViolationKind::kIdentityMismatch,
               child(measure_pointer, "bucket_coordinate"),
               "a DISCOUNT tenor bucket must reference curve_role_map.discount_curve_id");
        }
      } else {
        const std::string key(enum_to_token(tenor.index_id.require_present(
            child(measure_pointer, "bucket_coordinate"))));
        const auto entry = inputs_identity.curve_role_map.forecast_curve_by_index.find(key);
        if (entry == inputs_identity.curve_role_map.forecast_curve_by_index.end() ||
            entry->second != tenor.curve_id) {
          fail(ContractViolationKind::kIdentityMismatch,
               child(measure_pointer, "bucket_coordinate"),
               "a FORECAST tenor bucket must match its curve_role_map entry");
        }
      }
    }
  }
}

std::string risk_result_content_fingerprint(const RiskResult& result) {
  return fingerprint_of(result_preimage(result.to_canonical()));
}

void assign_derived_identities(PricingResult& result) {
  result.replay.content_fingerprint = pricing_result_content_fingerprint(result);
}

void assign_derived_identities(RiskResult& result) {
  result.replay.content_fingerprint = risk_result_content_fingerprint(result);
}

bool is_lane_a_scalar_map(const CanonicalValue& node, std::string* offending_key) {
  if (!node.is_object()) {
    return false;
  }
  for (const auto& member : node.as_object().members) {
    const CanonicalValue& value = member.second;
    if (value.is_object() || value.is_null()) {
      if (offending_key != nullptr) {
        *offending_key = member.first;
      }
      return false;
    }
    if (value.is_array()) {
      for (const CanonicalValue& item : value.as_array().items) {
        if (item.is_object() || item.is_array() || item.is_null()) {
          if (offending_key != nullptr) {
            *offending_key = member.first;
          }
          return false;
        }
      }
    }
  }
  return true;
}

}  // namespace Shiori::rates::dto
