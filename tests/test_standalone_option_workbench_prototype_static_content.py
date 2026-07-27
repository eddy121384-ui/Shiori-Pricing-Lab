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


def test_index_html_wires_timing_fields_to_real_trader_inputs() -> None:
    # The old hardcoded "Time to Expiry" (090 18:25) and "Delivery Delay"
    # ("1") controls are long gone. As of Issue #143 the timing fields are no
    # longer read-only echoes of a priced case's context either -- the trader
    # owns them, so each is a real input the manual completion path fills.
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "pricing-timestamp-input",
        "expiry-timestamp-input",
        "as-of-timestamp-input",
        "settlement-lag-input",
        "expiry-date-input",
        "valuation-date-input",
        "reporting-date-input",
        "forward-settlement-date-input",
        "option-settlement-date-input",
    ):
        assert f'id="{element_id}"' in text
    # The superseded display-only elements must not linger alongside the
    # inputs that replaced them -- two places showing the same field is
    # exactly the ambiguity this replacement removes.
    for removed_id in (
        "option-terms-pricing-timestamp",
        "option-terms-expiry-timestamp",
        "option-terms-settlement-lag",
        "option-terms-expiry",
    ):
        assert f'id="{removed_id}"' not in text


def test_index_html_offers_no_yield_vol_basis_anywhere() -> None:
    # YIELD_VOL is rejected by the reviewed pricing guard and converting a
    # Bloomberg yield vol is unapproved methodology (#103), so the basis
    # control must not offer it at all.
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    assert 'value="PRICE_VOL"' in text
    assert 'value="EQUIVALENT_PRICE_VOL"' in text
    assert 'value="YIELD_VOL"' not in text


def test_curve_editor_locks_the_rate_basis_and_warns_against_relabeling() -> None:
    # The curve editor may only produce CONTINUOUS_ZERO_RATE nodes, and must
    # say plainly that OVME's MMkt/repo rates are not continuous zero rates.
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    assert "CONTINUOUS_ZERO_RATE" in text
    assert "OPTION_DISCOUNT_CURVE" in text
    assert "MMkt" in text and "repo" in text
    # No Bond Reference Curve is offered on this route.
    assert "BOND_REFERENCE_CURVE" not in text
