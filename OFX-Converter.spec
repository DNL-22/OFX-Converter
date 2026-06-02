# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OFX-Converter.

Build with:   pyinstaller OFX-Converter.spec
Output:       dist/OFX-Converter.exe   (single-file, no console window)
"""

block_cipher = None

# Bundle Flask templates alongside the executable
datas = [
    ("templates", "templates"),
]

hiddenimports = []

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OFX-Converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,            # keep the console: closing it shuts down the server
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,               # add a path to an .ico here later if you want one
)
