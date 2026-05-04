# transducin/vf_sr_builder.py
# SPDX-License-Identifier: Apache-2.0
#
# Generador de DICOM Structured Report TID 1500 para datos de campo visual
# (Visual Field / Perimetría estática). Recibe VisualFieldData y produce un
# ComprehensiveSR con los índices globales (MD, PSD, VFI, GHT) y opcionalmente
# la rejilla de puntos individuales.
#
# Diseñado para integrar con pts925_watcher.py:
#   PTS 925Wi → DICOM OPV → pts925_extractor → VisualFieldData
#   → build_vf_sr() → DICOM SR TID 1500 → C-STORE → Orthanc
#
# Estándar:
#   TID 1500 Measurement Report con TID 1501 grupo "Visual Field"
#   Anticipando TID 6002 (Visual Field Key Measurements, Supplement 247)
#
# Códigos SNOMED-CT / DICOM:
#   MD   392023002 — Mean deviation of visual field
#   PSD  392024008 — Pattern standard deviation of visual field
#   VFI  VFINDEX   (99OFTALMOS, provisional) — Visual Field Index
#   GHT  GHTEST    (99OFTALMOS, provisional) — Glaucoma Hemifield Test
#   FL   FIXLOSS   (99OFTALMOS, provisional) — Fixation losses
#   FP   FALSEPOS  (99OFTALMOS, provisional) — False positives rate
#   FN   FALSENEG  (99OFTALMOS, provisional) — False negatives rate
#   FOVT FOVTHRES  (99OFTALMOS, provisional) — Foveal threshold
#
# Sitios anatómicos:
#   Visual field    416533001 — Visual pathway structure (SCT)
#   Eye             81745001  — Eye (SCT)

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

import highdicom as hd
import highdicom.sr as hd_sr
from highdicom.coding_schemes import CodingSchemeIdentificationItem
from pydicom.sr.codedict import codes as dcm_codes

from transducin.clinical_data import VisualFieldData, study_description_label
from transducin.dicom_config import (
    MANUFACTURER,
    MANUFACTURER_OPTOPOL,
    PRIVATE_SCHEME,
    PRIVATE_SCHEME_NAME,
    PRIVATE_SCHEME_ORG,
    RETINAOS_TRANSFER_SYNTAX,
    SOP_PERIMETRY,
)
from transducin.noel_id import is_valid_noel, dob_from_noel

logger = logging.getLogger(__name__)

from transducin import __version__ as _TRANSDUCIN_VERSION

# ── Measurement codes ────────────────────────────────────────────────────────

# SNOMED-CT — los que tienen código estándar
_MD_CODE  = hd_sr.CodedConcept("392023002", "SCT", "Mean deviation of visual field")
_PSD_CODE = hd_sr.CodedConcept("392024008", "SCT", "Pattern standard deviation of visual field")

# 99OFTALMOS — provisionales hasta que Supplement 247 TID 6002 sea adoptado
_VFI_CODE     = hd_sr.CodedConcept("VFINDEX",   PRIVATE_SCHEME, "Visual Field Index")
_GHT_CODE     = hd_sr.CodedConcept("GHTEST",    PRIVATE_SCHEME, "Glaucoma Hemifield Test")
_FL_CODE      = hd_sr.CodedConcept("FIXLOSS",   PRIVATE_SCHEME, "Fixation losses")
_FP_CODE      = hd_sr.CodedConcept("FALSEPOS",  PRIVATE_SCHEME, "False positive rate")
_FN_CODE      = hd_sr.CodedConcept("FALSENEG",  PRIVATE_SCHEME, "False negative rate")
_FOVT_CODE    = hd_sr.CodedConcept("FOVTHRES",  PRIVATE_SCHEME, "Foveal threshold")

# Unidades UCUM
_DB    = hd_sr.CodedConcept("dB",  "UCUM", "decibel")
_PCT   = hd_sr.CodedConcept("%",   "UCUM", "percent")
_RATIO = hd_sr.CodedConcept("1",   "UCUM", "no units")

# Procedimiento reportado
_VF_PROC = hd_sr.CodedConcept("252780009", "SCT", "Static visual field test")

# Sitios anatómicos
_SITE_EYE         = hd_sr.CodedConcept("81745001",  "SCT", "Eye")
_SITE_VISUAL_PATH = hd_sr.CodedConcept("416533001", "SCT", "Visual pathway structure")

