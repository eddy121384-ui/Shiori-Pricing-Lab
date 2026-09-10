"""Produce one matched Government Bond Futures Converter release pair (Issue #206).

    .venv\\Scripts\\python.exe tools\\release_government_bond_futures_converter.py

The two artifacts a release carries, built together, in this order, once:

    A. build the runtime directory                    (PyInstaller)
    B. create ONE runtime ZIP, immutably named        (never rebuilt after this)
    C. hash that exact ZIP                            (SHA-256)
    D. stamp the manifest from that hash              (never typed by hand)
    E. compile the bootstrapper from that manifest    (hash + URL baked in)
    F. verify the pair actually agrees                (or fail the release)

**Why this script exists.** The first RC failed on every launch with a checksum
error, and the cause was an ordering defect, not a hashing one: a bootstrapper
had been compiled against a manifest that still held the unpublished-RC
placeholder hash, pointing at a release tag that did not exist. The two halves
of the release were never a pair. Building them in one command, and refusing to
finish when they disagree, is what stops that happening again.

**Immutability.** ``Government_Bond_Futures_Converter_Runtime_v<version>.zip``
is a name that must mean exactly one byte sequence forever. If the payload has
to change, the version bumps (rc1 -> rc2); the ZIP behind a version whose hash
has been published is never rewritten. :func:`require_version_not_already_stamped`
enforces that against the checked-in manifest.

Step F re-reads the compiled executable and checks the hash and URL really are
inside it, and re-hashes the ZIP on disk to prove it was not regenerated
between C and E. Nothing is published from here; ``--verify-locally`` runs the
real bootstrapper against the real asset over loopback so a clean install can
be proven before any release exists.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_government_bond_futures_converter as runtime_builder  # noqa: E402
import build_government_bond_futures_converter_bootstrapper as bootstrapper_builder  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST = PROJECT_ROOT / "dist"
MANIFEST_PATH = PROJECT_ROOT / "packaging" / "runtime_manifest.json"

PLACEHOLDER_SHA = "0" * 64


class ReleaseError(RuntimeError):
    """The release is not internally consistent and must not be published."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_version_not_already_stamped(manifest: dict, *, allow_restamp: bool) -> None:
    """Refuse to re-hash a version whose hash has already been recorded.

    A published asset name is a promise about bytes. Rebuilding
    ``...Runtime_v1.0.0-rc1.zip`` with a different payload would break every
    bootstrapper already carrying the old hash, and PyInstaller output is not
    byte-reproducible, so a rebuild *always* produces a different hash.
    """

    if manifest["sha256"] == PLACEHOLDER_SHA or allow_restamp:
        return
    raise ReleaseError(
        f"{manifest['asset_name']} already has a recorded hash "
        f"({manifest['sha256'][:16]}...).\n"
        "Re-zipping it would change those bytes and break every bootstrapper that "
        "carries the old hash.\n"
        f"Bump runtime_version in {MANIFEST_PATH} (rc1 -> rc2) and run again, or pass "
        "--allow-restamp if this hash was never handed to anyone."
    )


