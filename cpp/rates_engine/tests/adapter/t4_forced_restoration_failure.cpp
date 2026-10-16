// #226 section 5.7 T4 — the forced-restoration-failure leg (docs/34 sections 5.5 C2 and 5.7 T4).
//
// WHAT THIS PROVES: a REAL restoration-time fault — a `QuantLib::Observer` whose `update()` throws
// while the process-global evaluation date is being restored — is contained by
// `QuantLibSettingsGuard`: it is caught inside the guard's implicitly-noexcept destructor, it latches
// PERMANENT process poison, it never terminates the process, it never releases the single-process gate
// into a usable state, and every later request is refused promptly instead of waiting on the gate.
//
// WHY THIS FILE IS ITS OWN TEST EXECUTABLE (`shiori_rates_tests_adapter_t4` in CMakeLists.txt):
// poisoning is process-wide and permanent, so sharing a binary with the other adapter tests would make
// their outcome depend on test order. The file deliberately stays in tests/adapter/ so it remains
// inside the QuantLib test include allow-list that tools/isolation_guard.py enforces; that allow-list
// is unchanged.
//
// WHY A REAL OBSERVER, NOT A MOCK: `ThrowingObserver` registers with QuantLib's own evaluation-date
// observable through `Observer::registerWith`, so its `update()` is reached from
// `Observable::notifyObservers()` exactly as a production observer's would be. A stand-in that never
// enters QuantLib's notification path would prove nothing about this adapter.
//
// THE PINNED v1.43 MECHANISM (read from the tag; exact signatures quoted in the PR body):
//   * ql/settings.hpp — `DateProxy& Settings::evaluationDate()`;
//     `DateProxy::operator=(const Date&)` forwards to `ObservableValue<Date>::operator=` only when the
//     value actually changes; `Settings::resetEvaluationDate()` assigns `Date()` (the raw-null state).
//   * ql/utilities/observablevalue.hpp — `operator=` assigns `value_` and THEN calls
//     `observable_->notifyObservers()`. The raw value is therefore written before any observer runs.
//   * ql/patterns/observable.cpp — `Observable::notifyObservers()` runs every observer's `update()`
//     inside try/catch and then `QL_ENSURE(successful, "could not notify one or more observers: " <<
//     errMsg)`. `QL_ENSURE` is defined unconditionally in ql/errors.hpp (no NDEBUG guard) and throws
//     `QuantLib::Error`, so the observer's fault escapes the DATE ASSIGNMENT rather than being
//     swallowed. QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN only changes how it escapes (boost::signals2
//     lets the observer's own exception propagate); both configurations throw out of the assignment.
//   * ql/settings.cpp — upstream `SavedSettings::~SavedSettings()` wraps its restore in
//     `catch (...) { /* nothing we can do except bailing out. */ }`. That is why docs/34 section 4.3
//     calls this fault reachable rather than hypothetical. This adapter does what upstream does not:
//     it records the failure as permanent process poison.
#include <gtest/gtest.h>

#include <ql/patterns/observable.hpp>
#include <ql/settings.hpp>
#include <ql/time/date.hpp>

#include <atomic>
#include <chrono>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/quantlib_adapter/adapter.hpp"
#include "shiori_rates/quantlib_adapter/testing.hpp"

namespace {

using Shiori::rates::adapter::QuantLibSettingsGuard;
using Shiori::rates::adapter::SerializationGate;

// The RAW stored evaluation date (`ObservableValue<Date>::value() const`), never the DateProxy
// conversion: `DateProxy::operator Date()` substitutes `Date::todaysDate()` for a raw-null value and
// therefore cannot tell "raw-null restored" from "an explicit today's date written instead".
[[nodiscard]] QuantLib::Date raw_evaluation_date() {
  return QuantLib::Settings::instance().evaluationDate().value();
}

void seed_raw_null_evaluation_date() {
  QuantLib::Settings::instance().resetEvaluationDate();
  ASSERT_TRUE(raw_evaluation_date() == QuantLib::Date());
}

void seed_explicit_evaluation_date(const QuantLib::Date& date) {
  QuantLib::Settings::instance().evaluationDate() = date;
  ASSERT_TRUE(raw_evaluation_date() == date);
}

// Restores whatever process-global raw evaluation date this file found, so a failing assertion cannot
// leak global state into the rest of the binary. Declared BEFORE the throwing observer in every test,
// so it is destroyed LAST and never notifies a still-registered throwing observer.
class ScopedRawEvaluationDate {
 public:
  ScopedRawEvaluationDate()
      : date_(raw_evaluation_date()), was_raw_null_(date_ == QuantLib::Date()) {}
  ~ScopedRawEvaluationDate() {
    if (was_raw_null_) {
      QuantLib::Settings::instance().resetEvaluationDate();
    } else {
      QuantLib::Settings::instance().evaluationDate() = date_;
    }
  }

