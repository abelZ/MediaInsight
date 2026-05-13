"""H.265/HEVC Sequence Parameter Set (SPS) parser.

Parses SPS NALU according to ITU-T H.265 (04/2015) Section 7.3.2.2.
"""

from typing import Optional, List, Tuple, Any, Union
from media_analyzer.parsers.h264.bitreader import BitReader

FieldEntry = Union[Tuple[str, Any], Tuple[str, Any, List[Any]]]

CHROMA_FORMAT_NAMES = {0: "Monochrome", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}


def parse_hevc_sps(data: bytes) -> Optional[List[FieldEntry]]:
    """
    Parse HEVC SPS NALU.

    Args:
        data: Raw SPS NALU bytes (starting with 2-byte NALU header)

    Returns:
        List of FieldEntry tuples, or None if parse fails.
    """
    if not data or len(data) < 4:
        return None

    try:
        # Skip 2-byte HEVC NALU header
        reader = BitReader(data[2:])
        fields: List[FieldEntry] = []

        # sps_video_parameter_set_id: u(4)
        fields.append(("sps_video_parameter_set_id", reader.read_bits(4)))

        # sps_max_sub_layers_minus1: u(3)
        max_sub_layers = reader.read_bits(3)
        fields.append(("sps_max_sub_layers_minus1", max_sub_layers))

        # sps_temporal_id_nesting_flag: u(1)
        fields.append(("sps_temporal_id_nesting_flag", reader.read_bool()))

        # profile_tier_level
        ptl_fields = _parse_profile_tier_level(reader, max_sub_layers)
        fields.append(("profile_tier_level", True, ptl_fields))

        # sps_seq_parameter_set_id: ue(v)
        fields.append(("sps_seq_parameter_set_id", reader.read_ue()))

        # chroma_format_idc: ue(v)
        chroma_format_idc = reader.read_ue()
        chroma_name = CHROMA_FORMAT_NAMES.get(chroma_format_idc, str(chroma_format_idc))
        fields.append(("chroma_format_idc", f"{chroma_format_idc} ({chroma_name})"))

        if chroma_format_idc == 3:
            fields.append(("separate_colour_plane_flag", reader.read_bool()))

        # pic_width_in_luma_samples: ue(v)
        width = reader.read_ue()
        fields.append(("pic_width_in_luma_samples", width))

        # pic_height_in_luma_samples: ue(v)
        height = reader.read_ue()
        fields.append(("pic_height_in_luma_samples", height))

        # conformance_window_flag
        conformance_window = reader.read_bool()
        if conformance_window:
            left = reader.read_ue()
            right = reader.read_ue()
            top = reader.read_ue()
            bottom = reader.read_ue()

            # Calculate sub_width/sub_height for cropping
            sub_width_c = 2 if chroma_format_idc in (1, 2) else 1
            sub_height_c = 2 if chroma_format_idc == 1 else 1

            cropped_w = width - sub_width_c * (left + right)
            cropped_h = height - sub_height_c * (top + bottom)

            crop_children: List[FieldEntry] = [
                ("conf_win_left_offset", left),
                ("conf_win_right_offset", right),
                ("conf_win_top_offset", top),
                ("conf_win_bottom_offset", bottom),
                ("cropped_width", cropped_w),
                ("cropped_height", cropped_h),
            ]
            fields.append(("conformance_window", True, crop_children))
            # Update actual dimensions
            width = cropped_w
            height = cropped_h
        else:
            fields.append(("conformance_window", False))

        fields.append(("width", width))
        fields.append(("height", height))

        # bit_depth_luma_minus8: ue(v)
        bit_depth_luma = reader.read_ue() + 8
        fields.append(("bit_depth_luma", bit_depth_luma))

        # bit_depth_chroma_minus8: ue(v)
        bit_depth_chroma = reader.read_ue() + 8
        fields.append(("bit_depth_chroma", bit_depth_chroma))

        # log2_max_pic_order_cnt_lsb_minus4: ue(v)
        log2_max_poc_lsb = reader.read_ue() + 4
        fields.append(("log2_max_pic_order_cnt_lsb", log2_max_poc_lsb))

        # sps_sub_layer_ordering_info_present_flag
        sub_ordering = reader.read_bool()
        start = 0 if sub_ordering else max_sub_layers
        for _ in range(start, max_sub_layers + 1):
            reader.read_ue()  # max_dec_pic_buffering
            reader.read_ue()  # max_num_reorder_pics
            reader.read_ue()  # max_latency_increase

        # log2_min_luma_coding_block_size_minus3
        log2_min_cb = reader.read_ue() + 3
        fields.append(("log2_min_luma_coding_block_size", log2_min_cb))

        # log2_diff_max_min_luma_coding_block_size
        log2_diff_cb = reader.read_ue()
        fields.append(("log2_max_luma_coding_block_size", log2_min_cb + log2_diff_cb))

        # log2_min_luma_transform_block_size_minus2
        log2_min_tb = reader.read_ue() + 2
        fields.append(("log2_min_luma_transform_block_size", log2_min_tb))

        # log2_diff_max_min_luma_transform_block_size
        log2_diff_tb = reader.read_ue()
        fields.append(("log2_max_luma_transform_block_size", log2_min_tb + log2_diff_tb))

        # max_transform_hierarchy_depth_inter/intra
        fields.append(("max_transform_hierarchy_depth_inter", reader.read_ue()))
        fields.append(("max_transform_hierarchy_depth_intra", reader.read_ue()))

        # scaling_list_enabled_flag
        scaling_list = reader.read_bool()
        fields.append(("scaling_list_enabled", scaling_list))
        if scaling_list:
            if reader.read_bool():  # sps_scaling_list_data_present_flag
                _skip_scaling_list_data(reader)

        # amp_enabled_flag
        fields.append(("amp_enabled_flag", reader.read_bool()))

        # sample_adaptive_offset_enabled_flag
        fields.append(("sample_adaptive_offset_enabled", reader.read_bool()))

        # pcm_enabled_flag
        pcm_enabled = reader.read_bool()
        if pcm_enabled:
            pcm_children: List[FieldEntry] = [
                ("pcm_sample_bit_depth_luma", reader.read_bits(4) + 1),
                ("pcm_sample_bit_depth_chroma", reader.read_bits(4) + 1),
                ("log2_min_pcm_luma_coding_block_size", reader.read_ue() + 3),
            ]
            pcm_children.append(("log2_max_pcm_luma_coding_block_size",
                                pcm_children[-1][1] + reader.read_ue()))
            pcm_children.append(("pcm_loop_filter_disabled", reader.read_bool()))
            fields.append(("pcm", True, pcm_children))
        else:
            fields.append(("pcm_enabled", False))

        # num_short_term_ref_pic_sets
        num_st_rps = reader.read_ue()
        fields.append(("num_short_term_ref_pic_sets", num_st_rps))

        # long_term_ref_pics_present_flag
        lt_ref_present = reader.read_bool()
        fields.append(("long_term_ref_pics_present", lt_ref_present))

        # sps_temporal_mvp_enabled_flag
        fields.append(("sps_temporal_mvp_enabled", reader.read_bool()))

        # strong_intra_smoothing_enabled_flag
        fields.append(("strong_intra_smoothing_enabled", reader.read_bool()))

        # vui_parameters_present_flag
        vui_present = reader.read_bool()
        if vui_present and reader.bits_remaining > 16:
            vui_children = _parse_vui_basic(reader)
            fields.append(("vui_parameters", True, vui_children))
        else:
            fields.append(("vui_parameters", False))

        return fields

    except (EOFError, ValueError, IndexError):
        return fields if fields else None


