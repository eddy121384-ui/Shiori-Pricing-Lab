# Linux Clang + AddressSanitizer/UndefinedBehaviorSanitizer triplet (Issue #226 sections 2.9 and 2.10).
#
# The ENTIRE dependency closure is instrumented, deliberately. ASan's correctness depends on the
# runtime interceptors being present in the libraries that allocate and free the memory under test; a
# QL_ENABLE_SESSIONS-style global allocation inside a NON-instrumented QuantLib would produce false
# negatives (a real error that the sanitizer never sees), which is worse than no sanitizer run at all.
#
# Do NOT reuse this triplet's binaries from a non-instrumented cache: the build tree name includes the
# triplet, and CI gives the ASan job its own cache scope.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)

set(VCPKG_CXX_FLAGS "-fsanitize=address,undefined -fno-omit-frame-pointer -ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_C_FLAGS "-fsanitize=address,undefined -fno-omit-frame-pointer -ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_LINKER_FLAGS "-fsanitize=address,undefined")

# RELEASE-ONLY, deliberately. A debug-only install (`set(VCPKG_BUILD_TYPE debug)`) makes vcpkg's
# pkgconfig fixup fail on Linux: for a debug-only staging tree the .pc file lands in
# `<package>/debug/share/pkgconfig`, while the fixup searches `<package>/share/pkgconfig` and reports
# "Package nlohmann_json was not found in the pkg-config search path". Building the release
# configuration avoids that mismatch, and sanitizers are supported in an optimized build -- which also
# exercises optimizer-dependent undefined behaviour that a Debug build cannot reach.
set(VCPKG_BUILD_TYPE release)
