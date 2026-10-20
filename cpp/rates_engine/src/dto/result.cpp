#include "shiori_rates/dto/result.hpp"

#include <cstdint>
#include <string>
#include <tuple>
#include <utility>

#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/kernel_input.hpp"

namespace Shiori::rates::dto {

namespace {

[[nodiscard]] std::string child(const std::string& pointer, const char* key) {
  return pointer_child(pointer, key);
}

// The same binary64 exactness rule `NumericWithUnit::from_canonical` applies in `numeric.cpp`, applied to
// this file's bare double fields (`headline.value`, `pv/par_rate/annuity_pvbp.value`,
// `components[].value`, `measures[].value`, `bump_spec.bump_size`). A JSON integer is an exact canonical
// number form, so an integer that binary64 cannot hold exactly must be REFUSED at this field's own
// pointer instead of being silently rounded (`9007199254740993` would become `9007199254740992`, and
// every identity recomputed from the rounded value would describe a number the document does not
// contain). Every integer with |v| <= 2^53 is exact; above that a value is exact exactly when it is a
// multiple of the binary64 spacing at its magnitude. Integer-only arithmetic: no double->int
// conversion, so no out-of-range cast near INT64_MIN/INT64_MAX.
[[nodiscard]] double require_binary64_number(const CanonicalValue& node, const std::string& pointer) {
  if (!node.is_integer()) {
    return node.as_double();
  }
  const std::int64_t raw = node.as_integer();
  constexpr std::int64_t kTwoTo53 = 9007199254740992LL;  // 2^53
  if (raw >= -kTwoTo53 && raw <= kTwoTo53) {
    return static_cast<double>(raw);
  }
  const std::uint64_t magnitude = raw < 0 ? static_cast<std::uint64_t>(-(raw + 1)) + 1U
                                          : static_cast<std::uint64_t>(raw);
  unsigned exponent = 0;  // floor(log2(magnitude)); >= 53 because |raw| > 2^53 here
  for (std::uint64_t probe = magnitude; probe > 1U; probe >>= 1U) {
    ++exponent;
  }
  // The binary64 spacing at magnitude 2^e is 2^(e - 52), so the value is exact exactly when its low
  // (e - 52) bits are zero; e is in [53, 63] here, so the shift is always in range.
  const unsigned dropped_bits = exponent - 52U;
  if ((magnitude & ((std::uint64_t{1} << dropped_bits) - 1U)) == 0U) {
    return static_cast<double>(raw);
  }
  fail(ContractViolationKind::kInvariantViolation, pointer,
       "an integer-valued numeric field must be exactly representable as binary64; this integer would "
       "be silently rounded, so the canonical field would not round-trip and every identity derived "
       "from it would describe a different number");
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

// docs/33 section 12.2 (line 908) and section 5.1 (line 315): a result's `valuation_context_id` is an
// EXACT echo of the V1 identity derived from `{valuation_date, reporting_currency}`, and section 5.1
// line 315 fixes that preimage membership. Re-deriving it here - through the DTO layer's one existing
// implementation of that recipe, `compute_valuation_context_id` - is what stops a stale or fabricated
// context id from riding along, including when the caller recomputes the result fingerprint over the
// fabricated content. The recipe itself is never restated in this file.
void require_valuation_context_identity(const std::string& id, const Date& valuation_date,
                                        Currency result_currency, const std::string& pointer) {
  ValuationContext derived;
  derived.valuation_context_id = id;
  derived.valuation_date = valuation_date;
  derived.reporting_currency = result_currency;
  if (id != compute_valuation_context_id(derived)) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "valuation_context_id"),
         "valuation_context_id must be the digest of its exact V1 preimage "
         "{valuation_date, reporting_currency}");
  }
}

