// V1 result-contract conformance: the identity and ordering rules that a RE-HASHED result document
// must not be able to bypass.
//
// Contract: docs/33_rates_market_model_result_contracts_225.md sections 5.1, 12.2/12.3, 13.2 and 15.4.
// Each rule below is asserted twice: a contract-valid document is ACCEPTED and keeps a stable
// fingerprint, and the previously-accepted contract-invalid document is REFUSED with the documented
// violation kind at the offending member's pointer.
//
// WHY THE REFUSALS ARE NOT FINGERPRINT REFUSALS
// A result's `replay.content_fingerprint` is a digest of the result preimage (section 15.4), so a
// producer can always recompute it over contract-invalid content. Every negative fixture here is
// therefore re-hashed with `assign_derived_identities` before it is decoded: the fingerprint fence
// passes, and the refusal must come from the contract rule under test, at its own pointer. A
// fingerprint check is not an identity check, and a re-hashed fingerprint is not evidence of a valid
// identity.
//
// FIXTURE POLICY (AGENTS.md rule 4)
// Everything below is a synthetic CONTRACT fixture. No market level, no price, no sensitivity and no
// bump methodology is claimed: numeric members are shape probes (0.0), open-vocabulary text members
// carry the contract's own `UNRESOLVED` marker (#225 section 7.0/15.2), and the `VolNodeKey` payload -
// whose shape is owned by the vol contract (result.hpp divergence register) - is the empty canonical
// object rather than an invented node vocabulary.
#include <gtest/gtest.h>

#include <functional>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/result.hpp"
#include "shiori_rates/dto/time.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"

