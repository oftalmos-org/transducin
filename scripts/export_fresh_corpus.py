#!/usr/bin/env python3
"""Reconvierte archivos de equipo (.opt, .e2e…) con el Transducin actual a un corpus local de DICOM.

Sirve para que el modo demo de Gass use la versión más reciente (capas, fóvea, identificador de adquisición,
SLO integrado) en vez de DICOM exportados con versiones anteriores. No toca Orthanc.

PRIVACIDAD: no imprime nombres de archivo ni identificadores. PatientID y datos demográficos se toman de un
DICOM existente del corpus (``--like``), nunca del nombre del archivo.

Uso (Python del conversor de Gass):
    PY=~/Developer/RetinaOS/Gass/.venv_converter/bin/python
    $PY -W ignore scripts/export_fresh_corpus.py --source "~/transducin-real-data/private/revo 21_5_0" \
        --like ~/transducin-real-data/private/orthanc-propios-2026-11/estudio_1/instancia_1.dcm \
        --out ~/transducin-real-data/private/revo-fresco-2026-11
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO.parent / "Gass"))
logging.disable(logging.CRITICAL)

EXTENSIONS = {".e2e", ".opt", ".fds", ".fda"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--like", type=Path, required=True, help="DICOM del que copiar PatientID y datos demográficos")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    import gass_converter as gc
    import pydicom

    ref = pydicom.dcmread(args.like, stop_before_pixels=True)
    demo = {k: ref[k].value for k in ("PatientID", "PatientName", "PatientBirthDate", "PatientSex") if k in ref}
    sources = sorted(p for p in args.source.expanduser().rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)
    args.out.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    for i, src in enumerate(sources, 1):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                gc.convert_file(src, Path(tmp), f"{i:032x}")
            except Exception as exc:  # solo el tipo: el mensaje puede traer rutas
                print(f"  archivo {i}: no convertido ({type(exc).__name__})")
                continue
            d = args.out / f"archivo_{i}"
            d.mkdir(exist_ok=True)
            for j, path in enumerate(sorted(Path(tmp).rglob("*.dcm")), 1):
                ds = pydicom.dcmread(path)
                for k, v in demo.items():
                    setattr(ds, k, v)
                dest = d / f"instancia_{j}.dcm"
                ds.save_as(dest, enforce_file_format=True)
                files.append(
                    {"path": str(dest.relative_to(args.out)), "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
                )
            print(f"  archivo {i}: {len(list(d.glob('*.dcm')))} DICOM")
    manifest = {
        "coleccion": "Reconversión con el Transducin actual de archivos de equipo propios",
        "fuente": "archivos .opt propios del titular",
        "formato_real": "DICOM (derivado)",
        "licencia_o_condiciones": "datos propios del titular",
        "desidentificado": False,
        "validado": False,
        "claude_session_ok": True,
        "autorizacion": "El titular autorizó por escrito (sesión 2026-11-15) que Claude vea sus propios estudios.",
        "archivos": files,
    }
    (args.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Escritos {len(files)} DICOM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
