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
#include "shiori_rates/dto/market.hpp"
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

// Every curve document used below MUST be refused, so a missing refusal is reported as a test failure
// rather than folded into a sentinel that could be confused with a real refusal.
[[nodiscard]] ContractViolation refusal_for_curve(const char* json) {
  try {
    (void)Shiori::rates::dto::Curve::from_canonical(parse_canonical_json(json), "");
  } catch (const ContractViolation& violation) {
    return violation;
  }
  ADD_FAILURE() << "a curve document must be refused: " << json;
  return ContractViolation(ContractViolationKind::kMissingRequiredField, "<no refusal>",
                           "no refusal was thrown");
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

TEST(KernelInputDecoder, RefusesDuplicateKeysBeforeAnyVersionDecision) {
  // Stage 0 (syntactic/canonical admission) runs BEFORE the Stage 1 version fence, so a syntactically
  // valid document that repeats a member key is a duplicate-key refusal even when it carries no
  // version tag at all: no schema decision is allowed to see such a document first.
  EXPECT_EQ(refusal_kind_for_kernel(R"({"a":1,"a":2})"), ContractViolationKind::kDuplicateKey);
  // The duplicate is detected on decoded input tokens, not on raw source text: `\u0061` decodes to
  // `a`, so it is the same member as a literal `a` - exactly the collapse a map parser would perform.
  EXPECT_EQ(refusal_kind_for_kernel(R"({"a":1,"\u0061":2})"),
            ContractViolationKind::kDuplicateKey);
}

TEST(KernelInputDecoder, RefusesANestedDuplicateMemberBeforeAnyDtoInterpretation) {
  // The gate is depth-independent: a duplicate nested inside an object or inside an array element is
  // still a Stage 0 refusal, so a partially interpreted DTO can never reach a version or field
  // decision.
  EXPECT_EQ(refusal_kind_for_kernel(R"({"outer":{"a":1,"a":2}})"),
            ContractViolationKind::kDuplicateKey);
  EXPECT_EQ(refusal_kind_for_kernel(R"({"outer":[{"a":1,"a":2}]})"),
            ContractViolationKind::kDuplicateKey);
}

TEST(KernelInputDecoder, PointsEveryVersionShapeFailureAtTheVersionMember) {
  // Stage 1 owns absent, non-string, unknown and wrong version alike, and reports each of them at the
  // version member: a version-shape problem is never reported against a business field.
  EXPECT_EQ(refusal_pointer_for_kernel("{}"), "/schema_version");
  EXPECT_EQ(refusal_pointer_for_kernel(R"({"schema_version":1})"), "/schema_version");
  EXPECT_EQ(refusal_pointer_for_kernel(R"({"schema_version":"RATES_KERNEL_INPUT_V2"})"),
            "/schema_version");
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
  // The locator must be the MISSING MEMBER'S OWN pointer, not the enclosing object's: only then can
  // the refusal be attributed to the field that is actually absent.
  EXPECT_EQ(pointer, "/valuation_product") << pointer;
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

TEST(CurveDecoder, FencesTheCurveEntryPointByItsOwnVersionBeforeItsBody) {
  // A curve inside a market snapshot is its own versioned entry point, so it owns the same fence as
  // the top-level documents: every version-shape problem is an unknown wire version AT THE VERSION
  // MEMBER, reported before a single curve member is read.
  const ContractViolation absent = refusal_for_curve("{}");
  EXPECT_EQ(absent.kind(), ContractViolationKind::kUnknownSchemaVersion);
  EXPECT_EQ(absent.pointer(), "/schema_version");

  const ContractViolation non_string = refusal_for_curve(R"({"schema_version":1})");
  EXPECT_EQ(non_string.kind(), ContractViolationKind::kUnknownSchemaVersion);
  EXPECT_EQ(non_string.pointer(), "/schema_version");

  const ContractViolation unknown = refusal_for_curve(R"({"schema_version":"DISCOUNT_CURVE_V2"})");
  EXPECT_EQ(unknown.kind(), ContractViolationKind::kUnknownSchemaVersion);
  EXPECT_EQ(unknown.pointer(), "/schema_version");

  // A recognised V1 token gets past the fence, so what is missing afterwards is a mandatory curve
  // member: a different refusal, at that member's own pointer and not at the version.
  const ContractViolation incomplete =
      refusal_for_curve(R"({"schema_version":"DISCOUNT_CURVE_V1"})");
  EXPECT_EQ(incomplete.kind(), ContractViolationKind::kMissingRequiredField);
  EXPECT_EQ(incomplete.pointer(), "/curve_id") << incomplete.pointer();
}
