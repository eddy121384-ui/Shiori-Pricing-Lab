"""Deterministic current-run JSON/Markdown export of the workbench display dict
(Issue #101, P1-08).

Scope: two small, pure functions that turn the **existing** bounded display
dict already returned by ``standalone_option_workbench.price_standalone_option_case``
or ``price_standalone_option_case_with_benchmark`` into deterministic UTF-8
JSON text and deterministic UTF-8 Markdown text. Both consume **only** that
plain ``dict`` -- never a ``PricingResult``, request, benchmark, comparison,
or calibration object, and never the pricing/comparison/calibration
functions themselves. This module computes, prices, compares, calibrates,
and fetches nothing; every value it emits is a verbatim read of a key
already present in ``display``.

**Not a stable, persisted, or replayable report contract.** The JSON/Markdown
text produced here is internal current-run evidence only -- there is no
schema/version registry, no report dataclass, no template engine, no
provider/plugin interface, and no filesystem or session persistence. The
shape mirrors today's ``display`` dict exactly (Issue #97 PR A /
Issue #125); if that dict's shape changes, this export's output changes with
it -- callers must not treat either artifact as a versioned external API.

**JSON.** ``render_standalone_run_as_json`` is ``json.dumps(display, indent=2,
ensure_ascii=False)`` plus a trailing newline, with one addition: a
``default`` hook that serializes a stray ``datetime.date``/``datetime.datetime``
value (none currently appears in ``display`` -- every date-like field is
already an ISO string -- but the hook exists so a future one normalizes
deterministically instead of silently stringifying) via ``.isoformat()``.
Any other type ``json.dumps`` cannot natively encode raises ``TypeError``
explicitly (the stdlib's own behavior for an unhandled ``default`` return) --
never a silent ``str(obj)`` fallback. No key is dropped, renamed, reordered,
or filtered; every ``None`` value serializes as JSON ``null``, exactly as
already carried by the existing display contract (Issue #97 §"never
fabricating a replacement value").

**Markdown.** ``render_standalone_run_as_markdown`` renders the sections
Context / Pricing / Greeks / Effective Forward / Live Bloomberg Quote /
Benchmark / Comparison /
Calibration / Assumptions / Excluded Components / Errors / Solver
Diagnostics -- all but Context/Pricing/Greeks/Assumptions/Excluded
Components are included only when the corresponding data is actually present in
``display`` (mirrors the existing UI's own conditional rendering: Live
Bloomberg Quote only in Bloomberg-DAPI mode; Benchmark/Comparison/
Calibration only exist in the merged benchmark-mode display; Solver
Diagnostics only when ``calibration["solver_status"]`` is not ``None``;
Errors only when the errors list is non-empty). Every existing
``None`` value renders as the literal text ``not available`` -- never a
fabricated ``0`` or other numeric replacement. Every other scalar value is
``str(value)`` verbatim -- no rounding, no reformatting, no recomputation
(deliberately more precise than the UI's ``.6f`` display formatting).

**Nested structured values (Codex P2 review of PR #127).** The known,
fixed-shape section fields (Context/Pricing/Benchmark/Comparison/
Calibration/Solver Diagnostics) are always scalar per the existing display
contract and stay on ``_fmt``'s simple one-line path. Two places can
legitimately carry a nested ``dict``/``list``/``tuple`` -- ``errors[*].detail``
(e.g. ``detail["reasons"]``, a list of strings) and an ``assumptions`` value
-- and are rendered through the small recursive ``_render_container_lines`` /
``_render_field_lines`` helpers instead of ``str(value)``: a bounded,
deterministic bullet-list renderer, not a generic serializer. ``None``
renders as ``not available`` at every nesting depth; a genuine ``0``,
``0.0``, ``False``, or ``""`` renders verbatim and is never confused with a
missing value; any other object type raises ``TypeError`` explicitly rather
than falling back to Python ``repr``.

**Greeks (Issue #133, Slice A).** JSON export needs no change --
``json.dumps(display, ...)`` already serializes the display dict's Greek
keys verbatim. Markdown export adds one unconditional ``## Greeks``
section split into two clearly headed subsections, so an exported number's
basis is never ambiguous:

- *Instrument analytics (per 100, no position sign)* -- the ``*_per_100``
  values, carrying CALL/PUT direction only;
- *Position risk (notional and BUY/SELL sign applied)* -- the
  ``position_*_total`` values, plus ``position`` / ``position_multiplier``
  and the two ``*_sign_applied`` flags.

Every label spells out the unit (per +1.00 clean price point, per +0.01
absolute volatility, per +1 calendar day), each subsection carries a
one-line note stating whether the position sign is in the number, and the
machine-readable ``greeks_units`` mapping the engine emitted is exported
alongside. Nothing here rescales, rounds, re-signs, or re-derives a Greek;
a ``FAILED`` run's Greeks are all ``None`` and render as ``not available``,
exactly like its premium fields already do.

**Trader override provenance (Issue #143).** JSON export needs no change --
``json.dumps(display, ...)`` already serializes a
``"trader_override_provenance"`` key verbatim if the display dict carries
one. Markdown export adds one conditional ``## Trader Override Provenance``
section, included only when that key is present and non-empty, listing each
override's field, case path, value, source system, basis, the reason Shiori
could not source it, and the Bloomberg acquisition event the run is anchored
to. The browser attaches this key to the display dict it sends to the export
routes; no pricing function produces it, and a run without overrides exports
exactly as before.

**Effective Forward (Issue #177).** JSON export needs no change --
``json.dumps(display, ...)`` already serializes an ``"effective_forward"``
key verbatim if the display dict carries one, including the whole nested
``shiori_derived_forward`` S490/carry/interim-coupon trace. Markdown export
adds one conditional ``## Effective Forward`` section, included only when
that key is present, naming the Forward source, the effective Forward, both
candidates, and any derivation error a Trader Forward Override carried the
run past. ``Pricing`` also gains a ``Forward source`` line, which is
``not available`` for any display dict predating that field. A run outside
the two Issue #177 Forward modes carries no ``effective_forward`` key and
exports exactly as before.

**No system clock, no quote ID, no version, no hidden metadata.** Neither
function reads the clock or generates an identifier; the only content is
what ``display`` already carries.

**Issue #6: live Bloomberg quote provenance, acquisition-time contract
(issue #6 comment 5028876767, PR #129 comment 5028878866).** JSON export
needs no change -- ``json.dumps(display, ...)`` already serializes a
``"live_bloomberg_quote"`` key verbatim if the display dict carries one
(Bloomberg-DAPI bond-quote-source mode), including its
``acquired_at``/``timestamp_basis``/``bloomberg_quote_observation_time``/
``case_as_of_timestamp``/``refreshed_scope``/``other_market_inputs`` fields
and the absence of any live ``source_as_of`` field. Markdown export adds one
conditional ``## Live Bloomberg Quote`` section, included only when that key
is present, with a one-line disclaimer stating that Bloomberg's
quote-observation time is unavailable, ``acquired_at`` is Shiori's own
acquisition time, only the bond quote was refreshed, and other case inputs
are unchanged -- no rounding, no recomputation, no omitted field, and
``None`` still renders as ``not available``. Manual (Case JSON) mode never
produces this key, so its Markdown export is unchanged.
"""

