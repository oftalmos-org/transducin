"""Mapeo de IDs de capa E2E → capas anatómicas, con contornos SINTÉTICOS (sin PHI).

Observación en archivos reales de un Spectralis (2026-11-11, solo estructura): los
volúmenes traen los contornos 0..6, 8, 14, 15 y 16 y NO traen el 9; el más profundo
es el 1. Es decir, ILM=0 y BM=1, no BM=9 (mapeo previo, de la documentación de
oct-converter). El mapa se elige validando el orden geométrico de las capas.
"""

from __future__ import annotations

import numpy as np

from transducin.spectralis_extractor import _resolve_e2e_layers, measure_e2e_volume

N_B, N_A = 61, 768
AXIAL_MM = 0.0038716699928045273  # mm/px, como en los archivos reales
PS = [0.01, AXIAL_MM, 0.05]
CD_BASE = {"noel_id": "", "vendor": "heidelberg_spectralis", "source_file": "", "study_date": "", "study_time": ""}


def _contour(depth_px: float) -> list:
    return [np.full(N_A, depth_px, dtype=np.float32) for _ in range(N_B)]


def _contours(depth_by_id: dict[int, float]) -> dict:
    return {f"contour{i}": _contour(d) for i, d in depth_by_id.items()}


# Profundidades (px) observadas en el volumen macular real de 61 cortes.
HEYEX = {
    0: 152.5,
    2: 166.7,
    3: 175.4,
    4: 182.3,
    5: 190.2,
    6: 197.1,
    8: 211.1,
    14: 217.1,
    15: 222.0,
    16: 228.0,
    1: 231.2,
}
# Numeración contigua de oct-converter (ILM, RNFL/GCL, GCL/IPL, IPL/INL, ..., BM=9).
CONTIGUOUS = {0: 152.5, 1: 166.7, 2: 170.0, 3: 175.4, 4: 182.3, 5: 190.2, 6: 197.1, 7: 205.0, 8: 211.1, 9: 231.2}


def test_resolve_heyex_ids_without_contour9():
    layers, name = _resolve_e2e_layers(_contours(HEYEX))
    assert name == "heyex_ids"
    assert layers["bm"] is not None and layers["ilm"] is not None
    # BM es la capa más profunda (contorno 1), ILM la más superficial (contorno 0)
    assert np.nanmean(layers["bm"]) > np.nanmean(layers["ipl_inl"]) > np.nanmean(layers["rnfl_gcl"])
    assert np.nanmean(layers["rnfl_gcl"]) > np.nanmean(layers["ilm"])
    assert np.isclose(np.nanmean(layers["bm"]), 231.2)
    assert np.isclose(np.nanmean(layers["rnfl_gcl"]), 166.7)  # contorno 2, no el 1


def test_resolve_contiguous_ids_unchanged():
    layers, name = _resolve_e2e_layers(_contours(CONTIGUOUS))
    assert name == "oct_converter_contiguous"
    assert np.isclose(np.nanmean(layers["bm"]), 231.2)  # contorno 9
    assert np.isclose(np.nanmean(layers["rnfl_gcl"]), 166.7)  # contorno 1
    assert np.isclose(np.nanmean(layers["ipl_inl"]), 175.4)  # contorno 3


def test_resolve_rejects_inconsistent_geometry():
    # BM más superficial que ILM: ningún mapa es válido → no medir.
    bad = _contours({0: 230.0, 1: 150.0, 2: 160.0, 3: 170.0, 4: 180.0})
    layers, name = _resolve_e2e_layers(bad)
    assert layers is None and name is None


def test_resolve_needs_ilm_and_bm():
    layers, name = _resolve_e2e_layers(_contours({0: 150.0, 2: 160.0}))
    assert layers is None and name is None


def test_measure_with_heyex_ids_gives_central_thickness_and_flags_assumption():
    cd = measure_e2e_volume(_contours(HEYEX), PS, {"bscan_data": []}, "R", dict(CD_BASE), label="sint")
    assert cd is not None
    # (231.2 - 152.5) px * 3.8717 µm/px ≈ 304.7 µm
    assert cd.cmt_um is not None and abs(cd.cmt_um - 304.7) < 1.0
    assert cd.extraction_confidence == "assumed"
    assert any("heyex_ids" in n for n in cd.confidence_notes)


def test_measure_with_contiguous_ids_keeps_confirmed():
    cd = measure_e2e_volume(_contours(CONTIGUOUS), PS, {"bscan_data": []}, "R", dict(CD_BASE), label="sint")
    assert cd is not None
    assert cd.cmt_um is not None and abs(cd.cmt_um - 304.7) < 1.0
    assert cd.extraction_confidence == "confirmed"


def test_orientation_follows_slo_not_slice_order():
    """Superior = y menor; columnas en x decreciente. Corte 0 inferior (y>0) pasa al final."""
    from transducin.spectralis_extractor import _orient_to_etdrs_convention

    n_b, n_a = 3, 4
    arr = np.arange(n_b * n_a, dtype=np.float32).reshape(n_b, n_a)  # fila i = corte i
    meta = [  # corte 0 inferior (y=+5), corte 2 superior (y=-5); A-scan crece en x
        {"posX1": -10.0, "posX2": 10.0, "posY1": 5.0, "posY2": 5.0},
        {"posX1": -10.0, "posX2": 10.0, "posY1": 0.0, "posY2": 0.0},
        {"posX1": -10.0, "posX2": 10.0, "posY1": -5.0, "posY2": -5.0},
    ]
    out, note = _orient_to_etdrs_convention({"a": arr, "none": None}, meta, n_b)
    assert out["none"] is None and "normalizada" in note
    # fila 0 = el corte superior (antes 2), con columnas invertidas
    assert np.array_equal(out["a"][0], arr[2][::-1])
    assert np.array_equal(out["a"][2], arr[0][::-1])

    # un corte con A-scan decreciente en x ya está en el orden pedido: no se invierte
    meta[1] = {"posX1": 10.0, "posX2": -10.0, "posY1": 0.0, "posY2": 0.0}
    out2, _ = _orient_to_etdrs_convention({"a": arr}, meta, n_b)
    assert np.array_equal(out2["a"][1], arr[1])


def test_orientation_without_geometry_is_not_applied_and_says_so():
    from transducin.spectralis_extractor import _orient_to_etdrs_convention

    arr = np.zeros((3, 4), dtype=np.float32)
    out, note = _orient_to_etdrs_convention({"a": arr}, [], 3)
    assert out["a"] is arr and "NO verificable" in note
