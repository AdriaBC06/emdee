# SPDX-License-Identifier: GPL-3.0-or-later
# PyInstaller specification for the Windows build.
#
#   pyinstaller packaging/windows/emdee.spec --noconfirm
#
# Produces dist/Emdee/, a self-contained folder that runs from anywhere.
#
# One directory, not one file, and deliberately so.  A --onefile build unpacks
# the whole of Qt into a temporary directory on every launch, which costs
# several seconds and, more importantly, breaks QtWebEngineProcess.exe: the
# render subprocess resolves its resources relative to its own location, and
# that location changes every run.  A folder is also what the installer wants to
# copy and what the portable archive should contain, so there is nothing to gain
# from paying for the extraction twice.
#
# The WebEngine payload is collected by PyQt6's own PyInstaller hooks, which
# place it under _internal/PyQt6/Qt6 where Qt goes looking for it.  Copying it a
# second time by hand — the obvious defensive move — only inflated the bundle by
# about 150 MB with files nothing reads.  What is worth doing by hand is
# checking afterwards that it really arrived: when this payload is incomplete
# the application still starts and the window still opens, and only the preview
# pane is silently blank.  See the verification at the end of this file.

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

REPO_ROOT = Path(SPECPATH).resolve().parent.parent
QT_ROOT = Path(HOMEPATH) / "PyQt6" / "Qt6"
if not QT_ROOT.is_dir():  # running from a venv layout rather than a wheel dir
    import PyQt6

    QT_ROOT = Path(PyQt6.__file__).parent / "Qt6"

APP_NAME = "Emdee"
ICON = REPO_ROOT / "packaging" / "windows" / "emdee.ico"


def _require(path: Path) -> Path:
    """Fail the build now rather than ship a bundle with a blank preview."""
    if not path.exists():
        raise SystemExit(
            f"emdee.spec: required Qt WebEngine component is missing: {path}\n"
            "The build would produce an application whose preview pane never "
            "renders. Check that PyQt6-WebEngine is installed in this "
            "environment."
        )
    return path


# --------------------------------------------------------------- data files
datas = [
    # resource_path() resolves assets under <bundle>/app/resources.
    (str(REPO_ROOT / "app" / "resources"), "app/resources"),
    # _welcome_document() looks for this beside the executable.
    (str(REPO_ROOT / "WELCOME.md"), "."),
    (str(REPO_ROOT / "LICENSE"), "."),
    (str(REPO_ROOT / "NOTICE"), "."),
]

# ------------------------------------------------------- Qt WebEngine payload
#
# Checked in the source environment before the build starts, so a broken or
# partial PyQt6-WebEngine install is reported now rather than becoming a blank
# preview pane in the finished application.
_require(QT_ROOT / "bin" / "QtWebEngineProcess.exe")
_require(QT_ROOT / "resources" / "icudtl.dat")
_require(QT_ROOT / "resources" / "qtwebengine_resources.pak")
_require(QT_ROOT / "translations" / "qtwebengine_locales")

binaries = collect_dynamic_libs("PyQt6")

# ANGLE and the software rasteriser. Chromium falls back to these on machines
# with no usable GPU driver — virtual machines, remote desktop sessions, older
# laptops — where their absence means a blank preview on exactly the hardware
# least able to diagnose it. The hooks do not always pick them up.
for name in ("libEGL.dll", "libGLESv2.dll", "d3dcompiler_47.dll", "opengl32sw.dll"):
    candidate = QT_ROOT / "bin" / name
    if candidate.exists():
        binaries.append((str(candidate), "."))


a = Analysis(
    [str(REPO_ROOT / "packaging" / "windows" / "entry_point.py")],
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # Imported for its side effect on Chromium's initialisation order, which
        # the dependency graph cannot see.
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebChannel",
        "PyQt6.QtSvg",
        "PyQt6.QtPrintSupport",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Qt modules Emdee never touches.
        "PyQt6.QtQuick3D",
        "PyQt6.QtMultimedia",
        "PyQt6.QtBluetooth",
        "PyQt6.QtNfc",
        "PyQt6.QtDesigner",
        "PyQt6.QtTest",
        "PyQt6.QtSql",
        # Pillow and Cairo arrive only as dependencies of cairosvg, which is a
        # build-time helper for tools/build_icons.py. Nothing in the running
        # application imports them.
        "PIL",
        "cairosvg",
        "cairocffi",
        "tkinter",
        "unittest",
        "pydoc",
    ],
    noarchive=False,
)

# Chromium ships a debug twin of every resource pack next to the real one. They
# are read only by a debug build of Qt and cost around 50 MB.
#
# Note what is *not* trimmed here. Qt6WebEngineCore.dll alone is 191 MB — it is
# a whole browser engine and there is no version of it that is not — and the
# qml/ tree stays because QtWebEngineWidgets is implemented on top of Qt Quick
# internally, so removing it breaks the preview in a way that only shows up at
# runtime. A smaller download is not worth reintroducing the exact failure this
# build is meant to rule out.
a.datas = [entry for entry in a.datas if not entry[0].endswith(".debug.pak")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX corrupts Qt's DLLs often enough not to be worth the size
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.exists() else None,
    version=str(REPO_ROOT / "packaging" / "windows" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

# ------------------------------------------------- post-build verification
#
# The spec is ordinary Python executed top to bottom, and COLLECT has written
# the folder by the time we get here, so this is a real check on the artefact
# rather than on our intentions. It exists because every failure mode it covers
# produces an application that launches, opens its window, and simply never
# renders a preview — the one bug a smoke test that only checks "does it start"
# will happily wave through.
_bundle = Path(DISTPATH) / APP_NAME / "_internal"
_missing = [
    str(rel)
    for rel in (
        Path("PyQt6/Qt6/bin/QtWebEngineProcess.exe"),
        Path("PyQt6/Qt6/resources/icudtl.dat"),
        Path("PyQt6/Qt6/resources/qtwebengine_resources.pak"),
        Path("PyQt6/Qt6/resources/qtwebengine_resources_100p.pak"),
        Path("PyQt6/Qt6/translations/qtwebengine_locales/en-US.pak"),
        Path("app/resources/icons/app/logo.svg"),
        Path("WELCOME.md"),
    )
    if not (_bundle / rel).exists()
]
if _missing:
    raise SystemExit(
        "emdee.spec: the bundle is missing files the application needs at "
        "runtime:\n  " + "\n  ".join(_missing)
    )
print(f"emdee.spec: WebEngine payload and application resources verified in {_bundle}")