// docs/33 section 12.4 (line 962) and section 13.4 (line 1051) make the result-side input identity an
// exact echo of the consumed `RatesKernelInput`: "Input identity also names the exact market/curve/
// fixing and optional vol/exercise/settlement/model/calibration identities ... A result that cannot
// name every PRESENT kernel input is not replayable and fails validation", and "Every optional input
// identity MUST echo the corresponding `RatesKernelInput` applicability state: PRESENT carries the
// exact consumed object's id; NOT_APPLICABLE remains structured `NULL_WITH_REASON(NOT_APPLICABLE)`.
// Omission, bare null, or fabricated ids are malformed." The applicability being echoed is the exact
// Phase-2 table of section 5.1 lines 305-307: an OIS consumes none of the four optional inputs, while a
// SWAPTION consumes the volatility input, the exercise terms and the settlement terms and (on the
// pricing path) carries `model_input` as structured NOT_APPLICABLE. This is a whole-document rule
// because it relates `product_type` to four independent union members; it runs AFTER the per-measure
// coordinate rules of section 13.2, so a measure that references an input the identity does not name is
// still reported at that measure's own coordinate.
//
// The V1 kernel's executable applicability table permits no ModelInput for OIS or Phase-2
// SWAPTION. Although the risk DTO reserves MODEL_PARAMETER for a later kernel schema, no current
// risk result may fabricate its consumption; its model and calibration identities are both N/A.
void require_inputs_identity_applicability(const InputsIdentity& identity, ProductType product_type,
                                           const std::string& pointer) {
  const std::string base = child(pointer, "inputs_identity");
  const bool derivative = product_type == ProductType::kSwaption;
  struct PayloadInput {
    const ValueOrReason<std::string>* field;
    const char* name;
  };
  const PayloadInput payload_inputs[] = {
      {&identity.volatility_input_id, "volatility_input_id"},
      {&identity.exercise_terms_id, "exercise_terms_id"},
      {&identity.settlement_terms_id, "settlement_terms_id"}};
  for (const PayloadInput& input : payload_inputs) {
    const std::string field_pointer = child(base, input.name);
    if (!derivative) {
      (void)input.field->require_null_with_reason(field_pointer, ReasonCategory::kNotApplicable);
      continue;
    }
    if (!input.field->present) {
      fail(ContractViolationKind::kInvalidTaggedUnionState, field_pointer,
           "a SWAPTION result must name every consumed kernel input: this identity is "
           "NULL_WITH_REASON but the Phase-2 swaption shape consumes it");
    }
  }
  const std::string model_pointer = child(base, "model_input_id");
  // Neither OIS nor Phase-2 SWAPTION can consume ModelInput in RatesKernelInput V1.
  // MODEL_PARAMETER remains a reserved DTO wire shape until a later kernel schema enables it.
  (void)identity.model_input_id.require_null_with_reason(model_pointer, ReasonCategory::kNotApplicable);
  // Lines 795/811 attach calibration evidence to a ModelInput (`calibration_ref` is PRESENT iff the
  // embedded calibration result is PRESENT), and section 12.4 line 962 / section 13.4 line 1051 make every
  // optional result identity an exact echo of the consumed kernel input: "PRESENT carries the exact
  // consumed object's id; NOT_APPLICABLE remains structured `NULL_WITH_REASON(NOT_APPLICABLE)`.
  // Omission, bare null, or fabricated ids are malformed." With no ModelInput consumed there is nothing
  // that could have consumed a calibration, so a PRESENT `calibration_result_id` is a fabricated
  // consumed-input claim of exactly that shape and is refused at its own pointer. No calibration
  // objective, optimizer, tolerance or methodology is involved.
  const std::string calibration_pointer = child(base, "calibration_result_id");
  (void)identity.calibration_result_id.require_null_with_reason(calibration_pointer,
                                                               ReasonCategory::kNotApplicable);
}

