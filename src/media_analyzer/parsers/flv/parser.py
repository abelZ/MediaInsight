"""FLV format parser - raw byte-level parsing without ffmpeg."""

import struct
from typing import Generator, BinaryIO, Optional, Dict, Any

from media_analyzer.parsers.base import BaseParser
from media_analyzer.core.models import (
    PacketInfo, StreamInfo, FLVHeader, FLVHeaderInfo,
    TagType, FrameType, VideoCodec, AudioCodec,
    AVCPacketType, AACPacketType,
    NALUInfo, H264NALUType, H265NALUType,
)
from media_analyzer.parsers.flv.script import AMF0Decoder


class FLVParseError(Exception):
    """Raised when FLV parsing encounters an error."""
    pass


class FLVParser(BaseParser):
    """
    Incremental FLV parser operating on raw bytes.

    Supports two modes:
      1. Memory-mapped file (seekable, random access, best performance)
      2. Stream mode (non-seekable, forward-only, for network streams)

    FLV File Structure:
      - Header (9 bytes): "FLV" + version + flags + data_offset
      - Body: PreviousTagSize0 (4 bytes) + [Tag + PreviousTagSize]*

    Tag Structure (11 bytes header + data):
      - TagType (1 byte, lower 5 bits): 8=audio, 9=video, 18=script
      - DataSize (3 bytes, big-endian uint24)
      - Timestamp (3 bytes, big-endian uint24, lower 24 bits)
      - TimestampExtended (1 byte, upper 8 bits of timestamp)
      - StreamID (3 bytes, always 0)
      - Data (DataSize bytes)
    """

    SIGNATURE = b"FLV"
    TAG_HEADER_SIZE = 11        # 1 + 3 + 3 + 1 + 3 bytes
    PREV_TAG_SIZE_FIELD = 4     # uint32 before each tag

    def __init__(self):
        self._header: Optional[FLVHeader] = None
        self._stream_info: Optional[StreamInfo] = None
        self._tag_count = 0
        self._video_count = 0
        self._audio_count = 0
        self._script_count = 0
        self._max_timestamp = 0
        self._metadata: Optional[Dict[str, Any]] = None

    @classmethod
    def sniff(cls, header_bytes: bytes) -> bool:
        """Check if data starts with FLV signature."""
        return len(header_bytes) >= 3 and header_bytes[:3] == cls.SIGNATURE

    def parse_header(self, data: bytes) -> FLVHeader:
        """
        Parse FLV file header (9+ bytes).

        Structure:
          Bytes 0-2: Signature "FLV"
          Byte 3:    Version (usually 1)
          Byte 4:    Type flags (bit0=video, bit2=audio)
          Bytes 5-8: Data offset (uint32, header size, usually 9)
        """
        if len(data) < 9:
            raise FLVParseError(f"Insufficient data for FLV header: {len(data)} bytes")
        if data[:3] != self.SIGNATURE:
            raise FLVParseError(f"Invalid FLV signature: {data[:3]!r}")

        version = data[3]
        type_flags = data[4]
        has_audio = bool(type_flags & 0x04)
        has_video = bool(type_flags & 0x01)
        data_offset = struct.unpack(">I", data[5:9])[0]

        self._header = FLVHeader(
            version=version,
            has_audio=has_audio,
            has_video=has_video,
            data_offset=data_offset,
            raw_bytes=data[:data_offset] if len(data) >= data_offset else data[:9],
        )
        return self._header

    def parse_incremental(self, source: BinaryIO) -> Generator[PacketInfo, None, None]:
        """
        Yields PacketInfo for each FLV tag.

        Algorithm:
          1. Read & validate FLV header (9 bytes)
          2. Skip to first PreviousTagSize0 (4 bytes, should be 0)
          3. Loop: read 11-byte tag header -> read DataSize payload -> yield PacketInfo
          4. Read next PreviousTagSize (4 bytes) -> repeat
        """
        # Step 1: Read and parse header
        header_data = source.read(9)
        if len(header_data) < 9:
            raise FLVParseError("File too short for FLV header")

        self.parse_header(header_data)

        # Yield FLV header as the first pseudo-tag (index 0)
        header_packet = PacketInfo(
            index=0,
            tag_type=TagType.HEADER,
            timestamp=0,
            data_size=self._header.data_offset,
            offset=0,
            stream_id=0,
            tag_total_size=self._header.data_offset,
            header_info=FLVHeaderInfo(
                version=self._header.version,
                has_audio=self._header.has_audio,
                has_video=self._header.has_video,
                data_offset=self._header.data_offset,
                type_flags_byte=header_data[4],
            ),
        )
        yield header_packet
        self._tag_count = 1  # header counts as index 0

        # Skip any extra header bytes (data_offset may be > 9)
        extra = self._header.data_offset - 9
        if extra > 0:
            skipped = source.read(extra)
            if len(skipped) < extra:
                raise FLVParseError("Truncated FLV header extension")

        # Step 2: Read PreviousTagSize0 (should be 0)
        prev_tag_size_bytes = source.read(self.PREV_TAG_SIZE_FIELD)
        if len(prev_tag_size_bytes) < self.PREV_TAG_SIZE_FIELD:
            return

        # Current byte offset: right after PreviousTagSize0
        offset = self._header.data_offset + self.PREV_TAG_SIZE_FIELD

        self._video_count = 0
        self._audio_count = 0
        self._script_count = 0
        self._max_timestamp = 0

        # Step 3: Parse tags
        while True:
            # Read 11-byte tag header
            tag_header = source.read(self.TAG_HEADER_SIZE)
            if len(tag_header) < self.TAG_HEADER_SIZE:
                break  # EOF or incomplete data

            tag_offset = offset  # This tag starts here

            # Parse tag header fields
            tag_type_byte = tag_header[0] & 0x1F  # Lower 5 bits
            data_size = struct.unpack(">I", b"\x00" + tag_header[1:4])[0]
            timestamp_low = struct.unpack(">I", b"\x00" + tag_header[4:7])[0]
            timestamp_ext = tag_header[7]
            stream_id = struct.unpack(">I", b"\x00" + tag_header[8:11])[0]

            # Full timestamp: extended byte is the upper 8 bits
            full_timestamp = (timestamp_ext << 24) | timestamp_low

            # Track max timestamp for duration
            if full_timestamp > self._max_timestamp:
                self._max_timestamp = full_timestamp

            # Read tag body/payload
            if data_size > 0:
                tag_data = source.read(data_size)
                if len(tag_data) < data_size:
                    break  # Truncated file
            else:
                tag_data = b""

            # Determine tag type
            try:
                tag_type = TagType(tag_type_byte)
            except ValueError:
                # Unknown tag type - skip it
                # Read PreviousTagSize and continue
                prev = source.read(self.PREV_TAG_SIZE_FIELD)
                if len(prev) < self.PREV_TAG_SIZE_FIELD:
                    break
                offset += self.TAG_HEADER_SIZE + data_size + self.PREV_TAG_SIZE_FIELD
                continue

            # Build PacketInfo
            packet = PacketInfo(
                index=self._tag_count,
                tag_type=tag_type,
                timestamp=full_timestamp,
                data_size=data_size,
                offset=tag_offset,
                stream_id=stream_id,
                tag_total_size=self.TAG_HEADER_SIZE + data_size,
            )

            # Parse sub-fields based on type
            if tag_type == TagType.VIDEO and data_size > 0:
                self._parse_video_tag(packet, tag_data)
                self._video_count += 1
            elif tag_type == TagType.AUDIO and data_size > 0:
                self._parse_audio_tag(packet, tag_data)
                self._audio_count += 1
            elif tag_type == TagType.SCRIPT and data_size > 0:
                self._parse_script_tag(packet, tag_data)
                self._script_count += 1

            yield packet
            self._tag_count += 1

            # Read PreviousTagSize (4 bytes after tag data)
            prev = source.read(self.PREV_TAG_SIZE_FIELD)
            if len(prev) < self.PREV_TAG_SIZE_FIELD:
                break
            offset += self.TAG_HEADER_SIZE + data_size + self.PREV_TAG_SIZE_FIELD

    def _parse_video_tag(self, packet: PacketInfo, data: bytes) -> None:
        """
        Parse video tag data.

        First byte: FrameType (upper 4 bits) | CodecID (lower 4 bits)
        For AVC (CodecID=7):
          Byte 1: AVCPacketType (0=seq header, 1=NALU, 2=end)
          Bytes 2-4: CompositionTime (SI24, signed 24-bit big-endian)
        """
        first_byte = data[0]
        frame_type_val = (first_byte >> 4) & 0x0F
        codec_id_val = first_byte & 0x0F

        # Parse frame type
        try:
            packet.frame_type = FrameType(frame_type_val)
        except ValueError:
            packet.frame_type = None

        # Parse codec ID
        try:
            packet.video_codec = VideoCodec(codec_id_val)
        except ValueError:
            packet.video_codec = None

        # AVC/HEVC specific fields
        if codec_id_val in (VideoCodec.AVC, 12, 13) and len(data) >= 5:
            try:
                packet.avc_packet_type = AVCPacketType(data[1])
            except ValueError:
                pass

            # CompositionTime is SI24 (signed 24-bit big-endian)
            ct_bytes = data[2:5]
            ct = struct.unpack(">I", b"\x00" + ct_bytes)[0]
            # Sign extension for 24-bit signed integer
            if ct >= 0x800000:
                ct -= 0x1000000
            packet.composition_time = ct

            # Parse NALUs from the payload
            nalu_payload = data[5:]  # After FrameType+CodecID(1) + AVCPacketType(1) + CTS(3)
            if packet.avc_packet_type == AVCPacketType.SEQUENCE_HEADER:
                # AVC: parse AVCDecoderConfigurationRecord for SPS/PPS
                if codec_id_val == VideoCodec.AVC:
                    packet.nalu_list = self._parse_avc_decoder_config(nalu_payload, 5)
                elif codec_id_val == 12:  # HEVC
                    packet.nalu_list = self._parse_hevc_decoder_config(nalu_payload, 5)
            elif packet.avc_packet_type == AVCPacketType.NALU:
                # Parse length-prefixed NALUs
                if codec_id_val == VideoCodec.AVC:
                    packet.nalu_list = self._parse_avc_nalus(nalu_payload, 5)
                elif codec_id_val == 12:  # HEVC
                    packet.nalu_list = self._parse_hevc_nalus(nalu_payload, 5)

    def _parse_avc_nalus(self, data: bytes, base_offset: int,
                          length_size: int = 4) -> list:
        """
        Parse AVC NALUs from length-prefixed format.

        AVC NALU stream in FLV uses length-prefixed NALUs:
          [NALULength (4 bytes)] [NALU data] [NALULength] [NALU data] ...

        NALU header (1 byte):
          forbidden_zero_bit (1 bit) | nal_ref_idc (2 bits) | nal_unit_type (5 bits)
        """
        nalus = []
        pos = 0
        idx = 0

        while pos + length_size <= len(data):
            # Read NALU length (big-endian, typically 4 bytes)
            if length_size == 4:
                nalu_len = struct.unpack(">I", data[pos:pos + 4])[0]
            elif length_size == 2:
                nalu_len = struct.unpack(">H", data[pos:pos + 2])[0]
            elif length_size == 1:
                nalu_len = data[pos]
            elif length_size == 3:
                nalu_len = struct.unpack(">I", b"\x00" + data[pos:pos + 3])[0]
            else:
                break

            pos += length_size

            if nalu_len == 0 or pos + nalu_len > len(data):
                break

            # Parse NALU header byte
            nalu_header = data[pos]
            nalu_type_val = nalu_header & 0x1F  # Lower 5 bits

            # Get type name
            try:
                nalu_type_enum = H264NALUType(nalu_type_val)
                nalu_type_name = nalu_type_enum.name
            except ValueError:
                nalu_type_name = f"UNKNOWN({nalu_type_val})"

            # Determine if VCL
            is_vcl = nalu_type_val in (1, 2, 3, 4, 5)

            # Capture header bytes for display (up to 4 bytes)
            header_bytes = data[pos:pos + min(4, nalu_len)]

            nalu_info = NALUInfo(
                index=idx,
                nalu_type=nalu_type_val,
                nalu_type_name=nalu_type_name,
                size=nalu_len,
                offset_in_tag=base_offset + pos - length_size,
                header_bytes=header_bytes,
                is_vcl=is_vcl,
            )
            nalus.append(nalu_info)

            pos += nalu_len
            idx += 1

        return nalus if nalus else None

    def _parse_hevc_nalus(self, data: bytes, base_offset: int,
                           length_size: int = 4) -> list:
        """
        Parse HEVC NALUs from length-prefixed format.

        HEVC NALU header (2 bytes):
          forbidden_zero_bit (1) | nal_unit_type (6) | nuh_layer_id (6) | nuh_temporal_id_plus1 (3)
        """
        nalus = []
        pos = 0
        idx = 0

        while pos + length_size <= len(data):
            if length_size == 4:
                nalu_len = struct.unpack(">I", data[pos:pos + 4])[0]
            elif length_size == 2:
                nalu_len = struct.unpack(">H", data[pos:pos + 2])[0]
            else:
                break

            pos += length_size

            if nalu_len == 0 or pos + nalu_len > len(data):
                break

            if nalu_len < 2:
                pos += nalu_len
                idx += 1
                continue

            # Parse HEVC NALU header (2 bytes)
            nalu_type_val = (data[pos] >> 1) & 0x3F  # Bits 1-6 of first byte

            try:
                nalu_type_enum = H265NALUType(nalu_type_val)
                nalu_type_name = nalu_type_enum.name
            except ValueError:
                nalu_type_name = f"UNKNOWN({nalu_type_val})"

            # VCL NALUs: types 0-31
            is_vcl = nalu_type_val <= 31

            header_bytes = data[pos:pos + min(4, nalu_len)]

            nalu_info = NALUInfo(
                index=idx,
                nalu_type=nalu_type_val,
                nalu_type_name=nalu_type_name,
                size=nalu_len,
                offset_in_tag=base_offset + pos - length_size,
                header_bytes=header_bytes,
                is_vcl=is_vcl,
            )
            nalus.append(nalu_info)

            pos += nalu_len
            idx += 1

        return nalus if nalus else None

    def _parse_avc_decoder_config(self, data: bytes, base_offset: int) -> list:
        """
        Parse AVCDecoderConfigurationRecord to extract SPS/PPS NALUs.

        Structure:
          configurationVersion (1)
          AVCProfileIndication (1)
          profile_compatibility (1)
          AVCLevelIndication (1)
          lengthSizeMinusOne (lower 2 bits of byte 4) + reserved
          numOfSequenceParameterSets (lower 5 bits of byte 5) + reserved
          for each SPS:
            sequenceParameterSetLength (2 bytes)
            sequenceParameterSetNALUnit (variable)
          numOfPictureParameterSets (1 byte)
          for each PPS:
            pictureParameterSetLength (2 bytes)
            pictureParameterSetNALUnit (variable)
        """
        nalus = []
        if len(data) < 6:
            return None

        idx = 0
        pos = 5  # Skip to numOfSPS byte

        # Number of SPS
        num_sps = data[pos] & 0x1F
        pos += 1

        for _ in range(num_sps):
            if pos + 2 > len(data):
                break
            sps_len = struct.unpack(">H", data[pos:pos + 2])[0]
            pos += 2
            if pos + sps_len > len(data):
                break

            nalu_header = data[pos] if sps_len > 0 else 0
            nalu_type_val = nalu_header & 0x1F
            header_bytes = data[pos:pos + min(4, sps_len)]

            nalus.append(NALUInfo(
                index=idx,
                nalu_type=nalu_type_val,
                nalu_type_name="SPS",
                size=sps_len,
                offset_in_tag=base_offset + pos - 2,
                header_bytes=header_bytes,
                is_vcl=False,
            ))
            pos += sps_len
            idx += 1

        # Number of PPS
        if pos < len(data):
            num_pps = data[pos]
            pos += 1

            for _ in range(num_pps):
                if pos + 2 > len(data):
                    break
                pps_len = struct.unpack(">H", data[pos:pos + 2])[0]
                pos += 2
                if pos + pps_len > len(data):
                    break

                nalu_header = data[pos] if pps_len > 0 else 0
                nalu_type_val = nalu_header & 0x1F
                header_bytes = data[pos:pos + min(4, pps_len)]

                nalus.append(NALUInfo(
                    index=idx,
                    nalu_type=nalu_type_val,
                    nalu_type_name="PPS",
                    size=pps_len,
                    offset_in_tag=base_offset + pos - 2,
                    header_bytes=header_bytes,
                    is_vcl=False,
                ))
                pos += pps_len
                idx += 1

        return nalus if nalus else None

    def _parse_hevc_decoder_config(self, data: bytes, base_offset: int) -> list:
        """
        Parse HEVCDecoderConfigurationRecord to extract VPS/SPS/PPS NALUs.

        Structure (simplified):
          configurationVersion (1)
          ... profile/level fields (22 bytes total header) ...
          numOfArrays (1 byte at offset 22)
          for each array:
            arrayCompleteness (1 bit) | reserved (1 bit) | NAL_unit_type (6 bits)
            numNalus (2 bytes)
            for each NALU:
              naluLength (2 bytes)
              naluData (variable)
        """
        nalus = []
        if len(data) < 23:
            return None

        idx = 0
        num_arrays = data[22]
        pos = 23

        for _ in range(num_arrays):
            if pos >= len(data):
                break

            nalu_type_val = data[pos] & 0x3F
            pos += 1

            if pos + 2 > len(data):
                break
            num_nalus = struct.unpack(">H", data[pos:pos + 2])[0]
            pos += 2

            try:
                type_enum = H265NALUType(nalu_type_val)
                type_name = type_enum.name
            except ValueError:
                type_name = f"UNKNOWN({nalu_type_val})"

            for _ in range(num_nalus):
                if pos + 2 > len(data):
                    break
                nalu_len = struct.unpack(">H", data[pos:pos + 2])[0]
                pos += 2
                if pos + nalu_len > len(data):
                    break

                header_bytes = data[pos:pos + min(4, nalu_len)]

                nalus.append(NALUInfo(
                    index=idx,
                    nalu_type=nalu_type_val,
                    nalu_type_name=type_name,
                    size=nalu_len,
                    offset_in_tag=base_offset + pos - 2,
                    header_bytes=header_bytes,
                    is_vcl=False,
                ))
                pos += nalu_len
                idx += 1

        return nalus if nalus else None

    def _parse_audio_tag(self, packet: PacketInfo, data: bytes) -> None:
        """
        Parse audio tag data.

        First byte: SoundFormat(4b) | SoundRate(2b) | SoundSize(1b) | SoundType(1b)
        For AAC (SoundFormat=10):
          Byte 1: AACPacketType (0=seq header, 1=raw)
        """
        first_byte = data[0]
        sound_format = (first_byte >> 4) & 0x0F
        rate_index = (first_byte >> 2) & 0x03
        sound_size = (first_byte >> 1) & 0x01
        sound_type = first_byte & 0x01

        # Parse audio codec
        try:
            packet.audio_codec = AudioCodec(sound_format)
        except ValueError:
            packet.audio_codec = None

        # Sample rate mapping
        rate_map = {0: 5500, 1: 11025, 2: 22050, 3: 44100}
        packet.sample_rate = rate_map.get(rate_index, 44100)

        # Sample size: 0=8-bit, 1=16-bit
        packet.sample_size = 16 if sound_size else 8

        # Channels: 0=mono, 1=stereo
        packet.channels = 2 if sound_type else 1

        # AAC specific
        if sound_format == AudioCodec.AAC and len(data) >= 2:
            try:
                packet.aac_packet_type = AACPacketType(data[1])
            except ValueError:
                pass

    def _parse_script_tag(self, packet: PacketInfo, data: bytes) -> None:
        """
        Parse script tag (AMF0 encoded metadata).

        Typically contains:
          - AMF0 String: event name (e.g., "onMetaData")
          - AMF0 Object/Array: metadata key-value pairs
        """
        try:
            decoder = AMF0Decoder(data)
            values = decoder.decode_all()

            if values and isinstance(values[0], str):
                packet.script_name = values[0]

            if len(values) >= 2 and isinstance(values[1], dict):
                packet.script_data = values[1]
                # Store metadata for stream info
                if packet.script_name == "onMetaData":
                    self._metadata = values[1]
        except Exception:
            # AMF decoding failure is non-fatal
            if len(data) >= 3 and data[0] == 0x02:
                # Try to at least get the name string
                str_len = struct.unpack(">H", data[1:3])[0]
                if len(data) >= 3 + str_len:
                    packet.script_name = data[3:3 + str_len].decode("utf-8", errors="replace")

    def get_stream_info(self) -> StreamInfo:
        """Return aggregate stream info."""
        info = StreamInfo(
            source_path="",
            format_name=f"FLV {self._header.version}" if self._header else "FLV",
            duration_ms=self._max_timestamp,
            total_tags=self._tag_count,
            video_tags=self._video_count,
            audio_tags=self._audio_count,
            script_tags=self._script_count,
        )

        if self._header:
            info.video_codec = "H.264" if self._header.has_video else None
            info.audio_codec = "AAC" if self._header.has_audio else None

        if self._metadata:
            info.metadata = self._metadata
            if "width" in self._metadata:
                info.width = int(self._metadata["width"])
            if "height" in self._metadata:
                info.height = int(self._metadata["height"])
            if "framerate" in self._metadata:
                info.framerate = float(self._metadata["framerate"])
            if "videodatarate" in self._metadata and "audiodatarate" in self._metadata:
                info.bitrate = int(
                    self._metadata["videodatarate"] + self._metadata["audiodatarate"]
                )

        return info
