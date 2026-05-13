"""Core data models for media analysis."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Any, Dict, List


class TagType(IntEnum):
    """FLV tag types."""
    HEADER = 0      # Virtual type for FLV file header display
    AUDIO = 8
    VIDEO = 9
    SCRIPT = 18


class FrameType(IntEnum):
    """Video frame types."""
    KEY = 1
    INTER = 2
    DISPOSABLE_INTER = 3
    GENERATED_KEY = 4
    VIDEO_INFO = 5


class VideoCodec(IntEnum):
    """Video codec identifiers."""
    JPEG = 1
    SORENSON_H263 = 2
    SCREEN_VIDEO = 3
    VP6 = 4
    VP6_ALPHA = 5
    SCREEN_VIDEO_V2 = 6
    AVC = 7        # H.264
    HEVC = 12      # H.265 (enhanced FLV)
    AV1 = 13      # AV1 (enhanced FLV)


class AudioCodec(IntEnum):
    """Audio codec identifiers."""
    LINEAR_PCM = 0
    ADPCM = 1
    MP3 = 2
    LINEAR_PCM_LE = 3
    NELLYMOSER_16K = 4
    NELLYMOSER_8K = 5
    NELLYMOSER = 6
    G711_A = 7
    G711_MU = 8
    AAC = 10
    SPEEX = 11
    MP3_8K = 14
    DEVICE_SPECIFIC = 15


class AVCPacketType(IntEnum):
    """AVC/HEVC packet types."""
    SEQUENCE_HEADER = 0
    NALU = 1
    END_OF_SEQUENCE = 2


class AACPacketType(IntEnum):
    """AAC packet types."""
    SEQUENCE_HEADER = 0
    RAW = 1


class H264NALUType(IntEnum):
    """H.264/AVC NALU types."""
    UNSPECIFIED = 0
    SLICE_NON_IDR = 1    # Coded slice of a non-IDR picture
    SLICE_DPA = 2        # Coded slice data partition A
    SLICE_DPB = 3        # Coded slice data partition B
    SLICE_DPC = 4        # Coded slice data partition C
    SLICE_IDR = 5        # Coded slice of an IDR picture
    SEI = 6              # Supplemental enhancement information
    SPS = 7              # Sequence parameter set
    PPS = 8              # Picture parameter set
    AUD = 9              # Access unit delimiter
    END_SEQUENCE = 10    # End of sequence
    END_STREAM = 11      # End of stream
    FILLER = 12          # Filler data
    SPS_EXT = 13         # SPS extension
    PREFIX_NALU = 14     # Prefix NAL unit
    SUBSET_SPS = 15      # Subset SPS
    DPS = 16             # Depth parameter set
    SLICE_AUX = 19       # Coded slice of an auxiliary coded picture
    SLICE_EXT = 20       # Coded slice extension
    SLICE_3D_EXT = 21    # Coded slice extension for 3D-AVC


class H265NALUType(IntEnum):
    """H.265/HEVC NALU types."""
    TRAIL_N = 0
    TRAIL_R = 1
    TSA_N = 2
    TSA_R = 3
    STSA_N = 4
    STSA_R = 5
    RADL_N = 6
    RADL_R = 7
    RASL_N = 8
    RASL_R = 9
    RSV_VCL_N10 = 10
    RSV_VCL_R11 = 11
    RSV_VCL_N12 = 12
    RSV_VCL_R13 = 13
    RSV_VCL_N14 = 14
    RSV_VCL_R15 = 15
    BLA_W_LP = 16
    BLA_W_RADL = 17
    BLA_N_LP = 18
    IDR_W_RADL = 19
    IDR_N_LP = 20
    CRA = 21
    VPS = 32             # Video parameter set
    SPS = 33             # Sequence parameter set
    PPS = 34             # Picture parameter set
    AUD = 35             # Access unit delimiter
    EOS = 36             # End of sequence
    EOB = 37             # End of bitstream
    FILLER = 38          # Filler data
    PREFIX_SEI = 39      # Supplemental enhancement information (prefix)
    SUFFIX_SEI = 40      # Supplemental enhancement information (suffix)


@dataclass(slots=True)
class NALUInfo:
    """Information about a single NALU within a video tag."""
    index: int               # NALU index within this tag
    nalu_type: int           # Raw NALU type value
    nalu_type_name: str      # Human-readable name
    size: int                # NALU data size in bytes
    offset_in_tag: int       # Byte offset relative to tag data start
    header_bytes: bytes      # First few bytes of the NALU header (for display)
    is_vcl: bool = False     # True if this is a VCL (video coding layer) NALU


@dataclass(slots=True)
class FLVHeaderInfo:
    """Structured FLV header info for display in the table."""
    version: int
    has_audio: bool
    has_video: bool
    data_offset: int
    type_flags_byte: int


@dataclass(slots=True)
class PacketInfo:
    """Core data model for one FLV tag - displayed as one table row."""
    index: int
    tag_type: TagType
    timestamp: int              # DTS in milliseconds
    data_size: int              # Payload size in bytes
    offset: int                 # Byte offset of tag in file/stream
    stream_id: int              # Always 0 for FLV
    tag_total_size: int         # Total tag size including header (11 + data_size)

    # Video-specific (None for non-video)
    frame_type: Optional[FrameType] = None
    video_codec: Optional[VideoCodec] = None
    avc_packet_type: Optional[AVCPacketType] = None
    composition_time: Optional[int] = None   # CTS offset (SI24)

    # Audio-specific (None for non-audio)
    audio_codec: Optional[AudioCodec] = None
    sample_rate: Optional[int] = None
    sample_size: Optional[int] = None   # 8 or 16 bit
    channels: Optional[int] = None      # 1=mono, 2=stereo
    aac_packet_type: Optional[AACPacketType] = None

    # Script-specific
    script_name: Optional[str] = None
    script_data: Optional[Dict[str, Any]] = None

    # NALU list for video frames containing multiple NALUs
    nalu_list: Optional[List[NALUInfo]] = None

    # FLV header info (only for HEADER type pseudo-tag)
    header_info: Optional[FLVHeaderInfo] = None

    @property
    def dts(self) -> int:
        """Decode timestamp."""
        return self.timestamp

    @property
    def pts(self) -> Optional[int]:
        """Presentation timestamp (DTS + CTS)."""
        if self.composition_time is not None:
            return self.timestamp + self.composition_time
        return self.timestamp

    @property
    def type_label(self) -> str:
        """Human-readable tag type label."""
        labels = {
            TagType.HEADER: "Header",
            TagType.AUDIO: "Audio",
            TagType.VIDEO: "Video",
            TagType.SCRIPT: "Script",
        }
        return labels.get(self.tag_type, f"Unknown({self.tag_type})")

    @property
    def codec_label(self) -> str:
        """Human-readable codec name."""
        if self.video_codec is not None:
            return self.video_codec.name
        elif self.audio_codec is not None:
            return self.audio_codec.name
        return ""

    @property
    def frame_label(self) -> str:
        """Human-readable frame type."""
        if self.frame_type is not None:
            return self.frame_type.name
        return ""

    @property
    def detail_label(self) -> str:
        """Additional detail info for display."""
        parts = []
        if self.tag_type == TagType.HEADER:
            if self.header_info:
                hi = self.header_info
                flags = []
                if hi.has_video:
                    flags.append("Video")
                if hi.has_audio:
                    flags.append("Audio")
                parts.append(f"FLV v{hi.version}")
                parts.append("+".join(flags) if flags else "None")
                parts.append(f"HeaderSize={hi.data_offset}")
        elif self.tag_type == TagType.VIDEO:
            if self.avc_packet_type is not None:
                parts.append(self.avc_packet_type.name)
            if self.nalu_list:
                nalu_types = [n.nalu_type_name for n in self.nalu_list]
                parts.append(f"NALUs: {', '.join(nalu_types)}")
        elif self.tag_type == TagType.AUDIO:
            if self.aac_packet_type is not None:
                parts.append(self.aac_packet_type.name)
            if self.sample_rate:
                parts.append(f"{self.sample_rate}Hz")
            if self.channels:
                parts.append("Stereo" if self.channels == 2 else "Mono")
        elif self.tag_type == TagType.SCRIPT:
            if self.script_name:
                parts.append(self.script_name)
        return " | ".join(parts)


@dataclass
class FLVHeader:
    """Parsed FLV file header."""
    version: int
    has_audio: bool
    has_video: bool
    data_offset: int       # Header size (usually 9)
    raw_bytes: bytes       # Original header bytes


@dataclass
class StreamInfo:
    """Aggregate metadata about the parsed stream."""
    source_path: str
    format_name: str = "FLV"
    duration_ms: int = 0
    total_tags: int = 0
    video_tags: int = 0
    audio_tags: int = 0
    script_tags: int = 0
    file_size: int = 0
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    framerate: Optional[float] = None
    bitrate: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
