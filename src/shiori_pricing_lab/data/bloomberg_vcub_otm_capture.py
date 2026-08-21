"""Typed records for the Bloomberg VCUB **OTM Swaptions / SABR** visual
capture, reconstructed from one or more overlapping screenshots (Issue #185).

Like :mod:`shiori_pricing_lab.data.bloomberg_vcub_capture`, this module holds
only the *shapes* the capture produces and the confirm/reject state machine
that guards them. No image handling, no OCR, no geometry, and no import of
anything from :mod:`shiori_pricing_lab.pricing`.

**The observed screen contract, and nothing wider.** Issue #185's target is
one exact screen state, and every constant below names a piece of it:

* tab ``OTM Swaptions / SABR``;
* Type ``Normal Vol Skew``;
* display mode ``Spread``;
* row key ``Term x Tenor`` -- ``1Mo x 1Yr``, ``3Mo x 10Yr``, ``30Yr x 30Yr``;
* strike headers ``-200bps ... -25bps, ATM, 25bps ... 200bps``.

Nothing here generalises past that. A screen whose Type or display mode says
something else is refused by the parser rather than transcribed under an
assumption about what its numbers mean.

**What the numbers on that screen are.** With Type ``Normal Vol Skew`` and
display ``Spread``, the ATM column carries an absolute normal vol and every
other column carries a *spread to it*, which is why several of them are
negative on a live screen. The two are therefore kept distinguishable all
the way into the canonical store rather than flattened into one "volatility"
column: :class:`VCUBOTMStrike` marks the ATM column by carrying no offset at
all, and the adapter files that column's numbers as absolute vols and the
rest as spreads. Nothing in this slice adds a spread to an ATM vol -- that
conversion, and every interpolation around it, belongs to the later vol-cube
issue.

**One capture, several screenshots.** The screen is longer than one
viewport, so a capture session is normally two to four vertically
overlapping images. They are parsed independently and merged by the
``Term x Tenor`` row key -- never stitched as pixels, and never ordered by
which file the operator picked first. :attr:`VCUBOTMCapture.sources` keeps
every image that formed the capture, and :attr:`VCUBOTMCapture.coverage`
records what each one contributed, so a stored surface can never claim a
single screenshot produced it.

**Fail-closed by construction**, exactly as in Issue #181:

* :class:`VCUBOTMTable` cannot hold two rows for one ``Term x Tenor``, rows
  out of canonical order, a repeated strike header, or a row whose value
  count does not match the strike axis;
* :class:`VCUBOTMCapture` cannot reach ``CONFIRMED`` while it carries any
  blocking error or has no table at all, and only a ``CONFIRMED`` capture
  exposes :attr:`~VCUBOTMCapture.accepted_table`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    VCUBCaptureProvenance,
    VCUBCaptureStatus,
)
from shiori_pricing_lab.data.bloomberg_vcub_screen_reader import (
    is_tenor_label,
    tenor_label_nominal_days,
)

#: The tab this capture recognises, spelled as the screen spells it.
OTM_SWAPTIONS_SABR_TAB = "OTM Swaptions / SABR"

#: The corner cell of the OTM matrix: the strike headers share its text line,
#: the ``Term x Tenor`` row labels share its column.
TERM_TENOR_ANCHOR = "Term x Tenor"

#: The one Type this capture knows how to read, and the one display mode.
#: Both decide what the numbers *mean*, so both are required to resolve and
#: to match: a screen showing anything else is refused rather than
#: transcribed under this one's semantics.
NORMAL_VOL_SKEW_TYPE = "Normal Vol Skew"
SPREAD_DISPLAY_MODE = "Spread"

PARSER_NAME = "shiori-vcub-otm-sabr-template"
PARSER_VERSION = "0.1.0"

#: The complete semantic row set this screen is expected to hold, as the
#: Cartesian product of the terms and tenors observed on the live
#: ``OTM Swaptions / SABR`` screen (Eddy's decision on PR #186, replacing a
#: proposed minimum screenshot count).
#:
#: **Why a template rather than a count.** How many screenshots an operator
#: took proves nothing about coverage: three of them can show half the table
#: as easily as one can. What does prove it is the row set itself, so a
#: capture is complete when it holds *these* coordinates and no others.
#: A row missing from this set blocks the capture and is named; a row outside
#: it blocks too, rather than silently widening the template to whatever a
#: screenshot happened to show.
#:
#: Both lists are transcribed from the screen, not derived from any tenor
#: convention, and this is the one place either is written down: a screen
#: whose rows genuinely differ is corrected by editing these two tuples,
#: never by relaxing the check that reads them.
EXPECTED_TERMS: tuple[str, ...] = (
    "1Mo",
    "3Mo",
    "6Mo",
    "9Mo",
    "1Yr",
    "2Yr",
    "3Yr",
    "5Yr",
    "7Yr",
    "10Yr",
    "15Yr",
    "20Yr",
    "30Yr",
)
EXPECTED_TENORS: tuple[str, ...] = ("1Yr", "2Yr", "5Yr", "10Yr", "15Yr", "20Yr", "30Yr")

#: Every ``(term, tenor)`` coordinate the complete surface carries: 13 x 7 =
#: 91 rows, each with the nine strike columns.
EXPECTED_ROWS: tuple[tuple[str, str], ...] = tuple(
    (term, tenor) for term in EXPECTED_TERMS for tenor in EXPECTED_TENORS
)

#: The strike axis the complete surface carries, left to right, as yield
#: offsets from ATM in basis points -- ``None`` being the ATM column itself.
#: The other half of the same completeness question: a screenshot cropped at
#: the left or right edge loses a whole strike column cleanly, with no gap for
#: the pitch check to see and no value left over for the outside-column check
#: to refuse, so a table can be 91 rows deep and still be missing a column
#: (Codex review round 2, PR #186). Offsets rather than label text because
#: the offset is the coordinate; how the screen spells it is not.
EXPECTED_STRIKE_OFFSETS_BP: tuple[float | None, ...] = (
    -200.0,
    -100.0,
    -50.0,
    -25.0,
    None,
    25.0,
    50.0,
    100.0,
    200.0,
)


def strike_label_for_offset(offset_bp: float | None) -> str:
    """How this screen heads the column at ``offset_bp``.

    Used only to name a coordinate in a message; a captured column keeps the
    screen's own header text.
    """

    return "ATM" if offset_bp is None else f"{offset_bp:g}bps"

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


# The three field-level checks below are deliberately module-local copies,
# following this package's stated convention (see ``data/_validation``): a
# sibling module's private validator is not imported for three small checks.
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


#: Every metadata field the OTM/SABR capture attempts to read off the screen.
#: The seven the ATM capture reads, plus ``display_mode``: this screen has a
#: display selector (``Spread``) that the ATM tab does not, and what it says
#: decides what the numbers in the table *are*. A field is either resolved to
#: the exact text the screen showed, or it is ``None`` *and* named in
#: ``unresolved_fields``. There is no third state.
OTM_METADATA_FIELDS: tuple[str, ...] = (
    "currency",
    "curve_config",
    "side",
    "quote_date",
    "tab",
    "vol_type",
    "source",
    "display_mode",
)


@dataclass(frozen=True)
class VCUBOTMSourceMetadata:
    """The OTM/SABR screen's own header context, transcribed verbatim.

    A separate shape from the ATM capture's metadata rather than a widened
    one: adding ``display_mode`` to that type would make every ATM capture
    report a field its screen has no selector for, permanently unresolved.
    The two screens are asked different questions, so they answer with
    different records -- and both keep the same either/or discipline, which
    is what stops an unresolved field being quietly filled in later.
    """

    currency: str | None = None
    curve_config: str | None = None
    side: str | None = None
    quote_date: str | None = None
    tab: str | None = None
    vol_type: str | None = None
    source: str | None = None
    display_mode: str | None = None
    unresolved_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.unresolved_fields, tuple):
            raise ValueError("unresolved_fields must be a tuple")
        unknown = [name for name in self.unresolved_fields if name not in OTM_METADATA_FIELDS]
        if unknown:
            raise ValueError(f"unresolved_fields names unknown metadata fields: {unknown}")
        if len(set(self.unresolved_fields)) != len(self.unresolved_fields):
            raise ValueError(f"unresolved_fields has duplicates: {self.unresolved_fields}")
        for name in OTM_METADATA_FIELDS:
            value = getattr(self, name)
            listed = name in self.unresolved_fields
            if value is None and not listed:
                raise ValueError(f"{name} is unresolved but is not listed in unresolved_fields")
            if value is not None:
                if listed:
                    raise ValueError(f"{name} is listed unresolved but carries a value")
                _require_non_blank(value, name)

    def to_dict(self) -> dict:
        payload = {name: getattr(self, name) for name in OTM_METADATA_FIELDS}
        payload["unresolved_fields"] = list(self.unresolved_fields)
        return payload



@dataclass(frozen=True)
class VCUBOTMCaptureIssue:
    """One reason the capture is not trustworthy, addressed to the trader.

    ``code`` is a stable machine token (the UI groups on it); ``message`` is
    the human sentence. ``row`` and ``strike`` are filled in whenever the
    issue belongs to one coordinate, and ``source`` names the screenshot it
    was read from -- with several images in one session, "which file" is
    part of "where do I look".
    """

    code: str
    message: str
    row: str | None = None
    strike: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.code, "code")
        _require_non_blank(self.message, "message")
        for name in ("row", "strike", "source"):
            value = getattr(self, name)
            if value is not None:
                _require_non_blank(value, name)

    def with_source(self, source: str) -> VCUBOTMCaptureIssue:
        """This issue, attributed to the screenshot it came from."""

        return VCUBOTMCaptureIssue(
            code=self.code,
            message=self.message,
            row=self.row,
            strike=self.strike,
            source=source,
        )

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "row": self.row,
            "strike": self.strike,
            "source": self.source,
        }


@dataclass(frozen=True)
class VCUBOTMStrike:
    """One strike column, as the screen's own header names it.

    ``offset_bp`` is the yield offset from ATM in basis points, taken from
    the header text itself (``-200bps`` -> ``-200.0``). It is ``None`` for
    the column headed ``ATM``, which is not an offset the screen states but
    the point the others are measured from -- and whose number is an
    absolute vol rather than a spread.
    """

    label: str
    offset_bp: float | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.label, "label")
        if self.offset_bp is not None:
            offset = _require_finite(self.offset_bp, "offset_bp")
            if offset == 0.0:
                raise ValueError(
                    "a 0bp offset column cannot be told apart from the ATM column; "
                    "the ATM column carries no offset at all"
                )
            object.__setattr__(self, "offset_bp", offset)

    @property
    def is_atm(self) -> bool:
        return self.offset_bp is None

    @property
    def sort_key(self) -> float:
        """Where this column sits on the strike axis, ATM at the origin."""

        return 0.0 if self.offset_bp is None else self.offset_bp

    def to_dict(self) -> dict:
        return {"label": self.label, "offset_bp": self.offset_bp}


@dataclass(frozen=True)
class VCUBOTMRow:
    """One ``Term x Tenor`` row and the values read across its strike axis.

    ``term`` and ``tenor`` keep the screen's own spelling (``"18Mo"`` stays
    ``"18Mo"``). They are held apart rather than as one ``"1Mo x 1Yr"``
    string because they are two coordinates: the canonical store files them
    as ``expiry`` and ``underlying_tenor``, and the merge across screenshots
    keys on the pair.

    ``values[i]`` belongs to strike column ``i`` and is either a finite
    number or ``None``. ``None`` means *this intersection was not resolved*,
    never zero -- on this screen a genuine ``0.00`` spread and an unread cell
    are entirely different readings.
    """

    term: str
    tenor: str
    values: tuple[float | None, ...]

    def __post_init__(self) -> None:
        for name in ("term", "tenor"):
            value = getattr(self, name)
            _require_non_blank(value, name)
            if not is_tenor_label(value):
                raise ValueError(
                    f"{name} must read as a tenor bucket such as '3Mo' or '10Yr', "
                    f"got {value!r}"
                )
        if not isinstance(self.values, tuple):
            raise ValueError("values must be a tuple")
        for index, value in enumerate(self.values):
            if value is not None:
                _require_finite(value, f"values[{index}]")

    @property
    def label(self) -> str:
        """This row's coordinate, as one string, for messages and the UI."""

        return f"{self.term} x {self.tenor}"

    @property
    def sort_key(self) -> tuple[float, float]:
        """Where this row sits in the canonical axis order.

        Nominal days, term first: the same ordering VCUB draws down the
        screen, derived from the labels themselves rather than from which
        screenshot happened to contain the row.
        """

        term_days = tenor_label_nominal_days(self.term)
        tenor_days = tenor_label_nominal_days(self.tenor)
        assert term_days is not None and tenor_days is not None  # both validated above
        return (term_days, tenor_days)

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "tenor": self.tenor,
            "label": self.label,
            "values": list(self.values),
        }


