"""
reprocess_cirrus.py
Full Cirrus re-ingestion from C:\\CIRRUS500\\SDOCT\\DataBase\\DATAFILES\\.

Each E-folder (E000 … E999) contains .EX.DCM files — obfuscated Cirrus DICOMs.
Pipeline per folder:
  1. Deobfuscate each .EX.DCM (carl_deobfuscator — unscrambles + transposes)
  2. Run cirrus_extractor.extract_from_exam → OCTClinicalData per study
  3. For each study with measurements:
       a. Upload deobfuscated DICOMs to Orthanc (with Overwrite=true)
       b. Build SR TID 1500 and upload

Modes:
  --test N          Dry-run on first N folders (default 3): report CMT, B-scan
                    orientation, timing per folder. No Orthanc changes.
  --estimate        Same as --test 3 but also projects full-run duration.
  --execute         Full run on all 1000 folders.
  --delete-existing Delete existing Cirrus studies in Orthanc before ingesting.
  --from-folder N   Start from E<N> (default 0).
  --limit N         Stop after N folders (default: all).

Usage:
  python reprocess_cirrus.py                       # --test 3 default
  python reprocess_cirrus.py --estimate            # 3 folders + time projection
  python reprocess_cirrus.py --execute --delete-existing    # full run
  python reprocess_cirrus.py --execute --from-folder 500 --limit 100
"""
from __future__ import annotations

import argparse
import base64
import gc
import json
import logging
import os
import re
import signal
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import psutil
import pydicom
from tqdm import tqdm

from transducin.cirrus_tags import apply_cirrus_study_tags

# ── Logging: WARNING+ to stdout (clean CLI), INFO+ to file ──────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FILE = LOG_DIR / f"reprocess_full_{_TS}.log"

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

for noisy in ("transducin.cirrus_extractor", "transducin.sr_builder",
              "transducin.noel_resolver", "transducin.carl_deobfuscator"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("reprocess_cirrus")

DATAFILES_DIR = Path(os.environ.get("CIRRUS_DATAFILES_DIR",
                                     r"C:\CIRRUS500\SDOCT\DataBase\DATAFILES"))

ORTHANC_BASE = f"http://{os.environ['ORTHANC_HOST']}:{os.environ['ORTHANC_HTTP_PORT']}"
_AUTH = base64.b64encode(
    f"{os.environ['ORTHANC_HTTP_USER']}:{os.environ['ORTHANC_HTTP_PASS']}".encode()
).decode()

# ── Safety / resilience constants ───────────────────────────────────────────
CHECKPOINT_FILE       = LOG_DIR / "reprocess_checkpoint.json"
HEARTBEAT_FILE        = LOG_DIR / "reprocess_heartbeat.log"

MIN_FREE_RAM_BYTES    = 2 * 1024**3          # 2 GB
MEM_GUARD_SLEEP_S     = 30
RETRY_DELAYS_S        = (5, 15, 30)          # per-cycle backoff
NAS_OUTAGE_REST_S     = 300                  # 5 min rest between retry cycles
MAX_RETRY_TOTAL_S     = 2 * 3600             # abort after 2h of retries
HEARTBEAT_INTERVAL_S  = 60


# ── Shared run state (checkpoint, shutdown flag, live stats) ────────────────
_SHUTDOWN_REQUESTED = False


class RunState:
    __slots__ = ("current_folder", "batch_idx", "counts")

    def __init__(self):
        self.current_folder = "-"
        self.batch_idx = 0
        self.counts = {"ok": 0, "empty": 0, "failed": 0}


_RUN_STATE = RunState()
_CHECKPOINT: Optional[dict] = None


# ── Checkpoint I/O ──────────────────────────────────────────────────────────

def _new_checkpoint() -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "started":      now,
        "last_updated": now,
        "processed":    {},     # folder_name -> {status, elapsed_s, completed}
        "counts":       {"ok": 0, "empty": 0, "failed": 0},
    }


