# transducin/pts925_watcher.py
# SPDX-License-Identifier: Apache-2.0
#
# Orthanc-polling service para PTS 925Wi.
#
# El PTS 925Wi envía DICOM OPV directamente a Orthanc via C-STORE.
# Este servicio sondea Orthanc periódicamente buscando estudios OPV
# que aún no tienen un SR pareado, los descarga, extrae los datos
# clínicos, genera un SR TID 1500 y lo sube de vuelta a Orthanc.
#
# Arquitectura:
#   PTS 925Wi ──C-STORE──► Orthanc (OPV ya presente)
#                              │
#   pts925_watcher (polling) ◄─┘
#     1. GET /tools/find  Modality=OPV  → lista de series OPV
#     2. Para cada serie OPV, verificar si el estudio ya tiene un SR Transducin
#     3. Si no tiene SR:
#        a. GET /instances/{id}/file  → DICOM OPV en memoria
#        b. pts925_extractor.extract_from_dicom() → VisualFieldData
#        c. vf_sr_builder.build_vf_sr()                → DICOM SR
#        d. POST /instances (upload)                   → SR en Orthanc
#     4. Dormir PTS_POLL_INTERVAL_SECONDS y repetir
#
# Configuración via .env:
#   PTS_POLL_INTERVAL_SECONDS   Intervalo de sondeo (default: 300 = 5 min)
#   PTS_SR_OUTPUT_DIR           Carpeta local para guardar copia del SR (opcional)
#   ORTHANC_HOST / ORTHANC_HTTP_PORT / ORTHANC_HTTP_USER / ORTHANC_HTTP_PASS
#
# Uso:
#   python -m transducin.pts925_watcher
#   python -m transducin.pts925_watcher --once          # una sola pasada, sin loop
#   python -m transducin.pts925_watcher --interval 60   # cada 60 segundos

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
import base64
import json

import pydicom
from pydicom.dataset import Dataset

from transducin.pts925_extractor import extract_from_dicom
from transducin.vf_sr_builder import build_vf_sr
from transducin.dicom_config import SOP_PERIMETRY, MANUFACTURER

logger = logging.getLogger("transducin.pts925_watcher")

# ── Configuración (todo via .env) ────────────────────────────────────────────

ORTHANC_HOST      = os.environ.get("ORTHANC_HOST", "127.0.0.1")
ORTHANC_HTTP_PORT = int(os.environ.get("ORTHANC_HTTP_PORT", "8042"))
ORTHANC_HTTP_USER = os.environ.get("ORTHANC_HTTP_USER", "orthanc")
ORTHANC_HTTP_PASS = os.environ.get("ORTHANC_HTTP_PASS", "")

POLL_INTERVAL = int(os.environ.get("PTS_POLL_INTERVAL_SECONDS", "300"))
SR_OUTPUT_DIR = os.environ.get("PTS_SR_OUTPUT_DIR", "")  # vacío = no guardar copia local
LOG_DIR       = Path(os.environ.get("PTS925_LOG_DIR", "logs"))

# Tag privado que Transducin escribe en cada SR (0009,0010) — para identificar SRs propios
_TRANSDUCIN_CREATOR = MANUFACTURER  # "RetinaOS-Transducin"

CONNECT_TIMEOUT = 10


# ── Orthanc REST helpers ────────────────────────────────────────────────────

def _base_url() -> str:
    return f"http://{ORTHANC_HOST}:{ORTHANC_HTTP_PORT}"


def _auth() -> tuple[str, str]:
    return (ORTHANC_HTTP_USER, ORTHANC_HTTP_PASS)


def _auth_header() -> str:
    user, pwd = _auth()
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return f"Basic {token}"


def _post_json(url: str, payload: dict, timeout: int = CONNECT_TIMEOUT) -> Optional[list | dict]:
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", _auth_header())
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error("POST %s fallo: %s", url, e)
        return None


def _get_json(url: str, timeout: int = CONNECT_TIMEOUT) -> Optional[dict | list]:
    req = Request(url)
    req.add_header("Authorization", _auth_header())
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.error("GET %s fallo: %s", url, e)
        return None


def _get_bytes(url: str, timeout: int = 30) -> Optional[bytes]:
    """Descarga contenido binario (DICOM instance)."""
    req = Request(url)
    req.add_header("Authorization", _auth_header())
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logger.error("GET bytes %s fallo: %s", url, e)
        return None


