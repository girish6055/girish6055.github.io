# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows one-folder build.

Build with:  pyinstaller INTECK_AI_Analytics.spec --noconfirm
Output:      dist\\INTECK_AI_Analytics\\INTECK_AI_Analytics.exe
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [
    ("inteck/web/templates", "inteck/web/templates"),
    ("inteck/web/static", "inteck/web/static"),
]
binaries = []
hiddenimports = collect_submodules("inteck")

# ultralytics ships YAML configs (including bytetrack.yaml) that must travel
# with the executable; torch/torchvision need their compiled extensions.
for package in ("ultralytics", "torch", "torchvision", "cv2", "lap"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] skipping {package}: {exc}")

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter", "PyQt5", "PySide2", "notebook", "IPython", "pandas.tests"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="INTECK_AI_Analytics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="INTECK_AI_Analytics",
)
