"""The image -> token seam of the VCUB visual capture (Issue #181).

Two layers are covered here. The conversion from a reader's raw detections
into :class:`VCUBTextToken` is pure and always runs. The end-to-end
image test renders a **synthetic** Bloomberg-shaped grid with Pillow, runs
the real reader over it, and checks that every cell still lands on its own
intersection at two different scales; it skips when the optional ``capture``
extra is not installed, which is also how CI sees it.

Nothing here is a Bloomberg screenshot. The fixture is drawn from the same
synthetic labels and generated numbers as
``test_bloomberg_vcub_atm_template.py`` -- this repository is public.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from test_bloomberg_vcub_atm_template import (
    EXPIRY_LABELS,
    TENOR_LABELS,
    _synthetic_value,
    provenance,
)

from shiori_pricing_lab.data.bloomberg_vcub_atm_template import parse_vcub_atm_tokens
from shiori_pricing_lab.data.bloomberg_vcub_capture import VCUBTextToken
from shiori_pricing_lab.data.bloomberg_vcub_ocr import (
    CAPTURE_EXTRA_INSTALL_COMMAND,
    MIN_TOKEN_CONFIDENCE,
    VCUBOCRUnavailableError,
    build_capture_provenance,
    diagnose_minus_evidence,
    read_tokens_from_image_bytes,
    tokens_from_detections,
    visual_minus_evidence,
)

_BOX = [(10.0, 20.0), (90.0, 22.0), (90.0, 44.0), (10.0, 42.0)]


# ---------------------------------------------------------------------------
# Detection -> token conversion
# ---------------------------------------------------------------------------


def test_a_detection_becomes_its_axis_aligned_bounding_box() -> None:
    tokens, notes = tokens_from_detections([[_BOX, "3Mo", 0.97]])

    assert notes == ()
    (token,) = tokens
    assert (token.left, token.top) == (10.0, 20.0)
    assert (token.width, token.height) == (80.0, 24.0)
    assert token.text == "3Mo"
    assert token.confidence == pytest.approx(0.97)


def test_surrounding_whitespace_in_a_detection_is_collapsed() -> None:
    (token,), _notes = tokens_from_detections([[_BOX, "  Normal   Vol \n", 0.9]])

    assert token.text == "Normal Vol"


def test_a_low_confidence_detection_is_dropped_and_named_rather_than_trusted() -> None:
    tokens, notes = tokens_from_detections(
        [[_BOX, "85.15", MIN_TOKEN_CONFIDENCE - 0.01]]
    )

    assert tokens == ()
    assert len(notes) == 1
    assert "85.15" in notes[0]


def test_a_detection_at_the_confidence_floor_is_kept() -> None:
    tokens, notes = tokens_from_detections([[_BOX, "85.15", MIN_TOKEN_CONFIDENCE]])

    assert len(tokens) == 1
    assert notes == ()


@pytest.mark.parametrize(
    "detection",
    [
        ["not-a-box", "3Mo", 0.9],
        [_BOX, "3Mo", "not-a-score"],
        [[(1.0, 2.0)], "3Mo", 0.9],
        [_BOX, "   ", 0.9],
        [[(5.0, 5.0), (5.0, 5.0)], "3Mo", 0.9],
    ],
)
def test_an_unusable_detection_is_noted_and_never_becomes_a_token(detection) -> None:
    tokens, notes = tokens_from_detections([detection])

    assert tokens == ()
    assert len(notes) == 1


@pytest.mark.parametrize("score", [1.5, -0.2, float("nan"), float("inf")])
def test_a_confidence_outside_zero_to_one_is_dropped_never_clamped(score) -> None:
    """Codex review, PR #182.

    Clamping turned an impossible score into a fully-trusted 1.0, and a NaN
    slipped past the floor comparison into VCUBTextToken, where it raised
    and failed the whole capture instead of dropping one detection.
    """

    tokens, notes = tokens_from_detections([[_BOX, "85.15", score]])

    assert tokens == ()
    assert len(notes) == 1
    assert "confidence outside [0, 1]" in notes[0]


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), float("-inf")])
def test_a_non_finite_box_coordinate_is_dropped_rather_than_raising(bad) -> None:
    """Codex review, PR #182: one bad box must cost one token, not the capture."""

    box = [(10.0, 20.0), (bad, 22.0), (90.0, 44.0), (10.0, 42.0)]

    tokens, notes = tokens_from_detections([[box, "3Mo", 0.9]])

    assert tokens == ()
    assert "non-finite bounding box" in notes[0]


