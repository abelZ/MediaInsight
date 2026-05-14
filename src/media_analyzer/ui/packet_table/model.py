"""Packet table model - QAbstractTableModel for virtual scrolling."""

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QColor, QFont
from typing import List, Optional

from media_analyzer.core.models import PacketInfo, TagType, FrameType, AVCPacketType


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

# Extra columns for TS packet view
TS_PKT_COLUMNS = [
    ("No.",         "index",            60),
    ("Type",        "type_label",       70),
    ("PID",         "_pid",             60),
    ("CC",          "_cc",              35),
    ("PUSI",        "_pusi",            45),
    ("Timestamp",   "timestamp",        90),
    ("Size",        "data_size",        70),
    ("Offset",      "offset",           90),
    ("Codec",       "codec_label",      90),
    ("Frame",       "frame_label",      60),
    ("Detail",      "detail_label",     200),
]

# Row background colors by tag type (dark theme, subtle tints)
# Very subtle color differences on a near-neutral dark base
TYPE_BG_COLORS = {
    TagType.HEADER: QColor(38, 32, 42),      # Very subtle purple tint
    TagType.VIDEO:  QColor(30, 34, 44),      # Very subtle blue tint
    TagType.AUDIO:  QColor(30, 38, 34),      # Very subtle green tint
    TagType.SCRIPT: QColor(38, 36, 30),      # Very subtle warm tint
}

# Row text colors by tag type (readable but not glaring)
TYPE_FG_COLORS = {
    TagType.HEADER: QColor(180, 160, 200),   # Soft purple
    TagType.VIDEO:  QColor(160, 185, 220),   # Soft blue
    TagType.AUDIO:  QColor(160, 200, 170),   # Soft green
    TagType.SCRIPT: QColor(200, 190, 150),   # Soft yellow
}

# Special video sub-type colors (still subtle)
VIDEO_IDR_BG = QColor(32, 36, 50)           # Slightly brighter blue for I-frames
VIDEO_IDR_FG = QColor(170, 195, 230)        # Soft bright blue
VIDEO_SEQ_BG = QColor(36, 32, 46)           # Slightly purple for sequence headers
VIDEO_SEQ_FG = QColor(175, 160, 210)        # Soft purple


def _get_row_colors(packet: PacketInfo, row: int):
    """
    Get (background, foreground) colors for a packet row.
    Considers tag type, video sub-type, and alternating rows.
    """
    tag_type = packet.tag_type

    # Default
    bg = TYPE_BG_COLORS.get(tag_type, QColor(30, 30, 40))
    fg = TYPE_FG_COLORS.get(tag_type, QColor(200, 200, 200))

    if tag_type == TagType.VIDEO:
        # Sequence headers get special color
        if packet.avc_packet_type == AVCPacketType.SEQUENCE_HEADER:
            bg = VIDEO_SEQ_BG
            fg = VIDEO_SEQ_FG
        # I-frames get brighter color
        elif packet.frame_type == FrameType.KEY:
            bg = VIDEO_IDR_BG
            fg = VIDEO_IDR_FG

    # Alternating row brightness (subtle ±8 on RGB)
    if row % 2 == 1:
        bg = QColor(
            min(255, bg.red() + 8),
            min(255, bg.green() + 8),
            min(255, bg.blue() + 8),
        )

    return bg, fg


class PacketTableModel(QAbstractTableModel):
    """
    High-performance table model backed by a flat list of PacketInfo.

    Qt's model/view architecture means only VISIBLE rows are ever
    queried - no matter if the list has millions of entries.

    Supports switching column layout (standard vs TS packet view).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._packets: List[PacketInfo] = []
        self._columns = COLUMNS  # Active column set

    def set_column_mode(self, mode: str) -> None:
        """Switch column layout. mode: 'standard' or 'ts_pkt'."""
        self.beginResetModel()
        if mode == "ts_pkt":
            self._columns = TS_PKT_COLUMNS
        else:
            self._columns = COLUMNS
        self.endResetModel()

    # --- Qt Model Interface ---

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._packets)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if index.row() >= len(self._packets):
            return None

        packet = self._packets[index.row()]
        col_name, col_attr, _ = self._columns[index.column()]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._format_cell(packet, col_attr)
        elif role == Qt.ItemDataRole.BackgroundRole:
            bg, _ = _get_row_colors(packet, index.row())
            return bg
        elif role == Qt.ItemDataRole.ForegroundRole:
            _, fg = _get_row_colors(packet, index.row())
            return fg
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            # Right-align numeric columns
            if col_attr in ("index", "timestamp", "data_size", "offset",
                           "composition_time", "dts", "pts", "_pid", "_cc"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elif col_attr == "_pusi":
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
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
                if section < len(self._columns):
                    return self._columns[section][0]
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
        # TS-specific virtual columns (from script_data)
        if attr == "_pid":
            if packet.script_data and "pid" in packet.script_data:
                pid = packet.script_data["pid"]
                return f"{pid}"
            return str(packet.stream_id) if packet.stream_id else ""
        elif attr == "_cc":
            if packet.script_data and "continuity_counter" in packet.script_data:
                return str(packet.script_data["continuity_counter"])
            return ""
        elif attr == "_pusi":
            if packet.script_data and "pusi" in packet.script_data:
                return "1" if packet.script_data["pusi"] else "0"
            return ""

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
