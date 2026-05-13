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
QTableView::item {
    padding: 2px 6px;
    border: none;
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

    # Use Fusion style for consistent cross-platform look
    app.setStyle("Fusion")

    # Apply dark theme
    apply_dark_theme(app)

    return app


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
