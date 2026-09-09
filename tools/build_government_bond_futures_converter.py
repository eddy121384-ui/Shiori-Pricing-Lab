"""Build the packaged Government Bond Futures Converter, then smoke-test it.

    .venv\\Scripts\\python.exe tools\\build_government_bond_futures_converter.py

Produces ``dist/Government Bond Futures Converter/`` -- a portable folder that
is copied to the workstation as-is. No installer, no administrator rights, no
registry write, no PATH entry.

The smoke test after the build is the point of running this rather than
PyInstaller directly. It starts the *packaged* executable with ``--no-window``
and checks the three things a build can silently get wrong:

1. the frozen runtime starts at all, without a Python installed on the machine;
2. its backend answers on loopback, and serves the app's own static page;
3. ``blpapi`` imports **from inside the bundle** -- the failure mode Issue
   #206's packaging gate exists to catch, since the Bloomberg wheel keeps its
   native ``blpapi3_64.dll`` inside the package directory.

It deliberately does **not** require a live Bloomberg session, so the build is
reproducible off the desk. Proving live DAPI from the packaged build is a
separate, manual step: run the built executable on a Bloomberg workstation and
convert a real contract. Pass ``--bloomberg`` to have this script do that check
too, which is what the Issue #206 UAT evidence is produced with.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = PROJECT_ROOT / "packaging" / "government_bond_futures_converter.spec"
APP_NAME = "Government Bond Futures Converter"
BUILT_APP = PROJECT_ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"

#: One representative contract from each market. FGBS is first because the
#: German contracts are the ones with an irregular first coupon period, so a
#: packaged build that got the schedule fields wrong shows it here.
SMOKE_CONTRACTS = ("FGBS", "ZN")


def build() -> None:
    """Run PyInstaller against the checked-in spec."""

    print(f"Building {APP_NAME}...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=str(PROJECT_ROOT),
        check=True,
    )
    if not BUILT_APP.is_file():
        raise SystemExit(f"BUILD FAILED: PyInstaller reported success but {BUILT_APP} is missing")
    print(f"Built {BUILT_APP}")


def _get(url: str, timeout: float = 30.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, body: dict, timeout: float = 60.0) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


#: Matches ``government_bond_futures_converter_app.EXIT_ALREADY_RUNNING``. Not
#: imported, deliberately: this script drives the *packaged* executable as a
#: black box, and reading the constant from the source tree would let a build
#: that shipped a different value still pass.
EXIT_ALREADY_RUNNING = 3


def _check_single_instance_guard() -> None:
    """A second launch must refuse while the first is still running.

    Called with the smoke-test instance already up, so this really is a second
    launch against a live guard -- the named mutex is held by the process the
    caller started. Windows only: the guard is a kernel object, and off Windows
    :func:`acquire_single_instance` is a documented no-op.
    """

    if sys.platform != "win32":
        print("  single-instance guard: skipped (Windows-only kernel object)")
        return
    second = subprocess.run(
        [str(BUILT_APP), "--no-window"],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(BUILT_APP.parent),
    )
    message = (second.stderr or "") + (second.stdout or "")
    if second.returncode != EXIT_ALREADY_RUNNING:
        raise SystemExit(
            "SMOKE FAILED: a second launch was not refused.\n"
            f"  exit code {second.returncode} (expected {EXIT_ALREADY_RUNNING})\n"
            f"  output: {message.strip()!r}"
        )
    if "already running" not in message:
        raise SystemExit(
            f"SMOKE FAILED: the refusal did not say the app is already running: {message.strip()!r}"
        )
    if "Traceback" in message:
        raise SystemExit(f"SMOKE FAILED: the refusal showed a traceback: {message.strip()!r}")
    print(f"  single-instance guard: second launch refused ({message.strip().splitlines()[0]})")


def smoke_test(check_bloomberg: bool) -> None:
    """Start the packaged app headless and exercise its real routes."""

    print("Smoke-testing the packaged build...")
    process = subprocess.Popen(
        [str(BUILT_APP), "--no-window"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(BUILT_APP.parent),
    )
    try:
        url = (process.stdout.readline() or "").strip()
        if not url.startswith("http://127.0.0.1:"):
            remainder = process.stdout.read()
            raise SystemExit(
                "SMOKE FAILED: the packaged app did not report a loopback URL.\n"
                f"It printed: {url!r}\n{remainder}"
            )
        print(f"  backend up on {url} (loopback only)")

        health = _get(url + "api/health")
        print(f"  health: {health}")

        catalogue = _get(url + "api/treasury-futures/contracts")
        codes = [contract["code"] for contract in catalogue["contracts"]]
        print(f"  contracts: {', '.join(codes)}")
        if len(codes) != 10:
            raise SystemExit(f"SMOKE FAILED: expected 10 registry contracts, got {codes}")

        with urllib.request.urlopen(url, timeout=30) as response:
            page = response.read().decode("utf-8")
        if "<title>Government Bond Futures Converter</title>" not in page:
            raise SystemExit("SMOKE FAILED: the packaged app did not serve its own page")
        if "Shiori" in page:
            raise SystemExit("SMOKE FAILED: the packaged page shows internal branding")
        print("  page served, no user-facing internal branding")

        # blpapi is proven from inside the bundle by asking for a CTD. Without
        # a Terminal this fails on the *connection*, which still proves the
        # import and the native DLL resolved; an import failure reports a
        # different, unmistakable message.
        status, payload = _post(
            url + "api/treasury-futures/ctd", {"contract_code": SMOKE_CONTRACTS[0]}
        )
        detail = payload.get("error", "")
        if "No module named" in detail or "DLL load failed" in detail:
            raise SystemExit(
                f"SMOKE FAILED: blpapi did not load from the packaged runtime.\n  {detail}"
            )
        print(f"  blpapi loaded from the bundle (CTD route answered HTTP {status})")

        _check_single_instance_guard()

        if not check_bloomberg:
            print("\nPASS (packaging). Live Bloomberg not checked -- pass --bloomberg on a")
            print("Bloomberg workstation to produce the Issue #206 UAT evidence.")
            return

        for code in SMOKE_CONTRACTS:
            status, payload = _post(url + "api/treasury-futures/ctd", {"contract_code": code})
            if status != 200 or not payload.get("is_confirmed_source"):
                raise SystemExit(f"BLOOMBERG FAILED for {code}: HTTP {status} {payload}")
            print(f"  {code} live CTD: {json.dumps(payload)}")
        print("\nPASS (packaging + live Bloomberg).")
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Build and smoke-test {APP_NAME}.")
    parser.add_argument(
        "--bloomberg",
        action="store_true",
        help="Also resolve live CTDs through the packaged build (Bloomberg workstation only).",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Smoke-test the existing dist/ build instead of rebuilding it.",
    )
    args = parser.parse_args()

    if not args.skip_build:
        build()
    elif not BUILT_APP.is_file():
        raise SystemExit(f"--skip-build was passed but {BUILT_APP} does not exist")
    smoke_test(args.bloomberg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
