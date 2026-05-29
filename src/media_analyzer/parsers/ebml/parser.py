"""WebM/MKV (Matroska) parser — EBML-based container format.

Parses EBML element hierarchy and yields one PacketInfo per element,
similar to how the MP4 parser yields one per box.

Displays as a tree in the BoxTreeView (same as MP4).
Also collects frame-level data for bitrate/timestamp analysis.

Reference: https://www.matroska.org/technical/elements.html
"""

import logging
import struct
from typing import Generator, BinaryIO, Dict, Optional, List, Any

from media_analyzer.parsers.base import BaseParser
from media_analyzer.core.models import (
    PacketInfo, StreamInfo, TagType, FrameType,
)
from media_analyzer.parsers.ebml.elements import (
    read_vint, read_vint_raw, read_element_header,
    EBML_HEADER, DOC_TYPE, SEGMENT, INFO, TRACKS,
    TIMESTAMP_SCALE, DURATION, TRACK_ENTRY, TRACK_NUMBER,
    TRACK_TYPE, CODEC_ID, CLUSTER, CLUSTER_TIMESTAMP,
    SIMPLE_BLOCK, BLOCK_GROUP, BLOCK, VIDEO,
    PIXEL_WIDTH, PIXEL_HEIGHT, AUDIO_ELEMENT,
    SAMPLING_FREQUENCY, CHANNELS, DEFAULT_DURATION,
    TRACK_TYPE_VIDEO, TRACK_TYPE_AUDIO,
    CONTAINER_ELEMENTS, CODEC_MAP, CUES, TAGS, SEEK_HEAD,
    EBML_VERSION, EBML_READ_VERSION, EBML_MAX_ID_LENGTH,
    EBML_MAX_SIZE_LENGTH, DOC_TYPE_VERSION, DOC_TYPE_READ_VERSION,
    MUXING_APP, WRITING_APP, TITLE, TRACK_UID, CODEC_NAME,
    CODEC_PRIVATE, DEFAULT_DURATION as DEFAULT_DURATION_ID,
    BLOCK_DURATION,
)

logger = logging.getLogger(__name__)


# Element ID → human-readable name
ELEMENT_NAMES = {
    EBML_HEADER: "EBML",
    EBML_VERSION: "EBMLVersion",
    EBML_READ_VERSION: "EBMLReadVersion",
    EBML_MAX_ID_LENGTH: "EBMLMaxIDLength",
    EBML_MAX_SIZE_LENGTH: "EBMLMaxSizeLength",
    DOC_TYPE: "DocType",
    DOC_TYPE_VERSION: "DocTypeVersion",
    DOC_TYPE_READ_VERSION: "DocTypeReadVersion",
    SEGMENT: "Segment",
    INFO: "Info",
    TIMESTAMP_SCALE: "TimestampScale",
    DURATION: "Duration",
    MUXING_APP: "MuxingApp",
    WRITING_APP: "WritingApp",
    TITLE: "Title",
    TRACKS: "Tracks",
    TRACK_ENTRY: "TrackEntry",
    TRACK_NUMBER: "TrackNumber",
    TRACK_UID: "TrackUID",
    TRACK_TYPE: "TrackType",
    CODEC_ID: "CodecID",
    CODEC_NAME: "CodecName",
    CODEC_PRIVATE: "CodecPrivate",
    DEFAULT_DURATION_ID: "DefaultDuration",
    VIDEO: "Video",
    PIXEL_WIDTH: "PixelWidth",
    PIXEL_HEIGHT: "PixelHeight",
    AUDIO_ELEMENT: "Audio",
    SAMPLING_FREQUENCY: "SamplingFrequency",
    CHANNELS: "Channels",
    CLUSTER: "Cluster",
    CLUSTER_TIMESTAMP: "Timestamp",
    SIMPLE_BLOCK: "SimpleBlock",
    BLOCK_GROUP: "BlockGroup",
    BLOCK: "Block",
    BLOCK_DURATION: "BlockDuration",
    CUES: "Cues",
    SEEK_HEAD: "SeekHead",
    TAGS: "Tags",
    0x4DBB: "Seek",
    0x53AB: "SeekID",
    0x53AC: "SeekPosition",
    0xEC: "Void",
    0xBF: "CRC-32",
    0x6254: "Tag",
    0x63C0: "Targets",
    0x67C8: "SimpleTag",
    0x45A3: "TagName",
    0x4487: "TagString",
    0x6532: "SignedElement",
    0x73A4: "SegmentUID",
    0x4461: "DateUTC",
    0x7384: "SegmentFilename",
    # Track details
    0x88: "FlagDefault",
    0x9C: "FlagLacing",
    0x55AA: "FlagForced",
    0x55EE: "MaxBlockAdditionID",
    0x56AA: "CodecDelay",
    0x56BB: "SeekPreRoll",
    0x22B59C: "Language",
    0x22B59D: "LanguageBCP47",
    0x23E383: "DefaultDuration",
    0x23314F: "TrackTimestampScale",
    0x6264: "BitDepth",
    0x9A: "FlagInterlaced",
    0xC8: "ReferenceFrame",
    # Video details
    0x54B0: "DisplayWidth",
    0x54BA: "DisplayHeight",
    0x54B2: "DisplayUnit",
    0x55B0: "Colour",
    0x55B1: "MatrixCoefficients",
    0x55B5: "BitsPerChannel",
    0x55B7: "ChromaSitingHorz",
    0x55B8: "ChromaSitingVert",
    0x55B9: "Range",
    0x55BA: "TransferCharacteristics",
    0x55BB: "Primaries",
    0xB0: "PixelWidth",
    0xBA: "PixelHeight",
    # Cues (seek index)
    0xBB: "CuePoint",
    0xB3: "CueTime",
    0xB7: "CueTrackPositions",
    0xF0: "CueRelativePosition",
    0xF7: "CueTrack",
    0xF1: "CueClusterPosition",
    0x5378: "CueBlockNumber",
    # Tags
    0x7373: "Tag",
    0x63C0: "Targets",
    0x68CA: "TargetTypeValue",
    0x63CA: "TargetType",
    0x63C5: "TagTrackUID",
    0x67C8: "SimpleTag",
    # Chapters
    0x1043A770: "Chapters",
    0x45B9: "EditionEntry",
    0xB6: "ChapterAtom",
    0x73C4: "ChapterUID",
    0x91: "ChapterTimeStart",
    0x92: "ChapterTimeEnd",
    0x80: "ChapterDisplay",
    0x85: "ChapString",
    0x437C: "ChapLanguage",
    # Attachments
    0x1941A469: "Attachments",
    0x61A7: "AttachedFile",
    0x466E: "FileName",
    0x4660: "FileDescription",
    0x4661: "FileMediaType",
    0x465C: "FileData",
    0x46AE: "FileUID",
}


