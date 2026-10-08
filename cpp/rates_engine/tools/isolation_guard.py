#!/usr/bin/env python3
"""QuantLib isolation guard for ``cpp/rates_engine/`` (Issue #227, enforcing #226 section 3.9).

WHAT IT ENFORCES
----------------
``shiori_rates_dto`` must have zero QuantLib dependency, and the adapter must remain the sole
production containment boundary. The boundary is not a convention: this tool parses every C++ source
and header in the tree and fails when a file that is not allowed to know about QuantLib does.

Tiers, by path relative to ``cpp/rates_engine/``:

  production  ``src/**``, ``include/**``, ``tools/**``   QuantLib includes allowed ONLY in
                                                         ``src/quantlib_adapter/**``
  test        ``tests/**``                               QuantLib includes allowed ONLY in
                                                         ``tests/adapter/**`` and
                                                         ``tests/defaults/**``
  benchmark   ``benchmarks/**``                          no QuantLib includes at all

Anything else with a C++ extension is ``UNCLASSIFIED`` and fails closed, because a file the guard
cannot place is a file it cannot clear.

WHY THE SCANNER IS NOT A ``grep``
---------------------------------
A QuantLib include in a comment or string literal is not a dependency, and a real include can be
split across lines with a trailing backslash or hidden behind a comment gap. A naive pattern search
gives both false positives (failing on a comment that merely documents the rule) and false negatives
(missing a continued include). This scanner first removes comments and literals while PRESERVING
line structure, then parses ``#include`` directives on logical lines.
The guard also reports a plain ``QuantLib::`` mention in a file that may not depend on QuantLib, and
``--self-test`` proves the guard is able to fail, including the raw-string and continuation traps.
A guard that cannot fail is not evidence.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile
from dataclasses import dataclass

CXX_EXTENSIONS = {".cpp", ".cc", ".cxx", ".hpp", ".h", ".hh", ".hxx", ".inl", ".ipp"}
IGNORED_DIRECTORIES = {"cmake", "docs", "triplets", "build"}
IGNORED_SUFFIXES = {".md", ".json", ".cmake", ".py", ".txt", ".in"}

PRODUCTION_PREFIXES = ("src/", "include/", "tools/")
TEST_PREFIXES = ("tests/",)
BENCHMARK_PREFIXES = ("benchmarks/",)
PRODUCTION_QUANTLIB_ALLOWLIST = ("src/quantlib_adapter/",)
TEST_QUANTLIB_ALLOWLIST = ("tests/adapter/", "tests/defaults/")

QUANTLIB_INCLUDE = re.compile(r"^[<\"](?:[^\"<>]*/)?ql(?:\.hpp|/[^\"<>]*)[>\"]$")
QUANTLIB_SYMBOL = re.compile(r"\bQuantLib\s*::|^\s*namespace\s+QuantLib\b", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    path: str
    line: int
    detail: str

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "detail": self.detail,
        }


def strip_comments_and_literals(text: str) -> str:
    """Return ``text`` with comments and string/char/raw literals replaced by spaces.

    Newlines are preserved so that reported line numbers still refer to the original file, and every
    replaced character becomes a space so a stripped literal cannot be reconstructed from the scan.
    """
    out = list(text)
    index = 0
    length = len(text)

    def blank(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if out[position] != "\n":
                out[position] = " "

    while index < length:
        char = text[index]

        # Line comment.
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            blank(index, end)
            index = end
            continue

        # Block comment.
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            blank(index, end)
            index = end
            continue

        # Raw string literal: R"delim( ... )delim", possibly with an empty delimiter.
        if char == "R" and index + 1 < length and text[index + 1] == '"':
            delimiter_start = index + 2
            delimiter_end = text.find("(", delimiter_start)
            if delimiter_end != -1 and delimiter_end - delimiter_start <= 16:
                delimiter = text[delimiter_start:delimiter_end]
                terminator = ")" + delimiter + '"'
                end = text.find(terminator, delimiter_end + 1)
                end = length if end == -1 else end + len(terminator)
                blank(index, end)
                index = end
                continue

        # Ordinary string or character literal, including a prefixed one (u8"...", L'...').
        if char in "\"'":
            quote = char
            cursor = index + 1
            while cursor < length:
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == quote:
                    cursor += 1
                    break
                if text[cursor] == "\n":
                    break
                cursor += 1
            blank(index, cursor)
            index = cursor
            continue

        index += 1

    return "".join(out)


def classify(relative_path: str) -> str:
    if relative_path.startswith(PRODUCTION_PREFIXES):
        return "production"
    if relative_path.startswith(TEST_PREFIXES):
        return "test"
    if relative_path.startswith(BENCHMARK_PREFIXES):
        return "benchmark"
    return "unclassified"


def quantlib_allowed(tier: str, relative_path: str) -> bool:
    if tier == "production":
        return relative_path.startswith(PRODUCTION_QUANTLIB_ALLOWLIST)
    if tier == "test":
        return relative_path.startswith(TEST_QUANTLIB_ALLOWLIST)
    return False


def logical_include_lines(stripped: str) -> list[tuple[int, str]]:
    """Yield ``(line_number, directive)`` for ``#include`` directives, joining continuations."""
    physical = stripped.split("\n")
    results: list[tuple[int, str]] = []
    index = 0
    while index < len(physical):
        line_number = index + 1
        current = physical[index]
        while current.rstrip().endswith("\\") and index + 1 < len(physical):
            current = current.rstrip()[:-1] + " " + physical[index + 1]
            index += 1
        directive = current.strip()
        if directive.startswith("#") and "include" in directive:
            results.append((line_number, directive))
        index += 1
    return results


