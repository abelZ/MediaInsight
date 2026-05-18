"""RTMP dual view — QTabWidget with RTMP Packets and FLV Tags tabs."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTabWidget
from PySide6.QtCore import Signal
from typing import List

from media_analyzer.core.models import PacketInfo
from media_analyzer.ui.packet_table.model import PacketTableModel
from media_analyzer.ui.packet_table.view import PacketTableView


class RTMPDualView(QWidget):
    """
    Dual-tab view for RTMP sessions.

    Tab 0: "RTMP Packets" — protocol-level view (handshake, commands, media chunks)
    Tab 1: "FLV Tags" — extracted FLV tags (audio/video/script with full parsing)

    Selection from either tab emits packet_selected for detail/hex panel.
    """

    packet_selected = Signal(object)  # Emits PacketInfo

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.South)

        # Tab 0: RTMP Packets
        self._rtmp_model = PacketTableModel(self)
        self._rtmp_model.set_column_mode("rtmp")
        self._rtmp_view = PacketTableView(self._rtmp_model)
        # Apply RTMP column widths
        from media_analyzer.ui.packet_table.model import RTMP_COLUMNS
        self._rtmp_view._apply_column_widths(RTMP_COLUMNS)
        self._tabs.addTab(self._rtmp_view, "RTMP Packets")

        # Tab 1: FLV Tags
        self._flv_model = PacketTableModel(self)
        self._flv_model.set_column_mode("flv")
        self._flv_view = PacketTableView(self._flv_model)
        self._tabs.addTab(self._flv_view, "FLV Tags")

        layout.addWidget(self._tabs)

        # Connect selection signals
        self._rtmp_view.packet_selected.connect(self._on_rtmp_selected)
        self._flv_view.packet_selected.connect(self._on_flv_selected)

    def _on_rtmp_selected(self, packet: PacketInfo):
        """Forward RTMP packet selection."""
        self.packet_selected.emit(packet)

    def _on_flv_selected(self, packet: PacketInfo):
        """Forward FLV tag selection."""
        self.packet_selected.emit(packet)

    def append_rtmp_packets(self, packets: List[PacketInfo]) -> None:
        """Add RTMP protocol packets to the RTMP tab."""
        self._rtmp_view.setUpdatesEnabled(False)
        self._rtmp_model.append_packets(packets)
        self._rtmp_view.setUpdatesEnabled(True)

    def append_flv_tags(self, packets: List[PacketInfo]) -> None:
        """Add FLV tags to the FLV tab."""
        self._flv_view.setUpdatesEnabled(False)
        self._flv_model.append_packets(packets)
        self._flv_view.setUpdatesEnabled(True)

    def clear(self) -> None:
        """Clear both tabs."""
        self._rtmp_model.clear()
        self._flv_model.clear()

    @property
    def rtmp_packet_count(self) -> int:
        return self._rtmp_model.packet_count

    @property
    def flv_packet_count(self) -> int:
        return self._flv_model.packet_count
