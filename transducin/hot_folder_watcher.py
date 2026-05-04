# transducin/hot_folder_watcher.py
# SPDX-License-Identifier: Apache-2.0
#
# Watcher multivendor: detecta archivos nuevos en la carpeta de entrada,
# ejecuta el pipeline correspondiente y envía a Orthanc via C-STORE.
#
# Formatos soportados y pipeline:
#
#   .opt              Revo FC130 (Optopol)
#     → opt_extractor → revo_opt_reader → OphthTomographyIS DICOM
#     → sr_builder TID 1500 (CMT + ETDRS + mRNFL + mGCIPL)
#     → C-STORE imagen + SR → Orthanc
#
#   .dcm / .ex.dcm    Cirrus HD-OCT (Carl Zeiss Meditec)
#     → carl_deobfuscator (XOR + JP2K) → DICOM limpio
#     → C-STORE → Orthanc
#
#
# Uso:
#   python -m transducin.hot_folder_watcher --watch input/ --output Output
#   python -m transducin.hot_folder_watcher --watch input/ --output Output \
#       --no-cstore --process-existing

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from transducin.clinical_data import OCTClinicalData
from transducin.opt_extractor import extract_from_opt
from transducin.revo_opt_reader import opt_to_dicom
from transducin.sr_builder import build_sr
from transducin.cirrus_extractor import extract_from_exam as cirrus_extract_from_exam
from transducin.cirrus_pdf_extractor import extract_from_pdf as cirrus_extract_from_pdf
from transducin.noel_id import is_valid_noel, dob_from_noel
from transducin.cirrus_tags import apply_cirrus_study_tags
from transducin.orthanc_client import resolve_study_uid

logger = logging.getLogger("transducin.watcher")

# ── Configuración por defecto ────────────────────────────────────────────────

DEFAULT_WATCH_DIR  = Path("input/REVO")
DEFAULT_CZM_WATCH_DIR = Path("/data/input/CZM")
DEFAULT_OUTPUT_DIR = Path("Output")
DEFAULT_LOG_DIR    = Path("logs")
ORTHANC_HOST       = os.environ.get("ORTHANC_HOST", "localhost")
ORTHANC_PORT       = int(os.environ.get("ORTHANC_PORT", "4242"))
ORTHANC_HTTP_PORT  = int(os.environ.get("ORTHANC_HTTP_PORT", "8042"))
ORTHANC_HTTP_USER  = os.environ.get("ORTHANC_HTTP_USER", "orthanc")
ORTHANC_HTTP_PASS  = os.environ.get("ORTHANC_HTTP_PASS", "")
ORTHANC_AET        = os.environ.get("ORTHANC_AET", "ORTHANC")
LOCAL_AET          = os.environ.get("TRANSDUCIN_AET", "TRANSDUCIN")


def _orthanc_rest_url() -> str:
    """URL base de la REST API de Orthanc."""
    import transducin.hot_folder_watcher as _self
    return f"http://{_self.ORTHANC_HOST}:{_self.ORTHANC_HTTP_PORT}"


def _orthanc_auth() -> tuple[str, str]:
    import transducin.hot_folder_watcher as _self
    return (_self.ORTHANC_HTTP_USER, _self.ORTHANC_HTTP_PASS)
CSTORE_RETRIES     = 3
CSTORE_RETRY_DELAY = 5   # segundos entre reintentos
SETTLE_DELAY       = 2   # segundos de espera tras detección (evita leer archivo incompleto)

# ── Routing de formatos ───────────────────────────────────────────────────────
# Extensiones que activan cada pipeline (minúsculas)
_EXT_REVO      = {".opt"}
_EXT_CZM       = {".dcm"}          # cubre .dcm y .ex.dcm (suffix siempre .dcm)
_EXT_PDF       = {".pdf"}          # Cirrus PDF reports (Macular Thickness, ONH+RNFL)

ALL_WATCHED_EXTS = _EXT_REVO | _EXT_CZM | _EXT_PDF


# ── C-STORE via pynetdicom ───────────────────────────────────────────────────

