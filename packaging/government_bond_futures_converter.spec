# PyInstaller spec for the Government Bond Futures Converter desk app
# (Issue #206). Build with:
#
#     .venv\Scripts\python.exe -m PyInstaller packaging\government_bond_futures_converter.spec
#
# or through tools/build_government_bond_futures_converter.py, which resolves
# the paths and checks the result.
#
# **onedir, not onefile.** A onefile build unpacks its whole payload to a
# temporary directory on every single launch: slow to start, and exactly the
# behaviour endpoint protection on a locked-down desk flags. A onedir folder
# can also just be copied to the workstation -- no installer, no administrator
# rights, no PATH entry, no registry write.
#
# **collect_all("blpapi") is load-bearing.** The Bloomberg wheel ships its own
# native library, blpapi3_64.dll, *inside* the package directory and loads it
# by ctypes at import. Without collecting the package's binaries the frozen
# app imports blpapi and then fails to find its DLL. This is the single thing
# the Issue #206 packaging gate was about, and it is verified end to end by
# tools/government_bond_futures_converter_packaged_probe.py against a live
# Terminal.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PROJECT_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is injected
SRC = PROJECT_ROOT / "src"
ASSETS = PROJECT_ROOT / "prototype" / "government-bond-futures-converter"

APP_NAME = "Government Bond Futures Converter"

blpapi_datas, blpapi_binaries, blpapi_hiddenimports = collect_all("blpapi")

analysis = Analysis(  # noqa: F821 - PyInstaller injects its own builtins
    [str(SRC / "shiori_pricing_lab" / "app" / "government_bond_futures_converter_app.py")],
    pathex=[str(SRC)],
    binaries=blpapi_binaries,
    # Served by government_bond_futures_converter_server.asset_dir(), which
    # resolves this same folder name under sys._MEIPASS when frozen.
    datas=blpapi_datas + [(str(ASSETS), "government-bond-futures-converter")],
    hiddenimports=blpapi_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # None of these are on the converter's import path; excluding them keeps
    # the shipped folder to what the desk actually runs. pandas and numpy are
    # NOT excluded -- they are pulled in transitively by the production
    # pricing package this app deliberately reuses unmodified.
    excludes=[
        "streamlit",
        "plotly",
        "matplotlib",
        "IPython",
        "tkinter",
        "pytest",
        "playwright",
        "rapidocr_onnxruntime",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # The whole point of the app shell: no console window ever appears, so a
    # failure must reach the trader as a dialog box instead. See
    # government_bond_futures_converter_app.show_error_dialog().
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collect = COLLECT(  # noqa: F821
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
