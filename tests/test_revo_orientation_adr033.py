# SPDX-License-Identifier: Apache-2.0
"""ADR-033: orientación validada de Revo, cuadrantes disjuntos, círculo central de 525 µm, anillo pRNFL de 1750 µm y foseta 3D.

Todo con datos sintéticos (sin pacientes). Marco: B-scan 0 superior; el índice de A-scan crece hacia nasal en OD y hacia
temporal en OS (el marco crudo es el del visor).
"""

import numpy as np
import pytest

from transducin.etdrs import LEGACY_ASSUMED, REVO_VALIDATED
from transducin.revo_opt_reader import (
    compute_cmt,
    compute_etdrs,
    compute_gcl_ipl,
    compute_peripapillary_rnfl,
    compute_rnfl_sectors,
    find_foveal_pit,
)

N_B, N_A = 120, 120
PARAMS = {"axial_um": 3.0, "lateral_um": 50.0, "scan_width_mm": 6.0}  # 50 µm por A-scan y por B-scan (6 mm / 120)


def _layers(thickness_um):
    """TOP en 100 px y BM = TOP + espesor/axial: BM − TOP = espesor."""
    top = np.full((N_B, N_A), 100.0, dtype=np.float32)
    return top, top + (thickness_um / PARAMS["axial_um"]).astype(np.float32)


def _thick_toward(side_high_a: bool, base=300.0, extra=80.0):
    """Mapa de espesor con ``extra`` µm más en el lado de A alto (True) o de A bajo (False) del centro."""
    a = np.arange(N_A)[None, :] * np.ones((N_B, 1))
    mask = a > N_A / 2 if side_high_a else a < N_A / 2
    return np.where(mask, base + extra, base)


@pytest.mark.parametrize("laterality", ["R", "L"])
def test_etdrs_nasal_side_follows_validated_orientation(laterality):
    # Lado grueso = nasal. OD: nasal en A alto; OS: nasal en A bajo.
    high = laterality == "R"
    top, bm = _layers(_thick_toward(high))
    g = compute_etdrs(top, bm, PARAMS, laterality=laterality, orientation=REVO_VALIDATED)
    assert g.N2 > g.T2 + 40 and g.N1 > g.T1 + 40


@pytest.mark.parametrize("laterality", ["R", "L"])
def test_legacy_orientation_is_the_mirror(laterality):
    # La regla heredada (sin orientation) pone el nasal en el lado opuesto: documenta lo que ADR-033 corrige.
    high = laterality == "R"
    top, bm = _layers(_thick_toward(high))
    g = compute_etdrs(top, bm, PARAMS, laterality=laterality)
    assert g.T2 > g.N2 + 40


def test_etdrs_regions_are_disjoint_quadrants_with_525_center():
    # Espesor = 100 µm + índice de región: cada región debe verse solo a sí misma (sin solapamiento).
    top, bm = _layers(np.full((N_B, N_A), 300.0))
    g = compute_etdrs(top, bm, PARAMS, laterality="R", center=(60, 60), orientation=REVO_VALIDATED)
    for name in ("C", "S1", "N1", "I1", "T1", "S2", "N2", "I2", "T2"):
        assert getattr(g, name) == pytest.approx(300.0, abs=0.5), name
    # el centro de 525 µm incluye un disco más grueso de 500–525 µm de radio que el de 500 µm
    th = np.full((N_B, N_A), 300.0)
    yy, xx = np.mgrid[0:N_B, 0:N_A]
    r = np.hypot((yy - 60) * 50.0, (xx - 60) * 50.0)
    th[(r >= 505) & (r < 525)] = 600.0
    top, bm = _layers(th)
    g2 = compute_etdrs(top, bm, PARAMS, laterality="R", center=(60, 60), orientation=REVO_VALIDATED)
    assert g2.C > 310.0


def test_cmt_radius_is_525():
    th = np.full((N_B, N_A), 300.0)
    yy, xx = np.mgrid[0:N_B, 0:N_A]
    r = np.hypot((yy - 60) * 50.0, (xx - 60) * 50.0)
    th[(r >= 510) & (r < 525)] = 700.0  # entre 500 y 525 µm: entra solo con radio de 525
    top, bm = _layers(th)
    assert compute_cmt(top, bm, PARAMS, center=(60, 60)) > 305.0


