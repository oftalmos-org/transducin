#!/usr/bin/env python3
"""Frecuencias agregadas R1–R6 (modo sombra). Solo cuentas: no imprime nombres de archivo ni identificadores.

Subcomandos:
  structural        exposición estructural por ruta (qué banderas dispara cada ruta con CMT+ETDRS y lateralidad conocida)
  corpus ROOT       pasa cada archivo soportado de ROOT por su adaptador (sin generar SR ni webhook) y cuenta banderas
  log DIR           agrega un log de sombra real (``TRANSDUCIN_SHADOW_LOG_DIR``), p. ej. el de lumen

Uso del corpus: solo colecciones públicas con manifiesto (CLAUDE.md §2.1.1). No usar con ``private/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

from transducin import shadow_log as sl
from transducin.adapter_registry import find_adapter
from transducin.clinical_data import ETDRSGrid, OCTClinicalData

_ROUTES = {
    "revo_opt": ("optopol_revo", "x.opt"),
    "cirrus_dcm": ("zeiss_cirrus", "x.dcm"),
    "cirrus_pdf": ("zeiss_cirrus", "x.pdf"),
    "spectralis_e2e": ("heidelberg_spectralis", "x.e2e"),
    "topcon_fda": ("topcon_dri_oct", "x.fda"),
}


def structural() -> dict:
    grid = ETDRSGrid(C=1.0, S1=1.0, N1=1.0, I1=1.0, T1=1.0, S2=1.0, N2=1.0, I2=1.0, T2=1.0)
    out = {}
    for route, (vendor, src) in _ROUTES.items():
        d = OCTClinicalData(
            noel_id="X",
            vendor=vendor,
            source_file=src,
            laterality="R",
            study_type="macular",
            cmt_um=1.0,
            etdrs_grid=grid,
        )
        a = sl.assess(d, "sr")
        out[route] = {
            "flags": a["flags"],
            "value_origin": a["value_origin"],
            "center_origin": a["center_origin"],
            "etdrs_impl": a["etdrs_impl"],
        }
    return out


def corpus(root: Path) -> dict:
    counts: Counter = Counter()
    per_route: dict[str, list[dict]] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith(".") or p.name == "MANIFEST.json":
            continue
        ad = find_adapter(p)
        if ad is None:
            counts["sin_adaptador (extensión no soportada)"] += 1
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = ad.extract(p)
        except Exception as exc:
            counts[f"error_extract:{type(exc).__name__}"] += 1
            continue
        counts["procesado"] += 1
        rec = sl.assess(data, "sr")
        per_route.setdefault(rec["route"], []).append(rec)
    agg = sl.aggregate([r for rs in per_route.values() for r in rs])
    return {"files": dict(counts), **agg}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("structural")
    sub.add_parser("corpus").add_argument("root", type=Path)
    sub.add_parser("log").add_argument("dir", type=Path)
    ap.add_argument("--out", type=Path, help="guardar el JSON agregado")
    args = ap.parse_args()
    if args.cmd == "structural":
        result = structural()
    elif args.cmd == "corpus":
        result = corpus(args.root)
    else:
        result = sl.aggregate(sl.read_records(args.dir))
    text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
