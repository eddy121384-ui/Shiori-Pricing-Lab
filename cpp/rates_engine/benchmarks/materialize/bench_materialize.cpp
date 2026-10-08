// Materialization cost: canonical bytes -> validated value tree (#226 section 10.4).
//
// MEASUREMENT ONLY. #227 contains no pricing, so "materialize" here means the deterministic DTO path a
// future kernel will consume: bytes in, validated in-memory request or a refusal out. Timing this
// boundary is what makes the future kernel's own cost separable from its input handling.
#include <benchmark/benchmark.h>

#include <string>

#include "m4_metadata.hpp"
#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/kernel_input.hpp"

namespace {

const char* const kRejectedDocument = R"({"schema_version":"RATES_KERNEL_INPUT_V1"})";

void BmParseAndReject(benchmark::State& state) {
  for (auto _ : state) {
    bool refused = false;
    try {
      (void)Shiori::rates::dto::RatesKernelInput::from_canonical(
          Shiori::rates::dto::parse_canonical_json(kRejectedDocument), "");
    } catch (const Shiori::rates::dto::ContractViolation&) {
      // The refusal IS the expected outcome: this measures the cost of refusing, which must not be a
      // slow path that a caller could be tempted to skip.
      refused = true;
    }
    benchmark::DoNotOptimize(refused);
  }
}
BENCHMARK(BmParseAndReject);

void BmCanonicalizeBytes(benchmark::State& state) {
  for (auto _ : state) {
    const std::string bytes =
        Shiori::rates::dto::parse_canonical_json(kRejectedDocument).dump();
    benchmark::DoNotOptimize(bytes);
  }
}
BENCHMARK(BmCanonicalizeBytes);

}  // namespace

SHIORI_BENCHMARK_MAIN("bench_materialize")