# Lateralidad
_LAT_RIGHT = hd_sr.CodedConcept("24028007", "SCT", "Right")
_LAT_LEFT  = hd_sr.CodedConcept("7771000",  "SCT", "Left")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _lat_code(laterality: str) -> hd_sr.CodedConcept:
    return _LAT_RIGHT if laterality.upper() in ("R", "OD") else _LAT_LEFT


def _make_tracking(label: str) -> hd_sr.TrackingIdentifier:
    return hd_sr.TrackingIdentifier(uid=generate_uid(), identifier=label)


def _finding_site(
    site: hd_sr.CodedConcept,
    laterality: str,
) -> hd_sr.FindingSite:
    return hd_sr.FindingSite(
        anatomic_location=site,
        laterality=_lat_code(laterality),
    )


def _measurement(
    name_code: hd_sr.CodedConcept,
    value: float,
    unit: hd_sr.CodedConcept,
    label: str,
    finding_sites: Optional[list] = None,
) -> hd_sr.Measurement:
    return hd_sr.Measurement(
        name=name_code,
        value=value,
        unit=unit,
        tracking_identifier=_make_tracking(label),
        finding_sites=finding_sites,
    )


def _algo_id() -> hd_sr.AlgorithmIdentification:
    return hd_sr.AlgorithmIdentification(
        name="Transducin",
        version=_TRANSDUCIN_VERSION,
        parameters=["PTS 925Wi DICOM OPV extraction"],
    )


def _to_dicom_pn(name: str) -> str:
    """Convierte nombre al formato DICOM PN (Apellidos^Nombres)."""
    if not name:
        return name
    if "^" in name:
        return name
    if "_" in name:
        parts = name.split("_", 1)
        return f"{parts[0].strip()}^{parts[1].strip()}"
    return name


# ── Visual Field measurement group ──────────────────────────────────────────

def _build_vf_group(
    data: VisualFieldData,
) -> Optional[hd_sr.MeasurementsAndQualitativeEvaluations]:
    """Grupo TID 1501 para mediciones de campo visual: MD, PSD, VFI, GHT,
    reliability y foveal threshold."""
    lat = data.laterality or "R"
    measurements: list[hd_sr.Measurement] = []
    qualitative_evals: list[hd_sr.QualitativeEvaluation] = []
    sites = [_finding_site(_SITE_EYE, lat)]

    # ── Índices globales ─────────────────────────────────────────────────────
    if data.md_db is not None:
        measurements.append(_measurement(
            _MD_CODE, data.md_db, _DB, "MD",
            finding_sites=sites,
        ))

    if data.psd_db is not None:
        measurements.append(_measurement(
            _PSD_CODE, data.psd_db, _DB, "PSD",
            finding_sites=sites,
        ))

    if data.vfi_pct is not None:
        measurements.append(_measurement(
            _VFI_CODE, data.vfi_pct, _PCT, "VFI",
            finding_sites=sites,
        ))

    # GHT como evaluación cualitativa (es texto, no numérico)
    if data.ght:
        ght_value_code = hd_sr.CodedConcept(
            data.ght.upper().replace(" ", "_")[:16],
            PRIVATE_SCHEME,
            data.ght,
        )
        qualitative_evals.append(
            hd_sr.QualitativeEvaluation(
                name=_GHT_CODE,
                value=ght_value_code,
            )
        )

    # ── Reliability ──────────────────────────────────────────────────────────
    if data.fixation_losses is not None:
        measurements.append(_measurement(
            _FL_CODE, round(data.fixation_losses * 100, 1), _PCT,
            "Fixation losses",
            finding_sites=sites,
        ))

    if data.false_pos_pct is not None:
        measurements.append(_measurement(
            _FP_CODE, data.false_pos_pct, _PCT, "False positives",
            finding_sites=sites,
        ))

    if data.false_neg_pct is not None:
        measurements.append(_measurement(
            _FN_CODE, data.false_neg_pct, _PCT, "False negatives",
            finding_sites=sites,
        ))

    # ── Foveal threshold ─────────────────────────────────────────────────────
    if data.foveal_threshold_db is not None:
        measurements.append(_measurement(
            _FOVT_CODE, data.foveal_threshold_db, _DB, "Foveal threshold",
            finding_sites=sites,
        ))

    if not measurements and not qualitative_evals:
        return None

    return hd_sr.MeasurementsAndQualitativeEvaluations(
        tracking_identifier=_make_tracking(
            f"Transducin-VF-{lat}-{data.study_date}"
        ),
        finding_sites=sites,
        algorithm_id=_algo_id(),
        measurements=measurements if measurements else None,
        qualitative_evaluations=qualitative_evals if qualitative_evals else None,
    )


