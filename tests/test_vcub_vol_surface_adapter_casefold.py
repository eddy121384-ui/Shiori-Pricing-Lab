"""Regression for PR #186: parser and adapter must accept the same vol-type casing.

The merge itself now normalises ``vol_type`` to the canonical
``NORMAL_VOL_SKEW_TYPE`` spelling once every screenshot's OCR reading agrees
on it after :func:`normalise_text`/``casefold()`` (Codex review round on PR
#186, P2). The adapter's own case-insensitive acceptance below this stays as
a second, independent guard -- it does not have to rewrite anything itself,
because by the time a capture reaches it the merge has already settled on
one spelling.
"""

from test_bloomberg_vcub_otm_template import (
    SLICE_A,
    SLICE_B,
    SLICE_C,
    read,
    screenshot_tokens,
)

from shiori_pricing_lab.data.bloomberg_vcub_otm_capture import NORMAL_VOL_SKEW_TYPE
from shiori_pricing_lab.data.bloomberg_vcub_otm_template import merge_vcub_otm_reads
from shiori_pricing_lab.data.vcub_vol_surface_adapter import (
    canonical_surface_from_confirmed_otm_capture,
)
from shiori_pricing_lab.data.vol_surface_store import SaveStatus, VolSurfaceStore


def test_supported_vol_type_with_ocr_case_difference_normalises_and_persists(tmp_path) -> None:
    chrome = {"vol_type": ("NORMAL", "VOL", "SKEW▾")}
    reads = [
        read(
            screenshot_tokens(rows=SLICE_A, chrome=chrome),
            reference="shot-a.png",
            digest_seed="a",
        ),
        read(
            screenshot_tokens(rows=SLICE_B, chrome=chrome),
            reference="shot-b.png",
            digest_seed="b",
        ),
        read(
            screenshot_tokens(rows=SLICE_C, chrome=chrome),
            reference="shot-c.png",
            digest_seed="c",
        ),
    ]
    capture = merge_vcub_otm_reads(reads)

    assert capture.blocking_errors == ()
    assert capture.metadata.vol_type == NORMAL_VOL_SKEW_TYPE
    confirmed = capture.confirm(reviewed_by="Eddy", reviewed_at="2026-08-21T09:31:00Z")
    surface = canonical_surface_from_confirmed_otm_capture(
        confirmed, capture_id="0123456789abcdef0123456789abcdef"
    )

    assert surface.identity.vol_type == NORMAL_VOL_SKEW_TYPE
    outcome = VolSurfaceStore(tmp_path / "vol_surfaces.sqlite3").save_confirmed_surface(surface)
    assert outcome.status is SaveStatus.SAVED