def package_runtime_zip(manifest: dict) -> Path:
    """Step B+C: create the one immutable ZIP and hash exactly it."""

    version = manifest["runtime_version"]
    zip_path = DIST / f"Government_Bond_Futures_Converter_Runtime_v{version}.zip"
    source = runtime_builder.BUILT_APP.parent
    if not source.is_dir():
        raise ReleaseError(f"the runtime directory {source} does not exist; build it first")

    if zip_path.exists():
        zip_path.unlink()
    print(f"[B] zipping {source.name} -> {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source.parent))
    print(f"    {zip_path.stat().st_size:,} bytes")
    return zip_path


def stamp_manifest(manifest: dict, zip_path: Path) -> dict:
    """Step D: record the produced asset's own hash and size."""

    manifest["asset_name"] = zip_path.name
    manifest["sha256"] = sha256_file(zip_path)
    manifest["size_bytes"] = zip_path.stat().st_size
    bootstrapper_builder.require_valid_manifest(manifest)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[C] sha256 {manifest['sha256']}")
    print(f"[D] manifest stamped: {MANIFEST_PATH}")
    return manifest


def verify_pair(exe: Path, zip_path: Path, manifest: dict) -> None:
    """Step F: the executable and the asset must actually agree.

    Reads the strings back out of the compiled binary rather than trusting the
    build inputs, and re-hashes the ZIP on disk to prove it was not regenerated
    after its hash was taken. Either check failing means the release is the
    unmatched pair that broke the first RC.
    """

    blob = exe.read_bytes()
    for label, value in (("sha256", manifest["sha256"]), ("asset url", manifest["asset_url"])):
        # C# string literals live in the metadata as UTF-16.
        if value.encode("utf-16-le") not in blob:
            raise ReleaseError(
                f"the built bootstrapper does not carry the {label} it was supposed to.\n"
                f"  expected: {value}\n"
                "Rebuild; do not publish this pair."
            )
    actual = sha256_file(zip_path)
    if actual != manifest["sha256"]:
        raise ReleaseError(
            "the runtime ZIP changed after its hash was recorded -- the pair is broken.\n"
            f"  manifest: {manifest['sha256']}\n  on disk : {actual}"
        )
    if zip_path.stat().st_size != manifest["size_bytes"]:
        raise ReleaseError("the runtime ZIP size no longer matches the manifest")
    print("[F] verified: the bootstrapper carries this exact asset's hash and URL")


class _ImmutableAssetServer:
    """Serves the produced ZIP over loopback, under its exact release name."""

    def __init__(self, zip_path: Path):
        directory = str(zip_path.parent)

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=directory, **k)

            def log_message(self, *a):  # noqa: A002
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/{zip_path.name}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def verify_locally(exe: Path, zip_path: Path, install_root: Path) -> None:
    """Run the real bootstrapper against the real asset, before any release exists.

    Only the *location* is overridden. The SHA-256 gating the install is the one
    compiled into the executable, and the bytes served are the exact release
    ZIP -- so this proves the matched pair, not a rehearsal of it.
    """

    if install_root.exists():
        shutil.rmtree(install_root)
    server = _ImmutableAssetServer(zip_path)
    try:
        print(f"[V] clean install from {server.url}")
        result = subprocess.run(
            [
                str(exe), "--install-root", str(install_root),
                "--asset-url", server.url, "--no-launch",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
    finally:
        server.close()

    log = install_root / "logs" / "bootstrapper.log"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "(no log)"
    if result.returncode != 0:
        raise ReleaseError(f"the clean install failed (exit {result.returncode}):\n{log_text}")
    marker = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    print(f"    installed {marker['runtime_version']} into {install_root}")

    before = len(log_text)
    warm = subprocess.run(
        [str(exe), "--install-root", str(install_root), "--asset-url",
         "http://127.0.0.1:9/must-not-be-fetched.zip", "--no-launch"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if warm.returncode != 0:
        raise ReleaseError("the warm launch failed; it should not have touched the network at all")
    warm_log = log.read_text(encoding="utf-8", errors="replace")[before:]
    if "no network used" not in warm_log:
        raise ReleaseError(f"the warm launch did not take the offline path:\n{warm_log}")
    print("[V] warm launch reused the installed runtime with no download")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-runtime-build",
        action="store_true",
        help="Reuse the existing dist/ runtime instead of rebuilding it.",
    )
    parser.add_argument(
        "--bloomberg",
        action="store_true",
        help="Include the live Bloomberg smoke check (Bloomberg workstation only).",
    )
    parser.add_argument(
        "--allow-restamp",
        action="store_true",
        help="Re-hash a version that already has a recorded hash. See the docstring.",
    )
    parser.add_argument(
        "--verify-locally",
        action="store_true",
        help="Prove a clean install of this exact pair over loopback.",
    )
    parser.add_argument(
        "--install-root",
        type=Path,
        default=Path.home() / "AppData" / "Local" / "GovernmentBondFuturesConverter",
        help="Where --verify-locally installs to.",
    )
    args = parser.parse_args()

    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        require_version_not_already_stamped(manifest, allow_restamp=args.allow_restamp)

        if not args.skip_runtime_build:
            print("[A] building the runtime")
            runtime_builder.build()
        elif not runtime_builder.BUILT_APP.is_file():
            raise ReleaseError(
                f"--skip-runtime-build was passed but {runtime_builder.BUILT_APP} is missing"
            )
        runtime_builder.smoke_test(args.bloomberg)

        zip_path = package_runtime_zip(manifest)
        manifest = stamp_manifest(manifest, zip_path)
        print("[E] compiling the bootstrapper against that manifest")
        exe = bootstrapper_builder.build()
        verify_pair(exe, zip_path, manifest)

        if args.verify_locally:
            verify_locally(exe, zip_path, args.install_root)

        print("\nRelease pair ready (NOT published):")
        print(f"  {exe.name}  ({exe.stat().st_size:,} bytes)")
        print(f"  {zip_path.name}  ({zip_path.stat().st_size:,} bytes)")
        print(f"  sha256 {manifest['sha256']}")
        print(f"  upload both to the release tagged for {manifest['runtime_version']}")
    except (ReleaseError, bootstrapper_builder.BootstrapperBuildError) as exc:
        print(f"\nRELEASE REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
