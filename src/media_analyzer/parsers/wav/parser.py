"""WAV (RIFF/WAVE) format parser.

Parses the RIFF chunk hierarchy and displays it as a tree.
Extracts audio format details from the fmt chunk.

Reference: https://www.mmsp.ece.mcgill.ca/Documents/AudioFormats/WAVE/WAVE.html
"""

import logging
import struct
from typing import Generator, BinaryIO, Dict, Optional, Any

from media_analyzer.parsers.base import BaseParser
from media_analyzer.core.models import PacketInfo, StreamInfo, TagType

logger = logging.getLogger(__name__)

# Audio format codes
AUDIO_FORMATS = {
    0x0001: "PCM",
    0x0002: "Microsoft ADPCM",
    0x0003: "IEEE Float",
    0x0006: "A-law",
    0x0007: "μ-law",
    0x0011: "IMA ADPCM",
    0x0016: "ITU G.723 ADPCM",
    0x0031: "GSM 6.10",
    0x0040: "ITU G.721 ADPCM",
    0x0050: "MPEG",
    0x0055: "MP3",
    0x00FF: "AAC",
    0x0160: "WMA v1",
    0x0161: "WMA v2",
    0x0162: "WMA Pro",
    0x0163: "WMA Lossless",
    0x1610: "WMA Voice",
    0xFFFE: "Extensible",
}

# LIST INFO chunk IDs
INFO_KEYS = {
    "IART": "Artist",
    "ICMT": "Comment",
    "ICOP": "Copyright",
    "ICRD": "Creation Date",
    "IENG": "Engineer",
    "IGNR": "Genre",
    "INAM": "Title",
    "IPRD": "Product",
    "ISFT": "Software",
    "ISRC": "Source",
    "ISBJ": "Subject",
    "ITRK": "Track Number",
}


