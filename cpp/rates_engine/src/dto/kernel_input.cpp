#include "shiori_rates/dto/kernel_input.hpp"

#include <string>
#include <utility>
#include <vector>

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

[[nodiscard]] std::string require_methodology_text(const CanonicalValue& node,
                                                   const std::string& pointer, const char* what) {
  return require_text(node, pointer, what);
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

// One derived pair: id + content fingerprint, where the id is optional (ResolvedSwap and
// RatesKernelInput have no id field).
[[nodiscard]] std::string derive_id(const CanonicalValue& document,
                                    const std::vector<std::string_view>& excluded) {
  CanonicalMembers members;
  for (const auto& member : document.as_object().members) {
    bool drop = false;
    for (const std::string_view key : excluded) {
      if (member.first == key) {
        drop = true;
        break;
      }
    }
    if (!drop) {
      members.push_back(member);
    }
  }
  return fingerprint_of(CanonicalValue::make_object(std::move(members)));
}

[[nodiscard]] std::vector<Date> date_list_from_canonical(const CanonicalValue& node,
                                                         const std::string& pointer) {
  if (!node.is_array()) {
    fail_wrong_type(pointer, "array of ISO dates", node.type_name());
  }
  std::vector<Date> result;
  const auto& items = node.as_array().items;
  result.reserve(items.size());
  for (std::size_t index = 0; index < items.size(); ++index) {
    result.push_back(Date::from_canonical(items[index], pointer_index(pointer, index)));
  }
  return result;
}

[[nodiscard]] CanonicalValue date_list_to_canonical(const std::vector<Date>& dates) {
  CanonicalItems items;
  items.reserve(dates.size());
  for (const Date& date : dates) {
    items.push_back(date.to_canonical());
  }
  return CanonicalValue::make_array(std::move(items));
}

[[nodiscard]] std::string require_fingerprint_node(const CanonicalValue& node,
                                                   const std::string& pointer) {
  return require_valid_fingerprint(node.is_string() ? node.as_string() : std::string(), pointer);
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// ResolvedSwap
// ---------------------------------------------------------------------------------------------
CanonicalValue ResolvedSwap::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("product_id", CanonicalValue(product_id));
  members.emplace_back("convention_set_id", enum_to_canonical(convention_set_id));
  members.emplace_back("currency", enum_to_canonical(currency));
  members.emplace_back("notional", notional.to_canonical());
  members.emplace_back("pay_receive", enum_to_canonical(pay_receive));
  members.emplace_back("fixed_rate", fixed_rate.to_canonical());
  members.emplace_back("spread", spread.to_canonical());
  members.emplace_back("floating_index", enum_to_canonical(floating_index));
  members.emplace_back("resolved", resolved);
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kResolvedSwapV1))));
  return CanonicalValue::make_object(std::move(members));
}

ResolvedSwap ResolvedSwap::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_schema_version(node, SchemaVersion::kResolvedSwapV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "product_id", "convention_set_id", "currency", "notional",
                     "pay_receive", "fixed_rate", "spread", "floating_index", "resolved",
                     "provenance", "content_fingerprint"});
  ResolvedSwap result;
  result.product_id = require_text(node.at("product_id", pointer), child(pointer, "product_id"),
                                   "string product id");
  // docs/32: an unknown convention_set_id fails closed rather than being coerced.
  result.convention_set_id = enum_from_canonical<ConventionSetId>(node.at("convention_set_id", pointer),
                                                                  child(pointer, "convention_set_id"));
  result.currency = enum_from_canonical<Currency>(node.at("currency", pointer),
                                                  child(pointer, "currency"));
  result.notional = NumericWithUnit::from_canonical(node.at("notional", pointer),
                                                    child(pointer, "notional"));
  result.notional.require_unit(Unit::kCurrencyAmount, child(pointer, "notional"));
  result.pay_receive = enum_from_canonical<PayReceive>(node.at("pay_receive", pointer),
                                                       child(pointer, "pay_receive"));
  result.fixed_rate = NumericWithUnit::from_canonical(node.at("fixed_rate", pointer),
                                                      child(pointer, "fixed_rate"));
  result.fixed_rate.require_unit(Unit::kDecimalAnnual, child(pointer, "fixed_rate"));
  result.spread = NumericWithUnit::from_canonical(node.at("spread", pointer),
                                                  child(pointer, "spread"));
  result.spread.require_unit(Unit::kDecimalAnnual, child(pointer, "spread"));
  result.floating_index = enum_from_canonical<FloatingIndex>(node.at("floating_index", pointer),
                                                             child(pointer, "floating_index"));
  const CanonicalValue& resolved_node = node.at("resolved", pointer);
  if (!resolved_node.is_object()) {
    fail_wrong_type(child(pointer, "resolved"), "object", resolved_node.type_name());
  }
  result.resolved = resolved_node;
  result.provenance = SourceProvenance::from_canonical(node.at("provenance", pointer),
                                                       child(pointer, "provenance"));
  result.content_fingerprint = require_fingerprint_node(node.at("content_fingerprint", pointer),
                                                        child(pointer, "content_fingerprint"));
  verify_resolved_swap_identities(result, pointer);
  return result;
}

