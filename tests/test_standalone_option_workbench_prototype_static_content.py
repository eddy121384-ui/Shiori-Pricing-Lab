"""Proves the prototype's static files no longer carry the old hardcoded
reference-image market claims that a real pricing case now runs underneath
(PR #136 correctness follow-up).

This is a plain text-content check on the on-disk HTML/JS -- no browser, no
DOM -- deliberately narrow: it exists only to catch a regression where
someone reintroduces the fictitious "US TREASURY" identity, its CUSIP/ISIN,
or the 32nds-formatted "96-15" price next to a screenshot of a real pricing
run against the bundled synthetic bond.
"""

from __future__ import annotations

from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parents[1] / "prototype" / "bond-option-workbench"

_STALE_MARKET_CLAIMS = (
    "US TREASURY",
    "91282CPZ7",  # the old fabricated CUSIP
    "US91282CPZ72",  # the old fabricated ISIN
    "96-15",  # the old 32nds-formatted price
)


def test_index_html_has_no_stale_market_claims() -> None:
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for claim in _STALE_MARKET_CLAIMS:
        assert claim not in text, f"stale market claim {claim!r} still present in index.html"


def test_script_js_has_no_stale_market_claims() -> None:
    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    for claim in _STALE_MARKET_CLAIMS:
        assert claim not in text, f"stale market claim {claim!r} still present in script.js"


def test_index_html_references_the_real_case_isin_field() -> None:
    # The real synthetic case's ISIN is now rendered dynamically from
    # /api/base's context, not hardcoded -- it must not appear literally in
    # the markup either (that would just be a different flavor of the same
    # stale-hardcoding bug).
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    assert "XS0000000001" not in text
    assert 'id="instr-isin"' in text
    assert 'id="instr-title"' in text