@dataclass(frozen=True)
class VCUBOTMTable:
    """The reconstructed ``Term x Tenor`` by strike matrix.

    Rows run in canonical order -- by term, then by tenor, both in nominal
    days read off the labels -- so the table a trader reviews is in the
    screen's own order regardless of the order the screenshots were picked
    in. Strike columns run left to right as the screen draws them, which the
    strike offsets must agree with.
    """

    strikes: tuple[VCUBOTMStrike, ...]
    rows: tuple[VCUBOTMRow, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.strikes, tuple) or not self.strikes:
            raise ValueError("strikes must be a non-empty tuple of VCUBOTMStrike")
        if any(not isinstance(strike, VCUBOTMStrike) for strike in self.strikes):
            raise ValueError("strikes must be a non-empty tuple of VCUBOTMStrike")
        labels = [strike.label for strike in self.strikes]
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        if duplicates:
            raise ValueError(f"strike headers repeat {duplicates}")
        atm_columns = [strike for strike in self.strikes if strike.is_atm]
        if len(atm_columns) != 1:
            raise ValueError(
                "the strike axis must carry exactly one ATM column -- it is what every "
                f"other column's spread is measured from; got {len(atm_columns)}"
            )
        keys = [strike.sort_key for strike in self.strikes]
        for index in range(len(keys) - 1):
            if keys[index + 1] <= keys[index]:
                raise ValueError(
                    "strike headers must increase left to right across the axis "
                    f"({labels[index]!r} then {labels[index + 1]!r})"
                )

        if not isinstance(self.rows, tuple) or not self.rows:
            raise ValueError("rows must be a non-empty tuple of VCUBOTMRow")
        if any(not isinstance(row, VCUBOTMRow) for row in self.rows):
            raise ValueError("rows must be a non-empty tuple of VCUBOTMRow")
        for row in self.rows:
            if len(row.values) != len(self.strikes):
                raise ValueError(
                    f"row {row.label} carries {len(row.values)} values against "
                    f"{len(self.strikes)} strike columns"
                )
        coordinates = [(row.term, row.tenor) for row in self.rows]
        repeated = sorted({key for key in coordinates if coordinates.count(key) > 1})
        if repeated:
            raise ValueError(f"rows repeat a Term x Tenor coordinate: {repeated}")
        for index in range(len(self.rows) - 1):
            if self.rows[index + 1].sort_key <= self.rows[index].sort_key:
                raise ValueError(
                    "rows must run in canonical Term x Tenor order "
                    f"({self.rows[index].label!r} then {self.rows[index + 1].label!r})"
                )

    @property
    def strike_labels(self) -> tuple[str, ...]:
        return tuple(strike.label for strike in self.strikes)

    @property
    def row_labels(self) -> tuple[str, ...]:
        return tuple(row.label for row in self.rows)

    def value_at(self, term: str, tenor: str, strike_label: str) -> float | None:
        """The value at one coordinate, or ``None`` if it was not resolved.

        Raises ``KeyError`` for a coordinate this table does not have -- an
        unknown coordinate is a caller bug, never a silently empty cell.
        """

        try:
            column = self.strike_labels.index(strike_label)
        except ValueError as exc:
            raise KeyError(f"no such strike column: {strike_label!r}") from exc
        for row in self.rows:
            if (row.term, row.tenor) == (term, tenor):
                return row.values[column]
        raise KeyError(f"no such row: {term!r} x {tenor!r}")

    def missing_expected_rows(self) -> tuple[str, ...]:
        """Which of :data:`EXPECTED_ROWS` this table does not hold, in order.

        The measure of a complete capture. Empty means every coordinate the
        screen is expected to carry was reconstructed -- from however many
        screenshots it took, which this check deliberately knows nothing
        about.
        """

        held = {(row.term, row.tenor) for row in self.rows}
        return tuple(
            f"{term} x {tenor}" for term, tenor in EXPECTED_ROWS if (term, tenor) not in held
        )

    def unexpected_rows(self) -> tuple[str, ...]:
        """Rows this table holds that the expected set does not name.

        A row outside the template is not a bonus: it means the screen was
        not the one this parser knows, or a label was misread into a
        coordinate that happens to be legal. Either way the template is not
        widened to fit it.
        """

        expected = set(EXPECTED_ROWS)
        return tuple(row.label for row in self.rows if (row.term, row.tenor) not in expected)

    def missing_expected_strikes(self) -> tuple[str, ...]:
        """Which of :data:`EXPECTED_STRIKE_OFFSETS_BP` this table does not hold.

        Checked as its own coordinate: every screenshot of a session can be
        cropped at the same vertical edge, which removes a strike column
        *and* its values together and so trips none of the geometry guards.
        """

        held = {strike.offset_bp for strike in self.strikes}
        return tuple(
            strike_label_for_offset(offset)
            for offset in EXPECTED_STRIKE_OFFSETS_BP
            if offset not in held
        )

    def unexpected_strikes(self) -> tuple[str, ...]:
        """Strike columns this table holds that the expected axis does not name."""

        expected = set(EXPECTED_STRIKE_OFFSETS_BP)
        return tuple(
            strike.label for strike in self.strikes if strike.offset_bp not in expected
        )

    @property
    def is_complete(self) -> bool:
        """Whether this table is exactly the expected surface, no more, no less.

        Both coordinates: the 91 ``Term x Tenor`` rows *and* the nine strike
        columns each of them carries. A capture short of either is part of
        the screen rather than the screen.
        """

        return not (
            self.missing_expected_rows()
            or self.unexpected_rows()
            or self.missing_expected_strikes()
            or self.unexpected_strikes()
        )

    def unresolved_cells(self) -> tuple[tuple[str, str], ...]:
        """Every ``(row label, strike label)`` intersection with no value."""

        return tuple(
            (row.label, strike.label)
            for row in self.rows
            for index, strike in enumerate(self.strikes)
            if row.values[index] is None
        )

    def to_dict(self) -> dict:
        return {
            "strikes": [strike.to_dict() for strike in self.strikes],
            "rows": [row.to_dict() for row in self.rows],
        }


