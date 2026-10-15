// QuantLib settings guard and serialization gate — behaviour that must not depend on QuantLib.
//
// These tests use only the adapter's QuantLib-free public header, which is itself the point: a caller
// cannot reach QuantLib through this boundary. The test that OBSERVES QuantLib's process-global state
// directly lives in tests/defaults/, which is one of the two directories allowed to include QuantLib.
#include <gtest/gtest.h>

#include <cstddef>
#include <string>
#include <vector>

#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/quantlib_adapter/adapter.hpp"

namespace {

using Shiori::rates::adapter::QuantLibSettingsGuard;
using Shiori::rates::adapter::SerializationGate;

// The `;`-separated `NAME=ON|OFF` macro configuration, split into its tokens.
[[nodiscard]] std::vector<std::string> macro_tokens(const std::string& configuration) {
  std::vector<std::string> tokens;
  std::string current;
  for (const char character : configuration) {
    if (character == ';') {
      tokens.push_back(current);
      current.clear();
    } else {
      current += character;
    }
  }
  tokens.push_back(current);
  return tokens;
}

}  // namespace

TEST(SettingsGuard, InstallsTheRequestedDateAndCleansUpWithoutPoisoning) {
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned())
      << "another test already poisoned this process: fail-closed state is process-wide";
  const SerializationGate::Counters before = SerializationGate::counters();
  {
    QuantLibSettingsGuard guard("2026-06-10");
    EXPECT_TRUE(guard.satisfied()) << guard.refusal_reason();
    EXPECT_EQ(guard.requested_iso_date(), "2026-06-10");
    EXPECT_TRUE(guard.refusal_reason().empty());
  }
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned())
      << "a successful guard must leave the adapter usable";
  const SerializationGate::Counters after = SerializationGate::counters();
  // Every guard acquires the process-wide gate, so QuantLib's global state is never touched outside
  // the serialization boundary.
  EXPECT_EQ(after.acquisitions, before.acquisitions + 1);
}

