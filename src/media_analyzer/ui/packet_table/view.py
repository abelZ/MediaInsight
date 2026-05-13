"""Packet table view - QTableView subclass with optimized settings."""

from PySide6.QtWidgets import QTableView, QHeaderView, QAbstractItemView
from PySide6.QtCore import Qt, Signal, QModelIndex, QSortFilterProxyModel

from media_analyzer.core.models import PacketInfo, TagType, FrameType, H264NALUType
from media_analyzer.ui.packet_table.model import PacketTableModel, COLUMNS


class PacketFilterProxyModel(QSortFilterProxyModel):
    """
    Filter proxy that shows/hides rows based on tag type and frame properties.
    Header rows always pass through. Video/Audio/Script can be toggled.
    Additional filters: IDR-only, has-SEI-only (these narrow video results).

    Performance: when all filters are OFF (common case during loading),
    filterAcceptsRow returns True immediately without any lookup.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_header = True
        self._show_video = True
        self._show_audio = True
        self._show_script = True
        # Narrowing filters (only affect video tags)
        self._only_idr = False       # When True, only show IDR (I-frame) video tags
        self._only_has_sei = False   # When True, only show video tags containing SEI NALUs
        self._all_visible = True     # Fast path flag

    def set_filter(self, show_video: bool, show_audio: bool, show_script: bool,
                   only_idr: bool = False, only_has_sei: bool = False) -> None:
        """Update which tag types are visible."""
        changed = (
            self._show_video != show_video or
            self._show_audio != show_audio or
            self._show_script != show_script or
            self._only_idr != only_idr or
            self._only_has_sei != only_has_sei
        )
        self._show_video = show_video
        self._show_audio = show_audio
        self._show_script = show_script
        self._only_idr = only_idr
        self._only_has_sei = only_has_sei
        self._all_visible = (
            show_video and show_audio and show_script
            and not only_idr and not only_has_sei
        )
        if changed:
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        """Decide if a row passes the filter."""
        # Fast path: all visible, no narrowing filters
        if self._all_visible:
            return True

        source_model = self.sourceModel()
        if not isinstance(source_model, PacketTableModel):
            return True

        packet = source_model.get_packet(source_row)
        if packet is None:
            return False

        tag_type = packet.tag_type

        if tag_type == TagType.HEADER:
            return self._show_header
        elif tag_type == TagType.VIDEO:
            if not self._show_video:
                return False
            # Apply narrowing filters for video
            if self._only_idr:
                if packet.frame_type != FrameType.KEY:
                    return False
            if self._only_has_sei:
                if not self._packet_has_sei(packet):
                    return False
            return True
        elif tag_type == TagType.AUDIO:
            return self._show_audio
        elif tag_type == TagType.SCRIPT:
            return self._show_script

        return True

    @staticmethod
    def _packet_has_sei(packet: PacketInfo) -> bool:
        """Check if a video packet contains a SEI NALU."""
        if not packet.nalu_list:
            return False
        for nalu in packet.nalu_list:
            # H.264 SEI = type 6, H.265 PREFIX_SEI = 39, SUFFIX_SEI = 40
            if nalu.nalu_type in (6, 39, 40):
                return True
        return False

    def get_packet(self, proxy_row: int):
        """Get the PacketInfo for a proxy row (maps back to source)."""
        proxy_index = self.index(proxy_row, 0)
        source_index = self.mapToSource(proxy_index)
        source_model = self.sourceModel()
        if isinstance(source_model, PacketTableModel):
            return source_model.get_packet(source_index.row())
        return None


class PacketTableView(QTableView):
    """
    Optimized table view for displaying media packets.

    Features:
    - Virtual scrolling (only renders visible rows)
    - Alternating row colors by tag type
    - Single-row selection
    - Column sizing hints
    """

    # Signal emitted when a packet row is selected
    packet_selected = Signal(object)  # PacketInfo

    def __init__(self, model: PacketTableModel, parent=None):
        super().__init__(parent)

        # Create filter proxy between source model and view
        self._source_model = model
        self._proxy_model = PacketFilterProxyModel(self)
        self._proxy_model.setSourceModel(model)
        self.setModel(self._proxy_model)

        self._setup_view()

    @property
    def proxy_model(self) -> PacketFilterProxyModel:
        """Access the filter proxy model."""
        return self._proxy_model

    @property
    def source_model(self) -> PacketTableModel:
        """Access the underlying source data model."""
        return self._source_model

    def _setup_view(self):
        """Configure table view settings for optimal performance."""
        # Selection behavior
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Performance: fixed row height for fast scrolling
        self.verticalHeader().setDefaultSectionSize(22)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().setVisible(False)  # Hide row numbers

        # Horizontal header
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        # Set column widths from hints
        for i, (_, _, width) in enumerate(COLUMNS):
            self.setColumnWidth(i, width)

        # Visual settings
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)  # We color by tag type instead
        self.setWordWrap(False)

        # Performance optimizations
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        # Enable sorting
        self.setSortingEnabled(False)  # Disable for now, packets are in order

    def currentChanged(self, current: QModelIndex, previous: QModelIndex):
        """Handle row selection change."""
        super().currentChanged(current, previous)
        if current.isValid():
            packet = self._proxy_model.get_packet(current.row())
            if packet:
                self.packet_selected.emit(packet)

    def scroll_to_bottom(self):
        """Scroll to the last row (useful during live parsing)."""
        model = self.model()
        if model.rowCount() > 0:
            last_index = model.index(model.rowCount() - 1, 0)
            self.scrollTo(last_index)
