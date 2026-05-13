"""H.264 Picture Parameter Set (PPS) parser.

Parses PPS NALU according to ITU-T H.264 (03/2010) Section 7.3.2.2.

Output format: List of field entries (same as SPS):
  - ("key", value)                    → simple leaf field
  - ("key", value, [children...])     → flag-controlled group
"""

from typing import Optional, List, Tuple, Any, Union

from media_analyzer.parsers.h264.bitreader import BitReader

FieldEntry = Union[Tuple[str, Any], Tuple[str, Any, List[Any]]]


def parse_pps(data: bytes) -> Optional[List[FieldEntry]]:
    """
    Parse H.264 PPS NALU and return structured field list.

    Args:
        data: Raw PPS NALU bytes (starting with the NALU header byte)

    Returns:
        List of FieldEntry tuples, or None if parse fails.
    """
    if not data or len(data) < 2:
        return None

    try:
        # Skip NALU header byte
        reader = BitReader(data[1:])
        fields: List[FieldEntry] = []

        # pic_parameter_set_id: ue(v)
        fields.append(("pic_parameter_set_id", reader.read_ue()))

        # seq_parameter_set_id: ue(v)
        fields.append(("seq_parameter_set_id", reader.read_ue()))

        # entropy_coding_mode_flag: u(1)
        entropy_mode = reader.read_bool()
        fields.append(("entropy_coding_mode",
                       "CABAC" if entropy_mode else "CAVLC"))

        # bottom_field_pic_order_in_frame_present_flag: u(1)
        fields.append(("bottom_field_pic_order_in_frame_present", reader.read_bool()))

        # num_slice_groups_minus1: ue(v)
        num_slice_groups = reader.read_ue()
        fields.append(("num_slice_groups", num_slice_groups + 1))

        if num_slice_groups > 0:
            slice_group_map_type = reader.read_ue()
            sg_children: List[FieldEntry] = [
                ("slice_group_map_type", slice_group_map_type),
                ("_note", f"{num_slice_groups + 1} groups (details skipped)"),
            ]
            fields.append(("slice_groups", True, sg_children))
            return fields  # Complex, stop here

        # num_ref_idx_l0/l1_default_active_minus1
        ref_l0 = reader.read_ue()
        ref_l1 = reader.read_ue()
        fields.append(("num_ref_idx_l0_default_active", ref_l0 + 1))
        fields.append(("num_ref_idx_l1_default_active", ref_l1 + 1))

        # weighted_pred_flag: u(1)
        weighted_pred = reader.read_bool()
        fields.append(("weighted_pred_flag", weighted_pred))

        # weighted_bipred_idc: u(2)
        weighted_bipred_idc = reader.read_bits(2)
        bipred_names = {0: "Off", 1: "Explicit", 2: "Implicit"}
        fields.append(("weighted_bipred_idc",
                       f"{weighted_bipred_idc} ({bipred_names.get(weighted_bipred_idc, '?')})"))

        # pic_init_qp_minus26: se(v)
        pic_init_qp = reader.read_se() + 26
        fields.append(("pic_init_qp", pic_init_qp))

        # pic_init_qs_minus26: se(v)
        pic_init_qs = reader.read_se() + 26
        fields.append(("pic_init_qs", pic_init_qs))

        # chroma_qp_index_offset: se(v)
        fields.append(("chroma_qp_index_offset", reader.read_se()))

        # deblocking_filter_control_present_flag: u(1)
        fields.append(("deblocking_filter_control_present", reader.read_bool()))

        # constrained_intra_pred_flag: u(1)
        fields.append(("constrained_intra_pred", reader.read_bool()))

        # redundant_pic_cnt_present_flag: u(1)
        fields.append(("redundant_pic_cnt_present", reader.read_bool()))

        # Extended fields (High profile)
        if reader.bits_remaining > 8:
            transform_8x8 = reader.read_bool()
            ext_children: List[FieldEntry] = [
                ("transform_8x8_mode_flag", transform_8x8),
            ]

            scaling_present = reader.read_bool()
            ext_children.append(("pic_scaling_matrix_present", scaling_present))
            if scaling_present:
                num_lists = 6 + (2 if transform_8x8 else 0)
                for i in range(num_lists):
                    if reader.read_bool():
                        _skip_scaling_list(reader, 16 if i < 6 else 64)

            second_chroma_qp = reader.read_se()
            ext_children.append(("second_chroma_qp_index_offset", second_chroma_qp))

            fields.append(("High Profile Extensions", True, ext_children))

        return fields

    except (EOFError, ValueError, IndexError):
        return fields if fields else None


def _skip_scaling_list(reader: BitReader, size: int) -> None:
    """Skip a scaling list."""
    last_scale = 8
    next_scale = 8
    for _ in range(size):
        if next_scale != 0:
            delta = reader.read_se()
            next_scale = (last_scale + delta + 256) % 256
        last_scale = next_scale if next_scale != 0 else last_scale
