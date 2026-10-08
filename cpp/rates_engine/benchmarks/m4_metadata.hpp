#pragma once
//
// M4 measurement metadata (docs/34 section 10.5) for every Shiori benchmark.
//
// A benchmark number without its configuration is not evidence: the same code compiled with different
// dependency pins, macro settings or numeric flags can produce different timings and DIFFERENT
// numbers. Every benchmark therefore prints its metadata before running.
//
// It goes to stderr on purpose, so that `--benchmark_format=json` keeps stdout parseable: a JSON
// consumer must not have to strip a metadata line out of the report.
//
// No QuantLib header is included here: the metadata is obtained through the adapter's QuantLib-free
// public header, which is the only way a benchmark may reach dependency identity.
//
#include <cstdio>
#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/engine/engine.hpp"
#include "shiori_rates/quantlib_adapter/adapter.hpp"

namespace Shiori::benchmarks {

inline void print_m4_metadata(const char* benchmark_name) {
  const auto& build = Shiori::rates::engine::build_identity();
  const auto& quantlib = Shiori::rates::adapter::quantlib_identity();

  CanonicalMembers metadata;
  metadata.emplace_back("engine_version",
                        CanonicalValue(std::string(Shiori::rates::engine::engine_version())));
  metadata.emplace_back("git_revision", CanonicalValue(build.git_revision));
  metadata.emplace_back("build_type", CanonicalValue(build.build_type));
  metadata.emplace_back("compiler", CanonicalValue(build.compiler + "/" + build.compiler_version));
  metadata.emplace_back("acquisition_mode", CanonicalValue(build.acquisition_mode));
  metadata.emplace_back("vcpkg_baseline", CanonicalValue(build.vcpkg_baseline));
  metadata.emplace_back("vcpkg_triplet", CanonicalValue(build.vcpkg_triplet));
  metadata.emplace_back("numeric_flags", CanonicalValue(build.numeric_flags));
  metadata.emplace_back("sanitizers", CanonicalValue(build.sanitizers));
  metadata.emplace_back("quantlib_version", CanonicalValue(quantlib.version));
  metadata.emplace_back("quantlib_macro_configuration",
                        CanonicalValue(quantlib.macro_configuration));
  metadata.emplace_back("benchmark_name", CanonicalValue(std::string(benchmark_name)));

  CanonicalMembers envelope;
  envelope.emplace_back("metadata", CanonicalValue::make_object(std::move(metadata)));
  envelope.emplace_back("schema_version", CanonicalValue(std::string("SHIORI_BENCHMARK_M4_V1")));
  envelope.emplace_back("shiori_engine_version",
                        CanonicalValue(std::string(Shiori::rates::engine::engine_version())));

  const std::string line = CanonicalValue::make_object(std::move(envelope)).dump();
  std::fputs(line.c_str(), stderr);
  std::fputc('\n', stderr);
  std::fflush(stderr);
}

// The shared entry point. Benchmarks own their main() so the metadata is emitted exactly once, before
// any measurement, and so the exit code is theirs.
#define SHIORI_BENCHMARK_MAIN(benchmark_name_literal)                        \
  int main(int argc, char** argv) {                                         \
    Shiori::benchmarks::print_m4_metadata(benchmark_name_literal);          \
    ::benchmark::Initialize(&argc, argv);                                   \
    if (::benchmark::ReportUnrecognizedArguments(argc, argv)) {             \
      return 1;                                                             \
    }                                                                       \
    ::benchmark::RunSpecifiedBenchmarks();                                  \
    ::benchmark::Shutdown();                                                \
    return 0;                                                               \
  }

}  // namespace Shiori::benchmarks
