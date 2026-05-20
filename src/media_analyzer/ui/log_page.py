"""Log view page — real-time application log display."""

import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QComboBox, QLabel, QCheckBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCharFormat, QColor, QFont, QTextCursor

from media_analyzer.core.logging_config import get_signal_handler


# Color scheme for log levels (dark theme)
LEVEL_COLORS = {
    logging.DEBUG:    QColor(120, 120, 140),   # Gray
    15:              QColor(100, 170, 180),    # PROTOCOL — teal
    logging.INFO:    QColor(180, 200, 220),   # Light blue-white
    logging.WARNING: QColor(220, 180, 80),    # Yellow-orange
    logging.ERROR:   QColor(230, 90, 90),     # Red
    logging.CRITICAL: QColor(255, 60, 60),    # Bright red
}

MAX_LOG_LINES = 10000  # Limit to prevent memory issues


class LogPage(QWidget):
    """
    Real-time log view with filtering and auto-scroll.

    Features:
    - Color-coded by log level
    - Level filter dropdown
    - Module filter (text contains)
    - Auto-scroll (toggleable)
    - Clear button
    - Max line limit to prevent memory bloat
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._auto_scroll = True
        self._min_level = logging.DEBUG
        self._line_count = 0
        self._setup_ui()
        self._connect_handler()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(8)

        # Level filter
        toolbar.addWidget(QLabel("Level:"))
        self._level_combo = QComboBox()
        self._level_combo.addItem("DEBUG", logging.DEBUG)
        self._level_combo.addItem("INFO", logging.INFO)
        self._level_combo.addItem("WARNING", logging.WARNING)
        self._level_combo.addItem("ERROR", logging.ERROR)
        self._level_combo.setCurrentIndex(0)  # Show all by default
        self._level_combo.currentIndexChanged.connect(self._on_level_changed)
        self._level_combo.setFixedWidth(100)
        toolbar.addWidget(self._level_combo)

        toolbar.addSpacing(16)

        # Auto-scroll toggle
        self._auto_scroll_cb = QCheckBox("Auto-scroll")
        self._auto_scroll_cb.setChecked(True)
        self._auto_scroll_cb.toggled.connect(self._on_auto_scroll_toggled)
        toolbar.addWidget(self._auto_scroll_cb)

        toolbar.addStretch()

        # Line count
        self._count_label = QLabel("0 lines")
        self._count_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self._count_label)

        toolbar.addSpacing(8)

        # Clear button
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(24)
        clear_btn.setFixedWidth(60)
        clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(clear_btn)

        layout.addLayout(toolbar)

        # Log text area
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setMaximumBlockCount(MAX_LOG_LINES)
        self._text_edit.setFont(QFont("Consolas", 9))
        self._text_edit.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1a1a20;
                color: #c0c0c8;
                border: none;
                selection-background-color: #3a5070;
            }
        """)
        self._text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._text_edit)

    def _connect_handler(self):
        """Connect to the global Qt log signal handler."""
        handler = get_signal_handler()
        handler.emitter.log_record.connect(self._on_log_record)

    def _on_log_record(self, record: logging.LogRecord):
        """Handle incoming log record."""
        # Level filter
        if record.levelno < self._min_level:
            return

        # Format and colorize
        msg = record.formatted_message if hasattr(record, 'formatted_message') else str(record)

        # Get color for level
        color = LEVEL_COLORS.get(record.levelno, QColor(180, 180, 180))

        # Append with color
        fmt = QTextCharFormat()
        fmt.setForeground(color)

        cursor = self._text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(msg + "\n", fmt)

        self._line_count += 1
        if self._line_count % 50 == 0:  # Update count periodically
            self._count_label.setText(f"{self._line_count} lines")

        # Auto-scroll
        if self._auto_scroll:
            scrollbar = self._text_edit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _on_level_changed(self, index: int):
        """Filter level changed."""
        self._min_level = self._level_combo.itemData(index)

    def _on_auto_scroll_toggled(self, checked: bool):
        self._auto_scroll = checked

    def _clear(self):
        """Clear all log entries."""
        self._text_edit.clear()
        self._line_count = 0
        self._count_label.setText("0 lines")
