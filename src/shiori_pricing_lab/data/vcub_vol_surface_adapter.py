"""Turn one **confirmed** VCUB ATM capture into a canonical vol surface
(Issue #183).

The one-way bridge between Issue #181's transcription shapes and Issue
#183's canonical model. It is a separate module on purpose: the canonical
model must not know what a screenshot is, and the SQLite store must not know
what Bloomberg is, so the vendor-specific mapping lives here and nowhere
else. A later OTM/SABR capture adds its own adapter beside this one and
reuses the same model and the same store.

**It transcribes the transcription.** Nothing is renamed, re-based,
converted, interpolated, or filled in. The screen's own spellings survive:
``"18Mo"`` stays ``"18Mo"``, ``"Normal Vol (OIS)"`` stays that string, and
the quote date stays the text the screen drew (``"08/18/26"``) rather than
becoming a parsed calendar date. An unresolved metadata field arrives here
as ``None`` and leaves as ``None``, named in the identity's own unresolved
list.
"""

from __future__ import annotations

from shiori_pricing_lab.data.bloomberg_vcub_atm_template import ATM_SWAPTIONS_TAB
from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    VCUBATMCapture,
    VCUBCaptureStatus,
)
from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfaceIdentity,
    VolSurfacePoint,
    VolSurfaceProvenance,
    VolSurfaceType,
)

#: How a capture's metadata field maps onto an identity field. ``quote_date``
#: becomes ``business_date``: same value, the canonical name for it. ``tab``
#: is not carried across -- it decides ``surface_type`` below and would
#: otherwise be the same fact stored twice.
_METADATA_TO_IDENTITY: dict[str, str] = {
    "quote_date": "business_date",
    "currency": "currency",
    "curve_config": "curve_config",
    "side": "side",
    "vol_type": "vol_type",
    "source": "source",
}


class UnconfirmedCaptureError(ValueError):
    """A capture nobody confirmed cannot become canonical market data.

    The first of two guards, and the one that produces a sentence a trader
    can act on. The second is structural: :class:`CanonicalVolSurface`
    requires a confirmer and a confirmation time, so an unconfirmed capture
    has nothing to build one from even if this check were bypassed.
    """


def canonical_surface_from_confirmed_capture(
    capture: VCUBATMCapture, *, capture_id: str
) -> CanonicalVolSurface:
    """Build the canonical surface one confirmed capture asserts.

    Reads :attr:`VCUBATMCapture.accepted_grid`, never ``grid``: a pending or
    rejected capture exposes no accepted grid at all, so its numbers cannot
    reach the canonical model even by mistake.

    Every intersection of the reviewed grid becomes a point, including the
    ones the parser left unresolved -- those carry ``volatility=None``.
    Dropping them would lose the coordinate the trader reviewed and make an
    unreadable cell indistinguishable from a cell outside the surface.
    """

    if not isinstance(capture, VCUBATMCapture):
        raise TypeError("capture must be a VCUBATMCapture")
    if capture.review_status is not VCUBCaptureStatus.CONFIRMED:
        raise UnconfirmedCaptureError(
            "only a CONFIRMED capture can be written to the canonical vol-surface store; "
            f"this one is {capture.review_status.value}"
        )
    grid = capture.accepted_grid
    if grid is None:  # pragma: no cover - unreachable while CONFIRMED implies a grid
        raise UnconfirmedCaptureError(
            "this capture exposes no accepted grid, so there is nothing to store"
        )
    metadata = capture.metadata
    if metadata.tab != ATM_SWAPTIONS_TAB:
        # A confirmed capture always resolved the tab -- an unresolved one is
        # a blocking error upstream -- so this only fires if a future screen
        # reaches this adapter. It must not be filed as an ATM swaption
        # surface on the strength of having a grid.
        raise UnconfirmedCaptureError(
            f"this adapter only files the {ATM_SWAPTIONS_TAB!r} screen as "
            f"{VolSurfaceType.ATM_SWAPTION.value}; this capture's tab is {metadata.tab!r}"
        )

    identity = VolSurfaceIdentity(
        surface_type=VolSurfaceType.ATM_SWAPTION,
        **{
            identity_field: getattr(metadata, metadata_field)
            for metadata_field, identity_field in _METADATA_TO_IDENTITY.items()
        },
        unresolved_fields=tuple(
            identity_field
            for metadata_field, identity_field in _METADATA_TO_IDENTITY.items()
            if metadata_field in metadata.unresolved_fields
        ),
    )
    provenance = VolSurfaceProvenance(
        capture_id=capture_id,
        source_reference=capture.provenance.source_reference,
        source_image_sha256=capture.provenance.source_image_sha256,
        source_image_bytes=capture.provenance.source_image_bytes,
        captured_at=capture.provenance.captured_at,
        parser_name=capture.provenance.parser_name,
        parser_version=capture.provenance.parser_version,
        confirmed_by=capture.reviewed_by,
        confirmed_at=capture.reviewed_at,
    )
    points = tuple(
        VolSurfacePoint(
            expiry=expiry,
            underlying_tenor=tenor,
            volatility=grid.values[row_index][column_index],
            strike_dimension=StrikeDimension.ATM,
        )
        # Row-major, in the screen's own axis order, so a stored surface
        # reads back in the order the trader reviewed it.
        for row_index, expiry in enumerate(grid.expiry_labels)
        for column_index, tenor in enumerate(grid.tenor_labels)
    )
    return CanonicalVolSurface(
        identity=identity,
        provenance=provenance,
        points=points,
        # The VCUB ATM screen states a vol *type* but no unit, and inferring
        # one from the magnitude of the numbers would be exactly the silent
        # unit coercion the capture slice refuses. It stays unresolved until
        # something states it.
        volatility_unit=None,
    )