from __future__ import annotations

import json
from datetime import date, datetime

_DISCLAIMER = (
    "Internal current-run evidence for the standalone bond-option leg only. "
    "Not a saved quote, replay contract, booking record, client termsheet, "
    "or full structured-product valuation."
)

# --- Section field lists: (markdown label, display-dict key) ---------------------
# Mirrors exactly the keys the existing UI already reads (standalone_option_ui.py
# _render_pricing_result / _render_benchmark_result) -- no new field is invented.

_CONTEXT_FIELDS = (
    ("Product ID", "product_id"),
    ("Product type", "product_type"),
    ("Valuation date", "valuation_date"),
    ("Result currency", "result_currency"),
    ("Status", "status"),
    ("Method", "method"),
    ("Source system", "source_system"),
    ("Source as-of", "source_as_of"),
    ("Retrieved at", "retrieved_at"),
    ("Snapshot ID", "snapshot_id"),
    ("Engine name", "engine_name"),
    ("Engine version", "engine_version"),
)

_PRICING_FIELDS = (
    ("Model fair premium per 100", "model_fair_premium_per_100"),
    ("Total notional model fair premium", "total_notional_model_fair_premium"),
    ("Forward clean price per 100", "forward_clean_price_per_100"),
    # Issue #177: which source produced the Forward this run priced from.
    ("Forward source", "forward_source"),
    ("Black-76 PV per 100", "black76_pv_per_100"),
    ("Effective reporting-date discount factor", "effective_reporting_date_discount_factor"),
    ("Time to expiry (years)", "time_to_expiry_year_fraction"),
    ("PV scaling formula", "pv_scaling_formula"),
    ("Priced component", "priced_component"),
    ("Priced component scope", "priced_component_scope"),
)