CanonicalValue resolved_swap_fingerprint_preimage(const ResolvedSwap& swap) {
  return strip_key(swap.to_canonical(), "content_fingerprint");
}

std::string resolved_swap_content_fingerprint(const ResolvedSwap& swap) {
  return fingerprint_of(resolved_swap_fingerprint_preimage(swap));
}

void verify_resolved_swap_identities(const ResolvedSwap& swap, const std::string& pointer) {
  if (resolved_swap_content_fingerprint(swap) != swap.content_fingerprint) {
    fail(ContractViolationKind::kFingerprintMismatch, child(pointer, "content_fingerprint"),
         "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(ResolvedSwap& swap) {
  swap.content_fingerprint = resolved_swap_content_fingerprint(swap);
}

// ---------------------------------------------------------------------------------------------
// ExerciseTerms
// ---------------------------------------------------------------------------------------------
CanonicalValue UnderlyingReference::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("underlying_product_id", CanonicalValue(underlying_product_id));
  members.emplace_back("underlying_start_rule", CanonicalValue(underlying_start_rule));
  members.emplace_back("underlying_start_rule_version",
                       CanonicalValue(underlying_start_rule_version));
  return CanonicalValue::make_object(std::move(members));
}

UnderlyingReference UnderlyingReference::from_canonical(const CanonicalValue& node,
                                                        const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"underlying_product_id", "underlying_start_rule",
                     "underlying_start_rule_version"});
  UnderlyingReference result;
  result.underlying_product_id = require_text(node.at("underlying_product_id", pointer),
                                              child(pointer, "underlying_product_id"),
                                              "string underlying product id");
  result.underlying_start_rule =
      require_methodology_text(node.at("underlying_start_rule", pointer),
                               child(pointer, "underlying_start_rule"),
                               "string underlying start rule");
  result.underlying_start_rule_version =
      require_methodology_text(node.at("underlying_start_rule_version", pointer),
                               child(pointer, "underlying_start_rule_version"),
                               "string underlying start rule version");
  return result;
}

namespace {

constexpr std::string_view kExerciseKeys[] = {
    "schema_version",             "exercise_terms_id",      "exercise_style",
    "exercise_dates",             "notice_dates",           "underlying_reference",
    "calendar_ref",               "business_day_convention", "timezone",
    "expiry_time",                "content_fingerprint"};

}  // namespace

CanonicalValue ExerciseTerms::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("exercise_terms_id", CanonicalValue(exercise_terms_id));
  members.emplace_back("exercise_style", enum_to_canonical(exercise_style));
  members.emplace_back("exercise_dates", date_list_to_canonical(exercise_dates));
  members.emplace_back("notice_dates", notice_dates.to_canonical());
  members.emplace_back("underlying_reference", underlying_reference.to_canonical());
  members.emplace_back("calendar_ref", calendar_ref.to_canonical());
  members.emplace_back("business_day_convention", business_day_convention.to_canonical());
  members.emplace_back("timezone", timezone.to_canonical());
  members.emplace_back("expiry_time", expiry_time.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kExerciseTermsV1))));
  return CanonicalValue::make_object(std::move(members));
}

ExerciseTerms ExerciseTerms::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_schema_version(node, SchemaVersion::kExerciseTermsV1, pointer);
  const std::vector<std::string_view> allowed_keys(std::begin(kExerciseKeys),
                                                  std::end(kExerciseKeys));
  require_only_keys(node, pointer, allowed_keys);
  ExerciseTerms result;
  result.exercise_terms_id = require_text(node.at("exercise_terms_id", pointer),
                                          child(pointer, "exercise_terms_id"),
                                          "string exercise terms id");
  result.exercise_style = enum_from_canonical<ExerciseStyleV1>(node.at("exercise_style", pointer),
                                                               child(pointer, "exercise_style"));
  result.exercise_dates = date_list_from_canonical(node.at("exercise_dates", pointer),
                                                   child(pointer, "exercise_dates"));
  result.notice_dates = ValueOrReason<std::vector<Date>>::from_canonical(
      node.at("notice_dates", pointer), child(pointer, "notice_dates"));
  result.underlying_reference = UnderlyingReference::from_canonical(
      node.at("underlying_reference", pointer), child(pointer, "underlying_reference"));
  result.calendar_ref = ValueOrReason<std::string>::from_canonical(node.at("calendar_ref", pointer),
                                                                  child(pointer, "calendar_ref"));
  result.business_day_convention = ValueOrReason<std::string>::from_canonical(
      node.at("business_day_convention", pointer), child(pointer, "business_day_convention"));
  result.timezone = ValueOrReason<std::string>::from_canonical(node.at("timezone", pointer),
                                                               child(pointer, "timezone"));
  result.expiry_time = ValueOrReason<std::string>::from_canonical(node.at("expiry_time", pointer),
                                                                  child(pointer, "expiry_time"));
  result.content_fingerprint = require_fingerprint_node(node.at("content_fingerprint", pointer),
                                                        child(pointer, "content_fingerprint"));

  // Section 10.2: EUROPEAN requires exactly one exercise date; BERMUDAN requires 1..n sorted
  // ascending with no duplicates.
  if (result.exercise_style == ExerciseStyleV1::kEuropean) {
    if (result.exercise_dates.size() != 1) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "exercise_dates"),
           "EUROPEAN exercise requires exactly one exercise date");
    }
  } else {
    if (result.exercise_dates.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "exercise_dates"),
           "BERMUDAN exercise requires at least one exercise date");
    }
    for (std::size_t index = 1; index < result.exercise_dates.size(); ++index) {
      if (!(result.exercise_dates[index - 1].text < result.exercise_dates[index].text)) {
        fail(ContractViolationKind::kInvariantViolation,
             pointer_index(child(pointer, "exercise_dates"), index),
             "BERMUDAN exercise dates must be strictly ascending with no duplicates");
      }
    }
  }
  verify_exercise_terms_identities(result, pointer);
  return result;
}

