#!/usr/bin/env python3
"""
Regenerate Figure 3 (CMT distribution by device/SOCT version) from
corpus_audit.py's output -- the current single source of truth -- for the
expanded 475-file corpus (four real device/version groups now that
CUU 21.5.0 grew from n=2 to n=28 valid CMT).

Run AFTER validation/corpus_audit.py.

Usage:
    python plot_cmt_distribution_v3.py

Outputs:
    fig3_cmt_distribution_v3.png
    fig3_stats_v3.txt   (group n's + Kruskal-Wallis H/p -- send this back)
"""

import csv
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats as scipy_stats

IN_CSV = Path(__file__).parent / "corpus_audit_results.csv"
PLOT_OUT = Path(__file__).parent / "fig3_cmt_distribution_v3.png"
STATS_OUT = Path(__file__).parent / "fig3_stats_v3.txt"

# Display order/labels for the four groups (matches Table 3/4b naming)
LABEL_MAP = {
    ("CUU", "FC130", "21.1.2"): "Site A FC130\nSOCT 21.1.2",
    ("CUU", "FC130", "21.5.0"): "Site A FC130\nSOCT 21.5.0",
    ("QRO", "REVO60", "11.5.x"): "Site B REVO60\nSOCT 11.5.x",
    ("QRO", "REVO130", "11.5.x"): "Site B REVO130\nSOCT 11.5.x",
}
ORDER = list(LABEL_MAP.keys())


def main():
    if not IN_CSV.exists():
        print(f"ERROR: {IN_CSV} not found. Run corpus_audit.py first.")
        sys.exit(1)

    with open(IN_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    groups = {}
    for r in rows:
        if r["acquisition_type"] != "macular" or not r["cmt_um"]:
            continue
        key = (r["site"], r["device"], r["soct_version"])
        groups.setdefault(key, []).append(float(r["cmt_um"]))

    keys = [k for k in ORDER if k in groups] + [k for k in groups if k not in ORDER]
    if len(keys) < 2:
        print("Fewer than 2 groups with CMT data -- check corpus_audit_results.csv")
        sys.exit(1)

    labels = [LABEL_MAP.get(k, " ".join(k)) for k in keys]
    data = [groups[k] for k in keys]
    ns = [len(d) for d in data]

    h_stat, p_val = scipy_stats.kruskal(*data)

    # Hockney-inspired palette: pool cerulean, coral, sunny yellow, grass green
    HOCKNEY = ["#00A9CE", "#F2704F", "#F4C542", "#6FB98F"]
    HOCKNEY_DARK = ["#00728A", "#B84A2C", "#B8892A", "#3F7D5C"]

    fig, ax = plt.subplots(figsize=(8, 5))
    fig.patch.set_facecolor("white")
    bp = ax.boxplot(
        data, tick_labels=[f"{lbl}\n(n={n})" for lbl, n in zip(labels, ns)], patch_artist=True, showmeans=False
    )
    for i, patch in enumerate(bp["boxes"]):
        patch.set_facecolor(HOCKNEY[i % len(HOCKNEY)])
        patch.set_alpha(0.75)
        patch.set_edgecolor(HOCKNEY_DARK[i % len(HOCKNEY_DARK)])
        patch.set_linewidth(1.3)
    for i, median in enumerate(bp["medians"]):
        median.set_color(HOCKNEY_DARK[i % len(HOCKNEY_DARK)])
        median.set_linewidth(1.8)

    rng = np.random.default_rng(0)
    for i, d in enumerate(data, start=1):
        jitter = rng.uniform(-0.08, 0.08, size=len(d))
        ax.scatter(
            np.full(len(d), i) + jitter,
            d,
            s=16,
            color=HOCKNEY_DARK[(i - 1) % len(HOCKNEY_DARK)],
            alpha=0.7,
            zorder=3,
            edgecolors="white",
            linewidths=0.4,
        )

    ax.axhline(250, color="#8A6FA8", ls="--", lw=1.0)
    ax.axhline(300, color="#8A6FA8", ls=":", lw=1.0)
    ax.set_facecolor("#FBFAF6")
    ax.set_ylabel("Central macular thickness (µm)", fontsize=11)
    ax.set_title(
        f"CMT distribution by device model and SOCT software version\n" f"Kruskal-Wallis H={h_stat:.3f}, p={p_val:.4f}",
        fontsize=10,
    )
    plt.tight_layout()
    fig.savefig(PLOT_OUT, dpi=300, bbox_inches="tight")
    fig.savefig(PLOT_OUT.with_suffix(".svg"), bbox_inches="tight")
    print(f"Plot saved: {PLOT_OUT}")

    lines = [
        "CMT distribution -- regenerated from corpus_audit_results.csv (475-file corpus)",
        "=" * 70,
    ]
    for lbl, n in zip(labels, ns):
        lines.append(f"  {lbl.replace(chr(10), ' '):<28} n={n}")
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
