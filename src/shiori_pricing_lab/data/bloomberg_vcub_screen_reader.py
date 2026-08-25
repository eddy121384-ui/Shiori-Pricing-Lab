"""What every VCUB screen reader shares: text lines, band geometry, and the
header widgets Bloomberg draws the same way on every tab.

Extracted unchanged from ``bloomberg_vcub_atm_template`` when Issue #185
added a second screen (OTM Swaptions / SABR). Both templates read the same
kind of picture -- a header strip of labelled selectors above a matrix whose
rows and columns are named by their own labels -- so the rules that decide
*where a token is* and *what a widget displayed* belong to neither tab in
particular. Nothing here knows which screen it is reading: it never names a
tab, an axis, or a metadata field that only one template resolves.

**Still not a coordinate table.** Not one absolute pixel constant survived
the move, and none may be added. Every threshold below is a fraction of a
pitch, a character width, or a box height measured from the tokens
themselves, which is what keeps a capture tolerant of re-cropping, window
moves, and DPI/scale changes: those translate and scale every token
together and leave the derived geometry unchanged.

**Still fail-closed.** :func:`assign_band` reports an ambiguous position
rather than rounding it to the nearer neighbour,
:func:`pitch_irregularity_message` describes the gap a dropped label leaves
instead of closing it, and every text rule here answers ``None`` where the
boxes do not settle the question. A caller turns those answers into blocking
errors; none of them is ever repaired here.

Nothing in this module imports :mod:`shiori_pricing_lab.pricing`, and
nothing here interpolates, converts, or re-bases a vol.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from shiori_pricing_lab.data.bloomberg_vcub_capture import VCUBTextToken

# --- Geometry tolerances, all expressed as fractions of measured pitch ----
#
# A value token whose centre lands within this fraction of the local pitch
# of a band boundary could belong to either neighbour, so it is refused
# rather than rounded.
BOUNDARY_AMBIGUITY_FRACTION = 0.12
# Two tokens belong to the same text line when their vertical spans overlap
# by at least this fraction of the shorter one's height.
LINE_OVERLAP_FRACTION = 0.5
# Row/column pitch must stay within this multiple of the median pitch. A
# label the reader dropped shows up as one ~2x gap, which is exactly the
# case that could otherwise push a value one band out.
PITCH_IRREGULARITY_MULTIPLE = 1.75

_TENOR_LABEL_RE = re.compile(r"^(\d{1,3})\s*(Mo|Yr|Wk|Dy|M|Y|W|D)$", re.IGNORECASE)
# Deliberately no exponent form and no bare "." -- "1e999" and "NaN" must be
# refused as malformed rather than reaching float().
_NUMERIC_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")
_NON_FINITE_TEXTS = frozenset({"NAN", "-NAN", "+NAN", "INF", "-INF", "+INF", "INFINITY",
                               "-INFINITY", "+INFINITY"})
DATE_RE = re.compile(r"\b\d{2}/\d{2}/(?:\d{2}|\d{4})\b")
SIDE_TEXTS = {"BID": "Bid", "MID": "Mid", "ASK": "Ask"}

KNOWN_SOURCE_TEXTS = frozenset({"BVOL", "CMPN", "BGN"})
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
# Whether the screen drew a space between two boxes, measured in character
# widths taken from the boxes themselves. Two bounds rather than one, because
# a single threshold has to answer even where the geometry does not: boxes a
# reader left 0.3 character widths apart could be one word it cut in half or
# two words set tight, and guessing either way stores text the screen may not
# have shown (Codex review, PR #182). Measured on the fixtures this parser is
# built from: a split inside a word leaves its halves touching (0.0), and
# real word spaces run 0.5 to 0.7 character widths.
_ONE_WORD_GAP_CHARACTER_WIDTHS = 0.15
_WORD_SPACE_GAP_CHARACTER_WIDTHS = 0.45

# Field labels VCUB draws on its own header lines. A labelled value run ends
# at the next one of these, so two fields sharing a line stay separate.
_FIELD_LABELS = frozenset(
    {"type", "source", "side", "date", "currency", "expiry", "tenor", "strike", "curve"}
)

_UNIT_NOMINAL_DAYS = {"D": 1.0, "W": 7.0, "M": 30.0, "Y": 365.0}

def normalise_text(text: str) -> str:
    return " ".join(text.split())


def is_tenor_label(text: str) -> bool:
    """Whether ``text`` reads as a VCUB tenor/expiry bucket label such as ``18Mo``."""

    return _TENOR_LABEL_RE.match(normalise_text(text)) is not None


#: Every VCUB tab draws its ``Type`` from one closed vocabulary --
#: ``Normal`` / ``Black`` / ``Lognormal`` / ``Shifted Lognormal`` / ``SABR``
#: followed by ``Vol`` and an optional parenthesised curve suffix (see
#: ``_VOL_TYPE_RE`` in ``bloomberg_vcub_atm_template``, which reads it off
#: the screen). Anchored at the start so ``Lognormal Vol`` cannot match on
#: its tail, and ``\b`` after ``vol`` so a longer word cannot either.
_NORMAL_VOL_TYPE_RE = re.compile(r"^normal\s+vol\b")


def is_normal_vol_type(text: object) -> bool:
    """Whether a stated vol type declares **normal** volatility space.

    ``Normal Vol (OIS)`` (the ATM tab) and ``Normal Vol Skew`` (the OTM
    Swaptions / SABR tab) both do; ``Lognormal Vol (OIS)``, ``Black Vol``,
    and an unresolved type (``None``) do not. Answered from the text the
    screen stated and from nothing else -- never from the magnitude of the
    numbers underneath it, and never from which tab they came from.
    """

    if not isinstance(text, str):
        return False
    return _NORMAL_VOL_TYPE_RE.match(normalise_text(text).casefold()) is not None


def tenor_label_nominal_days(text: str) -> float | None:
    """Nominal-day ordering key for a bucket label, or ``None`` if it is not one.

    Used only to check that an axis the geometry already ordered is *also*
    monotonic in tenor. It is a consistency check on the read, never a
    day-count, never a year fraction, and never an input to any calculation.
    """

    match = _TENOR_LABEL_RE.match(normalise_text(text))
    if match is None:
        return None
    unit = match.group(2)[0].upper()
    return int(match.group(1)) * _UNIT_NOMINAL_DAYS[unit]


def parse_cell_number(text: str) -> tuple[float | None, str | None]:
    """Return ``(value, failure_code)`` for one cell token's text."""

    normalised = normalise_text(text)
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
class TextLine:
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

        return join_by_geometry(self.tokens)[0]