# Issue #133 Slice A. Labels carry the unit explicitly so the exported
# number is never ambiguous, and the two bases are rendered as two
# separately-headed groups: instrument analytics (no BUY/SELL sign) and
# position risk (notional AND BUY/SELL sign). The machine-readable units
# mapping the engine emitted is exported alongside them under "Greeks units".
_GREEKS_INSTRUMENT_FIELDS = (
    ("Forward price delta per 100 (per +1.00 clean price point)", "forward_price_delta_per_100"),
    (
        "Forward price gamma per 100 (per +1.00 clean price point squared)",
        "forward_price_gamma_per_100",
    ),
    ("Vega per 100 (per +0.01 absolute volatility)", "vega_per_vol_point_per_100"),
    ("Theta per 100 (per +1 calendar day)", "theta_per_calendar_day_per_100"),
)

_GREEKS_POSITION_FIELDS = (
    (
        "Position forward price delta total (per +1.00 clean price point)",
        "position_forward_price_delta_total",
    ),
    (
        "Position forward price gamma total (per +1.00 clean price point squared)",
        "position_forward_price_gamma_total",
    ),
    (
        "Position vega total (per +0.01 absolute volatility)",
        "position_vega_per_vol_point_total",
    ),
    (
        "Position theta total (per +1 calendar day)",
        "position_theta_per_calendar_day_total",
    ),
)

_GREEKS_INSTRUMENT_NOTE = (
    "Instrument analytics per 100: CALL/PUT direction only. BUY/SELL position "
    "sign is NOT applied -- an otherwise identical BUY and SELL show the same "
    "values here."
)

_GREEKS_POSITION_NOTE = (
    "Trader position risk: per 100 x notional / 100 x position multiplier "
    "(BUY = +1, SELL = -1). An otherwise identical SELL total is exactly the "
    "negative of the BUY total."
)

_GREEKS_POSITION_CONTEXT_FIELDS = (
    ("Position", "position"),
    ("Position multiplier", "position_multiplier"),
    ("Per-100 position sign applied", "greeks_per_100_position_sign_applied"),
    ("Position total sign applied", "greeks_position_total_sign_applied"),
)

_BENCHMARK_FIELDS = (
    ("Benchmark ID", "benchmark_id"),
    ("Source type", "source_type"),
    ("Source system", "source_system"),
    ("Source as-of", "source_as_of"),
    ("Retrieved at", "retrieved_at"),
    ("Quote side", "quote_side"),
    ("Premium per 100", "premium_per_100"),
    ("Total premium", "total_premium"),
    ("Currency", "currency"),
    ("Product ID", "product_id"),
    ("Snapshot ID", "snapshot_id"),
    ("Underlying ID", "underlying_id"),
    ("Source reference", "source_reference"),
    ("Notes", "notes"),
)

