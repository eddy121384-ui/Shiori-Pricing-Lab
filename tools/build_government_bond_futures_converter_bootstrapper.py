"""Build the coworker-facing Government Bond Futures Converter bootstrapper.

    .venv\\Scripts\\python.exe tools\\build_government_bond_futures_converter_bootstrapper.py

Produces ``dist/Government_Bond_Futures_Converter_v<version>.exe`` -- the one
small file a coworker downloads. Measured at roughly 10 KB.

**No SDK is installed or required.** The compiler is ``csc.exe`` from the
in-box .NET Framework, which ships with Windows; the produced executable needs
only .NET Framework 4.x, which also ships with Windows. That is the whole
reason this architecture was chosen over Inno Setup, NSIS or a native
toolchain, none of which is present on a standard workstation.

The bootstrapper's manifest is compiled *into* it: :func:`generate_manifest_source`
turns ``packaging/runtime_manifest.json`` into a C# class of constants, so one
executable is bound to exactly one runtime version, URL and SHA-256. There is
no configuration file next to the exe that could be edited to point it
somewhere else.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "packaging" / "runtime_manifest.json"
SOURCE_PATH = PROJECT_ROOT / "bootstrapper" / "Bootstrapper.cs"
DIST_DIR = PROJECT_ROOT / "dist"

#: The in-box compiler. 64-bit first: it is present on every supported
#: workstation, and the 32-bit path is the fallback for a 32-bit Windows that
#: this app does not otherwise support but that must still produce a readable
#: refusal rather than a build error.
CSC_CANDIDATES = (
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
)

#: Every field the bootstrapper compiles in. Listed here so a manifest missing
#: one fails the build rather than producing an executable with an empty URL.
REQUIRED_MANIFEST_FIELDS = (
    "schema",
    "runtime_version",
    "asset_name",
    "asset_url",
    "sha256",
    "size_bytes",
    "executable_relative_path",
)

MANIFEST_SCHEMA = "government-bond-futures-converter/runtime-manifest/v1"


class BootstrapperBuildError(RuntimeError):
    """A build input is wrong -- reported before anything is compiled."""


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    """Read and validate the runtime manifest.

    Validated here rather than trusted, because everything this file says ends
    up baked into an executable that coworkers run: a mistyped SHA-256 or a
    ``latest`` URL would not fail until it reached a desk.
    """

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BootstrapperBuildError(f"{path} could not be read as JSON: {exc}") from exc
    require_valid_manifest(manifest)
    return manifest


def require_valid_manifest(manifest: dict) -> None:
    """Raise unless ``manifest`` is a usable immutable runtime contract."""

    if not isinstance(manifest, dict):
        raise BootstrapperBuildError("the runtime manifest must be a JSON object")
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        raise BootstrapperBuildError(f"the runtime manifest is missing: {', '.join(missing)}")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise BootstrapperBuildError(
            f"unknown manifest schema {manifest['schema']!r}; expected {MANIFEST_SCHEMA!r}"
        )
    sha = str(manifest["sha256"])
    if len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
        raise BootstrapperBuildError(f"sha256 must be 64 hex characters, got {sha!r}")
    url = str(manifest["asset_url"])
    if not url.startswith("https://"):
        raise BootstrapperBuildError(f"asset_url must be https, got {url!r}")
    # "latest" is the one URL shape this design forbids: it would let the
    # payload behind a shipped executable change without the executable, and
    # the SHA-256 compiled beside it would then reject every download.
    if "/latest/" in url or url.rstrip("/").endswith("/latest"):
        raise BootstrapperBuildError("asset_url must name an immutable version, never 'latest'")
    version = str(manifest["runtime_version"])
    if version not in url:
        raise BootstrapperBuildError(
            f"asset_url does not contain the runtime version {version!r}: {url!r}"
        )
    if not isinstance(manifest["size_bytes"], int) or manifest["size_bytes"] < 0:
        raise BootstrapperBuildError("size_bytes must be a non-negative integer")


def _cs_string(value: object) -> str:
    """Render a Python value as a C# string literal."""

    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate_manifest_source(manifest: dict) -> str:
    """Return the C# constants class the bootstrapper compiles against."""

    return (
        "// GENERATED from packaging/runtime_manifest.json -- do not edit.\n"
        "// Compiled into the bootstrapper so one executable is bound to exactly\n"
        "// one runtime version, URL and SHA-256, with nothing beside the exe that\n"
        "// could be edited to redirect it.\n"
        "static class Manifest\n"
        "{\n"
        f"    public const string RuntimeVersion = {_cs_string(manifest['runtime_version'])};\n"
        f"    public const string AssetName = {_cs_string(manifest['asset_name'])};\n"
        f"    public const string AssetUrl = {_cs_string(manifest['asset_url'])};\n"
        f"    public const string Sha256 = {_cs_string(manifest['sha256'])};\n"
        f"    public const long SizeBytes = {int(manifest['size_bytes'])};\n"
        "    public const string ExecutableRelativePath = "
        f"{_cs_string(manifest['executable_relative_path'])};\n"
        "}\n"
    )


def find_csc(candidates=CSC_CANDIDATES) -> Path:
    """Return the in-box C# compiler, or explain that this is a Windows build."""

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BootstrapperBuildError(
        "The in-box .NET Framework C# compiler (csc.exe) was not found. The "
        "bootstrapper is a Windows artifact and is built on Windows; no SDK "
        "install is needed there, because csc.exe ships with the OS."
    )


def build_command(csc: Path, sources: list[Path], output: Path) -> list[str]:
    """The exact compiler invocation.

    ``/target:winexe`` is what makes the shipped executable a Windows-subsystem
    program: a coworker double-clicking it never sees a console window, which
    is a hard requirement of Issue #206. Diagnostics go to the support log
    instead.
    """

    return [
        str(csc),
        "/nologo",
        "/target:winexe",
        "/platform:anycpu",
        "/optimize+",
        f"/out:{output}",
        "/reference:System.dll",
        "/reference:System.IO.Compression.dll",
        "/reference:System.IO.Compression.FileSystem.dll",
        *[str(source) for source in sources],
    ]


def build(output: Path | None = None, manifest_path: Path = MANIFEST_PATH) -> Path:
    """Generate the manifest source, compile, and return the built executable."""

    manifest = load_manifest(manifest_path)
    csc = find_csc()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    generated = DIST_DIR / "Manifest.g.cs"
    generated.write_text(generate_manifest_source(manifest), encoding="utf-8")

    if output is None:
        output = DIST_DIR / f"Government_Bond_Futures_Converter_v{manifest['runtime_version']}.exe"
    command = build_command(csc, [SOURCE_PATH, generated], output)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not output.is_file():
        raise BootstrapperBuildError(
            "The bootstrapper did not compile.\n"
            f"{result.stdout}\n{result.stderr}".strip()
        )
    print(f"Built {output} ({output.stat().st_size:,} bytes)")
    print(f"  runtime version : {manifest['runtime_version']}")
    print(f"  runtime asset   : {manifest['asset_name']}")
    print(f"  asset url       : {manifest['asset_url']}")
    print(f"  sha256          : {manifest['sha256']}")
    if manifest["sha256"] == "0" * 64:
        print(
            "\n  NOTE: the manifest still carries the unpublished-RC placeholder hash, so "
            "this\n  build refuses every download by design. Run "
            "build_government_bond_futures_converter.py\n  --package-runtime to produce a payload "
            "and stamp its real hash into the manifest."
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=None, help="Where to write the executable.")
    args = parser.parse_args()
    try:
        build(args.output)
    except BootstrapperBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
