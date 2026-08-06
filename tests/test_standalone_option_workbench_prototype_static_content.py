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

import re
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


def test_index_html_wires_timing_fields_without_duplicate_trader_entry() -> None:
    # The old hardcoded "Time to Expiry" (090 18:25) and "Delivery Delay"
    # ("1") controls are long gone. Fields still owned by the trader remain
    # real inputs. Pricing/as-of/valuation are read-only because the Bloomberg
    # acquisition event now supplies them mechanically, and (Issue #143) the
    # expiry timestamp is read-only too because the one Expiry interaction
    # composes it.
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "pricing-timestamp-input",
        "expiry-timestamp-input",
        "as-of-timestamp-input",
        "expiry-datetime-input",
        "expiry-offset-input",
        "valuation-date-input",
        "reporting-date-input",
        "forward-settlement-date-input",
        "option-settlement-date-input",
    ):
        assert f'id="{element_id}"' in text
    for element_id in (
        "pricing-timestamp-input",
        "as-of-timestamp-input",
        "valuation-date-input",
        "expiry-timestamp-input",
    ):
        assert f'id="{element_id}"' in text
        start = text.index(f'id="{element_id}"')
        assert "readonly" in text[start : start + 180]
    # The superseded display-only elements must not linger alongside the
    # inputs that replaced them -- two places showing the same field is
    # exactly the ambiguity this replacement removes. `expiry-date-input` and
    # the free-text `expiry-timestamp` entry are gone as separate controls:
    # Issue #143 folds them into one Expiry interaction.
    for removed_id in (
        "option-terms-pricing-timestamp",
        "option-terms-expiry-timestamp",
        "option-terms-settlement-lag",
        "option-terms-expiry",
        "settlement-lag-input",
        "business-day-convention-select",
        "redemption-amount-input",
        "yield-convention-select",
        "expiry-date-input",
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


# --- Minimum-input trader workflow (Issue #143) -------------------------------


def test_main_screen_declares_between_six_and_nine_workflow_groups() -> None:
    """Acceptance criterion 2, checked on the static markup as well as in the
    browser: the ordinary trader flow is a short sequence of real decisions and
    reviews, not a backend metadata wall."""

    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    groups = re.findall(r'data-workflow-group="([a-z-]+)"', text)
    assert 6 <= len(groups) <= 9
    assert groups == [
        "instrument",
        "option-type",
        "position",
        "strike",
        "notional",
        "expiry",
        "forward-review",
        "vol-review",
        "discounting-review",
    ]


def test_advanced_section_starts_collapsed_in_the_markup() -> None:
    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    start = text.index('id="advanced-body"')
    assert "hidden" in text[start : start + 60]
    assert '>Expand<' in text[text.index('id="advanced-indicator"') :][:60]


def test_unresolved_dependency_panel_states_what_why_evidence_and_next_step() -> None:
    """Acceptance criterion 6: the blocker explanation is a fixed four-part
    structure, not a bare 'unavailable'."""

    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for label in (
        "What is missing",
        "Why it is missing",
        "Bloomberg evidence",
        "What you can do next",
    ):
        assert label in text
    for element_id in (
        "unresolved-title",
        "unresolved-missing",
        "unresolved-why",
        "unresolved-evidence",
        "unresolved-next",
    ):
        assert f'id="{element_id}"' in text


def test_script_records_the_real_bloomberg_evidence_for_each_unresolved_input() -> None:
    """The evidence strings must name the actual mnemonic and the actual
    response, so an unexplained blank field can never reappear."""

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    # Forward: the confirmed overrides and the real BAD_FLD answer.
    assert "OPT_UNDL_FORWARD_PX" in text
    assert "Field not applicable to security" in text
    assert "OP046" in text and "OP188" in text
    # OP131 appears only to say it is never sent and never defaulted to zero.
    assert "never defaulted to 0" in text
    # Volatility: both candidates came back BAD_FLD, and YIELD_VOL is refused.
    assert "PRICE_VOL returned BAD_FLD" in text
    assert "EQUIVALENT_PRICE_VOL returned BAD_FLD" in text
    assert "YIELD_VOL is not a substitute" in text
    # Discounting: the prohibited relabelling is named explicitly.
    assert "MMkt, repo, FTP, par and" in text


def test_script_never_maps_a_bloomberg_description_into_a_typed_enum() -> None:
    """The five profile/derivation-owned reference fields must be seeded null
    from a Bloomberg lookup, never from bond_master_raw's description strings."""

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    seeded = text[text.index("bond_reference_data_universe: [") :]
    seeded = seeded[: seeded.index("],")]
    for field in (
        "day_count",
        "bond_type",
        "ex_dividend_days",
        "last_coupon_date",
        "status",
    ):
        assert f"{field}: null," in seeded, f"{field} is not seeded null from a lookup"
    # And the prohibited coercions appear nowhere at all.
    assert "ACT_ACT_ISDA" not in text
    assert "ACT_ACT_BOND" not in text
    assert "FIXED_COUPON_BULLET" not in text


def test_price_gate_is_wired_to_the_real_builder_validation_route() -> None:
    """Requirement 5: Price enablement asks the server's typed builder, and a
    structurally complete form alone never enables it."""

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    assert "/api/case/validate" in text
    assert 'builderValidation.state === "ready"' in text


# --- Issue #157: the eight Advanced technical fields --------------------------


def test_index_html_shows_a_provenance_readout_for_every_pre_filled_field() -> None:
    """No unlabelled silent default: every one of the eight fields has its own
    visible source line, and the section says whether the profile applied."""

    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "prov-day-count",
        "prov-bond-type",
        "prov-ex-dividend-days",
        "prov-last-coupon-date",
        "prov-bond-status",
        "prov-reporting-date",
        "prov-forward-settlement-date",
        "prov-option-settlement-date",
        "advanced-profile-status",
    ):
        assert f'id="{element_id}"' in text