def _cstore(dcm_path: Path, retries: int = CSTORE_RETRIES) -> bool:
    """Envía un .dcm a Orthanc via C-STORE. Retorna True si éxito."""
    try:
        from pynetdicom import AE, StoragePresentationContexts
        import pydicom
    except ImportError:
        logger.error("pynetdicom no disponible — C-STORE omitido.")
        return False

    import pydicom
    try:
        ds = pydicom.dcmread(str(dcm_path))
    except Exception as e:
        logger.error("No se pudo leer %s: %s", dcm_path.name, e)
        return False

    for attempt in range(1, retries + 1):
        try:
            ae = AE(ae_title=LOCAL_AET)
            ae.requested_contexts = StoragePresentationContexts
            assoc = ae.associate(ORTHANC_HOST, ORTHANC_PORT, ae_title=ORTHANC_AET)
            if assoc.is_established:
                status = assoc.send_c_store(ds)
                assoc.release()
                if status and status.Status == 0x0000:
                    logger.info("C-STORE OK: %s → Orthanc", dcm_path.name)
                    return True
                else:
                    logger.warning("C-STORE status inesperado: 0x%04x (intento %d/%d)",
                                   status.Status if status else 0xFFFF, attempt, retries)
            else:
                logger.warning("C-STORE: no se pudo establecer asociación (intento %d/%d)",
                               attempt, retries)
        except Exception as e:
            logger.warning("C-STORE error (intento %d/%d): %s", attempt, retries, e)

        if attempt < retries:
            time.sleep(CSTORE_RETRY_DELAY)

    logger.error("C-STORE FALLIDO después de %d intentos: %s", retries, dcm_path.name)
    return False


# ── Conversión .opt → .dcm imagen ───────────────────────────────────────────

def _convert_opt_to_dcm(
    opt_path: Path,
    output_dir: Path,
    clinical_data,
) -> list[Path]:
    """Convierte .opt Revo FC130 → DICOM(s): OCT cube + SLO + ENFACE + ANGPRV/OCTA_MIP.

    Usa revo_opt_reader.opt_to_dicom() que parsea el contenedor binario .opt.
    Omite solo BMETR (sin imagen OCT ni en-face).
    """
    if clinical_data.study_type in ("bmetr", "biometry"):
        logger.info("  Conversión omitida: tipo '%s' no genera imagen.", clinical_data.study_type)
        return []
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        noel = clinical_data.noel_id or "UNKNOWN"
        date = clinical_data.study_date or ""
        lat  = clinical_data.laterality or ""
        dcm_paths = opt_to_dicom(
            opt_path, output_dir,
            noel_id=noel, study_date=date, laterality=lat,
            patient_name=clinical_data.patient_name or "",
            patient_dob=clinical_data.patient_dob or "",
        )
        return dcm_paths
    except Exception as e:
        logger.error("Conversión .opt → .dcm falló para %s: %s", opt_path.name, e)
        return []


# ── Nombre estandarizado para processed/ ─────────────────────────────────────

def _standardized_name(
    cd: "OCTClinicalData",
    suffix: str,
    ts: Optional[str] = None,
) -> str:
    """Genera nombre estandarizado: {NOEL}_{YYYYMMDD}_{LAT}_{TYPE}.opt

    Si NOEL ID no está disponible, usa patient_name sanitizado como prefijo.
    Si hay colisión, ts (timestamp) se agrega antes de la extensión.
    """
    prefix = cd.noel_id
    if not prefix:
        # Fallback: sanitize patient name
        prefix = cd.patient_name.replace(" ", "_").replace("^", "_")
        if not prefix:
            prefix = "UNKNOWN"

    lat = {"R": "OD", "L": "OS"}.get(cd.laterality, cd.laterality or "XX")
    study_type = (cd.study_type or "unknown").upper()
    date = cd.study_date or "00000000"

    name = f"{prefix}_{date}_{lat}_{study_type}"
    if ts:
        name = f"{name}_{ts}"
    return f"{name}{suffix}"


# ── Pipeline completo por archivo ────────────────────────────────────────────

