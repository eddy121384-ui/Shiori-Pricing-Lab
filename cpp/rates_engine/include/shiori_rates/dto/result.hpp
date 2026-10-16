#pragma once
//
// Result DTOs — docs/33 (Issue #225) sections 12 (PricingResult) and 13 (RiskResult), including the
// Lane A diagnostics that #226 section 12.3 keeps inside the result contract.
//
// SCOPE: this is the versioned schema plumbing #227 section 4 requires ("RATES_PRICING_RESULT_V1",
// "RATES_RISK_RESULT_V1", "may implement schema plumbing without executable pricing/calibration
// behavior"). It encodes, validates and round-trips the result document. It computes NO price, NO
// sensitivity and NO risk measure, and it selects no bump size, bucket boundary or sign convention:
// every one of those values is data that a caller must supply, and the fields whose values are RED
// (`revaluation_rule_id`, `revaluation_rule_version`) fail closed if a caller tries to make them
// look resolved.
//
// LANE SEPARATION (docs/34 section 12): Lane A is exactly this document. Lane B
// (`RATES_RUNTIME_TELEMETRY_V1`) is a separate versioned channel owned by `shiori_rates_diagnostics`
// and is NEVER a member of PricingResult/RiskResult. To make that mechanical rather than a comment,
// `assumptions` and `diagnostics` are restricted to a map of JSON scalars: a nested object (the
// natural shape of a telemetry bag) is refused at decode time.
//
// DIVERGENCE REGISTER (open vocabularies #225 does not fix — #227 chooses no value):
//   * `sign_convention` / `pv_sign_convention` / `component_pvs[].sign_convention_ref` are open
//     convention tokens, not a closed enum, because #225 section 12.2 writes `<ValueOrReason<enum>>`
//     without defining the vocabulary and explicitly keeps the legacy receive/pay rule REFERENCE
//     ONLY. An invented enum would be a schema invention.
//   * `replay.tolerance` is an open documented-token field (for example `ABS_1E_9`), as in #225.
//   * `RiskBucketCoordinate(kind=VOL_NODE).node_key` is a `VolNodeKey`, whose shape is owned by the
//     vol contract implementation; it is carried as a canonical child payload exactly like
//     `VolatilityInput.payload`.
//
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/market.hpp"
#include "shiori_rates/dto/numeric.hpp"
#include "shiori_rates/dto/time.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"
#include "shiori_rates/dto/version.hpp"

