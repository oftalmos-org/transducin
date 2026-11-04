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
from transducin.laterality_evidence import collect as collect_evidence
from transducin.laterality_evidence import evaluate as evaluate_evidence

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
        route = sl.classify_route(data)
        evidence, unavailable = collect_evidence(data, route)
        rec = sl.assess(data, "sr", evaluate_evidence(evidence, (data.laterality or "").strip(), unavailable))
        rec["study_hmac"] = f"corpus-{counts['procesado']}"  # sin identificadores reales
        per_route.setdefault(rec["route"], []).append(rec)
    agg = sl.aggregate([r for rs in per_route.values() for r in rs])
    return {"files": dict(counts), **agg}


EXPECTED_ROUTES = ("revo_opt", "cirrus_dcm", "cirrus_pdf", "spectralis_e2e", "topcon_fda")
MIN_N = 10  # por debajo de este número de estudios únicos no se interpretan porcentajes


def _pct(n: int, d: int) -> str:
    return f"{n} ({100 * n / d:.0f} %)" if d else "—"


def _true(b: dict, key: str) -> int:
    return b["evidence"].get(key, {}).get("True", 0)


def _flag_true(b: dict, key: str) -> int:
    return b["flags"].get(key, {}).get("True", 0)


def markdown(agg: dict) -> str:
    """Informe en español a partir de un agregado (solo cuentas)."""
    rows = agg["by_route"]
    uniq = agg["unique_studies"]
    uniq_txt = ", ".join(f"{k}: {v}" for k, v in sorted(uniq.items())) or "ninguno"
    out = ["# Informe del modo sombra — lateralidad y riesgos R1–R6", ""]
    out += [f"Registros (emisiones): **{agg['n_records']}** · Estudios únicos por ruta: {uniq_txt}", ""]
    out += [
        "## 1. Lateralidad por ruta y destino",
        "",
        (
            "| Ruta | Destino | n | Sin lateralidad / por defecto | Fuentes concordantes (≥2 orígenes) "
            "| Concordancia independiente (≥2 fuertes) | Conflicto | Confirmada por referencia | Emitido ≠ evidencia |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for key in sorted(rows):
        b = rows[key]
        route, dest = key.split("|")
        n = b["n"]
        defaulted = b["flags"].get("R1_laterality", {}).get("defaulted", 0)
        out.append(
            f"| {route} | {dest} | {n} | {_pct(defaulted, n)} | {_pct(_true(b, 'concordant_multi_group'), n)} "
            f"| {_pct(_true(b, 'concordant_independent'), n)} | {_pct(_true(b, 'conflict'), n)} "
            f"| {_pct(_true(b, 'confirmed_by_reference'), n)} | {_pct(b['emitted_vs_evidence'].get('mismatch', 0), n)} |"
        )
    out += [
        "",
        'Destino `sr`: una lateralidad vacía se emite como **"R"** (asignación por defecto). Destino `webhook`: se envía vacía.',
        "",
        "## 2. Categorías de R1 y estado de la evidencia",
        "",
        "| Ruta | Destino | R1 | Estado de la evidencia | Tipo de conflicto |",
        "|---|---|---|---|---|",
    ]
    for key in sorted(rows):
        b = rows[key]
        route, dest = key.split("|")
        out.append(
            f"| {route} | {dest} | {b['flags'].get('R1_laterality', {})} | {b['laterality_state']} "
            f"| {b['evidence'].get('conflict_kind', {})} |"
        )
    out += [
        "",
        "## 3. R2–R6 por ruta y destino",
        "",
        "| Ruta | Destino | n | R2 solapados | R3 cuña nasal | R4 orientación | R5 centro geométrico | R6 sin procedencia |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key in sorted(rows):
        b = rows[key]
        route, dest = key.split("|")
        n = b["n"]
        out.append(
            f"| {route} | {dest} | {n} | {_pct(_flag_true(b, 'R2_overlap_sectors'), n)} "
            f"| {_pct(_flag_true(b, 'R3_cirrus_nasal_wedge'), n)} | {_pct(_flag_true(b, 'R4_orientation_unvalidated'), n)} "
            f"| {b['flags'].get('R5_geometric_center_as_foveal', {})} "
            f"| {_pct(_flag_true(b, 'R6_no_provenance_or_validation'), n)} |"
        )
    out += ["", "## 4. Por tipo de estudio", "", "| Ruta | Tipo | n | R1 | R5 |", "|---|---|---|---|---|"]
    for key in sorted(agg["by_route_study_type"]):
        b = agg["by_route_study_type"][key]
        route, typ = key.split("|")
        out.append(
            f"| {route} | {typ} | {b['n']} | {b['flags'].get('R1_laterality', {})} "
            f"| {b['flags'].get('R5_geometric_center_as_foveal', {})} |"
        )
    out += ["", "## 5. Limitaciones y datos insuficientes", ""]
    seen = {k.split("|")[0] for k in rows}
    for r in EXPECTED_ROUTES:
        if r not in seen:
            out.append(
                f"- **{r}: ruta no ejercitada** (0 registros). No se concluye nada; 0 hallazgos no es validación."
            )
        elif uniq.get(r, 0) < MIN_N:
            out.append(
                f"- **{r}: datos insuficientes** ({uniq.get(r, 0)} estudios únicos < {MIN_N}); porcentajes no interpretables."
            )
    unavailable: dict[str, int] = {}
    for b in rows.values():
        for k, v in b["evidence"].get("unavailable", {}).items():
            unavailable[k] = unavailable.get(k, 0) + v
    out += [
        f"- Evidencia no disponible (archivo ausente/ilegible/demasiado grande): {unavailable or 'ninguna'}.",
        (
            "- Un **conflicto** entre fuentes no es un ojo equivocado: demostrarlo exige una referencia independiente "
            "(localizador/SLO o confirmación médica)."
        ),
        "- Spectralis y Topcon: el metadato nativo es **un solo dato original**; solo se suma el nombre de archivo (débil).",
        "- Confirmación médica e información independiente de adquisición aún no tienen productor: su recuento es 0 por construcción.",
        (
            "- Los estudios únicos se cuentan por HMAC de estudio; una emisión aparece en `sr` y en `webhook`, "
            "y los reprocesos del SR pueden duplicar registros `sr`."
        ),
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("structural")
    sub.add_parser("corpus").add_argument("root", type=Path)
    sub.add_parser("log").add_argument("dir", type=Path)
    ap.add_argument("--out", type=Path, help="guardar el JSON agregado")
    ap.add_argument("--md", type=Path, help="guardar el informe en Markdown (corpus y log)")
    args = ap.parse_args()
    if args.cmd == "structural":
        result = structural()
    elif args.cmd == "corpus":
        result = corpus(args.root)
        if args.md:
            args.md.write_text(markdown(result), encoding="utf-8")
    else:
        result = sl.aggregate(sl.read_records(args.dir))
        if args.md:
            args.md.write_text(markdown(result), encoding="utf-8")
    text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
