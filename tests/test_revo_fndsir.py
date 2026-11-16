"""Revo: el SLO integrado FNDSIR (632×632) se exporta solo si trae contenido real."""

from __future__ import annotations

import struct

import numpy as np

from tests import generate_synthetic_opt as g
from transducin.revo_opt_reader import opt_to_dicom, read_opt


def _i8(name: str, img: np.ndarray) -> bytes:
    h, w = img.shape
    return g._chunk(name, g._wrap(b"I8" + struct.pack("<II", w, h) + img.astype(np.uint8).tobytes()))


def _file(tmp_path, fndsir: np.ndarray):
    parts = [g.build_params(), g.build_octparams(), *g.build_top_bm(), _i8("FNDSIR", fndsir)]
    parts += [g.build_t_chunk(i) for i in range(4)]
    p = tmp_path / "f.opt"
    p.write_bytes(b"".join(parts))
    return p


def _circle(values: np.ndarray) -> np.ndarray:
    yy, xx = np.mgrid[:632, :632]
    mask = (xx - 315.5) ** 2 + (yy - 315.5) ** 2 <= 316**2
    out = np.zeros((632, 632), dtype=np.uint8)
    out[mask] = values[mask]
    return out


def test_fndsir_with_content_is_exported(tmp_path):
    rng = np.random.default_rng(1)
    img = _circle(rng.integers(20, 200, (632, 632)))
    assert read_opt(_file(tmp_path, img))["fndsir"] is not None
    files = opt_to_dicom(
        _file(tmp_path, img),
        tmp_path / "out",
        noel_id="",
        patient_id="X",
        study_date="",
        laterality="R",
        study_type="macular",
    )
    assert any(f.name.endswith("_FNDSIR.dcm") for f in files)


def test_blank_fndsir_is_not_exported(tmp_path):
    flat = _circle(np.full((632, 632), 90))
    assert read_opt(_file(tmp_path, flat))["fndsir"] is None
    zeros = np.zeros((632, 632), dtype=np.uint8)
    assert read_opt(_file(tmp_path, zeros))["fndsir"] is None
