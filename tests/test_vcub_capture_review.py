"""The workbench-side review store for VCUB ATM captures (Issue #181).

The store is what makes "confirmation is impossible while anything blocks" a
server-side fact: the page names a capture by id and never posts one back,
so it cannot drop a blocking error on the round trip.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from test_bloomberg_vcub_atm_template import canonical_tokens

import shiori_pricing_lab.app.vcub_capture_review as review_module
from shiori_pricing_lab.app.vcub_capture_review import (
    VCUBCaptureReviewStore,
    capture_id_for,
    utc_now_iso,
)
from shiori_pricing_lab.data.bloomberg_vcub_capture import VCUBCaptureStatus

_IMAGE = b"\x89PNG\r\n\x1a\n-synthetic-not-a-screenshot"
_CAPTURED_AT = "2026-08-18T09:30:00Z"
_REVIEWED_AT = "2026-08-18T09:41:00Z"


@pytest.fixture()
def stub_reader(monkeypatch):
    """Replace the OCR seam so these tests stay offline and deterministic."""

    calls: list[bytes] = []

    def _read(raw_image, *, engine=None, **kwargs):
        calls.append(bytes(raw_image))
        return tuple(canonical_tokens()), ("'85' was read with confidence 0.20 and was dropped",)

    monkeypatch.setattr(review_module, "read_tokens_from_image_bytes", _read)
    return calls


def _parse(store: VCUBCaptureReviewStore, *, raw_image: bytes = _IMAGE):
    return store.parse_image(
        source_reference="vcub_atm_usd.png", raw_image=raw_image, captured_at=_CAPTURED_AT
    )


def test_a_parsed_capture_is_filed_under_review_and_can_be_fetched_back(stub_reader) -> None:
    store = VCUBCaptureReviewStore()

    capture_id, capture, notes = _parse(store)

    assert store.get(capture_id) is capture
    assert capture.review_status is VCUBCaptureStatus.PENDING_REVIEW
    assert capture.grid.value_at("3Mo", "4Yr") is not None
    assert notes and "confidence" in notes[0]


def test_the_store_reads_the_operator_bytes_and_keeps_only_their_hash(stub_reader) -> None:
    store = VCUBCaptureReviewStore()

    _capture_id, capture, _notes = _parse(store)

    assert stub_reader == [_IMAGE]
    assert capture.provenance.source_image_bytes == len(_IMAGE)
    assert capture.provenance.source_reference == "vcub_atm_usd.png"
    assert _IMAGE not in repr(capture).encode("latin-1", "ignore")


def test_the_same_image_read_at_the_same_instant_reuses_one_review_slot(stub_reader) -> None:
    store = VCUBCaptureReviewStore()

    first_id, _first, _ = _parse(store)
    second_id, _second, _ = _parse(store)

    assert first_id == second_id
    assert len(store) == 1


def test_a_different_image_gets_its_own_review_slot(stub_reader) -> None:
    store = VCUBCaptureReviewStore()

    first_id, _first, _ = _parse(store)
    second_id, _second, _ = _parse(store, raw_image=_IMAGE + b"!")

    assert first_id != second_id
    assert len(store) == 2


def test_confirming_records_the_named_trader_and_the_moment(stub_reader) -> None:
    store = VCUBCaptureReviewStore()
    capture_id, _capture, _ = _parse(store)

    confirmed = store.confirm(capture_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    assert confirmed.review_status is VCUBCaptureStatus.CONFIRMED
    assert (confirmed.reviewed_by, confirmed.reviewed_at) == ("Eddy", _REVIEWED_AT)
    assert store.get(capture_id) is confirmed


def test_rejecting_leaves_the_captured_values_unaccepted(stub_reader) -> None:
    store = VCUBCaptureReviewStore()
    capture_id, _capture, _ = _parse(store)

    rejected = store.reject(capture_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    assert rejected.review_status is VCUBCaptureStatus.REJECTED
    assert rejected.accepted_grid is None
    assert store.get(capture_id).accepted_grid is None


def test_a_blocked_capture_cannot_be_confirmed_through_the_store(monkeypatch) -> None:
    tokens = [token for token in canonical_tokens() if token.text != "Expiry"]
    monkeypatch.setattr(
        review_module,
        "read_tokens_from_image_bytes",
        lambda raw_image, *, engine=None, **kwargs: (tuple(tokens), ()),
    )
    store = VCUBCaptureReviewStore()
    capture_id, capture, _ = _parse(store)

    assert not capture.can_confirm
    with pytest.raises(ValueError, match="EXPIRY_ANCHOR_UNRESOLVED"):
        store.confirm(capture_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)
    assert store.get(capture_id).review_status is VCUBCaptureStatus.PENDING_REVIEW


def test_a_decided_capture_is_never_reset_by_re_reading_the_same_image(stub_reader) -> None:
    """Codex review round 3, PR #182.

    Reusing the review slot keeps a re-parse from leaving an orphan behind,
    but only while the slot is still pending. Overwriting a decided capture
    let a terminal decision be silently reset and the same capture reviewed
    twice.
    """

    store = VCUBCaptureReviewStore()
    first_id, _capture, _ = _parse(store)
    store.confirm(first_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    second_id, second, _ = _parse(store)

    assert store.get(first_id).review_status is VCUBCaptureStatus.CONFIRMED
    assert store.get(first_id).reviewed_by == "Eddy"
    assert second_id != first_id
    assert second.review_status is VCUBCaptureStatus.PENDING_REVIEW


def test_a_pending_capture_slot_is_still_reused_by_re_reading_the_same_image(
    stub_reader,
) -> None:
    """The decided-slot rule must not cost the ordinary re-parse its slot."""

    store = VCUBCaptureReviewStore()

    first_id, _first, _ = _parse(store)
    second_id, _second, _ = _parse(store)

    assert first_id == second_id
    assert len(store) == 1


def test_a_concurrent_confirm_and_reject_cannot_both_be_accepted(stub_reader) -> None:
    """Codex review round 3, PR #182.

    ``create_server`` is a ThreadingHTTPServer, so both can arrive at once.
    Un-serialised, both callers saw the same pending object, both returned a
    decision, and whichever wrote last silently determined the record -- so
    a trader could be shown a rejection while the stored record said
    confirmed. Which caller wins is a race; that exactly one does is not.
    """

    store = VCUBCaptureReviewStore()
    capture_id, _capture, _ = _parse(store)

    # Widen the window between the read and the write. This does not create
    # the race, it only makes an un-serialised sequence observable.
    unlocked_get = store._get_locked

    def slow_get(identifier: str):
        found = unlocked_get(identifier)
        time.sleep(0.05)
        return found

    store._get_locked = slow_get

    outcomes: list[tuple[str, str]] = []
    start = threading.Barrier(2)

    def review(action, name: str) -> None:
        start.wait()
        try:
            outcomes.append((name, action(capture_id, reviewed_by=name, reviewed_at=_REVIEWED_AT)
                             .review_status.value))
        except ValueError:
            outcomes.append((name, "refused"))

    threads = [
        threading.Thread(target=review, args=(store.confirm, "confirm")),
        threading.Thread(target=review, args=(store.reject, "reject")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    accepted = [name for name, result in outcomes if result != "refused"]
    assert len(accepted) == 1, f"both callers were accepted: {outcomes}"
    retained = unlocked_get(capture_id)
    assert retained.review_status is not VCUBCaptureStatus.PENDING_REVIEW
    assert retained.reviewed_by == accepted[0]


def test_the_store_stays_consistent_under_concurrent_parse_and_review(stub_reader) -> None:
    """Deadlock-freedom and eviction, exercised together.

    The single-transition test above pins one race in isolation. This one
    runs parse, confirm, reject, and capacity eviction against each other on
    several threads: the lock is non-reentrant, so any locked method that
    called a public locking one would hang here rather than fail quietly.
    ``ValueError`` (already decided) and ``KeyError`` (evicted before the
    review landed) are both legitimate outcomes; anything else is not.
    """

    store = VCUBCaptureReviewStore(capacity=3)
    unexpected: list[str] = []

    def churn(worker: int) -> None:
        try:
            for index in range(15):
                capture_id, _capture, _notes = store.parse_image(
                    source_reference="vcub.png",
                    raw_image=bytes([index % 4]) * 8,
                    captured_at=_CAPTURED_AT,
                )
                try:
                    action = store.confirm if index % 2 else store.reject
                    action(capture_id, reviewed_by=f"t{worker}", reviewed_at=_REVIEWED_AT)
                except (ValueError, KeyError):
                    pass
                len(store)
        except Exception as exc:  # noqa: BLE001
            unexpected.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=churn, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not any(thread.is_alive() for thread in threads), "a thread deadlocked on the store"
    assert unexpected == []
    assert len(store) <= 3


def test_an_unknown_capture_id_is_an_error_not_a_blank_capture() -> None:
    with pytest.raises(KeyError, match="no capture is under review"):
        VCUBCaptureReviewStore().get("deadbeef")


def test_the_store_forgets_the_oldest_capture_once_it_is_full(stub_reader, monkeypatch) -> None:
    store = VCUBCaptureReviewStore(capacity=2)
    ids = [
        store.parse_image(
            source_reference="vcub.png", raw_image=_IMAGE + bytes([index]), captured_at=_CAPTURED_AT
        )[0]
        for index in range(3)
    ]

    assert len(store) == 2
    with pytest.raises(KeyError):
        store.get(ids[0])
    assert store.get(ids[2]) is not None


def test_a_capacity_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="capacity must be at least 1"):
        VCUBCaptureReviewStore(capacity=0)


def test_the_capture_id_depends_on_both_the_image_and_the_instant() -> None:
    assert capture_id_for("a" * 64, _CAPTURED_AT) == capture_id_for("a" * 64, _CAPTURED_AT)
    assert capture_id_for("a" * 64, _CAPTURED_AT) != capture_id_for("b" * 64, _CAPTURED_AT)
    assert capture_id_for("a" * 64, _CAPTURED_AT) != capture_id_for("a" * 64, _REVIEWED_AT)


def test_the_default_capture_timestamp_is_an_explicit_utc_instant() -> None:
    assert utc_now_iso().endswith("Z")
    assert len(utc_now_iso()) == len("2026-08-18T09:30:00Z")


def test_the_capture_slice_never_reaches_the_pricing_package() -> None:
    """Transcription and confirmation must not touch a pricing path at all.

    Checked by importing the whole capture slice in a fresh interpreter and
    looking at what came with it: a docstring may name the pricing package,
    but nothing in this slice may actually pull it in.
    """

    import subprocess
    import sys

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, json;"
            "import shiori_pricing_lab.app.vcub_capture_review;"
            "import shiori_pricing_lab.data.bloomberg_vcub_atm_template;"
            "import shiori_pricing_lab.data.bloomberg_vcub_ocr;"
            "print(json.dumps([name for name in sys.modules "
            "if name.startswith('shiori_pricing_lab.pricing')]))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(probe.stdout.strip()) == []
