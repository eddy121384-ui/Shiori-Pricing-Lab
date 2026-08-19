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

Nothing in this module imports :mod:`shiori_pricing_lab.pricing`, and
nothing here interpolates, converts, or re-bases a vol.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    METADATA_FIELDS,
    VCUBATMCapture,
    VCUBATMGrid,
    VCUBCaptureIssue,
    VCUBCaptureProvenance,
    VCUBSourceMetadata,
    VCUBTextToken,
)
from shiori_pricing_lab.products.enums import Currency

#: The tab whose layout this template knows. The prototype recognises this
#: one VCUB screen and nothing else -- it is not a generic Bloomberg reader.
ATM_SWAPTIONS_TAB = "ATM Swaptions"

#: The corner cell of the VCUB ATM matrix: the tenor headers share its text
#: line, the expiry labels share its column.
EXPIRY_ANCHOR = "Expiry"

# --- Geometry tolerances, all expressed as fractions of measured pitch ----
#
# A value token whose centre lands within this fraction of the local pitch
# of a band boundary could belong to either neighbour, so it is refused
# rather than rounded.
_BOUNDARY_AMBIGUITY_FRACTION = 0.12
# Two tokens belong to the same text line when their vertical spans overlap
# by at least this fraction of the shorter one's height.
_LINE_OVERLAP_FRACTION = 0.5
# Row/column pitch must stay within this multiple of the median pitch. A
# label the reader dropped shows up as one ~2x gap, which is exactly the
# case that could otherwise push a value one band out.
_PITCH_IRREGULARITY_MULTIPLE = 1.75

_TENOR_LABEL_RE = re.compile(r"^(\d{1,3})\s*(Mo|Yr|Wk|Dy|M|Y|W|D)$", re.IGNORECASE)
# Deliberately no exponent form and no bare "." -- "1e999" and "NaN" must be
# refused as malformed rather than reaching float().
_NUMERIC_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")
_NON_FINITE_TEXTS = frozenset({"NAN", "-NAN", "+NAN", "INF", "-INF", "+INF", "INFINITY",
                               "-INFINITY", "+INFINITY"})
