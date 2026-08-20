"""The workbench-side review store for VCUB ATM captures (Issue #181).

The store is what makes "confirmation is impossible while anything blocks" a
server-side fact: the page names a capture by id and never posts one back,
so it cannot drop a blocking error on the round trip.
"""

from __future__ import annotations

import json
import random
import threading
import time

import pytest
from test_bloomberg_vcub_atm_template import (
    canonical_tokens,
    grid_tokens,
    metadata_tokens,
    outside_grid_tokens,
    provenance,
)

import shiori_pricing_lab.app.vcub_capture_review as review_module
from shiori_pricing_lab.app.vcub_capture_review import (
    VCUBCaptureReviewStore,
    VCUBCaptureStoreFullError,
    capture_id_for,
    response_fingerprint,
    utc_now_iso,
)
from shiori_pricing_lab.data.bloomberg_vcub_atm_template import parse_vcub_atm_tokens
from shiori_pricing_lab.data.bloomberg_vcub_capture import VCUBCaptureStatus

_IMAGE = b"\x89PNG\r\n\x1a\n-synthetic-not-a-screenshot"
_CAPTURED_AT = "2026-08-18T09:30:00Z"
_REVIEWED_AT = "2026-08-18T09:41:00Z"


def _tokens_with_cell(text: str):
    """The canonical grid with one cell replaced by ``text``."""

    return metadata_tokens() + grid_tokens(value_overrides={(2, 3): text}) + (
        outside_grid_tokens()
    )


def _capture_with_cell(text: str):
    return parse_vcub_atm_tokens(tuple(_tokens_with_cell(text)), provenance=provenance())


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


def test_a_traders_own_confirmation_can_be_repeated_without_raising(stub_reader) -> None:
    """Issue #183 gave a confirmation a second job: writing the surface.

    That write can fail, so the trader has to be able to press Confirm again
    to retry it -- which ``confirm`` alone refuses, the state machine being
    terminal (Codex review, PR #184).
    """

    store = VCUBCaptureReviewStore()
    capture_id, _capture, _notes = _parse(store)

    first = store.confirm_idempotent(capture_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)
    again = store.confirm_idempotent(capture_id, reviewed_by="Eddy")

    assert first.review_status is VCUBCaptureStatus.CONFIRMED
    assert again is first
    assert again.reviewed_at == _REVIEWED_AT


