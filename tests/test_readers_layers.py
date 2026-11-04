"""Readers vendorizados + adapters D1/D2 con archivos SINTÉTICOS (sin PHI).

Los .e2e/.fda se generan en tmp_path con tests/_synthetic.py; nunca se abren
archivos reales (política de privacidad RetinaOS §2).
"""

from __future__ import annotations

import numpy as np
import pytest

from tests._synthetic import build_e2e, build_fda, thickness_profile
from transducin.adapters.spectralis import SpectralisE2EAdapter
from transducin.adapters.topcon import TopconFdaAdapter
from transducin.readers.e2e_reader import E2EReader
from transducin.readers.fda_reader import FDAReader

# ── E2E ───────────────────────────────────────────────────────────────────────


def test_e2e_layer_volumes(tmp_path):
    vols = E2EReader(build_e2e(tmp_path / "s.e2e")).read_layer_volumes()
    assert len(vols) == 1
    v = vols[0]
    assert v.laterality == "R"
    assert v.n_bscans == 25 and v.n_ascans == 256
    assert set(v.contours) == {"contour0", "contour1", "contour3", "contour9"}
    assert len(v.contours["contour0"]) == 25
    assert v.sqi_mean == pytest.approx(0.25, abs=1e-4)
    assert v.pixel_spacing[1] == pytest.approx(0.00387, rel=1e-4)
    assert v.acquisition_date is not None


def test_e2e_thickness_map_values(tmp_path):
    maps = E2EReader(build_e2e(tmp_path / "s.e2e")).read_thickness_map()
    assert len(maps) == 1
    m = maps[0]
    assert m.laterality == "R" and m.study_type == "macular"
    assert m.cmt_um == pytest.approx(300.0, abs=1.0)
    assert set(m.etdrs_sectors) == {"C", "S1", "N1", "I1", "T1", "S2", "N2", "I2", "T2"}
    for v in m.etdrs_sectors.values():
        assert v == pytest.approx(300.0, abs=1.5)
    assert m.rnfl_sectors["G"] == pytest.approx(40.0, abs=1.0)
    assert m.gcl_ipl_sectors["G"] == pytest.approx(60.0, abs=1.0)
    assert m.gcl_avg_um == pytest.approx(60.0, abs=1.0)
    assert m.sqi_mean == pytest.approx(0.25, abs=1e-4)
    assert m.scan_date is not None


def test_e2e_adapter_end_to_end(tmp_path):
    cd = SpectralisE2EAdapter().extract(build_e2e(tmp_path / "s.e2e", laterality="L"))
    assert cd.extraction_confidence == "confirmed"
    assert cd.laterality == "L"
    assert cd.cmt_um == pytest.approx(300.0, abs=1.0)
    assert cd.etdrs_grid.C == pytest.approx(300.0, abs=1.5)
    assert cd.rnfl.global_avg == pytest.approx(40.0, abs=1.0)
    assert cd.gcl_ipl.global_avg == pytest.approx(60.0, abs=1.0)
    assert cd.gcl_avg_um == pytest.approx(60.0, abs=1.0)
    assert cd.sqi_mean == pytest.approx(0.25, abs=1e-4)
    assert cd.study_type == "macular"
    assert cd.study_date == "20231114" and len(cd.study_time) == 6
    assert cd.noel_id == "" and cd.patient_name == "" and cd.patient_dob == ""


def test_e2e_laterality_swaps_nasal_temporal(tmp_path):
    """El espesor varía a lo largo de las A-scans: R y L deben intercambiar N y T."""
    prof = thickness_profile(25, 256, 250.0, grad_um=120.0)
    r = SpectralisE2EAdapter().extract(build_e2e(tmp_path / "r.e2e", laterality="R", thick_um=prof))
    l = SpectralisE2EAdapter().extract(build_e2e(tmp_path / "l.e2e", laterality="L", thick_um=prof))
    assert r.etdrs_grid.N1 != pytest.approx(r.etdrs_grid.T1, abs=1.0)  # el gradiente se nota
    assert r.etdrs_grid.N1 == pytest.approx(l.etdrs_grid.T1, abs=0.5)
    assert r.etdrs_grid.T1 == pytest.approx(l.etdrs_grid.N1, abs=0.5)
    assert r.cmt_um == pytest.approx(l.cmt_um, abs=0.5)


def test_e2e_unknown_laterality_does_not_assume_right(tmp_path):
    """Sin lateralidad no se miden sectores (no se asume ojo derecho)."""
    cd = SpectralisE2EAdapter().extract(build_e2e(tmp_path / "s.e2e", laterality=None))
    assert cd.cmt_um is None and cd.etdrs_grid is None
    assert cd.laterality == "" and cd.extraction_confidence == "unknown"


def test_e2e_without_contours_degrades(tmp_path):
    p = build_e2e(tmp_path / "s.e2e", with_contours=False)
    assert E2EReader(p).read_thickness_map() == []
    cd = SpectralisE2EAdapter().extract(p)
    assert cd.cmt_um is None and cd.laterality == "R"
    assert cd.extraction_confidence == "unknown"


