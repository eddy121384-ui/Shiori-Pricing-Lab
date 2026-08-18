"""Provenance and confirm/reject invariants for a VCUB visual capture (Issue #181).

These are the guarantees that stop a screenshot from becoming market data by
accident: a capture with blocking errors can never be confirmed, a rejected
capture's numbers stay unaccepted, and a confirmed one carries who confirmed
it and when.
"""

from __future__ import annotations

import pytest
from test_bloomberg_vcub_atm_template import canonical_tokens, provenance

from shiori_pricing_lab.data.bloomberg_vcub_atm_template import parse_vcub_atm_tokens
from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    PARSER_NAME,
    PARSER_VERSION,
    VCUBATMCapture,
    VCUBATMGrid,
    VCUBCaptureIssue,
    VCUBCaptureProvenance,
    VCUBCaptureStatus,
    VCUBSourceMetadata,
    VCUBTextToken,
)
from shiori_pricing_lab.data.bloomberg_vcub_ocr import (
    build_capture_provenance,
    sha256_of_image_bytes,
)

_REVIEWED_AT = "2026-08-18T09:45:00Z"


def _pending(**overrides) -> VCUBATMCapture:
    fields = {
        "provenance": provenance(),
        "metadata": VCUBSourceMetadata(
            currency="USD", unresolved_fields=tuple(
                name
                for name in ("curve_config", "side", "quote_date", "tab", "vol_type", "source")
            )
        ),
        "grid": VCUBATMGrid(("1Mo", "3Mo"), ("1Yr", "2Yr"), ((80.0, 80.25), (81.5, None))),
    }
    fields.update(overrides)
    return VCUBATMCapture(**fields)


# ---------------------------------------------------------------------------
# 16: provenance
# ---------------------------------------------------------------------------


def test_the_capture_keeps_the_source_reference_hash_and_reader_version() -> None:
    capture = parse_vcub_atm_tokens(canonical_tokens(), provenance=provenance())

    assert capture.provenance.source_reference == "vcub_atm_usd.png"
    assert capture.provenance.source_image_sha256 == "a" * 64
    assert capture.provenance.parser_name == PARSER_NAME
    assert capture.provenance.parser_version == PARSER_VERSION


def test_the_image_hash_is_the_hash_of_exactly_the_bytes_that_were_read() -> None:
    raw = b"\x89PNG\r\n\x1a\n-not-a-real-screenshot"

    built = build_capture_provenance(
        source_reference=r"C:\Users\eddy\vcub.png", raw_image=raw, captured_at=_REVIEWED_AT
    )

    assert built.source_image_sha256 == sha256_of_image_bytes(raw)
    assert built.source_image_bytes == len(raw)
    assert built.source_image_sha256 != sha256_of_image_bytes(raw + b"!")


def test_provenance_refuses_a_hash_that_is_not_a_sha256_digest() -> None:
    with pytest.raises(ValueError, match="64 lower-case hex"):
        provenance(source_image_sha256="deadbeef")


def test_provenance_refuses_a_timestamp_with_no_offset() -> None:
    with pytest.raises(ValueError, match="ISO-8601"):
        provenance(captured_at="2026-08-18 09:30:00")


# ---------------------------------------------------------------------------
# 17: confirmation is impossible while anything blocks
# ---------------------------------------------------------------------------


def test_a_capture_with_a_blocking_error_cannot_be_confirmed() -> None:
    capture = _pending(
        blocking_errors=(VCUBCaptureIssue("MALFORMED_NUMERIC_CELL", "3Mo x 4Yr reads '8S.15'"),)
    )

    assert not capture.can_confirm
    with pytest.raises(ValueError, match="MALFORMED_NUMERIC_CELL"):
        capture.confirm(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)


def test_a_capture_with_no_grid_cannot_be_confirmed() -> None:
    capture = _pending(grid=None)

    assert not capture.can_confirm
    with pytest.raises(ValueError, match="no reconstructed grid"):
        capture.confirm(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)


def test_a_confirmed_state_cannot_be_constructed_around_a_blocking_error() -> None:
    """The guard is structural, so no caller can route around ``confirm``."""

    with pytest.raises(ValueError, match="never be CONFIRMED"):
        _pending(
            blocking_errors=(VCUBCaptureIssue("DUPLICATE_CELL", "two values in 2Mo x 3Yr"),),
            review_status=VCUBCaptureStatus.CONFIRMED,
            reviewed_by="Eddy",
            reviewed_at=_REVIEWED_AT,
        )


