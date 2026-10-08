#include "shiori_rates/dto/version.hpp"

#include <string>
#include <string_view>
#include <utility>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"

namespace Shiori::rates::dto {

namespace {

struct VersionToken {
  SchemaVersion version;
  const char* token;
};

// Exact tokens from docs/33 (Issue #225) section 15.2, plus RESOLVED_SWAP_V1 which #225 section
// 12.2 (inputs_identity.resolved_swap_schema_version) names explicitly, and
// RATES_RUNTIME_TELEMETRY_V1 which #226 section 12.4 names for the Lane B channel.
constexpr VersionToken kVersionTokens[] = {
    {SchemaVersion::kRatesKernelInputV1, "RATES_KERNEL_INPUT_V1"},
    {SchemaVersion::kMarketSnapshotV1, "MARKET_SNAPSHOT_V1"},
    {SchemaVersion::kCurveSetV1, "CURVE_SET_V1"},
    {SchemaVersion::kDiscountCurveV1, "DISCOUNT_CURVE_V1"},
    {SchemaVersion::kForwardCurveV1, "FORWARD_CURVE_V1"},
    {SchemaVersion::kFixingStoreV1, "FIXING_STORE_V1"},
    {SchemaVersion::kVolQuoteV1, "VOL_QUOTE_V1"},
    {SchemaVersion::kVolatilityInputV1, "VOLATILITY_INPUT_V1"},
    {SchemaVersion::kModelInputV1, "MODEL_INPUT_V1"},
    {SchemaVersion::kExerciseTermsV1, "EXERCISE_TERMS_V1"},
    {SchemaVersion::kSettlementTermsV1, "SETTLEMENT_TERMS_V1"},
    {SchemaVersion::kResolvedSwapV1, "RESOLVED_SWAP_V1"},
    {SchemaVersion::kRatesPricingResultV1, "RATES_PRICING_RESULT_V1"},
    {SchemaVersion::kRatesRiskResultV1, "RATES_RISK_RESULT_V1"},
    {SchemaVersion::kModelCalibrationInputV1, "MODEL_CALIBRATION_INPUT_V1"},
    {SchemaVersion::kModelCalibrationResultV1, "MODEL_CALIBRATION_RESULT_V1"},
    {SchemaVersion::kRatesRuntimeTelemetryV1, "RATES_RUNTIME_TELEMETRY_V1"},
};

}  // namespace

const char* to_token(SchemaVersion version) noexcept {
  for (const auto& entry : kVersionTokens) {
    if (entry.version == version) {
      return entry.token;
    }
  }
  return "UNKNOWN_SCHEMA_VERSION";
}

bool schema_version_from_token(const std::string_view token, SchemaVersion& out) noexcept {
  for (const auto& entry : kVersionTokens) {
    if (token == entry.token) {
      out = entry.version;
      return true;
    }
  }
  return false;
}

SchemaVersion require_schema_version(const CanonicalValue& node, const SchemaVersion expected,
                                     const std::string& pointer) {
  if (!node.is_object()) {
    fail_wrong_type(pointer, "object", node.type_name());
  }
  const CanonicalValue* version_node = node.find("schema_version");
  if (version_node == nullptr) {
    // The version is checked before any other content, so a missing version is reported against the
    // object itself rather than against a field path that does not exist yet.
    fail_missing(pointer, "schema_version");
  }
  if (!version_node->is_string()) {
    fail_wrong_type(pointer_child(pointer, "schema_version"), "string version token",
                    version_node->type_name());
  }
  const std::string& token = version_node->as_string();
  SchemaVersion found = expected;
  if (!schema_version_from_token(token, found)) {
    fail(ContractViolationKind::kUnknownSchemaVersion, pointer_child(pointer, "schema_version"),
         "wire version is not known to this build");
  }
  if (found != expected) {
    std::string message = "expected ";
    message += to_token(expected);
    message += ", found ";
    message += token;
    fail(ContractViolationKind::kUnknownSchemaVersion, pointer_child(pointer, "schema_version"),
         std::move(message));
  }
  return found;
}

}  // namespace Shiori::rates::dto
