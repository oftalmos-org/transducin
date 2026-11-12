"""Lectura de píxeles de B-scan y fondo de ojo en .e2e, con archivos SINTÉTICOS (sin PHI).

La decodificación (flotante propio de 16 bits + transformada logarítmica) se compara
con una referencia escrita aparte, valor por valor. Ver ADR-027.
"""

from __future__ import annotations

import math

import numpy as np

from tests._synthetic import build_e2e
from transducin.readers.e2e_reader import E2EReader, _ufloat16_lut


def _ref_ufloat16(i: int) -> float:
    """Referencia literal del formato (6 bits de exponente, 10 de mantisa invertida)."""
    bits = format(i, "016b")[::-1]
    mantissa = int(bits[:10], 2)
    exponent = int(bits[10:][::-1], 2)
    return (1 + mantissa / 1024) * 2.0 ** (exponent - 63)


def _ref_pixel(raw: int) -> int:
    v = _ref_ufloat16(raw)
    if v <= 1:
        v = (math.log(v + 2.44e-4) + 8.3) / 8.285
    v = min(max(v, 0.0), 1.0)
    return int(v * 255 + 0.5)


def test_lut_matches_reference_formula():
    lut = _ufloat16_lut()
    assert lut.shape == (65536,)
    rng = np.random.default_rng(0)
    samples = [0, 1, 2, 1023, 1024, 0x3FF0, 0x8000, 0xFFFF] + rng.integers(0, 65536, 400).tolist()
    for i in samples:
        assert math.isclose(lut[i], _ref_ufloat16(i), rel_tol=1e-12), i


def test_read_bscans_decodes_pixels_like_reference(tmp_path):
    rng = np.random.default_rng(1)
    raw = rng.integers(0, 65536, size=(3, 8, 16), dtype=np.uint32).astype(np.uint16)
    path = build_e2e(tmp_path / "s.e2e", n_b=3, image_raw=raw)

    vols = E2EReader(path).read_bscans()
    assert len(vols) == 1
    v = vols[0]
    assert v.laterality == "R"
    assert len(v.images) == 3
    for k in range(3):
        img = v.images[k]
        assert img.dtype == np.uint8 and img.shape == (8, 16)
        expected = np.array([[_ref_pixel(int(x)) for x in row] for row in raw[k]], dtype=np.uint8)
        assert np.array_equal(img, expected)


def test_read_bscans_fundus_is_separate_and_uint8(tmp_path):
    raw = np.full((2, 4, 4), 20000, dtype=np.uint16)
    fundus = (np.arange(36, dtype=np.uint8).reshape(6, 6) * 3).astype(np.uint8)
    path = build_e2e(tmp_path / "f.e2e", n_b=2, image_raw=raw, fundus=fundus)

    v = E2EReader(path).read_bscans()[0]
    assert len(v.images) == 2  # el fondo no se mezcla con los cortes
    assert v.fundus is not None and np.array_equal(v.fundus, fundus)


def test_read_bscans_empty_without_images(tmp_path):
    path = build_e2e(tmp_path / "n.e2e", n_b=3)
    assert E2EReader(path).read_bscans() == []
