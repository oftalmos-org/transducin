"""
retag_cirrus_studies.py
Re-tag StudyDescription on existing Cirrus studies in Orthanc.

Why
---
Past ingestions used the generic study_description_label() map, producing
labels like "OCT Macular OD" / "OCT HD Line". The current pipeline emits
vendor-specific "Zeiss Cirrus HD-OCT ... OD|OS" labels via
apply_cirrus_study_tags(). This script retro-applies the new labels to
every Cirrus study already in Orthanc.

Pipeline per study
------------------
  1. GET /studies/{id} → StudyDescription + series list
  2. GET /series/{sid} for each series → SeriesDescription
     GET /series/{sid}/shared-tags for laterality (fallback: first instance)
  3. Classify:
       primary_type = first match in PRIORITY among series_types
       laterality   = unanimous OD/OS across series, else '' (mixed)
       new_desc     = _CIRRUS_STUDY_LABELS[primary_type] + " OD|OS" (optional)
  4. If new_desc == old_desc: SAME (skip).
     Else, in --execute:
       for each instance:
         b = GET /instances/{id}/file
         ds = pydicom.dcmread; ds.StudyDescription = new_desc
         POST /instances with Overwrite: true   (preserves SOPInstanceUID)

Safety harness (mirrors reprocess_cirrus.py)
--------------------------------------------
  - Checkpoint per study (resume-safe)
  - Batch rest between N studies (default 100 / 30s)
  - HTTP retry: 4xx=skip, 5xx/OSError=3-retry backoff + 5min rest + 2h cap
  - SIGINT/SIGTERM flushes checkpoint then exits
  - Heartbeat log every 60s
  - RAM guard (2GB minimum free)

Modes
-----
  python retag_cirrus_studies.py                       # dry-run (default)
  python retag_cirrus_studies.py --execute             # mutate Orthanc
  python retag_cirrus_studies.py --patient SILT19500101  # single patient
  python retag_cirrus_studies.py --workers 4           # parallel workers
  python retag_cirrus_studies.py --fresh               # ignore checkpoint
"""
from __future__ import annotations

import argparse
import base64
import csv
import gc
import io
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import psutil
import pydicom
from tqdm import tqdm

from transducin.cirrus_tags import (
    _CIRRUS_STUDY_LABELS,
    infer_laterality,
    infer_study_type,
)


# ── Logging ─────────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FILE = LOG_DIR / f"retag_cirrus_{_TS}.log"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_root = logging.getLogger()
_root.handlers.clear()
_root.setLevel(logging.INFO)

_fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_fh.setLevel(logging.INFO)
_fh.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
_root.addHandler(_fh)

_sh = logging.StreamHandler(sys.stdout)
_sh.setLevel(logging.WARNING)
_sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
_root.addHandler(_sh)

logging.getLogger("pydicom").setLevel(logging.ERROR)
logger = logging.getLogger("retag_cirrus")


# ── Constants ───────────────────────────────────────────────────────────────
ORTHANC_BASE = f"http://{os.environ['ORTHANC_HOST']}:{os.environ['ORTHANC_HTTP_PORT']}"
_AUTH = base64.b64encode(
    f"{os.environ['ORTHANC_HTTP_USER']}:{os.environ['ORTHANC_HTTP_PASS']}".encode()
).decode()

CHECKPOINT_FILE       = LOG_DIR / "retag_checkpoint.json"
HEARTBEAT_FILE        = LOG_DIR / "retag_heartbeat.log"

MIN_FREE_RAM_BYTES    = 2 * 1024**3
MEM_GUARD_SLEEP_S     = 30
RETRY_DELAYS_S        = (5, 15, 30)
NAS_OUTAGE_REST_S     = 300
MAX_RETRY_TOTAL_S     = 2 * 3600
HEARTBEAT_INTERVAL_S  = 60

