# transducin/retroactive_sr.py
# SPDX-License-Identifier: Apache-2.0
#
# Script de retroactividad: genera SRs TID 1500 para estudios ya en Orthanc
# que no tienen SR asociado.
#
# Flujo por estudio:
#   1. Consulta Orthanc — estudios OPT sin SR
#   2. Filtra por fabricante soportado (Cirrus, Spectralis, Topcon)
#   3. Descarga DICOMs a un directorio temporal
#   4. Corre el extractor correspondiente
#   5. Genera SR con el StudyInstanceUID del estudio existente
#   6. C-STORE → Orthanc (mismo estudio)
#
# Uso:
#   python -m transducin.retroactive_sr [--dry-run] [--patient NOELID]
#                                        [--from-date YYYYMMDD] [--vendor cirrus]
#
# Fabricantes soportados:
#   cirrus      → Carl Zeiss Meditec / CIRRUS HD-OCT (.dcm con tags 0073,xxxx)
#   revo        → Optopol Technology / Revo FC130 (.opt en disco → extract_from_opt)
#   spectralis  → Heidelberg Engineering / SPECTRALIS (.e2e en disco — no Orthanc-native)
#   topcon      → Topcon / DRI OCT (.fda en disco — no Orthanc-native)
#
# Nota: Cirrus embeds measurements in private DICOM tags (0073,xxxx).
# Revo requires the original .opt file (searched in OPT_PROCESSED_DIR).
# Spectralis y Topcon requieren el archivo propietario original — no aplicable aquí.

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from transducin.noel_id import is_valid_noel
from transducin.noel_resolver import resolve_noel_id
from transducin.orthanc_client import (
    ORTHANC_HTTP_HOST,
    ORTHANC_HTTP_PORT,
    ORTHANC_HTTP_USER,
    ORTHANC_HTTP_PASS,
    _get_json,
    _post_json,
    _auth_header,
)
from transducin.sr_builder import build_sr

logger = logging.getLogger("transducin.retroactive_sr")

# Umbral SQI para advertencia en log (escala 0-10, default 6)
_SQI_WARN = float(os.environ.get("TRANSDUCIN_SQI_MIN_WARN", "6")) / 10.0

# Fabricantes reconocidos
_CIRRUS_MANUFACTURERS = {"carl zeiss meditec", "zeiss"}
_CIRRUS_MODELS        = {"cirrus hd-oct", "cirrus", "cirrus oct"}
_REVO_MANUFACTURERS   = {"optopol technology", "optopol"}
_REVO_MODELS          = {"revo fc130", "revo"}

# Directorio donde revo_watcher mueve los .opt procesados
OPT_PROCESSED_DIR = Path(os.environ.get(
    "REVO_OPT_PROCESSED_DIR",
    r"C:\SOCT_DATA\BACKUP\processed",
))


# ── Helpers Orthanc ────────────────────────────────────────────────────────────

def _auth() -> tuple[str, str]:
    return (ORTHANC_HTTP_USER, ORTHANC_HTTP_PASS)


def _base() -> str:
    return f"http://{ORTHANC_HTTP_HOST}:{ORTHANC_HTTP_PORT}"


def _download_bytes(url: str) -> Optional[bytes]:
    """Descarga contenido binario desde Orthanc REST."""
    req = Request(url)
    req.add_header("Authorization", _auth_header(*_auth()))
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception as e:
        logger.error("  Descarga fallida %s: %s", url, e)
        return None


