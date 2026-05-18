# RTMP Support Implementation Plan

## Executive Summary
The RTMP (Real Time Messaging Protocol) implementation will reuse the existing FLV parser infrastructure by:
1. Creating a network source layer (StreamingRTMPSource) that handles RTMP handshake and chunk reassembly
2. Creating an RTMP parser (RTMPParser) that extracts FLV tags from RTMP messages
3. Delegating all tag parsing to existing FLV tag parsing methods
4. Integrating with the existing UI threading model (ParseWorker auto-detection)

---

## Phase 1: RTMP Network Foundation

### 1.1 RTMP Protocol Basics

**RTMP Chunk Structure:**
- Messages are divided into chunks (default: 128 bytes)
- Chunk format: [Chunk Header] [Chunk Data]
- Chunk Header variable length (1, 4, 8, or 12 bytes depending on format type)

**RTMP Handshake (3-phase):**
- C0: Client sends 1 byte (version 0x03)
- C1: Client sends 1536 bytes (timestamp + random data)
- C2: Client sends 1536 bytes (copy of S1)
- Server responds with S0 (version), S1 (1536 bytes), S2 (copy of C1)

**RTMP Message Types (Control Messages):**
- Set Chunk Size (type 1): Defines chunk size
- Abort Message (type 2): Discard incomplete message
- Acknowledgement (type 3): Window acknowledgement
- Set Peer Bandwidth (type 5): Flow control
- Audio Data (type 8): Audio payload
- Video Data (type 9): Video payload
- Script Data (type 18): Metadata

**RTMP Command Messages:**
- Connect: Client initiates connection with app info
- CreateStream: Client requests stream ID
- Play: Server begins sending stream data
- Publish: Client begins sending stream data

### 1.2 StreamingRTMPSource Implementation

**Location:** `src/media_analyzer/network/rtmp_source.py`

**Key Responsibilities:**
1. TCP socket connection management
2. RTMP handshake protocol (C0/C1/C2)
3. RTMP Connect command handling
4. CreateStream for play/publish
5. Chunk stream reassembly (mapping chunk IDs to messages)
6. Message buffer management

**Class Structure:**
```python
class StreamingRTMPSource(BaseSource):
    def __init__(self, url: str, timeout: int = 30):
        # Parse URL: rtmp://host:port/app/stream
        # Initialize socket, buffers, chunk streams
        
    def connect(self) -> bool:
        # Perform RTMP handshake
        # Send Connect command
        # Send CreateStream
        # Wait for play response
        
    def _handle_handshake(self) -> bool:
        # Send C0 (version byte)
        # Send C1 (1536 random bytes)
        # Receive S0, S1, S2
        # Send C2 (copy of S1)
        # Verify S2 == C1
```

---

## Phase 2: RTMP Parser Layer

### 2.1 RTMPParser Implementation

**Location:** `src/media_analyzer/parsers/rtmp/parser.py`

**Key Responsibilities:**
1. Receive RTMP messages from StreamingRTMPSource
2. Extract FLV tag payloads from RTMP Video/Audio/Script messages
3. Synthesize FLV header from first tags
4. Delegate tag parsing to FLVParser methods
5. Yield PacketInfo objects compatible with UI

---

## Phase 3: Integration with Existing UI

### 3.1 ParseWorker Enhancement

**Current Behavior:**
- ParseWorker detects format from first batch: TS (has "pid"), MP4 (has "box_type"), else FLV
- Routes to appropriate UI view and parser

**New Behavior for RTMP:**
- URL detection: Check if url starts with "rtmp://" or "rtmps://"
- Create StreamingRTMPSource instead of StreamingHTTPSource
- ParseWorker auto-selects RTMPParser
- All downstream UI code unchanged (signals, threading, display)

---

## Phase 4: Implementation Checklist

### Tier 1: Core RTMP Foundation
- [ ] `src/media_analyzer/network/rtmp_source.py` - StreamingRTMPSource class
- [ ] Implement RTMP handshake (C0/C1/C2)
- [ ] Implement Connect and CreateStream commands
- [ ] Implement chunk reassembly engine
- [ ] Test with public RTMP streams

### Tier 2: Parser Integration  
- [ ] `src/media_analyzer/parsers/rtmp/parser.py` - RTMPParser class
- [ ] FLV header synthesis logic
- [ ] Message type dispatch (Audio/Video/Script)
- [ ] Tag parsing delegation to FLVParser
- [ ] Unit tests for tag extraction

### Tier 3: UI Integration
- [ ] URL detection for rtmp:// in MainWindow._open_url()
- [ ] Auto-detection of RTMP in _on_packets_ready()
- [ ] Download progress display for RTMP streams
- [ ] Save functionality for RTMP buffers

### Tier 4: Advanced Features
- [ ] Connection recovery and reconnection
- [ ] Frame rate limiting for UI responsiveness
- [ ] Bandwidth adaptation
- [ ] Statistics display (bitrate, resolution, codec)
- [ ] Performance profiling and optimization

