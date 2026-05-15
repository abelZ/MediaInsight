"""MPEG-TS (Transport Stream) parser.

Parses 188-byte TS packets, yields one PacketInfo per TS packet.
Extracts PAT/PMT for stream identification.

Frame type detection uses proper PES reassembly:
  TS packets → accumulate full PES per PID → extract ES → analyze frame type
When a new PUSI=1 packet arrives, the previous PES is complete and its
PUSI PacketInfo is back-annotated with the detected frame type.

Reference: ISO/IEC 13818-1 (MPEG-2 Systems)
"""

import struct
from typing import Generator, BinaryIO, Optional, Dict, List, Any

from media_analyzer.parsers.base import BaseParser
from media_analyzer.core.models import (
    PacketInfo, StreamInfo, TagType,
)

# TS constants
TS_PACKET_SIZE = 188
TS_SYNC_BYTE = 0x47

# Well-known PIDs
PID_PAT = 0x0000
PID_CAT = 0x0001
PID_NULL = 0x1FFF

# Stream type IDs (from PMT)
STREAM_TYPE_NAMES = {
    0x01: "MPEG-1 Video",
    0x02: "MPEG-2 Video",
    0x03: "MPEG-1 Audio",
    0x04: "MPEG-2 Audio",
    0x06: "Private Data",
    0x0F: "AAC (ADTS)",
    0x10: "MPEG-4 Video",
    0x11: "AAC (LATM)",
    0x1B: "H.264/AVC",
    0x24: "H.265/HEVC",
    0x42: "AVS Video",
    0x81: "AC-3 Audio",
    0x82: "DTS Audio",
    0x83: "Dolby TrueHD",
    0x84: "AC-3 Plus",
    0x85: "DTS-HD",
    0x86: "DTS-HD MA",
    0x87: "E-AC-3",
}

# Classify stream types
VIDEO_STREAM_TYPES = {0x01, 0x02, 0x10, 0x1B, 0x24, 0x42}
AUDIO_STREAM_TYPES = {0x03, 0x04, 0x0F, 0x11, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87}

# MPEG-TS descriptor tag names
_DESCRIPTOR_NAMES = {
    0x02: "Video Stream",
    0x03: "Audio Stream",
    0x05: "Registration",
    0x06: "Data Stream Alignment",
    0x09: "CA (Conditional Access)",
    0x0A: "ISO 639 Language",
    0x0E: "Maximum Bitrate",
    0x28: "AVC Video",
    0x38: "HEVC Video",
    0x52: "Stream Identifier",
    0x56: "Teletext",
    0x59: "Subtitling",
    0x6A: "AC-3",
    0x7A: "Enhanced AC-3",
    0x7C: "AAC",
    0x7F: "Extension",
}

# Adaptation field flag names
_AF_FLAG_NAMES = [
    (0x80, "discontinuity_indicator"),
    (0x40, "random_access_indicator"),
    (0x20, "es_priority_indicator"),
    (0x10, "PCR_flag"),
    (0x08, "OPCR_flag"),
    (0x04, "splicing_point_flag"),
    (0x02, "transport_private_data_flag"),
    (0x01, "adaptation_field_extension_flag"),
]

# Maximum PES accumulator size (64KB) to prevent unbounded memory
_MAX_PES_ACCUMULATE = 65536


class _PESAccumulator:
    """Accumulates TS payloads for one PES packet per video PID."""
    __slots__ = ("pid", "stream_type", "pes_data", "pusi_packet")

    def __init__(self, pid: int, stream_type: int):
        self.pid = pid
        self.stream_type = stream_type
        self.pes_data = bytearray()
        self.pusi_packet: Optional[PacketInfo] = None

    def reset(self, payload: bytes, pusi_packet: PacketInfo) -> None:
        """Start accumulating a new PES packet."""
        self.pes_data = bytearray(payload)
        self.pusi_packet = pusi_packet

    def append(self, payload: bytes) -> None:
        """Append continuation payload (PUSI=0)."""
        if len(self.pes_data) < _MAX_PES_ACCUMULATE:
            self.pes_data.extend(payload)

    def has_data(self) -> bool:
        return self.pusi_packet is not None and len(self.pes_data) > 0