_DATE_RE = re.compile(r"\b\d{2}/\d{2}/(?:\d{2}|\d{4})\b")
_SIDE_TEXTS = {"BID": "Bid", "MID": "Mid", "ASK": "Ask"}
_VOL_TYPE_RE = re.compile(
    r"\b(Normal|Black|Lognormal|Shifted Lognormal|SABR)\s+Vol(\s*\([^)]*\))?", re.IGNORECASE
)
_KNOWN_SOURCE_TEXTS = frozenset({"BVOL", "CMPN", "BGN"})
# Bloomberg writes every clickable action on a screen as "N) Label". The
# first live capture read Source as "BVOL 16) Use This Contributor in
# Configuration" because nothing stopped the value run at the action that
# followed it, so these markers are treated as field boundaries.
# The whitespace before ")" is what a *split* detection leaves behind: the
# reader may return "16", ")" and "Use" as separate boxes, and joining a line
# puts a space between them. Without it the live screen's Source read back as
# "BVOL 16 ) Use This Contributor in Configuration" -- the same contamination
# this boundary was added for, arriving through a different grouping (Codex
# review, PR #182).
_MENU_ACTION_RE = re.compile(r"\s*\b\d{1,3}\s*\)\s+")
# The curve/config *name*, anchored on the word Bloomberg always ends it
# with, and reaching back over the words that precede it so it captures
# "USD RFR BVOL Cube (Default)" without swallowing the currency and index
# selectors drawn to its left on the same line. The character class covers
# the punctuation a curve name can legitimately carry -- "RFR/OIS" -- because
# a class that stopped at the slash would begin matching *after* it.
# Matched as a word anywhere in a detection, not only at its end: once a
# reader hands back the whole field in one box the anchor sits mid-text.
_CUBE_ANCHOR_RE = re.compile(r"\bcube\b", re.IGNORECASE)
# Matched against the candidate's own text, not only against a token that
# happens to hold the marker alone: the reader may return "9) Analyze Cube"
# as a single detection, and an exact-token test would let that menu action
# through as a configuration name.
_MENU_MARKER_RE = re.compile(r"^\d{1,3}\s*\)")
# What a configuration name may be made of. Generous on purpose, and it only
# ever decides whether the field resolves: a character not listed here makes
# the capture leave Curve/Config unresolved, never mis-sliced. Seven rounds of
# review on this field all shared one shape -- a guess about where a name ends
# produced a *wrong* name -- so the guess now costs a resolution instead.
#
# The widget-separator test below is built from the *same* string, so the two
# are complements by construction. Kept as two independent enumerations they
# disagreed, and the disagreement truncated a name: a name could carry "+"
# while a trailing "+" also ended a widget, so `USD RFR+ OIS BVOL Cube
# (Default)` split into boxes stored `OIS BVOL Cube (Default)` -- a real
# Bloomberg name with its head cut off, presented as resolved (Codex review,
# PR #182).
_NAME_CHARACTERS = r"0-9A-Za-z ()/&._+:-"
_NAME_PLAUSIBLE_RE = re.compile(rf"^[{_NAME_CHARACTERS}]+$")
# A widget's text ends where a separator glyph does -- the dropdown caret
# Bloomberg draws on the right of each selector. Anything a name can carry is
# still part of the value, parentheses included: a reader that returns
# "(Default)" as "(", "Default", ")" would otherwise have its lone "(" read as
# the end of a widget, cutting the name off from its own suffix.
#
# A caret the reader hands back as a name character -- "v", "." -- is not seen
# as a boundary here, and the walk then runs to the start of its line, where
# the field goes unresolved. That direction costs a resolution; the direction
# this rule refuses to take costs correctness.
_WIDGET_SEPARATOR_TAIL_RE = re.compile(rf"[^{_NAME_CHARACTERS}]$")
# Two tokens belong to the same widget while the gap between them stays
# within a normal word space. Measured in character widths taken from the
# tokens themselves, so it scales with font size and DPI like every other
# threshold in this module.
_FIELD_GAP_CHARACTER_WIDTHS = 2.0
# Two tokens are two words -- rather than one word the reader cut in half --
# once the gap between them is a real fraction of a character. Boxes are drawn
# tight around their glyphs, so a split inside a word leaves them touching,
# while the live header's word spaces run about 0.7 character widths: the
# threshold sits well clear of both.
_SPLIT_WORD_GAP_CHARACTER_WIDTHS = 0.25
# Closed vocabularies are matched against alphanumeric runs rather than
# whitespace-separated words: the live screen glues a dropdown caret onto
# the value it belongs to, so "USD" and "Mid" never appeared as bare words
# and both fields read Unresolved on a screen that plainly showed them.
_ALPHANUMERIC_RUN_RE = re.compile(r"[0-9A-Za-z]+")
# Field labels VCUB draws on its own header lines. A labelled value run ends
# at the next one of these, so two fields sharing a line stay separate.
_FIELD_LABELS = frozenset(
    {"type", "source", "side", "date", "currency", "expiry", "tenor", "strike", "curve"}
)

_UNIT_NOMINAL_DAYS = {"D": 1.0, "W": 7.0, "M": 30.0, "Y": 365.0}


class _Issues:
    """Collects blocking errors and warnings without letting them mix up."""

    def __init__(self) -> None:
        self.blocking: list[VCUBCaptureIssue] = []
        self.warnings: list[VCUBCaptureIssue] = []

    def block(self, code: str, message: str, *, expiry=None, tenor=None) -> None:
        self.blocking.append(VCUBCaptureIssue(code, message, expiry=expiry, tenor=tenor))

    def warn(self, code: str, message: str, *, expiry=None, tenor=None) -> None:
        self.warnings.append(VCUBCaptureIssue(code, message, expiry=expiry, tenor=tenor))


def _normalise(text: str) -> str:
    return " ".join(text.split())


def is_tenor_label(text: str) -> bool:
    """Whether ``text`` reads as a VCUB tenor/expiry bucket label such as ``18Mo``."""

    return _TENOR_LABEL_RE.match(_normalise(text)) is not None


def tenor_label_nominal_days(text: str) -> float | None:
    """Nominal-day ordering key for a bucket label, or ``None`` if it is not one.

    Used only to check that an axis the geometry already ordered is *also*
    monotonic in tenor. It is a consistency check on the read, never a
    day-count, never a year fraction, and never an input to any calculation.
    """

    match = _TENOR_LABEL_RE.match(_normalise(text))
    if match is None:
        return None
    unit = match.group(2)[0].upper()
    return int(match.group(1)) * _UNIT_NOMINAL_DAYS[unit]