def _upload_dicom(dcm_bytes: bytes) -> Optional[str]:
    """Sube un DICOM a Orthanc via POST /instances. Retorna el Orthanc instance ID."""
    url = f"{_base_url()}/instances"
    req = Request(url, data=dcm_bytes, method="POST")
    req.add_header("Content-Type", "application/dicom")
    req.add_header("Authorization", _auth_header())
    try:
        with urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
            result = json.loads(resp.read())
            status = result.get("Status", "")
            oid = result.get("ID", "")
            if status in ("Success", "AlreadyStored"):
                logger.info("  Upload SR OK: %s (%s)", oid, status)
                return oid
            else:
                logger.warning("  Upload SR status inesperado: %s", result)
                return oid
    except Exception as e:
        logger.error("  Upload SR fallo: %s", e)
        return None


# ── Lógica de polling ────────────────────────────────────────────────────────

def find_unpaired_opv_series() -> list[dict]:
    """Encuentra series OPV en Orthanc que no tienen un SR Transducin pareado.

    Returns:
        Lista de dicts con: orthanc_series_id, orthanc_study_id, study_uid,
        patient_id, study_date, instances (list of orthanc instance IDs).
    """
    base = _base_url()

    # 1. Buscar todas las series con Modality=OPV
    opv_series = _post_json(f"{base}/tools/find", {
        "Level": "Series",
        "Query": {"Modality": "OPV"},
        "Expand": True,
    })

    if not opv_series:
        return []

    # Si no vino expandido, expandir manualmente
    if opv_series and isinstance(opv_series[0], str):
        expanded = []
        for sid in opv_series:
            info = _get_json(f"{base}/series/{sid}")
            if info:
                expanded.append(info)
        opv_series = expanded

    unpaired: list[dict] = []

    for series_info in opv_series:
        study_oid = series_info.get("ParentStudy", "")
        series_oid = series_info.get("ID", "")

        if not study_oid:
            continue

        # 2. Obtener info del estudio
        study_info = _get_json(f"{base}/studies/{study_oid}")
        if not study_info:
            continue

        study_tags = study_info.get("MainDicomTags", {})
        study_uid = study_tags.get("StudyInstanceUID", "")
        patient_id = study_tags.get("PatientID", "")
        study_date = study_tags.get("StudyDate", "")

        # 3. Verificar si el estudio ya tiene un SR Transducin
        if _study_has_transducin_sr(study_oid):
            continue

        instances = series_info.get("Instances", [])
        if not instances:
            continue

        unpaired.append({
            "orthanc_series_id": series_oid,
            "orthanc_study_id": study_oid,
            "study_uid": study_uid,
            "patient_id": patient_id,
            "study_date": study_date,
            "instances": instances,
        })

    return unpaired


def _study_has_transducin_sr(study_oid: str) -> bool:
    """Verifica si un estudio ya tiene un SR generado por Transducin.

    Busca series con Modality=SR dentro del estudio y revisa si alguna
    instancia tiene el tag privado Transducin (0009,0010).
    """
    base = _base_url()

    study_info = _get_json(f"{base}/studies/{study_oid}")
    if not study_info:
        return False

    # Revisar todas las series del estudio
    for series_oid in study_info.get("Series", []):
        series_info = _get_json(f"{base}/series/{series_oid}")
        if not series_info:
            continue

        series_tags = series_info.get("MainDicomTags", {})
        modality = series_tags.get("Modality", "")

        if modality != "SR":
            continue

        # Hay un SR — verificar si es de Transducin
        # Revisar la primera instancia de la serie SR
        sr_instances = series_info.get("Instances", [])
        if not sr_instances:
            continue

        # Obtener tags de la primera instancia
        inst_tags = _get_json(f"{base}/instances/{sr_instances[0]}/simplified-tags")
        if not inst_tags:
            continue

        # Buscar tag privado Transducin o SeriesDescription que lo identifique
        # El tag privado (0009,0010) no aparece en simplified-tags,
        # así que usamos SeriesNumber=910 (que asignamos en vf_sr_builder)
        series_number = series_tags.get("SeriesNumber", "")
        if series_number == "910":
            return True

        # Fallback: buscar en el contenido del SR
        # Revisar si InstitutionName o ManufacturerModelName contiene "PTS 925"
        mfr_model = inst_tags.get("ManufacturerModelName", "")
        if "PTS 925" in mfr_model:
            return True

    return False