def _get_opt_studies(
    patient_id: Optional[str] = None,
    from_date: Optional[str] = None,
) -> list[dict]:
    """Devuelve estudios con al menos una serie OPT, sin SR todavía.

    Consulta directamente a nivel de Study usando ModalitiesInStudy=OPT.
    Usa Expand=true para evitar GET individual por estudio.
    """
    study_query: dict = {"ModalitiesInStudy": "OPT"}
    if patient_id:
        study_query["PatientID"] = patient_id
    if from_date:
        study_query["StudyDate"] = f"{from_date}-"

    # Usar Expand=true para recibir los MainDicomTags directamente en la respuesta
    study_list = _post_json(
        f"{_base()}/tools/find",
        {"Level": "Study", "Query": study_query, "Expand": True},
        auth=_auth(),
    ) or []
    logger.info("  Estudios OPT encontrados: %d", len(study_list))

    study_map: dict[str, dict] = {}
    for st_info in study_list:
        oid = st_info.get("ID", "")
        if not oid:
            continue
        tags    = st_info.get("MainDicomTags", {})
        pt_tags = st_info.get("PatientMainDicomTags", {})
        patient_id   = pt_tags.get("PatientID", "")
        patient_name = pt_tags.get("PatientName", "")
        patient_dob  = pt_tags.get("PatientBirthDate", "")
        if not is_valid_noel(patient_id):
            logger.warning("PatientID inválido '%s' (%s) — resolviendo via nombre", patient_id, oid[:8])
            patient_id = resolve_noel_id(patient_name, patient_dob=patient_dob)
            if not is_valid_noel(patient_id):
                logger.error("No se pudo resolver NOEL ID para '%s' — saltando estudio %s", patient_name, oid[:8])
                continue
        study_map[oid] = {
            "orthanc_id":   oid,
            "study_uid":    tags.get("StudyInstanceUID", ""),
            "study_date":   tags.get("StudyDate", ""),
            "patient_id":   patient_id,
            "patient_name": patient_name,
            "modalities":   st_info.get("ModalitiesInStudy", []),
        }

    return list(study_map.values())


def _study_has_sr(orthanc_study_id: str) -> bool:
    """True si el estudio ya contiene al menos una serie SR."""
    info = _get_json(f"{_base()}/studies/{orthanc_study_id}", auth=_auth())
    if not info:
        return False
    return "SR" in info.get("ModalitiesInStudy", [])


def _get_series_for_study(orthanc_study_id: str) -> list[dict]:
    """Lista series de un estudio con sus metadatos principales."""
    # /studies/{id}/series devuelve objetos completos, no solo IDs
    series_objs = _get_json(
        f"{_base()}/studies/{orthanc_study_id}/series", auth=_auth()
    ) or []
    result = []
    for obj in series_objs:
        if isinstance(obj, str):
            # Orthanc antiguo devuelve solo el ID — hacer GET individual
            obj = _get_json(f"{_base()}/series/{obj}", auth=_auth()) or {}
        tags = obj.get("MainDicomTags", {})
        result.append({
            "orthanc_id":  obj.get("ID", ""),
            "modality":    tags.get("Modality", ""),
            "description": tags.get("SeriesDescription", ""),
            "manufacturer": tags.get("Manufacturer", "").lower(),
            "model":        tags.get("ManufacturerModelName", "").lower(),
            "instances":   obj.get("Instances", []),
        })
    return result


def _instance_tags(instance_id: str) -> dict:
    """Devuelve los tags simplificados de una instancia (nivel instancia)."""
    info = _get_json(f"{_base()}/instances/{instance_id}/simplified-tags", auth=_auth())
    return info or {}


def _detect_vendor(series_list: list[dict]) -> Optional[str]:
    """Detecta el fabricante dominante en las series OPT de un estudio.

    Intenta primero con los MainDicomTags de la serie (rápido).
    Si no hay fabricante ahí, consulta los tags de la primera instancia.
    """
    for s in series_list:
        if s.get("modality") != "OPT":
            continue
        mfr   = s.get("manufacturer", "")
        model = s.get("model", "")

        # Si la serie no tiene fabricante, buscar en la primera instancia
        if not mfr and not model:
            instances = s.get("instances", [])
            if instances:
                itags = _instance_tags(instances[0])
                mfr   = itags.get("Manufacturer", "").lower()
                model = itags.get("ManufacturerModelName", "").lower()

        if any(m in mfr for m in _CIRRUS_MANUFACTURERS) or \
           any(m in model for m in _CIRRUS_MODELS):
            return "cirrus"
        if any(m in mfr for m in _REVO_MANUFACTURERS) or \
           any(m in model for m in _REVO_MODELS):
            return "revo"
        if "heidelberg" in mfr or "spectralis" in model:
            return "spectralis"
        if "topcon" in mfr or "triton" in model or "maestro" in model:
            return "topcon"

        logger.warning("  Fabricante no reconocido (mfr='%s' model='%s')", mfr, model)

    return None


