"""Buscador de SLO y mapa de grosor en color (ADR-028), con datos SINTÉTICOS (sin PHI)."""

from __future__ import annotations

import numpy as np
import pydicom

from tests._synthetic import build_e2e, thickness_profile
from transducin.adapters.spectralis import SpectralisE2EAdapter
from transducin.clinical_data import OCTClinicalData
from transducin.slo_finder import build_finder, decode_finder, thickness_colormap


def _raster(n_b: int = 25, span: float = 10.0) -> list[dict]:
    """Trama regular: corte 0 abajo (y=+span), último arriba; A-scan crece en x."""
    ys = np.linspace(span, -span, n_b)
    return [{"posX1": -span, "posX2": span, "posY1": float(y), "posY2": float(y), "scaley": 0.00387} for y in ys]


def test_colormap_anchors_and_range():
    c = thickness_colormap(np.array([0, 100, 320, 800, 5000]))
    assert tuple(c[0]) == (0, 0, 0) and tuple(c[1]) == (128, 128, 128)
    assert tuple(c[2]) == (0, 200, 60)  # verde ~320 µm
    assert tuple(c[3]) == tuple(c[4]) == (255, 255, 255)  # satura en 800


def test_finder_lines_follow_geometry_and_map_is_inside_scan_area():
    fundus = np.full((64, 64), 120, dtype=np.uint8)
    t = np.full((25, 128), 300.0)
    f = build_finder(fundus, _raster(), 25, t, out_px=64)
    assert f is not None and f["size"] == [64, 64] and len(f["lines"]) == 25
    # 30° ↔ 64 px (2.13 px/°); trama de ±10°: x de 10.67 a 53.33; el corte 0 (y=+10°) queda abajo
    x1, y1, x2, y2 = f["lines"][0]
    assert abs(x1 - 10.67) < 0.1 and abs(x2 - 53.33) < 0.1 and y1 > 32 and y1 == y2
    assert f["lines"][-1][1] < 32  # el último (superior) queda arriba
    assert "thickness_png_b64" in f


def test_no_map_for_radial_or_inconsistent_lines():
    fundus = np.zeros((64, 64), dtype=np.uint8)
    radial = [
        {
            "posX1": -5.0 * np.cos(a),
            "posX2": 5.0 * np.cos(a),
            "posY1": -5.0 * np.sin(a),
            "posY2": 5.0 * np.sin(a),
            "scaley": 0.004,
        }
        for a in np.linspace(0, np.pi, 24, endpoint=False)
    ]
    f = build_finder(fundus, radial, 24, np.full((24, 64), 250.0), out_px=64)
    assert f is not None and "thickness_png_b64" not in f  # las líneas radiales no forman una trama
    assert build_finder(None, radial, 24) is None  # sin SLO no hay buscador
    assert build_finder(fundus, radial, 23) is None  # cantidad de cortes inconsistente


def test_to_dicom_embeds_finder_and_layers(tmp_path):
    raw = np.random.default_rng(8).integers(0, 65536, size=(25, 8, 256), dtype=np.uint32).astype(np.uint16)
    fundus = np.random.default_rng(9).integers(0, 255, size=(32, 32), dtype=np.uint8)
    src = build_e2e(
        tmp_path / "f.e2e",
        laterality="R",
        thick_um=thickness_profile(25, 256, 250.0, grad_um=120.0),
        image_raw=raw,
        fundus=fundus,
        scan_mm=6.0,
    )
    cd = OCTClinicalData(noel_id="", vendor="heidelberg_spectralis", laterality="R", study_type="macular")
    ds = pydicom.dcmread(SpectralisE2EAdapter().to_dicom(src, cd, tmp_path / "o")[0])
    fd = decode_finder(ds)
    assert fd is not None and len(fd["lines"]) == 25 and fd["field_deg"] == 30.0 and fd["slo_jpeg_b64"]


def test_gray_thickness_png_encodes_um_and_mask():
    import base64
    import io

    from PIL import Image

    fundus = np.full((64, 64), 120, dtype=np.uint8)
    f = build_finder(fundus, _raster(), 25, np.full((25, 128), 400.0), out_px=64)
    img = Image.open(io.BytesIO(base64.b64decode(f["thickness_gray_png_b64"])))
    assert img.mode == "LA" and f["thickness_range_um"] == [0, 800]
    a = np.asarray(img)
    inside = a[..., 1] == 255
    assert inside.any() and not inside.all()
    assert abs(float(a[..., 0][inside].mean()) * 800 / 255 - 400) < 4  # µm recuperables
