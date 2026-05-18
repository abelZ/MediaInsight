# RTMP Technical Implementation Reference

## Overview
This document provides detailed technical specifications for RTMP source and parser implementation.

---

## Part 1: RTMP Protocol Specification

### 1.1 RTMP Handshake

The RTMP handshake is a 3-phase exchange before data transmission:

**Client -> Server:**
- C0: 1 byte version (0x03)
- C1: 1536 bytes (timestamp[4] + random[1532])

**Server -> Client:**
- S0: 1 byte version (0x03)
- S1: 1536 bytes (timestamp[4] + random[1532])
- S2: 1536 bytes (copy of C1)

**Client -> Server:**
- C2: 1536 bytes (copy of S1)

### 1.2 RTMP Message Format

**Chunk Header Types:**

Type 0 (Full): 12 bytes - [Format(2) | StreamID(6)] + Timestamp(3) + Length(3) + Type(1) + StreamID(4)

Type 1 (8 bytes): Reuses stream state, updates length and type

Type 2 (4 bytes): Only timestamp delta

Type 3 (1 byte): Reuses all state

**Extended Stream ID:**
- If bits 2-7 of chunk header byte are 0: next 1 byte is stream ID (1-255)
- If bits 2-7 are 1: next 3 bytes form stream ID (256+)

### 1.3 RTMP Control and Media Messages

**Message Types:**
- 1: Set Chunk Size
- 2: Abort Message
- 3: Acknowledgement
- 4: User Control (ping/pong)
- 5: Window Acknowledgement Size
- 8: Audio Data
- 9: Video Data
- 18: Script Data (AMF0)
- 20: Command (AMF0)

---

## Part 2: StreamingRTMPSource Implementation

### 2.1 Class Responsibilities

```
StreamingRTMPSource(DataSource):
  - Parse rtmp://host:port/app/stream URLs
  - Establish TCP socket with timeout
  - Execute RTMP handshake (C0/C1/C2)
  - Send Connect and CreateStream commands
  - Reassemble chunks into messages
  - Buffer streaming data
  - Track progress for UI
  - Implement DataSource interface
```

### 2.2 Key Methods

**open()** -> BinaryIO
- Connect to server
- Perform handshake
- Return stream reader

**close()** -> None
- Close socket
- Release buffers

**read_range(offset, size)** -> bytes
- Random access to buffered data
- Supports hex view on demand

**size** property
- 0 for live streams
- Estimated for VOD

**name** property
- Display "rtmp://host/app/stream"

---

## Part 3: RTMPParser Implementation

### 3.1 Parser Responsibilities

```
RTMPParser(BaseParser):
  - Synthesize FLV header (9 bytes) as first packet
  - Read RTMP messages from StreamingRTMPSource
  - Extract tag payloads (type 8/9/18)
  - Delegate parsing to FLVParser methods
  - Track has_video/has_audio flags
  - Yield PacketInfo compatible with UI
```

### 3.2 Reusable FLVParser Methods

These methods work unchanged for RTMP:

1. `_parse_video_tag_traditional()` - Traditional codecs
2. `_parse_video_tag_enhanced()` - Enhanced RTMP (HEVC/AV1)
3. `_parse_avc_nalus()` - H.264 NALU extraction
4. `_parse_hevc_nalus()` - H.265 NALU extraction
5. `_parse_av1_obus()` - AV1 OBU extraction
6. `_parse_audio_tag()` - Audio codec parsing
7. `_parse_script_tag()` - Metadata extraction

---

## Part 4: Integration Points

### 4.1 URL Detection

MainWindow._open_url():
- Check url.startswith('rtmp://' or 'rtmps://')
- Create StreamingRTMPSource(url)
- Call self._start_parsing(source)

### 4.2 Parser Selection

ParseWorker:
- Source type detection: Check if isinstance(source, StreamingRTMPSource)
- Auto-select RTMPParser
- All downstream logic unchanged

### 4.3 Progress Tracking

RTMPParser.parse_incremental():
- Emit download_progress(bytes_received, packet_count)
- Display "Streaming: X MB/s (Y packets)"

### 4.4 Save Functionality

StreamingRTMPSource.save_to_file(path):
- Write buffered data to disk
- Enable only when appropriate (VOD or enough buffered)

---

## Part 5: Implementation Order

### Phase 1: Network Foundation
- Create StreamingRTMPSource
- Implement URL parsing
- Implement RTMP handshake
- Implement Connect command
- Test connection to public server

### Phase 2: Parser Integration
- Create RTMPParser
- Implement FLV header synthesis
- Implement message dispatch
- Test tag extraction
- Verify packet format

### Phase 3: UI Integration
- Add RTMP URL detection
- Auto-select parser
- Test progress display
- Test save functionality

### Phase 4: Advanced Features
- Connection recovery
- Bandwidth reporting
- Enhanced RTMP codec support
- Performance optimization

---

## Testing Checklist

### Unit Tests
- RTMP handshake protocol
- Chunk reassembly
- Message dispatch
- Tag extraction

### Integration Tests
- Public RTMP server connection
- Stream parsing
- Codec variations
- Error recovery

### Manual Tests
- Live stream viewing
- VOD stream seeking
- File save
- High-bitrate handling

