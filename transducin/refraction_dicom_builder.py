# transducin/refraction_dicom_builder.py
# SPDX-License-Identifier: Apache-2.0
#
# Generador de DICOM Structured Report TID 1500 para datos refractivos
# capturados via RS-232 desde autorefractómetro (URK-800) o lensómetro (CCQ-800).
#
# Pipeline:
#   rs232_capture.py → POST /api/instrument-readings (antiscribe)
#   → build_refraction_sr() → ComprehensiveSR TID 1500 → POST /instances → Orthanc
#
# Estándar:
#   TID 1500 Measurement Report con TID 1501 grupo por ojo.
#   Modality "AR" (autorefractor) o "LEN" (lensómetro).
#
# Códigos (todos 99OFTALMOS — no hay códigos SNOMED estándar para sph/cyl/ax):
#   REFRSPH  — Sphere power (diopters)
#   REFRCYL  — Cylinder power (diopters)
#   REFRAX   — Cylinder axis (degrees)
#   REFRADD  — Near addition power (diopters)
#   REFRPD   — Pupillary distance (mm)
#   REFRVA   — Visual acuity (qualitative, texto libre)
#
# Procedimiento reportado:
#   252886007 SCT "Refraction of eye"  — autorefractor
#   167004    SCT "Lensometry"         — lensómetro
#
# Sitios anatómicos:
#   81745001 SCT "Eye" + lateralidad

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pydicom
from pydicom.uid import generate_uid

import highdicom as hd
import highdicom.sr as hd_sr
from highdicom.coding_schemes import CodingSchemeIdentificationItem

from transducin.dicom_config import (
    MANUFACTURER,
    MANUFACTURER_UNICOS,
    MANUFACTURER_YEASN,
    MODEL_CCQ800,
    MODEL_URK800,
    PRIVATE_SCHEME,
    PRIVATE_SCHEME_NAME,
    PRIVATE_SCHEME_ORG,
    RETINAOS_TRANSFER_SYNTAX,
)
from transducin.orthanc_client import (
    ORTHANC_HTTP_HOST,
    ORTHANC_HTTP_PASS,
    ORTHANC_HTTP_PORT,
    ORTHANC_HTTP_USER,
    _auth_header,
    resolve_study_uid,
)

logger = logging.getLogger("transducin.refraction")

from transducin import __version__ as _TRANSDUCIN_VERSION

# ── Códigos de medición (99OFTALMOS) ─────────────────────────────────────────

_SPH_CODE = hd_sr.CodedConcept("REFRSPH", PRIVATE_SCHEME, "Sphere power")
_CYL_CODE = hd_sr.CodedConcept("REFRCYL", PRIVATE_SCHEME, "Cylinder power")
_AX_CODE  = hd_sr.CodedConcept("REFRAX",  PRIVATE_SCHEME, "Cylinder axis")
_ADD_CODE = hd_sr.CodedConcept("REFRADD", PRIVATE_SCHEME, "Near addition power")
_PD_CODE  = hd_sr.CodedConcept("REFRPD",  PRIVATE_SCHEME, "Pupillary distance")
_VA_CODE  = hd_sr.CodedConcept("REFRVA",  PRIVATE_SCHEME, "Visual acuity")

# Unidades UCUM
_DIOP = hd_sr.CodedConcept("[diop]", "UCUM", "diopter")
_DEG  = hd_sr.CodedConcept("deg",   "UCUM", "degree")
_MM   = hd_sr.CodedConcept("mm",    "UCUM", "millimeter")

# Procedimientos reportados
_PROC_AUTOREFRACTION = hd_sr.CodedConcept("252886007", "SCT", "Refraction of eye")
_PROC_LENSOMETRY     = hd_sr.CodedConcept("167004",    "SCT", "Lensometry")

# Lateralidad
_LAT_RIGHT = hd_sr.CodedConcept("24028007", "SCT", "Right")
_LAT_LEFT  = hd_sr.CodedConcept("7771000",  "SCT", "Left")

# Sitio anatómico
_SITE_EYE = hd_sr.CodedConcept("81745001", "SCT", "Eye")


# ── Dataclass de entrada ──────────────────────────────────────────────────────