_COMPARISON_FIELDS = (
    ("Status", "status"),
    ("Reason", "reason"),
    ("Comparison metric", "comparison_metric"),
    ("Active quote side", "active_quote_side"),
    ("Pass threshold", "pass_threshold"),
    ("Fail threshold", "fail_threshold"),
    ("Near-zero threshold per 100", "near_zero_threshold_per_100"),
    ("Model fair premium per 100", "model_fair_premium_per_100"),
    ("Model total premium", "model_total_premium"),
    ("Benchmark premium per 100", "benchmark_premium_per_100"),
    ("Benchmark total premium", "benchmark_total_premium"),
    ("Signed residual per 100", "signed_residual_per_100"),
    ("Absolute residual per 100", "absolute_residual_per_100"),
    ("Relative residual", "relative_residual"),
    ("Alignment note", "alignment_note"),
)

_CALIBRATION_FIELDS = (
    ("Status", "status"),
    ("Reason", "reason"),
    ("Diagnostic note", "diagnostic_note"),
    ("Resolution error type", "resolution_error_type"),
    ("Resolution error message", "resolution_error_message"),
    ("Forward clean price per 100", "forward_clean_price_per_100"),
    ("Strike clean price per 100", "strike_clean_price_per_100"),
    ("Forward dirty price per 100", "forward_dirty_price_per_100"),
    ("Strike dirty price per 100", "strike_dirty_price_per_100"),
    ("Time to expiry (years)", "time_to_expiry_year_fraction"),
    ("Effective reporting-date discount factor", "effective_reporting_date_discount_factor"),
    ("Implied PRICE_VOL", "implied_price_vol"),
    ("Model premium per 100 (solver)", "model_premium_per_100"),
    ("Premium residual per 100 (solver)", "premium_residual_per_100"),
)

_LIVE_BLOOMBERG_QUOTE_FIELDS = (
    ("Security", "security"),
    ("Verified ISIN", "verified_isin"),
    ("Source system", "source_system"),
    ("Quote side", "quote_side"),
    ("Currency", "currency"),
    ("Clean price per 100", "clean_price_per_100"),
    ("Accrued interest per 100", "accrued_interest_per_100"),
    ("Acquired at", "acquired_at"),
    ("Timestamp basis", "timestamp_basis"),
    ("Bloomberg quote observation time", "bloomberg_quote_observation_time"),
    ("Case as-of timestamp", "case_as_of_timestamp"),
    ("Refreshed scope", "refreshed_scope"),
    ("Refreshed inputs", "refreshed_inputs"),
    ("Other market inputs", "other_market_inputs"),
)

# Issue #177 / Codex P1 review of PR #178. This used to assert flatly that
# "only the bond quote was refreshed -- curve, forward, ... remain from the
# case JSON". That is true of the workflow function alone, but not of the
# Workbench refresh route wrapping it: since Issue #171 it can also re-source
# the Option Discount Curve, and since Issue #177 it re-derives the Forward
# whenever the run's source is the Shiori derived one -- now the default. So
# the fixed sentence no longer claims what was left unchanged; the run's own
# "Refreshed scope" / "Refreshed inputs" fields state exactly what was
# re-sourced, and the disclaimer points at them.
_LIVE_BLOOMBERG_QUOTE_DISCLAIMER = (
    "Bloomberg quote-observation time is not provided by this DAPI path. "
    "Acquired at is when Shiori received this quote. Refreshed scope and "
    "Refreshed inputs below state exactly which inputs this run re-sourced; "
    "every other market input remains from the case JSON. This is a "
    "current-run mixed-provenance calculation, not a historical replay."
)

# Issue #177. Exactly the keys the workbench bridge's own
# ``effective_forward`` section carries (see
# ``app/standalone_option_workbench_server.apply_effective_forward_to_case``),
# so an exported run answers, on its own: which Forward was priced, where it
# came from, what the other candidate was, and -- when the derivation failed
# while a Trader Forward Override carried the run -- why. The full
# ``shiori_derived_forward`` S490/carry/coupon trace is deliberately not
# flattened into labelled lines here: it is a nested structure of the Issue
# #173/#175 primitives' own fields, already exported verbatim by the JSON
# export, and re-labelling it in Markdown would be a second, drifting copy of
# their contracts.
_EFFECTIVE_FORWARD_FIELDS = (
    ("Forward source", "forward_source"),
    ("Effective forward clean price per 100", "effective_forward_clean_price_per_100"),
    (
        "Shiori Derived S490 forward clean price per 100",
        "shiori_derived_forward_clean_price_per_100",
    ),
    ("Trader Forward Override per 100", "trader_forward_override_per_100"),
    ("Shiori Derived forward error", "shiori_derived_forward_error"),
    ("Spot settlement date (tS)", "spot_settlement_date"),
    ("Convention profile", "convention_profile"),
)