  ScopedRawEvaluationDate(const ScopedRawEvaluationDate&) = delete;
  ScopedRawEvaluationDate& operator=(const ScopedRawEvaluationDate&) = delete;

 private:
  QuantLib::Date date_;
  bool was_raw_null_;
};

// A REAL QuantLib observer whose update() throws. It is registered through
// `Observer::registerWith(Settings::instance().evaluationDate())`, so it sits on QuantLib's own
// observer list and is reached through `Observable::notifyObservers()`.
class ThrowingObserver : public QuantLib::Observer {
 public:
  void update() override {
    update_calls_.fetch_add(1, std::memory_order_relaxed);
    throw std::runtime_error(
        "forced observer failure during the QuantLib settings notification path");
  }

  [[nodiscard]] int update_calls() const { return update_calls_.load(std::memory_order_relaxed); }

 private:
  std::atomic<int> update_calls_{0};
};

struct ForcedRestorationFailureObservation {
  QuantLib::Date raw_date_after;
  int observer_updates = 0;
  bool poisoned = false;
  std::string poison_reason;
};

// Forces ONE restoration-time failure: seed `prior`, construct a guard with the throwing observer NOT
// yet registered (so the install notification stays clean and the forced fault is unambiguously the
// RESTORATION notification), register the observer, then let the guard's destructor run at scope exit.
// With an unguarded destructor the observer's throw escapes an implicitly-noexcept destructor and
// std::terminate aborts this entire binary at the marked line below.
void force_restoration_failure(const QuantLib::Date& prior,
                               ForcedRestorationFailureObservation& observation) {
  if (prior == QuantLib::Date()) {
    seed_raw_null_evaluation_date();
  } else {
    seed_explicit_evaluation_date(prior);
  }

  ThrowingObserver observer;  // declared BEFORE the guard, so it outlives the guard's destructor
  {
    QuantLibSettingsGuard guard("2026-06-10");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
    ASSERT_TRUE(raw_evaluation_date() == QuantLib::Date(10, QuantLib::June, 2026));

    // Registering AFTER construction keeps the constructor's install notification on the clean path
    // asserted above, so the fault below can only be the restoration notification.
    observer.registerWith(QuantLib::Settings::instance().evaluationDate());
    ASSERT_EQ(observer.update_calls(), 0);

    // The guard is destroyed here, while `observer` is still registered. Its restoration notifies
    // QuantLib's observers, the observer throws, and QuantLib re-reports that as a QuantLib::Error
    // from `Observable::notifyObservers()`. THE FALSIFIABILITY POINT: without the destructor's catch
    // this is a std::terminate of the whole test binary.
  }

  observation.raw_date_after = raw_evaluation_date();
  observation.observer_updates = observer.update_calls();
  observation.poisoned = Shiori::rates::adapter::adapter_poisoned();
  observation.poison_reason = Shiori::rates::adapter::adapter_poison_reason();
}

}  // namespace