CanonicalValue exercise_terms_identity_preimage(const ExerciseTerms& terms) {
  return strip_key(strip_key(terms.to_canonical(), "exercise_terms_id"), "content_fingerprint");
}

CanonicalValue exercise_terms_fingerprint_preimage(const ExerciseTerms& terms,
                                                   const std::string& terms_id) {
  ExerciseTerms copy = terms;
  copy.exercise_terms_id = terms_id;
  return strip_key(copy.to_canonical(), "content_fingerprint");
}

std::string exercise_terms_content_fingerprint(const ExerciseTerms& terms) {
  const std::string id = fingerprint_of(exercise_terms_identity_preimage(terms));
  return fingerprint_of(exercise_terms_fingerprint_preimage(terms, id));
}

void verify_exercise_terms_identities(const ExerciseTerms& terms, const std::string& pointer) {
  const std::string expected_id = fingerprint_of(exercise_terms_identity_preimage(terms));
  if (terms.exercise_terms_id != expected_id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "exercise_terms_id"),
         "declared exercise_terms_id does not match the recomputed identity");
  }
  if (terms.content_fingerprint !=
      fingerprint_of(exercise_terms_fingerprint_preimage(terms, expected_id))) {
    fail(ContractViolationKind::kFingerprintMismatch, child(pointer, "content_fingerprint"),
         "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(ExerciseTerms& terms) {
  terms.exercise_terms_id = fingerprint_of(exercise_terms_identity_preimage(terms));
  terms.content_fingerprint =
      fingerprint_of(exercise_terms_fingerprint_preimage(terms, terms.exercise_terms_id));
}

// ---------------------------------------------------------------------------------------------
// SettlementTerms
// ---------------------------------------------------------------------------------------------
CanonicalValue CashSettlementMethodology::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("methodology_id", CanonicalValue(methodology_id));
  members.emplace_back("methodology_version", CanonicalValue(methodology_version));
  members.emplace_back("settlement_rate_source", CanonicalValue(settlement_rate_source));
  members.emplace_back("settlement_date_rule", CanonicalValue(settlement_date_rule));
  return CanonicalValue::make_object(std::move(members));
}

CashSettlementMethodology CashSettlementMethodology::from_canonical(const CanonicalValue& node,
                                                                    const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"methodology_id", "methodology_version", "settlement_rate_source",
                     "settlement_date_rule"});
  CashSettlementMethodology result;
  result.methodology_id = require_methodology_text(node.at("methodology_id", pointer),
                                                   child(pointer, "methodology_id"),
                                                   "string methodology id");
  result.methodology_version = require_methodology_text(node.at("methodology_version", pointer),
                                                        child(pointer, "methodology_version"),
                                                        "string methodology version");
  result.settlement_rate_source = require_methodology_text(node.at("settlement_rate_source", pointer),
                                                           child(pointer, "settlement_rate_source"),
                                                           "string settlement rate source");
  result.settlement_date_rule = require_methodology_text(node.at("settlement_date_rule", pointer),
                                                         child(pointer, "settlement_date_rule"),
                                                         "string settlement date rule");
  return result;
}

CanonicalValue SettlementProvenance::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("source", enum_to_canonical(source));
  CanonicalItems refs;
  refs.reserve(evidence_refs.size());
  for (const std::string& ref : evidence_refs) {
    refs.emplace_back(CanonicalValue(ref));
  }
  members.emplace_back("evidence_refs", CanonicalValue::make_array(std::move(refs)));
  members.emplace_back("methodology_id", CanonicalValue(methodology_id));
  members.emplace_back("methodology_version", CanonicalValue(methodology_version));
  return CanonicalValue::make_object(std::move(members));
}

SettlementProvenance SettlementProvenance::from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer) {
  require_only_keys(node, pointer, {"source", "evidence_refs", "methodology_id",
                                    "methodology_version"});
  SettlementProvenance result;
  result.source = enum_from_canonical<MarketSource>(node.at("source", pointer),
                                                   child(pointer, "source"));
  const CanonicalValue& refs_node = node.at("evidence_refs", pointer);
  if (!refs_node.is_array()) {
    fail_wrong_type(child(pointer, "evidence_refs"), "array", refs_node.type_name());
  }
  for (const CanonicalValue& item : refs_node.as_array().items) {
    if (!item.is_string()) {
      fail_wrong_type(child(pointer, "evidence_refs"), "string", item.type_name());
    }
    result.evidence_refs.push_back(item.as_string());
  }
  result.methodology_id = require_methodology_text(node.at("methodology_id", pointer),
                                                   child(pointer, "methodology_id"),
                                                   "string methodology id");
  result.methodology_version = require_methodology_text(node.at("methodology_version", pointer),
                                                        child(pointer, "methodology_version"),
                                                        "string methodology version");
  return result;
}

CanonicalValue SettlementTerms::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("settlement_terms_id", CanonicalValue(settlement_terms_id));
  members.emplace_back("settlement_type", enum_to_canonical(settlement_type));
  members.emplace_back("settlement_method", settlement_method.to_canonical());
  members.emplace_back("settlement_method_version", settlement_method_version.to_canonical());
  members.emplace_back("cash_settlement_methodology",
                       cash_settlement_methodology.to_canonical());
  members.emplace_back("settlement_date", settlement_date.to_canonical());
  members.emplace_back("settlement_currency", enum_to_canonical(settlement_currency));
  members.emplace_back("source_methodology_provenance",
                       source_methodology_provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kSettlementTermsV1))));
  return CanonicalValue::make_object(std::move(members));
}

SettlementTerms SettlementTerms::from_canonical(const CanonicalValue& node,
                                                const std::string& pointer) {
  require_schema_version(node, SchemaVersion::kSettlementTermsV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "settlement_terms_id", "settlement_type",
                     "settlement_method", "settlement_method_version",
                     "cash_settlement_methodology", "settlement_date", "settlement_currency",
                     "source_methodology_provenance", "content_fingerprint"});
  SettlementTerms result;
  result.settlement_terms_id = require_text(node.at("settlement_terms_id", pointer),
                                            child(pointer, "settlement_terms_id"),
                                            "string settlement terms id");
  result.settlement_type = enum_from_canonical<SettlementType>(node.at("settlement_type", pointer),
                                                               child(pointer, "settlement_type"));
  result.settlement_method = ValueOrReason<std::string>::from_canonical(
      node.at("settlement_method", pointer), child(pointer, "settlement_method"));
  result.settlement_method_version = ValueOrReason<std::string>::from_canonical(
      node.at("settlement_method_version", pointer), child(pointer, "settlement_method_version"));
  result.cash_settlement_methodology = ValueOrReason<CashSettlementMethodology>::from_canonical(
      node.at("cash_settlement_methodology", pointer), child(pointer, "cash_settlement_methodology"));
  result.settlement_date = ValueOrReason<Date>::from_canonical(node.at("settlement_date", pointer),
                                                               child(pointer, "settlement_date"));
  result.settlement_currency = enum_from_canonical<Currency>(node.at("settlement_currency", pointer),
                                                             child(pointer, "settlement_currency"));
  result.source_methodology_provenance = SettlementProvenance::from_canonical(
      node.at("source_methodology_provenance", pointer), child(pointer, "source_methodology_provenance"));
  result.content_fingerprint = require_fingerprint_node(node.at("content_fingerprint", pointer),
                                                        child(pointer, "content_fingerprint"));

  // Section 11.2: CASH requires PRESENT method/version/methodology; PHYSICAL requires all three to
  // be structured NOT_APPLICABLE. A physical settlement must not carry a cash-only method.
  if (result.settlement_type == SettlementType::kCash) {
    if (!result.settlement_method.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "settlement_method"),
           "CASH settlement requires a PRESENT settlement method");
    }
    if (!result.settlement_method_version.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState,
           child(pointer, "settlement_method_version"),
           "CASH settlement requires a PRESENT settlement method version");
    }
    if (!result.cash_settlement_methodology.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState,
           child(pointer, "cash_settlement_methodology"),
           "CASH settlement requires a PRESENT cash settlement methodology");
    }
  } else {
    result.settlement_method.require_null_with_reason(child(pointer, "settlement_method"),
                                                     ReasonCategory::kNotApplicable);
    result.settlement_method_version.require_null_with_reason(
        child(pointer, "settlement_method_version"), ReasonCategory::kNotApplicable);
    result.cash_settlement_methodology.require_null_with_reason(
        child(pointer, "cash_settlement_methodology"), ReasonCategory::kNotApplicable);
  }
  verify_settlement_terms_identities(result, pointer);
  return result;
}

