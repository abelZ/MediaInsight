"""Application setup and theme application."""

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor
from PySide6.QtCore import Qt

from media_analyzer.ui.themes import (
    get_current_theme, generate_stylesheet, Theme
)


def create_application(argv=None) -> QApplication:
    """Create and configure the QApplication instance."""
    if argv is None:
        argv = sys.argv

    app = QApplication(argv)
    app.setApplicationName("Media Analyzer")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("MediaAnalyzer")

    # Set application icon (visible in taskbar and window title)
    _set_app_icon(app)

    # Use Fusion style for consistent cross-platform look
    app.setStyle("Fusion")

    # Apply default theme
    apply_theme(app, get_current_theme())

    return app


def _set_app_icon(app: QApplication) -> None:
    """Set application icon from resources. Works on Windows, macOS, and Linux."""
    import os
    import sys
    from PySide6.QtGui import QIcon, QPixmap

    # Find icon directory — handle both normal and PyInstaller frozen mode
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        base_dir = sys._MEIPASS
    else:
        # Running from source
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    icon_dir = os.path.join(base_dir, "resources", "icons")

    icon = QIcon()

    # Add multiple sizes for best rendering across platforms and DPI scales
    for size in (16, 32, 48, 64, 128, 256, 512):
        png_path = os.path.join(icon_dir, f"app_icon_{size}.png")
        if os.path.exists(png_path):
            icon.addFile(png_path)

    # Fallback: try SVG (Qt renders at any resolution, ideal for HiDPI)
    svg_path = os.path.join(icon_dir, "app_icon.svg")
    if os.path.exists(svg_path):
        icon.addFile(svg_path)

    if not icon.isNull():
        app.setWindowIcon(icon)

    # macOS: also set the Dock icon explicitly
    import platform
    if platform.system() == "Darwin":
        _set_macos_dock_icon(icon_dir)


def _set_macos_dock_icon(icon_dir: str) -> None:
    """Set macOS Dock icon using native API (pyobjc or ctypes fallback)."""
    import os

    png_path = os.path.join(icon_dir, "app_icon_256.png")
    if not os.path.exists(png_path):
        return

    try:
        from Foundation import NSData
        from AppKit import NSApplication, NSImage

        icon_data = NSData.dataWithContentsOfFile_(png_path)
        if icon_data:
            icon_image = NSImage.alloc().initWithData_(icon_data)
            if icon_image:
                NSApplication.sharedApplication().setApplicationIconImage_(icon_image)
    except ImportError:
        pass


def apply_theme(app: QApplication, theme: Theme) -> None:
    """Apply a theme's palette and stylesheet to the application."""
    _apply_palette(app, theme)
    app.setStyleSheet(generate_stylesheet(theme))


def _apply_palette(app: QApplication, theme: Theme) -> None:
    """Set QPalette colors from theme."""
    def _hex_to_rgb(hex_color: str):
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    palette = QPalette()

    bg_r, bg_g, bg_b = _hex_to_rgb(theme.bg_primary)
    base_r, base_g, base_b = _hex_to_rgb(theme.bg_secondary)
    tert_r, tert_g, tert_b = _hex_to_rgb(theme.bg_tertiary)
    fg_r, fg_g, fg_b = _hex_to_rgb(theme.fg_primary)
    sel_r, sel_g, sel_b = _hex_to_rgb(theme.selection_bg[:7])  # Handle alpha in hex

    palette.setColor(QPalette.ColorRole.Window, QColor(bg_r, bg_g, bg_b))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(fg_r, fg_g, fg_b))
    palette.setColor(QPalette.ColorRole.Base, QColor(base_r, base_g, base_b))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tert_r, tert_g, tert_b))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tert_r, tert_g, tert_b))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(fg_r, fg_g, fg_b))
    palette.setColor(QPalette.ColorRole.Text, QColor(fg_r, fg_g, fg_b))
    palette.setColor(QPalette.ColorRole.Button, QColor(tert_r, tert_g, tert_b))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(fg_r, fg_g, fg_b))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Link, QColor(*_hex_to_rgb(theme.fg_accent)))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(sel_r, sel_g, sel_b))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    app.setPalette(palette)