def scan_text(relative_path: str, text: str) -> list[Finding]:
    tier = classify(relative_path)
    findings: list[Finding] = []
    if tier == "unclassified":
        findings.append(
            Finding(
                "UNCLASSIFIED",
                "error",
                relative_path,
                1,
                "file is in no known tier; the guard fails closed rather than clearing it",
            )
        )
        return findings

    stripped = strip_comments_and_literals(text)
    allowed = quantlib_allowed(tier, relative_path)

    for line_number, directive in logical_include_lines(stripped):
        match = re.search(r"#\s*include\s*(.+)$", directive)
        if match is None:
            continue
        target = match.group(1).strip()
        if not QUANTLIB_INCLUDE.match(target):
            continue
        if allowed:
            continue
        findings.append(
            Finding(
                "QUANTLIB_INCLUDE",
                "error",
                relative_path,
                line_number,
                f"{tier} file may not include QuantLib: {target}",
            )
        )

    if not allowed:
        for match in QUANTLIB_SYMBOL.finditer(stripped):
            line_number = stripped.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(
                    "SYMBOL_MENTION",
                    "error",
                    relative_path,
                    line_number,
                    "names a QuantLib symbol outside the containment boundary",
                )
            )

    return findings


def iter_cxx_files(root: pathlib.Path) -> list[pathlib.Path]:
    results = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in CXX_EXTENSIONS:
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if any(part in IGNORED_DIRECTORIES for part in parts[:-1]):
            continue
        if relative.suffix.lower() in IGNORED_SUFFIXES:
            continue
        results.append(path)
    return sorted(results)


