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
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    PARSER_NAME,
    PARSER_VERSION,
    VCUBCaptureProvenance,
    VCUBTextToken,
)

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
    *, source_reference: str, raw_image: bytes, captured_at: str | None = None
) -> VCUBCaptureProvenance:
    """Build the provenance record for one operator-supplied image.

    ``captured_at`` defaults to the moment this runs, in UTC. The image
    bytes themselves are hashed and then dropped -- this repository is
    public and never stores a live Bloomberg screen.
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
        parser_name=PARSER_NAME,
        parser_version=PARSER_VERSION,
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
        if right <= left or bottom <= top:
            notes.append(f"{cleaned!r} had a degenerate bounding box and was ignored")
            continue
        tokens.append(
            VCUBTextToken(
                text=cleaned,
                left=left,
                top=top,
                width=right - left,
                height=bottom - top,
                confidence=min(max(confidence, 0.0), 1.0),
            )
        )
    return tuple(tokens), tuple(notes)


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
    """

    if not isinstance(raw_image, (bytes, bytearray)) or not raw_image:
        raise ValueError("raw_image must be non-empty bytes")
    image = _decode_image(raw_image)
    reader = _load_engine() if engine is None else engine
    detections, _elapsed = reader(image)
    return tokens_from_detections(detections or (), min_confidence=min_confidence)
