// Startup / identity-read cost (#226 section 10.2).
//
// MEASUREMENT ONLY. #226 section 10.8 forbids an absolute-time pass/fail gate, so nothing here asserts
// a duration; the benchmark exists to make a regression visible, and its M4 metadata is what makes the
// number comparable at all.
#include <benchmark/benchmark.h>

#include <string>

#include "m4_metadata.hpp"
#include "shiori_rates/engine/engine.hpp"

namespace {

void BmEngineIdentity(benchmark::State& state) {
  for (auto _ : state) {
    const auto identity = Shiori::rates::engine::identity();
    benchmark::DoNotOptimize(identity.engine_version);
    benchmark::ClobberMemory();
  }
}
BENCHMARK(BmEngineIdentity);

void BmBuildIdentity(benchmark::State& state) {
  for (auto _ : state) {
    const auto& build = Shiori::rates::engine::build_identity();
    benchmark::DoNotOptimize(build.numeric_flags);
  }
}
BENCHMARK(BmBuildIdentity);

void BmEngineVersion(benchmark::State& state) {
  for (auto _ : state) {
    const std::string_view version = Shiori::rates::engine::engine_version();
    benchmark::DoNotOptimize(version);
  }
}
BENCHMARK(BmEngineVersion);

}  // namespace

SHIORI_BENCHMARK_MAIN("bench_startup")
