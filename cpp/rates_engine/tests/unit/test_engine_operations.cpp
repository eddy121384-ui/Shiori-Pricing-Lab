// Engine identity and the two operations #227 implements.
//
// #227 prices nothing, so the evidence here is deliberately about identity and fail-closed behaviour:
// the engine must report a deterministic identity, and it must refuse anything that is not a valid V1
// kernel input WITHOUT returning a payload that a caller could mistake for a validated request.
#include <gtest/gtest.h>

#include <string>

#include "shiori_rates/engine/engine.hpp"

namespace {

using Shiori::rates::engine::BuildIdentity;
using Shiori::rates::engine::OperationOutcome;

[[nodiscard]] bool contains(const std::string& haystack, const char* needle) {
  return haystack.find(needle) != std::string::npos;
}

}  // namespace

TEST(EngineIdentity, IsDeterministicAndNamesTheBoundaryItActuallyImplements) {
  EXPECT_EQ(Shiori::rates::engine::engine_name(), "shiori_rates_engine");
  EXPECT_FALSE(Shiori::rates::engine::engine_version().empty());
  EXPECT_NE(Shiori::rates::engine::engine_version().find('.'), std::string::npos);

  // The method token must not name a pricing methodology: #227 implements no pricing method, and a
  // token like "BLACK_76" would claim behaviour this build does not have.
  EXPECT_EQ(Shiori::rates::engine::engine_method(), "SCHEMA_VALIDATION_ONLY");

  const auto identity = Shiori::rates::engine::identity();
  EXPECT_EQ(identity.engine_name, std::string(Shiori::rates::engine::engine_name()));
  EXPECT_EQ(identity.engine_version, std::string(Shiori::rates::engine::engine_version()));
  EXPECT_EQ(identity.method, std::string(Shiori::rates::engine::engine_method()));
  // Two reads must agree: a version that can change between reads cannot be a cache-key component.
  EXPECT_EQ(Shiori::rates::engine::identity().engine_version, identity.engine_version);
}

TEST(BuildIdentity, IsPopulatedAndCarriesNoFastMathConfiguration) {
  const BuildIdentity& build = Shiori::rates::engine::build_identity();
  EXPECT_FALSE(build.build_type.empty());
  EXPECT_FALSE(build.compiler.empty());
  EXPECT_FALSE(build.acquisition_mode.empty());
  EXPECT_FALSE(build.numeric_flags.empty());
  EXPECT_FALSE(build.git_revision.empty());
  EXPECT_FALSE(build.sanitizers.empty());

  // The acquisition mode is one of the two sanctioned values; there is no implicit downgrade between
  // them, so a third value would mean the build cannot say how its dependencies arrived.
  EXPECT_TRUE(build.acquisition_mode == "VCPKG" || build.acquisition_mode == "FETCHCONTENT");

  // Numeric policy is enforced by the build options, and this asserts that the REPORTED configuration
  // agrees with the policy: a build that reports fast-math would make every number it produces
  // non-comparable with a compliant build.
  EXPECT_FALSE(contains(build.numeric_flags, "Ofast"));
  EXPECT_FALSE(contains(build.numeric_flags, "/fp:fast"));
}

TEST(ValidateKernelInput, RefusesAnEmptyDocumentWithoutReturningAPayload) {
  const OperationOutcome outcome = Shiori::rates::engine::validate_kernel_input("{}");
  EXPECT_FALSE(outcome.success);
  EXPECT_FALSE(outcome.refusal_token.empty());
  EXPECT_FALSE(outcome.refusal_message.empty());
  // No partial payload may be returned: an empty payload string is how the transport layer knows
  // there is nothing to present as an accepted request.
  EXPECT_TRUE(outcome.payload_json.empty());
  EXPECT_TRUE(outcome.fingerprint.empty());
}

TEST(ValidateKernelInput, RefusesMalformedJsonAtTheTransportBoundary) {
  const OperationOutcome outcome = Shiori::rates::engine::validate_kernel_input("not json");
  EXPECT_FALSE(outcome.success);
  EXPECT_FALSE(outcome.refusal_token.empty());
  EXPECT_TRUE(outcome.payload_json.empty());
}

TEST(CanonicalizeKernelInput, RefusesANonObjectDocument) {
  const OperationOutcome outcome = Shiori::rates::engine::canonicalize_kernel_input("[]");
  EXPECT_FALSE(outcome.success);
  EXPECT_FALSE(outcome.refusal_token.empty());
  EXPECT_TRUE(outcome.payload_json.empty());
}

TEST(Operations, AreDeterministicForTheSameInput) {
  // Determinism is the whole point of the canonical contract: the same bytes must yield the same
  // refusal, the same token and the same pointer on every run, or a failure cannot be reproduced.
  const char* const document = R"({"schema_version":"RATES_KERNEL_INPUT_V1","valuation_product":1})";
  const OperationOutcome first = Shiori::rates::engine::validate_kernel_input(document);
  const OperationOutcome second = Shiori::rates::engine::validate_kernel_input(document);
  EXPECT_EQ(first.success, second.success);
  EXPECT_EQ(first.refusal_token, second.refusal_token);
  EXPECT_EQ(first.refusal_pointer, second.refusal_pointer);
  EXPECT_EQ(first.refusal_message, second.refusal_message);
  EXPECT_EQ(first.payload_json, second.payload_json);
}
