#!/usr/bin/env python3
"""
Bland-Altman analysis: Transducin CMT vs native SOCT display CMT.

Usage:
    1. Fill in the 'native_soct_cmt' column in reference_pairs.csv
    2. python3 bland_altman_cmt.py

Outputs:
    - bland_altman_cmt.png   (Bland-Altman plot, print-ready 300 DPI)
    - bland_altman_stats.txt (mean bias, SD, 95% LoA, ICC, n)
"""

import csv
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

DATA_FILE   = Path(__file__).parent / "reference_pairs.csv"
PLOT_OUT    = Path(__file__).parent / "bland_altman_cmt.png"
STATS_OUT   = Path(__file__).parent / "bland_altman_stats.txt"


def icc_2_1(Y: np.ndarray) -> float:
    """ICC(2,1) two-way mixed, absolute agreement, single measures."""
    n = Y.shape[0]
    k = Y.shape[1]
    grand_mean = Y.mean()
    row_means  = Y.mean(axis=1)
    col_means  = Y.mean(axis=0)

    SS_r = k * np.sum((row_means - grand_mean) ** 2)
    SS_c = n * np.sum((col_means  - grand_mean) ** 2)
    SS_t = np.sum((Y - grand_mean) ** 2)
    SS_e = SS_t - SS_r - SS_c

    MS_r = SS_r / (n - 1)
    MS_e = SS_e / ((n - 1) * (k - 1))

    icc = (MS_r - MS_e) / (MS_r + (k - 1) * MS_e)
    return float(icc)


def main():
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found.")
        sys.exit(1)

    with open(DATA_FILE) as f:
        rows = list(csv.DictReader(f))

    # Only use rows where both values are present
    pairs = []
    for r in rows:
        try:
            t = float(r["transducin_cmt"])
            n = float(r["native_soct_cmt"])
            pairs.append((r["id"], t, n, r.get("device",""), r.get("soct_version","")))
        except (ValueError, KeyError):
            pass  # Skip rows missing native value

    if len(pairs) < 2:
        print("Not enough pairs with native_soct_cmt filled in.")
        sys.exit(1)

    ids      = [p[0] for p in pairs]
    trans    = np.array([p[1] for p in pairs])
    native   = np.array([p[2] for p in pairs])
    devices  = [p[3] for p in pairs]

    diff     = trans - native
    mean_val = (trans + native) / 2.0

    bias     = diff.mean()
    sd       = diff.std(ddof=1)
    loa_lo   = bias - 1.96 * sd
    loa_hi   = bias + 1.96 * sd
    icc      = icc_2_1(np.column_stack([trans, native]))

    n = len(pairs)

    # --- Plot ---
    fig, ax = plt.subplots(figsize=(7, 5))
    fig.patch.set_facecolor("white")

    # Color by device
    colors = {"REVO 60": "#2166ac", "REVO FC130": "#d73027"}
    default = "#666666"
    for xv, yv, dev in zip(mean_val, diff, devices):
        c = colors.get(dev, default)
        ax.scatter(xv, yv, color=c, s=50, zorder=3, edgecolors="white", linewidths=0.5)

    ax.axhline(bias,   color="#1a1a1a", lw=1.5, ls="-",  label=f"Bias = {bias:+.1f} µm")
    ax.axhline(loa_hi, color="#1a1a1a", lw=1.0, ls="--", label=f"+1.96 SD = {loa_hi:+.1f} µm")
    ax.axhline(loa_lo, color="#1a1a1a", lw=1.0, ls="--", label=f"−1.96 SD = {loa_lo:+.1f} µm")
    ax.axhline(0, color="#aaaaaa", lw=0.8, ls=":")

    # Shading
    ax.fill_between([mean_val.min()-5, mean_val.max()+5], loa_lo, loa_hi,
                    alpha=0.07, color="#1a1a1a")

    ax.set_xlabel("Mean of Transducin and native SOCT CMT (µm)", fontsize=11)
    ax.set_ylabel("Transducin − native SOCT CMT (µm)",           fontsize=11)
    ax.set_title(f"Bland-Altman: Transducin vs native SOCT CMT\n"
                 f"(n={n}; bias {bias:+.1f} µm; 95% LoA [{loa_lo:+.1f}, {loa_hi:+.1f}] µm; ICC {icc:.3f})",
                 fontsize=10)
    ax.set_xlim(mean_val.min() - 10, mean_val.max() + 10)
    ax.legend(fontsize=9, framealpha=0.9)

    # Legend for devices
    from matplotlib.lines import Line2D
    handles = [Line2D([0],[0], marker='o', color='w', markerfacecolor=v, markersize=8, label=k)
               for k,v in colors.items() if any(d == k for d in devices)]
    if handles:
        ax.legend(handles=handles + ax.get_legend_handles_labels()[0],
                  labels=[k for k in colors if any(d == k for d in devices)] +
                         ax.get_legend_handles_labels()[1],
                  fontsize=9, framealpha=0.9)

    plt.tight_layout()
    fig.savefig(PLOT_OUT, dpi=300, bbox_inches="tight")
    print(f"Plot saved: {PLOT_OUT}")

    # --- Stats ---
    stats = f"""Bland-Altman Analysis: Transducin CMT vs. native SOCT CMT
============================================================
n                   : {n}
Transducin CMT      : {trans.mean():.1f} ± {trans.std(ddof=1):.1f} µm (mean ± SD)
Native SOCT CMT     : {native.mean():.1f} ± {native.std(ddof=1):.1f} µm
Mean bias           : {bias:+.2f} µm  (Transducin − native)
SD of differences   : {sd:.2f} µm
95% LoA lower       : {loa_lo:+.2f} µm
95% LoA upper       : {loa_hi:+.2f} µm
ICC(2,1)            : {icc:.4f}
Within ±2 µm        : {(np.abs(diff) <= 2).sum()}/{n}
Within ±5 µm        : {(np.abs(diff) <= 5).sum()}/{n}
Within ±10 µm       : {(np.abs(diff) <= 10).sum()}/{n}

Per-scan:
{'ID':<14} {'Transducin':>11} {'Native':>8} {'Diff':>7}
{'-'*44}
"""
    for i, (id_, t, nat) in enumerate(zip(ids, trans, native)):
        stats += f"{id_:<14} {t:>11.1f} {nat:>8.1f} {t-nat:>+7.1f}\n"

    STATS_OUT.write_text(stats)
    print(stats)


if __name__ == "__main__":
    main()
