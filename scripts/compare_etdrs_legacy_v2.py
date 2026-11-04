#!/usr/bin/env python3
"""Compara el ETDRS legacy (compute_etdrs) con el ETDRS v2 sobre los datasets reales del corpus.

Uso:
    TRANSDUCIN_REAL_DATA_DIR=~/transducin-real-data python scripts/compare_etdrs_legacy_v2.py

Separa tres efectos sobre cada sector:
  1. GEOMETRÍA: medios anillos solapados (legacy) frente a cuadrantes de 90° (v2), con LA MISMA orientación.
  2. ORIENTACIÓN: cuánto cambian los sectores con cada combinación posible (B-scan 0 superior/inferior × sentido del A-scan).
  3. CENTRO: centro geométrico frente a candidato foveal (mínimo de espesor), sin validar.
Solo usa datos públicos (UK Biobank Topcon, eyepydata). No imprime metadatos de identidad. Escribe un JSON en
$TRANSDUCIN_REAL_DATA_DIR/public/comparisons/.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transducin.adapters.topcon import TopconFdaAdapter
from transducin.etdrs import (
    HEIDELBERG_VOL,
    LEGACY_ASSUMED,
    REGIONS,
    Orientation,
    compute_etdrs_v2,
    find_foveal_candidate,
)
from transducin.etdrs_policy import _masks as legacy_masks
from transducin.revo_opt_reader import compute_etdrs

SECTORS = ("S1", "N1", "I1", "T1", "S2", "N2", "I2", "T2")


def legacy(thickness_um: np.ndarray, lateral_um: float, scan_mm: float, lat: str) -> dict:
    g = compute_etdrs(
        np.zeros_like(thickness_um),
        thickness_um,
        {"axial_um": 1.0, "lateral_um": lateral_um, "scan_width_mm": scan_mm},
        laterality=lat,
    )
    return {k: getattr(g, k) for k in REGIONS}


def v2(t, lateral_um, bscan_um, lat, orient, center=None):
    return compute_etdrs_v2(
        t, lateral_um=lateral_um, bscan_um=bscan_um, laterality=lat, orientation=orient, center=center
    )


def fmt(x):
    return "—" if x is None else f"{x:6.1f}"


def analyze(
    name: str, t: np.ndarray, lateral_um: float, bscan_um: float, lat: str, orient: Orientation, label_orient: str
) -> dict:
    scan_mm = bscan_um * t.shape[0] / 1000.0
    out: dict = {"name": name, "laterality": lat, "scan_mm": round(scan_mm, 3), "shape": list(t.shape)}
    # 0. Solapamiento del legacy: cada píxel del anillo cae en 2 sectores
    m = legacy_masks(t.shape[0], t.shape[1], lateral_um, bscan_um, lat)
    inner = sum(int(m[s].sum()) for s in ("S1", "N1", "I1", "T1"))
    ring_px = int(np.logical_or.reduce([m[s] for s in ("S1", "N1", "I1", "T1")]).sum())
    out["legacy_inner_overlap_factor"] = round(inner / ring_px, 3)
    # 1. Geometría (misma orientación supuesta para ambos)
    leg = legacy(t, lateral_um, scan_mm, lat)
    same = v2(t, lateral_um, bscan_um, lat, LEGACY_ASSUMED)
    out["geometry_effect"] = {
        k: {
            "legacy": leg[k],
            "v2": same.value(k),
            "diff": None if same.value(k) is None or leg[k] is None else round(same.value(k) - leg[k], 1),
        }
        for k in REGIONS
    }
    # 2. Orientación: las 4 combinaciones (row0 × sentido del A-scan para este ojo)
    right = lat.upper() in ("R", "OD")
    combos = {}
    for row0, toward in itertools.product(("superior", "inferior"), (True, False)):
        o = Orientation(row0, toward if right else True, True if right else toward, False, "combinación de prueba")
        r = v2(t, lateral_um, bscan_um, lat, o)
        combos[f"row0={row0}, A-scan hacia {'nasal' if toward else 'temporal'}"] = {
            k: r.value(k) for k in SECTORS if r.value(k) is not None
        }
    vals = {k: [c[k] for c in combos.values() if k in c] for k in SECTORS}
    out["orientation_sensitivity_um"] = {k: round(max(v) - min(v), 1) for k, v in vals.items() if v}
    out["orientation_combos"] = combos
    out["v2_with_preset"] = {
        "orientation": label_orient,
        "regions": {k: (None if same.value(k) is None else round(same.value(k), 1)) for k in REGIONS},
    }
    pr = v2(t, lateral_um, bscan_um, lat, orient)
    out["v2_with_preset"] = {
        "orientation": label_orient,
        "validated": orient.validated,
        "regions": {k: (None if pr.value(k) is None else round(pr.value(k), 1)) for k in REGIONS},
    }
    out["coverage"] = {
        k: {"coverage": round(pr.regions[k].coverage, 3), "available": pr.regions[k].available} for k in REGIONS
    }
    # 3. Centro
    cand = find_foveal_candidate(t, lateral_um, bscan_um)
    geo_c = v2(t, lateral_um, bscan_um, lat, orient).central_subfield_um
    cand_r = v2(t, lateral_um, bscan_um, lat, orient, center=cand)
    cg = ((t.shape[0] - 1) / 2, (t.shape[1] - 1) / 2)
    out["center"] = {
        "geometric": [round(cg[0], 1), round(cg[1], 1)],
        "foveal_candidate": [cand.row, cand.col],
        "offset_um": [round((cand.row - cg[0]) * bscan_um, 1), round((cand.col - cg[1]) * lateral_um, 1)],
        "central_um_geometric": None if geo_c is None else round(float(geo_c), 1),
        "central_um_candidate": None if cand_r.central_subfield_um is None else round(cand_r.central_subfield_um, 1),
        "clinical_central_eligible": cand_r.clinical_central_eligible,
    }
    return out


def print_table(res: dict) -> None:
    print(
        f"\n### {res['name']} — ojo {res['laterality']}, cubo {res['scan_mm']} mm, factor de solapamiento legacy (anillo interno): {res['legacy_inner_overlap_factor']}"
    )
    print(
        "| Región | legacy | v2 (misma orientación) | Δ (v2−legacy) | sensibilidad a la orientación (rango, µm) | cobertura v2 |"
    )
    print("|---|---|---|---|---|---|")
    for k in REGIONS:
        g = res["geometry_effect"][k]
        sens = res["orientation_sensitivity_um"].get(k)
        cov = res["coverage"][k]
        print(
            f"| {k} | {fmt(g['legacy'])} | {fmt(g['v2'])} | {fmt(g['diff'])} | {'' if sens is None else f'{sens:.1f}'} | {cov['coverage']:.2f}{'' if cov['available'] else ' (no disponible)'} |"
        )
    c = res["center"]
    print(
        f"Centro: candidato foveal a {c['offset_um']} µm (B, A) del centro geométrico; disco central {c['central_um_geometric']} µm (geométrico) vs {c['central_um_candidate']} µm (candidato). Elegible como CMT clínico: {c['clinical_central_eligible']}."
    )


def main() -> int:
    root = os.environ.get("TRANSDUCIN_REAL_DATA_DIR")
    if not root:
        print("Define TRANSDUCIN_REAL_DATA_DIR", file=sys.stderr)
        return 2
    base = Path(root) / "public"
    results = []
    fda = base / "topcon/fda/eg_oct_fda.fda"
    if fda.exists():
        r = TopconFdaAdapter(experimental=True).experimental_thickness(fda)
        lat = "L"
        for nm, tm in (
            ("Topcon UK Biobank — ILM→BM (RETINA_1→4)", r.thickness_um),
            ("Topcon UK Biobank — ILM→RPE candidato (RETINA_1→3)", r.rpe_thickness_um),
        ):
            # La orientación Topcon no está verificada: se usa el supuesto legacy y se mide la sensibilidad
            results.append(analyze(nm, tm, 11.71875, 46.875, lat, LEGACY_ASSUMED, "supuesto legacy (no validada)"))
    npz = base / "heidelberg/derived/eyepydata_layers_ilm_bm.npz"
    if npz.exists():
        z = np.load(npz)
        for tag, lat in (("od", "R"), ("os", "L")):
            sz, sy, sx = z[f"{tag}_scale_mm_zyx"]
            t = (z[f"{tag}_bm_px"] - z[f"{tag}_ilm_px"]) * sy * 1000.0
            results.append(
                analyze(
                    f"Heidelberg eyepydata healthy_{tag.upper()} — ILM→BM",
                    t,
                    float(sx) * 1000.0,
                    float(sz) * 1000.0,
                    lat,
                    HEIDELBERG_VOL,
                    "Heidelberg .vol (B-scan 0 inferior; nasal por convención)",
                )
            )
    for res in results:
        print_table(res)
    out = base / "comparisons"
    out.mkdir(parents=True, exist_ok=True)
    (out / "etdrs_legacy_vs_v2.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nJSON: {out / 'etdrs_legacy_vs_v2.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
