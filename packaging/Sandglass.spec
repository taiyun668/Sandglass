from pathlib import Path
import tomllib

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


ROOT = Path(SPECPATH).resolve()
if not (ROOT / "pyproject.toml").is_file():
    ROOT = ROOT.parent
metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
VERSION = metadata["project"]["version"]
version_numbers = tuple(int(part) for part in VERSION.split("."))
version_quad = (*version_numbers, *(0 for _ in range(4 - len(version_numbers))))[:4]
version_text = ".".join(str(part) for part in version_quad)
version_file = ROOT / "build" / "Sandglass-version.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(
    str(
        VSVersionInfo(
            ffi=FixedFileInfo(filevers=version_quad, prodvers=version_quad),
            kids=[
                StringFileInfo([
                    StringTable("040904B0", [
                        StringStruct("CompanyName", "Ayun"),
                        StringStruct("FileDescription", "Sandglass local AI usage meter"),
                        StringStruct("FileVersion", version_text),
                        StringStruct("InternalName", "Sandglass"),
                        StringStruct("LegalCopyright", "Copyright (c) 2026 Ayun"),
                        StringStruct("OriginalFilename", "Sandglass.exe"),
                        StringStruct("ProductName", "Sandglass"),
                        StringStruct("ProductVersion", version_text),
                    ])
                ]),
                VarFileInfo([VarStruct("Translation", [1033, 1200])]),
            ],
        )
    ),
    encoding="utf-8",
)

a = Analysis(
    [str(ROOT / "sandglass-desktop.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "sandglass" / "web"), "sandglass/web"),
        (str(ROOT / "native" / "SandglassShell" / "bin"), "sandglass/native"),
        (str(ROOT / "build" / "Sandglass-build-provenance.json"), "sandglass"),
    ],
    hiddenimports=["pystray._win32", "webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sandglass",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(ROOT / "sandglass" / "web" / "assets" / "orb.ico"),
    manifest=str(ROOT / "packaging" / "sandglass.manifest"),
    version=str(version_file),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Sandglass",
    contents_directory="_internal",
)
