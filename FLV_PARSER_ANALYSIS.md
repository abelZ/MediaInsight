# FLV Parser Implementation & RTMP Support Planning

## Executive Summary
This document provides a detailed analysis of the FLV parser implementation to inform RTMP support design. Since RTMP carries FLV-format media data (video/audio tags), understanding the existing FLV infrastructure is critical for RTMP integration.

---

## 1. FLV FILE STRUCTURE OVERVIEW

### 1.1 FLV Header (9 bytes minimum)
Byte 0-2:   "FLV" (signature, 3 bytes)
Byte 3:     Version (1 byte, typically 0x01)
Byte 4:     Type Flags (1 byte)
            - Bit 0: Has Video (LSB)
            - Bit 2: Has Audio
            - Bits 1,3-7: Reserved (0)
Bytes 5-8:  Data Offset (4 bytes, big-endian uint32)
            - Typically 0x00000009 (9 bytes for minimal header)
            - Can be larger if header extension exists

### 1.2 FLV Body Structure
After FLV header:
- PreviousTagSize0 (4 bytes): always 0
- [Tag 1] [PreviousTagSize] [Tag 2] [PreviousTagSize] ...

Each PreviousTagSize = 11 + DataSize

---

## 2. FLV TAG STRUCTURE (11-byte header)

### 2.1 Tag Header Format
Byte 0:        TagType (lower 5 bits): 8=Audio, 9=Video, 18=Script
Bytes 1-3:     DataSize (uint24 big-endian, payload size)
Bytes 4-6:     Timestamp (uint24 big-endian, lower 24 bits)
Byte 7:        TimestampExtended (upper 8 bits)
               Full timestamp = (ext << 24) | timestamp_low
Bytes 8-10:    StreamID (uint24, always 0 for FLV)

---

## 3. VIDEO TAG PARSING

### 3.1 Traditional Format
Byte 0: FrameType(upper 4 bits) | CodecID(lower 4 bits)
  FrameType: 1=Key, 2=Inter, 3=Disposable, 4=Generated
  CodecID: 7=H.264/AVC, 12=HEVC, 13=AV1

For AVC (CodecID=7):
- Byte 1: AVCPacketType (0=config, 1=NALUs, 2=end)
- Bytes 2-4: CompositionTime (SI24)
- Bytes 5+: NALUs in length-prefixed format [4-byte length][data]...

### 3.2 Enhanced RTMP Format (byte 0 & 0x80 = 1)
Byte 0: [1(IsExHeader) | FrameType(3) | PacketType(4)]
Bytes 1-4: FourCC (hvc1, av01, vp09, avc1)
Bytes 5+: Payload (frames: CTS(3 bytes) + NALUs)

---

## 4. AUDIO TAG PARSING

Byte 0: [SoundFormat(4) | SoundRate(2) | SoundSize(1) | SoundType(1)]
  SoundFormat: 2=MP3, 10=AAC, etc.
  SoundRate: 0=5.5k, 1=11k, 2=22k, 3=44k (Hz)
  SoundSize: 0=8-bit, 1=16-bit
  SoundType: 0=mono, 1=stereo

For AAC (SoundFormat=10):
  Byte 1: AACPacketType (0=config, 1=raw)
  Bytes 2+: AAC frame data

---

## 5. SCRIPT TAG PARSING

Uses AMF0 decoder:
- Typical structure: [String "onMetaData"] [Object with metadata]
- Common fields: duration, width, height, framerate, bitrate, filesize, codecs

---

## 6. PARSE INCREMENTAL ALGORITHM

1. Read FLV header (9 bytes) -> yield as pseudo-tag
2. Skip header extension (if data_offset > 9)
3. Read PreviousTagSize0 (4 bytes)
4. Loop until EOF:
   a. Read 11-byte tag header
   b. Extract TagType, DataSize, Timestamp, StreamID
   c. Read DataSize bytes of payload
   d. Parse based on TagType
   e. Yield PacketInfo
   f. Read next PreviousTagSize (4 bytes)

Generator-based: yields incrementally, ideal for streaming/RTMP

---

## 7. UI INTEGRATION (Main Window)

### _start_parsing(source)
- Stops existing worker if running
- Clears UI state (table, detail, hex)
- Creates ParseWorker(source)
- Connects signals: packets_ready, progress, download_progress, parse_finished, error
- Calls worker.start()

### _on_packets_ready(packets)
- Auto-detects format from first batch (TS/MP4/FLV)
- Routes to table model or box tree view
- Updates status: "{count:,} tags loaded"

### _on_download_progress(downloaded, total)
- Updates status: "Downloading: X / Y MB (Z%)"
- Enables Save As when fully_downloaded = True
- For StreamingHTTPSource only

### _stop_parsing()
- Calls worker.stop()
- Waits up to 3 seconds
- Hides progress bar

### _save_as()
- Checks source is StreamingHTTPSource
- Gets save path from user dialog
- Calls source.save_to_file(path)
- Updates status with result

---

## 8. KEY REUSE OPPORTUNITIES FOR RTMP

Already Available (No Changes):
1. _parse_video_tag_traditional() -> traditional codecs
2. _parse_video_tag_enhanced() -> Enhanced RTMP (HEVC/AV1)
3. _parse_avc_nalus() -> AVC extraction
4. _parse_hevc_nalus() -> HEVC extraction
5. _parse_av1_obus() -> AV1 extraction
6. _parse_audio_tag() -> audio parsing
7. _parse_script_tag() -> metadata
8. PacketInfo model -> direct use
9. UI threading/worker -> unchanged

Needs New Implementation:
1. StreamingRTMPSource (socket-based connection)
2. RTMP handshake protocol (C0/C1/C2, Connect)
3. RTMP chunk reassembly (typically 128-byte chunks)
4. FLV header synthesis (infer from first tags)
5. RTMPParser extending BaseParser

---

## 9. RTMP PARSER ARCHITECTURE

RTMPParser should:
1. Receive RTMP stream from StreamingRTMPSource
2. Reassemble chunks into complete RTMP messages
3. Extract FLV tag structure from each message
4. Delegate tag parsing to FLVParser methods
5. Synthesize FLV header (infer has_video/has_audio)
6. Yield PacketInfo with enhanced_rtmp flag if applicable

Integration with UI:
- ParseWorker auto-detects parser type based on source
- All downstream UI code unchanged
- download_progress still works for RTMP streaming

---

## 10. IMPLEMENTATION ROADMAP

Phase 1: Source Layer
- StreamingRTMPSource with RTMP handshake
- Chunk stream reassembly engine
- Connection state tracking

Phase 2: Parser Layer
- RTMPParser(BaseParser)
- FLV header synthesis
- Message extraction
- Reuse all tag parsing

Phase 3: UI Integration
- Auto-detect RTMP URLs
- Route to correct parser
- Test with threading

Phase 4: Testing & Optimization
- Live/VOD RTMP streams
- Enhanced RTMP codecs
- Connection recovery
- Performance profiling

---

## Appendix: Key Constants

TagType: HEADER=0, AUDIO=8, VIDEO=9, SCRIPT=18
FrameType: KEY=1, INTER=2, DISPOSABLE=3, GENERATED=4
VideoCodec: AVC=7, HEVC=12, AV1=13
AVCPacketType: SEQ_HEADER=0, NALU=1, END=2
FourCC: hvc1, av01, vp09, avc1
