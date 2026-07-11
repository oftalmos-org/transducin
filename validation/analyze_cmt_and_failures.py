#!/usr/bin/env python3
"""
Reads corpus_audit_results.csv (LOCAL, per-file, has noel_id/site/device/
soct_version/acquisition_type/cmt_um/rnfl_present/biometry_present/error --
no raw filenames) and produces, all safe to paste back into a Claude
conversation:

  1. Kruskal-Wallis H/p for macular CMT, in two framings:
       - 4 groups: CUU 21.1.2, CUU 21.5.0, QRO REVO130, QRO REVO60
       - 3 groups: Site A combined (21.1.2+21.5.0), QRO REVO130, QRO REVO60
  2. A tally of *error type* (not filename) for macular files with missing
     CMT, broken down by group.
  3. Same success/fail tally for optic_nerve (success = rnfl_present) and
     biometry (success = biometry_present), broken down by group -- to
     explain the SR shortfall not accounted for by macular CMT failures
     alone (run_pipeline_batch.py: 277/475 SR OK vs 371 measurement-capable
     files; macular CMT failures alone only explain 78 of that gap).

Run AFTER validation/corpus_audit.py.

Usage:
    python analyze_cmt_and_failures.py

Outputs (next to this script, aggregate only):
    cmt_kruskal_stats.txt
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

from scipy import stats as scipy_stats

IN_CSV = Path(__file__).parent / "corpus_audit_results.csv"
OUT_TXT = Path(__file__).parent / "cmt_kruskal_stats.txt"


def group_key(row):
    return (row["site"], row["device"], row["soct_version"])


def main():
    if not IN_CSV.exists():
        print(f"ERROR: {IN_CSV} not found. Run corpus_audit.py first.")
        return

    with IN_CSV.open(encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    macular = [r for r in all_rows if r.get("acquisition_type") == "macular"]

    cmt_by_group = defaultdict(list)
    failures_by_group = defaultdict(list)
    for row in macular:
        g = group_key(row)
        cmt = row.get("cmt_um", "")
        if cmt:
            try:
                cmt_by_group[g].append(float(cmt))
                continue
            except ValueError:
                pass
        failures_by_group[g].append(row.get("error", "") or "(sin mensaje de error)")

    lines = ["=" * 70, "CMT Kruskal-Wallis + failure-mode tally", "=" * 70, ""]

    lines.append("-- n y CMT-valido por grupo --")
    for g in sorted(cmt_by_group.keys() | failures_by_group.keys()):
        n_ok = len(cmt_by_group.get(g, []))
        n_fail = len(failures_by_group.get(g, []))
        lines.append(f"  {g}: valido={n_ok}  fallido={n_fail}  total={n_ok + n_fail}")

    # 4-group Kruskal-Wallis
    groups_4 = [v for v in cmt_by_group.values() if len(v) >= 2]
    if len(groups_4) >= 2:
        h, p = scipy_stats.kruskal(*groups_4)
        lines.append(f"\n-- Kruskal-Wallis, 4 grupos separados -- H={h:.3f} p={p:.4f}")
    else:
        lines.append("\n-- Kruskal-Wallis 4 grupos: no hay suficientes grupos con n>=2 --")

    # 3-group: combine both CUU (Site A) subgroups
    site_a_combined = []
    other_groups = []
    for g, vals in cmt_by_group.items():
        if g[0] == "CUU":
            site_a_combined.extend(vals)
        else:
            other_groups.append(vals)
    groups_3 = [site_a_combined] + [v for v in other_groups if len(v) >= 2]
    if site_a_combined and len(groups_3) >= 2:
        h3, p3 = scipy_stats.kruskal(*groups_3)
        lines.append(f"-- Kruskal-Wallis, 3 grupos (Site A combinado) -- H={h3:.3f} p={p3:.4f}")

    lines.append("\n-- Tally de tipo de error (fallos de CMT), por grupo --")
    lines.append("   (mensaje de error truncado, SIN nombre de archivo)")
    for g in sorted(failures_by_group.keys()):
        lines.append(f"\n  Grupo {g} ({len(failures_by_group[g])} fallos):")
        # Normalize: take just the exception-type prefix before the first ':'
        types = Counter(err.split(":")[0].split("|")[0].strip() for err in failures_by_group[g])
        for err_type, count in types.most_common(10):
            lines.append(f"    {count:>3}x  {err_type}")

    # -- optic_nerve (success = rnfl_present) and biometry (success = biometry_present) --
    def analyze_type(acquisition_type, success_field, section_title):
        rows_t = [r for r in all_rows if r.get("acquisition_type") == acquisition_type]
        ok_by_group = defaultdict(int)
        fail_by_group = defaultdict(list)
        for row in rows_t:
            g = group_key(row)
            if str(row.get(success_field, "")).strip().lower() in ("true", "1", "yes"):
                ok_by_group[g] += 1
            else:
                fail_by_group[g].append(row.get("error", "") or "(sin mensaje de error)")

        out = [f"\n{'=' * 70}", section_title, "=" * 70]
        for g in sorted(ok_by_group.keys() | fail_by_group.keys()):
            n_ok = ok_by_group.get(g, 0)
            n_fail = len(fail_by_group.get(g, []))
            out.append(f"  {g}: exitoso={n_ok}  fallido={n_fail}  total={n_ok + n_fail}")
        total_ok = sum(ok_by_group.values())
        total_fail = sum(len(v) for v in fail_by_group.values())
        out.append(f"  TOTAL: exitoso={total_ok}  fallido={total_fail}  ({acquisition_type})")
        if any(fail_by_group.values()):
            out.append("\n  -- tipo de error, por grupo --")
            for g in sorted(fail_by_group.keys()):
                if not fail_by_group[g]:
                    continue
                out.append(f"\n  Grupo {g} ({len(fail_by_group[g])} fallos):")
                types = Counter(err.split(":")[0].split("|")[0].strip() for err in fail_by_group[g])
                for err_type, count in types.most_common(10):
                    out.append(f"    {count:>3}x  {err_type}")
        return out

    lines += analyze_type("optic_nerve", "rnfl_present", "OPTIC NERVE -- exito/fallo (rnfl_present) por grupo")
    lines += analyze_type("biometry", "biometry_present", "BIOMETRY -- exito/fallo (biometry_present) por grupo")

    # macular's OWN etdrs/mRNFL (cd.rnfl computed via compute_rnfl_sectors,
    # a DIFFERENT code path than optic_nerve's compute_peripapillary_rnfl --
    # same "rnfl_present" column, different acquisition_type filter) --
    # added to check the real effect of the 2026-07 opt_extractor.py fix
    # (guarded None-formatting in ETDRS/mRNFL/mGCIPL notes, which used to
    # crash and abort this block before etdrs/rnfl/gcl_ipl were computed).
    lines += analyze_type("macular", "etdrs_present", "MACULAR -- exito/fallo (etdrs_present) por grupo")
    lines += analyze_type("macular", "rnfl_present", "MACULAR -- exito/fallo (mRNFL / rnfl_present) por grupo")

    report = "\n".join(lines)
    print(report)
    OUT_TXT.write_text(report, encoding="utf-8")
    print(f"\nEscrito: {OUT_TXT}")


if __name__ == "__main__":
    main()