def process_opt_file(
    opt_path: Path,
    output_dir: Path,
    do_cstore: bool = True,
    noel_index: Optional[dict[str, str]] = None,
) -> bool:
    """Ejecuta el pipeline completo para un archivo .opt.

    Returns:
        True si el pipeline completó sin errores críticos.
    """
    logger.info("═══ Procesando: %s", opt_path.name)
    success = True

    # 1. Extraer metadatos clínicos
    try:
        clinical_data = extract_from_opt(opt_path, noel_index=noel_index)
        logger.info("  Extracción: noel=%s lat=%s type=%s date=%s confidence=%s",
                    clinical_data.noel_id or "(none)",
                    clinical_data.laterality,
                    clinical_data.study_type,
                    clinical_data.study_date,
                    clinical_data.extraction_confidence)
    except Exception as e:
        logger.error("  Extracción FALLÓ: %s", e)
        return False

    # 2. Convertir .opt → .dcm imágenes (OCT cube, SLO, ENFACE, ANGPRV, OCTA_MIP)
    dcm_image_paths: list[Path] = []
    try:
        dcm_image_paths = _convert_opt_to_dcm(opt_path, output_dir / "images", clinical_data)
        if dcm_image_paths:
            for p in dcm_image_paths:
                logger.info("  Imagen DICOM: %s", p.name)
        elif clinical_data.study_type in ("bmetr", "biometry"):
            pass  # biometry — no image expected
        else:
            logger.warning("  Conversión .opt → .dcm sin resultado — SR se generará sin referencia imagen")
            if clinical_data.study_type not in ("fundus",):
                success = False
    except Exception as e:
        logger.error("  Conversión FALLÓ: %s", e)
        success = False

    # 3. Generar DICOM SR (solo para estudios con mediciones clínicas y NOEL ID conocido)
    sr_path: Optional[Path] = None
    if clinical_data.study_type == "unknown" and not clinical_data.has_measurements():
        logger.info("  SR omitido: tipo '%s' sin mediciones.", clinical_data.study_type)
    elif not clinical_data.noel_id:
        logger.warning("  SR omitido: noel_id desconocido — imagen DICOM guardada sin SR.")
    else:
        try:
            stem = opt_path.stem.replace(" ", "_")
            sr_filename = f"{stem}_SR.dcm"
            sr_path = output_dir / "sr" / sr_filename

            import pydicom
            # Usar el primer DICOM como referencia para el SR
            ref_ds = pydicom.dcmread(str(dcm_image_paths[0])) if dcm_image_paths else None

            from pydicom.uid import generate_uid as _gen_uid
            _fallback_uid = ref_ds.StudyInstanceUID if ref_ds else _gen_uid()
            _study_uid = resolve_study_uid(
                noel_id=clinical_data.noel_id,
                study_date=clinical_data.study_date or "",
                fallback_uid=str(_fallback_uid),
                orthanc_base_url=_orthanc_rest_url(),
                auth=_orthanc_auth(),
            )
            build_sr(clinical_data, reference_dataset=ref_ds,
                     output_path=sr_path, study_instance_uid=_study_uid)
            logger.info("  SR generado: %s (study_uid=…%s)", sr_path.name, _study_uid[-12:])
        except Exception as e:
            logger.error("  Generación SR FALLÓ: %s", e)
            sr_path = None
            success = False

    # 4 + 5. C-STORE a Orthanc — todas las imágenes + SR
    if do_cstore:
        for dcm_p in dcm_image_paths:
            if dcm_p.exists():
                ok = _cstore(dcm_p)
                if not ok:
                    logger.error("  C-STORE imagen FALLÓ: %s", dcm_p.name)
                    success = False

        if sr_path and sr_path.exists():
            ok = _cstore(sr_path)
            if not ok:
                logger.error("  C-STORE SR FALLÓ: %s", sr_path.name)
                success = False
    else:
        logger.info("  C-STORE omitido (do_cstore=False)")

    # 7. Mover .opt a processed/ con nombre estandarizado (nunca borrar)
    try:
        processed_dir = opt_path.parent / "processed"
        processed_dir.mkdir(exist_ok=True)
        dest = processed_dir / _standardized_name(clinical_data, opt_path.suffix)
        if dest.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = processed_dir / _standardized_name(clinical_data, opt_path.suffix, ts)
        shutil.move(str(opt_path), str(dest))
        logger.info("  Movido a processed/: %s", dest.name)
    except Exception as e:
        logger.error("  No se pudo mover a processed/: %s", e)

    status = "OK" if success else "PARCIAL"
    logger.info("  Resultado: %s — %s", status, opt_path.name)
    return success




def _route_file(path: Path, output_dir: Path, do_cstore: bool) -> None:
    """Despacha un archivo al pipeline correcto según su extensión."""
    ext = path.suffix.lower()
    if ext in _EXT_REVO:
        process_opt_file(path, output_dir, do_cstore=do_cstore)
    elif ext in _EXT_CZM:
        process_czm_file(path, output_dir, do_cstore=do_cstore)
    elif ext in _EXT_PDF:
        process_cirrus_pdf_file(path, output_dir, do_cstore=do_cstore)
    else:
        logger.debug("  Extensión ignorada: %s", ext)


