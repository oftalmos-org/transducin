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


# ── Procedencia y contornos sin identificar ───────────────────────────────────

_RETINA_IDS = {
    "MULTILAYERS_1": "RETINA_1",
    "MULTILAYERS_2": "RETINA_2",
    "MULTILAYERS_4": "RETINA_3",
    "MULTILAYERS_7": "RETINA_4",
}


def test_provenance_is_vendor_contour_derived_not_vendor_reported(tmp_path):
    e2e = SpectralisE2EAdapter().extract(build_e2e(tmp_path / "s.e2e"))
    fda = TopconFdaAdapter().extract(build_fda(tmp_path / "t.fda"))
    assert e2e.measurement_provenance == "vendor_contour_derived"
    assert fda.measurement_provenance == "vendor_contour_derived"
    assert e2e.extraction_confidence == "confirmed"  # campo independiente, sin cambios


def test_provenance_empty_when_nothing_measured(tmp_path):
    cd = TopconFdaAdapter().extract(build_fda(tmp_path / "t.fda", with_contours=False))
    assert cd.measurement_provenance == ""


def test_fda_unidentified_contours_are_kept_but_not_measured(tmp_path):
    """RETINA_1..4 (3D OCT-1000 Mark II): sin identidad anatómica → sin espesores, con aviso."""
    p = build_fda(tmp_path / "t.fda", layer_ids=_RETINA_IDS)
    r = FDAReader(p)
    vol = r.read_layer_volumes()[0]
    assert set(vol.contours) == set(_RETINA_IDS.values())  # se conservan con su nombre original
    assert r.read_thickness_map() == []
    assert r.unidentified_contours == sorted(_RETINA_IDS.values())
    cd = TopconFdaAdapter().extract(p)
    assert cd.cmt_um is None and cd.etdrs_grid is None
    assert cd.measurement_provenance == ""
    assert any(n.startswith("UNIDENTIFIED") for n in cd.confidence_notes)


# ── Espesor ILM→BM experimental (solo Topcon 3D OCT-1000 Mark II) ─────────────

from transducin.topcon_experimental import ExperimentalUnavailable, thickness_from_contours
from transducin.topcon_retina_map import HYPOTHESIS_RETINA_MAP


def _mk2_fda(tmp_path, **kw):
    opts = {"layer_ids": _RETINA_IDS, "model_name": "3DOCT-1000MK2", "axial_um": 3.5}
    opts.update(kw)
    return build_fda(tmp_path / "t.fda", **opts)


def test_experimental_is_disabled_by_default(tmp_path):
    with pytest.raises(RuntimeError, match="deshabilitado"):
        TopconFdaAdapter().experimental_thickness(_mk2_fda(tmp_path))


def test_experimental_thickness_values_provenance_and_no_clinical_output(tmp_path):
    p = _mk2_fda(tmp_path)
    res = TopconFdaAdapter(experimental=True).experimental_thickness(p)
    assert np.allclose(res.thickness_um, 100 * 3.5)  # (RETINA_4 − RETINA_1) · 3.5 µm/px, sin tocar los contornos
    assert res.stats["valid_fraction"] == 1.0 and res.flags["invalid_positions_total"] == 0
    assert res.axial_scale["verified"] is True and res.axial_scale["value_um_per_px"] == pytest.approx(3.5)
    assert res.measurement_provenance == "vendor_contour_derived"
    assert res.status == "experimental" and res.publishable_as_clinical is False
    assert res.identity["binary_correspondence_confirmed"] is False
    assert "revisión visual" in res.identity["anatomical_identity_source"]
    # no alimenta la ruta clínica: extract() sigue sin espesores ni procedencia
    cd = TopconFdaAdapter(experimental=True).extract(p)
    assert cd.cmt_um is None and cd.etdrs_grid is None and cd.measurement_provenance == ""
    assert "sectores S/N/I/T" in res.stats["sector_labels"]


def test_experimental_refuses_other_models_scales_and_missing_contours(tmp_path):
    ad = TopconFdaAdapter(experimental=True)
    with pytest.raises(ExperimentalUnavailable, match="modelo"):
        ad.experimental_thickness(_mk2_fda(tmp_path, model_name="3DOCT-2000"))
    with pytest.raises(ExperimentalUnavailable, match="escala axial"):
        ad.experimental_thickness(_mk2_fda(tmp_path, axial_um=2.6))
    with pytest.raises(ExperimentalUnavailable, match="@CONTOUR_INFO"):
        ad.experimental_thickness(_mk2_fda(tmp_path, with_contours=False))
    with pytest.raises(ExperimentalUnavailable, match="faltan"):  # contornos MULTILAYERS_n, no RETINA_n
        ad.experimental_thickness(_mk2_fda(tmp_path, layer_ids=None))
    with pytest.raises(ExperimentalUnavailable, match="modelo"):  # archivo sin @HW_INFO_03
        ad.experimental_thickness(build_fda(tmp_path / "x.fda", layer_ids=_RETINA_IDS, axial_um=3.5))