// (a) The ordinary paths must still pass: an initially explicit date is restored exactly, and an
// initially raw-null / clock-following date is restored AS raw null (docs/34 section 5.7 T4's two
// mandatory cases). A green forced-failure leg that came at the cost of the happy path would be
// worthless, so this is asserted in the same executable that later poisons the process.
TEST(T4ForcedRestorationFailure, NormalRawNullAndExplicitRestorationStillSucceed) {
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned())
      << "a forced-failure test in this binary left the process poisoned";
  [[maybe_unused]] const ScopedRawEvaluationDate saved;

  // Case (i): initially UNSET / raw null.
  seed_raw_null_evaluation_date();
  {
    QuantLibSettingsGuard guard("2026-06-10");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
    EXPECT_TRUE(guard.previous_evaluation_date_was_unset());
    EXPECT_TRUE(raw_evaluation_date() == QuantLib::Date(10, QuantLib::June, 2026));
  }
  EXPECT_TRUE(raw_evaluation_date() == QuantLib::Date())
      << "the raw-null / clock-following state must come back as RAW NULL, not as a concrete date";
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());

  // Case (ii): initially EXPLICIT.
  const QuantLib::Date explicit_date(17, QuantLib::March, 2025);
  seed_explicit_evaluation_date(explicit_date);
  {
    QuantLibSettingsGuard guard("2026-06-11");
    ASSERT_TRUE(guard.satisfied()) << guard.refusal_reason();
    EXPECT_FALSE(guard.previous_evaluation_date_was_unset());
    EXPECT_TRUE(raw_evaluation_date() == QuantLib::Date(11, QuantLib::June, 2026));
  }
  EXPECT_TRUE(raw_evaluation_date() == explicit_date)
      << "the exact prior raw date must be restored";
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

// (b), (c), (e) for the DESTRUCTOR path, from BOTH prior states. The process must survive the fault,
// must be poisoned with an attributable reason, and a later request must be refused — nothing here
// may return success.
TEST(T4ForcedRestorationFailure, ThrowingObserverDuringRestorationPoisonsAndNeverTerminates) {
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned());
  [[maybe_unused]] const ScopedRawEvaluationDate saved;

  const QuantLib::Date explicit_prior(17, QuantLib::March, 2025);

  // Case 1: prior state EXPLICIT.
  ForcedRestorationFailureObservation explicit_case;
  force_restoration_failure(explicit_prior, explicit_case);

  // (b) The fault travelled through QuantLib's real notification path, and the process is still alive
  // to make this assertion. An implementation that swallowed the fault and returned success would
  // report `poisoned == false` here.
  EXPECT_EQ(explicit_case.observer_updates, 1)
      << "the forced fault must travel through QuantLib's real observer notification path";
  EXPECT_TRUE(explicit_case.poisoned)
      << "a restoration-time fault must fail closed, never report success";
  // (e) Attribution: the reason names the restoration notification, not an unrelated failure.
  EXPECT_NE(explicit_case.poison_reason.find("restore"), std::string::npos)
      << explicit_case.poison_reason;
  EXPECT_NE(explicit_case.poison_reason.find("observers failed"), std::string::npos)
      << explicit_case.poison_reason;

  // The pinned QuantLib writes the raw value BEFORE it notifies, so the restoration write itself
  // completed. docs/34 section 5.7 T4 deliberately does NOT require "exactly as before" on the
  // forced-failure path; this assertion is a mechanism canary, not a restatement of that contract.
  EXPECT_TRUE(explicit_case.raw_date_after == explicit_prior);

  // (c)+(d) The poison is latched, so a later request is refused instead of being priced on polluted
  // global state — and it is refused PROMPTLY, because `SerializationGate::acquire` checks the poison
  // flag before it touches the gate mutex.
  const auto refusal_start = std::chrono::steady_clock::now();
  EXPECT_THROW(
      {
        const SerializationGate gate = SerializationGate::acquire("t4_after_failed_restore");
        (void)gate.held();
      },
      Shiori::rates::dto::ContractViolation);
  const auto refusal_elapsed = std::chrono::steady_clock::now() - refusal_start;
  EXPECT_LT(refusal_elapsed, std::chrono::seconds(2)) << "a refusal must not wait on the gate mutex";

  Shiori::rates::adapter::unpoison_adapter_for_testing();
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned());

  // Case 2: prior state RAW NULL (the second mandatory T4 case, forced to fail).
  ForcedRestorationFailureObservation raw_null_case;
  force_restoration_failure(QuantLib::Date(), raw_null_case);
  EXPECT_EQ(raw_null_case.observer_updates, 1);
  EXPECT_TRUE(raw_null_case.poisoned);
  EXPECT_NE(raw_null_case.poison_reason.find("observers failed"), std::string::npos)
      << raw_null_case.poison_reason;
  EXPECT_TRUE(raw_null_case.raw_date_after == QuantLib::Date())
      << "the failed restore from raw null must still have written raw null back";

  Shiori::rates::adapter::unpoison_adapter_for_testing();
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

