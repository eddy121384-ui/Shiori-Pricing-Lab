#!/usr/bin/env python3
"""Generate the Python <-> C++ canonicalization parity fixtures (Issue #227).

WHAT THIS PROVES
----------------
The canonical JSON form and the SHA-256 content fingerprint are a *cross-language* contract: the
Python application writes canonical documents and the C++ engine must reproduce the identical bytes
and the identical digest. This script writes a tracked fixture file that BOTH sides assert against:

* ``tests/test_rates_bridge_canonical.py`` (Python) asserts the fixture still matches the Python
  implementation;
* ``cpp/rates_engine/tests/parity/test_canonical_parity.cpp`` (C++) asserts the C++ implementation
  reproduces every canonical string and every fingerprint byte for byte.

Neither side is allowed to regenerate the fixture from its own output at test time: a fixture both
sides merely agree on is not evidence. The numbers below are ground truth that was *independently*
verified against V8's ``String(number)`` (see the Python bridge test), so the fixture transfers an
external oracle into the C++ suite rather than encoding C++'s own behaviour.

The file is deterministic: regenerate with

    python cpp/rates_engine/tests/fixtures/generate_parity_fixtures.py

and the committed bytes must not change. A diff means the canonical rule changed, which is a
contract change and not a test refresh.
"""

from __future__ import annotations

import json
import pathlib
import random
import struct
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "src"))

from shiori_pricing_lab.rates_bridge import (  # noqa: E402
    canonical_number,
    dumps_bytes,
    fingerprint_bytes,
)

OUTPUT = pathlib.Path(__file__).resolve().parent / "canonical_parity_fixtures.json"

# Anchors that pin each branch of the ECMAScript Number::toString rule: the fixed/scientific
# thresholds, signed zero, subnormals, the 2**53 exact-integer boundary, and the largest finite
# double.
ANCHOR_NUMBERS = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    100.0,
    0.0425,
    0.1,
    0.2,
    0.3,
    1.0 / 3.0,
    3.141592653589793,
    1e-7,
    1e-6,
    1e-5,
    1e15,
    1e16,
    1e20,
    1e21,
    1e22,
    -1e21,
    2.0**53,
    2.0**53 + 2.0,
    123456789012345680.0,
    4.9e-324,
    5e-324,
    2.2250738585072014e-308,
    1.7976931348623157e308,
    1.5e-320,
    9007199254740991.0,
    0.5,
    0.25,
    1.25,
]


def _anchors() -> list[float]:
    values = list(ANCHOR_NUMBERS)
    # A deterministic pseudorandom sweep with an explicit seed: reproducible across Python versions
    # (random.Random is a stable algorithm), so regenerating never produces a spurious diff.
    rng = random.Random(20260610)
    for _ in range(220):
        values.append(rng.uniform(-1e6, 1e6))
    for _ in range(120):
        values.append(rng.uniform(-1.0, 1.0))
    for _ in range(120):
        values.append(rng.uniform(-1e-8, 1e-8))
    for _ in range(60):
        exponent = rng.randint(-320, 308)
        mantissa = rng.uniform(1.0, 9.999)
        values.append(mantissa * (10.0**exponent) if -300 < exponent < 300 else 0.0)
    # Raw bit patterns cover subnormals and values a decimal literal cannot express exactly.
    for step in range(1, 160):
        bits = (step * 0x0123456789ABCDEF) & 0xFFFFFFFFFFFFFFFF
        candidate = struct.unpack("<d", struct.pack("<Q", bits))[0]
        if candidate == candidate and candidate not in (float("inf"), float("-inf")):
            values.append(candidate)
    return values


# Documents exercise: key sorting by UTF-8 byte order (including a non-ASCII key), whitespace and
# key-order removal, minimal string escaping, the named control escapes and the \u00xx form, integer
# versus float representation, nesting, and empty containers.
DOCUMENTS = [
    {
        "name": "empty_object",
        "json": "{ }",
    },
    {
        "name": "key_order_and_whitespace",
        "json": '{ "zebra" : 1 ,\n  "alpha": [ 1, 2, { "b": true, "a": null } ] , "beta": "x" }',
    },
    {
        "name": "utf8_key_order",
        "json": '{"é": 1, "z": 2, "A": 3, "a": 4, "0": 5}',
    },
    {
        "name": "string_escapes",
        "json": json.dumps(
            {
                "quote": 'he said "hi"',
                "backslash": "a\\b",
                "controls": "tab\tnl\ncr\rbs\bff\f",
                "low_control": "\u0001\u001f",
                "literal_utf8": "café — 東京",
                "slash": "not/escaped",
            },
            ensure_ascii=False,
        ),
    },
    {
        "name": "numeric_forms",
        "json": '{"int": 42, "neg": -7, "zero": 0, "big": 9007199254740993, "float": 1.5, '
        '"float_whole": 100.0, "tiny": 1e-7, "huge": 1e21, "negative_zero": -0.0}',
    },
    {
        "name": "nested_arrays",
        "json": '[[], {}, [{}], {"a": [[1], [2, [3]]]}]',
    },
    {
        "name": "unicode_escaped_input",
        "json": '{"escaped": "\\u00e9", "raw": "é", "latin1_escaped": "\\u0000"}',
    },
]


def main() -> int:
    numbers = []
    seen: set[float] = set()
    for value in _anchors():
        if value in seen:
            continue
        seen.add(value)
        numbers.append({"value": value, "canonical": canonical_number(value)})
        # The negative of every value is part of the same rule; include it explicitly.
        if -value not in seen:
            seen.add(-value)
            numbers.append({"value": -value, "canonical": canonical_number(-value)})

    documents = []
    for document in DOCUMENTS:
        parsed = json.loads(document["json"])
        canonical = dumps_bytes(parsed)
        documents.append(
            {
                "name": document["name"],
                # `json` is the PARSED value, so both sides canonicalize the same value tree.
                "json": parsed,
                # `json_text` is the deliberately NON-canonical source text. The C++ suite parses
                # it, so whitespace removal and key ordering are proven across languages.
                "json_text": document["json"],
                "canonical": canonical.decode("utf-8"),
                "fingerprint": fingerprint_bytes(canonical),
            }
        )

    payload = {
        "schema_version": "SHIORI_CANONICAL_PARITY_V1",
        "generated_by": "cpp/rates_engine/tests/fixtures/generate_parity_fixtures.py",
        "numbers": numbers,
        "documents": documents,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    # Written with explicit LF and no newline translation: the fixture must be byte-identical on
    # every platform, otherwise "regenerate and diff" cannot be used as a check.
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"wrote {OUTPUT} ({len(numbers)} numbers, {len(documents)} documents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