class _TrackInfo:
    """Metadata for a single track."""
    __slots__ = ('number', 'track_type', 'codec_id', 'codec_label',
                 'width', 'height', 'sample_rate', 'channels',
                 'default_duration_ns', 'codec_private')

    def __init__(self):
        self.number: int = 0
        self.track_type: int = 0
        self.codec_id: str = ""
        self.codec_label: str = ""
        self.width: int = 0
        self.height: int = 0
        self.sample_rate: float = 0
        self.channels: int = 0
        self.default_duration_ns: int = 0
        self.codec_private: bytes = b""


class EBMLParser(BaseParser):
    """
    Parser for WebM/MKV (Matroska) containers.

    Yields one PacketInfo per EBML element for tree display.
    Also tracks frame-level data for bitrate/timestamp charts.
    """

    def __init__(self):
        self._doc_type: str = ""
        self._timestamp_scale_ns: int = 1_000_000  # Default: 1ms per unit
        self._duration_units: float = 0.0
        self._tracks: Dict[int, _TrackInfo] = {}
        self._tag_count: int = 0
        self._video_count: int = 0
        self._audio_count: int = 0
        self._max_timestamp_ms: int = 0
        self._last_track_codec_id: str = ""  # Codec ID of last parsed TrackEntry

    @classmethod
    def sniff(cls, header_bytes: bytes) -> bool:
        """Check EBML header magic: 0x1A 0x45 0xDF 0xA3."""
        return len(header_bytes) >= 4 and header_bytes[:4] == b'\x1a\x45\xdf\xa3'

    def parse_header(self, data: bytes) -> dict:
        return {"format": "ebml"}

    def parse_incremental(self, source: BinaryIO) -> Generator[PacketInfo, None, None]:
        """
        Parse WebM/MKV file, yielding PacketInfo for each EBML element.

        Each PacketInfo has script_data with:
        - box_type: element name (for tree display)
        - depth: nesting level (for tree hierarchy)
        - ebml_id: raw element ID
        """
        self._tag_count = 0

        data = source.read()
        if len(data) < 4:
            return

        pos = 0
        yield from self._parse_elements(data, pos, len(data), depth=0)

    def _parse_elements(self, data: bytes, start: int, end: int,
                        depth: int) -> Generator[PacketInfo, None, None]:
        """Recursively parse EBML elements at a given depth."""
        pos = start
        while pos < end:
            if pos + 2 > len(data):
                break

            try:
                elem_id, elem_size, hdr_len = read_element_header(data, pos)
            except ValueError:
                break

            elem_data_start = pos + hdr_len
            elem_name = ELEMENT_NAMES.get(elem_id, f"Unknown(0x{elem_id:X})")

            # For unknown-size elements, find extent
            if elem_size == -1:
                if elem_id == SEGMENT:
                    elem_size = end - elem_data_start
                elif elem_id == CLUSTER:
                    elem_size = self._find_element_end(data, elem_data_start, end) - elem_data_start
                else:
                    break

            elem_total = hdr_len + elem_size
            elem_end = elem_data_start + elem_size

            # Collect metadata before yielding (for Info/Tracks)
            self._collect_metadata(data, elem_id, elem_data_start, elem_size)

            # Determine detail text for leaf elements
            detail = self._get_element_detail(data, elem_id, elem_data_start, elem_size)

            # Determine tag type for coloring
            tag_type = TagType.SCRIPT  # Default for structure elements
            frame_type = None
            codec_label = ""
            if elem_id == SIMPLE_BLOCK or elem_id == BLOCK:
                tag_type, frame_type, detail, codec_label = self._classify_block(
                    data, elem_data_start, elem_size)

            # Yield PacketInfo for this element
            pkt = PacketInfo(
                index=self._tag_count,
                tag_type=tag_type,
                timestamp=0,
                data_size=elem_size,
                offset=pos,
                stream_id=0,
                tag_total_size=elem_total,
                frame_type=frame_type,
                script_data={
                    "box_type": elem_name,
                    "depth": depth,
                    "ebml_id": elem_id,
                    "is_container": elem_id in CONTAINER_ELEMENTS,
                    "detail": detail,
                },
            )

            # For CodecPrivate, attach parsed codec config (avcC/hvcC)
            if elem_id == CODEC_PRIVATE and elem_size > 0:
                codec_config = self._parse_codec_private(
                    data, elem_data_start, elem_size, self._last_track_codec_id)
                if codec_config:
                    pkt.script_data["codec_config"] = codec_config
                    pkt.script_data["codec_id"] = self._last_track_codec_id

            # For blocks, add timestamp
            if elem_id in (SIMPLE_BLOCK, BLOCK):
                ts_ms = self._get_block_timestamp(data, elem_data_start, elem_size)
                pkt = PacketInfo(
                    index=pkt.index,
                    tag_type=pkt.tag_type,
                    timestamp=ts_ms,
                    data_size=pkt.data_size,
                    offset=pkt.offset,
                    stream_id=pkt.stream_id,
                    tag_total_size=pkt.tag_total_size,
                    frame_type=pkt.frame_type,
                    script_data=pkt.script_data,
                )

            self._tag_count += 1
            yield pkt

            # Recurse into container elements (but not Cluster internals for performance)
            if elem_id in CONTAINER_ELEMENTS and elem_id != CLUSTER:
                yield from self._parse_elements(data, elem_data_start, elem_end, depth + 1)
            elif elem_id == CLUSTER:
                # For Clusters: show direct children (Timestamp + Blocks)
                yield from self._parse_cluster_children(data, elem_data_start, elem_end, depth + 1)

            pos = elem_end
            if pos <= elem_data_start and elem_size == 0:
                pos = elem_data_start + 1

    def _parse_cluster_children(self, data: bytes, start: int, end: int,
                                depth: int) -> Generator[PacketInfo, None, None]:
        """Parse Cluster children: Timestamp and SimpleBlock/BlockGroup elements."""
        pos = start
        cluster_ts = 0

        while pos < end:
            if pos + 2 > len(data):
                break
            try:
                elem_id, elem_size, hdr_len = read_element_header(data, pos)
            except ValueError:
                break

            elem_data_start = pos + hdr_len
            elem_name = ELEMENT_NAMES.get(elem_id, f"Unknown(0x{elem_id:X})")
            elem_total = hdr_len + elem_size
            elem_end = elem_data_start + elem_size

            if elem_size == -1:
                break

            # Read cluster timestamp
            if elem_id == CLUSTER_TIMESTAMP and elem_size <= 8:
                cluster_ts = int.from_bytes(data[elem_data_start:elem_end], "big")

            # Classify block
            tag_type = TagType.SCRIPT
            frame_type = None
            detail = ""
            codec_label = ""
            ts_ms = 0

            if elem_id == CLUSTER_TIMESTAMP:
                detail = str(cluster_ts)
            elif elem_id in (SIMPLE_BLOCK, BLOCK):
                tag_type, frame_type, detail, codec_label = self._classify_block(
                    data, elem_data_start, elem_size)
                # Compute absolute timestamp
                ts_ms = self._compute_block_timestamp(data, elem_data_start, elem_size, cluster_ts)
                if ts_ms > self._max_timestamp_ms:
                    self._max_timestamp_ms = ts_ms
                # Count frames
                if tag_type == TagType.VIDEO:
                    self._video_count += 1
                elif tag_type == TagType.AUDIO:
                    self._audio_count += 1

            pkt = PacketInfo(
                index=self._tag_count,
                tag_type=tag_type,
                timestamp=ts_ms,
                data_size=elem_size,
                offset=pos,
                stream_id=0,
                tag_total_size=elem_total,
                frame_type=frame_type,
                script_data={
                    "box_type": elem_name,
                    "depth": depth,
                    "ebml_id": elem_id,
                    "detail": detail,
                    "codec_name": codec_label,
                    "ebml_track": True,  # For bitrate/timestamp extraction
                },
            )
            self._tag_count += 1
            yield pkt

            # Recurse into BlockGroup
            if elem_id == BLOCK_GROUP:
                yield from self._parse_cluster_children(data, elem_data_start, elem_end, depth + 1)

            pos = elem_end

    def _classify_block(self, data: bytes, start: int, size: int):
        """Determine tag_type, frame_type, detail, codec_label for a SimpleBlock/Block."""
        if size < 4:
            return TagType.SCRIPT, None, "", ""

        pos = start
        try:
            track_num, vint_len = read_vint(data, pos)
        except ValueError:
            return TagType.SCRIPT, None, "", ""
        pos += vint_len

        if pos + 3 > start + size:
            return TagType.SCRIPT, None, "", ""

        time_offset = struct.unpack(">h", data[pos:pos+2])[0]
        flags = data[pos + 2]
        keyframe = bool(flags & 0x80)
        frame_data_size = (start + size) - (pos + 3)

        track = self._tracks.get(track_num)
        if track is None:
            return TagType.SCRIPT, None, f"Track {track_num}", ""

        if track.track_type == TRACK_TYPE_VIDEO:
            tag_type = TagType.VIDEO
            frame_type = FrameType.KEY if keyframe else FrameType.INTER
        elif track.track_type == TRACK_TYPE_AUDIO:
            tag_type = TagType.AUDIO
            frame_type = None
        else:
            return TagType.SCRIPT, None, f"Track {track_num}", ""

        detail = f"Track {track_num} ({track.codec_label}), {frame_data_size} bytes"
        if keyframe:
            detail += ", Keyframe"

        return tag_type, frame_type, detail, track.codec_label

    def _compute_block_timestamp(self, data: bytes, start: int, size: int,
                                 cluster_ts: int) -> int:
        """Compute absolute timestamp in ms for a block."""
        if size < 4:
            return 0
        pos = start
        try:
            _, vint_len = read_vint(data, pos)
        except ValueError:
            return 0
        pos += vint_len
        if pos + 2 > start + size:
            return 0
        time_offset = struct.unpack(">h", data[pos:pos+2])[0]
        ts_units = cluster_ts + time_offset
        return int(ts_units * self._timestamp_scale_ns / 1_000_000)

    def _get_block_timestamp(self, data: bytes, start: int, size: int) -> int:
        """Get block timestamp (for top-level element yield)."""
        # This requires cluster_ts which we don't have at this level
        return 0

    def _collect_metadata(self, data: bytes, elem_id: int, start: int, size: int) -> None:
        """Collect stream metadata from Info and Tracks elements."""
        if elem_id == TIMESTAMP_SCALE and size <= 8:
            self._timestamp_scale_ns = int.from_bytes(data[start:start+size], "big")
        elif elem_id == DURATION and size in (4, 8):
            if size == 4:
                self._duration_units = struct.unpack(">f", data[start:start+4])[0]
            else:
                self._duration_units = struct.unpack(">d", data[start:start+8])[0]
        elif elem_id == DOC_TYPE:
            self._doc_type = data[start:start+size].decode("ascii", errors="replace").rstrip('\x00')
        elif elem_id == TRACK_ENTRY:
            self._parse_track_entry(data, start, size)

    def _parse_track_entry(self, data: bytes, start: int, size: int) -> None:
        """Parse a TrackEntry and store track info."""
        track = _TrackInfo()
        pos = start
        end = start + size
        while pos < end:
            try:
                eid, esz, ehdr = read_element_header(data, pos)
            except ValueError:
                break
            dstart = pos + ehdr
            if eid == TRACK_NUMBER and esz <= 8:
                track.number = int.from_bytes(data[dstart:dstart+esz], "big")
            elif eid == TRACK_TYPE and esz <= 8:
                track.track_type = int.from_bytes(data[dstart:dstart+esz], "big")
            elif eid == CODEC_ID:
                track.codec_id = data[dstart:dstart+esz].decode("ascii", errors="replace").rstrip('\x00')
                track.codec_label = CODEC_MAP.get(track.codec_id, track.codec_id)
            elif eid == DEFAULT_DURATION_ID and esz <= 8:
                track.default_duration_ns = int.from_bytes(data[dstart:dstart+esz], "big")
            elif eid == CODEC_PRIVATE:
                track.codec_private = data[dstart:dstart+esz]
            elif eid == VIDEO:
                self._parse_video_settings(data, dstart, esz, track)
            elif eid == AUDIO_ELEMENT:
                self._parse_audio_settings(data, dstart, esz, track)
            pos = dstart + esz
        if track.number > 0:
            self._tracks[track.number] = track
            self._last_track_codec_id = track.codec_id

    def _parse_video_settings(self, data: bytes, start: int, size: int, track: _TrackInfo):
        pos = start
        end = start + size
        while pos < end:
            try:
                eid, esz, ehdr = read_element_header(data, pos)
            except ValueError:
                break
            dstart = pos + ehdr
            if eid == PIXEL_WIDTH and esz <= 4:
                track.width = int.from_bytes(data[dstart:dstart+esz], "big")
            elif eid == PIXEL_HEIGHT and esz <= 4:
                track.height = int.from_bytes(data[dstart:dstart+esz], "big")
            pos = dstart + esz

    def _parse_audio_settings(self, data: bytes, start: int, size: int, track: _TrackInfo):
        pos = start
        end = start + size
        while pos < end:
            try:
                eid, esz, ehdr = read_element_header(data, pos)
            except ValueError:
                break
            dstart = pos + ehdr
            if eid == SAMPLING_FREQUENCY and esz in (4, 8):
                if esz == 4:
                    track.sample_rate = struct.unpack(">f", data[dstart:dstart+4])[0]
                else:
                    track.sample_rate = struct.unpack(">d", data[dstart:dstart+8])[0]
            elif eid == CHANNELS and esz <= 4:
                track.channels = int.from_bytes(data[dstart:dstart+esz], "big")
            pos = dstart + esz

    def _parse_codec_private(self, data: bytes, start: int, size: int,
                             codec_id: str) -> Optional[Dict[str, Any]]:
        """Parse CodecPrivate data based on codec type.

        For H.264: parses avcC (AVCDecoderConfigurationRecord) with SPS/PPS.
        For H.265: parses hvcC (HEVCDecoderConfigurationRecord) with VPS/SPS/PPS.
        For AAC: parses AudioSpecificConfig.
        For Opus: parses OpusHead.
        For Vorbis: parses identification header.
        For FLAC: parses STREAMINFO metadata block.
        """
        if size < 2:
            return None

        cp_data = data[start:start + size]

        if codec_id == "V_MPEG4/ISO/AVC" and size >= 7:
            return self._parse_avcc(cp_data)
        elif codec_id == "V_MPEGH/ISO/HEVC" and size >= 23:
            return self._parse_hvcc(cp_data)
        elif codec_id.startswith("A_AAC"):
            return self._parse_aac_codec_private(cp_data)
        elif codec_id == "A_OPUS" and size >= 11:
            return self._parse_opus_codec_private(cp_data)
        elif codec_id == "A_VORBIS" and size >= 30:
            return self._parse_vorbis_codec_private(cp_data)
        elif codec_id == "A_FLAC" and size >= 4:
            return self._parse_flac_codec_private(cp_data)
        return None

    def _parse_avcc(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Parse AVCDecoderConfigurationRecord (avcC) from CodecPrivate."""
        if len(data) < 7:
            return None
        result: Dict[str, Any] = {
            "type": "avcC",
            "configuration_version": data[0],
            "profile_idc": data[1],
            "profile_compatibility": f"0x{data[2]:02X}",
            "level_idc": data[3],
            "nalu_length_size": (data[4] & 0x03) + 1,
        }

        profile_names = {
            66: "Baseline", 77: "Main", 88: "Extended",
            100: "High", 110: "High 10", 122: "High 4:2:2",
            244: "High 4:4:4 Predictive",
        }
        result["profile_name"] = profile_names.get(data[1], f"Unknown({data[1]})")
        result["level"] = f"{data[3] / 10:.1f}"

        # Parse SPS
        num_sps = data[5] & 0x1F
        result["num_sps"] = num_sps
        pos = 6
        sps_list = []
        for _ in range(num_sps):
            if pos + 2 > len(data):
                break
            sps_len = struct.unpack(">H", data[pos:pos+2])[0]
            pos += 2
            if pos + sps_len > len(data):
                break
            sps_data = data[pos:pos+sps_len]
            pos += sps_len
            sps_fields = self._parse_h264_sps(sps_data)
            sps_list.append(sps_fields)
        if sps_list:
            result["sps"] = sps_list

        # Parse PPS
        if pos < len(data):
            num_pps = data[pos]
            result["num_pps"] = num_pps
            pos += 1
            pps_list = []
            for _ in range(num_pps):
                if pos + 2 > len(data):
                    break
                pps_len = struct.unpack(">H", data[pos:pos+2])[0]
                pos += 2
                if pos + pps_len > len(data):
                    break
                pps_data = data[pos:pos+pps_len]
                pos += pps_len
                pps_fields = self._parse_h264_pps(pps_data)
                pps_list.append(pps_fields)
            if pps_list:
                result["pps"] = pps_list

        return result

    def _parse_hvcc(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Parse HEVCDecoderConfigurationRecord (hvcC) from CodecPrivate."""
        if len(data) < 23:
            return None
        result: Dict[str, Any] = {
            "type": "hvcC",
            "configuration_version": data[0],
        }
        byte1 = data[1]
        result["general_profile_space"] = (byte1 >> 6) & 0x03
        result["general_tier_flag"] = (byte1 >> 5) & 0x01
        result["general_profile_idc"] = byte1 & 0x1F
        result["general_profile_compatibility_flags"] = struct.unpack(">I", data[2:6])[0]
        result["general_level_idc"] = data[12]
        result["level"] = f"{data[12] / 30:.1f}"

        chroma_format = (data[13] >> 6) & 0x03 if len(data) > 13 else 0
        bit_depth_luma = ((data[14] >> 5) & 0x07) + 8 if len(data) > 14 else 8
        bit_depth_chroma = ((data[14] >> 2) & 0x07) + 8 if len(data) > 14 else 8
        result["chroma_format_idc"] = chroma_format
        result["bit_depth_luma"] = bit_depth_luma
        result["bit_depth_chroma"] = bit_depth_chroma

        # Number of arrays
        if len(data) > 22:
            nalu_length_size = (data[21] & 0x03) + 1
            num_arrays = data[22]
            result["nalu_length_size"] = nalu_length_size
            result["num_arrays"] = num_arrays

            # Parse arrays (VPS/SPS/PPS)
            pos = 23
            for _ in range(num_arrays):
                if pos + 3 > len(data):
                    break
                nalu_type = data[pos] & 0x3F
                num_nalus = struct.unpack(">H", data[pos+1:pos+3])[0]
                pos += 3

                for _ in range(num_nalus):
                    if pos + 2 > len(data):
                        break
                    nalu_len = struct.unpack(">H", data[pos:pos+2])[0]
                    pos += 2
                    if pos + nalu_len > len(data):
                        break
                    nalu_data = data[pos:pos+nalu_len]
                    pos += nalu_len

                    if nalu_type == 33:  # SPS
                        sps_fields = self._parse_hevc_sps(nalu_data)
                        if sps_fields:
                            result["sps"] = sps_fields
                    elif nalu_type == 34:  # PPS
                        pps_fields = self._parse_hevc_pps(nalu_data)
                        if pps_fields:
                            result["pps"] = pps_fields
                    elif nalu_type == 32:  # VPS
                        result["vps"] = {"raw": nalu_data[:32].hex()}

        return result

    @staticmethod
    def _parse_h264_sps(sps_data: bytes) -> Dict[str, Any]:
        """Parse H.264 SPS NALU data."""
        try:
            from media_analyzer.parsers.h264.sps import parse_sps
            fields = parse_sps(sps_data)
            if fields:
                result = {}
                for entry in fields:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        result[str(entry[0])] = entry[1]
                return result
        except Exception:
            pass
        return {"raw": sps_data[:16].hex()}

    @staticmethod
    def _parse_h264_pps(pps_data: bytes) -> Dict[str, Any]:
        """Parse H.264 PPS NALU data."""
        try:
            from media_analyzer.parsers.h264.pps import parse_pps
            fields = parse_pps(pps_data)
            if fields:
                result = {}
                for entry in fields:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        result[str(entry[0])] = entry[1]
                return result
        except Exception:
            pass
        return {"raw": pps_data[:16].hex()}

    @staticmethod
    def _parse_hevc_sps(sps_data: bytes) -> Dict[str, Any]:
        """Parse H.265 SPS NALU data."""
        try:
            from media_analyzer.parsers.h265.sps import parse_hevc_sps
            fields = parse_hevc_sps(sps_data)
            if fields:
                result = {}
                for entry in fields:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        result[str(entry[0])] = entry[1]
                return result
        except Exception:
            pass
        return {"raw": sps_data[:16].hex()}

    @staticmethod
    def _parse_hevc_pps(pps_data: bytes) -> Dict[str, Any]:
        """Parse H.265 PPS NALU data."""
        try:
            from media_analyzer.parsers.h265.pps import parse_hevc_pps
            fields = parse_hevc_pps(pps_data)
            if fields:
                result = {}
                for entry in fields:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        result[str(entry[0])] = entry[1]
                return result
        except Exception:
            pass
        return {"raw": pps_data[:16].hex()}

    @staticmethod
    def _parse_aac_codec_private(data: bytes) -> Optional[Dict[str, Any]]:
        """Parse AAC AudioSpecificConfig from CodecPrivate.
        MKV stores raw AudioSpecificConfig (2-5 bytes) directly."""
        if len(data) < 2:
            return None
        result: Dict[str, Any] = {"type": "AudioSpecificConfig"}

        byte0 = data[0]
        byte1 = data[1]
        audio_object_type = (byte0 >> 3) & 0x1F
        freq_index = ((byte0 & 0x07) << 1) | ((byte1 >> 7) & 0x01)
        channel_config = (byte1 >> 3) & 0x0F

        # Extended AOT (if AOT == 31)
        if audio_object_type == 31 and len(data) >= 3:
            audio_object_type = 32 + ((byte0 & 0x07) << 3) | ((byte1 >> 5) & 0x07)
            freq_index = ((byte1 & 0x1E) >> 1)
            channel_config = ((byte1 & 0x01) << 3) | ((data[2] >> 5) & 0x07)

        freq_table = [96000, 88200, 64000, 48000, 44100, 32000,
                      24000, 22050, 16000, 12000, 11025, 8000, 7350]
        sample_rate = freq_table[freq_index] if freq_index < len(freq_table) else 0

        # If freq_index == 0xF, explicit 24-bit sample rate follows
        if freq_index == 0x0F and len(data) >= 5:
            sample_rate = (data[1] & 0x7F) << 17 | data[2] << 9 | data[3] << 1 | (data[4] >> 7)

        aot_names = {
            1: "AAC Main", 2: "AAC-LC", 3: "AAC SSR",
            4: "AAC LTP", 5: "SBR (HE-AAC)", 6: "AAC Scalable",
            23: "ER AAC LD", 29: "PS (HE-AAC v2)", 39: "ER AAC ELD",
        }
        channel_names = {
            1: "Mono (1ch)", 2: "Stereo (2ch)", 3: "3.0 (3ch)",
            4: "4.0 (4ch)", 5: "5.0 (5ch)", 6: "5.1 (6ch)", 7: "7.1 (8ch)",
        }

        result["audio_object_type"] = audio_object_type
        result["audio_object_type_name"] = aot_names.get(audio_object_type, f"AOT {audio_object_type}")
        result["sampling_frequency_index"] = freq_index
        result["sampling_frequency"] = f"{sample_rate} Hz"
        result["channel_configuration"] = channel_config
        result["channel_layout"] = channel_names.get(channel_config, f"{channel_config}ch")

        # Check for SBR/PS extension
        if audio_object_type in (5, 29) or (len(data) >= 4 and audio_object_type == 2):
            result["note"] = "May contain SBR/PS extension (HE-AAC)"

        return result

    @staticmethod
    def _parse_opus_codec_private(data: bytes) -> Optional[Dict[str, Any]]:
        """Parse OpusHead structure from CodecPrivate.
        Format: 'OpusHead' magic (8 bytes) + version(1) + channels(1) +
                pre_skip(2) + input_sample_rate(4) + output_gain(2) + mapping_family(1)"""
        if len(data) < 11:
            return None
        result: Dict[str, Any] = {"type": "OpusHead"}

        # Check for 'OpusHead' magic
        offset = 0
        if data[:8] == b'OpusHead':
            offset = 8
        elif len(data) >= 19 and data[:8] != b'OpusHead':
            # Some muxers skip the magic and start at version
            offset = 0

        if offset + 11 > len(data):
            # Minimal: version(1) + channels(1) + preskip(2) + rate(4) + gain(2) + family(1)
            if len(data) >= 11:
                offset = 0
            else:
                return None

        result["version"] = data[offset]
        result["output_channel_count"] = data[offset + 1]
        result["pre_skip"] = int.from_bytes(data[offset + 2:offset + 4], "little")
        result["input_sample_rate"] = int.from_bytes(data[offset + 4:offset + 8], "little")
        result["output_gain"] = int.from_bytes(data[offset + 8:offset + 10], "little", signed=True)
        result["channel_mapping_family"] = data[offset + 10]

        channels = result["output_channel_count"]
        channel_desc = {1: "Mono", 2: "Stereo", 3: "Linear Surround",
                        4: "Quadraphonic", 5: "5.0 Surround",
                        6: "5.1 Surround", 7: "6.1 Surround", 8: "7.1 Surround"}
        result["channel_layout"] = channel_desc.get(channels, f"{channels}ch")

        # Gain in dB (Q7.8 fixed-point)
        gain_db = result["output_gain"] / 256.0
        result["output_gain_db"] = f"{gain_db:.2f} dB"

        return result

    @staticmethod
    def _parse_vorbis_codec_private(data: bytes) -> Optional[Dict[str, Any]]:
        """Parse Vorbis CodecPrivate (laced identification + comment + setup headers).
        First byte is number_of_headers (always 2 for 3 headers).
        Then sizes in Xiph lacing, then the raw header data."""
        if len(data) < 30:
            return None
        result: Dict[str, Any] = {"type": "VorbisConfig"}

        # First byte: number of packets minus 1 (should be 2)
        num_headers_minus1 = data[0]
        if num_headers_minus1 != 2:
            result["raw"] = data[:16].hex()
            return result

        # Read Xiph lacing sizes for first 2 headers
        pos = 1
        sizes = []
        for _ in range(2):
            size = 0
            while pos < len(data):
                b = data[pos]
                pos += 1
                size += b
                if b < 255:
                    break
            sizes.append(size)

        if len(sizes) < 2 or pos + sizes[0] + sizes[1] > len(data):
            result["raw"] = data[:16].hex()
            return result

        # Parse identification header (first header)
        id_header = data[pos:pos + sizes[0]]
        if len(id_header) >= 23 and id_header[0] == 0x01 and id_header[1:7] == b'vorbis':
            result["vorbis_version"] = int.from_bytes(id_header[7:11], "little")
            result["audio_channels"] = id_header[11]
            result["audio_sample_rate"] = int.from_bytes(id_header[12:16], "little")
            result["bitrate_maximum"] = int.from_bytes(id_header[16:20], "little", signed=True)
            result["bitrate_nominal"] = int.from_bytes(id_header[20:24], "little", signed=True)
            result["bitrate_minimum"] = int.from_bytes(id_header[24:28], "little", signed=True)
            blocksize_byte = id_header[28]
            result["blocksize_0"] = 1 << (blocksize_byte & 0x0F)
            result["blocksize_1"] = 1 << ((blocksize_byte >> 4) & 0x0F)

            channels = result["audio_channels"]
            channel_desc = {1: "Mono", 2: "Stereo", 3: "3.0", 4: "Quadraphonic",
                            5: "5.0 Surround", 6: "5.1 Surround", 8: "7.1 Surround"}
            result["channel_layout"] = channel_desc.get(channels, f"{channels}ch")

            # Format bitrates nicely
            nom = result["bitrate_nominal"]
            if nom > 0:
                result["bitrate_nominal_kbps"] = f"{nom / 1000:.0f} kbps"
        else:
            result["raw"] = id_header[:16].hex()

        return result

    @staticmethod
    def _parse_flac_codec_private(data: bytes) -> Optional[Dict[str, Any]]:
        """Parse FLAC CodecPrivate (fLaC marker + STREAMINFO metadata block).
        MKV format: the CodecPrivate starts with a METADATA_BLOCK_HEADER(4 bytes)
        followed by STREAMINFO (34 bytes), optionally preceded by 'fLaC' marker."""
        if len(data) < 4:
            return None
        result: Dict[str, Any] = {"type": "FLAC_STREAMINFO"}

        offset = 0
        # Check for 'fLaC' marker
        if data[:4] == b'fLaC':
            offset = 4

        # METADATA_BLOCK_HEADER: 1 byte (last-flag + type) + 3 bytes (size)
        if offset + 4 > len(data):
            return None

        block_type = data[offset] & 0x7F
        block_size = int.from_bytes(data[offset + 1:offset + 4], "big")
        offset += 4

        if block_type != 0:  # Type 0 = STREAMINFO
            # Try without header (some muxers put raw STREAMINFO)
            if len(data) >= 34:
                offset = 0
                block_size = 34
            else:
                result["raw"] = data[:16].hex()
                return result

        # STREAMINFO: 34 bytes
        if offset + 34 > len(data):
            result["raw"] = data[:min(16, len(data))].hex()
            return result

        si = data[offset:offset + 34]
        result["min_block_size"] = int.from_bytes(si[0:2], "big")
        result["max_block_size"] = int.from_bytes(si[2:4], "big")
        result["min_frame_size"] = int.from_bytes(si[4:7], "big")
        result["max_frame_size"] = int.from_bytes(si[7:10], "big")

        # Bits 80-99: sample rate(20) + channels-1(3) + bits_per_sample-1(5) + total_samples(36)
        packed = int.from_bytes(si[10:18], "big")
        total_samples = packed & 0xFFFFFFFFF  # Lower 36 bits
        bits_per_sample = ((packed >> 36) & 0x1F) + 1
        channels = ((packed >> 41) & 0x07) + 1
        sample_rate = (packed >> 44) & 0xFFFFF

        result["sample_rate"] = f"{sample_rate} Hz"
        result["channels"] = channels
        result["bits_per_sample"] = bits_per_sample
        result["total_samples"] = total_samples

        if sample_rate > 0 and total_samples > 0:
            duration_s = total_samples / sample_rate
            mins = int(duration_s // 60)
            secs = duration_s % 60
            result["duration"] = f"{mins:02d}:{secs:06.3f} ({duration_s:.3f}s)"

        channel_desc = {1: "Mono", 2: "Stereo", 3: "3.0",
                        4: "Quadraphonic", 5: "5.0", 6: "5.1", 8: "7.1"}
        result["channel_layout"] = channel_desc.get(channels, f"{channels}ch")

        # MD5 signature (16 bytes at offset 18)
        md5 = si[18:34].hex()
        if md5 != "0" * 32:
            result["md5_signature"] = md5

        return result

    def _get_element_detail(self, data: bytes, elem_id: int, start: int, size: int) -> str:
        """Get human-readable detail for leaf elements."""
        if size == 0:
            return ""

        # String elements
        if elem_id in (DOC_TYPE, CODEC_ID, 0x437C):  # DocType, CodecID, ChapLanguage
            return data[start:start+size].decode("ascii", errors="replace").rstrip('\x00')
        elif elem_id in (MUXING_APP, WRITING_APP, TITLE, 0x466E, 0x4660, 0x4661,
                         0x45A3, 0x4487, 0x85, CODEC_NAME):
            # UTF-8 strings: MuxingApp, WritingApp, Title, FileName, FileDescription,
            # FileMediaType, TagName, TagString, ChapString, CodecName
            return data[start:start+size].decode("utf-8", errors="replace").rstrip('\x00')
        elif elem_id == 0x22B59C or elem_id == 0x22B59D:  # Language, LanguageBCP47
            return data[start:start+size].decode("ascii", errors="replace").rstrip('\x00')

        # Integer elements
        elif elem_id in (TIMESTAMP_SCALE, TRACK_NUMBER, TRACK_UID, PIXEL_WIDTH,
                         PIXEL_HEIGHT, CHANNELS, CLUSTER_TIMESTAMP, 0x56AA, 0x56BB,
                         0x55EE, 0x6264, 0x54B0, 0x54BA, 0x54B2,
                         0x5378, 0x68CA, 0x63C5, 0x46AE,
                         0x55B1, 0x55B5, 0x55B7, 0x55B8, 0x55B9, 0x55BA, 0x55BB,
                         EBML_VERSION, EBML_READ_VERSION, EBML_MAX_ID_LENGTH,
                         EBML_MAX_SIZE_LENGTH, DOC_TYPE_VERSION, DOC_TYPE_READ_VERSION):
            if size <= 8:
                val = int.from_bytes(data[start:start+size], "big")
                # Add unit for specific fields
                if elem_id == TIMESTAMP_SCALE:
                    return f"{val} ns"
                elif elem_id == 0x56AA:
                    return f"{val} ns (CodecDelay)"
                elif elem_id == 0x56BB:
                    return f"{val} ns (SeekPreRoll)"
                elif elem_id == DEFAULT_DURATION_ID:
                    return f"{val} ns ({val/1000000:.2f} ms)"
                return str(val)

        # Default duration (integer, ns)
        elif elem_id == DEFAULT_DURATION_ID and size <= 8:
            val = int.from_bytes(data[start:start+size], "big")
            return f"{val} ns ({val/1000000:.2f} ms)"

        # Float elements
        elif elem_id in (DURATION, SAMPLING_FREQUENCY, 0x23314F):
            if size == 4:
                val = struct.unpack(">f", data[start:start+4])[0]
            elif size == 8:
                val = struct.unpack(">d", data[start:start+8])[0]
            else:
                return ""
            if elem_id == SAMPLING_FREQUENCY:
                return f"{val:.0f} Hz"
            elif elem_id == DURATION:
                return f"{val:.3f}"
            return f"{val}"

        # Track type
        elif elem_id == TRACK_TYPE and size <= 8:
            val = int.from_bytes(data[start:start+size], "big")
            types = {1: "video", 2: "audio", 3: "complex", 0x10: "logo",
                     0x11: "subtitle", 0x12: "buttons", 0x20: "control", 0x21: "metadata"}
            return types.get(val, str(val))

        # Boolean flag elements
        elif elem_id in (0x88, 0x9C, 0x55AA, 0x9A):  # FlagDefault, FlagLacing, FlagForced, FlagInterlaced
            if size <= 8:
                val = int.from_bytes(data[start:start+size], "big")
                return str(val)

        # CueTime (integer, in cluster timestamp units)
        elif elem_id == 0xB3 and size <= 8:
            val = int.from_bytes(data[start:start+size], "big")
            return str(val)
        elif elem_id in (0xF7, 0xF1, 0xF0) and size <= 8:  # CueTrack, CueClusterPosition, CueRelativePosition
            val = int.from_bytes(data[start:start+size], "big")
            return str(val)

        # Binary data (show size only for large, hex for small)
        elif elem_id in (CODEC_PRIVATE, 0x465C, 0x63A2):  # CodecPrivate, FileData
            if elem_id == CODEC_PRIVATE:
                # Try to provide meaningful summary based on codec
                codec_id = self._last_track_codec_id
                if codec_id == "V_MPEG4/ISO/AVC" and size >= 7:
                    # avcC format
                    profile = data[start + 1] if size > 1 else 0
                    level = data[start + 3] if size > 3 else 0
                    profile_names = {
                        66: "Baseline", 77: "Main", 88: "Extended",
                        100: "High", 110: "High 10", 122: "High 4:2:2",
                        244: "High 4:4:4 Predictive",
                    }
                    pname = profile_names.get(profile, f"Unknown({profile})")
                    return f"avcC: {pname} Profile, Level {level/10:.1f} ({size} bytes)"
                elif codec_id == "V_MPEGH/ISO/HEVC" and size >= 23:
                    # hvcC format check
                    if data[start] == 1:  # configurationVersion == 1
                        profile_idc = data[start + 1] & 0x1F
                        level_idc = data[start + 12] if size > 12 else 0
                        return f"hvcC: Profile {profile_idc}, Level {level_idc/30:.1f} ({size} bytes)"
                elif codec_id.startswith("A_AAC") and size >= 2:
                    byte0 = data[start]
                    byte1 = data[start + 1]
                    aot = (byte0 >> 3) & 0x1F
                    aot_names = {1: "Main", 2: "LC", 3: "SSR", 4: "LTP", 5: "SBR", 29: "PS"}
                    return f"AAC {aot_names.get(aot, f'AOT{aot}')} ({size} bytes)"
                elif codec_id == "A_OPUS":
                    return f"OpusHead ({size} bytes)"
                elif codec_id == "A_VORBIS":
                    return f"Vorbis Headers ({size} bytes)"
                elif codec_id == "A_FLAC":
                    return f"FLAC STREAMINFO ({size} bytes)"
                return f"{size} bytes"
            if size <= 16:
                return data[start:start+size].hex()
            return f"{size} bytes"

        # SeekID (binary, typically 4 bytes representing an element ID)
        elif elem_id == 0x53AB and size <= 4:  # SeekID
            val = int.from_bytes(data[start:start+size], "big")
            name = ELEMENT_NAMES.get(val, f"0x{val:X}")
            return name
        elif elem_id == 0x53AC and size <= 8:  # SeekPosition
            val = int.from_bytes(data[start:start+size], "big")
            return str(val)

        # Chapter timestamps (uint, ns)
        elif elem_id in (0x91, 0x92) and size <= 8:  # ChapterTimeStart/End
            val = int.from_bytes(data[start:start+size], "big")
            ms = val / 1_000_000
            return f"{ms:.0f} ms"

        # SegmentUID (binary, 16 bytes)
        elif elem_id == 0x73A4:
            return data[start:start+size].hex()

        return ""

    def _find_element_end(self, data: bytes, start: int, limit: int) -> int:
        """Find end of unknown-size element by scanning for next Cluster."""
        pos = start
        while pos < limit - 4:
            if (data[pos] == 0x1F and data[pos+1] == 0x43 and
                    data[pos+2] == 0xB6 and data[pos+3] == 0x75):
                return pos
            pos += 1
        return limit

    def get_stream_info(self) -> StreamInfo:
        """Return aggregate stream info."""
        format_name = "WebM" if self._doc_type == "webm" else "MKV"

        duration_ms = 0
        if self._duration_units > 0:
            duration_ms = int(self._duration_units * self._timestamp_scale_ns / 1_000_000)
        elif self._max_timestamp_ms > 0:
            duration_ms = self._max_timestamp_ms

        video_codec = None
        audio_codec = None
        width = None
        height = None
        for t in self._tracks.values():
            if t.track_type == TRACK_TYPE_VIDEO and not video_codec:
                video_codec = t.codec_label
                width = t.width or None
                height = t.height or None
            elif t.track_type == TRACK_TYPE_AUDIO and not audio_codec:
                audio_codec = t.codec_label

        return StreamInfo(
            source_path="",
            format_name=format_name,
            duration_ms=duration_ms,
            total_tags=self._tag_count,
            video_tags=self._video_count,
            audio_tags=self._audio_count,
            video_codec=video_codec,
            audio_codec=audio_codec,
            width=width,
            height=height,
        )