def test_a_repeat_is_only_ever_the_capture_s_own_confirmer(stub_reader) -> None:
    """One decision, by one named trader -- the Issue #181 invariant stands."""

    store = VCUBCaptureReviewStore()
    capture_id, _capture, _notes = _parse(store)
    store.confirm_idempotent(capture_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    with pytest.raises(ValueError, match="only a PENDING_REVIEW capture"):
        store.confirm_idempotent(capture_id, reviewed_by="Someone Else")

    assert store.get(capture_id).reviewed_by == "Eddy"


def test_a_rejected_capture_is_never_confirmed_by_a_repeat(stub_reader) -> None:
    store = VCUBCaptureReviewStore()
    capture_id, _capture, _notes = _parse(store)
    store.reject(capture_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    with pytest.raises(ValueError, match="only a PENDING_REVIEW capture"):
        store.confirm_idempotent(capture_id, reviewed_by="Eddy")

    assert store.get(capture_id).review_status is VCUBCaptureStatus.REJECTED


def test_overlapping_repeats_by_one_trader_all_get_the_same_confirmation(stub_reader) -> None:
    """Codex review round 2, PR #184.

    The check and the transition have to happen together under the lock. As
    a read followed by a write, two same-trader requests on a pending
    capture -- a double click, a browser retrying a timed-out POST -- both
    saw ``PENDING_REVIEW``, and the loser was then refused for a
    confirmation that had in fact been accepted.
    """

    store = VCUBCaptureReviewStore()
    capture_id, _capture, _notes = _parse(store)
    barrier = threading.Barrier(8)
    results: list[object] = []

    def press() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(store.confirm_idempotent(capture_id, reviewed_by="Eddy"))
        except BaseException as exc:  # noqa: BLE001
            results.append(exc)

    threads = [threading.Thread(target=press) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert len(results) == 8
    assert not any(isinstance(result, BaseException) for result in results)
    # Every presser sees one decision, recorded once, at one instant.
    assert {result.reviewed_at for result in results} == {store.get(capture_id).reviewed_at}


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


def test_two_clients_parsing_one_image_never_share_a_review_slot(monkeypatch) -> None:
    """Codex review round 4, PR #182.

    Two clients reading the same image in the same second used to share an
    id, and the later store replaced the capture the first client was still
    looking at -- so that client's Confirm applied to OCR output and
    provenance it never saw. The reads run outside the lock and even the
    filename may differ, so the two captures can genuinely disagree.
    """

    reads = iter(
        [
            tuple(canonical_tokens()),
            tuple(canonical_tokens()[:-1]),  # the second read lost a cell
        ]
    )
    monkeypatch.setattr(
        review_module,
        "read_tokens_from_image_bytes",
        lambda raw_image, *, engine=None, **kwargs: (next(reads), ()),
    )
    store = VCUBCaptureReviewStore()

    first_id, first, _ = store.parse_image(
        source_reference="clientA.png", raw_image=_IMAGE, captured_at=_CAPTURED_AT
    )
    second_id, second, _ = store.parse_image(
        source_reference="clientB.png", raw_image=_IMAGE, captured_at=_CAPTURED_AT
    )

    assert first_id != second_id
    confirmed = store.confirm(first_id, reviewed_by="ClientA", reviewed_at=_REVIEWED_AT)
    assert response_fingerprint(confirmed) == response_fingerprint(
        first.confirm(reviewed_by="ClientA", reviewed_at=_REVIEWED_AT)
    )
    assert confirmed.provenance.source_reference == "clientA.png"
    assert store.get(second_id).review_status is VCUBCaptureStatus.PENDING_REVIEW
    assert store.get(second_id).provenance.source_reference == "clientB.png"
    assert second.provenance.source_reference == "clientB.png"


def test_an_evicted_capture_id_is_never_handed_to_a_different_capture(monkeypatch) -> None:
    """Eviction may lose a capture; it must never redirect one.

    The store is capacity-bounded, so an id can fall out of it. Recycling
    that id for a later capture reintroduces the round-4 failure through the
    back door: a client holding the old id would confirm a capture it never
    reviewed. Eviction's one safe outcome is ``get`` raising, which the
    route answers as 404, and that is what must happen here.
    """

    reads = iter(
        [tuple(canonical_tokens())] + [tuple(canonical_tokens()[:-1])] * 8
    )
    monkeypatch.setattr(
        review_module,
        "read_tokens_from_image_bytes",
        lambda raw_image, *, engine=None, **kwargs: (next(reads), ()),
    )
    store = VCUBCaptureReviewStore(capacity=2)

    mine, reviewed, _ = store.parse_image(
        source_reference="mine.png", raw_image=b"A", captured_at=_CAPTURED_AT
    )
    for other in (b"B", b"C"):
        store.parse_image(
            source_reference="other.png", raw_image=other, captured_at=_CAPTURED_AT
        )
    reclaimed, _capture, _ = store.parse_image(
        source_reference="mine.png", raw_image=b"A", captured_at=_CAPTURED_AT
    )

    assert reclaimed != mine
    assert reviewed.grid is not None
    with pytest.raises(KeyError, match="no capture is under review"):
        store.confirm(mine, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)


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


@pytest.mark.parametrize("seed", range(6))
def test_the_retention_invariants_hold_under_randomised_operation(monkeypatch, seed: int) -> None:
    """Every retention rule at once, against randomised parse/decide traffic.

    The single-rule tests above each pin one behaviour in a hand-built
    scenario. Rounds 5 and 6 both found defects that only appeared when the
    rules interacted -- an id recycled after eviction, and a re-read
    refreshing an age that eviction then relied on -- so this drives the
    store the way a session would and checks all four invariants after every
    step:

    1. a decided capture is never removed;
    2. every id still held goes on matching the response its own client was
       handed -- not merely the newest one. Checking only the latest return
       is vacuous: that return has already overwritten the slot, so a
       ``_find_identifier`` that reused every pending base id passed all six
       seeds. Each outstanding client's content is therefore remembered by
       id and re-checked after every step (Codex review, PR #182);
    3. only the oldest *pending* capture by true insertion age is evicted;
    4. capacity is never exceeded.
    """

    rng = random.Random(seed)
    variants = [
        tuple(canonical_tokens()),
        tuple(canonical_tokens()[:-1]),
        tuple(canonical_tokens()[:-2]),
        # The signed-zero pair: two reads Python calls equal but a client is
        # served differently. Included so the guard exercises the exact class
        # of divergence that dataclass equality missed, rather than only
        # coarse structural differences.
        tuple(_tokens_with_cell("-0.00")),
        tuple(_tokens_with_cell("0.00")),
    ]
    monkeypatch.setattr(
        review_module,
        "read_tokens_from_image_bytes",
        lambda raw_image, *, engine=None, **kwargs: (variants[rng.randrange(len(variants))], ()),
    )
    capacity = rng.randint(2, 4)
    store = VCUBCaptureReviewStore(capacity=capacity)
    birth: dict[str, int] = {}
    decided: set[str] = set()
    # id -> the reviewable content its client was handed. A decision advances
    # review_status, so only the content a trader actually reviewed is
    # compared; that must never change under an id someone is holding.
    handed: dict[str, str] = {}
    clock = 0

    def reviewed_content(capture) -> str:
        payload = capture.to_dict()
        for volatile in ("review_status", "reviewed_by", "reviewed_at", "can_confirm"):
            payload.pop(volatile)
        return json.dumps(payload, sort_keys=True)

    def every_outstanding_id_still_matches_its_client() -> None:
        for identifier, content in handed.items():
            held = store._captures.get(identifier)
            if held is None:
                continue  # legitimately evicted; ids are never recycled
            assert reviewed_content(held) == content, (
                f"the capture under {identifier[:8]} is no longer the one its client reviewed"
            )

    for _ in range(40):
        if rng.random() < 0.65:
            before = list(store._captures)
            pending_before = [
                identifier
                for identifier in before
                if store._captures[identifier].review_status is VCUBCaptureStatus.PENDING_REVIEW
            ]
            try:
                capture_id, capture, _notes = store.parse_image(
                    source_reference="vcub.png",
                    raw_image=bytes([rng.randrange(5)]),
                    captured_at=_CAPTURED_AT,
                )
            except VCUBCaptureStoreFullError:
                assert decided <= set(store._captures), "a refusal discarded a decision"
                continue
            assert response_fingerprint(store._captures[capture_id]) == response_fingerprint(
                capture
            ), "stored capture is not the one handed out"
            content = reviewed_content(capture)
            # The invariant, stated where it can actually fail: an id already
            # handed to a client must never come back carrying different
            # content. Recording the newest content unconditionally -- the
            # first version of this guard -- destroyed the earlier client's
            # record and let a "reuse any pending slot" mutant pass.
            assert handed.get(capture_id, content) == content, (
                f"id {capture_id[:8]} was handed out again for different content"
            )
            handed[capture_id] = content
            if capture_id not in birth:
                birth[capture_id] = clock
                clock += 1
            evicted = [item for item in before if item not in store._captures]
            if evicted:
                oldest_pending = min(pending_before, key=lambda item: birth.get(item, -1))
                assert evicted == [oldest_pending], "eviction did not take the oldest pending"
        elif store._captures:
            capture_id = rng.choice(list(store._captures))
            try:
                action = store.confirm if rng.random() < 0.5 else store.reject
                action(capture_id, reviewed_by="trader", reviewed_at=_REVIEWED_AT)
                decided.add(capture_id)
            except (ValueError, KeyError):
                pass

        assert decided <= set(store._captures), "a decided capture was evicted"
        assert len(store._captures) <= capacity
        every_outstanding_id_still_matches_its_client()


def test_the_store_stays_consistent_under_concurrent_parse_and_review(stub_reader) -> None:
    """Deadlock-freedom and eviction, exercised together.

    The single-transition test above pins one race in isolation. This one
    runs parse, confirm, reject, and capacity eviction against each other on
    several threads: the lock is non-reentrant, so any locked method that
    called a public locking one would hang here rather than fail quietly.
    ``ValueError`` (already decided), ``KeyError`` (evicted before the review
    landed), and ``VCUBCaptureStoreFullError`` (the retention policy refusing
    to discard a decision) are all legitimate outcomes; anything else is not.
    """

    store = VCUBCaptureReviewStore(capacity=3)
    unexpected: list[str] = []

    def churn(worker: int) -> None:
        try:
            for index in range(15):
                try:
                    capture_id, _capture, _notes = store.parse_image(
                        source_reference="vcub.png",
                        raw_image=bytes([index % 4]) * 8,
                        captured_at=_CAPTURED_AT,
                    )
                except VCUBCaptureStoreFullError:
                    continue
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


def test_the_store_forgets_the_oldest_pending_capture_once_it_is_full(stub_reader) -> None:
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


# ---------------------------------------------------------------------------
# Retention policy (Eddy/Sophira RED decision on Issue #181)
# ---------------------------------------------------------------------------


def _fill(store: VCUBCaptureReviewStore, count: int, *, first: int = 0) -> list[str]:
    return [
        store.parse_image(
            source_reference="vcub.png",
            raw_image=_IMAGE + bytes([index]),
            captured_at=_CAPTURED_AT,
        )[0]
        for index in range(first, first + count)
    ]


@pytest.mark.parametrize("decide", ["confirm", "reject"])
def test_a_decided_capture_is_never_evicted_to_make_room(stub_reader, decide: str) -> None:
    """Requirement 1: a decision is a record, and the store never loses one."""

    store = VCUBCaptureReviewStore(capacity=2)
    (decided_id,) = _fill(store, 1)
    getattr(store, decide)(decided_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    _fill(store, 6, first=1)  # far more pressure than the store can hold

    kept = store.get(decided_id)
    assert kept.review_status is not VCUBCaptureStatus.PENDING_REVIEW
    assert kept.reviewed_by == "Eddy"


def test_the_oldest_pending_capture_is_the_one_evicted(stub_reader) -> None:
    """Requirement 2: pending captures are the only eviction candidates."""

    store = VCUBCaptureReviewStore(capacity=3)
    first, second, third = _fill(store, 3)
    store.confirm(first, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    _fill(store, 1, first=3)

    assert store.get(first).review_status is VCUBCaptureStatus.CONFIRMED
    with pytest.raises(KeyError):
        store.get(second)  # the oldest *pending* one, not the oldest one
    assert store.get(third) is not None


def test_a_store_full_of_decided_captures_refuses_a_new_parse(stub_reader) -> None:
    """Requirement 4: refuse the parse rather than discard a decision."""

    store = VCUBCaptureReviewStore(capacity=2)
    for capture_id in _fill(store, 2):
        store.confirm(capture_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    with pytest.raises(VCUBCaptureStoreFullError, match="already been confirmed or rejected"):
        _fill(store, 1, first=2)

    assert len(store) == 2


def test_the_store_full_message_says_what_to_do_about_it(stub_reader) -> None:
    store = VCUBCaptureReviewStore(capacity=1)
    (only_id,) = _fill(store, 1)
    store.reject(only_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    with pytest.raises(VCUBCaptureStoreFullError) as caught:
        _fill(store, 1, first=1)

    message = str(caught.value)
    assert "Restart the workbench" in message
    assert "in memory only" in message


def test_two_reads_differing_only_in_signed_zero_get_distinct_slots(monkeypatch) -> None:
    """Codex review on `49bf0cd`, PR #182.

    Slot reuse asks "is this the read the client is already looking at",
    and Python equality answers that question wrongly for signed zero:
    ``-0.0 == 0.0`` is true, but the client is shown -- and served -- two
    different numbers. Reusing the slot therefore let a client confirm a
    capture whose displayed value it never saw.
    """

    reads = iter(
        [
            tuple(_tokens_with_cell("-0.00")),
            tuple(_tokens_with_cell("0.00")),
        ]
    )
    monkeypatch.setattr(
        review_module,
        "read_tokens_from_image_bytes",
        lambda raw_image, *, engine=None, **kwargs: (next(reads), ()),
    )
    store = VCUBCaptureReviewStore()

    first_id, first, _ = store.parse_image(
        source_reference="a.png", raw_image=_IMAGE, captured_at=_CAPTURED_AT
    )
    second_id, second, _ = store.parse_image(
        source_reference="a.png", raw_image=_IMAGE, captured_at=_CAPTURED_AT
    )

    assert first.grid == second.grid, "dataclass equality alone would have reused the slot"
    assert first_id != second_id
    assert json.dumps(store.get(first_id).grid.value_at("3Mo", "4Yr")) == json.dumps(
        first.grid.value_at("3Mo", "4Yr")
    )


def test_the_response_fingerprint_separates_values_python_calls_equal() -> None:
    negative = _capture_with_cell("-0.00")
    positive = _capture_with_cell("0.00")

    assert negative.grid == positive.grid  # Python's own answer
    assert response_fingerprint(negative) != response_fingerprint(positive)


def test_a_refused_parse_reserves_no_identifier(stub_reader) -> None:
    """Codex review round 6, PR #182.

    The id was committed before the capacity check could refuse, so every
    refused parse burned one permanently: 200 refusals of a single image
    burned 200 ids, growing the set without bound and lengthening the
    suffix walk each time.
    """

    store = VCUBCaptureReviewStore(capacity=1)
    (only_id,) = _fill(store, 1)
    store.confirm(only_id, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)
    reserved_before = len(store._issued_identifiers)

    for _ in range(25):
        with pytest.raises(VCUBCaptureStoreFullError):
            _fill(store, 1, first=1)

    assert len(store._issued_identifiers) == reserved_before


def test_reusing_a_pending_slot_does_not_make_it_look_younger(stub_reader) -> None:
    """Codex review round 6, PR #182.

    Re-reading a capture refreshed its position, so the next parse evicted a
    *younger* pending capture instead of the oldest one -- breaking the very
    rule the retention policy is built on.
    """

    store = VCUBCaptureReviewStore(capacity=2)
    oldest, newer = _fill(store, 2)

    # An identical re-read of the oldest: same slot, and its age must stand.
    again, _capture, _notes = store.parse_image(
        source_reference="vcub.png", raw_image=_IMAGE + bytes([0]), captured_at=_CAPTURED_AT
    )
    assert again == oldest
    _fill(store, 1, first=2)

    with pytest.raises(KeyError):
        store.get(oldest)
    assert store.get(newer) is not None


def test_recording_a_decision_never_pushes_another_capture_out(stub_reader) -> None:
    """Deciding replaces an entry rather than adding one, so it frees nothing
    and must cost nothing."""

    store = VCUBCaptureReviewStore(capacity=3)
    first, second, third = _fill(store, 3)

    store.confirm(third, reviewed_by="Eddy", reviewed_at=_REVIEWED_AT)

    assert len(store) == 3
    assert {store.get(first) is not None, store.get(second) is not None} == {True}


def test_an_identical_re_read_at_capacity_reuses_its_slot_without_refusing(
    stub_reader,
) -> None:
    """A re-read that reuses its own slot adds nothing, so a full store of
    pending captures must not treat it as needing room."""

    store = VCUBCaptureReviewStore(capacity=2)
    first, second = _fill(store, 2)

    again, _capture, _notes = store.parse_image(
        source_reference="vcub.png", raw_image=_IMAGE + bytes([1]), captured_at=_CAPTURED_AT
    )

    assert again == second
    assert store.get(first) is not None
    assert len(store) == 2


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