def _download_series_to_dir(
    series: dict,
    dest_dir: Path,
) -> list[Path]:
    """Descarga todas las instancias de una serie a dest_dir como .dcm."""
    downloaded = []
    for inst_id in series.get("instances", []):
        url   = f"{_base()}/instances/{inst_id}/file"
        data  = _download_bytes(url)
        if data:
            out = dest_dir / f"{inst_id}.dcm"
            out.write_bytes(data)
            downloaded.append(out)
    return downloaded


def _cstore_file(dcm_path: Path) -> bool:
    """C-STORE un archivo DICOM a Orthanc vía REST store."""
    data = dcm_path.read_bytes()
    req  = Request(f"{_base()}/instances", data=data, method="POST")
    req.add_header("Authorization", _auth_header(*_auth()))
    req.add_header("Content-Type", "application/dicom")
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status in (200, 202)
    except Exception as e:
        logger.error("  C-STORE fallido para %s: %s", dcm_path.name, e)
        return False


# ── Procesamiento por fabricante ──────────────────────────────────────────────

def _process_cirrus_study(
    study: dict,
    series_list: list[dict],
    output_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Descarga series OPT Cirrus, extrae mediciones y genera SR."""
    from transducin.cirrus_extractor import extract_from_exam as cirrus_extract

    study_id    = study["orthanc_id"]
    noel_id     = study["patient_id"]
    study_date  = study["study_date"]
    study_uid   = study["study_uid"]

    opt_series = [s for s in series_list if s["modality"] == "OPT"]
    if not opt_series:
        logger.warning("  Sin series OPT en estudio %s — saltando", study_id[:8])
        return False

    with tempfile.TemporaryDirectory(prefix="transducin_retro_") as tmp:
        tmp_path = Path(tmp)

        # Descargar todas las series OPT
        total = 0
        for s in opt_series:
            downloaded = _download_series_to_dir(s, tmp_path)
            total += len(downloaded)
            logger.info("  Serie %s: %d instancias descargadas", s["orthanc_id"][:8], len(downloaded))

        if total == 0:
            logger.warning("  Sin instancias descargadas para %s", study_id[:8])
            return False

        # Extraer mediciones
        try:
            clinical_list = cirrus_extract(tmp_path, noel_id=noel_id)
        except Exception as e:
            logger.error("  cirrus_extractor falló: %s", e)
            return False

        if not clinical_list:
            logger.warning("  Sin mediciones extraíbles (tags 0073,xxxx ausentes o vacíos)")
            return False

        sr_dir = output_dir / "sr_retro"
        sr_dir.mkdir(parents=True, exist_ok=True)
        success = False

        for cd in clinical_list:
            if not cd.has_measurements():
                continue
            # Usar el StudyInstanceUID del estudio Orthanc existente
            cd_study_uid = study_uid

            sr_name = (
                f"{noel_id}_{cd.study_type}_{cd.laterality}"
                f"_{study_date}_cirrus_retro_SR.dcm"
            )
            sr_path = sr_dir / sr_name

            sqi_low = cd.sqi_mean is not None and cd.sqi_mean < _SQI_WARN
            logger.info(
                "  SR: %s lat=%s CMT=%s SQI=%s",
                cd.study_type, cd.laterality,
                f"{cd.cmt_um:.0f}µm" if cd.cmt_um is not None else "N/A",
                f"{cd.sqi_mean * 10:.1f}/10{'  BAJA' if sqi_low else ''}"
                if cd.sqi_mean is not None else "N/A",
            )

            if dry_run:
                logger.info("  [DRY-RUN] SR no escrito ni enviado: %s", sr_name)
                success = True
                continue

            try:
                build_sr(cd, reference_dataset=None, output_path=sr_path,
                         study_instance_uid=cd_study_uid)
                ok = _cstore_file(sr_path)
                logger.info("  C-STORE SR: %s", "OK" if ok else "FALLIDO")
                if ok:
                    success = True
            except Exception as e:
                logger.error("  SR build falló: %s", e)

    return success


def _find_opt_file(patient_id: str, study_date: str, patient_name: str) -> Optional[Path]:
    """Busca el archivo .opt original en OPT_PROCESSED_DIR.

    Matching strategy: scan all .opt files and match by NOEL ID + study date,
    or by patient name + study date if NOEL ID not in filename.
    """
    if not OPT_PROCESSED_DIR.is_dir():
        logger.warning("  OPT_PROCESSED_DIR no existe: %s", OPT_PROCESSED_DIR)
        return None

    pid_upper = patient_id.upper()
    # PatientName in DICOM is "APELLIDOS^NOMBRES", in filename is "APELLIDOS_NOMBRES"
    name_norm = patient_name.upper().replace("^", "_").replace(" ", "_")

    candidates = []
    for opt in OPT_PROCESSED_DIR.glob("*.opt"):
        stem = opt.stem.upper()
        if study_date and study_date not in stem:
            continue
        if pid_upper and pid_upper in stem:
            candidates.append(opt)
        elif name_norm and name_norm in stem:
            candidates.append(opt)

    if not candidates:
        return None
    # Return the largest (most data — macular scan preferred)
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def _process_revo_study(
    study: dict,
    series_list: list[dict],
    output_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Localiza el archivo .opt original, extrae mediciones y genera SR."""
    from transducin.opt_extractor import extract_from_opt, build_noel_index
    from transducin.sr_builder import build_sr

    noel_id      = study["patient_id"]
    study_date   = study["study_date"]
    study_uid    = study["study_uid"]
    patient_name = study.get("patient_name", "")

    opt_path = _find_opt_file(noel_id, study_date, patient_name)
    if opt_path is None:
        logger.warning("  No se encontró .opt para %s/%s en %s", noel_id, study_date, OPT_PROCESSED_DIR)
        return False

    logger.info("  .opt encontrado: %s", opt_path.name)

    noel_index = build_noel_index(OPT_PROCESSED_DIR, include_processed=True)

    try:
        cd = extract_from_opt(opt_path, noel_index=noel_index)
    except Exception as e:
        logger.error("  extract_from_opt falló: %s", e)
        return False

    if not cd.has_measurements():
        logger.warning("  Sin mediciones extraíbles en %s", opt_path.name)
        return False

    sr_dir = output_dir / "sr_retro"
    sr_dir.mkdir(parents=True, exist_ok=True)

    sr_name = (
        f"{noel_id}_{cd.study_type}_{cd.laterality}"
        f"_{study_date}_revo_retro_SR.dcm"
    )
    sr_path = sr_dir / sr_name

    sqi_low = cd.sqi_mean is not None and cd.sqi_mean < _SQI_WARN
    logger.info(
        "  SR: %s lat=%s CMT=%s SQI=%s",
        cd.study_type, cd.laterality,
        f"{cd.cmt_um:.0f}µm" if cd.cmt_um is not None else "N/A",
        f"{cd.sqi_mean * 10:.1f}/10{'  BAJA' if sqi_low else ''}"
        if cd.sqi_mean is not None else "N/A",
    )

    if dry_run:
        logger.info("  [DRY-RUN] SR no escrito ni enviado: %s", sr_name)
        return True

    try:
        build_sr(cd, reference_dataset=None, output_path=sr_path,
                 study_instance_uid=study_uid)
        ok = _cstore_file(sr_path)
        logger.info("  C-STORE SR: %s", "OK" if ok else "FALLIDO")
        return ok
    except Exception as e:
        logger.error("  SR build falló: %s", e)
        return False


# ── Entry point ───────���────────────────────────────────────────────────────────

def run_retroactive(
    patient_id: Optional[str] = None,
    from_date:  Optional[str] = None,
    vendor_filter: Optional[str] = None,
    assume_vendor: Optional[str] = None,
    output_dir: Path = Path("Output_retro"),
    dry_run: bool = False,
) -> None:
    """Genera SRs retroactivos para estudios en Orthanc sin SR.

    Args:
        patient_id:    Filtrar por PatientID NOEL (None = todos)
        from_date:     Fecha mínima YYYYMMDD (None = sin límite)
        vendor_filter: "cirrus" | "revo" | None (None = todos los soportados)
        assume_vendor: Si se especifica, salta la detección de fabricante y
                       trata todos los estudios OPT como este vendor.
                       Útil cuando se sabe que todos los estudios son del
                       mismo fabricante (ahorra 1 GET por estudio).
        output_dir:    Directorio para SRs generados
        dry_run:       Si True, detecta y loga pero no escribe ni C-STORE
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("═══ Retroactive SR — Orthanc %s:%s", ORTHANC_HTTP_HOST, ORTHANC_HTTP_PORT)
    if assume_vendor:
        logger.info("  Vendor asumido: %s (sin detección automática)", assume_vendor)
    if dry_run:
        logger.info("  [DRY-RUN] modo activo — no se escribirán ni enviarán SRs")

    studies = _get_opt_studies(patient_id=patient_id, from_date=from_date)
    logger.info("  Estudios OPT únicos: %d", len(studies))

    done = skipped = errors = 0
    total = len(studies)

    for idx, study in enumerate(studies, 1):
        oid        = study["orthanc_id"]
        noel_id    = study["patient_id"] or "UNKNOWN"
        study_date = study["study_date"] or "?"

        # Usar ModalitiesInStudy ya obtenido para evitar GET adicional
        if "SR" in study.get("modalities", []):
            logger.info("  [%d/%d] %s/%s — ya tiene SR, saltando", idx, total, noel_id, study_date)
            skipped += 1
            continue

        logger.info("─── [%d/%d] Estudio %s | %s | %s", idx, total, noel_id, study_date, oid[:8])

        series_list = _get_series_for_study(oid)

        if assume_vendor:
            vendor = assume_vendor
        else:
            vendor = _detect_vendor(series_list)

        if vendor is None:
            logger.info("  Fabricante no reconocido — saltando")
            skipped += 1
            continue

        if vendor_filter and vendor != vendor_filter:
            logger.info("  Fabricante %s no coincide con filtro '%s' — saltando", vendor, vendor_filter)
            skipped += 1
            continue

        logger.info("  Vendor: %s", vendor)

        try:
            if vendor == "cirrus":
                ok = _process_cirrus_study(study, series_list, output_dir, dry_run=dry_run)
            elif vendor == "revo":
                ok = _process_revo_study(study, series_list, output_dir, dry_run=dry_run)
            else:
                logger.info("  Fabricante %s: requiere archivo propietario — saltando", vendor)
                skipped += 1
                continue
        except Exception as e:
            logger.error("  Error inesperado en %s: %s", oid[:8], e)
            errors += 1
            continue

        if ok:
            done += 1
            logger.info("  ✓ SR %s para %s/%s", "detectado (dry-run)" if dry_run else "generado", noel_id, study_date)
        else:
            errors += 1

    logger.info(
        "═══ Retroactive SR completado — generados: %d | saltados: %d | errores: %d | total: %d",
        done, skipped, errors, total,
    )


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Genera SRs TID 1500 retroactivos para estudios Cirrus/Revo ya en Orthanc."
    )
    parser.add_argument(
        "--patient", metavar="NOELID",
        help="Procesar solo este PatientID (p.ej. JAHJ19870831)"
    )
    parser.add_argument(
        "--from-date", metavar="YYYYMMDD",
        help="Procesar solo estudios desde esta fecha"
    )
    parser.add_argument(
        "--vendor", choices=["cirrus", "revo"], default=None,
        help="Filtrar por fabricante (default: todos los soportados)"
    )
    parser.add_argument(
        "--assume-vendor", choices=["cirrus", "revo"], default=None,
        metavar="VENDOR",
        help="Asumir este fabricante para todos los estudios, sin detección automática. "
             "Más rápido cuando se sabe que todos los OPT son del mismo equipo."
    )
    parser.add_argument(
        "--output", metavar="DIR", default="Output_retro",
        help="Directorio para SRs generados (default: Output_retro)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Detectar y logar sin escribir ni C-STORE"
    )

    args = parser.parse_args()

    run_retroactive(
        patient_id    = args.patient,
        from_date     = args.from_date,
        vendor_filter = args.vendor,
        assume_vendor = args.assume_vendor,
        output_dir    = Path(args.output),
        dry_run       = args.dry_run,
    )


if __name__ == "__main__":
    main()
