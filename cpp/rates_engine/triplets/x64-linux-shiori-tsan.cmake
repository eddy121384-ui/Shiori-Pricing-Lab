# Linux Clang + ThreadSanitizer triplet (Issue #226 sections 2.9 and 2.10).
#
# The dependency closure MUST be TSan-instrumented and MUST NOT be substituted by the release or ASan
# closure. TSan does not detect a data race that happens entirely inside an uninstrumented library, so
# reusing non-instrumented QuantLib binaries would turn the TSan job into a test that always passes for
# the wrong reason.
#
# TSan and ASan cannot be combined in one binary; each has its own triplet, binary tree and cache scope.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)

set(VCPKG_CXX_FLAGS "-fsanitize=thread -fno-omit-frame-pointer -ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_C_FLAGS "-fsanitize=thread -fno-omit-frame-pointer -ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_LINKER_FLAGS "-fsanitize=thread")

set(VCPKG_BUILD_TYPE debug)
