"""Revo: el «Wide Field» de córnea (3.724 µm/px, 8 mm) se clasifica como segmento anterior."""

from transducin.opt_extractor import is_anterior_wide


def test_anterior_wide_from_axial_resolution_and_width():
    assert is_anterior_wide({"axial_um": 3.724, "scan_width_mm": 8.0})


def test_retinal_wide_field_is_not_anterior():
    assert not is_anterior_wide({"axial_um": 2.8, "scan_width_mm": 12.0})


def test_missing_params_are_not_anterior():
    assert not is_anterior_wide({})