CanonicalValue settlement_terms_identity_preimage(const SettlementTerms& terms) {
  return strip_key(strip_key(terms.to_canonical(), "settlement_terms_id"), "content_fingerprint");
}

CanonicalValue settlement_terms_fingerprint_preimage(const SettlementTerms& terms,
                                                     const std::string& terms_id) {
  SettlementTerms copy = terms;
  copy.settlement_terms_id = terms_id;
  return strip_key(copy.to_canonical(), "content_fingerprint");
}

std::string settlement_terms_content_fingerprint(const SettlementTerms& terms) {
  const std::string id = fingerprint_of(settlement_terms_identity_preimage(terms));
  return fingerprint_of(settlement_terms_fingerprint_preimage(terms, id));
}

void verify_settlement_terms_identities(const SettlementTerms& terms, const std::string& pointer) {
  const std::string expected_id = fingerprint_of(settlement_terms_identity_preimage(terms));
  if (terms.settlement_terms_id != expected_id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "settlement_terms_id"),
         "declared settlement_terms_id does not match the recomputed identity");
  }
  if (terms.content_fingerprint !=
      fingerprint_of(settlement_terms_fingerprint_preimage(terms, expected_id))) {
    fail(ContractViolationKind::kFingerprintMismatch, child(pointer, "content_fingerprint"),
         "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(SettlementTerms& terms) {
  terms.settlement_terms_id = fingerprint_of(settlement_terms_identity_preimage(terms));
  terms.content_fingerprint =
      fingerprint_of(settlement_terms_fingerprint_preimage(terms, terms.settlement_terms_id));
}

// ---------------------------------------------------------------------------------------------
// ModelInput (STRUCTURAL PLACEHOLDER envelope)
// ---------------------------------------------------------------------------------------------
CanonicalValue ModelInput::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("model_input_id", CanonicalValue(model_input_id));
  members.emplace_back("model_id", enum_to_canonical(model_id));
  members.emplace_back("model_version", CanonicalValue(model_version));
  members.emplace_back("payload", payload);
  members.emplace_back("provenance", provenance.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kModelInputV1))));
  return CanonicalValue::make_object(std::move(members));
}

ModelInput ModelInput::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  require_schema_version(node, SchemaVersion::kModelInputV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "model_input_id", "model_id", "model_version", "payload",
                     "provenance", "content_fingerprint"});
  ModelInput result;
  result.model_input_id = require_text(node.at("model_input_id", pointer),
                                       child(pointer, "model_input_id"),
                                       "string model input id");
  result.model_id = enum_from_canonical<ModelId>(node.at("model_id", pointer),
                                                child(pointer, "model_id"));
  result.model_version = require_methodology_text(node.at("model_version", pointer),
                                                  child(pointer, "model_version"),
                                                  "string model version");
  const CanonicalValue& payload_node = node.at("payload", pointer);
  if (!payload_node.is_object()) {
    fail_wrong_type(child(pointer, "payload"), "object", payload_node.type_name());
  }
  result.payload = payload_node;
  result.provenance = SourceProvenance::from_canonical(node.at("provenance", pointer),
                                                       child(pointer, "provenance"));
  result.content_fingerprint = require_fingerprint_node(node.at("content_fingerprint", pointer),
                                                        child(pointer, "content_fingerprint"));
  verify_model_input_identities(result, pointer);
  return result;
}

CanonicalValue model_input_identity_preimage(const ModelInput& input) {
  return strip_key(strip_key(input.to_canonical(), "model_input_id"), "content_fingerprint");
}

CanonicalValue model_input_fingerprint_preimage(const ModelInput& input,
                                                const std::string& input_id) {
  ModelInput copy = input;
  copy.model_input_id = input_id;
  return strip_key(copy.to_canonical(), "content_fingerprint");
}

std::string model_input_content_fingerprint(const ModelInput& input) {
  const std::string id = fingerprint_of(model_input_identity_preimage(input));
  return fingerprint_of(model_input_fingerprint_preimage(input, id));
}

void verify_model_input_identities(const ModelInput& input, const std::string& pointer) {
  const std::string expected_id = fingerprint_of(model_input_identity_preimage(input));
  if (input.model_input_id != expected_id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "model_input_id"),
         "declared model_input_id does not match the recomputed identity");
  }
  if (input.content_fingerprint !=
      fingerprint_of(model_input_fingerprint_preimage(input, expected_id))) {
    fail(ContractViolationKind::kFingerprintMismatch, child(pointer, "content_fingerprint"),
         "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(ModelInput& input) {
  input.model_input_id = fingerprint_of(model_input_identity_preimage(input));
  input.content_fingerprint =
      fingerprint_of(model_input_fingerprint_preimage(input, input.model_input_id));
}

// ---------------------------------------------------------------------------------------------
// ValuationContext / ValuationProduct / CurveSelection
// ---------------------------------------------------------------------------------------------
CanonicalValue ValuationContext::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("valuation_context_id", CanonicalValue(valuation_context_id));
  members.emplace_back("valuation_date", valuation_date.to_canonical());
  members.emplace_back("reporting_currency", enum_to_canonical(reporting_currency));
  return CanonicalValue::make_object(std::move(members));
}