# ── Build SR ─────────────────────────────────────────────────────────────────

def build_vf_sr(
    data: VisualFieldData,
    reference_dataset: Optional[Dataset] = None,
    output_path: Optional[Path] = None,
    study_instance_uid: Optional[str] = None,
) -> Dataset:
    """Construye un DICOM SR TID 1500 desde VisualFieldData.

    Genera un grupo TID 1501 con los índices globales del campo visual
    (MD, PSD, VFI), GHT como evaluación cualitativa, y métricas de
    confiabilidad (fixation losses, false pos/neg).

    Args:
        data:               VisualFieldData del PTS 925Wi.
        reference_dataset:  Dataset DICOM OPV de referencia (opcional).
        output_path:        Si se provee, guarda el SR en disco.
        study_instance_uid: StudyInstanceUID para vincular al mismo estudio.

    Returns:
        Dataset pydicom del SR (ComprehensiveSR TID 1500).
    """
    if not data.noel_id:
        raise ValueError("VisualFieldData.noel_id es requerido para generar SR.")
    if not is_valid_noel(data.noel_id):
        logger.warning(
            "noel_id '%s' no cumple formato NOEL — SR generado de todas formas.",
            data.noel_id,
        )

    ref_ds = reference_dataset if reference_dataset is not None else _minimal_vf_reference(data)

    if study_instance_uid:
        ref_ds.StudyInstanceUID = study_instance_uid

    # ── Observation context ───────────────────────────────────────────────────
    obs_ctx = hd_sr.ObservationContext(
        observer_person_context=hd_sr.ObserverContext(
            observer_type=dcm_codes.cid270.Person,
            observer_identifying_attributes=hd_sr.PersonObserverIdentifyingAttributes(
                name="TRANSDUCIN^RETINAOS",
            ),
        ),
        observer_device_context=hd_sr.ObserverContext(
            observer_type=dcm_codes.cid270.Device,
            observer_identifying_attributes=hd_sr.DeviceObserverIdentifyingAttributes(
                uid=generate_uid(),
                name="Optopol Technology PTS 925Wi",
            ),
        ),
    )

    # ── Grupo VF ──────────────────────────────────────────────────────────────
    groups: list[hd_sr.MeasurementsAndQualitativeEvaluations] = []

    vf_group = _build_vf_group(data)
    if vf_group is not None:
        groups.append(vf_group)

    if not groups:
        logger.warning("VisualFieldData sin mediciones — SR generado sin grupo de medición.")
        groups.append(hd_sr.MeasurementsAndQualitativeEvaluations(
            tracking_identifier=_make_tracking(
                f"Transducin-VF-empty-{data.laterality}"
            ),
        ))

    # ── MeasurementReport TID 1500 ────────────────────────────────────────────
    report = hd_sr.MeasurementReport(
        observation_context=obs_ctx,
        procedure_reported=_VF_PROC,
        imaging_measurements=groups,
    )

    # ── ComprehensiveSR ───────────────────────────────────────────────────────
    # Registrar coding scheme privado (DICOM PS3.3 C.12.1)
    _private_cs = CodingSchemeIdentificationItem(
        designator=PRIVATE_SCHEME,
        name=PRIVATE_SCHEME_NAME,
        responsible_organization=PRIVATE_SCHEME_ORG,
    )

    now = datetime.now()
    sr = hd.sr.ComprehensiveSR(
        evidence=[ref_ds],
        content=report,
        series_instance_uid=generate_uid(),
        series_number=910,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer=MANUFACTURER_OPTOPOL,
        is_final=True,
        coding_schemes=[_private_cs],
    )

    sr.ManufacturerModelName = "PTS 925Wi"
    sr.DeviceSerialNumber    = data.device_serial or ""
    sr.PatientID             = data.noel_id
    sr.PatientName           = _to_dicom_pn(data.patient_name) or data.noel_id
    sr.PatientBirthDate      = data.patient_dob or dob_from_noel(data.noel_id)
    sr.StudyDate             = data.study_date or now.strftime("%Y%m%d")
    sr.StudyDescription      = study_description_label("visual_field", data.laterality)
    sr.ContentDate           = now.strftime("%Y%m%d")
    sr.ContentTime           = now.strftime("%H%M%S.%f")

    # Trazabilidad Transducin (private tags 0009,xx)
    sr.add_new((0x0009, 0x0010), "LO", MANUFACTURER)
    sr.add_new((0x0009, 0x1001), "CS", data.extraction_confidence.upper())
    sr.add_new((0x0009, 0x1002), "LO", Path(data.source_file).name[:64])
    sr.add_new((0x0009, 0x1003), "LO", f"Transducin/{_TRANSDUCIN_VERSION}")

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sr.save_as(str(output_path), write_like_original=False)
        logger.info(
            "VF SR guardado: %s (PatientID=%s, MD=%s, PSD=%s)",
            output_path, data.noel_id,
            f"{data.md_db:.1f}" if data.md_db is not None else "N/A",
            f"{data.psd_db:.1f}" if data.psd_db is not None else "N/A",
        )

    return sr


