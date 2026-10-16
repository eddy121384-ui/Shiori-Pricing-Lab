# CI provisioning vs. source identity (Issue #227; finding `cmake/ShioriBuildIdentity.cmake:82`).
#
# `.github/workflows/cpp-rates-engine.yml` materializes the pinned vcpkg checkout and its binary caches
# INSIDE `$GITHUB_WORKSPACE`, and `cmake/ShioriBuildIdentity.cmake` stamps the reported source identity
# from `git status --porcelain`. Without narrow ignore rules every CLEAN CI artifact was therefore
# reported as `<sha>-dirty` and was indistinguishable from a genuinely modified checkout. The fix is a
# root `.gitignore` covering exactly those provisioning directories.
#
# This check runs in the real checkout and asserts both halves of that fix:
#   * the provisioning directories the workflow creates are invisible to `git status`;
#   * a genuine untracked directory still IS reported (the ignore rules stay narrow).
# The provisioning names are read from the workflow rather than duplicated here, so a new provisioning
# directory is caught by this test instead of silently re-dirtying the identity.
#
# Usage: cmake -DREPO_ROOT=<repo root> -P assert_provisioning_paths_are_ignored.cmake

if(NOT DEFINED REPO_ROOT OR REPO_ROOT STREQUAL "")
  message(FATAL_ERROR "REPO_ROOT is not set: pass -DREPO_ROOT=<repo root>")
endif()

find_package(Git QUIET)
if(NOT GIT_FOUND OR GIT_EXECUTABLE STREQUAL "")
  message(FATAL_ERROR "git is required for the provisioning ignore check")
endif()

set(_workflow "${REPO_ROOT}/.github/workflows/cpp-rates-engine.yml")
if(NOT EXISTS "${_workflow}")
  message(FATAL_ERROR
    "the CI provisioning authority ${_workflow} is missing; this check cannot stay honest without it")
endif()

file(READ "${_workflow}" _workflow_text)
string(REGEX MATCHALL "\\.vcpkg-(root|cache)[A-Za-z0-9_-]*" _provisioning_names "${_workflow_text}")
list(REMOVE_DUPLICATES _provisioning_names)
list(SORT _provisioning_names)

set(_failures "")
# Anti-vacuity: the workflow does provision both families, so a drifted extraction cannot make this
# check pass while testing nothing.
foreach(_family IN ITEMS ".vcpkg-root" ".vcpkg-cache")
  list(FIND _provisioning_names "${_family}" _family_index)
  if(_family_index EQUAL -1)
    string(APPEND _failures "the workflow no longer provisions '${_family}'\n")
  endif()
endforeach()

# The control directory is deliberately NOT provisioning-shaped: it must stay visible to `git status`.
set(_control_name "shiori_ci_provisioning_untracked_control")

set(_created_dirs "")
foreach(_name IN LISTS _provisioning_names)
  # In CI these directories already exist (a full vcpkg checkout) and must never be touched; locally
  # they are created so the check exercises the same situation CI is in, and only then removed.
  if(NOT IS_DIRECTORY "${REPO_ROOT}/${_name}")
    file(MAKE_DIRECTORY "${REPO_ROOT}/${_name}")
    file(WRITE "${REPO_ROOT}/${_name}/provisioning-placeholder.txt" "ci provisioning placeholder\n")
    list(APPEND _created_dirs "${_name}")
  endif()
endforeach()
file(MAKE_DIRECTORY "${REPO_ROOT}/${_control_name}")
file(WRITE "${REPO_ROOT}/${_control_name}/untracked-control.txt" "genuine untracked control\n")
list(APPEND _created_dirs "${_control_name}")

execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${REPO_ROOT}" status --porcelain
  RESULT_VARIABLE _status_result OUTPUT_VARIABLE _porcelain ERROR_VARIABLE _status_error)

if(NOT _status_result EQUAL 0)
  string(APPEND _failures "git status failed: ${_status_error}\n")
else()
  foreach(_name IN LISTS _provisioning_names)
    string(FIND "${_porcelain}" "?? ${_name}/" _found)
    if(NOT _found EQUAL -1)
      string(APPEND _failures
        "'${_name}/' is reported as untracked, so a clean CI artifact's source identity is '-dirty'\n")
    endif()
  endforeach()
  string(FIND "${_porcelain}" "?? ${_control_name}/" _control_found)
  if(_control_found EQUAL -1)
    string(APPEND _failures
      "the untracked control '${_control_name}/' is NOT reported: this check cannot tell a working "
      "ignore rule from a blind one\n")
  endif()
endif()

# Remove only what this check created, so a CI workspace's real provisioning trees are never touched.
foreach(_name IN LISTS _created_dirs)
  if(NOT _name STREQUAL "")
    file(REMOVE_RECURSE "${REPO_ROOT}/${_name}")
  endif()
endforeach()

if(_failures)
  message(FATAL_ERROR "CI provisioning / source identity check FAILED:\n${_failures}")
endif()

message(STATUS "CI provisioning ignore check passed for: ${_provisioning_names}")
