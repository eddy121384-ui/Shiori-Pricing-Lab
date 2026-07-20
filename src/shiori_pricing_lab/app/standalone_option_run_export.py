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
Context / Pricing / Benchmark / Comparison / Calibration / Assumptions /
Excluded Components / Errors / Solver Diagnostics -- the last four are
included only when the corresponding data is actually present in
``display`` (mirrors the existing UI's own conditional rendering:
Benchmark/Comparison/Calibration only exist in the merged benchmark-mode
display; Solver Diagnostics only when ``calibration["solver_status"]`` is
not ``None``; Errors only when the errors list is non-empty). Every existing
``None`` value renders as the literal text ``not available`` -- never a
fabricated ``0`` or other numeric replacement. Every other value is
``str(value)`` verbatim -- no rounding, no reformatting, no recomputation
(deliberately more precise than the UI's ``.6f`` display formatting).

**No system clock, no quote ID, no version, no hidden metadata.** Neither
function reads the clock or generates an identifier; the only content is
what ``display`` already carries.
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
    ("Black-76 PV per 100", "black76_pv_per_100"),
    ("Effective reporting-date discount factor", "effective_reporting_date_discount_factor"),
    ("Time to expiry (years)", "time_to_expiry_year_fraction"),
    ("PV scaling formula", "pv_scaling_formula"),
    ("Priced component", "priced_component"),
    ("Priced component scope", "priced_component_scope"),
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
            if isinstance(value, (list, tuple)):
                rendered = ", ".join(str(item) for item in value)
            else:
                rendered = _fmt(value)
            lines.append(f"- **{key}:** {rendered}")
    lines.append("")
    return lines


def _errors_section(errors: object) -> list[str]:
    lines = ["## Errors", ""]
    for error in errors:
        lines.append(f"- **{error['code']}**: {error['message']}")
        detail = error.get("detail")
        if detail:
            for detail_key, detail_value in detail.items():
                lines.append(f"    - {detail_key}: {_fmt(detail_value)}")
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