def test_a_box_whose_derived_extent_overflows_is_dropped_rather_than_raising() -> None:
    """Codex review round 2, PR #182.

    Each corner is finite, but the width overflows to infinity, which
    VCUBTextToken refuses -- one bad detection must not fail the capture.
    """

    box = [(-1e308, 20.0), (1e308, 22.0), (1e308, 44.0), (-1e308, 42.0)]

    tokens, notes = tokens_from_detections([[box, "3Mo", 0.9]])

    assert tokens == ()
    assert "degenerate bounding box" in notes[0]


def test_a_valid_confidence_is_carried_through_exactly() -> None:
    (token,), _notes = tokens_from_detections([[_BOX, "3Mo", 1.0]])

    assert token.confidence == 1.0


def test_an_empty_detection_list_reads_as_no_tokens() -> None:
    assert tokens_from_detections([]) == ((), ())


# ---------------------------------------------------------------------------
# Visual sign evidence (live-acceptance defect #3): pixels only, no OCR
# engine needed -- numpy is a core dependency, so these always run.
#
# Round 2: Eddy's inspection of his own screenshots found the minus stroke
# usually sits *inside* RapidOCR's own detection box, at its extreme left
# edge, not outside it -- the recognizer omitted it from the text without
# shrinking the box to match. The search region is therefore the token's own
# box plus only a small margin.
#
# Round 3: round 2 found "the first digit" by comparing every component's
# height to the *tallest component present* in the crop. A later component
# unusually taller than the real digits (a merged blob, a stray ascender)
# inflated that baseline and made genuine leading digits fall below the
# digit-height threshold, producing false NUMERIC_SIGN_AMBIGUOUS on clean
# positive live values (33.92, 34.31, 35.19, 34.25, 1.34, 1.29, 1.39, 0.85).
# The fix classifies from the LEFTMOST component only, against the token's
# own fixed height -- never against another component in the same crop.
#
# Round 4: the token's own bbox height was itself the wrong reference.
# RapidOCR's detection box runs noticeably taller than the glyphs actually
# drawn inside it on the real screenshots (a ~21px box around ~12px digits),
# so a real digit's height as a fraction of the *box* undershoots the digit
# threshold -- the same false-ambiguous failure mode, and it also explains
# the still-failing dropped-minus cells (a genuine 1px minus followed by a
# 12px digit that, measured against a ~21px box, no longer reads as
# "digit-height" either). The reference is now a local glyph-height
# baseline derived from the token's own components (a robust median after
# discarding components far smaller than a first-pass median), never the
# bbox and never the single tallest component.
# ---------------------------------------------------------------------------

_DARK_BACKGROUND = (20, 20, 24)
_LIGHT_FOREGROUND = (220, 220, 225)


def _blank_image(width: int = 350, height: int = 150) -> np.ndarray:
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:, :] = _DARK_BACKGROUND
    return image


def _paint(image: np.ndarray, *, top: int, bottom: int, left: int, right: int) -> None:
    image[top:bottom, left:right] = _LIGHT_FOREGROUND


def _digit_token(
    *, left: float, top: float, width: float = 34.0, height: float = 14.0
) -> VCUBTextToken:
    return VCUBTextToken(
        text="2.99", left=left, top=top, width=width, height=height, confidence=1.0
    )


def test_a_blank_prefix_with_only_the_digit_is_no_evidence() -> None:
    """A positive value: only the (tall) first digit is drawn, nothing
    precedes it, so it stays positive."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0)
    _paint(image, top=21, bottom=33, left=110, right=118)  # first digit only

    assert visual_minus_evidence(image, token) is None


def test_a_minus_inside_the_boxs_own_left_edge_reads_as_negative() -> None:
    """The confirmed real-world shape: the minus stroke sits *inside* the
    token's own detection box, at its extreme left edge, separated from
    the first digit -- not outside the box at all."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0)
    _paint(image, top=26, bottom=28, left=101, right=106)  # minus, thin, mid-height
    _paint(image, top=21, bottom=33, left=110, right=118)  # first digit, tall

    evidence = visual_minus_evidence(image, token)
    assert evidence == "negative"