def test_index_html_keeps_every_pre_filled_field_editable() -> None:
    """Pre-filled is not read-only: unlike the derived timing values, none of
    these eight controls is marked readonly."""

    text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "day-count-select",
        "bond-type-select",
        "ex-dividend-days-input",
        "last-coupon-date-input",
        "bond-status-select",
        "reporting-date-input",
        "forward-settlement-date-input",
        "option-settlement-date-input",
    ):
        start = text.index(f'id="{element_id}"')
        assert "readonly" not in text[start : start + 120]


def test_script_asks_the_server_for_the_profile_and_decides_no_convention_itself() -> None:
    """The browser holds no convention, calendar or schedule knowledge: it calls
    the reviewed resolver and renders what comes back.

    ``test_script_never_maps_a_bloomberg_description_into_a_typed_enum`` above
    already asserts the two profile enum literals appear nowhere in this file;
    together they mean an auto-filled value can only have come from the server.
    """

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    assert "/api/bond/advanced-profile" in text
    for tier in (
        "BLOOMBERG_AUTO",
        "SHIORI_DERIVED",
        "UST_PROFILE_DEFAULT",
        "TRADER_OVERRIDE",
    ):
        assert tier in text
    # No calendar, holiday table or coupon-date arithmetic on this side.
    # Identifier-shaped names only: the prose above legitimately explains that
    # Shiori writes no holiday table.
    for forbidden in ("isBusinessDay", "businessDay", "addMonths", "HOLIDAYS"):
        assert forbidden not in text


def test_convention_profile_is_a_browser_state_input_not_a_hardcoded_server_default() -> None:
    """Issue #157 P1-1 correction, extended by Issue #161 requirement D:
    convention_profile must be explicit request input, and the control that
    displays it must be a real, visible, trader-overridable selector sourced
    from that same browser state -- not a second, independently-typed
    string, and no longer a hardcoded constant."""

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    assert "let selectedConventionProfile = null;" in text
    assert "convention_profile: selectedConventionProfile," in text
    assert "convention-profile-select" in text
    # The picker is a genuine override: a change handler that records the
    # trader took the selection over and re-asks the resolver.
    assert "conventionProfileOverridden = true;" in text

    html_text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="convention-profile-select"' in html_text
    # Visible inside the always-rendered card-head row, not inside the
    # collapsible advanced-body -- see the element's position relative to
    # the "Advanced" card-head/card-body boundary.
    head_start = html_text.index('id="advanced-head"')
    body_start = html_text.index('id="advanced-body"')
    picker_pos = html_text.index('id="convention-profile-select"')
    assert head_start < picker_pos < body_start


def test_the_profile_selector_options_are_never_a_second_copy_of_the_registry() -> None:
    """Issue #161 requirement D: the selectable profiles must come from the
    server's own answer, so a trader can never pick a profile the resolver
    would reject for this bond, nor be denied one it would accept.

    The page therefore hardcodes no profile name at all: its options are
    built from the candidates response.
    """

    from shiori_pricing_lab.pricing.bli_bond_convention_profile import (
        SUPPORTED_CONVENTION_PROFILE_NAMES,
    )

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    assert "conventionProfileCandidateNames()" in text
    for name in SUPPORTED_CONVENTION_PROFILE_NAMES:
        assert f'"{name}"' not in text, (
            f"script.js names the {name!r} profile literally; the selector's options must "
            "come from the server's own answer, not a second copy of the registry"
        )


