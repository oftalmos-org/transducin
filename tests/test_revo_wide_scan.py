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


def _file_with_pit(tmp_path, pit_a: int):
    """12 × 12 mm con disco en a=40 y una foseta de ~120 µm en ``pit_a``."""
    ax = 2.8
    bs, la = 12000 / N_B, 12000 / N_A
    B, A = np.meshgrid((np.arange(N_B) - 32) * bs, (np.arange(N_A) - pit_a) * la, indexing="ij")
    thick_um = 330.0 - 120.0 * np.exp(-(B**2 + A**2) / (2 * 400.0**2))
    top = np.full((N_B, N_A), 100.0, dtype=np.float32)
    isos = (top + thick_um / ax).astype(np.float32)
    bm = isos + 5
    m = np.full((N_B, 4), np.nan, dtype="<f4")
    m[24:41] = [36.0, 0.0, 44.0, 0.0]  # disco en a≈40, cortes 24–40
    dm = g._chunk("DMARKERS", g._wrap(struct.pack("<I", N_B) + m.tobytes()))
    parts = [
        g.build_params(),
        _octparams(12.0),
        g.build_layer("TOP", top),
        g.build_layer("ISOS", isos),
        g.build_layer("BM", bm),
        dm,
    ]
    parts += [g.build_t_chunk(i) for i in range(N_B)]
    p = tmp_path / "pit.opt"
    p.write_bytes(b"".join(parts))
    return p


def test_wide_scan_locates_fovea_and_measures_there(tmp_path):
    r = read_opt(_file_with_pit(tmp_path, pit_a=88))  # a 4.5 mm del disco
    f = r["fovea"]
    assert f is not None and abs(f["a"] - 88) <= 3 and abs(f["b"] - 32) <= 3
    assert r["cmt_failure_reason"] is None and r["cmt_um"] is not None
    # en la foseta el grosor ILM→BM ronda 215 + 14 (BM) µm, muy por debajo del ~330 del centro del volumen
    assert r["cmt_um"] < 260


def test_wide_scan_pit_too_close_to_disc_is_rejected(tmp_path):
    r = read_opt(_file_with_pit(tmp_path, pit_a=52))  # a ~1.1 mm del disco: no es una fóvea plausible
    assert r["fovea"] is None and r["cmt_um"] is None and r["cmt_failure_reason"] == "fovea_not_centered"