def test_two_thin_marks_before_the_real_digit_is_ambiguous() -> None:
    """The leftmost component is thin but the very next component is a
    second thin mark, not a digit -- the decision only ever looks at the
    leftmost component and its immediate neighbour, so it does not go
    hunting further right for the real digit, and must block rather than
    guess which mark (if either) is the sign. Two normal digits are
    included so the local glyph-height baseline has enough of a normal
    population to resolve robustly."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0, width=60.0)
    _paint(image, top=26, bottom=28, left=101, right=104)
    _paint(image, top=26, bottom=28, left=106, right=109)  # a second, separate mark
    _paint(image, top=21, bottom=33, left=113, right=121)  # normal digit
    _paint(image, top=21, bottom=33, left=125, right=133)  # normal digit

    assert visual_minus_evidence(image, token) == "ambiguous"


def test_a_later_oversized_component_does_not_shadow_a_normal_leading_digit() -> None:
    """Round 3 regression: a normal leading digit followed later by a
    component far taller than any real digit (a merged blob, a stray
    ascender) must not make the leading digit look too short to be one.
    This reproduces the shape category behind the live false positives on
    33.92, 34.31, 35.19, 34.25, 1.34, 1.29, 1.39, and 0.85 -- classifying
    from the leftmost component alone, against the token's own fixed
    height, never against the tallest component elsewhere in the crop."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0, width=40.0, height=14.0)
    _paint(image, top=21, bottom=33, left=101, right=109)  # normal leading digit
    _paint(image, top=18, bottom=36, left=112, right=120)  # spuriously oversized later blob

    assert visual_minus_evidence(image, token) is None


def test_a_positive_control_value_stays_positive() -> None:
    """The live positive control (1Mo x 1Yr / -50bps = 14.69): only the
    first digit is drawn, nothing precedes it, so it stays positive."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0)
    _paint(image, top=21, bottom=33, left=101, right=109)  # "1" of 14.69

    assert visual_minus_evidence(image, token) is None


def test_a_dropped_minus_with_bbox_taller_than_its_glyphs_reads_as_negative() -> None:
    """Round 4 regression: the real shape behind the live failures Eddy
    diagnosed. RapidOCR's detection box (~21px) runs noticeably taller
    than the glyphs actually drawn inside it (~12px digits), so a real
    digit's height as a fraction of the *box* undershoots the digit
    threshold. The reference must come from the token's own local glyph
    population, not its bbox: text='2.92', component heights ~[1, 12, 2,
    12, 12] against a 21px-tall box -- the minus is the 1px leading
    component, immediately followed by a 12px digit."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0, width=44.0, height=21.0)
    _paint(image, top=30, bottom=31, left=101, right=105)  # minus, 1px
    _paint(image, top=25, bottom=37, left=108, right=116)  # "2", 12px
    _paint(image, top=34, bottom=36, left=118, right=120)  # ".", 2px
    _paint(image, top=25, bottom=37, left=122, right=130)  # "9", 12px
    _paint(image, top=25, bottom=37, left=132, right=140)  # "2", 12px

    assert visual_minus_evidence(image, token) == "negative"


def test_a_positive_value_with_bbox_taller_than_its_glyphs_stays_positive() -> None:
    """Round 4 regression: the same bbox/glyph mismatch on a clean
    positive value must not misread the leading digit as too short to be
    one. Reproduces the shape category behind the live false positives on
    33.92, 34.31, 35.19, 34.25, 14.69, 10.04, 10.28, 10.91, 10.19, 4.10,
    1.34, 1.29, 1.39, and 0.85: a ~21px box around ~12px digit glyphs,
    with no component preceding the leading digit."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0, width=44.0, height=21.0)
    _paint(image, top=25, bottom=37, left=101, right=109)  # "3", 12px, leading
    _paint(image, top=25, bottom=37, left=111, right=119)  # "3", 12px
    _paint(image, top=34, bottom=36, left=121, right=123)  # ".", 2px
    _paint(image, top=25, bottom=37, left=125, right=133)  # "9", 12px
    _paint(image, top=25, bottom=37, left=135, right=143)  # "2", 12px

    assert visual_minus_evidence(image, token) is None


def test_a_blob_too_tall_to_be_a_stroke_is_ambiguous_not_negative() -> None:
    """Foreground precedes the first digit, but it is not thin enough to
    read as a stroke, and not tall enough to read as a digit against the
    local glyph baseline either -- unreadable as a sign, must not guess.
    A second normal digit is included so the baseline reflects the real
    glyph population rather than being pulled toward the blob's height by
    too few data points."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0, width=60.0)
    _paint(image, top=24, bottom=30, left=101, right=106)  # 6 rows: too tall, not thin
    _paint(image, top=21, bottom=33, left=110, right=118)  # normal digit
    _paint(image, top=21, bottom=33, left=122, right=130)  # normal digit

    assert visual_minus_evidence(image, token) == "ambiguous"


