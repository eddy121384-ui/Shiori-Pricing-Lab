"""Minimum Python <-> C++ Rates Engine invocation surface (Issue #227).

``shiori_pricing_lab.rates_bridge`` is the smallest possible seam between the Python
application and the C++20 Rates Engine under ``cpp/rates_engine/``. It holds exactly
two capabilities and nothing else:

* :mod:`shiori_pricing_lab.rates_bridge.canonical` -- the canonical UTF-8 JSON form and
  SHA-256 content fingerprint that must match the C++ implementation byte for byte
  (issue #225, ``docs/33_rates_market_model_result_contracts_225.md`` section 15.4);
* :mod:`shiori_pricing_lab.rates_bridge.engine` -- deterministic CLI invocation of the
  engine (canonical request on stdin, result JSON on stdout, exit-2 fail-closed
  refusals surfaced as :class:`RatesEngineError`).

Division of responsibility: **Python remains responsible for the user interface,
market-data acquisition and persistence, research workflows, and validation.** The C++
engine owns deterministic computation and performs **no live Bloomberg access**; this
bridge performs no network access at all. Nothing in this package fetches, guesses, or
fabricates market data, pricing results, or executable paths.
"""

from __future__ import annotations

from shiori_pricing_lab.rates_bridge.canonical import (
    SHA256_HEX_LENGTH,
    canonical_number,
    dumps,
    dumps_bytes,
    fingerprint,
    fingerprint_bytes,
    fingerprint_value,
    is_valid_fingerprint,
)
from shiori_pricing_lab.rates_bridge.engine import (
    RatesEngineError,
    engine_identity,
    find_engine_cli,
    invoke,
)

__all__ = [
    "SHA256_HEX_LENGTH",
    "RatesEngineError",
    "canonical_number",
    "dumps",
    "dumps_bytes",
    "engine_identity",
    "find_engine_cli",
    "fingerprint",
    "fingerprint_bytes",
    "fingerprint_value",
    "invoke",
    "is_valid_fingerprint",
]