def _parse_cell_number(text: str) -> tuple[float | None, str | None]:
    """Return ``(value, failure_code)`` for one cell token's text."""

    normalised = _normalise(text)
    if normalised.upper() in _NON_FINITE_TEXTS:
        return None, "NON_FINITE_NUMERIC_CELL"
    if _NUMERIC_RE.match(normalised) is None:
        return None, "MALFORMED_NUMERIC_CELL"
    try:
        value = float(normalised.replace(",", ""))
    except ValueError:
        return None, "MALFORMED_NUMERIC_CELL"
    if not math.isfinite(value):
        return None, "NON_FINITE_NUMERIC_CELL"
    return value, None


@dataclass(frozen=True)
class _TextLine:
    """Tokens that share one visual text line, ordered left to right."""

    tokens: tuple[VCUBTextToken, ...]

    @property
    def top(self) -> float:
        return min(token.top for token in self.tokens)

    @property
    def bottom(self) -> float:
        return max(token.bottom for token in self.tokens)

    def joined_text(self) -> str:
        """This line's text, spaced as the screen drew it.

        Spacing comes from the boxes, never from the fact that the reader
        returned two of them. Joining unconditionally put a space wherever a
        word had been split, and every metadata rule reads this text: a
        ``BVOL`` cut into ``BV`` and ``OL`` stored Source as ``BV OL``, and a
        ``16)`` marker cut into ``1`` and ``6)`` stopped reading as a menu
        boundary at all, leaving its ``1`` attached to Source (Codex review,
        PR #182). Identical pixels must not produce different evidence
        because of how the reader boxed them.
        """

        return _join_by_geometry(self.tokens)


def _group_into_lines(tokens: Sequence[VCUBTextToken]) -> list[_TextLine]:
    """Group tokens into visual text lines by vertical overlap.

    Overlap is measured relative to the shorter token, so a tall token (a
    boxed header) still joins the line of the short digits beside it, and
    the grouping scales with the image because nothing here is an absolute
    pixel threshold.
    """

    lines: list[list[VCUBTextToken]] = []
    for token in sorted(tokens, key=lambda item: (item.y_center, item.left)):
        for line in lines:
            top = min(existing.top for existing in line)
            bottom = max(existing.bottom for existing in line)
            overlap = min(bottom, token.bottom) - max(top, token.top)
            shorter = min(bottom - top, token.height)
            if shorter > 0 and overlap / shorter >= _LINE_OVERLAP_FRACTION:
                line.append(token)
                break
        else:
            lines.append([token])
    return [
        _TextLine(tuple(sorted(line, key=lambda item: item.left)))
        for line in sorted(lines, key=lambda line: min(item.top for item in line))
    ]


def _band_edges(centres: Sequence[float], fallback_extent: float) -> tuple[list[float], float]:
    """Return ``(boundaries, outer_half_pitch)`` for bands around ``centres``.

    ``boundaries[i]`` separates band ``i`` from band ``i + 1``; the outer
    edges of the first and last band sit ``outer_half_pitch`` beyond their
    centres. With a single band there is no measurable pitch, so the band's
    own detected extent is used instead.
    """

    if len(centres) == 1:
        return [], fallback_extent
    pitches = [centres[index + 1] - centres[index] for index in range(len(centres) - 1)]
    boundaries = [
        (centres[index] + centres[index + 1]) / 2.0 for index in range(len(centres) - 1)
    ]
    return boundaries, min(pitches) / 2.0


