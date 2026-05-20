# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for MediaInsight.

Builds for both Windows and macOS:
- Windows: dist/MediaInsight/ folder with MediaInsight.exe
- macOS: dist/MediaInsight.app bundle

Bundles:
- VLC libraries from vendor/vlc/<platform>/ for embedded playback
- Application icons from resources/icons/
"""

import sys
import os

block_cipher = None

# Paths
ROOT_DIR = os.path.dirname(os.path.abspath(SPEC))
SRC_DIR = os.path.join(ROOT_DIR, 'src')
RESOURCES_DIR = os.path.join(ROOT_DIR, 'resources')
VENDOR_DIR = os.path.join(ROOT_DIR, 'vendor')

# --- Platform-specific VLC bundling ---
vlc_datas = []
vlc_binaries = []

if sys.platform == 'win32':
    vlc_dir = os.path.join(VENDOR_DIR, 'vlc', 'win64')
    if os.path.isdir(vlc_dir):
        # Bundle libvlc.dll and libvlccore.dll as binaries
        for dll in ('libvlc.dll', 'libvlccore.dll'):
            dll_path = os.path.join(vlc_dir, dll)
            if os.path.isfile(dll_path):
                vlc_binaries.append((dll_path, '.'))

        # Bundle plugins directory — must be at same level as libvlc.dll
        # VLC looks for plugins/ relative to the directory containing libvlc.dll
        plugins_dir = os.path.join(vlc_dir, 'plugins')
        if os.path.isdir(plugins_dir):
            vlc_datas.append((plugins_dir, 'plugins'))

elif sys.platform == 'darwin':
    vlc_dir = os.path.join(VENDOR_DIR, 'vlc', 'macos')
    vlc_lib_dir = os.path.join(vlc_dir, 'lib')
    if os.path.isdir(vlc_lib_dir):
        # Bundle dylibs
        for f in os.listdir(vlc_lib_dir):
            if f.endswith('.dylib'):
                vlc_binaries.append(
                    (os.path.join(vlc_lib_dir, f), 'vlc/lib'))

        # Bundle plugins
        plugins_dir = os.path.join(vlc_dir, 'plugins')
        if os.path.isdir(plugins_dir):
            vlc_datas.append((plugins_dir, os.path.join('vlc', 'plugins')))

# --- Data files ---
datas = [
    (os.path.join(RESOURCES_DIR, 'icons'), os.path.join('resources', 'icons')),
]
datas.extend(vlc_datas)

# --- Collect python-vlc module (single-file module that fails to import at build time) ---
# PyInstaller can't auto-collect it because `import vlc` tries to load libvlc at import time
try:
    import importlib.util as _ilu
    _vlc_spec = _ilu.find_spec('vlc')
    if _vlc_spec and _vlc_spec.origin and os.path.isfile(_vlc_spec.origin):
        # Copy vlc.py to the bundle root so `import vlc` works at runtime
        datas.append((_vlc_spec.origin, '.'))
except Exception:
    pass

# --- Hidden imports ---
# All application modules that may be imported dynamically (lazy imports, etc.)
hiddenimports = [
    # Parsers
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
    # Core
    'media_analyzer.core.logging_config',
    'media_analyzer.core.models',
    'media_analyzer.core.source',
    'media_analyzer.core.rtmp',
    'media_analyzer.core.rtmp.client',
    'media_analyzer.core.rtmp.chunk',
    'media_analyzer.core.rtmp.constants',
    'media_analyzer.core.rtmp.amf0',
    'media_analyzer.core.rtmp.flv_writer',
    'media_analyzer.core.hls',
    'media_analyzer.core.hls.m3u8_parser',
    # UI
    'media_analyzer.ui.themes',
    'media_analyzer.ui.player_page',
    'media_analyzer.ui.box_tree_view',
    'media_analyzer.ui.bitrate_page',
    'media_analyzer.ui.timestamp_page',
    'media_analyzer.ui.log_page',
    'media_analyzer.ui.rtmp_view',
    'media_analyzer.ui.rtmp_control_bar',
    'media_analyzer.ui.hls_view',
    'media_analyzer.ui.detail_panel',
    'media_analyzer.ui.hex_view',
    # Workers
    'media_analyzer.workers.parse_worker',
    'media_analyzer.workers.rtmp_worker',
    'media_analyzer.workers.hls_worker',
    # Third-party
    'pymediainfo',
]

# --- Analysis ---
a = Analysis(
    [os.path.join(SRC_DIR, 'media_analyzer', '__main__.py')],
    pathex=[SRC_DIR],
    binaries=vlc_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --- Executable ---
exe_kwargs = dict(
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
)

# Platform-specific icon
if sys.platform == 'win32':
    exe_kwargs['icon'] = os.path.join(RESOURCES_DIR, 'icons', 'app_icon.ico')
elif sys.platform == 'darwin':
    exe_kwargs['icon'] = os.path.join(RESOURCES_DIR, 'icons', 'app_icon_256.png')

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    **exe_kwargs,
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

# --- macOS app bundle ---
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='MediaInsight.app',
        icon=os.path.join(RESOURCES_DIR, 'icons', 'app_icon_256.png'),
        bundle_identifier='com.mediainsight.app',
        info_plist={
            'CFBundleName': 'MediaInsight',
            'CFBundleDisplayName': 'MediaInsight',
            'CFBundleVersion': '0.1.0',
            'CFBundleShortVersionString': '0.1.0',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '10.15',
            'NSMicrophoneUsageDescription': 'MediaInsight needs audio access for playback.',
        },
    )