// docs/33 section 12.4 (line 962) and section 13.4 (line 1051): "A result's `product_id/product_type`
// MUST echo `RatesKernelInput.valuation_product`, and `inputs_identity.resolved_swap_product_id` MUST
// equal the embedded `RatesKernelInput.resolved_swap.product_id` (the same as top-level product_id for a
// vanilla swap; the underlying id for a derivative)". The kernel side already locks the same shape
// (`RatesKernelInput::validate_invariants`: OIS equality, and derivative distinctness from the underlying
// swap), so a result whose two ids contradict it cannot echo any valid consumed request: an accepted
// result is replay-linked to exactly one shape, and section 5.1 line 303 forbids substituting the
// derivative identity with the underlying one. Both lanes share this rule; nothing is renamed.
void require_product_identity_linkage(const std::string& product_id, ProductType product_type,
                                      const InputsIdentity& identity, const std::string& pointer) {
  if (product_type == ProductType::kSwaption) {
    if (product_id == identity.resolved_swap_product_id) {
      fail(ContractViolationKind::kIdentityMismatch, child(pointer, "product_id"),
           "a swaption result's product_id identifies the derivative and must be distinct from "
           "inputs_identity.resolved_swap_product_id, which names the underlying swap; the derivative "
           "identity is never substituted by the underlying id");
    }
    return;
  }
  if (product_id != identity.resolved_swap_product_id) {
    fail(ContractViolationKind::kIdentityMismatch, child(pointer, "product_id"),
         "a vanilla OIS result's product_id must equal inputs_identity.resolved_swap_product_id, the "
         "embedded ResolvedSwap product identity it echoes");
  }
}

// docs/33 section 16 (line 1257): `errors` is the machine-dispatchable domain-code channel, and its
// vocabulary is exactly the eleven `RatesErrorCode` tokens ("Error-code vocabulary (minimum; extension
// requires methodology review)"). A producer can re-hash any content, so the fingerprint fence cannot
// carry this rule: an unapproved token is refused HERE, at the offending `errors/<i>/code`, using the
// DTO layer's one existing token table (`rates_error_code_from_token`). `warnings` is deliberately NOT
// validated: section 16 keeps the warning vocabulary open (for example `FORWARD_STARTING` /
// `DATA_QUALITY`), and `DiagnosticRecord::from_canonical` decodes both lists, so the closed check must
// live at the result level rather than in the record decoder.
void require_closed_error_vocabulary(const std::vector<DiagnosticRecord>& errors,
                                     const std::string& pointer) {
  for (std::size_t index = 0; index < errors.size(); ++index) {
    RatesErrorCode decoded = RatesErrorCode::kEngineError;
    if (!rates_error_code_from_token(errors[index].code, decoded)) {
      fail(ContractViolationKind::kUnknownEnumToken,
           child(pointer_index(child(pointer, "errors"), index), "code"),
           "errors[].code must be one of the closed eleven-token RatesErrorCode vocabulary of "
           "docs/33 section 16; an unapproved machine-dispatchable code is refused");
    }
  }
}

// docs/33 section 16 (lines 1253-1255): "SUCCESS - valued, no warnings" and "SUCCESS_WITH_WARNINGS -
// valued with machine-readable warnings". The warning list is the only thing that distinguishes the two
// valued statuses, so a status that contradicts it is refused at `warnings`. FAILED owns its own error
// rule and gains no warning rule here.
void require_status_warning_rule(PricingStatus status, const std::vector<DiagnosticRecord>& warnings,
                                 const std::string& pointer) {
  if (status == PricingStatus::kSuccess) {
    if (!warnings.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "warnings"),
           "a SUCCESS result must not carry warnings; a valued result with warnings is "
           "SUCCESS_WITH_WARNINGS");
    }
    return;
  }
  if (status == PricingStatus::kSuccessWithWarnings && warnings.empty()) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "warnings"),
         "a SUCCESS_WITH_WARNINGS result must carry at least one machine-readable warning");
  }
}