def _parse_profile_tier_level(reader: BitReader, max_sub_layers: int) -> List[FieldEntry]:
    """Parse profile_tier_level structure for HEVC SPS."""
    fields: List[FieldEntry] = []
    try:
        profile_space = reader.read_bits(2)
        fields.append(("general_profile_space", profile_space))

        tier_flag = reader.read_bool()
        fields.append(("general_tier_flag", "High" if tier_flag else "Main"))

        profile_idc = reader.read_bits(5)
        profile_names = {
            1: "Main", 2: "Main 10", 3: "Main Still Picture",
            4: "Range Extensions", 5: "High Throughput",
            9: "Screen Content Coding",
        }
        fields.append(("general_profile_idc",
                      f"{profile_idc} ({profile_names.get(profile_idc, 'Unknown')})"))

        # general_profile_compatibility_flags: u(32)
        compat = reader.read_bits(32)
        fields.append(("general_profile_compatibility", f"0x{compat:08X}"))

        # general_constraint_indicator_flags: u(48)
        constraints_hi = reader.read_bits(32)
        constraints_lo = reader.read_bits(16)
        fields.append(("general_constraint_flags",
                      f"0x{constraints_hi:08X}{constraints_lo:04X}"))

        # general_level_idc: u(8)
        level_idc = reader.read_bits(8)
        fields.append(("general_level_idc", f"{level_idc} (Level {level_idc / 30.0:.1f})"))

        # Sub-layer profile/level presence flags
        if max_sub_layers > 0:
            sub_profile_present = []
            sub_level_present = []
            for i in range(max_sub_layers):
                sub_profile_present.append(reader.read_bool())
                sub_level_present.append(reader.read_bool())
            if max_sub_layers < 8:
                reader.skip_bits(2 * (8 - max_sub_layers))  # reserved

            # Skip sub-layer details
            for i in range(max_sub_layers):
                if sub_profile_present[i]:
                    reader.skip_bits(2 + 1 + 5 + 32 + 48)  # profile info
                if sub_level_present[i]:
                    reader.skip_bits(8)

    except (EOFError, ValueError):
        pass
    return fields