# Priority order for multi-series studies. First match wins.
PRIORITY = (
    "macular", "optic_nerve", "hd_line", "fundus", "en_face",
    "ganglion_cell", "angio", "analysis", "unknown",
)
# Only real scan types vote on laterality. 'analysis' (background-processing
# wrappers) and 'unknown' (empty/garbage SeriesDescription) are excluded
# because their Laterality tag often echoes a sibling series from the other
# eye, producing spurious OU classifications on single-eye studies.
LAT_VOTING_TYPES = frozenset({
    "macular", "optic_nerve", "hd_line", "fundus",
    "en_face", "ganglion_cell", "angio",
})
_LAT_LABEL = {"R": "OD", "L": "OS", "OU": "OU"}


# ── Shared run state ────────────────────────────────────────────────────────
_SHUTDOWN_REQUESTED = False
_STATE_LOCK = threading.Lock()


class RunState:
    __slots__ = ("current_study", "batch_idx", "counts", "label_counts")

    def __init__(self):
        self.current_study = "-"
        self.batch_idx = 0
        self.counts = {"changed": 0, "same": 0, "failed": 0}
        self.label_counts: dict[str, int] = {}


_RUN_STATE = RunState()
_CHECKPOINT: Optional[dict] = None


# ── Checkpoint I/O ──────────────────────────────────────────────────────────

def _new_checkpoint() -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "started":      now,
        "last_updated": now,
        "processed":    {},
        "counts":       {"changed": 0, "same": 0, "failed": 0},
    }


def _load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            cp = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            cp.setdefault("processed", {})
            cp.setdefault("counts", {"changed": 0, "same": 0, "failed": 0})
            logger.info("Checkpoint loaded: %d studies processed",
                        len(cp["processed"]))
            return cp
        except Exception as e:
            logger.warning("Checkpoint unreadable (%s); starting fresh", e)
    return _new_checkpoint()


def _save_checkpoint(cp: dict) -> None:
    cp["last_updated"] = datetime.now().isoformat(timespec="seconds")
    tmp = CHECKPOINT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cp, indent=2), encoding="utf-8")
    tmp.replace(CHECKPOINT_FILE)


def _record_study(cp: dict, study_id: str, result: dict) -> None:
    cp["processed"][study_id] = {
        "status":        result.get("status", "?"),
        "old_desc":      result.get("old_desc", ""),
        "new_desc":      result.get("new_desc", ""),
        "instances_ok":  result.get("instances_ok", 0),
        "instances_err": result.get("instances_err", 0),
        "elapsed_s":     round(result.get("elapsed_s", 0.0), 2),
        "completed":     datetime.now().isoformat(timespec="seconds"),
    }
    cp["counts"] = dict(_RUN_STATE.counts)


# ── Memory guard ────────────────────────────────────────────────────────────

def _wait_for_memory() -> None:
    while not _SHUTDOWN_REQUESTED:
        avail = psutil.virtual_memory().available
        if avail >= MIN_FREE_RAM_BYTES:
            return
        logger.warning("RAM libre %.2f GB < 2 GB; esperando %ds",
                       avail / 1024**3, MEM_GUARD_SLEEP_S)
        for _ in range(MEM_GUARD_SLEEP_S):
            if _SHUTDOWN_REQUESTED:
                return
            time.sleep(1)


# ── HTTP retry wrapper ──────────────────────────────────────────────────────

def _retry_http_call(fn, op_name: str = "http"):
    t_start = time.time()
    while True:
        if _SHUTDOWN_REQUESTED:
            raise RuntimeError("Shutdown requested during retry")
        if time.time() - t_start > MAX_RETRY_TOTAL_S:
            msg = (f"Orthanc unreachable for {MAX_RETRY_TOTAL_S/3600:.1f}h - "
                   "aborting. Resume by re-running the same command.")
            logger.error(msg)
            raise SystemExit(msg)

        for delay in RETRY_DELAYS_S:
            if _SHUTDOWN_REQUESTED:
                raise RuntimeError("Shutdown requested during retry")
            try:
                return fn()
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    logger.warning("%s bad_request (HTTP %d); skipping", op_name, e.code)
                    raise
                logger.warning("%s fail (HTTP %d); retry en %ds", op_name, e.code, delay)
                time.sleep(delay)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                logger.warning("%s fail (%s); retry en %ds", op_name, e, delay)
                time.sleep(delay)

        logger.error("%s: Orthanc no responde tras 3 intentos; "
                     "pausa %ds antes del proximo ciclo", op_name, NAS_OUTAGE_REST_S)
        for _ in range(NAS_OUTAGE_REST_S):
            if _SHUTDOWN_REQUESTED:
                raise RuntimeError("Shutdown requested during retry rest")
            if time.time() - t_start > MAX_RETRY_TOTAL_S:
                break
            time.sleep(1)


