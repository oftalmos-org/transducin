#!/usr/bin/env python3
"""Convierte con Transducin archivos de equipo (.E2E, .opt…) y sube los DICOM derivados a Orthanc.

ESCRIBE en Orthanc: por defecto solo muestra qué haría (simulación). Subir exige --yes.
Los originales no se tocan (ADR-024): se suben instancias derivadas nuevas.

PRIVACIDAD:
- URL y credenciales salen de ORTHANC_URL, ORTHANC_USER, ORTHANC_PASS; no se imprimen.
- No imprime nombres de archivo ni identificadores; solo conteos.
- --patient-id es obligatorio y se aplica como PatientID de TODO lo derivado, para que Orthanc lo agrupe con
  los demás estudios del titular. Nunca se infiere del nombre del archivo.

Uso (el Python del conversor de Gass ya tiene Transducin instalado):
    export ORTHANC_URL=... ORTHANC_USER=... ORTHANC_PASS=...
    PY=~/Developer/RetinaOS/Gass/.venv_converter/bin/python
    $PY scripts/orthanc_push_derived.py --patient-id "<PatientID exacto>" \
        --source ~/transducin-real-data/private/spectralis-2026-11-propios      # simulación
    $PY scripts/orthanc_push_derived.py --patient-id "<PatientID exacto>" \
        --source ~/transducin-real-data/private/spectralis-2026-11-propios --yes  # sube de verdad
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO.parent / "Gass"))
logging.disable(logging.CRITICAL)

EXTENSIONS = {".e2e", ".opt", ".fds", ".fda"}


def _orthanc(path: str, body: bytes | None = None, ctype: str = "application/json"):
    base = os.environ.get("ORTHANC_URL", "").rstrip("/")
    if not base:
        sys.exit("Falta ORTHANC_URL en el entorno.")
    req = Request(base + path, data=body)
    if body is not None:
        req.add_header("Content-Type", ctype)
    user, pw = os.environ.get("ORTHANC_USER", ""), os.environ.get("ORTHANC_PASS", "")
    if user:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read() or b"null")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patient-id", required=True)
    ap.add_argument("--source", type=Path, required=True, help="carpeta con los archivos de equipo")
    ap.add_argument("--yes", action="store_true", help="subir de verdad (sin esto solo simula)")
    args = ap.parse_args()

    import gass_converter as gc  # el mismo conversor que usa Gass
    import pydicom

    sources = sorted(p for p in args.source.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)
    print(f"Archivos de equipo: {len(sources)}")
    to_send: list[bytes] = []
    skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, src in enumerate(sources, 1):
            try:
                job = Path(tmp) / f"job{i}"
                job.mkdir()
                res = gc.convert_file(src, job, f"{i:032x}")
            except Exception as exc:  # solo el tipo: el mensaje puede traer rutas
                print(f"  archivo {i}: no convertido ({type(exc).__name__})")
                continue
            for path in sorted(job.rglob("*.dcm")):
                ds = pydicom.dcmread(path)
                ds.PatientID = args.patient_id
                already = _orthanc("/tools/lookup", str(ds.SOPInstanceUID).encode(), "text/plain")
                if already:
                    skipped += 1
                    continue
                buf = Path(tmp) / f"out{len(to_send)}.dcm"
                ds.save_as(buf, enforce_file_format=True)
                to_send.append(buf.read_bytes())
            print(f"  archivo {i}: {len(res.files)} imágenes, {len(res.sr_files)} SR")
    print(f"Instancias nuevas: {len(to_send)} · ya existentes en Orthanc: {skipped}")
    if not args.yes:
        print("Simulación: no se subió nada. Repite con --yes para subir.")
        return 0
    ok = 0
    for blob in to_send:
        try:
            _orthanc("/instances", blob, "application/dicom")
            ok += 1
        except HTTPError as exc:
            print(f"  error HTTP {exc.code}")
    print(f"Subidas {ok}/{len(to_send)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
