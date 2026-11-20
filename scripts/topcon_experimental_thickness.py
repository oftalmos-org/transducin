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
    """Tres paneles: ILM→BM (RETINA_1→4), ILM→RPE candidato (RETINA_1→3) y su diferencia (RETINA_3→4)."""
    _n_b, n_a = res.thickness_um.shape
    lat_mm = n_a * 0.01171875
    panels = [
        ("ILM→BM (RETINA_1→RETINA_4)", res.thickness_um, "viridis"),
        ("ILM→RPE candidato (RETINA_1→RETINA_3)", res.rpe_thickness_um, "viridis"),
    ]
    if res.rpe_thickness_um is not None:
        panels.append(("diferencia = RETINA_3→RETINA_4", res.thickness_um - res.rpe_thickness_um, "magma"))
    else:
        panels = panels[:1]
    fig, axes = plt.subplots(1, len(panels), figsize=(5.6 * len(panels), 6.2), squeeze=False)
    cx = cy = lat_mm / 2
    for ax, (title, t, cmap) in zip(axes[0], panels):
        v = t[np.isfinite(t)]
        lo, hi = (np.percentile(v, 2), np.percentile(v, 98)) if v.size else (0, 1)
        ax.set_facecolor("#cccccc")
        im = ax.imshow(
            np.ma.masked_invalid(t),
            cmap=cmap,
            vmin=lo,
            vmax=hi,
            extent=[0, lat_mm, lat_mm, 0],
            aspect="equal",
            interpolation="nearest",
        )
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03).set_label("µm")
        for r_mm, ls in ((0.5, "-"), (1.5, "--")):
            ax.add_patch(plt.Circle((cx, cy), r_mm, fill=False, color="white", lw=1.0, ls=ls))
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("A-scan (mm)")
    axes[0][0].set_ylabel("B-scan (mm) — sin orientación S/I/N/T verificada")
    fig.suptitle(
        "EXPERIMENTAL — derivado de contornos del fabricante; no es CMT clínico ni valor reportado por Topcon\n"
        "identidad anatómica sin confirmar en el binario (RETINA_3 = posible límite RPE / OS-RPE); círculos: 0,5 y 1,5 mm del centro geométrico",
        fontsize=9,
        color="#b00020",
    )
    st, cp = res.stats, res.comparison
    txt = (
        f"{source_name} · {res.device.get('model')} ROM {res.device.get('rom')} · axial {res.axial_scale['value_um_per_px']} µm/px (verificado) · procedencia: {res.measurement_provenance}\n"
        f"ILM→BM: válido {st['valid_fraction'] * 100:.1f} % · mediana {st.get('median_um')} µm · disco central {st['central_disc_r500um']['mean_um']} · anillo {st['ring_500_1500um']['mean_um']}"
    )
    if cp:
        d = cp["difference_um_ilm_bm_minus_ilm_rpe"]
        txt += (
            f"\nILM→RPE: mediana {res.rpe_stats.get('median_um')} µm · disco central {res.rpe_stats['central_disc_r500um']['mean_um']} · anillo {res.rpe_stats['ring_500_1500um']['mean_um']}"
            f"\nBM−RPE: mediana {d['median']} µm (p5–p95 {d['p5']}–{d['p95']}) · centro {cp['central_disc_r500um']['difference_um']} · anillo {cp['ring_500_1500um']['difference_um']} · correlación de mapas {cp['correlation_of_maps']}"
        )
    fig.text(0.01, 0.005, txt, fontsize=7, va="bottom", family="monospace")
    fig.tight_layout(rect=(0, 0.16, 1, 0.9))
    fig.savefig(out_png, dpi=150)
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
    if res.rpe_thickness_um is not None:
        np.save(out / "thickness_map_ilm_rpe_candidate.npy", res.rpe_thickness_um)
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