// (b), (e) for the CONSTRUCTOR path: the install notification runs inside the constructor, after the
// captured state has been read but before any owning guard exists, so an escaping exception would
// leave QuantLib's process-global date half-installed. It must fail closed instead: no success, no
// QuantLib exception type crossing the adapter boundary, no half-installed date.
TEST(T4ForcedRestorationFailure, ThrowingObserverDuringInstallationFailsClosedWithoutHalfInstalledState) {
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned());
  [[maybe_unused]] const ScopedRawEvaluationDate saved;

  const QuantLib::Date prior(17, QuantLib::March, 2025);
  seed_explicit_evaluation_date(prior);

  ThrowingObserver observer;
  observer.registerWith(QuantLib::Settings::instance().evaluationDate());

  // The installation notification fires from the constructor's own assignment. QuantLib converts the
  // observer's throw into a QuantLib::Error out of that assignment; nothing may escape this
  // construction.
  QuantLibSettingsGuard guard("2026-06-10");
  EXPECT_FALSE(guard.satisfied()) << "a failed installation must not report success";
  // (e) Attribution.
  EXPECT_NE(guard.refusal_reason().find("install"), std::string::npos) << guard.refusal_reason();
  EXPECT_NE(guard.refusal_reason().find("observers failed"), std::string::npos)
      << guard.refusal_reason();
  EXPECT_TRUE(Shiori::rates::adapter::adapter_poisoned());
  EXPECT_GE(observer.update_calls(), 1);
  EXPECT_TRUE(raw_evaluation_date() == prior)
      << "the requested date must not survive a failed installation";

  Shiori::rates::adapter::unpoison_adapter_for_testing();
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

