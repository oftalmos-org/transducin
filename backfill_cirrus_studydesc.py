"""
backfill_cirrus_studydesc.py
Backfill empty StudyDescription on Cirrus HD-OCT studies already in Orthanc.

Scope: Cirrus-only (Manufacturer contains "Zeiss"). The Revo counterpart is
backfill_revo_studydesc.py.  This covers studies uploaded by
reprocess_cirrus.py BEFORE cirrus_tags.apply_cirrus_study_tags() was added
(folders E000-E194 from the 2026-04-13 run).

For each target study we derive StudyDescription from the first OPT series'
SeriesDescription + ImageLaterality using the same inference helpers as the
live pipeline, then Replace the tag via Orthanc /studies/{id}/modify.

Safety: checkpoint (per study), HTTP retry with 2h abort, SIGINT/SIGTERM
graceful shutdown, heartbeat file, batched modifications with rest.

Usage
-----
  python backfill_cirrus_studydesc.py                # dry-run (default)
  python backfill_cirrus_studydesc.py --execute      # apply to Orthanc
  python backfill_cirrus_studydesc.py --execute --batch-size 50 --batch-rest 60
  python backfill_cirrus_studydesc.py --execute --fresh    # ignore checkpoint
"""
from __future__ import annotations

import argparse
import base64
import gc
import json
import logging
import os
import signal
import sys
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
from tqdm import tqdm

from transducin.cirrus_tags import infer_study_type
from transducin.clinical_data import study_description_label


# ── Logging: WARNING+ to stdout, INFO+ to file ──────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FILE = LOG_DIR / f"backfill_cirrus_{_TS}.log"

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

logger = logging.getLogger("backfill_cirrus")


# ── Config ──────────────────────────────────────────────────────────────────
BASE = f"http://{os.environ['ORTHANC_HOST']}:{os.environ['ORTHANC_HTTP_PORT']}"
AUTH = base64.b64encode(
    f"{os.environ['ORTHANC_HTTP_USER']}:{os.environ['ORTHANC_HTTP_PASS']}".encode()
).decode()

CHECKPOINT_FILE       = LOG_DIR / "backfill_cirrus_checkpoint.json"
HEARTBEAT_FILE        = LOG_DIR / "backfill_cirrus_heartbeat.log"

RETRY_DELAYS_S        = (5, 15, 30)
NAS_OUTAGE_REST_S     = 300
MAX_RETRY_TOTAL_S     = 2 * 3600
HEARTBEAT_INTERVAL_S  = 60


# ── Shared run state ────────────────────────────────────────────────────────
_SHUTDOWN_REQUESTED = False


class RunState:
    __slots__ = ("current_study", "batch_idx", "counts")

    def __init__(self):
        self.current_study = "-"
        self.batch_idx = 0
        self.counts = {"ok": 0, "dry": 0, "failed": 0, "skipped": 0}


_RUN_STATE = RunState()
_CHECKPOINT: Optional[dict] = None


# ── Checkpoint ──────────────────────────────────────────────────────────────

def _new_checkpoint() -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "started":      now,
        "last_updated": now,
        "processed":    {},   # study_id -> {status, label, completed}
        "counts":       {"ok": 0, "dry": 0, "failed": 0, "skipped": 0},
    }


def _load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            cp = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            cp.setdefault("processed", {})
            cp.setdefault("counts", {"ok": 0, "dry": 0, "failed": 0, "skipped": 0})
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


def _record_study(cp: dict, study_id: str, status: str, label: str) -> None:
    cp["processed"][study_id] = {
        "status":    status,
        "label":     label,
        "completed": datetime.now().isoformat(timespec="seconds"),
    }
    cp["counts"] = dict(_RUN_STATE.counts)


# ── HTTP retry ──────────────────────────────────────────────────────────────

def _retry_http_call(fn, op_name: str = "http"):
    """3 attempts (5/15/30s) + 5-min rest cycles; abort after 2h total."""
    t_start = time.time()
    while True:
        if _SHUTDOWN_REQUESTED:
            raise RuntimeError("Shutdown requested during retry")
        if time.time() - t_start > MAX_RETRY_TOTAL_S:
            msg = (f"Orthanc unreachable for {MAX_RETRY_TOTAL_S/3600:.1f}h — "
                   "aborting. Resume by re-running the same command "
                   "(checkpoint will skip processed studies).")
            logger.error(msg)
            raise SystemExit(msg)

        for delay in RETRY_DELAYS_S:
            if _SHUTDOWN_REQUESTED:
                raise RuntimeError("Shutdown requested during retry")
            try:
                return fn()
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                logger.warning("%s fail (%s); retry en %ds", op_name, e, delay)
                time.sleep(delay)

        logger.error("%s: Orthanc no responde tras 3 intentos; "
                     "pausa %ds antes del próximo ciclo", op_name, NAS_OUTAGE_REST_S)
        for _ in range(NAS_OUTAGE_REST_S):
            if _SHUTDOWN_REQUESTED:
                raise RuntimeError("Shutdown requested during retry rest")
            if time.time() - t_start > MAX_RETRY_TOTAL_S:
                break
            time.sleep(1)


