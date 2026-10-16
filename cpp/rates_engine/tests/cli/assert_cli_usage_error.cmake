# Assertion for the #227 CLI usage-error contract, run as its own `cmake -P` test so that the exit code,
# stdout and stderr are checked INDEPENDENTLY. The CLI transport itself is not touched by this check.
#
# Contract (tools/rates_engine_cli/main.cpp lines 6-10 and `usage()` at line 98):
#   * exit 0 = SUCCESS, 2 = contract refusal, 3 = usage error;
#   * a usage error writes its diagnostics to STDERR only and emits NO envelope on stdout.
#
# The form this replaces - `WILL_FAIL TRUE` together with `PASS_REGULAR_EXPRESSION "\"status\":\"FAILED\""`
# - cannot express that. CMake decides the result from the output match (the exit code is not consulted)
# and `WILL_FAIL` then inverts it, so the old property asserted the ABSENCE of the pattern and never
# looked at the exit code: an erroneous `"status":"SUCCESS"` response would have passed it as well.
#
# Usage (see CMakeLists.txt, label `cli`):
#   cmake -DCLI=<cli executable> -P assert_cli_usage_error.cmake                 -> must succeed
#   cmake -DCLI=<unused> -DDECOY=ZERO_EXIT|FAKE_SUCCESS -P ...                   -> must FAIL
# The two DECOY modes are the negative controls for the assertion itself: the CMake test property that
# runs them is `WILL_FAIL TRUE` with NO PASS_REGULAR_EXPRESSION, so there the nonzero exit of this script
# is the whole test result - the exact property the old CLI test was missing.

if(NOT DEFINED DECOY)
  set(DECOY "NONE")
endif()

# `main()` returns `usage(...)`, and `usage()` returns `kExitUsage` (main.cpp:44,105,297).
set(_expected_exit_code "3")

if(DECOY STREQUAL "ZERO_EXIT")
  # A stand-in "CLI" that exits 0 and prints nothing at all.
  set(_command "${CMAKE_COMMAND}" -E true)
elseif(DECOY STREQUAL "FAKE_SUCCESS")
  # A stand-in "CLI" that exits 0 while claiming a successful envelope on stdout.
  set(_command "${CMAKE_COMMAND}" -E echo
      "{\"errors\":[],\"payload\":null,\"schema_version\":\"RATES_ENGINE_CLI_V1\",\"status\":\"SUCCESS\",\"warnings\":[]}")
else()
  if(NOT DEFINED CLI OR CLI STREQUAL "")
    message(FATAL_ERROR "CLI is not set: pass -DCLI=<path to shiori_rates_cli>")
  endif()
  set(_command "${CLI}" --operation definitely-not-an-operation)
endif()

execute_process(COMMAND ${_command}
  RESULT_VARIABLE _exit_code
  OUTPUT_VARIABLE _stdout
  ERROR_VARIABLE _stderr)

set(_failures "")
if(NOT _exit_code STREQUAL _expected_exit_code)
  string(APPEND _failures
    "  exit code is '${_exit_code}', expected '${_expected_exit_code}' (kExitUsage)\n")
endif()

# A usage error is not an operation result: stdout must stay empty, and it must never carry an envelope
# or a success token even if a future change started printing something there.
string(STRIP "${_stdout}" _stdout_stripped)
if(NOT _stdout_stripped STREQUAL "")
  string(APPEND _failures
    "  stdout must stay empty on a usage error, observed '${_stdout_stripped}'\n")
endif()
if(_stdout MATCHES "SUCCESS")
  string(APPEND _failures "  stdout claims success\n")
endif()
if(_stdout MATCHES "schema_version")
  string(APPEND _failures "  stdout carries an envelope schema token\n")
endif()

# The diagnostic must name the offending operation and offer the usage line.
if(NOT _stderr MATCHES "unknown operation")
  string(APPEND _failures "  stderr does not name the unknown operation\n")
endif()
if(NOT _stderr MATCHES "--operation")
  string(APPEND _failures "  stderr does not print the usage line\n")
endif()

if(_failures)
  message(FATAL_ERROR
    "CLI usage-error assertion FAILED (DECOY=${DECOY}):\n${_failures}"
    "observed: exit=${_exit_code}\nstdout='${_stdout}'\nstderr='${_stderr}'")
endif()

message(STATUS
  "CLI usage-error contract holds: exit ${_exit_code}, empty stdout, diagnostic on stderr")