@pytest.mark.parametrize("laterality", ["R", "L"])
def test_macular_rnfl_and_gcipl_follow_orientation(laterality):
    high = laterality == "R"
    base = np.full((N_B, N_A), 100.0, dtype=np.float32)
    layer2 = base + (_thick_toward(high, base=20.0, extra=20.0) / PARAMS["axial_um"]).astype(np.float32)
    nfl = base
    r = compute_rnfl_sectors(nfl, layer2, PARAMS, laterality=laterality, orientation=REVO_VALIDATED)
    assert r.nasal > r.temporal + 10
    g = compute_gcl_ipl(nfl, layer2, PARAMS, laterality=laterality, orientation=REVO_VALIDATED)
    assert g.nasal > g.temporal + 10
    # sin orientation: regla heredada (espejo)
    r_old = compute_rnfl_sectors(nfl, layer2, PARAMS, laterality=laterality)
    assert r_old.temporal > r_old.nasal + 10


@pytest.mark.parametrize("laterality", ["R", "L"])
def test_peripapillary_rnfl_orientation_and_ring(laterality):
    # Anillo de 1750 µm: grosor distinto en el lado nasal (OD: A alto; OS: A bajo) y un grosor distinto dentro de r<1650 µm.
    yy, xx = np.mgrid[0:N_B, 0:N_A]
    r = np.hypot((yy - 60) * 50.0, (xx - 60) * 50.0)
    nasal_is_high = laterality == "R"
    nasal = (xx > 60) if nasal_is_high else (xx < 60)
    th = np.where(r < 1650, 400.0, np.where(nasal, 140.0, 90.0))  # solo el anillo (≥1700) cuenta
    top = np.full((N_B, N_A), 100.0, dtype=np.float32)
    nfl = top + (th / PARAMS["axial_um"]).astype(np.float32)
    s = compute_peripapillary_rnfl(nfl, top, PARAMS, laterality=laterality, b_center=60, a_center=60)
    assert s.nasal == pytest.approx(140.0, abs=5.0)
    assert s.temporal == pytest.approx(90.0, abs=5.0)
    # el espesor de la zona interna (r < 1650 µm) no debe entrar en el anillo de 1750 ± 50 µm
    assert s.global_avg < 150.0


def test_revo_orientation_is_the_opposite_of_legacy_and_validated():
    assert REVO_VALIDATED.validated is True and LEGACY_ASSUMED.validated is False
    assert REVO_VALIDATED.ascan_toward_nasal_od is True and REVO_VALIDATED.ascan_toward_nasal_os is False
    assert REVO_VALIDATED.ascan_toward_nasal_od != LEGACY_ASSUMED.ascan_toward_nasal_od
    assert REVO_VALIDATED.ascan_toward_nasal_os != LEGACY_ASSUMED.ascan_toward_nasal_os


def _volume_with_pit(pit_b, pit_a, depth=120.0):
    yy, xx = np.mgrid[0:N_B, 0:N_A]
    r = np.hypot((yy - pit_b) * 50.0, (xx - pit_a) * 50.0)
    th = 330.0 - depth * np.exp(-((r / 250.0) ** 2))
    return _layers(th)


def test_find_foveal_pit_locates_off_center_pit():
    top, bm = _volume_with_pit(70, 52)
    f = find_foveal_pit(top, bm, PARAMS)
    assert f is not None and abs(f["b"] - 70) <= 2 and abs(f["a"] - 52) <= 2
    assert f["method"] == "foveal_pit_min_thickness" and f["depth_um"] >= 40


def test_find_foveal_pit_rejects_flat_retina():
    top, bm = _layers(np.full((N_B, N_A), 300.0))
    assert find_foveal_pit(top, bm, PARAMS) is None


def test_find_foveal_pit_needs_dense_bscans():
    top, bm = _volume_with_pit(60, 60)
    sparse = dict(PARAMS, scan_width_mm=18.0)  # 150 µm entre B-scans
    assert find_foveal_pit(top, bm, sparse) is None
