"""H.264 Sequence Parameter Set (SPS) parser.

Parses SPS NALU according to ITU-T H.264 (03/2010) Section 7.3.2.1.1.
Extracts profile, level, dimensions, chroma format, and other key fields.

Output format: List of field entries, where each entry is:
  - ("key", value)                    → simple leaf field
  - ("key", value, [children...])     → flag-controlled group (collapsed by default)
"""

from typing import Optional, List, Tuple, Any, Union

from media_analyzer.parsers.h264.bitreader import BitReader

# A field entry: either (key, value) or (key, value, children)
FieldEntry = Union[Tuple[str, Any], Tuple[str, Any, List[Any]]]

# Profile IDC names
PROFILE_NAMES = {
    66: "Baseline",
    77: "Main",
    88: "Extended",
    100: "High",
    110: "High 10",
    122: "High 4:2:2",
    244: "High 4:4:4 Predictive",
    44: "CAVLC 4:4:4 Intra",
    83: "Scalable Baseline",
    86: "Scalable High",
    118: "Multiview High",
    128: "Stereo High",
    138: "Multiview Depth High",
}

# Chroma format names
CHROMA_FORMAT_NAMES = {
    0: "Monochrome",
    1: "4:2:0",
    2: "4:2:2",
    3: "4:4:4",
}

# High profiles that have extended SPS fields
HIGH_PROFILES = {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139}


def parse_sps(data: bytes) -> Optional[List[FieldEntry]]:
    """
    Parse H.264 SPS NALU and return structured field list.

    Args:
        data: Raw SPS NALU bytes (starting with the NALU header byte)

    Returns:
        List of FieldEntry tuples, or None if parse fails.
    """
    if not data or len(data) < 4:
        return None

    try:
        # Skip NALU header byte
        reader = BitReader(data[1:])
        fields: List[FieldEntry] = []

        # profile_idc: u(8)
        profile_idc = reader.read_bits(8)
        profile_name = PROFILE_NAMES.get(profile_idc, f"Unknown({profile_idc})")
        fields.append(("profile_idc", f"{profile_idc} ({profile_name})"))

        # constraint_set flags
        cs0 = reader.read_bool()
        cs1 = reader.read_bool()
        cs2 = reader.read_bool()
        cs3 = reader.read_bool()
        cs4 = reader.read_bool()
        cs5 = reader.read_bool()
        reader.skip_bits(2)  # reserved_zero_2bits
        flags_str = f"{int(cs0)}{int(cs1)}{int(cs2)}{int(cs3)}{int(cs4)}{int(cs5)}"
        fields.append(("constraint_set_flags", flags_str))

        # level_idc: u(8)
        level_idc = reader.read_bits(8)
        fields.append(("level_idc", f"{level_idc} ({level_idc // 10}.{level_idc % 10})"))

        # seq_parameter_set_id: ue(v)
        sps_id = reader.read_ue()
        fields.append(("seq_parameter_set_id", sps_id))

        # Extended fields for High profiles
        chroma_format_idc = 1  # Default for non-High profiles
        separate_colour_plane_flag = False

        if profile_idc in HIGH_PROFILES:
            chroma_format_idc = reader.read_ue()
            chroma_name = CHROMA_FORMAT_NAMES.get(chroma_format_idc, str(chroma_format_idc))

            high_children: List[FieldEntry] = []
            high_children.append(("chroma_format_idc", f"{chroma_format_idc} ({chroma_name})"))

            if chroma_format_idc == 3:
                separate_colour_plane_flag = reader.read_bool()
                high_children.append(("separate_colour_plane_flag", separate_colour_plane_flag))

            bit_depth_luma = reader.read_ue() + 8
            bit_depth_chroma = reader.read_ue() + 8
            high_children.append(("bit_depth_luma", bit_depth_luma))
            high_children.append(("bit_depth_chroma", bit_depth_chroma))

            qpprime_bypass = reader.read_bool()
            high_children.append(("qpprime_y_zero_transform_bypass_flag", qpprime_bypass))

            scaling_matrix_present = reader.read_bool()
            high_children.append(("seq_scaling_matrix_present_flag", scaling_matrix_present))
            if scaling_matrix_present:
                num_lists = 12 if chroma_format_idc == 3 else 8
                for i in range(num_lists):
                    if reader.read_bool():
                        _skip_scaling_list(reader, 16 if i < 6 else 64)

            fields.append(("High Profile Extensions", True, high_children))

        # log2_max_frame_num_minus4: ue(v)
        log2_max_frame_num = reader.read_ue() + 4
        fields.append(("log2_max_frame_num", log2_max_frame_num))

        # pic_order_cnt_type: ue(v)
        poc_type = reader.read_ue()
        fields.append(("pic_order_cnt_type", poc_type))

        if poc_type == 0:
            log2_max_poc_lsb = reader.read_ue() + 4
            poc_children: List[FieldEntry] = [
                ("log2_max_pic_order_cnt_lsb", log2_max_poc_lsb),
            ]
            fields.append(("POC Type 0", True, poc_children))
        elif poc_type == 1:
            poc_children: List[FieldEntry] = []
            poc_children.append(("delta_pic_order_always_zero_flag", reader.read_bool()))
            poc_children.append(("offset_for_non_ref_pic", reader.read_se()))
            poc_children.append(("offset_for_top_to_bottom_field", reader.read_se()))
            num_ref = reader.read_ue()
            poc_children.append(("num_ref_frames_in_pic_order_cnt_cycle", num_ref))
            for i in range(num_ref):
                reader.read_se()  # offset_for_ref_frame[i]
            fields.append(("POC Type 1", True, poc_children))

        # max_num_ref_frames: ue(v)
        fields.append(("max_num_ref_frames", reader.read_ue()))

        # gaps_in_frame_num_value_allowed_flag: u(1)
        fields.append(("gaps_in_frame_num_allowed", reader.read_bool()))

        # Dimensions
        pic_width_in_mbs_minus1 = reader.read_ue()
        pic_height_in_map_units_minus1 = reader.read_ue()
        frame_mbs_only_flag = reader.read_bool()

        fields.append(("pic_width_in_mbs_minus1", pic_width_in_mbs_minus1))
        fields.append(("pic_height_in_map_units_minus1", pic_height_in_map_units_minus1))
        fields.append(("frame_mbs_only_flag", frame_mbs_only_flag))

        if not frame_mbs_only_flag:
            fields.append(("mb_adaptive_frame_field_flag", reader.read_bool()))

        fields.append(("direct_8x8_inference_flag", reader.read_bool()))

        # Frame cropping
        frame_cropping_flag = reader.read_bool()
        crop_left = crop_right = crop_top = crop_bottom = 0

        if frame_cropping_flag:
            crop_left = reader.read_ue()
            crop_right = reader.read_ue()
            crop_top = reader.read_ue()
            crop_bottom = reader.read_ue()
            crop_children: List[FieldEntry] = [
                ("frame_crop_left_offset", crop_left),
                ("frame_crop_right_offset", crop_right),
                ("frame_crop_top_offset", crop_top),
                ("frame_crop_bottom_offset", crop_bottom),
            ]
            fields.append(("frame_cropping", True, crop_children))
        else:
            fields.append(("frame_cropping", False))

        # Compute actual dimensions
        if chroma_format_idc == 0:
            crop_unit_x, crop_unit_y = 1, 2 - (1 if frame_mbs_only_flag else 0)
        elif chroma_format_idc == 1:
            crop_unit_x, crop_unit_y = 2, 2 * (2 - (1 if frame_mbs_only_flag else 0))
        elif chroma_format_idc == 2:
            crop_unit_x, crop_unit_y = 2, 2 - (1 if frame_mbs_only_flag else 0)
        else:
            crop_unit_x, crop_unit_y = 1, 2 - (1 if frame_mbs_only_flag else 0)

        width = (pic_width_in_mbs_minus1 + 1) * 16
        height = (pic_height_in_map_units_minus1 + 1) * 16 * (1 if frame_mbs_only_flag else 2)

        if frame_cropping_flag:
            width -= (crop_left + crop_right) * crop_unit_x
            height -= (crop_top + crop_bottom) * crop_unit_y

        fields.append(("width", width))
        fields.append(("height", height))

        # VUI parameters
        vui_present = reader.read_bool()
        if vui_present and reader.bits_remaining > 0:
            vui_children = _parse_vui_fields(reader)
            fields.append(("vui_parameters", True, vui_children))
        else:
            fields.append(("vui_parameters_present", False))

        return fields

    except (EOFError, ValueError, IndexError):
        # Return whatever we parsed so far
        return fields if fields else None