class WAVParser(BaseParser):
    """
    Parser for WAV (RIFF/WAVE) audio files.

    Yields one PacketInfo per RIFF chunk for tree display.
    """

    def __init__(self):
        self._audio_format: int = 0
        self._num_channels: int = 0
        self._sample_rate: int = 0
        self._byte_rate: int = 0
        self._block_align: int = 0
        self._bits_per_sample: int = 0
        self._data_size: int = 0
        self._chunk_count: int = 0
        self._file_size: int = 0

    @classmethod
    def sniff(cls, header_bytes: bytes) -> bool:
        """Check RIFF/WAVE magic."""
        return (len(header_bytes) >= 12 and
                header_bytes[:4] == b'RIFF' and
                header_bytes[8:12] == b'WAVE')

    def parse_header(self, data: bytes) -> dict:
        return {"format": "wav"}

    def parse_incremental(self, source: BinaryIO) -> Generator[PacketInfo, None, None]:
        """Parse WAV file, yielding PacketInfo for each RIFF chunk."""
        self._chunk_count = 0

        # Read RIFF header (12 bytes)
        riff_hdr = source.read(12)
        if len(riff_hdr) < 12:
            return

        riff_size = struct.unpack_from("<I", riff_hdr, 4)[0]
        self._file_size = riff_size + 8

        # Yield RIFF/WAVE header
        yield PacketInfo(
            index=0,
            tag_type=TagType.HEADER,
            timestamp=0,
            data_size=riff_size,
            offset=0,
            stream_id=0,
            tag_total_size=self._file_size,
            script_data={
                "box_type": "RIFF (WAVE)",
                "depth": 0,
                "is_container": True,
                "riff_layout": True,
                "fields": {
                    "Format": "WAVE",
                    "File Size": f"{self._file_size:,} bytes",
                },
            },
        )
        self._chunk_count = 1

        # Parse sub-chunks
        pos = 12
        end = 8 + riff_size  # End of RIFF data

        while pos < end:
            chunk_hdr = source.read(8)
            if len(chunk_hdr) < 8:
                break

            chunk_id = chunk_hdr[:4].decode("ascii", errors="replace")
            chunk_size = struct.unpack_from("<I", chunk_hdr, 4)[0]
            chunk_data_start = pos + 8

            # Read chunk data
            chunk_data = source.read(chunk_size)
            if len(chunk_data) < chunk_size:
                chunk_data = chunk_data  # Partial read OK

            # Parse specific chunks
            fields = {}
            byte_ranges = {}  # field_name -> (offset_from_chunk_start, length)
            detail = ""
            is_container = False

            if chunk_id == "fmt ":
                fields, detail, byte_ranges = self._parse_fmt(chunk_data)
            elif chunk_id == "data":
                self._data_size = chunk_size
                detail = f"{chunk_size:,} bytes"
                duration_s = chunk_size / self._byte_rate if self._byte_rate > 0 else 0
                fields = {
                    "Data Size": f"{chunk_size:,} bytes",
                    "Duration": f"{duration_s:.3f} s",
                }
                byte_ranges = {
                    "Data Size": (4, 4),  # chunk_size field in header
                }
            elif chunk_id == "fact":
                if len(chunk_data) >= 4:
                    sample_count = struct.unpack_from("<I", chunk_data, 0)[0]
                    fields = {"Sample Count": f"{sample_count:,}"}
                    detail = f"{sample_count:,} samples"
                    byte_ranges = {"Sample Count": (8, 4)}
            elif chunk_id == "LIST":
                is_container = True
                if len(chunk_data) >= 4:
                    list_type = chunk_data[:4].decode("ascii", errors="replace")
                    fields = {"List Type": list_type}
                    detail = list_type
            elif chunk_id == "bext":
                fields = self._parse_bext(chunk_data)
                detail = "Broadcast Extension"

            script_data = {
                "box_type": chunk_id.strip(),
                "depth": 1,
                "is_container": is_container,
                "detail": detail,
                "riff_layout": True,
            }
            if fields:
                script_data["fields"] = fields
            if byte_ranges:
                script_data["byte_ranges"] = byte_ranges

            yield PacketInfo(
                index=self._chunk_count,
                tag_type=TagType.AUDIO,
                timestamp=0,
                data_size=chunk_size,
                offset=pos,
                stream_id=0,
                tag_total_size=8 + chunk_size,
                script_data=script_data,
            )
            self._chunk_count += 1

            # Parse LIST sub-chunks
            if chunk_id == "LIST" and len(chunk_data) >= 4:
                list_type = chunk_data[:4].decode("ascii", errors="replace")
                yield from self._parse_list_chunks(
                    chunk_data[4:], chunk_data_start + 4, list_type)

            # Advance to next chunk (pad to even boundary)
            pos = chunk_data_start + chunk_size
            if chunk_size % 2 == 1:
                source.read(1)  # Skip padding byte
                pos += 1

    def _parse_fmt(self, data: bytes) -> tuple:
        """Parse fmt chunk. Returns (fields_dict, detail_str, byte_ranges_dict).
        Byte ranges are relative to chunk start (offset 0 = chunk_id byte)."""
        if len(data) < 16:
            return {}, "", {}

        # Chunk header is 8 bytes (chunk_id + chunk_size) before data
        HDR = 8

        self._audio_format = struct.unpack_from("<H", data, 0)[0]
        self._num_channels = struct.unpack_from("<H", data, 2)[0]
        self._sample_rate = struct.unpack_from("<I", data, 4)[0]
        self._byte_rate = struct.unpack_from("<I", data, 8)[0]
        self._block_align = struct.unpack_from("<H", data, 12)[0]
        self._bits_per_sample = struct.unpack_from("<H", data, 14)[0]

        format_name = AUDIO_FORMATS.get(self._audio_format, f"Unknown (0x{self._audio_format:04X})")
        channels_str = "Mono" if self._num_channels == 1 else (
            "Stereo" if self._num_channels == 2 else f"{self._num_channels}ch")

        fields = {
            "Audio Format": f"{format_name} (0x{self._audio_format:04X})",
            "Channels": f"{self._num_channels} ({channels_str})",
            "Sample Rate": f"{self._sample_rate:,} Hz",
            "Byte Rate": f"{self._byte_rate:,} bytes/s ({self._byte_rate * 8 // 1000} kbps)",
            "Block Align": f"{self._block_align} bytes",
            "Bits Per Sample": str(self._bits_per_sample),
        }

        byte_ranges = {
            "Audio Format": (HDR + 0, 2),
            "Channels": (HDR + 2, 2),
            "Sample Rate": (HDR + 4, 4),
            "Byte Rate": (HDR + 8, 4),
            "Block Align": (HDR + 12, 2),
            "Bits Per Sample": (HDR + 14, 2),
        }

        # Extended format info
        if len(data) >= 18:
            ext_size = struct.unpack_from("<H", data, 16)[0]
            fields["Extension Size"] = f"{ext_size} bytes"
            byte_ranges["Extension Size"] = (HDR + 16, 2)

        detail = f"{format_name}, {self._sample_rate}Hz, {channels_str}, {self._bits_per_sample}bit"
        return fields, detail, byte_ranges

    def _parse_bext(self, data: bytes) -> dict:
        """Parse Broadcast Audio Extension chunk."""
        fields = {}
        if len(data) >= 256:
            desc = data[:256].decode("ascii", errors="replace").rstrip('\x00').strip()
            if desc:
                fields["Description"] = desc
        if len(data) >= 288:
            originator = data[256:288].decode("ascii", errors="replace").rstrip('\x00').strip()
            if originator:
                fields["Originator"] = originator
        if len(data) >= 320:
            ref = data[288:320].decode("ascii", errors="replace").rstrip('\x00').strip()
            if ref:
                fields["Originator Reference"] = ref
        if len(data) >= 330:
            date = data[320:330].decode("ascii", errors="replace").rstrip('\x00').strip()
            if date:
                fields["Origination Date"] = date
        if len(data) >= 338:
            time_str = data[330:338].decode("ascii", errors="replace").rstrip('\x00').strip()
            if time_str:
                fields["Origination Time"] = time_str
        return fields

    def _parse_list_chunks(self, data: bytes, base_offset: int,
                           list_type: str) -> Generator[PacketInfo, None, None]:
        """Parse sub-chunks within a LIST chunk."""
        pos = 0
        while pos + 8 <= len(data):
            sub_id = data[pos:pos+4].decode("ascii", errors="replace")
            sub_size = struct.unpack_from("<I", data, pos + 4)[0]
            sub_data_start = pos + 8

            if sub_data_start + sub_size > len(data):
                break

            sub_data = data[sub_data_start:sub_data_start + sub_size]

            # For INFO list, decode as text
            detail = ""
            fields = {}
            if list_type == "INFO":
                text = sub_data.decode("ascii", errors="replace").rstrip('\x00')
                label = INFO_KEYS.get(sub_id.strip(), sub_id.strip())
                detail = text
                fields = {"Label": label, "Value": text}

            script_data = {
                "box_type": sub_id.strip(),
                "depth": 2,
                "is_container": False,
                "detail": detail,
                "riff_layout": True,
            }
            if fields:
                script_data["fields"] = fields

            yield PacketInfo(
                index=self._chunk_count,
                tag_type=TagType.SCRIPT,
                timestamp=0,
                data_size=sub_size,
                offset=base_offset + pos,
                stream_id=0,
                tag_total_size=8 + sub_size,
                script_data=script_data,
            )
            self._chunk_count += 1

            pos = sub_data_start + sub_size
            if sub_size % 2 == 1:
                pos += 1  # Pad to even

    def get_stream_info(self) -> StreamInfo:
        """Return aggregate stream info."""
        format_name = AUDIO_FORMATS.get(self._audio_format, "PCM")
        duration_ms = 0
        if self._byte_rate > 0:
            duration_ms = int(self._data_size * 1000 / self._byte_rate)

        return StreamInfo(
            source_path="",
            format_name="WAV",
            duration_ms=duration_ms,
            total_tags=self._chunk_count,
            audio_tags=self._chunk_count - 1,
            audio_codec=format_name,
            file_size=self._file_size,
        )
