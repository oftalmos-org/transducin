"""Capas de segmentación del .opt Revo en el DICOM OCT (ADR-028). Solo datos sintéticos, sin PHI."""

from __future__ import annotations

import struct

import numpy as np
import pydicom

from tests import generate_synthetic_opt as g
from transducin.layer_contours import decode_layers
from transducin.revo_opt_reader import layers_for_dicom, opt_to_dicom

N_B, N_A, H = 6, 32, 120


def _t(idx: int, h: int = H) -> bytes:
    frame = np.full((h, N_A), 40, dtype=np.uint8)
    header = b"\x54\x00" + struct.pack("<II", N_A, h)
    return g._chunk(f"T{idx}", g._wrap(header + frame.tobytes()))


def _octparams() -> bytes:
    recs = [
        g._octparam(3, 0x22, 6.0),
        g._octparam(5, 0x12, N_A),
        g._octparam(6, 0x12, N_B),
        g._octparam(8, 0x22, 6.0 / N_A),
        g._octparam(9, 0x22, 0.0028),
        g._octparam(11, 0x12, H),
    ]
    return g._chunk("OCTPARAMS", g._wrap(b"".join(recs)))


def _build(path, layers: dict[str, np.ndarray], n_t: int = N_B):
    parts = [g.build_params(), _octparams()]
    parts += [g.build_layer(name, arr.astype(np.float32)) for name, arr in layers.items()]
    parts += [_t(i) for i in range(n_t)]
    path.write_bytes(b"".join(parts))
    return path


def _layers(top_row=20.0, step=10.0, frames=N_B) -> dict[str, np.ndarray]:
    names = ["TOP", "NFL", "GCL", "BM"]
    out = {}
    for i, n in enumerate(names):
        out[n] = np.full((frames, N_A), top_row + i * step, dtype=np.float32)
    out["NFL"][0, :4] = 0  # ceros = sin dato
    return out


def _convert(tmp_path, layers, n_t=N_B):
    src = _build(tmp_path / "SYNTH_OCT.opt", layers, n_t)
    files = opt_to_dicom(
        src, tmp_path / "out", noel_id="", patient_id="X1", study_date="20261113", laterality="R", study_type="macular"
    )
    oct_file = next(f for f in files if f.name.endswith("_OCT.dcm"))
    return pydicom.dcmread(oct_file)


def test_layers_block_written_in_anatomical_order_with_nan_for_zeros(tmp_path):
    ds = _convert(tmp_path, _layers())
    meta, arr = decode_layers(ds)
    keys = [layer["key"] for layer in meta["layers"]]
    assert keys == ["c00_TOP", "c01_NFL", "c02_GCL", "c10_BM"]
    assert [layer["label"] for layer in meta["layers"]] == ["ILM", "NFL/GCL", "GCL/IPL", "Bruch (BM)"]
    assert all(layer["status"] == "documented" for layer in meta["layers"])
    assert arr.shape == (4, N_B, N_A)
    assert np.allclose(arr[0], 20.0) and np.allclose(arr[3], 50.0)
    assert np.isnan(arr[1, 0, :4]).all() and np.isfinite(arr[1, 1]).all()


def test_no_block_when_contours_fall_outside_the_image(tmp_path):
    ds = _convert(tmp_path, _layers(top_row=400.0))  # filas > altura del B-scan (120)
    assert decode_layers(ds) is None


def test_no_block_when_frame_count_differs(tmp_path):
    ds = _convert(tmp_path, _layers(frames=N_B - 1))
    assert decode_layers(ds) is None


def test_no_layers_no_block(tmp_path):
    ds = _convert(tmp_path, {})
    assert decode_layers(ds) is None


def test_layers_for_dicom_rejects_empty_or_zero_height():
    assert layers_for_dicom(None, 3, 100) is None
    assert layers_for_dicom({"TOP": np.ones((3, 4), np.float32)}, 3, 0) is None


def test_elm_before_onl_and_both_marked_inferred(tmp_path):
    base = _layers()
    base["ELM"] = np.full((N_B, N_A), 60.0, dtype=np.float32)
    base["ONL"] = np.full((N_B, N_A), 65.0, dtype=np.float32)  # el dato real los trae en este orden
    meta, _ = decode_layers(_convert(tmp_path, base))
    keys = [layer["key"] for layer in meta["layers"]]
    assert keys.index("c06_ELM") < keys.index("c07_ONL")
    status = {layer["key"].split("_", 1)[1]: layer["status"] for layer in meta["layers"]}
    assert status["ELM"] == status["ONL"] == "inferred" and status["TOP"] == "documented"