def _skip_scaling_list(reader: BitReader, size: int) -> None:
    """Skip a scaling list in the SPS."""
    last_scale = 8
    next_scale = 8
    for _ in range(size):
        if next_scale != 0:
            delta = reader.read_se()
            next_scale = (last_scale + delta + 256) % 256
        last_scale = next_scale if next_scale != 0 else last_scale


def _parse_vui_fields(reader: BitReader) -> List[FieldEntry]:
    """Parse VUI parameters and return as field list."""
    fields: List[FieldEntry] = []
    try:
        # aspect_ratio_info
        aspect_present = reader.read_bool()
        if aspect_present:
            aspect_ratio_idc = reader.read_bits(8)
            ar_children: List[FieldEntry] = [("aspect_ratio_idc", aspect_ratio_idc)]
            if aspect_ratio_idc == 255:  # Extended_SAR
                ar_children.append(("sar_width", reader.read_bits(16)))
                ar_children.append(("sar_height", reader.read_bits(16)))
            fields.append(("aspect_ratio_info", True, ar_children))

        # overscan_info
        if reader.read_bool():
            fields.append(("overscan_appropriate_flag", reader.read_bool()))

        # video_signal_type
        video_signal_present = reader.read_bool()
        if video_signal_present:
            vs_children: List[FieldEntry] = []
            vs_children.append(("video_format", reader.read_bits(3)))
            vs_children.append(("video_full_range_flag", reader.read_bool()))
            colour_desc_present = reader.read_bool()
            if colour_desc_present:
                vs_children.append(("colour_primaries", reader.read_bits(8)))
                vs_children.append(("transfer_characteristics", reader.read_bits(8)))
                vs_children.append(("matrix_coefficients", reader.read_bits(8)))
            fields.append(("video_signal_type", True, vs_children))

        # chroma_loc_info
        if reader.read_bool():
            cl_children: List[FieldEntry] = [
                ("chroma_sample_loc_type_top", reader.read_ue()),
                ("chroma_sample_loc_type_bottom", reader.read_ue()),
            ]
            fields.append(("chroma_loc_info", True, cl_children))

        # timing_info
        timing_present = reader.read_bool()
        if timing_present:
            num_units_in_tick = reader.read_bits(32)
            time_scale = reader.read_bits(32)
            fixed_frame_rate = reader.read_bool()
            t_children: List[FieldEntry] = [
                ("num_units_in_tick", num_units_in_tick),
                ("time_scale", time_scale),
                ("fixed_frame_rate_flag", fixed_frame_rate),
            ]
            if num_units_in_tick > 0:
                fps = time_scale / (2.0 * num_units_in_tick)
                t_children.append(("framerate", f"{fps:.4f}"))
            fields.append(("timing_info", True, t_children))

    except (EOFError, ValueError):
        pass  # VUI parsing is best-effort
    return fields
