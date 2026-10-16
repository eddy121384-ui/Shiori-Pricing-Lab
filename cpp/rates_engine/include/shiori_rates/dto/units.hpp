#pragma once
//
// Closed wire vocabularies for the V1 Rates boundary.
//
// Provenance of each vocabulary (never invented here):
//   * `Currency`, `FloatingIndex`, `PayReceive`, `SettlementType` reuse the values that already
//     exist in the Python product layer (`src/shiori_pricing_lab/products/enums.py`). #225 section
//     6.2/7.2 say "vocabulary from products/enums.py, value is data", so the token set is reused
//     rather than re-invented.
//   * `ProductType`, `CurveRole`, `PillarValueType`, `Unit`, `ExerciseStyleV1`, `ObservationState`,
//     `MarketSource`, `PricingStatus`, `HeadlineSemantics`, `RiskMeasureId`, `RiskBumpType`,
//     `CurveUsageRole`, `ConvergenceStatus`, `ReasonCategory`, `ValueState` are defined by
//     docs/33 (Issue #225) sections 4.1, 5.1, 7.1.1, 7.4, 8.3, 10.2, 12.2, 13.2, 14.3, 16.
//
// DELIBERATE DIVERGENCE (documented so it cannot be mistaken for an oversight):
//   * `ExerciseStyleV1` is {EUROPEAN, BERMUDAN} because #225 section 10.2/§10 rule excludes
//     AMERICAN from the RATES_KERNEL_INPUT_V1 vocabulary. The Python `ExerciseStyle` enum contains
//     AMERICAN for the existing bond-option line, which is a different, stable product line that
//     #227 must not change. The Rates DTO therefore uses its own stricter vocabulary.
//   * `ModelId` lists only the vocabulary tokens named by #225 section 14.3. The *selection* of a
//     model is RED-model data; this file defines no default and picks none.
//   * Methodology identifiers whose value is explicitly UNRESOLVED-RED in #225 (`compounding`,
//     `day_count`, `accrual_boundary`, interpolation/extrapolation `method_id`, `rule_id`,
//     objective/optimizer ids, `model_version`, settlement methodology fields) are NOT modelled as
//     closed enums, because #225 gives them open vocabularies ("LINEAR_IN_ZERO | ..."). They are
//     modelled as open methodology text and are never defaulted by this library.
//
#include <string_view>

#include "shiori_rates/dto/enum_tokens.hpp"