def _assign_band(
    position: float,
    centres: Sequence[float],
    boundaries: Sequence[float],
    outer_half_pitch: float,
) -> tuple[int | None, bool]:
    """Return ``(band_index, ambiguous)`` for ``position``.

    ``band_index`` is ``None`` when the position falls outside the outer
    edges entirely. ``ambiguous`` is ``True`` when it sits close enough to a
    boundary that either neighbour is a defensible answer -- the caller must
    refuse it rather than pick one.

    Each boundary is tested against the pitch between **its own** two
    centres, never against a pitch borrowed from elsewhere on the axis
    (Codex review, PR #182). On an axis whose bands are not all the same
    width, a tolerance taken from the assigned band's narrower neighbour is
    too small at a wide boundary, so a position that is geometrically
    ambiguous there would be handed a confident cell -- exactly the
    wrong-cell assignment this module exists to prevent.
    """

    if position < centres[0] - outer_half_pitch or position > centres[-1] + outer_half_pitch:
        return None, False
    index = 0
    ambiguous = False
    for boundary_index, boundary in enumerate(boundaries):
        if position > boundary:
            index = boundary_index + 1
        pitch = centres[boundary_index + 1] - centres[boundary_index]
        if abs(position - boundary) <= pitch * _BOUNDARY_AMBIGUITY_FRACTION:
            ambiguous = True
    return index, ambiguous


def _spans_are_orderable(
    first_low: float, first_high: float, second_low: float, second_high: float
) -> bool:
    """Whether two label boxes are unambiguously ordered along one axis.

    Overlap alone does not make an order ambiguous, which is what the first
    live Bloomberg capture proved: on a real VCUB grid the rows are dense
    enough that adjacent expiry labels' detected boxes bleed into one
    another by a pixel or two while their centres stay a full row apart.
    Refusing any overlap failed that capture closed on ``2Mo``/``3Mo``
    despite their order being perfectly legible.

    What *is* ambiguous is two labels close enough to be reading as one
    visual line -- exactly the condition :func:`_group_into_lines` uses to
    decide two tokens share a line -- so the two tests use the same
    fraction, and two labels the grouper would have merged are still
    refused here.

    This never widens what a value can be assigned to: the axis order comes
    from the centres, every band boundary comes from the centres, and so
    does every cell placement. Only the box-touching test is relaxed.
    """

    overlap = first_high - second_low
    if overlap <= 0:
        return True
    shorter = min(first_high - first_low, second_high - second_low)
    if shorter <= 0:
        return False
    return overlap / shorter < _LINE_OVERLAP_FRACTION


def _check_pitch_regularity(
    centres: Sequence[float], issues: _Issues, *, code: str, axis: str
) -> None:
    if len(centres) < 3:
        return
    pitches = [centres[index + 1] - centres[index] for index in range(len(centres) - 1)]
    narrowest = min(pitches)
    widest = max(pitches)
    if narrowest <= 0:
        return
    # Measured against the *narrowest* gap, not the median (Codex review,
    # PR #182). A dropped label leaves one gap about twice its neighbours,
    # and with only two gaps to compare -- a four-label axis that lost one
    # interior label -- any median is dragged up by the wide gap itself, so
    # the ratio could never trip. The narrowest gap is the axis's own
    # unambiguous unit and cannot be inflated by the very anomaly being
    # looked for.
    if widest > narrowest * _PITCH_IRREGULARITY_MULTIPLE:
        issues.block(
            code,
            f"the {axis} spacing is irregular (widest gap {widest:.1f}px against a narrowest of "
            f"{narrowest:.1f}px), which is what a {axis[:-1]} the reader missed looks like; "
            "values around that gap could be one band out, so the capture is refused",
        )


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


def _field_segments(text: str) -> list[tuple[str, bool]]:
    """Split one header line into ``(segment, is_menu_action)`` pairs.

    A Bloomberg header line packs several independent widgets side by side,
    and the reader returns them as one line. Splitting at the ``N)`` action
    markers separates a field's value from the menu entry drawn next to it,
    which is what stopped ``Source`` from swallowing the whole rest of its
    line on the first live capture.
    """

    boundaries = list(_MENU_ACTION_RE.finditer(text))
    segments: list[tuple[str, bool]] = []
    cursor = 0
    for boundary in boundaries:
        segments.append((text[cursor : boundary.start()], cursor != 0))
        cursor = boundary.end()
    segments.append((text[cursor:], bool(boundaries)))
    return [(segment.strip(), is_menu) for segment, is_menu in segments if segment.strip()]


def _trim_ui_glyphs(text: str) -> str:
    """Drop the dropdown carets and separators a widget's text carries.

    Only characters that cannot begin or end a real value are removed, so a
    parenthesised suffix such as ``Normal Vol (OIS)`` survives intact.
    """

    while text and not (text[-1].isalnum() or text[-1] == ")"):
        text = text[:-1]
    while text and not (text[0].isalnum() or text[0] == "("):
        text = text[1:]
    return text.strip()