def test_a_mark_pinned_to_the_boxs_own_top_edge_is_ambiguous() -> None:
    """Thin, but sitting at the token's own top edge rather than its
    mid-height -- not where a minus/hyphen actually sits."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0)
    _paint(image, top=20, bottom=22, left=101, right=106)
    _paint(image, top=21, bottom=33, left=110, right=118)

    assert visual_minus_evidence(image, token) == "ambiguous"


def test_a_gridline_wider_than_one_glyph_is_ambiguous_not_negative() -> None:
    """A table/column gridline drawn far wider than one glyph -- present
    as foreground, but not a bounded sign, so it must not be read as
    one; it still needs a human look (ambiguous), not a silent guess."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0, width=80.0)  # right = 180
    _paint(image, top=26, bottom=28, left=97, right=172)  # very wide thin band
    _paint(image, top=21, bottom=33, left=175, right=179)  # first digit, gap before it

    assert visual_minus_evidence(image, token) == "ambiguous"


def test_the_detector_generalises_across_token_sizes_and_positions() -> None:
    """No fixed x/y: two tokens of different size, in different parts of
    the image, each with their own correctly-scaled minus inside its own
    box, both read as negative."""

    image = _blank_image()
    small = _digit_token(left=60.0, top=10.0, width=20.0, height=10.0)
    _paint(image, top=14, bottom=15, left=61, right=64)
    _paint(image, top=11, bottom=19, left=67, right=72)

    large = _digit_token(left=250.0, top=80.0, width=40.0, height=20.0)
    _paint(image, top=88, bottom=90, left=248, right=254)
    _paint(image, top=82, bottom=98, left=258, right=268)

    assert visual_minus_evidence(image, small) == "negative"
    assert visual_minus_evidence(image, large) == "negative"


def test_diagnose_minus_evidence_reports_the_components_it_found() -> None:
    """The richer object the diagnostic tool prints from: both boxes, which
    one was treated as the first digit, and a human-readable reason."""

    image = _blank_image()
    token = _digit_token(left=100.0, top=20.0)
    _paint(image, top=26, bottom=28, left=101, right=106)
    _paint(image, top=21, bottom=33, left=110, right=118)

    evidence = diagnose_minus_evidence(image, token)

    assert evidence.classification == "negative"
    assert evidence.first_digit_component == (21, 33, 110, 118)
    assert evidence.prefix_components == ((26, 28, 101, 106),)
    assert set(evidence.components) == {(26, 28, 101, 106), (21, 33, 110, 118)}
    assert "stroke" in evidence.reason


def test_a_token_flush_against_the_images_own_edge_has_no_crop_to_inspect() -> None:
    """No room to the left at all -- degenerate, not an error -- stays
    unevidenced rather than guessed."""

    image = _blank_image()
    token = _digit_token(left=0.0, top=20.0)

    assert visual_minus_evidence(image, token) is None


# ---------------------------------------------------------------------------
# Missing optional dependency
# ---------------------------------------------------------------------------


def test_a_missing_reader_reports_the_exact_command_that_installs_it(monkeypatch) -> None:
    import shiori_pricing_lab.data.bloomberg_vcub_ocr as ocr_module

    def _no_engine():
        raise VCUBOCRUnavailableError(
            "Visual capture needs the optional OCR reader, which is not installed. "
            f"From the repository root, run: {CAPTURE_EXTRA_INSTALL_COMMAND}"
        )

    monkeypatch.setattr(ocr_module, "_load_engine", _no_engine)
    monkeypatch.setattr(ocr_module, "_decode_image", lambda raw: raw)

    with pytest.raises(VCUBOCRUnavailableError, match=r"pip install -e"):
        read_tokens_from_image_bytes(b"not-really-an-image")


def test_an_injected_reader_is_used_instead_of_the_optional_dependency(monkeypatch) -> None:
    import shiori_pricing_lab.data.bloomberg_vcub_ocr as ocr_module

    monkeypatch.setattr(ocr_module, "_decode_image", lambda raw: raw)

    tokens, notes = read_tokens_from_image_bytes(
        b"pretend-image", engine=lambda image: ([[_BOX, "1Yr", 0.95]], 0.01)
    )

    assert [token.text for token in tokens] == ["1Yr"]
    assert notes == ()


def test_reading_refuses_empty_input() -> None:
    with pytest.raises(ValueError, match="non-empty bytes"):
        read_tokens_from_image_bytes(b"")