def process_unpaired_opv(entry: dict) -> bool:
    """Procesa una serie OPV sin SR: descarga, extrae, genera SR, sube.

    Args:
        entry: dict de find_unpaired_opv_series().

    Returns:
        True si el SR fue generado y subido exitosamente.
    """
    base = _base_url()
    patient_id = entry["patient_id"]
    study_date = entry["study_date"]
    study_uid = entry["study_uid"]
    instances = entry["instances"]

    logger.info(
        "=== OPV sin SR: patient=%s date=%s study=...%s instances=%d",
        patient_id, study_date, study_uid[-12:] if study_uid else "?",
        len(instances),
    )

    # Procesar cada instancia OPV de la serie
    for inst_oid in instances:
        # 1. Descargar DICOM OPV
        dcm_bytes = _get_bytes(f"{base}/instances/{inst_oid}/file")
        if not dcm_bytes:
            logger.error("  No se pudo descargar instancia %s", inst_oid)
            continue

        # 2. Parsear DICOM en memoria
        try:
            ds = pydicom.dcmread(io.BytesIO(dcm_bytes))
        except Exception as e:
            logger.error("  No se pudo parsear DICOM %s: %s", inst_oid, e)
            continue

        # 3. Extraer datos de campo visual
        try:
            vf_data = extract_from_dicom(ds)
        except Exception as e:
            logger.error("  Extraccion VF fallo para %s: %s", inst_oid, e)
            continue

        if vf_data is None:
            logger.warning("  Extraccion retorno None para %s", inst_oid)
            continue

        if not vf_data.noel_id:
            logger.warning("  NOEL ID no encontrado en %s — SR omitido", inst_oid)
            continue

        logger.info(
            "  VF: noel=%s lat=%s MD=%s PSD=%s VFI=%s GHT=%s pts=%d",
            vf_data.noel_id, vf_data.laterality,
            f"{vf_data.md_db:.1f}" if vf_data.md_db is not None else "N/A",
            f"{vf_data.psd_db:.1f}" if vf_data.psd_db is not None else "N/A",
            f"{vf_data.vfi_pct:.0f}%" if vf_data.vfi_pct is not None else "N/A",
            vf_data.ght or "N/A",
            len(vf_data.points),
        )

        # 4. Generar SR
        try:
            sr_output_path = None
            if SR_OUTPUT_DIR:
                sr_dir = Path(SR_OUTPUT_DIR) / "sr"
                sr_dir.mkdir(parents=True, exist_ok=True)
                sr_output_path = sr_dir / f"{patient_id}_{study_date}_{vf_data.laterality}_VF_SR.dcm"

            sr_ds = build_vf_sr(
                vf_data,
                reference_dataset=ds,
                output_path=sr_output_path,
                study_instance_uid=study_uid,
            )
            logger.info("  SR generado (study_uid=...%s)", study_uid[-12:])
        except Exception as e:
            logger.error("  Generacion SR fallo: %s", e)
            continue

        # 5. Serializar y subir a Orthanc
        try:
            buf = io.BytesIO()
            sr_ds.save_as(buf, write_like_original=False)
            sr_bytes = buf.getvalue()
        except Exception as e:
            logger.error("  Serializacion SR fallo: %s", e)
            continue

        oid = _upload_dicom(sr_bytes)
        if oid:
            logger.info("  SR subido a Orthanc: %s", oid)
            return True
        else:
            logger.error("  Upload SR fallo para %s", inst_oid)

    return False


# ── Adaptación de extract_from_dicom para aceptar Dataset ────────────────

# Monkey-patch: el extractor original espera filepath, pero aquí tenemos
# el Dataset ya parseado. Envolvemos para aceptar ambos.
_original_extract = extract_from_dicom


def extract_from_dicom(source) -> Optional:
    """Wrapper que acepta Path/str o Dataset directamente."""
    if isinstance(source, Dataset):
        # Reusar la lógica de extracción pero desde Dataset en memoria
        return _extract_from_dataset(source)
    return _original_extract(source)