def _resolve_metadata(lines: Sequence[_TextLine], tab_resolved: bool) -> VCUBSourceMetadata:
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

    line_texts = [line.joined_text() for line in lines]
    segments = [segment for text in line_texts for segment in _field_segments(text)]
    value_segments = [segment for segment, is_menu in segments if not is_menu]
    runs = [
        run for segment, _is_menu in segments for run in _ALPHANUMERIC_RUN_RE.findall(segment)
    ]

    resolved: dict[str, str | None] = dict.fromkeys(METADATA_FIELDS, None)

    resolved["currency"] = _unique_member(runs, set(Currency))
    # Only a *value* segment can carry the curve name: "Analyze Cube" is a
    # menu action, and counting it made the live screen's two "Cube"
    # occurrences ambiguous and left this field unresolved.
    resolved["curve_config"] = _unique_curve_config(lines)
    side = _unique_member([run.upper() for run in runs], set(_SIDE_TEXTS))
    resolved["side"] = None if side is None else _SIDE_TEXTS[side]
    resolved["quote_date"] = _unique_match(line_texts, _DATE_RE)
    if tab_resolved:
        resolved["tab"] = ATM_SWAPTIONS_TAB
    resolved["vol_type"] = _labelled_value(value_segments, "Type") or _unique_match(
        value_segments, _VOL_TYPE_RE
    )
    resolved["source"] = _labelled_value(value_segments, "Source") or _unique_member(
        [run.upper() for run in runs], _KNOWN_SOURCE_TEXTS
    )

    unresolved = tuple(name for name in METADATA_FIELDS if resolved[name] is None)
    return VCUBSourceMetadata(**resolved, unresolved_fields=unresolved)


def _unique_member(candidates: Sequence[str], allowed: set[str]) -> str | None:
    found = {candidate for candidate in candidates if candidate in allowed}
    return found.pop() if len(found) == 1 else None


def _character_width(token: VCUBTextToken) -> float:
    return token.width / max(len(_normalise(token.text)), 1)


def _starts_a_new_widget(left: VCUBTextToken, right: VCUBTextToken) -> bool:
    """Whether ``right`` begins a different widget from ``left``.

    Two real boundary signals, both read off the boxes rather than guessed
    from the joined text: a separator glyph closing the left token (the
    dropdown caret), or a gap wider than a word space between them.
    """

    if _WIDGET_SEPARATOR_TAIL_RE.search(_normalise(left.text)):
        return True
    gap = right.left - left.right
    scale = max(_character_width(left), _character_width(right))
    return scale > 0 and gap > scale * _FIELD_GAP_CHARACTER_WIDTHS


def _curve_config_in_line(line: _TextLine) -> str | None:
    """The curve/config name on one header line, whole, or ``None``.

    Read from the *tokens*, anchored on the word Bloomberg ends the name
    with and extended leftwards until a real widget boundary. Earlier
    versions searched the joined text for a bounded phrase, which meant
    every character not enumerated in the name's character class became a
    false boundary: ``USD RFR+OIS BVOL Cube`` came back as ``OIS BVOL
    Cube``, a partial Bloomberg name presented as resolved metadata (Codex
    review, PR #182). A token is atomic here, so a match can no longer
    restart in the middle of one.
    """

    tokens = line.tokens
    anchors = [
        index
        for index, token in enumerate(tokens)
        if _CUBE_ANCHOR_RE.search(_normalise(token.text))
    ]
    if len(anchors) != 1:
        return None  # no name here, or two: either way not this line's value
    anchor = anchors[0]

    last = _extend_over_parenthetical(tokens, anchor)
    if last is None:
        return None  # a suffix opened and never closed: the name would be partial

    first = anchor
    while first > 0 and not _starts_a_new_widget(tokens[first - 1], tokens[first]):
        first -= 1

    # "9) Analyze Cube" is a menu action, not a configuration value -- whether
    # the reader put the marker in its own box or in the same one as the text.
    if first > 0 and _MENU_MARKER_RE.match(_normalise(tokens[first - 1].text)):
        return None

    # A boundary was *observed* to the left only when the walk stopped at
    # one. Running out of tokens proves nothing, so the field is unresolved
    # there (Codex review, PR #182): the selectors standing beside it can
    # arrive with their carets dropped, and `USD RFR USD RFR BVOL Cube
    # (Default)` then reads as a name on a screen displaying only half of
    # it.
    #
    # An earlier version of this rule still trusted a *single-word* leading
    # box, reasoning that separately detected widgets keep their own boxes
    # so the gap between them would be caught above. The live header
    # disproves that: its words sit 5px apart against a 14px threshold, so
    # on real geometry a gap never marks a widget boundary and only the
    # caret does. One word per detection therefore stored the same wrong
    # name.
    if first == 0:
        return None

    name = _trim_ui_glyphs(_join_name(tokens[first : last + 1]))
    if not name or _MENU_MARKER_RE.match(name):
        return None

    # Everything below refuses rather than repairs. A detection can hold this
    # field and its neighbour at once, and when the reader drops the caret
    # between them nothing in the text says where one ends -- so a candidate
    # is only trusted when it accounts for the whole of what it was read from.
    if not _NAME_PLAUSIBLE_RE.match(name):
        return None  # a separator glyph survives inside it: two widgets, not one
    if not (name.casefold().endswith("cube") or name.endswith(")")):
        return None  # text trails the name: the next widget came along with it
    return name