namespace {

using Shiori::rates::dto::AllSelectedCurvesTarget;
using Shiori::rates::dto::CanonicalMembers;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ComponentPv;
using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::ConventionSetId;
using Shiori::rates::dto::Currency;
using Shiori::rates::dto::CurveUsageRole;
using Shiori::rates::dto::Date;
using Shiori::rates::dto::DiagnosticRecord;
using Shiori::rates::dto::EngineIdentity;
using Shiori::rates::dto::FloatingIndex;
using Shiori::rates::dto::HeadlineSemantics;
using Shiori::rates::dto::HeadlineValue;
using Shiori::rates::dto::InputsIdentity;
using Shiori::rates::dto::ModelParameterBucketCoordinate;
using Shiori::rates::dto::NumericWithUnit;
using Shiori::rates::dto::PricingResult;
using Shiori::rates::dto::PricingStatus;
using Shiori::rates::dto::ProductType;
using Shiori::rates::dto::RatesErrorCode;
using Shiori::rates::dto::ReasonCategory;
using Shiori::rates::dto::ReplayIdentity;
using Shiori::rates::dto::RiskBucketCoordinate;
using Shiori::rates::dto::RiskBumpSpec;
using Shiori::rates::dto::RiskBumpTarget;
using Shiori::rates::dto::RiskBumpType;
using Shiori::rates::dto::RiskMeasure;
using Shiori::rates::dto::RiskMeasureId;
using Shiori::rates::dto::RiskResult;
using Shiori::rates::dto::SchemaVersion;
using Shiori::rates::dto::StructuredReason;
using Shiori::rates::dto::TenorBucketCoordinate;
using Shiori::rates::dto::Unit;
using Shiori::rates::dto::ValueOrReason;
using Shiori::rates::dto::ValuationContext;
using Shiori::rates::dto::VolNodeBucketCoordinate;
using Shiori::rates::dto::assign_derived_identities;
using Shiori::rates::dto::compute_valuation_context_id;
using Shiori::rates::dto::enum_to_token;
using Shiori::rates::dto::is_valid_fingerprint;
using Shiori::rates::dto::kFingerprintHexLength;
using Shiori::rates::dto::kUnresolvedToken;
using Shiori::rates::dto::pricing_result_content_fingerprint;
using Shiori::rates::dto::risk_result_content_fingerprint;
using Shiori::rates::dto::to_token;

// ---------------------------------------------------------------------------------------------
// Fixture constants. Synthetic identifiers only; the two reason-code tokens are the contract's own
// text (PRICING_FAILED, section 12.3) and an approved section 16 domain code (ENGINE_ERROR).
// ---------------------------------------------------------------------------------------------
constexpr std::string_view kFixtureValuationDate = "2024-01-01";
constexpr std::string_view kFixtureProductId = "SYNTHETIC_CONTRACT_FIXTURE_SWAP";
// The SWAPTION's own product id. docs/33 lines 931/962 require the top-level `product_id` to identify the
// DERIVATIVE and `inputs_identity.resolved_swap_product_id` to identify the UNDERLYING swap it exercises,
// so the two ids are distinct by construction and the derivative is never identified by the swap id.
constexpr std::string_view kFixtureSwaptionProductId = "SYNTHETIC_CONTRACT_FIXTURE_SWAPTION";
constexpr std::string_view kFixtureEngineName = "SYNTHETIC_CONTRACT_FIXTURE_ENGINE";
constexpr std::string_view kFixtureEngineVersion = "SHAPE_ONLY_NOT_A_RELEASE";
constexpr std::string_view kFixtureMethod = "SCHEMA_VALIDATION_ONLY";
constexpr std::string_view kFixtureSnapshotId = "SYNTHETIC_CONTRACT_FIXTURE_SNAPSHOT";
constexpr std::string_view kFixtureCurveSetId = "SYNTHETIC_CONTRACT_FIXTURE_CURVE_SET";
constexpr std::string_view kFixtureDiscountCurveId = "SYNTHETIC_CONTRACT_FIXTURE_DISCOUNT_CURVE";
constexpr std::string_view kFixtureForecastCurveId = "SYNTHETIC_CONTRACT_FIXTURE_FORECAST_CURVE";
constexpr std::string_view kFixtureFixingStoreId = "SYNTHETIC_CONTRACT_FIXTURE_FIXING_STORE";
constexpr std::string_view kFixtureVolatilityInputId = "SYNTHETIC_CONTRACT_FIXTURE_VOL_INPUT";
constexpr std::string_view kFixtureOtherVolatilityInputId = "SYNTHETIC_CONTRACT_FIXTURE_VOL_INPUT_2";
constexpr std::string_view kFixtureModelInputId = "SYNTHETIC_CONTRACT_FIXTURE_MODEL_INPUT";
constexpr std::string_view kFixtureOtherModelInputId = "SYNTHETIC_CONTRACT_FIXTURE_MODEL_INPUT_2";
constexpr std::string_view kFixtureExerciseTermsId = "SYNTHETIC_CONTRACT_FIXTURE_EXERCISE_TERMS";
constexpr std::string_view kFixtureSettlementTermsId = "SYNTHETIC_CONTRACT_FIXTURE_SETTLEMENT_TERMS";
// A token this build KNOWS but which names a DIFFERENT contract than the embedded ResolvedSwap.
constexpr std::string_view kFixtureOtherContractToken = "CURVE_SET_V1";
constexpr std::string_view kPricingFailedReasonCode = "PRICING_FAILED";
constexpr std::string_view kOtherUnavailableCode = "ENGINE_ERROR";
// docs/33 section 16 keeps the warning vocabulary OPEN (its own examples are FORWARD_STARTING and
// DATA_QUALITY), so this token is machine-readable but deliberately NOT one of the eleven error codes:
// it proves the closed error check never reaches `warnings`.
constexpr std::string_view kFixtureWarningCode = "DATA_QUALITY";
// A shape-only token that is NOT in the section 16 error table. It claims no domain meaning; it exists
// only so the closed-vocabulary rule has an unapproved code to refuse.
constexpr std::string_view kUnapprovedErrorCode = "SHAPE_PROBE_NOT_A_SECTION_16_CODE";
// `sign_convention` / `pv_sign_convention` are open convention tokens (#225 section 12.2 leaves the
// vocabulary unfixed and keeps the legacy receive/pay rule REFERENCE ONLY), so the mismatch probe uses
// a shape-only token rather than naming or inventing a real sign convention.
constexpr std::string_view kOtherSignConventionToken = "SHAPE_ONLY_OTHER_SIGN_CONVENTION";

// The closed section 16 error vocabulary, taken from the DTO layer's own table so the fixture cannot
// drift from it. The static assertion is the second half of the pin: eleven codes, no more, no less.
constexpr RatesErrorCode kApprovedErrorCodes[] = {
    RatesErrorCode::kUnsupportedProduct,        RatesErrorCode::kMissingMarketData,
    RatesErrorCode::kValuationDateMismatch,     RatesErrorCode::kCurrencyMismatch,
    RatesErrorCode::kMarketSnapshotMismatch,    RatesErrorCode::kInvalidProduct,
    RatesErrorCode::kMissingReferenceData,      RatesErrorCode::kSameDayFixingRuleUnresolved,
    RatesErrorCode::kUnknownSchemaVersion,      RatesErrorCode::kUnknownMethodologyVersion,
    RatesErrorCode::kEngineError,
};
static_assert(sizeof(kApprovedErrorCodes) / sizeof(kApprovedErrorCodes[0]) == 11,
              "the approved V1 error vocabulary is exactly the eleven section 16 codes");

// ---------------------------------------------------------------------------------------------
// Small builders
// ---------------------------------------------------------------------------------------------
[[nodiscard]] Date fixture_date() { return Date(std::string(kFixtureValuationDate)); }

// A declared fingerprint is an INPUT at this layer, and the DTO layer has no consumed RatesKernelInput
// to compare against (section 15.4 binds `inputs_fingerprint` to that kernel document, which does not
// exist in a result-shape fixture). The value below is a validated-shape placeholder, never presented
// as replay evidence.
[[nodiscard]] std::string shape_only_fingerprint() {
  return std::string(kFingerprintHexLength, '0');
}

// Section 5.1: the one V1 valuation-context identity preimage is {valuation_date, reporting_currency}.
// The fixture uses the DTO layer's existing derived-identity helper so the test never restates (or
// re-invents) the recipe; what is under test is that the RESULT layer re-derives and refuses.
[[nodiscard]] std::string derived_valuation_context_id(const Date& valuation_date,
                                                       Currency reporting_currency) {
  ValuationContext context;
  context.valuation_date = valuation_date;
  context.reporting_currency = reporting_currency;
  return compute_valuation_context_id(context);
}

[[nodiscard]] EngineIdentity fixture_engine() {
  EngineIdentity engine;
  engine.engine_name = std::string(kFixtureEngineName);
  engine.engine_version = std::string(kFixtureEngineVersion);
  engine.method = std::string(kFixtureMethod);
  return engine;
}

[[nodiscard]] ReplayIdentity fixture_replay() {
  ReplayIdentity replay;
  replay.content_fingerprint = shape_only_fingerprint();  // replaced by assign_derived_identities
  replay.inputs_fingerprint = shape_only_fingerprint();
  replay.tolerance = std::string(kUnresolvedToken);
  return replay;
}

// `optional_inputs_present` distinguishes an OIS-shaped identity (vol/model/exercise/settlement are
// structured NOT_APPLICABLE) from a SWAPTION-shaped one (those inputs are consumed and therefore
// PRESENT). Both are internally coherent: the fixture never claims a consumed input it does not name.
[[nodiscard]] InputsIdentity fixture_inputs_identity(bool optional_inputs_present) {
  InputsIdentity identity;
  identity.market_snapshot_id = std::string(kFixtureSnapshotId);
  identity.curve_set_id = std::string(kFixtureCurveSetId);
  identity.curve_role_map.discount_curve_id = std::string(kFixtureDiscountCurveId);
  identity.curve_role_map.forecast_curve_by_index.emplace(
      std::string(enum_to_token(FloatingIndex::kUsdSofr)), std::string(kFixtureForecastCurveId));
  identity.fixing_store_id = std::string(kFixtureFixingStoreId);
  identity.volatility_input_id =
      optional_inputs_present
          ? ValueOrReason<std::string>::of(std::string(kFixtureVolatilityInputId))
          : ValueOrReason<std::string>::not_applicable();
  identity.exercise_terms_id =
      optional_inputs_present ? ValueOrReason<std::string>::of(std::string(kFixtureExerciseTermsId))
                              : ValueOrReason<std::string>::not_applicable();
  identity.settlement_terms_id =
      optional_inputs_present
          ? ValueOrReason<std::string>::of(std::string(kFixtureSettlementTermsId))
          : ValueOrReason<std::string>::not_applicable();
  identity.resolved_swap_product_id = std::string(kFixtureProductId);
  identity.convention_set_id = std::string(enum_to_token(ConventionSetId::kUsdSofrOisV1));
  // The only ResolvedSwap wire version this build knows (docs/32 section 2), taken from the contract's
  // own token table rather than spelled out, so the fixture cannot drift from the vocabulary.
  identity.resolved_swap_schema_version = std::string(to_token(SchemaVersion::kResolvedSwapV1));
  identity.model_input_id =
      optional_inputs_present
          ? ValueOrReason<std::string>::of(std::string(kFixtureModelInputId))
          : ValueOrReason<std::string>::not_applicable();
  identity.calibration_result_id = ValueOrReason<std::string>::not_applicable();
  return identity;
}

// ---------------------------------------------------------------------------------------------
// PricingResult fixtures (docs/33 section 12)
// ---------------------------------------------------------------------------------------------
[[nodiscard]] HeadlineValue currency_headline(Currency headline_currency) {
  HeadlineValue headline;
  headline.semantics = HeadlineSemantics::kPresentValue;
  headline.value = 0.0;  // shape probe: this lane computes no price
  headline.unit = Unit::kCurrencyAmount;
  headline.currency = ValueOrReason<Currency>::of(headline_currency);
  headline.sign_convention = ValueOrReason<std::string>::of(std::string(kUnresolvedToken));
  return headline;
}

// SUCCESS with PRESENT(HeadlineValue): the shape that exercises the headline currency rule and the
// headline/supplemental-metric agreement rule (section 12.3).
[[nodiscard]] PricingResult successful_pricing_result(Currency headline_currency) {
  PricingResult result;
  result.product_id = std::string(kFixtureProductId);
  result.product_type = ProductType::kOis;
  result.valuation_date = fixture_date();
  result.result_currency = Currency::kUsd;
  result.valuation_context_id =
      derived_valuation_context_id(result.valuation_date, result.result_currency);
  result.headline = ValueOrReason<HeadlineValue>::of(currency_headline(headline_currency));
  result.pv =
      ValueOrReason<NumericWithUnit>::of(NumericWithUnit(0.0, Unit::kCurrencyAmount));  // probe
  result.pv_sign_convention = ValueOrReason<std::string>::of(std::string(kUnresolvedToken));
  result.component_pvs = ValueOrReason<std::vector<ComponentPv>>::not_applicable();
  result.annuity_pvbp = ValueOrReason<NumericWithUnit>::not_applicable();
  result.par_rate = ValueOrReason<NumericWithUnit>::not_applicable();
  result.status = PricingStatus::kSuccess;
  result.engine = fixture_engine();
  result.inputs_identity = fixture_inputs_identity(false);
  result.replay = fixture_replay();
  assign_derived_identities(result);
  return result;
}

// FAILED with exactly NULL_WITH_REASON(UNAVAILABLE, PRICING_FAILED) plus one error record
// (sections 12.3/16). Every supplemental metric stays structured NOT_APPLICABLE: a FAILED result never
// carries a value, a bare null or a zero.
[[nodiscard]] PricingResult failed_pricing_result(std::string reason_code) {
  PricingResult result;
  result.product_id = std::string(kFixtureProductId);
  result.product_type = ProductType::kOis;
  result.valuation_date = fixture_date();
  result.result_currency = Currency::kUsd;
  result.valuation_context_id =
      derived_valuation_context_id(result.valuation_date, result.result_currency);
  result.headline = ValueOrReason<HeadlineValue>::none(
      StructuredReason(ReasonCategory::kUnavailable, std::move(reason_code)));
  result.pv = ValueOrReason<NumericWithUnit>::not_applicable();
  result.pv_sign_convention = ValueOrReason<std::string>::not_applicable();
  result.component_pvs = ValueOrReason<std::vector<ComponentPv>>::not_applicable();
  result.annuity_pvbp = ValueOrReason<NumericWithUnit>::not_applicable();
  result.par_rate = ValueOrReason<NumericWithUnit>::not_applicable();
  result.status = PricingStatus::kFailed;
  DiagnosticRecord error;
  error.code = std::string(kOtherUnavailableCode);
  error.message = "SYNTHETIC CONTRACT FIXTURE ERROR RECORD";
  result.errors.push_back(std::move(error));
  result.engine = fixture_engine();
  result.inputs_identity = fixture_inputs_identity(false);
  result.replay = fixture_replay();
  assign_derived_identities(result);
  return result;
}

// ---------------------------------------------------------------------------------------------
// RiskResult fixtures (docs/33 section 13)
// ---------------------------------------------------------------------------------------------
[[nodiscard]] RiskBumpSpec bump_spec(RiskBumpType bump_type, Unit bump_unit,
                                     ValueOrReason<RiskBumpTarget> bump_target) {
  RiskBumpSpec spec;
  spec.bump_type = bump_type;
  spec.bump_size = 0.0;  // shape probe: this lane selects no bump size
  spec.bump_unit = bump_unit;
  spec.bump_target = std::move(bump_target);
  // RED-risk values (section 13.2): carried as the contract's own open marker, never as a chosen rule.
  spec.revaluation_rule_id = std::string(kUnresolvedToken);
  spec.revaluation_rule_version = std::string(kUnresolvedToken);
  return spec;
}

[[nodiscard]] RiskBucketCoordinate tenor_bucket_coordinate() {
  TenorBucketCoordinate coordinate;
  coordinate.curve_id = std::string(kFixtureDiscountCurveId);
  coordinate.curve_usage_role = CurveUsageRole::kDiscount;
  coordinate.index_id = ValueOrReason<FloatingIndex>::not_applicable();
  coordinate.coordinate = NumericWithUnit(0.0, Unit::kYearsFraction);  // shape probe
  coordinate.label = ValueOrReason<std::string>::not_applicable();
  return RiskBucketCoordinate{std::move(coordinate)};
}

[[nodiscard]] RiskBucketCoordinate vol_node_bucket_coordinate(std::string volatility_input_id) {
  VolNodeBucketCoordinate coordinate;
  coordinate.volatility_input_id = std::move(volatility_input_id);
  // VolNodeKey shape is owned by the vol contract and is not implemented in this lane, so the fixture
  // carries the empty canonical object instead of inventing unpublished node fields.
  coordinate.node_key = CanonicalValue::make_object(CanonicalMembers{});
  return RiskBucketCoordinate{std::move(coordinate)};
}

[[nodiscard]] RiskBucketCoordinate model_parameter_bucket_coordinate(std::string model_input_id) {
  ModelParameterBucketCoordinate coordinate;
  coordinate.model_input_id = std::move(model_input_id);
  coordinate.parameter_name = std::string(kUnresolvedToken);
  return RiskBucketCoordinate{std::move(coordinate)};
}

[[nodiscard]] RiskMeasure bucketed_tenor_measure() {
  RiskMeasure measure;
  measure.measure_id = RiskMeasureId::kDv01;
  measure.value = 0.0;  // shape probe: this lane computes no sensitivity
  measure.unit = Unit::kCurrencyAmountPerBasisPoint;
  measure.bump_spec = bump_spec(RiskBumpType::kBucketedTenor, Unit::kBasisPoints,
                                ValueOrReason<RiskBumpTarget>::not_applicable());
  measure.bucket_coordinate = ValueOrReason<RiskBucketCoordinate>::of(tenor_bucket_coordinate());
  measure.market_snapshot_id = std::string(kFixtureSnapshotId);
  return measure;
}

[[nodiscard]] RiskMeasure parallel_measure() {
  RiskMeasure measure;
  measure.measure_id = RiskMeasureId::kDv01;
  measure.value = 0.0;
  measure.unit = Unit::kCurrencyAmountPerBasisPoint;
  measure.bump_spec = bump_spec(
      RiskBumpType::kParallelBp, Unit::kBasisPoints,
      ValueOrReason<RiskBumpTarget>::of(RiskBumpTarget{AllSelectedCurvesTarget{}}));
  measure.bucket_coordinate = ValueOrReason<RiskBucketCoordinate>::not_applicable();
  measure.market_snapshot_id = std::string(kFixtureSnapshotId);
  return measure;
}

[[nodiscard]] RiskMeasure model_parameter_measure(std::string model_input_id) {
  RiskMeasure measure;
  measure.measure_id = RiskMeasureId::kGamma;
  measure.value = 0.0;
  measure.unit = Unit::kDecimalSensitivity;
  measure.bump_spec = bump_spec(RiskBumpType::kModelParam, Unit::kDecimal,
                                ValueOrReason<RiskBumpTarget>::not_applicable());
  measure.bucket_coordinate = ValueOrReason<RiskBucketCoordinate>::of(
      model_parameter_bucket_coordinate(std::move(model_input_id)));
  measure.market_snapshot_id = std::string(kFixtureSnapshotId);
  return measure;
}

[[nodiscard]] RiskMeasure vol_point_measure(std::string volatility_input_id) {
  RiskMeasure measure;
  measure.measure_id = RiskMeasureId::kVega;
  measure.value = 0.0;
  measure.unit = Unit::kDecimalSensitivity;
  measure.bump_spec = bump_spec(RiskBumpType::kVolPoint, Unit::kDecimal,
                                ValueOrReason<RiskBumpTarget>::not_applicable());
  measure.bucket_coordinate = ValueOrReason<RiskBucketCoordinate>::of(
      vol_node_bucket_coordinate(std::move(volatility_input_id)));
  measure.market_snapshot_id = std::string(kFixtureSnapshotId);
  return measure;
}

// V1-valid shape: two DV01s (N/A bump target before PRESENT), then VOL_POINT VEGA.
// MODEL_PARAM remains reserved in the wire contract; no RatesKernelInput V1 consumes ModelInput.
[[nodiscard]] RiskResult risk_result_fixture() {
  RiskResult result;
  // A swaption result identifies the DERIVATIVE; the underlying swap is named by the embedded
  // `inputs_identity.resolved_swap_product_id` (docs/33 lines 931/962).
  result.product_id = std::string(kFixtureSwaptionProductId);
  result.product_type = ProductType::kSwaption;
  result.valuation_date = fixture_date();
  result.result_currency = Currency::kUsd;
  result.valuation_context_id =
      derived_valuation_context_id(result.valuation_date, result.result_currency);
  result.measures.push_back(bucketed_tenor_measure());
  result.measures.push_back(parallel_measure());
  result.measures.push_back(vol_point_measure(std::string(kFixtureVolatilityInputId)));
  result.inputs_identity = fixture_inputs_identity(true);
  result.inputs_identity.model_input_id = ValueOrReason<std::string>::not_applicable();
  result.status = PricingStatus::kSuccess;
  result.engine = fixture_engine();
  result.replay = fixture_replay();
  assign_derived_identities(result);
  return result;
}

// FAILED with exactly one error record and no measures: a failure computes no sensitivity, and section
// 16 requires >=1 structured error. The error code is a parameter so the closed-vocabulary rule can be
// probed with both an approved and an unapproved token.
[[nodiscard]] RiskResult failed_risk_result(const std::string& error_code) {
  RiskResult result = risk_result_fixture();
  result.status = PricingStatus::kFailed;
  result.measures.clear();
  // Section 13.4 (line 1051): a FAILED result carries no measure, so nothing in this document consumes
  // a ModelInput and the identity must stay structured NOT_APPLICABLE rather than keep claiming the
  // model input the successful fixture consumed. The volatility/exercise/settlement identities stay
  // PRESENT: they echo the Phase-2 swaption kernel input identically in both statuses.
  result.inputs_identity.model_input_id = ValueOrReason<std::string>::not_applicable();
  DiagnosticRecord error;
  error.code = error_code;
  error.message = "SYNTHETIC CONTRACT FIXTURE ERROR RECORD";
  result.errors.push_back(std::move(error));
  assign_derived_identities(result);
  return result;
}

// ---------------------------------------------------------------------------------------------
// Decode probes
// ---------------------------------------------------------------------------------------------
struct Refusal {
  bool refused = false;
  // Sentinel kind for "nothing was thrown"; a test always asserts `refused` first.
  ContractViolationKind kind = ContractViolationKind::kInvariantViolation;
  std::string pointer;
  std::string message;
};

[[nodiscard]] Refusal decode_pricing_result(const CanonicalValue& document) {
  try {
    (void)PricingResult::from_canonical(document, "");
  } catch (const ContractViolation& violation) {
    return Refusal{true, violation.kind(), violation.pointer(), violation.message()};
  }
  return Refusal{};
}

[[nodiscard]] Refusal decode_risk_result(const CanonicalValue& document) {
  try {
    (void)RiskResult::from_canonical(document, "");
  } catch (const ContractViolation& violation) {
    return Refusal{true, violation.kind(), violation.pointer(), violation.message()};
  }
  return Refusal{};
}

void expect_refusal(const Refusal& refusal, ContractViolationKind kind, const std::string& pointer) {
  ASSERT_TRUE(refusal.refused) << "the contract-invalid document must be refused";
  EXPECT_EQ(std::string(to_token(refusal.kind)), std::string(to_token(kind)))
      << "refused at " << refusal.pointer << ": " << refusal.message;
  EXPECT_EQ(refusal.pointer, pointer) << refusal.message;
  // A re-hashed document passes the fingerprint fence by construction, so a fingerprint refusal here
  // would mean the fixture never exercised the rule under test.
  EXPECT_NE(refusal.kind, ContractViolationKind::kFingerprintMismatch)
      << "the refusal must come from the contract rule, not from the fingerprint fence";
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// Finding 1 - `valuation_context_id` is re-derived, not trusted (PricingResult)
// docs/33 section 12.2 line 908 + section 5.1 line 315.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultContextIdentity, AcceptsTheDerivedIdAndKeepsAFingerprintStable) {
  const PricingResult fixture = successful_pricing_result(Currency::kUsd);
  const CanonicalValue document = fixture.to_canonical();
  EXPECT_TRUE(is_valid_fingerprint(fixture.replay.content_fingerprint));
  EXPECT_EQ(fixture.replay.content_fingerprint, pricing_result_content_fingerprint(fixture));

  const Refusal refusal = decode_pricing_result(document);
  ASSERT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;

  const PricingResult decoded = PricingResult::from_canonical(document, "");
  EXPECT_EQ(decoded.replay.content_fingerprint, fixture.replay.content_fingerprint);
  EXPECT_EQ(decoded.to_canonical().dump(), document.dump());
}

TEST(PricingResultContextIdentity, RefusesAStaleIdEvenAfterTheFingerprintIsRehashed) {
  PricingResult stale = successful_pricing_result(Currency::kUsd);
  stale.valuation_context_id = std::string(kFingerprintHexLength, 'a');
  ASSERT_NE(stale.valuation_context_id,
            derived_valuation_context_id(stale.valuation_date, stale.result_currency));
  // Re-hash over the contract-invalid content: the fingerprint fence now passes by construction.
  assign_derived_identities(stale);
  EXPECT_EQ(stale.replay.content_fingerprint, pricing_result_content_fingerprint(stale));
  EXPECT_NE(stale.replay.content_fingerprint,
            successful_pricing_result(Currency::kUsd).replay.content_fingerprint);

  expect_refusal(decode_pricing_result(stale.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/valuation_context_id");
}

// ---------------------------------------------------------------------------------------------
// Finding 1 - `valuation_context_id` is re-derived, not trusted (RiskResult)
// docs/33 section 13.2 line 981 + section 5.1 line 315.
// ---------------------------------------------------------------------------------------------
TEST(RiskResultContextIdentity, AcceptsTheDerivedIdAndKeepsAFingerprintStable) {
  const RiskResult fixture = risk_result_fixture();
  const CanonicalValue document = fixture.to_canonical();
  EXPECT_EQ(fixture.replay.content_fingerprint, risk_result_content_fingerprint(fixture));

  const Refusal refusal = decode_risk_result(document);
  ASSERT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;

  const RiskResult decoded = RiskResult::from_canonical(document, "");
  EXPECT_EQ(decoded.replay.content_fingerprint, fixture.replay.content_fingerprint);
  EXPECT_EQ(decoded.to_canonical().dump(), document.dump());
}

TEST(RiskResultContextIdentity, RefusesAStaleIdEvenAfterTheFingerprintIsRehashed) {
  RiskResult stale = risk_result_fixture();
  stale.valuation_context_id = std::string(kFingerprintHexLength, 'b');
  ASSERT_NE(stale.valuation_context_id,
            derived_valuation_context_id(stale.valuation_date, stale.result_currency));
  assign_derived_identities(stale);
  EXPECT_EQ(stale.replay.content_fingerprint, risk_result_content_fingerprint(stale));

  expect_refusal(decode_risk_result(stale.to_canonical()), ContractViolationKind::kIdentityMismatch,
                 "/valuation_context_id");
}

// ---------------------------------------------------------------------------------------------
// Finding 4 - `inputs_identity.resolved_swap_schema_version` is exactly RESOLVED_SWAP_V1
// docs/33 section 12.2 line 933 ("# RESOLVED_SWAP_V1") + docs/32 section 2 line 86.
// ---------------------------------------------------------------------------------------------
TEST(InputsIdentitySchemaVersion, AcceptsTheResolvedSwapWireVersion) {
  const PricingResult fixture = successful_pricing_result(Currency::kUsd);
  EXPECT_EQ(fixture.inputs_identity.resolved_swap_schema_version,
            std::string(to_token(SchemaVersion::kResolvedSwapV1)));
  const Refusal refusal = decode_pricing_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

TEST(InputsIdentitySchemaVersion, RefusesAnotherKnownContractTokenAfterRehash) {
  PricingResult wrong_version = successful_pricing_result(Currency::kUsd);
  // CURVE_SET_V1 is a token this build KNOWS: that recognition is exactly what let the previous
  // check accept it as the wire version of the embedded ResolvedSwap.
  wrong_version.inputs_identity.resolved_swap_schema_version =
      std::string(kFixtureOtherContractToken);
  assign_derived_identities(wrong_version);

  expect_refusal(decode_pricing_result(wrong_version.to_canonical()),
                 ContractViolationKind::kUnknownSchemaVersion,
                 "/inputs_identity/resolved_swap_schema_version");
}

// ---------------------------------------------------------------------------------------------
// Finding 5 - a PRESENT currency-denominated headline carries the enclosing result_currency
// docs/33 section 12.3 line 955.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultHeadlineCurrency, AcceptsTheEnclosingResultCurrency) {
  const PricingResult fixture = successful_pricing_result(Currency::kUsd);
  ASSERT_TRUE(fixture.headline.present);
  EXPECT_EQ(fixture.headline.value.currency.value, fixture.result_currency);

  const Refusal refusal = decode_pricing_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

TEST(PricingResultHeadlineCurrency, RefusesADifferentCurrencyAfterRehash) {
  PricingResult mismatched = successful_pricing_result(Currency::kEur);
  ASSERT_EQ(mismatched.result_currency, Currency::kUsd);
  ASSERT_TRUE(mismatched.headline.present);
  ASSERT_TRUE(mismatched.headline.value.currency.present);
  ASSERT_NE(mismatched.headline.value.currency.value, mismatched.result_currency);
  assign_derived_identities(mismatched);

  expect_refusal(decode_pricing_result(mismatched.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/headline/value/currency");
}

// The symmetric half of the headline currency rule (docs/33 section 12.3 lines 947 and 955): only
// `CURRENCY_AMOUNT` and `CURRENCY_AMOUNT_PER_BASIS_POINT` are currency-denominated, so every other
// rate/dimensionless unit carries structured NOT_APPLICABLE currency and a PRESENT currency there is
// contradictory metadata on the authoritative headline.
[[nodiscard]] PricingResult rate_headline_result(const Unit unit,
                                                 ValueOrReason<Currency> currency) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  HeadlineValue headline;
  headline.semantics = HeadlineSemantics::kParRate;
  headline.value = 0.0;  // shape probe: this lane computes no price
  headline.unit = unit;
  headline.currency = std::move(currency);
  headline.sign_convention = ValueOrReason<std::string>::of(std::string(kUnresolvedToken));
  result.headline = ValueOrReason<HeadlineValue>::of(std::move(headline));
  // A successful headline that designates par_rate requires the supplemental metric PRESENT and exactly
  // equal to the headline value+unit (docs/33 line 956).
  result.par_rate = ValueOrReason<NumericWithUnit>::of(NumericWithUnit(0.0, unit));
  assign_derived_identities(result);
  return result;
}

TEST(PricingResultHeadlineCurrency, AcceptsStructuredNotApplicableCurrencyForARateHeadline) {
  const PricingResult fixture =
      rate_headline_result(Unit::kDecimalAnnual, ValueOrReason<Currency>::not_applicable());
  ASSERT_TRUE(fixture.headline.present);
  EXPECT_FALSE(fixture.headline.value.currency.present);

  const Refusal refusal = decode_pricing_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

TEST(PricingResultHeadlineCurrency, RefusesAPresentCurrencyOnADimensionlessHeadline) {
  PricingResult present_currency =
      rate_headline_result(Unit::kDecimalAnnual, ValueOrReason<Currency>::of(Currency::kUsd));
  ASSERT_TRUE(present_currency.headline.present);
  ASSERT_TRUE(present_currency.headline.value.currency.present);
  // Re-hashed after the change, so the refusal is the applicability rule and not the fingerprint fence.
  assign_derived_identities(present_currency);

  expect_refusal(decode_pricing_result(present_currency.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/headline/value/currency");
}

TEST(PricingResultHeadlineCurrency, KeepsPresentCurrencyForBothCurrencyDenominatedUnits) {
  // Scope guard: the forward branch and the CURRENCY_AMOUNT_PER_BASIS_POINT behaviour are preserved, so
  // the new inverse branch cannot be over-applied. CURRENCY_AMOUNT is exercised by the
  // enclosing-currency test above; the basis-point unit is reached through the headline semantics whose
  // supplemental metric owns it (a PRESENT par_rate requires DECIMAL_ANNUAL, docs/33 line 956 rules).
  PricingResult basis_point = successful_pricing_result(Currency::kUsd);
  HeadlineValue headline;
  headline.semantics = HeadlineSemantics::kAnnuityPvbp;
  headline.value = 0.0;  // shape probe: this lane computes no price
  headline.unit = Unit::kCurrencyAmountPerBasisPoint;
  headline.currency = ValueOrReason<Currency>::of(Currency::kUsd);
  headline.sign_convention = ValueOrReason<std::string>::of(std::string(kUnresolvedToken));
  basis_point.headline = ValueOrReason<HeadlineValue>::of(std::move(headline));
  basis_point.annuity_pvbp = ValueOrReason<NumericWithUnit>::of(
      NumericWithUnit(0.0, Unit::kCurrencyAmountPerBasisPoint));
  assign_derived_identities(basis_point);

  const Refusal refusal = decode_pricing_result(basis_point.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

// ---------------------------------------------------------------------------------------------
// Finding 6 - a FAILED headline is exactly UNAVAILABLE / PRICING_FAILED
// docs/33 section 12.3 line 953.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultFailedHeadline, AcceptsUnavailablePricingFailed) {
  const PricingResult fixture = failed_pricing_result(std::string(kPricingFailedReasonCode));
  ASSERT_FALSE(fixture.headline.present);
  EXPECT_EQ(fixture.headline.reason.category, ReasonCategory::kUnavailable);
  EXPECT_EQ(fixture.headline.reason.code, std::string(kPricingFailedReasonCode));

  const Refusal refusal = decode_pricing_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

TEST(PricingResultFailedHeadline, RefusesAnotherCodeUnderTheRequiredCategory) {
  PricingResult wrong_code = failed_pricing_result(std::string(kOtherUnavailableCode));
  // The category is still UNAVAILABLE, so the refusal can only be driven by the code.
  ASSERT_EQ(wrong_code.headline.reason.category, ReasonCategory::kUnavailable);
  ASSERT_NE(wrong_code.headline.reason.code, std::string(kPricingFailedReasonCode));
  assign_derived_identities(wrong_code);

  expect_refusal(decode_pricing_result(wrong_code.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState, "/headline/reason");
}

// ---------------------------------------------------------------------------------------------
// Finding 2 - `measures` is strictly ascending and duplicate-free
// docs/33 section 15.4 line 1232.
// ---------------------------------------------------------------------------------------------
TEST(RiskResultMeasureOrder, AcceptsTheCanonicalOrderAndKeepsAFingerprintStable) {
  const RiskResult fixture = risk_result_fixture();
  ASSERT_EQ(fixture.measures.size(), 3U);
  // The fixture's own arrangement is the documented canonical one: the all-DV01 prefix puts the
  // structured NOT_APPLICABLE `bump_target` first.
  EXPECT_EQ(fixture.measures[0].measure_id, RiskMeasureId::kDv01);
  EXPECT_FALSE(fixture.measures[0].bump_spec.bump_target.present);
  EXPECT_TRUE(fixture.measures[0].bucket_coordinate.present);
  EXPECT_EQ(fixture.measures[1].measure_id, RiskMeasureId::kDv01);
  EXPECT_TRUE(fixture.measures[1].bump_spec.bump_target.present);
  EXPECT_FALSE(fixture.measures[1].bucket_coordinate.present);

  const CanonicalValue document = fixture.to_canonical();
  EXPECT_EQ(fixture.replay.content_fingerprint, risk_result_content_fingerprint(fixture));

  const Refusal refusal = decode_risk_result(document);
  ASSERT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;

  const RiskResult decoded = RiskResult::from_canonical(document, "");
  EXPECT_EQ(decoded.replay.content_fingerprint, fixture.replay.content_fingerprint);
  EXPECT_EQ(decoded.to_canonical().dump(), document.dump());
}

TEST(RiskResultMeasureOrder, RefusesAnInvertedNotApplicableBeforePresentPair) {
  RiskResult inverted = risk_result_fixture();
  ASSERT_EQ(inverted.measures.size(), 3U);
  // Swap the two all-DV01 rows: this inverts exactly one component, the `bump_target` state
  // (NOT_APPLICABLE must sort before PRESENT), so the inversion cannot be excused by `measure_id`.
  std::swap(inverted.measures[0], inverted.measures[1]);
  ASSERT_TRUE(inverted.measures[0].bump_spec.bump_target.present);
  ASSERT_FALSE(inverted.measures[1].bump_spec.bump_target.present);
  assign_derived_identities(inverted);

  expect_refusal(decode_risk_result(inverted.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/measures/1");
}

TEST(RiskResultMeasureOrder, RefusesADuplicateMeasureKeyAfterRehash) {
  RiskResult duplicated = risk_result_fixture();
  const RiskMeasure duplicate = duplicated.measures[1];
  duplicated.measures.insert(duplicated.measures.begin() + 2, duplicate);
  ASSERT_EQ(duplicated.measures.size(), 4U);
  // Byte-identical keys: same measure_id, same canonical bump_target, same canonical bucket.
  ASSERT_EQ(duplicated.measures[1].to_canonical().dump(),
            duplicated.measures[2].to_canonical().dump());
  assign_derived_identities(duplicated);

  const Refusal refusal = decode_risk_result(duplicated.to_canonical());
  ASSERT_TRUE(refusal.refused);
  EXPECT_EQ(refusal.kind, ContractViolationKind::kInvariantViolation) << refusal.message;
  EXPECT_EQ(refusal.pointer, "/measures/2");
  EXPECT_NE(refusal.message.find("duplicate"), std::string::npos)
      << "the duplicate-key rule, not the ordering rule, must have refused: " << refusal.message;
}

// ---------------------------------------------------------------------------------------------
// Finding 3 - a VOL_NODE bucket names the consumed VolatilityInput
// docs/33 section 13.2 line 1028.
// ---------------------------------------------------------------------------------------------
TEST(RiskResultMeasureOrder, OrdersDifferentMeasuresByTheirCanonicalEnumToken) {
  // The tuple's `measure_id` component is compared through its canonical enum token. docs/33 line 1232
  // leaves that component unqualified while naming the two structured siblings "canonical ...
  // serialization", and the sibling tuple of the same paragraph defines its enum component as "the
  // PRESENT canonical enum token" (line 1226); the contract publishes a declaration order inline only
  // where it means one (line 1229, strikes). "DELTA" therefore sorts before "DV01" even though DV01
  // precedes DELTA in the section 13.2 vocabulary listing, so the token-ordered document is accepted
  // and the vocabulary-ordered one is refused. This pins the convention that CurveSet and FixingStore
  // ordering use as well, so one canonical order applies to every fingerprint-relevant array.
  RiskResult canonical = risk_result_fixture();
  canonical.measures.clear();
  RiskMeasure delta = parallel_measure();
  delta.measure_id = RiskMeasureId::kDelta;
  RiskMeasure dv01 = parallel_measure();
  dv01.measure_id = RiskMeasureId::kDv01;
  canonical.measures.push_back(std::move(delta));
  canonical.measures.push_back(std::move(dv01));
  // The identity remains NOT_APPLICABLE for ModelInput, as required by all V1 kernel invocations.
  canonical.inputs_identity.model_input_id = ValueOrReason<std::string>::not_applicable();
  assign_derived_identities(canonical);

  const Refusal accepted = decode_risk_result(canonical.to_canonical());
  ASSERT_FALSE(accepted.refused) << std::string(to_token(accepted.kind)) << " at " << accepted.pointer
                                 << ": " << accepted.message;

  // The two rows differ only in `measure_id`, so the inversion cannot be excused by any other component.
  RiskResult inverted = canonical;
  std::swap(inverted.measures[0], inverted.measures[1]);
  assign_derived_identities(inverted);
  expect_refusal(decode_risk_result(inverted.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/measures/1");
}

TEST(RiskResultVolNodeBucket, AcceptsTheConsumedVolatilityInputId) {
  const RiskResult fixture = risk_result_fixture();
  ASSERT_EQ(fixture.measures.size(), 3U);
  const auto& coordinate =
      std::get<VolNodeBucketCoordinate>(fixture.measures[2].bucket_coordinate.value.body);
  ASSERT_TRUE(fixture.inputs_identity.volatility_input_id.present);
  EXPECT_EQ(coordinate.volatility_input_id, fixture.inputs_identity.volatility_input_id.value);

  const Refusal refusal = decode_risk_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

TEST(RiskResultVolNodeBucket, RefusesAnotherVolatilityInputIdAfterRehash) {
  RiskResult mismatched = risk_result_fixture();
  auto& coordinate =
      std::get<VolNodeBucketCoordinate>(mismatched.measures[2].bucket_coordinate.value.body);
  coordinate.volatility_input_id = std::string(kFixtureOtherVolatilityInputId);
  ASSERT_NE(coordinate.volatility_input_id, mismatched.inputs_identity.volatility_input_id.value);
  assign_derived_identities(mismatched);

  expect_refusal(decode_risk_result(mismatched.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/measures/2/bucket_coordinate");
}

TEST(RiskResultVolNodeBucket, RefusesAnInapplicableCoordinateWhenNoInputIsConsumed) {
  RiskResult absent = risk_result_fixture();
  absent.inputs_identity.volatility_input_id = ValueOrReason<std::string>::not_applicable();
  assign_derived_identities(absent);

  expect_refusal(decode_risk_result(absent.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/measures/2/bucket_coordinate");
}

// ---------------------------------------------------------------------------------------------
// V1 MODEL_PARAM is a reserved DTO shape, but no valid V1 kernel can supply a ModelInput.
// ---------------------------------------------------------------------------------------------
TEST(RiskResultModelParameterBucket, RefusesAnOtherwiseMatchingModelParameterResult) {
  RiskResult result = risk_result_fixture();
  result.measures.insert(result.measures.begin() + 2,
                         model_parameter_measure(std::string(kFixtureModelInputId)));
  result.inputs_identity.model_input_id =
      ValueOrReason<std::string>::of(std::string(kFixtureModelInputId));
  assign_derived_identities(result);
  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/measures/2/bucket_coordinate");
}

TEST(RiskResultModelParameterBucket, RefusesModelParameterEvenWhenModelIdentityIsNotApplicable) {
  RiskResult result = risk_result_fixture();
  result.measures.insert(result.measures.begin() + 2,
                         model_parameter_measure(std::string(kFixtureModelInputId)));
  assign_derived_identities(result);
  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/measures/2/bucket_coordinate");
}

TEST(RiskResultModelParameterBucket, RefusesAFabricatedModelInputWithoutAnyModelMeasure) {
  RiskResult result = risk_result_fixture();
  result.inputs_identity.model_input_id =
      ValueOrReason<std::string>::of(std::string(kFixtureOtherModelInputId));
  assign_derived_identities(result);
  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/inputs_identity/model_input_id");
}

// ---------------------------------------------------------------------------------------------
// N3 - the eleven section 16 error codes are the only machine-dispatchable `errors` tokens
// docs/33 section 16 (lines 1257-1271) + include/shiori_rates/dto/error.hpp channel 1.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultErrorVocabulary, AcceptsEveryApprovedV1ErrorToken) {
  for (const RatesErrorCode code : kApprovedErrorCodes) {
    PricingResult result = failed_pricing_result(std::string(kPricingFailedReasonCode));
    ASSERT_FALSE(result.errors.empty());
    result.errors[0].code = std::string(to_token(code));
    assign_derived_identities(result);

    const Refusal refusal = decode_pricing_result(result.to_canonical());
    EXPECT_FALSE(refusal.refused) << to_token(code) << " refused at " << refusal.pointer << ": "
                                  << refusal.message;
  }
}

TEST(PricingResultErrorVocabulary, RefusesAnUnapprovedErrorCodeAfterRehash) {
  PricingResult result = failed_pricing_result(std::string(kPricingFailedReasonCode));
  ASSERT_FALSE(result.errors.empty());
  result.errors[0].code = std::string(kUnapprovedErrorCode);
  assign_derived_identities(result);

  const Refusal refusal = decode_pricing_result(result.to_canonical());
  expect_refusal(refusal, ContractViolationKind::kUnknownEnumToken, "/errors/0/code");
  // The refusal must name the closed vocabulary so a producer learns which table it must draw from.
  EXPECT_NE(refusal.message.find("closed"), std::string::npos) << refusal.message;
  EXPECT_NE(refusal.message.find("vocabulary"), std::string::npos) << refusal.message;
}

TEST(RiskResultErrorVocabulary, AcceptsEveryApprovedV1ErrorToken) {
  for (const RatesErrorCode code : kApprovedErrorCodes) {
    const RiskResult result = failed_risk_result(std::string(to_token(code)));
    const Refusal refusal = decode_risk_result(result.to_canonical());
    EXPECT_FALSE(refusal.refused) << to_token(code) << " refused at " << refusal.pointer << ": "
                                  << refusal.message;
  }
}

TEST(RiskResultErrorVocabulary, RefusesAnUnapprovedErrorCodeAfterRehash) {
  RiskResult result = failed_risk_result(std::string(kUnapprovedErrorCode));
  ASSERT_FALSE(result.errors.empty());
  assign_derived_identities(result);

  const Refusal refusal = decode_risk_result(result.to_canonical());
  expect_refusal(refusal, ContractViolationKind::kUnknownEnumToken, "/errors/0/code");
  EXPECT_NE(refusal.message.find("vocabulary"), std::string::npos) << refusal.message;
}

TEST(ResultErrorVocabulary, LeavesTheOpenWarningVocabularyUnvalidated) {
  // DATA_QUALITY is a section 16 example warning, not one of the eleven error codes: if the closed
  // check ever reached `warnings`, this accepted document would be refused. Both result types must keep
  // the warning channel independent of the error channel.
  PricingResult pricing = successful_pricing_result(Currency::kUsd);
  pricing.status = PricingStatus::kSuccessWithWarnings;
  DiagnosticRecord pricing_warning;
  pricing_warning.code = std::string(kFixtureWarningCode);
  pricing_warning.message = "SYNTHETIC CONTRACT FIXTURE WARNING RECORD";
  pricing.warnings.push_back(std::move(pricing_warning));
  assign_derived_identities(pricing);
  const Refusal pricing_refusal = decode_pricing_result(pricing.to_canonical());
  EXPECT_FALSE(pricing_refusal.refused) << pricing_refusal.pointer << ": " << pricing_refusal.message;

  RiskResult risk = risk_result_fixture();
  risk.status = PricingStatus::kSuccessWithWarnings;
  DiagnosticRecord risk_warning;
  risk_warning.code = std::string(kFixtureWarningCode);
  risk_warning.message = "SYNTHETIC CONTRACT FIXTURE WARNING RECORD";
  risk.warnings.push_back(std::move(risk_warning));
  assign_derived_identities(risk);
  const Refusal risk_refusal = decode_risk_result(risk.to_canonical());
  EXPECT_FALSE(risk_refusal.refused) << risk_refusal.pointer << ": " << risk_refusal.message;
}

// ---------------------------------------------------------------------------------------------
// N4 - SUCCESS carries no warnings; SUCCESS_WITH_WARNINGS carries a non-empty warning list
// docs/33 section 16 (lines 1253-1254); one pair of tests per status and result type.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultStatusWarnings, AcceptsASuccessWithoutWarnings) {
  const PricingResult fixture = successful_pricing_result(Currency::kUsd);
  ASSERT_EQ(fixture.status, PricingStatus::kSuccess);
  ASSERT_TRUE(fixture.warnings.empty());

  const Refusal refusal = decode_pricing_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
}

TEST(PricingResultStatusWarnings, RefusesASuccessCarryingAWarningAfterRehash) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  DiagnosticRecord warning;
  warning.code = std::string(kFixtureWarningCode);
  warning.message = "SYNTHETIC CONTRACT FIXTURE WARNING RECORD";
  result.warnings.push_back(std::move(warning));
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/warnings");
}

TEST(PricingResultStatusWarnings, AcceptsSuccessWithWarningsCarryingAWarning) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.status = PricingStatus::kSuccessWithWarnings;
  DiagnosticRecord warning;
  warning.code = std::string(kFixtureWarningCode);
  warning.message = "SYNTHETIC CONTRACT FIXTURE WARNING RECORD";
  result.warnings.push_back(std::move(warning));
  assign_derived_identities(result);

  const Refusal refusal = decode_pricing_result(result.to_canonical());
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
}

TEST(PricingResultStatusWarnings, RefusesSuccessWithWarningsAndNoWarningAfterRehash) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.status = PricingStatus::kSuccessWithWarnings;
  ASSERT_TRUE(result.warnings.empty());
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/warnings");
}

TEST(RiskResultStatusWarnings, AcceptsASuccessWithoutWarnings) {
  const RiskResult fixture = risk_result_fixture();
  ASSERT_EQ(fixture.status, PricingStatus::kSuccess);
  ASSERT_TRUE(fixture.warnings.empty());

  const Refusal refusal = decode_risk_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
}

TEST(RiskResultStatusWarnings, RefusesASuccessCarryingAWarningAfterRehash) {
  RiskResult result = risk_result_fixture();
  DiagnosticRecord warning;
  warning.code = std::string(kFixtureWarningCode);
  warning.message = "SYNTHETIC CONTRACT FIXTURE WARNING RECORD";
  result.warnings.push_back(std::move(warning));
  assign_derived_identities(result);

  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/warnings");
}

TEST(RiskResultStatusWarnings, AcceptsSuccessWithWarningsCarryingAWarning) {
  RiskResult result = risk_result_fixture();
  result.status = PricingStatus::kSuccessWithWarnings;
  DiagnosticRecord warning;
  warning.code = std::string(kFixtureWarningCode);
  warning.message = "SYNTHETIC CONTRACT FIXTURE WARNING RECORD";
  result.warnings.push_back(std::move(warning));
  assign_derived_identities(result);

  const Refusal refusal = decode_risk_result(result.to_canonical());
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
}

TEST(RiskResultStatusWarnings, RefusesSuccessWithWarningsAndNoWarningAfterRehash) {
  RiskResult result = risk_result_fixture();
  result.status = PricingStatus::kSuccessWithWarnings;
  ASSERT_TRUE(result.warnings.empty());
  assign_derived_identities(result);

  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/warnings");
}

// ---------------------------------------------------------------------------------------------
// N5 - a PRESENT_VALUE headline and the designated PV agree on sign semantics
// docs/33 section 12.3 line 956 ("... exactly equal the headline value+unit (and currency/sign
// semantics where applicable); disagreement fails closed").
// ---------------------------------------------------------------------------------------------
TEST(PricingResultHeadlinePvSignConvention, AcceptsTheMatchingSignConvention) {
  const PricingResult fixture = successful_pricing_result(Currency::kUsd);
  ASSERT_TRUE(fixture.headline.present);
  ASSERT_EQ(fixture.headline.value.semantics, HeadlineSemantics::kPresentValue);
  ASSERT_TRUE(fixture.pv.present);
  ASSERT_TRUE(fixture.headline.value.sign_convention.present);
  ASSERT_TRUE(fixture.pv_sign_convention.present);
  EXPECT_EQ(fixture.headline.value.sign_convention.value, fixture.pv_sign_convention.value);

  const Refusal refusal = decode_pricing_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
}

TEST(PricingResultHeadlinePvSignConvention, RefusesADifferentSignConventionTokenAfterRehash) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.headline.value.sign_convention =
      ValueOrReason<std::string>::of(std::string(kOtherSignConventionToken));
  // Same PRESENT state on both sides; only the carried token differs, so the refusal cannot be driven
  // by the union state.
  ASSERT_TRUE(result.pv_sign_convention.present);
  ASSERT_NE(result.headline.value.sign_convention.value, result.pv_sign_convention.value);
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/pv_sign_convention");
}

TEST(PricingResultHeadlinePvSignConvention, RefusesAMismatchedUnionStateAfterRehash) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  // The PV is PRESENT, so the existing rule already forces PRESENT `pv_sign_convention`; the headline
  // carries the structured NOT_APPLICABLE state instead, so only the state-agreement rule refuses this.
  result.headline.value.sign_convention = ValueOrReason<std::string>::not_applicable();
  ASSERT_TRUE(result.pv_sign_convention.present);
  ASSERT_FALSE(result.headline.value.sign_convention.present);
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/pv_sign_convention");
}

TEST(PricingResultHeadlinePvSignConvention, DoesNotApplyToANonPvDesignation) {
  // The rule is scoped to the headline that designates the supplemental PV. A PAR_RATE headline leaves
  // `pv` structured NOT_APPLICABLE, so the headline's own sign convention is not compared against the
  // unused `pv_sign_convention` - even though the two differ here.
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.headline.value.semantics = HeadlineSemantics::kParRate;
  result.headline.value.unit = Unit::kDecimalAnnual;
  result.headline.value.currency = ValueOrReason<Currency>::not_applicable();
  result.pv = ValueOrReason<NumericWithUnit>::not_applicable();
  result.pv_sign_convention = ValueOrReason<std::string>::not_applicable();
  result.par_rate = ValueOrReason<NumericWithUnit>::of(NumericWithUnit(0.0, Unit::kDecimalAnnual));
  ASSERT_TRUE(result.headline.value.sign_convention.present);
  ASSERT_FALSE(result.pv_sign_convention.present);
  assign_derived_identities(result);

  const Refusal refusal = decode_pricing_result(result.to_canonical());
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
}

// ---------------------------------------------------------------------------------------------
// Finding 6b - a non-currency headline carries the structured NOT_APPLICABLE *category*
// docs/33 section 12.3 lines 947 and 955.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultHeadlineCurrency, RefusesAGenericReasonCategoryOnADimensionlessHeadline) {
  // Line 955 names the state itself ("dimensionless/rate headlines use structured NOT_APPLICABLE"), and
  // line 947 annotates the field the same way. A generic failure category is therefore not an
  // equivalent: each of the other four categories reports a different, untrue condition (a data outage,
  // missing market data, an unresolved methodology or a failed capture) for a currency that is simply
  // inapplicable to the unit.
  const ReasonCategory wrong_categories[] = {ReasonCategory::kUnavailable,
                                             ReasonCategory::kMissingMarketData,
                                             ReasonCategory::kUnresolvedMethodology,
                                             ReasonCategory::kFailedCapture};
  for (const ReasonCategory category : wrong_categories) {
    PricingResult result = rate_headline_result(
        Unit::kDecimalAnnual,
        ValueOrReason<Currency>::none(StructuredReason(category, "FIXTURE_CURRENCY_REASON")));
    ASSERT_TRUE(result.headline.present);
    ASSERT_FALSE(result.headline.value.currency.present)
        << "the fixture must carry NULL_WITH_REASON, not a value";
    // Re-hashed, so the refusal is the applicability rule and not the fingerprint fence.
    assign_derived_identities(result);

    const Refusal refusal = decode_pricing_result(result.to_canonical());
    EXPECT_TRUE(refusal.refused) << "a reason category must not stand in for NOT_APPLICABLE";
    EXPECT_EQ(std::string(to_token(refusal.kind)),
              std::string(to_token(ContractViolationKind::kInvalidTaggedUnionState)))
        << refusal.pointer << ": " << refusal.message;
    EXPECT_EQ(refusal.pointer, "/headline/value/currency") << refusal.message;
  }

  // Scope guard: the contract's own NOT_APPLICABLE state and the currency-denominated PRESENT state
  // remain accepted, so the rule is exactly the category of this one field and nothing wider.
  const Refusal not_applicable = decode_pricing_result(
      rate_headline_result(Unit::kDecimalAnnual, ValueOrReason<Currency>::not_applicable())
          .to_canonical());
  EXPECT_FALSE(not_applicable.refused) << not_applicable.pointer << ": " << not_applicable.message;
  const Refusal present = decode_pricing_result(
      successful_pricing_result(Currency::kUsd).to_canonical());
  EXPECT_FALSE(present.refused) << present.pointer << ": " << present.message;
}

// ---------------------------------------------------------------------------------------------
// Finding 3 - `curve_role_map.discount_curve_id` is a mandatory echo, never omitted or invented
// docs/33 section 12.2 line 926, section 12.4 line 962, section 5.1 line 309.
// ---------------------------------------------------------------------------------------------
namespace {
// The canonical locators below rewrite ONE member of the produced document and leave every sibling
// byte untouched, which is exactly the document a producer that re-hashes can hand to the decoder.
[[nodiscard]] CanonicalValue with_object_member(const CanonicalValue& object, const std::string& key,
                                                CanonicalValue replacement) {
  CanonicalMembers members;
  bool replaced = false;
  for (const auto& member : object.as_object().members) {
    if (member.first == key) {
      members.emplace_back(key, std::move(replacement));
      replaced = true;
    } else {
      members.push_back(member);
    }
  }
  if (!replaced) {
    members.emplace_back(key, std::move(replacement));
  }
  return CanonicalValue::make_object(std::move(members));
}

[[nodiscard]] CanonicalValue without_object_member(const CanonicalValue& object,
                                                   const std::string& key) {
  CanonicalMembers members;
  for (const auto& member : object.as_object().members) {
    if (member.first != key) {
      members.push_back(member);
    }
  }
  return CanonicalValue::make_object(std::move(members));
}

// Replaces `discount_curve_id` inside inputs_identity.curve_role_map with `replacement`, or removes the
// key entirely when `replacement` is nullopt.
[[nodiscard]] CanonicalValue with_discount_curve_id(const CanonicalValue& document,
                                                    const std::optional<CanonicalValue>& replacement) {
  const CanonicalValue* identity = document.find("inputs_identity");
  const CanonicalValue* role_map = identity->find("curve_role_map");
  const CanonicalValue mutated_role_map = replacement.has_value()
                                              ? with_object_member(*role_map, "discount_curve_id",
                                                                   *replacement)
                                              : without_object_member(*role_map, "discount_curve_id");
  return with_object_member(document, "inputs_identity",
                            with_object_member(*identity, "curve_role_map", mutated_role_map));
}
}  // namespace

TEST(PricingResultCurveRoleMap, AcceptsTheExactEchoOfTheMandatoryDiscountSelection) {
  const PricingResult fixture = successful_pricing_result(Currency::kUsd);
  ASSERT_TRUE(fixture.inputs_identity.curve_role_map.discount_curve_id.has_value());
  ASSERT_FALSE(fixture.inputs_identity.curve_role_map.discount_curve_id->empty());

  const CanonicalValue document = fixture.to_canonical();
  const Refusal refusal = decode_pricing_result(document);
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;

  const PricingResult decoded = PricingResult::from_canonical(document, "");
  EXPECT_EQ(decoded.inputs_identity.curve_role_map.discount_curve_id,
            fixture.inputs_identity.curve_role_map.discount_curve_id);
  EXPECT_EQ(decoded.inputs_identity.curve_role_map.forecast_curve_by_index,
            fixture.inputs_identity.curve_role_map.forecast_curve_by_index);
  EXPECT_EQ(decoded.to_canonical().dump(), document.dump());
}

TEST(PricingResultCurveRoleMap, RefusesAnAbsentDiscountCurveIdEvenWhenRehashed) {
  // Section 12.4 line 962: a result that cannot name every PRESENT kernel input is not replayable and
  // fails validation. The kernel `curve_selection.discount_curve_id` is mandatory (section 5.1 line
  // 309), so an omitted key is a missing required field reported on the curve map itself - the
  // fingerprint fence cannot carry this rule because the document's fingerprint is recomputable.
  const CanonicalValue document =
      with_discount_curve_id(successful_pricing_result(Currency::kUsd).to_canonical(), std::nullopt);
  expect_refusal(decode_pricing_result(document), ContractViolationKind::kMissingRequiredField,
                 "/inputs_identity/curve_role_map/discount_curve_id");
}

TEST(PricingResultCurveRoleMap, RefusesANullOrEmptyDiscountCurveIdEvenWhenRehashed) {
  // A bare null is not the PRESENT selection either, and an empty string names no curve: the value is
  // required to be the exact non-empty echo of the consumed selection, so no default is substituted.
  const CanonicalValue null_document = with_discount_curve_id(
      successful_pricing_result(Currency::kUsd).to_canonical(), CanonicalValue(nullptr));
  expect_refusal(decode_pricing_result(null_document), ContractViolationKind::kWrongType,
                 "/inputs_identity/curve_role_map/discount_curve_id");

  const CanonicalValue empty_document = with_discount_curve_id(
      successful_pricing_result(Currency::kUsd).to_canonical(),
      CanonicalValue(std::string()));
  expect_refusal(decode_pricing_result(empty_document), ContractViolationKind::kInvariantViolation,
                 "/inputs_identity/curve_role_map/discount_curve_id");
}

TEST(RiskResultCurveRoleMap, RefusesAMissingDiscountCurveIdEvenWhenRehashed) {
  // The same identity rule applies to the risk result (section 13.2 line 1000: "exact echo of
  // RatesKernelInput.curve_selection"); the risk measure loop reads the same member.
  const CanonicalValue document =
      with_discount_curve_id(risk_result_fixture().to_canonical(), std::nullopt);
  expect_refusal(decode_risk_result(document), ContractViolationKind::kMissingRequiredField,
                 "/inputs_identity/curve_role_map/discount_curve_id");
}

// ---------------------------------------------------------------------------------------------
// The result echo cannot name a convention set or a forecast selection no valid
// RATES_KERNEL_INPUT_V1 could have consumed: `convention_set_id` is decoded through the closed
// ConventionSetId vocabulary (docs/33 line 932, "from ResolvedSwap provenance"), and the forecast
// key set must equal {USD_SOFR}, the single floating index USD_SOFR_OIS_V1 denotes (docs/32 section 6)
// and the kernel's curve_selection requires (docs/33 section 5.1). Every negative fixture is
// re-hashed so the refusal comes from the rule under test, never from the fingerprint fence.
// ---------------------------------------------------------------------------------------------
namespace {
constexpr std::string_view kFixtureUnknownConventionSetId = "SYNTHETIC_NOT_A_CONVENTION_SET";
constexpr std::string_view kFixtureOtherForecastCurveId = "SYNTHETIC_CONTRACT_FIXTURE_FORECAST_CURVE_2";

void set_forecast_selection(InputsIdentity& identity,
                            std::map<std::string, std::string> forecast_curve_by_index) {
  identity.curve_role_map.forecast_curve_by_index = std::move(forecast_curve_by_index);
}

[[nodiscard]] std::string usd_sofr_key() { return std::string(enum_to_token(FloatingIndex::kUsdSofr)); }
[[nodiscard]] std::string eur_estr_key() { return std::string(enum_to_token(FloatingIndex::kEurEstr)); }

void expect_both_results_refuse(const std::function<void(InputsIdentity&)>& mutate,
                                ContractViolationKind kind, const std::string& pointer) {
  PricingResult pricing = successful_pricing_result(Currency::kUsd);
  mutate(pricing.inputs_identity);
  assign_derived_identities(pricing);
  ASSERT_EQ(pricing.replay.content_fingerprint, pricing_result_content_fingerprint(pricing));
  expect_refusal(decode_pricing_result(pricing.to_canonical()), kind, pointer);

  RiskResult risk = risk_result_fixture();
  mutate(risk.inputs_identity);
  assign_derived_identities(risk);
  ASSERT_EQ(risk.replay.content_fingerprint, risk_result_content_fingerprint(risk));
  expect_refusal(decode_risk_result(risk.to_canonical()), kind, pointer);
}
}  // namespace

TEST(ResultInputsIdentityKernelEcho, AcceptsTheV1ConventionSetAndTheExactForecastSelection) {
  const PricingResult pricing = successful_pricing_result(Currency::kUsd);
  const RiskResult risk = risk_result_fixture();
  for (const InputsIdentity* identity : {&pricing.inputs_identity, &risk.inputs_identity}) {
    EXPECT_EQ(identity->convention_set_id,
              std::string(enum_to_token(ConventionSetId::kUsdSofrOisV1)));
    ASSERT_EQ(identity->curve_role_map.forecast_curve_by_index.size(), 1U);
    EXPECT_EQ(identity->curve_role_map.forecast_curve_by_index.count(usd_sofr_key()), 1U);
  }

  const CanonicalValue pricing_document = pricing.to_canonical();
  const Refusal pricing_refusal = decode_pricing_result(pricing_document);
  EXPECT_FALSE(pricing_refusal.refused) << pricing_refusal.pointer << ": " << pricing_refusal.message;
  EXPECT_EQ(PricingResult::from_canonical(pricing_document, "").to_canonical().dump(),
            pricing_document.dump());

  const CanonicalValue risk_document = risk.to_canonical();
  const Refusal risk_refusal = decode_risk_result(risk_document);
  EXPECT_FALSE(risk_refusal.refused) << risk_refusal.pointer << ": " << risk_refusal.message;
  EXPECT_EQ(RiskResult::from_canonical(risk_document, "").to_canonical().dump(),
            risk_document.dump());
}

TEST(ResultInputsIdentityKernelEcho, RefusesAnUnknownConventionSetAfterRehash) {
  expect_both_results_refuse(
      [](InputsIdentity& identity) {
        identity.convention_set_id = std::string(kFixtureUnknownConventionSetId);
      },
      ContractViolationKind::kUnknownEnumToken, "/inputs_identity/convention_set_id");
}

TEST(ResultInputsIdentityKernelEcho, RefusesAnEmptyForecastSelectionAfterRehash) {
  expect_both_results_refuse(
      [](InputsIdentity& identity) { set_forecast_selection(identity, {}); },
      ContractViolationKind::kMissingRequiredField,
      "/inputs_identity/curve_role_map/forecast_curve_by_index/" + usd_sofr_key());
}

TEST(ResultInputsIdentityKernelEcho, RefusesAForecastSelectionForAnotherIndexOnlyAfterRehash) {
  expect_both_results_refuse(
      [](InputsIdentity& identity) {
        set_forecast_selection(identity, {{eur_estr_key(), std::string(kFixtureForecastCurveId)}});
      },
      ContractViolationKind::kMissingRequiredField,
      "/inputs_identity/curve_role_map/forecast_curve_by_index/" + usd_sofr_key());
}

TEST(ResultInputsIdentityKernelEcho, RefusesAnExtraUnconsumedForecastIndexAfterRehash) {
  expect_both_results_refuse(
      [](InputsIdentity& identity) {
        set_forecast_selection(identity,
                               {{usd_sofr_key(), std::string(kFixtureForecastCurveId)},
                                {eur_estr_key(), std::string(kFixtureOtherForecastCurveId)}});
      },
      ContractViolationKind::kInvariantViolation,
      "/inputs_identity/curve_role_map/forecast_curve_by_index");
}

// ---------------------------------------------------------------------------------------------
// docs/33 section 12.3: a PRESENT sign convention is the explicit token a signed value is read by, so a
// PRESENT empty string - on the headline, on pv_sign_convention or on a component reference - names no
// convention. The open vocabulary itself stays unvalidated; only the empty token is refused.
// ---------------------------------------------------------------------------------------------
namespace {
[[nodiscard]] ComponentPv fixture_component(std::string sign_convention_ref) {
  ComponentPv component;
  component.component_id = "SYNTHETIC_CONTRACT_FIXTURE_COMPONENT";
  component.value = 0.0;  // shape probe
  component.unit = Unit::kCurrencyAmount;
  component.sign_convention_ref = ValueOrReason<std::string>::of(std::move(sign_convention_ref));
  return component;
}

void expect_rehashed_pricing_refusal(PricingResult result, const std::string& pointer) {
  assign_derived_identities(result);
  ASSERT_EQ(result.replay.content_fingerprint, pricing_result_content_fingerprint(result));
  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kInvariantViolation, pointer);
}
}  // namespace

TEST(PricingResultSignConvention, AcceptsPresentNonEmptyTokensIncludingAComponentReference) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.component_pvs = ValueOrReason<std::vector<ComponentPv>>::of(
      {fixture_component(std::string(kUnresolvedToken))});
  assign_derived_identities(result);
  const CanonicalValue document = result.to_canonical();
  const Refusal refusal = decode_pricing_result(document);
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
  EXPECT_EQ(PricingResult::from_canonical(document, "").to_canonical().dump(), document.dump());
}

TEST(PricingResultSignConvention, RefusesEmptyHeadlineAndPvTokensEvenWhenTheyAgreeAfterRehash) {
  // Both empty and therefore equal: the headline/pv agreement rule alone would accept this pair.
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.headline.value.sign_convention = ValueOrReason<std::string>::of(std::string());
  result.pv_sign_convention = ValueOrReason<std::string>::of(std::string());
  expect_rehashed_pricing_refusal(result, "/headline/value/sign_convention/value");
}

TEST(PricingResultSignConvention, RefusesAnEmptyPvSignConventionAfterRehash) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.pv_sign_convention = ValueOrReason<std::string>::of(std::string());
  expect_rehashed_pricing_refusal(result, "/pv_sign_convention/value");
}

TEST(PricingResultSignConvention, RefusesAnEmptyComponentSignConventionReferenceAfterRehash) {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.component_pvs =
      ValueOrReason<std::vector<ComponentPv>>::of({fixture_component(std::string())});
  expect_rehashed_pricing_refusal(result, "/component_pvs/value/0/sign_convention_ref/value");
}

// ---------------------------------------------------------------------------------------------
// Finding 4 - the result input identity echoes the kernel applicability
// docs/33 section 5.1 lines 305-307, section 12.4 line 962, section 13.4 line 1051.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultInputsIdentityApplicability, RefusesAnOisIdentityClaimingTheDerivativeOnlyInputs) {
  // Section 5.1 line 306: for an OIS the volatility input, exercise terms, settlement terms and model
  // input are structured NULL_WITH_REASON(NOT_APPLICABLE). Line 1051 makes the result echo that state,
  // so an OIS result may not claim any of them as consumed - each is refused at its own pointer.
  struct OptionalInput {
    ValueOrReason<std::string> InputsIdentity::*field;
    const char* pointer;
  };
  const OptionalInput fields[] = {
      {&InputsIdentity::volatility_input_id, "/inputs_identity/volatility_input_id"},
      {&InputsIdentity::exercise_terms_id, "/inputs_identity/exercise_terms_id"},
      {&InputsIdentity::settlement_terms_id, "/inputs_identity/settlement_terms_id"},
      {&InputsIdentity::model_input_id, "/inputs_identity/model_input_id"}};
  for (const OptionalInput& input : fields) {
    PricingResult result = successful_pricing_result(Currency::kUsd);
    ASSERT_EQ(result.product_type, ProductType::kOis);
    result.inputs_identity.*input.field = ValueOrReason<std::string>::of(std::string("FIXTURE_ID"));
    assign_derived_identities(result);

    const Refusal refusal = decode_pricing_result(result.to_canonical());
    EXPECT_TRUE(refusal.refused) << input.pointer << " must not be claimable by an OIS result";
    EXPECT_EQ(std::string(to_token(refusal.kind)),
              std::string(to_token(ContractViolationKind::kInvalidTaggedUnionState)))
        << refusal.pointer << ": " << refusal.message;
    EXPECT_EQ(refusal.pointer, input.pointer) << refusal.message;
  }
}

TEST(PricingResultInputsIdentityApplicability, RefusesASwaptionIdentityOmittingAConsumedInput) {
  // Section 5.1 line 307: the Phase-2 swaption shape consumes a volatility input, exercise terms and
  // settlement terms, so a swaption result that reports them as not applicable cannot name every
  // PRESENT kernel input and is not replayable (line 962).
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.product_type = ProductType::kSwaption;
  ASSERT_FALSE(result.inputs_identity.volatility_input_id.present);
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/inputs_identity/volatility_input_id");
}

TEST(PricingResultInputsIdentityApplicability, RefusesAPresentModelInputOnASwaptionResult) {
  // The Phase-2 pricing path carries `model_input` as structured NOT_APPLICABLE (section 5.1 line 307),
  // and a pricing result consumes no ModelInput: there is no measure that could name one. A PRESENT
  // model identity is therefore the fabricated consumed-input claim line 1051 calls malformed.
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.product_type = ProductType::kSwaption;
  result.inputs_identity.volatility_input_id =
      ValueOrReason<std::string>::of(std::string(kFixtureVolatilityInputId));
  result.inputs_identity.exercise_terms_id =
      ValueOrReason<std::string>::of(std::string(kFixtureExerciseTermsId));
  result.inputs_identity.settlement_terms_id =
      ValueOrReason<std::string>::of(std::string(kFixtureSettlementTermsId));
  result.inputs_identity.model_input_id =
      ValueOrReason<std::string>::of(std::string(kFixtureModelInputId));
  ASSERT_TRUE(result.inputs_identity.model_input_id.present);
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState, "/inputs_identity/model_input_id");
}

TEST(PricingResultInputsIdentityApplicability, AcceptsTheSwaptionShapeWithTheStructuredModelState) {
  // The contract-valid Phase-2 swaption identity: the three consumed payloads are PRESENT and the model
  // input stays structured NOT_APPLICABLE. This is the positive half of the two refusals above. The
  // top-level `product_id` names the DERIVATIVE, distinct from the underlying swap the identity echoes
  // (docs/33 lines 931/962), so this is also the accepted swaption side of the product-linkage rule.
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.product_type = ProductType::kSwaption;
  result.product_id = std::string(kFixtureSwaptionProductId);
  result.inputs_identity.volatility_input_id =
      ValueOrReason<std::string>::of(std::string(kFixtureVolatilityInputId));
  result.inputs_identity.exercise_terms_id =
      ValueOrReason<std::string>::of(std::string(kFixtureExerciseTermsId));
  result.inputs_identity.settlement_terms_id =
      ValueOrReason<std::string>::of(std::string(kFixtureSettlementTermsId));
  result.inputs_identity.model_input_id = ValueOrReason<std::string>::not_applicable();
  assign_derived_identities(result);

  const CanonicalValue document = result.to_canonical();
  const Refusal refusal = decode_pricing_result(document);
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
  const PricingResult decoded = PricingResult::from_canonical(document, "");
  EXPECT_TRUE(decoded.inputs_identity.volatility_input_id.present);
  EXPECT_TRUE(decoded.inputs_identity.exercise_terms_id.present);
  EXPECT_TRUE(decoded.inputs_identity.settlement_terms_id.present);
  EXPECT_FALSE(decoded.inputs_identity.model_input_id.present);
  EXPECT_NE(decoded.product_id, decoded.inputs_identity.resolved_swap_product_id);
}

TEST(RiskResultInputsIdentityApplicability, RefusesAPresentModelInputThatNoMeasureConsumes) {
  RiskResult result = risk_result_fixture();
  ASSERT_FALSE(result.inputs_identity.model_input_id.present);
  result.inputs_identity.model_input_id =
      ValueOrReason<std::string>::of(std::string(kFixtureModelInputId));
  assign_derived_identities(result);
  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState, "/inputs_identity/model_input_id");
}

TEST(RiskResultInputsIdentityApplicability, RefusesAnOisIdentityClaimingTheDerivativeOnlyInputs) {
  // The risk result echoes the same kernel applicability as the pricing result (section 13.4 line
  // 1051), so an OIS risk identity claiming a volatility input is refused at that field. The per-measure
  // VOL_NODE rule is satisfied by the fixture, which is why the refusal is the identity rule.
  RiskResult result = risk_result_fixture();
  result.product_type = ProductType::kOis;
  ASSERT_TRUE(result.inputs_identity.volatility_input_id.present);
  assign_derived_identities(result);

  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/inputs_identity/volatility_input_id");
}

TEST(RiskResultInputsIdentityApplicability, KeepsTheConsumedInputCoverageAndTheFailedShape) {
  // The approved V1 swaption shape may consume a volatility input but not a ModelInput;
  // a FAILED result is unvalued with the same structured NOT_APPLICABLE model state.
  const Refusal consumed = decode_risk_result(risk_result_fixture().to_canonical());
  EXPECT_FALSE(consumed.refused) << consumed.pointer << ": " << consumed.message;

  const RiskResult failed = failed_risk_result(std::string(kOtherUnavailableCode));
  ASSERT_TRUE(failed.measures.empty());
  ASSERT_FALSE(failed.inputs_identity.model_input_id.present);
  const Refusal failed_refusal = decode_risk_result(failed.to_canonical());
  EXPECT_FALSE(failed_refusal.refused) << failed_refusal.pointer << ": " << failed_refusal.message;
}

namespace {

// The contract-valid Phase-2 swaption pricing shape: the three consumed payloads PRESENT and the model
// input structured NOT_APPLICABLE (section 5.1 line 307). The top-level `product_id` is the DERIVATIVE id,
// so each linkage probe below changes exactly one id.
[[nodiscard]] PricingResult swaption_pricing_result() {
  PricingResult result = successful_pricing_result(Currency::kUsd);
  result.product_type = ProductType::kSwaption;
  result.product_id = std::string(kFixtureSwaptionProductId);
  result.inputs_identity.volatility_input_id =
      ValueOrReason<std::string>::of(std::string(kFixtureVolatilityInputId));
  result.inputs_identity.exercise_terms_id =
      ValueOrReason<std::string>::of(std::string(kFixtureExerciseTermsId));
  result.inputs_identity.settlement_terms_id =
      ValueOrReason<std::string>::of(std::string(kFixtureSettlementTermsId));
  result.inputs_identity.model_input_id = ValueOrReason<std::string>::not_applicable();
  return result;
}

// A FAILED, measure-free risk result moved to the OIS shape (section 5.1 line 306: the four optional
// inputs are structured NOT_APPLICABLE), with the caller owning `product_id`.
[[nodiscard]] RiskResult ois_failed_risk_result() {
  RiskResult result = failed_risk_result(std::string(kOtherUnavailableCode));
  result.product_type = ProductType::kOis;
  result.product_id = std::string(kFixtureProductId);
  result.inputs_identity.volatility_input_id = ValueOrReason<std::string>::not_applicable();
  result.inputs_identity.exercise_terms_id = ValueOrReason<std::string>::not_applicable();
  result.inputs_identity.settlement_terms_id = ValueOrReason<std::string>::not_applicable();
  return result;
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// Finding 4 - a calibration identity echoes a consumed ModelInput, and only in that direction
// docs/33 lines 795/811, section 12.4 line 962, section 13.4 line 1051.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultInputsIdentityApplicability,
     RefusesAFabricatedCalibrationIdWithoutAConsumedModelInput) {
  // Lines 795/811 attach calibration evidence to a ModelInput (`calibration_ref` is PRESENT iff the
  // embedded calibration payload is PRESENT), and line 962 makes every optional result identity an exact
  // echo of the consumed kernel input. With no ModelInput consumed nothing can have consumed a
  // calibration result, so a PRESENT calibration id is precisely the fabricated consumed-input claim line
  // 1051 calls malformed: it is refused at its own pointer and never silently cleared to make the
  // document validate. No calibration objective, optimizer or tolerance is involved.
  PricingResult result = successful_pricing_result(Currency::kUsd);
  ASSERT_FALSE(result.inputs_identity.model_input_id.present);
  result.inputs_identity.calibration_result_id =
      ValueOrReason<std::string>::of(std::string("SYNTHETIC_CONTRACT_FIXTURE_CALIBRATION"));
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/inputs_identity/calibration_result_id");

  // The same field carrying a NON-NOT_APPLICABLE structured reason is refused at the same pointer: the
  // echo rule requires the structured NOT_APPLICABLE state, not merely the absence of a value.
  PricingResult wrong_category = successful_pricing_result(Currency::kUsd);
  wrong_category.inputs_identity.calibration_result_id = ValueOrReason<std::string>::none(
      StructuredReason(ReasonCategory::kUnavailable, "FIXTURE_CALIBRATION_UNAVAILABLE"));
  assign_derived_identities(wrong_category);
  expect_refusal(decode_pricing_result(wrong_category.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/inputs_identity/calibration_result_id");
}

TEST(RiskResultInputsIdentityApplicability,
     RefusesAFabricatedCalibrationIdWithoutAConsumedModelInput) {
  // The same rule in the risk lane (line 1051). The fixture FAILED and carries no measure, so nothing
  // consumes a ModelInput and a PRESENT calibration id cannot echo any consumed object.
  RiskResult result = failed_risk_result(std::string(kOtherUnavailableCode));
  ASSERT_FALSE(result.inputs_identity.model_input_id.present);
  result.inputs_identity.calibration_result_id =
      ValueOrReason<std::string>::of(std::string("SYNTHETIC_CONTRACT_FIXTURE_CALIBRATION"));
  assign_derived_identities(result);

  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvalidTaggedUnionState,
                 "/inputs_identity/calibration_result_id");
}

TEST(InputsIdentityApplicability, KeepsModelAndCalibrationIdsNotApplicableForCurrentKernelV1) {
  const RiskResult result = risk_result_fixture();
  ASSERT_FALSE(result.inputs_identity.model_input_id.present);
  ASSERT_FALSE(result.inputs_identity.calibration_result_id.present);
  const Refusal refusal = decode_risk_result(result.to_canonical());
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
}

// ---------------------------------------------------------------------------------------------
// Finding 5 - the result product identity agrees with the embedded ResolvedSwap identity
// docs/33 section 12.4 line 962, section 13.4 line 1051, section 5.1 line 303.
// ---------------------------------------------------------------------------------------------
TEST(PricingResultProductIdentityLinkage, RefusesAnOisResultWhoseProductIdIsNotTheResolvedSwapId) {
  // Line 962: for a vanilla swap the top-level `product_id` IS the embedded ResolvedSwap's product id, so
  // an OIS result that names anything else cannot echo any consumable request. The refusal is
  // `kIdentityMismatch` at the result's own `product_id`, and the accepted OIS fixture in the rest of this
  // suite is the positive twin that isolates the id relationship.
  PricingResult result = successful_pricing_result(Currency::kUsd);
  ASSERT_EQ(result.product_type, ProductType::kOis);
  ASSERT_EQ(result.product_id, result.inputs_identity.resolved_swap_product_id);
  result.product_id = std::string(kFixtureSwaptionProductId);
  ASSERT_NE(result.product_id, result.inputs_identity.resolved_swap_product_id);
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/product_id");
}

TEST(PricingResultProductIdentityLinkage,
     RefusesASwaptionResultThatNamesTheUnderlyingSwapAsItsOwnProduct) {
  // Lines 931/962: for a derivative the top-level `product_id` identifies the DERIVATIVE while the
  // identity's `resolved_swap_product_id` identifies the underlying swap it exercises, so the two ids must
  // differ. Substituting the underlying id for the derivative is exactly the substitution section 5.1 line
  // 303 forbids.
  PricingResult result = swaption_pricing_result();
  ASSERT_NE(result.product_id, result.inputs_identity.resolved_swap_product_id);
  result.product_id = result.inputs_identity.resolved_swap_product_id;
  assign_derived_identities(result);

  expect_refusal(decode_pricing_result(result.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/product_id");
}

TEST(PricingResultProductIdentityLinkage, AcceptsADistinctDerivativeIdOverTheUnderlyingSwap) {
  // The positive half: a distinct derivative id over an explicitly named underlying swap is accepted, and
  // the underlying swap is still identified by the identity member (never dropped, never renamed).
  PricingResult result = swaption_pricing_result();
  assign_derived_identities(result);

  const CanonicalValue document = result.to_canonical();
  const Refusal refusal = decode_pricing_result(document);
  EXPECT_FALSE(refusal.refused) << refusal.pointer << ": " << refusal.message;
  const PricingResult decoded = PricingResult::from_canonical(document, "");
  EXPECT_EQ(decoded.product_type, ProductType::kSwaption);
  EXPECT_EQ(decoded.product_id, std::string(kFixtureSwaptionProductId));
  EXPECT_EQ(decoded.inputs_identity.resolved_swap_product_id, std::string(kFixtureProductId));
  EXPECT_NE(decoded.product_id, decoded.inputs_identity.resolved_swap_product_id);
}

TEST(RiskResultProductIdentityLinkage,
     AcceptsTheDerivativeIdOverTheUnderlyingSwapAndRefusesTheSwapIdItself) {
  // Line 1051 makes the risk lane identical: the swaption fixture identifies the derivative and echoes the
  // underlying swap, and stays accepted; renaming the result with the underlying swap's id is refused.
  const RiskResult fixture = risk_result_fixture();
  ASSERT_EQ(fixture.product_type, ProductType::kSwaption);
  ASSERT_NE(fixture.product_id, fixture.inputs_identity.resolved_swap_product_id);
  const Refusal accepted = decode_risk_result(fixture.to_canonical());
  EXPECT_FALSE(accepted.refused) << accepted.pointer << ": " << accepted.message;

  RiskResult renamed = fixture;
  renamed.product_id = fixture.inputs_identity.resolved_swap_product_id;
  assign_derived_identities(renamed);
  expect_refusal(decode_risk_result(renamed.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/product_id");
}

TEST(RiskResultProductIdentityLinkage, RefusesAnOisRiskResultWhoseProductIdIsNotTheResolvedSwapId) {
  RiskResult result = ois_failed_risk_result();
  ASSERT_EQ(result.product_id, result.inputs_identity.resolved_swap_product_id);
  result.product_id = std::string(kFixtureSwaptionProductId);
  assign_derived_identities(result);

  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/product_id");

  // Only the id was wrong: the same OIS document with the embedded swap id is accepted.
  RiskResult consistent = ois_failed_risk_result();
  assign_derived_identities(consistent);
  const Refusal accepted = decode_risk_result(consistent.to_canonical());
  EXPECT_FALSE(accepted.refused) << accepted.pointer << ": " << accepted.message;
}

namespace {

// The canonical `ValueOrReason<T>` PRESENT envelope around an already-built value node.
[[nodiscard]] CanonicalValue present_envelope(CanonicalValue value) {
  CanonicalMembers members;
  members.emplace_back("state", CanonicalValue(std::string("PRESENT")));
  members.emplace_back("value", std::move(value));
  return CanonicalValue::make_object(std::move(members));
}

[[nodiscard]] CanonicalValue numeric_with_unit_node(Unit unit, CanonicalValue value) {
  CanonicalMembers members;
  members.emplace_back("unit", CanonicalValue(std::string(enum_to_token(unit))));
  members.emplace_back("value", std::move(value));
  return CanonicalValue::make_object(std::move(members));
}

// Replaces one member of `measures[index]`.
[[nodiscard]] CanonicalValue with_measure_member(const CanonicalValue& document, std::size_t index,
                                                 const std::string& key, CanonicalValue replacement) {
  const auto& source_items = document.find("measures")->as_array().items;
  Shiori::rates::dto::CanonicalItems items;
  items.reserve(source_items.size());
  for (std::size_t position = 0; position < source_items.size(); ++position) {
    items.push_back(position == index ? with_object_member(source_items[position], key, replacement)
                                      : source_items[position]);
  }
  return with_object_member(document, "measures", CanonicalValue::make_array(std::move(items)));
}

// Replaces `measures[index].bump_spec.bump_size`.
[[nodiscard]] CanonicalValue with_bump_size(const CanonicalValue& document, std::size_t index,
                                            CanonicalValue replacement) {
  const CanonicalValue* measure = &document.find("measures")->as_array().items[index];
  return with_measure_member(
      document, index, "bump_spec",
      with_object_member(*measure->find("bump_spec"), "bump_size", std::move(replacement)));
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// The binary64 exactness rule applies to every double-valued DTO field, in both result lanes
// ---------------------------------------------------------------------------------------------
TEST(ResultNumericExactness, RefusesANonExactIntegerValueAtEachDoubleValuedFieldPointer) {
  // An integer JSON token is an exact canonical number form, and these fields store binary64: an integer
  // binary64 cannot hold exactly must be refused at the field's own pointer instead of being silently
  // rounded. The canonical form of an integral double is itself an integer token (the serializer prints
  // plain digits for a 21-digit-or-shorter magnitude), so this is a decode path the DTO's own round trip
  // uses - not a hypothetical input. Canonical-tree surgery keeps every other member self-consistent; the
  // field rule is checked while decoding, before the document fingerprint fence.
  const CanonicalValue not_exact = CanonicalValue(std::int64_t{9007199254740993LL});  // 2^53 + 1
  const CanonicalValue pricing = successful_pricing_result(Currency::kUsd).to_canonical();

  expect_refusal(decode_pricing_result(with_object_member(
                     pricing, "pv",
                     present_envelope(numeric_with_unit_node(Unit::kCurrencyAmount, not_exact)))),
                 ContractViolationKind::kInvariantViolation, "/pv/value/value");

  const CanonicalValue* headline_inner = pricing.find("headline")->find("value");
  expect_refusal(decode_pricing_result(with_object_member(
                     pricing, "headline",
                     present_envelope(with_object_member(*headline_inner, "value", not_exact)))),
                 ContractViolationKind::kInvariantViolation, "/headline/value/value");

  const CanonicalValue risk = risk_result_fixture().to_canonical();
  expect_refusal(decode_risk_result(with_measure_member(risk, 0, "value", not_exact)),
                 ContractViolationKind::kInvariantViolation, "/measures/0/value");
  expect_refusal(decode_risk_result(with_bump_size(risk, 0, not_exact)),
                 ContractViolationKind::kInvariantViolation, "/measures/0/bump_spec/bump_size");
}

TEST(ResultNumericExactness, KeepsAnExactlyRepresentableLargeIntegerAndLeavesTheFenceToTheFingerprint) {
  // The accepted twin of the refusals above: 2^53 + 2 IS exactly representable, and the DTO's own canonical
  // form of it is the integer token, so the exact integer path must decode exactly and round-trip
  // byte-identically. Nothing is rounded and no field is rewritten. The headline carries the same value
  // and unit as `pv` because section 12.3 requires the designated supplemental metric to agree with the
  // headline exactly, so only the numeric representation is under test here.
  PricingResult integral = successful_pricing_result(Currency::kUsd);
  integral.headline.value.value = 9007199254740994.0;
  integral.pv =
      ValueOrReason<NumericWithUnit>::of(NumericWithUnit(9007199254740994.0, Unit::kCurrencyAmount));
  assign_derived_identities(integral);

  const CanonicalValue document = integral.to_canonical();
  ASSERT_NE(document.dump().find("\"value\":9007199254740994"), std::string::npos)
      << "the fixture must exercise the integer-token decode path: " << document.dump();
  const Refusal accepted = decode_pricing_result(document);
  EXPECT_FALSE(accepted.refused) << accepted.pointer << ": " << accepted.message;
  const PricingResult decoded = PricingResult::from_canonical(document, "");
  ASSERT_TRUE(decoded.pv.present);
  EXPECT_EQ(decoded.pv.value.value, 9007199254740994.0);
  EXPECT_EQ(decoded.to_canonical().dump(), document.dump());

  // The same exact value placed in an otherwise unchanged document passes the FIELD rule and is caught
  // only by the fingerprint fence, which no longer describes the mutated document. The invoice and the
  // headline are moved together so the section 12.3 agreement rule is satisfied and nothing but the fence
  // can refuse this document: that is the proof the rule refuses non-exact integers specifically, not
  // "integer-valued fields".
  const CanonicalValue original = successful_pricing_result(Currency::kUsd).to_canonical();
  const CanonicalValue* original_headline_inner = original.find("headline")->find("value");
  const CanonicalValue exact_integer = CanonicalValue(std::int64_t{9007199254740994LL});
  const CanonicalValue exact_leaf = with_object_member(
      with_object_member(original, "pv",
                         present_envelope(
                             numeric_with_unit_node(Unit::kCurrencyAmount, exact_integer))),
      "headline",
      present_envelope(with_object_member(*original_headline_inner, "value", exact_integer)));
  const Refusal fence_only = decode_pricing_result(exact_leaf);
  ASSERT_TRUE(fence_only.refused);
  EXPECT_EQ(fence_only.kind, ContractViolationKind::kFingerprintMismatch)
      << fence_only.pointer << ": " << fence_only.message;
  EXPECT_EQ(fence_only.pointer, "/replay/content_fingerprint");
}

// ---------------------------------------------------------------------------------------------
// Final review: replay identity must never name an empty PRESENT input, and FAILED is not valued.
// ---------------------------------------------------------------------------------------------
TEST(ResultOptionalIdentity, RefusesEmptyPresentInputIdsInPricingAndRiskAfterRehash) {
  struct OptionalId {
    ValueOrReason<std::string> InputsIdentity::*field;
    const char* name;
  };
  const OptionalId fields[] = {
      {&InputsIdentity::volatility_input_id, "volatility_input_id"},
      {&InputsIdentity::exercise_terms_id, "exercise_terms_id"},
      {&InputsIdentity::settlement_terms_id, "settlement_terms_id"},
      {&InputsIdentity::model_input_id, "model_input_id"},
      {&InputsIdentity::calibration_result_id, "calibration_result_id"},
  };
  for (const OptionalId& input : fields) {
    const std::string pointer = "/inputs_identity/" + std::string(input.name) + "/value";

    PricingResult pricing = swaption_pricing_result();
    pricing.inputs_identity.*input.field = ValueOrReason<std::string>::of("");
    assign_derived_identities(pricing);
    expect_refusal(decode_pricing_result(pricing.to_canonical()),
                   ContractViolationKind::kInvariantViolation, pointer);

    RiskResult risk = risk_result_fixture();
    risk.inputs_identity.*input.field = ValueOrReason<std::string>::of("");
    assign_derived_identities(risk);
    expect_refusal(decode_risk_result(risk.to_canonical()),
                   ContractViolationKind::kInvariantViolation, pointer);
  }
}

TEST(ResultOptionalIdentity, PreservesValidNonemptyAndNotApplicableInputIds) {
  PricingResult swaption = swaption_pricing_result();
  // This builder sets the synthetic derivative shape; as with other positive tests, recompute its
  // fingerprint after assigning the final swaption identity before testing valid decode.
  assign_derived_identities(swaption);
  const Refusal pricing = decode_pricing_result(swaption.to_canonical());
  EXPECT_FALSE(pricing.refused) << pricing.pointer << ": " << pricing.message;
  const Refusal risk = decode_risk_result(risk_result_fixture().to_canonical());
  EXPECT_FALSE(risk.refused) << risk.pointer << ": " << risk.message;
  const Refusal ois = decode_pricing_result(successful_pricing_result(Currency::kUsd).to_canonical());
  EXPECT_FALSE(ois.refused) << ois.pointer << ": " << ois.message;
}

TEST(RiskResultFailedStatus, RefusesValuedMeasuresDespiteARehashedFailedDocument) {
  RiskResult result = failed_risk_result(std::string(kOtherUnavailableCode));
  ASSERT_TRUE(result.measures.empty());
  ASSERT_FALSE(result.errors.empty());
  result.measures.push_back(bucketed_tenor_measure());
  assign_derived_identities(result);
  expect_refusal(decode_risk_result(result.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/measures");
}

TEST(RiskResultFailedStatus, KeepsUnvaluedFailureAndValuedSuccessValid) {
  const RiskResult failed = failed_risk_result(std::string(kOtherUnavailableCode));
  ASSERT_TRUE(failed.measures.empty());
  EXPECT_FALSE(decode_risk_result(failed.to_canonical()).refused);
  const RiskResult success = risk_result_fixture();
  ASSERT_FALSE(success.measures.empty());
  EXPECT_FALSE(decode_risk_result(success.to_canonical()).refused);
}

TEST(PricingResultFailedStatus, RefusesAllPresentSupplementalValuesAfterRehash) {
  PricingResult pv = failed_pricing_result(std::string(kPricingFailedReasonCode));
  pv.pv = ValueOrReason<NumericWithUnit>::of(NumericWithUnit(0.0, Unit::kCurrencyAmount));
  assign_derived_identities(pv);
  expect_refusal(decode_pricing_result(pv.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/pv");

  PricingResult sign = failed_pricing_result(std::string(kPricingFailedReasonCode));
  sign.pv_sign_convention = ValueOrReason<std::string>::of(std::string(kUnresolvedToken));
  assign_derived_identities(sign);
  expect_refusal(decode_pricing_result(sign.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/pv_sign_convention");

  PricingResult components = failed_pricing_result(std::string(kPricingFailedReasonCode));
  components.component_pvs = ValueOrReason<std::vector<ComponentPv>>::of({});
  assign_derived_identities(components);
  expect_refusal(decode_pricing_result(components.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/component_pvs");

  PricingResult annuity = failed_pricing_result(std::string(kPricingFailedReasonCode));
  annuity.annuity_pvbp =
      ValueOrReason<NumericWithUnit>::of(NumericWithUnit(0.0, Unit::kCurrencyAmountPerBasisPoint));
  assign_derived_identities(annuity);
  expect_refusal(decode_pricing_result(annuity.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/annuity_pvbp");

  PricingResult rate = failed_pricing_result(std::string(kPricingFailedReasonCode));
  rate.par_rate = ValueOrReason<NumericWithUnit>::of(NumericWithUnit(0.0, Unit::kDecimalAnnual));
  assign_derived_identities(rate);
  expect_refusal(decode_pricing_result(rate.to_canonical()),
                 ContractViolationKind::kInvariantViolation, "/par_rate");
}

TEST(PricingResultFailedStatus, KeepsUnvaluedFailureAndValuedSuccessValid) {
  const Refusal failed = decode_pricing_result(
      failed_pricing_result(std::string(kPricingFailedReasonCode)).to_canonical());
  EXPECT_FALSE(failed.refused) << failed.pointer << ": " << failed.message;
  const Refusal success = decode_pricing_result(
      successful_pricing_result(Currency::kUsd).to_canonical());
  EXPECT_FALSE(success.refused) << success.pointer << ": " << success.message;
}
