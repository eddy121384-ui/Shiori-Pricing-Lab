"""Turn one **confirmed** VCUB capture into a canonical vol surface
(Issues #183 and #185).

The one-way bridge between the transcription shapes of Issue #181 (ATM
Swaptions) and Issue #185 (OTM Swaptions / SABR) and Issue #183's canonical
model. It is a separate module on purpose: the canonical model must not know
what a screenshot is, and the SQLite store must not know what Bloomberg is,
so the vendor-specific mapping lives here and nowhere else. The two screens
share this module, the same canonical model, and the same store -- there is
no second OTM-only persistence path.

**It transcribes the transcription.** Nothing is renamed, re-based,
converted, interpolated, or filled in. The screen's own spellings survive:
``"18Mo"`` stays ``"18Mo"``, ``"Normal Vol (OIS)"`` stays that string, and
the quote date stays the text the screen drew (``"08/18/26"``) rather than
becoming a parsed calendar date. An unresolved metadata field arrives here
as ``None`` and leaves as ``None``, named in the identity's own unresolved
list.

**Every capture is its own surface identity.** ``capture_id`` -- the review
store's id for this exact image read, not a Bloomberg quote timestamp --
becomes :attr:`~shiori_pricing_lab.data.vol_surface.VolSurfaceIdentity.capture_id`
(Eddy's PR #184 decision #1), so a second screenshot of the same screen
later the same day files as a new surface rather than colliding with the
first one.
"""

from __future__ import annotations