def _extract_from_dataset(ds: Dataset):
    """Extrae VisualFieldData desde un Dataset pydicom ya cargado en memoria."""
    from transducin.clinical_data import VisualFieldData
    from transducin.noel_id import is_valid_noel
    from transducin.pts925_extractor import (
        _get_str, _get_float, _extract_laterality, _extract_test_pattern, _extract_strategy,
        _extract_fixation_losses, _extract_global_indices, _extract_test_points,
    )

    sop_class = _get_str(ds, "SOPClassUID")
    if sop_class and sop_class != str(SOP_PERIMETRY):
        logger.warning("SOP Class inesperada: %s", sop_class)

    patient_id = _get_str(ds, "PatientID")
    patient_name = _get_str(ds, "PatientName")
    patient_dob = _get_str(ds, "PatientBirthDate")

    noel_id = patient_id if is_valid_noel(patient_id) else ""
    if not noel_id and patient_name:
        candidate = str(patient_name).replace("^", "").replace(" ", "")
        if is_valid_noel(candidate):
            noel_id = candidate

    study_date = _get_str(ds, "StudyDate")
    laterality = _extract_laterality(ds)
    test_pattern = _extract_test_pattern(ds)
    strategy = _extract_strategy(ds)
    stimulus_size = _get_str(ds, (0x0024, 0x0028))

    false_pos_pct = _get_float(ds, (0x0024, 0x0042))
    false_neg_pct = _get_float(ds, (0x0024, 0x0046))
    fixation_losses = _extract_fixation_losses(ds)

    foveal_measured = _get_str(ds, (0x0024, 0x0086))
    foveal_threshold = None
    if foveal_measured == "YES":
        foveal_threshold = _get_float(ds, (0x0024, 0x0087))

    md_db, psd_db, vfi_pct, ght = _extract_global_indices(ds)
    points = _extract_test_points(ds)

    device_serial = _get_str(ds, "DeviceSerialNumber")
    software_version = _get_str(ds, "SoftwareVersions")

    sop_uid = _get_str(ds, "SOPInstanceUID")

    vf = VisualFieldData(
        noel_id=noel_id,
        laterality=laterality,
        study_date=study_date,
        patient_name=str(patient_name),
        patient_dob=patient_dob,
        test_pattern=test_pattern,
        strategy=strategy,
        fixation_target="",
        stimulus_size=stimulus_size,
        md_db=md_db,
        psd_db=psd_db,
        vfi_pct=vfi_pct,
        ght=ght,
        points=points,
        fixation_losses=fixation_losses,
        false_pos_pct=false_pos_pct,
        false_neg_pct=false_neg_pct,
        foveal_threshold_db=foveal_threshold,
        device_serial=device_serial,
        software_version=software_version,
        source_file=sop_uid,
        extraction_confidence="confirmed" if (md_db is not None or points) else "assumed",
        notes=[],
    )

    n_pts = len(points)
    logger.info(
        "PTS 925 OPV: noel=%s lat=%s date=%s pattern=%s MD=%.1f PSD=%.1f VFI=%s GHT=%s pts=%d",
        noel_id or "(none)", laterality, study_date,
        test_pattern, md_db or 0.0, psd_db or 0.0,
        f"{vfi_pct:.0f}%" if vfi_pct is not None else "N/A",
        ght or "N/A", n_pts,
    )

    return vf


# ── Ciclo de polling ─────────────────────────────────────────────────────────

def poll_once() -> int:
    """Ejecuta una pasada de polling. Retorna el número de SRs generados."""
    logger.info("Polling Orthanc para OPV sin SR...")

    try:
        unpaired = find_unpaired_opv_series()
    except Exception as e:
        logger.error("Error buscando OPV sin SR: %s", e)
        return 0

    if not unpaired:
        logger.info("No hay OPV sin SR pareado.")
        return 0

    logger.info("Encontrados %d serie(s) OPV sin SR.", len(unpaired))
    generated = 0
    for entry in unpaired:
        try:
            ok = process_unpaired_opv(entry)
            if ok:
                generated += 1
        except Exception as e:
            logger.error("Error procesando OPV %s: %s", entry.get("patient_id", "?"), e)

    logger.info("Pasada completa: %d SR(s) generados de %d OPV.", generated, len(unpaired))
    return generated


def run_loop(interval: int) -> None:
    """Ejecuta el polling en loop con el intervalo dado (segundos)."""
    logger.info("Iniciando loop de polling (intervalo=%d s)", interval)
    while True:
        try:
            poll_once()
        except Exception as e:
            logger.error("Error en ciclo de polling: %s", e)
        time.sleep(interval)


# ── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"pts925_{datetime.now().strftime('%Y%m%d')}.log"

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
        description="Transducin PTS 925Wi -- Orthanc polling OPV -> SR"
    )
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL,
        help=f"Intervalo de polling en segundos (default: {POLL_INTERVAL})",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Ejecutar una sola pasada y salir (sin loop)",
    )
    parser.add_argument(
        "--logs", default=str(LOG_DIR),
        help=f"Carpeta de logs (default: {LOG_DIR})",
    )
    parser.add_argument("--orthanc-host", default=None)
    parser.add_argument("--orthanc-http-port", type=int, default=None)
    args = parser.parse_args()

    # Override config con CLI args
    import transducin.pts925_watcher as _self
    if args.orthanc_host:
        _self.ORTHANC_HOST = args.orthanc_host
    if args.orthanc_http_port:
        _self.ORTHANC_HTTP_PORT = args.orthanc_http_port

    log_dir = Path(args.logs).expanduser().resolve()
    _setup_logging(log_dir)

    logger.info("PTS 925Wi Orthanc polling service")
    logger.info("  Orthanc:   %s:%d", ORTHANC_HOST, ORTHANC_HTTP_PORT)
    logger.info("  Intervalo: %d s", args.interval)
    logger.info("  SR local:  %s", SR_OUTPUT_DIR or "(no)")

    if args.once:
        n = poll_once()
        logger.info("Pasada unica completada: %d SR(s).", n)
    else:
        try:
            run_loop(args.interval)
        except KeyboardInterrupt:
            logger.info("Detenido por usuario.")


if __name__ == "__main__":
    main()
