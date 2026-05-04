#!/usr/bin/env python3
"""
Genera tests/fixtures/synthetic_macular.opt — archivo .opt Revo FC130 sintético mínimo.
Sin datos de pacientes reales. Fixture para pytest (JOSS).

Formato .opt (ingeniería inversa RetinaOS 2026):
  magic(4) | name_len(4 LE) | name(n) | NUL | meta(4) | field2(4) | payload
  payload  = type(1) | compressed_size(4 LE) | zlib(data)

Parámetros de diseño:
  N_BSCANS=128, N_ASCANS=512, SCAN_W_MM=10.0, AXIAL_UM=2.8
  lateral_um = 10000/512 = 19.531 µm  →  ETDRS: centro ~240px, sectores >800px
  ILM en 640px, BM en 751px → grosor=111px × 2.8µm = 310.8µm (rango 280-320 ✓)
"""
import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

MAGIC        = b"\xa5\xa5\xa5\xff"
OUT          = Path(__file__).parent / "fixtures" / "synthetic_macular.opt"
STUDY_UID    = "1.2.826.0.1.3680043.8.498.10000000000000000001"
PATIENT_ID   = "SYNTH00000000"

N_BSCANS     = 128
N_ASCANS     = 512
DEPTH_PX     = 64        # altura del T-chunk (pequeño; CMT/ETDRS usan layer arrays, no píxeles)
SCAN_W_MM    = 10.0
AXIAL_UM     = 2.800
ILM_PX       = 640.0
THICKNESS_PX = 111.0     # 111 × 2.8 = 310.8 µm


# ── primitivos ────────────────────────────────────────────────────────────────

def _wrap(payload: bytes) -> bytes:
    """Envuelve payload con prefijo que espera _decompress_block."""
    c = zlib.compress(payload, level=6)
    return b"\x01" + struct.pack("<I", len(c)) + c


def _chunk(name: str, payload: bytes, meta: int = 0, field2: int = 0) -> bytes:
    nb = name.encode("ascii")
    return (
        MAGIC
        + struct.pack("<I", len(nb)) + nb + b"\x00"
        + struct.pack("<I", meta)
        + struct.pack("<I", field2)
        + payload
    )


def _octparam(tag: int, type_code: int, value) -> bytes:
    vb = struct.pack("<I", int(value)) if type_code == 0x12 else struct.pack("<f", float(value))
    return bytes([tag, 0, 0, type_code]) + vb


# ── chunks ────────────────────────────────────────────────────────────────────

def build_params() -> bytes:
    # Raw ASCII; _decompress_block falla → extract_study_uid usa bytes crudos
    text = f"MACULAR 20260101 {STUDY_UID} {PATIENT_ID}\x00".encode("ascii")
    return _chunk("PARAMS", text)


def build_octparams() -> bytes:
    lat = SCAN_W_MM / N_ASCANS   # mm/ascan = 0.019531 → lateral_um = 19.531
    recs = [
        _octparam(3,  0x22, SCAN_W_MM),
        _octparam(5,  0x12, N_ASCANS),
        _octparam(6,  0x12, N_BSCANS),
        _octparam(8,  0x22, lat),
        _octparam(9,  0x22, AXIAL_UM / 1000.0),
        _octparam(11, 0x12, 992),   # depth realista aunque T-chunks sean menores
    ]
    return _chunk("OCTPARAMS", _wrap(b"".join(recs)))


def build_myopi() -> bytes:
    bio = {"al": 23.5, "k1": 43.0, "k2": 44.0, "cct": 545, "acd": 3.1}
    return _chunk("MYOPI", _wrap(json.dumps(bio).encode("utf-8")))


def build_layer(name: str, arr: np.ndarray) -> bytes:
    """arr: (n_bscans, n_ascans) float32 — formato width|n_frames|data."""
    n_frames, width = arr.shape
    header = struct.pack("<II", width, n_frames)
    return _chunk(name, _wrap(header + arr.astype("<f4").tobytes()))


def build_top_bm():
    """
    ILM=640px con dip foveal gaussiano (+20px al centro).
    BM=ILM+111px → grosor uniforme 310.8µm.
    El dip no afecta el grosor (BM sigue ILM), así CMT es estable en 310.8µm.
    """
    cy, cx = N_BSCANS // 2, N_ASCANS // 2
    y = np.arange(N_BSCANS) - cy
    x = np.arange(N_ASCANS) - cx
    Y, X = np.meshgrid(y, x, indexing="ij")   # (n_bscans, n_ascans)
    sigma_a, sigma_b = 80, 12                  # ascans, bscans
    dip = 20.0 * np.exp(-(X**2 / (2 * sigma_a**2) + Y**2 / (2 * sigma_b**2)))
    top = (ILM_PX + dip).astype(np.float32)
    bm  = (top + THICKNESS_PX).astype(np.float32)
    return top, bm


def build_t_chunk(idx: int) -> bytes:
    """B-scan sintético N_ASCANS×DEPTH_PX uint8: negro con línea de ILM escalada."""
    frame = np.zeros((DEPTH_PX, N_ASCANS), dtype=np.uint8)
    ilm_row = int(ILM_PX * DEPTH_PX / 992)
    if 0 <= ilm_row < DEPTH_PX:
        frame[ilm_row, :] = 180
    header = b"\x54\x00" + struct.pack("<II", N_ASCANS, DEPTH_PX)
    return _chunk(f"T{idx}", _wrap(header + frame.tobytes()))


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    top, bm = build_top_bm()

    parts = [
        build_params(),
        build_octparams(),
        build_myopi(),
        build_layer("TOP", top),
        build_layer("BM",  bm),
    ]
    for i in range(N_BSCANS):
        parts.append(build_t_chunk(i))

    data = b"".join(parts)
    OUT.write_bytes(data)
    print(f"Escrito: {OUT}  ({len(data) / 1024:.1f} KB)")

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from transducin.revo_opt_reader import read_opt
    r = read_opt(OUT)
    cmt   = r.get("cmt_um")
    etdrs = r.get("etdrs")

    print(f"n_frames : {r['n_frames']}")
    print(f"shape    : {r['shape']}")
    print(f"CMT      : {cmt:.1f} µm" if cmt else "CMT: None")
    print(f"ETDRS C  : {etdrs.C:.1f} µm" if etdrs else "ETDRS: None")
    print(f"myopi    : {r.get('myopi')}")
    print(f"study_uid: {r.get('study_uid')}")

    if etdrs:
        fields = [etdrs.C, etdrs.S1, etdrs.N1, etdrs.I1, etdrs.T1,
                  etdrs.S2, etdrs.N2, etdrs.I2, etdrs.T2]
        nones = sum(1 for f in fields if f is None)
        print(f"ETDRS sectores None: {nones}/9  {'✓' if nones == 0 else '✗'}")

    ok = (
        cmt is not None and 280 <= cmt <= 320
        and etdrs is not None
        and r["n_frames"] == N_BSCANS
    )
    print(f"\n{'✓ PASS' if ok else '✗ FAIL'}")


if __name__ == "__main__":
    main()
