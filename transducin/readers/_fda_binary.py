# transducin/readers/_fda_binary.py
#
# Vendorized from marksgraham/OCT-Converter (MIT License)
# Source: oct_converter/readers/binary_structs/fda_binary.py
# Copyright (c) marksgraham/OCT-Converter contributors
# https://github.com/marksgraham/OCT-Converter
#
# Binary struct definitions for Topcon .fda/.fds (Topcon File Format).
# Based on: https://bitbucket.org/uocte/uocte/wiki/Topcon%20File%20Format
from construct import (
    Array,
    Float32n,
    Float64n,
    Int8un,
    Int16un,
    Int32un,
    PaddedString,
    Struct,
    this,
)

header = Struct(
    "file_code" / PaddedString(4, "ascii"),
    "file_type" / PaddedString(3, "ascii"),
    "major_ver" / Int32un,
    "minor_ver" / Int32un,
)

oct_header = Struct(
    "scan_mode" / Int8un,
    "unknown1" / Int32un,
    "unknown2" / Int32un,
    "width" / Int32un,
    "height" / Int32un,
    "number_slices" / Int32un,
    "unknown3" / Int32un,
)

oct_header_2 = Struct(
    "scan_mode" / Int8un,
    "width" / Int32un,
    "height" / Int32un,
    "bits_per_pixel" / Int32un,
    "number_slices" / Int32un,
    "format" / Int8un,
    "size" / Int32un,
)

fundus_header = Struct(
    "width" / Int32un,
    "height" / Int32un,
    "bits_per_pixel" / Int32un,
    "number_slices" / Int32un,
    "format" / PaddedString(4, "ascii"),
    "size" / Int32un,
)

hw_info_03_header = Struct(
    "model_name" / PaddedString(16, "ascii"),
    "serial_number" / PaddedString(16, "ascii"),
    "spect_sn" / PaddedString(16, "ascii"),
    "rom_ver" / PaddedString(16, "ascii"),
    "unknown" / PaddedString(16, "ascii"),
    "eq_calib_year" / Int16un,
    "eq_calib_month" / Int16un,
    "eq_calib_day" / Int16un,
    "eq_calib_hour" / Int16un,
    "eq_calib_minute" / Int16un,
    "spect_calib_year" / Int16un,
    "spect_calib_month" / Int16un,
    "spect_calib_day" / Int16un,
    "spect_calib_hour" / Int16un,
    "spect_calib_minute" / Int16un,
)

hw_info_02_header = Struct(
    "model_name" / PaddedString(16, "ascii"),
    "serial_number" / PaddedString(16, "ascii"),
    "spect_sn" / PaddedString(16, "ascii"),
    "rom_ver" / PaddedString(16, "ascii"),
    "eq_calib_year" / Int16un,
    "eq_calib_month" / Int16un,
    "eq_calib_day" / Int16un,
    "spect_calib_year" / Int16un,
    "spect_calib_month" / Int16un,
    "spect_calib_day" / Int16un,
)

hw_info_01_header = Struct(
    "model_name" / PaddedString(16, "ascii"),
    "serial_number" / PaddedString(16, "ascii"),
    "spect_sn" / PaddedString(16, "ascii"),
    "rom_ver" / PaddedString(16, "ascii"),
    "eq_calib_year" / Int16un,
    "eq_calib_month" / Int16un,
    "eq_calib_day" / Int16un,
    "spect_calib_year" / Int16un,
    "spect_calib_month" / Int16un,
    "spect_calib_day" / Int16un,
)

patient_info_02_header = Struct(
    "patient_id" / PaddedString(32, "ascii"),
    "first_name" / PaddedString(32, "ascii"),
    "last_name" / PaddedString(32, "ascii"),
    "mid_name" / PaddedString(8, "ascii"),
    "sex" / Int8un,
    "birth_date" / Int16un[3],
    "occup_reg" / Int8un[20][2],
    "r_date" / Int16un[3],
    "record_id" / Int8un[16],
    "lv_date" / Int16un[3],
    "physician" / Int8un[64][2],
    "zip_code" / Int8un[12],
    "addr" / Int8un[48][2],
    "phones" / Int8un[16][2],
    "nx_date" / Int16un[6],
    "multipurpose_field" / Int8un[20][3],
    "descp" / Int8un[64],
    "reserved" / Int8un[32],
)

capture_info_02_header = Struct(
    "eye" / Int8un,
    "scan_mode" / Int8un,
    "session_id" / Int32un,
    "label" / PaddedString(100, "ascii"),
    "cap_date" / Int16un[6],
)

capture_info_header = Struct(
    "eye" / Int8un,
    "cap_date" / Int16un[6],
)

param_scan_04_header = Struct(
    "fixation" / Int32un,
    "mirror_pos" / Int32un,
    "polar" / Int32un,
    "x_dimension_mm" / Float64n,
    "y_dimension_mm" / Float64n,
    "z_resolution_um" / Float64n,
    "comp_eff_2" / Float64n,
    "comp_eff_3" / Float64n,
    "base_pos" / Int8un,
    "used_calib_data" / Int8un,
)

param_scan_02_header = Struct(
    "scan_mode" / Int8un,
    "light_level" / Int32un,
    "fixation" / Int32un,
    "mirror_pos" / Int32un,
    "nd" / Int32un,
    "polar" / Int32un,
    "x_dimension_mm" / Float64n,
    "y_dimension_mm" / Float64n,
    "z_resolution_um" / Float64n,
    "comp_eff_2" / Float64n,
    "comp_eff_3" / Float64n,
    "noise_thresh" / Float64n,
    "range_adj" / Float64n,
    "base_pos" / Int8un,
)

img_trc_02_header = Struct(
    "width" / Int32un,
    "height" / Int32un,
    "bits_per_pixel" / Int32un,
    "num_slices_0x2" / Int32un,
    "format" / Int8un,
    "size" / Int32un,
)

contour_info_header = Struct(
    "id" / PaddedString(20, "ascii"),
    "method" / Int8un,
    "format" / Int8un,
    "width" / Int32un,
    "height" / Int32un,
    "size" / Int32un,
)

fast_q2_info_header = Struct("various_quality_statistics" / Float32n[6])

main_module_info_header = Struct(
    "software_name" / PaddedString(128, "ascii"),
    "file_version_1" / Int16un,
    "file_version_2" / Int16un,
    "file_version_3" / Int16un,
    "file_version_4" / Int16un,
    "string" / PaddedString(128, "ascii"),
)

thumbnail_header = Struct(
    "size" / Int32un,
)

patientext_info_header = Struct("unknown" / Int8un[128])
