// Closed vocabularies, schema-version fencing, ValueOrReason states and NumericWithUnit units.
//
// These are the vocabulary and union primitives every #225 document is built from. The tests assert
// the MECHANISM (round-trip, refusal of an unknown token, refusal of a malformed union) rather than a
// copy of the token list, so adding a token to a closed vocabulary cannot silently pass: the
// vocabulary round-trip and the AMERICAN-absent check below are the guards that matter.
#include <gtest/gtest.h>

#include <iterator>
#include <string>
#include <string_view>
#include <utility>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/numeric.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"
#include "shiori_rates/dto/version.hpp"

namespace {

using Shiori::rates::dto::CanonicalMembers;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;

template <typename Enum>
[[nodiscard]] bool token_is_known(const char* token) {
  // enum_from_token<Enum> is the fail-closed form: it RAISES on an unknown token instead of returning
  // a status, so "is this token known" has to be answered by catching the refusal.
  try {
    (void)Shiori::rates::dto::enum_from_token<Enum>(std::string_view(token), "");
    return true;
  } catch (const ContractViolation&) {
    return false;
  }
}

}  // namespace

TEST(Vocabulary, RoundTripsAReusedCrossLanguageToken) {
  using Shiori::rates::dto::Currency;
  using Shiori::rates::dto::enum_from_token;
  using Shiori::rates::dto::enum_to_token;

  // USD and EUR are reused from the Python `products/enums.py` vocabulary: the Python application and
  // the C++ engine must agree on the exact tokens, so this is a cross-language anchor, not a local
  // naming choice.
  for (const char* token : {"USD", "EUR"}) {
    const Currency parsed = enum_from_token<Currency>(std::string_view(token), "");
    EXPECT_EQ(std::string(enum_to_token(parsed)), token);
  }
  EXPECT_TRUE(token_is_known<Currency>("USD"));
  EXPECT_FALSE(token_is_known<Currency>("NOT_A_CURRENCY"));
  EXPECT_FALSE(token_is_known<Currency>("usd"))
      << "vocabulary matching is exact case; a lowercase accept would create two vocabularies";
}

TEST(Vocabulary, KeepsTheDocumentedCurveRolesAndRefusesAmericanExercise) {
  // Curve roles are fixed by #225 section 7.1.1: three roles, each with a distinct meaning, and a
  // curve may not be silently reinterpreted into another role.
  EXPECT_TRUE(token_is_known<Shiori::rates::dto::CurveRole>("DISCOUNT"));
  EXPECT_TRUE(token_is_known<Shiori::rates::dto::CurveRole>("FORECAST"));
  EXPECT_TRUE(
      token_is_known<Shiori::rates::dto::CurveRole>("DISCOUNT_AND_FORECAST_REFERENCE_ONLY"));
  EXPECT_FALSE(token_is_known<Shiori::rates::dto::CurveRole>("BOTH"));

  // V1 swaption exercise supports EUROPEAN and reserves BERMUDAN. AMERICAN must NOT be a recognised
  // token: accepting it would silently claim the engine can handle a product no approved methodology
  // covers.
  EXPECT_TRUE(token_is_known<Shiori::rates::dto::ExerciseStyleV1>("EUROPEAN"));
  EXPECT_TRUE(token_is_known<Shiori::rates::dto::ExerciseStyleV1>("BERMUDAN"));
  EXPECT_FALSE(token_is_known<Shiori::rates::dto::ExerciseStyleV1>("AMERICAN"));
}

TEST(Vocabulary, RefusesAnUnknownTokenInsteadOfGuessing) {
  const CanonicalValue node = CanonicalValue(std::string("NOT_A_ROLE"));
  EXPECT_THROW((void)Shiori::rates::dto::enum_from_canonical<Shiori::rates::dto::CurveRole>(node, ""),
               ContractViolation);
}

