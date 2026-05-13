"""Packet table model - QAbstractTableModel for virtual scrolling."""

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QColor, QFont
from typing import List, Optional

from media_analyzer.core.models import PacketInfo, TagType


# Column definitions: (header_name, attribute_or_property, width_hint)
COLUMNS = [
    ("No.",         "index",            60),
    ("Type",        "type_label",       70),
    ("Timestamp",   "timestamp",        90),
    ("Size",        "data_size",        80),
    ("Offset",      "offset",           100),
    ("CTS",         "composition_time", 60),
    ("DTS",         "dts",              80),
    ("PTS",         "pts",              80),
    ("Codec",       "codec_label",      100),
    ("Frame",       "frame_label",      100),
    ("Detail",      "detail_label",     200),
]

# Row background colors by tag type (dark theme)
TYPE_BG_COLORS = {
    TagType.HEADER: QColor(60, 30, 60),      # Dark purple
    TagType.VIDEO:  QColor(30, 40, 70),      # Dark blue
    TagType.AUDIO:  QColor(30, 60, 40),      # Dark green
    TagType.SCRIPT: QColor(60, 55, 30),      # Dark yellow/brown
}

# Row text colors by tag type
TYPE_FG_COLORS = {
    TagType.HEADER: QColor(220, 160, 255),   # Light purple
    TagType.VIDEO:  QColor(140, 180, 255),   # Light blue
    TagType.AUDIO:  QColor(140, 255, 140),   # Light green
    TagType.SCRIPT: QColor(255, 220, 140),   # Light yellow
}


class PacketTableModel(QAbstractTableModel):
    """
    High-performance table model backed by a flat list of PacketInfo.

    Qt's model/view architecture means only VISIBLE rows are ever
    queried - no matter if the list has millions of entries.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._packets: List[PacketInfo] = []

    # --- Qt Model Interface ---

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._packets)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(COLUMNS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if index.row() >= len(self._packets):
            return None

        packet = self._packets[index.row()]
        col_name, col_attr, _ = COLUMNS[index.column()]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._format_cell(packet, col_attr)
        elif role == Qt.ItemDataRole.BackgroundRole:
            return TYPE_BG_COLORS.get(packet.tag_type)
        elif role == Qt.ItemDataRole.ForegroundRole:
            return TYPE_FG_COLORS.get(packet.tag_type, QColor(200, 200, 200))
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            # Right-align numeric columns
            if col_attr in ("index", "timestamp", "data_size", "offset",
                           "composition_time", "dts", "pts"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        elif role == Qt.ItemDataRole.UserRole:
            return packet  # For selection handling
        elif role == Qt.ItemDataRole.FontRole:
            if col_attr == "offset":
                return QFont("Consolas", 9)

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return COLUMNS[section][0]
            elif orientation == Qt.Orientation.Vertical:
                return str(section)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if orientation == Qt.Orientation.Horizontal:
                return int(Qt.AlignmentFlag.AlignCenter)
        return None

    # --- Data Management ---

    def append_packets(self, packets: List[PacketInfo]) -> None:
        """
        Batch-append packets from parser worker thread.
        Uses beginInsertRows/endInsertRows for efficient model updates.
        """
        if not packets:
            return
        start = len(self._packets)
        end = start + len(packets) - 1
        self.beginInsertRows(QModelIndex(), start, end)
        self._packets.extend(packets)
        self.endInsertRows()

    def clear(self) -> None:
        """Clear all packets."""
        self.beginResetModel()
        self._packets.clear()
        self.endResetModel()

    def get_packet(self, row: int) -> Optional[PacketInfo]:
        """Get packet at given row index."""
        if 0 <= row < len(self._packets):
            return self._packets[row]
        return None

    @property
    def packet_count(self) -> int:
        return len(self._packets)

    # --- Formatting ---

    def _format_cell(self, packet: PacketInfo, attr: str) -> str:
        """Format a cell value for display."""
        value = getattr(packet, attr, None)

        if value is None:
            return ""

        # Special formatting for certain attributes
        if attr == "offset":
            return f"0x{value:08X}"
        elif attr == "data_size":
            return f"{value:,}"
        elif attr == "timestamp" or attr == "dts" or attr == "pts":
            return str(value)
        elif attr == "index":
            return str(value)

        return str(value)
