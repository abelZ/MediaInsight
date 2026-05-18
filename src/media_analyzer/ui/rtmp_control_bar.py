"""RTMP control bar — Pause/Resume, Disconnect, stats, recording indicator."""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
)
from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtGui import QColor


class RTMPControlBar(QWidget):
    """
    Horizontal control bar shown below the nav bar during RTMP sessions.

    Layout: [Recording Dot] [Pause/Resume] [Disconnect] [----Stats Label----]
    """

    pause_clicked = Signal()
    resume_clicked = Signal()
    disconnect_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "disconnected"
        self._dot_visible = True
        self._setup_ui()
        self._setup_blink_timer()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Recording dot indicator
        self._dot_label = QLabel()
        self._dot_label.setFixedSize(12, 12)
        self._dot_label.setStyleSheet(self._dot_style("red"))
        layout.addWidget(self._dot_label)

        # Status text (short)
        self._state_label = QLabel("Disconnected")
        self._state_label.setFixedWidth(90)
        self._state_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        layout.addWidget(self._state_label)

        # Pause button
        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setFixedWidth(70)
        self._btn_pause.clicked.connect(self.pause_clicked.emit)
        self._btn_pause.setEnabled(False)
        layout.addWidget(self._btn_pause)

        # Resume button
        self._btn_resume = QPushButton("Resume")
        self._btn_resume.setFixedWidth(70)
        self._btn_resume.clicked.connect(self.resume_clicked.emit)
        self._btn_resume.setEnabled(False)
        self._btn_resume.hide()
        layout.addWidget(self._btn_resume)

        # Disconnect button
        self._btn_disconnect = QPushButton("Disconnect")
        self._btn_disconnect.setFixedWidth(90)
        self._btn_disconnect.clicked.connect(self.disconnect_clicked.emit)
        self._btn_disconnect.setEnabled(False)
        layout.addWidget(self._btn_disconnect)

        # Separator
        layout.addSpacing(12)

        # Stats label (stretches)
        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet("font-size: 11px; color: #aaa;")
        self._stats_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._stats_label, 1)

        self.setFixedHeight(36)

    def _setup_blink_timer(self):
        """Timer for blinking recording dot."""
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._toggle_dot)

    def _toggle_dot(self):
        """Toggle dot visibility for blink effect."""
        self._dot_visible = not self._dot_visible
        if self._state == "playing":
            color = "red" if self._dot_visible else "transparent"
        elif self._state == "connecting" or self._state == "handshake":
            color = "#f0ad4e" if self._dot_visible else "transparent"
        else:
            color = "gray"
        self._dot_label.setStyleSheet(self._dot_style(color))

    @staticmethod
    def _dot_style(color: str) -> str:
        """Generate stylesheet for the recording dot."""
        return (
            f"background-color: {color}; "
            f"border-radius: 6px; "
            f"min-width: 12px; max-width: 12px; "
            f"min-height: 12px; max-height: 12px;"
        )

    def set_state(self, state: str) -> None:
        """
        Update control bar state.
        States: "connecting", "handshake", "playing", "paused", "disconnected", "error"
        """
        self._state = state

        if state in ("connecting", "handshake"):
            self._state_label.setText("Connecting...")
            self._dot_label.setStyleSheet(self._dot_style("#f0ad4e"))
            self._btn_pause.setEnabled(False)
            self._btn_pause.show()
            self._btn_resume.hide()
            self._btn_disconnect.setEnabled(True)
            self._blink_timer.start()

        elif state == "playing":
            self._state_label.setText("Receiving")
            self._dot_label.setStyleSheet(self._dot_style("red"))
            self._btn_pause.setEnabled(True)
            self._btn_pause.show()
            self._btn_resume.hide()
            self._btn_disconnect.setEnabled(True)
            self._blink_timer.start()

        elif state == "paused":
            self._state_label.setText("Paused")
            self._dot_label.setStyleSheet(self._dot_style("gray"))
            self._btn_pause.hide()
            self._btn_resume.show()
            self._btn_resume.setEnabled(True)
            self._btn_disconnect.setEnabled(True)
            self._blink_timer.stop()

        elif state == "disconnected":
            self._state_label.setText("Disconnected")
            self._dot_label.setStyleSheet(self._dot_style("gray"))
            self._btn_pause.setEnabled(False)
            self._btn_pause.show()
            self._btn_resume.hide()
            self._btn_disconnect.setEnabled(False)
            self._blink_timer.stop()

        elif state == "error":
            self._state_label.setText("Error")
            self._dot_label.setStyleSheet(self._dot_style("#d9534f"))
            self._btn_pause.setEnabled(False)
            self._btn_pause.show()
            self._btn_resume.hide()
            self._btn_disconnect.setEnabled(False)
            self._blink_timer.stop()

    def update_stats(self, stats: dict) -> None:
        """Update statistics display.

        Args:
            stats: dict with keys: bytes, rtmp_count, flv_count, duration_ms
        """
        bytes_val = stats.get("bytes", 0)
        rtmp_count = stats.get("rtmp_count", 0)
        flv_count = stats.get("flv_count", 0)
        duration_ms = stats.get("duration_ms", 0)

        # Format bytes
        if bytes_val >= 1024 * 1024:
            size_str = f"{bytes_val / (1024*1024):.1f} MB"
        elif bytes_val >= 1024:
            size_str = f"{bytes_val / 1024:.1f} KB"
        else:
            size_str = f"{bytes_val} B"

        # Format duration
        secs = duration_ms // 1000
        mins = secs // 60
        secs = secs % 60
        hours = mins // 60
        mins = mins % 60
        if hours > 0:
            dur_str = f"{hours}:{mins:02d}:{secs:02d}"
        else:
            dur_str = f"{mins:02d}:{secs:02d}"

        self._stats_label.setText(
            f"RTMP: {rtmp_count:,} pkts | FLV: {flv_count:,} tags | "
            f"{size_str} | {dur_str}"
        )

    def reset(self) -> None:
        """Reset to initial state."""
        self.set_state("disconnected")
        self._stats_label.setText("")
