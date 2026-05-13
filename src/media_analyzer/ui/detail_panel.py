"""Detail panel widget - shows parsed fields of selected packet."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLabel
from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt, Signal
from typing import Any, Dict, Optional

from media_analyzer.core.models import (
    PacketInfo, TagType, FrameType, VideoCodec, AudioCodec,
    AVCPacketType, AACPacketType, NALUInfo, FLVHeaderInfo,
)

# Custom role to store NALUInfo on tree items
NALU_DATA_ROLE = Qt.ItemDataRole.UserRole + 1


class DetailPanelWidget(QWidget):
    """
    Tree-based detail panel showing parsed fields of selected FLV tag.
    Clicking a NALU item emits nalu_selected with the NALUInfo.
    """

    # Emitted when a NALU tree item is clicked; carries (NALUInfo, PacketInfo)
    nalu_selected = Signal(object, object)

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

        # Connect tree item click to NALU selection
        self._tree.currentItemChanged.connect(self._on_tree_item_changed)

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

        # Tag Header section
        header_item = QTreeWidgetItem(self._tree, ["Tag Header", ""])
        header_item.setExpanded(True)
        self._add_field(header_item, "Index", str(packet.index))
        self._add_field(header_item, "Type", f"{packet.type_label} ({packet.tag_type.value})")
        self._add_field(header_item, "Data Size", f"{packet.data_size:,} bytes")
        self._add_field(header_item, "Total Size", f"{packet.tag_total_size:,} bytes (incl. header)")
        self._add_field(header_item, "Offset", f"0x{packet.offset:08X} ({packet.offset:,})")
        self._add_field(header_item, "Stream ID", str(packet.stream_id))

        # Timing section
        timing_item = QTreeWidgetItem(self._tree, ["Timing", ""])
        timing_item.setExpanded(True)
        self._add_field(timing_item, "Timestamp (DTS)", f"{packet.timestamp} ms")
        if packet.composition_time is not None:
            self._add_field(timing_item, "Composition Time (CTS)", f"{packet.composition_time} ms")
        pts = packet.pts
        if pts is not None:
            self._add_field(timing_item, "PTS", f"{pts} ms")
        # Format as time
        dts_sec = packet.timestamp / 1000.0
        self._add_field(timing_item, "DTS (formatted)",
                       f"{int(dts_sec // 60):02d}:{dts_sec % 60:06.3f}")

        # Type-specific section
        if packet.tag_type == TagType.VIDEO:
            self._show_video_details(packet)
        elif packet.tag_type == TagType.AUDIO:
            self._show_audio_details(packet)
        elif packet.tag_type == TagType.SCRIPT:
            self._show_script_details(packet)

    def _show_header_details(self, packet: PacketInfo) -> None:
        """Show FLV file header details."""
        hi = packet.header_info
        if hi is None:
            return

        header_item = QTreeWidgetItem(self._tree, ["FLV File Header", ""])
        header_item.setExpanded(True)

        self._add_field(header_item, "Signature", "FLV")
        self._add_field(header_item, "Version", str(hi.version))
        self._add_field(header_item, "Type Flags", f"0x{hi.type_flags_byte:02X} (0b{hi.type_flags_byte:08b})")
        self._add_field(header_item, "Has Video", "Yes" if hi.has_video else "No")
        self._add_field(header_item, "Has Audio", "Yes" if hi.has_audio else "No")
        self._add_field(header_item, "Header Size (DataOffset)", f"{hi.data_offset} bytes")
        self._add_field(header_item, "Offset", "0x00000000 (0)")

    def _show_video_details(self, packet: PacketInfo) -> None:
        """Show video-specific fields."""
        video_item = QTreeWidgetItem(self._tree, ["Video", ""])
        video_item.setExpanded(True)

        if packet.video_codec is not None:
            codec_name = packet.video_codec.name
            codec_desc = {
                VideoCodec.AVC: "H.264/AVC",
                VideoCodec.HEVC: "H.265/HEVC",
                VideoCodec.VP6: "On2 VP6",
                VideoCodec.SORENSON_H263: "Sorenson H.263",
                VideoCodec.SCREEN_VIDEO: "Screen Video",
                VideoCodec.AV1: "AV1",
            }.get(packet.video_codec, codec_name)
            self._add_field(video_item, "Codec",
                           f"{codec_desc} (ID: {packet.video_codec.value})")

        if packet.frame_type is not None:
            frame_desc = {
                FrameType.KEY: "Keyframe (IDR)",
                FrameType.INTER: "Inter frame (P/B)",
                FrameType.DISPOSABLE_INTER: "Disposable inter frame",
                FrameType.GENERATED_KEY: "Generated keyframe",
                FrameType.VIDEO_INFO: "Video info/command frame",
            }.get(packet.frame_type, packet.frame_type.name)
            self._add_field(video_item, "Frame Type", frame_desc)

        if packet.avc_packet_type is not None:
            pkt_desc = {
                AVCPacketType.SEQUENCE_HEADER: "Sequence Header (SPS/PPS)",
                AVCPacketType.NALU: "NALU (Video Data)",
                AVCPacketType.END_OF_SEQUENCE: "End of Sequence",
            }.get(packet.avc_packet_type, packet.avc_packet_type.name)
            self._add_field(video_item, "AVC Packet Type", pkt_desc)

        # NALU list
        if packet.nalu_list:
            nalu_root = QTreeWidgetItem(self._tree,
                                         ["NALUs", f"{len(packet.nalu_list)} unit(s)"])
            nalu_root.setExpanded(True)

            for nalu in packet.nalu_list:
                nalu_item = QTreeWidgetItem(nalu_root,
                    [f"NALU #{nalu.index}", f"{nalu.nalu_type_name} ({nalu.size:,} bytes)"])
                nalu_item.setExpanded(False)
                # Store NALUInfo so clicking this item can update hex view
                nalu_item.setData(0, NALU_DATA_ROLE, nalu)

                self._add_field(nalu_item, "Type", f"{nalu.nalu_type_name} (type={nalu.nalu_type})")
                self._add_field(nalu_item, "Size", f"{nalu.size:,} bytes")
                self._add_field(nalu_item, "Offset in Tag", f"+{nalu.offset_in_tag}")
                self._add_field(nalu_item, "VCL", "Yes" if nalu.is_vcl else "No (non-VCL)")
                if nalu.header_bytes:
                    hex_str = " ".join(f"{b:02X}" for b in nalu.header_bytes)
                    self._add_field(nalu_item, "Header Bytes", hex_str)

    def _show_audio_details(self, packet: PacketInfo) -> None:
        """Show audio-specific fields."""
        audio_item = QTreeWidgetItem(self._tree, ["Audio", ""])
        audio_item.setExpanded(True)

        if packet.audio_codec is not None:
            codec_desc = {
                AudioCodec.AAC: "AAC",
                AudioCodec.MP3: "MP3",
                AudioCodec.LINEAR_PCM: "Linear PCM (platform endian)",
                AudioCodec.LINEAR_PCM_LE: "Linear PCM (little-endian)",
                AudioCodec.ADPCM: "ADPCM",
                AudioCodec.NELLYMOSER: "Nellymoser",
                AudioCodec.SPEEX: "Speex",
                AudioCodec.G711_A: "G.711 A-law",
                AudioCodec.G711_MU: "G.711 mu-law",
            }.get(packet.audio_codec, packet.audio_codec.name)
            self._add_field(audio_item, "Codec",
                           f"{codec_desc} (ID: {packet.audio_codec.value})")

        if packet.sample_rate is not None:
            self._add_field(audio_item, "Sample Rate", f"{packet.sample_rate} Hz")

        if packet.sample_size is not None:
            self._add_field(audio_item, "Sample Size", f"{packet.sample_size}-bit")

        if packet.channels is not None:
            ch_desc = "Stereo" if packet.channels == 2 else "Mono"
            self._add_field(audio_item, "Channels", f"{ch_desc} ({packet.channels})")

        if packet.aac_packet_type is not None:
            pkt_desc = {
                AACPacketType.SEQUENCE_HEADER: "Sequence Header (AudioSpecificConfig)",
                AACPacketType.RAW: "Raw AAC Frame Data",
            }.get(packet.aac_packet_type, str(packet.aac_packet_type.value))
            self._add_field(audio_item, "AAC Packet Type", pkt_desc)

    def _show_script_details(self, packet: PacketInfo) -> None:
        """Show script/metadata fields."""
        script_item = QTreeWidgetItem(self._tree, ["Script Data (AMF0)", ""])
        script_item.setExpanded(True)

        if packet.script_name:
            self._add_field(script_item, "Event Name", packet.script_name)

        if packet.script_data:
            metadata_item = QTreeWidgetItem(script_item, ["Metadata", ""])
            metadata_item.setExpanded(True)
            self._add_dict_fields(metadata_item, packet.script_data)

    def _add_dict_fields(self, parent: QTreeWidgetItem, data: Dict[str, Any],
                         max_depth: int = 3) -> None:
        """Recursively add dictionary fields to tree."""
        if max_depth <= 0:
            self._add_field(parent, "...", "(truncated)")
            return

        for key, value in data.items():
            if isinstance(value, dict):
                sub_item = QTreeWidgetItem(parent, [str(key), f"({len(value)} fields)"])
                self._add_dict_fields(sub_item, value, max_depth - 1)
            elif isinstance(value, list):
                sub_item = QTreeWidgetItem(parent, [str(key), f"[{len(value)} items]"])
                for i, v in enumerate(value[:20]):  # Limit to 20 items
                    self._add_field(sub_item, f"[{i}]", self._format_value(v))
            else:
                self._add_field(parent, str(key), self._format_value(value))

    def _add_field(self, parent: QTreeWidgetItem, name: str, value: str) -> QTreeWidgetItem:
        """Add a field item to the tree."""
        item = QTreeWidgetItem(parent, [name, value])
        return item

    def _format_value(self, value: Any) -> str:
        """Format a value for display."""
        if isinstance(value, float):
            # Show reasonable precision
            if value == int(value):
                return str(int(value))
            return f"{value:.4f}"
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif value is None:
            return "null"
        return str(value)

    def clear(self) -> None:
        """Clear the detail panel."""
        self._tree.clear()
        self._current_packet = None
        self._title.setText("Tag Details")

    def _on_tree_item_changed(self, current: QTreeWidgetItem, previous: QTreeWidgetItem) -> None:
        """Handle tree item selection — emit nalu_selected if a NALU item is clicked."""
        if current is None or self._current_packet is None:
            return

        # Walk up the tree to find the item (or its parent) carrying NALUInfo
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
