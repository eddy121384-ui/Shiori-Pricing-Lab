#pragma once
//
// Wire-schema version tokens — #225 section 15.2.
//
//   * Every top-level contract carries an explicit machine-readable `schema_version`.
//   * Pattern `<CONTRACT>_V<n>`; a wire-schema revision ships under a NEW version string and never
//     mutates a published shape.
//   * Unknown versions FAIL CLOSED BEFORE ANY OTHER CONTENT IS READ.
//
// #227 implements this rule literally: `require_schema_version` is the first call in every decoder,
// and a version token that this build does not know is refused before a single other field is
// examined. Methodology versions evolve independently of wire versions (section 15.2) and are
// modelled as open methodology text, never as this enum.
//
#include <string>
#include <string_view>

#include "shiori_rates/dto/canonical.hpp"

namespace Shiori::rates::dto {

enum class SchemaVersion {
  kRatesKernelInputV1,
  kMarketSnapshotV1,
  kCurveSetV1,
  kDiscountCurveV1,
  kForwardCurveV1,
  kFixingStoreV1,
  kVolQuoteV1,
  kVolatilityInputV1,
  kModelInputV1,
  kExerciseTermsV1,
  kSettlementTermsV1,
  kResolvedSwapV1,
  kRatesPricingResultV1,
  kRatesRiskResultV1,
  kModelCalibrationInputV1,
  kModelCalibrationResultV1,
  kRatesRuntimeTelemetryV1,
};

// Exact wire token, for example "RATES_KERNEL_INPUT_V1".
[[nodiscard]] const char* to_token(SchemaVersion version) noexcept;

// Returns false for an unknown token. Never guesses and never coerces across versions.
[[nodiscard]] bool schema_version_from_token(std::string_view token, SchemaVersion& out) noexcept;

// Reads `schema_version` and validates it.
//   * absent          -> ContractViolation(kMissingRequiredField)
//   * unknown token   -> ContractViolation(kUnknownSchemaVersion)
//   * known, != want  -> ContractViolation(kUnknownSchemaVersion) (a document of another contract
//                        is not partially honoured)
// Callers must invoke this before reading any other member of the object.
[[nodiscard]] SchemaVersion require_schema_version(const CanonicalValue& node,
                                                  SchemaVersion expected,
                                                  const std::string& pointer);

}  // namespace Shiori::rates::dto
