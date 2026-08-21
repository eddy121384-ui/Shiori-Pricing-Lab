"""The workbench bridge's VCUB OTM Swaptions / SABR capture routes (Issue #185).

Every test here drives the real ``ThreadingHTTPServer`` over loopback, the
same way the served page does, with the OCR seam stubbed so the suite stays
offline and deterministic. The stub answers each posted image with the
synthetic slice of the shared fixture that image's *name* asks for, which is
how a multi-image capture session is exercised without an OCR engine or a
Bloomberg screenshot.
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
from test_bloomberg_vcub_otm_template import (
    ROW_LABELS,
    SLICE_A,
    SLICE_B,
    SLICE_C,
    STRIKE_LABELS,
    screenshot_tokens,
)

import shiori_pricing_lab.app.standalone_option_workbench_server as server_module
import shiori_pricing_lab.app.vcub_capture_review as review_module
from shiori_pricing_lab.app.standalone_option_workbench_server import PROTOTYPE_DIR, create_server
from shiori_pricing_lab.app.vcub_capture_review import VCUBCaptureReviewStore
from shiori_pricing_lab.data.bloomberg_vcub_ocr import VCUBOCRUnavailableError
from shiori_pricing_lab.data.vol_surface_store import VolSurfaceStore

#: Three synthetic files, each carrying distinct bytes, so the server sees
#: three genuinely different images the way three real screenshots would be.
_SLICES = {"shot-a.png": SLICE_A, "shot-b.png": SLICE_B, "shot-c.png": SLICE_C}


def _image_bytes(name: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n-synthetic-not-a-screenshot-" + name.encode("ascii")


def _image(name: str) -> dict:
    return {
        "source_reference": name,
        "image_base64": base64.b64encode(_image_bytes(name)).decode("ascii"),
    }


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
    """Clean, throwaway stores per test -- the real ones are process-wide."""

    monkeypatch.setattr(
        server_module, "VCUB_OTM_CAPTURE_REVIEW_STORE", VCUBCaptureReviewStore()
    )
    monkeypatch.setattr(
        server_module, "VOL_SURFACE_STORE", VolSurfaceStore(tmp_path / "vol_surfaces.sqlite3")
    )


@pytest.fixture()
def stub_reader(monkeypatch):
    """Answer each image with the slice its own bytes name."""

    def _read(raw_image, *, engine=None, **kwargs):
        for name, rows in _SLICES.items():
            if raw_image == _image_bytes(name):
                return tuple(screenshot_tokens(rows=rows)), (
                    "'8S' was read with confidence 0.21 and was dropped",
                )
        raise AssertionError(f"unexpected image bytes: {raw_image!r}")

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


def _parse(server_url: str, names=("shot-a.png", "shot-b.png", "shot-c.png")) -> tuple[int, dict]:
    return _post_json(
        f"{server_url}/api/vcub/otm/parse", {"images": [_image(name) for name in names]}
    )


def _confirm(server_url: str, capture_id: str, reviewed_by: str = "Eddy") -> tuple[int, dict]:
    return _post_json(
        f"{server_url}/api/vcub/otm/confirm",
        {"capture_id": capture_id, "reviewed_by": reviewed_by},
    )


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def test_the_otm_capture_view_static_file_is_served_verbatim(server_url: str) -> None:
    with urllib.request.urlopen(f"{server_url}/vcub_otm_capture.js") as response:
        assert response.read() == (PROTOTYPE_DIR / "vcub_otm_capture.js").read_bytes()


def test_the_api_contract_id_moved_with_the_new_otm_routes() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "launch_workbench.py").read_text(
        encoding="utf-8"
    )

    assert f'"{server_module.API_CONTRACT_ID}"' in launcher


def test_three_screenshots_in_one_request_return_one_reviewable_capture(
    server_url, stub_reader
) -> None:
    status, payload = _parse(server_url)

    assert status == 200
    assert payload["capture_id"]
    capture = payload["capture"]
    assert capture["review_status"] == "PENDING_REVIEW"
    assert capture["can_confirm"] is True
    assert len(capture["sources"]) == 3
    assert [row["label"] for row in capture["table"]["rows"]] == [
        f"{term} x {tenor}" for term, tenor in ROW_LABELS
    ]
    assert [strike["label"] for strike in capture["table"]["strikes"]] == list(STRIKE_LABELS)


def test_the_reader_notes_name_the_screenshot_they_came_from(server_url, stub_reader) -> None:
    _status, payload = _parse(server_url)

    assert payload["reader_notes"] == [
        f"{name}: '8S' was read with confidence 0.21 and was dropped"
        for name in ("shot-a.png", "shot-b.png", "shot-c.png")
    ]


def test_the_coverage_block_says_what_each_screenshot_contributed(
    server_url, stub_reader
) -> None:
    _status, payload = _parse(server_url)

    coverage = {item["source_reference"]: item for item in payload["capture"]["coverage"]}
    assert coverage["shot-a.png"]["first_row"] == "1Mo x 1Yr"
    assert coverage["shot-c.png"]["last_row"] == "1Yr x 10Yr"
    assert coverage["shot-b.png"]["shared_row_count"] > 0


def test_the_same_screenshot_twice_is_refused_as_a_bad_request(server_url, stub_reader) -> None:
    status, payload = _parse(server_url, names=("shot-a.png", "shot-a.png"))

    assert status == 400
    assert "more than once" in payload["error"]


def test_an_empty_image_list_is_refused(server_url) -> None:
    status, payload = _post_json(f"{server_url}/api/vcub/otm/parse", {"images": []})

    assert status == 400
    assert "non-empty list" in payload["error"]


def test_a_request_without_images_is_refused(server_url) -> None:
    status, payload = _post_json(f"{server_url}/api/vcub/otm/parse", {"foo": 1})

    assert status == 400
    assert "'images'" in payload["error"]


def test_an_image_that_is_not_base64_is_refused(server_url) -> None:
    status, payload = _post_json(
        f"{server_url}/api/vcub/otm/parse",
        {"images": [{"source_reference": "a.png", "image_base64": "not base64!"}]},
    )

    assert status == 400
    assert "images[0].image_base64" in payload["error"]


def test_more_screenshots_than_one_session_may_carry_are_refused(server_url) -> None:
    images = [_image(f"shot-{index}.png") for index in range(20)]
    status, payload = _post_json(f"{server_url}/api/vcub/otm/parse", {"images": images})

    assert status == 400
    assert "above the" in payload["error"]


def test_a_missing_ocr_reader_is_reported_as_unavailable_not_as_a_bad_request(
    server_url, monkeypatch
) -> None:
    def _unavailable(raw_image, *, engine=None, **kwargs):
        raise VCUBOCRUnavailableError("Visual capture needs the optional OCR reader")

    monkeypatch.setattr(review_module, "read_tokens_from_image_bytes", _unavailable)
    status, payload = _parse(server_url, names=("shot-a.png",))

    assert status == 501
    assert "optional OCR reader" in payload["error"]


# ---------------------------------------------------------------------------
# Confirm, reject, and where a confirmed capture lands
# ---------------------------------------------------------------------------


def test_one_confirm_stores_one_snapshot_of_every_coordinate(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)

    status, payload = _confirm(server_url, parsed["capture_id"])

    assert status == 200
    assert payload["capture"]["review_status"] == "CONFIRMED"
    assert payload["storage"]["status"] == "SAVED"
    assert payload["storage"]["point_count"] == len(ROW_LABELS) * len(STRIKE_LABELS)
    assert payload["storage"]["surface_id"]


def test_the_stored_surface_keeps_every_screenshot_that_produced_it(
    server_url, stub_reader
) -> None:
    _status, parsed = _parse(server_url)
    _status, confirmed = _confirm(server_url, parsed["capture_id"])

    surface = server_module.VOL_SURFACE_STORE.fetch_surface(
        confirmed["storage"]["surface_id"]
    )
    assert [image.source_reference for image in surface.provenance.source_images] == [
        "shot-a.png",
        "shot-b.png",
        "shot-c.png",
    ]


def test_the_stored_surface_keeps_the_spread_and_the_atm_vol_apart(
    server_url, stub_reader
) -> None:
    _status, parsed = _parse(server_url)
    _status, confirmed = _confirm(server_url, parsed["capture_id"])

    surface = server_module.VOL_SURFACE_STORE.fetch_surface(
        confirmed["storage"]["surface_id"]
    )
    kinds = {
        (point.strike_dimension.value, point.value_kind.value) for point in surface.points
    }
    assert kinds == {
        ("ATM", "ABSOLUTE_VOL"),
        ("YIELD_OFFSET_BP", "SPREAD_TO_ATM"),
    }


def test_confirming_the_same_capture_again_is_idempotent(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)
    _confirm(server_url, parsed["capture_id"])

    status, payload = _confirm(server_url, parsed["capture_id"])

    assert status == 200
    assert payload["storage"]["status"] == "ALREADY_SAVED"


def test_a_second_trader_cannot_re_decide_a_confirmed_capture(server_url, stub_reader) -> None:
    _status, parsed = _parse(server_url)
    _confirm(server_url, parsed["capture_id"])

    status, payload = _confirm(server_url, parsed["capture_id"], reviewed_by="Someone Else")

    assert status == 400
    assert "only a PENDING_REVIEW capture" in payload["error"]


def test_a_storage_failure_is_reported_as_confirmed_but_not_saved(
    server_url, stub_reader, monkeypatch
) -> None:
    _status, parsed = _parse(server_url)

    class _RefusingStore:
        database_path = Path("unwritable.sqlite3")

        def save_confirmed_surface(self, surface):
            raise RuntimeError("the vol-surface store refused the write: disk is read-only")

    monkeypatch.setattr(server_module, "VOL_SURFACE_STORE", _RefusingStore())
    status, payload = _confirm(server_url, parsed["capture_id"])

    assert status == 200
    assert payload["capture"]["review_status"] == "CONFIRMED"
    assert payload["storage"]["status"] == "FAILED"
    assert "read-only" in payload["storage"]["error"]


def test_rejecting_a_capture_offers_nothing_to_the_canonical_store(
    server_url, stub_reader
) -> None:
    _status, parsed = _parse(server_url)

    status, payload = _post_json(
        f"{server_url}/api/vcub/otm/reject",
        {"capture_id": parsed["capture_id"], "reviewed_by": "Eddy"},
    )

    assert status == 200
    assert payload["capture"]["review_status"] == "REJECTED"
    assert payload["capture"]["table"] is not None  # still reviewable evidence
    assert payload["storage"]["status"] == "NOT_ATTEMPTED"
    assert server_module.VOL_SURFACE_STORE.list_surfaces() == ()


def test_a_blocked_capture_cannot_be_confirmed_through_the_route(
    server_url, monkeypatch
) -> None:
    """Two screenshots that disagree about one coordinate."""

    def _read(raw_image, *, engine=None, **kwargs):
        if raw_image == _image_bytes("shot-a.png"):
            return tuple(screenshot_tokens(rows=SLICE_A)), ()
        return (
            tuple(screenshot_tokens(rows=SLICE_B, value_overrides={(4, 2): "99.99"})),
            (),
        )

    monkeypatch.setattr(review_module, "read_tokens_from_image_bytes", _read)
    _status, parsed = _parse(server_url, names=("shot-a.png", "shot-b.png"))
    assert parsed["capture"]["can_confirm"] is False

    status, payload = _confirm(server_url, parsed["capture_id"])

    assert status == 400
    assert "OVERLAP_VALUE_CONFLICT" in payload["error"]
    assert server_module.VOL_SURFACE_STORE.list_surfaces() == ()


def test_an_unknown_capture_id_is_a_404(server_url) -> None:
    status, _payload = _confirm(server_url, "0" * 32)

    assert status == 404


def test_an_atm_capture_id_is_unknown_to_the_otm_routes(server_url, monkeypatch) -> None:
    """Two stores, two id spaces: a capture is reviewed where it was made."""

    from test_bloomberg_vcub_atm_template import canonical_tokens

    monkeypatch.setattr(
        server_module, "VCUB_CAPTURE_REVIEW_STORE", VCUBCaptureReviewStore()
    )
    monkeypatch.setattr(
        review_module,
        "read_tokens_from_image_bytes",
        lambda raw_image, *, engine=None, **kwargs: (tuple(canonical_tokens()), ()),
    )
    _status, atm = _post_json(
        f"{server_url}/api/vcub/atm/parse",
        {
            "source_reference": "atm.png",
            "image_base64": base64.b64encode(_image_bytes("atm.png")).decode("ascii"),
        },
    )

    status, _payload = _confirm(server_url, atm["capture_id"])

    assert status == 404
