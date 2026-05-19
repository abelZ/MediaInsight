"""MP4/MOV (ISO Base Media File Format) parser.

Parses the box (atom) hierarchy of MP4/MOV/M4A files.
Yields one PacketInfo per box in depth-first order.

Reference: ISO/IEC 14496-12 (ISO Base Media File Format)
"""

import struct
from typing import Generator, BinaryIO, Optional, Dict, List, Any

from media_analyzer.parsers.base import BaseParser
from media_analyzer.core.models import PacketInfo, StreamInfo, TagType


# Container boxes that should be recursed into
CONTAINER_BOXES = {
    b'moov', b'trak', b'mdia', b'minf', b'stbl', b'udta', b'edts',
    b'mvex', b'moof', b'traf', b'meco', b'sinf', b'schi', b'rinf',
    b'dinf', b'ilst', b'meta',  # meta has a 4-byte version/flags before children
    # Sample entry boxes (children of stsd entries) that contain sub-boxes
    b'avc1', b'avc3', b'hvc1', b'hev1', b'vp09', b'av01',
    b'mp4a', b'Opus', b'fLaC', b'ac-3', b'ec-3',
    b'encv', b'enca',  # Encrypted entries
    b'stsd',  # Sample Description (special handling)
}

# Boxes whose children should be treated as containers regardless of their type
# (e.g. ilst sub-items like \xa9too, \xa9nam are containers with 'data' child)
ILST_ITEM_CONTAINER = True  # Flag: all children of ilst are containers

# Box type display names for common boxes
BOX_DESCRIPTIONS = {
    "ftyp": "File Type",
    "moov": "Movie Container",
    "mvhd": "Movie Header",
    "trak": "Track Container",
    "tkhd": "Track Header",
    "mdia": "Media Container",
    "mdhd": "Media Header",
    "hdlr": "Handler Reference",
    "minf": "Media Information",
    "vmhd": "Video Media Header",
    "smhd": "Sound Media Header",
    "dinf": "Data Information",
    "dref": "Data Reference",
    "stbl": "Sample Table",
    "stsd": "Sample Description",
    "stts": "Time-to-Sample",
    "ctts": "Composition Offset",
    "stsc": "Sample-to-Chunk",
    "stsz": "Sample Size",
    "stco": "Chunk Offset (32-bit)",
    "co64": "Chunk Offset (64-bit)",
    "stss": "Sync Sample (Keyframes)",
    "sdtp": "Sample Dependency Type",
    "edts": "Edit Container",
    "elst": "Edit List",
    "udta": "User Data",
    "meta": "Metadata",
    "ilst": "Item List (iTunes)",
    "mdat": "Media Data",
    "free": "Free Space",
    "skip": "Skip",
    "wide": "Wide (reserved)",
    "moof": "Movie Fragment",
    "mfhd": "Movie Fragment Header",
    "traf": "Track Fragment",
    "tfhd": "Track Fragment Header",
    "tfdt": "Track Fragment Decode Time",
    "trun": "Track Run",
    "mvex": "Movie Extends",
    "trex": "Track Extends",
    "mehd": "Movie Extends Header",
    "pssh": "Protection System Specific Header",
    "sinf": "Protection Scheme Info",
    "frma": "Original Format",
    "schm": "Scheme Type",
    "schi": "Scheme Information",
    "sbgp": "Sample-to-Group",
    "sgpd": "Sample Group Description",
    "colr": "Colour Information",
    "pasp": "Pixel Aspect Ratio",
    "btrt": "Bitrate",
    "avcC": "AVC Configuration",
    "hvcC": "HEVC Configuration",
    "av1C": "AV1 Configuration",
    "esds": "ES Descriptor",
    "dOps": "Opus Specific",
}

# Epoch offset: MP4 timestamps start from 1904-01-01, Unix from 1970-01-01
MP4_EPOCH_OFFSET = 2082844800