def _parenthesis_delta(token: VCUBTextToken) -> int:
    text = _normalise(token.text)
    return text.count("(") - text.count(")")


def _extend_over_parenthetical(
    tokens: Sequence[VCUBTextToken], anchor: int
) -> int | None:
    """Index of the name's last token, consuming a whole ``(...)`` suffix.

    The reader may return ``(Default)`` as one box or as ``(``, ``Default``
    and ``)``. Taking a single following token dropped the suffix in the
    split case and stored the shortened name as resolved metadata (Codex
    review, PR #182), so the suffix is followed until its parentheses
    balance. A suffix that opens and never closes inside this widget yields
    ``None``: better to leave the field unresolved than to record a name
    missing what the screen displayed.
    """

    index = anchor
    depth = _parenthesis_delta(tokens[anchor])
    while True:
        if depth <= 0:
            following = index + 1
            begins_suffix = (
                depth == 0
                and following < len(tokens)
                and _normalise(tokens[following].text).startswith("(")
                and not _starts_a_new_widget(tokens[index], tokens[following])
            )
            if not begins_suffix:
                return index
        following = index + 1
        if following >= len(tokens) or _starts_a_new_widget(tokens[index], tokens[following]):
            return None
        index = following
        depth += _parenthesis_delta(tokens[index])


def _separated_by_a_space(left: VCUBTextToken, right: VCUBTextToken) -> bool:
    """Whether the screen drew a space between two tokens of one name."""

    scale = max(_character_width(left), _character_width(right))
    return scale > 0 and (right.left - left.right) > scale * _SPLIT_WORD_GAP_CHARACTER_WIDTHS


def _join_by_geometry(tokens: Sequence[VCUBTextToken]) -> str:
    """Join tokens, inserting a space only where their boxes show one."""

    text = _normalise(tokens[0].text)
    # Deliberately ragged: the last token has no successor to pair with.
    for left, right in zip(tokens, tokens[1:], strict=False):
        text += (" " if _separated_by_a_space(left, right) else "") + _normalise(right.text)
    return text


def _join_name(tokens: Sequence[VCUBTextToken]) -> str:
    """Join a name's tokens, closing up spacing the reader introduced.

    Whether a space goes between two tokens is read off their boxes, never
    assumed. Joining unconditionally inserted a character the screen never
    displayed: identical geometry stored ``RFR+OIS`` when the reader returned
    one box and ``RFR+ OIS`` when it split the word in two (Codex review,
    PR #182), so the recorded Bloomberg name depended on the reader's
    grouping.
    """

    text = _join_by_geometry(tokens)
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+\)", ")", text)


def _unique_curve_config(lines: Sequence[_TextLine]) -> str | None:
    """The one curve/config name on the screen, whole, or nothing."""

    found = {
        name for line in lines if (name := _curve_config_in_line(line)) is not None
    }
    return found.pop() if len(found) == 1 else None


def _unique_match(line_texts: Sequence[str], pattern: re.Pattern[str]) -> str | None:
    found = {
        _trim_ui_glyphs(match.group(0))
        for text in line_texts
        for match in pattern.finditer(text)
    }
    found.discard("")
    return found.pop() if len(found) == 1 else None