def _load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            cp = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            cp.setdefault("processed", {})
            cp.setdefault("counts", {"ok": 0, "empty": 0, "failed": 0})
            logger.info("Checkpoint loaded: %d folders processed",
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


def _record_folder(cp: dict, folder_name: str, result: dict) -> None:
    cp["processed"][folder_name] = {
        "status":    result.get("status", "?"),
        "elapsed_s": round(result.get("elapsed_s", 0.0), 2),
        "completed": datetime.now().isoformat(timespec="seconds"),
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
    """Call fn() with 3-attempt exponential backoff + 5-min rest cycles.

    Aborts via SystemExit after MAX_RETRY_TOTAL_S (2h) total elapsed.
    Raises RuntimeError if shutdown is requested mid-retry.
    """
    t_start = time.time()
    while True:
        if _SHUTDOWN_REQUESTED:
            raise RuntimeError("Shutdown requested during retry")
        if time.time() - t_start > MAX_RETRY_TOTAL_S:
            msg = (f"Orthanc unreachable for {MAX_RETRY_TOTAL_S/3600:.1f}h — "
                   "aborting. Resume by re-running the same command "
                   "(checkpoint will skip processed folders).")
            logger.error(msg)
            raise SystemExit(msg)

        for delay in RETRY_DELAYS_S:
            if _SHUTDOWN_REQUESTED:
                raise RuntimeError("Shutdown requested during retry")
            try:
                return fn()
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    logger.warning("%s bad_file (HTTP %d); skipping", op_name, e.code)
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


# ── Signal handlers (SIGINT / SIGTERM) ──────────────────────────────────────

def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _SHUTDOWN_REQUESTED
        if _SHUTDOWN_REQUESTED:
            return  # already handling
        _SHUTDOWN_REQUESTED = True
        logger.warning("Signal %s received — flushing checkpoint and exiting",
                       signum)
        if _CHECKPOINT is not None:
            try:
                _save_checkpoint(_CHECKPOINT)
            except Exception as e:
                logger.error("Checkpoint flush failed: %s", e)

    signal.signal(signal.SIGINT, _handler)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (AttributeError, ValueError):
        pass  # SIGTERM unavailable on some platforms


# ── Heartbeat thread ────────────────────────────────────────────────────────

def _heartbeat_loop() -> None:
    while not _SHUTDOWN_REQUESTED:
        try:
            vm = psutil.virtual_memory()
            line = (f"{datetime.now().isoformat(timespec='seconds')} "
                    f"batch={_RUN_STATE.batch_idx} "
                    f"folder={_RUN_STATE.current_folder} "
                    f"ok={_RUN_STATE.counts['ok']} "
                    f"empty={_RUN_STATE.counts['empty']} "
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
    # Truncate previous run's heartbeat — only current run matters
    try:
        HEARTBEAT_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass
    t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    t.start()


# ── Orthanc helpers ─────────────────────────────────────────────────────────

def _orthanc_get(url):
    def _call():
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {_AUTH}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    return _retry_http_call(_call, op_name=f"GET {url.rsplit('/',1)[-1]}")


def _orthanc_post_json(url, data):
    def _call():
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     method="POST")
        req.add_header("Authorization", f"Basic {_AUTH}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    return _retry_http_call(_call, op_name=f"POST {url.rsplit('/',1)[-1]}")


def _orthanc_post_dicom(url, dcm_bytes, overwrite=True):
    def _call():
        req = urllib.request.Request(url, data=dcm_bytes, method="POST")
        req.add_header("Authorization", f"Basic {_AUTH}")
        req.add_header("Content-Type", "application/dicom")
        if overwrite:
            req.add_header("Overwrite", "true")
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    return _retry_http_call(_call, op_name="POST /instances")


def _orthanc_delete(url) -> int:
    def _call():
        req = urllib.request.Request(url, method="DELETE")
        req.add_header("Authorization", f"Basic {_AUTH}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status
    return _retry_http_call(_call, op_name=f"DELETE {url.rsplit('/',1)[-1]}")


# ── Discovery ────────────────────────────────────────────────────────────────

def _list_exam_folders(from_folder: int = 0, limit: int = 0) -> list[Path]:
    all_dirs = sorted([d for d in DATAFILES_DIR.glob("E*")
                        if d.is_dir() and re.match(r"^E\d+$", d.name)])
    selected = all_dirs[from_folder:]
    if limit > 0:
        selected = selected[:limit]
    return selected


def _find_exam_dcm_files(exam_dir: Path) -> list[Path]:
    """All DICOM-looking files in an E-folder (.EX.DCM, .DCM, no-ext)."""
    files = []
    for ext in ("*.EX.DCM", "*.ex.dcm", "*.DCM", "*.dcm"):
        files.extend(exam_dir.glob(ext))
    # Deduplicate by resolved path
    unique = {}
    for f in files:
        unique[f.resolve()] = f
    return sorted(unique.values())


# ── Cleanup: delete existing Cirrus studies ─────────────────────────────────

def _delete_existing_cirrus() -> int:
    """Delete all studies where any series has Manufacturer containing 'Zeiss'."""
    series_ids = _orthanc_post_json(f"{ORTHANC_BASE}/tools/find", {
        "Level": "Series",
        "Query": {"Manufacturer": "*Zeiss*", "Modality": "OPT"},
    })
    study_ids = set()
    print(f"  Series Zeiss/OPT encontradas: {len(series_ids)}")
    for sid in tqdm(series_ids, desc="Buscando padres", unit="ser",
                     file=sys.stdout, mininterval=0.5):
        info = _orthanc_get(f"{ORTHANC_BASE}/series/{sid}")
        p = info.get("ParentStudy", "")
        if p:
            study_ids.add(p)
    print(f"  Estudios Cirrus únicos a borrar: {len(study_ids)}")
    deleted = 0
    for oid in tqdm(sorted(study_ids), desc="Borrando estudios", unit="study",
                     file=sys.stdout, mininterval=0.5):
        try:
            _orthanc_delete(f"{ORTHANC_BASE}/studies/{oid}")
            deleted += 1
        except Exception as e:
            tqdm.write(f"  [WARN] no se pudo borrar {oid[:12]}: {e}")
    return deleted


# ── Per-folder processing ───────────────────────────────────────────────────

def _deobfuscate_to(src: Path, out: Path) -> str:
    """Deobfuscate or copy one .EX.DCM to `out`. Returns 'pixel' / 'analysis' / 'error'.

    carl_deobfuscator.process_dicom_file() silently skips files without
    PixelData. But those files (SOPClass Raw Data Storage) carry the
    clinical measurements — (0073,1140) analysis XML, (0073,1150) ILM,
    (0073,1155) BM, (0073,1255) ONH XML. We must copy them through so
    extract_from_exam can see them.
    """
    import shutil
    from transducin.carl_deobfuscator import process_dicom_file

    try:
        ds = pydicom.dcmread(str(src), force=True)
    except Exception:
        return "error"
    has_pixel = "PixelData" in ds

    if not has_pixel:
        try:
            shutil.copy2(str(src), str(out))
            return "analysis"
        except Exception:
            return "error"

    try:
        if process_dicom_file(src, out, verbose=False, save_png=False):
            return "pixel"
        return "error"
    except Exception:
        return "error"


def _process_folder_test(exam_dir: Path) -> dict:
    """Test mode: deobfuscate in-memory, extract, report CMT + orientation.

    No writes to Orthanc. No writes to disk except temp.
    Returns stats dict.
    """
    from transducin.cirrus_extractor import extract_from_exam

    t0 = time.time()
    src_files = _find_exam_dcm_files(exam_dir)
    if not src_files:
        return {"folder": exam_dir.name, "status": "empty", "n_files": 0,
                "elapsed_s": 0.0, "studies": []}

    # Deobfuscate pixel files AND copy analysis files to tmp dir
    with tempfile.TemporaryDirectory(prefix="cirrus_test_") as tmp:
        tmp_path = Path(tmp)
        ok = err = analysis = 0
        for src in src_files:
            stem = src.name.split(".")[0]
            out = tmp_path / (stem + ".dcm")
            result = _deobfuscate_to(src, out)
            if result == "pixel":
                ok += 1
            elif result == "analysis":
                analysis += 1
            else:
                err += 1

        # Extract clinical
        try:
            clinical_list = extract_from_exam(tmp_path, noel_id="PLACEHOLDER")
        except Exception as e:
            return {"folder": exam_dir.name, "status": "extract_fail",
                    "error": str(e), "n_files": len(src_files),
                    "deobf_ok": ok, "deobf_err": err,
                    "elapsed_s": time.time() - t0, "studies": []}

        # Check B-scan orientation on ANY DICOM with pixel data
        orientation = _check_bscan_orientation(tmp_path)

        studies_summary = []
        for cd in clinical_list:
            studies_summary.append({
                "study_date": cd.study_date,
                "laterality": cd.laterality,
                "type":       cd.study_type,
                "cmt_um":     cd.cmt_um,
                "etdrs_C":    cd.etdrs_grid.C if cd.etdrs_grid else None,
                "rnfl_avg":   cd.rnfl.global_avg if cd.rnfl else None,
                "cdr":        cd.cup_disc_ratio,
            })

    return {
        "folder":     exam_dir.name,
        "status":     "ok",
        "n_files":    len(src_files),
        "deobf_ok":   ok,
        "deobf_err":  err,
        "analysis":   analysis,
        "orientation": orientation,
        "n_studies":  len(clinical_list),
        "studies":    studies_summary,
        "elapsed_s":  time.time() - t0,
    }


_SOP_OCT_TOMOGRAPHY = "1.2.840.10008.5.1.4.1.1.77.1.5.4"   # Ophthalmic Tomography


def _check_bscan_orientation(tmp_path: Path) -> dict:
    """Inspect one OCT Tomography DICOM header to confirm B-scan orientation.

    Uses only header tags (NumberOfFrames/Rows/Columns) — no pixel_array call,
    because Cirrus DICOMs often have corrupted PhotometricInterpretation after
    deobfuscation ('MONOCHROME2 5  e  p' with trailing garbage) which breaks
    pydicom's pixel decoder. Header-only is sufficient to confirm the
    transpose was applied.

    After transpose: Rows=axial (depth, ~1024), Columns=A-scans (~512).
    NumberOfFrames = n_bscans (2 for HD 5-line, 128/200 for cube).
    """
    for dcm in tmp_path.glob("*.dcm"):
        try:
            ds = pydicom.dcmread(str(dcm), stop_before_pixels=True, force=True)
        except Exception:
            continue
        sop = str(getattr(ds, "SOPClassUID", "")).split("\x00")[0].strip()
        if sop != _SOP_OCT_TOMOGRAPHY:
            continue
        rows = int(getattr(ds, "Rows", 0) or 0)
        cols = int(getattr(ds, "Columns", 0) or 0)
        n_frames_raw = getattr(ds, "NumberOfFrames", 1)
        try:
            n_frames = int(str(n_frames_raw).split("\x00")[0].strip())
        except Exception:
            n_frames = 1
        if rows == 0 or cols == 0:
            continue
        photometric = str(getattr(ds, "PhotometricInterpretation", ""))
        photometric_clean = photometric.split()[0] if photometric else ""
        photometric_ok = photometric_clean in ("MONOCHROME1", "MONOCHROME2")
        return {
            "series":      str(getattr(ds, "SeriesDescription", "?")),
            "shape":       [n_frames, rows, cols],
            "rows_are_axial": rows > cols,
            "bits":        int(getattr(ds, "BitsAllocated", 0) or 0),
            "photometric": photometric,
            "photometric_clean": photometric_ok,
        }
    return {"series": None, "shape": None, "rows_are_axial": None, "bits": 0,
            "photometric": None, "photometric_clean": None}


def _process_folder_execute(exam_dir: Path) -> dict:
    """Execute mode: deobfuscate, upload DICOMs, build+upload SR."""
    from transducin.cirrus_extractor import extract_from_exam
    from transducin.sr_builder import build_sr
    from transducin.noel_resolver import resolve_patient_demographics

    t0 = time.time()
    src_files = _find_exam_dcm_files(exam_dir)
    if not src_files:
        return {"folder": exam_dir.name, "status": "empty", "elapsed_s": 0.0}

    with tempfile.TemporaryDirectory(prefix="cirrus_exec_") as tmp:
        tmp_path = Path(tmp)
        deobf_ok = deobf_err = analysis = 0
        for src in src_files:
            stem = src.name.split(".")[0]
            out = tmp_path / (stem + ".dcm")
            result = _deobfuscate_to(src, out)
            if result == "pixel":
                deobf_ok += 1
            elif result == "analysis":
                analysis += 1
            else:
                deobf_err += 1

        # Sanitize StudyDescription on each deobfuscated DICOM before upload,
        # so studies land in Orthanc with clinical labels (OCT Macular OD, etc.)
        # instead of empty tags.  Mirrors hot_folder_watcher.py behavior.
        dcm_files = sorted(tmp_path.glob("*.dcm"))
        tag_ok = tag_err = 0
        for dcm in dcm_files:
            try:
                ds = pydicom.dcmread(str(dcm))
                apply_cirrus_study_tags(ds)
                ds.save_as(str(dcm), write_like_original=False)
                tag_ok += 1
            except Exception:
                tag_err += 1

        # Upload deobfuscated DICOMs
        upload_ok = upload_err = 0
        for dcm in dcm_files:
            try:
                _orthanc_post_dicom(f"{ORTHANC_BASE}/instances", dcm.read_bytes())
                upload_ok += 1
            except Exception:
                upload_err += 1

        # Extract clinical
        try:
            clinical_list = extract_from_exam(tmp_path, noel_id="PLACEHOLDER")
        except Exception as e:
            return {"folder": exam_dir.name, "status": "extract_fail",
                    "error": str(e), "elapsed_s": time.time() - t0}

        # For each study with measurements, resolve NOEL + build/upload SR
        sr_ok = sr_err = 0
        for cd in clinical_list:
            if not cd.has_measurements():
                continue
            # Resolve NOEL from the deobfuscated DICOM's PatientName
            try:
                first_dcm = dcm_files[0] if dcm_files else None
                if first_dcm:
                    ref_ds = pydicom.dcmread(str(first_dcm), stop_before_pixels=True)
                    cd.patient_name = str(getattr(ref_ds, "PatientName", "") or cd.patient_name)
                    cd.patient_dob  = str(getattr(ref_ds, "PatientBirthDate", "") or cd.patient_dob)
                demo = resolve_patient_demographics(
                    patient_name=cd.patient_name or "",
                    patient_dob=cd.patient_dob,
                )
                cd.noel_id = demo["noel_id"] or cd.noel_id
                if demo["patient_dob"]:
                    cd.patient_dob = demo["patient_dob"]

                # Build and upload SR
                with tempfile.NamedTemporaryFile(suffix="_SR.dcm", delete=False) as tf:
                    sr_path = Path(tf.name)
                try:
                    build_sr(cd, reference_dataset=ref_ds if first_dcm else None,
                             output_path=sr_path)
                    _orthanc_post_dicom(f"{ORTHANC_BASE}/instances",
                                         sr_path.read_bytes())
                    sr_ok += 1
                finally:
                    sr_path.unlink(missing_ok=True)
            except Exception:
                sr_err += 1

    return {
        "folder":      exam_dir.name,
        "status":      "ok",
        "n_files":     len(src_files),
        "deobf_ok":    deobf_ok,
        "deobf_err":   deobf_err,
        "upload_ok":   upload_ok,
        "upload_err":  upload_err,
        "n_studies":   len(clinical_list),
        "sr_ok":       sr_ok,
        "sr_err":      sr_err,
        "elapsed_s":   time.time() - t0,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def _print_test_report(results: list[dict], estimate: bool = False,
                        total_folders: int = 0) -> None:
    print("\n" + "═" * 70)
    print("TEST REPORT — first {} folders".format(len(results)))
    print("═" * 70)
    for r in results:
        print(f"\n[{r['folder']}] ({r['elapsed_s']:.1f}s, {r['n_files']} files, "
              f"pixel={r.get('deobf_ok',0)} analysis={r.get('analysis',0)} "
              f"err={r.get('deobf_err',0)})")
        if r.get("orientation"):
            o = r["orientation"]
            print(f"  B-scan: series={o['series']!r} shape={o['shape']} "
                  f"rows_are_axial={o['rows_are_axial']} bits={o['bits']}")
            if o.get("photometric") and not o.get("photometric_clean"):
                print(f"    ⚠ PhotometricInterpretation corrupto: {o['photometric']!r}")
        for s in r.get("studies", []):
            cmt = f"{s['cmt_um']:.1f}µm" if s['cmt_um'] is not None else "N/A"
            c   = f"{s['etdrs_C']:.1f}µm" if s['etdrs_C'] is not None else "N/A"
            rnfl= f"{s['rnfl_avg']:.1f}µm" if s['rnfl_avg'] is not None else "N/A"
            cdr = f"{s['cdr']:.2f}" if s['cdr'] is not None else "N/A"
            print(f"  Study {s['study_date']}/{s['laterality']}/{s['type']}: "
                  f"CMT={cmt}  ETDRS.C={c}  RNFL={rnfl}  C/D={cdr}")
        if r["status"] != "ok":
            print(f"  STATUS: {r['status']} {r.get('error','')}")

    if estimate and results:
        avg_s = sum(r["elapsed_s"] for r in results) / len(results)
        proj_s = avg_s * total_folders
        hrs = proj_s / 3600.0
        print("\n" + "─" * 70)
        print(f"TIME ESTIMATE — {total_folders} folders × {avg_s:.2f}s/folder "
              f"≈ {proj_s:.0f}s  (~{hrs:.1f}h)")
        print("─" * 70)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--test", type=int, nargs="?", const=3, default=0,
                   metavar="N", help="Dry-run on N folders (default 3)")
    g.add_argument("--estimate", action="store_true",
                   help="Test 3 folders + project full-run time")
    g.add_argument("--execute", action="store_true",
                   help="Full re-ingestion with Orthanc writes")
    ap.add_argument("--delete-existing", action="store_true",
                    help="Delete existing Cirrus studies before ingesting (execute mode)")
    ap.add_argument("--from-folder", type=int, default=None,
                    help="Start from E<N>. Overrides checkpoint if set.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N folders (0 = all)")
    ap.add_argument("--batch-size", type=int, default=50,
                    help="Folders per batch before rest (default 50)")
    ap.add_argument("--batch-rest", type=int, default=60,
                    help="Seconds to rest between batches (default 60)")
    ap.add_argument("--fresh", action="store_true",
                    help="Ignore checkpoint and reprocess all folders")
    args = ap.parse_args()

    if not any([args.test, args.estimate, args.execute]):
        args.test = 3   # default

    print("═" * 70)
    mode = "EXECUTE" if args.execute else ("ESTIMATE" if args.estimate else f"TEST ({args.test})")
    print(f"Cirrus reprocess — {mode}")
    print(f"Source: {DATAFILES_DIR}")
    print(f"Orthanc: {ORTHANC_BASE}")
    print("═" * 70)
    sys.stdout.flush()

    from_folder = args.from_folder if args.from_folder is not None else 0
    all_folders = _list_exam_folders(from_folder=from_folder, limit=args.limit)
    total_all = len(_list_exam_folders())  # full count for estimate
    print(f"Total E-folders disponibles: {total_all}")

    if args.estimate or args.test:
        n = args.test if args.test else 3
        sample = all_folders[:n]
        print(f"Muestreando {len(sample)} folder(s) en modo test...")
        results = []
        pbar = tqdm(sample, desc="Cirrus TEST", unit="folder", file=sys.stdout,
                    mininterval=0.5, dynamic_ncols=True)
        for f in pbar:
            r = _process_folder_test(f)
            results.append(r)
            pbar.set_postfix({"ok": sum(1 for x in results if x["status"] == "ok"),
                              "last_s": f"{r['elapsed_s']:.1f}"})
        pbar.close()
        _print_test_report(results, estimate=args.estimate, total_folders=total_all)
        return

    # ── EXECUTE mode ────────────────────────────────────────────────────────
    global _CHECKPOINT
    _CHECKPOINT = _new_checkpoint() if args.fresh else _load_checkpoint()
    _RUN_STATE.counts = dict(_CHECKPOINT["counts"])
    _install_signal_handlers()
    _start_heartbeat()

    if args.delete_existing:
        print("\n⚠  DELETE-EXISTING: borrando estudios Cirrus existentes en Orthanc...")
        n_deleted = _delete_existing_cirrus()
        print(f"  Borrados: {n_deleted} estudios\n")

    # Apply checkpoint: skip already-processed folders unless --from-folder set
    processed_names = set(_CHECKPOINT["processed"].keys())
    if args.from_folder is not None and processed_names:
        logger.warning("--from-folder=%d overrides checkpoint (%d previously processed)",
                       args.from_folder, len(processed_names))
        pending = all_folders
    else:
        pending = [f for f in all_folders if f.name not in processed_names]
        if processed_names:
            print(f"Checkpoint: {len(processed_names)} ya procesados, "
                  f"{len(pending)} pendientes")

    if not pending:
        print("Nada por procesar (todos los folders completados según checkpoint).")
        print("Usa --fresh para reprocesar desde cero.")
        return

    print(f"Procesando {len(pending)} folders en batches de {args.batch_size}"
          f" (rest={args.batch_rest}s entre batches)...")
    print(f"Log detallado: {_LOG_FILE}")
    print(f"Heartbeat:     {HEARTBEAT_FILE}")
    print(f"Checkpoint:    {CHECKPOINT_FILE}")
    sys.stdout.flush()

    total_elapsed = 0.0
    batch_size = max(1, args.batch_size)
    batch_rest = max(0, args.batch_rest)
    batch_ok = batch_fail = batch_empty = 0

    pbar = tqdm(pending, desc="Cirrus EXEC", unit="folder", file=sys.stdout,
                mininterval=0.5, dynamic_ncols=True)
    try:
        for i, f in enumerate(pbar):
            if _SHUTDOWN_REQUESTED:
                break

            _wait_for_memory()
            if _SHUTDOWN_REQUESTED:
                break

            _RUN_STATE.current_folder = f.name
            try:
                r = _process_folder_execute(f)
            except SystemExit:
                raise
            except Exception as e:
                logger.exception("Folder %s: unhandled error", f.name)
                r = {"folder": f.name, "status": "failed",
                     "error": str(e), "elapsed_s": 0.0}

            total_elapsed += r.get("elapsed_s", 0.0)
            status = r.get("status", "failed")
            if status == "ok":
                _RUN_STATE.counts["ok"] += 1
                batch_ok += 1
                logger.info("[OK] %s (%.1fs): deobf %s/%s upload %s SR %s",
                            f.name, r["elapsed_s"], r["deobf_ok"], r["n_files"],
                            r.get("upload_ok", 0), r.get("sr_ok", 0))
            elif status == "empty":
                _RUN_STATE.counts["empty"] += 1
                batch_empty += 1
                logger.info("[EMPTY] %s", f.name)
            else:
                _RUN_STATE.counts["failed"] += 1
                batch_fail += 1
                logger.warning("[FAIL] %s: %s", f.name, r.get("error", "?"))

            _record_folder(_CHECKPOINT, f.name, r)
            _save_checkpoint(_CHECKPOINT)
            pbar.set_postfix(_RUN_STATE.counts)

            # End-of-batch: checkpoint already saved above; rest + gc.
            if (i + 1) % batch_size == 0 and (i + 1) < len(pending):
                _RUN_STATE.batch_idx += 1
                print(f"\nBatch {_RUN_STATE.batch_idx} complete: "
                      f"{batch_ok} ok, {batch_empty} empty, {batch_fail} failed. "
                      f"Resting {batch_rest}s...")
                sys.stdout.flush()
                batch_ok = batch_fail = batch_empty = 0
                gc.collect()
                for _ in range(batch_rest):
                    if _SHUTDOWN_REQUESTED:
                        break
                    time.sleep(1)
    finally:
        pbar.close()
        _save_checkpoint(_CHECKPOINT)

    if _SHUTDOWN_REQUESTED:
        print("═" * 70)
        print(f"INTERRUPTED — checkpoint saved at {CHECKPOINT_FILE}")
        print(f"Progreso: ok={_RUN_STATE.counts['ok']} "
              f"empty={_RUN_STATE.counts['empty']} "
              f"failed={_RUN_STATE.counts['failed']}")
        print("Re-ejecutá el mismo comando para continuar.")
        return

    print("═" * 70)
    print(f"EXECUTE DONE: ok={_RUN_STATE.counts['ok']} "
          f"empty={_RUN_STATE.counts['empty']} "
          f"failed={_RUN_STATE.counts['failed']}  "
          f"total={total_elapsed:.0f}s (~{total_elapsed/3600:.1f}h)")


if __name__ == "__main__":
    main()