# ── Pipeline Cirrus CZM .DCM ────────────────────────────────────────────────

def process_czm_file(
    dcm_path: Path,
    output_dir: Path,
    do_cstore: bool = True,
) -> bool:
    """Desofusca un .DCM Cirrus CZM, genera SR TID 1500 y envía a Orthanc.

    Returns:
        True si el pipeline completó sin errores críticos.
    """
    logger.info("═══ Cirrus CZM: %s", dcm_path.name)
    import pydicom
    from transducin.carl_deobfuscator import is_obfuscated, process_dicom_file

    success = True

    try:
        out_dir = output_dir / "cirrus"
        out_dir.mkdir(parents=True, exist_ok=True)

        if is_obfuscated(dcm_path.read_bytes()[:512]):
            out_path = out_dir / dcm_path.name
            ok = process_dicom_file(
                input_path=dcm_path,
                output_path=out_path,
                verbose=False,
                save_png=False,
                write_dcm=True,
            )
            if not ok:
                logger.error("  Desofuscación FALLÓ: %s", dcm_path.name)
                return False
            logger.info("  Desofuscado → %s", out_path.name)
            clean_dcm = out_path
        else:
            logger.info("  %s no está ofuscado — usando directo.", dcm_path.name)
            clean_dcm = dcm_path

        # ── Sanitizar y enriquecer ANTES del C-STORE ───────────────────────────
        # La ofuscación CZM agrega null-bytes y basura a PID y campos de fecha.
        # Se corrigen en disco para que la imagen almacenada en Orthanc sea válida.
        noel_id: Optional[str] = None
        patient_name: str = ""
        patient_dob: str = ""
        ref_ds = None
        try:
            import re as _re
            ref_ds = pydicom.dcmread(str(clean_dcm))

            # PID: conservar solo A-Z0-9, tomar primeros 12 chars
            pid_raw = str(getattr(ref_ds, "PatientID", ""))
            pid = _re.sub(r"[^A-Z0-9]", "", pid_raw.upper())[:12]
            if is_valid_noel(pid):
                noel_id = pid
                ref_ds.PatientID = pid

            _DA_TAGS = [
                "PatientBirthDate", "StudyDate", "SeriesDate",
                "ContentDate", "AcquisitionDate",
            ]
            _TM_TAGS = ["StudyTime", "SeriesTime", "ContentTime", "AcquisitionTime"]
            _UI_TAGS = [
                "StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
                "FrameOfReferenceUID", "MediaStorageSOPInstanceUID",
                "SOPClassUID", "MediaStorageSOPClassUID",
            ]
            _PN_TAGS = [
                "PatientName", "ReferringPhysicianName",
                "PerformingPhysicianName", "OperatorsName",
            ]
            for _tag in _DA_TAGS:
                if hasattr(ref_ds, _tag):
                    raw_val = str(getattr(ref_ds, _tag, ""))
                    setattr(ref_ds, _tag, _re.sub(r"[^0-9]", "", raw_val)[:8])
            for _tag in _TM_TAGS:
                if hasattr(ref_ds, _tag):
                    raw_val = str(getattr(ref_ds, _tag, ""))
                    setattr(ref_ds, _tag, _re.sub(r"[^0-9.]", "", raw_val)[:13])
            for _tag in _UI_TAGS:
                if hasattr(ref_ds, _tag):
                    raw_val = getattr(ref_ds, _tag, "")
                    if hasattr(raw_val, "__iter__") and not isinstance(raw_val, str):
                        raw_val = list(raw_val)[0] if raw_val else ""
                    setattr(ref_ds, _tag, _re.sub(r"[^0-9.]", "", str(raw_val))[:64])
            # PN: conservar solo ASCII imprimible; NO usar noel_id como fallback
            for _tag in _PN_TAGS:
                if hasattr(ref_ds, _tag):
                    raw_val = str(getattr(ref_ds, _tag, ""))
                    clean_val = "".join(c for c in raw_val if 0x20 <= ord(c) < 0x7F)
                    setattr(ref_ds, _tag, clean_val)

            # Capturar nombre y DOB limpios del DICOM; DOB con fallback desde NOEL
            patient_name = str(getattr(ref_ds, "PatientName", ""))
            patient_dob  = str(getattr(ref_ds, "PatientBirthDate", ""))
            if not patient_dob and noel_id:
                patient_dob = dob_from_noel(noel_id)
                ref_ds.PatientBirthDate = patient_dob

            # StudyDescription clínica desde SeriesDescription ya inferida por
            # carl_deobfuscator (ej. "Macular Cube 512x128" → "OCT Macular")
            study_desc = apply_cirrus_study_tags(ref_ds)

            # Guardar tags corregidos en disco ANTES del C-STORE
            ref_ds.save_as(str(clean_dcm), write_like_original=False)
            logger.info("  Tags sanitizados → %s (NOEL=%s, DOB=%s, study=%s)",
                        clean_dcm.name, noel_id or "?", patient_dob or "?",
                        study_desc)
        except Exception as e:
            logger.warning("  Sanitización DICOM parcial: %s", e)

        if not noel_id:
            # Fallback: componente del path (carpeta de paciente)
            for part in reversed(dcm_path.parts[:-1]):
                if is_valid_noel(part):
                    noel_id = part
                    if not patient_dob:
                        patient_dob = dob_from_noel(noel_id)
                    break

        # C-STORE imagen (con tags ya corregidos)
        if do_cstore:
            ok = _cstore(clean_dcm)
            if not ok:
                logger.error("  C-STORE imagen Cirrus FALLÓ: %s", clean_dcm.name)
                success = False
            else:
                logger.info("  C-STORE imagen: OK")

        # Mover original a processed/ ANTES de extraer, para que la carpeta
        # del examen quede limpia. El SR se genera cuando el último archivo
        # del examen haya sido procesado (carpeta vacía de .EX.DCM).
        processed_dir = dcm_path.parent / "processed"
        processed_dir.mkdir(exist_ok=True)
        dest = processed_dir / dcm_path.name
        if dest.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = processed_dir / f"{dcm_path.stem}_{ts}{dcm_path.suffix}"
        shutil.move(str(dcm_path), str(dest))
        logger.info("  Movido a processed/: %s", dest.name)

        # Comprobar si quedan archivos pendientes en la carpeta del examen.
        # Si aún hay .EX.DCM sin procesar, esperar al último — evita SR duplicados.
        pending = [
            f for f in dcm_path.parent.iterdir()
            if f.name.upper().endswith(".EX.DCM") and f != dcm_path
        ]
        if pending:
            logger.info(
                "  Examen incompleto (%d archivos pendientes) — SR diferido.", len(pending)
            )
            return success

        if not noel_id:
            logger.warning("  No se pudo inferir NOEL ID para %s — SR omitido.", dcm_path.name)
        else:
            # Todos los archivos del examen están en processed/: extraer y generar SR
            exam_dir = processed_dir  # leer desde processed/
            try:
                clinical_results = cirrus_extract_from_exam(
                    exam_dir, noel_id,
                    patient_name=patient_name,
                    patient_dob=patient_dob,
                )
                logger.info("  Extracción Cirrus: %d resultado(s)", len(clinical_results))
                for cd in clinical_results:
                    try:
                        lat_tag = cd.laterality or "UNK"
                        sr_name = f"{noel_id}_{cd.study_type}_{lat_tag}_{cd.study_date}_SR.dcm"
                        sr_path = output_dir / "sr" / sr_name
                        sr_path.parent.mkdir(parents=True, exist_ok=True)
                        from pydicom.uid import generate_uid as _gen_uid
                        _study_uid = resolve_study_uid(
                            noel_id=noel_id,
                            study_date=cd.study_date or "",
                            fallback_uid=_gen_uid(),
                            orthanc_base_url=_orthanc_rest_url(),
                            auth=_orthanc_auth(),
                        )
                        build_sr(cd, reference_dataset=ref_ds, output_path=sr_path,
                                 study_instance_uid=_study_uid)
                        logger.info("  SR generado: %s (study_uid=…%s)", sr_path.name, _study_uid[-12:])
                        if do_cstore:
                            ok = _cstore(sr_path)
                            logger.info("  C-STORE SR: %s", "OK" if ok else "FALLIDO")
                            if not ok:
                                success = False
                    except Exception as e:
                        logger.error("  SR build FALLÓ (%s %s): %s", cd.study_type, cd.laterality, e)
                        success = False
            except Exception as e:
                logger.error("  Extracción Cirrus FALLÓ: %s", e)
                success = False

        return success

    except Exception as e:
        logger.error("  Error procesando Cirrus %s: %s", dcm_path.name, e)
        return False


