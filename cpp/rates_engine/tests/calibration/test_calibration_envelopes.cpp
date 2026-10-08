// Model / calibration envelopes (#225 sections 14.2/14.3) — schema plumbing only.
//
// #227 may implement schema plumbing without executable calibration behaviour, and it must NOT invent
// a model parameter, objective, optimizer, tolerance or convergence shape while RED-225-M1/M2 values
// are open. The envelopes therefore carry a version tag, a derived identity and a byte-exact payload.
//
// These tests prove the fence (an envelope of the wrong version, or with an unknown key, is refused)
// and that the two envelopes are DISTINCT versions, so a calibration result can never be decoded as a
// calibration input.
#include <gtest/gtest.h>

#include <string>

#include "shiori_rates/dto/calibration.hpp"
#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/version.hpp"

namespace {

using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::ModelCalibrationInput;
using Shiori::rates::dto::ModelCalibrationResult;
using Shiori::rates::dto::parse_canonical_json;

[[nodiscard]] ContractViolationKind refusal_kind(const auto& decode) {
  return decode();
}

}  // namespace

TEST(CalibrationEnvelopes, FenceTheDocumentByVersion) {
  const auto decode_input = []() {
    try {
      (void)ModelCalibrationInput::from_canonical(parse_canonical_json("{}"), "");
    } catch (const ContractViolation& violation) {
      return violation.kind();
    }
    return ContractViolationKind::kInvariantViolation;  // sentinel: nothing was thrown
  };
  const auto decode_result = []() {
    try {
      (void)ModelCalibrationResult::from_canonical(parse_canonical_json("{}"), "");
    } catch (const ContractViolation& violation) {
      return violation.kind();
    }
    return ContractViolationKind::kInvariantViolation;
  };
  EXPECT_EQ(refusal_kind(decode_input), ContractViolationKind::kUnknownSchemaVersion);
  EXPECT_EQ(refusal_kind(decode_result), ContractViolationKind::kUnknownSchemaVersion);
}

TEST(CalibrationEnvelopes, RefuseAWrongVersionTokenAndAnUnknownKey) {
  EXPECT_THROW((void)ModelCalibrationInput::from_canonical(
                   parse_canonical_json(R"({"schema_version":"MODEL_CALIBRATION_INPUT_V2"})"), ""),
               ContractViolation);
  EXPECT_THROW((void)ModelCalibrationResult::from_canonical(
                   parse_canonical_json(R"({"schema_version":"MODEL_CALIBRATION_RESULT_V2"})"), ""),
               ContractViolation);
  // An unknown key is refused rather than ignored: the payload is opaque to #227, so re-typing a field
  // here would be inventing a shape that this issue explicitly does not own.
  EXPECT_THROW((void)ModelCalibrationInput::from_canonical(
                   parse_canonical_json(
                       R"({"schema_version":"MODEL_CALIBRATION_INPUT_V1","invented_parameter":1})"),
                   ""),
               ContractViolation);
}

TEST(CalibrationEnvelopes, UseDistinctVersionTokens) {
  using Shiori::rates::dto::SchemaVersion;
  using Shiori::rates::dto::to_token;
  const std::string input_token = to_token(SchemaVersion::kModelCalibrationInputV1);
  const std::string result_token = to_token(SchemaVersion::kModelCalibrationResultV1);
  EXPECT_FALSE(input_token.empty());
  EXPECT_FALSE(result_token.empty());
  // Distinct tokens are what stops a result being decoded as an input (or vice versa) when a document
  // is routed by its version tag.
  EXPECT_NE(input_token, result_token);
  EXPECT_NE(input_token, to_token(SchemaVersion::kRatesKernelInputV1));
}
