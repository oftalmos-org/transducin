"""Revo: un escaneo ancho que incluye el disco no calcula CMT/ETDRS (el centro del volumen no es la fóvea)."""

from __future__ import annotations

import struct

import numpy as np

from tests import generate_synthetic_opt as g
from transducin.revo_opt_reader import read_opt

N_B, N_A = 64, 128


def _octparams(width_mm: float) -> bytes:
    recs = [
        g._octparam(3, 0x22, width_mm),
        g._octparam(5, 0x12, N_A),
        g._octparam(6, 0x12, N_B),
        g._octparam(8, 0x22, width_mm / N_A),
        g._octparam(9, 0x22, 0.0028),
        g._octparam(11, 0x12, 992),
    ]
    return g._chunk("OCTPARAMS", g._wrap(b"".join(recs)))


def _dmarkers(valid_rows: int) -> bytes:
    m = np.full((N_B, 4), np.nan, dtype="<f4")
    m[:valid_rows] = [10.0, 0.0, 30.0, 0.0]
    return g._chunk("DMARKERS", g._wrap(struct.pack("<I", N_B) + m.tobytes()))


def _file(tmp_path, width_mm: float, disc_rows: int):
    top = np.full((N_B, N_A), 640.0, dtype=np.float32)
    parts = [g.build_params(), _octparams(width_mm), g.build_layer("TOP", top), g.build_layer("BM", top + 111)]
    if disc_rows:
        parts.append(_dmarkers(disc_rows))
    parts += [g.build_t_chunk(i) for i in range(N_B)]
    p = tmp_path / "w.opt"
    p.write_bytes(b"".join(parts))
    return p


def test_wide_scan_with_disc_skips_cmt_and_etdrs(tmp_path):
    r = read_opt(_file(tmp_path, 12.0, disc_rows=20))
    assert r["cmt_um"] is None and r["etdrs"] is None
    assert r["cmt_failure_reason"] == "fovea_not_centered"


def test_wide_scan_without_disc_keeps_cmt(tmp_path):
    r = read_opt(_file(tmp_path, 12.0, disc_rows=0))
    assert r["cmt_um"] is not None and r["cmt_failure_reason"] is None


def test_narrow_scan_with_disc_markers_keeps_cmt(tmp_path):
    r = read_opt(_file(tmp_path, 6.0, disc_rows=20))
    assert r["cmt_um"] is not None
