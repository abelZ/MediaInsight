"""FLV format parser - raw byte-level parsing without ffmpeg."""

import struct
from typing import Generator, BinaryIO, Optional, Dict, Any

from media_analyzer.parsers.base import BaseParser
from media_analyzer.core.models import (
    PacketInfo, StreamInfo, FLVHeader, FLVHeaderInfo,
    TagType, FrameType, VideoCodec, AudioCodec,
    AVCPacketType, AACPacketType, EnhancedPacketType,
    NALUInfo, H264NALUType, H265NALUType, AV1OBUType,
    FOURCC_HEVC, FOURCC_AV1, FOURCC_VP9, FOURCC_AVC,
)
from media_analyzer.parsers.flv.script import AMF0Decoder
from media_analyzer.parsers.h264.sps import parse_sps
from media_analyzer.parsers.h264.pps import parse_pps
from media_analyzer.parsers.h265.vps import parse_hevc_vps
from media_analyzer.parsers.h265.sps import parse_hevc_sps
from media_analyzer.parsers.h265.pps import parse_hevc_pps


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
        Parse video tag data. Supports both traditional FLV and Enhanced RTMP.

        Traditional format:
          Byte 0: FrameType (upper 4 bits) | CodecID (lower 4 bits)
          Byte 1: AVCPacketType (0=seq header, 1=NALU, 2=end)
          Bytes 2-4: CompositionTime (SI24)

        Enhanced RTMP (IsExHeader=1, bit 7 of byte 0 set):
          Byte 0: [1 | PacketType(4b) | 0(3b)] — when high bit set
          Actually: byte0 & 0x80 → enhanced mode
          Byte 0: high nibble has 0b1000 (IsExHeader), low nibble = PacketType
          Bytes 1-4: FourCC codec identifier ('hvc1', 'av01', etc.)
          Bytes 5+: Payload (for CodedFrames: CTS(3) + NALUs)
        """
        first_byte = data[0]
        is_ex_header = (first_byte & 0x80) != 0

        if is_ex_header:
            self._parse_video_tag_enhanced(packet, data)
        else:
            self._parse_video_tag_traditional(packet, data)

    def _parse_video_tag_traditional(self, packet: PacketInfo, data: bytes) -> None:
        """Parse traditional FLV video tag (CodecID in lower 4 bits)."""
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

        # AVC/HEVC/AV1 specific fields
        if codec_id_val in (VideoCodec.AVC, 12, 13) and len(data) >= 5:
            try:
                packet.avc_packet_type = AVCPacketType(data[1])
            except ValueError:
                pass

            # CompositionTime is SI24 (signed 24-bit big-endian)
            ct_bytes = data[2:5]
            ct = struct.unpack(">I", b"\x00" + ct_bytes)[0]
            if ct >= 0x800000:
                ct -= 0x1000000
            packet.composition_time = ct

            # Parse NALUs/OBUs from the payload
            nalu_payload = data[5:]
            if packet.avc_packet_type == AVCPacketType.SEQUENCE_HEADER:
                if codec_id_val == VideoCodec.AVC:
                    packet.nalu_list = self._parse_avc_decoder_config(nalu_payload, 5)
                elif codec_id_val == 12:  # HEVC
                    packet.nalu_list = self._parse_hevc_decoder_config(nalu_payload, 5)
                elif codec_id_val == 13:  # AV1
                    packet.nalu_list = self._parse_av1_config(nalu_payload, 5)
            elif packet.avc_packet_type == AVCPacketType.NALU:
                if codec_id_val == VideoCodec.AVC:
                    packet.nalu_list = self._parse_avc_nalus(nalu_payload, 5)
                elif codec_id_val == 12:  # HEVC
                    packet.nalu_list = self._parse_hevc_nalus(nalu_payload, 5)
                elif codec_id_val == 13:  # AV1
                    packet.nalu_list = self._parse_av1_obus(nalu_payload, 5)

    def _parse_video_tag_enhanced(self, packet: PacketInfo, data: bytes) -> None:
        """
        Parse Enhanced RTMP video tag.

        Byte 0: [1(IsExHeader) | FrameType(3b) | PacketType(4b)]
        Bytes 1-4: FourCC codec identifier
        Bytes 5+: Payload
        """
        if len(data) < 5:
            return

        first_byte = data[0]
        packet.is_enhanced_rtmp = True

        # Frame type from bits 4-6 (3 bits after IsExHeader bit)
        frame_type_val = (first_byte >> 4) & 0x07
        # Map enhanced frame types: 1=key, 2=inter, 3=disposable, 4=generated_key
        try:
            packet.frame_type = FrameType(frame_type_val) if frame_type_val > 0 else None
        except ValueError:
            packet.frame_type = None

        # Packet type from lower 4 bits
        pkt_type = first_byte & 0x0F

        # FourCC codec identifier
        fourcc = data[1:5]
        packet.fourcc = fourcc

        # Map FourCC to VideoCodec enum
        if fourcc == FOURCC_HEVC:
            packet.video_codec = VideoCodec.HEVC
        elif fourcc == FOURCC_AV1:
            packet.video_codec = VideoCodec.AV1
        elif fourcc == FOURCC_AVC:
            packet.video_codec = VideoCodec.AVC
        else:
            # VP9 or unknown
            pass

        # Map enhanced packet type to AVCPacketType for unified handling
        if pkt_type == EnhancedPacketType.SEQUENCE_START:
            packet.avc_packet_type = AVCPacketType.SEQUENCE_HEADER
        elif pkt_type in (EnhancedPacketType.CODED_FRAMES,
                          EnhancedPacketType.CODED_FRAMES_X):
            packet.avc_packet_type = AVCPacketType.NALU
        elif pkt_type == EnhancedPacketType.SEQUENCE_END:
            packet.avc_packet_type = AVCPacketType.END_OF_SEQUENCE

        # Payload starts at byte 5
        payload = data[5:]
        payload_offset = 5  # offset within tag data

        # For CODED_FRAMES (type 1), first 3 bytes are CTS
        if pkt_type == EnhancedPacketType.CODED_FRAMES and len(payload) >= 3:
            ct = struct.unpack(">I", b"\x00" + payload[:3])[0]
            if ct >= 0x800000:
                ct -= 0x1000000
            packet.composition_time = ct
            payload = payload[3:]
            payload_offset += 3
        elif pkt_type == EnhancedPacketType.CODED_FRAMES_X:
            packet.composition_time = 0

        # Parse based on codec + packet type
        if pkt_type == EnhancedPacketType.SEQUENCE_START:
            if fourcc == FOURCC_HEVC:
                packet.nalu_list = self._parse_hevc_decoder_config(payload, payload_offset)
            elif fourcc == FOURCC_AV1:
                packet.nalu_list = self._parse_av1_config(payload, payload_offset)
            elif fourcc == FOURCC_AVC:
                packet.nalu_list = self._parse_avc_decoder_config(payload, payload_offset)
        elif pkt_type in (EnhancedPacketType.CODED_FRAMES,
                          EnhancedPacketType.CODED_FRAMES_X):
            if fourcc == FOURCC_HEVC:
                packet.nalu_list = self._parse_hevc_nalus(payload, payload_offset)
            elif fourcc == FOURCC_AV1:
                packet.nalu_list = self._parse_av1_obus(payload, payload_offset)
            elif fourcc == FOURCC_AVC:
                packet.nalu_list = self._parse_avc_nalus(payload, payload_offset)

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

            # Parse SPS/PPS bitstream fields
            nalu_data = data[pos:pos + nalu_len]
            if nalu_type_val == H264NALUType.SPS and nalu_len > 4:
                nalu_info.parsed_fields = parse_sps(nalu_data)
            elif nalu_type_val == H264NALUType.PPS and nalu_len > 2:
                nalu_info.parsed_fields = parse_pps(nalu_data)

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

            # Parse HEVC VPS/SPS/PPS bitstream fields
            nalu_data = data[pos:pos + nalu_len]
            if nalu_type_val == H265NALUType.VPS and nalu_len > 4:
                nalu_info.parsed_fields = parse_hevc_vps(nalu_data)
            elif nalu_type_val == H265NALUType.SPS and nalu_len > 4:
                nalu_info.parsed_fields = parse_hevc_sps(nalu_data)
            elif nalu_type_val == H265NALUType.PPS and nalu_len > 3:
                nalu_info.parsed_fields = parse_hevc_pps(nalu_data)

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

            sps_nalu = NALUInfo(
                index=idx,
                nalu_type=nalu_type_val,
                nalu_type_name="SPS",
                size=sps_len,
                offset_in_tag=base_offset + pos - 2,
                header_bytes=header_bytes,
                is_vcl=False,
            )
            # Parse SPS bitstream fields
            if sps_len > 4:
                sps_nalu.parsed_fields = parse_sps(data[pos:pos + sps_len])
            nalus.append(sps_nalu)
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

                pps_nalu = NALUInfo(
                    index=idx,
                    nalu_type=nalu_type_val,
                    nalu_type_name="PPS",
                    size=pps_len,
                    offset_in_tag=base_offset + pos - 2,
                    header_bytes=header_bytes,
                    is_vcl=False,
                )
                # Parse PPS bitstream fields
                if pps_len > 2:
                    pps_nalu.parsed_fields = parse_pps(data[pos:pos + pps_len])
                nalus.append(pps_nalu)
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

                hevc_nalu = NALUInfo(
                    index=idx,
                    nalu_type=nalu_type_val,
                    nalu_type_name=type_name,
                    size=nalu_len,
                    offset_in_tag=base_offset + pos - 2,
                    header_bytes=header_bytes,
                    is_vcl=False,
                )
                # Parse HEVC VPS/SPS/PPS bitstream fields
                nalu_data = data[pos:pos + nalu_len]
                if nalu_type_val == H265NALUType.VPS and nalu_len > 4:
                    hevc_nalu.parsed_fields = parse_hevc_vps(nalu_data)
                elif nalu_type_val == H265NALUType.SPS and nalu_len > 4:
                    hevc_nalu.parsed_fields = parse_hevc_sps(nalu_data)
                elif nalu_type_val == H265NALUType.PPS and nalu_len > 3:
                    hevc_nalu.parsed_fields = parse_hevc_pps(nalu_data)

                nalus.append(hevc_nalu)
                pos += nalu_len
                idx += 1

        return nalus if nalus else None

        return nalus if nalus else None

    def _parse_av1_config(self, data: bytes, base_offset: int) -> list:
        """
        Parse AV1CodecConfigurationRecord.

        Structure (4 bytes fixed header + configOBUs):
          Byte 0: marker(1) | version(7)
          Byte 1: seq_profile(3) | seq_level_idx_0(5)
          Byte 2: seq_tier_0(1) | high_bitdepth(1) | twelve_bit(1) | monochrome(1)
                   | chroma_subsampling_x(1) | chroma_subsampling_y(1)
                   | chroma_sample_position(2)
          Byte 3: reserved(3) | initial_presentation_delay_present(1)
                   | initial_presentation_delay_minus_one(4) or reserved(4)
          Bytes 4+: configOBUs[] (sequence header OBU etc.)
        """
        nalus = []
        if len(data) < 4:
            return None

        # Parse the 4-byte config header as a "config" NALUInfo
        marker = (data[0] >> 7) & 0x01
        version = data[0] & 0x7F
        seq_profile = (data[1] >> 5) & 0x07
        seq_level_idx = data[1] & 0x1F
        seq_tier = (data[2] >> 7) & 0x01
        high_bitdepth = (data[2] >> 6) & 0x01
        twelve_bit = (data[2] >> 5) & 0x01
        monochrome = (data[2] >> 4) & 0x01
        chroma_sub_x = (data[2] >> 3) & 0x01
        chroma_sub_y = (data[2] >> 2) & 0x01
        chroma_sample_pos = data[2] & 0x03

        # Compute bit depth
        if high_bitdepth and twelve_bit:
            bit_depth = 12
        elif high_bitdepth:
            bit_depth = 10
        else:
            bit_depth = 8

        # Chroma format
        if monochrome:
            chroma = "Monochrome"
        elif chroma_sub_x and chroma_sub_y:
            chroma = "4:2:0"
        elif chroma_sub_x:
            chroma = "4:2:2"
        else:
            chroma = "4:4:4"

        profile_names = {0: "Main", 1: "High", 2: "Professional"}

        config_fields = [
            ("version", version),
            ("seq_profile", f"{seq_profile} ({profile_names.get(seq_profile, 'Unknown')})"),
            ("seq_level_idx", seq_level_idx),
            ("seq_tier", "High" if seq_tier else "Main"),
            ("bit_depth", bit_depth),
            ("monochrome", bool(monochrome)),
            ("chroma_format", chroma),
            ("chroma_sample_position", chroma_sample_pos),
        ]

        config_nalu = NALUInfo(
            index=0,
            nalu_type=0,
            nalu_type_name="AV1Config",
            size=len(data),
            offset_in_tag=base_offset,
            header_bytes=data[:4],
            is_vcl=False,
            parsed_fields=config_fields,
        )
        nalus.append(config_nalu)

        # Parse configOBUs (typically contains sequence header OBU)
        if len(data) > 4:
            obu_list = self._parse_av1_obus(data[4:], base_offset + 4)
            if obu_list:
                for obu in obu_list:
                    obu.index = len(nalus)
                    nalus.append(obu)

        return nalus if nalus else None

    def _parse_av1_obus(self, data: bytes, base_offset: int) -> list:
        """
        Parse AV1 OBU (Open Bitstream Unit) stream.

        OBU Header (1-2 bytes):
          obu_forbidden_bit (1) | obu_type (4) | obu_extension_flag (1)
          | obu_has_size_field (1) | obu_reserved_1bit (1)
        If obu_has_size_field: followed by leb128-encoded size
        """
        nalus = []
        pos = 0
        idx = 0

        while pos < len(data):
            if pos >= len(data):
                break

            obu_start = pos
            header_byte = data[pos]
            pos += 1

            obu_type = (header_byte >> 3) & 0x0F
            extension_flag = (header_byte >> 2) & 0x01
            has_size_field = (header_byte >> 1) & 0x01

            if extension_flag:
                if pos >= len(data):
                    break
                pos += 1  # Skip extension byte

            # Read OBU size (leb128)
            obu_size = 0
            if has_size_field:
                obu_size, bytes_read = self._read_leb128(data, pos)
                pos += bytes_read
            else:
                # No size field — rest of data is this OBU
                obu_size = len(data) - pos

            if pos + obu_size > len(data):
                obu_size = len(data) - pos

            # Get OBU type name
            try:
                obu_type_enum = AV1OBUType(obu_type)
                obu_type_name = obu_type_enum.name
            except ValueError:
                obu_type_name = f"UNKNOWN({obu_type})"

            header_bytes = data[obu_start:obu_start + min(4, pos - obu_start + obu_size)]

            nalu_info = NALUInfo(
                index=idx,
                nalu_type=obu_type,
                nalu_type_name=f"OBU_{obu_type_name}",
                size=obu_size,
                offset_in_tag=base_offset + obu_start,
                header_bytes=header_bytes,
                is_vcl=(obu_type in (3, 4, 6)),  # Frame header, tile group, frame
            )

            # Parse sequence header OBU
            if obu_type == AV1OBUType.SEQUENCE_HEADER and obu_size > 2:
                nalu_info.parsed_fields = self._parse_av1_sequence_header(
                    data[pos:pos + obu_size])

            nalus.append(nalu_info)
            pos += obu_size
            idx += 1

        return nalus if nalus else None

    @staticmethod
    def _read_leb128(data: bytes, pos: int) -> tuple:
        """Read a leb128-encoded unsigned integer. Returns (value, bytes_consumed)."""
        value = 0
        bytes_read = 0
        for i in range(8):  # Max 8 bytes
            if pos + i >= len(data):
                break
            byte = data[pos + i]
            value |= (byte & 0x7F) << (i * 7)
            bytes_read += 1
            if (byte & 0x80) == 0:
                break
        return value, bytes_read

    @staticmethod
    def _parse_av1_sequence_header(data: bytes) -> list:
        """
        Parse AV1 Sequence Header OBU basic fields.
        Extracts profile, level, dimensions, bit depth, color info.
        """
        from media_analyzer.parsers.h264.bitreader import BitReader

        try:
            reader = BitReader(data)
            fields = []

            # seq_profile: u(3)
            seq_profile = reader.read_bits(3)
            profile_names = {0: "Main", 1: "High", 2: "Professional"}
            fields.append(("seq_profile",
                          f"{seq_profile} ({profile_names.get(seq_profile, 'Unknown')})"))

            # still_picture: u(1)
            fields.append(("still_picture", reader.read_bool()))

            # reduced_still_picture_header: u(1)
            reduced_header = reader.read_bool()
            fields.append(("reduced_still_picture_header", reduced_header))

            if reduced_header:
                # seq_level_idx[0]: u(5)
                fields.append(("seq_level_idx", reader.read_bits(5)))
            else:
                # timing_info_present_flag
                timing_present = reader.read_bool()
                if timing_present:
                    num_units = reader.read_bits(32)
                    time_scale = reader.read_bits(32)
                    equal_picture_interval = reader.read_bool()
                    t_children = [
                        ("num_units_in_display_tick", num_units),
                        ("time_scale", time_scale),
                        ("equal_picture_interval", equal_picture_interval),
                    ]
                    if equal_picture_interval:
                        t_children.append(("num_ticks_per_picture", reader.read_ue() + 1))
                    if num_units > 0:
                        t_children.append(("framerate", f"{time_scale / num_units:.4f}"))
                    fields.append(("timing_info", True, t_children))

                # initial_display_delay_present_flag
                reader.read_bool()

                # operating_points
                operating_points_cnt = reader.read_bits(5) + 1
                for i in range(operating_points_cnt):
                    reader.read_bits(12)  # operating_point_idc
                    level_idx = reader.read_bits(5)
                    if i == 0:
                        fields.append(("seq_level_idx", level_idx))
                    if level_idx > 7:
                        reader.read_bits(1)  # seq_tier

            # frame_width_bits_minus_1: u(4)
            frame_width_bits = reader.read_bits(4) + 1
            # frame_height_bits_minus_1: u(4)
            frame_height_bits = reader.read_bits(4) + 1

            # max_frame_width_minus_1: u(n)
            max_width = reader.read_bits(frame_width_bits) + 1
            # max_frame_height_minus_1: u(n)
            max_height = reader.read_bits(frame_height_bits) + 1

            fields.append(("max_frame_width", max_width))
            fields.append(("max_frame_height", max_height))

            # frame_id_numbers_present_flag (if not reduced_header)
            if not reduced_header:
                frame_id_present = reader.read_bool()
                if frame_id_present:
                    reader.read_bits(4)  # delta_frame_id_length
                    reader.read_bits(3)  # additional_frame_id_length

            # use_128x128_superblock
            fields.append(("use_128x128_superblock", reader.read_bool()))

            # enable_filter_intra, enable_intra_edge_filter
            fields.append(("enable_filter_intra", reader.read_bool()))
            fields.append(("enable_intra_edge_filter", reader.read_bool()))

            # Additional enable flags (if not reduced_header)
            if not reduced_header:
                fields.append(("enable_interintra_compound", reader.read_bool()))
                fields.append(("enable_masked_compound", reader.read_bool()))
                fields.append(("enable_warped_motion", reader.read_bool()))
                fields.append(("enable_dual_filter", reader.read_bool()))
                enable_order_hint = reader.read_bool()
                fields.append(("enable_order_hint", enable_order_hint))
                if enable_order_hint:
                    fields.append(("enable_jnt_comp", reader.read_bool()))
                    fields.append(("enable_ref_frame_mvs", reader.read_bool()))

                # seq_choose_screen_content_tools
                seq_force_screen = reader.read_bool()
                if not seq_force_screen:
                    reader.read_bits(1)  # seq_force_screen_content_tools

                # seq_choose_integer_mv
                seq_force_int_mv = reader.read_bool()
                if not seq_force_int_mv:
                    reader.read_bits(1)

                if enable_order_hint:
                    fields.append(("order_hint_bits", reader.read_bits(3) + 1))

            # enable_superres, enable_cdef, enable_restoration
            fields.append(("enable_superres", reader.read_bool()))
            fields.append(("enable_cdef", reader.read_bool()))
            fields.append(("enable_restoration", reader.read_bool()))

            # color_config
            color_children = _parse_av1_color_config(reader, seq_profile)
            fields.append(("color_config", True, color_children))

            # film_grain_params_present
            fields.append(("film_grain_params_present", reader.read_bool()))

            return fields

        except (EOFError, ValueError, IndexError):
            return fields if fields else None

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

            # Store all AMF values for full display
            packet.script_amf_values = values

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


def _parse_av1_color_config(reader, seq_profile) -> list:
    """Parse AV1 color_config fields (module-level helper)."""
    fields = []
    try:
        high_bitdepth = reader.read_bool()
        if seq_profile == 2 and high_bitdepth:
            twelve_bit = reader.read_bool()
            bit_depth = 12 if twelve_bit else 10
        elif seq_profile <= 2:
            bit_depth = 10 if high_bitdepth else 8
        else:
            bit_depth = 8

        fields.append(("bit_depth", bit_depth))

        if seq_profile == 1:
            mono_chrome = False
        else:
            mono_chrome = reader.read_bool()
        fields.append(("mono_chrome", mono_chrome))

        color_desc_present = reader.read_bool()
        if color_desc_present:
            fields.append(("color_primaries", reader.read_bits(8)))
            fields.append(("transfer_characteristics", reader.read_bits(8)))
            fields.append(("matrix_coefficients", reader.read_bits(8)))

        if mono_chrome:
            fields.append(("color_range", reader.read_bool()))
        else:
            color_range = reader.read_bool()
            fields.append(("color_range", color_range))
            if seq_profile in (0, 1):
                subsampling_x = 1
                subsampling_y = 1
            elif bit_depth == 12:
                subsampling_x = reader.read_bits(1)
                if subsampling_x:
                    subsampling_y = reader.read_bits(1)
                else:
                    subsampling_y = 0
            else:
                subsampling_x = 1
                subsampling_y = 0

            if subsampling_x and subsampling_y:
                fields.append(("chroma_format", "4:2:0"))
                fields.append(("chroma_sample_position", reader.read_bits(2)))
            elif subsampling_x:
                fields.append(("chroma_format", "4:2:2"))
            else:
                fields.append(("chroma_format", "4:4:4"))

    except (EOFError, ValueError):
        pass
    return fields
