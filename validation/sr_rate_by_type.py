#!/usr/bin/env python3
"""
Reads validation/pipeline_results.csv (LOCAL, per-file, no filenames -- has
"site", "study_type", and "sr_ok" columns from run_pipeline_batch.py) and
reports the SR generation rate broken down by acquisition type, scoped to
Site A only (site == "CUU"), so Table 3 can show a real per-type SR Generated
count instead of "*" (not separately tracked).

Run AFTER validation/run_pipeline_batch.py (no new data collection needed --
this just re-aggregates what that run already computed).

Usage:
    python sr_rate_by_type.py

Output (aggregate only, safe to paste back):
    sr_rate_by_type.txt
"""

import csv
from collections import defaultdict
from pathlib import Path

IN_CSV = Path(__file__).parent / "pipeline_results.csv"
OUT_TXT = Path(__file__).parent / "sr_rate_by_type.txt"

SITE_A_VALUE = "CUU"

# Table 3 row order, mapped to the study_type slug written by run_pipeline_batch.py
TABLE3_ORDER = [
    ("macular", "Macular cube 3D"),
    ("optic_nerve", "Optic nerve / RNFL"),
    ("angio", "Angiography (OCTA)"),
    ("wide_field", "Wide-field"),
    ("ultra_wide", "Ultra-wide-field"),
    ("biometry", "Biometry (B-OCT)"),
    ("fundus", "Fundus photo only"),
    ("hd_line", "HD Line Raster"),
]


def main():
    if not IN_CSV.exists():
        print(f"ERROR: {IN_CSV} not found. Run run_pipeline_batch.py first.")
        return

    with IN_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_type = defaultdict(lambda: {"total": 0, "sr_ok": 0})
    seen_types = set()
    other_site_a_types = set()

    for r in rows:
        if r.get("site", "").strip() != SITE_A_VALUE:
            continue
        stype = (r.get("study_type") or "").strip()
        seen_types.add(stype)
        by_type[stype]["total"] += 1
        if str(r.get("sr_ok", "")).strip().lower() in ("true", "1"):
            by_type[stype]["sr_ok"] += 1

    known_slugs = {slug for slug, _ in TABLE3_ORDER}
    other_site_a_types = seen_types - known_slugs

    lines = ["=" * 60, "SR generation rate by acquisition type -- Site A only", "=" * 60, ""]
    total_all, sr_all = 0, 0
    for slug, label in TABLE3_ORDER:
        n = by_type[slug]["total"]
        ok = by_type[slug]["sr_ok"]
        total_all += n
        sr_all += ok
        pct = 100 * ok / n if n else 0
        lines.append(f"  {label:<24} {ok}/{n} ({pct:.1f}%)")
    lines.append("")
    lines.append(
        f"  Total Site A: {sr_all}/{total_all} ({100*sr_all/total_all:.1f}%)" if total_all else "  Total Site A: 0/0"
    )

    if other_site_a_types:
        lines.append("")
        lines.append("  WARNING -- unrecognized study_type values found (not in Table 3 mapping):")
        for stype in sorted(other_site_a_types):
            n = by_type[stype]["total"]
            ok = by_type[stype]["sr_ok"]
            lines.append(f"    {stype!r}: {ok}/{n}")

    report = "\n".join(lines)
    print(report)
    OUT_TXT.write_text(report, encoding="utf-8")
    print(f"\nEscrito: {OUT_TXT}")


if __name__ == "__main__":
    main()