def test_a_capture_cannot_be_confirmed_twice() -> None:
    confirmed = _pending().confirm(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    with pytest.raises(ValueError, match="only a PENDING_REVIEW capture"):
        confirmed.confirm(reviewed_by="Someone Else", reviewed_at=_REVIEWED_AT)


# ---------------------------------------------------------------------------
# 18: rejection leaves the values unaccepted
# ---------------------------------------------------------------------------


def test_rejecting_a_capture_leaves_its_values_unaccepted() -> None:
    rejected = _pending().reject(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    assert rejected.review_status is VCUBCaptureStatus.REJECTED
    assert rejected.accepted_grid is None
    assert rejected.grid is not None  # still visible for the audit trail
    assert not rejected.can_confirm


def test_a_capture_still_under_review_exposes_no_accepted_values() -> None:
    assert _pending().accepted_grid is None


def test_a_capture_with_blocking_errors_can_still_be_rejected() -> None:
    capture = _pending(blocking_errors=(VCUBCaptureIssue("DUPLICATE_CELL", "two values"),))

    rejected = capture.reject(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    assert rejected.review_status is VCUBCaptureStatus.REJECTED
    assert rejected.accepted_grid is None


# ---------------------------------------------------------------------------
# 19: a confirmed capture records who and when
# ---------------------------------------------------------------------------


def test_a_confirmed_capture_records_its_confirmation_provenance() -> None:
    confirmed = _pending().confirm(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    assert confirmed.review_status is VCUBCaptureStatus.CONFIRMED
    assert confirmed.reviewed_by == "Eddy"
    assert confirmed.reviewed_at == _REVIEWED_AT
    assert confirmed.accepted_grid is confirmed.grid
    assert confirmed.provenance.source_image_sha256 == "a" * 64


def test_a_review_state_cannot_be_recorded_without_a_named_reviewer() -> None:
    with pytest.raises(ValueError, match="reviewed_by"):
        _pending(review_status=VCUBCaptureStatus.REJECTED, reviewed_at=_REVIEWED_AT)


def test_a_pending_capture_cannot_carry_review_provenance() -> None:
    with pytest.raises(ValueError, match="PENDING_REVIEW capture must not carry"):
        _pending(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)


def test_the_serialised_capture_carries_the_whole_review_trail() -> None:
    payload = _pending().confirm(reviewed_by="Eddy", reviewed_at=_REVIEWED_AT).to_dict()

    assert payload["review_status"] == "CONFIRMED"
    assert payload["reviewed_by"] == "Eddy"
    assert payload["reviewed_at"] == _REVIEWED_AT
    assert payload["provenance"]["source_image_sha256"] == "a" * 64
    assert payload["can_confirm"] is False


# ---------------------------------------------------------------------------
# Record-level validation
# ---------------------------------------------------------------------------


def test_metadata_cannot_hold_a_value_for_a_field_it_calls_unresolved() -> None:
    with pytest.raises(ValueError, match="listed unresolved but carries a value"):
        VCUBSourceMetadata(
            currency="USD",
            unresolved_fields=(
                "currency", "curve_config", "side", "quote_date", "tab", "vol_type", "source"
            ),
        )


def test_metadata_cannot_leave_a_missing_field_off_the_unresolved_list() -> None:
    with pytest.raises(ValueError, match="not listed in unresolved_fields"):
        VCUBSourceMetadata(currency="USD", unresolved_fields=())


def test_a_grid_cannot_repeat_a_label_on_either_axis() -> None:
    with pytest.raises(ValueError, match="must not repeat a label"):
        VCUBATMGrid(("1Mo", "1Mo"), ("1Yr",), ((80.0,), (81.0,)))


def test_a_grid_cannot_hold_a_non_finite_value() -> None:
    with pytest.raises(ValueError, match="finite number"):
        VCUBATMGrid(("1Mo",), ("1Yr",), ((float("nan"),),))


def test_a_grid_rejects_a_row_whose_width_does_not_match_the_tenor_axis() -> None:
    with pytest.raises(ValueError, match="one entry per tenor label"):
        VCUBATMGrid(("1Mo",), ("1Yr", "2Yr"), ((80.0,),))


def test_asking_a_grid_for_a_label_it_does_not_have_is_an_error_not_an_empty_cell() -> None:
    grid = VCUBATMGrid(("1Mo",), ("1Yr",), ((80.0,),))

    with pytest.raises(KeyError, match="no such tenor label"):
        grid.value_at("1Mo", "30Yr")


def test_a_text_token_must_have_a_real_box() -> None:
    with pytest.raises(ValueError, match="width must be positive"):
        VCUBTextToken(text="1Mo", left=0.0, top=0.0, width=0.0, height=10.0)


def test_a_text_token_confidence_must_be_a_probability() -> None:
    with pytest.raises(ValueError, match="within \\[0, 1\\]"):
        VCUBTextToken(text="1Mo", left=0.0, top=0.0, width=10.0, height=10.0, confidence=1.4)


def test_the_provenance_default_reader_identity_is_this_parser() -> None:
    built = VCUBCaptureProvenance(
        source_reference="x.png",
        source_image_sha256="b" * 64,
        source_image_bytes=1,
        captured_at=_REVIEWED_AT,
    )

    assert (built.parser_name, built.parser_version) == (PARSER_NAME, PARSER_VERSION)