def _unique_line_containing(line_texts: Sequence[str], needle: str) -> str | None:
    found = [text for text in line_texts if needle in text.casefold()]
    return found[0] if len(found) == 1 else None


def _labelled_value(line_texts: Sequence[str], label: str) -> str | None:
    """Return the value that follows a unique ``label`` on one header line.

    VCUB puts several labelled fields side by side on one line, so the value
    run stops at the next field label rather than swallowing the rest of the
    line. Two matching labels means the parser cannot tell which one
    describes this grid, so the field stays unresolved.
    """

    label_pattern = re.compile(rf"\b{re.escape(label)}\b\s*:?\s*(.+)$", re.IGNORECASE)
    matches: list[str] = []
    for text in line_texts:
        found = label_pattern.search(text)
        if found is None:
            continue
        value_words: list[str] = []
        for word in found.group(1).split():
            if word.rstrip(":").casefold() in _FIELD_LABELS:
                break
            value_words.append(word)
        if value_words:
            matches.append(_trim_ui_glyphs(" ".join(value_words)))
    unique = {match for match in matches if match}
    return unique.pop() if len(unique) == 1 else None


# --------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------


def _find_anchor_line(
    lines: Sequence[_TextLine], issues: _Issues
) -> tuple[_TextLine, VCUBTextToken] | None:
    anchors = [
        (line, token)
        for line in lines
        for token in line.tokens
        if _normalise(token.text).casefold() == EXPIRY_ANCHOR.casefold()
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
    anchor_line: _TextLine, anchor: VCUBTextToken, issues: _Issues
) -> list[VCUBTextToken] | None:
    right_of_anchor = [token for token in anchor_line.tokens if token.left >= anchor.right]
    headers = [token for token in right_of_anchor if is_tenor_label(token.text)]
    strays = [token for token in right_of_anchor if not is_tenor_label(token.text)]
    for stray in strays:
        issues.block(
            "TENOR_HEADER_UNEXPECTED_TOKEN",
            f"{_normalise(stray.text)!r} sits on the tenor header line but does not read as a "
            "swap tenor, so the column layout cannot be trusted",
        )
    if not headers:
        issues.block(
            "TENOR_HEADERS_UNRESOLVED",
            f"no swap-tenor headers were found to the right of {EXPIRY_ANCHOR!r}",
        )
        return None

    headers.sort(key=lambda token: token.x_center)
    labels = [_normalise(token.text) for token in headers]
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
        if not _spans_are_orderable(left.left, left.right, right.left, right.right):
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
    _check_pitch_regularity(
        [token.x_center for token in headers],
        issues,
        code="COLUMN_PITCH_IRREGULAR",
        axis="columns",
    )
    return headers


