// Deterministic cache key (#226 sections 7.2/7.6 and 8.2).
//
// A cache key must be a function of EXPLICIT identity only, and an unknown or empty identity must make
// the key non-reusable rather than merely "different". A key that silently omits the engine version
// can serve a result computed by a different engine, which is the failure mode these tests exist to
// prevent.
#include <gtest/gtest.h>

#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/immutable.hpp"

namespace {

using Shiori::rates::dto::CacheKey;
using Shiori::rates::dto::CacheKeyComponents;

[[nodiscard]] CacheKeyComponents complete_components() {
  CacheKeyComponents components;
  components.inputs_fingerprint = std::string(64, 'a');
  components.engine_version = "0.1.0";
  components.methodology_id = "USD_SOFR_OIS_V1";
  components.methodology_version = "1";
  components.quantlib_version = "1.43.0";
  components.quantlib_macro_configuration = "QL_ENABLE_SESSIONS=OFF";
  components.acquisition_mode = "VCPKG";
  components.dependency_baseline_and_triplet =
      "2750401336fb7c95f6619657a46a7e798661341c:x64-windows-static";
  components.build_numeric_flags = "/fp:precise (fast-math forbidden)";
  return components;
}

}  // namespace

TEST(CacheKey, IsDeterministicAndReusableWhenEveryRequiredComponentIsPresent) {
  const CacheKey first = CacheKey::from_components(complete_components(), "");
  const CacheKey second = CacheKey::from_components(complete_components(), "");
  EXPECT_TRUE(first.is_reusable());
  EXPECT_EQ(first.digest, second.digest);
  // The digest must be the digest of the canonical component preimage, so a peer can recompute it.
  EXPECT_EQ(first.digest, first.compute_digest());
  EXPECT_EQ(first.digest.size(), 64U);

  const CacheKey reparsed = CacheKey::from_canonical(first.to_canonical(), "");
  EXPECT_EQ(reparsed.digest, first.digest);
  EXPECT_EQ(reparsed.to_canonical().dump(), first.to_canonical().dump());
}

TEST(CacheKey, IsRefusedWhenARequiredComponentIsEmpty) {
  // An absent engine version means "unknown identity", and #226 section 7.6 makes an unknown identity
  // non-reusable. It must be refused at construction, not silently hashed as an empty value.
  CacheKeyComponents components = complete_components();
  components.engine_version.clear();
  EXPECT_THROW((void)CacheKey::from_components(components, ""), Shiori::rates::dto::ContractViolation);

  CacheKey unusable;
  unusable.components = components;
  unusable.digest = unusable.compute_digest();
  std::string reason;
  EXPECT_FALSE(unusable.is_reusable(&reason));
  EXPECT_NE(reason.find("engine_version"), std::string::npos) << reason;

  // Every required component must be enforced the same way: an expression like "the framework version
  // is optional" is how a cache starts mixing incompatible results.
  const char* const required[] = {"inputs_fingerprint", "methodology_id", "methodology_version",
                                  "quantlib_version", "acquisition_mode",
                                  "dependency_baseline_and_triplet", "build_numeric_flags"};
  for (const char* name : required) {
    CacheKeyComponents partial = complete_components();
    if (std::string(name) == "inputs_fingerprint") {
      partial.inputs_fingerprint.clear();
    } else if (std::string(name) == "methodology_id") {
      partial.methodology_id.clear();
    } else if (std::string(name) == "methodology_version") {
      partial.methodology_version.clear();
    } else if (std::string(name) == "quantlib_version") {
      partial.quantlib_version.clear();
    } else if (std::string(name) == "acquisition_mode") {
      partial.acquisition_mode.clear();
    } else if (std::string(name) == "dependency_baseline_and_triplet") {
      partial.dependency_baseline_and_triplet.clear();
    } else {
      partial.build_numeric_flags.clear();
    }
    CacheKey key;
    key.components = partial;
    key.digest = key.compute_digest();
    std::string partial_reason;
    EXPECT_FALSE(key.is_reusable(&partial_reason)) << name;
    EXPECT_NE(partial_reason.find(name), std::string::npos) << partial_reason;
    EXPECT_THROW((void)CacheKey::from_components(partial, ""),
                 Shiori::rates::dto::ContractViolation);
  }
}

TEST(CacheKey, DistinguishesEveryComponentThatMustNotCollide) {
  // Each of these differences means the cached value was produced under a different configuration.
  // Sharing a key across them is a wrong-number bug, not a performance issue.
  const CacheKey baseline = CacheKey::from_components(complete_components(), "");

  CacheKeyComponents engine_changed = complete_components();
  engine_changed.engine_version = "0.2.0";
  CacheKeyComponents quantlib_changed = complete_components();
  quantlib_changed.quantlib_version = "1.44.0";
  CacheKeyComponents macros_changed = complete_components();
  macros_changed.quantlib_macro_configuration = "QL_ENABLE_SESSIONS=ON";
  CacheKeyComponents baseline_changed = complete_components();
  baseline_changed.dependency_baseline_and_triplet =
      "2750401336fb7c95f6619657a46a7e798661341c:x64-linux-shiori-gcc";
  CacheKeyComponents flags_changed = complete_components();
  flags_changed.build_numeric_flags = "-ffp-contract=off (no -Ofast)";
  CacheKeyComponents inputs_changed = complete_components();
  inputs_changed.inputs_fingerprint = std::string(64, 'b');

  EXPECT_NE(CacheKey::from_components(engine_changed, "").digest, baseline.digest);
  EXPECT_NE(CacheKey::from_components(quantlib_changed, "").digest, baseline.digest);
  EXPECT_NE(CacheKey::from_components(macros_changed, "").digest, baseline.digest);
  EXPECT_NE(CacheKey::from_components(baseline_changed, "").digest, baseline.digest);
  EXPECT_NE(CacheKey::from_components(flags_changed, "").digest, baseline.digest);
  EXPECT_NE(CacheKey::from_components(inputs_changed, "").digest, baseline.digest);
}

TEST(CacheKey, RefusesATamperedDigest) {
  const CacheKey key = CacheKey::from_components(complete_components(), "");
  const std::string tampered_digest =
      key.digest.substr(0, 63) + (key.digest[63] == '0' ? "1" : "0");

  Shiori::rates::dto::CanonicalMembers members;
  members.emplace_back("components", key.components.to_canonical());
  members.emplace_back("digest", Shiori::rates::dto::CanonicalValue(tampered_digest));
  EXPECT_THROW((void)CacheKey::from_canonical(
                   Shiori::rates::dto::CanonicalValue::make_object(std::move(members)), ""),
               Shiori::rates::dto::ContractViolation);

  CacheKey tampered = key;
  tampered.digest = tampered_digest;
  std::string reason;
  EXPECT_FALSE(tampered.is_reusable(&reason));
  EXPECT_FALSE(reason.empty());
}
