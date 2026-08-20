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

**Retention policy (Eddy/Sophira RED decision on Issue #181).** A capture a
trader has decided on is a record of that decision, so the store must never
lose one quietly:

* ``CONFIRMED`` and ``REJECTED`` captures are immutable and non-evictable
  for the lifetime of this server process;
* at capacity the store evicts the oldest ``PENDING_REVIEW`` capture;
* an issued capture id is never reused, so an id that *has* been evicted
  fails loudly on lookup rather than silently addressing a later capture;
* if the store is full and nothing pending can be evicted, the new parse is
  refused with :class:`VCUBCaptureStoreFullError` -- a decided record is
  never sacrificed to make room.

**Prototype limitation, stated explicitly.** This retention is in-memory and
process-scoped by design. Restarting the workbench loses the review history,
including confirmed and rejected captures, and this module deliberately adds
no database, no file persistence, no audit store, and no retention
subsystem. Durable retention and audit persistence belong to a later
production market-data ingestion issue, not to this prototype. A decision
that must outlive the process must be recorded outside it.

Nothing in this module imports :mod:`shiori_pricing_lab.pricing` or the
workbench's pricing entry point. Parsing an image and confirming a capture
do not price anything, do not touch the trader's ticket, and do not feed any
market-data input -- Issue #181 ends at a reconstructed table plus a trader's
decision about it.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from collections import OrderedDict
from datetime import UTC, datetime

from shiori_pricing_lab.data.bloomberg_vcub_atm_template import parse_vcub_atm_tokens
from shiori_pricing_lab.data.bloomberg_vcub_capture import (
    VCUBATMCapture,
    VCUBCaptureStatus,
)
from shiori_pricing_lab.data.bloomberg_vcub_ocr import (
    build_capture_provenance,
    read_tokens_from_image_bytes,
)

#: How many captures one workbench session holds at once. Under the
#: retention policy above a decided capture is never evicted, so this is
#: really "how many decisions one session can record" rather than "how much
#: scrollback" -- 8 would have refused the ninth confirm of a sitting. This
#: is an ordinary implementation detail chosen to fit a review session, not a
#: change to the policy: the bound itself, and refusing rather than evicting
#: at it, are exactly as decided.
DEFAULT_CAPACITY = 64


class VCUBCaptureStoreFullError(RuntimeError):
    """The review store is full of decided captures and cannot take another.

    Raised rather than evicting a confirmed or rejected record. Recovering
    means restarting the workbench, which is the documented prototype
    limitation -- there is no durable store to fall back on.
    """


def utc_now_iso() -> str:
    """The current instant as a second-precision UTC ISO-8601 string."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def response_fingerprint(capture: VCUBATMCapture) -> str:
    """Exactly what a client would receive for ``capture``, as one string.

    Slot reuse turns on "is this read the same as the one a client is already
    looking at", and the honest test of that is the response itself, not
    Python equality over the dataclass. The two diverge: a cell read as
    ``-0.00`` and one read as ``0.00`` produce ``-0.0`` and ``0.0``, which
    compare equal in Python but serialise -- and render -- differently, so
    reusing the slot let a client confirm a capture whose displayed value it
    never saw (Codex review on `49bf0cd`, PR #182).
    """

    return json.dumps(capture.to_dict(), sort_keys=True)


#: Mixed into every id this process mints, generated once at import time.
#: Issue #183's canonical store hashes ``capture_id`` into a surface's
#: identity (Eddy's PR #184 decision #1), and the store's own SQLite file is
#: an ordinary local file two independently launched workbench processes can
#: point at. Without a process salt, two such processes reading
#: byte-identical image bytes within the same wall-clock second would derive
#: the *same* deterministic id from content alone -- with no shared state to
#: notice they are different processes -- and one process's confirmed
#: surface could silently collide with, or be refused as a conflict of,
#: the other's (Codex review round 5, PR #184). The salt is process-lifetime
#: stable, so every intra-process guarantee ``capture_id_for`` and
#: :meth:`VCUBCaptureReviewStore._find_identifier` provide -- same inputs
#: reuse the same slot, the purity :func:`capture_id_for` itself is tested
#: against -- is unaffected; only cross-process collision closes.
#:
#: Not sufficient alone against ``os.fork()``: a POSIX fork duplicates the
#: parent's memory, so every forked child inherits this exact value, and two
#: children parsing identical bytes at the identical second would still
#: collide (Codex review round 6, PR #184). Nothing in this codebase forks
#: today -- the workbench is one ``ThreadingHTTPServer`` process launched
#: fresh by ``start_shiori.bat`` -- but the id is cheap to make correct
#: regardless, so :func:`capture_id_for` also mixes in ``os.getpid()`` at
#: call time: a fork always gives the child a new pid even though its copy
#: of this module-level value is bit-identical to the parent's.
_PROCESS_SALT = secrets.token_hex(16)


def capture_id_for(source_image_sha256: str, captured_at: str) -> str:
    """A stable id for one image read at one instant, unique to this process.

    Derived from the image hash and the capture time rather than a purely
    random token, so re-parsing the same file in the same second within
    *this* process reuses the same review slot instead of leaving an orphan
    behind. The per-process salt (:data:`_PROCESS_SALT`) plus the calling
    process's own pid make it globally unique too: two workbench processes
    -- including two that share inherited memory via ``os.fork()`` -- reading
    identical bytes at the identical second mint different ids, which is
    what keeps two operators' confirmed surfaces from colliding through a
    shared database file. ``os.getpid()`` is read fresh on every call rather
    than cached alongside the salt, since a fork changes it after the salt
    has already been computed and inherited.
    """

    digest = hashlib.sha256(
        f"{_PROCESS_SALT}|{os.getpid()}|{source_image_sha256}|{captured_at}".encode()
    )
    return digest.hexdigest()[:32]


class VCUBCaptureReviewStore:
    """The captures this workbench process currently has under review."""

    def __init__(self, *, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be at least 1, got {capacity!r}")
        self._capacity = capacity
        self._captures: OrderedDict[str, VCUBATMCapture] = OrderedDict()
        # Every id this store has ever handed out, including ones whose
        # capture has since been evicted. An id is never recycled: see
        # :meth:`_find_identifier`. One 32-character string per parse that
        # actually claimed a slot -- a refused parse reserves nothing.
        self._issued_identifiers: set[str] = set()
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
            identifier = self._find_identifier(
                provenance.source_image_sha256, captured_at, capture
            )
            if identifier not in self._captures:
                # Only a genuinely new slot grows the store; an identical
                # re-read reusing its own slot does not, and must not be able
                # to trip the capacity refusal.
                self._make_room_locked()
            # Reserved only now that the slot is certain. Committing before
            # ``_make_room_locked`` could raise meant every refused parse burned
            # an id permanently -- 200 refusals of one image burned 200 ids --
            # growing this set without bound and lengthening the suffix walk
            # each time (Codex review round 6, PR #182).
            self._issued_identifiers.add(identifier)
            self._store_locked(identifier, capture)
        return identifier, capture, reader_notes

    def _make_room_locked(self) -> None:
        """Free one slot by evicting the oldest pending capture, or refuse.

        Caller must hold ``self._lock``. A decided capture is never a
        candidate: when nothing pending is left to give up, the parse is
        refused instead (Eddy/Sophira RED decision on Issue #181).
        """

        while len(self._captures) >= self._capacity:
            oldest_pending = next(
                (
                    identifier
                    for identifier, held in self._captures.items()
                    if held.review_status is VCUBCaptureStatus.PENDING_REVIEW
                ),
                None,
            )
            if oldest_pending is None:
                decided = len(self._captures)
                raise VCUBCaptureStoreFullError(
                    f"the review store is full: all {decided} captures it holds have already "
                    "been confirmed or rejected, and a decided capture is never discarded to "
                    "make room. Restart the workbench to begin a new review session -- this "
                    "prototype keeps its review history in memory only."
                )
            del self._captures[oldest_pending]

    def _find_identifier(
        self, source_image_sha256: str, captured_at: str, capture: VCUBATMCapture
    ) -> str:
        """An id for this read that cannot displace a capture someone else holds.

        Pure: it reserves nothing. The caller commits the returned id to
        :attr:`_issued_identifiers` only once the slot is certain.

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
        * an id whose capture was *evicted* must not be handed out again
          either. The store is capacity-bounded, so an id can fall out of
          it; recycling that id for a different capture reintroduces the
          same failure through the back door, and turns eviction's one safe
          outcome -- ``get`` raising, which the route answers as 404 -- into
          a silent retarget onto a capture nobody reviewed. Eviction may
          lose a capture; it must never redirect one.

        Sameness is decided by :func:`response_fingerprint` -- the serialised
        response a client would receive -- rather than by dataclass equality,
        which treats ``-0.0`` and ``0.0`` as the same value while the client
        is shown two different numbers.
        """

        identifier = capture_id_for(source_image_sha256, captured_at)
        attempt = 0
        while True:
            existing = self._captures.get(identifier)
            reusable = existing is not None and response_fingerprint(
                existing
            ) == response_fingerprint(capture)
            if reusable or identifier not in self._issued_identifiers:
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

    def confirm_idempotent(
        self, capture_id: str, *, reviewed_by: str, reviewed_at: str | None = None
    ) -> VCUBATMCapture:
        """Accept a capture, or hand back the confirmation this trader already made.

        The same decision as :meth:`confirm`, made once, but safe to repeat.
        Issue #183 gave a confirmation a second job -- writing the surface to
        the canonical store -- and that write can fail, so the trader needs
        to be able to press Confirm again to retry it. :meth:`confirm` alone
        cannot serve that: the state machine is terminal, so the second press
        raises and the trader is stranded with a capture that is confirmed
        and not durable (Codex review, PR #184).

        The check and the transition happen **together under the lock**, not
        as a read followed by a write. Two same-trader requests overlapping on
        a pending capture -- a double click, a browser retrying a timed-out
        POST -- otherwise both saw ``PENDING_REVIEW``, and the loser then hit
        the terminal state and was refused, for a confirmation that had in
        fact been accepted (Codex review round 2, PR #184). That is the same
        read-modify-write hazard the lock was introduced for in round 3 of
        PR #182.

        Only the capture's *own* confirmer gets the repeat. Anyone else falls
        through to the ordinary transition and is refused there, so a second
        person can never be told their confirmation was accepted -- one
        decision, by one named trader, exactly as Issue #181 decided.
        """

        with self._lock:
            held = self._get_locked(capture_id)
            if (
                held.review_status is VCUBCaptureStatus.CONFIRMED
                and held.reviewed_by == reviewed_by
            ):
                return held
            confirmed = held.confirm(
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
        """Caller must hold ``self._lock``.

        Never evicts. Making room is :meth:`_make_room_locked`'s job and
        happens only when a *new* slot is being claimed, so recording a
        decision -- which replaces an entry rather than adding one -- can
        never push another capture out.

        Assignment deliberately does *not* refresh an existing key's position:
        ``OrderedDict`` keeps it where it was, and a new key lands at the end
        on its own. Refreshing it meant an identical re-read of the oldest
        pending capture moved it to the back, so the next parse evicted a
        *younger* pending capture instead -- breaking the oldest-pending rule
        the retention policy is built on (Codex review round 6, PR #182).
        """

        self._captures[capture_id] = capture
