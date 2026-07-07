#!/usr/bin/env python3
# DEPRECATED — superseded by validation/corpus_audit.py (single source of truth,
# see its docstring). This script's hardcoded GROUPS folder list is the root
# cause of the historical 351->372->452 file-count drift across sessions —
# it silently misses any subfolder added after the list was written. Kept only
# for historical reference; do NOT run it for new numbers.
"""
Full-corpus batch processor — 351 .opt files across 5 site/device/version groups.

Produces: validation/full_corpus_results.csv
          validation/full_corpus_stats.txt   (paper-ready summary)
"""

import csv
import logging
import traceback
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

CORPUS_ROOT = Path("corpus")

GROUPS = [
    # (site_dir, site, device, soct_version)
    ("CUU/21.1.2", "CUU", "FC130", "21.1.2"),
    ("CUU/21.5.0", "CUU", "FC130", "21.5.0"),
    ("QRO/REVO 60 NUEVOS", "QRO", "REVO60", "11.5.x"),
    ("QRO/REVO 60 VIEJOS", "QRO", "REVO60", "11.5.x"),
    ("QRO/REVO130", "QRO", "REVO130", "11.5.x"),
]

OUT_CSV = Path(__file__).parent / "full_corpus_results.csv"
OUT_STATS = Path(__file__).parent / "full_corpus_stats.txt"

FIELDNAMES = [
    "id",
    "site",
    "device",
    "soct_version",
    "filename",
    "success",
    "acquisition_type",
    "laterality",
    "n_bscans",
    "n_ascans",
    "cmt_um",
    "sqi_mean",
    "etdrs_present",
    "rnfl_present",
    "biometry_present",
    "cdr",
    "noel_id",
    "study_date",
    "confidence",
    "error",
]


def process_file(opt_path: Path, site: str, device: str, soct_version: str, idx: int) -> dict:
    row = {
        "id": idx,
        "site": site,
        "device": device,
        "soct_version": soct_version,
        "filename": opt_path.name,
        "success": False,
        "acquisition_type": "",
        "laterality": "",
        "n_bscans": None,
        "n_ascans": None,
        "cmt_um": None,
        "sqi_mean": None,
        "etdrs_present": False,
        "rnfl_present": False,
        "biometry_present": False,
        "cdr": None,
        "noel_id": "",
        "study_date": "",
        "confidence": "",
        "error": "",
    }
    try:
        from transducin.opt_extractor import extract_from_opt
        from transducin.revo_opt_reader import parse_opt_chunks, parse_octparams

        cd = extract_from_opt(opt_path)

        row["success"] = True
        row["acquisition_type"] = cd.study_type or ""
        row["laterality"] = cd.laterality or ""
        row["noel_id"] = cd.noel_id or ""
        row["study_date"] = cd.study_date or ""
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

        # n_bscans / n_ascans from OCTPARAMS
        try:
            raw = opt_path.read_bytes()
            chunks = parse_opt_chunks(raw)
            params = parse_octparams(raw, chunks)
            row["n_bscans"] = params.get("n_bscans")
            row["n_ascans"] = params.get("n_ascans")
        except Exception:
            pass

    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        row["error"] += "\n" + traceback.format_exc()[-400:]

    return row


