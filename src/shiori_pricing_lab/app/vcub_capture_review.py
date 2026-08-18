"""Server-side review state for VCUB ATM visual captures (Issue #181).

The workbench keeps parsed captures **here**, not in the browser, and the
confirm/reject routes name a capture by id rather than posting one back. That
is deliberate: a capture the page could round-trip is a capture whose
``blocking_errors`` a page could drop on the way, and "confirmation is
impossible while anything blocks" has to be a fact about the server, not a
promise about the client.

Nothing in this module imports :mod:`shiori_pricing_lab.pricing` or the
workbench's pricing entry point. Parsing an image and confirming a capture
do not price anything, do not touch the trader's ticket, and do not feed any
market-data input -- Issue #181 ends at a reconstructed table plus a trader's
decision about it.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import UTC, datetime

from shiori_pricing_lab.data.bloomberg_vcub_atm_template import parse_vcub_atm_tokens
from shiori_pricing_lab.data.bloomberg_vcub_capture import VCUBATMCapture
from shiori_pricing_lab.data.bloomberg_vcub_ocr import (
    build_capture_provenance,
    read_tokens_from_image_bytes,
)

#: How many captures one workbench session keeps reviewable at once. A
#: trader compares one screenshot at a time; the rest is just enough history
#: to go back to the previous one.
DEFAULT_CAPACITY = 8


def utc_now_iso() -> str:
    """The current instant as a second-precision UTC ISO-8601 string."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def capture_id_for(source_image_sha256: str, captured_at: str) -> str:
    """A stable id for one image read at one instant.

    Derived from the image hash and the capture time rather than a random
    token, so re-parsing the same file in the same second reuses the same
    review slot instead of leaving an orphan behind.
    """

    digest = hashlib.sha256(f"{source_image_sha256}|{captured_at}".encode())
    return digest.hexdigest()[:32]


class VCUBCaptureReviewStore:
    """The captures this workbench process currently has under review."""

    def __init__(self, *, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be at least 1, got {capacity!r}")
        self._capacity = capacity
        self._captures: OrderedDict[str, VCUBATMCapture] = OrderedDict()

    def __len__(self) -> int:
        return len(self._captures)

    def parse_image(
        self,
        *,
        source_reference: str,
        raw_image: bytes,
        captured_at: str | None = None,
        engine=None,
    ) -> tuple[str, VCUBATMCapture, tuple[str, ...]]:
        """Read one operator-supplied image and file the result for review.

        Returns ``(capture_id, capture, reader_notes)``. The image bytes are
        hashed for provenance and then dropped -- they are never stored here
        and never written to disk by this module.
        """

        captured_at = utc_now_iso() if captured_at is None else captured_at
        provenance = build_capture_provenance(
            source_reference=source_reference, raw_image=raw_image, captured_at=captured_at
        )
        tokens, reader_notes = read_tokens_from_image_bytes(raw_image, engine=engine)
        capture = parse_vcub_atm_tokens(tokens, provenance=provenance)
        identifier = capture_id_for(provenance.source_image_sha256, captured_at)
        self._store(identifier, capture)
        return identifier, capture, reader_notes

    def get(self, capture_id: str) -> VCUBATMCapture:
        try:
            return self._captures[capture_id]
        except KeyError as exc:
            raise KeyError(f"no capture is under review with id {capture_id!r}") from exc

    def confirm(
        self, capture_id: str, *, reviewed_by: str, reviewed_at: str | None = None
    ) -> VCUBATMCapture:
        """Accept a capture on a named trader's behalf, or raise if it is blocked."""

        confirmed = self.get(capture_id).confirm(
            reviewed_by=reviewed_by,
            reviewed_at=utc_now_iso() if reviewed_at is None else reviewed_at,
        )
        self._store(capture_id, confirmed)
        return confirmed

    def reject(
        self, capture_id: str, *, reviewed_by: str, reviewed_at: str | None = None
    ) -> VCUBATMCapture:
        """Refuse a capture on a named trader's behalf, leaving its values unaccepted."""

        rejected = self.get(capture_id).reject(
            reviewed_by=reviewed_by,
            reviewed_at=utc_now_iso() if reviewed_at is None else reviewed_at,
        )
        self._store(capture_id, rejected)
        return rejected

    def _store(self, capture_id: str, capture: VCUBATMCapture) -> None:
        self._captures[capture_id] = capture
        self._captures.move_to_end(capture_id)
        while len(self._captures) > self._capacity:
            self._captures.popitem(last=False)