def group_into_lines(tokens: Sequence[VCUBTextToken]) -> list[TextLine]:
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
            if shorter > 0 and overlap / shorter >= LINE_OVERLAP_FRACTION:
                line.append(token)
                break
        else:
            lines.append([token])
    return [
        TextLine(tuple(sorted(line, key=lambda item: item.left)))
        for line in sorted(lines, key=lambda line: min(item.top for item in line))
    ]


def band_edges(centres: Sequence[float], fallback_extent: float) -> tuple[list[float], float]:
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


def assign_band(
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
        if abs(position - boundary) <= pitch * BOUNDARY_AMBIGUITY_FRACTION:
            ambiguous = True
    return index, ambiguous


def spans_are_orderable(
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
    visual line -- exactly the condition :func:`group_into_lines` uses to
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
    return overlap / shorter < LINE_OVERLAP_FRACTION


def pitch_irregularity_message(centres: Sequence[float], *, axis: str) -> str | None:
    """The gap a label the reader missed would leave on this axis, or ``None``.

    Returns a sentence for the caller to raise as a blocking error rather
    than raising one itself, so both templates -- which carry different
    issue types and different error codes -- share the one measurement.
    """

    if len(centres) < 3:
        return None
    pitches = [centres[index + 1] - centres[index] for index in range(len(centres) - 1)]
    narrowest = min(pitches)
    widest = max(pitches)
    if narrowest <= 0:
        return None
    # Measured against the *narrowest* gap, not the median (Codex review,
    # PR #182). A dropped label leaves one gap about twice its neighbours,
    # and with only two gaps to compare -- a four-label axis that lost one
    # interior label -- any median is dragged up by the wide gap itself, so
    # the ratio could never trip. The narrowest gap is the axis's own
    # unambiguous unit and cannot be inflated by the very anomaly being
    # looked for.
    if widest <= narrowest * PITCH_IRREGULARITY_MULTIPLE:
        return None
    return (
        f"the {axis} spacing is irregular (widest gap {widest:.1f}px against a narrowest of "
        f"{narrowest:.1f}px), which is what a {axis[:-1]} the reader missed looks like; "
        "values around that gap could be one band out, so the capture is refused"
    )


def field_segments(text: str) -> list[tuple[str, bool]]:
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


def trim_ui_glyphs(text: str) -> str:
    """Drop the dropdown carets and separators a widget's text carries.

    Only characters that cannot begin or end a real value are removed, so a
    parenthesised suffix such as ``Normal Vol (OIS)`` survives intact.
    """

    while text and not (text[-1].isalnum() or text[-1] == ")"):
        text = text[:-1]
    while text and not (text[0].isalnum() or text[0] == "("):
        text = text[1:]
    return text.strip()


def unique_member(candidates: Sequence[str], allowed: set[str]) -> str | None:
    found = {candidate for candidate in candidates if candidate in allowed}
    return found.pop() if len(found) == 1 else None


def character_width(token: VCUBTextToken) -> float:
    return token.width / max(len(normalise_text(token.text)), 1)


def starts_a_new_widget(left: VCUBTextToken, right: VCUBTextToken) -> bool:
    """Whether ``right`` begins a different widget from ``left``.

    Two real boundary signals, both read off the boxes rather than guessed
    from the joined text: a separator glyph closing the left token (the
    dropdown caret), or a gap wider than a word space between them.
    """

    if _WIDGET_SEPARATOR_TAIL_RE.search(normalise_text(left.text)):
        return True
    gap = right.left - left.right
    scale = max(character_width(left), character_width(right))
    return scale > 0 and gap > scale * _FIELD_GAP_CHARACTER_WIDTHS


def curve_config_in_line(line: TextLine) -> str | None:
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
        if _CUBE_ANCHOR_RE.search(normalise_text(token.text))
    ]
    if len(anchors) != 1:
        return None  # no name here, or two: either way not this line's value
    anchor = anchors[0]

    last = extend_over_parenthetical(tokens, anchor)
    if last is None:
        return None  # a suffix opened and never closed: the name would be partial

    first = anchor
    while first > 0 and not starts_a_new_widget(tokens[first - 1], tokens[first]):
        first -= 1

    # "9) Analyze Cube" is a menu action, not a configuration value -- whether
    # the reader put the marker in its own box or in the same one as the text.
    if first > 0 and _MENU_MARKER_RE.match(normalise_text(tokens[first - 1].text)):
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

    joined = join_name(tokens[first : last + 1])
    if joined is None:
        return None
    name = trim_ui_glyphs(joined)
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


def parenthesis_delta(token: VCUBTextToken) -> int:
    text = normalise_text(token.text)
    return text.count("(") - text.count(")")


def extend_over_parenthetical(
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
    depth = parenthesis_delta(tokens[anchor])
    while True:
        if depth <= 0:
            following = index + 1
            begins_suffix = (
                depth == 0
                and following < len(tokens)
                and normalise_text(tokens[following].text).startswith("(")
                and not starts_a_new_widget(tokens[index], tokens[following])
            )
            if not begins_suffix:
                return index
        following = index + 1
        if following >= len(tokens) or starts_a_new_widget(tokens[index], tokens[following]):
            return None
        index = following
        depth += parenthesis_delta(tokens[index])


def space_between(left: VCUBTextToken, right: VCUBTextToken) -> bool | None:
    """Whether the screen drew a space, or ``None`` if the boxes do not say."""

    scale = max(character_width(left), character_width(right))
    if scale <= 0:
        return None
    ratio = (right.left - left.right) / scale
    if ratio < _ONE_WORD_GAP_CHARACTER_WIDTHS:
        return False
    if ratio >= _WORD_SPACE_GAP_CHARACTER_WIDTHS:
        return True
    return None


def widget_values(tokens: Sequence[VCUBTextToken]) -> list[str]:
    """Each widget's own displayed value, where the boxes settle its spacing.

    A closed vocabulary is matched against these rather than against every
    word on the screen. The live header shows why: its curve name is
    ``USD RFR BVOL Cube (Default)``, so ``BVOL`` and ``USD`` both appear on a
    screen whose Source and Currency widgets are elsewhere. Scanning
    everything let an unreadable Source inherit the curve's contributor code
    -- Source ``CMPN`` stored as ``BVOL``, Currency ``EUR`` stored as ``USD``
    (Codex review, PR #182). A widget displaying exactly ``USD`` is evidence
    of the currency; the same letters inside a longer name are not.

    A widget whose own spacing is illegible contributes nothing, so a value
    cannot be assembled across a gap that may not be there.
    """

    widgets: list[list[VCUBTextToken]] = [[tokens[0]]]
    # Deliberately ragged: the last token has no successor to pair with.
    for left, right in zip(tokens, tokens[1:], strict=False):
        if starts_a_new_widget(left, right):
            widgets.append([right])
        else:
            widgets[-1].append(right)

    values: list[str] = []
    for widget in widgets:
        text, certain = join_by_geometry(widget)
        if certain and (value := trim_ui_glyphs(text)):
            values.append(value)
    return values


def join_by_geometry(tokens: Sequence[VCUBTextToken]) -> tuple[str, bool]:
    """Join tokens by their boxes; also report whether every gap was legible.

    An illegible gap is joined *closed*, because the alternative is writing a
    character the screen may never have drawn. That still leaves the text
    uncertain -- two words set tight would be run together -- so the flag
    travels with it and the one rule that copies screen text verbatim
    declines to use it.
    """

    text = normalise_text(tokens[0].text)
    certain = True
    # Deliberately ragged: the last token has no successor to pair with.
    for left, right in zip(tokens, tokens[1:], strict=False):
        space = space_between(left, right)
        if space is None:
            certain = False
            space = False
        text += (" " if space else "") + normalise_text(right.text)
    return text, certain


def join_name(tokens: Sequence[VCUBTextToken]) -> str | None:
    """Join a name's tokens, closing up spacing the reader introduced.

    Whether a space goes between two tokens is read off their boxes, never
    assumed. Joining unconditionally inserted a character the screen never
    displayed: identical geometry stored ``RFR+OIS`` when the reader returned
    one box and ``RFR+ OIS`` when it split the word in two (Codex review,
    PR #182), so the recorded Bloomberg name depended on the reader's
    grouping.
    """

    text, certain = join_by_geometry(tokens)
    if not certain:
        return None  # its own spacing is a guess: better unresolved than wrong
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+\)", ")", text)


