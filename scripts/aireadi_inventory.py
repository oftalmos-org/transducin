#!/usr/bin/env python
"""Inventario AGREGADO de AI-READI por equipo (solo cabeceras técnicas).

Se ejecuta en local, lo corre el titular. Lee cabeceras DICOM sin pixeles y
cuenta valores técnicos por carpeta de equipo. NO emite PatientID, nombres,
fechas, UIDs ni nada por archivo: solo conteos.

Sobre identidad (ADR-026) cuenta, por grupo: PatientID vacío / con forma NOEL /
otro, distribución de longitudes, número de PatientID distintos, y si hay
PatientBirthDate o IssuerOfPatientID. Nunca escribe los valores.

Uso:
    .venv314/bin/python -I scripts/aireadi_inventory.py <ruta/dataset> --out inventario.json
        [--progress 500] [--timeout 60] [--max-files N]

Muestra avance en stderr cada --progress archivos. --timeout (segundos por
archivo) es de mejor esfuerzo: usa SIGALRM y no interrumpe una lectura bloqueada
en el kernel (volumen de red caído); ante eso, el avance dejará de moverse.
Ctrl+C escribe el resultado parcial.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import pydicom

# Solo atributos técnicos. Prohibido añadir identificadores o fechas.
TAGS = {
    "Modality": "modality",
    "Manufacturer": "manufacturer",
    "ManufacturerModelName": "model",
    "SoftwareVersions": "software",
    "SOPClassUID": "sop_class",
    "Rows": "rows",
    "Columns": "columns",
    "NumberOfFrames": "frames",
}
SECCIONES = ("retinal_oct", "retinal_octa", "retinal_photography")
_NOEL = re.compile(r"^[A-Z]{3,4}\d{8}$")


def _valor(v) -> str:
    if v is None:
        return "no consta"
    if isinstance(v, (list, tuple)) or hasattr(v, "__iter__") and not isinstance(v, (str, bytes)):
        return "|".join(str(x) for x in v)
    return str(v)


def _clase_patient_id(pid: str) -> str:
    """Clasifica sin guardar el valor: vacio | forma_noel | otro."""
    pid = pid.strip()
    if not pid:
        return "vacio"
    return "forma_noel" if _NOEL.match(pid) else "otro"


class _Timeout(Exception):
    pass


def _on_alarm(_signum, _frame):
    raise _Timeout


def _leer(f: Path, timeout: int):
    if timeout:
        signal.alarm(timeout)
    try:
        return pydicom.dcmread(f, stop_before_pixels=True, force=True)
    finally:
        if timeout:
            signal.alarm(0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--progress", type=int, default=500, help="avance en stderr cada N archivos (0 = nunca)")
    ap.add_argument("--timeout", type=int, default=0, help="segundos por archivo, mejor esfuerzo (0 = sin límite)")
    ap.add_argument("--max-files", type=int, default=0, help="detenerse tras N archivos (0 = todos)")
    args = ap.parse_args()

    if not args.dataset.is_dir():
        print(f"No existe la carpeta: {args.dataset}", file=sys.stderr)
        return 2
    if args.timeout:
        signal.signal(signal.SIGALRM, _on_alarm)

    agg: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    distintos: dict[str, set[str]] = defaultdict(set)  # solo para contar; nunca se escribe
    errores: Counter = Counter()
    total = 0
    t0 = time.monotonic()
    interrumpido = False

    try:
        for seccion in SECCIONES:
            carpeta = args.dataset / seccion
            if not carpeta.is_dir():
                errores[f"{seccion}: carpeta ausente"] += 1
                continue
            for f in sorted(carpeta.rglob("*.dcm")):
                if args.max_files and total >= args.max_files:
                    raise StopIteration
                total += 1
                if args.progress and total % args.progress == 0:
                    print(f"  {total} archivos, {time.monotonic() - t0:.0f}s", file=sys.stderr, flush=True)
                partes = f.relative_to(args.dataset).parts
                clave = "/".join(partes[:3])  # seccion/tipo/equipo
                try:
                    ds = _leer(f, args.timeout)
                except _Timeout:
                    errores[f"{clave}: Timeout"] += 1
                    continue
                except Exception as e:  # archivo truncado o ilegible
                    errores[f"{clave}: {type(e).__name__}"] += 1
                    continue
                agg[clave]["archivos"]["n"] += 1
                for kw, nombre in TAGS.items():
                    agg[clave][nombre][_valor(ds.get(kw))] += 1
                # lateralidad: solo el estado, nunca más
                lat = _valor(ds.get("ImageLaterality", ds.get("Laterality")))
                agg[clave]["lateralidad"][lat if lat in ("R", "L") else "no consta"] += 1
                # identidad (ADR-026): solo clases y longitudes, nunca el valor
                pid = str(ds.get("PatientID", "") or "")
                agg[clave]["patient_id_clase"][_clase_patient_id(pid)] += 1
                if pid.strip():
                    agg[clave]["patient_id_longitud"][str(len(pid.strip()))] += 1
                    distintos[clave].add(pid.strip())
                agg[clave]["patient_birth_date"][
                    "presente" if str(ds.get("PatientBirthDate", "") or "").strip() else "vacio"
                ] += 1
                agg[clave]["issuer_of_patient_id"][
                    "presente" if str(ds.get("IssuerOfPatientID", "") or "").strip() else "vacio"
                ] += 1
    except StopIteration:
        pass
    except KeyboardInterrupt:
        interrumpido = True
        print("Interrumpido: se escribe el resultado parcial.", file=sys.stderr)

    grupos = {k: {a: dict(c) for a, c in v.items()} for k, v in sorted(agg.items())}
    for clave, vals in distintos.items():
        grupos[clave]["patient_id_distintos"] = {"n": len(vals)}
    salida = {
        "avisos": "Solo conteos agregados. Sin identificadores, fechas ni UIDs.",
        "parcial": interrumpido or bool(args.max_files and total >= args.max_files),
        "archivos_vistos": total,
        "segundos": round(time.monotonic() - t0),
        "grupos": grupos,
        "errores_lectura": dict(errores),
    }
    args.out.write_text(json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Escrito {args.out} ({len(agg)} grupos, {sum(errores.values())} errores de lectura)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
