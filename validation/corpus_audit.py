#!/usr/bin/env python3
"""
corpus_audit.py -- single source of truth for the Transducin validation corpus.

Supersedes run_full_batch_v1_DEPRECATED.py (hardcoded GROUPS list -- root
cause of the historical 351->372->452 file-count drift), run_full_batch_v2.py,
table3_site_breakdown.py, scan_type_counter_DEPRECATED.py and
run_site_b_DEPRECATED.py (narrower pilot-subset scope -- root cause of the
"19 vs 88" Site B confusion) as the one script that produces corpus counts.

Design:
  - One full recursive walk of CORPUS_ROOT (`rglob("*.opt")`) -- no folder
    allowlist, so it can never go stale as the corpus grows.
  - Two-tier parsing to bound memory regardless of corpus size (.opt files
    run up to ~800 MB):
      Tier 1 (every file): reads only a bounded byte budget (default 800KB)
        and classifies acquisition_type from chunk presence + OCTPARAMS
        dimensions -- the same signals transducin.opt_extractor.extract_from_opt
        itself uses for classification, just without the segmentation/pixel
        read. This alone is authoritative for type in most cases.
      Tier 2 (only when needed): calls the real, unmodified
        transducin.opt_extractor.extract_from_opt() to get quantitative
        measurements (CMT/RNFL/biometry/etc.) -- only for files whose tier-1
        type needs them, or when tier-1 was inconclusive (fail-safe toward
        running tier 2 rather than silently skipping a real macular file).
  - Results are streamed to CSV row-by-row (never buffered in memory) and
    aggregated into a single summary (txt + json) that replaces
    full_corpus_stats_v2.txt + table3_site_breakdown.txt + fig3_stats_v2.txt.

Privacy: no filename or patient name ever appears in the summary or the
per-file CSV -- only NOEL_ID (already a pseudonym per protocol) and the
relative folder path.

Usage:
    python corpus_audit.py [CORPUS_ROOT] [--light-budget BYTES]

Produces (next to this script):
    corpus_audit_summary.txt / .json  -- aggregate, PHI-free. Safe to paste
                                          back into a Claude conversation.
    corpus_audit_results.csv          -- one row per file (NOEL_ID + relative
                                          folder). LOCAL ONLY -- do not paste
                                          this into chat.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from transducin.opt_extractor import extract_from_opt, build_noel_index
from transducin.revo_opt_reader import parse_opt_chunks, parse_octparams, extract_study_uid

DEFAULT_CORPUS_ROOT = Path(r"corpus")
DEFAULT_LIGHT_BUDGET_BYTES = 800_000

OUT_CSV = Path(__file__).parent / "corpus_audit_results.csv"
OUT_SUMMARY_TXT = Path(__file__).parent / "corpus_audit_summary.txt"
OUT_SUMMARY_JSON = Path(__file__).parent / "corpus_audit_summary.json"

FIELDNAMES = [
    "noel_id",
    "site",
    "device",
    "soct_version",
    "rel_folder",
    "file_size_mb",
    "tier",
    "tier1_type",
    "acquisition_type",
    "laterality",
    "success",
    "cmt_um",
    "sqi_mean",
    "etdrs_present",
    "rnfl_present",
    "biometry_present",
    "cdr",
    "device_uid_signal",
    "confidence",
    "error",
]

# Types that need real quantitative measurements -> escalate to tier 2.
QUANTITATIVE_TYPES = {"macular", "optic_nerve", "rnfl", "biometry"}
# Types with no extractable measurements -> tier-1 classification is enough.
SKIP_HEAVY_TYPES = {"angio", "wide_field", "ultra_wide", "fundus", "hd_line", "anterior_segment"}

# Same keyword map as transducin.opt_extractor._FILENAME_KEYWORDS -- kept as
# a local copy since that name is a private module symbol not meant for
# cross-module import.
_FILENAME_KEYWORDS = {
    "biometr": "biometry",
    "bmetr": "biometry",
    "calculo": "biometry",
    "lio": "biometry",
    "topo": "anterior_segment",
    "topogr": "anterior_segment",
    "macula": "macular",
    "nervio": "optic_nerve",
    "nerve": "optic_nerve",
    "optic": "optic_nerve",
    "disco": "optic_nerve",
    "disc": "optic_nerve",
    "angio": "angio",
    "wide": "wide_field",
    "ultra": "ultra_wide",
}

_NOEL_PREFIX = re.compile(r"^([A-Z]{3,4}\d{8})_", re.IGNORECASE)


def classify_path(rel_parts):
    """Site/device/soct_version from folder-name convention. Ported verbatim
    from run_full_batch_v2.py's classify() -- this is now the one place this
    logic lives."""
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


def classify_acquisition_type_light(filename_stem, chunks, params):
    """Provisional acquisition type from OCTPARAMS dimensions (most
    authoritative -- mirrors opt_extractor's own dimension-based override),
    then chunk presence, then filename keywords as last resort. Needs no
    B-scan/segmentation data, only what tier 1's bounded read already has.

    Returns (type_or_None, confident: bool). confident=False means none of
    the signals were available/conclusive within the byte budget -- caller
    should escalate to the heavy tier rather than guess.
    """
    n_frames = params.get("n_bscans")
    n_ascans = params.get("n_ascans")

    if n_frames is not None and n_ascans is not None:
        if n_frames == 192 and n_ascans == 640:
            return "optic_nerve", True
        if n_frames == 112 and n_ascans == 512:
            return "optic_nerve", True
        if n_frames in (168, 256) and n_ascans in (768, 1024):
            return "macular", True
        if n_frames == 85 and n_ascans == 640:
            return "macular", True
        if n_frames in (304, 319, 320) and n_ascans <= 320:
            return "angio", True
        if n_ascans >= 4096:
            return "ultra_wide", True
        if n_frames <= 8 and n_ascans >= 1024:
            return "wide_field", True
        if n_frames <= 25 and n_ascans >= 512:
            return "hd_line", True

    if "ANGPRV" in chunks:
        return "angio", True
    if "DMARKERS" in chunks:
        known_macular_dims = (
            n_frames is not None
            and n_ascans is not None
            and ((n_frames in (168, 256) and n_ascans in (768, 1024)) or (n_frames == 85 and n_ascans == 640))
        )
        return ("macular" if known_macular_dims else "optic_nerve"), True
    if "EYE" in chunks:
        return ("macular" if (n_frames or 0) >= 100 else "optic_nerve"), True
    if "FNDSRECO" in chunks:
        return "wide_field", True

    stem_lower = filename_stem.lower()
    for kw, typ in _FILENAME_KEYWORDS.items():
        if kw in stem_lower:
            return typ, True

    return None, False


def needs_heavy_tier(tier1_type, tier1_confident):
    if not tier1_confident:
        return True  # inconclusive -> fail-safe toward the heavy tier
    if tier1_type in QUANTITATIVE_TYPES:
        return True
    if tier1_type in SKIP_HEAVY_TYPES:
        return False
    return True  # unknown/ambiguous type -> fail-safe toward the heavy tier


def audit_file(opt_path, corpus_root, light_budget, noel_index):
    rel_parts = opt_path.relative_to(corpus_root).parent.parts
    site, device, soct_version = classify_path(rel_parts)

    row = {
        "noel_id": "",
        "site": site,
        "device": device,
        "soct_version": soct_version,
        "rel_folder": "/".join(rel_parts),
        "file_size_mb": round(opt_path.stat().st_size / 1024 / 1024, 1),
        "tier": 1,
        "tier1_type": "",
        "acquisition_type": "",
        "laterality": "",
        "success": False,
        "cmt_um": None,
        "sqi_mean": None,
        "etdrs_present": False,
        "rnfl_present": False,
        "biometry_present": False,
        "cdr": None,
        "device_uid_signal": "",
        "confidence": "",
        "error": "",
    }

    m = _NOEL_PREFIX.match(opt_path.name)
    if m:
        row["noel_id"] = m.group(1).upper()

    try:
        with open(opt_path, "rb") as fh:
            light_data = fh.read(light_budget)
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return row

    tier1_type, tier1_confident = None, False
    try:
        chunks = parse_opt_chunks(light_data)
        params = parse_octparams(light_data, chunks)
        tier1_type, tier1_confident = classify_acquisition_type_light(opt_path.stem, chunks, params)
        row["tier1_type"] = tier1_type or ""
        uid = extract_study_uid(light_data, chunks)
        if uid and uid.count(".") >= 3:
            row["device_uid_signal"] = uid.split(".")[-3]
    except Exception as e:
        row["error"] = f"tier1: {type(e).__name__}: {str(e)[:150]}"

    if not needs_heavy_tier(tier1_type, tier1_confident):
        row["tier"] = 1
        row["acquisition_type"] = tier1_type or ""
        row["success"] = True
        return row

    row["tier"] = 2
    try:
        cd = extract_from_opt(opt_path, noel_index=noel_index)
        row["success"] = True
        row["acquisition_type"] = cd.study_type or ""
        row["laterality"] = cd.laterality or ""
        if cd.noel_id:
            row["noel_id"] = cd.noel_id
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
        prefix = f"{row['error']} | " if row["error"] else ""
        row["error"] = f"{prefix}tier2: {type(e).__name__}: {str(e)[:200]}"

    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_root", nargs="?", default=str(DEFAULT_CORPUS_ROOT))
    parser.add_argument(
        "--light-budget",
        type=int,
        default=DEFAULT_LIGHT_BUDGET_BYTES,
        help="Bytes read for the tier-1 pass (default: 800000)",
    )
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root)
    light_budget = args.light_budget

    if not corpus_root.exists():
        print(f"ERROR: corpus root not found: {corpus_root}")
        print('Pass the correct path as an argument: python corpus_audit.py "<path>"')
        sys.exit(1)

    files = sorted(corpus_root.rglob("*.opt"))
    total = len(files)
    print(f"Descubiertos {total} archivos .opt bajo {corpus_root}")
    print(f"Light-tier budget: {light_budget} bytes\n")

    print("Construyendo NOEL index (solo nombres de archivo, sin abrir contenido)...")
    noel_index = build_noel_index(corpus_root)
    print(f"NOEL index: {len(noel_index)} pacientes\n")

    site_device_version = Counter()
    site_device_version_type = Counter()
    macular_group = Counter()
    macular_cmt_valid = Counter()
    tier1_only = 0
    tier2 = 0
    n_success = 0
    n_fail = 0
    failures = []
    device_signals_by_group = {}

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()

        for idx, opt_path in enumerate(files, 1):
            row = audit_file(opt_path, corpus_root, light_budget, noel_index)
            writer.writerow(row)
            f.flush()

            key_sdv = f"{row['site']} {row['device']} {row['soct_version']}"
            site_device_version[key_sdv] += 1
            site_device_version_type[f"{key_sdv} {row['acquisition_type'] or 'unknown'}"] += 1

            if row["tier"] == 1:
                tier1_only += 1
            else:
                tier2 += 1

            if row["success"]:
                n_success += 1
            else:
                n_fail += 1
                failures.append((row["site"], row["device"], row["rel_folder"], row["error"]))

            if row["acquisition_type"] == "macular":
                macular_group[key_sdv] += 1
                if row["cmt_um"] is not None:
                    macular_cmt_valid[key_sdv] += 1

            if row["device_uid_signal"]:
                group_key = (row["site"], row["device"])
                device_signals_by_group.setdefault(group_key, Counter())[row["device_uid_signal"]] += 1

            status = "OK" if row["success"] else "FAIL"
            print(f"  [{idx:>4}/{total}] {status:<4} tier{row['tier']} {key_sdv:<25} {row['acquisition_type'] or '?'}")

    minority_groups = 0
    flagged_files = 0
    for signal_counts in device_signals_by_group.values():
        if len(signal_counts) > 1:
            minority_groups += 1
            dominant_count = signal_counts.most_common(1)[0][1]
            flagged_files += sum(signal_counts.values()) - dominant_count

    macular_total = sum(macular_group.values())
    macular_cmt_total = sum(macular_cmt_valid.values())

    lines = [
        "=" * 70,
        "CORPUS AUDIT -- validation/corpus_audit.py",
        "=" * 70,
        f"Corpus root                  : {corpus_root}",
        f"Light-tier budget            : {light_budget} bytes",
        f"Total archivos .opt          : {total}",
    ]
    if total:
        lines.append(f"Parse success / fail          : {n_success}/{total} ({100*n_success/total:.1f}%)")
    lines += [
        f"Tier 1 (ligero, sin escalar)  : {tier1_only}",
        f"Tier 2 (pesado, medicion)     : {tier2}",
        "",
        "-- Site x Device x SOCT version (conteo de archivos) --",
    ]
    for key in sorted(site_device_version):
        lines.append(f"  {key:<30}  n={site_device_version[key]}")

    lines += ["", "-- Site x Device x SOCT version x Tipo de adquisicion --"]
    for key in sorted(site_device_version_type):
        lines.append(f"  {key:<40}  n={site_device_version_type[key]}")

    lines += ["", "-- CMT valido en maculares, por grupo --"]
    for key in sorted(macular_group):
        n_grp = macular_group[key]
        n_cmt = macular_cmt_valid.get(key, 0)
        lines.append(f"  {key:<30}  n={n_grp:<4}  CMT valido={n_cmt}")
    if macular_total:
        lines.append(
            f"  TOTAL macular: {macular_total}   CMT extraido: {macular_cmt_total}/{macular_total} "
            f"({100*macular_cmt_total/macular_total:.1f}%)"
        )
    else:
        lines.append("  TOTAL macular: 0")

    lines += [
        "",
        "-- Cross-check heuristico path-vs-contenido (firma de dispositivo en StudyUID) --",
        "   (heuristica, no prueba definitiva -- ver CSV local para NOEL_ID/carpeta de los marcados)",
        f"  Grupos site/device revisados  : {len(device_signals_by_group)}",
        f"  Grupos con firma mixta        : {minority_groups}",
        f"  Archivos en firma minoritaria : {flagged_files}",
    ]

    if failures:
        lines += ["", f"-- Fallas de parse ({len(failures)}), sin nombres de archivo --"]
        for site, device, rel_folder, error in failures[:20]:
            lines.append(f"  [{site}/{device}] {rel_folder}: {(error or '')[:80]}")
        if len(failures) > 20:
            lines.append(f"  ... y {len(failures) - 20} mas (ver CSV local)")

    lines += [
        "",
        f"CSV por archivo (LOCAL -- no pegar al chat): {OUT_CSV}",
    ]

    summary = "\n".join(lines)
    print("\n" + summary)
    OUT_SUMMARY_TXT.write_text(summary, encoding="utf-8")

    json_summary = {
        "corpus_root": str(corpus_root),
        "light_tier_budget_bytes": light_budget,
        "total_files": total,
        "parse_success": n_success,
        "parse_fail": n_fail,
        "tier1_only": tier1_only,
        "tier2": tier2,
        "site_device_version": dict(site_device_version),
        "site_device_version_type": dict(site_device_version_type),
        "macular_total": macular_total,
        "macular_cmt_valid": macular_cmt_total,
        "device_crosscheck_groups_checked": len(device_signals_by_group),
        "device_crosscheck_mixed_groups": minority_groups,
        "device_crosscheck_flagged_files": flagged_files,
    }
    OUT_SUMMARY_JSON.write_text(json.dumps(json_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nEscrito: {OUT_SUMMARY_TXT}")
    print(f"Escrito: {OUT_SUMMARY_JSON}")
    print(f"Escrito (local, NO pegar al chat): {OUT_CSV}")


if __name__ == "__main__":
    main()