# ---------------------------------------------------------------------------
# End-to-end: a synthetic image, the real reader, the real template
# ---------------------------------------------------------------------------

_FONT_SIZE = 20
_ROW_PITCH = 46
_COLUMN_PITCH = 132
_LABEL_X = 40
_FIRST_COLUMN_RIGHT = 300
_HEADER_Y = 250
_FIRST_ROW_Y = 300


def render_synthetic_atm_screenshot(scale: float = 1.0) -> bytes:
    """Draw a Bloomberg-shaped ATM grid. Synthetic labels, generated numbers."""

    pillow = pytest.importorskip("PIL", reason="the optional capture extra is not installed")
    from PIL import Image, ImageDraw, ImageFont

    assert pillow is not None
    width = int((_FIRST_COLUMN_RIGHT + _COLUMN_PITCH * len(TENOR_LABELS) + 80) * scale)
    height = int((_FIRST_ROW_Y + _ROW_PITCH * len(EXPIRY_LABELS) + 90) * scale)
    image = Image.new("RGB", (width, height), (12, 12, 16))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=int(_FONT_SIZE * scale))

    def write(text: str, x: float, y: float, anchor: str = "ls") -> None:
        draw.text((x * scale, y * scale), text, font=font, fill=(232, 232, 236), anchor=anchor)

    write("USD RFR BVOL Cube (Default)", _LABEL_X, 60)
    write("ATM Swaptions", _LABEL_X, 120)
    write("Mid 08/18/26", 420, 120)
    write("Type Normal Vol (OIS)", _LABEL_X, 180)
    write("Source BVOL", 500, 180)

    write("Expiry", _LABEL_X, _HEADER_Y)
    for column_index, label in enumerate(TENOR_LABELS):
        write(label, _FIRST_COLUMN_RIGHT + column_index * _COLUMN_PITCH, _HEADER_Y, anchor="rs")
    for row_index, label in enumerate(EXPIRY_LABELS):
        write(label, _LABEL_X, _FIRST_ROW_Y + row_index * _ROW_PITCH)
        for column_index in range(len(TENOR_LABELS)):
            write(
                f"{_synthetic_value(row_index, column_index):.2f}",
                _FIRST_COLUMN_RIGHT + column_index * _COLUMN_PITCH,
                _FIRST_ROW_Y + row_index * _ROW_PITCH,
                anchor="rs",
            )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def reader():
    pytest.importorskip(
        "rapidocr_onnxruntime",
        reason=f"the optional capture extra is not installed: {CAPTURE_EXTRA_INSTALL_COMMAND}",
    )
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


@pytest.mark.parametrize("scale", [1.0, 1.4])
def test_a_synthetic_screenshot_reconstructs_every_cell_at_its_own_intersection(
    reader, scale: float
) -> None:
    raw_image = render_synthetic_atm_screenshot(scale)

    tokens, _notes = read_tokens_from_image_bytes(raw_image, engine=reader)
    capture = parse_vcub_atm_tokens(tokens, provenance=provenance())

    assert [issue.code for issue in capture.blocking_errors] == []
    assert capture.grid.expiry_labels == EXPIRY_LABELS
    assert capture.grid.tenor_labels == TENOR_LABELS
    for row_index, expiry in enumerate(EXPIRY_LABELS):
        for column_index, tenor in enumerate(TENOR_LABELS):
            assert capture.grid.value_at(expiry, tenor) == pytest.approx(
                _synthetic_value(row_index, column_index)
            ), f"{expiry} x {tenor} did not survive the round trip at scale {scale}"


def test_a_synthetic_screenshot_reads_its_metadata_or_says_it_could_not(reader) -> None:
    raw_image = render_synthetic_atm_screenshot()

    tokens, _notes = read_tokens_from_image_bytes(raw_image, engine=reader)
    metadata = parse_vcub_atm_tokens(tokens, provenance=provenance()).metadata

    for name in ("currency", "side", "quote_date", "tab", "vol_type", "source", "curve_config"):
        value = getattr(metadata, name)
        assert (value is None) == (name in metadata.unresolved_fields)
    assert metadata.tab == "ATM Swaptions"


def test_the_capture_hashes_the_operator_image_it_actually_read() -> None:
    raw_image = render_synthetic_atm_screenshot()

    built = build_capture_provenance(
        source_reference=r"C:\Users\eddy\Desktop\vcub_atm.png", raw_image=raw_image
    )

    assert built.source_image_bytes == len(raw_image)
    assert built.source_reference.endswith("vcub_atm.png")
