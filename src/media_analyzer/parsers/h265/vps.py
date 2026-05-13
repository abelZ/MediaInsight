"""H.265/HEVC Video Parameter Set (VPS) parser.

Parses VPS NALU according to ITU-T H.265 (04/2015) Section 7.3.2.1.
"""

from typing import Optional, List, Tuple, Any, Union
from media_analyzer.parsers.h264.bitreader import BitReader

FieldEntry = Union[Tuple[str, Any], Tuple[str, Any, List[Any]]]


def parse_hevc_vps(data: bytes) -> Optional[List[FieldEntry]]:
    """
    Parse HEVC VPS NALU.

    Args:
        data: Raw VPS NALU bytes (starting with 2-byte NALU header)

    Returns:
        List of FieldEntry tuples, or None if parse fails.
    """
    if not data or len(data) < 4:
        return None

    try:
        # Skip 2-byte HEVC NALU header
        reader = BitReader(data[2:])
        fields: List[FieldEntry] = []

        # vps_video_parameter_set_id: u(4)
        vps_id = reader.read_bits(4)
        fields.append(("vps_video_parameter_set_id", vps_id))

        # vps_base_layer_internal_flag: u(1)
        fields.append(("vps_base_layer_internal_flag", reader.read_bool()))

        # vps_base_layer_available_flag: u(1)
        fields.append(("vps_base_layer_available_flag", reader.read_bool()))

        # vps_max_layers_minus1: u(6)
        max_layers = reader.read_bits(6)
        fields.append(("vps_max_layers_minus1", max_layers))

        # vps_max_sub_layers_minus1: u(3)
        max_sub_layers = reader.read_bits(3)
        fields.append(("vps_max_sub_layers_minus1", max_sub_layers))

        # vps_temporal_id_nesting_flag: u(1)
        fields.append(("vps_temporal_id_nesting_flag", reader.read_bool()))

        # vps_reserved_0xffff_16bits: u(16)
        reader.skip_bits(16)

        # profile_tier_level
        ptl_fields = _parse_profile_tier_level(reader, True, max_sub_layers)
        fields.append(("profile_tier_level", True, ptl_fields))

        # vps_sub_layer_ordering_info_present_flag
        sub_ordering_present = reader.read_bool()
        fields.append(("vps_sub_layer_ordering_info_present", sub_ordering_present))

        # Skip sub-layer ordering info
        start = 0 if sub_ordering_present else max_sub_layers
        for _ in range(start, max_sub_layers + 1):
            reader.read_ue()  # max_dec_pic_buffering
            reader.read_ue()  # max_num_reorder_pics
            reader.read_ue()  # max_latency_increase

        # vps_max_layer_id: u(6)
        fields.append(("vps_max_layer_id", reader.read_bits(6)))

        # vps_num_layer_sets_minus1: ue(v)
        num_layer_sets = reader.read_ue()
        fields.append(("vps_num_layer_sets_minus1", num_layer_sets))

        # vps_timing_info_present_flag
        timing_present = reader.read_bool()
        if timing_present:
            num_units = reader.read_bits(32)
            time_scale = reader.read_bits(32)
            t_children: List[FieldEntry] = [
                ("vps_num_units_in_tick", num_units),
                ("vps_time_scale", time_scale),
            ]
            if num_units > 0:
                t_children.append(("framerate", f"{time_scale / num_units:.4f}"))
            fields.append(("vps_timing_info", True, t_children))
        else:
            fields.append(("vps_timing_info", False))

        return fields

    except (EOFError, ValueError, IndexError):
        return fields if fields else None


def _parse_profile_tier_level(reader: BitReader, profile_present: bool,
                               max_sub_layers: int) -> List[FieldEntry]:
    """Parse profile_tier_level structure."""
    fields: List[FieldEntry] = []
    try:
        if profile_present:
            # general_profile_space: u(2)
            profile_space = reader.read_bits(2)
            fields.append(("general_profile_space", profile_space))

            # general_tier_flag: u(1)
            tier_flag = reader.read_bool()
            fields.append(("general_tier_flag", "High" if tier_flag else "Main"))

            # general_profile_idc: u(5)
            profile_idc = reader.read_bits(5)
            profile_names = {
                1: "Main", 2: "Main 10", 3: "Main Still Picture",
                4: "Range Extensions", 5: "High Throughput",
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
        level_val = level_idc / 30.0
        fields.append(("general_level_idc", f"{level_idc} ({level_val:.1f})"))

        # sub_layer flags (skip detailed parsing)
        if max_sub_layers > 0:
            for _ in range(max_sub_layers):
                reader.read_bits(2)  # sub_layer_profile_present + sub_layer_level_present

    except (EOFError, ValueError):
        pass
    return fields