from shiori_pricing_lab.data.bloomberg_vcub_atm_template import ATM_SWAPTIONS_TAB
from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    VCUBATMCapture,
    VCUBCaptureStatus,
)
from shiori_pricing_lab.data.bloomberg_vcub_otm_capture import (
    NORMAL_VOL_SKEW_TYPE,
    OTM_SWAPTIONS_SABR_TAB,
    SPREAD_DISPLAY_MODE,
    VCUBOTMCapture,
)
from shiori_pricing_lab.data.bloomberg_vcub_screen_reader import normalise_text
from shiori_pricing_lab.data.vol_surface import (
    CanonicalVolSurface,
    StrikeDimension,
    VolSurfaceIdentity,
    VolSurfacePoint,
    VolSurfaceProvenance,
    VolSurfaceSourceImage,
    VolSurfaceType,
    VolValueKind,
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
        capture_id=capture_id,
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


def canonical_surface_from_confirmed_otm_capture(
    capture: VCUBOTMCapture, *, capture_id: str
) -> CanonicalVolSurface:
    """Build the canonical surface one confirmed OTM/SABR capture asserts.

    Reads :attr:`VCUBOTMCapture.accepted_table`, never ``table``: a pending
    or rejected capture exposes no accepted table at all, so its numbers
    cannot reach the canonical model even by mistake.

    Every intersection of the reviewed table becomes a point, including the
    ones the parser left unresolved -- those carry ``volatility=None``, for
    the same reason as on the ATM path.

    **The screen's own semantics decide each point's kind.** The column
    headed ``ATM`` files as a :attr:`StrikeDimension.ATM` point carrying an
    absolute vol; every other column files as
    :attr:`StrikeDimension.YIELD_OFFSET_BP` at the basis-point offset its
    header states, carrying a :attr:`VolValueKind.SPREAD_TO_ATM`. Nothing
    here adds the two together, and nothing here converts either.

    **Every screenshot of the session is kept.** The capture's images become
    the provenance's :attr:`~VolSurfaceProvenance.source_images`, in the
    order they were supplied, so a stored multi-image surface can name every
    file that produced it rather than one arbitrary hash.
    """

    if not isinstance(capture, VCUBOTMCapture):
        raise TypeError("capture must be a VCUBOTMCapture")
    if capture.review_status is not VCUBCaptureStatus.CONFIRMED:
        raise UnconfirmedCaptureError(
            "only a CONFIRMED capture can be written to the canonical vol-surface store; "
            f"this one is {capture.review_status.value}"
        )
    table = capture.accepted_table
    if table is None:  # pragma: no cover - unreachable while CONFIRMED implies a table
        raise UnconfirmedCaptureError(
            "this capture exposes no accepted table, so there is nothing to store"
        )
    if not table.is_complete:
        # Unreachable through the review flow -- an incomplete surface cannot
        # reach CONFIRMED at all -- and re-checked here for the same reason
        # the screen contract below is: this function is the last gate before
        # the canonical store, and what it files must be the whole screen
        # (Eddy's decision on PR #186).
        raise UnconfirmedCaptureError(
            "this capture does not hold the complete expected surface: "
            f"{len(table.missing_expected_rows())} expected rows are missing and "
            f"{len(table.unexpected_rows())} are outside the template"
        )
    metadata = capture.metadata
    # All three are blocking errors upstream, so a CONFIRMED capture has
    # them. They are re-checked because they are what makes the numbers
    # below mean anything: the tab says which screen this is, and Type plus
    # display mode say that its non-ATM columns are spreads rather than vols.
    if metadata.tab != OTM_SWAPTIONS_SABR_TAB:
        raise UnconfirmedCaptureError(
            f"this adapter only files the {OTM_SWAPTIONS_SABR_TAB!r} screen as "
            f"{VolSurfaceType.OTM_SWAPTION_SABR.value}; this capture's tab is "
            f"{metadata.tab!r}"
        )
    vol_type_matches = (
        metadata.vol_type is not None
        and normalise_text(metadata.vol_type).casefold() == NORMAL_VOL_SKEW_TYPE.casefold()
    )
    if not vol_type_matches or metadata.display_mode != SPREAD_DISPLAY_MODE:
        raise UnconfirmedCaptureError(
            f"this adapter only files a {NORMAL_VOL_SKEW_TYPE!r} screen displayed as "
            f"{SPREAD_DISPLAY_MODE!r}, whose ATM column is an absolute vol and whose other "
            f"columns are spreads to it; this capture reads {metadata.vol_type!r} / "
            f"{metadata.display_mode!r}"
        )

    identity = VolSurfaceIdentity(
        surface_type=VolSurfaceType.OTM_SWAPTION_SABR,
        capture_id=capture_id,
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
    first = capture.sources[0]
    provenance = VolSurfaceProvenance(
        capture_id=capture_id,
        source_reference=first.source_reference,
        source_image_sha256=first.source_image_sha256,
        source_image_bytes=first.source_image_bytes,
        captured_at=first.captured_at,
        parser_name=first.parser_name,
        parser_version=first.parser_version,
        confirmed_by=capture.reviewed_by,
        confirmed_at=capture.reviewed_at,
        source_images=tuple(
            VolSurfaceSourceImage(
                source_reference=source.source_reference,
                sha256=source.source_image_sha256,
                size_bytes=source.source_image_bytes,
            )
            for source in capture.sources
        ),
    )
    points = tuple(
        VolSurfacePoint(
            expiry=row.term,
            underlying_tenor=row.tenor,
            volatility=row.values[column_index],
            strike_dimension=(
                StrikeDimension.ATM if strike.is_atm else StrikeDimension.YIELD_OFFSET_BP
            ),
            strike_offset=strike.offset_bp,
            value_kind=(
                VolValueKind.ABSOLUTE_VOL if strike.is_atm else VolValueKind.SPREAD_TO_ATM
            ),
        )
        # Row-major, in the screen's own axis order, so a stored surface
        # reads back in the order the trader reviewed it.
        for row in table.rows
        for column_index, strike in enumerate(table.strikes)
    )
    return CanonicalVolSurface(
        identity=identity,
        provenance=provenance,
        points=points,
        # The screen states a vol *type* and a display mode but no unit for
        # the numbers themselves, and inferring one from their magnitude
        # would be exactly the silent unit coercion the capture slice
        # refuses. It stays unresolved until something states it.
        volatility_unit=None,
    )