ValuationContext ValuationContext::from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  require_only_keys(node, pointer,
                    {"valuation_context_id", "valuation_date", "reporting_currency"});
  ValuationContext result;
  result.valuation_context_id = require_text(node.at("valuation_context_id", pointer),
                                             child(pointer, "valuation_context_id"),
                                             "string valuation context id");
  result.valuation_date = Date::from_canonical(node.at("valuation_date", pointer),
                                              child(pointer, "valuation_date"));
  result.reporting_currency = enum_from_canonical<Currency>(node.at("reporting_currency", pointer),
                                                            child(pointer, "reporting_currency"));
  const std::string expected = compute_valuation_context_id(result);
  if (result.valuation_context_id != expected) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "valuation_context_id"),
         "valuation_context_id must be the digest of its exact V1 preimage "
         "{valuation_date, reporting_currency}");
  }
  return result;
}

std::string compute_valuation_context_id(const ValuationContext& context) {
  // #225 section 5.1: the preimage is EXACTLY {valuation_date, reporting_currency}, excluding the id.
  CanonicalMembers members;
  members.emplace_back("reporting_currency", enum_to_canonical(context.reporting_currency));
  members.emplace_back("valuation_date", context.valuation_date.to_canonical());
  return fingerprint_of(CanonicalValue::make_object(std::move(members)));
}

CanonicalValue ValuationProduct::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("product_id", CanonicalValue(product_id));
  members.emplace_back("product_type", enum_to_canonical(product_type));
  members.emplace_back("underlying_product_id", underlying_product_id.to_canonical());
  return CanonicalValue::make_object(std::move(members));
}

ValuationProduct ValuationProduct::from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  require_only_keys(node, pointer, {"product_id", "product_type", "underlying_product_id"});
  ValuationProduct result;
  result.product_id = require_text(node.at("product_id", pointer), child(pointer, "product_id"),
                                   "string product id");
  // CALLABLE_SWAP / RANGE_ACCRUAL and every other token are refused here as unknown V1 vocabulary.
  result.product_type = enum_from_canonical<ProductType>(node.at("product_type", pointer),
                                                         child(pointer, "product_type"));
  result.underlying_product_id = ValueOrReason<std::string>::from_canonical(
      node.at("underlying_product_id", pointer), child(pointer, "underlying_product_id"));
  return result;
}

CanonicalValue CurveSelection::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("discount_curve_id", CanonicalValue(discount_curve_id));
  members.emplace_back("forecast_curve_by_index",
                       DtoCodec<std::map<std::string, std::string>>::encode(forecast_curve_by_index));
  return CanonicalValue::make_object(std::move(members));
}

CurveSelection CurveSelection::from_canonical(const CanonicalValue& node,
                                              const std::string& pointer) {
  require_only_keys(node, pointer, {"discount_curve_id", "forecast_curve_by_index"});
  CurveSelection result;
  result.discount_curve_id = require_text(node.at("discount_curve_id", pointer),
                                          child(pointer, "discount_curve_id"),
                                          "string discount curve id");
  const CanonicalValue& forecast_node = node.at("forecast_curve_by_index", pointer);
  if (!forecast_node.is_object()) {
    fail_wrong_type(child(pointer, "forecast_curve_by_index"), "object", forecast_node.type_name());
  }
  for (const auto& member : forecast_node.as_object().members) {
    // A free-text key would silently create a second index vocabulary.
    (void)enum_from_token<FloatingIndex>(member.first,
                                        pointer_child(child(pointer, "forecast_curve_by_index"),
                                                      member.first));
    if (!member.second.is_string() || member.second.as_string().empty()) {
      fail_wrong_type(pointer_child(child(pointer, "forecast_curve_by_index"), member.first),
                      "non-empty curve id string", member.second.type_name());
    }
    result.forecast_curve_by_index.emplace(member.first, member.second.as_string());
  }
  return result;
}

// ---------------------------------------------------------------------------------------------
// RatesKernelInput
// ---------------------------------------------------------------------------------------------
CanonicalValue RatesKernelInput::to_canonical() const {
  CanonicalMembers members;
  members.emplace_back("valuation_product", valuation_product.to_canonical());
  members.emplace_back("resolved_swap", resolved_swap.to_canonical());
  members.emplace_back("market_snapshot", market_snapshot.to_canonical());
  members.emplace_back("curve_selection", curve_selection.to_canonical());
  members.emplace_back("exercise_terms", exercise_terms.to_canonical());
  members.emplace_back("settlement_terms", settlement_terms.to_canonical());
  members.emplace_back("model_input", model_input.to_canonical());
  members.emplace_back("valuation_context", valuation_context.to_canonical());
  members.emplace_back("content_fingerprint", CanonicalValue(content_fingerprint));
  members.emplace_back("schema_version",
                       CanonicalValue(std::string(to_token(SchemaVersion::kRatesKernelInputV1))));
  return CanonicalValue::make_object(std::move(members));
}

