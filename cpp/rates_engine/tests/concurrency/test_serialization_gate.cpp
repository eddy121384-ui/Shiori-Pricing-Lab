// Serialized QuantLib execution (#226 section 5.2).
//
// The initial production posture DISABLES parallel pricing. This test proves the gate actually
// serializes: it observes, from inside the critical section, that no second holder is ever present.
// A comment claiming serialization is not evidence; a counter that can never exceed one is.
#include <gtest/gtest.h>

#include <atomic>
#include <thread>
#include <vector>

#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/quantlib_adapter/adapter.hpp"

namespace {

using Shiori::rates::adapter::SerializationGate;

}  // namespace

TEST(SerializationPosture, ParallelPricingIsDisabledAtCompileTime) {
  // A runtime flag would let a caller enable concurrency the engine cannot support, so the posture is
  // a compile-time constant rather than a configuration value.
  static_assert(!Shiori::rates::adapter::kProductionParallelPricingEnabled,
                "parallel pricing must remain disabled under the initial posture");
  EXPECT_FALSE(Shiori::rates::adapter::kProductionParallelPricingEnabled);
}

TEST(SerializationPosture, NeverAllowsTwoConcurrentHolders) {
  constexpr int kThreads = 4;
  constexpr int kIterations = 60;

  std::atomic<int> holders{0};
  std::atomic<int> max_holders{0};
  std::atomic<int> overlapping_observations{0};

  const auto worker = [&holders, &max_holders, &overlapping_observations]() {
    for (int iteration = 0; iteration < kIterations; ++iteration) {
      const SerializationGate gate = SerializationGate::acquire("concurrency_probe");
      const int now = holders.fetch_add(1) + 1;
      int observed = max_holders.load();
      while (now > observed && !max_holders.compare_exchange_weak(observed, now)) {
        // keep the largest observed occupancy
      }
      if (now != 1) {
        overlapping_observations.fetch_add(1);
      }
      std::this_thread::yield();
      holders.fetch_sub(1);
    }
  };

  std::vector<std::thread> threads;
  threads.reserve(kThreads);
  for (int index = 0; index < kThreads; ++index) {
    threads.emplace_back(worker);
  }
  for (std::thread& thread : threads) {
    thread.join();
  }

  EXPECT_EQ(overlapping_observations.load(), 0)
      << "the gate allowed two QuantLib-touching operations to overlap";
  EXPECT_EQ(max_holders.load(), 1);
  EXPECT_EQ(holders.load(), 0);
}

TEST(SerializationPosture, CountersAccountForEveryAcquisition) {
  SerializationGate::reset_counters_for_testing();
  constexpr int kThreads = 3;
  constexpr int kIterations = 20;
  const auto worker = []() {
    for (int iteration = 0; iteration < kIterations; ++iteration) {
      const SerializationGate gate = SerializationGate::acquire("counter_probe");
      (void)gate.held();
    }
  };
  std::vector<std::thread> threads;
  threads.reserve(kThreads);
  for (int index = 0; index < kThreads; ++index) {
    threads.emplace_back(worker);
  }
  for (std::thread& thread : threads) {
    thread.join();
  }

  const SerializationGate::Counters counters = SerializationGate::counters();
  // Every acquisition is either an uncontended acquisition or a wait, so the accounting identity must
  // hold exactly. Lane B reports these numbers (G-6), and a counter that cannot be reconciled is not
  // evidence.
  EXPECT_EQ(counters.acquisitions, kThreads * kIterations);
  EXPECT_GE(counters.waits, 0);
  EXPECT_LE(counters.waits, counters.acquisitions);
}
