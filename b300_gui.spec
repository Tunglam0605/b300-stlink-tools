# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)

a = Analysis(
    [str(project_root / "b300_gui_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "branding" / "b300-stlink-icon.png"), "branding"),
        (str(project_root / "branding" / "b300-stlink-wordmark.png"), "branding"),
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
    a.binaries,
    a.datas,
    [],
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