RatesKernelInput RatesKernelInput::from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer) {
  require_schema_version(node, SchemaVersion::kRatesKernelInputV1, pointer);
  require_only_keys(node, pointer,
                    {"schema_version", "valuation_product", "resolved_swap", "market_snapshot",
                     "curve_selection", "exercise_terms", "settlement_terms", "model_input",
                     "valuation_context", "content_fingerprint"});
  RatesKernelInput result;
  result.valuation_product = ValuationProduct::from_canonical(node.at("valuation_product", pointer),
                                                              child(pointer, "valuation_product"));
  result.resolved_swap = ResolvedSwap::from_canonical(node.at("resolved_swap", pointer),
                                                      child(pointer, "resolved_swap"));
  result.market_snapshot = MarketSnapshot::from_canonical(node.at("market_snapshot", pointer),
                                                         child(pointer, "market_snapshot"));
  result.curve_selection = CurveSelection::from_canonical(node.at("curve_selection", pointer),
                                                         child(pointer, "curve_selection"));
  result.exercise_terms = ValueOrReason<ExerciseTerms>::from_canonical(
      node.at("exercise_terms", pointer), child(pointer, "exercise_terms"));
  result.settlement_terms = ValueOrReason<SettlementTerms>::from_canonical(
      node.at("settlement_terms", pointer), child(pointer, "settlement_terms"));
  result.model_input = ValueOrReason<ModelInput>::from_canonical(node.at("model_input", pointer),
                                                                child(pointer, "model_input"));
  result.valuation_context = ValuationContext::from_canonical(node.at("valuation_context", pointer),
                                                              child(pointer, "valuation_context"));
  result.content_fingerprint = require_fingerprint_node(node.at("content_fingerprint", pointer),
                                                        child(pointer, "content_fingerprint"));
  result.validate_invariants(pointer);
  verify_kernel_input_identities(result, pointer);
  return result;
}

