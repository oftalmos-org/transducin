#!/usr/bin/env python3
"""
Regenerate Figure 3 (CMT distribution by device/SOCT version) from the
current, auto-discovered corpus — replaces the stale plot that was built
from an intermediate ~372-file run never reconciled with Table 3's 452.

Run AFTER run_full_batch_v2.py (reads its output CSV).

Usage:
    python plot_cmt_distribution.py

Outputs:
    fig3_cmt_distribution_v2.png
    fig3_stats_v2.txt   (group n's + Kruskal-Wallis H/p — send this back)
"""

import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats as scipy_stats

IN_CSV = Path(__file__).parent / "full_corpus_results_v2.csv"
PLOT_OUT = Path(__file__).parent / "fig3_cmt_distribution_v2.png"
STATS_OUT = Path(__file__).parent / "fig3_stats_v2.txt"


def main():
    if not IN_CSV.exists():
        print(f"ERROR: {IN_CSV} not found. Run run_full_batch_v2.py first.")
        sys.exit(1)

    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    groups = {}
    for r in rows:
        if r["acquisition_type"] != "macular" or not r["cmt_um"]:
            continue
        key = f"{r['site']} {r['device']} {r['soct_version']}"
        groups.setdefault(key, []).append(float(r["cmt_um"]))

    if len(groups) < 2:
        print("Fewer than 2 groups with CMT data — check full_corpus_results_v2.csv")
        sys.exit(1)

    labels = sorted(groups)
    data = [groups[k] for k in labels]
    ns = [len(d) for d in data]

    h_stat, p_val = scipy_stats.kruskal(*data)

    fig, ax = plt.subplots(figsize=(7.5, 5))
    fig.patch.set_facecolor("white")
    bp = ax.boxplot(
        data, tick_labels=[f"{lbl}\n(n={n})" for lbl, n in zip(labels, ns)], patch_artist=True, showmeans=False
    )
    for patch in bp["boxes"]:
        patch.set_facecolor("#a6bddb")
        patch.set_alpha(0.7)

    rng = np.random.default_rng(0)
    for i, d in enumerate(data, start=1):
        jitter = rng.uniform(-0.08, 0.08, size=len(d))
        ax.scatter(
            np.full(len(d), i) + jitter,
            d,
            s=14,
            color="#2166ac",
            alpha=0.6,
            zorder=3,
            edgecolors="white",
            linewidths=0.3,
        )

    ax.axhline(250, color="#666666", ls="--", lw=0.8)
    ax.axhline(300, color="#666666", ls=":", lw=0.8)
    ax.set_ylabel("Central macular thickness (µm)", fontsize=11)
    ax.set_title(
        f"CMT distribution by device model and SOCT software version\n" f"Kruskal-Wallis H={h_stat:.2f}, p={p_val:.3f}",
        fontsize=10,
    )
    plt.tight_layout()
    fig.savefig(PLOT_OUT, dpi=300, bbox_inches="tight")
    print(f"Plot saved: {PLOT_OUT}")

    lines = [
        "CMT distribution — regenerated from full_corpus_results_v2.csv",
        "=" * 60,
    ]
    for lbl, n in zip(labels, ns):
        lines.append(f"  {lbl:<25} n={n}")
    lines += [
        "",
        f"Total CMT-valid macular scans : {sum(ns)}",
        f"Kruskal-Wallis H              : {h_stat:.3f}",
        f"p-value                       : {p_val:.4f}",
        f"Significant (p<0.05)          : {'yes' if p_val < 0.05 else 'no'}",
    ]
    report = "\n".join(lines)
    STATS_OUT.write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
