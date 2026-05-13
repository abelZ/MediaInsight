"""Application setup and dark theme."""

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor
from PySide6.QtCore import Qt


DARK_STYLESHEET = """
/* Global */
QWidget {
    background-color: #1e1e2e;
    color: #d4d4d4;
    font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 12px;
}

/* Main Window */
QMainWindow {
    background-color: #1e1e2e;
}

/* Splitter */
QSplitter::handle {
    background-color: #444;
}
QSplitter::handle:horizontal {
    width: 3px;
}
QSplitter::handle:vertical {
    height: 3px;
}

/* Table View */
QTableView {
    background-color: #1a1a2e;
    gridline-color: transparent;
    border: 1px solid #333;
    selection-background-color: #264f78;
    selection-color: #ffffff;
}
QTableView::item:selected {
    background-color: #264f78;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #2d2d4d;
    color: #aaccff;
    padding: 4px 6px;
    border: 1px solid #3d3d5d;
    font-weight: bold;
    font-size: 11px;
}
QHeaderView::section:hover {
    background-color: #3d3d5d;
}

/* Scrollbar */
QScrollBar:vertical {
    background-color: #1a1a2e;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #4a4a5a;
    border-radius: 4px;
    min-height: 30px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background-color: #5a5a6a;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background-color: #1a1a2e;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #4a4a5a;
    border-radius: 4px;
    min-width: 30px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #5a5a6a;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* Dialog */
QDialog {
    background-color: #2d2d3d;
}
QLineEdit {
    background-color: #1a1a2e;
    color: #d4d4d4;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 4px 8px;
}
QLineEdit:focus {
    border-color: #77a;
}
QPushButton {
    background-color: #3d3d5d;
    color: #ddd;
    border: 1px solid #555;
    border-radius: 3px;
    padding: 5px 15px;
}
QPushButton:hover {
    background-color: #4d4d6d;
    border-color: #77a;
}
QPushButton:pressed {
    background-color: #264f78;
}

/* Checkbox */
QCheckBox {
    spacing: 4px;
    padding: 2px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #555;
    border-radius: 2px;
    background-color: #2d2d3d;
}
QCheckBox::indicator:checked {
    background-color: #264f78;
    border-color: #77a;
}

/* Message Box */
QMessageBox {
    background-color: #2d2d3d;
}

/* Tool Tip */
QToolTip {
    background-color: #3d3d4d;
    color: #ddd;
    border: 1px solid #555;
    padding: 4px;
}
"""


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

    # Apply dark theme
    apply_dark_theme(app)

    return app


def _set_app_icon(app: QApplication) -> None:
    """Set application icon from resources. Works on Windows, macOS, and Linux."""
    import os
    from PySide6.QtGui import QIcon, QPixmap

    # Find icon relative to this file's location
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    icon_dir = os.path.join(base_dir, "resources", "icons")

    icon = QIcon()

    # Add multiple sizes for best rendering across platforms and DPI scales
    # macOS Retina needs 128/256/512, Windows needs 16/32/48, Linux needs 48/64/128
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
    # When running as a script (not .app bundle), the Dock shows Python icon.
    # Setting NSApplication's applicationIconImage fixes this.
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
        # Try pyobjc (if available)
        from Foundation import NSData
        from AppKit import NSApplication, NSImage

        icon_data = NSData.dataWithContentsOfFile_(png_path)
        if icon_data:
            icon_image = NSImage.alloc().initWithData_(icon_data)
            if icon_image:
                NSApplication.sharedApplication().setApplicationIconImage_(icon_image)
    except ImportError:
        # pyobjc not available — try ctypes approach via Qt
        # Qt's setWindowIcon usually works for Dock on modern macOS + Qt6
        pass


def apply_dark_theme(app: QApplication) -> None:
    """Apply dark theme palette and stylesheet."""
    # Set dark palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 46))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Base, QColor(26, 26, 46))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(37, 37, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(61, 61, 77))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Text, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 61))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Link, QColor(130, 180, 230))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(38, 79, 120))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    app.setPalette(palette)

    # Apply stylesheet
    app.setStyleSheet(DARK_STYLESHEET)
