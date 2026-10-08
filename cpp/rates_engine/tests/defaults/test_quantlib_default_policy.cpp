// QuantLib defaults audit (Issue #226 section 3.6/3.7).
//
// POLICY BEING TESTED: no Shiori methodology decision may come from a QuantLib DEFAULT. QuantLib's
// most dangerous default is its process-global evaluation date: when it is unset the library follows
// the wall clock, so a pricing call could silently depend on when it ran. The adapter therefore never
// lets a QuantLib object be constructed before an EXPLICIT evaluation date is installed, and restores
// the previous state exactly afterwards.
//
// This directory is one of the two test locations allowed to include QuantLib headers, precisely so
// that the global state can be OBSERVED rather than asserted from the adapter's own bookkeeping.
#include <gtest/gtest.h>

#include <ql/settings.hpp>
#include <ql/time/date.hpp>

#include <string>

#include "shiori_rates/quantlib_adapter/adapter.hpp"

namespace {

using Shiori::rates::adapter::QuantLibSettingsGuard;

[[nodiscard]] QuantLib::Date current_evaluation_date() {
  return QuantLib::Settings::instance().evaluationDate();
}

}  // namespace

TEST(QuantLibDefaults, GuardInstallsAnExplicitEvaluationDateThatQuantLibItselfReports) {
  // A raw-null QuantLib date means "no explicit date / follow the clock". That state must never be
  // what a pricing call sees, so the guard installs an explicit date and this test reads it back
  // through QuantLib, not through the adapter.
  const QuantLib::Date before = current_evaluation_date();
  {
    QuantLibSettingsGuard guard("2026-06-10");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
    const QuantLib::Date observed = current_evaluation_date();
    EXPECT_TRUE(observed == QuantLib::Date(10, QuantLib::June, 2026))
        << "the guard must install the requested date, not merely claim to";
    EXPECT_FALSE(observed == QuantLib::Date())
        << "an unset evaluation date would let QuantLib follow the clock";
  }
  // Restoration is exact, including the UNSET state: writing a substitute date here would change
  // process-global behaviour for every later caller.
  const QuantLib::Date after = current_evaluation_date();
  if (before == QuantLib::Date()) {
    EXPECT_TRUE(after == QuantLib::Date())
        << "an unset evaluation date must be restored as unset, not as a concrete date";
  } else {
    EXPECT_TRUE(after == before);
  }
}

TEST(QuantLibDefaults, GuardPreservesUnrelatedProcessGlobalSettings) {
  QuantLib::Settings& settings = QuantLib::Settings::instance();
  const bool original = settings.enforcesTodaysHistoricFixings();
  settings.enforcesTodaysHistoricFixings() = true;
  EXPECT_TRUE(QuantLib::Settings::instance().enforcesTodaysHistoricFixings());
  {
    QuantLibSettingsGuard guard("2026-06-10");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
  }
  // The flag is part of the process-global state the guard is responsible for; leaving it flipped
  // would make a later valuation depend on an earlier caller.
  EXPECT_TRUE(QuantLib::Settings::instance().enforcesTodaysHistoricFixings());
  settings.enforcesTodaysHistoricFixings() = original;
}

TEST(QuantLibDefaults, IdentityNamesTheGlobalStateTheBoundaryOwns) {
  const auto& identity = Shiori::rates::adapter::quantlib_identity();
  // G-2 requires more than a version number: the macro configuration is part of dependency identity.
  EXPECT_NE(identity.macro_configuration.find("QL_ENABLE_SESSIONS="), std::string::npos);
  EXPECT_NE(identity.macro_configuration.find("QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN="),
            std::string::npos);
  // The guard's subject has to be named: an unstated process-global surface is not audited.
  EXPECT_NE(identity.global_settings_quantifiable.find("evaluation_date"), std::string::npos);
  EXPECT_NE(identity.global_settings_quantifiable.find("enforces_todays_historic_fixings"),
            std::string::npos);
  EXPECT_TRUE(Shiori::rates::adapter::quantlib_linked());
}