def unique_curve_config(lines: Sequence[TextLine]) -> str | None:
    """The one curve/config name on the screen, whole, or nothing."""

    found = {
        name for line in lines if (name := curve_config_in_line(line)) is not None
    }
    return found.pop() if len(found) == 1 else None


def unique_match(line_texts: Sequence[str], pattern: re.Pattern[str]) -> str | None:
    found = {
        trim_ui_glyphs(match.group(0))
        for text in line_texts
        for match in pattern.finditer(text)
    }
    found.discard("")
    return found.pop() if len(found) == 1 else None


def unique_line_containing(line_texts: Sequence[str], needle: str) -> str | None:
    found = [text for text in line_texts if needle in text.casefold()]
    return found[0] if len(found) == 1 else None


def labelled_value(line_texts: Sequence[str], label: str) -> str | None:
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
            matches.append(trim_ui_glyphs(" ".join(value_words)))
    unique = {match for match in matches if match}
    return unique.pop() if len(unique) == 1 else None

def lines_below(
    lines: Sequence[TextLine], anchor_line: TextLine
) -> list[TextLine]:
    """The text lines the grouper placed below ``anchor_line``.

    Which line a token belongs to is settled once, by
    :func:`group_into_lines`, and those lines come back ordered top to
    bottom. Asking that question again here -- by testing whether a line's
    box clears the anchor line's box -- asks a *different* and stricter one,
    and on a real VCUB grid it is false: the second live capture drew its
    header band's box touching the first data row's, so ``1Mo`` was dropped
    from the expiry column and its entire row of values came back as
    ``NUMERIC_TOKEN_OUTSIDE_ROWS``. Boxes merely meeting, at zero overlap,
    was enough.

    Note what this does *not* relax. A label the reader genuinely missed
    still leaves an orphaned row of numbers, and that still fails the
    capture closed -- this only stops discarding a label that was read.
    """

    seen_anchor = False
    below: list[TextLine] = []
    for line in lines:
        if line is anchor_line:
            seen_anchor = True
        elif seen_anchor:
            below.append(line)
    return below