class MP4Parser(BaseParser):
    """
    ISO Base Media File Format parser.
    Yields one PacketInfo per box in depth-first traversal order.
    """

    def __init__(self):
        self._stream_info: Optional[StreamInfo] = None
        self._box_count = 0
        self._file_size = 0
        self._timescale = 0
        self._duration = 0
        # Track sample tables for mdat sample listing
        self._tracks: List[Dict[str, Any]] = []  # Collected track info
        self._current_track: Optional[Dict[str, Any]] = None

    @classmethod
    def sniff(cls, header_bytes: bytes) -> bool:
        """Check if data looks like MP4/MOV (ftyp box at start, or other root boxes)."""
        if len(header_bytes) < 8:
            return False
        box_type = header_bytes[4:8]
        # Common first boxes in MP4/MOV files
        if box_type in (b'ftyp', b'moov', b'free', b'wide', b'mdat', b'skip', b'pnot'):
            return True
        # Also check for valid box size + known types at offset 0
        if len(header_bytes) >= 12:
            size = struct.unpack(">I", header_bytes[0:4])[0]
            if 8 <= size <= len(header_bytes) and header_bytes[4:8].isalpha():
                return True
        return False

    def parse_header(self, data: bytes) -> dict:
        return {"format": "MP4/MOV"}

    def parse_incremental(self, source: BinaryIO) -> Generator[PacketInfo, None, None]:
        """Yield one PacketInfo per MP4 box in depth-first order."""
        self._box_count = 0
        self._mdat_boxes: List[tuple] = []  # (offset, size, depth) for deferred mdat sample listing

        # Get file size
        source.seek(0, 2)
        self._file_size = source.tell()
        source.seek(0)

        # Parse root-level boxes
        yield from self._parse_boxes(source, 0, self._file_size, depth=0)

        # After all boxes parsed: emit mdat samples (handles mdat-before-moov case)
        if self._mdat_boxes and self._tracks:
            for mdat_offset, mdat_size, mdat_depth, mdat_box_index in self._mdat_boxes:
                yield from self._emit_mdat_samples(mdat_offset, mdat_size, mdat_depth, mdat_box_index)

    def _parse_boxes(self, source: BinaryIO, start: int, end: int,
                     depth: int, parent_type: bytes = b'') -> Generator[PacketInfo, None, None]:
        """Parse boxes within a range [start, end). parent_type for context-dependent parsing."""
        pos = start

        while pos < end - 8:  # Need at least 8 bytes for box header
            source.seek(pos)
            header = source.read(8)
            if len(header) < 8:
                break

            size = struct.unpack(">I", header[0:4])[0]
            box_type = header[4:8]

            # Handle special sizes
            header_size = 8
            if size == 1:
                # 64-bit extended size
                ext = source.read(8)
                if len(ext) < 8:
                    break
                size = struct.unpack(">Q", ext)[0]
                header_size = 16
            elif size == 0:
                # Box extends to end of file
                size = end - pos

            if size < header_size:
                break  # Invalid box

            # Decode box type as ASCII (handles non-ASCII like \xa9too)
            try:
                box_type_str = box_type.decode("latin-1")
            except Exception:
                box_type_str = box_type.hex()

            # Determine if this box is a container
            is_container = box_type in CONTAINER_BOXES
            # Children of ilst are always containers (e.g. \xa9too, \xa9nam, covr, etc.)
            if parent_type == b'ilst':
                is_container = True

            payload_start = pos + header_size
            payload_size = size - header_size

            # Determine child offset for container boxes with fixed headers
            # before their sub-boxes
            child_skip = 0
            if box_type == b'meta' and payload_size >= 4:
                child_skip = 4  # version(1) + flags(3)
            elif box_type == b'stsd' and payload_size >= 8:
                child_skip = 8  # version(1) + flags(3) + entry_count(4)
            elif box_type in (b'avc1', b'avc3', b'hvc1', b'hev1',
                              b'vp09', b'av01', b'encv'):
                child_skip = 78  # Video sample entry fixed header
            elif box_type in (b'mp4a', b'Opus', b'fLaC', b'ac-3',
                              b'ec-3', b'enca'):
                child_skip = 28  # Audio sample entry fixed header

            # Parse box-specific fields
            fields = self._parse_box_fields(source, box_type, pos + header_size,
                                            payload_size, header_size, parent_type)

            # Build PacketInfo
            script_data = {
                "box_type": box_type_str,
                "depth": depth,
                "is_container": is_container,
                "header_size": header_size,
            }
            if fields:
                script_data["fields"] = fields

            desc = BOX_DESCRIPTIONS.get(box_type_str, "")
            if desc:
                script_data["description"] = desc

            packet = PacketInfo(
                index=self._box_count,
                tag_type=TagType.SCRIPT,
                timestamp=0,
                data_size=payload_size,
                offset=pos,
                stream_id=0,
                tag_total_size=size,
                script_name=box_type_str,
                script_data=script_data,
            )
            self._box_count += 1
            yield packet

            # Track sample table state for mdat listing
            self._track_sample_tables(source, box_type, pos + header_size, payload_size)

            # Record mdat position for deferred sample listing
            if box_type == b'mdat':
                self._mdat_boxes.append((pos, size, depth + 1, self._box_count - 1))

            # Recurse into container boxes
            if is_container:
                child_start = payload_start + child_skip
                child_end = pos + size
                yield from self._parse_boxes(source, child_start, child_end,
                                            depth + 1, parent_type=box_type)

            # Move to next box
            pos += size

    def _parse_box_fields(self, source: BinaryIO, box_type: bytes,
                          payload_offset: int, payload_size: int,
                          header_size: int, parent_type: bytes = b'') -> Optional[Dict[str, Any]]:
        """Parse fields for specific box types."""
        if payload_size <= 0:
            return None

        # Limit read to avoid loading huge mdat
        read_size = min(payload_size, 2048)
        source.seek(payload_offset)
        data = source.read(read_size)
        if not data:
            return None

        try:
            if box_type == b'ftyp':
                return self._parse_ftyp(data, payload_size)
            elif box_type == b'mvhd':
                return self._parse_mvhd(data)
            elif box_type == b'tkhd':
                return self._parse_tkhd(data)
            elif box_type == b'mdhd':
                return self._parse_mdhd(data)
            elif box_type == b'hdlr':
                return self._parse_hdlr(data)
            elif box_type == b'stsd':
                return self._parse_stsd(data)
            elif box_type == b'stts':
                return self._parse_stts(data)
            elif box_type == b'stsc':
                return self._parse_stsc(data)
            elif box_type == b'stsz':
                return self._parse_stsz(data)
            elif box_type in (b'stco', b'co64'):
                return self._parse_stco(data, box_type == b'co64')
            elif box_type == b'stss':
                return self._parse_stss(data)
            elif box_type == b'ctts':
                return self._parse_ctts(data)
            elif box_type == b'elst':
                return self._parse_elst(data)
            elif box_type == b'vmhd':
                return self._parse_vmhd(data)
            elif box_type == b'smhd':
                return self._parse_smhd(data)
            elif box_type == b'trun':
                return self._parse_trun(data)
            elif box_type == b'tfhd':
                return self._parse_tfhd(data)
            elif box_type == b'tfdt':
                return self._parse_tfdt(data)
            elif box_type == b'mfhd':
                return self._parse_mfhd(data)
            elif box_type == b'trex':
                return self._parse_trex(data)
            elif box_type == b'avcC':
                return self._parse_avcC(data)
            elif box_type == b'hvcC':
                return self._parse_hvcC(data)
            elif box_type == b'esds':
                return self._parse_esds(data)
            elif box_type == b'av1C':
                return self._parse_av1C(data)
            elif box_type == b'data' and parent_type != b'':
                # iTunes metadata 'data' box
                return self._parse_ilst_data(data)
        except (struct.error, IndexError):
            pass

        return None

    # --- Box field parsers ---

    def _parse_ftyp(self, data: bytes, total_size: int) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        major_brand = data[0:4].decode("ascii", errors="replace")
        minor_version = struct.unpack(">I", data[4:8])[0]
        brands = []
        pos = 8
        while pos + 4 <= min(len(data), total_size):
            brands.append(data[pos:pos+4].decode("ascii", errors="replace"))
            pos += 4
        return {
            "major_brand": major_brand,
            "minor_version": minor_version,
            "compatible_brands": brands,
        }

    def _parse_mvhd(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 4:
            return {}
        version = data[0]
        if version == 0 and len(data) >= 100:
            creation_time = struct.unpack(">I", data[4:8])[0]
            modification_time = struct.unpack(">I", data[8:12])[0]
            timescale = struct.unpack(">I", data[12:16])[0]
            duration = struct.unpack(">I", data[16:20])[0]
            self._timescale = timescale
            self._duration = duration
        elif version == 1 and len(data) >= 112:
            creation_time = struct.unpack(">Q", data[4:12])[0]
            modification_time = struct.unpack(">Q", data[12:20])[0]
            timescale = struct.unpack(">I", data[20:24])[0]
            duration = struct.unpack(">Q", data[24:32])[0]
            self._timescale = timescale
            self._duration = duration
        else:
            return {"version": version}

        duration_ms = int(duration * 1000 / timescale) if timescale else 0
        rate = struct.unpack(">I", data[20 if version == 0 else 32:
                                        24 if version == 0 else 36])[0]
        return {
            "version": version,
            "timescale": timescale,
            "duration": duration,
            "duration_ms": duration_ms,
            "duration_display": self._format_duration(duration_ms),
            "rate": f"{(rate >> 16)}.{rate & 0xFFFF}",
        }

    def _parse_tkhd(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 4:
            return {}
        version = data[0]
        flags = (data[1] << 16) | (data[2] << 8) | data[3]
        if version == 0 and len(data) >= 84:
            track_id = struct.unpack(">I", data[12:16])[0]
            duration = struct.unpack(">I", data[20:24])[0]
            width = struct.unpack(">I", data[76:80])[0] / 65536.0
            height = struct.unpack(">I", data[80:84])[0] / 65536.0
        elif version == 1 and len(data) >= 96:
            track_id = struct.unpack(">I", data[20:24])[0]
            duration = struct.unpack(">Q", data[28:36])[0]
            width = struct.unpack(">I", data[88:92])[0] / 65536.0
            height = struct.unpack(">I", data[92:96])[0] / 65536.0
        else:
            return {"version": version, "flags": flags}

        return {
            "version": version,
            "flags": flags,
            "track_id": track_id,
            "duration": duration,
            "width": int(width),
            "height": int(height),
            "enabled": bool(flags & 0x01),
        }

    def _parse_mdhd(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 4:
            return {}
        version = data[0]
        if version == 0 and len(data) >= 24:
            timescale = struct.unpack(">I", data[12:16])[0]
            duration = struct.unpack(">I", data[16:20])[0]
            lang_code = struct.unpack(">H", data[20:22])[0]
        elif version == 1 and len(data) >= 36:
            timescale = struct.unpack(">I", data[20:24])[0]
            duration = struct.unpack(">Q", data[24:32])[0]
            lang_code = struct.unpack(">H", data[32:34])[0]
        else:
            return {"version": version}

        # Decode ISO 639-2 language
        lang = ""
        if lang_code:
            lang = chr(((lang_code >> 10) & 0x1F) + 0x60)
            lang += chr(((lang_code >> 5) & 0x1F) + 0x60)
            lang += chr((lang_code & 0x1F) + 0x60)

        duration_ms = int(duration * 1000 / timescale) if timescale else 0
        return {
            "version": version,
            "timescale": timescale,
            "duration": duration,
            "duration_ms": duration_ms,
            "duration_display": self._format_duration(duration_ms),
            "language": lang,
        }

    def _parse_hdlr(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 24:
            return {}
        handler_type = data[8:12].decode("ascii", errors="replace")
        # Name is null-terminated string after byte 24
        name = data[24:].split(b'\x00', 1)[0].decode("utf-8", errors="replace")
        return {
            "handler_type": handler_type,
            "name": name,
        }

    def _parse_stsd(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        entry_count = struct.unpack(">I", data[4:8])[0]
        entries = []
        pos = 8
        for _ in range(min(entry_count, 10)):  # Limit entries
            if pos + 8 > len(data):
                break
            entry_size = struct.unpack(">I", data[pos:pos+4])[0]
            codec_type = data[pos+4:pos+8].decode("ascii", errors="replace")
            entries.append(codec_type)
            pos += entry_size if entry_size > 8 else 8
        return {
            "entry_count": entry_count,
            "codecs": entries,
        }

    def _parse_stts(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        entry_count = struct.unpack(">I", data[4:8])[0]
        entries = []
        pos = 8
        for _ in range(entry_count):
            if pos + 8 > len(data):
                break
            count = struct.unpack(">I", data[pos:pos+4])[0]
            delta = struct.unpack(">I", data[pos+4:pos+8])[0]
            entries.append({"count": count, "delta": delta})
            pos += 8
        return {"entry_count": entry_count, "entries": entries}

    def _parse_stsc(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        entry_count = struct.unpack(">I", data[4:8])[0]
        entries = []
        pos = 8
        for _ in range(min(entry_count, 20)):
            if pos + 12 > len(data):
                break
            first_chunk = struct.unpack(">I", data[pos:pos+4])[0]
            samples_per_chunk = struct.unpack(">I", data[pos+4:pos+8])[0]
            sample_desc_idx = struct.unpack(">I", data[pos+8:pos+12])[0]
            entries.append({"first_chunk": first_chunk, "samples_per_chunk": samples_per_chunk,
                          "sample_description_index": sample_desc_idx})
            pos += 12
        return {"entry_count": entry_count, "entries": entries}

    def _parse_stsz(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 12:
            return {}
        sample_size = struct.unpack(">I", data[4:8])[0]
        sample_count = struct.unpack(">I", data[8:12])[0]
        result: Dict[str, Any] = {"sample_size": sample_size, "sample_count": sample_count}
        if sample_size == 0 and sample_count > 0:
            # Variable size: read all entries
            sizes = []
            pos = 12
            for _ in range(sample_count):
                if pos + 4 > len(data):
                    break
                sizes.append(struct.unpack(">I", data[pos:pos+4])[0])
                pos += 4
            result["sizes"] = sizes
            # Keep first_sizes for backward compat with detail panel display
            result["first_sizes"] = sizes[:20]
        return result

    def _parse_stco(self, data: bytes, is_co64: bool) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        entry_count = struct.unpack(">I", data[4:8])[0]
        offsets = []
        pos = 8
        entry_size = 8 if is_co64 else 4
        for _ in range(min(entry_count, 20)):
            if pos + entry_size > len(data):
                break
            if is_co64:
                offsets.append(struct.unpack(">Q", data[pos:pos+8])[0])
            else:
                offsets.append(struct.unpack(">I", data[pos:pos+4])[0])
            pos += entry_size
        return {"entry_count": entry_count, "first_offsets": offsets}

    def _parse_stss(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        entry_count = struct.unpack(">I", data[4:8])[0]
        samples = []
        pos = 8
        for _ in range(entry_count):
            if pos + 4 > len(data):
                break
            samples.append(struct.unpack(">I", data[pos:pos+4])[0])
            pos += 4
        return {"entry_count": entry_count, "sync_samples": samples}

    def _parse_ctts(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        version = data[0]
        entry_count = struct.unpack(">I", data[4:8])[0]
        entries = []
        pos = 8
        for _ in range(min(entry_count, 20)):
            if pos + 8 > len(data):
                break
            count = struct.unpack(">I", data[pos:pos+4])[0]
            if version == 0:
                offset = struct.unpack(">I", data[pos+4:pos+8])[0]
            else:
                offset = struct.unpack(">i", data[pos+4:pos+8])[0]
            entries.append({"count": count, "offset": offset})
            pos += 8
        return {"version": version, "entry_count": entry_count, "entries": entries}

    def _parse_elst(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        version = data[0]
        entry_count = struct.unpack(">I", data[4:8])[0]
        entries = []
        pos = 8
        for _ in range(min(entry_count, 10)):
            if version == 0:
                if pos + 12 > len(data):
                    break
                seg_duration = struct.unpack(">I", data[pos:pos+4])[0]
                media_time = struct.unpack(">i", data[pos+4:pos+8])[0]
                media_rate = struct.unpack(">I", data[pos+8:pos+12])[0]
                pos += 12
            else:
                if pos + 20 > len(data):
                    break
                seg_duration = struct.unpack(">Q", data[pos:pos+8])[0]
                media_time = struct.unpack(">q", data[pos+8:pos+16])[0]
                media_rate = struct.unpack(">I", data[pos+16:pos+20])[0]
                pos += 20
            entries.append({
                "segment_duration": seg_duration,
                "media_time": media_time,
                "media_rate": f"{(media_rate >> 16)}.{media_rate & 0xFFFF}",
            })
        return {"version": version, "entry_count": entry_count, "entries": entries}

    def _parse_vmhd(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 12:
            return {}
        return {
            "version": data[0],
            "graphicsmode": struct.unpack(">H", data[4:6])[0],
        }

    def _parse_smhd(self, data: bytes) -> Dict[str, Any]:
        if len(data) < 8:
            return {}
        balance = struct.unpack(">h", data[4:6])[0]
        return {
            "version": data[0],
            "balance": balance / 256.0,
        }

    def _parse_trun(self, data: bytes) -> Dict[str, Any]:
        """Parse Track Run box (in fragmented MP4)."""
        if len(data) < 8:
            return {}
        version = data[0]
        flags = (data[1] << 16) | (data[2] << 8) | data[3]
        sample_count = struct.unpack(">I", data[4:8])[0]
        result: Dict[str, Any] = {
            "version": version,
            "flags": f"0x{flags:06X}",
            "sample_count": sample_count,
        }
        pos = 8
        # Optional fields based on flags
        if flags & 0x000001:  # data_offset_present
            if pos + 4 <= len(data):
                result["data_offset"] = struct.unpack(">i", data[pos:pos+4])[0]
                pos += 4
        if flags & 0x000004:  # first_sample_flags_present
            if pos + 4 <= len(data):
                result["first_sample_flags"] = struct.unpack(">I", data[pos:pos+4])[0]
                pos += 4
        # Per-sample entries
        has_duration = bool(flags & 0x000100)
        has_size = bool(flags & 0x000200)
        has_flags = bool(flags & 0x000400)
        has_cts = bool(flags & 0x000800)
        entry_size = (4 if has_duration else 0) + (4 if has_size else 0) + \
                     (4 if has_flags else 0) + (4 if has_cts else 0)
        samples = []
        for _ in range(min(sample_count, 20)):
            if pos + entry_size > len(data):
                break
            sample = {}
            if has_duration:
                sample["duration"] = struct.unpack(">I", data[pos:pos+4])[0]
                pos += 4
            if has_size:
                sample["size"] = struct.unpack(">I", data[pos:pos+4])[0]
                pos += 4
            if has_flags:
                sample["flags"] = struct.unpack(">I", data[pos:pos+4])[0]
                pos += 4
            if has_cts:
                if version == 0:
                    sample["composition_offset"] = struct.unpack(">I", data[pos:pos+4])[0]
                else:
                    sample["composition_offset"] = struct.unpack(">i", data[pos:pos+4])[0]
                pos += 4
            samples.append(sample)
        if samples:
            result["samples"] = samples
        return result

    def _parse_tfhd(self, data: bytes) -> Dict[str, Any]:
        """Parse Track Fragment Header."""
        if len(data) < 8:
            return {}
        flags = (data[1] << 16) | (data[2] << 8) | data[3]
        track_id = struct.unpack(">I", data[4:8])[0]
        result: Dict[str, Any] = {"track_id": track_id, "flags": f"0x{flags:06X}"}
        pos = 8
        if flags & 0x000001 and pos + 8 <= len(data):
            result["base_data_offset"] = struct.unpack(">Q", data[pos:pos+8])[0]
            pos += 8
        if flags & 0x000002 and pos + 4 <= len(data):
            result["sample_description_index"] = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4
        if flags & 0x000008 and pos + 4 <= len(data):
            result["default_sample_duration"] = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4
        if flags & 0x000010 and pos + 4 <= len(data):
            result["default_sample_size"] = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4
        if flags & 0x000020 and pos + 4 <= len(data):
            result["default_sample_flags"] = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4
        return result

    def _parse_tfdt(self, data: bytes) -> Dict[str, Any]:
        """Parse Track Fragment Decode Time."""
        if len(data) < 4:
            return {}
        version = data[0]
        if version == 0 and len(data) >= 8:
            decode_time = struct.unpack(">I", data[4:8])[0]
        elif version == 1 and len(data) >= 12:
            decode_time = struct.unpack(">Q", data[4:12])[0]
        else:
            return {"version": version}
        return {"version": version, "base_media_decode_time": decode_time}

    def _parse_mfhd(self, data: bytes) -> Dict[str, Any]:
        """Parse Movie Fragment Header."""
        if len(data) < 8:
            return {}
        sequence_number = struct.unpack(">I", data[4:8])[0]
        return {"sequence_number": sequence_number}

    def _parse_trex(self, data: bytes) -> Dict[str, Any]:
        """Parse Track Extends."""
        if len(data) < 24:
            return {}
        return {
            "track_id": struct.unpack(">I", data[4:8])[0],
            "default_sample_description_index": struct.unpack(">I", data[8:12])[0],
            "default_sample_duration": struct.unpack(">I", data[12:16])[0],
            "default_sample_size": struct.unpack(">I", data[16:20])[0],
            "default_sample_flags": struct.unpack(">I", data[20:24])[0],
        }

    # ------------------------------------------------------------------
    # Codec configuration box parsers
    # ------------------------------------------------------------------

    def _parse_avcC(self, data: bytes) -> Dict[str, Any]:
        """Parse AVCDecoderConfigurationRecord (avcC box)."""
        if len(data) < 7:
            return {}
        result: Dict[str, Any] = {
            "configuration_version": data[0],
            "profile_idc": data[1],
            "profile_compatibility": f"0x{data[2]:02X}",
            "level_idc": data[3],
            "nalu_length_size": (data[4] & 0x03) + 1,
        }

        # Profile name
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
        for i in range(num_sps):
            if pos + 2 > len(data):
                break
            sps_len = struct.unpack(">H", data[pos:pos+2])[0]
            pos += 2
            if pos + sps_len > len(data):
                break
            sps_data = data[pos:pos+sps_len]
            pos += sps_len
            # Parse SPS fields using existing parser
            sps_fields = self._parse_sps_fields(sps_data)
            sps_list.append(sps_fields)
        if sps_list:
            result["sps"] = sps_list

        # Parse PPS
        if pos < len(data):
            num_pps = data[pos]
            result["num_pps"] = num_pps
            pos += 1
            pps_list = []
            for i in range(num_pps):
                if pos + 2 > len(data):
                    break
                pps_len = struct.unpack(">H", data[pos:pos+2])[0]
                pos += 2
                if pos + pps_len > len(data):
                    break
                pps_data = data[pos:pos+pps_len]
                pos += pps_len
                pps_fields = self._parse_pps_fields(pps_data)
                pps_list.append(pps_fields)
            if pps_list:
                result["pps"] = pps_list

        return result

    def _parse_hvcC(self, data: bytes) -> Dict[str, Any]:
        """Parse HEVCDecoderConfigurationRecord (hvcC box)."""
        if len(data) < 23:
            return {}
        result: Dict[str, Any] = {
            "configuration_version": data[0],
        }
        # general_profile_space(2) + general_tier_flag(1) + general_profile_idc(5)
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
                type_names = {32: "VPS", 33: "SPS", 34: "PPS"}
                type_name = type_names.get(nalu_type, f"type_{nalu_type}")

                for j in range(num_nalus):
                    if pos + 2 > len(data):
                        break
                    nalu_len = struct.unpack(">H", data[pos:pos+2])[0]
                    pos += 2
                    if pos + nalu_len > len(data):
                        break
                    nalu_data = data[pos:pos+nalu_len]
                    pos += nalu_len

                    if nalu_type == 33:  # SPS
                        sps_fields = self._parse_hevc_sps_fields(nalu_data)
                        if sps_fields:
                            result["sps"] = sps_fields
                    elif nalu_type == 34:  # PPS
                        pps_fields = self._parse_hevc_pps_fields(nalu_data)
                        if pps_fields:
                            result["pps"] = pps_fields

        return result

    @staticmethod
    def _parse_esds(data: bytes) -> Dict[str, Any]:
        """Parse ES Descriptor (esds box) — extract AudioSpecificConfig."""
        if len(data) < 4:
            return {}
        # Skip version(1) + flags(3)
        result: Dict[str, Any] = {"version": data[0]}
        pos = 4

        # ES_Descriptor tag = 0x03
        def read_descriptor_header(d, p):
            if p >= len(d):
                return None, 0, p
            tag = d[p]
            p += 1
            size = 0
            for _ in range(4):
                if p >= len(d):
                    break
                b = d[p]
                p += 1
                size = (size << 7) | (b & 0x7F)
                if not (b & 0x80):
                    break
            return tag, size, p

        tag, size, pos = read_descriptor_header(data, pos)
        if tag == 0x03:  # ES_Descriptor
            if pos + 2 <= len(data):
                es_id = struct.unpack(">H", data[pos:pos+2])[0]
                result["es_id"] = es_id
                pos += 3  # es_id(2) + stream_priority(1)

            # DecoderConfigDescriptor tag = 0x04
            tag, size, pos = read_descriptor_header(data, pos)
            if tag == 0x04 and pos + 13 <= len(data):
                object_type = data[pos]
                stream_type = (data[pos+1] >> 2) & 0x3F
                buffer_size = (data[pos+2] << 16) | (data[pos+3] << 8) | data[pos+4]
                max_bitrate = struct.unpack(">I", data[pos+5:pos+9])[0]
                avg_bitrate = struct.unpack(">I", data[pos+9:pos+13])[0]
                pos += 13

                object_type_names = {
                    0x40: "AAC-LC", 0x67: "AAC-LC (MPEG2)",
                    0x69: "MP3", 0x6B: "MP3 (MPEG1)",
                }
                result["object_type_indication"] = object_type
                result["object_type_name"] = object_type_names.get(object_type, f"0x{object_type:02X}")
                result["stream_type"] = stream_type
                result["buffer_size_db"] = buffer_size
                result["max_bitrate"] = max_bitrate
                result["avg_bitrate"] = avg_bitrate

                # DecoderSpecificInfo tag = 0x05 (AudioSpecificConfig)
                tag, size, pos = read_descriptor_header(data, pos)
                if tag == 0x05 and size >= 2 and pos + 2 <= len(data):
                    asc_byte0 = data[pos]
                    asc_byte1 = data[pos+1]
                    audio_object_type = (asc_byte0 >> 3) & 0x1F
                    freq_index = ((asc_byte0 & 0x07) << 1) | ((asc_byte1 >> 7) & 0x01)
                    channel_config = (asc_byte1 >> 3) & 0x0F

                    freq_table = [96000, 88200, 64000, 48000, 44100, 32000,
                                  24000, 22050, 16000, 12000, 11025, 8000, 7350]
                    sample_rate = freq_table[freq_index] if freq_index < len(freq_table) else 0

                    aot_names = {
                        1: "AAC Main", 2: "AAC-LC", 3: "AAC SSR",
                        4: "AAC LTP", 5: "SBR", 6: "AAC Scalable",
                        23: "ER AAC LD", 39: "ER AAC ELD",
                    }
                    result["audio_object_type"] = audio_object_type
                    result["audio_object_type_name"] = aot_names.get(audio_object_type, f"AOT {audio_object_type}")
                    result["sampling_frequency_index"] = freq_index
                    result["sampling_frequency"] = sample_rate
                    result["channel_configuration"] = channel_config

                    channel_names = {
                        1: "Mono", 2: "Stereo", 3: "3.0", 4: "4.0",
                        5: "5.0", 6: "5.1", 7: "7.1",
                    }
                    result["channel_layout"] = channel_names.get(channel_config, f"{channel_config}ch")

        return result

    @staticmethod
    def _parse_av1C(data: bytes) -> Dict[str, Any]:
        """Parse AV1CodecConfigurationRecord (av1C box)."""
        if len(data) < 4:
            return {}
        # marker(1) + version(7)
        marker = (data[0] >> 7) & 0x01
        version = data[0] & 0x7F
        # seq_profile(3) + seq_level_idx_0(5)
        seq_profile = (data[1] >> 5) & 0x07
        seq_level_idx = data[1] & 0x1F
        # seq_tier_0(1) + high_bitdepth(1) + twelve_bit(1) + monochrome(1)
        # + chroma_subsampling_x(1) + chroma_subsampling_y(1) + chroma_sample_position(2)
        seq_tier = (data[2] >> 7) & 0x01
        high_bitdepth = (data[2] >> 6) & 0x01
        twelve_bit = (data[2] >> 5) & 0x01
        monochrome = (data[2] >> 4) & 0x01
        chroma_x = (data[2] >> 3) & 0x01
        chroma_y = (data[2] >> 2) & 0x01

        bit_depth = 8
        if high_bitdepth:
            bit_depth = 12 if twelve_bit else 10

        profile_names = {0: "Main", 1: "High", 2: "Professional"}

        return {
            "version": version,
            "seq_profile": seq_profile,
            "profile_name": profile_names.get(seq_profile, f"Profile {seq_profile}"),
            "seq_level_idx": seq_level_idx,
            "seq_tier": seq_tier,
            "bit_depth": bit_depth,
            "monochrome": bool(monochrome),
            "chroma_subsampling_x": chroma_x,
            "chroma_subsampling_y": chroma_y,
        }

    @staticmethod
    def _parse_sps_fields(sps_data: bytes) -> Dict[str, Any]:
        """Parse H.264 SPS using existing parser, return as dict."""
        try:
            from media_analyzer.parsers.h264.sps import parse_sps
            fields = parse_sps(sps_data)
            if fields:
                # Convert structured entries to flat dict for display
                result = {}
                for entry in fields:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        result[str(entry[0])] = entry[1]
                return result
        except Exception:
            pass
        return {"raw": sps_data[:16].hex()}

    @staticmethod
    def _parse_pps_fields(pps_data: bytes) -> Dict[str, Any]:
        """Parse H.264 PPS using existing parser, return as dict."""
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
    def _parse_hevc_sps_fields(sps_data: bytes) -> Dict[str, Any]:
        """Parse H.265 SPS using existing parser, return as dict."""
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
    def _parse_hevc_pps_fields(pps_data: bytes) -> Dict[str, Any]:
        """Parse H.265 PPS using existing parser, return as dict."""
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
    def _parse_ilst_data(data: bytes) -> Dict[str, Any]:
        """Parse iTunes metadata 'data' box content."""
        if len(data) < 8:
            return {}
        # data box: version(1) + flags(3) + locale(4) + value
        type_indicator = struct.unpack(">I", data[0:4])[0]
        # Well-known type indicators:
        # 1 = UTF-8 text, 2 = UTF-16, 13 = JPEG, 14 = PNG, 21 = signed int
        locale = struct.unpack(">I", data[4:8])[0]
        value_data = data[8:]

        result: Dict[str, Any] = {"type_indicator": type_indicator}

        if type_indicator == 1:
            # UTF-8 text
            result["value"] = value_data.decode("utf-8", errors="replace")
        elif type_indicator == 21:
            # Integer
            if len(value_data) == 1:
                result["value"] = value_data[0]
            elif len(value_data) == 2:
                result["value"] = struct.unpack(">H", value_data)[0]
            elif len(value_data) == 4:
                result["value"] = struct.unpack(">I", value_data)[0]
            elif len(value_data) == 8:
                result["value"] = struct.unpack(">Q", value_data)[0]
        elif type_indicator in (13, 14):
            # Image data (JPEG/PNG)
            result["value"] = f"<{len(value_data)} bytes image>"
        else:
            if len(value_data) <= 64:
                result["value"] = value_data.hex()
            else:
                result["value"] = f"<{len(value_data)} bytes>"

        return result

    # ------------------------------------------------------------------
    # mdat sample/chunk listing
    # ------------------------------------------------------------------

    def _track_sample_tables(self, source: BinaryIO, box_type: bytes,
                             payload_offset: int, payload_size: int) -> None:
        """Collect sample table info during parsing for mdat listing."""
        if box_type == b'trak':
            # Start a new track
            self._current_track = {"handler": "", "stco": [], "stsz": [],
                                   "stsc": [], "stss": set(), "stts": [], "timescale": 0}
            self._tracks.append(self._current_track)
        elif box_type == b'hdlr' and self._current_track is not None:
            # Only set handler if not already set (first hdlr in trak/mdia is correct,
            # ignore subsequent hdlr from udta/meta)
            if not self._current_track["handler"] and payload_size >= 12:
                source.seek(payload_offset + 8)
                ht = source.read(4)
                self._current_track["handler"] = ht.decode("ascii", errors="replace")
        elif box_type == b'stco' and self._current_track is not None:
            source.seek(payload_offset)
            raw = source.read(min(payload_size, 65536))
            if len(raw) >= 8:
                count = struct.unpack(">I", raw[4:8])[0]
                offsets = []
                for i in range(count):
                    pos = 8 + i * 4
                    if pos + 4 > len(raw):
                        break
                    offsets.append(struct.unpack(">I", raw[pos:pos+4])[0])
                self._current_track["stco"] = offsets
        elif box_type == b'co64' and self._current_track is not None:
            source.seek(payload_offset)
            raw = source.read(min(payload_size, 131072))
            if len(raw) >= 8:
                count = struct.unpack(">I", raw[4:8])[0]
                offsets = []
                for i in range(count):
                    pos = 8 + i * 8
                    if pos + 8 > len(raw):
                        break
                    offsets.append(struct.unpack(">Q", raw[pos:pos+8])[0])
                self._current_track["stco"] = offsets
        elif box_type == b'stsz' and self._current_track is not None:
            source.seek(payload_offset)
            raw = source.read(min(payload_size, 131072))
            if len(raw) >= 12:
                sample_size = struct.unpack(">I", raw[4:8])[0]
                sample_count = struct.unpack(">I", raw[8:12])[0]
                if sample_size != 0:
                    self._current_track["stsz"] = [sample_size] * min(sample_count, 50000)
                else:
                    sizes = []
                    for i in range(sample_count):
                        pos = 12 + i * 4
                        if pos + 4 > len(raw):
                            break
                        sizes.append(struct.unpack(">I", raw[pos:pos+4])[0])
                    self._current_track["stsz"] = sizes
        elif box_type == b'stsc' and self._current_track is not None:
            source.seek(payload_offset)
            raw = source.read(min(payload_size, 65536))
            if len(raw) >= 8:
                count = struct.unpack(">I", raw[4:8])[0]
                entries = []
                for i in range(count):
                    pos = 8 + i * 12
                    if pos + 12 > len(raw):
                        break
                    fc = struct.unpack(">I", raw[pos:pos+4])[0]
                    spc = struct.unpack(">I", raw[pos+4:pos+8])[0]
                    entries.append((fc, spc))
                self._current_track["stsc"] = entries
        elif box_type == b'stss' and self._current_track is not None:
            source.seek(payload_offset)
            raw = source.read(min(payload_size, 65536))
            if len(raw) >= 8:
                count = struct.unpack(">I", raw[4:8])[0]
                sync = set()
                for i in range(count):
                    pos = 8 + i * 4
                    if pos + 4 > len(raw):
                        break
                    sync.add(struct.unpack(">I", raw[pos:pos+4])[0])
                self._current_track["stss"] = sync
        elif box_type == b'stts' and self._current_track is not None:
            source.seek(payload_offset)
            raw = source.read(min(payload_size, 131072))
            if len(raw) >= 8:
                count = struct.unpack(">I", raw[4:8])[0]
                entries = []
                for i in range(count):
                    pos = 8 + i * 8
                    if pos + 8 > len(raw):
                        break
                    sc = struct.unpack(">I", raw[pos:pos+4])[0]
                    sd = struct.unpack(">I", raw[pos+4:pos+8])[0]
                    entries.append((sc, sd))  # (sample_count, sample_delta)
                self._current_track["stts"] = entries
        elif box_type == b'mdhd' and self._current_track is not None:
            source.seek(payload_offset)
            raw = source.read(min(payload_size, 64))
            if len(raw) >= 4:
                version = raw[0]
                if version == 0 and len(raw) >= 16:
                    timescale = struct.unpack(">I", raw[12:16])[0]
                    self._current_track["timescale"] = timescale
                elif version == 1 and len(raw) >= 24:
                    timescale = struct.unpack(">I", raw[20:24])[0]
                    self._current_track["timescale"] = timescale

    def _emit_mdat_samples(self, mdat_offset: int, mdat_size: int,
                           depth: int, mdat_box_index: int) -> Generator[PacketInfo, None, None]:
        """
        Emit virtual chunk + sample entries inside mdat.

        Structure:
          mdat
          ├── track1.chunk1
          │   ├── sample1
          │   ├── sample2
          │   └── ...
          ├── track2.chunk1
          │   └── sample1
          └── ...
        """
        # Build chunk list with per-sample details
        # (chunk_offset, chunk_size, track_idx, chunk_idx, samples, handler)
        # samples: [(offset, size, is_sync, global_sample_idx_1based), ...]
        chunks = []

        for track_idx, track in enumerate(self._tracks):
            handler = track.get("handler", "????")
            if handler not in ("vide", "soun"):
                continue
            stco = track.get("stco", [])
            stsz = track.get("stsz", [])
            stsc = track.get("stsc", [])
            stss = track.get("stss", set())

            if not stco or not stsz:
                continue

            sample_idx = 0
            for chunk_idx, chunk_offset in enumerate(stco):
                # Find samples_per_chunk for this chunk
                spc = 1
                for i, (fc, s) in enumerate(stsc):
                    if chunk_idx + 1 >= fc:
                        spc = s
                    else:
                        break

                # Collect per-sample info
                chunk_size = 0
                sample_list = []
                offset_in_chunk = chunk_offset
                for j in range(spc):
                    if sample_idx + j >= len(stsz):
                        break
                    s_size = stsz[sample_idx + j]
                    is_sync = (sample_idx + j + 1) in stss
                    sample_list.append((offset_in_chunk, s_size, is_sync, sample_idx + j + 1))
                    chunk_size += s_size
                    offset_in_chunk += s_size

                chunks.append((chunk_offset, chunk_size, track_idx, chunk_idx,
                              sample_list, handler))
                sample_idx += spc

        # Sort by file offset
        chunks.sort(key=lambda x: x[0])

        # Limit to avoid overwhelming the UI
        MAX_CHUNKS = 10000
        total = len(chunks)
        if total > MAX_CHUNKS:
            chunks = chunks[:MAX_CHUNKS]

        # Emit chunk → sample hierarchy
        for offset, size, track_idx, chunk_idx, sample_list, handler in chunks:
            track_label = f"track{track_idx + 1}"
            chunk_label = f"chunk{chunk_idx + 1}"
            box_type_str = f"{track_label}.{chunk_label}"
            sample_count = len(sample_list)
            track_type = "Video" if handler == "vide" else "Audio" if handler == "soun" else handler

            has_sync = any(s[2] for s in sample_list)
            sync_str = " [KEY]" if has_sync else ""
            sample_str = f"{sample_count} samples" if sample_count > 1 else "1 sample"

            chunk_box_index = self._box_count
            tag_type = TagType.VIDEO if handler == "vide" else \
                       TagType.AUDIO if handler == "soun" else TagType.SCRIPT

            # Emit chunk node (child of mdat)
            chunk_pkt = PacketInfo(
                index=self._box_count,
                tag_type=tag_type,
                timestamp=0,
                data_size=size,
                offset=offset,
                stream_id=track_idx,
                tag_total_size=size,
                script_name=box_type_str,
                script_data={
                    "box_type": box_type_str,
                    "depth": depth,
                    "is_container": True,
                    "description": f"{track_type} {sample_str}{sync_str}",
                    "track_index": track_idx + 1,
                    "chunk_index": chunk_idx + 1,
                    "sample_count": sample_count,
                    "handler": handler,
                    "is_sync": has_sync,
                    "mdat_parent_index": mdat_box_index,
                },
            )
            self._box_count += 1
            yield chunk_pkt

            # Emit each sample as child of this chunk
            for s_offset, s_size, s_sync, s_idx in sample_list:
                sync_mark = " [KEY]" if s_sync else ""
                sample_pkt = PacketInfo(
                    index=self._box_count,
                    tag_type=tag_type,
                    timestamp=0,
                    data_size=s_size,
                    offset=s_offset,
                    stream_id=track_idx,
                    tag_total_size=s_size,
                    script_name=f"sample{s_idx}",
                    script_data={
                        "box_type": f"sample{s_idx}",
                        "depth": depth + 1,
                        "is_container": False,
                        "description": f"{track_type} Sample #{s_idx}{sync_mark}",
                        "sample_index": s_idx,
                        "track_index": track_idx + 1,
                        "chunk_index": chunk_idx + 1,
                        "handler": handler,
                        "is_sync": s_sync,
                        "chunk_parent_index": chunk_box_index,
                    },
                )
                self._box_count += 1
                yield sample_pkt

        # Truncation marker
        if total > MAX_CHUNKS:
            trunc_pkt = PacketInfo(
                index=self._box_count,
                tag_type=TagType.SCRIPT,
                timestamp=0,
                data_size=0,
                offset=0,
                stream_id=0,
                tag_total_size=0,
                script_name="...",
                script_data={
                    "box_type": "...",
                    "depth": depth,
                    "is_container": False,
                    "description": f"({total - MAX_CHUNKS} more chunks not shown)",
                    "mdat_parent_index": mdat_box_index,
                },
            )
            self._box_count += 1
            yield trunc_pkt

    @staticmethod
    def _format_duration(ms: int) -> str:
        """Format milliseconds to human-readable HH:MM:SS.mmm."""
        if ms <= 0:
            return "0:00.000"
        hours = ms // 3600000
        mins = (ms % 3600000) // 60000
        secs = (ms % 60000) / 1000.0
        if hours > 0:
            return f"{hours}:{mins:02d}:{secs:06.3f}"
        return f"{mins}:{secs:06.3f}"

    def get_stream_info(self) -> StreamInfo:
        """Return aggregate stream info."""
        duration_ms = 0
        if self._timescale > 0 and self._duration > 0:
            duration_ms = int(self._duration * 1000 / self._timescale)

        # Store tracks data for bitrate analysis
        tracks_for_bitrate = []
        for track in self._tracks:
            tracks_for_bitrate.append({
                "handler": track.get("handler", ""),
                "timescale": track.get("timescale", 0),
                "stts": track.get("stts", []),
                "stsz": track.get("stsz", []),
                "stss": track.get("stss", set()),
            })

        return StreamInfo(
            source_path="",
            format_name="MP4/MOV",
            duration_ms=duration_ms,
            total_tags=self._box_count,
            file_size=self._file_size,
            metadata={"tracks": tracks_for_bitrate},
        )