# ── Pipeline Cirrus PDF ──────────────────────────────────────────────────────

def process_cirrus_pdf_file(
    pdf_path: Path,
    output_dir: Path,
    do_cstore: bool = True,
) -> bool:
    """Extrae mediciones de un PDF Cirrus, genera SR TID 1500 y envía a Orthanc.

    Solo procesa PDFs cuyo nombre contiene '__' (patrón Cirrus export).

    Returns:
        True si al menos un SR fue generado sin errores críticos.
    """
    logger.info("═══ Cirrus PDF: %s", pdf_path.name)

    if "__" not in pdf_path.name:
        logger.info("  Sin '__' en nombre — no es PDF Cirrus, ignorando.")
        return False

    success = False
    try:
        clinical_results = cirrus_extract_from_pdf(pdf_path)
        if not clinical_results:
            logger.warning("  Sin resultados clínicos en %s", pdf_path.name)
            return False

        logger.info("  Extracción PDF: %d resultado(s)", len(clinical_results))

        sr_dir = output_dir / "sr"
        sr_dir.mkdir(parents=True, exist_ok=True)

        for cd in clinical_results:
            try:
                stem = pdf_path.stem.replace(" ", "_")
                sr_path = sr_dir / f"{stem}_{cd.laterality}_SR.dcm"
                from pydicom.uid import generate_uid as _gen_uid
                _study_uid = resolve_study_uid(
                    noel_id=cd.noel_id or "",
                    study_date=cd.study_date or "",
                    fallback_uid=_gen_uid(),
                    orthanc_base_url=_orthanc_rest_url(),
                    auth=_orthanc_auth(),
                )
                build_sr(cd, reference_dataset=None, output_path=sr_path,
                         study_instance_uid=_study_uid)
                logger.info("  SR generado: %s", sr_path.name)
                if do_cstore:
                    ok = _cstore(sr_path)
                    logger.info("  C-STORE SR: %s", "OK" if ok else "FALLIDO")
                    if ok:
                        success = True
                else:
                    success = True
            except Exception as e:
                logger.error("  SR build FALLÓ (%s): %s", cd.laterality, e)

        # Mover original a processed/
        processed_dir = pdf_path.parent / "processed"
        processed_dir.mkdir(exist_ok=True)
        dest = processed_dir / pdf_path.name
        if dest.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = processed_dir / f"{pdf_path.stem}_{ts}{pdf_path.suffix}"
        shutil.move(str(pdf_path), str(dest))
        logger.info("  Movido a processed/: %s", dest.name)

    except Exception as e:
        logger.error("  Error procesando PDF Cirrus %s: %s", pdf_path.name, e)

    return success


