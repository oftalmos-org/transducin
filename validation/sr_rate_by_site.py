#!/usr/bin/env python3
"""
Reads validation/pipeline_results.csv (LOCAL, per-file, no filenames -- already
has "site" and "sr_ok" columns from run_pipeline_batch.py) and reports the SR
generation rate broken down by site, so Table 3 (Site A only) can cite a
scope-consistent number instead of the combined 277/475 corpus-wide figure.

Run AFTER validation/run_pipeline_batch.py (no new data collection needed --
this just re-aggregates what that run already computed).

Usage:
    python sr_rate_by_site.py

Output (aggregate only, safe to paste back):
    sr_rate_by_site.txt
"""

import csv
from collections import defaultdict
from pathlib import Path

IN_CSV = Path(__file__).parent / "pipeline_results.csv"
OUT_TXT = Path(__file__).parent / "sr_rate_by_site.txt"


def main():
    if not IN_CSV.exists():
        print(f"ERROR: {IN_CSV} not found. Run run_pipeline_batch.py first.")
        return

    with IN_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_site = defaultdict(lambda: {"total": 0, "sr_ok": 0})
    for r in rows:
        site = r.get("site", "?")
        by_site[site]["total"] += 1
        if str(r.get("sr_ok", "")).strip().lower() in ("true", "1"):
            by_site[site]["sr_ok"] += 1

    lines = ["=" * 60, "SR generation rate by site", "=" * 60, ""]
    total_all, sr_all = 0, 0
    for site in sorted(by_site):
        n = by_site[site]["total"]
        ok = by_site[site]["sr_ok"]
        total_all += n
        sr_all += ok
        pct = 100 * ok / n if n else 0
        lines.append(f"  Site {site}: {ok}/{n} ({pct:.1f}%)")
    lines.append("")
    lines.append(f"  Combined (both sites): {sr_all}/{total_all} ({100*sr_all/total_all:.1f}%)")

    report = "\n".join(lines)
    print(report)
    OUT_TXT.write_text(report, encoding="utf-8")
    print(f"\nEscrito: {OUT_TXT}")


if __name__ == "__main__":
    main()
