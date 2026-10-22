// QuantLib defaults audit (Issue #226 section 3.6/3.7).
//
// POLICY BEING TESTED: no Shiori methodology decision may come from a QuantLib DEFAULT. QuantLib's
// most dangerous default is its process-global evaluation date: when it is unset the library follows
// the wall clock, so a pricing call could silently depend on when it ran. The adapter therefore never
// lets a QuantLib object be constructed before an EXPLICIT evaluation date is installed, and restores
// the previous state exactly afterwards.
//
// RAW STATE, NOT THE CONVERTED DATE: every observation in this file goes through
// `Settings::instance().evaluationDate().value()` (`ObservableValue<Date>::value() const`).
// `DateProxy::operator Date()` substitutes `Date::todaysDate()` for a raw-null date, so a test written
// through the conversion cannot distinguish "the raw-null / clock-following state was restored" from
// "an explicit today's date was written instead" -- it would pass against a guard with exactly the
// #226 conformance defect these tests exist to catch.
//
// This directory is one of the two test locations allowed to include QuantLib headers, precisely so
// that the global state can be OBSERVED rather than asserted from the adapter's own bookkeeping.
#include <gtest/gtest.h>

#include <ql/settings.hpp>
#include <ql/time/date.hpp>

#include <stdexcept>
#include <string>

#include "shiori_rates/quantlib_adapter/adapter.hpp"

namespace {

using Shiori::rates::adapter::QuantLibSettingsGuard;

// The RAW stored evaluation date: `ObservableValue<Date>::value() const`, never an implicit
// DateProxy -> Date conversion.
[[nodiscard]] QuantLib::Date raw_evaluation_date() {
  return QuantLib::Settings::instance().evaluationDate().value();
}

void seed_raw_null_evaluation_date() {
  QuantLib::Settings::instance().resetEvaluationDate();
  ASSERT_TRUE(raw_evaluation_date() == QuantLib::Date())
      << "the raw-null precondition must be seeded, not assumed from the ambient process state";
}

void seed_explicit_evaluation_date(const QuantLib::Date& date) {
  QuantLib::Settings::instance().evaluationDate() = date;
  ASSERT_TRUE(raw_evaluation_date() == date)
      << "the explicit precondition must be seeded, not assumed from the ambient process state";
}

// Saves and restores the process-global Settings state this file mutates, whatever it was when the
// test started: the raw evaluation date (raw null and explicit are distinct and each is restored as
// itself) and the fixings flag. Without this, a test would leave the process in a state that a later
// test -- or a later caller in the same process -- observes.
class ScopedSettingsState {
 public:
  ScopedSettingsState()
      : evaluation_date_(raw_evaluation_date()),
        evaluation_date_was_raw_null_(evaluation_date_ == QuantLib::Date()),
        enforces_historic_fixings_(
            QuantLib::Settings::instance().enforcesTodaysHistoricFixings()) {}

  ~ScopedSettingsState() {
    if (evaluation_date_was_raw_null_) {
      QuantLib::Settings::instance().resetEvaluationDate();
    } else {
      QuantLib::Settings::instance().evaluationDate() = evaluation_date_;
    }
    QuantLib::Settings::instance().enforcesTodaysHistoricFixings() =
        enforces_historic_fixings_;
  }

  ScopedSettingsState(const ScopedSettingsState&) = delete;
  ScopedSettingsState& operator=(const ScopedSettingsState&) = delete;

