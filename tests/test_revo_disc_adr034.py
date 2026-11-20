# SPDX-License-Identifier: Apache-2.0
"""ADR-034: copa óptica de Revo por el plano de las puntas de DMARKERS desplazado 150 µm hacia el vítreo.

Datos sintéticos (sin pacientes). Disco de 100 A-scans × 32 B-scans con las puntas en la fila 300; el borde (ILM) está
en la fila 200, por encima del plano desplazado (fila 250), y una copa rectangular de 40 A-scans × 16 B-scans con la
ILM en la fila 320 queda 70 px (210 µm) por debajo de él.
"""

import math

import numpy as np
import pytest

from transducin.clinical_data import OCTClinicalData
from transducin.revo_opt_reader import disc_metrics_from_markers
from transducin.sr_builder import build_sr

N_B, N_A = 64, 200
PARAMS = {"axial_um": 3.0, "lateral_um": 10.0, "scan_width_mm": 6.4, "depth_px": 1024}  # 0.01 mm × 0.1 mm por celda
DISC_B, DISC_X = (16, 48), (50.0, 150.0)
CUP_B, CUP_X = (24, 40), (80, 120)
TIP_Y, RIM_Y, CUP_Y = 300.0, 200.0, 320.0


def _volume():
    top = np.full((N_B, N_A), RIM_Y, dtype=np.float32)
    top[CUP_B[0] : CUP_B[1], CUP_X[0] : CUP_X[1]] = CUP_Y
    markers = np.full((N_B, 4), np.nan)
    markers[DISC_B[0] : DISC_B[1]] = [DISC_X[0], TIP_Y, DISC_X[1], TIP_Y]
    return top, markers


def test_plane_method_matches_closed_form():
    top, markers = _volume()
    m = disc_metrics_from_markers(top, markers, PARAMS)
    assert m["cup_method"] == "dmarkers_plane"
    expected = {
        "disc_diam_h_mm": 1.0,  # 100 A-scans × 0.01 mm
        "disc_diam_v_mm": 3.2,  # 32 B-scans × 0.1 mm
        "cup_diam_h_mm": 0.4,
        "cup_diam_v_mm": 1.6,
        "disc_area_mm2": 3.2,
        "cup_area_mm2": 0.64,
        "rim_area_mm2": 2.56,
        "cup_depth_mean_mm": 0.21,  # (320 − 250) px × 3 µm, desde el plano desplazado
        "cup_depth_max_mm": 0.21,
        "cup_vol_mm3": 0.1344,  # 0.21 mm × 0.64 mm²
        "cdr": 0.2,  # razón de áreas
        "vcdr": 0.5,
        "hcdr": 0.4,
    }
    for key, value in expected.items():
        assert math.isclose(m[key], value, abs_tol=1e-3), (key, m[key], value)


def test_rim_above_tips_but_below_shifted_plane_is_cup():
    """Sin el desplazamiento de 150 µm el borde no sería copa; con él, una ILM 30 µm sobre las puntas sí lo es."""
    top, markers = _volume()
    top[DISC_B[0] : DISC_B[1], 50:60] = TIP_Y - 10  # 30 µm sobre las puntas, 120 µm bajo el plano desplazado
    m = disc_metrics_from_markers(top, markers, PARAMS)
    assert m["cup_area_mm2"] > 0.64


def test_tilted_tips_follow_the_line_between_them():
    top, markers = _volume()
    markers[DISC_B[0] : DISC_B[1]] = [DISC_X[0], 250.0, DISC_X[1], 350.0]  # el plano baja 1 px por A-scan
    m = disc_metrics_from_markers(top, markers, PARAMS)
    # En la copa (x 80..119) el plano desplazado va de 230 a 269: toda la copa (fila 320) sigue debajo.
    assert math.isclose(m["cup_area_mm2"], 0.64, abs_tol=1e-3)
    assert 0.15 < m["cup_depth_mean_mm"] < 0.27


@pytest.mark.parametrize("bad_y", [np.nan, 0.0, 5000.0])
def test_unusable_axial_tips_fall_back_to_rim_threshold_for_whole_volume(bad_y):
    top, markers = _volume()
    markers[DISC_B[0] + 3, 1] = bad_y
    m = disc_metrics_from_markers(top, markers, PARAMS)
    assert m["cup_method"] == "ilm_rim_threshold"
    # Referencia = ILM del borde (200) + 150 µm (50 px) = 250: misma copa en este volumen sintético.
    assert math.isclose(m["cup_area_mm2"], 0.64, abs_tol=1e-3)


def test_swapped_tips_are_reordered():
    top, markers = _volume()
    markers[DISC_B[0] : DISC_B[1]] = [DISC_X[1], TIP_Y, DISC_X[0], TIP_Y]
    m = disc_metrics_from_markers(top, markers, PARAMS)
    assert math.isclose(m["cdr"], 0.2, abs_tol=1e-3)


def test_too_few_disc_rows_returns_none():
    top, markers = _volume()
    markers[:] = np.nan
    markers[30:32] = [DISC_X[0], TIP_Y, DISC_X[1], TIP_Y]
    assert disc_metrics_from_markers(top, markers, PARAMS) is None


def test_sr_emits_each_disc_measure_with_its_own_code():
    cd = OCTClinicalData(
        noel_id="XXXX20000101",
        laterality="L",
        study_type="optic_nerve",
        study_date="20260101",
        cup_disc_ratio=0.2,
        vcdr=0.5,
        hcdr=0.4,
        disc_area_mm2=3.2,
        cup_area_mm2=0.64,
        rim_area_mm2=2.56,
        cup_vol_mm3=0.1344,
        cup_depth_mean_mm=0.21,
        cup_depth_max_mm=0.21,
        disc_diam_h_mm=1.0,
        disc_diam_v_mm=3.2,
        cup_diam_h_mm=0.4,
        cup_diam_v_mm=1.6,
        cup_method="dmarkers_plane",
    )
    sr = build_sr(cd)

    found = {}

    def walk(seq):
        for item in seq:
            if "ConceptNameCodeSequence" in item and "MeasuredValueSequence" in item:
                code = item.ConceptNameCodeSequence[0].CodeValue
                found[code] = float(item.MeasuredValueSequence[0].NumericValue)
            if "ContentSequence" in item:
                walk(item.ContentSequence)

    walk(sr.ContentSequence)
    expected = {
        "363932005": 0.2,
        "363930007": 0.5,
        "HCDR": 0.4,
        "DISCAREA": 3.2,
        "CUPAREA": 0.64,
        "RIMAREA": 2.56,
        "CUPVOL": 0.1344,
        "CUPDEPMEAN": 0.21,
        "CUPDEPMAX": 0.21,
        "DISCDIAMH": 1.0,
        "DISCDIAMV": 3.2,
        "CUPDIAMH": 0.4,
        "CUPDIAMV": 1.6,
    }
    for code, value in expected.items():
        assert code in found, code
        assert math.isclose(found[code], value, abs_tol=1e-4), (code, found[code])