def _minimal_vf_reference(data: VisualFieldData) -> Dataset:
    """Dataset mínimo de referencia cuando no hay DICOM OPV disponible."""
    from pydicom.dataset import FileMetaDataset

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = SOP_PERIMETRY
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = RETINAOS_TRANSFER_SYNTAX

    ds = Dataset()
    ds.file_meta = file_meta
    ds.ensure_file_meta()

    ds.SOPClassUID       = SOP_PERIMETRY
    ds.SOPInstanceUID    = generate_uid()
    ds.StudyInstanceUID  = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientID         = data.noel_id
    ds.PatientName       = _to_dicom_pn(data.patient_name) or data.noel_id
    ds.PatientBirthDate  = data.patient_dob or dob_from_noel(data.noel_id)
    ds.PatientSex        = ""
    ds.StudyDate         = data.study_date or datetime.now().strftime("%Y%m%d")
    ds.StudyTime         = ""
    ds.AccessionNumber   = ""
    ds.StudyID           = ""
    ds.StudyDescription  = study_description_label("visual_field", data.laterality)
    ds.ReferringPhysicianName = ""
    ds.Modality          = "OPV"
    ds.SeriesNumber      = 1
    ds.InstanceNumber    = 1
    ds.ImageLaterality   = data.laterality or ""
    ds.Rows              = 1
    ds.Columns           = 1
    ds.BitsAllocated     = 8
    ds.BitsStored        = 8
    ds.HighBit           = 7
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel   = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData         = b"\x00"

    return ds


# ── Tests ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    logging.basicConfig(level=logging.INFO)
    errs = 0

    def check(label, cond, detail=""):
        global errs
        status = "OK" if cond else "FAIL"
        if not cond:
            errs += 1
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))

    print("=== vf_sr_builder tests ===")

    from transducin.clinical_data import VisualFieldData, VisualFieldPoint

    vf = VisualFieldData(
        noel_id="JAHJ19870831",
        laterality="L",
        study_date="20260226",
        patient_name="JAURRIETA_JESUS",
        md_db=-3.5,
        psd_db=4.2,
        vfi_pct=87.0,
        ght="Within Normal Limits",
        fixation_losses=0.05,
        false_pos_pct=2.0,
        false_neg_pct=1.0,
        foveal_threshold_db=34.0,
        device_serial="PTS925-001",
        source_file="test_opv.dcm",
        extraction_confidence="confirmed",
        points=[VisualFieldPoint(x_deg=-3, y_deg=3, threshold_db=28.0)],
    )

    # Test 1: Build SR sin referencia
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "test_vf_sr.dcm"
        sr_ds = build_vf_sr(vf, output_path=out)
        check("SR generado", sr_ds is not None)
        check("SR en disco", out.exists())
        check("PatientID", sr_ds.PatientID == "JAHJ19870831")
        check("Modality SR", sr_ds.Modality == "SR")
        check("ContentDate", sr_ds.ContentDate != "")

        # Verificar que se puede releer
        import pydicom
        re = pydicom.dcmread(str(out))
        check("SR releíble", re.PatientID == "JAHJ19870831")

    # Test 2: SR sin mediciones
    vf_empty = VisualFieldData(noel_id="TEST12345678", laterality="R", study_date="20260401")
    sr_empty = build_vf_sr(vf_empty)
    check("SR vacío generado", sr_empty is not None)

    # Test 3: noel_id requerido
    try:
        build_vf_sr(VisualFieldData())
        check("ValueError sin noel_id", False)
    except ValueError:
        check("ValueError sin noel_id", True)

    print(f"{'PASS' if errs == 0 else 'FAIL'} — {errs} error(es)")
    if errs:
        raise SystemExit(1)