# ── Watchdog handler ─────────────────────────────────────────────────────────

class OptFileHandler(FileSystemEventHandler):
    def __init__(self, output_dir: Path, do_cstore: bool = True):
        self.output_dir = output_dir
        self.do_cstore  = do_cstore
        self._processing: set[str] = set()

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in ALL_WATCHED_EXTS:
            return
        if str(path) in self._processing:
            return
        self._processing.add(str(path))

        time.sleep(SETTLE_DELAY)

        if not path.exists():
            self._processing.discard(str(path))
            return

        try:
            _route_file(path, self.output_dir, do_cstore=self.do_cstore)
        except Exception as e:
            logger.error("Error no capturado procesando %s: %s", path.name, e)
        finally:
            self._processing.discard(str(path))


# ── Procesamiento de archivos existentes ────────────────────────────────────

def process_existing(watch_dir: Path, output_dir: Path, do_cstore: bool = True) -> None:
    """Procesa todos los archivos soportados ya existentes en la carpeta vigilada."""
    pending = [
        f for f in watch_dir.rglob("*")
        if f.suffix.lower() in ALL_WATCHED_EXTS
        and "processed" not in f.parts
    ]

    if not pending:
        logger.info("No hay archivos pendientes en %s", watch_dir)
        return

    logger.info("Procesando %d archivo(s) existentes...", len(pending))
    for f in sorted(pending):
        try:
            _route_file(f, output_dir, do_cstore=do_cstore)
        except Exception as e:
            logger.error("Error procesando %s: %s", f.name, e)