# ── Signal handlers ─────────────────────────────────────────────────────────

def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _SHUTDOWN_REQUESTED
        if _SHUTDOWN_REQUESTED:
            return
        _SHUTDOWN_REQUESTED = True
        logger.warning("Signal %s received - flushing checkpoint and exiting", signum)
        if _CHECKPOINT is not None:
            try:
                _save_checkpoint(_CHECKPOINT)
            except Exception as e:
                logger.error("Checkpoint flush failed: %s", e)

    signal.signal(signal.SIGINT, _handler)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (AttributeError, ValueError):
        pass


# ── Heartbeat ───────────────────────────────────────────────────────────────

def _heartbeat_loop() -> None:
    while not _SHUTDOWN_REQUESTED:
        try:
            vm = psutil.virtual_memory()
            line = (f"{datetime.now().isoformat(timespec='seconds')} "
                    f"batch={_RUN_STATE.batch_idx} "
                    f"study={_RUN_STATE.current_study} "
                    f"changed={_RUN_STATE.counts['changed']} "
                    f"same={_RUN_STATE.counts['same']} "
                    f"failed={_RUN_STATE.counts['failed']} "
                    f"ram_free={vm.available/1024**3:.2f}GB "
                    f"ram_pct={vm.percent:.0f}%\n")
            with HEARTBEAT_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        for _ in range(HEARTBEAT_INTERVAL_S):
            if _SHUTDOWN_REQUESTED:
                return
            time.sleep(1)


def _start_heartbeat() -> None:
    try:
        HEARTBEAT_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass
    threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat").start()


# ── Orthanc helpers ─────────────────────────────────────────────────────────

def _orthanc_get(url):
    def _call():
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {_AUTH}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    return _retry_http_call(_call, op_name=f"GET {url.rsplit('/', 1)[-1]}")


def _orthanc_post_json(url, data):
    def _call():
        req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
        req.add_header("Authorization", f"Basic {_AUTH}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    return _retry_http_call(_call, op_name=f"POST {url.rsplit('/', 1)[-1]}")


def _orthanc_get_bytes(url) -> bytes:
    def _call():
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {_AUTH}")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    return _retry_http_call(_call, op_name=f"GET {url.rsplit('/', 1)[-1]}/file")


def _orthanc_post_dicom(url, dcm_bytes, overwrite=True):
    def _call():
        req = urllib.request.Request(url, data=dcm_bytes, method="POST")
        req.add_header("Authorization", f"Basic {_AUTH}")
        req.add_header("Content-Type", "application/dicom")
        if overwrite:
            req.add_header("Overwrite", "true")
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())
    return _retry_http_call(_call, op_name="POST /instances")


# ── Discovery ───────────────────────────────────────────────────────────────

