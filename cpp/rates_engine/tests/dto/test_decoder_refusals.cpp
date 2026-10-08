// Decoder front-door refusals: a document that is not a valid V1 document is refused, and refused
// for the right reason.
//
// #226 section 16 makes a contract violation RAISE rather than be returned as a domain status, so
// these tests are the evidence that the front door cannot half-accept a document: an unknown version,
// an unknown key or a wrong type must never produce a partially populated DTO that later code treats
// as validated input.
#include <gtest/gtest.h>

#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/result.hpp"
#include "shiori_rates/dto/version.hpp"

namespace {

using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::parse_canonical_json;

[[nodiscard]] ContractViolationKind refusal_kind_for_kernel(const char* json) {
  try {
    (void)Shiori::rates::dto::RatesKernelInput::from_canonical(parse_canonical_json(json), "");
  } catch (const ContractViolation& violation) {
    return violation.kind();
  }
  return ContractViolationKind::kUnknownSchemaVersion;  // sentinel: nothing was thrown
}

[[nodiscard]] std::string refusal_pointer_for_kernel(const char* json) {
  try {
    (void)Shiori::rates::dto::RatesKernelInput::from_canonical(parse_canonical_json(json), "");
  } catch (const ContractViolation& violation) {
    return violation.pointer();
  }
  return "<no refusal>";
}

}  // namespace

TEST(KernelInputDecoder, RefusesAnAbsentVersionTagBeforeReadingAnyField) {
  // The empty document is the canonical probe: the version must be checked first, otherwise a
  // migration could read a V2 field under V1 rules.
  EXPECT_EQ(refusal_kind_for_kernel("{}"), ContractViolationKind::kUnknownSchemaVersion);
}

TEST(KernelInputDecoder, RefusesAnUnknownVersionTag) {
  EXPECT_EQ(refusal_kind_for_kernel(R"({"schema_version":"RATES_KERNEL_INPUT_V2"})"),
            ContractViolationKind::kUnknownSchemaVersion);
  EXPECT_EQ(refusal_kind_for_kernel(R"({"schema_version":1})"),
            ContractViolationKind::kUnknownSchemaVersion);
}

TEST(KernelInputDecoder, RefusesAnUnknownTopLevelKey) {
  // #225 fixes the top-level key set. Silently ignoring an unknown key would let a document carry a
  // field the engine never validated, which is how a stale client and a new engine drift apart
  // without anyone noticing.
  EXPECT_THROW(
      (void)Shiori::rates::dto::RatesKernelInput::from_canonical(
          parse_canonical_json(R"({"schema_version":"RATES_KERNEL_INPUT_V1","bogus":1})"), ""),
      ContractViolation);
}

TEST(KernelInputDecoder, RefusesAWrongTypedMemberAndPointsAtIt) {
  const std::string pointer = refusal_pointer_for_kernel(
      R"({"schema_version":"RATES_KERNEL_INPUT_V1","valuation_product":1})");
  EXPECT_NE(pointer, "<no refusal>");
  // The pointer must identify the offending member: a refusal with no location cannot be diagnosed.
  EXPECT_NE(pointer.find("valuation_product"), std::string::npos) << pointer;
}

TEST(KernelInputDecoder, RefusesAMissingMandatoryMemberWithItsOwnLocation) {
  // A version tag with nothing else is missing a mandatory member, which is a different refusal from
  // an unknown version. Conflating the two would hide a broken writer behind a version error.
  const std::string pointer =
      refusal_pointer_for_kernel(R"({"schema_version":"RATES_KERNEL_INPUT_V1"})");
  EXPECT_NE(pointer, "<no refusal>");
  EXPECT_NE(pointer.find("valuation_product"), std::string::npos) << pointer;
}

TEST(ResultDecoders, FenceBothResultDocumentsByVersion) {
  // The result documents are decodable by the DTO layer (#227 requirement: the C++ DTO layer must be
  // able to represent and round-trip the approved V1 result shapes). #227 implements no pricing, so
  // what is proven here is the same front-door fence as for the kernel input.
  try {
    (void)Shiori::rates::dto::PricingResult::from_canonical(parse_canonical_json("{}"), "");
    FAIL() << "an empty pricing result must be refused";
  } catch (const ContractViolation& violation) {
    EXPECT_EQ(violation.kind(), ContractViolationKind::kUnknownSchemaVersion);
  }
  try {
    (void)Shiori::rates::dto::RiskResult::from_canonical(parse_canonical_json("{}"), "");
    FAIL() << "an empty risk result must be refused";
  } catch (const ContractViolation& violation) {
    EXPECT_EQ(violation.kind(), ContractViolationKind::kUnknownSchemaVersion);
  }
  // A wrong version token is refused by the same fence rather than being treated as a missing field.
  EXPECT_THROW(
      (void)Shiori::rates::dto::PricingResult::from_canonical(
          parse_canonical_json(R"({"schema_version":"RATES_PRICING_RESULT_V2"})"), ""),
      ContractViolation);
}