def main():
    rows = []
    idx = 1

    for rel_dir, site, device, soct in GROUPS:
        group_dir = CORPUS_ROOT / rel_dir
        files = sorted(group_dir.rglob("*.opt"))
        tag = f"[{site}/{device}/{soct}]"
        print(f"\n{tag} — {len(files)} archivos en {rel_dir}")
        for opt_path in files:
            row = process_file(opt_path, site, device, soct, idx)
            rows.append(row)
            status = "✓" if row["success"] else "✗"
            acq = row["acquisition_type"] or "?"
            cmt = f"CMT={row['cmt_um']:.0f}µm" if row["cmt_um"] else ""
            err = f" ERROR: {row['error'][:60]}" if row["error"] else ""
            print(f"  {status} {idx:>3}  {opt_path.name[:50]:<50}  {acq:<12}  {cmt}{err}")
            idx += 1

    # Write CSV
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    # Build stats
    total = len(rows)
    ok = [r for r in rows if r["success"]]
    failed = [r for r in rows if not r["success"]]
    n_ok = len(ok)

    acq_counts: dict[str, int] = {}
    for r in ok:
        acq_counts[r["acquisition_type"] or "unknown"] = acq_counts.get(r["acquisition_type"] or "unknown", 0) + 1

    macular = [r for r in ok if r["acquisition_type"] == "macular"]
    with_cmt = [r for r in macular if r["cmt_um"] is not None]
    cmt_vals = [r["cmt_um"] for r in with_cmt]
    cmt_mean = sum(cmt_vals) / len(cmt_vals) if cmt_vals else None

    device_stats: dict[str, dict] = {}
    for r in rows:
        key = f"{r['device']} {r['soct_version']}"
        if key not in device_stats:
            device_stats[key] = {"total": 0, "ok": 0}
        device_stats[key]["total"] += 1
        if r["success"]:
            device_stats[key]["ok"] += 1

    lat_ok = sum(1 for r in ok if r["laterality"])
    noel_ok = sum(1 for r in ok if r["noel_id"])
    conf_ok = sum(1 for r in ok if r["confidence"] == "confirmed")

    with_rnfl = sum(1 for r in ok if r["rnfl_present"])
    with_cdr = sum(1 for r in ok if r["cdr"] is not None)
    with_bio = sum(1 for r in ok if r["biometry_present"])

    lines = [
        "=" * 65,
        "TRANSDUCIN — FULL CORPUS BATCH RESULTS",
        "=" * 65,
        f"Total archivos             : {total}",
        f"Parse success              : {n_ok}/{total} ({100*n_ok/total:.1f}%)",
        f"Parse failed               : {len(failed)}",
        "",
        "── Por dispositivo / versión SOCT ──",
    ]
    for key, s in device_stats.items():
        pct = 100 * s["ok"] / s["total"] if s["total"] else 0
        lines.append(f"  {key:<25}  {s['ok']}/{s['total']} ({pct:.1f}%)")

    lines += [
        "",
        "── Tipos de adquisición (archivos exitosos) ──",
    ]
    for acq, count in sorted(acq_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {acq:<20}  {count}")

    lines += [
        "",
        "── Métricas clínicas extraídas ──",
        f"  Lateralidad conocida       : {lat_ok}/{n_ok} ({100*lat_ok/n_ok:.1f}%)" if n_ok else "",
        f"  NOEL ID resuelto           : {noel_ok}/{n_ok} ({100*noel_ok/n_ok:.1f}%)" if n_ok else "",
        f"  Confianza 'confirmed'      : {conf_ok}/{n_ok} ({100*conf_ok/n_ok:.1f}%)" if n_ok else "",
        f"  Maculares procesados       : {len(macular)}",
        f"  CMT extraído               : {len(with_cmt)}/{len(macular)} ({100*len(with_cmt)/len(macular):.1f}%)"
        if macular
        else "  CMT: n/a",
        f"  CMT media ± (ver CSV)      : {cmt_mean:.1f} µm" if cmt_mean else "  CMT media: n/a",
        f"  RNFL peripapillar          : {with_rnfl}",
        f"  C/D ratio                  : {with_cdr}",
        f"  Biometría                  : {with_bio}",
    ]

    if failed:
        lines += ["", f"── Archivos fallidos ({len(failed)}) ──"]
        for r in failed:
            lines.append(f"  [{r['site']}/{r['device']}] {r['filename']}")
            lines.append(f"    {r['error'][:100]}")

    lines += ["", f"CSV: {OUT_CSV}"]

    report = "\n".join(lines)
    print("\n" + report)
    OUT_STATS.write_text(report, encoding="utf-8")
    print(f"\nStats guardadas: {OUT_STATS}")


if __name__ == "__main__":
    main()
