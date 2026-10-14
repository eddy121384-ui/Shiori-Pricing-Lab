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

// The section 15.4 canonical order for this fixture is
//   [ BUCKETED_TENOR DV01 (target NOT_APPLICABLE) , PARALLEL_BP DV01 (target PRESENT) ,
//     MODEL_PARAM GAMMA , VOL_POINT VEGA ]
// because the approved tuple is (measure_id, target state, target serialization, bucket state, bucket
// serialization): the all-DV01 prefix is ordered by "structured NOT_APPLICABLE before PRESENT" on
// `bump_target`, and the remaining rows are ordered by `measure_id`.
[[nodiscard]] RiskResult risk_result_fixture() {
  RiskResult result;
  result.product_id = std::string(kFixtureProductId);
  result.product_type = ProductType::kSwaption;
  result.valuation_date = fixture_date();
  result.result_currency = Currency::kUsd;
  result.valuation_context_id =
      derived_valuation_context_id(result.valuation_date, result.result_currency);
  result.measures.push_back(bucketed_tenor_measure());
  result.measures.push_back(parallel_measure());
  result.measures.push_back(model_parameter_measure(std::string(kFixtureModelInputId)));
  result.measures.push_back(vol_point_measure(std::string(kFixtureVolatilityInputId)));
  result.inputs_identity = fixture_inputs_identity(true);
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
  ASSERT_EQ(fixture.measures.size(), 4U);
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
  ASSERT_EQ(inverted.measures.size(), 4U);
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
  ASSERT_EQ(duplicated.measures.size(), 5U);
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
  ASSERT_EQ(fixture.measures.size(), 4U);
  const auto& coordinate =
      std::get<VolNodeBucketCoordinate>(fixture.measures[3].bucket_coordinate.value.body);
  ASSERT_TRUE(fixture.inputs_identity.volatility_input_id.present);
  EXPECT_EQ(coordinate.volatility_input_id, fixture.inputs_identity.volatility_input_id.value);

  const Refusal refusal = decode_risk_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

TEST(RiskResultVolNodeBucket, RefusesAnotherVolatilityInputIdAfterRehash) {
  RiskResult mismatched = risk_result_fixture();
  auto& coordinate =
      std::get<VolNodeBucketCoordinate>(mismatched.measures[3].bucket_coordinate.value.body);
  coordinate.volatility_input_id = std::string(kFixtureOtherVolatilityInputId);
  ASSERT_NE(coordinate.volatility_input_id, mismatched.inputs_identity.volatility_input_id.value);
  assign_derived_identities(mismatched);

  expect_refusal(decode_risk_result(mismatched.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/measures/3/bucket_coordinate");
}

TEST(RiskResultVolNodeBucket, RefusesAnInapplicableCoordinateWhenNoInputIsConsumed) {
  RiskResult absent = risk_result_fixture();
  absent.inputs_identity.volatility_input_id = ValueOrReason<std::string>::not_applicable();
  assign_derived_identities(absent);

  expect_refusal(decode_risk_result(absent.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/measures/3/bucket_coordinate");
}

// ---------------------------------------------------------------------------------------------
// Finding 3 - a MODEL_PARAMETER bucket names the consumed ModelInput
// docs/33 section 13.2 line 1032.
// ---------------------------------------------------------------------------------------------
TEST(RiskResultModelParameterBucket, AcceptsTheConsumedModelInputId) {
  const RiskResult fixture = risk_result_fixture();
  const auto& coordinate =
      std::get<ModelParameterBucketCoordinate>(fixture.measures[2].bucket_coordinate.value.body);
  ASSERT_TRUE(fixture.inputs_identity.model_input_id.present);
  EXPECT_EQ(coordinate.model_input_id, fixture.inputs_identity.model_input_id.value);

  const Refusal refusal = decode_risk_result(fixture.to_canonical());
  EXPECT_FALSE(refusal.refused) << std::string(to_token(refusal.kind)) << " at " << refusal.pointer
                                << ": " << refusal.message;
}

TEST(RiskResultModelParameterBucket, RefusesAnotherModelInputIdAfterRehash) {
  RiskResult mismatched = risk_result_fixture();
  auto& coordinate =
      std::get<ModelParameterBucketCoordinate>(mismatched.measures[2].bucket_coordinate.value.body);
  coordinate.model_input_id = std::string(kFixtureOtherModelInputId);
  ASSERT_NE(coordinate.model_input_id, mismatched.inputs_identity.model_input_id.value);
  assign_derived_identities(mismatched);

  expect_refusal(decode_risk_result(mismatched.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/measures/2/bucket_coordinate");
}

TEST(RiskResultModelParameterBucket, RefusesAnInapplicableCoordinateWhenNoInputIsConsumed) {
  RiskResult absent = risk_result_fixture();
  absent.inputs_identity.model_input_id = ValueOrReason<std::string>::not_applicable();
  assign_derived_identities(absent);

  expect_refusal(decode_risk_result(absent.to_canonical()),
                 ContractViolationKind::kIdentityMismatch, "/measures/2/bucket_coordinate");
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
