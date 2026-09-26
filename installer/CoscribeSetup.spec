# Build CoscribeSetup.exe:  cd installer  &&  pyinstaller --noconfirm CoscribeSetup.spec   (output: installer/dist)
# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['coscribe_setup.py'],
    pathex=[],
    binaries=[],
    datas=[('../app/static/coscribe.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['unittest', 'pydoc', 'test', '_decimal', 'decimal', '_bz2', 'bz2', '_lzma', 'lzma', 'pdb', 'doctest', 'difflib', 'xmlrpc', 'pydoc_data'],
    noarchive=False,
    optimize=0,
)
# Slim Tcl/Tk: timezone data, message catalogs and legacy encodings aren't used by this window.
import re as _re
_drop = _re.compile(r"^_(tcl|tk)_data[/\\](tzdata|msgs|encoding|images|demos)[/\\]", _re.I)
a.datas = [d for d in a.datas if not _drop.search(d[0]) or d[0].lower().endswith(("utf-8.enc", "cp1252.enc", "ascii.enc"))]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CoscribeSetup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['../app/static/coscribe.ico'],
)