# ── Setup logging ────────────────────────────────────────────────────────────

def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"watcher_{datetime.now().strftime('%Y%m%d')}.log"

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
        description="Transducin — watcher automático .opt → DICOM + C-STORE"
    )
    parser.add_argument("--watch",   nargs="+",
                        default=[str(DEFAULT_WATCH_DIR)],
                        help=f"Carpeta(s) a vigilar (default: {DEFAULT_WATCH_DIR}); acepta múltiples rutas")
    parser.add_argument("--output",  default=str(DEFAULT_OUTPUT_DIR),
                        help=f"Carpeta de salida DICOM (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--logs",    default=str(DEFAULT_LOG_DIR),
                        help=f"Carpeta de logs (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--no-cstore", action="store_true",
                        help="Deshabilitar C-STORE (solo convertir localmente)")
    parser.add_argument("--process-existing", action="store_true",
                        help="Procesar archivos .opt ya existentes antes de empezar a vigilar")
    parser.add_argument("--orthanc-host", default=None,
                        help="Orthanc host (default: env ORTHANC_HOST o localhost)")
    parser.add_argument("--orthanc-port", type=int, default=None,
                        help="Orthanc port DICOM (default: env ORTHANC_PORT o 4242)")
    parser.add_argument("--orthanc-http-port", type=int, default=None,
                        help="Orthanc REST API port (default: env ORTHANC_HTTP_PORT o 8042)")
    parser.add_argument("--orthanc-user", default=None,
                        help="Orthanc REST user (default: env ORTHANC_HTTP_USER o 'ojos')")
    parser.add_argument("--orthanc-pass", default=None,
                        help="Orthanc REST password (default: env ORTHANC_HTTP_PASS o 'ojos')")
    parser.add_argument("--orthanc-aet",  default=None,
                        help="Orthanc AE Title (default: env ORTHANC_AET o ORTHANC)")
    args = parser.parse_args()

    # Override constants con valores de CLI si se proporcionaron
    import transducin.hot_folder_watcher as _self
    if args.orthanc_host:
        _self.ORTHANC_HOST = args.orthanc_host
    if args.orthanc_port:
        _self.ORTHANC_PORT = args.orthanc_port
    if args.orthanc_http_port:
        _self.ORTHANC_HTTP_PORT = args.orthanc_http_port
    if args.orthanc_user:
        _self.ORTHANC_HTTP_USER = args.orthanc_user
    if args.orthanc_pass:
        _self.ORTHANC_HTTP_PASS = args.orthanc_pass
    if args.orthanc_aet:
        _self.ORTHANC_AET = args.orthanc_aet

    watch_dirs = [Path(d).expanduser().resolve() for d in args.watch]
    output_dir = Path(args.output).expanduser().resolve()
    log_dir    = Path(args.logs).expanduser().resolve()
    do_cstore  = not args.no_cstore

    _setup_logging(log_dir)

    valid_watch_dirs: list[Path] = []
    for wd in watch_dirs:
        if not wd.exists():
            logger.warning("Carpeta de vigilancia no existe (omitida): %s", wd)
        else:
            valid_watch_dirs.append(wd)

    if not valid_watch_dirs:
        logger.error("Ninguna carpeta de vigilancia existe — abortando.")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Transducin watcher iniciando")
    for wd in valid_watch_dirs:
        logger.info("  Vigilando: %s", wd)
    logger.info("  Salida:    %s", output_dir)
    logger.info("  C-STORE:   %s → %s:%d", LOCAL_AET, ORTHANC_HOST, ORTHANC_PORT)
    logger.info("  C-STORE habilitado: %s", do_cstore)

    if args.process_existing:
        for wd in valid_watch_dirs:
            process_existing(wd, output_dir, do_cstore=do_cstore)

    handler  = OptFileHandler(output_dir=output_dir, do_cstore=do_cstore)
    observer = Observer()
    for wd in valid_watch_dirs:
        observer.schedule(handler, str(wd), recursive=True)
    observer.start()
    logger.info("Observador iniciado (%d carpeta(s)). Ctrl+C para detener.", len(valid_watch_dirs))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Deteniendo watcher...")
        observer.stop()

    observer.join()
    logger.info("Watcher detenido.")


if __name__ == "__main__":
    main()
