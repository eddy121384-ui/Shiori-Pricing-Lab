"""Canonical JSON form and content fingerprints for the Shiori Rates wire contract.

Issue #227 puts a C++20 Rates Engine under ``cpp/rates_engine/``. Python and C++ must
produce the **exact same canonical UTF-8 bytes** -- and therefore the exact same
SHA-256 fingerprint -- for the same logical value, or replay identity (issue #225,
``docs/33_rates_market_model_result_contracts_225.md`` section 15.4) is meaningless.
This module is the Python half of that contract and the single place where the format
is decided:

* **objects** (``dict``): keys sorted by their UTF-8 byte order; rendered as
  ``{"k":v,...}`` with ``,`` and ``:`` separators and no whitespace;
* **arrays** (``list`` / ``tuple``): element order preserved, ``[a,b]``;
* **strings**: minimal JSON escaping (``"``, ``\\``, ``\\b \\f \\n \\r \\t``; any
  other code point below U+0020 as ``\\u00xx``); every other code point -- including
  non-ASCII -- is emitted as literal UTF-8, never ``\\uXXXX``-escaped. Lone surrogates
  are rejected because they have no UTF-8 encoding;
* **numbers**: ECMAScript ``Number::toString`` (ECMA-262) shortest round-tripping form
  with ES fixed/scientific thresholds -- see :func:`canonical_number`;
* **integers**: plain decimal, signed 64-bit only (no ``+``, no leading zeros);
* **``true`` / ``false`` / ``null``**.

The format *details* (separators, escaping, number formatting) are owned by #227 per
``docs/33`` section 15.4; the *preimage* semantics (which fields participate in a
fingerprint) are owned by #225. This module implements the format only: it makes no
pricing, methodology, market-data, or schema-shape decision, performs no I/O, and
never touches the network.

Fingerprint dispatch is deliberately explicit:

* :func:`fingerprint_value` canonicalizes a JSON value and hashes the resulting bytes;
* :func:`fingerprint_bytes` hashes already-serialized bytes/text directly;
* :func:`fingerprint` dispatches -- ``str``/``bytes`` are treated as *already
  serialized* canonical payloads, everything else is canonicalized first.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

__all__ = [
    "SHA256_HEX_LENGTH",
    "canonical_number",
    "dumps",
    "dumps_bytes",
    "fingerprint",
    "fingerprint_bytes",
    "fingerprint_value",
    "is_valid_fingerprint",
]

#: Length of a lowercase hexadecimal SHA-256 digest.
SHA256_HEX_LENGTH = 64

#: Signed 64-bit integer range accepted by the canonical form. Python integers are
#: unbounded, so the bridge must reject anything the C++ DTO cannot carry rather than
#: silently emitting a value that will not round-trip.
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

# The five short escapes JSON defines, plus the two structural characters.
_JSON_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

_LOWER_HEX_DIGITS = frozenset("0123456789abcdef")


def canonical_number(value: float) -> str:
    """Return the ECMAScript ``Number::toString`` text of a finite double.

    This is the algorithm of ECMA-262 (``Number::toString``), i.e. what JavaScript
    prints for a number: the shortest decimal digit string that round-trips to the
    same double, laid out with the ES fixed notation window ``-6 < n <= 21`` and
    scientific notation outside it. Python's ``repr`` gives the same shortest digits,
    so it is used only to *obtain* the digits; the fixed/scientific layout is then
    derived from the ES rules, not from ``repr``.

    Anchors (asserted in ``tests/test_rates_bridge_canonical.py``): ``100.0 -> "100"``,
    ``0.0425 -> "0.0425"``, ``1e16 -> "10000000000000000"``, ``1e20 ->
    "100000000000000000000"``, ``1e21 -> "1e+21"``, ``1e-6 -> "0.000001"``, ``1e-7 ->
    "1e-7"``, ``1.5e-7 -> "1.5e-7"``, ``123456789012345680.0 ->
    "123456789012345680"``. Both zeros serialize as ``"0"`` -- ``-0.0`` is not a
    distinct JSON number.

    ``NaN`` and ``Infinity`` raise :class:`ValueError` (JSON has no representation for
    them, and this contract never guesses a sentinel). Non-numeric input raises
    :class:`TypeError`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"canonical_number() requires a real number, got {type(value).__name__}"
        )
    try:
        number = float(value)
    except OverflowError as exc:  # an int too large to be any double
        raise ValueError(
            f"value is not representable as a finite double: {value!r}"
        ) from exc
    if math.isnan(number):
        raise ValueError("NaN has no canonical JSON number form")
    if math.isinf(number):
        raise ValueError("Infinity has no canonical JSON number form")
    if number == 0.0:  # True for -0.0 as well
        return "0"

    negative = math.copysign(1.0, number) < 0.0

    # Step 1: shortest round-tripping digits, decomposed into (digits, k, n).
    text = repr(abs(number)).lower()
    mantissa, _, exponent_text = text.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    if "." in mantissa:
        point_pos = mantissa.index(".")
        raw = mantissa.replace(".", "", 1)
    else:
        point_pos = len(mantissa)
        raw = mantissa

    trailing_zeros = len(raw) - len(raw.rstrip("0"))
    digits = raw.lstrip("0")
    if trailing_zeros:
        digits = digits[: max(len(digits) - trailing_zeros, 0)]
    if not digits:
        return "0"

    k = len(digits)
    n = point_pos - len(raw) + trailing_zeros + k + exponent

    # Step 2: ECMA-262 Number::toString layout. No exponent is zero-padded: the
    # engine prints "1e+21" and "1.5e-7", never "1e+021" or "1.5e-07".
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        scientific_exponent = n - 1
        sign = "+" if scientific_exponent >= 0 else "-"
        magnitude = str(abs(scientific_exponent))
        if k == 1:
            body = digits + "e" + sign + magnitude
        else:
            body = digits[0] + "." + digits[1:] + "e" + sign + magnitude

    return "-" + body if negative else body