@dataclass(frozen=True)
class VCUBOTMSourceCoverage:
    """What one screenshot contributed to the merged capture.

    Kept so the review UI can answer "which image did this row come from,
    and where did two images overlap" without the trader re-deriving it, and
    so a capture that silently lost an image's rows is visible rather than
    inferred.
    """

    source_reference: str
    source_image_sha256: str
    row_labels: tuple[str, ...]
    shared_row_labels: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_blank(self.source_reference, "source_reference")
        _require_non_blank(self.source_image_sha256, "source_image_sha256")
        for name in ("row_labels", "shared_row_labels"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(
                not isinstance(label, str) or not label.strip() for label in value
            ):
                raise ValueError(f"{name} must be a tuple of non-blank strings")
        unknown = set(self.shared_row_labels) - set(self.row_labels)
        if unknown:
            raise ValueError(
                f"shared_row_labels names rows this image did not read: {sorted(unknown)}"
            )

    def to_dict(self) -> dict:
        return {
            "source_reference": self.source_reference,
            "source_image_sha256": self.source_image_sha256,
            "row_count": len(self.row_labels),
            "row_labels": list(self.row_labels),
            "first_row": self.row_labels[0] if self.row_labels else None,
            "last_row": self.row_labels[-1] if self.row_labels else None,
            "shared_row_count": len(self.shared_row_labels),
            "shared_row_labels": list(self.shared_row_labels),
        }


@dataclass(frozen=True)
class VCUBOTMImageRead:
    """What one screenshot on its own said, before anything was merged.

    Deliberately a separate shape from the capture: a capture is one
    reviewable decision over the whole session, while this is the evidence
    one image contributed. Keeping them apart is what lets the merge compare
    two images' readings of the same row instead of letting the second
    overwrite the first.
    """

    provenance: VCUBCaptureProvenance
    metadata: VCUBOTMSourceMetadata
    table: VCUBOTMTable | None = None
    blocking_errors: tuple[VCUBOTMCaptureIssue, ...] = ()
    warnings: tuple[VCUBOTMCaptureIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, VCUBCaptureProvenance):
            raise ValueError("provenance must be a VCUBCaptureProvenance")
        if not isinstance(self.metadata, VCUBOTMSourceMetadata):
            raise ValueError("metadata must be a VCUBOTMSourceMetadata")
        if self.table is not None and not isinstance(self.table, VCUBOTMTable):
            raise ValueError("table must be a VCUBOTMTable or None")
        _require_issue_tuples(self)


@dataclass(frozen=True)
class VCUBOTMCapture:
    """One reviewable OTM/SABR capture, reconstructed from ``sources``.

    ``table`` is ``None`` when no screenshot yielded a topology safe enough
    to merge at all. When some did, the merged table is present with
    unresolved intersections left as ``None`` and every reason to distrust
    the read listed in ``blocking_errors`` -- so the trader sees exactly
    where the read broke rather than a bare failure.

    Every image in ``sources`` is a distinct file: the same screenshot
    supplied twice is refused upstream rather than counted twice in the
    provenance of a stored surface. They share one ``captured_at`` and one
    parser because they are one capture session, read in one pass.
    """

    sources: tuple[VCUBCaptureProvenance, ...]
    metadata: VCUBOTMSourceMetadata
    table: VCUBOTMTable | None = None
    coverage: tuple[VCUBOTMSourceCoverage, ...] = ()
    blocking_errors: tuple[VCUBOTMCaptureIssue, ...] = ()
    warnings: tuple[VCUBOTMCaptureIssue, ...] = ()
    review_status: VCUBCaptureStatus = VCUBCaptureStatus.PENDING_REVIEW
    reviewed_by: str | None = None
    reviewed_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("sources must be a non-empty tuple of VCUBCaptureProvenance")
        if any(not isinstance(source, VCUBCaptureProvenance) for source in self.sources):
            raise ValueError("sources must be a non-empty tuple of VCUBCaptureProvenance")
        digests = [source.source_image_sha256 for source in self.sources]
        if len(set(digests)) != len(digests):
            raise ValueError(
                "the same image cannot appear twice in one capture: "
                f"{sorted({digest for digest in digests if digests.count(digest) > 1})}"
            )
        first = self.sources[0]
        for source in self.sources[1:]:
            if (source.captured_at, source.parser_name, source.parser_version) != (
                first.captured_at,
                first.parser_name,
                first.parser_version,
            ):
                raise ValueError(
                    "every image in one capture session is read in the same pass by the same "
                    "parser; these sources disagree on captured_at or parser"
                )
        if not isinstance(self.metadata, VCUBOTMSourceMetadata):
            raise ValueError("metadata must be a VCUBOTMSourceMetadata")
        if self.table is not None and not isinstance(self.table, VCUBOTMTable):
            raise ValueError("table must be a VCUBOTMTable or None")
        if not isinstance(self.coverage, tuple) or any(
            not isinstance(item, VCUBOTMSourceCoverage) for item in self.coverage
        ):
            raise ValueError("coverage must be a tuple of VCUBOTMSourceCoverage")
        _require_issue_tuples(self)
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
            if self.table is None:
                raise ValueError("a capture with no reconstructed table can never be CONFIRMED")
            # Structural, not merely reported: an incomplete or over-wide
            # surface cannot become a CONFIRMED capture at all, so it cannot
            # reach the canonical store through any path (Eddy's decision on
            # PR #186).
            if not self.table.is_complete:
                raise ValueError(
                    "a capture whose coordinates are not the expected surface can never be "
                    f"CONFIRMED: {_incompleteness(self.table)}"
                )

    @property
    def can_confirm(self) -> bool:
        """Whether a trader is allowed to accept this capture at all.

        A partial surface is never confirmable, however cleanly it was read:
        the merge raises a blocking error for it, and this reads the table
        itself as well so the two can never disagree.
        """

        return (
            self.review_status is VCUBCaptureStatus.PENDING_REVIEW
            and not self.blocking_errors
            and self.table is not None
            and self.table.is_complete
        )

    @property
    def accepted_table(self) -> VCUBOTMTable | None:
        """The table **only** once a trader has confirmed it; otherwise ``None``."""

        if self.review_status is not VCUBCaptureStatus.CONFIRMED:
            return None
        return self.table

    def confirm(self, *, reviewed_by: str, reviewed_at: str) -> VCUBOTMCapture:
        """Return a ``CONFIRMED`` copy, or raise if confirmation is not allowed."""

        if self.review_status is not VCUBCaptureStatus.PENDING_REVIEW:
            raise ValueError(
                f"only a PENDING_REVIEW capture can be confirmed, this one is "
                f"{self.review_status.value}"
            )
        # Blocking errors are reported before the missing-table case: when the
        # topology itself failed both are true, and the reason is what the
        # trader needs to see, not the symptom.
        if self.blocking_errors:
            raise ValueError(
                "this capture cannot be confirmed while it has blocking errors: "
                + ", ".join(issue.code for issue in self.blocking_errors)
            )
        if self.table is None:
            raise ValueError("this capture has no reconstructed table and cannot be confirmed")
        if not self.table.is_complete:
            raise ValueError(
                "this capture does not hold the complete expected surface and cannot be "
                f"confirmed: {_incompleteness(self.table)}"
            )
        return _replace_review(
            self,
            review_status=VCUBCaptureStatus.CONFIRMED,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )

    def reject(self, *, reviewed_by: str, reviewed_at: str) -> VCUBOTMCapture:
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
            "sources": [source.to_dict() for source in self.sources],
            "metadata": self.metadata.to_dict(),
            "table": None if self.table is None else self.table.to_dict(),
            "coverage": [item.to_dict() for item in self.coverage],
            # The rows the expected surface names and this capture does not
            # hold. Sent even when empty: a review that cannot say "nothing
            # is missing" is not a review of completeness (Eddy's decision on
            # PR #186).
            "missing_rows": [] if self.table is None else list(
                self.table.missing_expected_rows()
            ),
            "unexpected_rows": [] if self.table is None else list(
                self.table.unexpected_rows()
            ),
            "missing_strikes": [] if self.table is None else list(
                self.table.missing_expected_strikes()
            ),
            "unexpected_strikes": [] if self.table is None else list(
                self.table.unexpected_strikes()
            ),
            "expected_row_count": len(EXPECTED_ROWS),
            "expected_strike_count": len(EXPECTED_STRIKE_OFFSETS_BP),
            "blocking_errors": [issue.to_dict() for issue in self.blocking_errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "review_status": self.review_status.value,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "can_confirm": self.can_confirm,
        }


def _incompleteness(table: VCUBOTMTable) -> str:
    """How a table falls short of the expected surface, in one sentence."""

    return (
        f"{len(table.missing_expected_rows())} expected rows missing, "
        f"{len(table.unexpected_rows())} rows outside the template, "
        f"{len(table.missing_expected_strikes())} expected strike columns missing, "
        f"{len(table.unexpected_strikes())} strike columns outside the axis"
    )


def _require_issue_tuples(record: VCUBOTMImageRead | VCUBOTMCapture) -> None:
    for name in ("blocking_errors", "warnings"):
        issues = getattr(record, name)
        if not isinstance(issues, tuple) or any(
            not isinstance(issue, VCUBOTMCaptureIssue) for issue in issues
        ):
            raise ValueError(f"{name} must be a tuple of VCUBOTMCaptureIssue")


def _replace_review(
    capture: VCUBOTMCapture,
    *,
    review_status: VCUBCaptureStatus,
    reviewed_by: str,
    reviewed_at: str,
) -> VCUBOTMCapture:
    return VCUBOTMCapture(
        sources=capture.sources,
        metadata=capture.metadata,
        table=capture.table,
        coverage=capture.coverage,
        blocking_errors=capture.blocking_errors,
        warnings=capture.warnings,
        review_status=review_status,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )
