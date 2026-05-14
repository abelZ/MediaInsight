"""MPEG-TS (Transport Stream) parser.

Parses 188-byte TS packets, yields one PacketInfo per TS packet.
Extracts PAT/PMT for stream identification.
When a packet has PUSI=1 (start of PES), includes PES header details.

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


class TSParser(BaseParser):
    """
    MPEG Transport Stream parser — yields one PacketInfo per 188-byte TS packet.
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
                    if is_video and pes_info.get("is_keyframe"):
                        detail["keyframe"] = True

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

        # Set video-specific fields
        if tag_type == TagType.VIDEO:
            from media_analyzer.core.models import VideoCodec, FrameType
            stream_type = detail.get("stream_type", 0)
            if stream_type == 0x1B:
                packet.video_codec = VideoCodec.AVC
            elif stream_type == 0x24:
                packet.video_codec = VideoCodec.HEVC
            if pusi and detail.get("keyframe"):
                packet.frame_type = FrameType.KEY
            elif pusi:
                packet.frame_type = FrameType.INTER
            if detail.get("pes", {}).get("cts_ms") is not None:
                packet.composition_time = detail["pes"]["cts_ms"]

        self._tag_count += 1
        return packet

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
        """Parse PES header from payload (when PUSI=1)."""
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

        # Try to detect keyframe from ES data
        es_start = 9 + pes_header_length
        if es_start < len(payload):
            es_data = payload[es_start:]
            info["is_keyframe"] = self._detect_keyframe_from_es(es_data)
            # Detect NALU types for video
            nalus = self._scan_start_codes(es_data)
            if nalus:
                info["nalu_types"] = nalus

        return info

    def _detect_keyframe_from_es(self, data: bytes) -> bool:
        """Detect if ES data starts with a keyframe."""
        # Scan first 64 bytes for NALU start codes
        limit = min(len(data), 64)
        pos = 0
        while pos + 4 < limit:
            if data[pos:pos + 4] == b'\x00\x00\x00\x01':
                nalu_byte = data[pos + 4]
                # H.264: type 5=IDR, 7=SPS
                nalu_type_264 = nalu_byte & 0x1F
                if nalu_type_264 in (5, 7):
                    return True
                # H.265: type = (byte >> 1) & 0x3F
                nalu_type_265 = (nalu_byte >> 1) & 0x3F
                if nalu_type_265 in (19, 20, 21, 32, 33):
                    return True
                pos += 4
            elif data[pos:pos + 3] == b'\x00\x00\x01':
                nalu_byte = data[pos + 3]
                nalu_type_264 = nalu_byte & 0x1F
                if nalu_type_264 in (5, 7):
                    return True
                pos += 3
            else:
                pos += 1
        # MPEG-2: sequence header 0x000001B3
        if b'\x00\x00\x01\xB3' in data[:32]:
            return True
        return False

    @staticmethod
    def _scan_start_codes(data: bytes) -> List[str]:
        """Scan ES data for NALU start codes and return type names."""
        nalus = []
        limit = min(len(data), 256)
        pos = 0
        while pos + 4 < limit and len(nalus) < 10:
            sc_len = 0
            if data[pos:pos + 4] == b'\x00\x00\x00\x01':
                sc_len = 4
            elif data[pos:pos + 3] == b'\x00\x00\x01':
                sc_len = 3

            if sc_len > 0:
                nalu_byte = data[pos + sc_len]
                nalu_type = nalu_byte & 0x1F
                from media_analyzer.core.models import H264NALUType
                try:
                    nalus.append(H264NALUType(nalu_type).name)
                except ValueError:
                    nalus.append(f"type_{nalu_type}")
                pos += sc_len + 1
            else:
                pos += 1
        return nalus

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