def _list_cirrus_studies(patient_filter: Optional[str] = None,
                         workers: int = 1) -> list[str]:
    """Return Orthanc study IDs for all Zeiss/Cirrus studies.

    Tries study-level /tools/find first (one call, Orthanc typically indexes
    Manufacturer at study level). If that returns 0 (some configs only keep
    Manufacturer at series level), falls back to a series-level /tools/find
    with Expand=true — a single call returns full series objects including
    ParentStudy, from which we dedupe studies in memory.

    `workers` is accepted for signature compatibility (no per-series GETs
    any more).
    """
    query: dict = {"Manufacturer": "*Zeiss*"}
    if patient_filter:
        query["PatientID"] = patient_filter

    study_ids = _orthanc_post_json(f"{ORTHANC_BASE}/tools/find", {
        "Level": "Study", "Query": query,
    }) or []
    if study_ids:
        logger.info("Discovery: %d Zeiss studies (patient_filter=%s)",
                    len(study_ids), patient_filter or "-")
        return sorted(study_ids)

    logger.warning("Discovery: study-level query returned 0 Zeiss studies; "
                   "falling back to series-level Expand grouping")
    expanded = _orthanc_post_json(f"{ORTHANC_BASE}/tools/find", {
        "Level": "Series", "Query": query, "Expand": True,
    }) or []
    study_set = {s.get("ParentStudy", "") for s in expanded}
    study_set.discard("")
    logger.info("Discovery fallback: %d series -> %d Zeiss studies "
                "(patient_filter=%s)",
                len(expanded), len(study_set), patient_filter or "-")
    return sorted(study_set)


# ── Classification (pure read) ──────────────────────────────────────────────

def _normalize_lat(value) -> str:
    """Normalise any laterality string (R/L/OD/OS/...) to 'R'/'L' or ''.

    Cirrus stores 'OS'/'OD' in the standard Laterality tag (0020,0060) instead
    of the DICOM-standard 'L'/'R', so we accept both spellings.
    """
    v = str(value or "").strip().upper()
    if v in ("R", "L"):
        return v
    if v == "OD":
        return "R"
    if v == "OS":
        return "L"
    return ""


def _series_laterality(series_id: str, first_instance_id: Optional[str]) -> str:
    """Return 'R'/'L'/'' for a series.

    Try /series/{id}/shared-tags first (one call covers homogeneous series).
    If absent, read the first instance's simplified tags. Last resort: pydicom
    read of the instance to check the Zeiss private tag (0057,1015).
    """
    try:
        st = _orthanc_get(f"{ORTHANC_BASE}/series/{series_id}/shared-tags?simplify")
        for key in ("ImageLaterality", "Laterality"):
            lat = _normalize_lat(st.get(key))
            if lat:
                return lat
    except urllib.error.HTTPError:
        pass

    if not first_instance_id:
        return ""
    try:
        tags = _orthanc_get(
            f"{ORTHANC_BASE}/instances/{first_instance_id}/simplified-tags")
        for key in ("ImageLaterality", "Laterality"):
            lat = _normalize_lat(tags.get(key))
            if lat:
                return lat
    except urllib.error.HTTPError:
        return ""

    try:
        b = _orthanc_get_bytes(f"{ORTHANC_BASE}/instances/{first_instance_id}/file")
        ds = pydicom.dcmread(io.BytesIO(b), stop_before_pixels=True, force=True)
        lat = infer_laterality(ds)
        if lat:
            return lat
        return _normalize_lat(getattr(ds, "Laterality", ""))
    except Exception:
        return ""


