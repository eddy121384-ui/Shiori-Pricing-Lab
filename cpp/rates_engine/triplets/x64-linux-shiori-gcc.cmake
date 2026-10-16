# Linux GCC triplet for the Shiori Rates Engine (Issue #226 sections 2.5 and 2.7).
#
# WHY A CUSTOM TRIPLET EXISTS AT ALL: GCC's default is `-ffp-contract=fast`, which is contraction and
# therefore a difference in floating-point results between this build and a build made with another
# compiler. #226 forbids fast-math and requires `-ffp-contract=off`, and that rule must hold for the
# DEPENDENCY CLOSURE too -- not only for Shiori's own sources. An engine compiled with `-ffp-contract=off`
# linked against a QuantLib compiled with `-ffp-contract=fast` is exactly the configuration mismatch
# the numeric policy exists to prevent.
#
# On Windows the pinned `x64-windows-static` triplet already compiles with MSVC's default `/fp:precise`,
# so no custom triplet is needed there.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)

set(VCPKG_CXX_FLAGS "-ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_C_FLAGS "-ffp-contract=off -fno-fast-math -fno-unsafe-math-optimizations")
set(VCPKG_LINKER_FLAGS "")

set(VCPKG_BUILD_TYPE release)