@dataclass
class RefractionEye:
    """Mediciones refractivas de un ojo, tal como las reporta el instrumento."""
    eye:       str                   # "OD" | "OI"
    sph:       Optional[float] = None  # esfera en dioptrías
    cyl:       Optional[float] = None  # cilindro en dioptrías
    ax:        Optional[int]   = None  # eje en grados (0–180)
    add_power: Optional[float] = None  # adición para cerca en dioptrías
    pd:        Optional[float] = None  # distancia pupilar en mm
    va:        Optional[str]   = None  # agudeza visual texto libre ("20/20", "0.8")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lat_code(eye: str) -> hd_sr.CodedConcept:
    return _LAT_RIGHT if eye.upper() in ("OD", "R") else _LAT_LEFT


def _make_tracking(label: str) -> hd_sr.TrackingIdentifier:
    return hd_sr.TrackingIdentifier(uid=generate_uid(), identifier=label)


def _finding_site(eye: str) -> hd_sr.FindingSite:
    return hd_sr.FindingSite(
        anatomic_location=_SITE_EYE,
        laterality=_lat_code(eye),
    )


def _measurement(
    name: hd_sr.CodedConcept,
    value: float,
    unit: hd_sr.CodedConcept,
    label: str,
    eye: str,
) -> hd_sr.Measurement:
    return hd_sr.Measurement(
        name=name,
        value=value,
        unit=unit,
        tracking_identifier=_make_tracking(label),
        finding_sites=[_finding_site(eye)],
    )


def _to_dicom_pn(name: str) -> str:
    if not name or "^" in name:
        return name
    if "_" in name:
        parts = name.split("_", 1)
        return f"{parts[0].strip()}^{parts[1].strip()}"
    return name


# ── Grupo de medición por ojo ─────────────────────────────────────────────────

def _build_eye_group(
    reading: RefractionEye,
    device_source: str,
) -> Optional[hd_sr.MeasurementsAndQualitativeEvaluations]:
    """Construye un grupo TID 1501 con las mediciones de un ojo."""
    eye = reading.eye.upper()
    measurements: list[hd_sr.Measurement] = []
    qualitative: list[hd_sr.QualitativeEvaluation] = []

    if reading.sph is not None:
        measurements.append(_measurement(_SPH_CODE, reading.sph, _DIOP, f"Sph {eye}", eye))

    if reading.cyl is not None:
        measurements.append(_measurement(_CYL_CODE, reading.cyl, _DIOP, f"Cyl {eye}", eye))

    if reading.ax is not None:
        measurements.append(_measurement(_AX_CODE, float(reading.ax), _DEG, f"Ax {eye}", eye))

    if reading.add_power is not None:
        measurements.append(_measurement(_ADD_CODE, reading.add_power, _DIOP, f"Add {eye}", eye))

    if reading.pd is not None:
        measurements.append(_measurement(_PD_CODE, reading.pd, _MM, f"PD {eye}", eye))

    if reading.va:
        va_code = hd_sr.CodedConcept(
            reading.va.replace("/", "_").replace(".", "p")[:16],
            PRIVATE_SCHEME,
            reading.va,
        )
        qualitative.append(hd_sr.QualitativeEvaluation(name=_VA_CODE, value=va_code))

    if not measurements and not qualitative:
        return None

    label = f"Refraction-{eye}-{device_source}"
    return hd_sr.MeasurementsAndQualitativeEvaluations(
        tracking_identifier=_make_tracking(label),
        measurements=measurements if measurements else None,
        qualitative_evaluations=qualitative if qualitative else None,
    )


# ── Builder principal ─────────────────────────────────────────────────────────

