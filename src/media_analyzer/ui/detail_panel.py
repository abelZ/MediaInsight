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

        # RTMP protocol packet (identified by "rtmp_message_type" in script_data)
        if packet.script_data and "rtmp_message_type" in packet.script_data:
            self._show_rtmp_packet_details(packet)
            return

        # FLV Header pseudo-tag
        if packet.tag_type == TagType.HEADER:
            self._show_header_details(packet)
            return

        # TS packet (identified by script_data containing "pid" key)
        if packet.script_data and "pid" in packet.script_data:
            self._show_ts_packet_details(packet)
            return

        # MP4 box (identified by script_data containing "box_type" key)
        if packet.script_data and "box_type" in packet.script_data:
            self._show_mp4_box_details(packet)
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
    # TS packet detail
    # -------------------------------------------------------------------------

    def _show_ts_packet_details(self, packet: PacketInfo) -> None:
        """Display TS packet header fields and PES info if present."""
        d = packet.script_data

        # If this is a video PUSI packet with PES data (back-annotated) → show PES-level view
        # PES view shows PES/ES layer details with optional NALU breakdown
        if (d.get("pusi") and packet.tag_type == TagType.VIDEO and
                (d.get("_pes_size") or packet.nalu_list)):
            self._show_pes_details(packet)
            return

        # Standard TS packet view
        self._show_ts_pkt_detail_standard(packet)

    def _show_pes_details(self, packet: PacketInfo) -> None:
        """Display PES-level detail: PES header + ES layer + NALU tree."""
        d = packet.script_data
        pes = d.get("pes", {})

        # --- PES Header section ---
        pes_hdr_item = QTreeWidgetItem(self._tree, ["PES Header", ""])
        pes_hdr_item.setExpanded(True)

        self._add_field(pes_hdr_item, "Stream ID",
                       pes.get("stream_id_hex", f"0x{pes.get('stream_id', 0):02X}"))
        self._add_field(pes_hdr_item, "PES Packet Length",
                       f"{pes.get('pes_packet_length', 0)} bytes")
        self._add_field(pes_hdr_item, "PES Header Data Length",
                       str(pes.get("pes_header_data_length", 0)))

        if pes.get("data_alignment"):
            self._add_field(pes_hdr_item, "Data Alignment", "Yes")
        if pes.get("priority"):
            self._add_field(pes_hdr_item, "PES Priority", "Yes")

        # Timing
        if "pts_ms" in pes:
            pts_ms = pes["pts_ms"]
            pts_raw = pes.get("pts", 0)
            self._add_field(pes_hdr_item, "PTS",
                           f"{pts_ms} ms (raw: {pts_raw})")
        if "dts_ms" in pes:
            dts_ms = pes["dts_ms"]
            dts_raw = pes.get("dts", 0)
            self._add_field(pes_hdr_item, "DTS",
                           f"{dts_ms} ms (raw: {dts_raw})")
        if "cts_ms" in pes:
            self._add_field(pes_hdr_item, "CTS (PTS-DTS)",
                           f"{pes['cts_ms']} ms")

        # PES size info
        pes_size = d.get("_pes_size", 0)
        if pes_size:
            self._add_field(pes_hdr_item, "Total PES Size",
                           f"{pes_size:,} bytes")

        # --- Elementary Stream section ---
        es_item = QTreeWidgetItem(self._tree, ["Elementary Stream", ""])
        es_item.setExpanded(True)

        stream_type = d.get("stream_type", 0)
        stream_type_name = d.get("stream_type_name", "Unknown")
        self._add_field(es_item, "Stream Type",
                       f"0x{stream_type:02X} ({stream_type_name})")

        if packet.video_codec:
            self._add_field(es_item, "Codec", packet.video_codec.name)
        if packet.frame_type:
            self._add_field(es_item, "Frame Type", packet.frame_label)

        # ES size
        es_offset = d.get("_es_offset_in_pes", 0)
        if pes_size and es_offset:
            es_size = pes_size - es_offset
            self._add_field(es_item, "ES Size", f"{es_size:,} bytes")

        # Keyframe indicator
        if pes.get("is_keyframe"):
            self._add_field(es_item, "Keyframe", "Yes (IDR/RAP)")

        # --- NALU list ---
        if packet.nalu_list:
            nalu_root = QTreeWidgetItem(self._tree,
                                        ["NALUs", f"{len(packet.nalu_list)} unit(s)"])
            nalu_root.setExpanded(True)

            for nalu in packet.nalu_list:
                nalu_item = QTreeWidgetItem(nalu_root,
                    [f"NALU #{nalu.index}",
                     f"{nalu.nalu_type_name} ({nalu.size:,} bytes)"])
                nalu_item.setExpanded(False)
                nalu_item.setData(0, NALU_DATA_ROLE, nalu)

                # NALU sub-fields
                self._add_field(nalu_item, "Type",
                                f"{nalu.nalu_type_name} (type={nalu.nalu_type})")
                self._add_field(nalu_item, "Size", f"{nalu.size:,} bytes")
                self._add_field(nalu_item, "Offset in ES", f"+{nalu.offset_in_tag}")
                self._add_field(nalu_item, "VCL",
                                "Yes" if nalu.is_vcl else "No (non-VCL)")
                if nalu.header_bytes:
                    hex_str = " ".join(f"{b:02X}" for b in nalu.header_bytes)
                    self._add_field(nalu_item, "Header Bytes", hex_str)

                # Parsed fields (SPS/PPS/VPS)
                if nalu.parsed_fields:
                    parsed_item = QTreeWidgetItem(nalu_item,
                        ["Parsed Fields", f"({len(nalu.parsed_fields)} entries)"])
                    parsed_item.setExpanded(True)
                    self._add_parsed_entries(parsed_item, nalu.parsed_fields)

        # --- Source TS packet info ---
        ts_info = QTreeWidgetItem(self._tree, ["Source TS Packet", ""])
        ts_info.setExpanded(False)
        self._add_field(ts_info, "File Offset",
                       f"0x{packet.offset:08X} ({packet.offset:,})")
        self._add_field(ts_info, "PID", f"{d.get('pid', 0)} ({d.get('pid_hex', '')})")
        self._add_field(ts_info, "Continuity Counter",
                       str(d.get("continuity_counter", 0)))

    def _show_ts_pkt_detail_standard(self, packet: PacketInfo) -> None:
        """Display standard TS packet header fields (non-PES view)."""
        d = packet.script_data

        # TS Header section
        ts_hdr = QTreeWidgetItem(self._tree, ["TS Packet Header", ""])
        ts_hdr.setExpanded(True)
        ts_hdr.setData(0, BYTE_RANGE_ROLE, (0, 4))

        pid = d.get("pid", 0)
        self._add_field(ts_hdr, "PID", f"{pid} ({d.get('pid_hex', '')})",
                        byte_range=(1, 2))
        self._add_field(ts_hdr, "PUSI (Payload Unit Start)",
                        "Yes" if d.get("pusi") else "No", byte_range=(1, 1))
        self._add_field(ts_hdr, "TEI (Transport Error)",
                        "Yes" if d.get("tei") else "No", byte_range=(1, 1))
        self._add_field(ts_hdr, "Priority",
                        "Yes" if d.get("priority") else "No", byte_range=(1, 1))
        self._add_field(ts_hdr, "Transport Scrambling",
                        str(d.get("tsc", 0)), byte_range=(3, 1))

        afc = d.get("adaptation_field_control", 0)
        afc_desc = {0: "Reserved", 1: "Payload only", 2: "AF only", 3: "AF + Payload"}.get(afc, str(afc))
        self._add_field(ts_hdr, "Adaptation Field Control",
                        f"{afc} ({afc_desc})", byte_range=(3, 1))
        self._add_field(ts_hdr, "Continuity Counter",
                        str(d.get("continuity_counter", 0)), byte_range=(3, 1))
        self._add_field(ts_hdr, "Payload Size",
                        f"{d.get('payload_size', 0)} bytes")
        self._add_field(ts_hdr, "Offset",
                        f"0x{packet.offset:08X} ({packet.offset:,})")

        # Adaptation Field
        af = d.get("adaptation_field")
        if af:
            af_item = QTreeWidgetItem(self._tree, ["Adaptation Field", ""])
            af_item.setExpanded(True)

            flags = af.get("flags", [])
            if flags:
                self._add_field(af_item, "Flags", ", ".join(flags))
            if "pcr_ms" in af:
                self._add_field(af_item, "PCR",
                               f"{af['pcr_ms']:.3f} ms (base={af.get('pcr_base', 0)})")
            if af.get("random_access"):
                self._add_field(af_item, "Random Access Point", "Yes")

        # Stream info
        stream_type = d.get("stream_type")
        if stream_type is not None:
            stream_item = QTreeWidgetItem(self._tree, ["Stream", ""])
            stream_item.setExpanded(True)
            self._add_field(stream_item, "Stream Type",
                           f"0x{stream_type:02X} ({d.get('stream_type_name', '')})")
            self._add_field(stream_item, "PID", f"{pid} ({d.get('pid_hex', '')})")

            category = "Video" if packet.tag_type == TagType.VIDEO else \
                       "Audio" if packet.tag_type == TagType.AUDIO else "Data"
            self._add_field(stream_item, "Category", category)

            if packet.video_codec:
                self._add_field(stream_item, "Codec", packet.video_codec.name)
            if packet.frame_type:
                self._add_field(stream_item, "Frame Type", packet.frame_label)

        # PES Header (only on PUSI=1 packets)
        pes = d.get("pes")
        if pes:
            pes_item = QTreeWidgetItem(self._tree, ["PES Header (Frame Start)", ""])
            pes_item.setExpanded(True)

            self._add_field(pes_item, "Stream ID",
                           pes.get("stream_id_hex", ""))
            self._add_field(pes_item, "PES Packet Length",
                           str(pes.get("pes_packet_length", 0)))
            self._add_field(pes_item, "PES Header Data Length",
                           str(pes.get("pes_header_data_length", 0)))

            if pes.get("data_alignment"):
                self._add_field(pes_item, "Data Alignment", "Yes")
            if pes.get("priority"):
                self._add_field(pes_item, "Priority", "Yes")

            # Timing
            if "pts_ms" in pes:
                pts_ms = pes["pts_ms"]
                pts_raw = pes.get("pts", 0)
                self._add_field(pes_item, "PTS",
                               f"{pts_ms} ms (raw: {pts_raw})")
            if "dts_ms" in pes:
                dts_ms = pes["dts_ms"]
                dts_raw = pes.get("dts", 0)
                self._add_field(pes_item, "DTS",
                               f"{dts_ms} ms (raw: {dts_raw})")
            if "cts_ms" in pes:
                self._add_field(pes_item, "CTS (PTS-DTS)",
                               f"{pes['cts_ms']} ms")

            # Keyframe indicator
            if pes.get("is_keyframe"):
                self._add_field(pes_item, "Keyframe", "Yes (IDR/RAP)")

            # NALU types found
            nalu_types = pes.get("nalu_types")
            if nalu_types:
                self._add_field(pes_item, "NALUs detected",
                               ", ".join(nalu_types))

        # PAT details
        pat = d.get("pat")
        if pat:
            self._show_ts_psi_pat(pat)

        # PMT details
        pmt = d.get("pmt")
        if pmt:
            self._show_ts_psi_pmt(pmt)

    def _show_ts_psi_pat(self, pat: Dict[str, Any]) -> None:
        """Display PAT content."""
        pat_item = QTreeWidgetItem(self._tree, ["PAT Content", ""])
        pat_item.setExpanded(True)

        self._add_field(pat_item, "Transport Stream ID",
                       str(pat.get("transport_stream_id", 0)))
        self._add_field(pat_item, "Version",
                       str(pat.get("version_number", 0)))

        programs = pat.get("programs", [])
        prog_item = QTreeWidgetItem(pat_item,
            ["Programs", f"{len(programs)} program(s)"])
        prog_item.setExpanded(True)
        for prog in programs:
            pn = prog.get("program_number", 0)
            pmt_pid = prog.get("pmt_pid", 0)
            self._add_field(prog_item, f"Program {pn}",
                           f"PMT PID = {pmt_pid} (0x{pmt_pid:04X})")

    def _show_ts_psi_pmt(self, pmt: Dict[str, Any]) -> None:
        """Display PMT content."""
        pmt_item = QTreeWidgetItem(self._tree, ["PMT Content", ""])
        pmt_item.setExpanded(True)

        self._add_field(pmt_item, "Program Number",
                       str(pmt.get("program_number", 0)))
        self._add_field(pmt_item, "Version",
                       str(pmt.get("version_number", 0)))
        pcr_pid = pmt.get("pcr_pid", 0)
        self._add_field(pmt_item, "PCR PID",
                       f"{pcr_pid} (0x{pcr_pid:04X})")

        # Program descriptors
        prog_descs = pmt.get("program_descriptors", [])
        if prog_descs:
            pd_item = QTreeWidgetItem(pmt_item,
                ["Program Descriptors", f"{len(prog_descs)}"])
            pd_item.setExpanded(False)
            self._show_descriptors(pd_item, prog_descs)

        # Streams
        streams = pmt.get("streams", [])
        streams_item = QTreeWidgetItem(pmt_item,
            ["Elementary Streams", f"{len(streams)} stream(s)"])
        streams_item.setExpanded(True)

        for stream in streams:
            st = stream.get("stream_type", 0)
            st_name = stream.get("stream_type_name", "Unknown")
            es_pid = stream.get("elementary_pid", 0)
            is_video = stream.get("is_video", False)
            is_audio = stream.get("is_audio", False)
            category = "Video" if is_video else "Audio" if is_audio else "Data"

            s_item = QTreeWidgetItem(streams_item,
                [f"PID {es_pid} (0x{es_pid:04X})", f"{st_name} [{category}]"])
            s_item.setExpanded(False)

            self._add_field(s_item, "Stream Type", f"0x{st:02X} ({st_name})")
            self._add_field(s_item, "Category", category)

            descs = stream.get("descriptors", [])
            if descs:
                d_item = QTreeWidgetItem(s_item,
                    ["Descriptors", f"{len(descs)}"])
                d_item.setExpanded(True)
                self._show_descriptors(d_item, descs)

    # -------------------------------------------------------------------------
    # MP4 box detail
    # -------------------------------------------------------------------------

    def _show_mp4_box_details(self, packet: PacketInfo) -> None:
        """Display MP4 box details."""
        d = packet.script_data
        box_type = d.get("box_type", "????")
        desc = d.get("description", "")

        # Box header section
        box_hdr = QTreeWidgetItem(self._tree, ["Box Header", ""])
        box_hdr.setExpanded(True)

        title = f"{box_type}"
        if desc:
            title += f" ({desc})"
        self._add_field(box_hdr, "Type", title,
                       byte_range=(4, 4))
        self._add_field(box_hdr, "Total Size",
                       f"{packet.tag_total_size:,} bytes",
                       byte_range=(0, 4))
        self._add_field(box_hdr, "Payload Size",
                       f"{packet.data_size:,} bytes")
        self._add_field(box_hdr, "Offset",
                       f"0x{packet.offset:08X} ({packet.offset:,})")
        header_size = d.get("header_size", 8)
        self._add_field(box_hdr, "Header Size",
                       f"{header_size} bytes")

        depth = d.get("depth", 0)
        self._add_field(box_hdr, "Depth", str(depth))

        if d.get("is_container"):
            self._add_field(box_hdr, "Container", "Yes (has child boxes)")

        # Parsed fields (if available)
        fields = d.get("fields")
        if fields:
            fields_item = QTreeWidgetItem(self._tree, ["Fields", ""])
            fields_item.setExpanded(True)

            for key, value in fields.items():
                if isinstance(value, dict):
                    sub_item = QTreeWidgetItem(fields_item,
                        [str(key), f"({len(value)} fields)"])
                    sub_item.setExpanded(False)
                    for sk, sv in value.items():
                        if isinstance(sv, dict):
                            # Nested dict (rare)
                            nested = QTreeWidgetItem(sub_item,
                                [str(sk), f"({len(sv)} fields)"])
                            nested.setExpanded(False)
                            for nk, nv in sv.items():
                                self._add_field(nested, str(nk), self._format_value(nv))
                        else:
                            self._add_field(sub_item, str(sk), self._format_value(sv))
                elif isinstance(value, list):
                    if len(value) > 0 and isinstance(value[0], dict):
                        # List of dicts — each dict is a collapsible node
                        sub_item = QTreeWidgetItem(fields_item,
                            [str(key), f"[{len(value)} entries]"])
                        sub_item.setExpanded(len(value) <= 3)
                        for i, entry in enumerate(value[:50]):
                            if isinstance(entry, dict):
                                # Each entry is a tree node
                                entry_label = f"[{i}]"
                                # Try to give a meaningful summary
                                summary_keys = [k for k in ("profile_idc", "profile_name",
                                    "width", "height", "entropy_coding_mode") if k in entry]
                                if summary_keys:
                                    summary = ", ".join(f"{k}={entry[k]}" for k in summary_keys[:2])
                                    entry_label = f"[{i}] ({summary})"
                                entry_item = QTreeWidgetItem(sub_item,
                                    [entry_label, f"{len(entry)} fields"])
                                entry_item.setExpanded(False)
                                for ek, ev in entry.items():
                                    self._add_field(entry_item, str(ek), self._format_value(ev))
                            else:
                                self._add_field(sub_item, f"[{i}]", str(entry))
                    elif len(value) <= 20:
                        # Short list (e.g. compatible_brands, sync_samples)
                        sub_item = QTreeWidgetItem(fields_item,
                            [str(key), f"[{len(value)} items]"])
                        sub_item.setExpanded(True)
                        for i, v in enumerate(value):
                            self._add_field(sub_item, f"[{i}]", str(v))
                    else:
                        # Long list — show count + first few
                        sub_item = QTreeWidgetItem(fields_item,
                            [str(key), f"[{len(value)} items]"])
                        sub_item.setExpanded(False)
                        for i, v in enumerate(value[:20]):
                            self._add_field(sub_item, f"[{i}]", str(v))
                        if len(value) > 20:
                            self._add_field(sub_item, "...",
                                          f"({len(value) - 20} more)")
                else:
                    self._add_field(fields_item, str(key), self._format_value(value))

    def _show_rtmp_packet_details(self, packet: PacketInfo) -> None:
        """Show RTMP protocol packet details."""
        sd = packet.script_data
        if not sd:
            return

        msg_type = sd.get("rtmp_message_type", "Unknown")
        self._title.setText(f"RTMP - #{packet.index} ({msg_type})")

        # RTMP Message Header
        header_item = QTreeWidgetItem(self._tree, ["RTMP Message", ""])
        header_item.setExpanded(True)

        self._add_field(header_item, "Message Type", msg_type)
        self._add_field(header_item, "Type ID", str(sd.get("rtmp_message_type_id", "")))
        self._add_field(header_item, "Direction", sd.get("direction", ""))
        self._add_field(header_item, "Chunk Stream ID", str(sd.get("csid", "")))
        self._add_field(header_item, "Message Stream ID", str(sd.get("msg_stream_id", "")))
        self._add_field(header_item, "Timestamp", f"{packet.timestamp} ms")
        self._add_field(header_item, "Payload Size", f"{packet.data_size} bytes")

        # Handshake details
        if sd.get("handshake_phase"):
            hs_item = QTreeWidgetItem(self._tree, ["Handshake", ""])
            hs_item.setExpanded(True)
            self._add_field(hs_item, "Phase", sd["handshake_phase"])
            if "version" in sd:
                self._add_field(hs_item, "Version", str(sd["version"]))
            if "c1_time" in sd:
                self._add_field(hs_item, "C1 Time", str(sd["c1_time"]))
            if "s1_time" in sd:
                self._add_field(hs_item, "S1 Time", str(sd["s1_time"]))

        # Protocol control details
        if "chunk_size" in sd:
            ctrl_item = QTreeWidgetItem(self._tree, ["Protocol Control", ""])
            ctrl_item.setExpanded(True)
            self._add_field(ctrl_item, "New Chunk Size", str(sd["chunk_size"]))

        if "window_ack_size" in sd:
            ctrl_item = QTreeWidgetItem(self._tree, ["Protocol Control", ""])
            ctrl_item.setExpanded(True)
            self._add_field(ctrl_item, "Window Ack Size", str(sd["window_ack_size"]))

        if "window_size" in sd:
            ctrl_item = QTreeWidgetItem(self._tree, ["Set Peer Bandwidth", ""])
            ctrl_item.setExpanded(True)
            self._add_field(ctrl_item, "Window Size", str(sd["window_size"]))
            limit_types = {0: "Hard", 1: "Soft", 2: "Dynamic"}
            self._add_field(ctrl_item, "Limit Type",
                          limit_types.get(sd.get("limit_type", -1), "Unknown"))

        if "event_type" in sd:
            uc_item = QTreeWidgetItem(self._tree, ["User Control", ""])
            uc_item.setExpanded(True)
            self._add_field(uc_item, "Event", sd["event_type"])
            if "event_stream_id" in sd:
                self._add_field(uc_item, "Stream ID", str(sd["event_stream_id"]))

        if "sequence_number" in sd:
            self._add_field(header_item, "Sequence Number", str(sd["sequence_number"]))

        # Command details
        if "command_name" in sd:
            cmd_item = QTreeWidgetItem(self._tree, ["Command", ""])
            cmd_item.setExpanded(True)
            self._add_field(cmd_item, "Name", sd["command_name"])
            if "transaction_id" in sd:
                self._add_field(cmd_item, "Transaction ID", str(sd["transaction_id"]))
            # AMF objects
            if "amf_objects" in sd:
                for i, obj in enumerate(sd["amf_objects"]):
                    if isinstance(obj, dict):
                        obj_item = QTreeWidgetItem(cmd_item,
                            [f"Object [{i}]", f"{{{len(obj)} properties}}"])
                        obj_item.setExpanded(i == 0)
                        for key, value in obj.items():
                            self._add_field(obj_item, str(key), self._format_value(value))
                    elif obj is None:
                        self._add_field(cmd_item, f"Arg [{i}]", "null")
                    else:
                        self._add_field(cmd_item, f"Arg [{i}]", str(obj))

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

        if packet.script_amf_values:
            # FLV AMF: display all AMF values recursively
            for i, value in enumerate(packet.script_amf_values):
                amf_type = self._get_amf_type_name(value)
                if i == 0 and isinstance(value, str):
                    item = QTreeWidgetItem(script_item,
                        [f"[{i}] Event Name", f'"{value}"'])
                    item.setData(0, BYTE_RANGE_ROLE, (D, 3 + len(value.encode("utf-8"))))
                else:
                    label = f"[{i}] {amf_type}"
                    self._add_amf_value(script_item, label, value)
        elif packet.script_name:
            # Fallback
            name_len = len(packet.script_name.encode("utf-8"))
            self._add_field(script_item, "Event Name", packet.script_name,
                            byte_range=(D, 3 + name_len))
            if packet.script_data:
                metadata_item = QTreeWidgetItem(script_item, ["Metadata", ""])
                metadata_item.setExpanded(True)
                self._add_amf_value_recursive(metadata_item, packet.script_data)

    def _show_descriptors(self, parent: QTreeWidgetItem,
                           descriptors: list) -> None:
        """Display MPEG-TS descriptors in the tree."""
        for desc in descriptors:
            tag = desc.get("tag", 0)
            tag_name = desc.get("tag_name", f"0x{tag:02X}")
            length = desc.get("length", 0)

            desc_item = QTreeWidgetItem(parent,
                [f"0x{tag:02X} {tag_name}", f"{length} bytes"])
            desc_item.setExpanded(False)

            # Show parsed fields
            for key, value in desc.items():
                if key in ("tag", "tag_name", "length", "raw_hex"):
                    continue
                self._add_field(desc_item, key, self._format_value(value))

            # Show raw hex
            if "raw_hex" in desc:
                self._add_field(desc_item, "Raw Data", desc["raw_hex"])

    def _add_amf_value(self, parent: QTreeWidgetItem, label: str, value: Any) -> None:
        """Add an AMF value to the tree with proper type display."""
        if isinstance(value, dict):
            item = QTreeWidgetItem(parent,
                [label, f"Object ({len(value)} properties)"])
            item.setExpanded(True)
            self._add_amf_value_recursive(item, value)
        elif isinstance(value, list):
            item = QTreeWidgetItem(parent,
                [label, f"Array [{len(value)} items]"])
            item.setExpanded(len(value) <= 10)
            for i, v in enumerate(value):
                sub_type = self._get_amf_type_name(v)
                self._add_amf_value(item, f"[{i}] {sub_type}", v)
        elif isinstance(value, str):
            self._add_field(parent, label, f'"{value}"')
        elif isinstance(value, bool):
            self._add_field(parent, label, "true" if value else "false")
        elif isinstance(value, float):
            # AMF Number — show integer if whole, else float
            if value == int(value) and abs(value) < 2**53:
                self._add_field(parent, label, str(int(value)))
            else:
                self._add_field(parent, label, f"{value:.6g}")
        elif value is None:
            self._add_field(parent, label, "null")
        else:
            self._add_field(parent, label, str(value))

    def _add_amf_value_recursive(self, parent: QTreeWidgetItem,
                                  data: Dict[str, Any]) -> None:
        """Recursively add AMF object/dict fields to tree."""
        for key, value in data.items():
            amf_type = self._get_amf_type_name(value)
            if isinstance(value, dict):
                item = QTreeWidgetItem(parent,
                    [str(key), f"Object ({len(value)} properties)"])
                item.setExpanded(False)
                self._add_amf_value_recursive(item, value)
            elif isinstance(value, list):
                item = QTreeWidgetItem(parent,
                    [str(key), f"Array [{len(value)} items]"])
                item.setExpanded(False)
                for i, v in enumerate(value):
                    sub_type = self._get_amf_type_name(v)
                    self._add_amf_value(item, f"[{i}] {sub_type}", v)
            elif isinstance(value, str):
                self._add_field(parent, str(key), f'"{value}"')
            elif isinstance(value, bool):
                self._add_field(parent, str(key), "true" if value else "false")
            elif isinstance(value, float):
                if value == int(value) and abs(value) < 2**53:
                    self._add_field(parent, str(key), str(int(value)))
                else:
                    self._add_field(parent, str(key), f"{value:.6g}")
            elif value is None:
                self._add_field(parent, str(key), "null")
            else:
                self._add_field(parent, str(key), str(value))

    @staticmethod
    def _get_amf_type_name(value: Any) -> str:
        """Get AMF0 type name for display."""
        if isinstance(value, str):
            return "String"
        elif isinstance(value, bool):
            return "Boolean"
        elif isinstance(value, (int, float)):
            return "Number"
        elif isinstance(value, dict):
            return "Object"
        elif isinstance(value, list):
            return "StrictArray"
        elif value is None:
            return "Null"
        return "Unknown"

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
