#include "shiori_rates/dto/error.hpp"

#include <string>

namespace Shiori::rates::dto {

namespace {

struct RatesErrorToken {
  RatesErrorCode code;
  const char* token;
};

// Exact tokens from docs/33 (Issue #225) section 16. Order mirrors the contract table.
constexpr RatesErrorToken kRatesErrorTokens[] = {
    {RatesErrorCode::kUnsupportedProduct, "UNSUPPORTED_PRODUCT"},
    {RatesErrorCode::kMissingMarketData, "MISSING_MARKET_DATA"},
    {RatesErrorCode::kValuationDateMismatch, "VALUATION_DATE_MISMATCH"},
    {RatesErrorCode::kCurrencyMismatch, "CURRENCY_MISMATCH"},
    {RatesErrorCode::kMarketSnapshotMismatch, "MARKET_SNAPSHOT_MISMATCH"},
    {RatesErrorCode::kInvalidProduct, "INVALID_PRODUCT"},
    {RatesErrorCode::kMissingReferenceData, "MISSING_REFERENCE_DATA"},
    {RatesErrorCode::kSameDayFixingRuleUnresolved, "SAME_DAY_FIXING_RULE_UNRESOLVED"},
    {RatesErrorCode::kUnknownSchemaVersion, "UNKNOWN_SCHEMA_VERSION"},
    {RatesErrorCode::kUnknownMethodologyVersion, "UNKNOWN_METHODOLOGY_VERSION"},
    {RatesErrorCode::kEngineError, "ENGINE_ERROR"},
};

struct ContractToken {
  ContractViolationKind kind;
  const char* token;
};

constexpr ContractToken kContractTokens[] = {
    {ContractViolationKind::kUnknownSchemaVersion, "UNKNOWN_SCHEMA_VERSION"},
    {ContractViolationKind::kMissingRequiredField, "MISSING_REQUIRED_FIELD"},
    {ContractViolationKind::kWrongType, "WRONG_TYPE"},
    {ContractViolationKind::kInvalidTaggedUnionState, "INVALID_TAGGED_UNION_STATE"},
    {ContractViolationKind::kUnknownEnumToken, "UNKNOWN_ENUM_TOKEN"},
    {ContractViolationKind::kFingerprintMismatch, "FINGERPRINT_MISMATCH"},
    {ContractViolationKind::kIdentityMismatch, "IDENTITY_MISMATCH"},
    {ContractViolationKind::kInvalidDate, "INVALID_DATE"},
    {ContractViolationKind::kInvalidTimestamp, "INVALID_TIMESTAMP"},
    {ContractViolationKind::kInvalidUtf8, "INVALID_UTF8"},
    {ContractViolationKind::kDuplicateKey, "DUPLICATE_KEY"},
    {ContractViolationKind::kUnknownField, "UNKNOWN_FIELD"},
    {ContractViolationKind::kNumericNotRepresentable, "NUMERIC_NOT_REPRESENTABLE"},
    {ContractViolationKind::kUnsupportedProduct, "UNSUPPORTED_PRODUCT"},
    {ContractViolationKind::kInvariantViolation, "INVARIANT_VIOLATION"},
};

}  // namespace

const char* to_token(RatesErrorCode code) noexcept {
  for (const auto& entry : kRatesErrorTokens) {
    if (entry.code == code) {
      return entry.token;
    }
  }
  return "ENGINE_ERROR";
}

bool rates_error_code_from_token(std::string_view token, RatesErrorCode& out) noexcept {
  for (const auto& entry : kRatesErrorTokens) {
    if (token == entry.token) {
      out = entry.code;
      return true;
    }
  }
  return false;
}

const char* to_token(ContractViolationKind kind) noexcept {
  for (const auto& entry : kContractTokens) {
    if (entry.kind == kind) {
      return entry.token;
    }
  }
  return "INVARIANT_VIOLATION";
}

std::string ContractViolation::build_what(ContractViolationKind kind, const std::string& pointer,
                                          const std::string& message) {
  std::string what = to_token(kind);
  if (!pointer.empty()) {
    what += " at ";
    what += pointer;
  }
  if (!message.empty()) {
    what += ": ";
    what += message;
  }
  return what;
}

void fail(ContractViolationKind kind, std::string pointer, std::string message) {
  throw ContractViolation(kind, std::move(pointer), std::move(message));
}

void fail_missing(std::string pointer, std::string_view field) {
  std::string message = "mandatory field '";
  message += field;
  message += "' is absent";
  throw ContractViolation(ContractViolationKind::kMissingRequiredField, std::move(pointer),
                          std::move(message));
}

void fail_wrong_type(std::string pointer, std::string_view expected, std::string_view actual) {
  std::string message = "expected ";
  message += expected;
  message += ", found ";
  message += actual;
  throw ContractViolation(ContractViolationKind::kWrongType, std::move(pointer),
                          std::move(message));
}

void fail_unknown_enum(std::string pointer, std::string_view token, std::string_view vocabulary) {
  std::string message = "token '";
  message += token;
  message += "' is not a member of ";
  message += vocabulary;
  throw ContractViolation(ContractViolationKind::kUnknownEnumToken, std::move(pointer),
                          std::move(message));
}

std::string pointer_child(const std::string& base, std::string_view segment) {
  std::string result = base;
  result += '/';
  result.append(segment.data(), segment.size());
  return result;
}

std::string pointer_index(const std::string& base, std::size_t index) {
  std::string result = base;
  result += '/';
  result += std::to_string(index);
  return result;
}

}  // namespace Shiori::rates::dto