def test_the_page_never_selects_a_convention_profile_for_the_trader() -> None:
    """Issue #161 follow-up item 1, structurally.

    Auto-selection was withdrawn because currency and coupon frequency
    describe a bond's cash flows, not its issuer class. The page must
    therefore assign `selectedConventionProfile` in exactly two places -- the
    picker's own change handler, and the per-bond reset that clears it -- and
    must not read a `suggested` field from anywhere.
    """

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    assert "suggested" not in text

    assignments = [
        line.strip()
        for line in text.splitlines()
        if "selectedConventionProfile =" in line and "===" not in line
    ]
    assert assignments == [
        "let selectedConventionProfile = null;",
        "selectedConventionProfile = null;",
        "selectedConventionProfile = chosen;",
    ], assignments


def test_the_export_source_system_follows_the_selected_profile() -> None:
    """Issue #161 follow-up item 6: with three profiles registered, a fixed
    `SHIORI_UST_...` label would stamp a German government bond's values as
    the UST profile's."""

    text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")
    assert "`SHIORI_${selectedConventionProfile}_CONVENTION_PROFILE`" in text
    assert "profileNameFromTier" in text

    # Executable lines only: the comments legitimately quote the withdrawn
    # label and the tier it belonged to, precisely to record that they are no
    # longer written out.
    code = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("//")
    )
    assert "SHIORI_UST_FIXED_COUPON_PROFILE" not in code
    # The provenance tier's description is derived the same way, so no
    # market's name is written out per tier either.
    assert "UST_PROFILE_DEFAULT" not in code


def test_no_identity_verification_claim_anywhere_in_the_served_page() -> None:
    """Structural proof the withdrawn Treasury-identity gate left no trace in
    the served page's own text -- neither markup nor script content."""

    text = (PROTOTYPE_DIR / "script.js").read_text(
        encoding="utf-8"
    ) + (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")
    lowered = text.lower()
    for forbidden in (
        "identity_verified",
        "treasury_verified",
        "is_treasury",
        "issuer_classification",
        "verified as treasury",
    ):
        assert forbidden not in lowered


# --- Issue #161: one parser, and a preview beside every field it parses ------


def test_every_trader_format_field_has_its_own_normalized_readout() -> None:
    """A normalized value or an explicit error is shown beside each of the
    three fields Issue #161 makes trader-format, so neither a silent
    conversion nor a silently-dropped entry has anywhere to hide."""

    html_text = (PROTOTYPE_DIR / "index.html").read_text(encoding="utf-8")

    for control_id, readout_id in (
        ("strike-price-input", "strike-normalized-preview"),
        ("notional-input", "notional-normalized-preview"),
        ("forward-price-input", "forward-normalized-preview"),
    ):
        assert f'id="{control_id}"' in html_text
        assert f'id="{readout_id}"' in html_text
        # The readout sits with its own control, not in some distant summary.
        assert 0 < html_text.index(readout_id) - html_text.index(control_id) < 400


def test_the_trader_format_parsers_exist_exactly_once_and_only_in_the_browser() -> None:
    """Issue #161 forbids browser/server/builder each growing their own
    parser. There is one Treasury-quote parser and one notional parser, both
    in script.js, and the canonical numeric contract is unchanged everywhere
    else -- so no Python module needs (or has) a copy."""

    script_text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")

    assert script_text.count("function parseTreasuryQuote(") == 1
    assert script_text.count("function parseNotional(") == 1
    # Both trader-format price fields go through the same parser, the notional
    # never does, and each control is read in exactly one place -- so two
    # readers cannot drift into disagreeing about a format.
    assert script_text.count("parseTreasuryQuote(els.strikePrice.value)") == 1
    assert script_text.count("parseTreasuryQuote(els.forwardPrice.value)") == 1
    assert script_text.count("parseNotional(els.notional.value)") == 1
    assert script_text.count("function readTraderFormatInputs(") == 1

    src_dir = PROTOTYPE_DIR.parents[1] / "src"
    for path in src_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "parse_treasury_32nds" not in text, path
        assert "parseTreasuryQuote" not in text, path


def test_the_expiry_offset_default_is_declared_once_and_is_taipei() -> None:
    """The pre-filled offset is one named constant, so "new ticket" and "back
    to +08:00" cannot drift apart, and it is never applied to the expiry date
    or time of day."""

    script_text = (PROTOTYPE_DIR / "script.js").read_text(encoding="utf-8")

    assert script_text.count('const DEFAULT_EXPIRY_UTC_OFFSET = "+08:00";') == 1
    # Written to exactly one control, in exactly one place.
    assert script_text.count("els.expiryOffset.value = DEFAULT_EXPIRY_UTC_OFFSET") == 1
    assert "els.expiryDatetime.value = DEFAULT" not in script_text