def test_e2e_parity_with_legacy_extractor(tmp_path):
    """Mismo resultado que spectralis_extractor (oct_converter completo)."""
    pytest.importorskip("oct_converter")
    from transducin.spectralis_extractor import extract_from_e2e

    prof = thickness_profile(25, 256, 250.0, grad_um=120.0)
    p = build_e2e(tmp_path / "s.e2e", laterality="L", thick_um=prof, with_image=True)
    try:
        legacy = extract_from_e2e(p)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"oct_converter no pudo leer el archivo sintético: {exc}")
    if not legacy:
        pytest.skip("oct_converter no produjo volúmenes con el archivo sintético mínimo")
    new = SpectralisE2EAdapter().extract(p)
    ref = legacy[0]
    assert new.cmt_um == pytest.approx(ref.cmt_um, abs=0.05)
    assert new.etdrs_grid == ref.etdrs_grid
    assert new.rnfl == ref.rnfl
    assert new.gcl_ipl == ref.gcl_ipl
    assert new.laterality == ref.laterality


# ── FDA ───────────────────────────────────────────────────────────────────────


def test_fda_chunk_listing_works_with_numpy2(tmp_path):
    """Regresión: np.fromstring(binario) fue eliminado en numpy>=2.3."""
    r = FDAReader(build_fda(tmp_path / "t.fda"))
    assert isinstance(r.chunk_dict[b"@CONTOUR_INFO"][0], list)  # 4 capas duplicadas
    assert r.read_oct_volume()[0].laterality == "R"


def test_fda_layer_volumes(tmp_path):
    vols = FDAReader(build_fda(tmp_path / "t.fda")).read_layer_volumes()
    assert len(vols) == 1
    v = vols[0]
    assert (v.n_bscans, v.n_ascans) == (25, 256)
    assert set(v.contours) == {"ILM", "RNFL_GCL", "IPL_INL", "BM"}
    assert v.contours["ILM"].shape == (25, 256)
    assert np.allclose(v.contours["ILM"], 100.0)
    assert np.allclose(v.contours["BM"], 200.0)
    assert v.pixel_spacing == pytest.approx([6.0 / 256, 6.0 / 25, 0.0026])
    assert v.sqi_mean == pytest.approx(0.85)
    assert v.acquisition_date.year == 2025


def test_fda_thickness_map_values(tmp_path):
    m = FDAReader(build_fda(tmp_path / "t.fda")).read_thickness_map()[0]
    assert m.cmt_um == pytest.approx(260.0, abs=1.0)  # 100 px × 2.6 µm
    assert m.rnfl_sectors["G"] == pytest.approx(39.0, abs=1.0)  # 15 px
    assert m.gcl_ipl_sectors["G"] == pytest.approx(59.8, abs=1.0)  # 23 px
    assert m.sqi_mean == pytest.approx(0.85)
    assert m.study_type == "macular" and m.laterality == "R"


@pytest.mark.parametrize("eye,lat", [(0, "R"), (1, "L")])
def test_fda_adapter_end_to_end(tmp_path, eye, lat):
    cd = TopconFdaAdapter().extract(build_fda(tmp_path / "t.fda", eye=eye))
    assert cd.laterality == lat
    assert cd.extraction_confidence == "confirmed"
    assert cd.cmt_um == pytest.approx(260.0, abs=1.0)
    assert cd.gcl_avg_um == pytest.approx(59.8, abs=1.0)
    assert cd.vendor == "topcon_dri_oct"
    assert cd.study_date == "20250314"
    assert cd.noel_id == "" and cd.patient_name == ""


def test_fda_laterality_swaps_nasal_temporal(tmp_path):
    prof = np.tile(np.linspace(80.0, 140.0, 256), (25, 1))
    r = TopconFdaAdapter().extract(build_fda(tmp_path / "r.fda", eye=0, thick_px=prof))
    l = TopconFdaAdapter().extract(build_fda(tmp_path / "l.fda", eye=1, thick_px=prof))
    assert r.etdrs_grid.N1 != pytest.approx(r.etdrs_grid.T1, abs=1.0)
    assert r.etdrs_grid.N1 == pytest.approx(l.etdrs_grid.T1, abs=0.5)
    assert r.etdrs_grid.T1 == pytest.approx(l.etdrs_grid.N1, abs=0.5)


def test_fda_unknown_laterality_does_not_assume_right(tmp_path):
    cd = TopconFdaAdapter().extract(build_fda(tmp_path / "t.fda", eye=None))
    assert cd.cmt_um is None and cd.laterality == ""


def test_fda_absent_segmentation_is_nan_not_bottom(tmp_path):
    """Capa con valor crudo 0 = ausente → NaN (no 'altura de imagen')."""
    v = FDAReader(build_fda(tmp_path / "t.fda", zero_layers=("MULTILAYERS_7",))).read_layer_volumes()[0]
    assert np.isnan(v.contours["BM"]).all()
    cd = TopconFdaAdapter().extract(build_fda(tmp_path / "t.fda", zero_layers=("MULTILAYERS_7",)))
    assert cd.cmt_um is None


def test_fda_without_contours_degrades(tmp_path):
    p = build_fda(tmp_path / "t.fda", with_contours=False)
    assert FDAReader(p).read_thickness_map() == []
    cd = TopconFdaAdapter().extract(p)
    assert cd.cmt_um is None and cd.laterality == "R"