def _resolve_expiry_labels(
    lines: Sequence[_TextLine],
    anchor_line: _TextLine,
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
        for line in lines
        if line.top > anchor_line.bottom
        for token in line.tokens
        if left_bound <= token.x_center < first_column_left_edge
    ]
    labels_tokens = [token for token in candidates if is_tenor_label(token.text)]
    for stray in candidates:
        if stray in labels_tokens:
            continue
        issues.warn(
            "UNRECOGNISED_ROW_LABEL_TOKEN",
            f"{_normalise(stray.text)!r} sits in the {EXPIRY_ANCHOR} column but does not read "
            "as an expiry bucket; it was not treated as a row",
        )
    if not labels_tokens:
        issues.block(
            "EXPIRY_LABELS_UNRESOLVED",
            f"no expiry labels were found below the {EXPIRY_ANCHOR!r} anchor",
        )
        return None

    labels_tokens.sort(key=lambda token: token.y_center)
    labels = [_normalise(token.text) for token in labels_tokens]
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
        if not _spans_are_orderable(upper.top, upper.bottom, lower.top, lower.bottom):
            issues.block(
                "EXPIRY_ROWS_NOT_ORDERABLE",
                f"expiry labels {labels[index]!r} and {labels[index + 1]!r} overlap vertically "
                "by enough to read as one row, so their top-to-bottom order is not unambiguous",
            )
            return None
    _check_monotonic_labels(
        labels, issues, code="EXPIRY_LABELS_NOT_MONOTONIC", axis="expiry labels"
    )
    _check_pitch_regularity(
        [token.y_center for token in labels_tokens],
        issues,
        code="ROW_PITCH_IRREGULAR",
        axis="rows",
    )
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
    lines = _group_into_lines(tokens)

    tab_matches = _unique_line_containing(
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
    column_boundaries, column_outer = _band_edges(
        column_centres, max(token.width for token in headers)
    )
    first_column_left_edge = column_centres[0] - column_outer

    label_tokens = _resolve_expiry_labels(
        lines, anchor_line, anchor, first_column_left_edge, issues
    )
    if label_tokens is None:
        return _capture(provenance, metadata, None, issues)

    row_centres = [token.y_center for token in label_tokens]
    row_boundaries, row_outer = _band_edges(
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
    # confirmable. Since ``_check_pitch_regularity`` has already refused
    # anything wider than ``narrowest * _PITCH_IRREGULARITY_MULTIPLE``, that
    # product is exactly the furthest a legitimate next row could sit.
    # With a single resolved row there is no pitch to measure at all:
    # ``_band_edges`` falls back to the surviving label's own height, which no
    # invariant relates to the missing row's spacing, so a window derived from
    # it can stop short of an orphaned row and wave a whole row away (Codex
    # review round 4, PR #182). When the pitch is unmeasurable the row axis
    # therefore adopts the column axis's rule instead -- refuse a stray number
    # at any distance -- rather than trusting a window it cannot size.
    row_pitch_is_measurable = len(row_centres) > 1
    missing_row_zone = (
        row_outer * 2.0 * _PITCH_IRREGULARITY_MULTIPLE if row_pitch_is_measurable else None
    )
    first_row_top_edge = row_centres[0] - row_outer
    last_row_bottom_edge = row_centres[-1] + row_outer

    expiry_labels = tuple(_normalise(token.text) for token in label_tokens)
    tenor_labels = tuple(_normalise(token.text) for token in headers)

    header_ids = {id(token) for token in headers} | {id(anchor)}
    label_ids = {id(token) for token in label_tokens}

    # Every token that lands on an intersection is collected first and only
    # then reduced to a value: when two tokens land on the same one, *neither*
    # is used, which a "first write wins" placement could not express.
    placed: dict[tuple[int, int], list[float]] = {}
    for token in tokens:
        if id(token) in header_ids or id(token) in label_ids:
            continue
        row_index, row_ambiguous = _assign_band(
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
            if near_a_missing_edge_row and _parse_cell_number(token.text)[0] is not None:
                issues.block(
                    "NUMERIC_TOKEN_OUTSIDE_ROWS",
                    f"the number {_normalise(token.text)!r} sits one row beyond the resolved "
                    "expiry rows, which is where a row label the reader missed would be; it "
                    "cannot be placed, so the grid may be incomplete",
                )
            continue

        column_index, column_ambiguous = _assign_band(
            token.x_center, column_centres, column_boundaries, column_outer
        )
        if column_index is None:
            # Beside the matrix. Ordinary chrome (a caption, a scrollbar
            # legend) is ignored, but a *number* the template cannot place is
            # exactly the structurally ambiguous case: it may well be a value
            # from a column the reader failed to resolve, so it is reported
            # rather than dropped.
            if _parse_cell_number(token.text)[0] is not None:
                issues.block(
                    "NUMERIC_TOKEN_OUTSIDE_COLUMNS",
                    f"the number {_normalise(token.text)!r} sits on a matrix row but outside "
                    "every resolved tenor column, so it cannot be placed",
                )
            continue

        expiry = expiry_labels[row_index]
        tenor = tenor_labels[column_index]
        if row_ambiguous or column_ambiguous:
            axis = "row" if row_ambiguous else "column"
            issues.block(
                "CELL_POSITION_AMBIGUOUS",
                f"{_normalise(token.text)!r} sits on a {axis} boundary and could belong to more "
                f"than one {axis}; it is left unresolved rather than assigned to "
                f"{expiry} x {tenor}",
                expiry=expiry,
                tenor=tenor,
            )
            continue

        value, failure_code = _parse_cell_number(token.text)
        if failure_code is not None:
            issues.block(
                failure_code,
                f"{expiry} x {tenor} reads {_normalise(token.text)!r}, which is not a usable "
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
