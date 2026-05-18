# FLV PARSER TECHNICAL SUMMARY

## Video Tag Parsing Modes

### Traditional FLV
First byte: [FrameType(4) | CodecID(4)]
- FrameType: 1=Key, 2=Inter, 3=Disposable, 4=Generated
- CodecID: 7=H.264/AVC

For AVC:
- Byte 1: AVCPacketType (0=config, 1=NALUs, 2=end)
- Bytes 2-4: CompositionTime (SI24, signed)
- Bytes 5+: Payload (NALUs in [4-byte length][data] format)

### Enhanced RTMP (byte 0 & 0x80 = 1)
Byte 0: [1 | FrameType(3) | PacketType(4)]
Bytes 1-4: FourCC (hvc1, av01, vp09, avc1)
Bytes 5+: Payload (frames: CTS(3) + NALUs)

---

## Audio Tag Parsing

Byte 0: [SoundFormat(4) | SoundRate(2) | SoundSize(1) | SoundType(1)]
- SoundFormat: 10=AAC
- SoundRate: 0=5.5k, 1=11k, 2=22k, 3=44k

For AAC (SoundFormat=10):
- Byte 1: AACPacketType (0=config, 1=raw)
- Bytes 2+: AAC frame data

---

## Parser Algorithm

1. Read FLV header (9 bytes)
   - Validate "FLV" signature
   - Extract version, has_video, has_audio
   - Yield as pseudo-tag

2. Loop until EOF:
   a. Read 11-byte tag header
   b. Parse tag_type, data_size, timestamp, stream_id
   c. Read DataSize bytes of payload
   d. Parse based on tag_type
   e. Yield PacketInfo
   f. Read next PreviousTagSize

Generator-based: yields packets incrementally, ideal for streaming

---

## UI Thread Model

_start_parsing(source):
1. Stop existing worker
2. Create ParseWorker
3. Connect signals
4. Start worker thread

ParseWorker.run():
1. Create parser (FLVParser, TSParser, etc)
2. Batch packets from parse_incremental()
3. Emit packets_ready, progress, download_progress
4. Emit parse_finished

_on_packets_ready(packets):
1. Format detection (first batch)
2. Route to table/tree view
3. Update status

_on_download_progress(down, total):
1. Update download status
2. Enable Save As when done

---

## RTMP Integration

What can be reused:
- _parse_video_tag_traditional()
- _parse_video_tag_enhanced()
- _parse_avc_nalus()
- _parse_hevc_nalus()
- _parse_av1_obus()
- _parse_audio_tag()
- _parse_script_tag()
- PacketInfo model
- UI threading model

What needs new implementation:
- StreamingRTMPSource
- RTMPParser
- RTMP handshake
- Chunk reassembly
- FLV header synthesis

---

## Key Insight

RTMP carries the EXACT SAME FLV tag structure as files:
- 11-byte header (tag type, size, timestamp, stream ID)
- Payload (video, audio, or script)
- But delivered via network chunks instead of sequential file bytes

Critical differences:
1. No initial FLV header in RTMP (must synthesize)
2. Messages fragmented into chunks (must reassemble)
3. Can have multiple stream IDs (need routing)
4. Timestamp format is identical (direct compatibility)

---

## Conclusion

RTMP support requires only new source/parser classes that reuse tag parsing.
No modifications needed to existing FLV parsing, UI threading, or data models.