namespace Shiori::rates::dto {

// A map of small typed scalar values (`assumptions`, `diagnostics`). Objects and nested arrays are
// refused: #225 requires typed data and forbids a narrative, and an object-valued entry is how a
// telemetry/prose bag would smuggle itself into the result contract.
using ScalarMap = std::map<std::string, CanonicalValue>;

// docs/33 section 12.2 `curve_role_map` (echoed verbatim from the kernel input's curve selection).
struct CurveRoleMap {
  std::optional<std::string> discount_curve_id;
  // Keyed by the validated FloatingIndex token; the value is a curve_id.
  std::map<std::string, std::string> forecast_curve_by_index;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CurveRoleMap from_canonical(const CanonicalValue& node,
                                                   const std::string& pointer);
  friend bool operator==(const CurveRoleMap& left, const CurveRoleMap& right) {
    return left.discount_curve_id == right.discount_curve_id &&
           left.forecast_curve_by_index == right.forecast_curve_by_index;
  }
};

// docs/33 section 12.2 `engine`: the Lane A engine identity (G-1). `engine_version` participates in
// replay identity; `method` is a deterministic method token, never dependency provenance (#226
// section 12.3 forbids collapsing source + methodology + version, and forbids overloading
// `engine_version` with QuantLib's version).
struct EngineIdentity {
  std::string engine_name;
  std::string engine_version;
  std::string method;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static EngineIdentity from_canonical(const CanonicalValue& node,
                                                     const std::string& pointer);
  friend bool operator==(const EngineIdentity& left, const EngineIdentity& right) {
    return left.engine_name == right.engine_name && left.engine_version == right.engine_version &&
           left.method == right.method;
  }
};

// docs/33 sections 12.2 / 13.2 `inputs_identity`.
struct InputsIdentity {
  std::string market_snapshot_id;
  std::string curve_set_id;
  CurveRoleMap curve_role_map;
  std::string fixing_store_id;
  ValueOrReason<std::string> volatility_input_id;
  ValueOrReason<std::string> exercise_terms_id;
  ValueOrReason<std::string> settlement_terms_id;
  std::string resolved_swap_product_id;
  std::string convention_set_id;
  std::string resolved_swap_schema_version;
  ValueOrReason<std::string> model_input_id;
  ValueOrReason<std::string> calibration_result_id;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static InputsIdentity from_canonical(const CanonicalValue& node,
                                                     const std::string& pointer);
  friend bool operator==(const InputsIdentity& left, const InputsIdentity& right);
};

// docs/33 sections 12.2 / 13.2 `replay`.
struct ReplayIdentity {
  std::string content_fingerprint;  // derived: canonical result preimage with this field excluded
  std::string inputs_fingerprint;   // MUST equal the consumed RatesKernelInput.content_fingerprint
  std::string tolerance;            // documented comparison tolerance token, e.g. ABS_1E_9

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ReplayIdentity from_canonical(const CanonicalValue& node,
                                                     const std::string& pointer);
  friend bool operator==(const ReplayIdentity& left, const ReplayIdentity& right) {
    return left.content_fingerprint == right.content_fingerprint &&
           left.inputs_fingerprint == right.inputs_fingerprint &&
           left.tolerance == right.tolerance;
  }
};

// docs/33 section 12.2 `headline_value_type`.
struct HeadlineValue {
  HeadlineSemantics semantics = HeadlineSemantics::kPresentValue;
  double value = 0.0;
  Unit unit = Unit::kCurrencyAmount;
  ValueOrReason<Currency> currency;
  ValueOrReason<std::string> sign_convention;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static HeadlineValue from_canonical(const CanonicalValue& node,
                                                    const std::string& pointer);
};

// docs/33 section 12.2 `component_pvs[]`.
struct ComponentPv {
  std::string component_id;
  double value = 0.0;
  Unit unit = Unit::kCurrencyAmount;
  ValueOrReason<std::string> sign_convention_ref;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ComponentPv from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer);
};

// ---------------------------------------------------------------------------------------------
// PricingResult (docs/33 section 12)
// ---------------------------------------------------------------------------------------------
struct PricingResult {
  std::string product_id;
  ProductType product_type = ProductType::kOis;
  Date valuation_date;
  std::string valuation_context_id;
  Currency result_currency = Currency::kUsd;
  ValueOrReason<HeadlineValue> headline;
  ValueOrReason<NumericWithUnit> pv;
  ValueOrReason<std::string> pv_sign_convention;
  ValueOrReason<std::vector<ComponentPv>> component_pvs;
  ValueOrReason<NumericWithUnit> annuity_pvbp;
  ValueOrReason<NumericWithUnit> par_rate;
  PricingStatus status = PricingStatus::kFailed;
  std::vector<DiagnosticRecord> warnings;
  std::vector<DiagnosticRecord> errors;
  EngineIdentity engine;
  InputsIdentity inputs_identity;
  ScalarMap assumptions;
  ScalarMap diagnostics;
  ReplayIdentity replay;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static PricingResult from_canonical(const CanonicalValue& node,
                                                    const std::string& pointer);
  // Section 12.3/12.4 applicability and linkage rules, called by from_canonical.
  void validate_rules(const std::string& pointer) const;
};

// ---------------------------------------------------------------------------------------------
// RiskResult (docs/33 section 13)
// ---------------------------------------------------------------------------------------------
struct TenorBucketCoordinate {
  std::string curve_id;
  CurveUsageRole curve_usage_role = CurveUsageRole::kDiscount;
  ValueOrReason<FloatingIndex> index_id;
  NumericWithUnit coordinate;  // unit MUST be YEARS_FRACTION
  ValueOrReason<std::string> label;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static TenorBucketCoordinate from_canonical(const CanonicalValue& node,
                                                            const std::string& pointer);
};