_EFFECTIVE_FORWARD_DISCLAIMER = (
    "The effective Forward is what Black-76 actually priced from. Shiori's own "
    "S490 repo-carry derivation is the default source; a Trader Forward Override "
    "is used only when the trader explicitly entered one, and then takes "
    "precedence. Both candidates are shown so the two can always be compared. No "
    "Forward is ever substituted from spot, a previous run, zero repo or flat "
    "carry: when the derived source is in use and unavailable, no run is produced "
    "at all."
)

_TRADER_OVERRIDE_PROVENANCE_FIELDS = (
    ("Field", "field"),
    ("Case path", "path"),
    ("Value", "value"),
    ("Source system", "source_system"),
    ("Basis", "basis"),
    ("Reason it could not be sourced", "reason_not_sourced"),
    ("Run anchored to Bloomberg acquisition", "run_acquired_at"),
)

_TRADER_OVERRIDE_PROVENANCE_DISCLAIMER = (
    "Every value below was entered by the trader because Shiori has no approved "
    "source for it. Each entry records the reason it could not be sourced and the "
    "Bloomberg acquisition event this run is anchored to. These are trader "
    "overrides, never observed market data."
)

_SOLVER_DIAGNOSTICS_FIELDS = (
    ("Solver status", "solver_status"),
    ("Solver reason", "solver_reason"),
    ("Lower price vol", "lower_price_vol"),
    ("Upper price vol", "upper_price_vol"),
    ("Premium tolerance per 100", "premium_tolerance_per_100"),
    ("Price vol tolerance", "price_vol_tolerance"),
    ("Max iterations", "max_iterations"),
    ("Iterations", "iterations"),
    ("Final bracket lower price vol", "final_bracket_lower_price_vol"),
    ("Final bracket upper price vol", "final_bracket_upper_price_vol"),
    ("Solver diagnostic note", "solver_diagnostic_note"),
)


def _json_default(value: object) -> str:
    """Deterministically normalize a stray date/datetime; reject everything else.

    ``json.dumps`` only calls this for a value it cannot natively encode.
    Every date-like field already in ``display`` is an ISO string, so this
    branch is not currently reached in production output -- it exists so a
    future date/datetime value normalizes deterministically via
    ``.isoformat()`` instead of silently falling back to ``str(obj)``. Any
    other unrecognized type raises ``TypeError`` explicitly, matching
    ``json.dumps``'s own contract for an unhandled ``default`` result.
    """

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(
        f"object of type {type(value).__name__!r} is not JSON serializable by "
        "the standalone run export helper"
    )


def render_standalone_run_as_json(display: dict) -> str:
    """Return deterministic, pretty-printed UTF-8 JSON text for ``display``.

    ``display`` is used exactly as returned by
    ``price_standalone_option_case`` / ``price_standalone_option_case_with_benchmark``
    -- read-only, never mutated. Identical input produces identical text.
    Ends with exactly one trailing newline.
    """

    if not isinstance(display, dict):
        raise TypeError(f"display must be a dict, got {type(display).__name__}")

    text = json.dumps(
        display,
        indent=2,
        ensure_ascii=False,
        default=_json_default,
    )
    return text + "\n"


def _fmt(value: object) -> str:
    """Return ``value`` verbatim as text, or the literal ``not available`` for ``None``.

    Never substitutes a fabricated ``0`` or other numeric placeholder --
    ``None`` is an existing, meaningful outcome of the display contract
    (e.g. a residual left unset on a mismatch outcome), not an error to
    paper over. No rounding or reformatting of numeric values.
    """

    if value is None:
        return "not available"
    return str(value)


