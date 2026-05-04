"""
reprocess_cooked_opts.py
Retroactive SR-only reprocessing of "cooked" .opt files in processed/.

The bug fixed in 823ae4c caused empty SRs for Revo scans processed before
segmentation completed. The Revo later re-saved each scan with layers;
those re-saved versions landed in processed/ with a _YYYYMMDD_HHMMSS suffix.

This script regenerates ONLY the SR (Measurement Report TID 1500):
  - Reads .opt directly (bypasses extract_from_opt's filename parser which
    fails on the _YYYYMMDD_HHMMSS suffix; we parse the stem ourselves).
  - Computes CMT/ETDRS/RNFL/mGCIPL with the corrected BM-TOP formula.
  - Pulls existing StudyInstanceUID from one OPT instance in Orthanc.
  - Deletes "Transducin-empty" SR (if present), builds and uploads new SR.
  - No DICOM images re-uploaded — existing images in Orthanc kept intact.

Usage:
    python reprocess_cooked_opts.py              # dry-run (default)
    python reprocess_cooked_opts.py --execute    # modify Orthanc
    python reprocess_cooked_opts.py --limit 5    # test on first 5
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.request
import urllib.error
import base64
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import pydicom
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
# Silence noisy upstream chatter so progress stays readable
for noisy in ("transducin.opt_extractor", "transducin.revo_opt_reader",
              "transducin.noel_resolver", "transducin.sr_builder"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("reprocess_cooked")

PROCESSED_DIR = Path(os.environ.get("REVO_PROCESSED_DIR",
                                     r"C:\SOCT_DATA\BACKUP\processed"))

ORTHANC_BASE = f"http://{os.environ['ORTHANC_HOST']}:{os.environ['ORTHANC_HTTP_PORT']}"
_AUTH = base64.b64encode(
    f"{os.environ['ORTHANC_HTTP_USER']}:{os.environ['ORTHANC_HTTP_PASS']}".encode()
).decode()

# Cooked file pattern: <base>_<YYYYMMDD>_<HHMMSS>.opt
_COOKED_SUFFIX_RE = re.compile(r"^(.+)_(\d{8})_(\d{6})$")

# Revo type → clinical_data.study_type mapping
_TYPE_MAP = {
    "MACULAR":     "macular",
    "OPTIC_NERVE": "optic_nerve",
    "RNFL":        "rnfl",
    "ANGIO":       "angio",
    "OCT":         "oct",
    "FUNDUS":      "fundus",
    "BMETR":       "biometry",
    "COLOR_FUNDUS": "fundus",
}


# ── Orthanc helpers ─────────────────────────────────────────────────────────

def _orthanc_get(url: str):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {_AUTH}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _orthanc_get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {_AUTH}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _orthanc_post_json(url: str, data: dict):
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
    req.add_header("Authorization", f"Basic {_AUTH}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _orthanc_post_dicom(url: str, dcm_bytes: bytes) -> dict:
    req = urllib.request.Request(url, data=dcm_bytes, method="POST")
    req.add_header("Authorization", f"Basic {_AUTH}")
    req.add_header("Content-Type", "application/dicom")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _orthanc_delete(url: str) -> int:
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Basic {_AUTH}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


# ── SR inspection ───────────────────────────────────────────────────────────

def _sr_is_empty(ds: pydicom.Dataset) -> bool:
    """True if the SR contains 'Transducin-empty' tracking identifier anywhere."""
    def _walk(d):
        for elem in d:
            if elem.VR == "SQ":
                for item in elem.value:
                    if _walk(item):
                        return True
            else:
                v = getattr(elem, "value", None)
                if isinstance(v, str) and "Transducin-empty" in v:
                    return True
        return False
    return _walk(ds)


def _find_sr_in_study(orthanc_study_id: str) -> list[dict]:
    """Return [{series_id, instance_id, is_empty}, ...] for each SR series."""
    info = _orthanc_get(f"{ORTHANC_BASE}/studies/{orthanc_study_id}")
    srs = []
    for series_id in info.get("Series", []):
        series = _orthanc_get(f"{ORTHANC_BASE}/series/{series_id}")
        if series.get("MainDicomTags", {}).get("Modality") != "SR":
            continue
        instances = series.get("Instances", [])
        if not instances:
            continue
        dcm_bytes = _orthanc_get_bytes(f"{ORTHANC_BASE}/instances/{instances[0]}/file")
        try:
            ds = pydicom.dcmread(io.BytesIO(dcm_bytes), stop_before_pixels=True)
            is_empty = _sr_is_empty(ds)
        except Exception:
            is_empty = False
        srs.append({"series_id": series_id, "instance_id": instances[0],
                    "is_empty": is_empty})
    return srs


def _find_reference_opt_instance(orthanc_study_id: str) -> tuple[pydicom.Dataset, str]:
    """Fetch the first OPT instance of the study as a reference dataset."""
    info = _orthanc_get(f"{ORTHANC_BASE}/studies/{orthanc_study_id}")
    for series_id in info.get("Series", []):
        series = _orthanc_get(f"{ORTHANC_BASE}/series/{series_id}")
        if series.get("MainDicomTags", {}).get("Modality") != "OPT":
            continue
        instances = series.get("Instances", [])
        if not instances:
            continue
        dcm_bytes = _orthanc_get_bytes(f"{ORTHANC_BASE}/instances/{instances[0]}/file")
        ds = pydicom.dcmread(io.BytesIO(dcm_bytes), stop_before_pixels=True)
        return ds, str(getattr(ds, "StudyInstanceUID", ""))
    raise RuntimeError(f"No OPT instance in study {orthanc_study_id[:12]}")


# ── Cooked-filename parser ──────────────────────────────────────────────────

def _parse_cooked_stem(stem: str) -> dict:
    """Parse cooked .opt stem, stripping the _YYYYMMDD_HHMMSS rename suffix.

    Example stems:
        GARCIA_LOPEZ_ANA_MARIA_20260409_OD_MACULAR_20260410_110101
        → base=GARCIA_LOPEZ_ANA_MARIA_20260409_OD_MACULAR
        → apellidos_nombres=GARCIA_LOPEZ_ANA_MARIA, date=20260409, lat=R, type=macular

    Returns dict: {patient_name, study_date, laterality, study_type}.
    """
    # Strip _YYYYMMDD_HHMMSS suffix if present
    m = _COOKED_SUFFIX_RE.match(stem)
    base = m.group(1) if m else stem

    parts = base.split("_")

    # Find study date (first 8-digit token)
    date_idx = next((i for i, p in enumerate(parts) if re.fullmatch(r"\d{8}", p)), -1)
    if date_idx < 0:
        return {"patient_name": "^".join(parts[-2:]) if len(parts) >= 2 else base,
                "study_date": "", "laterality": "", "study_type": "unknown"}

    study_date = parts[date_idx]
    name_parts = parts[:date_idx]
    suffix = parts[date_idx + 1:]   # [OD/OS, TYPE1, TYPE2, ...]

    # Laterality
    lat_idx = next((i for i, p in enumerate(suffix) if p in ("OD", "OS")), -1)
    if lat_idx >= 0:
        laterality = "R" if suffix[lat_idx] == "OD" else "L"
        type_tokens = [p for i, p in enumerate(suffix) if i != lat_idx]
    else:
        laterality = ""
        type_tokens = list(suffix)
    type_raw = "_".join(type_tokens).upper()
    study_type = _TYPE_MAP.get(type_raw, type_raw.lower() or "unknown")

    # Patient name = "APELLIDOS^NOMBRES" — last token is fname, rest is lname
    if len(name_parts) >= 2:
        lname = "_".join(name_parts[:-1])
        fname = name_parts[-1]
        patient_name = f"{lname}^{fname}"
    elif name_parts:
        patient_name = name_parts[0]
    else:
        patient_name = ""

    return {
        "patient_name": patient_name,
        "study_date":   study_date,
        "laterality":   laterality,
        "study_type":   study_type,
    }


# ── Clinical data extraction bypassing the broken filename parser ───────────

def _extract_clinical_from_cooked(opt_path: Path):
    """Full extraction from a cooked .opt — parses stem manually, computes measurements."""
    from transducin.clinical_data import OCTClinicalData
    from transducin.revo_opt_reader import (
        parse_opt_chunks, parse_octparams, extract_layer,
        compute_cmt, compute_etdrs, compute_rnfl_sectors, compute_gcl_ipl,
        extract_sqi,
    )
    from transducin.noel_resolver import resolve_patient_demographics

    parsed = _parse_cooked_stem(opt_path.stem)

    # Resolve NOEL + demographics from PatientName (+ optional DOB later)
    demo = resolve_patient_demographics(patient_name=parsed["patient_name"])

    cd = OCTClinicalData(
        source_file = str(opt_path),
        noel_id     = demo["noel_id"],
        patient_name = demo["patient_name"] or parsed["patient_name"],
        patient_dob = demo["patient_dob"],
        study_date  = parsed["study_date"],
        laterality  = parsed["laterality"],
        study_type  = parsed["study_type"],
        extraction_confidence = "confirmed",
        vendor      = "revo",
    )

    # Measurements only relevant for macular/rnfl/oct/optic_nerve
    if cd.study_type not in ("macular", "rnfl", "oct", "optic_nerve"):
        return cd

    data = opt_path.read_bytes()
    chunks = parse_opt_chunks(data)
    params = parse_octparams(data, chunks)
    sqi = extract_sqi(data, chunks)

    top = extract_layer(data, chunks, "TOP")
    nfl = extract_layer(data, chunks, "NFL")
    gcl = extract_layer(data, chunks, "GCL")
    inl = extract_layer(data, chunks, "INL")
    bm  = extract_layer(data, chunks, "BM")

    # CMT + ETDRS = grosor retinal completo (BM − TOP)
    if top is not None and bm is not None:
        cmt = compute_cmt(top, bm, params, sqi=sqi)
        if cmt is not None:
            cd.cmt_um = round(cmt, 1)
        etdrs = compute_etdrs(top, bm, params, laterality=cd.laterality, sqi=sqi)
        if etdrs is not None:
            cd.etdrs_grid = etdrs

    # mRNFL macular = (NFL − TOP) — computed inside compute_rnfl_sectors via (gcl - nfl)
    # The current signature is (nfl, gcl, params) for mRNFL. For peripapillar, same fn
    # is called with different args in opt_extractor.
    if nfl is not None and gcl is not None:
        rnfl = compute_rnfl_sectors(nfl, gcl, params, laterality=cd.laterality, sqi=sqi)
        if rnfl is not None:
            cd.rnfl = rnfl

    if gcl is not None and inl is not None:
        gcl_ipl = compute_gcl_ipl(gcl, inl, params, laterality=cd.laterality, sqi=sqi)
        if gcl_ipl is not None:
            cd.gcl_ipl = gcl_ipl

    if sqi is not None and len(sqi) > 0:
        cd.sqi_mean = float(sqi.mean())

    return cd


# ── Main pipeline ────────────────────────────────────────────────────────────

def _find_cooked_files() -> list[Path]:
    from transducin.revo_opt_reader import has_segmentation
    all_cooked = [f for f in PROCESSED_DIR.glob("*.opt")
                  if _COOKED_SUFFIX_RE.match(f.stem)]
    logger.info("Archivos con sufijo _YYYYMMDD_HHMMSS: %d", len(all_cooked))
    with_seg = [f for f in all_cooked if has_segmentation(f)]
    logger.info("  Con segmentación:  %d", len(with_seg))
    logger.info("  Sin segmentación:  %d (saltados)", len(all_cooked) - len(with_seg))
    return sorted(with_seg)


def _find_study_in_orthanc(noel_id: str, study_date: str) -> str:
    if not noel_id or not study_date:
        return ""
    found = _orthanc_post_json(f"{ORTHANC_BASE}/tools/find", {
        "Level": "Study",
        "Query": {"PatientID": noel_id, "StudyDate": study_date},
    })
    return found[0] if found else ""


def process_one(opt_path: Path, execute: bool) -> tuple[str, str]:
    """Returns (result, message) where result ∈ {processed, skipped, failed}."""
    # 1. Extract clinical data
    try:
        cd = _extract_clinical_from_cooked(opt_path)
    except Exception as e:
        return "failed", f"extract: {e}"

    if not cd.has_measurements():
        return "skipped", "no measurements from .opt"

    # 2. Find study in Orthanc
    try:
        study_id = _find_study_in_orthanc(cd.noel_id, cd.study_date)
    except Exception as e:
        return "failed", f"orthanc find: {e}"
    if not study_id:
        return "skipped", f"study not in Orthanc ({cd.noel_id}/{cd.study_date})"

    # 3. Inspect existing SRs
    try:
        srs = _find_sr_in_study(study_id)
    except Exception as e:
        return "failed", f"SR inspect: {e}"

    empty_srs     = [s for s in srs if s["is_empty"]]
    non_empty_srs = [s for s in srs if not s["is_empty"]]

    if non_empty_srs:
        return "skipped", f"already has {len(non_empty_srs)} real SR(s)"

    cmt_str = f"CMT={cd.cmt_um:.1f}µm" if cd.cmt_um else "no CMT"
    msg = f"{study_id[:8]} {cd.noel_id}/{cd.study_date}/{cd.study_type}/{cd.laterality} {cmt_str}"

    if not execute:
        prefix = "[DRY] would replace " if empty_srs else "[DRY] would add SR "
        return "processed", f"{prefix}— {msg}"

    # 4. Delete empty SR
    for s in empty_srs:
        try:
            _orthanc_delete(f"{ORTHANC_BASE}/series/{s['series_id']}")
        except Exception as e:
            return "failed", f"delete SR: {e}"

    # 5. Build and upload SR
    try:
        ref_ds, study_uid = _find_reference_opt_instance(study_id)
        from transducin.sr_builder import build_sr
        with tempfile.TemporaryDirectory(prefix="transducin_sr_") as tmp:
            sr_path = Path(tmp) / f"{opt_path.stem}_SR.dcm"
            build_sr(cd, reference_dataset=ref_ds, output_path=sr_path,
                     study_instance_uid=study_uid)
            dcm_bytes = sr_path.read_bytes()
        r = _orthanc_post_dicom(f"{ORTHANC_BASE}/instances", dcm_bytes)
        if r.get("Status") not in ("Success", "AlreadyStored"):
            return "failed", f"upload status={r.get('Status')}"
    except Exception as e:
        return "failed", f"build/upload SR: {e}"

    return "processed", msg


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--execute", action="store_true",
                    help="Modificar Orthanc (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Procesar solo primeros N archivos (0=todos)")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print("═" * 60)
    print(f"Retroactive Revo SR-only — {mode}")
    print(f"Processed dir: {PROCESSED_DIR}")
    print(f"Orthanc:       {ORTHANC_BASE}")
    print("═" * 60)
    sys.stdout.flush()

    files = _find_cooked_files()
    if args.limit:
        files = files[:args.limit]
        print(f"Limitado a {len(files)} archivos")

    counts = {"processed": 0, "skipped": 0, "failed": 0}
    t0 = time.time()

    pbar = tqdm(files, unit="file", desc="Revo SR", file=sys.stdout,
                mininterval=0.5, dynamic_ncols=True)
    for f in pbar:
        t_file = time.time()
        result, msg = process_one(f, execute=args.execute)
        counts[result] += 1
        pbar.set_postfix({"ok": counts["processed"], "skip": counts["skipped"],
                          "fail": counts["failed"]})
        tqdm.write(f"  [{result:9s}] {f.name[:55]:55s} ({time.time()-t_file:.1f}s) {msg}")
    pbar.close()

    elapsed = time.time() - t0
    print("═" * 60)
    print(f"RESUMEN ({mode}): processed={counts['processed']} "
          f"skipped={counts['skipped']} failed={counts['failed']} "
          f"elapsed={elapsed:.1f}s")
    print("═" * 60)
    if not args.execute:
        print("Nada fue modificado. --execute para aplicar.")


if __name__ == "__main__":
    main()
