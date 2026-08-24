"""Bloomberg VCUB OTM/SABR sign-recognition diagnostic (live acceptance #3, PR #186).

Bounded, read-only workstation diagnostic CLI -- **not** part of production
parsing, and never imported by it. It changes no production behaviour and
adds no new heuristic; it only prints evidence that already exists inside
one screenshot's own OCR token stream.

**The one question it answers.** At a named ``Term x Tenor / strike``
coordinate, on one or more of the operator's own screenshots, is there ANY
OCR token evidence of a minus sign near the numeric token -- or has OCR
dropped it before this repository's code ever sees it? Live acceptance #2
found and fixed the case where a separate minus-glyph token exists but was
being paired too late (or not at all); live acceptance #3 needs to know
whether the remaining ``-50bps`` failures are the *same* class of defect
(a minus token exists, just wasn't picked up) or a *different* one (OCR
never produced a minus token at all, which this repository's rules forbid
guessing around -- see ``_reconstructed_minus_tokens`` in
``bloomberg_vcub_otm_template.py``).

**How it answers it.** By re-running the exact same private geometry the
production parser already uses for one image -- the strike-header bands,
the row bands, and ``_reconstructed_minus_tokens``'s own left-adjacency
pairing -- and printing, for each requested coordinate on each image:
every raw OCR token whose box sits at or near that cell (its text, pixel
box, and confidence), whether a separate minus-glyph token was found and
paired to a numeric token there, what ``parse_cell_number`` reads from
that pairing, and -- from the real, completely unmodified
``parse_vcub_otm_tokens`` -- the value production itself would store at
that coordinate. Reusing the parser's own private functions rather than
re-deriving the geometry here means this diagnostic cannot silently
disagree with what the parser actually does.

**Nothing here changes production.** The merge, ``OVERLAP_VALUE_CONFLICT``,
and the single-image parser are untouched by this file. It never infers a
sign from another screenshot, a column, a neighbouring value, or an
expected skew shape -- it only prints what one image's own tokens say.

**No screenshot or live Bloomberg value is read, stored, or committed by
this repository.** Run it locally against your own local image files.
Nothing it prints is written to disk unless you pass ``--crop-dir``, and
that output is yours to inspect and delete -- never add it to git.

One command, run at the operator's own workstation::

    python tools/bloomberg_vcub_otm_sign_diagnostic.py \\
        --image shot_a.png --image shot_b.png \\
        --coordinate "10Yr x 15Yr / -50bps" \\
        --coordinate "15Yr x 1Yr / -50bps" \\
        --coordinate "3Mo x 10Yr / -50bps"

Add a negative control -- a coordinate that read correctly -- for
comparison, the same way::

    ... --coordinate "1Mo x 1Yr / -50bps"

Optionally save a local crop of each requested cell so it can be checked
by eye, off by default::

    ... --crop-dir C:\\Users\\eddy\\Desktop\\otm-sign-check
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from shiori_pricing_lab.data.bloomberg_vcub_capture import VCUBTextToken
from shiori_pricing_lab.data.bloomberg_vcub_ocr import (
    VCUBOCRUnavailableError,
    build_capture_provenance,
    read_tokens_from_image_bytes,
)
from shiori_pricing_lab.data.bloomberg_vcub_otm_capture import PARSER_NAME, PARSER_VERSION
from shiori_pricing_lab.data.bloomberg_vcub_otm_template import (
    _find_anchor_line,
    _Issues,  # noqa: PLC2701 -- deliberate: see module docstring
    _looks_like_a_lone_minus,
    _reconstructed_minus_tokens,
    _resolve_row_labels,
    _resolve_strike_headers,
    _row_centre,
    _run_left,
    _run_right,
    parse_row_label,
    parse_strike_header,
    parse_vcub_otm_tokens,
)
from shiori_pricing_lab.data.bloomberg_vcub_screen_reader import (
    assign_band,
    band_edges,
    group_into_lines,
    parse_cell_number,
)


class _ImageGeometry:
    """The same band geometry ``parse_vcub_otm_tokens`` computes for one
    image, gathered here purely for inspection -- nothing here feeds back
    into the parser."""

    def __init__(
        self,
        *,
        strikes,
        column_edges: list[float],
        column_boundaries: list[float],
        column_outer: float,
        rows_found,
        row_centres: list[float],
        row_boundaries: list[float],
        row_outer: float,
        minus_by_number_id: dict[int, VCUBTextToken],
    ) -> None:
        self.strikes = strikes
        self.column_edges = column_edges
        self.column_boundaries = column_boundaries
        self.column_outer = column_outer
        self.rows_found = rows_found
        self.row_centres = row_centres
        self.row_boundaries = row_boundaries
        self.row_outer = row_outer
        self.minus_by_number_id = minus_by_number_id


def _resolve_geometry(tokens: Sequence[VCUBTextToken]) -> _ImageGeometry | list[str]:
    """The matrix geometry for one image's tokens, or the blocking messages
    that stopped it being established (mirrors ``parse_vcub_otm_tokens``'s
    own early-exit points, calling its own private helpers directly)."""

    issues = _Issues()
    lines = group_into_lines(tokens)

    anchor_found = _find_anchor_line(lines, issues)
    if anchor_found is None:
        return [issue.message for issue in issues.blocking]
    anchor_line, header_line_runs = anchor_found

    headers_found = _resolve_strike_headers(header_line_runs, issues)
    if headers_found is None:
        return [issue.message for issue in issues.blocking]
    headers, strikes, anchor_tokens = headers_found

    column_edges = [_run_right(run) for run in headers]
    column_boundaries, column_outer = band_edges(
        column_edges, max(_run_right(run) - _run_left(run) for run in headers)
    )
    first_column_left_edge = column_edges[0] - column_outer

    header_ids = {id(token) for run in headers for token in run} | {
        id(token) for token in anchor_tokens
    }
    minus_by_number_id = _reconstructed_minus_tokens(tokens, header_ids)
    minus_token_ids = {id(minus) for minus in minus_by_number_id.values()}

    rows_found = _resolve_row_labels(
        lines, anchor_line, anchor_tokens, first_column_left_edge, minus_token_ids, issues
    )
    if rows_found is None:
        return [issue.message for issue in issues.blocking]

    row_centres = [_row_centre(row_tokens) for row_tokens, _term, _tenor in rows_found]
    row_heights = [
        max(token.bottom for token in row_tokens) - min(token.top for token in row_tokens)
        for row_tokens, _term, _tenor in rows_found
    ]
    row_boundaries, row_outer = band_edges(row_centres, max(row_heights))

    return _ImageGeometry(
        strikes=strikes,
        column_edges=column_edges,
        column_boundaries=column_boundaries,
        column_outer=column_outer,
        rows_found=rows_found,
        row_centres=row_centres,
        row_boundaries=row_boundaries,
        row_outer=row_outer,
        minus_by_number_id=minus_by_number_id,
    )


def _band_span(index: int, centres: Sequence[float], boundaries: Sequence[float], outer: float):
    lower = boundaries[index - 1] if index > 0 else centres[index] - outer
    upper = boundaries[index] if index < len(boundaries) else centres[index] + outer
    return lower, upper


def _locate_coordinate(
    geometry: _ImageGeometry, term: str, tenor: str, strike_text: str
) -> tuple[int, int] | str:
    """``(row_index, column_index)`` for this coordinate on this image, or a
    reason it could not be found there."""

    target_strike = parse_strike_header(strike_text)
    if target_strike is None:
        return f"{strike_text!r} does not read as a strike header this parser recognises"
    column_index = next(
        (
            index
            for index, strike in enumerate(geometry.strikes)
            if strike.offset_bp == target_strike.offset_bp
        ),
        None,
    )
    if column_index is None:
        available = ", ".join(strike.label for strike in geometry.strikes)
        return f"no {strike_text!r} strike column on this image (has: {available})"

    row_index = next(
        (
            index
            for index, (_tokens, row_term, row_tenor) in enumerate(geometry.rows_found)
            if (row_term, row_tenor) == (term, tenor)
        ),
        None,
    )
    if row_index is None:
        return f"no {term} x {tenor!r} row on this image ({len(geometry.rows_found)} rows found)"
    return row_index, column_index


def _describe_token(token: VCUBTextToken, geometry: _ImageGeometry) -> str:
    row_index, row_ambiguous = assign_band(
        token.y_center, geometry.row_centres, geometry.row_boundaries, geometry.row_outer
    )
    column_index, column_ambiguous = assign_band(
        token.right, geometry.column_edges, geometry.column_boundaries, geometry.column_outer
    )
    placement = "outside every row/column band"
    if row_index is not None and column_index is not None:
        row_term, row_tenor = geometry.rows_found[row_index][1], geometry.rows_found[row_index][2]
        strike_label = geometry.strikes[column_index].label
        flags = []
        if row_ambiguous:
            flags.append("row-boundary-ambiguous")
        if column_ambiguous:
            flags.append("column-boundary-ambiguous")
        placement = f"-> {row_term} x {row_tenor} / {strike_label}"
        if flags:
            placement += f" ({', '.join(flags)})"
    elif row_index is not None:
        placement = "on a row band but outside every strike column"
    elif column_index is not None:
        placement = "on a strike column but outside every row band"

    is_minus_candidate = _looks_like_a_lone_minus(token)
    paired_as_minus_for = [
        number_id
        for number_id, minus in geometry.minus_by_number_id.items()
        if minus is token
    ]
    has_paired_minus = id(token) in geometry.minus_by_number_id
    value, failure = parse_cell_number(token.text)

    flags = []
    if is_minus_candidate:
        paired_note = " (paired)" if paired_as_minus_for else " (unpaired)"
        flags.append("MINUS-GLYPH-CANDIDATE" + paired_note)
    if has_paired_minus:
        flags.append("HAS-PAIRED-MINUS -> reads as negative in production")
    flag_text = f"  [{', '.join(flags)}]" if flags else ""

    own_number = (
        f"parse_cell_number={value!r}"
        if failure is None
        else f"parse_cell_number FAILED ({failure})"
    )
    confidence = "n/a" if token.confidence is None else f"{token.confidence:.3f}"
    return (
        f"    text={token.text!r:>10}  box=(left={token.left:.1f} top={token.top:.1f} "
        f"right={token.right:.1f} bottom={token.bottom:.1f})  confidence={confidence}  "
        f"{own_number}  {placement}{flag_text}"
    )


def _describe_coordinate(
    image_path: str,
    tokens: Sequence[VCUBTextToken],
    geometry: _ImageGeometry | list[str],
    term: str,
    tenor: str,
    strike_text: str,
    authoritative_value,
    margin: float | None,
) -> tuple[str, tuple[float, float, float, float] | None]:
    header = f"  {term} x {tenor} / {strike_text}"
    if isinstance(geometry, list):
        reasons = "; ".join(geometry)
        return f"{header}: matrix geometry not established on this image ({reasons})", None

    located = _locate_coordinate(geometry, term, tenor, strike_text)
    if isinstance(located, str):
        return f"{header}: {located}", None
    row_index, column_index = located

    row_top, row_bottom = _band_span(
        row_index, geometry.row_centres, geometry.row_boundaries, geometry.row_outer
    )
    col_left, col_right = _band_span(
        column_index, geometry.column_edges, geometry.column_boundaries, geometry.column_outer
    )
    pad = margin if margin is not None else 1.5 * max(geometry.row_outer, geometry.column_outer)
    box = (col_left - pad, row_top - pad, col_right + pad, row_bottom + pad)

    nearby = [
        token
        for token in tokens
        if not (
            token.right < box[0]
            or token.left > box[2]
            or token.bottom < box[1]
            or token.top > box[3]
        )
    ]
    nearby.sort(key=lambda token: token.left)

    lines = [f"{header}  (production value = {authoritative_value!r})"]
    if not nearby:
        lines.append("    no OCR tokens at all within the searched box -- OCR saw nothing here")
    for token in nearby:
        lines.append(_describe_token(token, geometry))
    return "\n".join(lines), box


def _parse_coordinate_arg(raw: str) -> tuple[str, str, str]:
    if "/" not in raw:
        raise argparse.ArgumentTypeError(f"expected 'TERM x TENOR / STRIKE', got {raw!r} (no '/')")
    row_part, strike_part = raw.rsplit("/", 1)
    row_part, strike_part = row_part.strip(), strike_part.strip()
    parsed_row = parse_row_label(row_part)
    if parsed_row is None:
        raise argparse.ArgumentTypeError(f"{row_part!r} does not read as a Term x Tenor label")
    return parsed_row[0], parsed_row[1], strike_part


def _save_crop(
    image_path: str, crop_dir: Path, label: str, box: tuple[float, float, float, float]
) -> None:
    from PIL import Image

    crop_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        left, top, right, bottom = box
        cropped = image.crop((max(0, left), max(0, top), right, bottom))
        out_path = crop_dir / f"{Path(image_path).stem}__{label}.png"
        cropped.save(out_path)
        print(f"    saved crop: {out_path}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded read-only diagnostic for the VCUB OTM/SABR sign-recognition defect "
            "(live acceptance #3, PR #186). Prints evidence; changes nothing."
        )
    )
    parser.add_argument(
        "--image", action="append", dest="images", required=True,
        help="Path to one local screenshot; repeatable",
    )
    parser.add_argument(
        "--coordinate", action="append", dest="coordinates", required=True,
        type=_parse_coordinate_arg,
        help="'TERM x TENOR / STRIKE', e.g. '10Yr x 15Yr / -50bps'; repeatable",
    )
    parser.add_argument(
        "--crop-dir", default=None,
        help="Save a local PNG crop of each requested cell here. Off by default. "
        "Never commit anything written here.",
    )
    parser.add_argument(
        "--margin", type=float, default=None,
        help="Pixels of extra search margin around each cell's own band (default: "
        "1.5x the wider of the row/column half-pitch)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    crop_dir = Path(args.crop_dir) if args.crop_dir else None

    for image_path in args.images:
        print(f"=== {image_path} ===")
        raw = Path(image_path).read_bytes()
        try:
            tokens, notes = read_tokens_from_image_bytes(raw)
        except VCUBOCRUnavailableError as exc:
            print(f"  {exc}")
            return 1
        for note in notes:
            print(f"  [dropped by OCR confidence floor] {note}")

        provenance = build_capture_provenance(
            source_reference=image_path,
            raw_image=raw,
            parser_name=PARSER_NAME,
            parser_version=PARSER_VERSION,
        )
        parsed = parse_vcub_otm_tokens(tokens, provenance=provenance)
        geometry = _resolve_geometry(tokens)

        for term, tenor, strike_text in args.coordinates:
            authoritative = None
            if parsed.table is not None:
                try:
                    authoritative = parsed.table.value_at(term, tenor, strike_text)
                except KeyError as exc:
                    authoritative = f"<{exc}>"
            else:
                authoritative = "<no table: this image's own topology did not resolve>"
            text, box = _describe_coordinate(
                image_path, tokens, geometry, term, tenor, strike_text, authoritative, args.margin
            )
            print(text)
            if crop_dir is not None and box is not None:
                label = f"{term}_x_{tenor}__{strike_text}".replace(" ", "").replace("/", "-")
                _save_crop(image_path, crop_dir, label, box)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
