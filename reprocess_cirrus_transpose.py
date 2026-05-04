"""
reprocess_cirrus_transpose.py
Delete inverted (untransposed) OPT Tomography instances in Orthanc.

Context
-------
The old Cirrus ingest pipeline uploaded OPT Tomography B-scans with
Rows=A-scans (~512) and Columns=depth (~1024) — inverted relative to OCT
convention. reprocess_cirrus.py re-ingested the same exams through
carl_deobfuscator (which transposes), generating NEW SOPInstanceUIDs,
so both versions now coexist as separate instances in Orthanc.

Scope (safe heuristic — standard Cirrus macular / optic-disc cubes only):
  Inverted cube: series_desc ∈ {Cube, Macular, Optic Disc} (excluding HD
                 variants 'OCT Cube 4096x5' / 'OCT Cube Nx2' / 'OCT En Face')
                 AND Rows ∈ {200, 512}  AND  Cols == 1024
  Correct cube: same series_desc scope
                 AND Rows == 1024  AND  Cols ∈ {200, 512}

Excluded from the cleanup (left untouched):
  - HD Single Line / HD 5-Line Raster / OCT Cube 4096x5 / OCT Cube Nx2
    (native shape has cols > rows → naïve rows<cols detector misfires).
  - Revo FC130 OPT  (~992x1024 native orientation, not a transpose bug).
  - OCT En Face, Fundus Photo (SLO), anything with non-standard rows.

This script identifies and (optionally) deletes inverted cube instances
at the instance level only. Sibling match: same (StudyInstanceUID,
SeriesDescription, Laterality) — re-ingestion generated new SeriesUIDs
so matching SeriesInstanceUID directly is too strict.

Scope filter: SOPClassUID 1.2.840.10008.5.1.4.1.1.77.1.5.4 (OPT Tomography).

Usage
-----
  python reprocess_cirrus_transpose.py              # dry-run (default)
  python reprocess_cirrus_transpose.py --execute    # actually delete
  python reprocess_cirrus_transpose.py --limit N    # stop after N scanned
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import logging
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from tqdm import tqdm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("reprocess_cirrus_transpose")

ORTHANC_BASE = f"http://{os.environ['ORTHANC_HOST']}:{os.environ['ORTHANC_HTTP_PORT']}"
_AUTH = base64.b64encode(
    f"{os.environ['ORTHANC_HTTP_USER']}:{os.environ['ORTHANC_HTTP_PASS']}".encode()
).decode()

SOP_OCT_TOMOGRAPHY = "1.2.840.10008.5.1.4.1.1.77.1.5.4"

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ── Classifier: standard Cirrus cubes only ──────────────────────────────────

STD_CUBE_ASCANS      = {200, 512}  # rows when untransposed, cols when correct
STD_CUBE_DEPTH       = 1024        # axial depth — Cirrus native sampling
CUBE_SERIES_KEYWORDS = ("cube", "macular", "optic disc")
# 'OCT Cube 4096x5' / 'OCT Cube 1024x2' etc. — HD variants whose name re-uses
# 'Cube' but whose shape breaks the rows<cols=inverted heuristic. Must be
# excluded so we don't touch them. \b + [25] keeps 'Macular Cube 512x128' /
# 'Optic Disc Cube 200x200' out of the exclusion list.
_HD_CUBE_SHORTHAND = re.compile(r"\bcube\s+\d+x[25]\b")


def _is_std_cube_series(series_desc: str) -> bool:
    s = (series_desc or "").lower()
    if "en face" in s or _HD_CUBE_SHORTHAND.search(s) is not None:
        return False
    return any(k in s for k in CUBE_SERIES_KEYWORDS)


def _is_inverted_cube(info: dict) -> bool:
    """True iff standard cube stored with axes swapped (pre-reprocess)."""
    return (
        _is_std_cube_series(info["series_desc"])
        and info["rows"] in STD_CUBE_ASCANS
        and info["cols"] == STD_CUBE_DEPTH
    )


def _is_correct_cube(info: dict) -> bool:
    """True iff standard cube with the correct (transposed) orientation —
    candidate sibling of an inverted cube."""
    return (
        _is_std_cube_series(info["series_desc"])
        and info["rows"] == STD_CUBE_DEPTH
        and info["cols"] in STD_CUBE_ASCANS
    )


# ── Orthanc helpers ─────────────────────────────────────────────────────────

def _orthanc_get(url):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {_AUTH}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _orthanc_post_json(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
    req.add_header("Authorization", f"Basic {_AUTH}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def _orthanc_delete(url) -> int:
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Basic {_AUTH}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


# ── Core logic ───────────────────────────────────────────────────────────────

def _find_opt_tomography_instances() -> list[str]:
    """Return Orthanc instance IDs for every OPT Tomography instance."""
    return _orthanc_post_json(f"{ORTHANC_BASE}/tools/find", {
        "Level": "Instance",
        "Query": {"SOPClassUID": SOP_OCT_TOMOGRAPHY},
    }) or []


def _instance_shape_and_context(iid: str) -> dict | None:
    """Fetch (Rows, Columns, SOPInstanceUID, PatientID, StudyDate) for one instance.

    Uses simplified-tags to pull only what we need. Returns None on error.
    """
    try:
        tags = _orthanc_get(f"{ORTHANC_BASE}/instances/{iid}/simplified-tags")
    except Exception as e:
        logger.warning("  [skip] %s: tags GET failed: %s", iid[:12], e)
        return None
    try:
        rows = int(str(tags.get("Rows", "0")).strip() or 0)
        cols = int(str(tags.get("Columns", "0")).strip() or 0)
    except ValueError:
        return None
    if rows == 0 or cols == 0:
        return None
    return {
        "orthanc_id": iid,
        "rows":       rows,
        "cols":       cols,
        "sop_uid":    tags.get("SOPInstanceUID", ""),
        "series_uid": tags.get("SeriesInstanceUID", ""),
        "study_uid":  tags.get("StudyInstanceUID", ""),
        "patient_id": tags.get("PatientID", ""),
        "study_date": tags.get("StudyDate", ""),
        "laterality": tags.get("Laterality", "") or tags.get("ImageLaterality", ""),
        "series_desc": tags.get("SeriesDescription", ""),
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--execute", action="store_true",
                    help="Actually delete inverted instances (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after scanning N instances (0 = all)")
    ap.add_argument("--workers", type=int, default=8,
                    help="Parallel HTTP workers for scan/delete (default 8)")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print("═" * 70)
    print(f"Cirrus transpose cleanup — {mode}")
    print(f"Orthanc: {ORTHANC_BASE}")
    print(f"SOPClass: OPT Tomography ({SOP_OCT_TOMOGRAPHY})")
    print("═" * 70)
    sys.stdout.flush()

    t0 = time.time()
    print("Discovering OPT Tomography instances...")
    instance_ids = _find_opt_tomography_instances()
    total = len(instance_ids)
    print(f"  Found: {total} instances")

    if args.limit > 0:
        instance_ids = instance_ids[:args.limit]
        print(f"  Limited to first {len(instance_ids)}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"transpose_cleanup_{mode.lower()}_{ts}.csv"

    # ── Phase 1: parallel scan, group by (StudyUID, SeriesDesc, Laterality) ─
    # Sibling match (Option B): a correct sibling is one that shares the
    # same StudyInstanceUID + SeriesDescription + Laterality. This is
    # more permissive than matching SeriesInstanceUID (re-ingestion
    # generated new series UIDs) and still ensures we only delete
    # inverted instances when a semantically equivalent correct version
    # exists in the same study.
    print(f"\nPhase 1/2: scanning tags ({args.workers} workers)...")
    group_map: dict[tuple[str, str, str], list[dict]] = {}
    no_group_key: list[dict] = []
    skipped = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_instance_shape_and_context, iid): iid
                   for iid in instance_ids}
        pbar = tqdm(as_completed(futures), total=len(futures), desc="scan",
                    unit="inst", file=sys.stdout, mininterval=0.5,
                    dynamic_ncols=True)
        for fut in pbar:
            info = fut.result()
            if info is None:
                skipped += 1
                continue
            key = (info["study_uid"], info["series_desc"], info["laterality"])
            if not info["study_uid"] or not info["series_desc"]:
                no_group_key.append(info)
                continue
            group_map.setdefault(key, []).append(info)
        pbar.close()

    scanned = sum(len(v) for v in group_map.values()) + len(no_group_key)

    # ── Phase 2: classify per group + act ──────────────────────────────────
    # Only standard Cirrus cubes (Macular / Optic Disc, 200/512 A-scans ×
    # 1024 depth) are in scope. HD Single Line, HD 5-Line, OCT Cube 4096x5,
    # OCT Cube Nx2, Revo FC130 and OCT En Face are left untouched — their
    # native shape breaks the rows<cols=inverted heuristic.
    print(f"\nPhase 2/2: classifying across {len(group_map)} "
          f"(study, series-desc, laterality) groups...")
    to_delete: list[dict] = []
    no_sibling: list[dict] = []
    correct_cube_count = 0
    inverted_cube_count = 0
    out_of_scope_count = 0
    groups_inverted_no_sibling = 0

    for key, instances in group_map.items():
        inverted_cubes = [i for i in instances if _is_inverted_cube(i)]
        correct_cubes  = [i for i in instances if _is_correct_cube(i)]
        inverted_cube_count += len(inverted_cubes)
        correct_cube_count  += len(correct_cubes)
        out_of_scope_count  += len(instances) - len(inverted_cubes) - len(correct_cubes)

        if inverted_cubes and correct_cubes:
            to_delete.extend(inverted_cubes)
        elif inverted_cubes:
            groups_inverted_no_sibling += 1
            no_sibling.extend(inverted_cubes)
        # else: nothing inverted in scope → do nothing

    # Instances missing the group key (no StudyInstanceUID or no
    # SeriesDescription) are unsafe to classify — never delete them, but
    # tally for reporting.
    for info in no_group_key:
        if _is_inverted_cube(info):
            inverted_cube_count += 1
            no_sibling.append(info)
        elif _is_correct_cube(info):
            correct_cube_count += 1
        else:
            out_of_scope_count += 1

    # ── Write CSV + optionally delete ──────────────────────────────────────
    deleted = delete_err = 0

    with log_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["action", "orthanc_id", "sop_uid", "series_uid",
                    "patient_id", "study_date", "laterality",
                    "series_desc", "rows", "cols"])

        for info in no_sibling:
            w.writerow(["NO_CORRECT_SIBLING", info["orthanc_id"],
                        info["sop_uid"], info["series_uid"],
                        info["patient_id"], info["study_date"],
                        info["laterality"], info["series_desc"],
                        info["rows"], info["cols"]])

        if not args.execute:
            for info in to_delete:
                w.writerow(["WOULD_DELETE", info["orthanc_id"],
                            info["sop_uid"], info["series_uid"],
                            info["patient_id"], info["study_date"],
                            info["laterality"], info["series_desc"],
                            info["rows"], info["cols"]])
        else:
            print(f"\nDeleting {len(to_delete)} inverted instances "
                  f"({args.workers} workers)...")

            def _do_delete(info):
                try:
                    _orthanc_delete(f"{ORTHANC_BASE}/instances/{info['orthanc_id']}")
                    return info, None
                except Exception as e:
                    return info, str(e)

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(_do_delete, info) for info in to_delete]
                pbar = tqdm(as_completed(futures), total=len(futures),
                            desc="delete", unit="inst", file=sys.stdout,
                            mininterval=0.5, dynamic_ncols=True)
                for fut in pbar:
                    info, err = fut.result()
                    if err is None:
                        deleted += 1
                        w.writerow(["DELETE", info["orthanc_id"],
                                    info["sop_uid"], info["series_uid"],
                                    info["patient_id"], info["study_date"],
                                    info["laterality"], info["series_desc"],
                                    info["rows"], info["cols"]])
                    else:
                        delete_err += 1
                        w.writerow(["DELETE_FAIL", info["orthanc_id"],
                                    info["sop_uid"], info["series_uid"],
                                    info["patient_id"], info["study_date"],
                                    info["laterality"], info["series_desc"],
                                    info["rows"], info["cols"]])
                        tqdm.write(f"  [FAIL] {info['orthanc_id'][:12]}: {err}")
                    pbar.set_postfix({"del": deleted, "err": delete_err})
                pbar.close()

    elapsed = time.time() - t0
    print("═" * 70)
    print(f"{mode} DONE in {elapsed:.0f}s")
    print(f"  Scanned:  {scanned}")
    print(f"  Skipped (missing shape): {skipped}")
    print(f"  Out of scope (HD, En Face, Revo, other shapes): {out_of_scope_count}")
    print()
    print(f"  Standard cubes — correct (Rows=1024, Cols∈{{200,512}}): {correct_cube_count}")
    print(f"  Standard cubes — inverted (Rows∈{{200,512}}, Cols=1024): {inverted_cube_count}")
    print(f"  Missing group key (no StudyUID or SeriesDesc): {len(no_group_key)}")
    print(f"  Groups with ONLY inverted (no correct sibling): {groups_inverted_no_sibling}")
    print()
    print(f"  Safe to delete (has correct sibling): {len(to_delete)}")
    print(f"  Blocked — NO_CORRECT_SIBLING:         {len(no_sibling)}")
    if args.execute:
        print(f"  Deleted:      {deleted}")
        print(f"  Delete fails: {delete_err}")
    else:
        print("\n  Next step: review CSV, then rerun with --execute.")
    print(f"\nLog: {log_path}")
    print("═" * 70)


if __name__ == "__main__":
    main()