def _skip_scaling_list_data(reader: BitReader) -> None:
    """Skip HEVC scaling list data."""
    try:
        for size_id in range(4):
            num = 6 if size_id == 3 else 16 if size_id == 0 else 6
            for _ in range(num):
                pred_mode = reader.read_bool()
                if not pred_mode:
                    reader.read_ue()
                else:
                    coeff_num = min(64, 1 << (4 + (size_id << 1)))
                    if size_id > 1:
                        reader.read_se()
                    for _ in range(coeff_num - 1):
                        reader.read_se()
    except (EOFError, ValueError):
        pass


def _parse_vui_basic(reader: BitReader) -> List[FieldEntry]:
    """Parse basic VUI parameters for HEVC."""
    fields: List[FieldEntry] = []
    try:
        # aspect_ratio_info_present_flag
        if reader.read_bool():
            ar_idc = reader.read_bits(8)
            ar_children: List[FieldEntry] = [("aspect_ratio_idc", ar_idc)]
            if ar_idc == 255:
                ar_children.append(("sar_width", reader.read_bits(16)))
                ar_children.append(("sar_height", reader.read_bits(16)))
            fields.append(("aspect_ratio_info", True, ar_children))

        # overscan_info_present_flag
        if reader.read_bool():
            fields.append(("overscan_appropriate_flag", reader.read_bool()))

        # video_signal_type_present_flag
        if reader.read_bool():
            vs_children: List[FieldEntry] = [
                ("video_format", reader.read_bits(3)),
                ("video_full_range_flag", reader.read_bool()),
            ]
            if reader.read_bool():  # colour_description_present
                vs_children.append(("colour_primaries", reader.read_bits(8)))
                vs_children.append(("transfer_characteristics", reader.read_bits(8)))
                vs_children.append(("matrix_coefficients", reader.read_bits(8)))
            fields.append(("video_signal_type", True, vs_children))

        # chroma_loc_info_present_flag
        if reader.read_bool():
            fields.append(("chroma_sample_loc_type_top", reader.read_ue()))
            fields.append(("chroma_sample_loc_type_bottom", reader.read_ue()))

        # neutral_chroma_indication_flag, field_seq_flag, frame_field_info_present_flag
        fields.append(("neutral_chroma_indication_flag", reader.read_bool()))
        fields.append(("field_seq_flag", reader.read_bool()))
        fields.append(("frame_field_info_present_flag", reader.read_bool()))

        # default_display_window_flag
        if reader.read_bool():
            dw_children: List[FieldEntry] = [
                ("def_disp_win_left_offset", reader.read_ue()),
                ("def_disp_win_right_offset", reader.read_ue()),
                ("def_disp_win_top_offset", reader.read_ue()),
                ("def_disp_win_bottom_offset", reader.read_ue()),
            ]
            fields.append(("default_display_window", True, dw_children))

        # vui_timing_info_present_flag
        if reader.read_bool():
            num_units = reader.read_bits(32)
            time_scale = reader.read_bits(32)
            t_children: List[FieldEntry] = [
                ("vui_num_units_in_tick", num_units),
                ("vui_time_scale", time_scale),
            ]
            if num_units > 0:
                t_children.append(("framerate", f"{time_scale / num_units:.4f}"))
            fields.append(("vui_timing_info", True, t_children))

    except (EOFError, ValueError):
        pass
    return fields