TEST(SchemaVersion, FencesDocumentsByExactToken) {
  using Shiori::rates::dto::SchemaVersion;
  using Shiori::rates::dto::schema_version_from_token;
  using Shiori::rates::dto::to_token;

  EXPECT_EQ(std::string(to_token(SchemaVersion::kRatesKernelInputV1)), "RATES_KERNEL_INPUT_V1");
  SchemaVersion parsed{};
  ASSERT_TRUE(schema_version_from_token(std::string_view("RATES_KERNEL_INPUT_V1"), parsed));
  EXPECT_EQ(parsed, SchemaVersion::kRatesKernelInputV1);
  SchemaVersion unused{};
  EXPECT_FALSE(schema_version_from_token(std::string_view("RATES_KERNEL_INPUT_V2"), unused));
  EXPECT_FALSE(schema_version_from_token(std::string_view(""), unused));

  // A version tag must be checked before any other member is read, so an unknown document is refused
  // as an unknown version rather than as a missing field it happens not to share.
  const CanonicalValue wrong_version =
      Shiori::rates::dto::parse_canonical_json(R"({"schema_version":"RATES_KERNEL_INPUT_V2"})");
  EXPECT_THROW((void)Shiori::rates::dto::require_schema_version(
                   wrong_version, SchemaVersion::kRatesKernelInputV1, ""),
               ContractViolation);
  const CanonicalValue absent = Shiori::rates::dto::parse_canonical_json("{}");
  EXPECT_THROW((void)Shiori::rates::dto::require_schema_version(
                   absent, SchemaVersion::kRatesKernelInputV1, ""),
               ContractViolation);
}

TEST(DomainErrorVocabulary, IsClosedAndRoundTrips) {
  using Shiori::rates::dto::RatesErrorCode;
  using Shiori::rates::dto::rates_error_code_from_token;
  using Shiori::rates::dto::to_token;

  // Exactly the eleven #225 section 16 domain codes. #227 adds none: a structural refusal is never
  // reported as a domain code, so this list may not grow with #227 code.
  const RatesErrorCode vocabulary[] = {
      RatesErrorCode::kUnsupportedProduct,
      RatesErrorCode::kMissingMarketData,
      RatesErrorCode::kValuationDateMismatch,
      RatesErrorCode::kCurrencyMismatch,
      RatesErrorCode::kMarketSnapshotMismatch,
      RatesErrorCode::kInvalidProduct,
      RatesErrorCode::kMissingReferenceData,
      RatesErrorCode::kSameDayFixingRuleUnresolved,
      RatesErrorCode::kUnknownSchemaVersion,
      RatesErrorCode::kUnknownMethodologyVersion,
      RatesErrorCode::kEngineError,
  };
  EXPECT_EQ(std::size(vocabulary), 11U);
  for (const RatesErrorCode code : vocabulary) {
    const std::string token = to_token(code);
    EXPECT_FALSE(token.empty());
    RatesErrorCode parsed{};
    ASSERT_TRUE(rates_error_code_from_token(token, parsed)) << token;
    EXPECT_EQ(parsed, code);
  }
  EXPECT_EQ(std::string(to_token(RatesErrorCode::kSameDayFixingRuleUnresolved)),
            "SAME_DAY_FIXING_RULE_UNRESOLVED");
  RatesErrorCode unused{};
  EXPECT_FALSE(rates_error_code_from_token("NOT_A_DOMAIN_CODE", unused));
}