// (d) A request issued after the poison must be refused WITHOUT waiting on a held gate. The gate mutex
// is deliberately held across the poison here: if `SerializationGate::acquire` ever checked the poison
// flag behind the lock, the probe thread would block on the held mutex, this test would report that
// instead of hanging, and the held gate is released before the probe is joined so the failure cannot
// deadlock the binary.
TEST(T4ForcedRestorationFailure, LaterRequestsAreRefusedPromptlyWithoutWaitingOnAHeldGate) {
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned());

  auto held = std::make_unique<SerializationGate>(SerializationGate::acquire("t4_holds_the_gate"));
  ASSERT_TRUE(held->held());
  Shiori::rates::adapter::poison_adapter_for_testing("t4 held-gate prompt-refusal probe");

  std::atomic<bool> refused{false};
  std::atomic<bool> acquired{false};
  std::atomic<bool> poison_attributed{false};
  std::thread probe([&refused, &acquired, &poison_attributed] {
    try {
      const SerializationGate gate = SerializationGate::acquire("t4_probe");
      (void)gate.held();
      acquired.store(true, std::memory_order_release);
    } catch (const Shiori::rates::dto::ContractViolation& error) {
      // The refusal must be the POISON refusal, not some other ContractViolation (for example a
      // re-entrancy refusal): a refusal for the wrong reason would be a false green.
      poison_attributed.store(error.message().find("poisoned") != std::string::npos,
                              std::memory_order_release);
      refused.store(true, std::memory_order_release);
    }
  });

  const auto start = std::chrono::steady_clock::now();
  bool settled = false;
  while (std::chrono::steady_clock::now() - start < std::chrono::seconds(5)) {
    if (refused.load(std::memory_order_acquire) || acquired.load(std::memory_order_acquire)) {
      settled = true;
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  if (!settled) {
    // Regression path: the probe is waiting on the held gate mutex. Release the gate so the thread
    // can finish and the defect is REPORTED rather than deadlocking the whole binary.
    held.reset();
    probe.join();
    FAIL() << "a request issued after poisoning waited on the held gate instead of being refused";
  }
  probe.join();
  held.reset();

  EXPECT_TRUE(refused.load(std::memory_order_acquire))
      << "a poisoned adapter admitted a request while the gate was held";
  EXPECT_FALSE(acquired.load(std::memory_order_acquire));
  EXPECT_TRUE(poison_attributed.load(std::memory_order_acquire))
      << "the prompt refusal must be attributable to the poison, not to another contract violation";

  Shiori::rates::adapter::unpoison_adapter_for_testing();
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}

// (c)+(d) for the one case the pre-lock check cannot cover: a request that was ALREADY past that check
// and waiting on the gate mutex when the poison latched. Admitting it would release the gate into a
// usable state in a permanently poisoned process, so the poison is rechecked with the lock held.
//
// The synchronisation is not a sleep: `SerializationGate::counters().waits` is incremented only AFTER
// the pre-lock poison check and only on the blocking path, so once it is observed the waiter has
// provably passed that check and is waiting on the mutex this test holds.
TEST(T4ForcedRestorationFailure, WaiterBlockedOnTheGateIsRefusedWhenPoisonLatches) {
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned());

  SerializationGate::reset_counters_for_testing();
  auto held = std::make_unique<SerializationGate>(SerializationGate::acquire("t4_waiter_target"));
  ASSERT_TRUE(held->held());

  std::atomic<bool> admitted{false};
  std::atomic<bool> refused{false};
  std::atomic<bool> poison_attributed{false};
  std::atomic<bool> reentrancy_text{false};
  std::thread waiter([&admitted, &refused, &poison_attributed, &reentrancy_text] {
    try {
      const SerializationGate gate = SerializationGate::acquire("t4_waiter");
      (void)gate.held();
      admitted.store(true, std::memory_order_release);
    } catch (const Shiori::rates::dto::ContractViolation& error) {
      const std::string& message = error.message();
      poison_attributed.store(message.find("poisoned") != std::string::npos,
                              std::memory_order_release);
      reentrancy_text.store(message.find("re-entrant") != std::string::npos,
                            std::memory_order_release);
      refused.store(true, std::memory_order_release);
    }
  });

  // Bounded wait for the waiter to be provably blocked inside the gate mutex.
  const auto blocked_start = std::chrono::steady_clock::now();
  bool blocked = false;
  while (std::chrono::steady_clock::now() - blocked_start < std::chrono::seconds(5)) {
    if (SerializationGate::counters().waits >= 1) {
      blocked = true;
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  if (!blocked) {
    // Release the gate so the waiter can finish, then report. A joinable std::thread must never be
    // abandoned to its destructor, which would terminate the binary instead of reporting.
    held.reset();
    waiter.join();
    FAIL() << "the waiter never reached the gate mutex; this case would prove nothing";
  }

  // Latch poison while the waiter is past the pre-lock check and still blocked, then release the gate.
  Shiori::rates::adapter::poison_adapter_for_testing("t4 waiter admission probe");
  held.reset();

  const auto settle_start = std::chrono::steady_clock::now();
  bool settled = false;
  while (std::chrono::steady_clock::now() - settle_start < std::chrono::seconds(5)) {
    if (admitted.load(std::memory_order_acquire) || refused.load(std::memory_order_acquire)) {
      settled = true;
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  // The mutex is already free, so the waiter cannot block here.
  waiter.join();

  EXPECT_TRUE(settled) << "the waiter neither acquired nor was refused within the bound";
  EXPECT_FALSE(admitted.load(std::memory_order_acquire))
      << "the gate was released into a usable state in a poisoned process";
  EXPECT_TRUE(refused.load(std::memory_order_acquire));
  EXPECT_TRUE(poison_attributed.load(std::memory_order_acquire))
      << "the waiter must be refused with the POISON refusal";
  EXPECT_FALSE(reentrancy_text.load(std::memory_order_acquire))
      << "the waiter must not be refused as re-entrancy";

  // Every subsequent request is refused too, with the same poison attribution.
  bool subsequent_refused = false;
  bool subsequent_poison_attributed = false;
  try {
    const SerializationGate gate = SerializationGate::acquire("t4_after_waiter");
    (void)gate.held();
  } catch (const Shiori::rates::dto::ContractViolation& error) {
    subsequent_refused = true;
    subsequent_poison_attributed = error.message().find("poisoned") != std::string::npos;
  }
  EXPECT_TRUE(subsequent_refused) << "a later acquire must also be refused";
  EXPECT_TRUE(subsequent_poison_attributed)
      << "the later refusal must be attributable to the poison";

  Shiori::rates::adapter::unpoison_adapter_for_testing();
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
}
