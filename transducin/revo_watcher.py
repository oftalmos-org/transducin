# transducin/revo_watcher.py
# SPDX-License-Identifier: Apache-2.0
#
# Hot folder watcher dedicado para Revo FC130 .OPT en production workstation.
#
# Pipeline:
#   Revo FC130 -> .OPT -> carpeta vigilada
#   -> opt_extractor.extract_from_opt()     -> OCTClinicalData
#   -> revo_opt_reader.opt_to_dicom()       -> DICOM images (OCT/SLO/ENFACE/ANGPRV/OCTA_MIP)
#   -> sr_builder.build_sr()                -> DICOM SR TID 1500
#   -> POST /instances (REST API)           -> Orthanc
#   -> mover .OPT a processed/ con nombre estandarizado
#
# Configuracion via .env:
#   REVO_WATCH_FOLDER       Carpeta de exportacion .OPT del Revo FC130
#   REVO_OUTPUT_DIR         Carpeta de salida DICOM (default: Output/revo)
#   REVO_LOG_DIR            Carpeta de logs (default: logs)
#   REVO_SETTLE_DELAY       Segundos de espera tras deteccion (default: 2)
#   ORTHANC_HOST            Host de Orthanc
#   ORTHANC_HTTP_PORT       Puerto REST API (default: 8042)
#   ORTHANC_HTTP_USER       Usuario REST API
#   ORTHANC_HTTP_PASS       Contrasena REST API
#
# Uso:
#   python -m transducin.revo_watcher --file archivo.opt --dry-run
#   python -m transducin.revo_watcher --file archivo.opt
#   python -m transducin.revo_watcher --file archivo.opt --no-move
#   python -m transducin.revo_watcher --process-existing
#   python -m transducin.revo_watcher

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from dotenv import load_dotenv
load_dotenv()  # carga .env del directorio de trabajo antes de leer os.environ

from transducin.opt_extractor import extract_from_opt, build_noel_index
from transducin.noel_resolver import resolve_noel_id, resolve_patient_demographics
from transducin.orthanc_client import resolve_study_uid

logger = logging.getLogger("transducin.revo_watcher")

# ── Configuracion (todo via .env) ────────────────────────────────────────────

ORTHANC_HOST      = os.environ.get("ORTHANC_HOST", "127.0.0.1")
ORTHANC_HTTP_PORT = int(os.environ.get("ORTHANC_HTTP_PORT", "8042"))
ORTHANC_HTTP_USER = os.environ.get("ORTHANC_HTTP_USER",
                        os.environ.get("ORTHANC_USER", "orthanc"))
ORTHANC_HTTP_PASS = os.environ.get("ORTHANC_HTTP_PASS",
                        os.environ.get("ORTHANC_PASSWORD", ""))

DEFAULT_WATCH_DIR  = Path(os.environ.get("REVO_WATCH_FOLDER", "input/REVO"))
DEFAULT_OUTPUT_DIR = Path(os.environ.get("REVO_OUTPUT_DIR", "Output/revo"))
DEFAULT_LOG_DIR    = Path(os.environ.get("REVO_LOG_DIR", "logs"))

SETTLE_DELAY = int(os.environ.get("REVO_SETTLE_DELAY", "2"))

# Espera de segmentación: el Revo primero escribe un quick-save sin capas,
# luego corre análisis AI y re-escribe el .opt con los chunks BM/TOP/NFL/GCL/etc.
# Esperar hasta que la segmentación esté presente antes de procesar.
SEG_WAIT_TIMEOUT = int(os.environ.get("REVO_SEG_WAIT_TIMEOUT", "180"))  # 3 min max
SEG_POLL_INTERVAL = int(os.environ.get("REVO_SEG_POLL_INTERVAL", "5"))

_WATCHED_EXTS = {".opt"}


# ── Orthanc REST API ────────────────────────────────────────────────────────

def _orthanc_url() -> str:
    return f"http://{ORTHANC_HOST}:{ORTHANC_HTTP_PORT}"


def _orthanc_auth() -> tuple[str, str]:
    return (ORTHANC_HTTP_USER, ORTHANC_HTTP_PASS)


def _auth_header() -> str:
    user, pwd = _orthanc_auth()
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return f"Basic {token}"


