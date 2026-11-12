"""Visor de contornos E2E: genera HTML con datos SINTÉTICOS y se niega a escribir en el repo."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

from tests._synthetic import build_e2e

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_contour_viewer.py"


def _load():
    spec = importlib.util.spec_from_file_location("e2e_contour_viewer", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make(tmp_path):
    raw = np.random.default_rng(3).integers(0, 65536, size=(3, 32, 48), dtype=np.uint32).astype(np.uint16)
    return build_e2e(tmp_path / "s.e2e", n_b=3, n_a=48, image_raw=raw)


def test_build_data_has_images_and_contours(tmp_path):
    mod = _load()
    data = mod.build_data(_make(tmp_path), None)
    assert len(data["volumes"]) == 1
    v = data["volumes"][0]
    assert v["n_slices"] == 3 and all(v["images"])
    keys = {c["key"] for c in v["contours"]}
    assert {"contour0", "contour9"} <= keys
    json.dumps(data)  # serializable


def test_main_writes_html_next_to_source_and_has_no_filename(tmp_path, monkeypatch):
    mod = _load()
    src = _make(tmp_path)
    monkeypatch.setattr("sys.argv", ["x", str(src)])
    assert mod.main() == 0
    html = (tmp_path / "s.contornos.html").read_text(encoding="utf-8")
    assert "s.e2e" not in html  # no se incrusta el nombre del archivo de origen


def test_main_refuses_to_write_inside_repo(tmp_path, monkeypatch, capsys):
    mod = _load()
    src = _make(tmp_path)
    inside = Path(__file__).resolve().parents[1] / "scripts" / "no_debe_existir.html"
    monkeypatch.setattr("sys.argv", ["x", str(src), "-o", str(inside)])
    assert mod.main() == 2
    assert not inside.exists()
