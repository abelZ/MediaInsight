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
                 'default_duration_ns')

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
            elif eid == VIDEO:
                self._parse_video_settings(data, dstart, esz, track)
            elif eid == AUDIO_ELEMENT:
                self._parse_audio_settings(data, dstart, esz, track)
            pos = dstart + esz
        if track.number > 0:
            self._tracks[track.number] = track

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