def _upload_to_orthanc(dcm_path: Path) -> bool:
    """Sube un DICOM a Orthanc via POST /instances. Retorna True si exito."""
    url = f"{_orthanc_url()}/instances"
    try:
        data = dcm_path.read_bytes()
    except Exception as e:
        logger.error("  No se pudo leer %s: %s", dcm_path.name, e)
        return False

    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/dicom")
    req.add_header("Authorization", _auth_header())

    try:
        with urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            status = result.get("Status", "")
            oid = result.get("ID", "")
            if status in ("Success", "AlreadyStored"):
                logger.info("  Upload OK: %s -> %s (%s)", dcm_path.name, oid[:12], status)
                return True
            else:
                logger.warning("  Upload status inesperado: %s", result)
                return True  # still stored
    except Exception as e:
        logger.error("  Upload FALLO %s: %s", dcm_path.name, e)
        return False


# ── Nombre estandarizado para processed/ ─────────────────────────────────────

def _standardized_name(cd, suffix: str, ts: Optional[str] = None) -> str:
    """Genera: {NOEL}_{YYYYMMDD}_{LAT}_{TYPE}.opt"""
    prefix = cd.noel_id
    if not prefix:
        prefix = cd.patient_name.replace(" ", "_").replace("^", "_") or "UNKNOWN"
    lat = {"R": "OD", "L": "OS"}.get(cd.laterality, cd.laterality or "XX")
    study_type = (cd.study_type or "unknown").upper()
    date = cd.study_date or "00000000"
    name = f"{prefix}_{date}_{lat}_{study_type}"
    if ts:
        name = f"{name}_{ts}"
    return f"{name}{suffix}"


def _move_to_processed(src: Path, cd, do_move: bool = True) -> None:
    """Mueve .OPT a processed/ con nombre estandarizado."""
    if not do_move:
        logger.info("  --no-move: archivo original no movido.")
        return
    try:
        processed_dir = src.parent / "processed"
        processed_dir.mkdir(exist_ok=True)
        dest = processed_dir / _standardized_name(cd, src.suffix)
        if dest.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = processed_dir / _standardized_name(cd, src.suffix, ts)
        shutil.move(str(src), str(dest))
        logger.info("  Movido a processed/: %s", dest.name)
    except Exception as e:
        logger.error("  No se pudo mover a processed/: %s", e)


# ── Espera de segmentación ───────────────────────────────────────────────────

def _wait_for_segmentation(
    opt_path: Path,
    timeout: int = SEG_WAIT_TIMEOUT,
    poll: int = SEG_POLL_INTERVAL,
) -> bool:
    """Espera a que el .opt contenga chunks de segmentación (BM, TOP).

    El Revo FC130 escribe el .opt en dos etapas:
      1. Quick-save inmediato (sin capas) — ~126 MB para un macular estándar
      2. Re-save post-segmentación AI (con capas) — ~147 MB

    Si se procesa antes de la etapa 2, las mediciones (CMT, ETDRS, RNFL)
    salen como None y el SR se genera vacío.

    Args:
        opt_path: Ruta al .opt bajo observación.
        timeout:  Máximo segundos a esperar.
        poll:     Intervalo entre chequeos.

    Returns:
        True si la segmentación apareció antes del timeout.
        False si el timeout venció (procesar de todas formas, con CMT=N/A).
    """
    from transducin.revo_opt_reader import has_segmentation

    if has_segmentation(opt_path):
        return True

    logger.info("  Esperando segmentación AI (max %ds)...", timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)
        if not opt_path.exists():
            logger.warning("  Archivo desapareció durante espera: %s", opt_path.name)
            return False
        if has_segmentation(opt_path):
            elapsed = timeout - int(deadline - time.time())
            logger.info("  Segmentación detectada tras %ds", elapsed)
            return True

    logger.warning("  Timeout esperando segmentación (%ds) — procesando sin capas",
                    timeout)
    return False


# ── Pipeline principal ───────────────────────────────────────────────────────

