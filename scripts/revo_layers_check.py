#!/usr/bin/env python3
"""Verificación AGREGADA de las capas de segmentación de .opt Revo (ADR-028), sin imprimir datos.

Lo corre el titular en local sobre su carpeta de .opt. Comprueba que los contornos del equipo caben en
la imagen del B-scan, que están en orden anatómico y que el DICOM OCT generado lleva el bloque de capas.

PRIVACIDAD:
- No imprime nombres de archivo (suelen contener el nombre del paciente), identificadores, fechas ni
  metadatos del archivo. Los archivos se numeran ``archivo_1``, ``archivo_2``… por orden alfabético.
- Solo salen formas, conteos y rangos de filas en píxeles. Silencia el registro del lector porque este
  imprime el nombre del archivo.
- Los DICOM de prueba se escriben en un directorio temporal que se borra al terminar.

Uso:
    .venv314/bin/python -I scripts/revo_layers_check.py "<carpeta con .opt>" --out revo_capas.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

logging.disable(logging.CRITICAL)  # el lector registra el nombre del archivo

import itertools

from transducin.layer_contours import decode_layers
from transducin.revo_opt_reader import _REVO_LAYER_ORDER, opt_to_dicom, read_opt


def analyze(path: Path) -> dict:
    r = read_opt(path)
    bscans = [b for b in r["bscans"] if min(b.shape) > 1]
    out: dict = {"n_bscans_decodificados": len(r["bscans"]), "n_bscans_validos": len(bscans)}
    if bscans:
        h, w = bscans[0].shape
        out["bscan_alto_ancho"] = [int(h), int(w)]
    p = r.get("params") or {}
    out["depth_px_octparams"] = p.get("depth_px")
    out["axial_um"] = p.get("axial_um")
    layers = r.get("layers") or {}
    info = {}
    for name, _lbl, _st in _REVO_LAYER_ORDER:
        a = layers.get(name)
        if a is None:
            continue
        valid = a[a > 0]
        info[name] = {
            "forma": [int(a.shape[0]), int(a.shape[1])],
            "fraccion_valida": round(float(valid.size / a.size), 3),
            "cortes_con_datos": int((a > 0).any(axis=1).sum()),
            "columnas_con_datos": int((a > 0).any(axis=0).sum()),
            "fila_min": round(float(valid.min()), 1) if valid.size else None,
            "fila_max": round(float(valid.max()), 1) if valid.size else None,
        }
    out["capas"] = info
    if bscans and info:
        h = bscans[0].shape[0]
        out["todas_las_filas_caben_en_la_imagen"] = all(
            v["fila_max"] is not None and v["fila_max"] <= h for v in info.values()
        )
        out["cortes_coinciden_con_bscans"] = all(v["forma"][0] == len(bscans) for v in info.values())
        # orden anatómico: cada contorno debe estar por debajo (fila mayor) del anterior donde ambos existen
        names = [n for n, _, _s in _REVO_LAYER_ORDER if n in layers]
        viol = {}
        for a_name, b_name in itertools.pairwise(names):
            a, b = layers[a_name], layers[b_name]
            if a.shape != b.shape:
                continue
            both = (a > 0) & (b > 0)
            viol[f"{a_name}->{b_name}"] = round(float(((b < a) & both).sum() / max(both.sum(), 1)), 4)
        out["fraccion_fuera_de_orden"] = viol
    return out


def dicom_roundtrip(path: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        files = opt_to_dicom(
            path, tmp, noel_id="", patient_id="VERIFICACION", study_date="", laterality="R", study_type="macular"
        )
        import pydicom

        oct_file = next((f for f in files if f.name.endswith("_OCT.dcm")), None)
        if oct_file is None:
            return {"dicom_oct": False}
        ds = pydicom.dcmread(oct_file, stop_before_pixels=True)
        dec = decode_layers(ds)
        if dec is None:
            return {"dicom_oct": True, "bloque_de_capas": False}
        meta, arr = dec
        return {
            "dicom_oct": True,
            "bloque_de_capas": True,
            "capas_en_dicom": [layer["key"].split("_", 1)[1] for layer in meta["layers"]],
            "forma_capa_corte_ascan": [int(x) for x in arr.shape],
            "fraccion_nan": round(float(np.isnan(arr).mean()), 3),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    files = sorted(p for p in args.folder.iterdir() if p.is_file() and p.suffix.lower() == ".opt")
    results = {}
    for i, f in enumerate(files, 1):
        entry: dict = {}
        try:
            entry.update(analyze(f))
            entry.update(dicom_roundtrip(f))
        except Exception as e:  # solo el tipo: el mensaje puede contener rutas o valores
            entry["error"] = type(e).__name__
        results[f"archivo_{i}"] = entry
    payload = {
        "avisos": "Solo formas, conteos y rangos de filas. Sin nombres, identificadores ni fechas.",
        "archivos": results,
    }
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Escrito {args.out.name}: {len(files)} archivos .opt analizados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
