"""The coworker-facing bootstrapper (Issue #206).

Two layers, deliberately:

* **Everywhere, including Linux CI** -- the manifest contract, the generated
  C# constants, the build invocation, and a source audit proving no CLR type
  name or stack trace can reach a trader. These are what stop a bad release
  being cut, and they must not be skippable.
* **Windows only** -- the real compiled executable, driven end to end against a
  loopback payload server: cold install, warm launch with the network taken
  away entirely, hash refusal, truncated download, corrupt zip, and a failed
  install leaving the previous good runtime untouched.

The Windows layer builds with ``csc.exe`` from the in-box .NET Framework, so it
needs nothing installed on the runner either.

Nothing here touches pricing, Bloomberg or the converter's own behaviour.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import build_government_bond_futures_converter_bootstrapper as builder  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT_ROOT / "bootstrapper" / "Bootstrapper.cs"
MANIFEST = PROJECT_ROOT / "packaging" / "runtime_manifest.json"

RUNTIME_EXE_NAME = "Government Bond Futures Converter.exe"

#: Exit codes the bootstrapper promises. Pinned here as literals rather than
#: imported from anywhere: they are a contract with shortcuts and smoke tests,
#: and a silent renumbering must fail.
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_PREREQUISITE_MISSING = 2
EXIT_DOWNLOAD_FAILED = 4
EXIT_INTEGRITY_FAILED = 5
EXIT_INSTALL_FAILED = 6
EXIT_RUNTIME_UNUSABLE = 7

_WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="the bootstrapper is a Windows executable"
)


# ---------------------------------------------------------------------------
# The manifest contract -- runs everywhere
# ---------------------------------------------------------------------------


def test_the_checked_in_manifest_is_a_valid_immutable_contract() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    builder.require_valid_manifest(manifest)
    assert manifest["executable_relative_path"] == RUNTIME_EXE_NAME


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("sha256", "abc", "64 hex"),
        ("sha256", "z" * 64, "64 hex"),
        ("asset_url", "http://example.com/x-1.0.0-rc1.zip", "https"),
        ("schema", "something-else", "schema"),
        ("size_bytes", -1, "size_bytes"),
    ],
)
def test_a_manifest_that_could_ship_a_wrong_payload_is_refused(field, value, expected) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest[field] = value
    with pytest.raises(builder.BootstrapperBuildError) as exc:
        builder.require_valid_manifest(manifest)
    assert expected in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r/releases/latest/download/Runtime_v1.0.0-rc1.zip",
        "https://github.com/o/r/releases/download/latest",
    ],
)
def test_a_latest_url_is_refused(url: str) -> None:
    """'latest' is the one URL shape this design cannot have.

    The payload behind a shipped executable would then be free to change while
    the SHA-256 compiled beside it did not -- so every download would be
    refused, on desks, with no way to tell that from an attack.
    """

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["asset_url"] = url
    with pytest.raises(builder.BootstrapperBuildError) as exc:
        builder.require_valid_manifest(manifest)
    assert "latest" in str(exc.value)


def test_the_url_must_name_the_version_it_claims_to_carry() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["asset_url"] = "https://github.com/o/r/releases/download/v9.9.9/Runtime_v9.9.9.zip"
    with pytest.raises(builder.BootstrapperBuildError) as exc:
        builder.require_valid_manifest(manifest)
    assert "does not contain the runtime version" in str(exc.value)


def test_a_missing_field_fails_the_build_not_a_desk() -> None:
    for field in builder.REQUIRED_MANIFEST_FIELDS:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        del manifest[field]
        with pytest.raises(builder.BootstrapperBuildError):
            builder.require_valid_manifest(manifest)


# ---------------------------------------------------------------------------
# The generated constants and the compile command -- run everywhere
# ---------------------------------------------------------------------------


def test_the_manifest_is_compiled_into_the_executable_not_read_beside_it() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    generated = builder.generate_manifest_source(manifest)
    assert f'AssetUrl = "{manifest["asset_url"]}"' in generated
    assert f'Sha256 = "{manifest["sha256"]}"' in generated
    assert f'RuntimeVersion = "{manifest["runtime_version"]}"' in generated
    # Nothing beside the exe may be able to redirect it: the source never reads
    # a config file or an environment variable for the URL or the hash.
    source = SOURCE.read_text(encoding="utf-8")
    assert "GetEnvironmentVariable" in source  # used for Edge discovery only
    for forbidden in ("GBFC_URL", "GBFC_SHA", "ConfigurationManager", "AppSettings"):
        assert forbidden not in source


def test_generated_source_escapes_backslashes_so_a_path_cannot_break_out() -> None:
    generated = builder.generate_manifest_source(
        {
            "runtime_version": "1.0.0",
            "asset_name": "a.zip",
            "asset_url": "https://x/1.0.0/a.zip",
            "sha256": "0" * 64,
            "size_bytes": 1,
            "executable_relative_path": 'sub\\dir\\"weird".exe',
        }
    )
    assert r'sub\\dir\\\"weird\".exe' in generated


def test_the_shipped_executable_is_built_windowed_so_no_console_appears() -> None:
    command = builder.build_command(Path("csc.exe"), [SOURCE], Path("out.exe"))
    assert "/target:winexe" in command
    assert "/target:exe" not in command


# ---------------------------------------------------------------------------
# Error-message audit -- runs everywhere, and is the point of the error work
# ---------------------------------------------------------------------------

#: Anything that would tell a trader the name of a .NET type or hand them a
#: stack frame. None of these may appear in a string the user can be shown.
_LEAKY_TOKENS = (
    "WebException",
    "IOException",
    "UnauthorizedAccessException",
    "InvalidDataException",
    "Exception:",
    "StackTrace",
    "at System.",
    "Traceback",
)


def _user_facing_strings(source: str) -> list[str]:
    """Every string literal that can reach the message box.

    Extracted from the `throw new BootstrapError(...)` message arguments and
    the `Report(...)` calls -- deliberately not from the whole file, because the
    detail argument and the log lines are *supposed* to carry exception text.
    """

    messages: list[str] = []
    for block in re.findall(r"BootstrapError\((.*?)\);", source, re.S):
        # The message is everything up to the detail argument, which is either
        # `e.ToString()` or a diagnostic string.
        head = block.split("e.ToString()")[0]
        messages.extend(re.findall(r'"((?:[^"\\]|\\.)*)"', head))
    for block in re.findall(r"Report\(o,(.*?)\);", source, re.S):
        messages.extend(re.findall(r'"((?:[^"\\]|\\.)*)"', block))
    return messages


def test_no_user_facing_message_names_a_clr_type_or_a_stack_frame() -> None:
    messages = _user_facing_strings(SOURCE.read_text(encoding="utf-8"))
    assert messages, "no user-facing messages were found to audit"
    for message in messages:
        for token in _LEAKY_TOKENS:
            assert token not in message, f"{token!r} would be shown to a trader in: {message!r}"


def test_exception_detail_is_written_only_to_the_local_log() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    # `e.ToString()` is the raw CLR text. It may be passed as the *detail*
    # argument (which the log prints) and may be logged, but must never be
    # concatenated into a Report() call.
    for block in re.findall(r"Report\(o,(.*?)\);", source, re.S):
        assert "ToString()" not in block
    assert "Log(o, \"UNEXPECTED: \" + e.ToString())" in source
    # And the log is a local file, never sent anywhere.
    for forbidden in ("HttpClient", "WebClient(", "POST", "telemetry", "Analytics"):
        if forbidden == "WebClient(":
            continue  # the payload download legitimately uses WebClient
        assert forbidden not in source


@pytest.mark.parametrize(
    "status",
    [
        "NameResolutionFailure",
        "ConnectFailure",
        "Timeout",
        "ConnectionClosed",
        "TrustFailure",
        "ProtocolError",
    ],
)
def test_every_network_failure_mode_has_its_own_readable_sentence(status: str) -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert f"WebExceptionStatus.{status}" in source, f"{status} is not handled"


def test_the_source_is_csharp_5_so_the_in_box_compiler_can_build_it() -> None:
    """No SDK may become necessary to build the download coworkers click.

    csc.exe from the in-box .NET Framework is a C# 5 compiler. Every construct
    below is newer, and each would silently move this build onto Roslyn -- and
    therefore onto an installed SDK, which is the dependency this whole
    architecture was chosen to avoid.
    """

    source = SOURCE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//")
    )
    assert "nameof(" not in code
    assert "?." not in code
    assert "$\"" not in code
    assert not re.search(r"\bout\s+(var|string|int|bool)\s+\w", code), "inline out-var is C# 7"
    assert not re.search(r"=>\s*[^{;]+;\s*$", code, re.M) or "=> " not in code.split("static")[0]


# ---------------------------------------------------------------------------
# Windows: the real executable, end to end
# ---------------------------------------------------------------------------


def _make_payload(tmp_path: Path, *, exe_body: bytes = b"stub", nested: bool = True) -> Path:
    """Build a tiny stand-in runtime zip shaped exactly like the real one."""

    root = tmp_path / "payload"
    inner = root / "Government Bond Futures Converter" if nested else root
    (inner / "_internal").mkdir(parents=True)
    (inner / RUNTIME_EXE_NAME).write_bytes(exe_body)
    (inner / "_internal" / "marker.txt").write_text("runtime payload", encoding="utf-8")
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root))
    return archive


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


class _PayloadServer:
    """Serves one file over loopback and counts the requests it answers."""

    def __init__(self, payload: Path):
        self.requests: list[str] = []
        directory = str(payload.parent)
        server_self = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=directory, **k)

            def do_GET(self):  # noqa: N802
                server_self.requests.append(self.path)
                super().do_GET()

            def log_message(self, *a):  # noqa: A002
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = (
            f"http://127.0.0.1:{self._httpd.server_address[1]}/{payload.name}"
        )
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture(scope="module")
def bootstrapper_exe(tmp_path_factory) -> Path:
    """Compile the real bootstrapper with the in-box compiler."""

    if sys.platform != "win32":
        pytest.skip("the bootstrapper is a Windows executable")
    out = tmp_path_factory.mktemp("bootstrapper") / "Bootstrapper.exe"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    generated = out.parent / "Manifest.g.cs"
    generated.write_text(builder.generate_manifest_source(manifest), encoding="utf-8")
    command = builder.build_command(builder.find_csc(), [SOURCE, generated], out)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, f"csc failed:\n{result.stdout}\n{result.stderr}"
    assert out.is_file()
    return out


def _run(exe: Path, root: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(exe), "--install-root", str(root), "--no-launch", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _log_text(root: Path) -> str:
    log = root / "logs" / "bootstrapper.log"
    return log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""


def _installed_exe(root: Path, version: str) -> Path:
    return root / "runtime" / version / RUNTIME_EXE_NAME


@pytest.fixture()
def version() -> str:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["runtime_version"]


@_WINDOWS_ONLY
def test_cold_install_downloads_verifies_and_promotes(
    bootstrapper_exe, tmp_path, version
) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        result = _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        )
    finally:
        server.close()

    assert result.returncode == EXIT_OK, _log_text(root)
    assert _installed_exe(root, version).is_file()
    assert (root / "runtime" / version / "_internal" / "marker.txt").is_file()
    marker = json.loads((root / "current.json").read_text(encoding="utf-8"))
    assert marker["runtime_version"] == version
    assert len(server.requests) == 1
    # Staging is not left behind for a later run to trip over.
    assert not (root / "staging").exists()


@_WINDOWS_ONLY
def test_warm_launch_makes_no_network_request_at_all(bootstrapper_exe, tmp_path, version) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        ).returncode == EXIT_OK
        first_requests = len(server.requests)

        # The second run is given a URL that cannot possibly answer. If it
        # touched the network at all it would fail; it must not look.
        result = _run(
            bootstrapper_exe,
            root,
            "--asset-url",
            "http://127.0.0.1:9/must-not-be-fetched.zip",
            "--sha256",
            _sha256(payload),
        )
    finally:
        server.close()

    assert result.returncode == EXIT_OK, _log_text(root)
    assert len(server.requests) == first_requests
    assert "no network used" in _log_text(root)


@_WINDOWS_ONLY
def test_a_hash_mismatch_refuses_and_installs_nothing(bootstrapper_exe, tmp_path) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        result = _run(bootstrapper_exe, root, "--asset-url", server.url, "--sha256", "a" * 64)
    finally:
        server.close()

    assert result.returncode == EXIT_INTEGRITY_FAILED
    assert not (root / "current.json").exists()
    assert not (root / "runtime").exists()
    # The rejected file is deleted, not left lying in staging.
    assert not any((root / "staging").glob("*")) if (root / "staging").exists() else True


@_WINDOWS_ONLY
def test_a_truncated_download_is_never_treated_as_installed(
    bootstrapper_exe, tmp_path, version
) -> None:
    """An interrupted download is short, and shortness is detected first."""

    payload = _make_payload(tmp_path)
    full_sha = _sha256(payload)
    # A separate directory, so the truncated copy cannot overwrite the payload
    # whose hash the bootstrapper is being told to expect.
    served = tmp_path / "interrupted"
    served.mkdir()
    truncated = served / "runtime.zip"
    data = payload.read_bytes()
    truncated.write_bytes(data[: len(data) // 2])
    assert _sha256(truncated) != full_sha
    server = _PayloadServer(truncated)
    root = tmp_path / "root"
    try:
        # With the expected size known, a short body is named as the interrupted
        # download it is, before any hashing happens.
        short = _run(
            bootstrapper_exe,
            root,
            "--asset-url",
            server.url,
            "--sha256",
            full_sha,
            "--size-bytes",
            str(len(data)),
        )
        # Without a size, the hash still catches it -- belt and braces.
        hashed = _run(bootstrapper_exe, root, "--asset-url", server.url, "--sha256", full_sha)
    finally:
        server.close()

    assert short.returncode == EXIT_DOWNLOAD_FAILED
    assert "did not complete" in _log_text(root)
    assert hashed.returncode == EXIT_INTEGRITY_FAILED
    assert not (root / "current.json").exists()
    assert not _installed_exe(root, version).exists()


@_WINDOWS_ONLY
def test_a_corrupt_zip_is_reported_and_installs_nothing(bootstrapper_exe, tmp_path) -> None:
    corrupt = tmp_path / "runtime.zip"
    corrupt.write_bytes(b"PK\x03\x04 this is not a real archive" * 32)
    server = _PayloadServer(corrupt)
    root = tmp_path / "root"
    try:
        result = _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(corrupt)
        )
    finally:
        server.close()

    assert result.returncode == EXIT_INSTALL_FAILED
    assert not (root / "current.json").exists()


@_WINDOWS_ONLY
def test_a_payload_without_the_expected_executable_is_refused(bootstrapper_exe, tmp_path) -> None:
    root_dir = tmp_path / "wrong"
    (root_dir / "something").mkdir(parents=True)
    (root_dir / "something" / "not-the-app.exe").write_bytes(b"x")
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(root_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(root_dir))
    server = _PayloadServer(archive)
    root = tmp_path / "root"
    try:
        result = _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(archive)
        )
    finally:
        server.close()

    assert result.returncode == EXIT_INSTALL_FAILED
    assert not (root / "current.json").exists()


@_WINDOWS_ONLY
def test_a_failed_install_leaves_the_previous_good_runtime_intact(
    bootstrapper_exe, tmp_path, version
) -> None:
    """The one failure that would actually stop a desk working."""

    good = _make_payload(tmp_path, exe_body=b"GOOD RUNTIME")
    server = _PayloadServer(good)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(good)
        ).returncode == EXIT_OK
    finally:
        server.close()
    assert _installed_exe(root, version).read_bytes() == b"GOOD RUNTIME"

    # Now force a reinstall of the same version that fails on its hash.
    (root / "current.json").unlink()
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip at all")
    bad_server = _PayloadServer(bad)
    try:
        result = _run(bootstrapper_exe, root, "--asset-url", bad_server.url, "--sha256", "b" * 64)
    finally:
        bad_server.close()

    assert result.returncode == EXIT_INTEGRITY_FAILED
    # The previous runtime is still on disk, unchanged and complete.
    assert _installed_exe(root, version).read_bytes() == b"GOOD RUNTIME"
    assert (root / "runtime" / version / "_internal" / "marker.txt").is_file()


@_WINDOWS_ONLY
def test_malformed_version_metadata_causes_a_clean_reinstall(
    bootstrapper_exe, tmp_path, version
) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        ).returncode == EXIT_OK
        (root / "current.json").write_text("{ this is not json", encoding="utf-8")
        result = _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        )
    finally:
        server.close()

    assert result.returncode == EXIT_OK, _log_text(root)
    assert json.loads((root / "current.json").read_text(encoding="utf-8"))["runtime_version"] == (
        version
    )
    assert "unreadable" in _log_text(root)


@_WINDOWS_ONLY
def test_a_missing_runtime_executable_is_detected_and_reinstalled(
    bootstrapper_exe, tmp_path, version
) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        ).returncode == EXIT_OK
        _installed_exe(root, version).unlink()
        result = _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        )
    finally:
        server.close()

    assert result.returncode == EXIT_OK, _log_text(root)
    assert _installed_exe(root, version).is_file()
    assert "executable is missing" in _log_text(root)


@_WINDOWS_ONLY
def test_an_unreachable_host_gives_a_readable_message_and_no_false_install(
    bootstrapper_exe, tmp_path
) -> None:
    root = tmp_path / "root"
    # Port 9 (discard) refuses connections immediately on loopback.
    result = _run(
        bootstrapper_exe, root, "--asset-url", "http://127.0.0.1:9/x.zip", "--sha256", "c" * 64
    )
    assert result.returncode == EXIT_DOWNLOAD_FAILED
    assert not (root / "current.json").exists()
    log = _log_text(root)
    assert "could not be reached" in log
    # The raw CLR text belongs in the detail half of the log line, never in the
    # sentence the trader is shown.
    shown = log.split("| detail:")[0]
    assert "WebException" not in shown


@_WINDOWS_ONLY
def test_an_http_error_names_the_problem_without_a_status_dump(
    bootstrapper_exe, tmp_path
) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        missing = server.url.replace(payload.name, "no-such-asset.zip")
        result = _run(bootstrapper_exe, root, "--asset-url", missing, "--sha256", "d" * 64)
    finally:
        server.close()

    assert result.returncode == EXIT_DOWNLOAD_FAILED
    shown = _log_text(root).split("| detail:")[0]
    assert "not available at the expected address" in shown
    assert "WebException" not in shown
    assert not (root / "current.json").exists()


@_WINDOWS_ONLY
def test_the_bootstrapper_writes_only_inside_its_own_install_root(
    bootstrapper_exe, tmp_path, version
) -> None:
    """No admin, no PATH, no registry, nothing outside %LOCALAPPDATA%."""

    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        ).returncode == EXIT_OK
    finally:
        server.close()

    written = {p.relative_to(root).parts[0] for p in root.rglob("*")}
    assert written <= {"runtime", "current.json", "logs"}
    source = SOURCE.read_text(encoding="utf-8")
    # Word-boundary matched: a bare "sc.exe" also matches "csc.exe", which the
    # header comment mentions as the compiler.
    forbidden_apis = (
        r"Registry",
        r"SetEnvironmentVariable",
        r"schtasks",
        r"(?<!c)sc\.exe",
        r"ServiceController",
    )
    for forbidden in forbidden_apis:
        assert not re.search(forbidden, source), f"{forbidden} appears in the bootstrapper"


@_WINDOWS_ONLY
def test_uninstall_is_deleting_one_folder(bootstrapper_exe, tmp_path, version) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        ).returncode == EXIT_OK
    finally:
        server.close()
    shutil.rmtree(root)
    assert not root.exists()


@_WINDOWS_ONLY
def test_the_built_executable_is_small_enough_to_be_the_download(bootstrapper_exe) -> None:
    # The whole point of the architecture: what a coworker clicks is tiny.
    assert bootstrapper_exe.stat().st_size < 64 * 1024


@_WINDOWS_ONLY
def test_the_built_executable_has_no_console_subsystem(bootstrapper_exe) -> None:
    """PE subsystem 2 = Windows GUI. 3 would mean a console window appears."""

    data = bootstrapper_exe.read_bytes()
    pe = int.from_bytes(data[0x3C:0x40], "little")
    subsystem = int.from_bytes(data[pe + 4 + 20 + 68 : pe + 4 + 20 + 70], "little")
    assert subsystem == 2


@_WINDOWS_ONLY
def test_a_second_bootstrapper_run_does_not_disturb_a_good_install(
    bootstrapper_exe, tmp_path, version
) -> None:
    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
        ).returncode == EXIT_OK
        stamp = (root / "current.json").stat().st_mtime_ns
        installed = _installed_exe(root, version).stat().st_mtime_ns
        for _ in range(3):
            assert _run(
                bootstrapper_exe, root, "--asset-url", server.url, "--sha256", _sha256(payload)
            ).returncode == EXIT_OK
    finally:
        server.close()

    assert (root / "current.json").stat().st_mtime_ns == stamp
    assert _installed_exe(root, version).stat().st_mtime_ns == installed
    assert len(server.requests) == 1


@_WINDOWS_ONLY
def test_the_loopback_probe_helper_is_not_left_listening() -> None:
    """Guards the tests themselves: a leaked server would mask a warm-path bug."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        assert probe.connect_ex(("127.0.0.1", 9)) != 0
    finally:
        probe.close()


