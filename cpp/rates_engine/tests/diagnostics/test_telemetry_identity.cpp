// Lane B `RATES_RUNTIME_TELEMETRY_V1` self-identity: the CLI encode -> parse -> decode -> re-encode
// round trip, the non-recursive preimage property, and the two tampered-label refusals.
//
// Authority: Sophira owner decision 6117299766 section B (locked, non-recursive):
//   * the content-fingerprint preimage excludes BOTH the document's own `content_fingerprint` AND its
//     own derived `telemetry_id`;
//   * `telemetry_id = fingerprint_of("telemetry_version:content_fingerprint")`, the same derivation on
//     encode and on decode, never a timestamp/pointer/counter;
//   * a decode independently verifies BOTH declared labels and never silently repairs one.
//
// Lane B is out of band: it is not a member of #225 `PricingResult`/`RiskResult`, so these tests touch
// no #225 / Lane A preimage. The construction path mirrors
// `tools/rates_engine_cli/main.cpp --operation telemetry`: fill the observations and metadata, then
// `assign_derived_identities`, then `to_canonical`.
#include <gtest/gtest.h>

#include <cstddef>
#include <string>
#include <utility>

#include "shiori_rates/diagnostics/runtime_telemetry.hpp"
#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/units.hpp"

namespace {

using Shiori::rates::diagnostics::CacheLayerObservation;
using Shiori::rates::diagnostics::PhaseTiming;
using Shiori::rates::diagnostics::RuntimeTelemetry;
using Shiori::rates::diagnostics::assign_derived_identities;
using Shiori::rates::diagnostics::dependency_instrumentation_for_triplet;
using Shiori::rates::diagnostics::telemetry_content_fingerprint;
using Shiori::rates::dto::CacheState;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::fingerprint_of;
using Shiori::rates::dto::parse_canonical_json;

// Literal test inputs, not observations of any run: the values only have to be complete and distinct
// enough that each participating field can be shown to move the fingerprint.
[[nodiscard]] RuntimeTelemetry build_cli_lane_b_telemetry() {
  RuntimeTelemetry telemetry;
  telemetry.telemetry_version = "RATES_RUNTIME_TELEMETRY_V1";
  telemetry.correlation_result_fingerprint = "unavailable-for-this-operation";
  telemetry.correlation_inputs_fingerprint = "unavailable-for-this-operation";
  telemetry.metadata.engine_version = "0.1.0";
  telemetry.metadata.quantlib_version = "1.35";
  telemetry.metadata.quantlib_macro_configuration = "QL_USE_STD_SHARED_PTR";
  telemetry.metadata.acquisition_mode = "vcpkg-manifest";
  telemetry.metadata.vcpkg_baseline = "0000000000000000000000000000000000000000";
  telemetry.metadata.vcpkg_triplet = "x64-windows";
  // Two DISTINCT instrumentation facts: the Shiori build's own sanitizer mode and the dependency
  // closure's instrumentation state. `x64-windows` is not one of the two pinned sanitizer triplets, so
  // the only honest dependency state here is the explicit no-evidence token, and it must never be filled
  // in from the Shiori mode.
  telemetry.metadata.dependency_instrumentation = "UNVERIFIED";
  telemetry.metadata.build_type = "Release";
  telemetry.metadata.build_sanitizers = "NONE";
  telemetry.metadata.compiler = "msvc/19.44";
  telemetry.metadata.numeric_flags = "/fp:precise (fast-math forbidden)";
  telemetry.metadata.benchmark_name = "none";
  telemetry.metadata.fixture_id = "none";

  // G-4: the cache is not implemented in #227, so the honest observation is DISABLED with zero
  // activity, exactly as the CLI reports it.
  CacheLayerObservation layer;
  layer.layer_id = "kernel_input";
  layer.state = CacheState::kDisabled;
  layer.key_digest = "none";
  telemetry.cache_layers.push_back(std::move(layer));

  telemetry.execution_path.path_id = "VALIDATE_ONLY";
  telemetry.execution_path.served_from_cache = false;

  telemetry.gate.mode = "SERIALIZED";
  telemetry.gate.gate_id = "shiori_rates_quantlib_gate";
  telemetry.gate.acquisitions = 3;
  telemetry.gate.waits = 1;
  telemetry.gate.reentrant_refusals = 0;

  PhaseTiming phase;
  phase.phase_id = "validate_kernel_input";
  phase.milliseconds = 0.125;
  telemetry.phases.push_back(std::move(phase));
  return telemetry;
}

// The CLI path through `assign_derived_identities`, so the round-trip test starts from real encode
// input.
[[nodiscard]] RuntimeTelemetry cli_lane_b_telemetry_with_identities() {
  RuntimeTelemetry telemetry = build_cli_lane_b_telemetry();
  assign_derived_identities(telemetry);
  return telemetry;
}

// Replaces the value of one top-level string member of an already-canonical document, so the only
// difference from the encoded document is that one label. A missing needle is a test bug and is
// reported rather than silently returning the untampered text.
[[nodiscard]] std::string tamper_top_level_string_member(const std::string& json,
                                                         const std::string& member,
                                                         const std::string& original,
                                                         const std::string& tampered) {
  const std::string needle = "\"" + member + "\":\"" + original + "\"";
  const std::size_t position = json.find(needle);
  if (position == std::string::npos) {
    ADD_FAILURE() << "member " << member << " with value " << original << " is not present in "
                  << json;
    return json;
  }
  std::string result = json;
  result.replace(position, needle.size(), "\"" + member + "\":\"" + tampered + "\"");
  return result;
}

// A different label of the same 64-lowercase-hex shape, so a refusal can only be the label
// comparison and never a shape check.
[[nodiscard]] std::string flip_first_hex_character(std::string token) {
  if (token.empty()) {
    ADD_FAILURE() << "a derived Lane B label must not be empty";
    return token;
  }
  token[0] = (token[0] == 'a') ? 'b' : 'a';
  return token;
}

// Removes one `"member":"value"` pair from an already-canonical document, together with one adjacent
// comma, so the remaining document is still valid canonical JSON. A missing needle is a test bug and is
// reported rather than silently leaving the member in place.
[[nodiscard]] std::string drop_string_member(const std::string& json, const std::string& member,
                                             const std::string& value) {
  const std::string needle = "\"" + member + "\":\"" + value + "\"";
  const std::size_t position = json.find(needle);
  if (position == std::string::npos) {
    ADD_FAILURE() << "member " << member << " with value " << value << " is not present in " << json;
    return json;
  }
  std::string result = json;
  std::size_t begin = position;
  std::size_t length = needle.size();
  if (begin > 0 && result[begin - 1] == ',') {
    --begin;
    ++length;
  } else if (begin + length < result.size() && result[begin + length] == ',') {
    ++length;
  }
  result.erase(begin, length);
  return result;
}

// Replaces one phase's `milliseconds` token in an already-canonical document, so the only difference
// from the encoded document is that one numeric token. A missing needle is a test bug and is reported
// rather than silently returning the untampered text.
[[nodiscard]] std::string with_phase_milliseconds_token(const std::string& json,
                                                        const std::string& original_token,
                                                        const std::string& replacement_token) {
  const std::string needle = "\"milliseconds\":" + original_token;
  const std::size_t position = json.find(needle);
  if (position == std::string::npos) {
    ADD_FAILURE() << "milliseconds token " << original_token << " is not present in " << json;
    return json;
  }
  std::string result = json;
  result.replace(position, needle.size(), "\"milliseconds\":" + replacement_token);
  return result;
}

// Every document handed to this helper MUST be refused, so a missing refusal fails the test instead
// of being folded into a sentinel that could be confused with a real refusal.
[[nodiscard]] ContractViolation refusal_for_telemetry(const std::string& json) {
  try {
    (void)RuntimeTelemetry::from_canonical(parse_canonical_json(json), "");
  } catch (const ContractViolation& violation) {
    return violation;
  }
  ADD_FAILURE() << "a Lane B telemetry document must be refused: " << json;
  return ContractViolation(ContractViolationKind::kInvariantViolation, "<no refusal>",
                           "no refusal was thrown");
}

}  // namespace