def _section(title: str, source: dict, fields: tuple[tuple[str, str], ...]) -> list[str]:
    lines = [f"## {title}", ""]
    for label, key in fields:
        lines.append(f"- **{label}:** {_fmt(source.get(key))}")
    lines.append("")
    return lines


_INDENT_UNIT = "    "  # 4 spaces per nesting level


def _is_scalar_or_none(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar_text(value: object) -> str:
    """Return ``value`` verbatim, or ``not available`` for ``None``.

    A genuine ``0``, ``0.0``, ``False``, or ``""`` is a real value and
    renders as itself -- only ``None`` (an existing, meaningful "unset"
    outcome) maps to the literal ``not available``.
    """

    return "not available" if value is None else str(value)


def _render_container_lines(value: object, level: int) -> list[str]:
    """Render a ``dict``/``list``/``tuple`` as deterministic nested bullets.

    ``level`` (>= 1) is the nesting depth, each level indented by one
    ``_INDENT_UNIT``. Dict keys preserve insertion order; list/tuple items
    preserve their original order. A nested ``None`` renders as
    ``not available``; a nested container recurses one level deeper; any
    other object type raises ``TypeError`` explicitly -- this function never
    falls back to Python ``repr`` for a type it does not recognize, and
    never mutates ``value``.
    """

    prefix = _INDENT_UNIT * level

    if isinstance(value, dict):
        if not value:
            return [f"{prefix}- (empty)"]
        lines: list[str] = []
        for key, item in value.items():
            if _is_scalar_or_none(item):
                lines.append(f"{prefix}- **{key}:** {_scalar_text(item)}")
            elif isinstance(item, (dict, list, tuple)):
                lines.append(f"{prefix}- **{key}:**")
                lines.extend(_render_container_lines(item, level + 1))
            else:
                raise TypeError(
                    f"object of type {type(item).__name__!r} is not renderable by "
                    "the standalone run export markdown helper"
                )
        return lines

    if isinstance(value, (list, tuple)):
        if not value:
            return [f"{prefix}- (empty)"]
        lines = []
        for item in value:
            if _is_scalar_or_none(item):
                lines.append(f"{prefix}- {_scalar_text(item)}")
            elif isinstance(item, (dict, list, tuple)):
                lines.append(f"{prefix}-")
                lines.extend(_render_container_lines(item, level + 1))
            else:
                raise TypeError(
                    f"object of type {type(item).__name__!r} is not renderable by "
                    "the standalone run export markdown helper"
                )
        return lines

    raise TypeError(
        f"object of type {type(value).__name__!r} is not renderable by the "
        "standalone run export markdown helper"
    )


def _render_field_lines(label: str, value: object) -> list[str]:
    """Render one top-level ``- **label:** value`` bullet, recursing for containers."""

    if _is_scalar_or_none(value):
        return [f"- **{label}:** {_scalar_text(value)}"]
    if isinstance(value, (dict, list, tuple)):
        return [f"- **{label}:**", *_render_container_lines(value, 1)]
    raise TypeError(
        f"object of type {type(value).__name__!r} is not renderable by the "
        "standalone run export markdown helper"
    )


def _excluded_components_section(components: object) -> list[str]:
    lines = ["## Excluded Components", ""]
    if not components:
        lines.append("- not available")
    else:
        lines.extend(f"- {component}" for component in components)
    lines.append("")
    return lines


def _assumptions_section(assumptions: object) -> list[str]:
    lines = ["## Assumptions", ""]
    if not assumptions:
        lines.append("- not available")
    else:
        for key, value in assumptions.items():
            lines.extend(_render_field_lines(key, value))
    lines.append("")
    return lines


def _errors_section(errors: object) -> list[str]:
    lines = ["## Errors", ""]
    for error in errors:
        lines.append(f"- **{error['code']}**: {error['message']}")
        detail = error.get("detail")
        if detail:
            lines.extend(_render_container_lines(detail, 1))
    lines.append("")
    return lines


def render_standalone_run_as_markdown(display: dict) -> str:
    """Return deterministic UTF-8 Markdown text for ``display``.

    ``display`` is used exactly as returned by
    ``price_standalone_option_case`` / ``price_standalone_option_case_with_benchmark``
    -- read-only, never mutated. Benchmark / Comparison / Calibration /
    Solver Diagnostics / Errors sections are included only when the
    corresponding data is present, mirroring the existing UI's own
    conditional rendering. Every ``None`` field renders as ``not available``;
    every other value is printed verbatim (no rounding or recomputation).
    """

    if not isinstance(display, dict):
        raise TypeError(f"display must be a dict, got {type(display).__name__}")

    lines: list[str] = [
        "# Shiori Standalone Bond Option — Current Run Export",
        "",
        _DISCLAIMER,
        "",
    ]

    lines.extend(_section("Context", display, _CONTEXT_FIELDS))
    lines.extend(_section("Pricing", display, _PRICING_FIELDS))

    lines.append("## Greeks")
    lines.append("")
    lines.append("### Instrument analytics (per 100, no position sign)")
    lines.append("")
    lines.append(f"> {_GREEKS_INSTRUMENT_NOTE}")
    lines.append("")
    for label, key in _GREEKS_INSTRUMENT_FIELDS:
        lines.append(f"- **{label}:** {_fmt(display.get(key))}")
    lines.append("")
    lines.append("### Position risk (notional and BUY/SELL sign applied)")
    lines.append("")
    lines.append(f"> {_GREEKS_POSITION_NOTE}")
    lines.append("")
    for label, key in _GREEKS_POSITION_FIELDS + _GREEKS_POSITION_CONTEXT_FIELDS:
        lines.append(f"- **{label}:** {_fmt(display.get(key))}")
    lines.extend(_render_field_lines("Greeks units", display.get("greeks_units")))
    lines.append("")

    effective_forward = display.get("effective_forward")
    if effective_forward is not None:
        lines.append("## Effective Forward")
        lines.append("")
        lines.append(f"> {_EFFECTIVE_FORWARD_DISCLAIMER}")
        lines.append("")
        for label, key in _EFFECTIVE_FORWARD_FIELDS:
            lines.append(f"- **{label}:** {_fmt(effective_forward.get(key))}")
        lines.append("")

    if "live_bloomberg_quote" in display:
        lines.append("## Live Bloomberg Quote")
        lines.append("")
        lines.append(f"> {_LIVE_BLOOMBERG_QUOTE_DISCLAIMER}")
        lines.append("")
        for label, key in _LIVE_BLOOMBERG_QUOTE_FIELDS:
            lines.append(f"- **{label}:** {_fmt(display['live_bloomberg_quote'].get(key))}")
        lines.append("")

    overrides = display.get("trader_override_provenance")
    if overrides:
        lines.append("## Trader Override Provenance")
        lines.append("")
        lines.append(f"> {_TRADER_OVERRIDE_PROVENANCE_DISCLAIMER}")
        lines.append("")
        for record in overrides:
            lines.append(f"### {_fmt(record.get('field'))}")
            lines.append("")
            for label, key in _TRADER_OVERRIDE_PROVENANCE_FIELDS:
                lines.append(f"- **{label}:** {_fmt(record.get(key))}")
            lines.append("")

    if "benchmark" in display:
        lines.extend(_section("Benchmark", display["benchmark"], _BENCHMARK_FIELDS))
    if "comparison" in display:
        lines.extend(_section("Comparison", display["comparison"], _COMPARISON_FIELDS))
    if "calibration" in display:
        lines.extend(_section("Calibration", display["calibration"], _CALIBRATION_FIELDS))

    lines.extend(_assumptions_section(display.get("assumptions")))
    lines.extend(_excluded_components_section(display.get("excluded_components")))

    errors = display.get("errors")
    if errors:
        lines.extend(_errors_section(errors))

    calibration = display.get("calibration")
    if calibration is not None and calibration.get("solver_status") is not None:
        lines.extend(_section("Solver Diagnostics", calibration, _SOLVER_DIAGNOSTICS_FIELDS))

    return "\n".join(lines).rstrip("\n") + "\n"
