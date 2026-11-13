"""Regresión con los ejemplos públicos REALES de UK Biobank (Topcon 3D OCT-1000 Mark II).

Se omite si no existe $TRANSDUCIN_REAL_DATA_DIR/public/topcon/{fda,fds}/. No se imprime
ni se afirma nada sobre identidad anatómica: RETINA_1..4 son contornos sin identificar.
"""

from __future__ import annotations

import io
import itertools
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from transducin.adapters.topcon import TopconFdaAdapter
from transducin.readers import _fda_binary as fb
from transducin.readers.fda_reader import FDAReader

pytestmark = pytest.mark.real_data

_ROOT = os.environ.get("TRANSDUCIN_REAL_DATA_DIR")
FDA = Path(_ROOT, "public/topcon/fda/eg_oct_fda.fda") if _ROOT else None
FDS = Path(_ROOT, "public/topcon/fds/eg_oct_fds.fds") if _ROOT else None

needs_fda = pytest.mark.skipif(not (FDA and FDA.exists()), reason="falta el .fda real (TRANSDUCIN_REAL_DATA_DIR)")
needs_fds = pytest.mark.skipif(not (FDS and FDS.exists()), reason="falta el .fds real (TRANSDUCIN_REAL_DATA_DIR)")


@needs_fda
def test_real_fda_structure_and_contours():
    r = FDAReader(FDA)
    assert r.read_oct_volume()[0].laterality == "L"
    vol = r.read_layer_volumes()[0]
    assert (vol.n_bscans, vol.n_ascans) == (128, 512)
    assert sorted(vol.contours) == ["RETINA_1", "RETINA_2", "RETINA_3", "RETINA_4"]
    ys = [vol.contours[f"RETINA_{i}"] for i in range(1, 5)]
    for y in ys:
        assert y.shape == (128, 512) and not np.isnan(y).any()
    for a, b in itertools.pairwise(ys):  # cada contorno queda por debajo del anterior (≥98 %)
        assert ((b - a) > 0).mean() >= 0.98
    # PixelSpacing leído del archivo (no inventado): 6 mm / 512 A-scans, 3.5 µm axial
    assert vol.pixel_spacing[0] == pytest.approx(6.0 / 512, rel=0.02)
    assert vol.pixel_spacing[2] == pytest.approx(0.0035)


@needs_fda
def test_real_fda_does_not_assign_anatomy():
    r = FDAReader(FDA)
    assert r.read_thickness_map() == []
    assert r.unidentified_contours == ["RETINA_1", "RETINA_2", "RETINA_3", "RETINA_4"]
    cd = TopconFdaAdapter().extract(FDA)
    assert cd.cmt_um is None and cd.etdrs_grid is None and cd.measurement_provenance == ""
    assert any(n.startswith("UNIDENTIFIED") for n in cd.confidence_notes)
    assert cd.laterality == "L"


@needs_fda
def test_real_fda_reader_orientation_matches_image_edges():
    """El orden de B-scans + 'H - v' del lector deja los contornos sobre bordes de la imagen
    más que el orden crudo (evidencia en validation/topcon_3doct1000mk2_contours.md)."""
    from PIL import Image

    r = FDAReader(FDA)
    loc, _ = r.chunk_dict[b"@IMG_JPEG"]
    imgs = []
    with open(FDA, "rb") as f:
        f.seek(loc)
        f.read(25)
        for _ in range(128):
            n = struct.unpack("<i", f.read(4))[0]
            imgs.append(np.asarray(Image.open(io.BytesIO(f.read(n))).convert("L"), dtype=np.float32))
        raw = {}
        for pos, size in r._chunk_positions(b"@CONTOUR_INFO"):
            f.seek(pos)
            h = fb.contour_info_header.parse(f.read(34))
            n = h.width * h.height
            raw[h.id] = np.frombuffer(f.read(n * 2), dtype="<u2").reshape(h.height, h.width).astype(np.float32)
    imgs = np.stack(imgs)
    H, cols, s = imgs.shape[1], np.arange(40, 470), np.arange(128)[:, None]

    def edge(order_flip: bool) -> float:
        tot = 0.0
        for v in raw.values():
            y = np.clip(np.rint(H - (v[::-1] if order_flip else v)).astype(int), 4, H - 5)[:, cols]
            tot += float(np.mean(np.abs(imgs[s, y + 4, cols[None, :]] - imgs[s, y - 4, cols[None, :]])))
        return tot

    assert edge(True) > edge(False) * 1.1


@needs_fds
def test_real_fds_has_no_segmentation_and_no_measurements():
    r = FDAReader(FDS)
    assert r.read_oct_volume()[0].laterality == "L"
    assert r.read_layer_volumes() == []
    cd = TopconFdaAdapter().extract(FDS)
    assert cd.cmt_um is None and cd.measurement_provenance == ""


@needs_fda
def test_real_fda_contour_viewer_builds_and_keeps_native_data(tmp_path):
    """El visor HTML se genera con los contornos nativos y las interpretaciones por separado."""
    import importlib.util
    import json
    import re

    spec = importlib.util.spec_from_file_location(
        "topcon_contour_viewer", Path(__file__).resolve().parents[1] / "scripts" / "topcon_contour_viewer.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "viewer.html"
    mod.build(FDA, out)
    html = out.read_text(encoding="utf-8")
    data = json.loads(
        re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.DOTALL)
        .group(1)
        .replace("<\\/", "</")
    )
    meta = data["meta"]
    assert meta["native_labels"] == ["RETINA_1", "RETINA_2", "RETINA_3", "RETINA_4"]
    assert len(data["images"]) == 128 and len(data["contours"]) == 4
    # coordenadas nativas íntegras: valores crudos uint16, sin transformar
    vol = FDAReader(FDA).read_layer_volumes()[0]
    raw1 = np.array(data["contours"][0])
    assert np.array_equal(650 - raw1[::-1], vol.contours["RETINA_1"])
    # interpretaciones por defecto: solo hipótesis provisionales en los extremos
    defaults = meta["default_interpretations"]
    assert set(defaults) == {"RETINA_1", "RETINA_4"}
    assert all(v["status"] == "hipotesis_provisional" for v in defaults.values())


@needs_fda
def test_real_fda_experimental_thickness_exploratory_stats():
    from transducin.adapters.topcon import TopconFdaAdapter

    res = TopconFdaAdapter(experimental=True).experimental_thickness(FDA)
    assert res.axial_scale["verified"] and res.axial_scale["value_um_per_px"] == pytest.approx(3.5)
    assert res.stats["valid_fraction"] == 1.0 and res.flags["invalid_positions_total"] == 0
    assert res.flags["incomplete_bscans_below_90pct_valid"] == []
    assert res.flags["intermediate_contour_order_violation_fraction"] == 0.0
    assert res.stats["median_um"] == pytest.approx(297.5, abs=1.0)
    assert res.stats["central_disc_r500um"]["mean_um"] == pytest.approx(287.0, abs=1.0)
    assert res.stats["central_disc_r500um"]["mean_um"] < res.stats["ring_500_1500um"]["mean_um"]  # depresión central
    assert res.publishable_as_clinical is False