 private:
  QuantLib::Date evaluation_date_;
  bool evaluation_date_was_raw_null_;
  bool enforces_historic_fixings_;
};

TEST(QuantLibDefaults, GuardInstallsTheRequestedDateAndRestoresASeededRawNullState) {
  [[maybe_unused]] const ScopedSettingsState prior_state;
  seed_raw_null_evaluation_date();
  const QuantLib::Date before_raw = raw_evaluation_date();

  {
    QuantLibSettingsGuard guard("2026-06-10");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
    // Inside the scope QuantLib itself must report the requested date through the RAW accessor, so
    // the value a pricing call would see is explicit rather than "whatever the clock says".
    EXPECT_TRUE(raw_evaluation_date() == QuantLib::Date(10, QuantLib::June, 2026))
        << "the guard must install the requested date, not merely claim to";
    EXPECT_FALSE(raw_evaluation_date() == QuantLib::Date())
        << "an unset evaluation date would let QuantLib follow the clock";
    // The guard must record the PRIOR state as raw-null. Recording `Date::todaysDate()` here IS the
    // #226 conformance defect: it turns a raw-null state into an explicit date on restoration.
    EXPECT_TRUE(guard.previous_evaluation_date_was_unset());
  }

  const QuantLib::Date after_raw = raw_evaluation_date();
  EXPECT_TRUE(after_raw == QuantLib::Date())
      << "a raw-null (follow-the-clock) evaluation date must be restored as RAW NULL; writing "
         "Date::todaysDate() here is a different, explicit process-global state";
  EXPECT_EQ(after_raw.serialNumber(), before_raw.serialNumber());
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
  // Reading the same state through the implicit conversion yields a CONCRETE date by construction,
  // which is exactly why the assertions above use the raw accessor: a conversion-based test would
  // observe todaysDate() and could not tell raw-null restoration from an explicit today's date.
  EXPECT_FALSE(static_cast<QuantLib::Date>(QuantLib::Settings::instance().evaluationDate()) ==
               QuantLib::Date())
      << "DateProxy::operator Date() substitutes a concrete date for a raw-null value";
}

TEST(QuantLibDefaults, GuardRestoresASeededExplicitEvaluationDateExactly) {
  [[maybe_unused]] const ScopedSettingsState prior_state;
  const QuantLib::Date seeded(17, QuantLib::March, 2025);
  seed_explicit_evaluation_date(seeded);
  const QuantLib::Date before_raw = raw_evaluation_date();

  {
    QuantLibSettingsGuard guard("2026-06-10");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
    EXPECT_TRUE(raw_evaluation_date() == QuantLib::Date(10, QuantLib::June, 2026));
    EXPECT_FALSE(guard.previous_evaluation_date_was_unset())
        << "an explicitly seeded date must not be recorded as the raw-null state";
  }

  const QuantLib::Date after_raw = raw_evaluation_date();
  EXPECT_TRUE(after_raw == seeded)
      << "the exact prior raw date must be restored, not today's date and not the requested date";
  EXPECT_EQ(after_raw.serialNumber(), before_raw.serialNumber());
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

// Runs the guard over a fixed request with a caller exception thrown inside the guarded scope, then
// returns the raw evaluation date observed after unwinding has destroyed the guard. This is the
// exception path the #227 guard can execute: its destructor's restore-and-verify code runs during
// unwinding exactly as it does on the normal path.
[[nodiscard]] QuantLib::Date raw_evaluation_date_after_throwing_scope(const QuantLib::Date& seeded) {
  if (seeded == QuantLib::Date()) {
    seed_raw_null_evaluation_date();
  } else {
    seed_explicit_evaluation_date(seeded);
  }

  bool threw = false;
  try {
    QuantLibSettingsGuard guard("2026-06-10");
    EXPECT_TRUE(guard.satisfied()) << guard.refusal_reason();
    throw std::runtime_error("simulated failure inside the guarded scope");
  } catch (const std::runtime_error&) {
    threw = true;
  }
  EXPECT_TRUE(threw) << "the guarded scope must have unwound through the guard's destructor";
  return raw_evaluation_date();
}

TEST(QuantLibDefaults, GuardRestoresTheRawEvaluationDateWhenTheGuardedScopeThrows) {
  [[maybe_unused]] const ScopedSettingsState prior_state;

  const QuantLib::Date seeded(17, QuantLib::March, 2025);
  const QuantLib::Date after_explicit = raw_evaluation_date_after_throwing_scope(seeded);
  EXPECT_TRUE(after_explicit == seeded)
      << "an explicitly seeded prior date must survive an exception thrown inside the guarded scope";

  const QuantLib::Date after_raw_null = raw_evaluation_date_after_throwing_scope(QuantLib::Date());
  EXPECT_TRUE(after_raw_null == QuantLib::Date())
      << "a raw-null prior state must survive an exception thrown inside the guarded scope";

  // The guard verifies restoration during unwinding, so a successful unwinding leaves the process
  // usable: poisoning here would mean the verification ran and failed.
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

TEST(QuantLibDefaults, GuardPreservesUnrelatedProcessGlobalSettings) {
  [[maybe_unused]] const ScopedSettingsState prior_state;
  QuantLib::Settings& settings = QuantLib::Settings::instance();
  settings.enforcesTodaysHistoricFixings() = true;
  EXPECT_TRUE(QuantLib::Settings::instance().enforcesTodaysHistoricFixings());
  {
    QuantLibSettingsGuard guard("2026-06-10");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
  }
  // The flag is part of the process-global state the guard is responsible for; leaving it flipped
  // would make a later valuation depend on an earlier caller.
  EXPECT_TRUE(QuantLib::Settings::instance().enforcesTodaysHistoricFixings());
}

TEST(QuantLibDefaults, IdentityNamesTheGlobalStateTheBoundaryOwns) {
  const auto& identity = Shiori::rates::adapter::quantlib_identity();
  // G-2 requires more than a version number: the macro configuration is part of dependency identity.
  EXPECT_NE(identity.macro_configuration.find("QL_ENABLE_SESSIONS="), std::string::npos);
  EXPECT_NE(identity.macro_configuration.find("QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN="),
            std::string::npos);
  // The rest of the docs/34 section 5.5 C10 behaviour set, including the three switches that change lazy
  // notification, failure and parallel-execution behaviour: each is reported with the value this
  // translation unit compiled against the resolved QuantLib headers, never an inferred default.
  EXPECT_NE(identity.macro_configuration.find("QL_FASTER_LAZY_OBJECTS="), std::string::npos)
      << identity.macro_configuration;
  EXPECT_NE(identity.macro_configuration.find("QL_THROW_IN_CYCLES="), std::string::npos)
      << identity.macro_configuration;
  EXPECT_NE(identity.macro_configuration.find("QL_ENABLE_OPENMP="), std::string::npos)
      << identity.macro_configuration;
  // The guard's subject has to be named: an unstated process-global surface is not audited.
  EXPECT_NE(identity.global_settings_quantifiable.find("evaluation_date"), std::string::npos);
  EXPECT_NE(identity.global_settings_quantifiable.find("enforces_todays_historic_fixings"),
            std::string::npos);
  EXPECT_TRUE(Shiori::rates::adapter::quantlib_linked());
}

}  // namespace
