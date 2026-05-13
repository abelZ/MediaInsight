"""Detail panel widget - shows parsed fields of selected packet."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel
from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt, Signal
from typing import Any, Dict, Optional, Tuple

from media_analyzer.core.models import (
    PacketInfo, TagType, FrameType, VideoCodec, AudioCodec,
    AVCPacketType, AACPacketType, NALUInfo, FLVHeaderInfo,
)

# Custom data roles for tree items
NALU_DATA_ROLE = Qt.ItemDataRole.UserRole + 1
# Stores (offset_in_tag_total, length) — byte range relative to tag start (including 11-byte header)
BYTE_RANGE_ROLE = Qt.ItemDataRole.UserRole + 2


class DetailPanelWidget(QWidget):
    """
    Tree-based detail panel showing parsed fields of selected FLV tag.
    Clicking a NALU item emits nalu_selected with the NALUInfo.
    Clicking any field with a byte range emits field_byte_range.
    """

    # Emitted when a NALU tree item is clicked; carries (NALUInfo, PacketInfo)
    nalu_selected = Signal(object, object)
    # Emitted when any field is clicked; carries (offset_in_tag, length)
    # offset is relative to the start of the entire tag (byte 0 = TagType)
    field_byte_range = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_packet: Optional[PacketInfo] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Title label
        self._title = QLabel("Tag Details")
        self._title.setStyleSheet("""
            QLabel {
                color: #aaa;
                font-weight: bold;
                font-size: 11px;
                padding: 2px;
            }
        """)
        layout.addWidget(self._title)

        # Tree widget
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Field", "Value"])
        self._tree.setColumnWidth(0, 160)
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setAnimated(False)
        self._tree.setStyleSheet("""
            QTreeWidget {
                background-color: #1e1e2e;
                color: #d4d4d4;
                border: 1px solid #333;
                alternate-background-color: #252535;
            }
            QTreeWidget::item {
                padding: 2px;
            }
            QTreeWidget::item:selected {
                background-color: #264f78;
            }
            QHeaderView::section {
                background-color: #2d2d3d;
                color: #aaa;
                padding: 4px;
                border: 1px solid #333;
                font-weight: bold;
            }
        """)
        layout.addWidget(self._tree)

        # Connect tree item click
        self._tree.currentItemChanged.connect(self._on_tree_item_changed)

    # -------------------------------------------------------------------------
    # Helpers to create tree items with optional byte-range annotation
    # -------------------------------------------------------------------------

    def _add_field(self, parent: QTreeWidgetItem, name: str, value: str,
                   byte_range: Optional[Tuple[int, int]] = None) -> QTreeWidgetItem:
        """Add a field item to the tree, optionally annotated with (offset, length)."""
        item = QTreeWidgetItem(parent, [name, value])
        if byte_range is not None:
            item.setData(0, BYTE_RANGE_ROLE, byte_range)
        return item

    # -------------------------------------------------------------------------
    # Show packet
    # -------------------------------------------------------------------------

    def show_packet(self, packet: PacketInfo) -> None:
        """Display parsed fields of a packet in the tree."""
        self._tree.clear()
        self._current_packet = packet

        if packet is None:
            self._title.setText("Tag Details")
            return

        self._title.setText(f"Tag Details - #{packet.index} ({packet.type_label})")

        # FLV Header pseudo-tag
        if packet.tag_type == TagType.HEADER:
            self._show_header_details(packet)
            return

        #
        # FLV Tag Header layout (11 bytes):
        #   Byte 0:    TagType
        #   Bytes 1-3: DataSize (uint24)
        #   Bytes 4-6: Timestamp low (uint24)
        #   Byte 7:    TimestampExtended
        #   Bytes 8-10: StreamID (uint24)
        #   Byte 11+:  Tag Data (DataSize bytes)
        #
        TAG_HDR = 11

        header_item = QTreeWidgetItem(self._tree, ["Tag Header", ""])
        header_item.setExpanded(True)
        header_item.setData(0, BYTE_RANGE_ROLE, (0, TAG_HDR))

        self._add_field(header_item, "Index", str(packet.index))
        self._add_field(header_item, "Type",
                        f"{packet.type_label} ({packet.tag_type.value})",
                        byte_range=(0, 1))
        self._add_field(header_item, "Data Size",
                        f"{packet.data_size:,} bytes",
                        byte_range=(1, 3))
        self._add_field(header_item, "Total Size",
                        f"{packet.tag_total_size:,} bytes (incl. header)")
        self._add_field(header_item, "Offset",
                        f"0x{packet.offset:08X} ({packet.offset:,})")
        self._add_field(header_item, "Timestamp",
                        f"{packet.timestamp} ms",
                        byte_range=(4, 4))  # 3 bytes low + 1 byte extended
        self._add_field(header_item, "Stream ID",
                        str(packet.stream_id),
                        byte_range=(8, 3))

        # Timing section (computed values, no specific bytes)
        timing_item = QTreeWidgetItem(self._tree, ["Timing", ""])
        timing_item.setExpanded(True)
        self._add_field(timing_item, "DTS", f"{packet.timestamp} ms",
                        byte_range=(4, 4))
        if packet.composition_time is not None:
            self._add_field(timing_item, "CTS",
                            f"{packet.composition_time} ms",
                            byte_range=(TAG_HDR + 2, 3))
        pts = packet.pts
        if pts is not None:
            self._add_field(timing_item, "PTS", f"{pts} ms")
        dts_sec = packet.timestamp / 1000.0
        self._add_field(timing_item, "DTS (formatted)",
                        f"{int(dts_sec // 60):02d}:{dts_sec % 60:06.3f}")

        # Type-specific section
        if packet.tag_type == TagType.VIDEO:
            self._show_video_details(packet, TAG_HDR)
        elif packet.tag_type == TagType.AUDIO:
            self._show_audio_details(packet, TAG_HDR)
        elif packet.tag_type == TagType.SCRIPT:
            self._show_script_details(packet, TAG_HDR)

    # -------------------------------------------------------------------------
    # FLV file header
    # -------------------------------------------------------------------------

    def _show_header_details(self, packet: PacketInfo) -> None:
        hi = packet.header_info
        if hi is None:
            return

        header_item = QTreeWidgetItem(self._tree, ["FLV File Header", ""])
        header_item.setExpanded(True)
        header_item.setData(0, BYTE_RANGE_ROLE, (0, hi.data_offset))

        self._add_field(header_item, "Signature", "FLV", byte_range=(0, 3))
        self._add_field(header_item, "Version", str(hi.version), byte_range=(3, 1))
        self._add_field(header_item, "Type Flags",
                        f"0x{hi.type_flags_byte:02X} (0b{hi.type_flags_byte:08b})",
                        byte_range=(4, 1))
        self._add_field(header_item, "Has Video",
                        "Yes" if hi.has_video else "No", byte_range=(4, 1))
        self._add_field(header_item, "Has Audio",
                        "Yes" if hi.has_audio else "No", byte_range=(4, 1))
        self._add_field(header_item, "Header Size (DataOffset)",
                        f"{hi.data_offset} bytes", byte_range=(5, 4))

    # -------------------------------------------------------------------------
    # Video tag
    # -------------------------------------------------------------------------

    def _show_video_details(self, packet: PacketInfo, tag_hdr: int) -> None:
        """
        Video tag data layout (after 11-byte tag header):
          Byte +0: FrameType(4b) | CodecID(4b)
          Byte +1: AVCPacketType  (for AVC/HEVC)
          Bytes +2..+4: CompositionTime SI24
          Byte +5...: NALU data
        """
        D = tag_hdr  # offset of tag data start

        video_item = QTreeWidgetItem(self._tree, ["Video", ""])
        video_item.setExpanded(True)
        video_item.setData(0, BYTE_RANGE_ROLE, (D, 1))

        if packet.video_codec is not None:
            codec_desc = {
                VideoCodec.AVC: "H.264/AVC", VideoCodec.HEVC: "H.265/HEVC",
                VideoCodec.VP6: "On2 VP6", VideoCodec.SORENSON_H263: "Sorenson H.263",
                VideoCodec.SCREEN_VIDEO: "Screen Video", VideoCodec.AV1: "AV1",
            }.get(packet.video_codec, packet.video_codec.name)
            self._add_field(video_item, "Codec",
                            f"{codec_desc} (ID: {packet.video_codec.value})",
                            byte_range=(D, 1))

        if packet.frame_type is not None:
            frame_desc = {
                FrameType.KEY: "Keyframe (IDR)", FrameType.INTER: "Inter frame (P/B)",
                FrameType.DISPOSABLE_INTER: "Disposable inter frame",
                FrameType.GENERATED_KEY: "Generated keyframe",
                FrameType.VIDEO_INFO: "Video info/command frame",
            }.get(packet.frame_type, packet.frame_type.name)
            self._add_field(video_item, "Frame Type", frame_desc,
                            byte_range=(D, 1))

        if packet.avc_packet_type is not None:
            pkt_desc = {
                AVCPacketType.SEQUENCE_HEADER: "Sequence Header (SPS/PPS)",
                AVCPacketType.NALU: "NALU (Video Data)",
                AVCPacketType.END_OF_SEQUENCE: "End of Sequence",
            }.get(packet.avc_packet_type, packet.avc_packet_type.name)
            self._add_field(video_item, "AVC Packet Type", pkt_desc,
                            byte_range=(D + 1, 1))

        if packet.composition_time is not None:
            self._add_field(video_item, "Composition Time",
                            f"{packet.composition_time} ms",
                            byte_range=(D + 2, 3))

        # NALU list
        if packet.nalu_list:
            nalu_root = QTreeWidgetItem(self._tree,
                                        ["NALUs", f"{len(packet.nalu_list)} unit(s)"])
            nalu_root.setExpanded(True)

            for nalu in packet.nalu_list:
                # Each NALU: offset_in_tag is relative to tag data start (byte 0 of data)
                # In the full tag bytes (including 11-byte header), it is at D + nalu.offset_in_tag
                nalu_abs = D + nalu.offset_in_tag
                # Total bytes = length_prefix(4) + nalu_data(nalu.size)
                nalu_total = 4 + nalu.size

                nalu_item = QTreeWidgetItem(nalu_root,
                    [f"NALU #{nalu.index}",
                     f"{nalu.nalu_type_name} ({nalu.size:,} bytes)"])
                nalu_item.setExpanded(False)
                nalu_item.setData(0, NALU_DATA_ROLE, nalu)
                # No BYTE_RANGE_ROLE on nalu_item itself — clicking it
                # switches hex view to NALU data, no highlight needed.

                # NALU sub-fields: byte_range is relative to the NALU start
                # (the length prefix byte 0), since hex view shows NALU data when clicked.
                self._add_field(nalu_item, "Type",
                                f"{nalu.nalu_type_name} (type={nalu.nalu_type})",
                                byte_range=(4, 1))  # first byte of NALU payload
                self._add_field(nalu_item, "Length Prefix", f"{nalu.size}",
                                byte_range=(0, 4))
                self._add_field(nalu_item, "Size", f"{nalu.size:,} bytes")
                self._add_field(nalu_item, "Offset in Tag", f"+{nalu.offset_in_tag}")
                self._add_field(nalu_item, "VCL",
                                "Yes" if nalu.is_vcl else "No (non-VCL)")
                if nalu.header_bytes:
                    hex_str = " ".join(f"{b:02X}" for b in nalu.header_bytes)
                    self._add_field(nalu_item, "Header Bytes", hex_str,
                                    byte_range=(4, min(4, nalu.size)))

                # Display parsed bitstream fields (SPS/PPS/VPS)
                if nalu.parsed_fields:
                    parsed_item = QTreeWidgetItem(nalu_item,
                        ["Parsed Fields", f"({len(nalu.parsed_fields)} entries)"])
                    parsed_item.setExpanded(True)
                    self._add_parsed_entries(parsed_item, nalu.parsed_fields)

    # -------------------------------------------------------------------------
    # Audio tag
    # -------------------------------------------------------------------------

    def _show_audio_details(self, packet: PacketInfo, tag_hdr: int) -> None:
        """
        Audio tag data layout:
          Byte +0: SoundFormat(4b)|SoundRate(2b)|SoundSize(1b)|SoundType(1b)
          Byte +1: AACPacketType (for AAC)
          Byte +2...: audio data
        """
        D = tag_hdr

        audio_item = QTreeWidgetItem(self._tree, ["Audio", ""])
        audio_item.setExpanded(True)
        audio_item.setData(0, BYTE_RANGE_ROLE, (D, 1))

        if packet.audio_codec is not None:
            codec_desc = {
                AudioCodec.AAC: "AAC", AudioCodec.MP3: "MP3",
                AudioCodec.LINEAR_PCM: "Linear PCM (platform endian)",
                AudioCodec.LINEAR_PCM_LE: "Linear PCM (little-endian)",
                AudioCodec.ADPCM: "ADPCM", AudioCodec.NELLYMOSER: "Nellymoser",
                AudioCodec.SPEEX: "Speex", AudioCodec.G711_A: "G.711 A-law",
                AudioCodec.G711_MU: "G.711 mu-law",
            }.get(packet.audio_codec, packet.audio_codec.name)
            self._add_field(audio_item, "Codec",
                            f"{codec_desc} (ID: {packet.audio_codec.value})",
                            byte_range=(D, 1))

        if packet.sample_rate is not None:
            self._add_field(audio_item, "Sample Rate",
                            f"{packet.sample_rate} Hz", byte_range=(D, 1))

        if packet.sample_size is not None:
            self._add_field(audio_item, "Sample Size",
                            f"{packet.sample_size}-bit", byte_range=(D, 1))

        if packet.channels is not None:
            ch_desc = "Stereo" if packet.channels == 2 else "Mono"
            self._add_field(audio_item, "Channels",
                            f"{ch_desc} ({packet.channels})", byte_range=(D, 1))

        if packet.aac_packet_type is not None:
            pkt_desc = {
                AACPacketType.SEQUENCE_HEADER: "Sequence Header (AudioSpecificConfig)",
                AACPacketType.RAW: "Raw AAC Frame Data",
            }.get(packet.aac_packet_type, str(packet.aac_packet_type.value))
            self._add_field(audio_item, "AAC Packet Type", pkt_desc,
                            byte_range=(D + 1, 1))

    # -------------------------------------------------------------------------
    # Script tag
    # -------------------------------------------------------------------------

    def _show_script_details(self, packet: PacketInfo, tag_hdr: int) -> None:
        D = tag_hdr

        script_item = QTreeWidgetItem(self._tree, ["Script Data (AMF0)", ""])
        script_item.setExpanded(True)
        script_item.setData(0, BYTE_RANGE_ROLE, (D, packet.data_size))

        if packet.script_name:
            # AMF0 string: type(1) + length(2) + string_data
            name_len = len(packet.script_name.encode("utf-8"))
            self._add_field(script_item, "Event Name", packet.script_name,
                            byte_range=(D, 3 + name_len))

        if packet.script_data:
            metadata_item = QTreeWidgetItem(script_item, ["Metadata", ""])
            metadata_item.setExpanded(True)
            self._add_dict_fields(metadata_item, packet.script_data)

    def _add_dict_fields(self, parent: QTreeWidgetItem, data: Dict[str, Any],
                         max_depth: int = 3) -> None:
        if max_depth <= 0:
            self._add_field(parent, "...", "(truncated)")
            return

        for key, value in data.items():
            if isinstance(value, dict):
                sub_item = QTreeWidgetItem(parent, [str(key), f"({len(value)} fields)"])
                self._add_dict_fields(sub_item, value, max_depth - 1)
            elif isinstance(value, list):
                sub_item = QTreeWidgetItem(parent, [str(key), f"[{len(value)} items]"])
                for i, v in enumerate(value[:20]):
                    self._add_field(sub_item, f"[{i}]", self._format_value(v))
            else:
                self._add_field(parent, str(key), self._format_value(value))

    def _format_value(self, value: Any) -> str:
        if isinstance(value, float):
            if value == int(value):
                return str(int(value))
            return f"{value:.4f}"
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif value is None:
            return "null"
        return str(value)

    def _add_parsed_entries(self, parent: QTreeWidgetItem, entries: list) -> None:
        """
        Render structured parsed field entries into the tree.

        Each entry is either:
          - (key, value) → simple leaf field, always visible
          - (key, value, children) → group node controlled by flag value
            If value is True/truthy: shown as expandable group (collapsed by default)
            If value is False: shown as simple leaf "key: false"
        """
        for entry in entries:
            if not isinstance(entry, (tuple, list)):
                continue

            if len(entry) == 3:
                # Group with children: (key, flag_value, children_list)
                key, flag_value, children = entry
                if flag_value:
                    # Flag is on → show as collapsible group
                    group_item = QTreeWidgetItem(parent,
                        [str(key), f"({len(children)} fields)"])
                    group_item.setExpanded(False)  # Collapsed by default
                    self._add_parsed_entries(group_item, children)
                else:
                    # Flag is off → just show the flag as disabled
                    self._add_field(parent, str(key), "false")

            elif len(entry) == 2:
                # Simple leaf: (key, value)
                key, value = entry
                # Skip internal entries
                if isinstance(key, str) and key.startswith("_"):
                    continue
                self._add_field(parent, str(key), self._format_value(value))

    # -------------------------------------------------------------------------
    # Clear
    # -------------------------------------------------------------------------

    def clear(self) -> None:
        self._tree.clear()
        self._current_packet = None
        self._title.setText("Tag Details")

    # -------------------------------------------------------------------------
    # Tree item selection handler
    # -------------------------------------------------------------------------

    def _on_tree_item_changed(self, current: QTreeWidgetItem,
                              previous: QTreeWidgetItem) -> None:
        if current is None or self._current_packet is None:
            return

        # Check for NALU data (walk up to find it)
        item = current
        nalu_info = None
        while item is not None:
            data = item.data(0, NALU_DATA_ROLE)
            if isinstance(data, NALUInfo):
                nalu_info = data
                break
            item = item.parent()

        if nalu_info is not None:
            self.nalu_selected.emit(nalu_info, self._current_packet)

        # Check for byte range on the clicked item itself
        br = current.data(0, BYTE_RANGE_ROLE)
        if br is not None and isinstance(br, (tuple, list)) and len(br) == 2:
            self.field_byte_range.emit(br[0], br[1])