struct VolNodeBucketCoordinate {
  std::string volatility_input_id;
  CanonicalValue node_key;  // VolNodeKey; shape owned by the vol contract (see header note)

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static VolNodeBucketCoordinate from_canonical(const CanonicalValue& node,
                                                              const std::string& pointer);
};

struct ModelParameterBucketCoordinate {
  std::string model_input_id;
  std::string parameter_name;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ModelParameterBucketCoordinate from_canonical(const CanonicalValue& node,
                                                                     const std::string& pointer);
};

// Tagged union: exactly one variant is serialized, selected by `kind`.
struct RiskBucketCoordinate {
  std::variant<TenorBucketCoordinate, VolNodeBucketCoordinate, ModelParameterBucketCoordinate> body;

  [[nodiscard]] RiskBucketKind kind() const;
  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static RiskBucketCoordinate from_canonical(const CanonicalValue& node,
                                                           const std::string& pointer);
};

struct AllSelectedCurvesTarget {
  [[nodiscard]] CanonicalValue to_canonical() const;
};
struct DiscountCurveTarget {
  std::string curve_id;
  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static DiscountCurveTarget from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer);
};
struct ForecastCurveTarget {
  std::string curve_id;
  FloatingIndex index_id = FloatingIndex::kUsdSofr;
  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ForecastCurveTarget from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer);
};

struct RiskBumpTarget {
  std::variant<AllSelectedCurvesTarget, DiscountCurveTarget, ForecastCurveTarget> body;

  [[nodiscard]] RiskBumpTargetKind kind() const;
  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static RiskBumpTarget from_canonical(const CanonicalValue& node,
                                                     const std::string& pointer);
};

// docs/33 section 13.2 `bump_spec`.
struct RiskBumpSpec {
  RiskBumpType bump_type = RiskBumpType::kParallelBp;
  double bump_size = 0.0;
  Unit bump_unit = Unit::kBasisPoints;
  ValueOrReason<RiskBumpTarget> bump_target;
  std::string revaluation_rule_id;       // UNRESOLVED (RED-risk)
  std::string revaluation_rule_version;  // UNRESOLVED (RED-risk)

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static RiskBumpSpec from_canonical(const CanonicalValue& node,
                                                   const std::string& pointer);
};

// docs/33 section 13.2 `measures[]`.
struct RiskMeasure {
  RiskMeasureId measure_id = RiskMeasureId::kDv01;
  double value = 0.0;
  Unit unit = Unit::kCurrencyAmountPerBasisPoint;
  RiskBumpSpec bump_spec;
  ValueOrReason<RiskBucketCoordinate> bucket_coordinate;
  std::string market_snapshot_id;
  std::optional<std::string> model_version;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static RiskMeasure from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer);
};

struct RiskResult {
  std::string product_id;
  ProductType product_type = ProductType::kOis;
  Date valuation_date;
  std::string valuation_context_id;
  Currency result_currency = Currency::kUsd;
  std::vector<RiskMeasure> measures;
  InputsIdentity inputs_identity;
  PricingStatus status = PricingStatus::kFailed;
  std::vector<DiagnosticRecord> warnings;
  std::vector<DiagnosticRecord> errors;
  EngineIdentity engine;
  ScalarMap diagnostics;
  ReplayIdentity replay;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static RiskResult from_canonical(const CanonicalValue& node,
                                                 const std::string& pointer);
  void validate_rules(const std::string& pointer) const;
};

// Derives `replay.content_fingerprint` for a result document: the canonical preimage with the own
// `replay.content_fingerprint` field excluded (docs/33 section 15.4 non-recursive rule).
[[nodiscard]] std::string pricing_result_content_fingerprint(const PricingResult& result);
[[nodiscard]] std::string risk_result_content_fingerprint(const RiskResult& result);
void assign_derived_identities(PricingResult& result);
void assign_derived_identities(RiskResult& result);

// Lane A / Lane B separation guard, exposed so the diagnostics target can be tested against it.
// Returns false when the payload would place a Lane B telemetry fact inside a Lane A result field.
[[nodiscard]] bool is_lane_a_scalar_map(const CanonicalValue& node,
                                        std::string* offending_key = nullptr);

}  // namespace Shiori::rates::dto
