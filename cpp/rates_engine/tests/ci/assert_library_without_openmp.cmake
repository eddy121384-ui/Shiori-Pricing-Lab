# Fails unless LIBRARY was compiled WITHOUT OpenMP code generation (Issue #226 docs/34 section 4.11
# O5/O6). QuantLib 1.43 has three BARE `#pragma omp` sites that any OpenMP compile flag silently
# activates while QL_ENABLE_OPENMP still reports OFF, so the option cannot prove the absence of
# library-internal parallelism; the produced object code can. A compiler that honours an OpenMP pragma
# emits calls into its OpenMP runtime, and a static library records those as undefined symbols:
#
#   GCC          GOMP_*             (libgomp)
#   Clang/Intel  __kmpc_*           (libomp)
#   MSVC         _vcomp_* plus a /DEFAULTLIB:VCOMP directive (vcomp140)
#   any          omp_get_* / omp_set_* / omp_in_* runtime API calls
#
# A library whose symbol names carry none of these was not compiled with OpenMP active.
#
# Usage: cmake -DLIBRARY=<static library> -P assert_library_without_openmp.cmake
cmake_minimum_required(VERSION 3.25)

if(NOT DEFINED LIBRARY OR LIBRARY STREQUAL "")
  message(FATAL_ERROR "assert_library_without_openmp: LIBRARY is required")
endif()
if(NOT EXISTS "${LIBRARY}" OR IS_DIRECTORY "${LIBRARY}")
  message(FATAL_ERROR "assert_library_without_openmp: '${LIBRARY}' is not a library file; "
                      "an unreadable library is not evidence of an OpenMP-free build")
endif()
file(SIZE "${LIBRARY}" _size)
if(_size EQUAL 0)
  message(FATAL_ERROR "assert_library_without_openmp: '${LIBRARY}' is empty")
endif()

# Symbol and directive names are NUL-separated printable runs inside the archive, so each run is
# matched from its start; the directive is matched anywhere within its run.
set(_runtime_symbol "^_*(imp_+)?(GOMP_|kmpc_|vcomp_|omp_get_|omp_set_|omp_in_)")
set(_vcomp_directive "[Dd][Ee][Ff][Aa][Uu][Ll][Tt][Ll][Ii][Bb]:\"?[Vv][Cc][Oo][Mm][Pp]")
file(STRINGS "${LIBRARY}" _hits REGEX "${_runtime_symbol}|${_vcomp_directive}")

if(_hits)
  list(REMOVE_DUPLICATES _hits)
  list(LENGTH _hits _count)
  list(SUBLIST _hits 0 10 _shown)
  list(JOIN _shown "\n  " _shown_text)
  message(FATAL_ERROR
    "OpenMP code generation found in '${LIBRARY}' (${_count} distinct OpenMP runtime references, "
    "first ones:\n  ${_shown_text}\n"
    "docs/34 section 4.11 O5/O6 requires QuantLib to be built without OpenMP: an OpenMP flag injected "
    "by a triplet, toolchain or environment activates the bare QuantLib pragmas and breaks the "
    "serialized single-threaded posture (section 5.2). Refusing to build.")
endif()
message(STATUS "assert_library_without_openmp: no OpenMP runtime reference in '${LIBRARY}'")