def process_opt(
    opt_path: Path,
    output_dir: Path,
    noel_index: Optional[dict[str, str]] = None,
    do_upload: bool = True,
    do_move: bool = True,
    wait_for_segmentation: bool = True,
) -> bool:
    """Pipeline completo: .OPT -> extract -> DICOM images -> SR -> upload -> move.

    Returns True si completo sin errores criticos.
    """
    logger.info("=== Procesando: %s", opt_path.name)
    success = True

    # 0. Esperar a que Revo termine la segmentación (salvo reprocesado explícito)
    if wait_for_segmentation:
        _wait_for_segmentation(opt_path)

    # 1. Extraer metadatos clinicos
    try:
        cd = extract_from_opt(opt_path, noel_index=noel_index)
        logger.info("  Extraccion: noel=%s lat=%s type=%s date=%s confidence=%s",
                     cd.noel_id or "(none)", cd.laterality, cd.study_type,
                     cd.study_date, cd.extraction_confidence)
    except Exception as e:
        logger.error("  Extraccion FALLO: %s", e)
        return False

    # 1b. Resolve NOEL ID + demographics via 3-tier lookup
    demo = resolve_patient_demographics(
        patient_name=cd.patient_name or "",
        patient_dob=cd.patient_dob,
        filename_index=noel_index,
    )
    if not cd.noel_id:
        cd.noel_id = demo["noel_id"]
    if not cd.patient_dob and demo["patient_dob"]:
        cd.patient_dob = demo["patient_dob"]
    if demo["patient_name"] and "^" in demo["patient_name"]:
        cd.patient_name = demo["patient_name"]
    patient_sex = demo["patient_sex"]
    logger.info("  NOEL resolver: %s DOB=%s sex=%s", cd.noel_id, cd.patient_dob, patient_sex)

    # Build StudyDescription from study type + laterality
    lat_str = {"R": "OD", "L": "OS"}.get(cd.laterality, "")
    type_str = (cd.study_type or "").replace("_", " ").title()
    study_desc = f"Revo FC130 {type_str} {lat_str}".strip()

    # 2. Convertir .opt -> DICOM images
    dcm_paths: list[Path] = []
    if cd.study_type not in ("bmetr", "biometry"):
        try:
            from transducin.revo_opt_reader import opt_to_dicom
            img_dir = output_dir / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            dcm_paths = opt_to_dicom(
                opt_path, img_dir,
                noel_id=cd.noel_id or "",
                study_date=cd.study_date or "",
                study_time=cd.study_time or "",
                laterality=cd.laterality or "",
                patient_name=cd.patient_name or "",
                patient_dob=cd.patient_dob or "",
                patient_sex=patient_sex,
                study_type=cd.study_type or "",
                study_description=study_desc,
            )
            for p in dcm_paths:
                logger.info("  Imagen DICOM: %s", p.name)
        except Exception as e:
            logger.error("  Conversion FALLO: %s", e)
            success = False
    else:
        logger.info("  Biometry -- sin imagen.")

    # 3. Generar SR TID 1500
    sr_path: Optional[Path] = None
    if cd.study_type == "unknown" and not cd.has_measurements():
        logger.info("  SR omitido: tipo unknown sin mediciones.")
    else:
        try:
            from transducin.sr_builder import build_sr
            import pydicom
            from pydicom.uid import generate_uid as _gen_uid

            ref_ds = pydicom.dcmread(str(dcm_paths[0])) if dcm_paths else None
            fallback_uid = ref_ds.StudyInstanceUID if ref_ds else str(_gen_uid())

            study_uid = resolve_study_uid(
                noel_id=cd.noel_id,
                study_date=cd.study_date or "",
                fallback_uid=fallback_uid,
                orthanc_base_url=_orthanc_url(),
                auth=_orthanc_auth(),
            )

            stem = opt_path.stem.replace(" ", "_")
            sr_path = output_dir / "sr" / f"{stem}_SR.dcm"
            build_sr(cd, reference_dataset=ref_ds, output_path=sr_path,
                     study_instance_uid=study_uid)
            logger.info("  SR generado: %s (study_uid=...%s)", sr_path.name, study_uid[-12:])
        except Exception as e:
            logger.error("  Generacion SR FALLO: %s", e)
            sr_path = None
            success = False

    # 4. Upload a Orthanc via REST API
    if do_upload:
        for dcm_p in dcm_paths:
            if dcm_p.exists():
                ok = _upload_to_orthanc(dcm_p)
                if not ok:
                    logger.error("  Upload imagen FALLO: %s", dcm_p.name)
                    success = False
        if sr_path and sr_path.exists():
            ok = _upload_to_orthanc(sr_path)
            if not ok:
                logger.error("  Upload SR FALLO: %s", sr_path.name)
                success = False
    else:
        logger.info("  Upload omitido (do_upload=False)")

    # 5. Mover .OPT a processed/
    _move_to_processed(opt_path, cd, do_move=do_move)

    status = "OK" if success else "PARCIAL"
    logger.info("  Resultado: %s -- %s", status, opt_path.name)
    return success


# ── Dry-run ──────────────────────────────────────────────────────────────────

