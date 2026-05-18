# RTMP Executive Summary

## Project Goal
Add RTMP (Real Time Messaging Protocol) streaming support to MediaInsight with zero duplication of existing FLV tag parsing logic.

## Architecture

### Layer 1: StreamingRTMPSource (src/media_analyzer/network/rtmp_source.py)
- Handles RTMP protocol details
- TCP socket management
- 3-phase handshake (C0/C1/C2)
- Chunk reassembly (4 header formats)
- Implements DataSource interface

### Layer 2: RTMPParser (src/media_analyzer/parsers/rtmp/parser.py)
- Extracts FLV tags from RTMP messages
- Synthesizes FLV header
- DELEGATES parsing to FLVParser (key design!)
- Implements BaseParser interface

### Layer 3: UI Integration
- MainWindow._open_url(): Add RTMP URL detection
- Everything else unchanged
- ParseWorker already abstracts source type

## Key Realization

All 7 FLV tag parsing methods reused without modification:
- _parse_video_tag_traditional()
- _parse_video_tag_enhanced()
- _parse_avc_nalus(), _parse_hevc_nalus(), _parse_av1_obus()
- _parse_audio_tag()
- _parse_script_tag()

Why? Tag structure identical in files and RTMP messages.

## Code Reuse
- New code: ~1100 lines (StreamingRTMPSource + RTMPParser)
- Reused: ~1150 lines (FLVParser, 100% of tag parsing)
- UI changes: ~5 lines

## RTMP Protocol Summary
- Handshake: C0/C1/C2 + S0/S1/S2 (3072 bytes total)
- Messages: Type 8 (Audio), 9 (Video), 18 (Script)
- Chunks: 4 header formats, default 128 bytes
- Reassembly: Per-stream state machine

## Implementation Phases
1. StreamingRTMPSource - socket + handshake
2. RTMPParser - message dispatch + tag extraction
3. UI Integration - URL detection
4. Testing - unit + integration + manual

## Documentation Complete
- FLV_PARSER_ANALYSIS.md - High-level overview
- RTMP_PLANNING_SUMMARY.md - 4-phase roadmap
- RTMP_TECHNICAL_REFERENCE.md - Protocol specs
- IMPLEMENTATION_GUIDE.md - Step-by-step guide

## Status
Design phase complete. Ready for implementation.

