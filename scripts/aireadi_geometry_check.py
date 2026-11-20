#!/usr/bin/env python3
"""Geometría AGREGADA de los OCT Spectralis de AI-READI (solo cabeceras; sin pixeles).

Lo corre el titular en local. Sirve para comparar la convención de geometría del DICOM nativo de
Spectralis (orientación, posición por corte, ubicación en el SLO) con la que genera Transducin.

PRIVACIDAD / LICENCIA (AI-READI Data License v2.0, cl. 3.B-3.C):
- Los datos no salen de la máquina: este script no imprime nada por archivo.
- Salida: solo conteos y valores técnicos redondeados, agrupados por lateralidad y número de cortes.
  Prohibido añadir PatientID, nombres, fechas, UIDs, rutas o nombres de archivo.
- No lee pixel data (stop_before_pixels).

Uso:
    .venv314/bin/python -I scripts/aireadi_geometry_check.py <carpeta heidelberg_spectralis> --out geometria.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pydicom

FRAMES_OF_INTEREST = {25, 27, 61}


def _seq0(ds, name):
    seq = getattr(ds, name, None)
    return seq[0] if seq else None


def _fg(ds, idx):
    pf = getattr(ds, "PerFrameFunctionalGroupsSequence", None)
    if not pf or idx >= len(pf):
        return None
    return pf[idx]


def _floats(v):
    try:
        return [float(x) for x in v]
    except Exception:
        return None


def _frame_ref_coords(fg):
    """ReferenceCoordinates del Ophthalmic Frame Location (x1,y1,x2,y2 en mm) si existe."""
    if fg is None:
        return None
    s = _seq0(fg, "OphthalmicFrameLocationSequence")
    if s is None:
        return None
    return _floats(getattr(s, "ReferenceCoordinates", None) or [])


def _frame_pos(fg):
    if fg is None:
        return None
    s = _seq0(fg, "PlanePositionSequence")
    return _floats(getattr(s, "ImagePositionPatient", None) or []) if s is not None else None


def _frame_orient(fg):
    if fg is None:
        return None
    s = _seq0(fg, "PlaneOrientationSequence")
    return _floats(getattr(s, "ImageOrientationPatient", None) or []) if s is not None else None


def _sign(x, eps=1e-6):
    return "+" if x > eps else "-" if x < -eps else "0"


def _r(vals, nd=2):
    return tuple(round(v, nd) for v in vals) if vals else None


def analyze(ds) -> dict | None:
    try:
        n = int(getattr(ds, "NumberOfFrames", 1))
    except Exception:
        return None
    if n not in FRAMES_OF_INTEREST:
        return None
    lat = str(getattr(ds, "ImageLaterality", "") or getattr(ds, "Laterality", "") or "?")
    out: dict = {"lat": lat, "frames": n}
    first, last = _fg(ds, 0), _fg(ds, n - 1)

    ps = None
    if first is not None:
        pm = _seq0(first, "PixelMeasuresSequence")
        if pm is not None:
            ps = _floats(getattr(pm, "PixelSpacing", None) or [])
            out["slice_thickness"] = getattr(pm, "SliceThickness", None)
            out["spacing_between"] = getattr(pm, "SpacingBetweenSlices", None)
    if ps is None:
        ps = _floats(getattr(ds, "PixelSpacing", None) or [])
    out["pixel_spacing"] = _r(ps, 4)

    o = _frame_orient(first) or _floats(getattr(ds, "ImageOrientationPatient", None) or [])
    out["orientation"] = _r(o, 2)

    p0, p1 = _frame_pos(first), _frame_pos(last)
    if p0 and p1 and len(p0) == 3 and len(p1) == 3:
        out["pos_delta_signs_first_to_last"] = "".join(_sign(b - a) for a, b in zip(p0, p1))

    c0, c1 = _frame_ref_coords(first), _frame_ref_coords(last)
    if c0 and c1 and len(c0) >= 4 and len(c1) >= 4:
        out["ref_x_dir_in_first"] = "x1<x2" if c0[0] < c0[2] else "x1>x2"
        out["ref_y_change_first_to_last"] = _sign(c1[1] - c0[1])
        out["ref_len_mm_first"] = round(abs(c0[2] - c0[0]), 1)
        out["ref_span_y_mm"] = round(abs(c1[1] - c0[1]), 1)
        out["ref_first_y_is_larger_than_last"] = c0[1] > c1[1]
    return out


# Convención observada en los .E2E propios y en los .vol públicos (eyepydata), ambos ojos:
# el primer corte queda en la parte inferior del SLO (y decrece hasta el último) y los cortes
# van de izquierda a derecha (x1 < x2).
EXPECTED = {"ref_first_y_is_larger_than_last": "True", "ref_x_dir_in_first": "x1<x2"}


def verify(groups) -> dict:
    """Compara los agregados con la convención esperada; sin valores por archivo."""
    out: dict = {}
    for key, fields in sorted(groups.items()):
        res = {}
        for f, exp in EXPECTED.items():
            c = fields.get(f)
            if not c:
                res[f] = "sin dato (la cabecera no trae la ubicación del corte en el SLO)"
                continue
            tot = sum(c.values())
            ok = c.get(exp, 0)
            res[f] = "coincide" if ok == tot else f"difiere: {tot - ok} de {tot} archivos no son {exp}"
        out[key] = res
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    groups: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    seen = skipped = 0
    errors: Counter = Counter()
    for p in args.folder.rglob("*"):
        if not p.is_file():
            continue
        try:
            ds = pydicom.dcmread(p, stop_before_pixels=True, force=True)
        except Exception:
            skipped += 1
            continue
        seen += 1
        try:
            r = analyze(ds)
        except Exception as e:  # solo el tipo de error: el mensaje podría contener valores de cabecera
            errors[type(e).__name__] += 1
            continue
        if r is None:
            continue
        key = f"lat={r.pop('lat')}|frames={r.pop('frames')}"
        groups[key]["archivos"][1] += 1
        for k, v in r.items():
            groups[key][k][str(v)] += 1
        if seen % 500 == 0:
            print(f"{seen} archivos", file=sys.stderr)

    result = {
        "avisos": "Solo conteos agregados. Sin identificadores, fechas, UIDs, rutas ni nombres de archivo.",
        "archivos_leidos": seen,
        "no_dicom": skipped,
        "errores_analisis_por_tipo": dict(errors),
        "verificacion_convencion": verify(groups),
        "grupos": {k: {f: dict(c.most_common(8)) for f, c in v.items()} for k, v in sorted(groups.items())},
    }
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for key, res in result["verificacion_convencion"].items():
        print(key, res)
    print(f"Escrito {args.out.name}: {seen} archivos leídos, {sum(g['archivos'][1] for g in groups.values())} de interés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