def _dry_run_single(opt_path: Path, output_dir: Path,
                    noel_index: Optional[dict[str, str]] = None) -> None:
    """Extrae datos clinicos y genera SR localmente -- sin upload, sin mover."""
    from transducin.revo_opt_reader import opt_to_dicom
    from transducin.sr_builder import build_sr

    logger.info("=== DRY RUN: %s", opt_path.name)

    try:
        cd = extract_from_opt(opt_path, noel_index=noel_index)
    except Exception as e:
        logger.error("  Extraccion FALLO: %s", e)
        return

    if not cd.noel_id:
        cd.noel_id = resolve_noel_id(
            patient_name=cd.patient_name or "",
            patient_dob=cd.patient_dob,
            filename_index=noel_index,
        )

    print("\n--- Extraccion ---")
    print(f"  NOEL ID:     {cd.noel_id or '(none)'}")
    print(f"  Paciente:    {cd.patient_name}")
    print(f"  DOB:         {cd.patient_dob}")
    print(f"  Lateralidad: {cd.laterality}")
    print(f"  Tipo:        {cd.study_type}")
    print(f"  Fecha:       {cd.study_date}")
    print(f"  Confidence:  {cd.extraction_confidence}")
    print(f"  SQI:         {cd.sqi_mean}" if cd.sqi_mean is not None else "  SQI:         N/A")

    print("\n--- Mediciones ---")
    print(f"  CMT:         {cd.cmt_um} um" if cd.cmt_um else "  CMT:         N/A")
    if cd.etdrs_grid:
        g = cd.etdrs_grid
        print(f"  ETDRS:       C={g.C} S1={g.S1} N1={g.N1} I1={g.I1} T1={g.T1}")
        print(f"               S2={g.S2} N2={g.N2} I2={g.I2} T2={g.T2}")
    else:
        print("  ETDRS:       N/A")
    if cd.rnfl:
        r = cd.rnfl
        print(f"  RNFL:        avg={r.global_avg} S={r.superior} I={r.inferior} N={r.nasal} T={r.temporal}")
    else:
        print("  RNFL:        N/A")
    if cd.gcl_ipl:
        g = cd.gcl_ipl
        print(f"  mGCIPL:      avg={g.global_avg} S={g.superior} I={g.inferior} N={g.nasal} T={g.temporal}")
    else:
        print("  mGCIPL:      N/A")
    print(f"  has_measurements(): {cd.has_measurements()}")

    if cd.confidence_notes:
        print("\n--- Notas ---")
        for note in cd.confidence_notes:
            print(f"  - {note}")

    # DICOM images
    print("\n--- Conversion DICOM ---")
    dcm_paths = []
    if cd.study_type not in ("bmetr", "biometry"):
        try:
            img_dir = output_dir / "dry_run" / "images"
            dcm_paths = opt_to_dicom(
                opt_path, img_dir,
                noel_id=cd.noel_id or "",
                study_date=cd.study_date or "",
                study_time=cd.study_time or "",
                laterality=cd.laterality or "",
                patient_name=cd.patient_name or "",
                patient_dob=cd.patient_dob or "",
                study_type=cd.study_type or "",
            )
            for p in dcm_paths:
                print(f"  Imagen: {p.name}")
        except Exception as e:
            logger.error("  Conversion FALLO: %s", e)

    # SR
    print("\n--- SR TID 1500 ---")
    if not cd.has_measurements() and cd.study_type == "unknown":
        print("  OMITIDO: tipo unknown sin mediciones")
        return

    try:
        import pydicom
        ref_ds = pydicom.dcmread(str(dcm_paths[0])) if dcm_paths else None
        sr_dir = output_dir / "dry_run" / "sr"
        stem = opt_path.stem.replace(" ", "_")
        sr_path = sr_dir / f"{stem}_SR.dcm"
        sr_ds = build_sr(cd, reference_dataset=ref_ds, output_path=sr_path)
        print(f"  SR guardado: {sr_path}")
        print(f"  PatientID:   {sr_ds.PatientID}")
        print(f"  StudyDate:   {sr_ds.StudyDate}")
        print(f"  Modality:    {sr_ds.Modality}")
        print(f"  ContentItems:{len(getattr(sr_ds, 'ContentSequence', []))}")
        print("\n  [DRY RUN] No se envio a Orthanc. No se movio el archivo original.")
    except Exception as e:
        logger.error("  Generacion SR FALLO: %s", e)


# ── Procesamiento de archivos existentes ─────────────────────────────────────

def process_existing(watch_dir: Path, output_dir: Path,
                     noel_index: Optional[dict[str, str]] = None,
                     do_upload: bool = True, do_move: bool = True) -> None:
    """Procesa todos los .OPT ya existentes en la carpeta vigilada."""
    pending = [
        f for f in watch_dir.rglob("*.opt")
        if "processed" not in f.parts
    ]
    if not pending:
        logger.info("No hay archivos .OPT pendientes en %s", watch_dir)
        return

    logger.info("Procesando %d archivo(s) .OPT existentes...", len(pending))
    for f in sorted(pending):
        try:
            process_opt(f, output_dir, noel_index=noel_index,
                        do_upload=do_upload, do_move=do_move)
        except Exception as e:
            logger.error("Error procesando %s: %s", f.name, e)