void RatesKernelInput::validate_invariants(const std::string& pointer) const {
  const Currency trade_currency = resolved_swap.currency;

  // Section 5.1: valuation_date agreement.
  if (valuation_context.valuation_date.text != market_snapshot.valuation_date.text) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "valuation_context"),
         "valuation_context.valuation_date must equal market_snapshot.valuation_date");
  }
  // Section 5.1: single-currency V1. The kernel never relabels or converts an amount and never
  // invents FX, so every currency-bearing input must agree with the trade currency.
  if (valuation_context.reporting_currency != trade_currency) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "valuation_context"),
         "reporting_currency must equal resolved_swap.currency in V1 (no FX contract exists)");
  }
  if (market_snapshot.curve_set.base_currency != trade_currency) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "market_snapshot"),
         "curve_set.base_currency must equal resolved_swap.currency in V1");
  }
  if (settlement_terms.present && settlement_terms.value.settlement_currency != trade_currency) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "settlement_terms"),
         "settlement_currency must equal resolved_swap.currency in V1");
  }
  const std::string curve_set_pointer = child(child(pointer, "market_snapshot"), "curve_set");
  const std::string curves_pointer = child(curve_set_pointer, "curves");
  for (std::size_t index = 0; index < market_snapshot.curve_set.curves.size(); ++index) {
    if (market_snapshot.curve_set.curves[index].common().currency != trade_currency) {
      fail(ContractViolationKind::kInvariantViolation, pointer_index(curves_pointer, index),
           "every curve currency must equal resolved_swap.currency in V1");
    }
  }

  // Section 5.1: curve_selection must resolve inside the embedded curve set, and each role must
  // permit its use. The kernel must not choose the first eligible curve or infer from array order.
  const auto find_curve = [this](const std::string& curve_id) -> const CurveCommon* {
    for (const Curve& curve : market_snapshot.curve_set.curves) {
      if (curve.common().curve_id == curve_id) {
        return &curve.common();
      }
    }
    return nullptr;
  };
  const CurveCommon* discount = find_curve(curve_selection.discount_curve_id);
  if (discount == nullptr) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "curve_selection"),
         "discount_curve_id must resolve to an embedded curve in market_snapshot.curve_set");
  }
  if (discount->curve_role != CurveRole::kDiscount &&
      discount->curve_role != CurveRole::kDiscountAndForecastReferenceOnly) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "curve_selection"),
         "discount_curve_id must resolve to a curve whose role permits discounting");
  }
  for (const auto& entry : curve_selection.forecast_curve_by_index) {
    const CurveCommon* forecast = find_curve(entry.second);
    if (forecast == nullptr) {
      fail(ContractViolationKind::kIdentityMismatch, child(pointer, "curve_selection"),
           "every forecast_curve_by_index target must resolve to an embedded curve");
    }
    if (forecast->curve_role != CurveRole::kForecast &&
        forecast->curve_role != CurveRole::kDiscountAndForecastReferenceOnly) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "curve_selection"),
           "a forecast target must resolve to a curve whose role permits forecasting");
    }
    if (!forecast->index_id.present || enum_to_token(forecast->index_id.value) != entry.first) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "curve_selection"),
           "a forecast target's PRESENT index_id must exactly equal the map key");
    }
  }

  // Section 5.1: exact product applicability. Applicability is structural, never a market default.
  const bool is_ois = valuation_product.product_type == ProductType::kOis;
  if (is_ois) {
    if (valuation_product.product_id != resolved_swap.product_id) {
      fail(ContractViolationKind::kIdentityMismatch, child(pointer, "valuation_product"),
           "for a vanilla OIS the product id must equal resolved_swap.product_id");
    }
    valuation_product.underlying_product_id.require_null_with_reason(
        child(child(pointer, "valuation_product"), "underlying_product_id"),
        ReasonCategory::kNotApplicable);
    exercise_terms.require_null_with_reason(child(pointer, "exercise_terms"),
                                           ReasonCategory::kNotApplicable);
    settlement_terms.require_null_with_reason(child(pointer, "settlement_terms"),
                                              ReasonCategory::kNotApplicable);
    model_input.require_null_with_reason(child(pointer, "model_input"),
                                        ReasonCategory::kNotApplicable);
    market_snapshot.volatility_input.require_null_with_reason(
        child(child(pointer, "market_snapshot"), "volatility_input"), ReasonCategory::kNotApplicable);
  } else {
    if (!valuation_product.underlying_product_id.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState,
           child(child(pointer, "valuation_product"), "underlying_product_id"),
           "a derivative requires a PRESENT underlying product id");
    }
    if (valuation_product.underlying_product_id.value != resolved_swap.product_id) {
      fail(ContractViolationKind::kIdentityMismatch,
           child(child(pointer, "valuation_product"), "underlying_product_id"),
           "underlying_product_id must equal resolved_swap.product_id");
    }
    if (!exercise_terms.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "exercise_terms"),
           "a swaption requires PRESENT exercise terms");
    }
    if (exercise_terms.value.exercise_style != ExerciseStyleV1::kEuropean) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "exercise_terms"),
           "the V1 swaption path requires exercise_style=EUROPEAN (BERMUDAN is reserved, not "
           "enabled by #227)");
    }
    if (!settlement_terms.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, child(pointer, "settlement_terms"),
           "a swaption requires PRESENT settlement terms");
    }
    if (!market_snapshot.volatility_input.present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState,
           child(child(pointer, "market_snapshot"), "volatility_input"),
           "a swaption requires a PRESENT volatility input");
    }
    model_input.require_null_with_reason(child(pointer, "model_input"),
                                        ReasonCategory::kNotApplicable);
    // Section 10.2/5.1: the exercise terms' underlying reference must agree with both the product
    // declaration and the embedded resolved swap.
    if (exercise_terms.value.underlying_reference.underlying_product_id !=
            resolved_swap.product_id ||
        exercise_terms.value.underlying_reference.underlying_product_id !=
            valuation_product.underlying_product_id.value) {
      fail(ContractViolationKind::kIdentityMismatch, child(pointer, "exercise_terms"),
           "exercise_terms.underlying_reference.underlying_product_id must equal "
           "underlying_product_id and resolved_swap.product_id");
    }
  }
}

CanonicalValue kernel_input_fingerprint_preimage(const RatesKernelInput& input) {
  return strip_key(input.to_canonical(), "content_fingerprint");
}

std::string kernel_input_content_fingerprint(const RatesKernelInput& input) {
  return fingerprint_of(kernel_input_fingerprint_preimage(input));
}

void verify_kernel_input_identities(const RatesKernelInput& input, const std::string& pointer) {
  if (kernel_input_content_fingerprint(input) != input.content_fingerprint) {
    fail(ContractViolationKind::kFingerprintMismatch, child(pointer, "content_fingerprint"),
         "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(RatesKernelInput& input) {
  // Innermost first: the kernel-input identity must cover its children's recomputed identities.
  assign_derived_identities(input.market_snapshot);
  assign_derived_identities(input.resolved_swap);
  if (input.exercise_terms.present) {
    assign_derived_identities(input.exercise_terms.value);
  }
  if (input.settlement_terms.present) {
    assign_derived_identities(input.settlement_terms.value);
  }
  if (input.model_input.present) {
    assign_derived_identities(input.model_input.value);
  }
  input.valuation_context.valuation_context_id = compute_valuation_context_id(input.valuation_context);
  input.content_fingerprint = kernel_input_content_fingerprint(input);
}

}  // namespace Shiori::rates::dto
