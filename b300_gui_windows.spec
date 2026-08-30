# -*- mode: python ; coding: utf-8 -*-

import tempfile
from pathlib import Path

from b300_core.build_info import build_commit as resolve_build_commit
from b300_core.factory_resource import load_trusted_bootloader

project_root = Path(SPECPATH)
trusted_bootloader = load_trusted_bootloader(project_root / "resources" / "firmware")
build_commit = resolve_build_commit()
build_commit_file = Path(tempfile.mkdtemp(prefix="b300-stlink-build-")) / "BUILD-COMMIT.txt"
build_commit_file.parent.mkdir(parents=True, exist_ok=True)
build_commit_file.write_text(build_commit + "\n", encoding="ascii")

a = Analysis(
    [str(project_root / "b300_gui_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "branding" / "b300-stlink-icon.png"), "branding"),
        (str(project_root / "branding" / "b300-stlink-wordmark.png"), "branding"),
        (str(project_root / "CHANGELOG.md"), "."),
        (str(build_commit_file), "."),
        (str(trusted_bootloader.image.path), "resources/firmware"),
        (str(trusted_bootloader.manifest_path), "resources/firmware"),
        (str(trusted_bootloader.catalog_path), "resources/firmware"),
    ],
    hiddenimports=[],
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
    name="b300-stlink-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "branding" / "b300-stlink-icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="b300-stlink-gui",
)
