// Canonical JSON cost: parse, serialize and fingerprint (#226 section 10.3).
//
// MEASUREMENT ONLY -- there is no correctness assertion and no timing gate here. Correctness is proven
// by the DTO and parity suites; a benchmark that also asserted correctness would report a number for a
// run whose validity it could not establish anyway.
#include <benchmark/benchmark.h>

#include <string>

#include "m4_metadata.hpp"
#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/fingerprint.hpp"

namespace {

// A representative request-shaped document: nested objects, an array of objects, a non-ASCII string
// and a mix of integers and decimals. It is canonical by construction so the parse cost is not
// measuring a whitespace scan.
const char* const kDocument = R"({"curve_selection":{"discount_curve_id":"dc","forecast_curve_by_index":{"USD_SOFR":"fc"}},"market_snapshot":{"curve_set":{"base_currency":"USD","curves":[{"curve_id":"dc","index_id":null,"pillars":[{"pillar_date":"2026-06-10","value":1.0},{"pillar_date":"2027-06-10","value":0.957},{"pillar_date":"2031-06-10","value":0.821}],"value_type":"DISCOUNT_FACTOR"},{"curve_id":"fc","index_id":"USD_SOFR","pillars":[{"pillar_date":"2026-06-10","value":0.0425},{"pillar_date":"2027-06-10","value":0.0431}],"value_type":"ZERO_RATE"}]},"snapshot_id":"snap"},"note":"café — 東京","schema_version":"RATES_KERNEL_INPUT_V1","valuation_context":{"reporting_currency":"USD","valuation_date":"2026-06-10"}})";

void BmParseCanonicalDocument(benchmark::State& state) {
  for (auto _ : state) {
    const auto value = Shiori::rates::dto::parse_canonical_json(kDocument);
    benchmark::DoNotOptimize(value);
  }
}
BENCHMARK(BmParseCanonicalDocument);

void BmDumpCanonicalDocument(benchmark::State& state) {
  const auto value = Shiori::rates::dto::parse_canonical_json(kDocument);
  for (auto _ : state) {
    const std::string text = value.dump();
    benchmark::DoNotOptimize(text);
  }
}
BENCHMARK(BmDumpCanonicalDocument);

void BmFingerprintCanonicalBytes(benchmark::State& state) {
  const std::string bytes = Shiori::rates::dto::parse_canonical_json(kDocument).dump();
  for (auto _ : state) {
    const std::string digest = Shiori::rates::dto::fingerprint_of_canonical_bytes(bytes);
    benchmark::DoNotOptimize(digest);
  }
}
BENCHMARK(BmFingerprintCanonicalBytes);

}  // namespace

SHIORI_BENCHMARK_MAIN("bench_dto")
