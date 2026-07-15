# Claude Code Instructions

@AGENTS.md

`AGENTS.md` is the repository authority. This file adds only Claude Code execution guidance.

## Before editing

- Identify the current Eddy-approved issue or PR slice.
- Confirm the branch, files, runtime path, and stop conditions.
- Read only the code, tests, and methodology needed for that slice.
- Apply the lean implementation gate before proposing new code or structure.

## Execution

- Implement one smallest complete slice.
- Reuse existing helpers and installed dependencies.
- Do not add speculative abstractions, compatibility behavior, persistence, or future-phase scaffolding.
- Stop on an unauthorized RED decision or scope contamination.
- Run the smallest relevant checks; RED changes also require focused tests, the full suite, and lint when practical.

## Pull requests

After opening or updating a PR, leave one concise top-level comment containing:

- changed behavior;
- changed files;
- exact checks and results;
- scope intentionally left out;
- current review and merge status.

Do not repeat project history or restate documents already linked by the issue.

Do not trigger Codex, resolve review threads, approve, merge, or close issues unless Eddy explicitly authorizes that action.
