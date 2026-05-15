# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for MediaInsight."""

import sys
import os

block_cipher = None

# Paths
ROOT_DIR = os.path.dirname(os.path.abspath(SPEC))
SRC_DIR = os.path.join(ROOT_DIR, 'src')
RESOURCES_DIR = os.path.join(ROOT_DIR, 'resources')

a = Analysis(
    [os.path.join(SRC_DIR, 'media_analyzer', '__main__.py')],
    pathex=[SRC_DIR],
    binaries=[],
    datas=[
        (os.path.join(RESOURCES_DIR, 'icons'), os.path.join('resources', 'icons')),
    ],
    hiddenimports=[
        'media_analyzer.parsers.flv',
        'media_analyzer.parsers.flv.parser',
        'media_analyzer.parsers.flv.script',
        'media_analyzer.parsers.ts',
        'media_analyzer.parsers.ts.parser',
        'media_analyzer.parsers.mp4',
        'media_analyzer.parsers.mp4.parser',
        'media_analyzer.parsers.h264',
        'media_analyzer.parsers.h264.bitreader',
        'media_analyzer.parsers.h264.sps',
        'media_analyzer.parsers.h264.pps',
        'media_analyzer.parsers.h265',
        'media_analyzer.parsers.h265.vps',
        'media_analyzer.parsers.h265.sps',
        'media_analyzer.parsers.h265.pps',
        'media_analyzer.ui.themes',
        'media_analyzer.ui.player_page',
        'media_analyzer.ui.box_tree_view',
        'pymediainfo',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test'],
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
    name='MediaInsight',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(RESOURCES_DIR, 'icons', 'app_icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MediaInsight',
)

# macOS app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='MediaInsight.app',
        icon=os.path.join(RESOURCES_DIR, 'icons', 'app_icon_256.png'),
        bundle_identifier='com.mediaanalyzer.mediainsight',
        info_plist={
            'CFBundleName': 'MediaInsight',
            'CFBundleDisplayName': 'MediaInsight',
            'CFBundleVersion': '0.1.0',
            'CFBundleShortVersionString': '0.1.0',
            'NSHighResolutionCapable': True,
        },
    )
