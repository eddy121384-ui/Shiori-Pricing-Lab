"""Template-driven reconstruction of a Bloomberg VCUB **ATM Swaptions**
``Expiry x Swap Tenor`` matrix from detected text tokens (Issue #181).

**Why this is not a coordinate table.** The parser never carries a single
absolute pixel constant. It locates the ``Expiry`` corner anchor, reads the
tenor headers that sit on that anchor's own text line, reads the expiry
labels that sit in that anchor's own column, and derives every row/column
band from *those detections*. Each threshold below is a fraction of the
grid's own measured pitch, so re-cropping the screenshot, moving the
terminal window, or changing DPI/scale translates and scales every token
together and leaves the derived geometry -- and therefore every cell
mapping -- unchanged.

**What this parser optimises for.** Not OCR recall. The failure that
matters is reading a plausible number and putting it in the wrong
expiry/tenor intersection, because a wrong-but-plausible vol survives
visual review far more easily than a hole does. So every value token is
placed by its *own* geometry and never by "the n-th number on this row":
a missing interior cell therefore cannot shift its neighbours sideways, and
a partial row cannot shift anything below it. On top of that, four
independent structural guards fail the capture closed rather than guess:

1. a value whose centre sits near a row/column boundary is *ambiguous*, not
   rounded to the nearer side;
2. a value outside every resolved band is reported, never clamped;
3. row and column pitch must stay roughly uniform, which is what catches a
   header or row label the reader dropped entirely (the dropped-label case
   is the one way a value could otherwise land one row/column out);
4. header and label sequences must be strictly increasing in tenor, which
   catches a mis-read label that would otherwise reorder the axis.

**Where the shared half lives.** The rules above that are not about the ATM
tab in particular -- text lines, band geometry, numeric tokens, and the
header widgets Bloomberg draws the same way on every screen -- moved
unchanged into :mod:`shiori_pricing_lab.data.bloomberg_vcub_screen_reader`
when Issue #185 added the OTM Swaptions / SABR template beside this one.
What stays here is what only this tab knows: its anchor, its two axes, and
the metadata it can read off its own header.

Nothing in this module imports :mod:`shiori_pricing_lab.pricing`, and
nothing here interpolates, converts, or re-bases a vol.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    METADATA_FIELDS,
    VCUBATMCapture,
    VCUBATMGrid,
    VCUBCaptureIssue,
    VCUBCaptureProvenance,
    VCUBSourceMetadata,
    VCUBTextToken,
)
from shiori_pricing_lab.data.bloomberg_vcub_screen_reader import (
    DATE_RE,
    KNOWN_SOURCE_TEXTS,
    PITCH_IRREGULARITY_MULTIPLE,
    SIDE_TEXTS,
    TextLine,
    assign_band,
    band_edges,
    field_segments,
    group_into_lines,
    is_tenor_label,
    join_by_geometry,
    labelled_value,
    lines_below,
    normalise_text,
    parse_cell_number,
    pitch_irregularity_message,
    spans_are_orderable,
    tenor_label_nominal_days,
    unique_curve_config,
    unique_line_containing,
    unique_match,
    unique_member,
    widget_values,
)
from shiori_pricing_lab.products.enums import Currency

#: The tab whose layout this template knows. The prototype recognises this
#: one VCUB screen and nothing else -- it is not a generic Bloomberg reader.
ATM_SWAPTIONS_TAB = "ATM Swaptions"

#: The corner cell of the VCUB ATM matrix: the tenor headers share its text
#: line, the expiry labels share its column.
EXPIRY_ANCHOR = "Expiry"

_VOL_TYPE_RE = re.compile(
    r"\b(Normal|Black|Lognormal|Shifted Lognormal|SABR)\s+Vol(\s*\([^)]*\))?", re.IGNORECASE
)
# Closed vocabularies are matched against alphanumeric runs rather than
# whitespace-separated words: the live screen glues a dropdown caret onto
# the value it belongs to, so "USD" and "Mid" never appeared as bare words
# and both fields read Unresolved on a screen that plainly showed them.
_ALPHANUMERIC_RUN_RE = re.compile(r"[0-9A-Za-z]+")


class _Issues:
    """Collects blocking errors and warnings without letting them mix up."""

    def __init__(self) -> None:
        self.blocking: list[VCUBCaptureIssue] = []
        self.warnings: list[VCUBCaptureIssue] = []

    def block(self, code: str, message: str, *, expiry=None, tenor=None) -> None:
        self.blocking.append(VCUBCaptureIssue(code, message, expiry=expiry, tenor=tenor))

    def warn(self, code: str, message: str, *, expiry=None, tenor=None) -> None:
        self.warnings.append(VCUBCaptureIssue(code, message, expiry=expiry, tenor=tenor))


def _check_monotonic_labels(
    labels: Sequence[str], issues: _Issues, *, code: str, axis: str
) -> None:
    days = [tenor_label_nominal_days(label) for label in labels]
    if any(value is None for value in days):
        return
    for index in range(len(days) - 1):
        if days[index + 1] <= days[index]:
            issues.block(
                code,
                f"the {axis} do not increase in tenor across the axis "
                f"({labels[index]!r} then {labels[index + 1]!r}), so the axis order cannot be "
                "trusted",
            )
            return


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


def _resolve_metadata(lines: Sequence[TextLine], tab_resolved: bool) -> VCUBSourceMetadata:
    """Read the screen's header context, marking anything uncertain unresolved.

    Every rule works on each line's *joined* text rather than on individual
    tokens, because a reader is free to split ``Normal Vol (OIS)`` into four
    tokens or return it as one and neither grouping should change the
    answer. Every rule also demands a *unique* match: two candidate dates,
    or two candidate currencies, means the parser cannot tell which one
    describes this grid, so the field is left unresolved rather than
    guessed. Issue #181 explicitly allows metadata to fail without failing
    the capture -- what it forbids is inventing a value.
    """

    joins = [join_by_geometry(line.tokens) for line in lines]
    segments = [
        (segment, is_menu, certain)
        for text, certain in joins
        for segment, is_menu in field_segments(text)
    ]
    # A rule that *stores what it read* may only read a line whose spacing the
    # boxes settled -- whether it copies a labelled value or the text a pattern
    # matched. A pattern is no protection on its own: `_VOL_TYPE_RE` tolerates
    # the whitespace before its parenthesis, so an illegible gap there stored
    # `Normal Vol(OIS)` where the same screen with one box stored
    # `Normal Vol (OIS)` (Codex review, PR #182).
    #
    # What is left reads illegible lines safely because it stores nothing from
    # them: the closed vocabularies match whole alphanumeric runs, and a gap
    # closed between two of them yields a run that is in no vocabulary, so the
    # field goes unresolved rather than wrong.
    legible_line_texts = [text for text, certain in joins if certain]
    legible_value_segments = [
        segment for segment, is_menu, certain in segments if not is_menu and certain
    ]
    # The closed vocabularies store a member of their own set rather than
    # screen text, but an illegible gap can still *manufacture* one: a Source
    # displayed as `B VOL` returned as two boxes joins closed to `BVOL`, which
    # is a real contributor code the screen never showed (Codex review,
    # PR #182). So runs are collected from fragments that no illegible gap
    # runs through -- such a run is simply never formed.
    #
    # Per fragment rather than per line on purpose: one unreadable gap
    # somewhere on a header line must not cost the currency, side and source
    # that are perfectly legible beside it.
    screen_widget_values = [
        value for line in lines for value in widget_values(line.tokens)
    ]

    resolved: dict[str, str | None] = dict.fromkeys(METADATA_FIELDS, None)

    resolved["currency"] = unique_member(screen_widget_values, set(Currency))
    # Only a *value* segment can carry the curve name: "Analyze Cube" is a
    # menu action, and counting it made the live screen's two "Cube"
    # occurrences ambiguous and left this field unresolved.
    resolved["curve_config"] = unique_curve_config(lines)
    side = unique_member(
        [value.upper() for value in screen_widget_values], set(SIDE_TEXTS)
    )
    resolved["side"] = None if side is None else SIDE_TEXTS[side]
    resolved["quote_date"] = unique_match(legible_line_texts, DATE_RE)
    if tab_resolved:
        resolved["tab"] = ATM_SWAPTIONS_TAB
    resolved["vol_type"] = labelled_value(legible_value_segments, "Type") or unique_match(
        legible_value_segments, _VOL_TYPE_RE
    )
    resolved["source"] = labelled_value(legible_value_segments, "Source") or unique_member(
        [value.upper() for value in screen_widget_values], KNOWN_SOURCE_TEXTS
    )

    unresolved = tuple(name for name in METADATA_FIELDS if resolved[name] is None)
    return VCUBSourceMetadata(**resolved, unresolved_fields=unresolved)





# --------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------


def _find_anchor_line(
    lines: Sequence[TextLine], issues: _Issues
) -> tuple[TextLine, VCUBTextToken] | None:
    anchors = [
        (line, token)
        for line in lines
        for token in line.tokens
        if normalise_text(token.text).casefold() == EXPIRY_ANCHOR.casefold()
    ]
    if not anchors:
        issues.block(
            "EXPIRY_ANCHOR_UNRESOLVED",
            f"the {EXPIRY_ANCHOR!r} corner anchor was not found, so the matrix has no origin "
            "and no row or column can be placed",
        )
        return None
    if len(anchors) > 1:
        issues.block(
            "EXPIRY_ANCHOR_AMBIGUOUS",
            f"{len(anchors)} {EXPIRY_ANCHOR!r} anchors were found; the parser cannot tell which "
            "one is the matrix corner, so no grid is reconstructed",
        )
        return None
    return anchors[0]


def _resolve_tenor_headers(
    anchor_line: TextLine, anchor: VCUBTextToken, issues: _Issues
) -> list[VCUBTextToken] | None:
    right_of_anchor = [token for token in anchor_line.tokens if token.left >= anchor.right]
    headers = [token for token in right_of_anchor if is_tenor_label(token.text)]
    strays = [token for token in right_of_anchor if not is_tenor_label(token.text)]
    for stray in strays:
        issues.block(
            "TENOR_HEADER_UNEXPECTED_TOKEN",
            f"{normalise_text(stray.text)!r} sits on the tenor header line but does not read as a "
            "swap tenor, so the column layout cannot be trusted",
        )
    if not headers:
        issues.block(
            "TENOR_HEADERS_UNRESOLVED",
            f"no swap-tenor headers were found to the right of {EXPIRY_ANCHOR!r}",
        )
        return None

    headers.sort(key=lambda token: token.x_center)
    labels = [normalise_text(token.text) for token in headers]
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        issues.block(
            "DUPLICATE_TENOR_LABEL",
            f"the tenor header row repeats {', '.join(duplicates)}; a value could belong to "
            "either column, so no cell is placed",
        )
        return None
    for index in range(len(headers) - 1):
        left, right = headers[index], headers[index + 1]
        if not spans_are_orderable(left.left, left.right, right.left, right.right):
            issues.block(
                "TENOR_HEADERS_NOT_ORDERABLE",
                f"tenor headers {labels[index]!r} and {labels[index + 1]!r} overlap "
                "horizontally by enough to read as one header, so their left-to-right order "
                "is not unambiguous",
            )
            return None
    _check_monotonic_labels(
        labels, issues, code="TENOR_HEADERS_NOT_MONOTONIC", axis="tenor headers"
    )
    pitch_message = pitch_irregularity_message(
        [token.x_center for token in headers], axis="columns"
    )
    if pitch_message is not None:
        issues.block("COLUMN_PITCH_IRREGULAR", pitch_message)
    return headers



def _resolve_expiry_labels(
    lines: Sequence[TextLine],
    anchor_line: TextLine,
    anchor: VCUBTextToken,
    first_column_left_edge: float,
    issues: _Issues,
) -> list[VCUBTextToken] | None:
    """Read the expiry labels sitting in the anchor's own column, below it.

    The column is bounded on the right by the first tenor column's own left
    edge, so nothing from inside the matrix can be mistaken for a row label,
    and on the left by the anchor's own width, so unrelated chrome further
    left is ignored.
    """

    left_bound = anchor.left - anchor.width
    candidates = [
        token
        for line in lines_below(lines, anchor_line)
        for token in line.tokens
        if left_bound <= token.x_center < first_column_left_edge
    ]
    labels_tokens = [token for token in candidates if is_tenor_label(token.text)]
    for stray in candidates:
        if stray in labels_tokens:
            continue
        issues.warn(
            "UNRECOGNISED_ROW_LABEL_TOKEN",
            f"{normalise_text(stray.text)!r} sits in the {EXPIRY_ANCHOR} column but does not read "
            "as an expiry bucket; it was not treated as a row",
        )
    if not labels_tokens:
        issues.block(
            "EXPIRY_LABELS_UNRESOLVED",
            f"no expiry labels were found below the {EXPIRY_ANCHOR!r} anchor",
        )
        return None

    labels_tokens.sort(key=lambda token: token.y_center)
    labels = [normalise_text(token.text) for token in labels_tokens]
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        issues.block(
            "DUPLICATE_EXPIRY_LABEL",
            f"the expiry column repeats {', '.join(duplicates)}; a value could belong to either "
            "row, so no cell is placed",
        )
        return None
    for index in range(len(labels_tokens) - 1):
        upper, lower = labels_tokens[index], labels_tokens[index + 1]
        if not spans_are_orderable(upper.top, upper.bottom, lower.top, lower.bottom):
            issues.block(
                "EXPIRY_ROWS_NOT_ORDERABLE",
                f"expiry labels {labels[index]!r} and {labels[index + 1]!r} overlap vertically "
                "by enough to read as one row, so their top-to-bottom order is not unambiguous",
            )
            return None
    _check_monotonic_labels(
        labels, issues, code="EXPIRY_LABELS_NOT_MONOTONIC", axis="expiry labels"
    )
    pitch_message = pitch_irregularity_message(
        [token.y_center for token in labels_tokens], axis="rows"
    )
    if pitch_message is not None:
        issues.block("ROW_PITCH_IRREGULAR", pitch_message)
    return labels_tokens


def parse_vcub_atm_tokens(
    tokens: Sequence[VCUBTextToken], *, provenance: VCUBCaptureProvenance
) -> VCUBATMCapture:
    """Reconstruct the ATM Swaptions matrix from ``tokens``.

    Returns a ``PENDING_REVIEW`` capture in every case -- a parse never
    accepts anything. When the topology could not be established the
    capture's ``grid`` is ``None``; when it could, the grid is present with
    unresolved intersections left as ``None`` and every reason to distrust
    the read listed in ``blocking_errors``.
    """

    issues = _Issues()
    lines = group_into_lines(tokens)

    tab_matches = unique_line_containing(
        [line.joined_text() for line in lines], ATM_SWAPTIONS_TAB.casefold()
    )
    if tab_matches is None:
        issues.block(
            "ATM_SWAPTIONS_TAB_UNRESOLVED",
            f"the {ATM_SWAPTIONS_TAB!r} tab anchor was not found exactly once, so this image was "
            "not recognised as the VCUB ATM Swaptions layout",
        )

    metadata = _resolve_metadata(lines, tab_resolved=tab_matches is not None)

    anchor_found = _find_anchor_line(lines, issues)
    if anchor_found is None:
        return _capture(provenance, metadata, None, issues)
    anchor_line, anchor = anchor_found

    headers = _resolve_tenor_headers(anchor_line, anchor, issues)
    if headers is None:
        return _capture(provenance, metadata, None, issues)

    column_centres = [token.x_center for token in headers]
    column_boundaries, column_outer = band_edges(
        column_centres, max(token.width for token in headers)
    )
    first_column_left_edge = column_centres[0] - column_outer

    label_tokens = _resolve_expiry_labels(
        lines, anchor_line, anchor, first_column_left_edge, issues
    )
    if label_tokens is None:
        return _capture(provenance, metadata, None, issues)

    row_centres = [token.y_center for token in label_tokens]
    row_boundaries, row_outer = band_edges(
        row_centres, max(token.height for token in label_tokens)
    )
    # Where a row the reader missed at the very top or bottom of the matrix
    # would have sat (Codex review, PR #182). A dropped *edge* label is
    # invisible to the pitch check -- there is no gap left behind, the axis
    # simply ends early -- so without this its whole row of values would fall
    # outside every band and be waved through as page chrome, silently
    # truncating the surface.
    #
    # The window is the widest pitch this axis is *allowed* to have, not the
    # narrowest one it happens to show (Codex review round 2). On an axis
    # whose pitches are uneven but still within the regularity threshold --
    # 28px and 44px, say -- a window of one narrowest pitch stops short of a
    # dropped row continuing at the wider pitch, and the truncated grid stays
    # confirmable. Since ``pitch_irregularity_message`` has already refused
    # anything wider than ``narrowest * PITCH_IRREGULARITY_MULTIPLE``, that
    # product is exactly the furthest a legitimate next row could sit.
    # With a single resolved row there is no pitch to measure at all:
    # ``band_edges`` falls back to the surviving label's own height, which no
    # invariant relates to the missing row's spacing, so a window derived from
    # it can stop short of an orphaned row and wave a whole row away (Codex
    # review round 4, PR #182). When the pitch is unmeasurable the row axis
    # therefore adopts the column axis's rule instead -- refuse a stray number
    # at any distance -- rather than trusting a window it cannot size.
    row_pitch_is_measurable = len(row_centres) > 1
    missing_row_zone = (
        row_outer * 2.0 * PITCH_IRREGULARITY_MULTIPLE if row_pitch_is_measurable else None
    )
    first_row_top_edge = row_centres[0] - row_outer
    last_row_bottom_edge = row_centres[-1] + row_outer

    expiry_labels = tuple(normalise_text(token.text) for token in label_tokens)
    tenor_labels = tuple(normalise_text(token.text) for token in headers)

    header_ids = {id(token) for token in headers} | {id(anchor)}
    label_ids = {id(token) for token in label_tokens}

    # Every token that lands on an intersection is collected first and only
    # then reduced to a value: when two tokens land on the same one, *neither*
    # is used, which a "first write wins" placement could not express.
    placed: dict[tuple[int, int], list[float]] = {}
    for token in tokens:
        if id(token) in header_ids or id(token) in label_ids:
            continue
        row_index, row_ambiguous = assign_band(
            token.y_center, row_centres, row_boundaries, row_outer
        )
        if row_index is None:
            # Beyond the matrix rows. Ordinary chrome above or below the
            # table is ignored, but a *number* sitting exactly where the
            # next row would be is the signature of a dropped edge label,
            # not chrome, so it fails the capture closed.
            near_a_missing_edge_row = missing_row_zone is None or (
                first_row_top_edge - missing_row_zone
                <= token.y_center
                <= last_row_bottom_edge + missing_row_zone
            )
            if near_a_missing_edge_row and parse_cell_number(token.text)[0] is not None:
                issues.block(
                    "NUMERIC_TOKEN_OUTSIDE_ROWS",
                    f"the number {normalise_text(token.text)!r} sits one row beyond the resolved "
                    "expiry rows, which is where a row label the reader missed would be; it "
                    "cannot be placed, so the grid may be incomplete",
                )
            continue

        column_index, column_ambiguous = assign_band(
            token.x_center, column_centres, column_boundaries, column_outer
        )
        if column_index is None:
            # Beside the matrix. Ordinary chrome (a caption, a scrollbar
            # legend) is ignored, but a *number* the template cannot place is
            # exactly the structurally ambiguous case: it may well be a value
            # from a column the reader failed to resolve, so it is reported
            # rather than dropped.
            if parse_cell_number(token.text)[0] is not None:
                issues.block(
                    "NUMERIC_TOKEN_OUTSIDE_COLUMNS",
                    f"the number {normalise_text(token.text)!r} sits on a matrix row but outside "
                    "every resolved tenor column, so it cannot be placed",
                )
            continue

        expiry = expiry_labels[row_index]
        tenor = tenor_labels[column_index]
        if row_ambiguous or column_ambiguous:
            axis = "row" if row_ambiguous else "column"
            issues.block(
                "CELL_POSITION_AMBIGUOUS",
                f"{normalise_text(token.text)!r} sits on a {axis} boundary and could belong "
                f"to more than one {axis}; it is left unresolved rather than assigned to "
                f"{expiry} x {tenor}",
                expiry=expiry,
                tenor=tenor,
            )
            continue

        value, failure_code = parse_cell_number(token.text)
        if failure_code is not None:
            issues.block(
                failure_code,
                f"{expiry} x {tenor} reads {normalise_text(token.text)!r}, which is not a usable "
                "number",
                expiry=expiry,
                tenor=tenor,
            )
            continue
        assert value is not None
        placed.setdefault((row_index, column_index), []).append(value)

    cells: dict[tuple[int, int], float] = {}
    for (row_index, column_index), candidates in placed.items():
        if len(candidates) == 1:
            cells[(row_index, column_index)] = candidates[0]
            continue
        issues.block(
            "DUPLICATE_CELL",
            f"{len(candidates)} values were read into {expiry_labels[row_index]} x "
            f"{tenor_labels[column_index]} "
            f"({', '.join(format(candidate, 'g') for candidate in candidates)}), so none of "
            "them is used",
            expiry=expiry_labels[row_index],
            tenor=tenor_labels[column_index],
        )

    values = tuple(
        tuple(cells.get((row_index, column_index)) for column_index in range(len(tenor_labels)))
        for row_index in range(len(expiry_labels))
    )
    grid = VCUBATMGrid(expiry_labels, tenor_labels, values)
    for expiry, tenor in grid.unresolved_cells():
        issues.warn(
            "UNRESOLVED_CELL",
            f"{expiry} x {tenor} has no resolved value; compare it against the screenshot",
            expiry=expiry,
            tenor=tenor,
        )
    return _capture(provenance, metadata, grid, issues)


def _capture(
    provenance: VCUBCaptureProvenance,
    metadata: VCUBSourceMetadata,
    grid: VCUBATMGrid | None,
    issues: _Issues,
) -> VCUBATMCapture:
    return VCUBATMCapture(
        provenance=provenance,
        metadata=metadata,
        grid=grid,
        blocking_errors=tuple(issues.blocking),
        warnings=tuple(issues.warnings),
    )