def scan_tree(root: pathlib.Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    files = iter_cxx_files(root)
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(
                Finding("UNREADABLE", "error", relative, 1, "file is not valid UTF-8")
            )
            continue
        findings.extend(scan_text(relative, text))
    return findings, len(files)


# ---------------------------------------------------------------------------------------------
# Self-test: the guard must be able to fail, including through the traps that defeat a naive scan.
# ---------------------------------------------------------------------------------------------
SELF_TEST_CASES: list[tuple[str, str, list[str], bool]] = [
    # (relative path, file content, expected finding codes, expect clean)
    (
        "src/engine/trap_prod_include.cpp",
        '#include <ql/time/date.hpp>\nint x;\n',
        ["QUANTLIB_INCLUDE"],
        False,
    ),
    (
        "src/engine/trap_prod_symbol.cpp",
        '#include "x.hpp"\nQuantLib::Date d;\n',
        ["SYMBOL_MENTION"],
        False,
    ),
    (
        "src/engine/trap_comment_clean.cpp",
        '// #include <ql/time/date.hpp>\n'
        "/* #include <ql/time/date.hpp> */\n"
        'const char* s = R"raw(#include <ql/time/date.hpp>)raw";\n'
        'const char* q = "#include <ql/time/date.hpp>";\n',
        [],
        True,
    ),
    (
        "src/engine/trap_rawstring.cpp",
        'const char* s = R"(#include <ql/a.hpp> // )";\n'
        "/* #include <ql/b.hpp> */\n"
        "#include <ql/date.hpp>\n",
        ["QUANTLIB_INCLUDE"],
        False,
    ),
    (
        "src/engine/trap_continuation.cpp",
        "#include \\\n  <ql/date.hpp>\n",
        ["QUANTLIB_INCLUDE"],
        False,
    ),
    (
        "tests/adapter/allowed.cpp",
        "#include <ql/settings.hpp>\n",
        [],
        True,
    ),
    (
        "tests/defaults/allowed.cpp",
        "#include <ql/version.hpp>\n",
        [],
        True,
    ),
    (
        "tests/dto/not_allowed.cpp",
        "#include <ql/time/date.hpp>\n",
        ["QUANTLIB_INCLUDE"],
        False,
    ),
    (
        "include/shiori_rates/public_bad.hpp",
        "#include <ql/date.hpp>\n",
        ["QUANTLIB_INCLUDE"],
        False,
    ),
    (
        "tools/rates_engine_cli/tool_bad.cpp",
        "#include <ql/date.hpp>\n",
        ["QUANTLIB_INCLUDE"],
        False,
    ),
    (
        "benchmarks/dto/bench_bad.cpp",
        "#include <ql/date.hpp>\n",
        ["QUANTLIB_INCLUDE"],
        False,
    ),
    (
        "somewhere/mystery.cpp",
        "int x;\n",
        ["UNCLASSIFIED"],
        False,
    ),
]


def run_self_test() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(prefix="shiori_guard_selftest_") as directory:
        root = pathlib.Path(directory)
        for relative, content, *_ in SELF_TEST_CASES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        for relative, _content, expected_codes, expect_clean in SELF_TEST_CASES:
            findings = scan_text(relative, (root / relative).read_text(encoding="utf-8"))
            codes = sorted({finding.code for finding in findings})
            ok = (not findings) if expect_clean else (codes == sorted(expected_codes))
            status = "PASS" if ok else "FAIL"
            if not ok:
                failures += 1
            expected = expected_codes or ["<clean>"]
            print(f"  [{status}] {relative}: expected={expected} observed={codes}")

        # A clean tree must produce no findings and a poisoned tree must produce some: this proves
        # the scan is wired to the file walk, not only to the per-file classifier.
        clean_root = root / "clean_tree"
        (clean_root / "src/dto").mkdir(parents=True, exist_ok=True)
        (clean_root / "src/dto/ok.cpp").write_text(
            '#include <string>\n// QuantLib::Date is mentioned only in a comment here\n',
            encoding="utf-8",
        )
        (clean_root / "tests/dto").mkdir(parents=True, exist_ok=True)
        (clean_root / "tests/dto/ok.cpp").write_text("#include <string>\n", encoding="utf-8")
        (clean_root / "tests/adapter").mkdir(parents=True, exist_ok=True)
        (clean_root / "tests/adapter/ok.cpp").write_text(
            "#include <ql/date.hpp>\n", encoding="utf-8"
        )
        clean_findings, clean_files = scan_tree(clean_root)
        if clean_findings or clean_files != 3:
            failures += 1
            print(f"  [FAIL] clean tree: findings={clean_findings} files={clean_files}")
        else:
            print(f"  [PASS] clean tree: 0 findings over {clean_files} files")

        dirty_root = root / "dirty_tree"
        (dirty_root / "src/dto").mkdir(parents=True, exist_ok=True)
        (dirty_root / "src/dto/bad.cpp").write_text("#include <ql/date.hpp>\n", encoding="utf-8")
        dirty_findings, dirty_files = scan_tree(dirty_root)
        if not dirty_findings or dirty_files != 1:
            failures += 1
            print(f"  [FAIL] dirty tree: findings={dirty_findings} files={dirty_files}")
        else:
            print(f"  [PASS] dirty tree: {len(dirty_findings)} findings over {dirty_files} files")

    print(f"self-test: {'FAILED' if failures else 'PASSED'} ({failures} failing case(s))")
    return 1 if failures else 0


def find_repo_root(start: pathlib.Path) -> pathlib.Path:
    for candidate in [start, *start.parents]:
        if (candidate / "AGENTS.md").is_file():
            return candidate
    return start


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="QuantLib isolation guard for cpp/rates_engine/")
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=None,
        help="repository root (default: discovered via AGENTS.md)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable findings")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the guard can fail, using temporary fixtures",
    )
    parser.add_argument("--list-allowlist", action="store_true", help="print the allow-list")
    arguments = parser.parse_args(argv)

    if arguments.list_allowlist:
        print("production allow-list:", ", ".join(PRODUCTION_QUANTLIB_ALLOWLIST))
        print("test allow-list:", ", ".join(TEST_QUANTLIB_ALLOWLIST))
        print("benchmark allow-list: <none>")
        return 0

    if arguments.self_test:
        return run_self_test()

    repo_root = arguments.root.resolve() if arguments.root else find_repo_root(pathlib.Path.cwd())
    tree = repo_root / "cpp" / "rates_engine"
    if not tree.is_dir():
        print(f"isolation guard: {tree} does not exist", file=sys.stderr)
        return 2

    findings, file_count = scan_tree(tree)

    if arguments.json:
        print(
            json.dumps(
                {
                    "root": str(tree),
                    "files_scanned": file_count,
                    "findings": [finding.as_dict() for finding in findings],
                    "violation_count": len(findings),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for finding in findings:
            print(
                f"{finding.severity.upper()}: {finding.path}:{finding.line}: "
                f"{finding.code}: {finding.detail}"
            )
        print(f"isolation guard: {file_count} C++ file(s) scanned, {len(findings)} finding(s)")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
