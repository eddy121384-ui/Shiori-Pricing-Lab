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

TEST(NumericWithUnit, KeepsEveryIntegerBinary64HoldsExactlyAndRefusesTheRest) {
  using Shiori::rates::dto::ContractViolationKind;
  using Shiori::rates::dto::NumericWithUnit;

  // A JSON integer is an exact canonical number form and the member stores binary64, so the decode must
  // be exact or refused: silently rounding would make the in-memory value (and every identity derived
  // from it) describe a number the document does not contain.
  auto decode = [](const std::string& integer_text) {
    return NumericWithUnit::from_canonical(
        Shiori::rates::dto::parse_canonical_json(
            std::string(R"({"unit":"DECIMAL_ANNUAL","value":)") + integer_text + "}"),
        "");
  };
  auto refusal = [](const std::string& integer_text) {
    try {
      (void)NumericWithUnit::from_canonical(
          Shiori::rates::dto::parse_canonical_json(
              std::string(R"({"unit":"DECIMAL_ANNUAL","value":)") + integer_text + "}"),
          "");
    } catch (const ContractViolation& violation) {
      return violation;
    }
    ADD_FAILURE() << "expected a refusal for the non-exact integer " << integer_text;
    return ContractViolation(ContractViolationKind::kInvariantViolation, "<no refusal>",
                             "no refusal was thrown");
  };

  // Boundary: 2^53 is the largest magnitude at which EVERY integer is exact, and it is accepted.
  EXPECT_EQ(decode("9007199254740992").value, 9007199254740992.0);
  EXPECT_EQ(decode("-9007199254740992").value, -9007199254740992.0);
  // Above 2^53 the binary64 spacing is 2, then 4, ... so exact values are accepted rather than refused:
  // 2^53 + 2, 2^54 (spacing 4), 2^54 + 4, and the negative limit -2^63 = INT64_MIN, the largest
  // magnitude the integer representation can hold and one binary64 holds exactly.
  EXPECT_EQ(decode("9007199254740994").value, 9007199254740994.0);
  EXPECT_EQ(decode("18014398509481984").value, 18014398509481984.0);
  EXPECT_EQ(decode("18014398509481988").value, 18014398509481988.0);
  EXPECT_EQ(decode("-9223372036854775808").value, -9223372036854775808.0);
  // INT64_MAX = 2^63 - 1 and INT64_MIN + 1 = -(2^63 - 1) are NOT exactly representable: the near-limit
  // cases are refused in integer arithmetic, with no float-to-integer cast anywhere.
  EXPECT_EQ(refusal("9223372036854775807").kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(refusal("9223372036854775807").pointer(), "/value");
  EXPECT_EQ(refusal("-9223372036854775807").pointer(), "/value");
  // The first two non-exact integers above the boundary, and one further up.
  for (const char* not_exact : {"9007199254740993", "-9007199254740993", "9007199254740995",
                                "18014398509481985", "9223372036854775806"}) {
    const ContractViolation violation = refusal(not_exact);
    EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation) << not_exact;
    EXPECT_EQ(violation.pointer(), "/value") << not_exact;
    EXPECT_FALSE(violation.message().empty());
  }

  // Floating values are untouched by the integer rule: a finite float token is a binary64 literal and is
  // stored as parsed, including values no integer in this range could name. (A decimal literal with more
  // precision than binary64 holds is already rounded by the JSON front end before any DTO decoder sees it;
  // the integer form is the case where the exact value IS still available to the decoder, which is
  // exactly what this rule closes.)
  EXPECT_EQ(decode("0.0425").value, 0.0425);
  EXPECT_EQ(decode("1e308").value, 1e308);
  EXPECT_EQ(decode("2.2250738585072014e-308").value, 2.2250738585072014e-308);
  // An accepted large integer survives a re-encode/re-decode with the SAME binary64 value, so no
  // precision is lost on the accepted path either. (The re-encoded token is the canonical form of a
  // binary64 member, unchanged by this rule.)
  const NumericWithUnit accepted = decode("9007199254740994");
  const NumericWithUnit round_tripped = NumericWithUnit::from_canonical(
      Shiori::rates::dto::parse_canonical_json(accepted.to_canonical().dump()), "");
  EXPECT_EQ(round_tripped.value, accepted.value);
  EXPECT_EQ(round_tripped.unit, accepted.unit);
}
