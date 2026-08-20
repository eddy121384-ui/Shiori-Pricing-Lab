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

import pytest
from test_bloomberg_vcub_atm_template import (
    EXPIRY_LABELS,
    TENOR_LABELS,
    _synthetic_value,
    provenance,
)

from shiori_pricing_lab.data.bloomberg_vcub_atm_template import parse_vcub_atm_tokens
from shiori_pricing_lab.data.bloomberg_vcub_ocr import (
    CAPTURE_EXTRA_INSTALL_COMMAND,
    MIN_TOKEN_CONFIDENCE,
    VCUBOCRUnavailableError,
    build_capture_provenance,
    read_tokens_from_image_bytes,
    tokens_from_detections,
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