def build_refraction_sr(
    readings: list[RefractionEye],
    noel_id: str,
    device_source: str,
    device_model: str,
    study_date: str,
    patient_name: str = "",
    patient_dob: str = "",
    output_dir: Optional[Path] = None,
    orthanc_base_url: Optional[str] = None,
    auth: Optional[tuple[str, str]] = None,
) -> Optional[Path]:
    """Genera un DICOM ComprehensiveSR TID 1500 con mediciones refractivas
    y lo sube a Orthanc. Si Orthanc no está disponible, guarda el .dcm en
    output_dir.

    Args:
        readings:        Lista de RefractionEye (OD y/o OI).
        noel_id:         PatientID formato NOEL (ej. "JAHJ19870831").
        device_source:   "autorefractor" | "lensometer"
        device_model:    "URK-800" | "CCQ-800" u otro
        study_date:      Fecha YYYYMMDD de la captura.
        patient_name:    Nombre completo (opcional).
        patient_dob:     Fecha nacimiento YYYYMMDD (opcional).
        output_dir:      Directorio destino para el .dcm (default: /tmp).
        orthanc_base_url: URL REST de Orthanc (default: env ORTHANC_HTTP_*).
        auth:            (user, password) para Orthanc (default: env).

    Returns:
        Path al .dcm guardado, o None si falló la generación.
    """
    if not readings:
        logger.warning("build_refraction_sr: lista de lecturas vacía — abortando")
        return None

    # ── Grupos por ojo ────────────────────────────────────────────────────────
    groups: list[hd_sr.MeasurementsAndQualitativeEvaluations] = []
    for r in readings:
        g = _build_eye_group(r, device_source)
        if g is not None:
            groups.append(g)

    if not groups:
        logger.warning("build_refraction_sr: sin mediciones válidas — abortando")
        return None

    # ── Procedimiento y observación ───────────────────────────────────────────
    proc = _PROC_AUTOREFRACTION if device_source == "autorefractor" else _PROC_LENSOMETRY

    from pydicom.sr.codedict import codes as dcm_codes
    obs_ctx = hd_sr.ObservationContext(
        observer_person_context=hd_sr.ObserverContext(
            observer_type=dcm_codes.cid270.Person,
            observer_identifying_attributes=hd_sr.PersonObserverIdentifyingAttributes(
                name="RetinaOS^Transducin",
            ),
        )
    )

    report = hd_sr.MeasurementReport(
        observation_context=obs_ctx,
        procedure_reported=proc,
        imaging_measurements=groups,
    )

    # ── Fabricante ────────────────────────────────────────────────────────────
    if device_source == "autorefractor":
        _mfr   = MANUFACTURER_UNICOS
        _model = MODEL_URK800
    else:
        _mfr   = MANUFACTURER_YEASN
        _model = MODEL_CCQ800
    # Si el device_model real difiere del default, usarlo literalmente
    if device_model and device_model not in (MODEL_URK800, MODEL_CCQ800):
        _model = device_model

    # ── Esquema privado ───────────────────────────────────────────────────────
    _private_cs = CodingSchemeIdentificationItem(
        designator=PRIVATE_SCHEME,
        name=PRIVATE_SCHEME_NAME,
        responsible_organization=PRIVATE_SCHEME_ORG,
    )

    # ── Dataset de referencia mínimo ──────────────────────────────────────────
    # ComprehensiveSR requiere un Dataset de referencia con metadatos de paciente.
    # Para refracción no hay imagen DICOM fuente — construimos un stub completo.
    now      = datetime.now()
    modality = "AR" if device_source == "autorefractor" else "LEN"

    from pydicom.dataset import FileMetaDataset
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID    = "1.2.840.10008.5.1.4.1.1.78.3"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID          = RETINAOS_TRANSFER_SYNTAX

    ref_ds = pydicom.Dataset()
    ref_ds.file_meta = file_meta
    ref_ds.ensure_file_meta()

    ref_ds.SOPClassUID    = "1.2.840.10008.5.1.4.1.1.78.3"
    ref_ds.SOPInstanceUID = generate_uid()
    ref_ds.StudyInstanceUID  = resolve_study_uid(
        noel_id=noel_id,
        study_date=study_date,
        fallback_uid=generate_uid(),
        orthanc_base_url=orthanc_base_url,
        auth=auth,
    )
    ref_ds.SeriesInstanceUID = generate_uid()
    ref_ds.PatientName       = _to_dicom_pn(patient_name) or noel_id
    ref_ds.PatientID         = noel_id
    ref_ds.PatientBirthDate  = patient_dob or ""
    ref_ds.PatientSex        = ""
    ref_ds.StudyDate         = study_date
    ref_ds.StudyTime         = ""
    ref_ds.AccessionNumber   = ""
    ref_ds.StudyID           = ""
    ref_ds.ReferringPhysicianName = ""
    ref_ds.Modality          = modality
    ref_ds.SeriesNumber      = 1
    ref_ds.InstanceNumber    = 1

    # ── ComprehensiveSR ───────────────────────────────────────────────────────
    sr = hd.sr.ComprehensiveSR(
        evidence=[ref_ds],
        content=report,
        series_instance_uid=generate_uid(),
        series_number=910,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer=_mfr,
        is_final=True,
        coding_schemes=[_private_cs],
    )

    sr.ManufacturerModelName = _model
    sr.DeviceSerialNumber    = ""
    sr.PatientID             = noel_id
    sr.PatientName           = _to_dicom_pn(patient_name) or noel_id
    sr.PatientBirthDate      = patient_dob or ""
    sr.StudyDate             = study_date
    sr.StudyInstanceUID      = ref_ds.StudyInstanceUID
    sr.ContentDate           = now.strftime("%Y%m%d")
    sr.ContentTime           = now.strftime("%H%M%S.%f")
    sr.add_new((0x0009, 0x0010), "LO", MANUFACTURER)
    sr.add_new((0x0009, 0x1001), "LO", _TRANSDUCIN_VERSION)

    # ── Serializar ────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    sr.save_as(buf, write_like_original=False)
    dcm_bytes = buf.getvalue()

    # ── Upload a Orthanc ──────────────────────────────────────────────────────
    base_url = orthanc_base_url or f"http://{ORTHANC_HTTP_HOST}:{ORTHANC_HTTP_PORT}"
    _auth    = auth or (ORTHANC_HTTP_USER, ORTHANC_HTTP_PASS)

    from urllib.request import Request, urlopen
    from urllib.error import URLError

    uploaded = False
    try:
        req = Request(f"{base_url}/instances", data=dcm_bytes, method="POST")
        req.add_header("Content-Type", "application/dicom")
        req.add_header("Authorization", _auth_header(_auth[0], _auth[1]))
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.info(
                "refraction SR → Orthanc OK: %s (%s %s)",
                result.get("ID", "?"), device_source, noel_id,
            )
            uploaded = True
    except URLError as e:
        logger.warning("Orthanc no disponible — guardando .dcm local: %s", e)
    except Exception as e:
        logger.warning("Error subiendo a Orthanc: %s", e)

    # ── Guardar en disco (siempre, como respaldo) ─────────────────────────────
    out_dir = Path(output_dir) if output_dir else Path("/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    lat_str = "_".join(r.eye for r in readings)
    fname   = f"{noel_id}_{study_date}_{device_source}_{lat_str}_SR.dcm"
    out_path = out_dir / fname
    out_path.write_bytes(dcm_bytes)
    if not uploaded:
        logger.info("Guardado en disco: %s", out_path)

    return out_path


# ── Tests internos ────────────────────────────────────────────────────────────

def _run_tests() -> None:
    from pathlib import Path as _Path
    import tempfile

    errs = 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal errs
        status = "OK" if cond else "FAIL"
        if not cond:
            errs += 1
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))

    print("═══ refraction_dicom_builder tests ═══")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: autorefractor OD + OI
        path = build_refraction_sr(
            readings=[
                RefractionEye("OD", -1.25, -0.75, 180, None, 64.0, "20/30"),
                RefractionEye("OI", -0.50, -0.25, 90,  None, 64.0, "20/20"),
            ],
            noel_id="JAHJ19870831",
            device_source="autorefractor",
            device_model="URK-800",
            study_date="20260410",
            output_dir=_Path(tmpdir),
            orthanc_base_url="http://127.0.0.1:19999",  # sin Orthanc — solo disco
        )
        check("autorefractor OD+OI genera .dcm", path is not None and path.exists())

        if path and path.exists():
            ds = pydicom.dcmread(str(path))
            check("SOPClassUID es ComprehensiveSR",
                  str(ds.SOPClassUID) == "1.2.840.10008.5.1.4.1.1.88.33",
                  str(ds.SOPClassUID))
            check("PatientID correcto", ds.PatientID == "JAHJ19870831")
            check("Modality SR", ds.Modality == "SR")  # ComprehensiveSR siempre es SR

        # Test 2: lensómetro solo OD con adición
        path2 = build_refraction_sr(
            readings=[RefractionEye("OD", -2.0, -1.0, 170, 2.25, None, None)],
            noel_id="JAHJ19870831",
            device_source="lensometer",
            device_model="CCQ-800",
            study_date="20260410",
            output_dir=_Path(tmpdir),
            orthanc_base_url="http://127.0.0.1:19999",
        )
        check("lensómetro OD con add genera .dcm", path2 is not None and path2.exists())

        if path2 and path2.exists():
            ds2 = pydicom.dcmread(str(path2))
            check("Modality SR (lensómetro)", ds2.Modality == "SR")

        # Test 3: lista vacía no genera archivo
        path3 = build_refraction_sr(
            readings=[],
            noel_id="JAHJ19870831",
            device_source="autorefractor",
            device_model="URK-800",
            study_date="20260410",
            output_dir=_Path(tmpdir),
        )
        check("lista vacía retorna None", path3 is None)

    print(f"{'PASS' if errs == 0 else 'FAIL'} — {errs} error(es)\n")
    if errs:
        raise SystemExit(1)


if __name__ == "__main__":
    import logging as _log
    _log.basicConfig(level=_log.INFO)
    _run_tests()