# ── Watchdog handler ─────────────────────────────────────────────────────────

class RevoOptHandler(FileSystemEventHandler):
    """Handler de watchdog para archivos .OPT del Revo FC130."""

    def __init__(self, output_dir: Path, noel_index: Optional[dict[str, str]] = None,
                 do_upload: bool = True, do_move: bool = True):
        self.output_dir = output_dir
        self.noel_index = noel_index or {}
        self.do_upload = do_upload
        self.do_move = do_move
        self._processing: set[str] = set()

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in _WATCHED_EXTS:
            return
        if "processed" in path.parts:
            return

        key = str(path)
        if key in self._processing:
            return
        self._processing.add(key)

        time.sleep(SETTLE_DELAY)

        if not path.exists():
            self._processing.discard(key)
            return

        try:
            process_opt(path, self.output_dir, noel_index=self.noel_index,
                        do_upload=self.do_upload, do_move=self.do_move)
        except Exception as e:
            logger.error("Error no capturado procesando %s: %s", path.name, e)
        finally:
            self._processing.discard(key)


# ── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"revo_{datetime.now().strftime('%Y%m%d')}.log"
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(str(log_file)),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger.info("Log iniciado: %s", log_file)


# ── Entrypoint ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transducin Revo FC130 -- watcher .OPT -> DICOM + SR -> Orthanc"
    )
    parser.add_argument("--watch", default=str(DEFAULT_WATCH_DIR),
                        help=f"Carpeta .OPT (default: {DEFAULT_WATCH_DIR})")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR),
                        help=f"Carpeta salida DICOM (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--logs", default=str(DEFAULT_LOG_DIR),
                        help=f"Carpeta de logs (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--no-upload", action="store_true",
                        help="Deshabilitar upload a Orthanc (solo generar localmente)")
    parser.add_argument("--no-move", action="store_true",
                        help="No mover el archivo original a processed/")
    parser.add_argument("--process-existing", action="store_true",
                        help="Procesar archivos .OPT existentes antes de vigilar")
    parser.add_argument("--file", default=None,
                        help="Procesar un solo archivo .OPT y salir")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo extraer y generar SR -- sin upload, sin mover")
    args = parser.parse_args()

    output_dir = Path(args.output).expanduser().resolve()
    log_dir    = Path(args.logs).expanduser().resolve()
    do_upload  = not args.no_upload
    do_move    = not args.no_move

    if args.dry_run:
        do_upload = False
        do_move = False

    _setup_logging(log_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Orthanc REST: %s (user=%s)", _orthanc_url(), ORTHANC_HTTP_USER)

    # ── --file mode ──────────────────────────────────────────────────────────
    if args.file:
        opt_path = Path(args.file).expanduser().resolve()
        if not opt_path.exists():
            logger.error("Archivo no encontrado: %s", opt_path)
            sys.exit(1)

        noel_idx = build_noel_index(opt_path.parent)

        if args.dry_run:
            _dry_run_single(opt_path, output_dir, noel_index=noel_idx)
        else:
            process_opt(opt_path, output_dir, noel_index=noel_idx,
                        do_upload=do_upload, do_move=do_move)
        return

    # ── watcher mode ─────────────────────────────────────────────────────────
    watch_dir = Path(args.watch).expanduser().resolve()
    if not watch_dir.exists():
        logger.error("Carpeta no existe: %s", watch_dir)
        sys.exit(1)

    logger.info("Revo FC130 watcher iniciando")
    logger.info("  Vigilando: %s", watch_dir)
    logger.info("  Salida:    %s", output_dir)
    logger.info("  Upload:    %s | Move: %s", do_upload, do_move)

    noel_idx = build_noel_index(watch_dir)

    if args.process_existing:
        process_existing(watch_dir, output_dir, noel_index=noel_idx,
                         do_upload=do_upload, do_move=do_move)

    handler = RevoOptHandler(output_dir=output_dir, noel_index=noel_idx,
                             do_upload=do_upload, do_move=do_move)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()
    logger.info("Observador iniciado. Ctrl+C para detener.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Deteniendo watcher Revo...")
        observer.stop()

    observer.join()
    logger.info("Watcher Revo detenido.")


if __name__ == "__main__":
    main()