def test_thickness_from_contours_marks_invalid_regions():
    ilm = np.full((4, 6), 100.0)
    bm = np.full((4, 6), 200.0)
    bm[0, 0] = np.nan  # contorno ausente
    bm[1, 1] = 90.0  # BM por encima de la ILM → geometría inválida
    bm[2, 2] = 105.0  # 17.5 µm: fuera del rango plausible
    bm[3, 3] = 100 + 400  # 1400 µm: fuera del rango plausible
    t, counts = thickness_from_contours(ilm, bm, 3.5)
    assert counts == {"contour_missing": 1, "geometry_invalid": 1, "out_of_plausible_range": 2}
    assert np.isnan(t[0, 0]) and np.isnan(t[1, 1]) and np.isnan(t[2, 2]) and np.isnan(t[3, 3])
    assert t[0, 1] == pytest.approx(350.0)
    assert np.isfinite(t).sum() == 24 - 4


def test_hypothesis_map_is_versioned_and_not_used_by_production_reader(tmp_path):
    assert HYPOTHESIS_RETINA_MAP["status"] == "hypothesis"
    assert HYPOTHESIS_RETINA_MAP["binary_correspondence_confirmed"] is False
    assert HYPOTHESIS_RETINA_MAP["used_for_thickness"] == ("RETINA_1", "RETINA_4")
    with pytest.raises(TypeError):
        HYPOTHESIS_RETINA_MAP["status"] = "confirmed"  # inmutable
    vol = FDAReader(_mk2_fda(tmp_path)).read_layer_volumes()[0]
    assert set(vol.contours) == set(_RETINA_IDS.values())  # el lector sigue sin renombrar a ILM/BM


# ── boct_converter: correcciones propias sobre oct-converter ──────────────────


def test_boct_laterality_filename_is_strict_unlike_oct_converter():
    pytest.importorskip("oct_converter")
    from transducin.boct_converter import laterality_from_filename as lat

    assert lat("Z506_OS_V_1x1.OCT") == "L" and lat("scan-OD.OCT") == "R"
    # oct-converter daba R, L y L para estos tres: aquí no hay token OD/OS aislado
    assert lat("MODEL_OS_scan.OCT") == "L"  # el "OD" de MODEL ya no cuenta; el token real es OS
    assert lat("ROSE_OD_scan.OCT") == "R"  # el "OS" de ROSE ya no cuenta
    assert lat("SUBJ_POSITION_OD.OCT") == "R"
    assert lat("scan_left_eye.OCT") == "" and lat("OS_OD.OCT") == ""  # ausente o ambigua


def test_boct_annotate_flags_placeholder_geometry_and_accepts_explicit_values(tmp_path):
    pytest.importorskip("oct_converter")
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian

    from transducin.boct_converter import annotate_boct_dicom

    def make(name):
        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.preamble = b"\x00" * 128  # como un DICOM real (create_dicom_from_boct)
        ds.ImageLaterality = "R"
        ds.Laterality = "R"
        pm = Dataset()
        pm.PixelSpacing = ["0.02", "0.02"]
        pm.SliceThickness = "0.2"
        sfg = Dataset()
        sfg.PixelMeasuresSequence = [pm]
        ds.SharedFunctionalGroupsSequence = [sfg]
        p = tmp_path / name
        pydicom.dcmwrite(p, ds, enforce_file_format=False)
        return p

    p1 = make("MODEL_OS_scan_0.dcm")
    notes = annotate_boct_dicom(p1, source_name="MODEL_OS_scan.OCT")
    ds = pydicom.dcmread(p1)
    assert ds.ImageLaterality == "L" and ds.Laterality == "L"  # corrige el R que dejó oct-converter
    assert any("SIN CALIBRAR" in n for n in notes) and "SIN CALIBRAR" in ds.ImageComments
    assert ds.SharedFunctionalGroupsSequence[0].PixelMeasuresSequence[0].PixelSpacing == [
        "0.02",
        "0.02",
    ]  # no se inventa otro valor

    p2 = make("scan_left_eye_0.dcm")
    annotate_boct_dicom(
        p2, source_name="scan_left_eye.OCT", laterality="R", pixel_spacing_mm=(0.0039, 0.0059), slice_thickness_mm=0.012
    )
    ds2 = pydicom.dcmread(p2)
    assert ds2.ImageLaterality == "R" and "explícitamente" in ds2.ImageComments
    item = ds2.SharedFunctionalGroupsSequence[0].PixelMeasuresSequence[0]
    assert [float(x) for x in item.PixelSpacing] == [0.0039, 0.0059] and float(item.SliceThickness) == 0.012
    assert "SIN CALIBRAR" not in ds2.ImageComments

    p3 = make("sin_lateralidad_0.dcm")
    annotate_boct_dicom(p3, source_name="sin_lateralidad.OCT")
    assert pydicom.dcmread(p3).ImageLaterality == ""  # desconocida → vacía, no asumida