def classify_study(study_id: str) -> dict:
    """Return classification dict for one study. No Orthanc writes."""
    st = _orthanc_get(f"{ORTHANC_BASE}/studies/{study_id}")
    mdt = st.get("MainDicomTags", {}) or {}
    pdt = st.get("PatientMainDicomTags", {}) or {}

    series_types: list[str] = []
    lats: list[str] = []
    series_lats: list[tuple[str, str]] = []
    instance_ids: list[str] = []

    for sid in st.get("Series", []):
        try:
            sr = _orthanc_get(f"{ORTHANC_BASE}/series/{sid}")
        except urllib.error.HTTPError:
            continue
        sr_mdt = sr.get("MainDicomTags", {}) or {}
        sdesc = sr_mdt.get("SeriesDescription", "") or ""
        stype = infer_study_type(sdesc)
        series_types.append(stype)
        sr_instances = sr.get("Instances", []) or []
        instance_ids.extend(sr_instances)
        lat = _series_laterality(sid, sr_instances[0] if sr_instances else None)
        series_lats.append((stype, lat))
        if stype in LAT_VOTING_TYPES:
            lats.append(lat)

    primary_type = next((t for t in PRIORITY if t in series_types), "unknown")

    # Prefer the laterality of the first series matching primary_type.
    # Bilateral Cirrus sessions commonly bundle OD + OS scans under one
    # StudyInstanceUID, so taking the union produced spurious "OU" labels.
    # Using the primary scan's eye yields e.g. "... Macular OD" for the OD
    # macular cube even when a fundus photo of OS is in the same study.
    laterality = ""
    for stype, lat in series_lats:
        if stype == primary_type and lat in ("R", "L"):
            laterality = lat
            break

    # Fallback: if the primary-type series had no laterality, fall back to
    # union across real scan types (Option 3 semantics).
    if not laterality:
        lat_set = {lat for lat in lats if lat in ("R", "L")}
        if len(lat_set) == 2:
            laterality = "OU"
        elif len(lat_set) == 1:
            laterality = lat_set.pop()

    base = _CIRRUS_STUDY_LABELS.get(primary_type, _CIRRUS_STUDY_LABELS["unknown"])
    new_desc = f"{base} {_LAT_LABEL[laterality]}" if laterality else base
    old_desc = str(mdt.get("StudyDescription", "") or "")

    return {
        "orthanc_id":   study_id,
        "patient_id":   pdt.get("PatientID", "") or mdt.get("PatientID", ""),
        "study_date":   mdt.get("StudyDate", ""),
        "n_series":     len(st.get("Series", [])),
        "n_instances":  len(instance_ids),
        "instance_ids": instance_ids,
        "series_types": series_types,
        "primary_type": primary_type,
        "laterality":   laterality,
        "old_desc":     old_desc,
        "new_desc":     new_desc,
        "action":       "SAME" if old_desc == new_desc else "CHANGE",
    }


# ── Execute: download/modify/re-upload one instance at a time ───────────────

def _retag_one_instance(instance_id: str, new_desc: str) -> None:
    """Download one instance, set StudyDescription, re-upload with Overwrite."""
    b = _orthanc_get_bytes(f"{ORTHANC_BASE}/instances/{instance_id}/file")
    ds = pydicom.dcmread(io.BytesIO(b), force=True)
    ds.StudyDescription = new_desc
    buf = io.BytesIO()
    ds.save_as(buf, write_like_original=False)
    _orthanc_post_dicom(f"{ORTHANC_BASE}/instances", buf.getvalue(), overwrite=True)


def retag_study_execute(cls: dict, workers: int = 1) -> dict:
    """Apply new StudyDescription to every instance of the study."""
    t0 = time.time()
    new_desc = cls["new_desc"]
    instance_ids = cls["instance_ids"]
    ok = err = 0
    errors_sample: list[str] = []

    def _one(iid: str):
        try:
            _retag_one_instance(iid, new_desc)
            return iid, None
        except Exception as e:
            return iid, str(e)

    if workers > 1 and len(instance_ids) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed(pool.submit(_one, iid) for iid in instance_ids):
                _iid, e = fut.result()
                if e is None:
                    ok += 1
                else:
                    err += 1
                    if len(errors_sample) < 3:
                        errors_sample.append(e)
    else:
        for iid in instance_ids:
            _iid, e = _one(iid)
            if e is None:
                ok += 1
            else:
                err += 1
                if len(errors_sample) < 3:
                    errors_sample.append(e)

    status = "CHANGED" if err == 0 else ("CHANGE_PARTIAL" if ok > 0 else "CHANGE_FAIL")
    return {
        "status":        status,
        "instances_ok":  ok,
        "instances_err": err,
        "errors_sample": errors_sample,
        "elapsed_s":     time.time() - t0,
    }


# ── Main ────────────────────────────────────────────────────────────────────

