#!/usr/bin/env python3
"""Descarga de Orthanc los estudios de UN PatientID (los del propio titular) a un corpus local.

Lo corre el titular en su máquina. Solo lectura sobre Orthanc.

PRIVACIDAD:
- Credenciales y URL salen de variables de entorno (ORTHANC_URL, ORTHANC_USER, ORTHANC_PASS); nunca se imprimen.
- Los archivos se guardan con nombres opacos (estudio_N/instancia_M.dcm), sin nombres ni identificadores.
- Solo se imprimen conteos por fabricante, modalidad y fecha de estudio no se imprime.
- Se escribe MANIFEST.json (CLAUDE.md §2.1.1) con claude_session_ok=false: el titular decide si lo pone en true.

Uso:
    export ORTHANC_URL=... ORTHANC_USER=... ORTHANC_PASS=...
    python3 scripts/orthanc_pull_own.py --patient-id "<PatientID exacto>" --list
    python3 scripts/orthanc_pull_own.py --patient-id "<PatientID exacto>" \
        --out ~/transducin-real-data/private/orthanc-propios-2026-11
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen


def _req(path: str, data: dict | None = None, raw: bool = False):
    base = os.environ.get("ORTHANC_URL", "").rstrip("/")
    if not base:
        sys.exit("Falta ORTHANC_URL en el entorno.")
    req = Request(base + path, data=json.dumps(data).encode() if data is not None else None)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    user, pw = os.environ.get("ORTHANC_USER", ""), os.environ.get("ORTHANC_PASS", "")
    if user:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    with urlopen(req, timeout=60) as resp:
        body = resp.read()
    return body if raw else json.loads(body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patient-id", required=True, help="PatientID exacto en Orthanc")
    ap.add_argument("--out", type=Path, help="carpeta de destino (fuera de cualquier repo)")
    ap.add_argument("--list", action="store_true", help="solo contar, sin descargar")
    args = ap.parse_args()
    if not args.list and not args.out:
        ap.error("--out es obligatorio salvo con --list")

    studies = sorted(_req("/tools/find", {"Level": "Study", "Query": {"PatientID": args.patient_id}, "Expand": False}))
    print(f"Estudios encontrados: {len(studies)}")
    by_vendor: Counter[str] = Counter()
    n_instances = 0
    plan: list[tuple[int, str, list[str]]] = []
    for si, sid in enumerate(studies, 1):
        insts = _req(f"/studies/{sid}/instances")
        ids = [i["ID"] for i in insts]
        n_instances += len(ids)
        for iid in ids:
            tags = _req(f"/instances/{iid}/simplified-tags")
            by_vendor[f"{tags.get('Manufacturer', '?')} · {tags.get('Modality', '?')}"] += 1
        plan.append((si, sid, ids))
    print(f"Instancias: {n_instances}")
    for k, v in sorted(by_vendor.items()):
        print(f"  {k}: {v}")
    if args.list:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    for si, _sid, ids in plan:
        d = args.out / f"estudio_{si}"
        d.mkdir(exist_ok=True)
        for mi, iid in enumerate(ids, 1):
            dest = d / f"instancia_{mi}.dcm"
            if dest.exists() and dest.stat().st_size > 0:  # reanuda: no vuelve a bajar lo ya completo
                blob = dest.read_bytes()
            else:
                blob = _req(f"/instances/{iid}/file", raw=True)
                dest.write_bytes(blob)
            print(f"  estudio {si}: instancia {mi}/{len(ids)}", flush=True)
            files.append({"path": str(dest.relative_to(args.out)), "sha256": hashlib.sha256(blob).hexdigest()})
    manifest = {
        "coleccion": "Estudios propios del titular descargados de Orthanc",
        "fuente": "Orthanc (Vitreum)",
        "fabricantes": dict(by_vendor),
        "formato_real": "DICOM",
        "licencia_o_condiciones": "datos propios del titular",
        "desidentificado": False,
        "validado": False,
        "claude_session_ok": False,
        "archivos": files,
    }
    (args.out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Descargados {len(files)} archivos. MANIFEST.json escrito con claude_session_ok=false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
