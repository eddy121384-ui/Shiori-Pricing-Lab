"""Proves the prototype's static files no longer carry the old hardcoded
reference-image market claims that a real pricing case now runs underneath
(PR #136 correctness follow-up, and the Codex review follow-up round).

This is a plain text-content check on the on-disk HTML/JS -- no browser, no
DOM -- deliberately narrow: it exists only to catch a regression where
someone reintroduces the fictitious "US TREASURY" identity, its CUSIP/ISIN,
the 32nds-formatted "96-15" price, the fabricated countdown/delay/vol/rate
numbers, or the US flag icon next to a screenshot of a real pricing run
against the bundled synthetic bond (which has no country field at all).
"""

from __future__ import annotations

from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parents[1] / "prototype" / "bond-option-workbench"

_STALE_MARKET_CLAIMS = (
    "US TREASURY",
    "91282CPZ7",  # the old fabricated CUSIP
    "US91282CPZ72",  # the old fabricated ISIN
    "96-15",  # the old 32nds-formatted price
    "090 18:25",  # the old fabricated "time to expiry" countdown
    "71.500",  # the old fabricated Normal Yield Vol (bp)
    "15.54",  # the old fabricated Lognormal Yield Vol
    "3.750",  # the old fabricated USD Rate (MMkt)
    "#b22234",  # the old US flag's red (Codex review: no country field exists)
    "#3c3b6e",  # the old US flag's blue
    # Codex final re-review: these rows/labels are fully removed (not
    # relabeled "Not available") -- none of these concepts exist in the
    # base case schema at all, and leaving the label with a placeholder
    # value still implied the product supports them.
    "Normal Yield Vol",
    "Lognormal Yield Vol",
    "USD Rate (MMkt)",
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


def test_index_html_has_no_us_flag_element() -> None:
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    assert 'class="flag"' not in text


def test_index_html_wires_timing_fields_to_real_context_elements() -> None:
    # The old hardcoded "Time to Expiry" (090 18:25) and "Delivery Delay"
    # ("1") controls are gone; these element IDs are populated by script.js
    # from context.pricing_timestamp / expiry_timestamp / settlement_lag_days.
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "option-terms-pricing-timestamp",
        "option-terms-expiry-timestamp",
        "option-terms-settlement-lag",
    ):
        assert f'id="{element_id}"' in text