namespace Shiori::rates::dto {

// ---------------------------------------------------------------------------------------------
// products/enums.py vocabulary (reused)
// ---------------------------------------------------------------------------------------------
enum class Currency { kUsd, kEur, kGbp, kJpy, kChf, kAud, kCad, kTwd, kNzd, kKrw, kHkd, kSgd };
SHIORI_ENUM_TOKENS(Currency, "Currency",
  {Currency::kUsd, "USD"}, {Currency::kEur, "EUR"}, {Currency::kGbp, "GBP"},
  {Currency::kJpy, "JPY"}, {Currency::kChf, "CHF"}, {Currency::kAud, "AUD"},
  {Currency::kCad, "CAD"}, {Currency::kTwd, "TWD"}, {Currency::kNzd, "NZD"},
  {Currency::kKrw, "KRW"}, {Currency::kHkd, "HKD"}, {Currency::kSgd, "SGD"})

enum class FloatingIndex {
  kUsdSofr,
  kEurEstr,
  kGbpSonia,
  kJpyTona,
  kChfSaron,
  kUsdSofrTerm3M,
  kEurEuribor3M,
  kEurEuribor6M,
};
SHIORI_ENUM_TOKENS(FloatingIndex, "FloatingIndex",
  {FloatingIndex::kUsdSofr, "USD_SOFR"},
  {FloatingIndex::kEurEstr, "EUR_ESTR"},
  {FloatingIndex::kGbpSonia, "GBP_SONIA"},
  {FloatingIndex::kJpyTona, "JPY_TONA"},
  {FloatingIndex::kChfSaron, "CHF_SARON"},
  {FloatingIndex::kUsdSofrTerm3M, "USD_SOFR_TERM_3M"},
  {FloatingIndex::kEurEuribor3M, "EUR_EURIBOR_3M"},
  {FloatingIndex::kEurEuribor6M, "EUR_EURIBOR_6M"})

enum class PayReceive { kPay, kReceive };
SHIORI_ENUM_TOKENS(PayReceive, "PayReceive", {PayReceive::kPay, "PAY"},
  {PayReceive::kReceive, "RECEIVE"})

enum class SettlementType { kCash, kPhysical };
SHIORI_ENUM_TOKENS(SettlementType, "SettlementType", {SettlementType::kCash, "CASH"},
  {SettlementType::kPhysical, "PHYSICAL"})

// ---------------------------------------------------------------------------------------------
// #225 vocabularies
// ---------------------------------------------------------------------------------------------
// #225 section 5.1: RATES_KERNEL_INPUT_V1 product vocabulary is EXACTLY OIS | SWAPTION.
// CALLABLE_SWAP and RANGE_ACCRUAL must be rejected as unknown in V1, not partially honoured.
enum class ProductType { kOis, kSwaption };
SHIORI_ENUM_TOKENS(ProductType, "ProductType", {ProductType::kOis, "OIS"},
  {ProductType::kSwaption, "SWAPTION"})

// #225 section 7.1.1.
enum class CurveRole { kDiscount, kForecast, kDiscountAndForecastReferenceOnly };
SHIORI_ENUM_TOKENS(CurveRole, "CurveRole", {CurveRole::kDiscount, "DISCOUNT"},
  {CurveRole::kForecast, "FORECAST"},
  {CurveRole::kDiscountAndForecastReferenceOnly, "DISCOUNT_AND_FORECAST_REFERENCE_ONLY"})

// #225 section 13.2: the valuation role being bumped, which is distinct from a curve's intrinsic
// role even when one combined-role curve serves both.
enum class CurveUsageRole { kDiscount, kForecast };
SHIORI_ENUM_TOKENS(CurveUsageRole, "CurveUsageRole", {CurveUsageRole::kDiscount, "DISCOUNT"},
  {CurveUsageRole::kForecast, "FORECAST"})

// #225 section 7.4 pillar value types.
enum class PillarValueType {
  kDiscountFactor,
  kZeroRateContinuous,
  kZeroRateSimple,
  kParRateDisplayOnlyNeverPrice,
};
SHIORI_ENUM_TOKENS(PillarValueType, "PillarValueType",
  {PillarValueType::kDiscountFactor, "DISCOUNT_FACTOR"},
  {PillarValueType::kZeroRateContinuous, "ZERO_RATE_CONTINUOUS"},
  {PillarValueType::kZeroRateSimple, "ZERO_RATE_SIMPLE"},
  {PillarValueType::kParRateDisplayOnlyNeverPrice, "PAR_RATE_DISPLAY_ONLY_NEVER_PRICE"})

// #225 section 7.2/7.4 units referenced by the curve/result contracts.
// The canonical Rates boundary accepts decimal annual rates and unitless ratios only; untagged
// percent and basis-point numerics are refused (section 7.4).
enum class Unit {
  kRatio,
  kDecimalAnnual,
  kPercentRawUnconverted,
  kCurrencyAmount,
  kCurrencyAmountPerBasisPoint,
  kBasisPoints,
  kDecimal,
  kDecimalSensitivity,
  kYearsFraction,
  kPerYear,
};
SHIORI_ENUM_TOKENS(Unit, "Unit",
  {Unit::kRatio, "RATIO"},
  {Unit::kDecimalAnnual, "DECIMAL_ANNUAL"},
  {Unit::kPercentRawUnconverted, "PERCENT_RAW_UNCONVERTED"},
  {Unit::kCurrencyAmount, "CURRENCY_AMOUNT"},
  {Unit::kCurrencyAmountPerBasisPoint, "CURRENCY_AMOUNT_PER_BASIS_POINT"},
  {Unit::kBasisPoints, "BASIS_POINTS"},
  {Unit::kDecimal, "DECIMAL"},
  {Unit::kDecimalSensitivity, "DECIMAL_SENSITIVITY"},
  {Unit::kYearsFraction, "YEARS_FRACTION"},
  {Unit::kPerYear, "PER_YEAR"})

// #225 section 10.2: AMERICAN is deliberately absent from the Rates vocabulary.
enum class ExerciseStyleV1 { kEuropean, kBermudan };
SHIORI_ENUM_TOKENS(ExerciseStyleV1, "ExerciseStyleV1",
  {ExerciseStyleV1::kEuropean, "EUROPEAN"}, {ExerciseStyleV1::kBermudan, "BERMUDAN"})

// #225 section 8.3.
enum class ObservationState { kHistorical, kForecastRequired, kProjected, kMissing };
SHIORI_ENUM_TOKENS(ObservationState, "ObservationState",
  {ObservationState::kHistorical, "HISTORICAL"},
  {ObservationState::kForecastRequired, "FORECAST_REQUIRED"},
  {ObservationState::kProjected, "PROJECTED"}, {ObservationState::kMissing, "MISSING"})

// #225 section 7.2 `source` and section 8.2 entry `source`.
enum class MarketSource {
  kBloombergDapi,
  kScreenTranscription,
  kSyntheticFixture,
  kResearchAdapter,
  kCurveProjection,
};
SHIORI_ENUM_TOKENS(MarketSource, "MarketSource",
  {MarketSource::kBloombergDapi, "BLOOMBERG_DAPI"},
  {MarketSource::kScreenTranscription, "SCREEN_TRANSCRIPTION"},
  {MarketSource::kSyntheticFixture, "SYNTHETIC_FIXTURE"},
  {MarketSource::kResearchAdapter, "RESEARCH_ADAPTER"},
  {MarketSource::kCurveProjection, "CURVE_PROJECTION"})

// #225 section 4.1 value state / explicit pillar state.
enum class ValueState { kResolved, kNullWithReason };
SHIORI_ENUM_TOKENS(ValueState, "ValueState", {ValueState::kResolved, "RESOLVED"},
  {ValueState::kNullWithReason, "NULL_WITH_REASON"})

// #225 section 4.1 structured reason categories.
enum class ReasonCategory {
  kNotApplicable,
  kUnavailable,
  kUnresolvedMethodology,
  kMissingMarketData,
  kFailedCapture,
};
SHIORI_ENUM_TOKENS(ReasonCategory, "ReasonCategory",
  {ReasonCategory::kNotApplicable, "NOT_APPLICABLE"},
  {ReasonCategory::kUnavailable, "UNAVAILABLE"},
  {ReasonCategory::kUnresolvedMethodology, "UNRESOLVED_METHODOLOGY"},
  {ReasonCategory::kMissingMarketData, "MISSING_MARKET_DATA"},
  {ReasonCategory::kFailedCapture, "FAILED_CAPTURE"})

// #225 section 12.2/16 status vocabulary.
enum class PricingStatus { kSuccess, kSuccessWithWarnings, kFailed };
SHIORI_ENUM_TOKENS(PricingStatus, "PricingStatus", {PricingStatus::kSuccess, "SUCCESS"},
  {PricingStatus::kSuccessWithWarnings, "SUCCESS_WITH_WARNINGS"},
  {PricingStatus::kFailed, "FAILED"})

// #225 section 12.2 headline_value_type.semantics.
enum class HeadlineSemantics { kPresentValue, kPremium, kParRate, kAnnuityPvbp };
SHIORI_ENUM_TOKENS(HeadlineSemantics, "HeadlineSemantics",
  {HeadlineSemantics::kPresentValue, "PRESENT_VALUE"},
  {HeadlineSemantics::kPremium, "PREMIUM"},
  {HeadlineSemantics::kParRate, "PAR_RATE"},
  {HeadlineSemantics::kAnnuityPvbp, "ANNUITY_PVBP"})

// #225 section 13.2: measure vocabulary is EXACTLY these five tokens. RHO/CS01 must fail closed.
enum class RiskMeasureId { kDv01, kPv01, kDelta, kGamma, kVega };
SHIORI_ENUM_TOKENS(RiskMeasureId, "RiskMeasureId", {RiskMeasureId::kDv01, "DV01"},
  {RiskMeasureId::kPv01, "PV01"}, {RiskMeasureId::kDelta, "DELTA"},
  {RiskMeasureId::kGamma, "GAMMA"}, {RiskMeasureId::kVega, "VEGA"})

// #225 section 13.2 bump_type vocabulary.
enum class RiskBumpType { kParallelBp, kBucketedTenor, kVolPoint, kModelParam };
SHIORI_ENUM_TOKENS(RiskBumpType, "RiskBumpType", {RiskBumpType::kParallelBp, "PARALLEL_BP"},
  {RiskBumpType::kBucketedTenor, "BUCKETED_TENOR"},
  {RiskBumpType::kVolPoint, "VOL_POINT"}, {RiskBumpType::kModelParam, "MODEL_PARAM"})

// #225 section 13.2 risk_bucket_coordinate_type discriminator.
enum class RiskBucketKind { kTenor, kVolNode, kModelParameter };
SHIORI_ENUM_TOKENS(RiskBucketKind, "RiskBucketKind", {RiskBucketKind::kTenor, "TENOR"},
  {RiskBucketKind::kVolNode, "VOL_NODE"},
  {RiskBucketKind::kModelParameter, "MODEL_PARAMETER"})

// #225 section 13.2 risk_bump_target_type discriminator.
enum class RiskBumpTargetKind { kAllSelectedCurves, kDiscountCurve, kForecastCurve };
SHIORI_ENUM_TOKENS(RiskBumpTargetKind, "RiskBumpTargetKind",
  {RiskBumpTargetKind::kAllSelectedCurves, "ALL_SELECTED_CURVES"},
  {RiskBumpTargetKind::kDiscountCurve, "DISCOUNT_CURVE"},
  {RiskBumpTargetKind::kForecastCurve, "FORECAST_CURVE"})

// #225 section 14.3 convergence status. Never prose.
enum class ConvergenceStatus { kConverged, kNotConverged, kNotApplicable };
SHIORI_ENUM_TOKENS(ConvergenceStatus, "ConvergenceStatus",
  {ConvergenceStatus::kConverged, "CONVERGED"},
  {ConvergenceStatus::kNotConverged, "NOT_CONVERGED"},
  {ConvergenceStatus::kNotApplicable, "NOT_APPLICABLE"})

// #225 section 14.2/14.3 model vocabulary. Selection is RED-model data; nothing here picks one.
enum class ModelId { kHullWhite1F, kBlack76, kBachelier };
SHIORI_ENUM_TOKENS(ModelId, "ModelId", {ModelId::kHullWhite1F, "HULL_WHITE_1F"},
  {ModelId::kBlack76, "BLACK_76"}, {ModelId::kBachelier, "BACHELIER"})

// #225 section 14.2 calibration instrument type vocabulary.
enum class InstrumentType { kEuropeanSwaption, kVanillaSwap };
SHIORI_ENUM_TOKENS(InstrumentType, "InstrumentType",
  {InstrumentType::kEuropeanSwaption, "EUROPEAN_SWAPTION"},
  {InstrumentType::kVanillaSwap, "VANILLA_SWAP"})

// #225 section 14.2 calibration market target kind. Only one resolvable kind exists in V1.
enum class CalibrationTargetKind { kVolQuoteNodeV1 };
SHIORI_ENUM_TOKENS(CalibrationTargetKind, "CalibrationTargetKind",
  {CalibrationTargetKind::kVolQuoteNodeV1, "VOL_QUOTE_NODE_V1"})

// #226 sections 10.5/10.6 benchmark cache-state enumeration (Lane B), never prose.
enum class CacheState { kCold, kWarm, kDisabled };
SHIORI_ENUM_TOKENS(CacheState, "CacheState", {CacheState::kCold, "COLD"},
  {CacheState::kWarm, "WARM"}, {CacheState::kDisabled, "DISABLED"})

// The single V1 token that marks an explicitly unresolved methodology value (RED).
// #227 never substitutes a value for it and never emits it as a chosen methodology.
inline constexpr std::string_view kUnresolvedToken = "UNRESOLVED";

[[nodiscard]] inline bool is_unresolved_methodology_token(const std::string_view text) noexcept {
  return text == kUnresolvedToken || text == "NOT_LOCKED";
}

// docs/32 (Issue #224) section 2: `ResolvedSwap` carries the resolving `convention_set_id`, and an
// UNKNOWN convention_set_id must fail closed rather than being coerced. The V1 vocabulary therefore
// contains exactly the identifier #224 proposes, and nothing else.
enum class ConventionSetId { kUsdSofrOisV1 };
SHIORI_ENUM_TOKENS(ConventionSetId, "ConventionSetId",
  {ConventionSetId::kUsdSofrOisV1, "USD_SOFR_OIS_V1"})

}  // namespace Shiori::rates::dto