TEST(SettingsGuard, RefusesAMalformedDateWithoutTouchingGlobalStateOrPoisoning) {
  QuantLibSettingsGuard guard("10-06-2026");
  EXPECT_FALSE(guard.satisfied());
  EXPECT_FALSE(guard.refusal_reason().empty());
  // A bad request is not a broken invariant: it must be refused, not poison the process. Poisoning
  // here would turn one caller's typo into an engine-wide outage.
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

TEST(SettingsGuard, RefusesANonExistentCalendarDate) {
  // 2026 is not a leap year, so 2026-02-30 is not a real date. The DTO layer validates calendar
  // dates on decode; this asserts the guard also refuses rather than constructing an invalid QuantLib
  // date.
  QuantLibSettingsGuard guard("2026-02-30");
  EXPECT_FALSE(guard.satisfied());
  EXPECT_FALSE(guard.refusal_reason().empty());
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

TEST(SettingsGuard, RefusesNestingBecauseAnInnerRestoreWouldInvalidateTheOuterVerification) {
  QuantLibSettingsGuard outer("2026-06-10");
  ASSERT_TRUE(outer.satisfied()) << outer.refusal_reason();
  EXPECT_THROW(
      {
        QuantLibSettingsGuard inner("2026-06-11");
        (void)inner.satisfied();
      },
      Shiori::rates::dto::ContractViolation);
  EXPECT_TRUE(outer.satisfied());
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

TEST(SerializationGate, SerializesAcquisitionAndRefusesReentrancy) {
  // The initial production posture disables parallel pricing. This is a compile-time constant so it
  // cannot be flipped by a runtime flag or an environment variable.
  static_assert(!Shiori::rates::adapter::kProductionParallelPricingEnabled,
                "parallel pricing must remain disabled under the initial posture");

  const SerializationGate::Counters before = SerializationGate::counters();
  {
    const SerializationGate gate = SerializationGate::acquire("test_operation");
    EXPECT_TRUE(gate.held());
    EXPECT_EQ(gate.operation(), "test_operation");
    // Re-entering on the same thread must be REFUSED, not deadlocked: a deadlock would present as an
    // unavailable engine with no diagnosis.
    EXPECT_THROW(
        {
          const SerializationGate inner = SerializationGate::acquire("nested");
          (void)inner.held();
        },
        Shiori::rates::dto::ContractViolation);
  }
  const SerializationGate::Counters after = SerializationGate::counters();
  EXPECT_EQ(after.acquisitions, before.acquisitions + 1);
  EXPECT_EQ(after.reentrant_refusals, before.reentrant_refusals + 1);
  // Releasing the gate must restore reentrancy availability: a leaked flag would make the engine
  // permanently unusable after one nested call.
  const SerializationGate again = SerializationGate::acquire("after_release");
  EXPECT_TRUE(again.held());
}

TEST(DependencyIdentity, ReportsAPinnedAndCompleteIdentity) {
  const auto& identity = Shiori::rates::adapter::quantlib_identity();
  // QuantLib is pinned to 1.43 by #226 section 3.2, and the adapter refuses to build against another
  // version. The runtime identity must agree with that pin.
  EXPECT_EQ(identity.major, 1);
  EXPECT_EQ(identity.minor, 43);
  EXPECT_EQ(identity.version.rfind("1.43", 0), 0U) << identity.version;
  EXPECT_TRUE(identity.complete());
  EXPECT_FALSE(identity.macro_configuration.empty());
  EXPECT_FALSE(identity.build_platform.empty());
  // The macro configuration is part of the dependency identity because the same version built with
  // different macros is a different dependency (G-2).
  EXPECT_NE(identity.macro_configuration.find("QL_ENABLE_SESSIONS="), std::string::npos)
      << identity.macro_configuration;
  // docs/34 section 5.5 C10 names five behaviour switches. All five are reported, and each token states
  // the value this translation unit compiled against, read from the resolved QuantLib headers (the same
  // evidence channel as the version pin) rather than an assumed default. Section 4.4 is why they matter:
  // a mismatch changes class layout, so two builds differing in one switch are not the same dependency.
  for (const char* required :
       {"QL_ENABLE_SESSIONS=", "QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN=",
        "QL_FASTER_LAZY_OBJECTS=", "QL_THROW_IN_CYCLES=", "QL_ENABLE_OPENMP=",
        "QL_HIGH_RESOLUTION_DATE=", "QL_DISABLE_DEPRECATED=", "QL_USE_INDEXED_COUPON="}) {
    EXPECT_NE(identity.macro_configuration.find(required), std::string::npos)
        << required << " is absent from " << identity.macro_configuration;
  }

  // The configuration is a non-empty, sorted, duplicate-free list of `NAME=ON|OFF` tokens: a switch can
  // never be reported without its value, no token is ever inferred, and the string stays byte-stable.
  const std::vector<std::string> tokens = macro_tokens(identity.macro_configuration);
  ASSERT_FALSE(tokens.empty());
  for (std::size_t index = 0; index < tokens.size(); ++index) {
    if (index > 0) {
      EXPECT_LT(tokens[index - 1], tokens[index])
          << "the macro configuration must be sorted and duplicate-free at " << tokens[index];
    }
    const std::size_t equals = tokens[index].find('=');
    ASSERT_NE(equals, std::string::npos) << tokens[index] << " reports no value";
    const std::string value = tokens[index].substr(equals + 1);
    EXPECT_TRUE(value == "ON" || value == "OFF")
        << tokens[index] << " must report the compiled ON/OFF value";
  }
  // The identity has to state the process-global surface the adapter is guarding.
  EXPECT_NE(identity.global_settings_quantifiable.find("evaluation_date"), std::string::npos);
  EXPECT_TRUE(Shiori::rates::adapter::quantlib_linked());
}
