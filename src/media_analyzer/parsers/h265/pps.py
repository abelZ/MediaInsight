"""H.265/HEVC Picture Parameter Set (PPS) parser.

Parses PPS NALU according to ITU-T H.265 (04/2015) Section 7.3.2.3.
"""

from typing import Optional, List, Tuple, Any, Union
from media_analyzer.parsers.h264.bitreader import BitReader

FieldEntry = Union[Tuple[str, Any], Tuple[str, Any, List[Any]]]


def parse_hevc_pps(data: bytes) -> Optional[List[FieldEntry]]:
    """
    Parse HEVC PPS NALU.

    Args:
        data: Raw PPS NALU bytes (starting with 2-byte NALU header)

    Returns:
        List of FieldEntry tuples, or None if parse fails.
    """
    if not data or len(data) < 3:
        return None

    try:
        # Skip 2-byte HEVC NALU header
        reader = BitReader(data[2:])
        fields: List[FieldEntry] = []

        # pps_pic_parameter_set_id: ue(v)
        fields.append(("pps_pic_parameter_set_id", reader.read_ue()))

        # pps_seq_parameter_set_id: ue(v)
        fields.append(("pps_seq_parameter_set_id", reader.read_ue()))

        # dependent_slice_segments_enabled_flag: u(1)
        fields.append(("dependent_slice_segments_enabled", reader.read_bool()))

        # output_flag_present_flag: u(1)
        fields.append(("output_flag_present", reader.read_bool()))

        # num_extra_slice_header_bits: u(3)
        num_extra_bits = reader.read_bits(3)
        fields.append(("num_extra_slice_header_bits", num_extra_bits))

        # sign_data_hiding_enabled_flag: u(1)
        fields.append(("sign_data_hiding_enabled", reader.read_bool()))

        # cabac_init_present_flag: u(1)
        fields.append(("cabac_init_present", reader.read_bool()))

        # num_ref_idx_l0_default_active_minus1: ue(v)
        fields.append(("num_ref_idx_l0_default_active", reader.read_ue() + 1))

        # num_ref_idx_l1_default_active_minus1: ue(v)
        fields.append(("num_ref_idx_l1_default_active", reader.read_ue() + 1))

        # init_qp_minus26: se(v)
        init_qp = reader.read_se() + 26
        fields.append(("init_qp", init_qp))

        # constrained_intra_pred_flag: u(1)
        fields.append(("constrained_intra_pred", reader.read_bool()))

        # transform_skip_enabled_flag: u(1)
        fields.append(("transform_skip_enabled", reader.read_bool()))

        # cu_qp_delta_enabled_flag: u(1)
        cu_qp_delta_enabled = reader.read_bool()
        fields.append(("cu_qp_delta_enabled", cu_qp_delta_enabled))
        if cu_qp_delta_enabled:
            fields.append(("diff_cu_qp_delta_depth", reader.read_ue()))

        # pps_cb_qp_offset: se(v)
        fields.append(("pps_cb_qp_offset", reader.read_se()))

        # pps_cr_qp_offset: se(v)
        fields.append(("pps_cr_qp_offset", reader.read_se()))

        # pps_slice_chroma_qp_offsets_present_flag: u(1)
        fields.append(("pps_slice_chroma_qp_offsets_present", reader.read_bool()))

        # weighted_pred_flag: u(1)
        fields.append(("weighted_pred_flag", reader.read_bool()))

        # weighted_bipred_flag: u(1)
        fields.append(("weighted_bipred_flag", reader.read_bool()))

        # transquant_bypass_enabled_flag: u(1)
        fields.append(("transquant_bypass_enabled", reader.read_bool()))

        # tiles_enabled_flag: u(1)
        tiles_enabled = reader.read_bool()

        # entropy_coding_sync_enabled_flag: u(1)
        entropy_sync = reader.read_bool()
        fields.append(("tiles_enabled", tiles_enabled))
        fields.append(("entropy_coding_sync_enabled", entropy_sync))

        if tiles_enabled:
            num_tile_columns = reader.read_ue() + 1
            num_tile_rows = reader.read_ue() + 1
            uniform_spacing = reader.read_bool()
            tile_children: List[FieldEntry] = [
                ("num_tile_columns", num_tile_columns),
                ("num_tile_rows", num_tile_rows),
                ("uniform_spacing_flag", uniform_spacing),
            ]
            if not uniform_spacing:
                for _ in range(num_tile_columns - 1):
                    reader.read_ue()
                for _ in range(num_tile_rows - 1):
                    reader.read_ue()
            if tiles_enabled and entropy_sync:
                tile_children.append(("loop_filter_across_tiles_enabled",
                                     reader.read_bool()))
            fields.append(("tiles", True, tile_children))

        # pps_loop_filter_across_slices_enabled_flag
        fields.append(("loop_filter_across_slices_enabled", reader.read_bool()))

        # deblocking_filter_control_present_flag
        deblocking_present = reader.read_bool()
        if deblocking_present:
            db_children: List[FieldEntry] = []
            override_enabled = reader.read_bool()
            db_children.append(("deblocking_filter_override_enabled", override_enabled))
            pps_deblocking_disabled = reader.read_bool()
            db_children.append(("pps_deblocking_filter_disabled", pps_deblocking_disabled))
            if not pps_deblocking_disabled:
                db_children.append(("pps_beta_offset_div2", reader.read_se()))
                db_children.append(("pps_tc_offset_div2", reader.read_se()))
            fields.append(("deblocking_filter_control", True, db_children))
        else:
            fields.append(("deblocking_filter_control", False))

        return fields

    except (EOFError, ValueError, IndexError):
        return fields if fields else None
