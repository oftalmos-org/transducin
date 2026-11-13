"""ETDRS legacy frente a v2 con los datasets reales del corpus local (Topcon UK Biobank y eyepydata)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from transducin.adapters.topcon import TopconFdaAdapter
from transducin.etdrs import HEIDELBERG_VOL, LEGACY_ASSUMED, REGIONS, compute_etdrs_v2, find_foveal_candidate
from transducin.etdrs_policy import _masks as legacy_masks
from transducin.revo_opt_reader import compute_etdrs

pytestmark = pytest.mark.real_data

_ROOT = os.environ.get("TRANSDUCIN_REAL_DATA_DIR")
FDA = Path(_ROOT, "public/topcon/fda/eg_oct_fda.fda") if _ROOT else None
NPZ = Path(_ROOT, "public/heidelberg/derived/eyepydata_layers_ilm_bm.npz") if _ROOT else None
needs_fda = pytest.mark.skipif(not (FDA and FDA.exists()), reason="falta el .fda real")
needs_npz = pytest.mark.skipif(not (NPZ and NPZ.exists()), reason="faltan los derivados de eyepydata")


def _heidelberg(tag):
    z = np.load(NPZ)
    sz, sy, sx = (float(v) * 1000.0 for v in z[f"{tag}_scale_mm_zyx"])  # entre B-scans, axial, lateral (µm)
    return z, (z[f"{tag}_bm_px"] - z[f"{tag}_ilm_px"]) * sy, sx, sz


def _legacy(t, lateral_um, scan_mm, lat):
    g = compute_etdrs(
        np.zeros_like(t), t, {"axial_um": 1.0, "lateral_um": lateral_um, "scan_width_mm": scan_mm}, laterality=lat
    )
    return {k: getattr(g, k) for k in REGIONS}


@needs_npz
@pytest.mark.parametrize("tag", ["od", "os"])
def test_heidelberg_file_facts_bscan0_is_inferior_and_ascans_run_left_to_right(tag):
    z = np.load(NPZ)
    start, end = z[f"{tag}_bscan_start_mm"], z[f"{tag}_bscan_end_mm"]
    assert start[0, 1] > start[-1, 1]  # el B-scan 0 está más abajo en el SLO (y mayor) → inferior
    assert (end[:, 0] > start[:, 0]).all()  # los A-scans crecen de izquierda a derecha en el SLO


@needs_npz
@pytest.mark.parametrize("tag,lat", [("od", "R"), ("os", "L")])
def test_heidelberg_small_cube_v2_coverage_and_legacy_overlap(tag, lat):
    _z, t, lateral, bscan = _heidelberg(tag)
    r = compute_etdrs_v2(t, lateral_um=lateral, bscan_um=bscan, laterality=lat, orientation=HEIDELBERG_VOL)
    assert all(r.regions[n].available and r.regions[n].coverage > 0.95 for n in ("C", "S1", "N1", "I1", "T1"))
    assert all(not r.regions[n].available and r.regions[n].coverage < 0.5 for n in ("S2", "N2", "I2", "T2"))
    m = legacy_masks(t.shape[0], t.shape[1], lateral, bscan, lat)
    inner = sum(int(m[s].sum()) for s in ("S1", "N1", "I1", "T1"))
    ring = int(np.logical_or.reduce([m[s] for s in ("S1", "N1", "I1", "T1")]).sum())
    assert inner / ring == pytest.approx(2.0, abs=0.001)  # legacy: cada píxel del anillo está en dos sectores
    assert r.central_label == "central_disc_geometric" and not r.clinical_central_eligible


@needs_npz
@pytest.mark.parametrize("tag,lat", [("od", "R"), ("os", "L")])
def test_heidelberg_orientation_consistency_nasal_thicker_than_temporal(tag, lat):
    """Comprobación de consistencia (2 ojos de un sujeto; NO prueba): con la convención de fondo de ojo el
    sector nasal interno sale más grueso que el temporal, como en mácula sana; con el supuesto legacy, al revés."""
    _z, t, lateral, bscan = _heidelberg(tag)
    heid = compute_etdrs_v2(t, lateral_um=lateral, bscan_um=bscan, laterality=lat, orientation=HEIDELBERG_VOL)
    leg = compute_etdrs_v2(t, lateral_um=lateral, bscan_um=bscan, laterality=lat, orientation=LEGACY_ASSUMED)
    assert heid.value("N1") > heid.value("T1")
    assert leg.value("N1") < leg.value("T1")
    assert any("orientación NO validada" in w for w in heid.warnings)


@needs_npz
@pytest.mark.parametrize("tag", ["od", "os"])
def test_heidelberg_foveal_candidate_is_close_to_geometric_center(tag):
    _z, t, lateral, bscan = _heidelberg(tag)
    c = find_foveal_candidate(t, lateral, bscan)
    g = ((t.shape[0] - 1) / 2, (t.shape[1] - 1) / 2)
    assert abs(c.row - g[0]) * bscan < 150 and abs(c.col - g[1]) * lateral < 150  # µm
    assert c.source == "foveal_candidate_min_thickness" and c.validated is False


@needs_fda
def test_topcon_legacy_vs_v2_on_both_experimental_maps():
    res = TopconFdaAdapter(experimental=True).experimental_thickness(FDA)
    for t in (res.thickness_um, res.rpe_thickness_um):
        leg = _legacy(t, 11.71875, 6.0, "L")
        v = compute_etdrs_v2(t, lateral_um=11.71875, bscan_um=46.875, laterality="L", orientation=LEGACY_ASSUMED)
        assert all(v.regions[n].available for n in REGIONS)  # cubo de 6 × 6 mm: cobertura completa
        assert v.value("C") == pytest.approx(leg["C"], abs=0.5)  # el centro no depende de la geometría de sectores
        diffs = [abs(v.value(k) - leg[k]) for k in REGIONS if k != "C"]
        assert 0.5 < max(diffs) < 10.0  # los sectores sí cambian, en µm de un ojo normal
        assert any("orientación NO validada" in w for w in v.warnings)


@needs_fda
def test_topcon_orientation_sensitivity_is_larger_than_the_geometry_effect():
    res = TopconFdaAdapter(experimental=True).experimental_thickness(FDA)
    t = res.thickness_um
    base = compute_etdrs_v2(t, lateral_um=11.71875, bscan_um=46.875, laterality="L", orientation=LEGACY_ASSUMED)
    swapped = compute_etdrs_v2(
        t,
        lateral_um=11.71875,
        bscan_um=46.875,
        laterality="L",
        orientation=type(LEGACY_ASSUMED)("superior", False, False, False, "prueba: A-scan hacia temporal"),
    )
    assert abs(base.value("N1") - swapped.value("N1")) > 8.0  # equivocar el lado nasal mueve el sector > 8 µm
