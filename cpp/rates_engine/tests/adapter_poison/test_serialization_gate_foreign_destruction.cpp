// Fail-closed behaviour of a held SerializationGate DESTROYED on a foreign thread (docs/34 section 5.2).
//
// A held gate owns the process-wide gate mutex. `std::mutex` must be unlocked by the thread that locked
// it, so a held gate destroyed on another thread must NOT unlock: unlocking there would be undefined
// behavior. The adapter instead gives up ownership without unlocking (`unique_lock::release()`), leaves
// the owning thread's re-entrancy marker untouched, and latches the fail-closed poison, so QuantLib can
// never run again without the serialization guarantee it was supposed to have.
//
// THIS LIVES IN ITS OWN TEST EXECUTABLE. The end state is permanent by design: the mutex is stranded
// locked (no thread may legally unlock it) and the process stays poisoned, which is exactly the
// documented fail-closed outcome, not a recoverable one. Sharing a binary with the other adapter tests
// would make their result depend on test order - the same reason the other permanent-poison coverage
// lives in `tests/adapter_poison/`.
//
// The mistake is reached through an ordinary C++ lifetime rule (a heap-owned gate destroyed on another
// thread); this test never runs undefined behavior itself, because the refused path never unlocks a
// foreign mutex.
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
using Shiori::rates::dto::ContractViolation;

TEST(SerializationGateForeignDestruction, NeverUnlocksTheGateMutexOnAForeignThread) {
  ASSERT_FALSE(adapter_poisoned());
  std::unique_ptr<SerializationGate> held =
      std::make_unique<SerializationGate>(SerializationGate::acquire("t_foreign_destruction"));
  ASSERT_TRUE(held->held());

  // The gate is heap-owned so it can be destroyed by a thread that never locked the mutex.
  std::thread foreign([&held] { held.reset(); });
  foreign.join();

  EXPECT_TRUE(adapter_poisoned()) << "a foreign-thread release must fail closed, not release the gate";
  EXPECT_NE(adapter_poison_reason().find("does not own it"), std::string::npos)
      << adapter_poison_reason();

  // The mutex stays locked on purpose; nothing can block on it, because the poison is checked before the
  // gate mutex is touched, so this refusal returns instead of waiting for a mutex nobody may release.
  EXPECT_THROW(
      {
        const SerializationGate refused = SerializationGate::acquire("t_after_foreign_destruction");
        (void)refused;
      },
      ContractViolation);
}

}  // namespace