def _process_one_study(study_id: str, args, csv_writer, csv_lock: threading.Lock) -> dict:
    """Classify + (optionally) retag one study. Returns result dict for checkpoint."""
    _RUN_STATE.current_study = study_id[:12]
    t0 = time.time()

    try:
        cls = classify_study(study_id)
    except Exception as e:
        logger.exception("Classify failed: %s", study_id[:12])
        with csv_lock:
            csv_writer.writerow([
                "CLASSIFY_FAIL", study_id, "", "", 0, 0, "", "", "", "", 0, 0,
                f"classify_error: {e}",
            ])
        return {"status": "failed", "old_desc": "", "new_desc": "",
                "instances_ok": 0, "instances_err": 0,
                "elapsed_s": time.time() - t0, "error": str(e)}

    if cls["action"] == "SAME":
        with csv_lock:
            csv_writer.writerow([
                "SAME", cls["orthanc_id"], cls["patient_id"], cls["study_date"],
                cls["n_series"], cls["n_instances"], cls["primary_type"],
                cls["laterality"], cls["old_desc"], cls["new_desc"], 0, 0, "",
            ])
        with _STATE_LOCK:
            _RUN_STATE.counts["same"] += 1
            _RUN_STATE.label_counts[cls["new_desc"]] = \
                _RUN_STATE.label_counts.get(cls["new_desc"], 0) + 1
        logger.info("[SAME] %s %s %s [%d ser, %d inst] desc=%r",
                    study_id[:12], cls["patient_id"], cls["study_date"],
                    cls["n_series"], cls["n_instances"], cls["new_desc"])
        return {"status": "SAME", "old_desc": cls["old_desc"],
                "new_desc": cls["new_desc"], "instances_ok": 0,
                "instances_err": 0, "elapsed_s": time.time() - t0}

    # action == CHANGE
    if not args.execute:
        with csv_lock:
            csv_writer.writerow([
                "WOULD_CHANGE", cls["orthanc_id"], cls["patient_id"],
                cls["study_date"], cls["n_series"], cls["n_instances"],
                cls["primary_type"], cls["laterality"], cls["old_desc"],
                cls["new_desc"], 0, 0, "",
            ])
        with _STATE_LOCK:
            _RUN_STATE.counts["changed"] += 1
            _RUN_STATE.label_counts[cls["new_desc"]] = \
                _RUN_STATE.label_counts.get(cls["new_desc"], 0) + 1
        logger.info("[WOULD_CHANGE] %s %s %s primary=%s lat=%r %r -> %r",
                    study_id[:12], cls["patient_id"], cls["study_date"],
                    cls["primary_type"], cls["laterality"],
                    cls["old_desc"], cls["new_desc"])
        return {"status": "WOULD_CHANGE", "old_desc": cls["old_desc"],
                "new_desc": cls["new_desc"], "instances_ok": 0,
                "instances_err": 0, "elapsed_s": time.time() - t0}

    # EXECUTE
    try:
        result = retag_study_execute(cls, workers=args.instance_workers)
    except Exception as e:
        logger.exception("Retag execute failed: %s", study_id[:12])
        with csv_lock:
            csv_writer.writerow([
                "CHANGE_FAIL", cls["orthanc_id"], cls["patient_id"],
                cls["study_date"], cls["n_series"], cls["n_instances"],
                cls["primary_type"], cls["laterality"], cls["old_desc"],
                cls["new_desc"], 0, cls["n_instances"], f"exec_error: {e}",
            ])
        with _STATE_LOCK:
            _RUN_STATE.counts["failed"] += 1
        return {"status": "failed", "old_desc": cls["old_desc"],
                "new_desc": cls["new_desc"], "instances_ok": 0,
                "instances_err": cls["n_instances"],
                "elapsed_s": time.time() - t0, "error": str(e)}

    with csv_lock:
        csv_writer.writerow([
            result["status"], cls["orthanc_id"], cls["patient_id"],
            cls["study_date"], cls["n_series"], cls["n_instances"],
            cls["primary_type"], cls["laterality"], cls["old_desc"],
            cls["new_desc"], result["instances_ok"], result["instances_err"],
            "; ".join(result["errors_sample"]) if result["errors_sample"] else "",
        ])
    with _STATE_LOCK:
        if result["status"] == "CHANGED":
            _RUN_STATE.counts["changed"] += 1
            _RUN_STATE.label_counts[cls["new_desc"]] = \
                _RUN_STATE.label_counts.get(cls["new_desc"], 0) + 1
        else:
            _RUN_STATE.counts["failed"] += 1
    logger.info("[%s] %s %s %s inst %d/%d %s",
                result["status"], study_id[:12], cls["patient_id"],
                cls["study_date"], result["instances_ok"],
                cls["n_instances"], cls["new_desc"])
    return {
        "status":        result["status"],
        "old_desc":      cls["old_desc"],
        "new_desc":      cls["new_desc"],
        "instances_ok":  result["instances_ok"],
        "instances_err": result["instances_err"],
        "elapsed_s":     time.time() - t0,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--execute", action="store_true",
                    help="Mutate Orthanc (default: dry-run)")
    ap.add_argument("--patient", type=str, default=None,
                    help="Restrict to a single PatientID")
    ap.add_argument("--from-index", type=int, default=0,
                    help="Start at N-th study (overrides checkpoint skip)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N studies (0 = all)")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="Studies per batch before rest (default 100)")
    ap.add_argument("--batch-rest", type=int, default=30,
                    help="Seconds to rest between batches (default 30)")
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel study workers (default 1 = serial)")
    ap.add_argument("--instance-workers", type=int, default=1,
                    help="Per-study instance upload workers (default 1)")
    ap.add_argument("--fresh", action="store_true",
                    help="Ignore checkpoint and process all studies")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print("=" * 70)
    print(f"Cirrus StudyDescription Re-tag - {mode}")
    print(f"Orthanc: {ORTHANC_BASE}")
    print(f"Source filter: Manufacturer=*Zeiss*"
          f"{' PatientID=' + args.patient if args.patient else ''}")
    print(f"Log: {_LOG_FILE}")
    print("=" * 70)
    sys.stdout.flush()

    # Discovery
    print("\nDiscovery phase: listing Zeiss series + grouping by parent study...")
    all_studies = _list_cirrus_studies(patient_filter=args.patient,
                                       workers=args.workers)
    print(f"  Parent studies (deduped): {len(all_studies)}")

    if args.from_index:
        all_studies = all_studies[args.from_index:]
        print(f"  Skipping first {args.from_index} (--from-index)")
    if args.limit > 0:
        all_studies = all_studies[:args.limit]
        print(f"  Limited to first {args.limit} (--limit)")

    if not all_studies:
        print("Nothing to do.")
        return

    # Checkpoint
    global _CHECKPOINT
    _CHECKPOINT = _new_checkpoint() if args.fresh else _load_checkpoint()
    _RUN_STATE.counts = dict(_CHECKPOINT["counts"])
    processed_names = set(_CHECKPOINT["processed"].keys())

    if args.from_index or args.patient:
        pending = all_studies
    else:
        pending = [s for s in all_studies if s not in processed_names]
        if processed_names:
            print(f"Checkpoint: {len(processed_names)} ya procesados, "
                  f"{len(pending)} pendientes")

    if not pending:
        print("Nothing pending (all studies processed per checkpoint).")
        print("Use --fresh to reprocess from scratch.")
        return

    # CSV
    csv_path = LOG_DIR / f"retag_cirrus_{mode.lower().replace('-','_')}_{_TS}.csv"
    csv_fh = csv_path.open("w", newline="", encoding="utf-8")
    csv_writer = csv.writer(csv_fh)
    csv_writer.writerow([
        "action", "orthanc_id", "patient_id", "study_date",
        "n_series", "n_instances", "primary_type", "laterality",
        "old_desc", "new_desc", "instances_ok", "instances_err", "error",
    ])
    csv_lock = threading.Lock()

    _install_signal_handlers()
    if args.execute:
        _start_heartbeat()

    print(f"\nProcessing {len(pending)} studies in batches of "
          f"{args.batch_size} (rest={args.batch_rest}s)"
          f"  workers={args.workers}"
          f"  instance_workers={args.instance_workers}")
    print(f"CSV: {csv_path}")
    sys.stdout.flush()

    batch_size = max(1, args.batch_size)
    pbar = tqdm(total=len(pending), desc=f"Cirrus retag {mode}",
                unit="study", file=sys.stdout, mininterval=0.5,
                dynamic_ncols=True)

    def _after_study(sid: str, result: dict) -> None:
        with _STATE_LOCK:
            _record_study(_CHECKPOINT, sid, result)
            _save_checkpoint(_CHECKPOINT)
        pbar.update(1)
        pbar.set_postfix(_RUN_STATE.counts)

    try:
        i = 0
        while i < len(pending) and not _SHUTDOWN_REQUESTED:
            batch = pending[i:i + batch_size]
            _wait_for_memory()
            if _SHUTDOWN_REQUESTED:
                break

            if args.workers > 1:
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = {pool.submit(_process_one_study, sid, args,
                                            csv_writer, csv_lock): sid
                               for sid in batch}
                    for fut in as_completed(futures):
                        sid = futures[fut]
                        try:
                            r = fut.result()
                        except SystemExit:
                            raise
                        except Exception as e:
                            logger.exception("Worker crashed on %s", sid[:12])
                            r = {"status": "failed", "error": str(e),
                                 "elapsed_s": 0.0}
                            with _STATE_LOCK:
                                _RUN_STATE.counts["failed"] += 1
                        _after_study(sid, r)
                        if _SHUTDOWN_REQUESTED:
                            break
            else:
                for sid in batch:
                    if _SHUTDOWN_REQUESTED:
                        break
                    try:
                        r = _process_one_study(sid, args, csv_writer, csv_lock)
                    except SystemExit:
                        raise
                    except Exception as e:
                        logger.exception("Study crashed: %s", sid[:12])
                        r = {"status": "failed", "error": str(e),
                             "elapsed_s": 0.0}
                        with _STATE_LOCK:
                            _RUN_STATE.counts["failed"] += 1
                    _after_study(sid, r)

            i += len(batch)

            if i < len(pending) and not _SHUTDOWN_REQUESTED:
                _RUN_STATE.batch_idx += 1
                c = _RUN_STATE.counts
                print(f"\nBatch {_RUN_STATE.batch_idx} complete: "
                      f"changed={c['changed']} same={c['same']} "
                      f"failed={c['failed']}. Resting {args.batch_rest}s...")
                sys.stdout.flush()
                csv_fh.flush()
                gc.collect()
                for _ in range(max(0, args.batch_rest)):
                    if _SHUTDOWN_REQUESTED:
                        break
                    time.sleep(1)
    finally:
        pbar.close()
        csv_fh.close()
        _save_checkpoint(_CHECKPOINT)

    # Summary
    c = _RUN_STATE.counts
    print("=" * 70)
    if _SHUTDOWN_REQUESTED:
        print(f"INTERRUPTED - checkpoint saved at {CHECKPOINT_FILE}")
    else:
        print(f"{mode} DONE")
    print(f"  Total processed: {c['changed'] + c['same'] + c['failed']}")
    print(f"  Changed:         {c['changed']}")
    print(f"  Same (skipped):  {c['same']}")
    print(f"  Failed:          {c['failed']}")
    print()
    if _RUN_STATE.label_counts:
        print("  By target label:")
        for label, n in sorted(_RUN_STATE.label_counts.items(),
                                key=lambda x: (-x[1], x[0])):
            print(f"    {n:>6}  {label}")
    print(f"\n  CSV: {csv_path}")
    if not args.execute and not _SHUTDOWN_REQUESTED:
        print("\n  Next step: review CSV, then rerun with --execute.")
    print("=" * 70)


if __name__ == "__main__":
    main()
