"""The workbench bridge's VCUB ATM capture routes (Issue #181).

Every test here drives the real ``ThreadingHTTPServer`` over loopback, the
same way the served page does, with the OCR seam stubbed so the suite stays
offline and deterministic.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_bloomberg_vcub_atm_template import canonical_tokens

import shiori_pricing_lab.app.standalone_option_workbench_server as server_module
import shiori_pricing_lab.app.vcub_capture_review as review_module
from shiori_pricing_lab.app.standalone_option_workbench_server import PROTOTYPE_DIR, create_server
from shiori_pricing_lab.app.vcub_capture_review import VCUBCaptureReviewStore
from shiori_pricing_lab.data.bloomberg_vcub_ocr import VCUBOCRUnavailableError
from shiori_pricing_lab.data.vol_surface_store import VolSurfaceStore

_IMAGE = b"\x89PNG\r\n\x1a\n-synthetic-not-a-screenshot"
_IMAGE_B64 = base64.b64encode(_IMAGE).decode("ascii")


@pytest.fixture()
def server_url() -> Iterator[str]:
    server = create_server(host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path) -> None:
    """Clean, throwaway stores per test -- the real ones are process-wide.

    The canonical store is redirected at a ``tmp_path`` database as well as
    the in-memory review store, so confirming a capture in a test never
    writes into the workbench's own ``data/vol_surfaces.sqlite3``.
    """

    monkeypatch.setattr(server_module, "VCUB_CAPTURE_REVIEW_STORE", VCUBCaptureReviewStore())
    monkeypatch.setattr(
        server_module, "VOL_SURFACE_STORE", VolSurfaceStore(tmp_path / "vol_surfaces.sqlite3")
    )


@pytest.fixture()
def stub_reader(monkeypatch):
    def _read(raw_image, *, engine=None, **kwargs):
        return tuple(canonical_tokens()), ("'8S' was read with confidence 0.21 and was dropped",)

    monkeypatch.setattr(review_module, "read_tokens_from_image_bytes", _read)


def _post_json(url: str, payload: object) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _parse(server_url: str, **overrides) -> tuple[int, dict]:
    body = {"source_reference": "vcub_atm_usd.png", "image_base64": _IMAGE_B64}
    body.update(overrides)
    return _post_json(f"{server_url}/api/vcub/atm/parse", body)


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def test_the_capture_view_static_files_are_served_verbatim(server_url: str) -> None:
    for route, file_name in (
        ("/vcub_capture.css", "vcub_capture.css"),
        ("/vcub_capture.js", "vcub_capture.js"),
    ):
        with urllib.request.urlopen(f"{server_url}{route}") as response:
            assert response.read() == (PROTOTYPE_DIR / file_name).read_bytes()


def test_the_api_contract_id_moved_with_the_new_capture_routes() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "launch_workbench.py").read_text(
        encoding="utf-8"
    )

    assert f'"{server_module.API_CONTRACT_ID}"' in launcher


def test_parsing_a_screenshot_returns_a_reviewable_capture(server_url, stub_reader) -> None:
    status, payload = _parse(server_url)

    assert status == 200
    assert payload["capture_id"]
    capture = payload["capture"]
    assert capture["review_status"] == "PENDING_REVIEW"
    assert capture["can_confirm"] is True
    assert capture["grid"]["expiry_labels"][0] == "1Mo"
    assert capture["grid"]["tenor_labels"][0] == "1Yr"
    assert payload["reader_notes"] == ["'8S' was read with confidence 0.21 and was dropped"]


def test_the_response_carries_the_hash_of_the_bytes_that_were_posted(
    server_url, stub_reader
) -> None:
    import hashlib

    _status, payload = _parse(server_url)

    provenance = payload["capture"]["provenance"]
    assert provenance["source_image_sha256"] == hashlib.sha256(_IMAGE).hexdigest()
    assert provenance["source_image_bytes"] == len(_IMAGE)
    assert provenance["source_reference"] == "vcub_atm_usd.png"


def test_the_posted_screenshot_is_never_written_to_disk(server_url, stub_reader, tmp_path) -> None:
    before = sorted(path.name for path in PROTOTYPE_DIR.iterdir())

    _parse(server_url)

    assert sorted(path.name for path in PROTOTYPE_DIR.iterdir()) == before
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "body,expected_fragment",
    [
        ({"source_reference": "x.png"}, "image_base64"),
        ({"image_base64": _IMAGE_B64}, "source_reference"),
    ],
)
def test_parse_rejects_a_body_missing_a_required_key(server_url, body, expected_fragment) -> None:
    status, payload = _post_json(f"{server_url}/api/vcub/atm/parse", body)

    assert status == 400
    assert expected_fragment in payload["error"]


def test_parse_rejects_a_malformed_json_body(server_url: str) -> None:
    request = urllib.request.Request(
        f"{server_url}/api/vcub/atm/parse",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request)

    assert caught.value.code == 400


@pytest.mark.parametrize("image_base64", ["not-base64!!", "", "   "])
def test_parse_rejects_an_unusable_image_payload(server_url, stub_reader, image_base64) -> None:
    status, payload = _parse(server_url, image_base64=image_base64)

    assert status == 400
    assert "image_base64" in payload["error"]


def test_an_oversized_image_is_refused_before_it_is_read(server_url, stub_reader) -> None:
    oversized = base64.b64encode(
        b"x" * (server_module.MAX_CAPTURE_IMAGE_BYTES + 1)
    ).decode("ascii")

    status, payload = _parse(server_url, image_base64=oversized)

    assert status == 400
    assert "limit for one screenshot" in payload["error"]


def test_a_store_full_of_decided_captures_answers_507_not_a_silent_eviction(
    server_url, stub_reader, monkeypatch
) -> None:
    """Eddy/Sophira RED decision on Issue #181: a decided capture is never
    discarded to make room, so the parse is refused instead."""

    monkeypatch.setattr(
        server_module, "VCUB_CAPTURE_REVIEW_STORE", VCUBCaptureReviewStore(capacity=1)
    )
    _status, parsed = _parse(server_url)
    _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    status, payload = _parse(server_url, source_reference="another.png")

    assert status == 507
    assert "already been confirmed or rejected" in payload["error"]
    assert "Restart the workbench" in payload["error"]
    # The decided capture is still there, and still confirmed.
    assert (
        server_module.VCUB_CAPTURE_REVIEW_STORE.get(parsed["capture_id"]).review_status.value
        == "CONFIRMED"
    )


def test_a_missing_ocr_reader_answers_501_with_the_install_command(
    server_url, monkeypatch
) -> None:
    def _unavailable(raw_image, *, engine=None, **kwargs):
        raise VCUBOCRUnavailableError(
            'Visual capture needs the optional OCR reader, which is not installed. '
            'From the repository root, run: .venv\\Scripts\\python -m pip install -e ".[capture]"'
        )

    monkeypatch.setattr(review_module, "read_tokens_from_image_bytes", _unavailable)

    status, payload = _parse(server_url)

    assert status == 501
    assert 'pip install -e ".[capture]"' in payload["error"]


# ---------------------------------------------------------------------------
# Confirm / reject
# ---------------------------------------------------------------------------


def test_confirming_records_the_named_trader_on_the_server_side(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 200
    capture = payload["capture"]
    assert capture["review_status"] == "CONFIRMED"
    assert capture["reviewed_by"] == "Eddy"
    assert capture["reviewed_at"].endswith("Z")
    assert capture["can_confirm"] is False


def test_rejecting_leaves_the_capture_unaccepted(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/reject",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 200
    assert payload["capture"]["review_status"] == "REJECTED"
    assert payload["capture"]["can_confirm"] is False
    assert (
        server_module.VCUB_CAPTURE_REVIEW_STORE.get(parsed["capture_id"]).accepted_grid is None
    )


def test_a_blocked_capture_cannot_be_confirmed_through_the_route(
    server_url, monkeypatch
) -> None:
    tokens = tuple(token for token in canonical_tokens() if token.text != "Expiry")
    monkeypatch.setattr(
        review_module,
        "read_tokens_from_image_bytes",
        lambda raw_image, *, engine=None, **kwargs: (tokens, ()),
    )
    _status, parsed = _parse(server_url)
    assert parsed["capture"]["can_confirm"] is False

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 400
    assert "EXPIRY_ANCHOR_UNRESOLVED" in payload["error"]
    assert (
        server_module.VCUB_CAPTURE_REVIEW_STORE.get(parsed["capture_id"]).review_status.value
        == "PENDING_REVIEW"
    )


def test_a_capture_the_server_never_saw_cannot_be_confirmed(server_url: str) -> None:
    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": "0" * 32, "reviewed_by": "Eddy"},
    )

    assert status == 404
    assert "no capture is under review" in payload["error"]


@pytest.mark.parametrize("reviewed_by", ["", "   "])
def test_a_review_without_a_named_trader_is_refused(server_url, stub_reader, reviewed_by) -> None:
    _status, parsed = _parse(server_url)

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": reviewed_by},
    )

    assert status == 400
    assert "reviewed_by" in payload["error"]


def test_a_capture_cannot_be_confirmed_twice(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)
    _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Someone Else"},
    )

    assert status == 400
    assert "PENDING_REVIEW" in payload["error"]


# ---------------------------------------------------------------------------
# 20: no production pricing path is reachable from this flow
# ---------------------------------------------------------------------------


def test_neither_parsing_nor_confirming_invokes_the_pricing_entry_point(
    server_url, stub_reader, monkeypatch
) -> None:
    def _explode(*args, **kwargs):
        raise AssertionError("the capture flow must never price anything")

    monkeypatch.setattr(server_module, "price_standalone_option_case", _explode)
    monkeypatch.setattr(server_module, "lookup_bloomberg_bond", _explode)
    monkeypatch.setattr(server_module, "fetch_usd_sofr_option_discount_curve", _explode)

    _status, parsed = _parse(server_url)
    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 200
    assert payload["capture"]["review_status"] == "CONFIRMED"


# --------------------------------------------------------------------------
# Issue #183 -- durable storage of a confirmed capture
# --------------------------------------------------------------------------


def test_confirming_saves_the_surface_and_says_so(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 200
    storage = payload["storage"]
    assert storage["status"] == "SAVED"
    assert storage["error"] is None
    assert storage["point_count"] == 48  # the 8x6 synthetic fixture grid
    stored = server_module.VOL_SURFACE_STORE.fetch_surface(storage["surface_id"])
    assert stored.provenance.capture_id == parsed["capture_id"]
    assert stored.provenance.confirmed_by == "Eddy"
    assert len(stored.points) == 48


def test_the_saved_surface_outlives_the_process_that_confirmed_it(
    server_url, stub_reader
) -> None:
    """A fresh store over the same file is what a restart actually looks like."""

    _status, parsed = _parse(server_url)
    _status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )
    surface_id = payload["storage"]["surface_id"]
    saved = server_module.VOL_SURFACE_STORE.fetch_surface(surface_id)

    after_restart = VolSurfaceStore(server_module.VOL_SURFACE_STORE.database_path)

    assert after_restart.fetch_surface(surface_id) == saved
    assert [summary.surface_id for summary in after_restart.list_surfaces()] == [surface_id]


def test_rejecting_never_offers_anything_to_the_canonical_store(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)

    _status, payload = _post_json(
        f"{server_url}/api/vcub/atm/reject",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert payload["storage"]["status"] == "NOT_ATTEMPTED"
    assert payload["storage"]["surface_id"] is None
    assert server_module.VOL_SURFACE_STORE.list_surfaces() == ()


def test_a_storage_failure_is_reported_rather_than_dressed_up_as_a_save(
    server_url, stub_reader, monkeypatch
) -> None:
    """The confirmation stands; the claim of durability must not."""

    def refuse(surface):
        raise RuntimeError("the vol-surface store refused the write: disk I/O error")

    monkeypatch.setattr(
        server_module.VOL_SURFACE_STORE, "save_confirmed_surface", refuse
    )
    _status, parsed = _parse(server_url)

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 200
    assert payload["capture"]["review_status"] == "CONFIRMED"
    assert payload["storage"]["status"] == "FAILED"
    assert payload["storage"]["surface_id"] is None
    assert "disk I/O error" in payload["storage"]["error"]


def test_the_confirmer_can_retry_a_save_that_failed(server_url, stub_reader, monkeypatch) -> None:
    """Codex review, PR #184.

    A storage failure left the capture confirmed but not durable, and the
    review state machine is terminal, so pressing Confirm again was a 400.
    The trader had no way to retry the write and no way to establish
    durability short of restarting and recapturing the screen.
    """

    real_save = server_module.VOL_SURFACE_STORE.save_confirmed_surface

    def refuse(surface):
        raise RuntimeError("the vol-surface store refused the write: disk I/O error")

    monkeypatch.setattr(server_module.VOL_SURFACE_STORE, "save_confirmed_surface", refuse)
    _status, parsed = _parse(server_url)
    _status, first = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )
    assert first["storage"]["status"] == "FAILED"

    monkeypatch.setattr(
        server_module.VOL_SURFACE_STORE, "save_confirmed_surface", real_save
    )
    status, retried = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 200
    assert retried["storage"]["status"] == "SAVED"
    assert retried["capture"]["review_status"] == "CONFIRMED"
    assert retried["capture"]["reviewed_by"] == "Eddy"
    assert retried["capture"]["reviewed_at"] == first["capture"]["reviewed_at"]
    assert len(server_module.VOL_SURFACE_STORE.list_surfaces()) == 1


def test_retrying_a_save_that_already_worked_stores_nothing_further(
    server_url, stub_reader
) -> None:
    _status, parsed = _parse(server_url)
    _status, first = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    _status, again = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert first["storage"]["status"] == "SAVED"
    assert again["storage"]["status"] == "ALREADY_SAVED"
    assert again["storage"]["surface_id"] == first["storage"]["surface_id"]
    assert len(server_module.VOL_SURFACE_STORE.list_surfaces()) == 1


def test_a_retry_never_lets_a_second_person_confirm_someone_elses_capture(
    server_url, stub_reader
) -> None:
    """The retry is for the trader who already decided it, and nobody else."""

    _status, parsed = _parse(server_url)
    _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Someone Else"},
    )

    assert status == 400
    assert "PENDING_REVIEW" in payload["error"]
    held = server_module.VCUB_CAPTURE_REVIEW_STORE.get(parsed["capture_id"])
    assert held.reviewed_by == "Eddy"


def test_a_rejected_capture_can_never_be_confirmed_by_a_retry(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)
    _post_json(
        f"{server_url}/api/vcub/atm/reject",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    status, payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 400
    assert "PENDING_REVIEW" in payload["error"]
    assert server_module.VOL_SURFACE_STORE.list_surfaces() == ()


def test_a_second_screenshot_of_one_surface_is_refused_not_silently_overwritten(
    server_url, stub_reader
) -> None:
    """Same screen, different image: whose provenance is true is not the server's call."""

    _status, first = _parse(server_url)
    _status, first_payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": first["capture_id"], "reviewed_by": "Eddy"},
    )
    second_image = base64.b64encode(_IMAGE + b"-a-second-screenshot").decode("ascii")
    _status, second = _parse(server_url, image_base64=second_image)
    assert second["capture_id"] != first["capture_id"]
    _status, second_payload = _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": second["capture_id"], "reviewed_by": "Eddy"},
    )

    assert first_payload["storage"]["status"] == "SAVED"
    assert second_payload["storage"]["status"] == "FAILED"
    assert "already stored with different content" in second_payload["storage"]["error"]
    assert len(server_module.VOL_SURFACE_STORE.list_surfaces()) == 1
    stored = server_module.VOL_SURFACE_STORE.fetch_surface(
        first_payload["storage"]["surface_id"]
    )
    assert stored.provenance.capture_id == first["capture_id"]


def test_a_confirmed_capture_changes_nothing_about_the_bundled_case(
    server_url, stub_reader
) -> None:
    case_path = Path(server_module.BASE_CASE_PATH)
    before = case_path.read_bytes()

    _status, parsed = _parse(server_url)
    _post_json(
        f"{server_url}/api/vcub/atm/confirm",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert case_path.read_bytes() == before
