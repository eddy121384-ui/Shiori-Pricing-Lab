// Negative control for tests/ci/assert_library_without_openmp.cmake (docs/34 section 4.11 O6).
//
// This translation unit is compiled WITH the toolchain's OpenMP flag into a static library that is
// never linked into anything. The assertion must REFUSE that library on every CI toolchain (GCC,
// Clang, MSVC); otherwise a clean scan of QuantLib would prove nothing about this toolchain's OpenMP
// code generation. It contains no QuantLib and no Shiori code.
double shiori_openmp_canary(const double* values, int count) {
  double total = 0.0;
#pragma omp parallel for reduction(+ : total)
  for (int index = 0; index < count; ++index) {
    total += values[index];
  }
  return total;
}
