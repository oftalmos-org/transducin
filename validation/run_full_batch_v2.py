#!/usr/bin/env python3
"""
Full-corpus batch processor v2 — auto-discovers every .opt file recursively.

Why v2: v1 (run_full_batch.py) used a hardcoded GROUPS list of subfolders.
That list went stale as the corpus grew (351 -> 372 -> 452 across sessions),
which is the root cause of the "150 of 372" vs Table 3's 452 mismatch in the
paper. This version walks CORPUS_ROOT recursively so it can never drift out
of sync with what's actually on disk again.

Usage:
    python run_full_batch_v2.py [CORPUS_ROOT]

If CORPUS_ROOT is omitted, edit the default below. Point it at the directory
that directly contains the CUU/ and QRO/ site folders (i.e. the same root
used by run_full_batch.py / run_pipeline_batch.py).

Produces (next to this script):
    full_corpus_results_v2.csv   — one row per file
    full_corpus_stats_v2.txt     — paper-ready summary (send this back, no PHI)

No patient names, dates, or PHI appear in the stats file — only aggregate
counts and NOEL IDs (already pseudonymized per protocol). Do not paste the
CSV back; the stats.txt is sufficient and safe to share.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_CORPUS_ROOT = Path(r"C:\Developer\RetinaDev\proyectos\dicom opt")

OUT_CSV = Path(__file__).parent / "full_corpus_results_v2.csv"
OUT_STATS = Path(__file__).parent / "full_corpus_stats_v2.txt"

FIELDNAMES = [
    "id",
    "site",
    "device",
    "soct_version",
    "rel_path",
    "success",
    "acquisition_type",
    "laterality",
    "cmt_um",
    "sqi_mean",
    "etdrs_present",
    "rnfl_present",
    "biometry_present",
    "cdr",
    "noel_id",
    "confidence",
    "error",
]


def classify(rel_parts):
    """Infer site/device/soct_version from the relative path, same
    convention as v1's GROUPS table but derived dynamically so new
    subfolders (e.g. a future CUU/21.6.0) are picked up automatically."""
    joined = "/".join(rel_parts).upper()
    site = rel_parts[0].upper() if rel_parts else "UNKNOWN"

    if "REVO130" in joined:
        device = "REVO130"
    elif "REVO 60" in joined or "REVO60" in joined or "REVO_60" in joined:
        device = "REVO60"
    elif "FC130" in joined or site == "CUU":
        device = "FC130"
    else:
        device = "UNKNOWN"

    if "21.5.0" in joined:
        soct = "21.5.0"
    elif "21.1.2" in joined or site == "CUU":
        soct = "21.1.2"
    else:
        soct = "11.5.x"

    return site, device, soct


def process_file(opt_path: Path, rel_parts, idx: int) -> dict:
    site, device, soct = classify(rel_parts)
    row = {
        "id": idx,
        "site": site,
        "device": device,
        "soct_version": soct,
        "rel_path": "/".join(rel_parts),
        "success": False,
        "acquisition_type": "",
        "laterality": "",
        "cmt_um": None,
        "sqi_mean": None,
        "etdrs_present": False,
        "rnfl_present": False,
        "biometry_present": False,
        "cdr": None,
        "noel_id": "",
        "confidence": "",
        "error": "",
    }
    try:
        from transducin.opt_extractor import extract_from_opt

        cd = extract_from_opt(opt_path)

        row["success"] = True
        row["acquisition_type"] = cd.study_type or ""
        row["laterality"] = cd.laterality or ""
        row["noel_id"] = cd.noel_id or ""
        row["confidence"] = cd.extraction_confidence or ""
        if cd.cmt_um is not None:
            row["cmt_um"] = round(float(cd.cmt_um), 1)
        if cd.sqi_mean is not None:
            row["sqi_mean"] = round(float(cd.sqi_mean), 3)
        if cd.etdrs_grid is not None and cd.etdrs_grid.has_data():
            row["etdrs_present"] = True
        if cd.rnfl is not None and cd.rnfl.has_data():
            row["rnfl_present"] = True
        if cd.axial_length_mm is not None:
            row["biometry_present"] = True
        if cd.cup_disc_ratio is not None:
            row["cdr"] = round(float(cd.cup_disc_ratio), 3)
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return row


def main():
    corpus_root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CORPUS_ROOT
    if not corpus_root.exists():
        print(f"ERROR: corpus root not found: {corpus_root}")
        print('Pass the correct path as an argument: python run_full_batch_v2.py "<path>"')
        sys.exit(1)

    files = sorted(corpus_root.rglob("*.opt"))
    print(f"Descubiertos {len(files)} archivos .opt bajo {corpus_root}\n")

    rows = []
    for idx, opt_path in enumerate(files, 1):
        rel_parts = opt_path.relative_to(corpus_root).parent.parts
        row = process_file(opt_path, rel_parts, idx)
        rows.append(row)
        status = "OK" if row["success"] else "FAIL"
        acq = row["acquisition_type"] or "?"
        print(f"  [{idx:>4}/{len(files)}] {status:<4} {row['site']}/{row['device']}/{row['soct_version']:<8} {acq}")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    ok = [r for r in rows if r["success"]]
    failed = [r for r in rows if not r["success"]]

    acq_counts = {}
    for r in ok:
        acq_counts[r["acquisition_type"] or "unknown"] = acq_counts.get(r["acquisition_type"] or "unknown", 0) + 1

    macular = [r for r in ok if r["acquisition_type"] == "macular"]
    with_cmt = [r for r in macular if r["cmt_um"] is not None]

    group_counts = {}
    group_cmt = {}
    for r in macular:
        key = f"{r['site']} {r['device']} {r['soct_version']}"
        group_counts[key] = group_counts.get(key, 0) + 1
        if r["cmt_um"] is not None:
            group_cmt[key] = group_cmt.get(key, 0) + 1

    lines = [
        "=" * 65,
        "TRANSDUCIN — FULL CORPUS BATCH RESULTS (v2, auto-discovered)",
        "=" * 65,
        f"Corpus root                : {corpus_root}",
        f"Total archivos              : {total}",
        f"Parse success               : {len(ok)}/{total} ({100*len(ok)/total:.1f}%)" if total else "n/a",
        f"Parse failed                : {len(failed)}",
        "",
        "-- Tipos de adquisicion (archivos exitosos) --",
    ]
    for acq, count in sorted(acq_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {acq:<20}  {count}")

    lines += [
        "",
        "-- Maculares por grupo site/device/version (para Figura 3) --",
    ]
    for key in sorted(group_counts):
        n_grp = group_counts[key]
        n_cmt = group_cmt.get(key, 0)
        lines.append(f"  {key:<25}  n={n_grp:<4}  CMT valido={n_cmt}")

    lines += [
        "",
        f"Maculares totales           : {len(macular)}",
        f"CMT extraido                : {len(with_cmt)}/{len(macular)} ({100*len(with_cmt)/len(macular):.1f}%)"
        if macular
        else "n/a",
    ]

    if failed:
        lines += ["", f"-- Archivos fallidos ({len(failed)}), sin nombres de paciente --"]
        for r in failed:
            lines.append(f"  [{r['site']}/{r['device']}] {r['rel_path']}: {r['error'][:100]}")

    lines += ["", f"CSV: {OUT_CSV}"]

    report = "\n".join(lines)
    print("\n" + report)
    OUT_STATS.write_text(report, encoding="utf-8")
    print(f"\nStats guardadas: {OUT_STATS}")


if __name__ == "__main__":
    main()
