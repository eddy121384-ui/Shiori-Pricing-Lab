"""Template-driven reconstruction of a Bloomberg VCUB **OTM Swaptions /
SABR** ``Term x Tenor`` by strike matrix, from one or more overlapping
screenshots (Issue #185).

**The same philosophy as the ATM template, on a taller screen.** Not one
absolute pixel constant: the parser locates the ``Term x Tenor`` corner
anchor, reads the strike headers that sit on that anchor's own text line,
reads the row labels that sit in that anchor's own column, and derives every
band from *those detections*. Every threshold it uses is a fraction of the
matrix's own measured pitch (they live in
:mod:`shiori_pricing_lab.data.bloomberg_vcub_screen_reader`, shared with the
ATM template), so re-cropping, moving the window, or changing DPI/scale
translates and scales every token together and changes no cell mapping.

**Columns are banded on their right edges, not their centres.** This screen
right-aligns both the strike header and every number under it against the
same column edge, while their *widths* differ by several characters --
``-200bps`` against ``4.01``. A centre therefore drifts with the length of
whatever was drawn, and the right edge does not, so the edge is the column's
real anchor here. Rows keep the ATM template's vertical centres, which is
what a left-aligned row label gives.

**Merging is semantic, never pixels.** Each screenshot is parsed on its own
into a :class:`VCUBOTMImageRead`; :func:`merge_vcub_otm_reads` then combines
them by the ``Term x Tenor`` row key and the strike headers, and orders the
result by the labels themselves. Which file the operator picked first
decides nothing. Adjacent screenshots are expected to overlap, and that
overlap is an integrity check rather than a nuisance:

* the same row read identically twice is one row;
* the same row read *differently* twice blocks the whole capture -- no
  first-wins, last-wins, averaging, or quietly preferring the clearer read;
* a row one image resolved and another left unresolved is filled from the
  image that read it and reported as a warning, so nothing is preferred
  silently;
* screenshots that do not overlap at all cannot prove that no row fell
  between them, so the capture is refused rather than presented as complete.

**Completeness is measured against the screen, not the file count.** The
merged surface must hold exactly the coordinates this screen is known to
carry -- the 91 ``Term x Tenor`` rows of
:data:`~shiori_pricing_lab.data.bloomberg_vcub_otm_capture.EXPECTED_ROWS`,
each with the nine strike columns of
:data:`~shiori_pricing_lab.data.bloomberg_vcub_otm_capture.EXPECTED_STRIKE_OFFSETS_BP`.
Short of either axis is a partial capture however cleanly each image read,
and a coordinate outside either is not this screen; all four cases block, and
each names what is involved. How many screenshots it took is irrelevant: one
that holds the whole surface passes, and four that between them hold half of
it do not.

**What this parser optimises for.** Not OCR recall: the failure that matters
is a plausible number landing in the wrong ``Term x Tenor x Strike``
coordinate, because that survives visual review far more easily than a hole
does. Every value is placed by its own geometry, never by its position in a
sequence of numbers, so a missing cell cannot shift its neighbours and a
partial row cannot shift the rows below it.

Nothing in this module imports :mod:`shiori_pricing_lab.pricing`, and
nothing here interpolates, converts, re-bases, or adds a spread to an ATM
vol.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    VCUBCaptureProvenance,
    VCUBTextToken,
)
from shiori_pricing_lab.data.bloomberg_vcub_otm_capture import (
    EXPECTED_ROWS,
    EXPECTED_STRIKE_OFFSETS_BP,
    NORMAL_VOL_SKEW_TYPE,
    OTM_METADATA_FIELDS,
    OTM_SWAPTIONS_SABR_TAB,
    SPREAD_DISPLAY_MODE,
    TERM_TENOR_ANCHOR,
    VCUBOTMCapture,
    VCUBOTMCaptureIssue,
    VCUBOTMImageRead,
    VCUBOTMRow,
    VCUBOTMSourceCoverage,
    VCUBOTMSourceMetadata,
    VCUBOTMStrike,
    VCUBOTMTable,
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
    space_between,
    spans_are_orderable,
    starts_a_new_widget,
    tenor_label_nominal_days,
    unique_curve_config,
    unique_line_containing,
    unique_match,
    unique_member,
    widget_values,
)
from shiori_pricing_lab.products.enums import Currency

#: What the tab strip must contain for this image to be read as the OTM
#: screen at all. Matched on the words rather than the whole
#: ``OTM Swaptions / SABR`` label because the spacing a reader leaves around
#: the slash is exactly the kind of detail that varies between boxes -- and
#: the topology anchors below, not this line, are what actually decide that
#: the picture is this matrix.
_TAB_NEEDLE = "otm swaptions"

#: The corner anchor as it must read once its tokens are joined, with the
#: reader's spacing removed: ``Term x Tenor`` may arrive as one box or as
#: three, and neither grouping should change the answer.
_ANCHOR_PATTERN = re.compile(r"^term\s*x\s*tenor$", re.IGNORECASE)

#: A strike header: an explicit basis-point offset, or the ATM column.
#: ``-200bps``/``25bps`` only -- no ``%``, no decimal, no bare number. The
#: unit is *read off the screen*, never inferred from the magnitude of the
#: numbers, so a header without ``bps`` is not a strike this parser knows.
_STRIKE_OFFSET_RE = re.compile(r"^([+-]?\d{1,4})\s*bps?$", re.IGNORECASE)
_ATM_STRIKE_LABEL = "ATM"

#: A ``Term x Tenor`` row label. Both halves must read as tenor buckets, and
#: the separator may be an ``x``, an ``X``, or the multiplication sign a
#: reader sometimes returns for it. Spacing is optional on purpose: joining
#: boxes by geometry may close a gap the screen drew, and this label's two
#: halves are structured enough that a closed gap is still unambiguous.
_ROW_LABEL_RE = re.compile(r"^\s*(\S+?)\s*[x×✕]\s*(\S+?)\s*$", re.IGNORECASE)

#: The display modes this capture knows how to read. One member: the
#: observed screen's ``Spread``. A mode this parser has never seen is still
#: captured as a real (if unsupported) reading by :func:`_display_mode_context`
#: rather than collapsed to "unresolved" -- what the selector says decides
#: whether a number is a vol or a spread, and that is not something to
#: assume, so both an unrecognised mode and a genuinely missing selector
#: block the capture, for different reasons and under different codes.
_DISPLAY_MODE_TEXTS = {"SPREAD": SPREAD_DISPLAY_MODE}


class DuplicateCaptureImageError(ValueError):
    """The same screenshot was supplied twice in one capture session.

    Raised rather than reported as a blocking error on a capture: a capture
    records which images formed it, and it has no way to record the same
    image twice. This is an input mistake -- a file picked twice, a file
    dragged twice -- and the caller shows it as such.
    """


class _Issues:
    """Collects blocking errors and warnings without letting them mix up."""

    def __init__(self) -> None:
        self.blocking: list[VCUBOTMCaptureIssue] = []
        self.warnings: list[VCUBOTMCaptureIssue] = []

    def block(self, code: str, message: str, *, row=None, strike=None) -> None:
        self.blocking.append(VCUBOTMCaptureIssue(code, message, row=row, strike=strike))

    def warn(self, code: str, message: str, *, row=None, strike=None) -> None:
        self.warnings.append(VCUBOTMCaptureIssue(code, message, row=row, strike=strike))


# --------------------------------------------------------------------------
# One screenshot
# --------------------------------------------------------------------------


def parse_strike_header(text: str) -> VCUBOTMStrike | None:
    """The strike column ``text`` names, or ``None`` if it names none.

    ``"ATM"`` is the column the others are measured from and carries no
    offset; ``"-50bps"`` carries ``-50.0``. Nothing else is a strike header
    this template recognises.
    """

    cleaned = normalise_text(text)
    if cleaned.upper() == _ATM_STRIKE_LABEL:
        return VCUBOTMStrike(label=cleaned)
    match = _STRIKE_OFFSET_RE.match(cleaned)
    if match is None:
        return None
    offset = float(match.group(1))
    if offset == 0.0:
        # A "0bps" column would be indistinguishable from ATM. The observed
        # screen has no such header, and guessing which of the two it meant
        # is exactly the kind of assumption this slice refuses.
        return None
    return VCUBOTMStrike(label=cleaned, offset_bp=offset)


def parse_row_label(text: str) -> tuple[str, str] | None:
    """The ``(term, tenor)`` pair ``text`` names, or ``None``.

    Both halves must read as tenor buckets in their own right, so a line of
    ordinary prose that happens to contain an ``x`` is never mistaken for a
    row of the matrix.
    """

    match = _ROW_LABEL_RE.match(normalise_text(text))
    if match is None:
        return None
    term, tenor = match.group(1), match.group(2)
    if not is_tenor_label(term) or not is_tenor_label(tenor):
        return None
    return normalise_text(term), normalise_text(tenor)


#: The literal label the ``Source`` widget carries. Anchoring on this word,
#: rather than on the contributor value that follows it, is what lets
#: :func:`_display_mode_context` find the display-mode widget even when the
#: contributor itself is misread (``BVOL`` -> ``BV0L``) and so does not
#: match :data:`~shiori_pricing_lab.data.bloomberg_vcub_screen_reader.KNOWN_SOURCE_TEXTS`
#: (Codex review, PR #186).
_SOURCE_LABEL_TEXT = "SOURCE"


def _display_mode_context(lines: Sequence[TextLine]) -> tuple[bool, str | None]:
    """Whether the ``Source`` label is on screen, and the display-mode widget's
    own raw text if -- and only if -- its position is unambiguous.

    Returns ``(source_label_seen, candidate)``. The dropdown carries no
    label of its own, so it cannot be found by content the way ``Type`` is
    -- it is found by position instead: the screen always draws it exactly
    two widgets after the literal ``Source`` label, with the contributor's
    own value between them, on the same line. Anchoring on the label rather
    than on the contributor's *value* matters: were it anchored on a
    recognised contributor, a misread contributor code would hide a
    perfectly legible display-mode reading right next to it.

    What this function refuses to do is guess *which* widget is the display
    mode when that two-widget spacing itself cannot be confirmed -- a
    screenshot whose OCR dropped the contributor's tokens entirely, or one
    cropped right after the contributor, both leave only one widget after
    ``Source`` rather than two, and there is no way to tell from a token
    stream alone whether that lone widget is the contributor or the display
    mode. Guessing either way risks misreading a value under the wrong
    field's meaning, so neither is attempted: ``candidate`` comes back
    ``None`` in both cases, and it is ``source_label_seen`` that lets the
    caller tell that apart from a screenshot that never showed this part of
    the chrome at all (Codex review, PR #186).
    """

    source_label_seen = False
    candidates: set[str] = set()
    for line in lines:
        values = widget_values(line.tokens)
        for index, value in enumerate(values):
            if value.strip().upper() != _SOURCE_LABEL_TEXT:
                continue
            source_label_seen = True
            if index + 2 < len(values):
                candidates.add(values[index + 2])
    candidate = candidates.pop() if len(candidates) == 1 else None
    return source_label_seen, candidate


def _resolve_metadata(
    lines: Sequence[TextLine], tab_resolved: bool
) -> tuple[VCUBOTMSourceMetadata, bool]:
    """Read the screen's header context, marking anything uncertain unresolved.

    The four fields VCUB draws the same way on every tab -- currency,
    curve/config, side and date -- are read exactly as the ATM template
    reads them, through the same shared rules. The three this screen words
    differently are read here:

    * ``Type`` carries ``Normal Vol Skew``, which the ATM tab's vol-type
      pattern does not describe, so the labelled value is used directly;
    * ``Source`` is read from the widgets alone, never from the text after
      the word "Source". On this screen the display selector sits
      immediately to the right of the contributor with no label of its own,
      so a labelled-value run would swallow it and store ``BVOL ... Spread``
      as the contributor;
    * ``display mode`` has no on-screen label at all, so :func:`_display_mode_context`
      finds it by that same adjacency to Source, and its raw text is kept
      even when it is not a mode this parser knows.

    Returns the metadata and a second, separate flag: whether the ``Source``
    label was seen on screen but the display-mode widget's own position
    could not be confirmed. That case is not the same as the selector simply
    not being in this screenshot's crop -- Source *is* visible, so this is
    evidence the top chrome was meant to be read here, just not cleanly
    enough to trust -- and the caller turns it into a blocker of its own
    rather than folding it into the ordinary unresolved-field bookkeeping
    below (Codex review, PR #186).
    """

    joins = [join_by_geometry(line.tokens) for line in lines]
    segments = [
        (segment, is_menu, certain)
        for text, certain in joins
        for segment, is_menu in field_segments(text)
    ]
    legible_line_texts = [text for text, certain in joins if certain]
    legible_value_segments = [
        segment for segment, is_menu, certain in segments if not is_menu and certain
    ]
    screen_widget_values = [
        value for line in lines for value in widget_values(line.tokens)
    ]

    resolved: dict[str, str | None] = dict.fromkeys(OTM_METADATA_FIELDS, None)
    resolved["currency"] = unique_member(screen_widget_values, set(Currency))
    resolved["curve_config"] = unique_curve_config(lines)
    side = unique_member(
        [value.upper() for value in screen_widget_values], set(SIDE_TEXTS)
    )
    resolved["side"] = None if side is None else SIDE_TEXTS[side]
    resolved["quote_date"] = unique_match(legible_line_texts, DATE_RE)
    if tab_resolved:
        resolved["tab"] = OTM_SWAPTIONS_SABR_TAB
    resolved["vol_type"] = labelled_value(legible_value_segments, "Type")
    resolved["source"] = unique_member(
        [value.upper() for value in screen_widget_values], KNOWN_SOURCE_TEXTS
    )
    source_label_seen, display_candidate = _display_mode_context(lines)
    resolved["display_mode"] = (
        None
        if display_candidate is None
        else _DISPLAY_MODE_TEXTS.get(display_candidate.upper(), display_candidate)
    )
    display_mode_context_ambiguous = source_label_seen and display_candidate is None

    unresolved = tuple(name for name in OTM_METADATA_FIELDS if resolved[name] is None)
    metadata = VCUBOTMSourceMetadata(**resolved, unresolved_fields=unresolved)
    return metadata, display_mode_context_ambiguous


def _check_value_semantics(metadata: VCUBOTMSourceMetadata, issues: _Issues) -> None:
    """Refuse a screen whose Type or display mode is not the observed one.

    These two fields are not decoration: together they say that the ATM
    column holds an absolute normal vol and every other column holds a
    spread to it. A capture that cannot read them, or reads something else,
    has numbers whose meaning is unknown -- and storing a number whose
    meaning is unknown is exactly what Issue #185 forbids.
    """

    if metadata.vol_type is None:
        issues.block(
            "VOL_TYPE_UNRESOLVED",
            "the Type selector could not be read, and it is what says these numbers are a "
            f"{NORMAL_VOL_SKEW_TYPE!r}; the capture is refused rather than stored under an "
            "assumed meaning",
        )
    elif normalise_text(metadata.vol_type).casefold() != NORMAL_VOL_SKEW_TYPE.casefold():
        issues.block(
            "UNSUPPORTED_VOL_TYPE",
            f"this capture only reads the {NORMAL_VOL_SKEW_TYPE!r} screen; the Type selector "
            f"reads {metadata.vol_type!r}, whose numbers this parser has never been shown",
        )

    if metadata.display_mode is None:
        issues.block(
            "DISPLAY_MODE_UNRESOLVED",
            "the display selector could not be read, and it is what says whether these "
            f"numbers are spreads ({SPREAD_DISPLAY_MODE!r}) or absolute vols; the capture is "
            "refused rather than stored under an assumed meaning",
        )
    elif metadata.display_mode != SPREAD_DISPLAY_MODE:
        issues.block(
            "UNSUPPORTED_DISPLAY_MODE",
            f"this capture only reads the {SPREAD_DISPLAY_MODE!r} display mode; the selector "
            f"reads {metadata.display_mode!r}",
        )


def _header_line_runs(line: TextLine) -> list[list[VCUBTextToken]]:
    """One text line split into the runs the screen drew as separate labels.

    A reader may hand back ``-200bps`` as one box or as ``-200`` and ``bps``,
    and ``Term x Tenor`` as one box or as three. Reading the header line run
    by run -- grouped by the same widget-gap rule the metadata strip uses --
    makes both groupings produce the same answer, instead of failing a
    perfectly legible screen because its glyphs were boxed differently.
    """

    runs: list[list[VCUBTextToken]] = [[line.tokens[0]]]
    # Deliberately ragged: the last token has no successor to pair with.
    for left, right in zip(line.tokens, line.tokens[1:], strict=False):
        if starts_a_new_widget(left, right):
            runs.append([right])
        else:
            runs[-1].append(right)
    return runs


def _find_anchor_line(
    lines: Sequence[TextLine], issues: _Issues
) -> tuple[TextLine, list[list[VCUBTextToken]]] | None:
    """The corner-anchor line, split into its runs, or ``None``.

    The header line is the one that reads ``Term x Tenor`` in one run and
    carries at least one strike header to its right. Two such lines mean the
    parser cannot tell which matrix it is looking at, and none means this
    image does not show one.
    """

    candidates: list[tuple[TextLine, list[list[VCUBTextToken]]]] = []
    for line in lines:
        runs = _header_line_runs(line)
        anchors = [
            index
            for index, run in enumerate(runs)
            if _ANCHOR_PATTERN.match(join_by_geometry(run)[0])
        ]
        if len(anchors) != 1:
            continue
        following = runs[anchors[0] + 1 :]
        if not any(
            parse_strike_header(join_by_geometry(run)[0]) is not None for run in following
        ):
            continue
        candidates.append((line, runs))

    if not candidates:
        issues.block(
            "TERM_TENOR_ANCHOR_UNRESOLVED",
            f"the {TERM_TENOR_ANCHOR!r} corner anchor was not found on a line carrying strike "
            "headers, so the matrix has no origin and no row or column can be placed",
        )
        return None
    if len(candidates) > 1:
        issues.block(
            "TERM_TENOR_ANCHOR_AMBIGUOUS",
            f"{len(candidates)} {TERM_TENOR_ANCHOR!r} anchors were found; the parser cannot "
            "tell which one is the matrix corner, so no grid is reconstructed",
        )
        return None
    return candidates[0]


def _resolve_strike_headers(
    runs: Sequence[Sequence[VCUBTextToken]], issues: _Issues
) -> tuple[list[list[VCUBTextToken]], list[VCUBOTMStrike], list[VCUBTextToken]] | None:
    """Read the strike headers to the right of the corner anchor.

    Returns the header runs, the strikes they name, and the anchor's own
    tokens. Anything drawn to the *left* of the corner is chrome outside the
    matrix and is reported as a warning: it cannot be a strike header, since
    those are all drawn to the right of the corner. Anything to the right
    that does not read as a strike header blocks -- there the column layout
    itself is in question.
    """

    texts = [join_by_geometry(run)[0] for run in runs]
    anchor_index = next(
        index for index, text in enumerate(texts) if _ANCHOR_PATTERN.match(text)
    )
    anchor_tokens = list(runs[anchor_index])
    for text in texts[:anchor_index]:
        issues.warn(
            "UNRECOGNISED_HEADER_LINE_TOKEN",
            f"{normalise_text(text)!r} sits left of the {TERM_TENOR_ANCHOR} corner on the "
            "header line; it is not part of the matrix and was ignored",
        )

    headers: list[list[VCUBTextToken]] = []
    strikes: list[VCUBOTMStrike] = []
    for run, text in zip(runs[anchor_index + 1 :], texts[anchor_index + 1 :], strict=True):
        strike = parse_strike_header(text)
        if strike is None:
            issues.block(
                "STRIKE_HEADER_UNEXPECTED_TOKEN",
                f"{normalise_text(text)!r} sits on the strike header line but does not read "
                "as a strike offset or as ATM, so the column layout cannot be trusted",
            )
            continue
        headers.append(list(run))
        strikes.append(strike)

    if not headers:
        issues.block(
            "STRIKE_HEADERS_UNRESOLVED",
            f"no strike headers were found to the right of {TERM_TENOR_ANCHOR!r}",
        )
        return None

    order = sorted(range(len(headers)), key=lambda index: _run_right(headers[index]))
    headers = [headers[index] for index in order]
    strikes = [strikes[index] for index in order]
    labels = [strike.label for strike in strikes]

    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        issues.block(
            "DUPLICATE_STRIKE_HEADER",
            f"the strike header row repeats {', '.join(duplicates)}; a value could belong to "
            "either column, so no cell is placed",
        )
        return None
    if len([strike for strike in strikes if strike.is_atm]) != 1:
        issues.block(
            "ATM_STRIKE_COLUMN_UNRESOLVED",
            "the strike header row must carry exactly one ATM column -- it is the vol every "
            "other column's spread is measured from -- and this one does not",
        )
        return None
    for index in range(len(headers) - 1):
        left, right = headers[index], headers[index + 1]
        if not spans_are_orderable(
            _run_left(left), _run_right(left), _run_left(right), _run_right(right)
        ):
            issues.block(
                "STRIKE_HEADERS_NOT_ORDERABLE",
                f"strike headers {labels[index]!r} and {labels[index + 1]!r} overlap "
                "horizontally by enough to read as one header, so their left-to-right order "
                "is not unambiguous",
            )
            return None
        if strikes[index + 1].sort_key <= strikes[index].sort_key:
            issues.block(
                "STRIKE_HEADERS_NOT_MONOTONIC",
                f"the strike headers do not increase across the axis ({labels[index]!r} then "
                f"{labels[index + 1]!r}), so the strike order cannot be trusted",
            )
            return None
    pitch_message = pitch_irregularity_message(
        [_run_right(run) for run in headers], axis="strike columns"
    )
    if pitch_message is not None:
        issues.block("STRIKE_COLUMN_PITCH_IRREGULAR", pitch_message)
    return headers, strikes, anchor_tokens


def _run_left(tokens: Sequence[VCUBTextToken]) -> float:
    return min(token.left for token in tokens)


def _run_right(tokens: Sequence[VCUBTextToken]) -> float:
    return max(token.right for token in tokens)


def _resolve_row_labels(
    lines: Sequence[TextLine],
    anchor_line: TextLine,
    anchor_tokens: Sequence[VCUBTextToken],
    first_column_left_edge: float,
    excluded_ids: set[int],
    issues: _Issues,
) -> list[tuple[list[VCUBTextToken], str, str]] | None:
    """Read the ``Term x Tenor`` labels sitting in the anchor's own column.

    The column is bounded on the right by the first strike column's own left
    edge, so nothing from inside the matrix can be mistaken for a row label,
    and on the left by the anchor's own width, so unrelated chrome further
    left is ignored. A row label may be several tokens (``1Mo``, ``x``,
    ``1Yr``), so each text line's label-column tokens are joined before they
    are read.

    ``excluded_ids`` keeps a split minus sign in the leftmost strike column
    out of this scan: a wide enough unsigned number there can place its own
    separate minus-glyph token's centre inside this same x-range, and
    without this it would be read as (nonsense) row-label text and corrupt
    that row's pitch instead of being folded into the cell's value where it
    belongs (Codex review, PR #186).
    """

    anchor_left = min(token.left for token in anchor_tokens)
    anchor_width = max(token.right for token in anchor_tokens) - anchor_left
    left_bound = anchor_left - anchor_width

    rows: list[tuple[list[VCUBTextToken], str, str]] = []
    for line in lines_below(lines, anchor_line):
        label_tokens = [
            token
            for token in line.tokens
            if left_bound <= token.x_center < first_column_left_edge
            and id(token) not in excluded_ids
        ]
        if not label_tokens:
            continue
        text, _certain = join_by_geometry(label_tokens)
        parsed = parse_row_label(text)
        if parsed is None:
            issues.warn(
                "UNRECOGNISED_ROW_LABEL_TOKEN",
                f"{normalise_text(text)!r} sits in the {TERM_TENOR_ANCHOR} column but does "
                "not read as a Term x Tenor label; it was not treated as a row",
            )
            continue
        rows.append((label_tokens, parsed[0], parsed[1]))

    if not rows:
        issues.block(
            "ROW_LABELS_UNRESOLVED",
            f"no {TERM_TENOR_ANCHOR!r} row labels were found below the anchor",
        )
        return None

    rows.sort(key=lambda item: _row_centre(item[0]))
    labels = [f"{term} x {tenor}" for _tokens, term, tenor in rows]
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        issues.block(
            "DUPLICATE_ROW_LABEL",
            f"the row label column repeats {', '.join(duplicates)}; a value could belong to "
            "either row, so no cell is placed",
        )
        return None
    for index in range(len(rows) - 1):
        upper = rows[index][0]
        lower = rows[index + 1][0]
        if not spans_are_orderable(
            min(token.top for token in upper),
            max(token.bottom for token in upper),
            min(token.top for token in lower),
            max(token.bottom for token in lower),
        ):
            issues.block(
                "ROWS_NOT_ORDERABLE",
                f"row labels {labels[index]!r} and {labels[index + 1]!r} overlap vertically by "
                "enough to read as one row, so their top-to-bottom order is not unambiguous",
            )
            return None
        if _row_sort_key(rows[index + 1]) <= _row_sort_key(rows[index]):
            issues.block(
                "ROW_LABELS_NOT_MONOTONIC",
                f"the row labels do not increase down the screen ({labels[index]!r} then "
                f"{labels[index + 1]!r}), so the row order cannot be trusted",
            )
            return None
    pitch_message = pitch_irregularity_message(
        [_row_centre(tokens) for tokens, _term, _tenor in rows], axis="rows"
    )
    if pitch_message is not None:
        issues.block("ROW_PITCH_IRREGULAR", pitch_message)
    return rows


def _row_centre(tokens: Sequence[VCUBTextToken]) -> float:
    """One row label's vertical centre, across however many boxes it took."""

    top = min(token.top for token in tokens)
    bottom = max(token.bottom for token in tokens)
    return (top + bottom) / 2.0


def _row_sort_key(row: tuple[list[VCUBTextToken], str, str]) -> tuple[float, float]:
    _tokens, term, tenor = row
    term_days = tenor_label_nominal_days(term)
    tenor_days = tenor_label_nominal_days(tenor)
    assert term_days is not None and tenor_days is not None  # both parsed as tenor labels
    return (term_days, tenor_days)


#: Glyphs an OCR reader might emit for Bloomberg's narrow minus sign when it
#: boxes it apart from the digits it belongs to, rather than folding it into
#: that token's own text (which ``parse_cell_number`` already reads
#: correctly, unchanged). A closed, narrow set on purpose: reconstruction
#: below only ever fires on positive visual evidence of a minus sitting
#: immediately before a numeric token, never a guess (live-acceptance
#: defect, PR #186).
_MINUS_GLYPHS = frozenset({"-", "‐", "‑", "‒", "–", "−"})


def _looks_like_a_lone_minus(token: VCUBTextToken) -> bool:
    return normalise_text(token.text) in _MINUS_GLYPHS


def _reconstructed_minus_tokens(
    tokens: Sequence[VCUBTextToken], excluded_ids: set[int]
) -> dict[int, VCUBTextToken]:
    """Map a numeric token's ``id()`` to the separate minus-glyph token
    touching it on the left, for every pairing that is unambiguous.

    A minus sign the reader boxed apart from its digits is still its own
    token, touching the digits with no space between them -- the same
    "one visual unit, more than one box" signal :func:`join_by_geometry`
    already uses to rejoin a split strike header such as ``-200`` + ``bps``.
    Nothing here infers a sign from anywhere except that one adjacent box:
    not the other screenshot in the capture session, not the column, not a
    neighbouring value, not the expected skew shape. A minus glyph that sits
    next to more than one numeric token, or a numeric token approached by
    more than one minus glyph, is evidence this parser cannot read
    unambiguously, so neither is guessed at -- the pairing is simply
    dropped and the number is read exactly as its own token states, which a
    conflicting overlapping screenshot can still catch (live-acceptance
    defect, PR #186).
    """

    minus_candidates = [
        token
        for token in tokens
        if id(token) not in excluded_ids and _looks_like_a_lone_minus(token)
    ]
    if not minus_candidates:
        return {}

    unsigned_numeric_tokens = [
        token
        for token in tokens
        if id(token) not in excluded_ids
        and not _looks_like_a_lone_minus(token)
        and not normalise_text(token.text).startswith(("+", "-"))
        and parse_cell_number(token.text)[0] is not None
    ]

    matches_by_number: dict[int, list[VCUBTextToken]] = {}
    for minus in minus_candidates:
        touching = [
            number
            for number in unsigned_numeric_tokens
            if minus.left < number.left
            and minus.top < number.bottom
            and number.top < minus.bottom
            and space_between(minus, number) is False
        ]
        if len(touching) != 1:
            continue
        matches_by_number.setdefault(id(touching[0]), []).append(minus)

    return {
        number_id: minuses[0]
        for number_id, minuses in matches_by_number.items()
        if len(minuses) == 1
    }


def parse_vcub_otm_tokens(
    tokens: Sequence[VCUBTextToken], *, provenance: VCUBCaptureProvenance
) -> VCUBOTMImageRead:
    """Reconstruct one screenshot's slice of the OTM/SABR matrix.

    Returns a read in every case -- a parse never accepts anything, and one
    screenshot is never a capture. When the topology could not be
    established the read's ``table`` is ``None``; when it could, the table is
    present with unresolved intersections left as ``None`` and every reason
    to distrust the read listed in ``blocking_errors``.
    """

    issues = _Issues()
    lines = group_into_lines(tokens)

    tab_line = unique_line_containing([line.joined_text() for line in lines], _TAB_NEEDLE)
    if tab_line is None:
        issues.block(
            "OTM_SABR_TAB_UNRESOLVED",
            f"the {OTM_SWAPTIONS_SABR_TAB!r} tab anchor was not found exactly once, so this "
            "image was not recognised as the VCUB OTM Swaptions / SABR layout",
        )

    metadata, display_mode_context_ambiguous = _resolve_metadata(
        lines, tab_resolved=tab_line is not None
    )
    if display_mode_context_ambiguous:
        issues.block(
            "DISPLAY_MODE_CONTEXT_AMBIGUOUS",
            "the Source selector is visible on this screenshot, but the display-mode widget "
            "that should sit two widgets after it could not be confirmed -- unlike a screenshot "
            "that never shows this part of the chrome at all, this one entered the Source/"
            "display region and its meaning could not be established, so the capture is refused "
            "rather than guessing which widget is the contributor and which is the display mode",
        )
    _check_value_semantics(metadata, issues)

    anchor_found = _find_anchor_line(lines, issues)
    if anchor_found is None:
        return _image_read(provenance, metadata, None, issues)
    anchor_line, header_line_runs = anchor_found

    headers_found = _resolve_strike_headers(header_line_runs, issues)
    if headers_found is None:
        return _image_read(provenance, metadata, None, issues)
    headers, strikes, anchor_tokens = headers_found

    # Right edges rather than centres: this screen right-aligns the header
    # and every number under it against the same column edge, while their
    # widths differ by several characters.
    column_edges = [_run_right(run) for run in headers]
    column_boundaries, column_outer = band_edges(
        column_edges, max(_run_right(run) - _run_left(run) for run in headers)
    )
    first_column_left_edge = column_edges[0] - column_outer

    header_ids = {id(token) for run in headers for token in run} | {
        id(token) for token in anchor_tokens
    }
    # Resolved before the row labels, not after: a split sign in the
    # leftmost strike column can sit far enough left of its own digits to
    # land inside the Term x Tenor label column's own x-range, and
    # _resolve_row_labels would otherwise absorb it into a row label and
    # corrupt that row's pitch. Excluding it here, before that scan runs,
    # is what keeps the two from colliding (Codex review, PR #186).
    minus_by_number_id = _reconstructed_minus_tokens(tokens, header_ids)
    minus_token_ids = {id(minus) for minus in minus_by_number_id.values()}

    rows_found = _resolve_row_labels(
        lines, anchor_line, anchor_tokens, first_column_left_edge, minus_token_ids, issues
    )
    if rows_found is None:
        return _image_read(provenance, metadata, None, issues)

    row_centres = [_row_centre(tokens) for tokens, _term, _tenor in rows_found]
    row_heights = [
        max(token.bottom for token in tokens) - min(token.top for token in tokens)
        for tokens, _term, _tenor in rows_found
    ]
    row_boundaries, row_outer = band_edges(row_centres, max(row_heights))
    # Where a row the reader missed at the very top or bottom of this
    # screenshot would have sat. A dropped *edge* label is invisible to the
    # pitch check -- there is no gap left behind, the slice simply ends early
    # -- so without this its whole row of values would fall outside every
    # band and be waved through as page chrome. On a multi-image capture that
    # matters twice over: a screenshot cropped through a row is exactly how
    # an operator's slice ends. Reasoning and bounds are the ATM template's
    # (PR #182, Codex rounds 2 and 4).
    row_pitch_is_measurable = len(row_centres) > 1
    missing_row_zone = (
        row_outer * 2.0 * PITCH_IRREGULARITY_MULTIPLE if row_pitch_is_measurable else None
    )
    first_row_top_edge = row_centres[0] - row_outer
    last_row_bottom_edge = row_centres[-1] + row_outer

    row_labels = [f"{term} x {tenor}" for _tokens, term, tenor in rows_found]
    strike_labels = [strike.label for strike in strikes]

    label_ids = {id(token) for tokens, _term, _tenor in rows_found for token in tokens}

    # Every token that lands on an intersection is collected first and only
    # then reduced to a value: when two tokens land on the same one, *neither*
    # is used, which a "first write wins" placement could not express.
    placed: dict[tuple[int, int], list[float]] = {}
    for token in tokens:
        if id(token) in header_ids or id(token) in label_ids or id(token) in minus_token_ids:
            continue
        row_index, row_ambiguous = assign_band(
            token.y_center, row_centres, row_boundaries, row_outer
        )
        if row_index is None:
            near_a_missing_edge_row = missing_row_zone is None or (
                first_row_top_edge - missing_row_zone
                <= token.y_center
                <= last_row_bottom_edge + missing_row_zone
            )
            if near_a_missing_edge_row and parse_cell_number(token.text)[0] is not None:
                issues.block(
                    "NUMERIC_TOKEN_OUTSIDE_ROWS",
                    f"the number {normalise_text(token.text)!r} sits one row beyond the "
                    "resolved rows, which is where a row label the reader missed would be -- "
                    "including a row this screenshot was cropped through. It cannot be "
                    "placed, so this screenshot is refused; re-take it so its first and last "
                    "rows are fully visible",
                )
            continue

        column_index, column_ambiguous = assign_band(
            token.right, column_edges, column_boundaries, column_outer
        )
        if column_index is None:
            if parse_cell_number(token.text)[0] is not None:
                issues.block(
                    "NUMERIC_TOKEN_OUTSIDE_COLUMNS",
                    f"the number {normalise_text(token.text)!r} sits on a matrix row but "
                    "outside every resolved strike column, so it cannot be placed",
                )
            continue

        row_label = row_labels[row_index]
        strike_label = strike_labels[column_index]
        # A separate minus-glyph token touching this one on the left is
        # folded in here, and nowhere else -- the same geometry
        # reconstruction join_by_geometry already applies to a split strike
        # header, applied to a split sign instead (live-acceptance defect,
        # PR #186).
        reconstructed_minus = minus_by_number_id.get(id(token))
        cell_text = (
            "-" + normalise_text(token.text) if reconstructed_minus is not None else token.text
        )
        if row_ambiguous or column_ambiguous:
            axis = "row" if row_ambiguous else "strike column"
            issues.block(
                "CELL_POSITION_AMBIGUOUS",
                f"{normalise_text(cell_text)!r} sits on a {axis} boundary and could belong "
                f"to more than one {axis}; it is left unresolved rather than assigned to "
                f"{row_label} x {strike_label}",
                row=row_label,
                strike=strike_label,
            )
            continue

        value, failure_code = parse_cell_number(cell_text)
        if failure_code is not None:
            issues.block(
                failure_code,
                f"{row_label} at {strike_label} reads {normalise_text(cell_text)!r}, which "
                "is not a usable number",
                row=row_label,
                strike=strike_label,
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
            f"{len(candidates)} values were read into {row_labels[row_index]} at "
            f"{strike_labels[column_index]} "
            f"({', '.join(format(candidate, 'g') for candidate in candidates)}), so none of "
            "them is used",
            row=row_labels[row_index],
            strike=strike_labels[column_index],
        )

    table_rows = tuple(
        VCUBOTMRow(
            term=term,
            tenor=tenor,
            values=tuple(
                cells.get((row_index, column_index)) for column_index in range(len(strikes))
            ),
        )
        for row_index, (_tokens, term, tenor) in enumerate(rows_found)
    )
    try:
        table = VCUBOTMTable(strikes=tuple(strikes), rows=table_rows)
    except ValueError as exc:
        # A safety net, not a repair: every condition below is already
        # checked above with its own message. Reaching here means the read
        # broke an invariant this parser did not anticipate, and a capture
        # with no table is the only safe answer to that.
        issues.block("TABLE_TOPOLOGY_REFUSED", f"the reconstructed table was refused: {exc}")
        return _image_read(provenance, metadata, None, issues)

    for row_label, strike_label in table.unresolved_cells():
        issues.warn(
            "UNRESOLVED_CELL",
            f"{row_label} at {strike_label} has no resolved value; compare it against the "
            "screenshot",
            row=row_label,
            strike=strike_label,
        )
    return _image_read(provenance, metadata, table, issues)


def _image_read(
    provenance: VCUBCaptureProvenance,
    metadata: VCUBOTMSourceMetadata,
    table: VCUBOTMTable | None,
    issues: _Issues,
) -> VCUBOTMImageRead:
    return VCUBOTMImageRead(
        provenance=provenance,
        metadata=metadata,
        table=table,
        blocking_errors=tuple(issues.blocking),
        warnings=tuple(issues.warnings),
    )


# --------------------------------------------------------------------------
# Several screenshots, one capture
# --------------------------------------------------------------------------


#: Selector-resolution codes that describe only what *one screenshot's own
#: crop* could show, not the capture. The intended workflow crops the top
#: Bloomberg chrome into the first screenshot only -- lower screenshots may
#: legitimately show nothing but table rows -- so one image leaving Type or
#: Display unresolved, or reading a value the merged session goes on to
#: correct, is expected and not on its own a reason to refuse the session.
#: These codes are therefore dropped from the per-image forwarding below and
#: re-evaluated exactly once against the *merged* metadata instead, by
#: :func:`_check_value_semantics`; every other blocking code -- topology,
#: OCR, numeric, row, strike -- is still carried through untouched (Codex
#: review, PR #186).
_SELECTOR_RESOLUTION_CODES = frozenset(
    {
        "VOL_TYPE_UNRESOLVED",
        "UNSUPPORTED_VOL_TYPE",
        "DISPLAY_MODE_UNRESOLVED",
        "UNSUPPORTED_DISPLAY_MODE",
    }
)


def merge_vcub_otm_reads(reads: Sequence[VCUBOTMImageRead]) -> VCUBOTMCapture:
    """Combine independently parsed screenshots into one reviewable capture.

    The merge is by coordinate, never by pixel or by file order: rows are
    keyed on ``Term x Tenor``, columns on the strike headers, and the result
    is ordered by the labels themselves. Every disagreement between two
    screenshots -- about a value, about the strike axis, about the screen's
    own metadata -- is a blocking error, because two screenshots of one
    screen state cannot honestly disagree.
    """

    if not reads:
        raise ValueError("a capture session needs at least one screenshot")
    digests = [read.provenance.source_image_sha256 for read in reads]
    repeated = sorted({digest for digest in digests if digests.count(digest) > 1})
    if repeated:
        raise DuplicateCaptureImageError(
            "the same screenshot was supplied more than once in this capture session "
            f"(sha256 {', '.join(digest[:12] for digest in repeated)}). Remove the duplicate "
            "and parse again."
        )

    issues = _Issues()
    for read in reads:
        source = read.provenance.source_reference
        issues.blocking.extend(
            issue.with_source(source)
            for issue in read.blocking_errors
            if issue.code not in _SELECTOR_RESOLUTION_CODES
        )
        issues.warnings.extend(issue.with_source(source) for issue in read.warnings)

    metadata = _merge_metadata(reads, issues)
    _check_value_semantics(metadata, issues)
    readable = [read for read in reads if read.table is not None]
    if not readable:
        return _capture(reads, metadata, None, (), issues)

    strikes = _merge_strike_axis(readable, issues)
    if strikes is None:
        return _capture(reads, metadata, None, (), issues)

    merged_rows = _merge_rows(readable, strikes, issues)
    coverage = _coverage(reads, merged_rows)
    if len(readable) == len(reads):
        # Only worth asking of a complete set: an image whose own topology
        # failed contributes no rows, so a "gap" between the images either
        # side of it would report the failure a second time under a
        # misleading name.
        _check_coverage_chain(readable, issues)

    ordered = tuple(
        row
        for _key, row in sorted(merged_rows.items(), key=lambda item: item[1].sort_key)
    )
    try:
        table = VCUBOTMTable(strikes=tuple(strikes), rows=ordered)
    except ValueError as exc:
        issues.block("MERGED_TABLE_REFUSED", f"the merged table was refused: {exc}")
        return _capture(reads, metadata, None, coverage, issues)
    _check_expected_coverage(table, issues)
    return _capture(reads, metadata, table, coverage, issues)


def _check_expected_coverage(table: VCUBOTMTable, issues: _Issues) -> None:
    """Refuse a merged surface that is not the one this screen is known to hold.

    The completeness invariant, checked against the screen's own semantic row
    set rather than against how many screenshots were supplied (Eddy's
    decision on PR #186). A capture short of the expected rows is partial
    however cleanly each image read, and a capture carrying rows the template
    does not name is not this screen -- both block, and both say exactly
    which coordinates are involved.
    """

    missing = table.missing_expected_rows()
    if missing:
        issues.block(
            "INCOMPLETE_SURFACE",
            f"{len(missing)} of the {len(EXPECTED_ROWS)} expected Term x Tenor rows were not "
            f"captured, so this is part of the screen rather than the screen: "
            f"{_named(missing)}. Capture the rest -- in the same sitting, overlapping what "
            "you already have -- and parse the whole set again",
        )
    for label in table.unexpected_rows():
        issues.block(
            "UNEXPECTED_ROW",
            f"{label} is not a row this screen is known to carry, so either the capture is "
            "not the expected screen or a row label was misread; the expected row set is not "
            "widened to fit it",
            row=label,
        )

    # The same question along the other axis. A session whose screenshots were
    # all cropped at the same vertical edge loses a strike column and its
    # values together, which leaves no gap for the pitch check and no stray
    # number for the outside-column check -- so without this a table could be
    # 91 rows deep and still be missing a coordinate (Codex review round 2).
    missing_strikes = table.missing_expected_strikes()
    if missing_strikes:
        issues.block(
            "INCOMPLETE_STRIKE_AXIS",
            f"{len(missing_strikes)} of the {len(EXPECTED_STRIKE_OFFSETS_BP)} expected strike "
            f"columns were not captured: {_named(missing_strikes)}. Every screenshot in this "
            "session is cropped short of them, so re-take the set wide enough to show the "
            "whole strike axis",
        )
    for label in table.unexpected_strikes():
        issues.block(
            "UNEXPECTED_STRIKE_COLUMN",
            f"{label} is not a strike column this screen is known to carry, so either the "
            "capture is not the expected screen or a header was misread; the expected axis is "
            "not widened to fit it",
            strike=label,
        )


#: How many coordinates a blocking message names before it summarises the
#: rest. Long enough to act on, short enough to read -- the complete list is
#: on the capture itself, which is what the review renders.
_NAMED_ROW_LIMIT = 12


def _named(labels: Sequence[str]) -> str:
    if len(labels) <= _NAMED_ROW_LIMIT:
        return ", ".join(labels)
    shown = ", ".join(labels[:_NAMED_ROW_LIMIT])
    return f"{shown} and {len(labels) - _NAMED_ROW_LIMIT} more"


def _merge_metadata(
    reads: Sequence[VCUBOTMImageRead], issues: _Issues
) -> VCUBOTMSourceMetadata:
    """One screen state's metadata, or a blocking error where they disagree.

    A field resolved by one screenshot and unresolved by another resolves:
    the screenshots are of one screen, and the one that could read the
    header read it. What is refused is two screenshots reading the same
    field *differently* -- that is two screen states, not one capture.
    Nothing is invented: a field no screenshot resolved stays unresolved.
    """

    resolved: dict[str, str | None] = dict.fromkeys(OTM_METADATA_FIELDS, None)
    for name in OTM_METADATA_FIELDS:
        values = {
            value
            for read in reads
            if (value := getattr(read.metadata, name)) is not None
        }
        if name == "vol_type":
            if values:
                resolved[name] = _merge_vol_type(values, issues)
            continue
        if len(values) > 1:
            issues.block(
                "METADATA_CONFLICT",
                f"the screenshots disagree about {name}: "
                f"{', '.join(repr(value) for value in sorted(values))}. They must all be of "
                "one screen state, so the capture is refused",
            )
            continue
        if values:
            resolved[name] = values.pop()
    unresolved = tuple(name for name in OTM_METADATA_FIELDS if resolved[name] is None)
    return VCUBOTMSourceMetadata(**resolved, unresolved_fields=unresolved)


def _merge_vol_type(values: set[str], issues: _Issues) -> str | None:
    """The one ``vol_type`` every screenshot's OCR agrees on, or ``None``.

    ``"Normal Vol Skew"`` and ``"NORMAL VOL SKEW"`` are two OCR passes over
    the same selector, not two screen states, so they are compared the same
    way every other free-text token this parser reads is compared:
    :func:`normalise_text` then ``casefold()``. Two readings that still
    differ after that are genuinely two screen states and still conflict.
    The canonical spelling :data:`NORMAL_VOL_SKEW_TYPE` is preferred whenever
    the normalised value matches it, so the merged result is the same string
    regardless of which screenshot's casing happened to be read first; a
    normalised value this template does not otherwise support is returned
    exactly as read, so :func:`_check_value_semantics` still names what was
    actually seen rather than a value nobody's screenshot showed.
    """

    normalised = {normalise_text(value).casefold() for value in values}
    if len(normalised) > 1:
        issues.block(
            "METADATA_CONFLICT",
            "the screenshots disagree about vol_type: "
            f"{', '.join(repr(value) for value in sorted(values))}. They must all be of one "
            "screen state, so the capture is refused",
        )
        return None
    if normalised == {normalise_text(NORMAL_VOL_SKEW_TYPE).casefold()}:
        return NORMAL_VOL_SKEW_TYPE
    return sorted(values)[0]


def _canonical_strike_label(offset_bp: float | None) -> str:
    """The one spelling this template writes for a column's own offset.

    A pure function of ``offset_bp`` -- never of any screenshot's OCR text
    -- so it is the same string regardless of which screenshot supplied it
    or how that screenshot happened to case ``bps``.
    """

    if offset_bp is None:
        return _ATM_STRIKE_LABEL
    return f"{int(offset_bp)}bps"


def _merge_strike_axis(
    readable: Sequence[VCUBOTMImageRead], issues: _Issues
) -> list[VCUBOTMStrike] | None:
    """The one strike axis every screenshot must agree on, or ``None``.

    Compared by parsed ``offset_bp`` -- the column's real coordinate -- not
    by the header's own OCR text: ``-200bps`` and ``-200BPS`` name the same
    column, and a casing difference between screenshots must not discard an
    otherwise-agreeing axis and its whole merged table. The merged strikes
    carry :func:`_canonical_strike_label`, so the result is the same string
    regardless of which screenshot's casing was read first (Codex review,
    PR #186).
    """

    axes = {
        tuple(strike.offset_bp for strike in read.table.strikes)
        for read in readable
        if read.table is not None
    }
    if len(axes) > 1:
        rendered = sorted(
            " | ".join(_canonical_strike_label(offset) for offset in axis)
            for axis in axes
        )
        issues.block(
            "STRIKE_HEADERS_DISAGREE",
            "the screenshots do not show the same strike columns "
            f"({' vs '.join(repr(axis) for axis in rendered)}), so their rows cannot be "
            "merged onto one strike axis",
        )
        return None
    axis = axes.pop()
    return [
        VCUBOTMStrike(label=_canonical_strike_label(offset), offset_bp=offset)
        for offset in axis
    ]


@dataclass
class _MergedRow:
    """One row as the screenshots seen so far have described it.

    ``origins[i]`` names the screenshot the value now held at column ``i``
    came from, and ``blank_in[i]`` the ones that showed this row with nothing
    there. Both are per *cell* rather than per row, which is what a conflict
    message needs to be able to say: with three screenshots, a cell the first
    left unresolved and the second filled belongs to the second, and naming
    the first would send the trader to an image that never held that value
    (Codex review round 2, PR #186).
    """

    row: VCUBOTMRow
    origins: list[str | None]
    blank_in: list[list[str]]


def _merge_rows(
    readable: Sequence[VCUBOTMImageRead],
    strikes: Sequence[VCUBOTMStrike],
    issues: _Issues,
) -> dict[tuple[str, str], VCUBOTMRow]:
    """Every screenshot's rows, keyed by coordinate, with overlap checked."""

    merged: dict[tuple[str, str], _MergedRow] = {}
    for read in readable:
        assert read.table is not None
        source = read.provenance.source_reference
        for row in read.table.rows:
            key = (row.term, row.tenor)
            held = merged.get(key)
            if held is None:
                merged[key] = _MergedRow(
                    row=row,
                    origins=[source if value is not None else None for value in row.values],
                    blank_in=[[] if value is not None else [source] for value in row.values],
                )
                continue
            merged[key] = _merge_overlapping_row(
                held, row, strikes, issues, source=source
            )
    return {key: held.row for key, held in merged.items()}


def _merge_overlapping_row(
    held: _MergedRow,
    incoming: VCUBOTMRow,
    strikes: Sequence[VCUBOTMStrike],
    issues: _Issues,
    *,
    source: str,
) -> _MergedRow:
    """One row another screenshot also showed.

    Identical readings deduplicate. A cell nothing has read yet is taken from
    the image that reads it, and reported as a warning naming both that image
    and the ones that showed the row without it -- the two do not disagree
    about a value there, one of them simply has none, and taking it is not
    preferring a clearer read over a conflicting one. Two *different* values
    at one coordinate block the whole capture: neither is chosen, neither is
    averaged, and neither is quietly overwritten.
    """

    values: list[float | None] = []
    origins: list[str | None] = []
    blank_in: list[list[str]] = []
    for index, strike in enumerate(strikes):
        first = held.row.values[index]
        second = incoming.values[index]
        origin = held.origins[index]
        blanks = list(held.blank_in[index])

        if first is not None and second is not None:
            # ``!=`` rather than an approximate comparison on purpose: these
            # are two reads of the same drawn glyphs, so anything but the
            # same number means one of them is wrong.
            if first != second:
                issues.block(
                    "OVERLAP_VALUE_CONFLICT",
                    f"{held.row.label} at {strike.label} reads {first!r} in {origin!r} and "
                    f"{second!r} in {source!r}. Two screenshots of one screen cannot hold two "
                    "values for one coordinate, so the whole capture is refused rather than "
                    "one of them chosen",
                    row=held.row.label,
                    strike=strike.label,
                )
            values.append(first)
            origins.append(origin)
            blank_in.append(blanks)
            continue

        if first is None and second is None:
            blanks.append(source)
            values.append(None)
            origins.append(None)
            blank_in.append(blanks)
            continue

        if first is None:
            issues.warn(
                "OVERLAP_PARTIAL_CELL",
                f"{held.row.label} at {strike.label} was read in {source!r} but not in "
                f"{_named(blanks)}; the value that was read is used. Check it against the "
                "screenshot",
                row=held.row.label,
                strike=strike.label,
            )
            values.append(second)
            origins.append(source)
            blank_in.append(blanks)
            continue

        issues.warn(
            "OVERLAP_PARTIAL_CELL",
            f"{held.row.label} at {strike.label} was read in {origin!r} but not in "
            f"{source!r}; the value that was read is used. Check it against the screenshot",
            row=held.row.label,
            strike=strike.label,
        )
        blanks.append(source)
        values.append(first)
        origins.append(origin)
        blank_in.append(blanks)

    return _MergedRow(
        row=VCUBOTMRow(
            term=held.row.term, tenor=held.row.tenor, values=tuple(values)
        ),
        origins=origins,
        blank_in=blank_in,
    )


def _check_coverage_chain(readable: Sequence[VCUBOTMImageRead], issues: _Issues) -> None:
    """Refuse a set of screenshots that cannot prove it skipped no rows.

    Each screenshot shows a *contiguous* slice of one long table, so two
    slices that share a row are provably contiguous together. Two that share
    none are not: whatever sits between them may never have been captured,
    and nothing in the images themselves can say. Overlapping is what an
    operator is asked to do, so this refuses the one case where a row could
    have gone missing without anyone noticing.
    """

    if len(readable) < 2:
        return
    slices = sorted(
        (
            (
                min(row.sort_key for row in read.table.rows),
                {(row.term, row.tenor) for row in read.table.rows},
                read.provenance.source_reference,
            )
            for read in readable
            if read.table is not None
        ),
        key=lambda item: item[0],
    )
    covered = set(slices[0][1])
    previous = slices[0][2]
    for _start, rows, reference in slices[1:]:
        if not rows & covered:
            issues.block(
                "IMAGE_COVERAGE_GAP",
                f"{reference!r} shares no Term x Tenor row with {previous!r} or any earlier "
                "screenshot, so nothing proves the rows between them were captured. Re-take "
                "the screenshots so each one overlaps the previous by at least one row",
            )
            return
        covered |= rows
        previous = reference


def _coverage(
    reads: Sequence[VCUBOTMImageRead], merged_rows: dict[tuple[str, str], VCUBOTMRow]
) -> tuple[VCUBOTMSourceCoverage, ...]:
    """What each screenshot contributed, and where it overlapped another."""

    seen: dict[tuple[str, str], int] = {}
    for read in reads:
        if read.table is None:
            continue
        for row in read.table.rows:
            key = (row.term, row.tenor)
            seen[key] = seen.get(key, 0) + 1
    return tuple(
        VCUBOTMSourceCoverage(
            source_reference=read.provenance.source_reference,
            source_image_sha256=read.provenance.source_image_sha256,
            row_labels=()
            if read.table is None
            else tuple(row.label for row in read.table.rows),
            shared_row_labels=()
            if read.table is None
            else tuple(
                row.label for row in read.table.rows if seen[(row.term, row.tenor)] > 1
            ),
        )
        for read in reads
    )


def _capture(
    reads: Sequence[VCUBOTMImageRead],
    metadata: VCUBOTMSourceMetadata,
    table: VCUBOTMTable | None,
    coverage: tuple[VCUBOTMSourceCoverage, ...],
    issues: _Issues,
) -> VCUBOTMCapture:
    return VCUBOTMCapture(
        sources=tuple(read.provenance for read in reads),
        metadata=metadata,
        table=table,
        coverage=coverage,
        blocking_errors=tuple(issues.blocking),
        warnings=tuple(issues.warnings),
    )
