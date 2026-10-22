// Serialization-gate thread-ownership conformance for the QuantLib adapter (docs/34 section 5.2).
//
// WHAT THIS SUITE PROVES:
//   * SAME THREAD — the C++20 return-by-value acquisition still works: a held gate may be moved within
//     the thread that acquired it, releases exactly once on that thread, and the process-wide
//     serialization plus the re-entrancy refusal are preserved across the move.
//   * FOREIGN MOVE — a held gate moved on a thread that does not own it is refused. The destination
//     stays unheld, the source keeps its ownership so it can still be released legally by its own
//     thread, and the fail-closed poison is latched. No `std::mutex::unlock` happens on a non-owner
//     thread, which is what the rule exists to prevent (unlocking a mutex from a non-owner thread is
//     undefined behavior).
//
// The mistake is exercised through the ordinary C++ lifetime rules (a heap-owned gate moved on another
// thread), so this suite never runs undefined behavior itself: the refused paths never unlock a foreign
// mutex. Each case here restores the adapter with the test-only hook, because a refused transfer leaves
// the source owned by its own thread and that thread can still release it legally.
//
// THE DESTRUCTION CASE IS DELIBERATELY ELSEWHERE: once a held gate is DESTROYED on a foreign thread the
// mutex is stranded locked (no thread may legally unlock it) and the process stays poisoned, so that
// case lives in `tests/adapter_poison/` - its own test executable, exactly like the other permanent
// poison coverage - instead of sharing a binary with these cases.
//
// NO NEW CAPABILITY: nothing here enables parallel pricing, adds a concurrency feature, or asserts any
// QuantLib thread-safety claim; `kProductionParallelPricingEnabled` stays false and the tests only pin
// the ownership protocol of the existing single-process gate.
#include <gtest/gtest.h>

#include <memory>
#include <string>
#include <thread>
#include <utility>

#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/quantlib_adapter/adapter.hpp"
#include "shiori_rates/quantlib_adapter/testing.hpp"

namespace {

using Shiori::rates::adapter::SerializationGate;
using Shiori::rates::adapter::adapter_poison_reason;
using Shiori::rates::adapter::adapter_poisoned;
using Shiori::rates::adapter::unpoison_adapter_for_testing;
using Shiori::rates::dto::ContractViolation;

// A gate that can be moved or destroyed from another thread without touching the owner's stack.
[[nodiscard]] std::unique_ptr<SerializationGate> acquire_on_heap(const char* operation) {
  return std::make_unique<SerializationGate>(SerializationGate::acquire(operation));
}

TEST(SerializationGateThreadOwnership, ReleasesAHeldGateOnItsOwnerThreadAndKeepsReentrancyRefused) {
  ASSERT_FALSE(adapter_poisoned());
  {
    SerializationGate gate = SerializationGate::acquire("t_owner_thread");
    EXPECT_TRUE(gate.held());
    EXPECT_EQ(gate.operation(), "t_owner_thread");

    // The acquisition is return-by-value, so the same-thread move is part of the ordinary protocol.
    SerializationGate moved = std::move(gate);
    EXPECT_TRUE(moved.held());
    EXPECT_FALSE(gate.held()) << "the moved-from gate must not keep ownership of the mutex";
    EXPECT_EQ(moved.operation(), "t_owner_thread");

    // Re-entrancy is still refused while the moved-to gate holds the single process gate: the
    // thread-local marker belongs to the mutex that is still held, not to the moved-from object.
    EXPECT_THROW(
        {
          const SerializationGate nested = SerializationGate::acquire("t_nested");
          (void)nested;
        },
        ContractViolation);
  }

  // The gate was released by its owning thread, so the adapter is usable again and the poison was never
  // latched: a same-thread lifetime is not a misuse.
  EXPECT_FALSE(adapter_poisoned());
  const SerializationGate after_release = SerializationGate::acquire("t_after_release");
  EXPECT_TRUE(after_release.held());
}

TEST(SerializationGateThreadOwnership, RefusesAForeignMoveAndKeepsTheSourceOwnedByItsThread) {
  ASSERT_FALSE(adapter_poisoned());
  std::unique_ptr<SerializationGate> held = acquire_on_heap("t_move_source");
  ASSERT_TRUE(held->held());

  std::thread thief([&held] {
    // The foreign thread must not receive the held gate: taking it would mean unlocking (or handing on)
    // a mutex this thread does not own. The destination stays unheld and the source keeps ownership.
    SerializationGate stolen = std::move(*held);
    EXPECT_FALSE(stolen.held()) << "a foreign thread must never receive a held gate";
    EXPECT_TRUE(adapter_poisoned()) << "the refusal must latch the fail-closed poison immediately";
  });
  thief.join();

  EXPECT_TRUE(adapter_poisoned());
  EXPECT_NE(adapter_poison_reason().find("does not own it"), std::string::npos)
      << adapter_poison_reason();
  EXPECT_TRUE(held->held()) << "the owning thread still holds the gate it locked";

  // Releasing on the owning thread is the legal path, and it is still available: the mutex is unlocked
  // by the thread that locked it, so the refusal above never had to unlock anything.
  held.reset();
  EXPECT_TRUE(adapter_poisoned())
      << "a cross-thread misuse keeps the adapter fail-closed; the poison is not silently cleared";

  // The test-only restore exists because this path did not strand the mutex; the production posture
  // remains failing closed until the process is restarted.
  unpoison_adapter_for_testing();
  const SerializationGate recovered = SerializationGate::acquire("t_recovered_after_move");
  EXPECT_TRUE(recovered.held());
}

TEST(SerializationGateThreadOwnership, RefusesAForeignMoveAssignmentAndLeavesBothGatesIntact) {
  ASSERT_FALSE(adapter_poisoned());
  // Only one gate can be held process-wide, so a refused assignment always has an unheld destination:
  // this is the reachable shape of the rule, and it also pins that the refusal happens before the
  // destination's own (empty) ownership is touched.
  std::unique_ptr<SerializationGate> target = std::make_unique<SerializationGate>();
  std::unique_ptr<SerializationGate> source = acquire_on_heap("t_assignment_source");
  ASSERT_TRUE(source->held());

  std::thread thief([&target, &source] {
    *target = std::move(*source);
    EXPECT_FALSE(target->held()) << "the destination must stay unheld when the transfer is refused";
    EXPECT_TRUE(source->held()) << "the source must keep its ownership so its own thread can release it";
  });
  thief.join();

  EXPECT_TRUE(adapter_poisoned());
  EXPECT_FALSE(target->held());
  EXPECT_TRUE(source->held());

  source.reset();  // legal release on the owning thread
  unpoison_adapter_for_testing();
  const SerializationGate recovered = SerializationGate::acquire("t_recovered_after_assignment");
  EXPECT_TRUE(recovered.held());
}

}  // namespace
