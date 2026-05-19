"""HLS segment list view with M3U8 info bar and raw M3U8 text tab."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QTabWidget, QPlainTextEdit,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor, QFont

from media_analyzer.core.hls.m3u8_parser import HLSPlaylist, HLSSegment


class HLSView(QWidget):
    """
    HLS segment list with M3U8 info bar and raw text tab.

    Layout:
    - Top: info bar showing M3U8 metadata
    - Bottom: QTabWidget
        - Tab 0: Segment list
        - Tab 1: Raw M3U8 text

    Clicking a segment emits segment_clicked signal.
    """

    segment_clicked = Signal(object)  # Emits HLSSegment

    def __init__(self, parent=None):
        super().__init__(parent)
        self._playlist = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Info bar
        self._info_label = QLabel("")
        self._info_label.setStyleSheet(
            "font-size: 11px; color: #aaa; padding: 4px; "
            "background-color: rgba(50, 50, 60, 150); border-radius: 3px;"
        )
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        # Tab widget (Segments | M3U8 Raw)
        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.South)

        # Tab 0: Segment list
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setStyleSheet("""
            QListWidget { font-size: 11px; }
            QListWidget::item { padding: 3px 6px; }
            QListWidget::item:selected { background-color: rgba(80, 120, 200, 100); }
        """)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._tabs.addTab(self._list, "Segments")

        # Tab 1: Raw M3U8 text
        self._raw_text = QPlainTextEdit()
        self._raw_text.setReadOnly(True)
        self._raw_text.setFont(QFont("Consolas", 10))
        self._raw_text.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e24; color: #c8c8d0; }"
        )
        self._tabs.addTab(self._raw_text, "M3U8")

        layout.addWidget(self._tabs, 1)

    def load_playlist(self, playlist: HLSPlaylist, raw_content: str = "") -> None:
        """Display M3U8 playlist info, segment list, and raw text."""
        self._playlist = playlist
        self._list.clear()

        # Info bar
        info_parts = []
        info_parts.append(f"Version: {playlist.version}")
        info_parts.append(f"Target Duration: {playlist.target_duration:.0f}s")
        info_parts.append(f"Segments: {len(playlist.segments)}")
        total_min = int(playlist.total_duration // 60)
        total_sec = playlist.total_duration % 60
        info_parts.append(f"Total: {total_min}:{total_sec:04.1f}")
        if playlist.playlist_type:
            info_parts.append(f"Type: {playlist.playlist_type}")
        elif playlist.is_endlist:
            info_parts.append("Type: VOD")
        else:
            info_parts.append("Type: Live")
        info_parts.append(f"Seq: {playlist.media_sequence}")
        self._info_label.setText("  |  ".join(info_parts))

        # Segment list items
        for seg in playlist.segments:
            # Extract filename from URI
            filename = seg.uri.split("/")[-1].split("?")[0]
            text = f"#{seg.index:<4d}  {seg.duration:.3f}s  {filename}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, seg)
            self._list.addItem(item)

        # Raw M3U8 content
        if raw_content:
            self._raw_text.setPlainText(raw_content)
        else:
            self._raw_text.setPlainText("")

    def set_segment_status(self, index: int, status: str) -> None:
        """Update status display for a segment.

        status: "pending" / "downloading" / "loaded" / "error"
        """
        if index < 0 or index >= self._list.count():
            return
        item = self._list.item(index)
        seg = item.data(Qt.ItemDataRole.UserRole)
        if not seg:
            return

        filename = seg.uri.split("/")[-1].split("?")[0]
        base_text = f"#{seg.index:<4d}  {seg.duration:.3f}s  {filename}"

        if status == "downloading":
            item.setText(f"{base_text}  ...")
            item.setForeground(QColor(200, 180, 100))
        elif status == "loaded":
            item.setText(f"{base_text}  ✓")
            item.setForeground(QColor(100, 200, 130))
        elif status == "error":
            item.setText(f"{base_text}  ✗")
            item.setForeground(QColor(230, 100, 100))
        else:
            item.setText(base_text)
            item.setForeground(QColor(180, 180, 190))

    def _on_item_clicked(self, item: QListWidgetItem):
        """Handle segment click."""
        seg = item.data(Qt.ItemDataRole.UserRole)
        if seg:
            self.segment_clicked.emit(seg)

    def clear(self) -> None:
        """Clear the view."""
        self._list.clear()
        self._raw_text.clear()
        self._info_label.setText("")
        self._playlist = None