TEST(LaneBTelemetryIdentity, CliEncodeDecodeReencodeIsByteIdentical) {
  const RuntimeTelemetry encoded_telemetry = cli_lane_b_telemetry_with_identities();
  const std::string encoded = encoded_telemetry.to_canonical().dump();

  const RuntimeTelemetry decoded =
      RuntimeTelemetry::from_canonical(parse_canonical_json(encoded), "");

  // Byte-identical re-encode: the decoder neither normalizes nor repairs the document.
  EXPECT_EQ(decoded.to_canonical().dump(), encoded);
  EXPECT_EQ(decoded.telemetry_version, encoded_telemetry.telemetry_version);
  EXPECT_EQ(decoded.telemetry_id, encoded_telemetry.telemetry_id);
  EXPECT_EQ(decoded.content_fingerprint, encoded_telemetry.content_fingerprint);
  // The id is exactly the documented version-plus-fingerprint derivation.
  EXPECT_EQ(encoded_telemetry.telemetry_id,
            fingerprint_of(CanonicalValue(encoded_telemetry.telemetry_version + ":" +
                                          encoded_telemetry.content_fingerprint)));
}

TEST(LaneBTelemetryIdentity, PreimageExcludesBothOwnIdentityFieldsAndKeepsObservations) {
  const RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  const std::string baseline = telemetry_content_fingerprint(telemetry);

  // Rule 1: neither of the document's own identity labels is an input to its own fingerprint.
  RuntimeTelemetry other_id = telemetry;
  other_id.telemetry_id = flip_first_hex_character(telemetry.telemetry_id);
  EXPECT_EQ(telemetry_content_fingerprint(other_id), baseline);

  RuntimeTelemetry other_fingerprint = telemetry;
  other_fingerprint.content_fingerprint = flip_first_hex_character(telemetry.content_fingerprint);
  EXPECT_EQ(telemetry_content_fingerprint(other_fingerprint), baseline);

  // Rule 2: genuine observations still participate; the preimage is not emptied.
  RuntimeTelemetry other_gate = telemetry;
  other_gate.gate.waits += 1;
  EXPECT_NE(telemetry_content_fingerprint(other_gate), baseline);

  RuntimeTelemetry other_timing = telemetry;
  other_timing.phases[0].milliseconds += 0.5;
  EXPECT_NE(telemetry_content_fingerprint(other_timing), baseline);

  RuntimeTelemetry other_cache = telemetry;
  other_cache.cache_layers[0].misses += 1;
  EXPECT_NE(telemetry_content_fingerprint(other_cache), baseline);

  RuntimeTelemetry other_path = telemetry;
  other_path.execution_path.served_from_cache = true;
  EXPECT_NE(telemetry_content_fingerprint(other_path), baseline);

  RuntimeTelemetry other_metadata = telemetry;
  other_metadata.metadata.compiler = "msvc/19.45";
  EXPECT_NE(telemetry_content_fingerprint(other_metadata), baseline);

  RuntimeTelemetry other_correlation = telemetry;
  other_correlation.correlation_result_fingerprint = "some-result-fingerprint";
  EXPECT_NE(telemetry_content_fingerprint(other_correlation), baseline);

  RuntimeTelemetry other_version = telemetry;
  other_version.telemetry_version = "RATES_RUNTIME_TELEMETRY_V2";
  EXPECT_NE(telemetry_content_fingerprint(other_version), baseline);
}