def req_get(url):
    def _call():
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {AUTH}")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    return _retry_http_call(_call, op_name=f"GET {url.rsplit('/',1)[-1]}")


def req_post(url, data, timeout=600):
    def _call():
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     method="POST")
        req.add_header("Authorization", f"Basic {AUTH}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    return _retry_http_call(_call, op_name=f"POST {url.rsplit('/',1)[-1]}")


# ── Signals + heartbeat ─────────────────────────────────────────────────────

def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        global _SHUTDOWN_REQUESTED
        if _SHUTDOWN_REQUESTED:
            return
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
        pass


def _heartbeat_loop() -> None:
    while not _SHUTDOWN_REQUESTED:
        try:
            vm = psutil.virtual_memory()
            line = (f"{datetime.now().isoformat(timespec='seconds')} "
                    f"batch={_RUN_STATE.batch_idx} "
                    f"study={_RUN_STATE.current_study} "
                    f"ok={_RUN_STATE.counts['ok']} "
                    f"dry={_RUN_STATE.counts['dry']} "
                    f"skipped={_RUN_STATE.counts['skipped']} "
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
    t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    t.start()


# ── Derivation ──────────────────────────────────────────────────────────────

def _pick_opt_series(series_infos: list[dict]) -> Optional[dict]:
    """Return the first series whose Modality is OPT (has PixelData), fallback
    to the first series if none flagged."""
    for s in series_infos:
        if s.get("MainDicomTags", {}).get("Modality", "") == "OPT":
            return s
    return series_infos[0] if series_infos else None


def _derive_label(series_infos: list[dict]) -> str:
    """Build StudyDescription using SeriesDescription + ImageLaterality from
    the first OPT series.  Mirrors cirrus_tags.apply_cirrus_study_tags()."""
    s = _pick_opt_series(series_infos)
    if not s:
        return study_description_label("unknown", "")
    tags = s.get("MainDicomTags", {})
    series_desc = tags.get("SeriesDescription", "") or ""
    lat = (tags.get("ImageLaterality", "") or "").strip().upper()
    if lat not in ("R", "L"):
        lat = ""
    study_type = infer_study_type(series_desc)
    return study_description_label(study_type, lat)


# ── Discovery ───────────────────────────────────────────────────────────────

def find_targets() -> list[dict]:
    """Return Cirrus OPT studies with empty StudyDescription, each annotated
    with its pre-fetched series infos for label derivation."""
    studies = req_post(f"{BASE}/tools/find", {
        "Level": "Study",
        "Query": {"ModalitiesInStudy": "OPT"},
        "Expand": True,
    })

    empty_desc = [
        st for st in studies
        if not st.get("MainDicomTags", {}).get("StudyDescription", "").strip()
    ]

    cirrus_targets = []
    for st in tqdm(empty_desc, desc="Filtering Cirrus", unit="study",
                   file=sys.stdout, mininterval=0.5):
        if _SHUTDOWN_REQUESTED:
            break
        series_ids = st.get("Series", [])
        if not series_ids:
            continue
        try:
            first = req_get(f"{BASE}/series/{series_ids[0]}")
        except Exception:
            continue
        mfr = first.get("MainDicomTags", {}).get("Manufacturer", "").lower()
        if "zeiss" not in mfr:
            continue
        series_infos = [first]
        for sid in series_ids[1:5]:
            try:
                series_infos.append(req_get(f"{BASE}/series/{sid}"))
            except Exception:
                pass
        st["_series_infos"] = series_infos
        cirrus_targets.append(st)
    return cirrus_targets


# ── Per-study work ──────────────────────────────────────────────────────────

def process_one(study: dict, execute: bool) -> tuple[str, str]:
    """Apply (or preview) StudyDescription backfill for one study.

    Returns (status, label).  status ∈ {"ok", "dry", "skipped", "failed"}.
    """
    sid = study.get("ID", "")
    label = _derive_label(study.get("_series_infos", []))

    if not label:
        logger.warning("[SKIP] %s: could not derive label", sid[:12])
        return "skipped", ""

    if not execute:
        logger.info("[DRY] %s → %r", sid[:12], label)
        return "dry", label

    try:
        req_post(f"{BASE}/studies/{sid}/modify", {
            "Replace":    {"StudyDescription": label},
            "Force":      True,
            "KeepSource": False,
        })
        logger.info("[OK] %s → %r", sid[:12], label)
        return "ok", label
    except Exception as e:
        logger.warning("[FAIL] %s → %r: %s", sid[:12], label, e)
        return "failed", label


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--execute", action="store_true",
                    help="Modify Orthanc (default: dry-run)")
    ap.add_argument("--batch-size", type=int, default=100,
                    help="Studies per batch before rest (default 100)")
    ap.add_argument("--batch-rest", type=int, default=30,
                    help="Seconds to rest between batches (default 30)")
    ap.add_argument("--fresh", action="store_true",
                    help="Ignore checkpoint and reprocess all targets")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print("═" * 70)
    print(f"Cirrus StudyDescription backfill — {mode}")
    print(f"Orthanc: {BASE}")
    print("═" * 70)
    sys.stdout.flush()

    global _CHECKPOINT
    _CHECKPOINT = _new_checkpoint() if args.fresh else _load_checkpoint()
    _RUN_STATE.counts = dict(_CHECKPOINT["counts"])
    _install_signal_handlers()
    _start_heartbeat()

    print("Descubriendo targets en Orthanc...")
    sys.stdout.flush()
    try:
        targets = find_targets()
    except SystemExit:
        raise
    except Exception as e:
        logger.error("find_targets failed: %s", e)
        print(f"\n[ERROR] Discovery falló: {e}")
        return

    print(f"Cirrus OPT studies con StudyDescription vacío: {len(targets)}")

    processed_ids = set(_CHECKPOINT["processed"].keys())
    pending = [st for st in targets if st.get("ID") not in processed_ids]
    if processed_ids:
        print(f"Checkpoint: {len(processed_ids)} ya procesados, "
              f"{len(pending)} pendientes")

    if not pending:
        print("Nothing to do.")
        print("Usa --fresh para reprocesar desde cero.")
        return

    print(f"Procesando {len(pending)} studies en batches de {args.batch_size}"
          f" (rest={args.batch_rest}s entre batches)...")
    print(f"Log detallado: {_LOG_FILE}")
    print(f"Heartbeat:     {HEARTBEAT_FILE}")
    print(f"Checkpoint:    {CHECKPOINT_FILE}")
    sys.stdout.flush()

    t0 = time.time()
    batch_size = max(1, args.batch_size)
    batch_rest = max(0, args.batch_rest)
    batch_ok = batch_dry = batch_fail = batch_skip = 0

    pbar = tqdm(pending, desc="Backfill Cirrus", unit="study",
                file=sys.stdout, mininterval=0.5, dynamic_ncols=True)
    try:
        for i, st in enumerate(pbar):
            if _SHUTDOWN_REQUESTED:
                break

            sid = st.get("ID", "")
            _RUN_STATE.current_study = sid[:12]
            try:
                status, label = process_one(st, execute=args.execute)
            except SystemExit:
                raise
            except Exception:
                logger.exception("Study %s: unhandled error", sid[:12])
                status, label = "failed", ""

            if status == "ok":
                _RUN_STATE.counts["ok"] += 1
                batch_ok += 1
            elif status == "dry":
                _RUN_STATE.counts["dry"] += 1
                batch_dry += 1
            elif status == "skipped":
                _RUN_STATE.counts["skipped"] += 1
                batch_skip += 1
            else:
                _RUN_STATE.counts["failed"] += 1
                batch_fail += 1

            _record_study(_CHECKPOINT, sid, status, label)
            _save_checkpoint(_CHECKPOINT)
            pbar.set_postfix(_RUN_STATE.counts)

            if (i + 1) % batch_size == 0 and (i + 1) < len(pending):
                _RUN_STATE.batch_idx += 1
                print(f"\nBatch {_RUN_STATE.batch_idx} complete: "
                      f"{batch_ok} ok, {batch_dry} dry, "
                      f"{batch_skip} skipped, {batch_fail} failed. "
                      f"Resting {batch_rest}s...")
                sys.stdout.flush()
                batch_ok = batch_dry = batch_fail = batch_skip = 0
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
        print(f"Progreso: {_RUN_STATE.counts}")
        print("Re-ejecutá el mismo comando para continuar.")
        return

    print("═" * 70)
    print(f"DONE — {_RUN_STATE.counts}  elapsed={time.time()-t0:.1f}s")
    print("═" * 70)
    if not args.execute:
        print("Nothing modified. --execute para aplicar.")


if __name__ == "__main__":
    main()
