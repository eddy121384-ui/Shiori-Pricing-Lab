"""Typed records for the Bloomberg VCUB **ATM Swaptions** visual capture
prototype (Issue #181).

This module holds only the *shapes* the capture produces and the
confirm/reject state machine that guards them. It contains no image
handling, no OCR, no geometry, and -- deliberately -- no import of anything
from :mod:`shiori_pricing_lab.pricing`. A capture is a transcription of what
a trader can see on a VCUB screen, nothing more.

**Methodology boundary (Issue #181 "Data/methodology boundary").** Nothing
here interpolates a surface, computes a ``Kproxy``, chooses a moneyness,
calibrates SABR, converts Normal/yield vol into ``PRICE_VOL``, or runs
Black-76. ``VCUBSourceMetadata.vol_type`` carries the vol type *as the
screen spelled it* (e.g. ``"Normal Vol (OIS)"``) and is never renamed,
re-based, or reinterpreted -- a normal/yield vol must never be silently
relabelled ``PRICE_VOL``.

**Fail-closed by construction.** The two invariants that matter most are
enforced in ``__post_init__`` rather than left to callers:

* :class:`VCUBSourceMetadata` cannot hold a value for a field it also lists
  as unresolved, and cannot leave a ``None`` field off that list -- so an
  unresolved metadata field can never be quietly filled in later
  (Issue #181 test requirement 15).
* :class:`VCUBATMCapture` cannot reach ``CONFIRMED`` while it carries any
  blocking error or has no grid at all (test requirement 17), and only a
  ``CONFIRMED`` capture ever exposes :attr:`~VCUBATMCapture.accepted_grid`
  -- a rejected or still-pending capture's numbers are structurally
  unaccepted (test requirement 18).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

PARSER_NAME = "shiori-vcub-atm-template"
PARSER_VERSION = "0.1.0"

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class VCUBCaptureStatus(StrEnum):
    """Review state of one visual capture.

    A capture starts ``PENDING_REVIEW`` and moves exactly once, by an
    explicit trader action, to ``CONFIRMED`` or ``REJECTED``. There is no
    automatic transition and no state in which values are accepted without a
    named reviewer.
    """

    PENDING_REVIEW = "PENDING_REVIEW"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


def _require_non_blank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


def _require_finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _require_iso_timestamp(value: object, field_name: str) -> str:
    _require_non_blank(value, field_name)
    assert isinstance(value, str)
    if not _ISO_UTC_RE.match(value):
        raise ValueError(
            f"{field_name} must be an ISO-8601 timestamp with an explicit offset, got {value!r}"
        )
    return value


@dataclass(frozen=True)
class VCUBTextToken:
    """One piece of text the capture saw, with its axis-aligned pixel box.

    This is the **only** contract between an image reader and the template
    parser. Everything downstream of this type is pure data, which is what
    lets Issue #181's structural test requirements run offline with no OCR
    engine installed.

    Coordinates are in image pixels with the origin at the top-left. They
    are never used as absolute constants by the parser -- only relative to
    other tokens' boxes (see ``bloomberg_vcub_atm_template``), which is what
    makes the capture tolerant of crop, DPI, and window-position changes.
    """

    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.text, "text")
        _require_finite(self.left, "left")
        _require_finite(self.top, "top")
        width = _require_finite(self.width, "width")
        height = _require_finite(self.height, "height")
        if width <= 0:
            raise ValueError(f"width must be positive, got {self.width!r}")
        if height <= 0:
            raise ValueError(f"height must be positive, got {self.height!r}")
        if self.confidence is not None:
            confidence = _require_finite(self.confidence, "confidence")
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(f"confidence must be within [0, 1], got {self.confidence!r}")

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def x_center(self) -> float:
        return self.left + self.width / 2.0

    @property
    def y_center(self) -> float:
        return self.top + self.height / 2.0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class VCUBCaptureProvenance:
    """Where one capture came from, and what read it.

    The source image itself is deliberately *not* held here. This repository
    is public and a live VCUB screenshot is proprietary Bloomberg screen
    content carrying live market data, so the capture keeps the operator's
    local reference plus a SHA-256 of the exact bytes read -- enough to
    prove later which file produced these numbers without ever publishing
    the image (Issue #181 "Screenshot handling", and
    ``docs/future_screenshot_assisted_data_capture.md`` §7's
    "policy-compliant reference or hash" case).
    """

    source_reference: str
    source_image_sha256: str
    source_image_bytes: int
    captured_at: str
    parser_name: str = PARSER_NAME
    parser_version: str = PARSER_VERSION

    def __post_init__(self) -> None:
        _require_non_blank(self.source_reference, "source_reference")
        _require_non_blank(self.source_image_sha256, "source_image_sha256")
        if not _SHA256_HEX_RE.match(self.source_image_sha256):
            raise ValueError(
                "source_image_sha256 must be 64 lower-case hex characters, "
                f"got {self.source_image_sha256!r}"
            )
        if isinstance(self.source_image_bytes, bool) or not isinstance(
            self.source_image_bytes, int
        ):
            raise ValueError(
                f"source_image_bytes must be an int, got {self.source_image_bytes!r}"
            )
        if self.source_image_bytes <= 0:
            raise ValueError(
                f"source_image_bytes must be positive, got {self.source_image_bytes!r}"
            )
        _require_iso_timestamp(self.captured_at, "captured_at")
        _require_non_blank(self.parser_name, "parser_name")
        _require_non_blank(self.parser_version, "parser_version")

    def to_dict(self) -> dict:
        return {
            "source_reference": self.source_reference,
            "source_image_sha256": self.source_image_sha256,
            "source_image_bytes": self.source_image_bytes,
            "captured_at": self.captured_at,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
        }


#: Every metadata field the ATM capture attempts to read off the screen.
#: A field is either resolved to the exact text the screen showed, or it is
#: ``None`` *and* named in ``unresolved_fields``. There is no third state.
METADATA_FIELDS: tuple[str, ...] = (
    "currency",
    "curve_config",
    "side",
    "quote_date",
    "tab",
    "vol_type",
    "source",
)


@dataclass(frozen=True)
class VCUBSourceMetadata:
    """The VCUB screen's own header context, transcribed verbatim.

    Every field is optional on purpose: Issue #181 requires metadata to be
    "extracted or explicitly marked unresolved", never invented. The
    ``__post_init__`` check below is what makes "explicitly marked" a
    structural fact rather than a convention.
    """

    currency: str | None = None
    curve_config: str | None = None
    side: str | None = None
    quote_date: str | None = None
    tab: str | None = None
    vol_type: str | None = None
    source: str | None = None
    unresolved_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.unresolved_fields, tuple):
            raise ValueError("unresolved_fields must be a tuple")
        unknown = [name for name in self.unresolved_fields if name not in METADATA_FIELDS]
        if unknown:
            raise ValueError(f"unresolved_fields names unknown metadata fields: {unknown}")
        if len(set(self.unresolved_fields)) != len(self.unresolved_fields):
            raise ValueError(f"unresolved_fields has duplicates: {self.unresolved_fields}")
        for name in METADATA_FIELDS:
            value = getattr(self, name)
            listed = name in self.unresolved_fields
            if value is None and not listed:
                raise ValueError(f"{name} is unresolved but is not listed in unresolved_fields")
            if value is not None:
                if listed:
                    raise ValueError(f"{name} is listed unresolved but carries a value")
                _require_non_blank(value, name)

    def to_dict(self) -> dict:
        payload = {name: getattr(self, name) for name in METADATA_FIELDS}
        payload["unresolved_fields"] = list(self.unresolved_fields)
        return payload


@dataclass(frozen=True)
class VCUBATMGrid:
    """An ``Expiry x Swap Tenor`` matrix in the screen's own visual order.

    ``expiry_labels`` runs down the rows and ``tenor_labels`` across the
    columns, exactly as VCUB draws them; both keep the screen's own spelling
    (``"18Mo"``, ``"4Yr"``) rather than a normalised tenor code, because
    this layer transcribes and does not interpret.

    ``values[row][column]`` is either a finite number or ``None``. ``None``
    means *this intersection was not resolved* -- Issue #181's explicit
    preference for "3Mo x 4Yr unresolved" over a number placed in the wrong
    cell. It never means zero.
    """

    expiry_labels: tuple[str, ...]
    tenor_labels: tuple[str, ...]
    values: tuple[tuple[float | None, ...], ...]

    def __post_init__(self) -> None:
        for name, labels in (
            ("expiry_labels", self.expiry_labels),
            ("tenor_labels", self.tenor_labels),
        ):
            if not isinstance(labels, tuple) or not labels:
                raise ValueError(f"{name} must be a non-empty tuple")
            for label in labels:
                _require_non_blank(label, name)
            if len(set(labels)) != len(labels):
                raise ValueError(f"{name} must not repeat a label: {labels}")
        if not isinstance(self.values, tuple) or len(self.values) != len(self.expiry_labels):
            raise ValueError(
                f"values must have one row per expiry label ({len(self.expiry_labels)})"
            )
        for row_index, row in enumerate(self.values):
            if not isinstance(row, tuple) or len(row) != len(self.tenor_labels):
                raise ValueError(
                    f"values[{row_index}] must have one entry per tenor label "
                    f"({len(self.tenor_labels)})"
                )
            for column_index, value in enumerate(row):
                if value is None:
                    continue
                _require_finite(
                    value, f"values[{row_index}][{column_index}]"
                )

    def value_at(self, expiry: str, tenor: str) -> float | None:
        """Return the value at ``expiry`` x ``tenor``, or ``None`` if unresolved.

        Raises ``KeyError`` for a label this grid does not have -- an
        unknown label is a caller bug, never a silently empty cell.
        """

        try:
            row_index = self.expiry_labels.index(expiry)
        except ValueError as exc:
            raise KeyError(f"no such expiry label: {expiry!r}") from exc
        try:
            column_index = self.tenor_labels.index(tenor)
        except ValueError as exc:
            raise KeyError(f"no such tenor label: {tenor!r}") from exc
        return self.values[row_index][column_index]

    def unresolved_cells(self) -> tuple[tuple[str, str], ...]:
        """Return every ``(expiry, tenor)`` intersection with no resolved value."""

        return tuple(
            (expiry, tenor)
            for row_index, expiry in enumerate(self.expiry_labels)
            for column_index, tenor in enumerate(self.tenor_labels)
            if self.values[row_index][column_index] is None
        )

    def to_dict(self) -> dict:
        return {
            "expiry_labels": list(self.expiry_labels),
            "tenor_labels": list(self.tenor_labels),
            "values": [list(row) for row in self.values],
        }


@dataclass(frozen=True)
class VCUBCaptureIssue:
    """One reason the capture is not trustworthy, addressed to the trader.

    ``code`` is a stable machine token (the UI groups on it); ``message`` is
    the human sentence. ``expiry``/``tenor`` are filled in whenever the issue
    is attributable to one intersection, so the trader can look straight at
    that cell on the screenshot.
    """

    code: str
    message: str
    expiry: str | None = None
    tenor: str | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.code, "code")
        _require_non_blank(self.message, "message")
        if self.expiry is not None:
            _require_non_blank(self.expiry, "expiry")
        if self.tenor is not None:
            _require_non_blank(self.tenor, "tenor")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "expiry": self.expiry,
            "tenor": self.tenor,
        }


@dataclass(frozen=True)
class VCUBATMCapture:
    """One reviewable VCUB ATM Swaptions capture.

    ``grid`` is ``None`` when the parser could not establish the matrix
    topology safely at all -- Issue #181's "the grid must fail closed if its
    topology cannot be established". When the topology *is* sound but
    individual cells are not, the grid is present with those cells
    unresolved and the reasons sit in ``blocking_errors``, so the trader can
    see exactly where the read broke rather than a bare failure.
    """

    provenance: VCUBCaptureProvenance
    metadata: VCUBSourceMetadata
    grid: VCUBATMGrid | None = None
    blocking_errors: tuple[VCUBCaptureIssue, ...] = ()
    warnings: tuple[VCUBCaptureIssue, ...] = ()
    review_status: VCUBCaptureStatus = VCUBCaptureStatus.PENDING_REVIEW
    reviewed_by: str | None = None
    reviewed_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, VCUBCaptureProvenance):
            raise ValueError("provenance must be a VCUBCaptureProvenance")
        if not isinstance(self.metadata, VCUBSourceMetadata):
            raise ValueError("metadata must be a VCUBSourceMetadata")
        if self.grid is not None and not isinstance(self.grid, VCUBATMGrid):
            raise ValueError("grid must be a VCUBATMGrid or None")
        for name, issues in (
            ("blocking_errors", self.blocking_errors),
            ("warnings", self.warnings),
        ):
            if not isinstance(issues, tuple) or any(
                not isinstance(issue, VCUBCaptureIssue) for issue in issues
            ):
                raise ValueError(f"{name} must be a tuple of VCUBCaptureIssue")
        if not isinstance(self.review_status, VCUBCaptureStatus):
            raise ValueError("review_status must be a VCUBCaptureStatus")

        if self.review_status is VCUBCaptureStatus.PENDING_REVIEW:
            if self.reviewed_by is not None or self.reviewed_at is not None:
                raise ValueError("a PENDING_REVIEW capture must not carry review provenance")
            return

        _require_non_blank(self.reviewed_by, "reviewed_by")
        _require_iso_timestamp(self.reviewed_at, "reviewed_at")

        if self.review_status is VCUBCaptureStatus.CONFIRMED:
            if self.blocking_errors:
                raise ValueError(
                    "a capture with blocking errors can never be CONFIRMED: "
                    f"{[issue.code for issue in self.blocking_errors]}"
                )
            if self.grid is None:
                raise ValueError("a capture with no reconstructed grid can never be CONFIRMED")

    @property
    def can_confirm(self) -> bool:
        """Whether a trader is allowed to accept this capture at all."""

        return (
            self.review_status is VCUBCaptureStatus.PENDING_REVIEW
            and not self.blocking_errors
            and self.grid is not None
        )

    @property
    def accepted_grid(self) -> VCUBATMGrid | None:
        """The grid **only** once a trader has confirmed it; otherwise ``None``.

        Every consumer downstream of the review flow reads this rather than
        :attr:`grid`, which is why a rejected or still-pending capture's
        numbers cannot leak into anything: there is nothing to read.
        """

        if self.review_status is not VCUBCaptureStatus.CONFIRMED:
            return None
        return self.grid

    def confirm(self, *, reviewed_by: str, reviewed_at: str) -> VCUBATMCapture:
        """Return a ``CONFIRMED`` copy, or raise if confirmation is not allowed."""

        if self.review_status is not VCUBCaptureStatus.PENDING_REVIEW:
            raise ValueError(
                f"only a PENDING_REVIEW capture can be confirmed, this one is "
                f"{self.review_status.value}"
            )
        # Blocking errors are reported before the missing-grid case: when the
        # topology itself failed both are true, and the reason is what the
        # trader needs to see, not the symptom.
        if self.blocking_errors:
            raise ValueError(
                "this capture cannot be confirmed while it has blocking errors: "
                + ", ".join(issue.code for issue in self.blocking_errors)
            )
        if self.grid is None:
            raise ValueError("this capture has no reconstructed grid and cannot be confirmed")
        return _replace_review(
            self,
            review_status=VCUBCaptureStatus.CONFIRMED,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )

    def reject(self, *, reviewed_by: str, reviewed_at: str) -> VCUBATMCapture:
        """Return a ``REJECTED`` copy. Always available while pending review."""

        if self.review_status is not VCUBCaptureStatus.PENDING_REVIEW:
            raise ValueError(
                f"only a PENDING_REVIEW capture can be rejected, this one is "
                f"{self.review_status.value}"
            )
        return _replace_review(
            self,
            review_status=VCUBCaptureStatus.REJECTED,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )

    def to_dict(self) -> dict:
        return {
            "provenance": self.provenance.to_dict(),
            "metadata": self.metadata.to_dict(),
            "grid": None if self.grid is None else self.grid.to_dict(),
            "blocking_errors": [issue.to_dict() for issue in self.blocking_errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "review_status": self.review_status.value,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "can_confirm": self.can_confirm,
        }


def _replace_review(
    capture: VCUBATMCapture,
    *,
    review_status: VCUBCaptureStatus,
    reviewed_by: str,
    reviewed_at: str,
) -> VCUBATMCapture:
    return VCUBATMCapture(
        provenance=capture.provenance,
        metadata=capture.metadata,
        grid=capture.grid,
        blocking_errors=capture.blocking_errors,
        warnings=capture.warnings,
        review_status=review_status,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )
