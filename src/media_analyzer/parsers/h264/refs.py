"""H.264 reference picture analysis — parses slice headers to determine reference relationships.

Implements a simplified DPB (Decoded Picture Buffer) model to compute which frames
reference which, based on POC (Picture Order Count) and frame_num from slice headers.

Reference: ITU-T H.264 (03/2010) Sections 7.3.3, 8.2.1, 8.2.4
"""

import struct
import logging
from typing import List, Optional, Tuple, BinaryIO, Dict, Any
from dataclasses import dataclass, field

from media_analyzer.parsers.h264.bitreader import BitReader

logger = logging.getLogger(__name__)


@dataclass
class SPSParams:
    """Key SPS parameters needed for slice header parsing."""
    profile_idc: int = 0
    chroma_format_idc: int = 1
    separate_colour_plane_flag: bool = False
    log2_max_frame_num: int = 4  # log2_max_frame_num_minus4 + 4
    pic_order_cnt_type: int = 0
    log2_max_pic_order_cnt_lsb: int = 4  # for poc_type 0
    delta_pic_order_always_zero_flag: bool = False  # for poc_type 1
    frame_mbs_only_flag: bool = True
    max_num_ref_frames: int = 1


@dataclass
class PPSParams:
    """Key PPS parameters needed for slice header parsing."""
    num_ref_idx_l0_default_active: int = 1  # num_ref_idx_l0_default_active_minus1 + 1
    num_ref_idx_l1_default_active: int = 1  # num_ref_idx_l1_default_active_minus1 + 1


@dataclass
class FrameRef:
    """Reference information for a single frame."""
    index: int = 0         # Frame index in decode order
    frame_type: str = "P"  # "I", "P", "B"
    frame_num: int = 0     # frame_num from slice header
    poc: int = 0           # Picture Order Count (display order)
    is_reference: bool = True  # Whether this frame can be used as reference
    num_ref_l0: int = 1    # Number of L0 references actually used
    num_ref_l1: int = 1    # Number of L1 references actually used
    ref_list0: List[int] = field(default_factory=list)  # Indices of L0 references
    ref_list1: List[int] = field(default_factory=list)  # Indices of L1 references (B-frames)


# High profiles that have chroma_format_idc in SPS
HIGH_PROFILES = {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139}


def parse_sps_params(sps_nalu: bytes) -> Optional[SPSParams]:
    """Extract key parameters from SPS NALU for slice header parsing.

    Args:
        sps_nalu: Raw SPS NALU bytes (including NALU header byte)
    """
    if not sps_nalu or len(sps_nalu) < 4:
        return None

    try:
        reader = BitReader(sps_nalu[1:])  # Skip NALU header
        params = SPSParams()

        params.profile_idc = reader.read_bits(8)
        reader.skip_bits(8)  # constraint_set flags + reserved
        reader.read_bits(8)  # level_idc
        reader.read_ue()     # seq_parameter_set_id

        if params.profile_idc in HIGH_PROFILES:
            params.chroma_format_idc = reader.read_ue()
            if params.chroma_format_idc == 3:
                params.separate_colour_plane_flag = reader.read_bool()
            reader.read_ue()  # bit_depth_luma_minus8
            reader.read_ue()  # bit_depth_chroma_minus8
            reader.read_bool()  # qpprime_y_zero_transform_bypass_flag
            scaling_present = reader.read_bool()
            if scaling_present:
                num_lists = 12 if params.chroma_format_idc == 3 else 8
                for i in range(num_lists):
                    if reader.read_bool():
                        _skip_scaling_list(reader, 16 if i < 6 else 64)

        params.log2_max_frame_num = reader.read_ue() + 4
        params.pic_order_cnt_type = reader.read_ue()

        if params.pic_order_cnt_type == 0:
            params.log2_max_pic_order_cnt_lsb = reader.read_ue() + 4
        elif params.pic_order_cnt_type == 1:
            params.delta_pic_order_always_zero_flag = reader.read_bool()
            reader.read_se()  # offset_for_non_ref_pic
            reader.read_se()  # offset_for_top_to_bottom_field
            num_ref = reader.read_ue()
            for _ in range(num_ref):
                reader.read_se()

        params.max_num_ref_frames = reader.read_ue()
        reader.read_bool()  # gaps_in_frame_num_allowed
        reader.read_ue()    # pic_width_in_mbs_minus1
        reader.read_ue()    # pic_height_in_map_units_minus1
        params.frame_mbs_only_flag = reader.read_bool()

        return params
    except (EOFError, ValueError, IndexError):
        return None


def _skip_scaling_list(reader: BitReader, size: int):
    """Skip scaling list in SPS."""
    last_scale = 8
    next_scale = 8
    for j in range(size):
        if next_scale != 0:
            delta = reader.read_se()
            next_scale = (last_scale + delta + 256) % 256
        if next_scale != 0:
            last_scale = next_scale


def parse_pps_params(pps_nalu: bytes) -> Optional[PPSParams]:
    """Extract key parameters from PPS NALU.

    PPS syntax: pps_id(ue) + sps_id(ue) + entropy_coding_mode_flag(1) +
                bottom_field_pic_order_in_frame_present_flag(1) +
                num_slice_groups_minus1(ue) + [slice group stuff if > 0] +
                num_ref_idx_l0_default_active_minus1(ue) +
                num_ref_idx_l1_default_active_minus1(ue)
    """
    if not pps_nalu or len(pps_nalu) < 2:
        return None

    try:
        reader = BitReader(pps_nalu[1:])  # Skip NALU header
        params = PPSParams()

        reader.read_ue()  # pic_parameter_set_id
        reader.read_ue()  # seq_parameter_set_id
        reader.read_bool()  # entropy_coding_mode_flag
        reader.read_bool()  # bottom_field_pic_order_in_frame_present_flag

        num_slice_groups_minus1 = reader.read_ue()
        if num_slice_groups_minus1 > 0:
            # Slice group parsing is complex; skip by returning defaults
            # Most content uses num_slice_groups = 1 (value 0)
            return params

        params.num_ref_idx_l0_default_active = reader.read_ue() + 1
        params.num_ref_idx_l1_default_active = reader.read_ue() + 1

        return params
    except (EOFError, ValueError, IndexError):
        return None


@dataclass
class SliceInfo:
    """Parsed slice header fields needed for reference analysis."""
    slice_type: int = 0        # 0=P, 1=B, 2=I, 3=SP, 4=SI (+5 for "all")
    frame_num: int = 0
    field_pic_flag: bool = False
    idr_pic_id: int = 0
    pic_order_cnt_lsb: int = 0
    delta_pic_order_cnt_bottom: int = 0
    num_ref_idx_l0_active: int = 1  # actual active count (minus1 + 1)
    num_ref_idx_l1_active: int = 1
    nal_ref_idc: int = 0  # From NALU header: >0 means used as reference


def parse_slice_header(nalu_data: bytes, nalu_offset: int,
                       sps: SPSParams, pps: PPSParams,
                       is_idr: bool) -> Optional[SliceInfo]:
    """Parse slice header to extract reference-related fields.

    Args:
        nalu_data: Buffer containing NALU data
        nalu_offset: Offset of first byte after NALU header
        sps: SPS parameters
        pps: PPS parameters (for default ref idx active counts)
        is_idr: Whether this is an IDR picture
    """
    if nalu_offset + 4 >= len(nalu_data):
        return None

    try:
        reader = BitReader(nalu_data[nalu_offset:nalu_offset + 32])
        info = SliceInfo()

        # Use PPS defaults for ref_idx_active
        info.num_ref_idx_l0_active = pps.num_ref_idx_l0_default_active
        info.num_ref_idx_l1_active = pps.num_ref_idx_l1_default_active

        reader.read_ue()  # first_mb_in_slice
        info.slice_type = reader.read_ue()
        reader.read_ue()  # pic_parameter_set_id

        if sps.separate_colour_plane_flag:
            reader.read_bits(2)  # colour_plane_id

        info.frame_num = reader.read_bits(sps.log2_max_frame_num)

        if not sps.frame_mbs_only_flag:
            info.field_pic_flag = reader.read_bool()
            if info.field_pic_flag:
                reader.read_bool()  # bottom_field_flag

        if is_idr:
            info.idr_pic_id = reader.read_ue()

        if sps.pic_order_cnt_type == 0:
            info.pic_order_cnt_lsb = reader.read_bits(sps.log2_max_pic_order_cnt_lsb)
            # Skip delta_pic_order_cnt_bottom for simplicity

        # Parse num_ref_idx_active override
        st = info.slice_type % 5  # Normalize (0=P, 1=B, 2=I)
        if st in (0, 1, 3):  # P, B, or SP
            override = reader.read_bool()
            if override:
                info.num_ref_idx_l0_active = reader.read_ue() + 1
                if st == 1:  # B-slice
                    info.num_ref_idx_l1_active = reader.read_ue() + 1

        return info
    except (EOFError, ValueError, IndexError):
        return None


def analyze_references(source: BinaryIO, sample_offsets: List[int],
                       sample_sizes: List[int], sync_set: set,
                       nalu_length_size: int,
                       sps_nalu: bytes,
                       pps_nalu: Optional[bytes] = None) -> List[FrameRef]:
    """Analyze reference relationships for all video samples in an MP4 track.

    Parses slice headers, computes POC, and builds reference picture lists.

    Args:
        source: File source for reading sample data
        sample_offsets: File offset of each sample
        sample_sizes: Size of each sample
        sync_set: Set of sync sample numbers (1-based)
        nalu_length_size: NALU length field size (typically 4)
        sps_nalu: Raw SPS NALU data for parameter extraction
        pps_nalu: Raw PPS NALU data for default ref idx active counts

    Returns:
        List of FrameRef with reference indices populated
    """
    sps = parse_sps_params(sps_nalu)
    if sps is None:
        # Fallback: return basic frame info without refs
        return _fallback_refs(len(sample_offsets), sync_set)

    # Parse PPS for default num_ref_idx_active
    pps = PPSParams()
    if pps_nalu:
        parsed_pps = parse_pps_params(pps_nalu)
        if parsed_pps:
            pps = parsed_pps

    # Parse slice headers for all samples
    frames: List[FrameRef] = []
    prev_poc_msb = 0
    prev_poc_lsb = 0
    max_poc_lsb = 1 << sps.log2_max_pic_order_cnt_lsb

    for i in range(len(sample_offsets)):
        is_idr = (i + 1) in sync_set
        fr = FrameRef(index=i)

        if is_idr:
            fr.frame_type = "I"
            fr.is_reference = True
            fr.frame_num = 0
            fr.poc = 0
            prev_poc_msb = 0
            prev_poc_lsb = 0
            frames.append(fr)
            continue

        # Read sample data for slice header
        slice_info = _read_slice_info(source, sample_offsets[i], sample_sizes[i],
                                      nalu_length_size, sps, pps, is_idr)
        if slice_info is None:
            fr.frame_type = "P"
            fr.poc = i * 2  # Fallback POC
            frames.append(fr)
            continue

        fr.frame_num = slice_info.frame_num
        st = slice_info.slice_type % 5
        if st == 2:
            fr.frame_type = "I"
        elif st == 1:
            fr.frame_type = "B"
        else:
            fr.frame_type = "P"

        # Determine if frame is used as reference from nal_ref_idc
        # nal_ref_idc > 0 means the frame is stored in DPB for reference
        fr.is_reference = (slice_info.nal_ref_idc > 0)

        fr.num_ref_l0 = slice_info.num_ref_idx_l0_active
        fr.num_ref_l1 = slice_info.num_ref_idx_l1_active

        # Compute POC (type 0 only — most common)
        if sps.pic_order_cnt_type == 0:
            poc_lsb = slice_info.pic_order_cnt_lsb
            # POC MSB derivation (ITU-T H.264 Section 8.2.1.1)
            if poc_lsb < prev_poc_lsb and (prev_poc_lsb - poc_lsb) >= max_poc_lsb // 2:
                poc_msb = prev_poc_msb + max_poc_lsb
            elif poc_lsb > prev_poc_lsb and (poc_lsb - prev_poc_lsb) > max_poc_lsb // 2:
                poc_msb = prev_poc_msb - max_poc_lsb
            else:
                poc_msb = prev_poc_msb
            fr.poc = poc_msb + poc_lsb
            # Update prev for next reference picture
            if fr.is_reference:
                prev_poc_msb = poc_msb
                prev_poc_lsb = poc_lsb
        else:
            # For poc_type 1 or 2, use frame_num * 2 as approximation
            fr.poc = fr.frame_num * 2

        frames.append(fr)

    # Build reference lists based on POC
    _build_reference_lists(frames, sps.max_num_ref_frames)

    return frames


def _read_slice_info(source: BinaryIO, offset: int, size: int,
                     nalu_length_size: int, sps: SPSParams, pps: PPSParams,
                     is_idr: bool) -> Optional[SliceInfo]:
    """Read and parse slice header from a sample."""
    read_size = min(64, size)
    if read_size < nalu_length_size + 2:
        return None

    try:
        source.seek(offset)
        data = source.read(read_size)
    except (OSError, IOError):
        return None

    if len(data) < nalu_length_size + 2:
        return None

    # Find the first VCL NALU (skip SPS/PPS/SEI)
    pos = 0
    while pos + nalu_length_size < len(data):
        nalu_len = int.from_bytes(data[pos:pos + nalu_length_size], "big")
        nalu_start = pos + nalu_length_size
        if nalu_start >= len(data):
            break

        nalu_header = data[nalu_start]
        nal_ref_idc = (nalu_header >> 5) & 0x03
        nal_type = nalu_header & 0x1F
        if nal_type in (7, 8, 6, 9):  # SPS, PPS, SEI, AUD
            pos = nalu_start + nalu_len
            continue

        if nal_type in (1, 5):  # Non-IDR slice, IDR slice
            info = parse_slice_header(data, nalu_start + 1, sps, pps, nal_type == 5)
            if info:
                info.nal_ref_idc = nal_ref_idc
            return info
        break

    return None


def _build_reference_lists(frames: List[FrameRef], max_refs: int):
    """Build reference picture lists for each frame based on POC.

    Implements ITU-T H.264 Section 8.2.4 (default reference picture list):
    - RefPicList0 for P-slices: short-term refs sorted by descending POC (nearest first)
    - RefPicList0 for B-slices: refs with POC < current in descending order
    - RefPicList1 for B-slices: refs with POC > current in ascending order

    Only keeps num_ref_idx_active entries (from slice header) for each frame.
    """
    # DPB: track reference frames currently available
    dpb: List[int] = []  # Indices of reference frames in DPB

    for i, fr in enumerate(frames):
        if fr.frame_type == "I":
            # IDR resets DPB
            if fr.frame_num == 0:
                dpb.clear()
            fr.ref_list0 = []
            fr.ref_list1 = []
            # Add to DPB
            dpb.append(i)
            if len(dpb) > max_refs:
                dpb.pop(0)
            continue

        current_poc = fr.poc

        if fr.frame_type == "P":
            # RefPicList0: DPB frames with POC < current, nearest first (descending POC)
            candidates = [(frames[j].poc, j) for j in dpb
                          if frames[j].poc < current_poc]
            candidates.sort(key=lambda x: -x[0])  # Descending POC (nearest first)
            # Only keep the number of references this frame actually uses
            fr.ref_list0 = [j for _, j in candidates[:fr.num_ref_l0]]

        elif fr.frame_type == "B":
            # RefPicList0: refs with POC < current, descending (nearest before)
            l0_before = [(frames[j].poc, j) for j in dpb
                         if frames[j].poc < current_poc]
            l0_before.sort(key=lambda x: -x[0])
            # Then refs with POC > current, ascending (nearest after)
            l0_after = [(frames[j].poc, j) for j in dpb
                        if frames[j].poc > current_poc]
            l0_after.sort(key=lambda x: x[0])
            fr.ref_list0 = [j for _, j in (l0_before + l0_after)[:fr.num_ref_l0]]

            # RefPicList1: refs with POC > current, ascending (nearest after)
            l1_after = [(frames[j].poc, j) for j in dpb
                        if frames[j].poc > current_poc]
            l1_after.sort(key=lambda x: x[0])
            # Then refs with POC < current, descending (nearest before)
            l1_before = [(frames[j].poc, j) for j in dpb
                         if frames[j].poc < current_poc]
            l1_before.sort(key=lambda x: -x[0])
            fr.ref_list1 = [j for _, j in (l1_after + l1_before)[:fr.num_ref_l1]]

        # Add reference frames to DPB (P-frames are always reference, B typically not)
        if fr.is_reference:
            dpb.append(i)
            if len(dpb) > max_refs:
                dpb.pop(0)
                dpb.pop(0)


def _fallback_refs(n_samples: int, sync_set: set) -> List[FrameRef]:
    """Fallback: basic frame info without actual reference parsing."""
    frames = []
    for i in range(n_samples):
        is_idr = (i + 1) in sync_set
        fr = FrameRef(index=i, frame_type="I" if is_idr else "P")
        frames.append(fr)
    return frames


def extract_sps_from_avcc(avcc_data: bytes) -> Optional[bytes]:
    """Extract the first SPS NALU from avcC data.

    avcC format: version(1) + profile(1) + compat(1) + level(1) +
                 nalu_length_size(1) + num_sps(1) + [sps_len(2) + sps_data]...
    """
    if len(avcc_data) < 8:
        return None
    num_sps = avcc_data[5] & 0x1F
    if num_sps < 1:
        return None
    pos = 6
    if pos + 2 > len(avcc_data):
        return None
    sps_len = struct.unpack(">H", avcc_data[pos:pos + 2])[0]
    pos += 2
    if pos + sps_len > len(avcc_data):
        return None
    return avcc_data[pos:pos + sps_len]


def extract_pps_from_avcc(avcc_data: bytes) -> Optional[bytes]:
    """Extract the first PPS NALU from avcC data.

    avcC: ...after SPS entries... num_pps(1) + [pps_len(2) + pps_data]...
    """
    if len(avcc_data) < 8:
        return None
    num_sps = avcc_data[5] & 0x1F
    pos = 6
    # Skip all SPS entries
    for _ in range(num_sps):
        if pos + 2 > len(avcc_data):
            return None
        sps_len = struct.unpack(">H", avcc_data[pos:pos + 2])[0]
        pos += 2 + sps_len
    # Read PPS
    if pos >= len(avcc_data):
        return None
    num_pps = avcc_data[pos]
    pos += 1
    if num_pps < 1:
        return None
    if pos + 2 > len(avcc_data):
        return None
    pps_len = struct.unpack(">H", avcc_data[pos:pos + 2])[0]
    pos += 2
    if pos + pps_len > len(avcc_data):
        return None
    return avcc_data[pos:pos + pps_len]