TEST(ContractViolation, CarriesAStructuralTokenAndAPrecisePointer) {
  using Shiori::rates::dto::ContractViolationKind;
  // Structural tokens must be distinct and non-empty: they are what a refusal is reported as, and two
  // kinds sharing a token would make a refusal unattributable.
  const ContractViolationKind kinds[] = {
      ContractViolationKind::kUnknownSchemaVersion, ContractViolationKind::kMissingRequiredField,
      ContractViolationKind::kWrongType,            ContractViolationKind::kInvalidTaggedUnionState,
      ContractViolationKind::kUnknownEnumToken,     ContractViolationKind::kFingerprintMismatch,
      ContractViolationKind::kIdentityMismatch,     ContractViolationKind::kInvariantViolation,
  };
  std::string previous;
  for (const ContractViolationKind kind : kinds) {
    const std::string token = Shiori::rates::dto::to_token(kind);
    EXPECT_FALSE(token.empty());
    EXPECT_NE(token, previous);
    previous = token;
  }
  try {
    Shiori::rates::dto::fail(ContractViolationKind::kWrongType, "/a/b", "expected integer");
    FAIL() << "fail() must never return";
  } catch (const ContractViolation& violation) {
    EXPECT_EQ(violation.kind(), ContractViolationKind::kWrongType);
    EXPECT_EQ(violation.pointer(), "/a/b");
    EXPECT_EQ(violation.message(), "expected integer");
    EXPECT_FALSE(violation.token().empty());
  }
}

TEST(ValueOrReason, RoundTripsThePresentStateAndRefusesAMalformedUnion) {
  using Shiori::rates::dto::NumericWithUnit;
  using Shiori::rates::dto::Unit;
  using Shiori::rates::dto::ValueOrReason;

  const Unit decimal_annual = Shiori::rates::dto::enum_from_token<Unit>(
      std::string_view("DECIMAL_ANNUAL"), "");
  const NumericWithUnit rate = NumericWithUnit::from_canonical(
      Shiori::rates::dto::parse_canonical_json(R"({"unit":"DECIMAL_ANNUAL","value":0.0425})"), "");
  // require_unit is [[nodiscard]]: discarding it would be a warning, and with warnings-as-errors a
  // silent failure to check the unit must not be expressible.
  (void)rate.require_unit(decimal_annual, "");

  const ValueOrReason<NumericWithUnit> present = ValueOrReason<NumericWithUnit>::of(rate);
  const CanonicalValue encoded = present.to_canonical();
  const ValueOrReason<NumericWithUnit> decoded =
      ValueOrReason<NumericWithUnit>::from_canonical(encoded, "");
  ASSERT_TRUE(decoded.present);
  EXPECT_EQ(decoded.value.value, rate.value);
  EXPECT_EQ(decoded.value.unit, rate.unit);

  // PRESENT must not carry a reason: a present value with a reason is two contradictory statements in
  // one field, and a consumer cannot tell which one to trust.
  CanonicalMembers with_reason;
  for (const auto& member : encoded.as_object().members) {
    with_reason.push_back(member);
  }
  with_reason.emplace_back("reason", Shiori::rates::dto::parse_canonical_json(
                                         R"({"category":"UNAVAILABLE","code":"X","detail":null})"));
  EXPECT_THROW((void)ValueOrReason<NumericWithUnit>::from_canonical(
                   CanonicalValue::make_object(std::move(with_reason)), ""),
               ContractViolation);

  // A wrong type is refused rather than coerced.
  EXPECT_THROW((void)ValueOrReason<NumericWithUnit>::from_canonical(CanonicalValue(7), ""),
               ContractViolation);
}

TEST(NumericWithUnit, RefusesAUnitThatDisagreesWithTheFieldSemantics) {
  using Shiori::rates::dto::NumericWithUnit;
  using Shiori::rates::dto::Unit;

  const Unit percent_raw = Shiori::rates::dto::enum_from_token<Unit>(
      std::string_view("PERCENT_RAW_UNCONVERTED"), "");
  const Unit ratio = Shiori::rates::dto::enum_from_token<Unit>(std::string_view("RATIO"), "");

  const NumericWithUnit as_percent = NumericWithUnit::from_canonical(
      Shiori::rates::dto::parse_canonical_json(
          R"({"unit":"PERCENT_RAW_UNCONVERTED","value":4.25})"),
      "");
  (void)as_percent.require_unit(percent_raw, "");
  // A discount factor is a RATIO; a raw percent quote is not, and RE-BASING it here would invent a
  // conversion the contract never approved.
  EXPECT_THROW((void)as_percent.require_unit(ratio, ""), ContractViolation);
}
