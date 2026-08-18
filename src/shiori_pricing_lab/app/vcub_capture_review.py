"""Server-side review state for VCUB ATM visual captures (Issue #181).

The workbench keeps parsed captures **here**, not in the browser, and the
confirm/reject routes name a capture by id rather than posting one back. That
is deliberate: a capture the page could round-trip is a capture whose
``blocking_errors`` a page could drop on the way, and "confirmation is
impossible while anything blocks" has to be a fact about the server, not a
promise about the client.

**One decision per capture, under a threading server.** ``create_server``
is a :class:`ThreadingHTTPServer`, so a confirm and a reject for the same
capture can arrive on two threads at once. Every read-modify-write below is
therefore serialised by :attr:`VCUBCaptureReviewStore._lock`; without it
both callers observed the same ``PENDING_REVIEW`` object, both returned
200, and whichever wrote last silently decided the record -- so the trader
who rejected a capture could be shown a rejection while the stored record
said confirmed (Codex review round 3, PR #182). The OCR read deliberately
stays *outside* the lock: it is the slow part and touches no shared state.

Nothing in this module imports :mod:`shiori_pricing_lab.pricing` or the
workbench's pricing entry point. Parsing an image and confirming a capture
do not price anything, do not touch the trader's ticket, and do not feed any
market-data input -- Issue #181 ends at a reconstructed table plus a trader's
decision about it.
"""

from __future__ import annotations

import hashlib
import threading
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
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
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
        # Outside the lock on purpose: reading the image is the slow step and
        # touches nothing shared.
        tokens, reader_notes = read_tokens_from_image_bytes(raw_image, engine=engine)
        capture = parse_vcub_atm_tokens(tokens, provenance=provenance)
        with self._lock:
            identifier = self._free_identifier(
                provenance.source_image_sha256, captured_at, capture
            )
            self._store_locked(identifier, capture)
        return identifier, capture, reader_notes

    def _free_identifier(
        self, source_image_sha256: str, captured_at: str, capture: VCUBATMCapture
    ) -> str:
        """An id for this read that cannot displace a capture someone else holds.

        Re-reading the same file in the same second reuses its review slot,
        which keeps a re-parse from leaving an orphan behind. That is only
        safe when the slot holds *exactly* this capture:

        * a decided capture must keep its slot, or a re-parse would silently
          reset a terminal decision and let the same capture be reviewed
          twice (round 3);
        * a *pending* capture must too, unless the new read is identical to
          it. Two clients parsing the same image in the same second otherwise
          share one id, and the later store replaces the capture the first
          client is still looking at -- so that client's Confirm would apply
          to OCR output and provenance it never saw (round 4). The reads run
          outside the lock and even ``source_reference`` may differ, so this
          is a real divergence, not a formality.

        Captures are frozen dataclasses of immutable fields, so ``==`` is an
        exact structural comparison: same provenance, same grid, same issues.
        """

        identifier = capture_id_for(source_image_sha256, captured_at)
        attempt = 0
        while True:
            existing = self._captures.get(identifier)
            if existing is None or existing == capture:
                return identifier
            attempt += 1
            identifier = capture_id_for(source_image_sha256, f"{captured_at}#{attempt}")

    def get(self, capture_id: str) -> VCUBATMCapture:
        with self._lock:
            return self._get_locked(capture_id)

    def _get_locked(self, capture_id: str) -> VCUBATMCapture:
        try:
            return self._captures[capture_id]
        except KeyError as exc:
            raise KeyError(f"no capture is under review with id {capture_id!r}") from exc

    def confirm(
        self, capture_id: str, *, reviewed_by: str, reviewed_at: str | None = None
    ) -> VCUBATMCapture:
        """Accept a capture on a named trader's behalf, or raise if it is blocked."""

        with self._lock:
            confirmed = self._get_locked(capture_id).confirm(
                reviewed_by=reviewed_by,
                reviewed_at=utc_now_iso() if reviewed_at is None else reviewed_at,
            )
            self._store_locked(capture_id, confirmed)
        return confirmed

    def reject(
        self, capture_id: str, *, reviewed_by: str, reviewed_at: str | None = None
    ) -> VCUBATMCapture:
        """Refuse a capture on a named trader's behalf, leaving its values unaccepted."""

        with self._lock:
            rejected = self._get_locked(capture_id).reject(
                reviewed_by=reviewed_by,
                reviewed_at=utc_now_iso() if reviewed_at is None else reviewed_at,
            )
            self._store_locked(capture_id, rejected)
        return rejected

    def _store_locked(self, capture_id: str, capture: VCUBATMCapture) -> None:
        """Caller must hold ``self._lock``."""

        self._captures[capture_id] = capture
        self._captures.move_to_end(capture_id)
        while len(self._captures) > self._capacity:
            self._captures.popitem(last=False)
