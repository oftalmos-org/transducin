"""Política de cobertura ETDRS por adaptador (sin tocar compute_etdrs ni Revo/Cirrus)."""

from __future__ import annotations

import numpy as np
import pytest

from tests._synthetic import build_fda
from transducin.adapters.topcon import TopconFdaAdapter
from transducin.etdrs_policy import STRICT, EtdrsCoveragePolicy, compute_etdrs_with_policy, unavailable_summary
from transducin.revo_opt_reader import compute_etdrs
from transducin.topcon_fda_extractor import measure_fda_volume

FIELDS = ("C", "S1", "N1", "I1", "T1", "S2", "N2", "I2", "T2")


def _cube(n_b, n_a, scan_mm, thick_um=300.0, axial_um=3.5, seed=0):
    rng = np.random.default_rng(seed)
    top = np.full((n_b, n_a), 100.0)
    bm = top + (thick_um + rng.normal(0, 15, (n_b, n_a))) / axial_um
    params = {
        "axial_um": axial_um,
        "lateral_um": scan_mm * 1000 / n_a,
        "scan_width_mm": scan_mm,
        "n_bscans": n_b,
        "n_ascans": n_a,
    }
    return top, bm, params


def _vals(grid):
    return {f: getattr(grid, f) for f in FIELDS}


def test_without_policy_result_is_exactly_compute_etdrs():
    top, bm, params = _cube(128, 512, 6.0)
    ref = compute_etdrs(top, bm, params, laterality="R")
    grid, report = compute_etdrs_with_policy(top, bm, params, "R", policy=None)
    assert _vals(grid) == _vals(ref) and report == {}


def test_permissive_policy_matches_compute_etdrs_so_masks_do_not_drift():
    """Las máscaras replicadas deben dar los mismos sectores que compute_etdrs (incluye NaN y fuera de rango)."""
    top, bm, params = _cube(96, 160, 5.0, seed=3)
    bm[10:20, 30:60] = np.nan
    bm[50:55, 100:110] = top[50:55, 100:110] - 5  # espesor negativo
    for lat in ("R", "L"):
        ref = compute_etdrs(top, bm, params, laterality=lat)
        grid, report = compute_etdrs_with_policy(top, bm, params, lat, EtdrsCoveragePolicy("permisiva", 0.0, 0.0))
        got, want = _vals(grid), _vals(ref)
        for f in FIELDS:
            assert got[f] == pytest.approx(want[f]) if want[f] is not None else got[f] is None
        assert all(report[f]["available"] for f in FIELDS)


def test_six_mm_cube_has_full_coverage():
    top, bm, params = _cube(128, 512, 6.0)
    grid, report = compute_etdrs_with_policy(top, bm, params, "L", STRICT)
    assert all(getattr(grid, f) is not None for f in FIELDS)
    assert all(report[f]["coverage"] >= 0.95 for f in FIELDS)
    assert unavailable_summary(report) is None


def test_small_cube_marks_outer_ring_unavailable_instead_of_representative_value():
    top, bm, params = _cube(512, 512, 3.2)  # como los .vol de eyepydata: el anillo externo no cabe
    legacy = compute_etdrs(top, bm, params, laterality="R")
    assert legacy.S2 is not None  # comportamiento histórico: un valor de solo las esquinas
    grid, report = compute_etdrs_with_policy(top, bm, params, "R", STRICT)
    for f in ("S2", "N2", "I2", "T2"):
        assert getattr(grid, f) is None
        assert report[f]["reason"] == "coverage_below_threshold" and report[f]["coverage"] < 0.3
    for f in ("C", "S1", "N1", "I1", "T1"):
        assert getattr(grid, f) == pytest.approx(getattr(legacy, f)) and report[f]["available"]
    assert "S2 (cobertura" in unavailable_summary(report)


def test_low_valid_fraction_marks_sector_unavailable():
    top, bm, params = _cube(128, 512, 6.0)
    bm[:64, :] = np.nan  # mitad superior sin segmentación
    grid, report = compute_etdrs_with_policy(top, bm, params, "R", STRICT)
    assert grid.S1 is None and report["S1"]["reason"] == "valid_fraction_below_threshold"
    assert grid.I1 is not None and report["I1"]["available"]


def test_policy_is_per_adapter_and_legacy_extractors_keep_old_behaviour(tmp_path):
    p3 = build_fda(tmp_path / "t3.fda", scan_mm=3.0)  # cubo de 3 mm, capas MULTILAYERS (identificadas)
    cd = TopconFdaAdapter().extract(p3)
    assert cd.cmt_um is not None and cd.etdrs_grid.S1 is not None
    assert cd.etdrs_grid.S2 is None and cd.etdrs_grid.T2 is None  # adaptador nuevo: política STRICT
    assert any(n.startswith("ETDRS no disponible por política 'strict'") for n in cd.confidence_notes)
    cd6 = TopconFdaAdapter().extract(build_fda(tmp_path / "t6.fda"))
    assert cd6.etdrs_grid.S2 is not None and not any("ETDRS no disponible" in n for n in cd6.confidence_notes)
    # extractor legado (política por defecto None): mismo comportamiento histórico
    from transducin.readers.fda_reader import FDAReader

    vol = FDAReader(p3).read_layer_volumes()[0]
    legacy = measure_fda_volume(
        vol.contours, vol.n_bscans, vol.n_ascans, vol.pixel_spacing, "R", {"noel_id": "", "vendor": "x"}
    )
    assert legacy.etdrs_grid.S2 is not None  # sin política no se anula nada