def dumps(value: Any) -> str:
    """Return the canonical JSON *text* for *value*.

    Unsupported types raise :class:`TypeError` -- the serializer never guesses (no
    automatic ``str()``, no ``Decimal``/``datetime`` coercion, no ``__dict__`` dump).
    """
    parts: list[str] = []
    _write_canonical(value, parts)
    return "".join(parts)


def dumps_bytes(value: Any) -> bytes:
    """Return the canonical JSON UTF-8 bytes for *value* (the fingerprint preimage)."""
    return dumps(value).encode("utf-8")


def fingerprint_bytes(data: bytes | bytearray) -> str:
    """Return the lowercase hex SHA-256 of already-serialized canonical bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(
            f"fingerprint_bytes() requires bytes, got {type(data).__name__}"
        )
    return hashlib.sha256(data).hexdigest()


def fingerprint_value(value: Any) -> str:
    """Canonicalize *value* and return the lowercase hex SHA-256 of its UTF-8 bytes."""
    return fingerprint_bytes(dumps_bytes(value))


def fingerprint(value: Any) -> str:
    """Fingerprint either an already-serialized payload or a JSON value.

    ``str`` / ``bytes`` / ``bytearray`` are taken as *already canonical* serialized
    payloads and hashed as-is; every other value is canonicalized first. Use
    :func:`fingerprint_value` when a ``str`` is meant as a JSON string *value*.
    """
    if isinstance(value, (bytes, bytearray)):
        return fingerprint_bytes(value)
    if isinstance(value, str):
        return fingerprint_bytes(value.encode("utf-8"))
    return fingerprint_value(value)


def is_valid_fingerprint(text: Any) -> bool:
    """Return ``True`` when *text* is exactly 64 lowercase hexadecimal characters.

    Fingerprints are wire tokens: uppercase, truncated, padded, or non-text forms are
    rejected rather than normalized, so a mismatch is visible instead of repaired.
    """
    if not isinstance(text, str) or len(text) != SHA256_HEX_LENGTH:
        return False
    return all(character in _LOWER_HEX_DIGITS for character in text)


def _write_canonical(value: Any, out: list[str]) -> None:
    """Append the canonical JSON text of *value* to *out*."""
    if value is None:
        out.append("null")
    elif isinstance(value, bool):
        # Checked before int: bool subclasses int and must never serialize as 0/1.
        out.append("true" if value else "false")
    elif isinstance(value, str):
        out.append(_canonical_string(value))
    elif isinstance(value, int):
        if value < INT64_MIN or value > INT64_MAX:
            raise ValueError(
                f"integer outside the signed 64-bit canonical range: {value!r}"
            )
        out.append(str(value))
    elif isinstance(value, float):
        out.append(canonical_number(value))
    elif isinstance(value, dict):
        _write_canonical_object(value, out)
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _write_canonical(item, out)
        out.append("]")
    else:
        raise TypeError(
            f"unsupported type for canonical JSON: {type(value).__name__}"
        )


def _write_canonical_object(value: dict, out: list[str]) -> None:
    """Append the canonical JSON text of a JSON object, keys in UTF-8 byte order."""
    for key in value:
        if not isinstance(key, str):
            raise TypeError(
                f"canonical JSON object keys must be strings, got {type(key).__name__}"
            )
    # UTF-8 byte order, not UTF-16 code-unit order: the two differ above the BMP
    # (UTF-16 code-unit order sorts a surrogate pair by its lead unit, below U+E000).
    ordered_keys = sorted(value, key=lambda key: key.encode("utf-8"))
    out.append("{")
    for index, key in enumerate(ordered_keys):
        if index:
            out.append(",")
        out.append(_canonical_string(key))
        out.append(":")
        _write_canonical(value[key], out)
    out.append("}")


def _canonical_string(value: str) -> str:
    """Return *value* as a canonical JSON string literal.

    Only the escapes JSON requires are produced. Non-ASCII text is emitted literally
    so the UTF-8 bytes are canonical and stable; control characters other than the
    five short escapes use ``\\u00xx`` with lowercase hexadecimal digits (the same
    lowercase convention the number form uses for ``e`` and the sign).
    """
    out = ['"']
    for character in value:
        escape = _JSON_ESCAPES.get(character)
        if escape is not None:
            out.append(escape)
            continue
        codepoint = ord(character)
        if codepoint < 0x20:
            out.append(f"\\u{codepoint:04x}")
        elif 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(
                f"string contains the lone surrogate U+{codepoint:04X}, "
                "which has no UTF-8 encoding"
            )
        else:
            out.append(character)
    out.append('"')
    return "".join(out)