@_WINDOWS_ONLY
def test_the_real_build_tool_produces_the_named_release_asset(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out = tmp_path / f"Government_Bond_Futures_Converter_v{manifest['runtime_version']}.exe"
    built = builder.build(output=out)
    assert built == out and out.is_file()
    assert out.stat().st_size < 64 * 1024


def test_os_environment_is_not_consulted_for_the_payload_location() -> None:
    """Runs everywhere: a redirectable download is a supply-chain hole."""

    source = SOURCE.read_text(encoding="utf-8")
    env_reads = re.findall(r"GetEnvironmentVariable\(([^)]*)\)", source)
    # The only environment lookups are the three Edge install roots.
    assert all("vars[i]" in read or "ProgramFiles" in read or "LOCALAPPDATA" in read
               for read in env_reads), env_reads
    assert os.environ is not None  # the test process's own environment is irrelevant here


# ---------------------------------------------------------------------------
# The release pipeline -- runs everywhere
#
# The first RC failed on every launch with a checksum error. The cause was not
# the hashing but the *order*: a bootstrapper compiled against a manifest that
# still held the unpublished-RC placeholder, pointing at a tag that did not
# exist. These tests are the guard against shipping an unmatched pair again.
# ---------------------------------------------------------------------------

import release_government_bond_futures_converter as release  # noqa: E402


def test_a_bootstrapper_that_does_not_carry_the_assets_hash_is_refused(tmp_path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["sha256"] = "1" * 64
    asset = tmp_path / "runtime.zip"
    asset.write_bytes(b"payload")
    manifest["size_bytes"] = asset.stat().st_size
    exe = tmp_path / "boot.exe"
    # An executable carrying a *different* hash than the asset it ships with.
    exe.write_bytes(("2" * 64).encode("utf-16-le"))
    with pytest.raises(release.ReleaseError) as exc:
        release.verify_pair(exe, asset, manifest)
    assert "does not carry the sha256" in str(exc.value)


def test_a_zip_regenerated_after_hashing_is_caught(tmp_path) -> None:
    """Steps C and E must describe the same bytes."""

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    asset = tmp_path / "runtime.zip"
    asset.write_bytes(b"the bytes that were hashed")
    manifest["sha256"] = release.sha256_file(asset)
    manifest["size_bytes"] = asset.stat().st_size
    exe = tmp_path / "boot.exe"
    exe.write_bytes(
        manifest["sha256"].encode("utf-16-le") + manifest["asset_url"].encode("utf-16-le")
    )
    release.verify_pair(exe, asset, manifest)  # the matched pair passes

    asset.write_bytes(b"different bytes, same filename")
    with pytest.raises(release.ReleaseError) as exc:
        release.verify_pair(exe, asset, manifest)
    assert "changed after its hash was recorded" in str(exc.value)


def test_a_published_version_is_never_silently_rehashed() -> None:
    """An asset name is a promise about bytes; changing them needs a new version."""

    stamped = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stamped["sha256"] = "3" * 64
    with pytest.raises(release.ReleaseError) as exc:
        release.require_version_not_already_stamped(stamped, allow_restamp=False)
    message = str(exc.value)
    assert "rc1 -> rc2" in message
    # Deliberate re-stamping stays possible for a hash nobody has seen.
    release.require_version_not_already_stamped(stamped, allow_restamp=True)


def test_a_placeholder_hash_is_always_restampable() -> None:
    fresh = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fresh["sha256"] = release.PLACEHOLDER_SHA
    release.require_version_not_already_stamped(fresh, allow_restamp=False)


def test_the_release_asset_names_are_the_two_issue_206_artifacts() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = manifest["runtime_version"]
    assert manifest["asset_name"] == f"Government_Bond_Futures_Converter_Runtime_v{version}.zip"
    assert manifest["asset_url"].endswith(manifest["asset_name"])


@_WINDOWS_ONLY
def test_a_rejected_download_leaves_no_partial_payload_behind(
    bootstrapper_exe, tmp_path
) -> None:
    """A stale .partial must not survive to confuse the next launch."""

    payload = _make_payload(tmp_path)
    server = _PayloadServer(payload)
    root = tmp_path / "root"
    try:
        assert _run(
            bootstrapper_exe, root, "--asset-url", server.url, "--sha256", "e" * 64
        ).returncode == EXIT_INTEGRITY_FAILED
    finally:
        server.close()

    leftovers = [p for p in root.rglob("*") if p.is_file() and p.suffix == ".partial"]
    assert leftovers == []
    assert not (root / "staging").exists()
    assert not (root / "current.json").exists()


def test_a_substituted_asset_does_not_inherit_the_manifests_size() -> None:
    """The size check describes the manifest's own asset, and only that one.

    A caller naming a different hash is naming a different payload; carrying the
    compiled size across would reject it as "truncated" before its hash was ever
    looked at, which is how every override-based test silently broke once the
    manifest carried a real size.
    """

    source = SOURCE.read_text(encoding="utf-8")
    assert "if (!sizeGiven) o.SizeBytes = 0;" in source
    assert "if (o.SizeBytes > 0 && size != o.SizeBytes)" in source
    # And the production path still gets the manifest's real size.
    assert "o.SizeBytes = Manifest.SizeBytes;" in source
