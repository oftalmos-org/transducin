#!/usr/bin/env python3
"""Mapa de espesor ILM→BM EXPERIMENTAL (Topcon 3D OCT-1000 Mark II) + estadísticas exploratorias.

Uso:
    python scripts/topcon_experimental_thickness.py ARCHIVO.fda --experimental [-o carpeta]

Requiere la opción explícita --experimental. Usa los contornos originales RETINA_1 y RETINA_4
(sin modificarlos). El resultado es exploratorio: NO es un CMT clínico, ni un DICOM cuantitativo,
ni una observación para Graefe. La identidad anatómica procede de revisión visual del especialista
y de bibliografía; la correspondencia con el campo binario no está confirmada.

Salida (por defecto $TRANSDUCIN_REAL_DATA_DIR/public/topcon/experimental/, fuera de Git):
    thickness_map.png, thickness_stats.json, thickness_map.npy
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transducin.adapters.topcon import TopconFdaAdapter
from transducin.topcon_experimental import ExperimentalUnavailable


def render(res, out_png: Path, source_name: str) -> None:
    t = res.thickness_um
    n_b, n_a = t.shape
    st = res.stats
    lat_mm = n_a * 0.01171875 if "central_disc_r500um" in st else 6.0
    fig, ax = plt.subplots(figsize=(8, 7.4))
    valid = t[np.isfinite(t)]
    lo, hi = (np.percentile(valid, 2), np.percentile(valid, 98)) if valid.size else (0, 1)
    ax.set_facecolor("#cccccc")
    im = ax.imshow(
        np.ma.masked_invalid(t),
        cmap="viridis",
        vmin=lo,
        vmax=hi,
        extent=[0, lat_mm, lat_mm, 0],
        aspect="equal",
        interpolation="nearest",
    )
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cb.set_label("espesor ILM→BM (µm) — experimental")
    cx = cy = lat_mm / 2
    for r_mm, ls in ((0.5, "-"), (1.5, "--")):
        ax.add_patch(plt.Circle((cx, cy), r_mm, fill=False, color="white", lw=1.2, ls=ls))
    ax.text(
        cx,
        cy - 0.5 - 0.08,
        "centro geométrico (fóvea no localizada)",
        color="white",
        ha="center",
        va="bottom",
        fontsize=7,
    )
    for b in res.flags["incomplete_bscans_below_90pct_valid"]:
        ax.plot([0, 0.08], [(b + 0.5) * lat_mm / n_b] * 2, color="red", lw=1.5)
    ax.set_xlabel("A-scan (mm)")
    ax.set_ylabel("B-scan (mm)  — sin orientación S/I/N/T verificada")
    ax.set_title(
        "EXPERIMENTAL — espesor ILM→BM (RETINA_1→RETINA_4)\nno es CMT clínico; identidad anatómica no confirmada en el binario",
        fontsize=10,
        color="#b00020",
    )
    c, ring = st["central_disc_r500um"], st["ring_500_1500um"]
    txt = (
        f"{source_name} · {res.device.get('model')} ROM {res.device.get('rom')} · eje axial {res.axial_scale['value_um_per_px']} µm/px (verificado)\n"
        f"válido {st['valid_fraction'] * 100:.1f} %  ·  media {st.get('mean_um')} µm  ·  mediana {st.get('median_um')}  ·  p5–p95 {st.get('p5_um')}–{st.get('p95_um')}\n"
        f"disco central r=0,5 mm: {c['mean_um']} µm  ·  anillo 0,5–1,5 mm: {ring['mean_um']} µm  ·  procedencia: {res.measurement_provenance}"
    )
    fig.text(0.01, 0.005, txt, fontsize=6.5, va="bottom", family="monospace")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fda", type=Path)
    ap.add_argument(
        "--experimental", action="store_true", help="obligatorio: reconoce que el resultado es exploratorio"
    )
    ap.add_argument("-o", "--output", type=Path, default=None)
    a = ap.parse_args()
    if not a.experimental:
        print("Este cálculo es experimental y no clínico: añade --experimental para confirmarlo.", file=sys.stderr)
        return 2
    root = os.environ.get("TRANSDUCIN_REAL_DATA_DIR")
    out = a.output or (Path(root) / "public/topcon/experimental" if root else Path.cwd() / "experimental")
    out.mkdir(parents=True, exist_ok=True)
    try:
        res = TopconFdaAdapter(experimental=True).experimental_thickness(a.fda)
    except ExperimentalUnavailable as exc:
        print(f"No disponible: {exc}", file=sys.stderr)
        return 3
    np.save(out / "thickness_map.npy", res.thickness_um)
    (out / "thickness_stats.json").write_text(
        json.dumps(res.to_json_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    render(res, out / "thickness_map.png", a.fda.name)
    s = res.stats
    print(
        f"OK {out} · válido {s['valid_fraction'] * 100:.1f} % · mediana {s['median_um']} µm · disco central {s['central_disc_r500um']['mean_um']} µm (EXPERIMENTAL)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