TEST(LaneBTelemetryIdentity, RefusesATamperedTelemetryId) {
  const RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  const std::string encoded = telemetry.to_canonical().dump();
  const std::string tampered = tamper_top_level_string_member(
      encoded, "telemetry_id", telemetry.telemetry_id,
      flip_first_hex_character(telemetry.telemetry_id));

  const ContractViolation violation = refusal_for_telemetry(tampered);
  // `telemetry_id` is excluded from the content-fingerprint preimage, so the declared
  // content_fingerprint still matches and the refusal is attributable to the id alone.
  EXPECT_EQ(violation.kind(), ContractViolationKind::kIdentityMismatch);
  EXPECT_EQ(violation.pointer(), "/telemetry_id");
}

TEST(LaneBTelemetryIdentity, RefusesATamperedContentFingerprint) {
  const RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  const std::string encoded = telemetry.to_canonical().dump();
  const std::string tampered = tamper_top_level_string_member(
      encoded, "content_fingerprint", telemetry.content_fingerprint,
      flip_first_hex_character(telemetry.content_fingerprint));

  const ContractViolation violation = refusal_for_telemetry(tampered);
  EXPECT_EQ(violation.kind(), ContractViolationKind::kFingerprintMismatch);
  EXPECT_EQ(violation.pointer(), "/content_fingerprint");
}

TEST(LaneBTelemetryIdentity, KeepsTheStageOneVersionFence) {
  // The retained Stage-1 fence: an absent or non-string version is a VERSION refusal at the version
  // member, before any other member is read, and V1 is never assumed.
  const ContractViolation absent = refusal_for_telemetry("{}");
  EXPECT_EQ(absent.kind(), ContractViolationKind::kUnknownSchemaVersion);
  EXPECT_EQ(absent.pointer(), "/telemetry_version");

  const ContractViolation non_string = refusal_for_telemetry(R"({"telemetry_version":1})");
  EXPECT_EQ(non_string.kind(), ContractViolationKind::kUnknownSchemaVersion);
  EXPECT_EQ(non_string.pointer(), "/telemetry_version");
}

