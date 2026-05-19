"""M3U8 playlist parser for HLS streams."""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urljoin


@dataclass
class HLSSegment:
    """A single HLS media segment."""
    index: int
    duration: float          # EXTINF duration in seconds
    uri: str                 # Absolute URL
    title: str = ""          # EXTINF title (optional)
    byte_range: Optional[Tuple[int, int]] = None  # (length, offset)
    is_discontinuity: bool = False
    program_date_time: Optional[str] = None


@dataclass
class HLSPlaylist:
    """Parsed M3U8 playlist."""
    version: int = 1
    target_duration: float = 0
    media_sequence: int = 0
    playlist_type: Optional[str] = None  # "VOD" / "EVENT" / None (live)
    is_endlist: bool = False
    segments: List[HLSSegment] = field(default_factory=list)
    total_duration: float = 0
    is_master: bool = False  # True if this is a master playlist


def parse_m3u8(content: str, base_url: str) -> HLSPlaylist:
    """
    Parse M3U8 content into a structured HLSPlaylist.

    Args:
        content: Raw M3U8 text
        base_url: Base URL for resolving relative segment URIs

    Returns:
        HLSPlaylist with parsed segments and metadata
    """
    playlist = HLSPlaylist()
    lines = content.strip().splitlines()

    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("Invalid M3U8: missing #EXTM3U header")

    # Check if master playlist
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            playlist.is_master = True
            return playlist

    segment_index = 0
    current_duration = 0.0
    current_title = ""
    current_discontinuity = False
    current_pdt = None
    current_byte_range = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("#EXT-X-VERSION:"):
            try:
                playlist.version = int(line.split(":")[1])
            except (ValueError, IndexError):
                pass

        elif line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                playlist.target_duration = float(line.split(":")[1])
            except (ValueError, IndexError):
                pass

        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                playlist.media_sequence = int(line.split(":")[1])
            except (ValueError, IndexError):
                pass

        elif line.startswith("#EXT-X-PLAYLIST-TYPE:"):
            playlist.playlist_type = line.split(":")[1].strip()

        elif line.startswith("#EXT-X-ENDLIST"):
            playlist.is_endlist = True

        elif line.startswith("#EXTINF:"):
            # Format: #EXTINF:<duration>[,<title>]
            info = line[8:]  # Skip "#EXTINF:"
            parts = info.split(",", 1)
            try:
                current_duration = float(parts[0])
            except ValueError:
                current_duration = 0.0
            current_title = parts[1].strip() if len(parts) > 1 else ""

        elif line.startswith("#EXT-X-DISCONTINUITY"):
            current_discontinuity = True

        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            current_pdt = line.split(":", 1)[1].strip()

        elif line.startswith("#EXT-X-BYTERANGE:"):
            # Format: <length>[@<offset>]
            br = line.split(":")[1].strip()
            parts = br.split("@")
            length = int(parts[0])
            offset = int(parts[1]) if len(parts) > 1 else None
            current_byte_range = (length, offset)

        elif not line.startswith("#"):
            # This is a segment URI
            uri = urljoin(base_url, line)
            segment = HLSSegment(
                index=segment_index,
                duration=current_duration,
                uri=uri,
                title=current_title,
                is_discontinuity=current_discontinuity,
                program_date_time=current_pdt,
                byte_range=current_byte_range,
            )
            playlist.segments.append(segment)
            playlist.total_duration += current_duration
            segment_index += 1

            # Reset per-segment state
            current_duration = 0.0
            current_title = ""
            current_discontinuity = False
            current_pdt = None
            current_byte_range = None

    return playlist
