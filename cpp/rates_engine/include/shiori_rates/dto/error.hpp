#pragma once
//
// Shiori Rates Engine — DTO failure semantics.
//
// Contract: docs/33_rates_market_model_result_contracts_225.md section 16.
//
// TWO DISTINCT FAILURE CHANNELS, DELIBERATELY KEPT APART:
//
//   1. DOMAIN failure codes (`RatesErrorCode`) — exactly the eleven-code vocabulary of #225
//      section 16. They appear inside `RATES_PRICING_RESULT_V1` / `RATES_RISK_RESULT_V1` as
//      return-path values. This enum must not be extended without the methodology review #225
//      section 16 requires, so #227 adds nothing to it.
//
//   2. CONTRACT / STRUCTURAL violations (`ContractViolation`) — a refused malformed document:
//      unknown wire version, missing mandatory field, invalid tagged-union state, an unverifiable
//      declared fingerprint. #225 section 16 states that contract/programming violations *raise*
//      rather than being returned as a domain status. They therefore surface as an exception and
//      are reported by transport surfaces (the CLI envelope, Lane B diagnostics) using their own
//      structural tokens, which are never presented as a #225 domain error code.
//
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace Shiori::rates::dto {

// ---------------------------------------------------------------------------------------------
// Channel 1: the closed #225 section 16 domain error vocabulary.
// ---------------------------------------------------------------------------------------------
enum class RatesErrorCode {
  kUnsupportedProduct,
  kMissingMarketData,
  kValuationDateMismatch,
  kCurrencyMismatch,
  kMarketSnapshotMismatch,
  kInvalidProduct,
  kMissingReferenceData,
  kSameDayFixingRuleUnresolved,
  kUnknownSchemaVersion,
  kUnknownMethodologyVersion,
  kEngineError,
};

// Exact wire tokens from #225 section 16.
[[nodiscard]] const char* to_token(RatesErrorCode code) noexcept;

// Returns false (leaving `out` untouched) for an unrecognised token; never guesses.
[[nodiscard]] bool rates_error_code_from_token(std::string_view token, RatesErrorCode& out) noexcept;

// ---------------------------------------------------------------------------------------------
// Channel 2: structural refusal.
// ---------------------------------------------------------------------------------------------
enum class ContractViolationKind {
  kUnknownSchemaVersion,
  kMissingRequiredField,
  kWrongType,
  kInvalidTaggedUnionState,
  kUnknownEnumToken,
  kFingerprintMismatch,
  kIdentityMismatch,
  kInvalidDate,
  kInvalidTimestamp,
  kInvalidUtf8,
  kDuplicateKey,
  kUnknownField,
  kNumericNotRepresentable,
  kUnsupportedProduct,
  kInvariantViolation,
};

[[nodiscard]] const char* to_token(ContractViolationKind kind) noexcept;

// Thrown when a document is refused. `pointer` is a JSON-pointer-like locator inside the document
// (for example `/market_snapshot/curve_set/curves/0/pillars/2`) so a caller can attribute the
// refusal precisely. It carries no financial meaning and no methodology decision.
class ContractViolation : public std::runtime_error {
 public:
  ContractViolation(ContractViolationKind kind, std::string pointer, std::string message)
      : std::runtime_error(build_what(kind, pointer, message)),
        kind_(kind),
        pointer_(std::move(pointer)),
        message_(std::move(message)) {}

  [[nodiscard]] ContractViolationKind kind() const noexcept { return kind_; }
  [[nodiscard]] const std::string& pointer() const noexcept { return pointer_; }
  [[nodiscard]] const std::string& message() const noexcept { return message_; }
  [[nodiscard]] std::string_view token() const noexcept { return to_token(kind_); }

 private:
  static std::string build_what(ContractViolationKind kind, const std::string& pointer,
                                const std::string& message);

  ContractViolationKind kind_;
  std::string pointer_;
  std::string message_;
};

// Convenience constructors used throughout the decoder.
[[noreturn]] void fail(ContractViolationKind kind, std::string pointer, std::string message);
[[noreturn]] void fail_missing(std::string pointer, std::string_view field);
[[noreturn]] void fail_wrong_type(std::string pointer, std::string_view expected,
                                  std::string_view actual);
[[noreturn]] void fail_unknown_enum(std::string pointer, std::string_view token,
                                    std::string_view vocabulary);

// Appends a child segment to a JSON-pointer-ish locator.
[[nodiscard]] std::string pointer_child(const std::string& base, std::string_view segment);
[[nodiscard]] std::string pointer_index(const std::string& base, std::size_t index);

}  // namespace Shiori::rates::dto