// ------------------------------------------------------------------------------------------------
// M4 dependency-instrumentation fact (docs/34 section 10.5 line 1768, sections 5.7 T3/11.3).
// ------------------------------------------------------------------------------------------------

TEST(LaneBTelemetryInstrumentation, NamesTheDependencyStateOnlyFromSourceBackedTriplets) {
  // The two pinned sanitizer triplets set the sanitizer in VCPKG_CXX_FLAGS/VCPKG_C_FLAGS/
  // VCPKG_LINKER_FLAGS for the whole dependency closure, so those two triplet tokens are the only ones
  // that back a named dependency state.
  EXPECT_EQ(dependency_instrumentation_for_triplet("x64-linux-shiori-tsan"), "THREAD");
  EXPECT_EQ(dependency_instrumentation_for_triplet("x64-linux-shiori-asan"), "ADDRESS+UNDEFINED");

  // Every other triplet - the non-sanitizer Shiori triplets, the Windows triplet, and the NONE fallback
  // used when no vcpkg triplet is configured - reports the EXPLICIT no-evidence state. It is never
  // upgraded to a named instrumentation state and never downgraded to a claim of "uninstrumented",
  // because a runtime check here cannot prove the absence of instrumentation in a pre-built library.
  for (const std::string& triplet :
       {std::string("x64-windows-static"), std::string("x64-linux-shiori-gcc"),
        std::string("x64-linux-shiori-clang"), std::string("NONE"), std::string("x64-linux"),
        std::string("x64-linux-shiori-tsan-dbg"), std::string("")}) {
    EXPECT_EQ(dependency_instrumentation_for_triplet(triplet), "UNVERIFIED") << triplet;
  }
}

TEST(LaneBTelemetryInstrumentation, KeepsTheTwoInstrumentationFactsDistinct) {
  // A Shiori-only sanitizer build must never be readable as an instrumented dependency: the two facts
  // are separate members with separate evidence, and both survive the wire round trip unchanged.
  RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  telemetry.metadata.build_sanitizers = "THREAD";
  telemetry.metadata.dependency_instrumentation = "UNVERIFIED";
  assign_derived_identities(telemetry);
  const std::string encoded = telemetry.to_canonical().dump();

  const RuntimeTelemetry decoded =
      RuntimeTelemetry::from_canonical(parse_canonical_json(encoded), "");
  EXPECT_EQ(decoded.metadata.build_sanitizers, "THREAD");
  EXPECT_EQ(decoded.metadata.dependency_instrumentation, "UNVERIFIED");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded);

  // Each fact participates in the fingerprint independently, so a measurement can never be compared
  // across a change in either one.
  const std::string baseline = telemetry_content_fingerprint(telemetry);
  RuntimeTelemetry other_build_mode = telemetry;
  other_build_mode.metadata.build_sanitizers = "ADDRESS+UNDEFINED";
  EXPECT_NE(telemetry_content_fingerprint(other_build_mode), baseline);

  RuntimeTelemetry other_dependency_state = telemetry;
  other_dependency_state.metadata.dependency_instrumentation = "THREAD";
  EXPECT_NE(telemetry_content_fingerprint(other_dependency_state), baseline);
}

