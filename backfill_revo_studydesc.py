"""
backfill_revo_studydesc.py
Backfill empty StudyDescription on Revo OPT studies in Orthanc.

Scope: Revo-only (Manufacturer contains "Optopol"). Cirrus studies are
handled by the ongoing full Cirrus re-ingestion.

For each target study, derives StudyDescription from the first OPT series'
SeriesDescription (e.g. "Revo FC130 OCT OD") by promoting it to a
study-level description — format matches new studies from revo_watcher.py.

Usage:
    python backfill_revo_studydesc.py              # dry-run (default)
    python backfill_revo_studydesc.py --execute    # modify Orthanc
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request

from dotenv import load_dotenv
load_dotenv()

from tqdm import tqdm

BASE = f"http://{os.environ['ORTHANC_HOST']}:{os.environ['ORTHANC_HTTP_PORT']}"
AUTH = base64.b64encode(
    f"{os.environ['ORTHANC_HTTP_USER']}:{os.environ['ORTHANC_HTTP_PASS']}".encode()
).decode()


def req_get(url):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {AUTH}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def req_post(url, data, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
    req.add_header("Authorization", f"Basic {AUTH}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _derive_study_description(series_infos: list[dict]) -> str:
    """Build a StudyDescription from the OPT series descriptions.

    Priority:
      1. If any series description already starts with "Revo FC130 " → use it.
      2. Fallback to generic "Revo FC130 OCT".
    """
    for s in series_infos:
        desc = s.get("MainDicomTags", {}).get("SeriesDescription", "").strip()
        if desc.startswith("Revo FC130"):
            # Strip laterality suffix to promote to study level
            # "Revo FC130 OCT OD" → "Revo FC130 OCT"
            return re.sub(r"\s+(OD|OS)$", "", desc).strip() or "Revo FC130 OCT"
    return "Revo FC130 OCT"


def find_targets() -> list[dict]:
    """Return list of Revo OPT studies with empty StudyDescription."""
    studies = req_post(f"{BASE}/tools/find", {
        "Level": "Study",
        "Query": {"ModalitiesInStudy": "OPT"},
        "Expand": True,
    })

    empty_desc = [
        st for st in studies
        if not st.get("MainDicomTags", {}).get("StudyDescription", "").strip()
    ]

    revo_targets = []
    for st in tqdm(empty_desc, desc="Filtering Revo", unit="study",
                    file=sys.stdout, mininterval=0.5):
        series_ids = st.get("Series", [])
        if not series_ids:
            continue
        try:
            s_info = req_get(f"{BASE}/series/{series_ids[0]}")
        except Exception:
            continue
        mfr = s_info.get("MainDicomTags", {}).get("Manufacturer", "").lower()
        if "optopol" not in mfr:
            continue
        # Prefetch all series infos for StudyDescription derivation
        series_infos = []
        for sid in series_ids[:5]:   # first 5 is enough
            try:
                series_infos.append(req_get(f"{BASE}/series/{sid}"))
            except Exception:
                pass
        st["_series_infos"] = series_infos
        revo_targets.append(st)
    return revo_targets


def process_one(study: dict, execute: bool) -> str:
    sid = study.get("ID", "")
    new_desc = _derive_study_description(study.get("_series_infos", []))
    pt = study.get("PatientMainDicomTags", {})
    tags = study.get("MainDicomTags", {})

    label = (f"{sid[:12]}  noel={pt.get('PatientID','?'):14s}  "
             f"date={tags.get('StudyDate','?')}  → {new_desc!r}")

    if not execute:
        return f"[DRY] {label}"

    try:
        result = req_post(f"{BASE}/studies/{sid}/modify", {
            "Replace": {"StudyDescription": new_desc},
            "Force":   True,
            "KeepSource": False,
        })
        new_sid = result.get("ID", "")
        return f"[OK ] {label}  → new_sid={new_sid[:12]}"
    except Exception as e:
        return f"[FAIL] {label}  {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true",
                    help="Modificar Orthanc (default: dry-run)")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print("═" * 60)
    print(f"Revo StudyDescription backfill — {mode}")
    print(f"Orthanc: {BASE}")
    print("═" * 60)
    sys.stdout.flush()

    targets = find_targets()
    print(f"\nRevo OPT studies needing backfill: {len(targets)}\n")

    if not targets:
        print("Nothing to do.")
        return

    t0 = time.time()
    counts = {"OK": 0, "DRY": 0, "FAIL": 0}
    for st in tqdm(targets, desc="Backfill", unit="study",
                    file=sys.stdout, mininterval=0.5):
        msg = process_one(st, execute=args.execute)
        tag = msg.split("]", 1)[0].strip("[ ")
        counts[tag] = counts.get(tag, 0) + 1
        tqdm.write(msg)

    print("═" * 60)
    print(f"DONE — {counts}  elapsed={time.time()-t0:.1f}s")
    print("═" * 60)
    if not args.execute:
        print("Nothing modified. --execute para aplicar.")


if __name__ == "__main__":
    main()