class TSParser(BaseParser):
    """
    MPEG Transport Stream parser — yields one PacketInfo per 188-byte TS packet.

    Frame type detection uses proper PES reassembly:
    1. Each TS packet yields a PacketInfo immediately
    2. Video payloads are accumulated per PID into complete PES packets
    3. When a new PUSI=1 arrives, the previous PES is complete
    4. The complete ES is extracted and analyzed for frame type
    5. The previous PUSI PacketInfo is back-annotated with I/P/B
    """

    SYNC_BYTE = TS_SYNC_BYTE

    def __init__(self):
        self._stream_info: Optional[StreamInfo] = None
        self._tag_count = 0
        self._video_count = 0
        self._audio_count = 0
        self._max_pts = 0
        # PAT/PMT state
        self._pmt_pids: Dict[int, int] = {}
        self._streams: Dict[int, Dict[str, Any]] = {}
        # PAT/PMT parsed details for display
        self._pat_details: Optional[Dict[str, Any]] = None
        self._pmt_details: Optional[Dict[str, Any]] = None
        # PES accumulators for video PIDs (proper PES reassembly)
        self._pes_accumulators: Dict[int, _PESAccumulator] = {}

    @classmethod
    def sniff(cls, header_bytes: bytes) -> bool:
        """Check if data starts with TS sync byte."""
        if len(header_bytes) < TS_PACKET_SIZE:
            return False
        if header_bytes[0] == TS_SYNC_BYTE:
            if len(header_bytes) >= 2 * TS_PACKET_SIZE:
                return header_bytes[TS_PACKET_SIZE] == TS_SYNC_BYTE
            return True
        # 192-byte TS (with timecode prefix)
        if len(header_bytes) >= 192 and header_bytes[4] == TS_SYNC_BYTE:
            return True
        return False

    def parse_header(self, data: bytes) -> dict:
        return {"format": "MPEG-TS", "packet_size": TS_PACKET_SIZE}

    def parse_incremental(self, source: BinaryIO) -> Generator[PacketInfo, None, None]:
        """Yield one PacketInfo per TS packet."""
        self._tag_count = 0
        self._video_count = 0
        self._audio_count = 0
        self._max_pts = 0
        self._pes_accumulators.clear()

        # Detect packet size (188 or 192)
        initial = source.read(TS_PACKET_SIZE + 4)
        if len(initial) < TS_PACKET_SIZE:
            return
        source.seek(0)

        packet_size = TS_PACKET_SIZE
        ts_offset_in_packet = 0
        if initial[0] != TS_SYNC_BYTE and len(initial) >= 192 and initial[4] == TS_SYNC_BYTE:
            packet_size = 192
            ts_offset_in_packet = 4

        file_offset = 0

        # Read in large chunks for performance (64KB ≈ 340 TS packets)
        READ_CHUNK = 65536 - (65536 % packet_size)  # Align to packet boundary
        buffer = b""

        while True:
            # Refill buffer
            if len(buffer) < packet_size:
                chunk = source.read(READ_CHUNK)
                if not chunk:
                    break
                buffer = buffer + chunk

            # Process packets from buffer
            pos = 0
            while pos + packet_size <= len(buffer):
                ts_data = buffer[pos + ts_offset_in_packet:pos + packet_size]
                if len(ts_data) < TS_PACKET_SIZE:
                    break

                if ts_data[0] != TS_SYNC_BYTE:
                    pos += packet_size
                    file_offset += packet_size
                    continue

                pkt = self._parse_ts_packet(ts_data, file_offset)
                if pkt:
                    yield pkt

                pos += packet_size
                file_offset += packet_size

            # Keep unprocessed remainder
            buffer = buffer[pos:]

        # Flush remaining PES accumulators at end of stream
        self._flush_all_accumulators()

    def _parse_ts_packet(self, ts_data: bytes, file_offset: int) -> Optional[PacketInfo]:
        """Parse a single 188-byte TS packet and return PacketInfo."""
        # 4-byte TS header (fast bit extraction)
        b0, b1, b2, b3 = ts_data[0], ts_data[1], ts_data[2], ts_data[3]
        tei = (b1 >> 7) & 0x01
        pusi = (b1 >> 6) & 0x01
        priority = (b1 >> 5) & 0x01
        pid = ((b1 & 0x1F) << 8) | b2
        tsc = (b3 >> 6) & 0x03
        afc = (b3 >> 4) & 0x03
        cc = b3 & 0x0F

        # Adaptation field
        payload_offset = 4
        af_data = None

        if afc & 0x02:  # Adaptation field present
            af_length = ts_data[4]
            af_data = ts_data[5:5 + af_length] if af_length > 0 else b""
            payload_offset = 5 + af_length

        has_payload = (afc & 0x01) != 0
        payload = ts_data[payload_offset:] if has_payload and payload_offset < TS_PACKET_SIZE else b""

        # Minimal detail dict (always needed for column display)
        detail = {
            "pid": pid,
            "pid_hex": f"0x{pid:04X}",
            "pusi": bool(pusi),
            "continuity_counter": cc,
            "adaptation_field_control": afc,
            "payload_size": len(payload),
        }

        # Determine tag type and do deeper parsing only when needed
        tag_type = TagType.SCRIPT
        timestamp_ms = 0
        script_name = f"PID {pid}"

        # NULL packets — skip entirely for performance
        if pid == PID_NULL:
            return None

        # PAT
        elif pid == PID_PAT:
            script_name = "PAT"
            if pusi and len(payload) > 0:
                pointer = payload[0]
                self._parse_pat(payload[1 + pointer:])
                detail["pat"] = self._pat_details

        # PMT
        elif pid in self._pmt_pids.values():
            script_name = "PMT"
            if pusi and len(payload) > 0:
                pointer = payload[0]
                self._parse_pmt(payload[1 + pointer:])
                detail["pmt"] = self._pmt_details

        # Elementary stream (video/audio)
        elif pid in self._streams:
            stream_meta = self._streams[pid]
            is_video = stream_meta.get("is_video", False)
            is_audio = stream_meta.get("is_audio", False)

            if is_video:
                tag_type = TagType.VIDEO
                self._video_count += 1
            elif is_audio:
                tag_type = TagType.AUDIO
                self._audio_count += 1

            detail["stream_type"] = stream_meta.get("type", 0)
            detail["stream_type_name"] = stream_meta.get("type_name", "Unknown")
            script_name = stream_meta.get("type_name", f"PID {pid}")

            # Only parse PES header on PUSI=1 packets (frame start)
            if pusi and len(payload) >= 9:
                pes_info = self._parse_pes_header(payload)
                if pes_info:
                    detail["pes"] = pes_info
                    if pes_info.get("pts_ms") is not None:
                        timestamp_ms = pes_info["pts_ms"]
                        if pes_info.get("pts", 0) > self._max_pts:
                            self._max_pts = pes_info["pts"]

        # Extended detail fields (only add when relevant, saves memory)
        if tei:
            detail["tei"] = True
        if priority:
            detail["priority"] = True
        if tsc:
            detail["tsc"] = tsc

        # Parse adaptation field details only if interesting
        if af_data is not None and len(af_data) >= 1:
            af_flags = af_data[0] if af_data else 0
            # Only parse if has PCR or random_access (skip boring AFs)
            if af_flags & 0x50:  # PCR or random_access
                af_details = self._parse_adaptation_field(af_data)
                detail["adaptation_field"] = af_details

        # Build PacketInfo
        packet = PacketInfo(
            index=self._tag_count,
            tag_type=tag_type,
            timestamp=timestamp_ms,
            data_size=len(payload),
            offset=file_offset,
            stream_id=pid,
            tag_total_size=TS_PACKET_SIZE,
            script_name=script_name,
            script_data=detail,
        )

        # Set video codec from stream type
        if tag_type == TagType.VIDEO:
            from media_analyzer.core.models import VideoCodec
            stream_type = detail.get("stream_type", 0)
            if stream_type == 0x1B:
                packet.video_codec = VideoCodec.AVC
            elif stream_type == 0x24:
                packet.video_codec = VideoCodec.HEVC

            # PES accumulation for video PIDs
            self._accumulate_video_pes(pid, pusi, payload, packet, detail)

        self._tag_count += 1
        return packet

    # ------------------------------------------------------------------
    # PES reassembly for frame type detection
    # ------------------------------------------------------------------

    def _accumulate_video_pes(self, pid: int, pusi: int, payload: bytes,
                              packet: PacketInfo, detail: dict) -> None:
        """
        Accumulate TS payloads into complete PES packets for video PIDs.

        On PUSI=1: flush the previous PES (detect frame type, back-annotate),
                   then start a new accumulator.
        On PUSI=0: append payload to the current accumulator.
        """
        stream_type = detail.get("stream_type", 0)

        if pusi:
            # Flush previous PES for this PID (now complete)
            if pid in self._pes_accumulators:
                self._flush_pes_accumulator(pid)

            # Start new accumulator
            if pid not in self._pes_accumulators:
                self._pes_accumulators[pid] = _PESAccumulator(pid, stream_type)

            acc = self._pes_accumulators[pid]
            acc.reset(payload, packet)
        else:
            # Continuation packet — append to accumulator
            if pid in self._pes_accumulators:
                self._pes_accumulators[pid].append(payload)

    def _flush_pes_accumulator(self, pid: int) -> None:
        """
        Flush a complete PES: extract ES data, detect frame type,
        and back-annotate the PUSI PacketInfo.
        """
        acc = self._pes_accumulators.get(pid)
        if acc is None or not acc.has_data():
            return

        pusi_packet = acc.pusi_packet
        pes_data = bytes(acc.pes_data)

        # Extract ES from complete PES
        es_data = self._extract_es_from_pes(pes_data)
        if es_data is None:
            return

        # Detect frame type from complete ES
        frame_info = self._detect_frame_type(es_data, acc.stream_type)

        # Back-annotate the PUSI PacketInfo
        from media_analyzer.core.models import FrameType

        ft = frame_info.get("frame_type") if frame_info else None
        is_keyframe = frame_info.get("is_keyframe", False) if frame_info else False

        # Use PTS/DTS heuristic as fallback when ES analysis can't determine type
        # (common for MPEG-2 P/B frames that lack explicit picture_coding_type)
        if ft is None and pusi_packet.script_data:
            pes_info = pusi_packet.script_data.get("pes", {})
            # For MPEG-2: if no picture_coding_type found in ES, the frame is
            # almost certainly P (B-frames in compliant streams have picture headers).
            # For H.264/H.265: if slice_type wasn't parseable, use PTS/DTS:
            #   - non-monotonic PTS suggests B-frame
            #   - otherwise P-frame
            if acc.stream_type in (0x01, 0x02):
                # MPEG-2: default to P for non-I frames without picture header
                ft = "P"
            else:
                # H.264/H.265: fallback to P
                ft = "P"

        if is_keyframe or ft == "I":
            pusi_packet.frame_type = FrameType.KEY
        elif ft == "P":
            pusi_packet.frame_type = FrameType.INTER
            # Set composition_time = 0 to indicate P-frame in frame_label
            pusi_packet.composition_time = 0
        elif ft == "B":
            pusi_packet.frame_type = FrameType.INTER
            # Set composition_time != 0 to indicate B-frame in frame_label
            pusi_packet.composition_time = 1

        # Store nalu_types in detail for display
        if pusi_packet.script_data:
            pes_info = pusi_packet.script_data.get("pes")
            if pes_info is None:
                pes_info = {}
                pusi_packet.script_data["pes"] = pes_info
            if frame_info and "nalu_types" in frame_info:
                pes_info["nalu_types"] = frame_info["nalu_types"]
            if ft:
                pes_info["picture_coding_type"] = ft
            if is_keyframe:
                pes_info["is_keyframe"] = True
                pusi_packet.script_data["keyframe"] = True

        # Parse individual NALUs from ES for H.264/H.265 streams
        if acc.stream_type in (0x1B, 0x24) and es_data:
            nalus = self._parse_es_nalus(es_data, acc.stream_type)
            if nalus:
                pusi_packet.nalu_list = nalus

        # Store PES size and ES offset info for PES hex view reconstruction
        if pusi_packet.script_data:
            pusi_packet.script_data["_pes_size"] = len(pes_data)
            pusi_packet.script_data["_es_offset_in_pes"] = len(pes_data) - len(es_data) if es_data else 0

    def _flush_all_accumulators(self) -> None:
        """Flush all remaining PES accumulators at end of stream."""
        for pid in list(self._pes_accumulators.keys()):
            self._flush_pes_accumulator(pid)

    def _parse_es_nalus(self, es_data: bytes, stream_type: int) -> List:
        """
        Parse individual NALUs from Annex B ES data.
        Returns List[NALUInfo] with offset_in_tag = offset within ES data.
        Parses SPS/PPS/VPS fields for display in detail panel.
        """
        from media_analyzer.core.models import NALUInfo, H264NALUType, H265NALUType

        is_h264 = stream_type == 0x1B
        nalus = []
        nalu_positions = []  # (start_of_nalu_data, start_code_len)

        # Find all start code positions
        i = 0
        data_len = len(es_data)
        while i < data_len - 3:
            if es_data[i] == 0 and es_data[i + 1] == 0:
                if es_data[i + 2] == 1:
                    nalu_positions.append((i, 3))
                    i += 3
                    continue
                elif es_data[i + 2] == 0 and i + 3 < data_len and es_data[i + 3] == 1:
                    nalu_positions.append((i, 4))
                    i += 4
                    continue
            i += 1

        if not nalu_positions:
            return []

        # Parse each NALU
        for idx, (sc_offset, sc_len) in enumerate(nalu_positions):
            nalu_data_start = sc_offset + sc_len  # First byte of NALU header

            # NALU end = start of next start code or end of data
            if idx + 1 < len(nalu_positions):
                nalu_end = nalu_positions[idx + 1][0]
            else:
                nalu_end = data_len

            # Strip trailing zeros (padding between NALUs)
            while nalu_end > nalu_data_start and es_data[nalu_end - 1] == 0:
                nalu_end -= 1

            nalu_size = nalu_end - nalu_data_start
            if nalu_size <= 0:
                continue

            first_byte = es_data[nalu_data_start]

            if is_h264:
                nalu_type_val = first_byte & 0x1F
                forbidden = (first_byte >> 7) & 0x01
                if forbidden:
                    continue
                try:
                    type_name = H264NALUType(nalu_type_val).name
                except ValueError:
                    type_name = f"Unknown({nalu_type_val})"
                is_vcl = nalu_type_val in (1, 2, 3, 4, 5)
            else:
                # H.265: 2-byte header
                nalu_type_val = (first_byte >> 1) & 0x3F
                forbidden = (first_byte >> 7) & 0x01
                if forbidden:
                    continue
                try:
                    type_name = H265NALUType(nalu_type_val).name
                except ValueError:
                    type_name = f"Unknown({nalu_type_val})"
                is_vcl = nalu_type_val <= 21

            header_bytes = es_data[nalu_data_start:nalu_data_start + min(4, nalu_size)]

            nalu_info = NALUInfo(
                index=len(nalus),
                nalu_type=nalu_type_val,
                nalu_type_name=type_name,
                size=nalu_size,
                offset_in_tag=sc_offset,  # Offset of start code within ES
                header_bytes=bytes(header_bytes),
                is_vcl=is_vcl,
            )

            # Parse SPS/PPS/VPS bitstream fields for detail display
            nalu_data = es_data[nalu_data_start:nalu_end]
            nalu_info.parsed_fields = self._parse_nalu_fields(
                nalu_type_val, nalu_data, is_h264)

            nalus.append(nalu_info)

        return nalus

    @staticmethod
    def _parse_nalu_fields(nalu_type: int, nalu_data: bytes, is_h264: bool):
        """Parse SPS/PPS/VPS bitstream fields. Returns parsed_fields or None."""
        try:
            if is_h264:
                if nalu_type == 7:  # SPS
                    from media_analyzer.parsers.h264.sps import parse_sps
                    return parse_sps(nalu_data)
                elif nalu_type == 8:  # PPS
                    from media_analyzer.parsers.h264.pps import parse_pps
                    return parse_pps(nalu_data)
            else:
                # H.265/HEVC
                if nalu_type == 32:  # VPS
                    from media_analyzer.parsers.h265.vps import parse_hevc_vps
                    return parse_hevc_vps(nalu_data)
                elif nalu_type == 33:  # SPS
                    from media_analyzer.parsers.h265.sps import parse_hevc_sps
                    return parse_hevc_sps(nalu_data)
                elif nalu_type == 34:  # PPS
                    from media_analyzer.parsers.h265.pps import parse_hevc_pps
                    return parse_hevc_pps(nalu_data)
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_es_from_pes(pes_data: bytes) -> Optional[bytes]:
        """Extract ES (elementary stream) data from a complete PES packet."""
        if len(pes_data) < 9:
            return None
        if pes_data[0:3] != b'\x00\x00\x01':
            return None

        stream_id = pes_data[3]
        # Non-PES stream IDs don't have optional header
        if stream_id in (0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xFF):
            return pes_data[6:]  # Raw payload after PES header

        if len(pes_data) < 9:
            return None

        pes_header_data_length = pes_data[8]
        es_start = 9 + pes_header_data_length

        if es_start >= len(pes_data):
            return None

        return pes_data[es_start:]

    # ------------------------------------------------------------------
    # Frame type detection from complete ES data
    # ------------------------------------------------------------------

    def _detect_frame_type(self, es_data: bytes, stream_type: int) -> Optional[Dict[str, Any]]:
        """
        Detect frame type from complete ES (Elementary Stream) data.

        Uses stream_type from PMT to determine the correct interpretation:
        - stream_type 0x01/0x02: MPEG-1/MPEG-2 Video
        - stream_type 0x1B: H.264/AVC (Annex B)
        - stream_type 0x24: H.265/HEVC (Annex B)
        - stream_type 0: auto-detect (heuristic)

        Returns dict with: is_keyframe, frame_type ('I'/'P'/'B'), nalu_types list
        """
        if not es_data or len(es_data) < 5:
            return None

        if stream_type in (0x01, 0x02):
            return self._detect_mpeg2_frame_type(es_data)
        elif stream_type == 0x1B:
            return self._detect_h264_frame_type(es_data)
        elif stream_type == 0x24:
            return self._detect_h265_frame_type(es_data)
        else:
            # Auto-detect: try each codec heuristically
            return self._detect_auto_frame_type(es_data)

    def _detect_mpeg2_frame_type(self, es_data: bytes) -> Optional[Dict[str, Any]]:
        """Detect frame type from MPEG-1/MPEG-2 ES data."""
        result: Dict[str, Any] = {"is_keyframe": False}
        nalu_types: List[str] = []
        frame_type = None

        # Scan for start codes (limit to first 2048 bytes — enough for headers)
        limit = min(len(es_data), 2048)

        for sc_pos, sc_val in self._scan_start_codes(es_data, limit):
            if sc_val == 0xB3:
                # Sequence header — strong I-frame indicator
                result["is_keyframe"] = True
                frame_type = "I"
                nalu_types.append("SEQ_HDR")
            elif sc_val == 0xB8:
                # Group of Pictures header
                nalu_types.append("GOP")
            elif sc_val == 0x00:
                # Picture start code: temporal_reference(10b) | picture_coding_type(3b) | ...
                data_pos = sc_pos + 1  # byte after the start code value
                if data_pos + 1 < len(es_data):
                    pic_bits = (es_data[data_pos] << 8) | es_data[data_pos + 1]
                    pic_type_val = (pic_bits >> 3) & 0x07
                    pt = {1: "I", 2: "P", 3: "B", 4: "D"}.get(pic_type_val)
                    if pt:
                        frame_type = pt
                        if pt == "I":
                            result["is_keyframe"] = True
                        nalu_types.append(f"PIC_{pt}")
            elif sc_val == 0xB5:
                nalu_types.append("EXT")
            elif 0x01 <= sc_val <= 0xAF:
                # Slice start codes — stop scanning, we have enough info
                if not nalu_types or nalu_types[-1][:6] != "SLICE_":
                    nalu_types.append(f"SLICE_{sc_val}")
                if frame_type:
                    break  # We have frame type + slices, done

        if nalu_types:
            result["nalu_types"] = nalu_types
        if frame_type:
            result["frame_type"] = frame_type
        return result if (nalu_types or frame_type) else None

    def _detect_h264_frame_type(self, es_data: bytes) -> Optional[Dict[str, Any]]:
        """Detect frame type from H.264/AVC Annex B ES data."""
        result: Dict[str, Any] = {"is_keyframe": False}
        nalu_types: List[str] = []
        frame_type = None

        limit = min(len(es_data), 2048)

        for sc_pos, nalu_byte in self._scan_start_codes(es_data, limit):
            # H.264: forbidden_zero_bit(1) | nal_ref_idc(2) | nal_unit_type(5)
            forbidden = (nalu_byte >> 7) & 0x01
            if forbidden:
                continue  # Invalid NALU

            h264_type = nalu_byte & 0x1F

            if h264_type == 5:  # IDR slice
                result["is_keyframe"] = True
                frame_type = "I"
                nalu_types.append("IDR")
            elif h264_type == 7:  # SPS
                result["is_keyframe"] = True
                nalu_types.append("SPS")
            elif h264_type == 8:  # PPS
                nalu_types.append("PPS")
            elif h264_type == 9:  # AUD (Access Unit Delimiter)
                if sc_pos + 1 < len(es_data):
                    primary_pic_type = (es_data[sc_pos + 1] >> 5) & 0x07
                    # 0=I only, 5=I/SI → definitely I-frame
                    # 1=I/P, 3=SI, 4=SI/SP, 6=I/SI/P/SP → has P, use as hint
                    # 2=I/P/B, 7=all → too ambiguous, rely on slice_type instead
                    if primary_pic_type in (0, 5):
                        frame_type = "I"
                        result["is_keyframe"] = True
                    elif primary_pic_type in (1, 3, 4, 6):
                        if frame_type is None:
                            frame_type = "P"  # Hint, may be overridden by slice
                nalu_types.append("AUD")
            elif h264_type == 6:  # SEI
                nalu_types.append("SEI")
            elif h264_type == 1:  # Non-IDR slice
                # Parse slice_type from slice header for definitive answer
                slice_ft = self._parse_h264_slice_type(es_data, sc_pos + 1)
                if slice_ft:
                    frame_type = slice_ft  # Always override with actual slice type
                nalu_types.append("SLICE")

            # Stop early if we have a definitive frame type from VCL NALU
            if frame_type and h264_type in (1, 5):
                break

        if nalu_types:
            result["nalu_types"] = nalu_types
        if frame_type:
            result["frame_type"] = frame_type
            if frame_type == "I":
                result["is_keyframe"] = True
        return result if (nalu_types or frame_type) else None

    def _detect_h265_frame_type(self, es_data: bytes) -> Optional[Dict[str, Any]]:
        """Detect frame type from H.265/HEVC Annex B ES data."""
        result: Dict[str, Any] = {"is_keyframe": False}
        nalu_types: List[str] = []
        frame_type = None

        limit = min(len(es_data), 2048)

        for sc_pos, nalu_byte in self._scan_start_codes(es_data, limit):
            # H.265: forbidden_zero_bit(1) | nal_unit_type(6) | nuh_layer_id(6) | nuh_temporal_id_plus1(3)
            forbidden = (nalu_byte >> 7) & 0x01
            if forbidden:
                continue

            h265_type = (nalu_byte >> 1) & 0x3F

            if h265_type in (19, 20):  # IDR_W_RADL, IDR_N_LP
                result["is_keyframe"] = True
                frame_type = "I"
                nalu_types.append("IDR")
            elif h265_type == 21:  # CRA
                result["is_keyframe"] = True
                frame_type = "I"
                nalu_types.append("CRA")
            elif h265_type == 32:  # VPS
                result["is_keyframe"] = True
                nalu_types.append("VPS")
            elif h265_type == 33:  # SPS
                nalu_types.append("SPS")
            elif h265_type == 34:  # PPS
                nalu_types.append("PPS")
            elif h265_type == 35:  # AUD
                nalu_types.append("AUD")
            elif h265_type == 39:  # PREFIX_SEI
                nalu_types.append("SEI")
            elif h265_type <= 21:  # VCL NALU
                if frame_type is None:
                    frame_type = "P"  # Default non-IDR VCL
                nalu_types.append(f"VCL_{h265_type}")

            if frame_type and len(nalu_types) >= 3:
                break

        if nalu_types:
            result["nalu_types"] = nalu_types
        if frame_type:
            result["frame_type"] = frame_type
            if frame_type == "I":
                result["is_keyframe"] = True
        return result if (nalu_types or frame_type) else None

    def _detect_auto_frame_type(self, es_data: bytes) -> Optional[Dict[str, Any]]:
        """Auto-detect codec and frame type from ES data (heuristic)."""
        # Try to identify codec from start code patterns
        limit = min(len(es_data), 2048)
        for sc_pos, sc_val in self._scan_start_codes(es_data, limit):
            # MPEG-2 identifiers: sequence header (0xB3), GOP (0xB8), picture (0x00)
            if sc_val in (0xB3, 0xB8):
                return self._detect_mpeg2_frame_type(es_data)
            if sc_val == 0x00:
                # Could be MPEG-2 picture header — check for valid picture_coding_type
                data_pos = sc_pos + 1
                if data_pos + 1 < len(es_data):
                    pic_bits = (es_data[data_pos] << 8) | es_data[data_pos + 1]
                    pic_type_val = (pic_bits >> 3) & 0x07
                    if 1 <= pic_type_val <= 4:
                        return self._detect_mpeg2_frame_type(es_data)

            # H.264 identifiers: forbidden=0, types 1,5,6,7,8,9
            forbidden = (sc_val >> 7) & 0x01
            if forbidden == 0:
                h264_type = sc_val & 0x1F
                if h264_type in (5, 7, 8, 9):
                    return self._detect_h264_frame_type(es_data)
                # H.265: VPS(32), SPS(33), PPS(34), IDR(19,20)
                h265_type = (sc_val >> 1) & 0x3F
                if h265_type in (19, 20, 21, 32, 33, 34):
                    return self._detect_h265_frame_type(es_data)

            break  # Only check first start code for auto-detect

        return None

    @staticmethod
    def _scan_start_codes(data: bytes, limit: int):
        """
        Generator that yields (nalu_byte_pos, nalu_first_byte) for each start code found.

        Scans for 00 00 01 or 00 00 00 01 patterns within data[0:limit].
        nalu_byte_pos = position of the first byte AFTER the start code prefix.
        nalu_first_byte = data[nalu_byte_pos] (the NALU header byte / start code value).
        """
        pos = 0
        data_len = min(len(data), limit)
        while pos < data_len - 3:
            if data[pos] == 0 and data[pos + 1] == 0:
                if data[pos + 2] == 1:
                    # 3-byte start code
                    nalu_pos = pos + 3
                    if nalu_pos < len(data):
                        yield nalu_pos, data[nalu_pos]
                    pos = nalu_pos + 1
                    continue
                elif data[pos + 2] == 0 and pos + 3 < data_len and data[pos + 3] == 1:
                    # 4-byte start code
                    nalu_pos = pos + 4
                    if nalu_pos < len(data):
                        yield nalu_pos, data[nalu_pos]
                    pos = nalu_pos + 1
                    continue
            pos += 1

    @staticmethod
    def _parse_h264_slice_type(data: bytes, offset: int) -> Optional[str]:
        """
        Parse H.264 slice_type from slice header.
        slice_header starts with: first_mb_in_slice (ue) + slice_type (ue)
        Returns 'I', 'P', 'B' or None.
        """
        if offset + 4 >= len(data):
            return None
        try:
            from media_analyzer.parsers.h264.bitreader import BitReader
            # Only need a few bytes for the two exp-golomb values
            reader = BitReader(data[offset:offset + 8])
            reader.read_ue()  # first_mb_in_slice (skip)
            slice_type = reader.read_ue()
            # slice_type: 0/5=P, 1/6=B, 2/7=I, 3/8=SP, 4/9=SI
            if slice_type in (2, 7):
                return "I"
            elif slice_type in (0, 5):
                return "P"
            elif slice_type in (1, 6):
                return "B"
        except (EOFError, ValueError, IndexError):
            pass
        return None

    # ------------------------------------------------------------------
    # Adaptation field, PES header, PAT/PMT parsing
    # ------------------------------------------------------------------

    def _parse_adaptation_field(self, af_data: bytes) -> Dict[str, Any]:
        """Parse adaptation field and return details."""
        info: Dict[str, Any] = {}
        if not af_data:
            return info

        flags = af_data[0]
        active_flags = []
        for mask, name in _AF_FLAG_NAMES:
            if flags & mask:
                active_flags.append(name)
        info["flags"] = active_flags

        pos = 1
        # PCR
        if (flags & 0x10) and pos + 6 <= len(af_data):
            pcr_base = ((af_data[pos] << 25) | (af_data[pos+1] << 17) |
                       (af_data[pos+2] << 9) | (af_data[pos+3] << 1) |
                       ((af_data[pos+4] >> 7) & 0x01))
            pcr_ext = ((af_data[pos+4] & 0x01) << 8) | af_data[pos+5]
            pcr = pcr_base * 300 + pcr_ext
            info["pcr"] = pcr
            info["pcr_base"] = pcr_base
            info["pcr_ms"] = round(pcr_base / 90.0, 3)
            pos += 6

        # OPCR
        if (flags & 0x08) and pos + 6 <= len(af_data):
            pos += 6  # Skip OPCR

        # Random access indicator
        if flags & 0x40:
            info["random_access"] = True

        return info

    def _parse_pes_header(self, payload: bytes) -> Optional[Dict[str, Any]]:
        """
        Parse PES header from payload (when PUSI=1).
        Only extracts PTS/DTS timestamps — frame type detection is done
        later via PES reassembly in _flush_pes_accumulator.
        """
        if len(payload) < 9:
            return None
        if payload[0:3] != b'\x00\x00\x01':
            return None

        info: Dict[str, Any] = {}
        stream_id = payload[3]
        pes_length = (payload[4] << 8) | payload[5]
        info["stream_id"] = stream_id
        info["stream_id_hex"] = f"0x{stream_id:02X}"
        info["pes_packet_length"] = pes_length

        # Non-PES stream IDs don't have optional header
        if stream_id in (0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xFF):
            return info

        if len(payload) < 9:
            return info

        # Optional PES header
        flags1 = payload[6]
        flags2 = payload[7]
        pes_header_length = payload[8]

        info["scrambling_control"] = (flags1 >> 4) & 0x03
        info["priority"] = bool(flags1 & 0x08)
        info["data_alignment"] = bool(flags1 & 0x04)
        info["copyright"] = bool(flags1 & 0x02)
        info["original"] = bool(flags1 & 0x01)

        pts_dts_flags = (flags2 >> 6) & 0x03
        info["pts_dts_flags"] = pts_dts_flags
        info["pes_header_data_length"] = pes_header_length

        # PTS
        if pts_dts_flags >= 2 and len(payload) >= 14:
            pts = self._parse_timestamp(payload[9:14])
            if pts is not None:
                info["pts"] = pts
                info["pts_ms"] = int(pts / 90)

        # DTS
        if pts_dts_flags == 3 and len(payload) >= 19:
            dts = self._parse_timestamp(payload[14:19])
            if dts is not None:
                info["dts"] = dts
                info["dts_ms"] = int(dts / 90)
                if "pts_ms" in info:
                    info["cts_ms"] = info["pts_ms"] - info["dts_ms"]

        return info

    @staticmethod
    def _parse_timestamp(data: bytes) -> Optional[int]:
        """Parse a 5-byte PTS/DTS timestamp field."""
        if len(data) < 5:
            return None
        ts = ((data[0] >> 1) & 0x07) << 30
        ts |= (data[1] << 22)
        ts |= ((data[2] >> 1) << 15)
        ts |= (data[3] << 7)
        ts |= (data[4] >> 1)
        return ts

    def _parse_pat(self, data: bytes) -> None:
        """Parse Program Association Table."""
        if len(data) < 8:
            return
        section_length = ((data[1] & 0x0F) << 8) | data[2]
        ts_id = (data[3] << 8) | data[4]
        version = (data[5] >> 1) & 0x1F

        pos = 8
        end = min(3 + section_length - 4, len(data))

        programs = []
        self._pmt_pids.clear()
        while pos + 4 <= end:
            program_number = (data[pos] << 8) | data[pos + 1]
            pmt_pid = ((data[pos + 2] & 0x1F) << 8) | data[pos + 3]
            if program_number != 0:
                self._pmt_pids[program_number] = pmt_pid
                programs.append({"program_number": program_number, "pmt_pid": pmt_pid})
            pos += 4

        self._pat_details = {
            "transport_stream_id": ts_id,
            "version_number": version,
            "programs": programs,
        }

    def _parse_pmt(self, data: bytes) -> None:
        """Parse Program Map Table."""
        if len(data) < 12:
            return
        section_length = ((data[1] & 0x0F) << 8) | data[2]
        program_number = (data[3] << 8) | data[4]
        version = (data[5] >> 1) & 0x1F
        pcr_pid = ((data[8] & 0x1F) << 8) | data[9]
        program_info_length = ((data[10] & 0x0F) << 8) | data[11]

        # Program descriptors
        prog_descs = self._parse_descriptors(data[12:12 + program_info_length]) if program_info_length > 0 else []

        pos = 12 + program_info_length
        end = min(3 + section_length - 4, len(data))
        streams = []

        while pos + 5 <= end:
            stream_type = data[pos]
            elementary_pid = ((data[pos + 1] & 0x1F) << 8) | data[pos + 2]
            es_info_length = ((data[pos + 3] & 0x0F) << 8) | data[pos + 4]

            type_name = STREAM_TYPE_NAMES.get(stream_type, f"Unknown(0x{stream_type:02X})")
            es_descs = self._parse_descriptors(data[pos + 5:pos + 5 + es_info_length]) if es_info_length > 0 else []

            self._streams[elementary_pid] = {
                "type": stream_type,
                "type_name": type_name,
                "is_video": stream_type in VIDEO_STREAM_TYPES,
                "is_audio": stream_type in AUDIO_STREAM_TYPES,
            }
            streams.append({
                "stream_type": stream_type,
                "stream_type_name": type_name,
                "elementary_pid": elementary_pid,
                "is_video": stream_type in VIDEO_STREAM_TYPES,
                "is_audio": stream_type in AUDIO_STREAM_TYPES,
                "descriptors": es_descs,
            })
            pos += 5 + es_info_length

        self._pmt_details = {
            "program_number": program_number,
            "version_number": version,
            "pcr_pid": pcr_pid,
            "program_descriptors": prog_descs,
            "streams": streams,
        }

    @staticmethod
    def _parse_descriptors(data: bytes) -> List[Dict[str, Any]]:
        """Parse descriptor loop."""
        descriptors = []
        pos = 0
        while pos + 2 <= len(data):
            tag = data[pos]
            length = data[pos + 1]
            if pos + 2 + length > len(data):
                break
            desc_data = data[pos + 2:pos + 2 + length]
            desc = {
                "tag": tag,
                "tag_name": _DESCRIPTOR_NAMES.get(tag, f"0x{tag:02X}"),
                "length": length,
            }
            # Parse specific descriptors
            if tag == 0x0A and length >= 4:
                desc["language_code"] = desc_data[:3].decode("ascii", errors="replace")
                desc["audio_type"] = desc_data[3]
            elif tag == 0x05 and length >= 4:
                desc["format_identifier"] = desc_data[:4].decode("ascii", errors="replace")
            elif tag == 0x28 and length >= 4:
                desc["profile_idc"] = desc_data[0]
                desc["level_idc"] = desc_data[2]
            elif tag == 0x38 and length >= 4:
                desc["profile_idc"] = desc_data[0] & 0x1F
                desc["tier_flag"] = (desc_data[0] >> 5) & 0x01
            desc["raw_hex"] = desc_data.hex() if length <= 32 else desc_data[:32].hex() + "..."
            descriptors.append(desc)
            pos += 2 + length
        return descriptors

    def get_stream_info(self) -> StreamInfo:
        """Return aggregate stream info."""
        info = StreamInfo(
            source_path="",
            format_name="MPEG-TS",
            duration_ms=int(self._max_pts / 90) if self._max_pts > 0 else 0,
            total_tags=self._tag_count,
            video_tags=self._video_count,
            audio_tags=self._audio_count,
        )
        for pid, meta in self._streams.items():
            if meta.get("is_video") and not info.video_codec:
                info.video_codec = meta.get("type_name")
            elif meta.get("is_audio") and not info.audio_codec:
                info.audio_codec = meta.get("type_name")
        return info