TEST(LaneBTelemetryInstrumentation, RefusesAV1DocumentThatOmitsEitherInstrumentationFact) {
  // The completed V1 metadata shape is ONE shape: both instrumentation facts are required, so a partial
  // or pre-completion document is refused at the missing member's own pointer instead of being silently
  // defaulted to an empty or inferred state.
  const RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  const std::string encoded = telemetry.to_canonical().dump();

  const ContractViolation no_build_mode =
      refusal_for_telemetry(drop_string_member(encoded, "build_sanitizers", "NONE"));
  EXPECT_EQ(no_build_mode.kind(), ContractViolationKind::kMissingRequiredField);
  EXPECT_EQ(no_build_mode.pointer(), "/metadata/build_sanitizers");

  const ContractViolation no_dependency_state =
      refusal_for_telemetry(drop_string_member(encoded, "dependency_instrumentation", "UNVERIFIED"));
  EXPECT_EQ(no_dependency_state.kind(), ContractViolationKind::kMissingRequiredField);
  EXPECT_EQ(no_dependency_state.pointer(), "/metadata/dependency_instrumentation");

  // A bare-null or empty replacement is refused too: neither fact may be blank on the wire.
  const std::string emptied = tamper_top_level_string_member(
      encoded, "dependency_instrumentation", "UNVERIFIED", "");
  const ContractViolation empty_dependency_state = refusal_for_telemetry(emptied);
  EXPECT_EQ(empty_dependency_state.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(empty_dependency_state.pointer(), "/metadata/dependency_instrumentation");
}

// ------------------------------------------------------------------------------------------------
// Phase timing exactness: `phases[i].milliseconds` is a double-valued DTO field like any other, so an
// integer token is admitted only when binary64 holds it exactly. Before this rule an integer above
// 2^53 was silently rounded, and a document carrying the fingerprint of the ROUNDED value validated
// while reserialization changed the field.
// ------------------------------------------------------------------------------------------------

TEST(LaneBTelemetryPhaseTiming, KeepsAnExactlyRepresentableIntegerElapsedTime) {
  RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  // 2^53 + 2 and 2^54 + 4 are integers binary64 holds exactly, so the canonical writer emits them as
  // integer tokens and the decoder must read them back unchanged.
  telemetry.phases[0].milliseconds = 9007199254740994.0;
  PhaseTiming second;
  second.phase_id = "price";
  second.milliseconds = 18014398509481988.0;
  telemetry.phases.push_back(second);
  assign_derived_identities(telemetry);
  const std::string encoded = telemetry.to_canonical().dump();

  // The wire really does carry integer tokens, so the path under test is the integer decode path.
  EXPECT_NE(encoded.find("\"milliseconds\":9007199254740994"), std::string::npos);
  EXPECT_NE(encoded.find("\"milliseconds\":18014398509481988"), std::string::npos);

  const RuntimeTelemetry decoded =
      RuntimeTelemetry::from_canonical(parse_canonical_json(encoded), "");
  ASSERT_EQ(decoded.phases.size(), 2U);
  EXPECT_EQ(decoded.phases[0].milliseconds, 9007199254740994.0);
  EXPECT_EQ(decoded.phases[1].milliseconds, 18014398509481988.0);
  EXPECT_EQ(decoded.to_canonical().dump(), encoded);
}

TEST(LaneBTelemetryPhaseTiming, RefusesANonExactIntegerElapsedTime) {
  const RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  const std::string encoded = telemetry.to_canonical().dump();  // "milliseconds":0.125

  // 2^53 + 1, 2^53 + 3, 2^54 + 1 and -(2^53 + 1) are all integers binary64 cannot hold exactly.
  for (const std::string& token : {"9007199254740993", "9007199254740995", "18014398509481985",
                                   "-9007199254740993"}) {
    const ContractViolation violation =
        refusal_for_telemetry(with_phase_milliseconds_token(encoded, "0.125", token));
    // The field rule fires during decode, BEFORE the stale-content fence is reached: the refusal is
    // attributed to the timing pointer and is not a fingerprint mismatch.
    EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation) << token;
    EXPECT_EQ(violation.pointer(), "/phases/0/milliseconds") << token;
  }
}

TEST(LaneBTelemetryPhaseTiming, RefusesANonExactIntegerElapsedTimeEvenWhenTheFingerprintMatches) {
  // The dangerous case the rule exists for: the declared `content_fingerprint` is the fingerprint of the
  // value a ROUNDING decoder would produce, so such a decoder would agree with the declared label and
  // accept a document whose canonical field no longer round-trips.
  RuntimeTelemetry telemetry = cli_lane_b_telemetry_with_identities();
  telemetry.phases[0].milliseconds = 9007199254740992.0;  // 2^53, exactly representable
  assign_derived_identities(telemetry);
  const std::string encoded = telemetry.to_canonical().dump();

  // The untampered document is consistent: its declared label is the fingerprint of the in-memory value,
  // which is also what a rounding decoder would recompute for the forged token below.
  const RuntimeTelemetry decoded =
      RuntimeTelemetry::from_canonical(parse_canonical_json(encoded), "");
  EXPECT_EQ(telemetry_content_fingerprint(decoded), telemetry.content_fingerprint);

  const ContractViolation violation = refusal_for_telemetry(
      with_phase_milliseconds_token(encoded, "9007199254740992", "9007199254740993"));
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/phases/0/milliseconds");
}
