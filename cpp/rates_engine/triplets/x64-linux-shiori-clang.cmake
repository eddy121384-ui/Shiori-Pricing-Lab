# Linux Clang triplet for the Shiori Rates Engine (Issue #226 sections 2.5 and 2.7).
#
# See x64-linux-shiori-gcc.cmake: the numeric policy is applied to the dependency closure, not only to
# Shiori's own sources, because a mixed flag build is a silent numeric-contract violation.
#
# Clang defaults to `-ffp-contract=on` (contraction within a statement), so the explicit
# `-ffp-contract=off` is load-bearing here too.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)

set(VCPKG_CXX_FLAGS "-ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_C_FLAGS "-ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_LINKER_FLAGS "")

set(VCPKG_BUILD_TYPE release)
