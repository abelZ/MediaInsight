# RTMP Support Implementation Guide

## Step 1: Prepare Directory Structure

Create RTMP parser module:
- src/media_analyzer/parsers/rtmp/__init__.py
- src/media_analyzer/parsers/rtmp/parser.py

Add RTMP source to network module:
- src/media_analyzer/network/rtmp_source.py

## Step 2: StreamingRTMPSource Implementation

File: src/media_analyzer/network/rtmp_source.py

Responsibilities:
1. Parse RTMP URLs (rtmp://host:port/app/stream)
2. Establish TCP socket connection
3. Execute RTMP handshake (C0/C1/C2)
4. Send Connect and CreateStream commands
5. Reassemble RTMP chunks into messages
6. Maintain message buffers
7. Implement DataSource interface

Key Methods:
- __init__(url, timeout=30): Parse URL and init
- open(): Connect and handshake
- close(): Disconnect
- read_range(offset, size): Random access to buffer
- size property: Return total or 0 for live
- name property: Return display name
- _perform_handshake(): Execute RTMP handshake
- _send_connect_command(): Send Connect AMF0 message
- _reassemble_chunks(): Generator yielding (msg_type, msg_data)

## Step 3: RTMPParser Implementation

File: src/media_analyzer/parsers/rtmp/parser.py

Responsibilities:
1. Extend BaseParser interface
2. Synthesize FLV header as first packet
3. Extract tags from RTMP messages
4. Delegate parsing to FLVParser methods
5. Yield PacketInfo compatible with UI

Key Methods:
- parse_incremental(source): Generator yielding PacketInfo
- _synthesize_flv_header(): Create FLV header packet
- _process_rtmp_message(msg_type, data): Extract and parse tag
- get_stream_info(): Return stream statistics
- sniff(header_bytes): Return False (detection at source level)

Reused FLVParser Methods (no changes):
- _parse_video_tag_traditional()
- _parse_video_tag_enhanced()
- _parse_avc_nalus()
- _parse_hevc_nalus()
- _parse_av1_obus()
- _parse_audio_tag()
- _parse_script_tag()

## Step 4: UI Integration

Modify src/media_analyzer/ui/main_window.py:

In _open_url(url) method, add URL detection:

    elif url.startswith(('rtmp://', 'rtmps://')):
        from media_analyzer.network.rtmp_source import StreamingRTMPSource
        source = StreamingRTMPSource(url)
        self._start_parsing(source)

## Step 5: No ParseWorker Changes Needed

The existing ParseWorker already:
- Accepts any DataSource instance
- Auto-detects format from first packets
- Routes to correct parser
- All signals and threading unchanged

## Implementation Order

Phase 1: Network Foundation
- Implement StreamingRTMPSource
- Test RTMP handshake
- Test Connect/CreateStream

Phase 2: Parser Integration
- Implement RTMPParser
- Test FLV header synthesis
- Test message dispatch

Phase 3: UI Integration
- Add URL detection
- Test end-to-end parsing
- Test progress display

Phase 4: Testing & Optimization
- Unit tests
- Integration tests
- Manual testing with live streams

## Critical Design Realization

All existing FLVParser methods work unchanged for RTMP because:

1. FLV tag structure is identical in RTMP messages
2. Audio/Video/Script tags have same format
3. Tag parsing methods are decoupled from file I/O
4. PacketInfo model is format-agnostic

This means RTMPParser is essentially a thin wrapper:
- Read RTMP messages
- Extract tag payloads
- Call FLVParser methods
- Yield PacketInfo

No reimplementation of tag parsing logic needed!

## Key Files to Reference

When implementing, reference:
- src/media_analyzer/core/source.py (DataSource interface)
- src/media_analyzer/parsers/base.py (BaseParser interface)
- src/media_analyzer/parsers/flv/parser.py (FLV tag parsing)
- src/media_analyzer/core/models.py (PacketInfo, TagType, etc.)
- src/media_analyzer/parsers/flv/script.py (AMF0 encoder/decoder)

## Error Handling Strategy

Connection Errors:
- Timeout: socket.settimeout()
- Connection refused: Catch ConnectionRefusedError
- Handshake failure: Verify version bytes

Protocol Errors:
- Invalid chunk format: Log and skip
- Incomplete message: Reconnect
- Unknown msg type: Log and continue

Parsing Errors:
- Malformed AMF0: Use defaults
- Missing tag header: Continue
- Truncated payload: Buffer and retry

## Testing Approach

Unit Tests:
1. RTMP handshake protocol
2. Chunk reassembly with various sizes
3. Message type dispatch
4. Tag extraction

Integration Tests:
1. Public test RTMP server
2. Receive and parse stream
3. Verify PacketInfo format
4. Test multiple codecs

Manual Tests:
1. Live RTMP stream
2. VOD RTMP stream
3. Connection recovery
4. Save stream to file