// docs/33 section 15.4 (line 1232): the canonical measure order is the approved tuple
// `(measure_id, canonical bump_spec.bump_target serialization, canonical RiskBucketCoordinate
// serialization)`, and a structured NOT_APPLICABLE sorts before PRESENT within each of the two
// structured components. Every component is compared through its canonical representation: the
// structured pair by its canonical JSON serialization, and `measure_id` by its canonical enum token.
// That matches the sibling tuple of the same paragraph, whose enum component is defined as "the PRESENT
// canonical enum token" (line 1226), and it is the only implementation-independent reading: the token
// vocabulary is contract-published whereas a declaration order is not, which is why the contract
// publishes that order inline wherever it means declaration order (line 1229, strikes:
// "ATM < YIELD_OFFSET_BP < ABSOLUTE_STRIKE < LOG_MONEYNESS"). The `present ? 1 : 0` components implement
// "NOT_APPLICABLE sorts before PRESENT" explicitly instead of relying on a serialization accident.
using MeasureOrderKey = std::tuple<std::string, int, std::string, int, std::string>;

[[nodiscard]] MeasureOrderKey measure_order_key(const RiskMeasure& measure) {
  return MeasureOrderKey{std::string(enum_to_token(measure.measure_id)),
                         measure.bump_spec.bump_target.present ? 1 : 0,
                         measure.bump_spec.bump_target.to_canonical().dump(),
                         measure.bucket_coordinate.present ? 1 : 0,
                         measure.bucket_coordinate.to_canonical().dump()};
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
  // Sections 12.2/12.4 (lines 926 and 962): `curve_role_map` is the exact echo of
  // `RatesKernelInput.curve_selection` and "result never invents selection". The kernel-side
  // `curve_selection` is mandatory and its `discount_curve_id` must resolve to an embedded discounting
  // curve (section 5.1 line 309), so there is no result shape in which the discount curve is absent: a
  // missing key fails as a missing required field, a bare `null` fails as the wrong type, and an empty
  // string fails as an empty field. The result therefore never carries an invented or omitted discount
  // curve, and no default is substituted.
  result.discount_curve_id = require_text(node.at("discount_curve_id", pointer),
                                          child(pointer, "discount_curve_id"),
                                          "non-empty discount curve id");
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
  // docs/33 section 12.2 line 933 names RESOLVED_SWAP_V1 as this field's value ("# RESOLVED_SWAP_V1
  // etc."), and docs/32 section 2 line 86 fixes RESOLVED_SWAP_V1 as the ResolvedSwap wire version
  // whose unknown values consumers refuse. Recognition of a token is not enough: any other known
  // contract token (for example CURVE_SET_V1) is the version of a DIFFERENT contract and cannot be
  // echoed as the version of the embedded ResolvedSwap.
  if (parsed != SchemaVersion::kResolvedSwapV1) {
    fail(ContractViolationKind::kUnknownSchemaVersion,
         child(pointer, "resolved_swap_schema_version"),
         "inputs_identity.resolved_swap_schema_version must be the supported ResolvedSwap wire "
         "version RESOLVED_SWAP_V1; a different known contract token names a different contract");
  }
  result.model_input_id = ValueOrReason<std::string>::from_canonical(
      node.at("model_input_id", pointer), child(pointer, "model_input_id"));
  result.calibration_result_id = ValueOrReason<std::string>::from_canonical(
      node.at("calibration_result_id", pointer), child(pointer, "calibration_result_id"));
  // Section 12.4 / 13.4: PRESENT optional identities must name an actual consumed input.
  // A PRESENT empty string is not an identity, even if the result is independently re-hashed.
  // This rule belongs only to input-id fields, not to every generic ValueOrReason<string>.
  struct OptionalId {
    const ValueOrReason<std::string>* field;
    const char* name;
  };
  const OptionalId ids[] = {
      {&result.volatility_input_id, "volatility_input_id"},
      {&result.exercise_terms_id, "exercise_terms_id"},
      {&result.settlement_terms_id, "settlement_terms_id"},
      {&result.model_input_id, "model_input_id"},
      {&result.calibration_result_id, "calibration_result_id"},
  };
  for (const OptionalId& id : ids) {
    if (id.field->present && id.field->value.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(child(pointer, id.name), "value"),
           "a PRESENT input identity must be a nonempty string");
    }
  }
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
  result.value = require_binary64_number(value_node, child(pointer, "value"));
  result.unit = enum_from_canonical<Unit>(node.at("unit", pointer), child(pointer, "unit"));
  result.currency = ValueOrReason<Currency>::from_canonical(node.at("currency", pointer),
                                                           child(pointer, "currency"));
  result.sign_convention = ValueOrReason<std::string>::from_canonical(
      node.at("sign_convention", pointer), child(pointer, "sign_convention"));
  // Section 12.3 (line 955) states currency applicability in both directions, and line 947 annotates
  // the field the same way: currency-denominated headline units require PRESENT(result_currency), while
  // dimensionless/rate headlines use structured NOT_APPLICABLE unless their approved unit explicitly
  // includes currency. In the enum only CURRENCY_AMOUNT and CURRENCY_AMOUNT_PER_BASIS_POINT are
  // currency-denominated, so every other unit must carry the structured NOT_APPLICABLE state and a
  // PRESENT currency there is contradictory metadata on the authoritative headline. This is the
  // symmetric half of the existing check; the enclosing result-level comparison of the PRESENT currency
  // against `result_currency` is unchanged.
  const bool currency_denominated =
      result.unit == Unit::kCurrencyAmount || result.unit == Unit::kCurrencyAmountPerBasisPoint;
  if (currency_denominated && !result.currency.present) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "currency"),
         "a currency-denominated headline unit requires PRESENT currency");
  }
  if (!currency_denominated && result.currency.present) {
    fail(ContractViolationKind::kInvariantViolation, child(pointer, "currency"),
         "a non-currency headline unit requires structured NOT_APPLICABLE currency");
  }
  if (!currency_denominated) {
    // Line 955 names the exact state ("dimensionless/rate headlines use structured NOT_APPLICABLE"), so
    // the category is part of the requirement rather than an implementation detail: a generic
    // UNAVAILABLE / MISSING_MARKET_DATA / UNRESOLVED_METHODOLOGY / FAILED_CAPTURE reason is not the
    // NOT_APPLICABLE applicability state and would misreport a deliberately inapplicable currency as a
    // data or methodology failure. Only this field's category is constrained here; no other reason
    // field is broadened.
    (void)result.currency.require_null_with_reason(child(pointer, "currency"),
                                                  ReasonCategory::kNotApplicable);
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
  result.value = require_binary64_number(value_node, child(pointer, "value"));
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
  // Section 12.2 / section 5.1: the context id is re-derived from its exact V1 preimage rather than
  // trusted, so a fabricated or stale id is refused even though its type is a non-empty string.
  require_valuation_context_identity(valuation_context_id, valuation_date, result_currency,
                                     pointer);
  // Section 16: the error channel is closed and the status/warning pair must be coherent. Both are
  // whole-document rules, so they are enforced here rather than in `DiagnosticRecord::from_canonical`,
  // which also serves the open warning vocabulary.
  require_closed_error_vocabulary(errors, pointer);
  require_status_warning_rule(status, warnings, pointer);
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
    // Section 12.3 line 953 requires the EXACT pair NULL_WITH_REASON(category=UNAVAILABLE,
    // code=PRICING_FAILED): "never bare null, omission, or zero". The category alone is not the
    // contract state, so another code in the same category (for example ENGINE_ERROR) is refused
    // rather than accepted as an equivalent failure. The code string is taken verbatim from the
    // contract; no new reason code is introduced or renamed.
    if (headline.reason.code != "PRICING_FAILED") {
      fail(ContractViolationKind::kInvalidTaggedUnionState,
           child(child(pointer, "headline"), "reason"),
           "a FAILED result requires the exact reason code PRICING_FAILED under category "
           "UNAVAILABLE");
    }
    if (errors.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "errors"),
           "a FAILED result must carry at least one error");
    }
    // Section 16: FAILED is not valued; supplemental fields may not leak stale priced results.
    if (pv.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "pv"),
           "a FAILED result must not carry a PRESENT pv");
    }
    if (pv_sign_convention.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "pv_sign_convention"),
           "a FAILED result has no pv whose sign convention can be asserted");
    }
    if (component_pvs.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "component_pvs"),
           "a FAILED result must not carry PRESENT component_pvs");
    }
    if (annuity_pvbp.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "annuity_pvbp"),
           "a FAILED result must not carry a PRESENT annuity_pvbp");
    }
    if (par_rate.present) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "par_rate"),
           "a FAILED result must not carry a PRESENT par_rate");
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
  // Section 12.3 line 955: currency-denominated headline units require
  // `headline.currency=PRESENT(result_currency)`. Presence alone is enforced where the headline is
  // decoded; equality with the enclosing `result_currency` is an enclosing-object rule and is
  // enforced here, so a headline may not silently disagree with the currency of the result that
  // carries it.
  if (headline.present) {
    const HeadlineValue& headline_value = headline.value;
    const bool currency_denominated = headline_value.unit == Unit::kCurrencyAmount ||
                                      headline_value.unit == Unit::kCurrencyAmountPerBasisPoint;
    if (currency_denominated && headline_value.currency.present &&
        headline_value.currency.value != result_currency) {
      fail(ContractViolationKind::kIdentityMismatch,
           child(child(child(pointer, "headline"), "value"), "currency"),
           "a currency-denominated headline must carry the enclosing result_currency");
    }
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
      // Section 12.3 line 956: the designated metric must agree with the headline
      // "currency/sign semantics where applicable", and disagreement fails closed. When the headline
      // designates the supplemental PV, the two sign conventions must be exactly equal - both the
      // ValueOrReason states (including a NULL_WITH_REASON reason) and the PRESENT token. This is pure
      // identity of the carried tokens: no sign convention is defined, ranked or corrected here, which
      // is why the rule is stated on the PV's own sign-convention member.
      if (value.semantics == HeadlineSemantics::kPresentValue &&
          !(value.sign_convention == pv_sign_convention)) {
        fail(ContractViolationKind::kIdentityMismatch, child(pointer, "pv_sign_convention"),
             "the headline designates the supplemental PV, so pv_sign_convention must carry exactly "
             "the headline sign_convention union state and PRESENT token");
      }
    }
  }
  // Section 12.4 (line 962): the result's input identity must echo the consumed kernel input's
  // applicability. A pricing result carries no measure and therefore consumes no ModelInput, so its
  // `model_input_id` must be the structured NOT_APPLICABLE state (section 5.1 line 307's Phase-2 path).
  require_inputs_identity_applicability(inputs_identity, product_type, pointer);
  // Section 12.4 (line 962): the top-level product identity and the identity echo must describe the same
  // consumed request - equal for a vanilla OIS, a distinct derivative id over the underlying swap for a
  // swaption.
  require_product_identity_linkage(product_id, product_type, inputs_identity, pointer);
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
  result.bump_size = require_binary64_number(size_node, child(pointer, "bump_size"));
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
  result.value = require_binary64_number(value_node, child(pointer, "value"));
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
  // Section 13.2 / section 5.1: the context id is re-derived from its exact V1 preimage rather than
  // trusted, so a fabricated or stale id is refused even though its type is a non-empty string.
  require_valuation_context_identity(valuation_context_id, valuation_date, result_currency,
                                     pointer);
  // Section 16, the same two rules as PricingResult: the closed error vocabulary and the
  // status/warning coherence.
  require_closed_error_vocabulary(errors, pointer);
  require_status_warning_rule(status, warnings, pointer);
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
  } else {
    if (errors.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "errors"),
           "a FAILED risk result must carry at least one error");
    }
    if (!measures.empty()) {
      fail(ContractViolationKind::kInvariantViolation, child(pointer, "measures"),
           "a FAILED risk result is not valued and must not carry measures");
    }
  }
  // Section 15.4 line 1232: `measures` is a fingerprint-relevant array with a schema-defined
  // canonical order, so document order is never hashed as if it were canonical - two equivalent
  // results written in different orders would otherwise replay to different fingerprints. The array
  // must be STRICTLY ascending by the approved tuple and exact duplicate keys are refused; the array
  // is refused rather than sorted, because reordering a result is the caller's contract, not the
  // decoder's silent repair.
  for (std::size_t index = 1; index < measures.size(); ++index) {
    const MeasureOrderKey previous = measure_order_key(measures[index - 1]);
    const MeasureOrderKey current = measure_order_key(measures[index]);
    const std::string offending = pointer_index(child(pointer, "measures"), index);
    if (previous == current) {
      fail(ContractViolationKind::kInvariantViolation, offending,
           "a duplicate exact (measure_id, bump_spec.bump_target, bucket_coordinate) measure key "
           "is forbidden");
    }
    if (!(previous < current)) {
      fail(ContractViolationKind::kInvariantViolation, offending,
           "measures must be strictly ascending by (measure_id, canonical bump_spec.bump_target "
           "serialization, canonical RiskBucketCoordinate serialization)");
    }
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
    } else if (measure.bucket_coordinate.present &&
               measure.bucket_coordinate.value.kind() == RiskBucketKind::kVolNode) {
      // Section 13.2 line 1028: `VOL_NODE.volatility_input_id` MUST equal the consumed
      // VolatilityInput id. This variant was previously unchecked, so any arbitrary input id could
      // name a vol surface the result never consumed. The PRESENT/NULL_WITH_REASON state of the
      // referenced input is part of the applicability rule, not only the id equality: a VOL_NODE
      // coordinate whose input is not PRESENT is an inapplicable coordinate and is refused.
      const auto& vol_node =
          std::get<VolNodeBucketCoordinate>(measure.bucket_coordinate.value.body);
      if (!inputs_identity.volatility_input_id.present) {
        fail(ContractViolationKind::kIdentityMismatch,
             child(measure_pointer, "bucket_coordinate"),
             "a VOL_NODE bucket requires inputs_identity.volatility_input_id to be PRESENT; no "
             "consumed VolatilityInput is named, so the coordinate is inapplicable");
      }
      if (vol_node.volatility_input_id != inputs_identity.volatility_input_id.value) {
        fail(ContractViolationKind::kIdentityMismatch,
             child(measure_pointer, "bucket_coordinate"),
             "VOL_NODE.volatility_input_id must equal the consumed "
             "inputs_identity.volatility_input_id");
      }
    } else if (measure.bucket_coordinate.present &&
               measure.bucket_coordinate.value.kind() == RiskBucketKind::kModelParameter) {
      // The V1 kernel cannot supply ModelInput, so no model-parameter measure is realizable.
      fail(ContractViolationKind::kInvalidTaggedUnionState,
           child(measure_pointer, "bucket_coordinate"),
           "MODEL_PARAMETER risk is not applicable to RATES_KERNEL_INPUT_V1");
    }
  }
  // All result identities must echo a V1-legal kernel input; model and calibration are N/A.
  require_inputs_identity_applicability(inputs_identity, product_type, pointer);
  // Section 13.4 (line 1051): the same product/product-identity linkage rule as the pricing result, so a
  // risk result cannot accept an id pair that no valid consumed request could have produced.
  require_product_identity_linkage(product_id, product_type, inputs_identity, pointer);
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
