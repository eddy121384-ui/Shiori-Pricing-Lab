"""Image -> text tokens for the VCUB ATM visual capture (Issue #181).

This is the one module in the capture slice that touches an image, and the
one that needs a dependency the rest of the repository does not have. It is
kept deliberately thin: it hashes the operator's bytes for provenance,
turns the image into detected text boxes, and hands
:mod:`shiori_pricing_lab.data.bloomberg_vcub_atm_template` a plain sequence
of :class:`VCUBTextToken`. All structure, geometry, and fail-closed
judgement live behind that token boundary, which is why Issue #181's
structural test requirements run offline with no OCR engine installed.

**Dependency choice.** The reader is ``rapidocr-onnxruntime``: pip-only
wheels on Windows, no system installer, no administrator rights, no PATH
entry, and no GPU -- unlike Tesseract (a separate Windows installer) or the
torch-based readers (a multi-gigabyte stack). It is declared as the
optional ``capture`` extra rather than a core requirement so the ordinary
``start_shiori.bat`` install stays exactly as heavy as it is today; a
workbench without it reports :class:`VCUBOCRUnavailableError` verbatim,
including the one command that fixes it, and captures nothing.

**Never a silent read.** A detection below :data:`MIN_TOKEN_CONFIDENCE` is
dropped and named in a warning rather than passed on as if it were solid --
and because a dropped header or row label shows up downstream as irregular
band pitch, dropping one fails the capture closed instead of shifting a
value into a neighbouring cell.

**Visual sign evidence (live-acceptance defect #3).** :func:`diagnose_minus_evidence`
is a narrowly-scoped second pass over one numeric token's own pixels, for the
case RapidOCR's text recognizer drops Bloomberg's narrow minus glyph from a
digit token's text while the stroke is still visible in the image -- usually
*inside* the engine's own detection box, at its extreme left edge, not
outside it. It classifies from the leftmost connected component only,
against a local glyph-height baseline derived from the token's own
components (never the token's bbox height, and never the single tallest
component -- both were tried and both broke on Eddy's real screenshots
across four rounds of the diagnostic). :func:`read_tokens_from_image_bytes`
calls it, through :func:`attach_visual_sign_evidence`, on every unsigned
numeric token it detects; :func:`tokens_from_detections` on its own never
touches it, since it has no image. A cell's own template parser
(:mod:`bloomberg_vcub_otm_template`) is what turns ``"negative"`` into a
resigned value and ``"ambiguous"`` into a blocking ``NUMERIC_SIGN_AMBIGUOUS``
-- this module only ever attaches the raw pixel evidence to the token.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import numpy as np

from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    PARSER_NAME,
    PARSER_VERSION,
    VCUBCaptureProvenance,
    VCUBTextToken,
)
from shiori_pricing_lab.data.bloomberg_vcub_screen_reader import normalise_text, parse_cell_number

#: Detections weaker than this are not trusted enough to place a value.
MIN_TOKEN_CONFIDENCE = 0.30

#: The exact command an operator runs to make visual capture available.
CAPTURE_EXTRA_INSTALL_COMMAND = r'.venv\Scripts\python -m pip install -e ".[capture]"'


class VCUBOCRUnavailableError(RuntimeError):
    """The visual-capture reader is not installed in this environment."""


def sha256_of_image_bytes(raw_image: bytes) -> str:
    """Return the lower-case SHA-256 hex digest of exactly the bytes read."""

    if not isinstance(raw_image, (bytes, bytearray)):
        raise ValueError(f"raw_image must be bytes, got {type(raw_image).__name__}")
    if not raw_image:
        raise ValueError("raw_image must not be empty")
    return hashlib.sha256(bytes(raw_image)).hexdigest()


def build_capture_provenance(
    *,
    source_reference: str,
    raw_image: bytes,
    captured_at: str | None = None,
    parser_name: str = PARSER_NAME,
    parser_version: str = PARSER_VERSION,
) -> VCUBCaptureProvenance:
    """Build the provenance record for one operator-supplied image.

    ``captured_at`` defaults to the moment this runs, in UTC. The image
    bytes themselves are hashed and then dropped -- this repository is
    public and never stores a live Bloomberg screen.

    ``parser_name``/``parser_version`` default to the ATM template's, which
    is what read every image before Issue #185; the OTM/SABR template passes
    its own, so a stored surface names the parser that actually read it.
    """

    if captured_at is None:
        captured_at = datetime.now(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    return VCUBCaptureProvenance(
        source_reference=source_reference,
        source_image_sha256=sha256_of_image_bytes(raw_image),
        source_image_bytes=len(raw_image),
        captured_at=captured_at,
        parser_name=parser_name,
        parser_version=parser_version,
    )


def tokens_from_detections(
    detections: Sequence[object], *, min_confidence: float = MIN_TOKEN_CONFIDENCE
) -> tuple[tuple[VCUBTextToken, ...], tuple[str, ...]]:
    """Convert an OCR engine's raw output into tokens plus dropped-token notes.

    ``detections`` is the ``[[box, text, score], ...]`` shape RapidOCR
    returns, where ``box`` is four ``(x, y)`` corner points. Each box is
    reduced to its axis-aligned bounding box, which is all the template
    parser uses. A detection that is malformed, blank, or below
    ``min_confidence`` produces a note rather than a token, so nothing weak
    ever reaches the geometry silently.
    """

    tokens: list[VCUBTextToken] = []
    notes: list[str] = []
    for index, detection in enumerate(detections):
        try:
            box, text, score = detection  # type: ignore[misc]
            points = [(float(point[0]), float(point[1])) for point in box]
            confidence = float(score)
        except (TypeError, ValueError, IndexError):
            notes.append(f"detection {index} was malformed and was ignored")
            continue
        if len(points) < 2:
            notes.append(f"detection {index} had fewer than two corner points and was ignored")
            continue
        cleaned = " ".join(str(text).split())
        if not cleaned:
            notes.append(f"detection {index} carried no text and was ignored")
            continue
        # A reader that answers with a non-finite coordinate, a NaN score, or
        # a score outside [0, 1] is not behaving like the reader this adapter
        # was written against (Codex review, PR #182). Such a detection is
        # dropped with a note rather than clamped into a fully-trusted token
        # or allowed to reach VCUBTextToken, where it would raise and turn
        # one bad box into a failed capture instead of one missing token.
        # A dropped detection is never silent: if it was a header or a row
        # label, the template's pitch check sees the gap and fails closed.
        if not all(math.isfinite(value) for point in points for value in point):
            notes.append(f"{cleaned!r} had a non-finite bounding box and was ignored")
            continue
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            notes.append(
                f"{cleaned!r} was read with a confidence outside [0, 1] ({score!r}) and was "
                "ignored"
            )
            continue
        if confidence < min_confidence:
            notes.append(
                f"{cleaned!r} was read with confidence {confidence:.2f}, below the "
                f"{min_confidence:.2f} floor, and was dropped"
            )
            continue
        left = min(x for x, _ in points)
        right = max(x for x, _ in points)
        top = min(y for _, y in points)
        bottom = max(y for _, y in points)
        width = right - left
        height = bottom - top
        # Finite corners do not guarantee a finite extent: coordinates as
        # extreme as -1e308 and 1e308 each pass the check above while their
        # difference overflows to infinity, which VCUBTextToken then refuses
        # -- turning one bad detection into a failed capture rather than a
        # dropped token (Codex review round 2, PR #182).
        if right <= left or bottom <= top or not math.isfinite(width) or not math.isfinite(height):
            notes.append(f"{cleaned!r} had a degenerate bounding box and was ignored")
            continue
        tokens.append(
            VCUBTextToken(
                text=cleaned,
                left=left,
                top=top,
                width=width,
                height=height,
                confidence=confidence,
            )
        )
    return tuple(tokens), tuple(notes)


#: How far left of a numeric token's own box the search region for
#: :func:`diagnose_minus_evidence` extends beyond the box itself, as a
#: multiple of the token's own height -- relative to that token's own
#: geometry, never a fixed pixel count or screen coordinate. Small on
#: purpose: Eddy's own inspection of the live screenshots found the minus
#: stroke usually sits *inside* RapidOCR's own detection box already, at
#: its extreme left edge -- the recognizer omitted it from the text
#: without shrinking the box to match -- so the box itself is the primary
#: search region, and this margin only catches a stroke that spilled
#: slightly outside it (round 2, PR #186).
_MINUS_SEARCH_MARGIN_SCALE = 0.2

#: A component at least this tall, relative to the token's own *local
#: glyph-height baseline* (see ``_glyph_height_baseline``), is read as a
#: digit rather than a sign. Round 3 measured this against the token's
#: own bbox height, but Eddy's real screenshots show RapidOCR's detection
#: box runs noticeably taller than the actual glyphs drawn inside it (a
#: ~21px box around ~12px digits), so a real digit's height as a fraction
#: of the *box* is well under this threshold -- false NUMERIC_SIGN_AMBIGUOUS
#: on clean positives, and the true minus stroke's follower also read as
#: "not a digit". The baseline must come from the glyphs actually present,
#: not the box around them (round 4, PR #186).
_DIGIT_HEIGHT_FRACTION = 0.6

#: A component at least this tall, relative to the crop's own height, is
#: read as thin/short enough to be a candidate minus stroke.
_MINUS_THIN_FRACTION = 0.35


def _glyph_height_baseline(heights: list[int]) -> float:
    """A robust local reference for "normal digit height" among this
    token's own connected components.

    Never the tallest component present (round 2's bug: one outlier
    inflates the baseline and shadows real digits) and never the token's
    own bbox height (round 3's bug: RapidOCR's detection box runs taller
    than the glyphs actually drawn inside it, on the real screenshots).
    Instead: discard components far smaller than a first-pass median --
    punctuation, a dropped minus stroke -- and take the median of what is
    left, so a small minority of tiny or oversized components on either
    side cannot move the baseline that the leftmost component is judged
    against.
    """

    if len(heights) == 1:
        return float(heights[0])
    initial_median = float(np.median(heights))
    normal_sized = [h for h in heights if h >= initial_median * 0.5]
    return float(np.median(normal_sized))


@dataclass(frozen=True)
class MinusEvidence:
    """What :func:`diagnose_minus_evidence` found in one token's own prefix.

    ``components`` is every connected foreground component found in the
    search region, and ``prefix_components`` the ones sitting before
    ``first_digit_component`` -- both as ``(top, bottom, left, right)``
    pixel boxes in the image's own coordinates (half-open on the bottom
    and right), left to right. ``reason`` is a short, human-readable
    account of how ``classification`` was reached, for
    ``tools/bloomberg_vcub_otm_sign_diagnostic.py`` to print.
    """

    classification: str | None
    reason: str
    search_box: tuple[float, float, float, float]
    components: tuple[tuple[int, int, int, int], ...]
    first_digit_component: tuple[int, int, int, int] | None
    prefix_components: tuple[tuple[int, int, int, int], ...]


def _connected_components(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """8-connected foreground components of a boolean mask, as ``(top,
    bottom, left, right)`` boxes (half-open), sorted left to right.

    A crop here holds only a handful of characters, so a plain flood fill
    is simpler -- and just as fast -- as pulling in an image-processing
    dependency this repository does not otherwise need.
    """

    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    boxes: list[tuple[int, int, int, int]] = []
    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y, start_x] or visited[start_y, start_x]:
                continue
            stack = [(start_y, start_x)]
            visited[start_y, start_x] = True
            min_y = max_y = start_y
            min_x = max_x = start_x
            while stack:
                y, x = stack.pop()
                min_y, max_y = min(min_y, y), max(max_y, y)
                min_x, max_x = min(min_x, x), max(max_x, x)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = y + dy, x + dx
                        if (
                            0 <= ny < height
                            and 0 <= nx < width
                            and mask[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            stack.append((ny, nx))
            boxes.append((min_y, max_y + 1, min_x, max_x + 1))
    boxes.sort(key=lambda box: box[2])
    return boxes


def diagnose_minus_evidence(image: np.ndarray, token: VCUBTextToken) -> MinusEvidence:
    """Inspect ``token``'s own leading prefix for Bloomberg's narrow minus
    stroke, and report exactly what was found.

    RapidOCR's text recognizer sometimes returns a numeric token's digits
    with no sign at all even though the minus stroke is still visually
    present -- inside the engine's own detection box, at its extreme left
    edge -- confirmed against the operator's own screenshots via
    ``tools/bloomberg_vcub_otm_sign_diagnostic.py`` (live-acceptance
    defect #3). This is the narrowly-scoped second pass that inspects
    *only* that one token's own pixels to answer it: never another
    screenshot, another cell, the strike column, or the expected skew
    shape.

    The decision looks only at the leftmost connected foreground
    component -- never at "the tallest component present", which an
    unusually tall later component (round 2's bug) can inflate and make
    a genuine leading digit look too short to be a digit. Height is
    judged against a local glyph-height baseline derived from this
    token's own components (see ``_glyph_height_baseline``), never
    against the token's own bbox height -- on the real screenshots
    RapidOCR's detection box runs noticeably taller than the glyphs
    actually drawn inside it, which made a genuine digit look too short
    relative to the box (round 3's bug). If the leftmost component is
    itself tall enough, relative to that local baseline, to read as a
    digit, there is no sign evidence -- the token's own text stands.
    Otherwise, if it is horizontally elongated, short relative to the
    crop's own height, sitting near the crop's own vertical midline, not
    wide enough to be a line drawn across the whole cell/grid, and
    immediately followed by a component tall enough (against the same
    local baseline) to read as a digit, it reads ``"negative"``. Any
    other leftmost shape reads ``"ambiguous"`` -- genuinely neither a
    plausible digit nor a plausible sign, never guessed either way. No
    foreground at all in the search region reads ``None``.

    The threshold that separates foreground from background is derived
    from this crop's own dynamic range, not a fixed brightness constant,
    so the same rule reads a light-on-dark or dark-on-light Bloomberg
    theme alike. All geometry is scaled from the token's own box, never
    an absolute screen coordinate.
    """

    margin = token.height * _MINUS_SEARCH_MARGIN_SCALE
    top = max(0, int(round(token.top)))
    bottom = min(image.shape[0], int(round(token.bottom)))
    left = max(0, int(round(token.left - margin)))
    right = min(image.shape[1], int(round(token.right)))
    search_box = (float(left), float(top), float(right), float(bottom))
    if bottom <= top or right <= left:
        return MinusEvidence(None, "no room to search", search_box, (), None, ())

    crop = image[top:bottom, left:right]
    gray = crop.astype(np.float64)
    if gray.ndim == 3:
        gray = gray.mean(axis=2)
    height, width = gray.shape

    dynamic_range = float(gray.max() - gray.min())
    if dynamic_range < 8.0:
        return MinusEvidence(
            None, "crop is uniform; nothing drawn there", search_box, (), None, ()
        )

    background = float(np.median(gray))
    foreground = np.abs(gray - background) > dynamic_range * 0.35
    if not foreground.any():
        return MinusEvidence(
            None, "no foreground pixels above threshold", search_box, (), None, ()
        )

    local_boxes = _connected_components(foreground)
    absolute_boxes = tuple(
        (top + box_top, top + box_bottom, left + box_left, left + box_right)
        for box_top, box_bottom, box_left, box_right in local_boxes
    )

    component_heights = [box_bottom - box_top for box_top, box_bottom, _l, _r in local_boxes]
    glyph_baseline = _glyph_height_baseline(component_heights)

    leftmost_top, leftmost_bottom, leftmost_left, leftmost_right = local_boxes[0]
    leftmost_box = absolute_boxes[0]
    leftmost_height = leftmost_bottom - leftmost_top
    leftmost_width = leftmost_right - leftmost_left
    leftmost_centre = (leftmost_top + leftmost_bottom) / 2.0

    baseline_frac = leftmost_height / glyph_baseline
    centre_frac = leftmost_centre / height
    width_frac = leftmost_width / width

    if baseline_frac >= _DIGIT_HEIGHT_FRACTION:
        return MinusEvidence(
            None,
            "leftmost component is itself digit-height; no sign precedes it",
            search_box,
            absolute_boxes,
            leftmost_box,
            (),
        )

    thin_enough = baseline_frac <= _MINUS_THIN_FRACTION
    elongated = leftmost_width > leftmost_height
    midline = 0.20 <= centre_frac <= 0.90
    not_grid_wide = width_frac < 0.90

    next_box = absolute_boxes[1] if len(local_boxes) > 1 else None
    next_is_digit = False
    if len(local_boxes) > 1:
        next_top, next_bottom, _next_left, _next_right = local_boxes[1]
        next_is_digit = ((next_bottom - next_top) / glyph_baseline) >= _DIGIT_HEIGHT_FRACTION

    if thin_enough and elongated and midline and not_grid_wide and next_is_digit:
        return MinusEvidence(
            "negative",
            "leftmost component is a short, thin, mid-height stroke followed by a "
            "normal digit-height component",
            search_box,
            absolute_boxes,
            next_box,
            (leftmost_box,),
        )
    return MinusEvidence(
        "ambiguous",
        "leftmost component is neither digit-height nor a plausible leading minus "
        f"(baseline_frac={baseline_frac:.2f}, elongated={elongated}, "
        f"centre_frac={centre_frac:.2f}, width_frac={width_frac:.2f}, "
        f"followed_by_digit={next_is_digit})",
        search_box,
        absolute_boxes,
        next_box,
        (leftmost_box,),
    )


def visual_minus_evidence(image: np.ndarray, token: VCUBTextToken) -> str | None:
    """The tri-state minus classification alone.

    See :func:`diagnose_minus_evidence` for the full evidence -- the
    component boxes and the reason -- behind this answer.
    """

    return diagnose_minus_evidence(image, token).classification


def _is_unsigned_numeric_text(text: str) -> bool:
    """Whether ``text`` is a plausible cell number carrying no sign of its
    own -- the same gate :mod:`bloomberg_vcub_otm_template`'s separate-minus-
    token reconstruction already uses, reused rather than restated so the
    two sign-evidence paths can never quietly diverge on what counts as
    "unsigned"."""

    normalised = normalise_text(text)
    return not normalised.startswith(("+", "-")) and parse_cell_number(text)[0] is not None


def attach_visual_sign_evidence(
    image: np.ndarray, tokens: Sequence[VCUBTextToken]
) -> tuple[VCUBTextToken, ...]:
    """Run :func:`visual_minus_evidence` over every unsigned numeric token
    and return the same tokens with ``sign_evidence`` filled in where it
    found something.

    Only unsigned numeric tokens are inspected -- a header, a row label, or
    a token whose own text already carries an explicit sign has nothing for
    this pixel check to add. A token this leaves alone comes back unchanged
    (``sign_evidence`` stays ``None``), so a caller that never runs this
    pass at all -- :func:`tokens_from_detections`, tested with no image --
    sees exactly the tokens it always has (live-acceptance defect, PR #186).
    """

    inspected: list[VCUBTextToken] = []
    for token in tokens:
        if not _is_unsigned_numeric_text(token.text):
            inspected.append(token)
            continue
        classification = visual_minus_evidence(image, token)
        inspected.append(
            token if classification is None else replace(token, sign_evidence=classification)
        )
    return tuple(inspected)


def _load_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # pragma: no cover - exercised by the unavailable-path test
        raise VCUBOCRUnavailableError(
            "Visual capture needs the optional OCR reader, which is not installed. "
            f"From the repository root, run: {CAPTURE_EXTRA_INSTALL_COMMAND}"
        ) from exc
    return RapidOCR()


def _decode_image(raw_image: bytes):
    try:
        import numpy
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - exercised by the unavailable-path test
        raise VCUBOCRUnavailableError(
            "Visual capture needs the optional image reader, which is not installed. "
            f"From the repository root, run: {CAPTURE_EXTRA_INSTALL_COMMAND}"
        ) from exc
    import io

    try:
        with Image.open(io.BytesIO(raw_image)) as image:
            return numpy.asarray(image.convert("RGB"))
    except UnidentifiedImageError as exc:
        raise ValueError("the supplied file is not a readable image") from exc


def read_tokens_from_image_bytes(
    raw_image: bytes, *, engine=None, min_confidence: float = MIN_TOKEN_CONFIDENCE
) -> tuple[tuple[VCUBTextToken, ...], tuple[str, ...]]:
    """Detect text in ``raw_image`` and return ``(tokens, dropped-token notes)``.

    ``engine`` is injectable so the conversion contract can be tested
    without the optional dependency; left unset, the RapidOCR reader is
    loaded lazily so importing this module never requires it.

    Every unsigned numeric token is then passed through
    :func:`attach_visual_sign_evidence`, the only place this happens: this
    is the one call in the capture slice that both decodes the image *and*
    hands tokens onward, so it is where the pixel-evidence second pass runs
    (live-acceptance defect, PR #186). :func:`tokens_from_detections` on its
    own is never touched by this -- it has no image to inspect, and every
    token it returns keeps ``sign_evidence=None``.
    """

    if not isinstance(raw_image, (bytes, bytearray)) or not raw_image:
        raise ValueError("raw_image must be non-empty bytes")
    image = _decode_image(raw_image)
    reader = _load_engine() if engine is None else engine
    detections, _elapsed = reader(image)
    tokens, notes = tokens_from_detections(detections or (), min_confidence=min_confidence)
    return attach_visual_sign_evidence(image, tokens), notes
