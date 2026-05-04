# transducin/sr_builder.py
# SPDX-License-Identifier: Apache-2.0
#
# Generador de DICOM Structured Report TID 1500 (Measurement Report)
# usando highdicom. Recibe OCTClinicalData y un Dataset DICOM de referencia
# (la imagen OCT ya almacenada en Orthanc) y produce un .dcm SR válido.
#
# Estándar implementado:
#   TID 1500 (Measurement Report) con TID 1501 grupos separados por tipo
#   de estudio, con anatomic context conforme a Supplement 247 / DICOM 2025c:
#     - FindingSite con lateralidad (CID 4209 + CID 244)
#     - Modificadores topográficos S/I/N/T (SNOMED-CT)
#     - AlgorithmIdentification por grupo
#     - Grupos separados: macular | peripapillar RNFL | biometría
#
# Supp 247 Eyecare Templates (TID 6000-series) no están implementados en
# highdicom 0.22.0 (Python 3.9). Esta implementación anticipa su estructura
# usando los mecanismos disponibles en TID 1500/1501.
#
# SNOMED-CT codes usados:
#   CMT          422453003 — Foveal retinal thickness
#   ETDRS sector 422399008 — Macular retinal thickness
#   RNFL         422995006 — Retinal nerve fiber layer thickness
#   mGCIPL       422455005 — Macular ganglion cell layer thickness
#   C/D ratio    363932005 — Cup to disc ratio
#   VCDR         363930007 — Vertical cup to disc ratio
#   AL           252017007 — Axial length of eye
#   CCT          397545004 — Corneal thickness measurement
#   K1           252014009 — Flat corneal meridian curvature
#   K2           252016006 — Steep corneal meridian curvature
#
# Códigos provisionales (99OFTALMOS — pending Supplement 247 / highdicom #406):
#   Disc area    DISCAREA  (99OFTALMOS) — Optic disc area (mm²)
#   Rim area     RIMAREA   (99OFTALMOS) — Neuroretinal rim area (mm²)
#   Cup volume   CUPVOL    (99OFTALMOS) — Optic cup volume (mm³)
#
# Anatomic sites (CID 4209):
#   Fovea centralis  67046006 — FoveaCentralis
#   Retina           5665001  — Retina
#   Optic nerve head 81016008 — OpticNerveHead
#   Eye              81745001 — Eye
#
# Topographic sector modifiers (SNOMED-CT):
#   Superior  264217000  Inferior  261089000
#   Nasal     255454004  Temporal  255352004

from __future__ import annotations

import logging
import os
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

from transducin.clinical_data import OCTClinicalData, ETDRSGrid, RNFLSectors, study_description_label
from transducin.dicom_config import (
    MANUFACTURER,
    MANUFACTURER_OPTOPOL,
    MANUFACTURER_ZEISS,
    MODEL_CIRRUS,
    MODEL_REVO,
    PRIVATE_SCHEME,
    PRIVATE_SCHEME_NAME,
    PRIVATE_SCHEME_ORG,
    RETINAOS_TRANSFER_SYNTAX,
    SOP_OPT,
)
from transducin.noel_id import is_valid_noel, dob_from_noel

logger = logging.getLogger(__name__)

# ── Versión del algoritmo ─────────────────────────────────────────────────────
from transducin import __version__ as _TRANSDUCIN_VERSION

# ── Medición codes (SNOMED-CT) ────────────────────────────────────────────────
_CMT_CODE    = hd_sr.CodedConcept("422453003", "SCT", "Foveal retinal thickness")
_ETDRS_CODE  = hd_sr.CodedConcept("422399008", "SCT", "Macular retinal thickness")
_RNFL_CODE   = hd_sr.CodedConcept("422995006", "SCT", "Retinal nerve fiber layer thickness")
_CD_CODE     = hd_sr.CodedConcept("363932005", "SCT", "Cup to disc ratio")
_VCDR_CODE   = hd_sr.CodedConcept("363930007", "SCT", "Vertical cup to disc ratio")
# Provisional — coding scheme privado 99OFTALMOS (CS ≤16 chars, sin hyphens)
# Registrado via CodingSchemeIdentificationSequence. Actualizar con Supp247.
_DISC_AREA_CODE = hd_sr.CodedConcept("DISCAREA",  PRIVATE_SCHEME, "Optic disc area")
_RIM_AREA_CODE  = hd_sr.CodedConcept("RIMAREA",   PRIVATE_SCHEME, "Neuroretinal rim area")
_CUP_VOL_CODE   = hd_sr.CodedConcept("CUPVOL",    PRIVATE_SCHEME, "Optic cup volume")
_GCLIPL_CODE = hd_sr.CodedConcept("422455005", "SCT", "Macular ganglion cell layer thickness")
_AL_CODE     = hd_sr.CodedConcept("252017007", "SCT", "Axial length of eye")
_CCT_CODE    = hd_sr.CodedConcept("397545004", "SCT", "Corneal thickness measurement")
_K1_CODE     = hd_sr.CodedConcept("252014009", "SCT", "Flat corneal meridian curvature")
_K2_CODE     = hd_sr.CodedConcept("252016006", "SCT", "Steep corneal meridian curvature")
_SQI_CODE         = hd_sr.CodedConcept("113061", "DCM", "Signal to Noise Ratio")
# Evaluación cualitativa de calidad (DCM 111069)
_SQI_QUALITY_NAME = hd_sr.CodedConcept("SQIEVAL", PRIVATE_SCHEME, "SQI Quality Evaluation")
_SQI_QUALITY_CODE = hd_sr.CodedConcept("111069",  "DCM", "Image quality is suboptimal")

# Umbral SQI para advertencia — leído en tiempo de importación para performance;
# override posible vía TRANSDUCIN_SQI_MIN_WARN=<0-10> (escala 0-10, default 6)
_SQI_WARN_THRESHOLD = float(os.environ.get("TRANSDUCIN_SQI_MIN_WARN", "6")) / 10.0

# Procedimiento reportado
_OCT_PROC    = hd_sr.CodedConcept("252416005", "SCT", "Optical coherence tomography of retina")

# Unidades UCUM
_UM    = hd_sr.CodedConcept("um",   "UCUM", "micrometer")
_MM    = hd_sr.CodedConcept("mm",   "UCUM", "millimeter")
_MM2   = hd_sr.CodedConcept("mm2",  "UCUM", "square millimeter")
_MM3   = hd_sr.CodedConcept("mm3",  "UCUM", "cubic millimeter")
_RATIO = hd_sr.CodedConcept("1",    "UCUM", "no units")

# ── Sitios anatómicos (SNOMED-CT via CID 4209) ────────────────────────────────
_SITE_FOVEA      = hd_sr.CodedConcept("67046006", "SCT", "Fovea centralis")
_SITE_RETINA     = hd_sr.CodedConcept("5665001",  "SCT", "Retina")
_SITE_OPTIC_HEAD = hd_sr.CodedConcept("81016008", "SCT", "Optic nerve head")
_SITE_EYE        = hd_sr.CodedConcept("81745001", "SCT", "Eye")

# ── Lateralidad (SNOMED-CT via CID 244) ──────────────────────────────────────
_LAT_RIGHT = hd_sr.CodedConcept("24028007", "SCT", "Right")
_LAT_LEFT  = hd_sr.CodedConcept("7771000",  "SCT", "Left")

# ── Modificadores topográficos de sector (SNOMED-CT) ─────────────────────────
_MOD_SUPERIOR = hd_sr.CodedConcept("264217000", "SCT", "Superior")
_MOD_INFERIOR = hd_sr.CodedConcept("261089000", "SCT", "Inferior")
_MOD_NASAL    = hd_sr.CodedConcept("255454004", "SCT", "Nasal")
_MOD_TEMPORAL = hd_sr.CodedConcept("255352004", "SCT", "Temporal")

# ── Tablas de sectores ────────────────────────────────────────────────────────
# (atributo OCTClinicalData, label, código de medición, modificador topográfico)
_ETDRS_SECTORS = [
    ("C",  "Center 1mm fovea",       _CMT_CODE,   None),
    ("S1", "Superior inner 3mm",     _ETDRS_CODE, _MOD_SUPERIOR),
    ("N1", "Nasal inner 3mm",        _ETDRS_CODE, _MOD_NASAL),
    ("I1", "Inferior inner 3mm",     _ETDRS_CODE, _MOD_INFERIOR),
    ("T1", "Temporal inner 3mm",     _ETDRS_CODE, _MOD_TEMPORAL),
    ("S2", "Superior outer 6mm",     _ETDRS_CODE, _MOD_SUPERIOR),
    ("N2", "Nasal outer 6mm",        _ETDRS_CODE, _MOD_NASAL),
    ("I2", "Inferior outer 6mm",     _ETDRS_CODE, _MOD_INFERIOR),
    ("T2", "Temporal outer 6mm",     _ETDRS_CODE, _MOD_TEMPORAL),
]

_RNFL_SECTORS = [
    ("global_avg", "RNFL global average",  _RNFL_CODE,   None),
    ("superior",   "RNFL superior sector", _RNFL_CODE,   _MOD_SUPERIOR),
    ("inferior",   "RNFL inferior sector", _RNFL_CODE,   _MOD_INFERIOR),
    ("nasal",      "RNFL nasal sector",    _RNFL_CODE,   _MOD_NASAL),
    ("temporal",   "RNFL temporal sector", _RNFL_CODE,   _MOD_TEMPORAL),
]

_GCLIPL_SECTORS = [
    ("global_avg", "mGCIPL global average",  _GCLIPL_CODE, None),
    ("superior",   "mGCIPL superior sector", _GCLIPL_CODE, _MOD_SUPERIOR),
    ("inferior",   "mGCIPL inferior sector", _GCLIPL_CODE, _MOD_INFERIOR),
    ("nasal",      "mGCIPL nasal sector",    _GCLIPL_CODE, _MOD_NASAL),
    ("temporal",   "mGCIPL temporal sector", _GCLIPL_CODE, _MOD_TEMPORAL),
]


def _to_dicom_pn(name: str) -> str:
    """Convierte nombre de filename Revo al formato DICOM PN (Apellidos^Nombres).

    Formato Revo: 'JAURRIETA HINOJOS_JESUS NOEL'
    Formato DICOM PN: 'JAURRIETA HINOJOS^JESUS NOEL'
    Si el nombre ya contiene '^' o no contiene '_', se retorna sin cambios.
    """
    if not name:
        return name
    if "^" in name:
        return name
    if "_" in name:
        parts = name.split("_", 1)
        return f"{parts[0].strip()}^{parts[1].strip()}"
    return name


def _make_tracking(label: str) -> hd_sr.TrackingIdentifier:
    return hd_sr.TrackingIdentifier(uid=generate_uid(), identifier=label)


def _lat_code(laterality: str) -> hd_sr.CodedConcept:
    return _LAT_RIGHT if laterality.upper() in ("R", "OD") else _LAT_LEFT


def _finding_site(
    site: hd_sr.CodedConcept,
    laterality: str,
    modifier: Optional[hd_sr.CodedConcept] = None,
) -> hd_sr.FindingSite:
    return hd_sr.FindingSite(
        anatomic_location=site,
        laterality=_lat_code(laterality),
        topographical_modifier=modifier,
    )


def _algo_id(vendor: str = "optopol_revo", source_file: str = "") -> hd_sr.AlgorithmIdentification:
    if vendor == "zeiss_cirrus":
        if source_file.lower().endswith(".pdf"):
            params = ["Cirrus HD-OCT PDF export OCR (pdfplumber/PyMuPDF)"]
        else:
            params = ["Cirrus HD-OCT private tags (0073,xxxx) — EX.DCM"]
    else:
        params = ["Revo FC130 internal segmentation (NFL/BM/GCL/INL/TOP layers)"]
    return hd_sr.AlgorithmIdentification(
        name="Transducin",
        version=_TRANSDUCIN_VERSION,
        parameters=params,
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


def _build_macular_group(
    data: OCTClinicalData,
) -> Optional[hd_sr.MeasurementsAndQualitativeEvaluations]:
    """Grupo TID 1501 para mediciones maculares: CMT, ETDRS 9-sector, mRNFL, mGCIPL."""
    lat = data.laterality or "R"
    measurements: list[hd_sr.Measurement] = []

    # CMT
    if data.cmt_um is not None:
        measurements.append(_measurement(
            _CMT_CODE, data.cmt_um, _UM, "CMT",
            finding_sites=[_finding_site(_SITE_FOVEA, lat)],
        ))

    # ETDRS 9 sectores
    if data.etdrs_grid is not None:
        for attr, label, code, mod in _ETDRS_SECTORS:
            val = getattr(data.etdrs_grid, attr, None)
            if val is not None:
                site = _SITE_FOVEA if mod is None else _SITE_RETINA
                measurements.append(_measurement(
                    code, val, _UM, f"ETDRS-{attr}",
                    finding_sites=[_finding_site(site, lat, mod)],
                ))

    # mRNFL macular (de scan macular, no peripapillar)
    if data.rnfl is not None and data.study_type in ("macular", "rnfl", "oct"):
        for attr, label, code, mod in _RNFL_SECTORS:
            val = getattr(data.rnfl, attr, None)
            if val is not None:
                measurements.append(_measurement(
                    code, val, _UM, f"mRNFL-{label}",
                    finding_sites=[_finding_site(_SITE_RETINA, lat, mod)],
                ))

    # mGCIPL
    if data.gcl_ipl is not None:
        for attr, label, code, mod in _GCLIPL_SECTORS:
            val = getattr(data.gcl_ipl, attr, None)
            if val is not None:
                measurements.append(_measurement(
                    code, val, _UM, f"mGCIPL-{label}",
                    finding_sites=[_finding_site(_SITE_RETINA, lat, mod)],
                ))

    # SQI promedio del cubo (indicador de calidad de adquisición)
    qualitative_evals: list[hd_sr.QualitativeEvaluation] = []
    if data.sqi_mean is not None:
        measurements.append(_measurement(
            _SQI_CODE, round(data.sqi_mean, 4), _RATIO, "SQI-mean",
            finding_sites=[_finding_site(_SITE_EYE, lat)],
        ))
        # Advertencia cualitativa cuando SQI < umbral configurable
        if data.sqi_mean < _SQI_WARN_THRESHOLD:
            qualitative_evals.append(
                hd_sr.QualitativeEvaluation(
                    name=_SQI_QUALITY_NAME,
                    value=_SQI_QUALITY_CODE,
                )
            )

    if not measurements:
        return None

    return hd_sr.MeasurementsAndQualitativeEvaluations(
        tracking_identifier=_make_tracking(
            f"Transducin-macular-{lat}-{data.study_date}"
        ),
        finding_sites=[_finding_site(_SITE_RETINA, lat)],
        algorithm_id=_algo_id(data.vendor, data.source_file),
        measurements=measurements,
        qualitative_evaluations=qualitative_evals if qualitative_evals else None,
    )


def _build_peripapillary_group(
    data: OCTClinicalData,
) -> Optional[hd_sr.MeasurementsAndQualitativeEvaluations]:
    """Grupo TID 1501 para RNFL peripapillar (ring 3.4 mm) y C/D ratio."""
    lat = data.laterality or "R"
    measurements: list[hd_sr.Measurement] = []

    # RNFL peripapillar (scan optic_nerve = Cirrus ONH cube,
    #                     rnfl_circle    = Spectralis / Topcon circle scan 3.4 mm)
    if data.rnfl is not None and data.study_type in ("optic_nerve", "rnfl_circle"):
        for attr, label, code, mod in _RNFL_SECTORS:
            val = getattr(data.rnfl, attr, None)
            if val is not None:
                measurements.append(_measurement(
                    code, val, _UM, f"pRNFL-{label}",
                    finding_sites=[_finding_site(_SITE_OPTIC_HEAD, lat, mod)],
                ))

    # C/D ratio (horizontal)
    if data.cup_disc_ratio is not None:
        measurements.append(_measurement(
            _CD_CODE, data.cup_disc_ratio, _RATIO, "C/D ratio",
            finding_sites=[_finding_site(_SITE_OPTIC_HEAD, lat)],
        ))
    # Vertical C/D ratio (VCDR) — SNOMED-CT 363930007
    if data.vcdr is not None:
        measurements.append(_measurement(
            _VCDR_CODE, data.vcdr, _RATIO, "Vertical C/D ratio",
            finding_sites=[_finding_site(_SITE_OPTIC_HEAD, lat)],
        ))
    # Disc area — código provisional 99OFTALMOS (pending Supp247 / highdicom #406)
    if data.disc_area_mm2 is not None:
        measurements.append(_measurement(
            _DISC_AREA_CODE, data.disc_area_mm2, _MM2, "Optic disc area",
            finding_sites=[_finding_site(_SITE_OPTIC_HEAD, lat)],
        ))
    # Rim area — código provisional 99OFTALMOS
    if data.rim_area_mm2 is not None:
        measurements.append(_measurement(
            _RIM_AREA_CODE, data.rim_area_mm2, _MM2, "Neuroretinal rim area",
            finding_sites=[_finding_site(_SITE_OPTIC_HEAD, lat)],
        ))
    # Cup volume — código provisional 99OFTALMOS
    if data.cup_vol_mm3 is not None:
        measurements.append(_measurement(
            _CUP_VOL_CODE, data.cup_vol_mm3, _MM3, "Optic cup volume",
            finding_sites=[_finding_site(_SITE_OPTIC_HEAD, lat)],
        ))

    if not measurements:
        return None

    return hd_sr.MeasurementsAndQualitativeEvaluations(
        tracking_identifier=_make_tracking(
            f"Transducin-peripapillary-{lat}-{data.study_date}"
        ),
        finding_sites=[_finding_site(_SITE_OPTIC_HEAD, lat)],
        algorithm_id=_algo_id(data.vendor, data.source_file),
        measurements=measurements,
    )


def _build_biometry_group(
    data: OCTClinicalData,
) -> Optional[hd_sr.MeasurementsAndQualitativeEvaluations]:
    """Grupo TID 1501 para biometría: AL, CCT, K1, K2."""
    lat = data.laterality or "R"
    measurements: list[hd_sr.Measurement] = []

    if data.axial_length_mm is not None:
        measurements.append(_measurement(
            _AL_CODE, data.axial_length_mm, _MM, "Axial length",
            finding_sites=[_finding_site(_SITE_EYE, lat)],
        ))
    if data.cct_um is not None:
        measurements.append(_measurement(
            _CCT_CODE, data.cct_um / 1000.0, _MM, "CCT",
            finding_sites=[_finding_site(_SITE_EYE, lat)],
        ))
    if data.k1_mm is not None:
        measurements.append(_measurement(
            _K1_CODE, data.k1_mm, _MM, "K1 flat radius",
            finding_sites=[_finding_site(_SITE_EYE, lat)],
        ))
    if data.k2_mm is not None:
        measurements.append(_measurement(
            _K2_CODE, data.k2_mm, _MM, "K2 steep radius",
            finding_sites=[_finding_site(_SITE_EYE, lat)],
        ))

    if not measurements:
        return None

    return hd_sr.MeasurementsAndQualitativeEvaluations(
        tracking_identifier=_make_tracking(
            f"Transducin-biometry-{lat}-{data.study_date}"
        ),
        finding_sites=[_finding_site(_SITE_EYE, lat)],
        measurements=measurements,
    )


def build_sr(
    data: OCTClinicalData,
    reference_dataset: Optional[Dataset] = None,
    output_path: Optional[Path] = None,
    study_instance_uid: Optional[str] = None,
) -> Dataset:
    """Construye un DICOM SR TID 1500 desde OCTClinicalData.

    Genera hasta tres grupos TID 1501 según el tipo de estudio:
      - Grupo macular:       CMT + ETDRS 9s + mRNFL + mGCIPL
                             FindingSite: Retina / FoveaCentralis + lateralidad
      - Grupo peripapillar:  RNFL ring 3.4 mm (S/I/N/T) + C/D ratio
                             FindingSite: OpticNerveHead + lateralidad
      - Grupo biometría:     AL + CCT + K1 + K2
                             FindingSite: Eye + lateralidad

    Cada grupo incluye AlgorithmIdentification("Transducin", version) y
    modificadores topográficos S/I/N/T por sector (SNOMED-CT).

    Args:
        data:               Mediciones clínicas extraídas del .opt.
        reference_dataset:  Dataset DICOM de imagen OCT de referencia (opcional).
        output_path:        Si se provee, guarda el SR en disco.
        study_instance_uid: StudyInstanceUID a usar (p.ej. obtenido vía
                            resolve_study_uid para colocar el SR en el mismo
                            estudio que las imágenes OCT del paciente). Si None,
                            se usa el UID del reference_dataset o uno nuevo.

    Returns:
        Dataset pydicom del SR (ComprehensiveSR TID 1500).
    """
    if not data.noel_id:
        raise ValueError("OCTClinicalData.noel_id es requerido para generar SR.")
    if not is_valid_noel(data.noel_id):
        logger.warning("noel_id '%s' no cumple formato NOEL — SR generado de todas formas.", data.noel_id)

    ref_ds = reference_dataset if reference_dataset is not None else _minimal_reference(data)

    # Inyectar StudyInstanceUID resuelto — esto hace que el SR quede en el mismo
    # estudio DICOM que las imágenes OCT ya presentes en Orthanc/PACS.
    if study_instance_uid:
        ref_ds.StudyInstanceUID = study_instance_uid

    # ── Vendor-specific manufacturer/model ─────────────────────────────────
    if data.vendor == "zeiss_cirrus":
        _manufacturer = MANUFACTURER_ZEISS
        _model        = MODEL_CIRRUS
    else:
        _manufacturer = MANUFACTURER_OPTOPOL
        _model        = MODEL_REVO

    # ── Observation context ────────────────────────────────────────────────
    # Device Observer only (TID 1500 §C.17.3 — software analysis is Device,
    # not Person). Person observer would be a reviewing physician; we omit
    # until/unless a human signs the report.
    _device_uid = data.device_serial if data.device_serial else str(generate_uid())

    obs_ctx = hd_sr.ObservationContext(
        observer_device_context=hd_sr.ObserverContext(
            observer_type=dcm_codes.cid270.Device,
            observer_identifying_attributes=hd_sr.DeviceObserverIdentifyingAttributes(
                uid=_device_uid,
                name=f"{_manufacturer} {_model}",
            ),
        ),
    )

    # ── Construir grupos según tipo de estudio ─────────────────────────────
    groups: list[hd_sr.MeasurementsAndQualitativeEvaluations] = []

    macular_group = _build_macular_group(data)
    if macular_group is not None:
        groups.append(macular_group)

    peripap_group = _build_peripapillary_group(data)
    if peripap_group is not None:
        groups.append(peripap_group)

    biometry_group = _build_biometry_group(data)
    if biometry_group is not None:
        groups.append(biometry_group)

    if not groups:
        # SR vacío válido (p.ej. fundus/angio sin métricas)
        logger.warning("OCTClinicalData sin mediciones — SR generado sin grupos de medición.")
        groups.append(hd_sr.MeasurementsAndQualitativeEvaluations(
            tracking_identifier=_make_tracking(
                f"Transducin-empty-{data.study_type}-{data.laterality}"
            ),
        ))

    # ── MeasurementReport TID 1500 ─────────────────────────────────────────
    report = hd_sr.MeasurementReport(
        observation_context=obs_ctx,
        procedure_reported=_OCT_PROC,
        imaging_measurements=groups,
    )

    # ── ComprehensiveSR ────────────────────────────────────────────────────
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
        series_number=900,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer=_manufacturer,
        is_final=True,
        coding_schemes=[_private_cs],
    )

    sr.ManufacturerModelName = _model
    sr.DeviceSerialNumber    = data.device_serial or ""
    sr.PatientID             = data.noel_id
    sr.PatientName           = _to_dicom_pn(data.patient_name) or data.noel_id
    sr.PatientBirthDate      = data.patient_dob or dob_from_noel(data.noel_id)
    sr.StudyDate             = data.study_date or now.strftime("%Y%m%d")
    sr.StudyDescription      = study_description_label(data.study_type, data.laterality)
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
        logger.info("SR guardado: %s (PatientID=%s, groups=%d)", output_path, data.noel_id, len(groups))

    return sr


def _minimal_reference(data: OCTClinicalData) -> Dataset:
    """Crea un Dataset mínimo de referencia cuando no hay imagen OCT disponible."""
    from pydicom.dataset import FileMetaDataset

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = SOP_OPT
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = RETINAOS_TRANSFER_SYNTAX

    ds = Dataset()
    ds.file_meta = file_meta
    ds.ensure_file_meta()

    ds.SOPClassUID       = SOP_OPT
    ds.SOPInstanceUID    = generate_uid()
    ds.StudyInstanceUID  = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientID         = data.noel_id
    ds.PatientName       = _to_dicom_pn(data.patient_name) or data.noel_id
    ds.PatientBirthDate  = data.patient_dob or dob_from_noel(data.noel_id)
    ds.PatientSex        = ""
    ds.StudyDate         = data.study_date or datetime.now().strftime("%Y%m%d")
    ds.StudyTime         = data.study_time or ""
    ds.AccessionNumber   = ""
    ds.StudyID           = ""
    ds.StudyDescription  = study_description_label(data.study_type, data.laterality)
    ds.ReferringPhysicianName = ""
    ds.Modality          = "OPT"
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


# ─────────────────────────────────────────────────────────────────────────────
# TESTS  —  python transducin/sr_builder.py
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import tempfile

    logging.basicConfig(level=logging.INFO)
    G, R, E = "\033[92m", "\033[91m", "\033[0m"
    errors = 0

    def check(label, condition, detail=""):
        global errors
        ok = bool(condition)
        print(f"  {'✓' if ok else '✗'} [{G+'PASS'+E if ok else R+'FAIL'+E}] {label}" + (f": {detail}" if detail else ""))
        if not ok:
            errors += 1

    print("\n══ sr_builder — TID 1500 + anatomic context (Fase 2) ══")

    # Datos de prueba con Siltspook — scan macular OS
    cd_mac = OCTClinicalData(
        noel_id      = "GHSI20000101",
        vendor       = "optopol_revo",
        source_file  = "GHOST_SILTSPOOK_20000101_120000_OS_OCT.opt",
        study_date   = "20000101",
        study_time   = "120000",
        laterality   = "L",
        study_type   = "macular",
        patient_name = "SILTSPOOK^GHOST",
        patient_dob  = "20000101",
        cmt_um       = 245.5,
        etdrs_grid   = ETDRSGrid(C=245.5, S1=310.0, N1=295.0, I1=305.0, T1=288.0),
        rnfl         = RNFLSectors(global_avg=95.3, superior=112.0, inferior=118.0),
        gcl_ipl      = RNFLSectors(global_avg=78.0, superior=82.0, inferior=76.0),
        extraction_confidence = "assumed",
    )

    # Scan optic nerve OD — solo RNFL peripapillar
    cd_onh = OCTClinicalData(
        noel_id    = "GHSI20000101",
        vendor     = "optopol_revo",
        source_file = "GHOST_SILTSPOOK_20000101_120100_OD_OCT.opt",
        study_date = "20000101",
        laterality = "R",
        study_type = "optic_nerve",
        patient_name = "SILTSPOOK^GHOST",
        rnfl       = RNFLSectors(global_avg=107.0, superior=135.0,
                                  inferior=128.0, nasal=75.0, temporal=88.0),
        cup_disc_ratio = 0.35,
        vcdr           = 0.28,
        disc_area_mm2  = 1.872,
        rim_area_mm2   = 1.412,
        cup_vol_mm3    = 0.063,
        extraction_confidence = "confirmed",
    )

    # Circle scan pRNFL — Spectralis / Topcon
    cd_circle = OCTClinicalData(
        noel_id    = "GHSI20000101",
        vendor     = "heidelberg_spectralis",
        source_file = "GHOST_SILTSPOOK_20000101_090000_OD_E2E.e2e",
        study_date = "20000101",
        laterality = "R",
        study_type = "rnfl_circle",
        patient_name = "SILTSPOOK^GHOST",
        rnfl       = RNFLSectors(global_avg=98.0, superior=122.0,
                                  inferior=126.0, nasal=74.0, temporal=69.0),
        extraction_confidence = "confirmed",
    )

    # Biometría
    cd_bio = OCTClinicalData(
        noel_id    = "GHSI20000101",
        vendor     = "optopol_revo",
        source_file = "GHOST_SILTSPOOK_20000101_130000_OD_BMETR.opt",
        study_date = "20000101",
        laterality = "R",
        study_type = "biometry",
        patient_name = "SILTSPOOK^GHOST",
        axial_length_mm = 23.45,
        cct_um          = 545.0,
        k1_mm           = 7.82,
        k2_mm           = 7.61,
        extraction_confidence = "confirmed",
    )

    def find_values(ds, search_code, found=None):
        """Busca recursivamente valores numéricos por código en ContentSequence."""
        if found is None:
            found = []
        for item in getattr(ds, "ContentSequence", []):
            code_seq = getattr(item, "ConceptNameCodeSequence", [])
            if code_seq and hasattr(code_seq[0], "CodeValue"):
                if code_seq[0].CodeValue == search_code:
                    mv = getattr(item, "MeasuredValueSequence", [])
                    if mv:
                        found.append(float(getattr(mv[0], "NumericValue", 0)))
            find_values(item, search_code, found)
        return found

    def count_finding_sites(ds, site_code, count=None):
        """Cuenta FindingSite items con código dado."""
        if count is None:
            count = [0]
        for item in getattr(ds, "ContentSequence", []):
            rel = getattr(item, "RelationshipType", "")
            cs  = getattr(item, "ConceptNameCodeSequence", [])
            if rel == "HAS CONCEPT MOD" and cs and getattr(cs[0], "CodeValue", "") == "363698007":
                # FindingSite — check child for matching anatomy code
                for child in getattr(item, "ContentSequence", []):
                    ccs = getattr(child, "ConceptCodeSequence", [])
                    if ccs and getattr(ccs[0], "CodeValue", "") == site_code:
                        count[0] += 1
            count_finding_sites(item, site_code, count)
        return count[0]

    with tempfile.TemporaryDirectory() as tmp:
        # ── Test 1: scan macular ──────────────────────────────────────────
        out1 = Path(tmp) / "test_macular_sr.dcm"
        sr1 = build_sr(cd_mac, output_path=out1)

        check("SOPClassUID ComprehensiveSR",
              str(sr1.SOPClassUID) in {
                  "1.2.840.10008.5.1.4.1.1.88.11",
                  "1.2.840.10008.5.1.4.1.1.88.22",
                  "1.2.840.10008.5.1.4.1.1.88.33",
                  "1.2.840.10008.5.1.4.1.1.88.34",
              }, str(sr1.SOPClassUID))
        check("PatientID NOEL",       sr1.PatientID == "GHSI20000101")
        check("StudyDate correcto",   sr1.StudyDate == "20000101")
        check("Archivo guardado",     out1.exists())

        sr1r = pydicom.dcmread(str(out1))
        check("Reabrir sin error",    True)
        check("PatientID en disco",   sr1r.PatientID == "GHSI20000101")
        check("ContentSequence OK",   hasattr(sr1r, "ContentSequence") and len(sr1r.ContentSequence) > 0)

        cmt_vals = find_values(sr1r, "422453003")
        check("CMT 245.5 µm en SR",   245.5 in cmt_vals, f"encontrados: {cmt_vals}")

        etdrs_vals = find_values(sr1r, "422399008")
        check("ETDRS sectores presentes", len(etdrs_vals) >= 4, f"n={len(etdrs_vals)}")

        rnfl_vals = find_values(sr1r, "422995006")
        check("mRNFL global 95.3",    95.3 in rnfl_vals, f"encontrados: {rnfl_vals}")

        gclipl_vals = find_values(sr1r, "422455005")
        check("mGCIPL global 78.0",   78.0 in gclipl_vals, f"encontrados: {gclipl_vals}")

        # ── Test 2: optic nerve (peripapillar RNFL) ───────────────────────
        out2 = Path(tmp) / "test_onh_sr.dcm"
        sr2 = build_sr(cd_onh, output_path=out2)
        sr2r = pydicom.dcmread(str(out2))

        prnfl_vals = find_values(sr2r, "422995006")
        check("pRNFL global 107.0",   107.0 in prnfl_vals, f"encontrados: {prnfl_vals}")
        check("pRNFL sectores S/I/N/T", len(prnfl_vals) >= 4, f"n={len(prnfl_vals)}")

        cd_vals = find_values(sr2r, "363932005")
        check("C/D ratio 0.35",       0.35 in cd_vals, f"encontrados: {cd_vals}")

        vcdr_vals = find_values(sr2r, "363930007")
        check("VCDR 0.28",            0.28 in vcdr_vals, f"encontrados: {vcdr_vals}")

        disc_vals = find_values(sr2r, "DISCAREA")
        check("Disc area 1.872 mm²",  1.872 in disc_vals, f"encontrados: {disc_vals}")

        rim_vals = find_values(sr2r, "RIMAREA")
        check("Rim area 1.412 mm²",   1.412 in rim_vals, f"encontrados: {rim_vals}")

        cup_vals = find_values(sr2r, "CUPVOL")
        check("Cup vol 0.063 mm³",    0.063 in cup_vals, f"encontrados: {cup_vals}")

        # ── Test 3: circle scan pRNFL (Spectralis / Topcon) ──────────────
        out3c = Path(tmp) / "test_circle_sr.dcm"
        sr3c  = build_sr(cd_circle, output_path=out3c)
        sr3cr = pydicom.dcmread(str(out3c))

        circle_vals = find_values(sr3cr, "422995006")
        check("circle pRNFL global 98.0",    98.0 in circle_vals, f"encontrados: {circle_vals}")
        check("circle pRNFL sectores S/I/N/T", len(circle_vals) >= 4, f"n={len(circle_vals)}")

        # ── Test 4: biometría ─────────────────────────────────────────────
        out3 = Path(tmp) / "test_bio_sr.dcm"
        sr3 = build_sr(cd_bio, output_path=out3)
        sr3r = pydicom.dcmread(str(out3))

        al_vals = find_values(sr3r, "252017007")
        check("AL 23.45 mm en SR",    any(abs(v - 23.45) < 0.01 for v in al_vals), f"encontrados: {al_vals}")

        cct_vals2 = find_values(sr3r, "397545004")
        check("CCT 0.545 mm en SR",   any(abs(v - 0.545) < 0.001 for v in cct_vals2), f"encontrados: {cct_vals2}")

        # ── Test 5: SR vacío (fundus sin mediciones) ──────────────────────
        cd_empty = OCTClinicalData(
            noel_id="GHSI20000101", laterality="R", study_type="fundus",
            source_file="test.opt"
        )
        sr4 = build_sr(cd_empty)
        check("SR vacío sin excepción", str(sr4.SOPClassUID).startswith("1.2.840"))

    print(f"\n{'══ TODOS LOS TESTS PASARON ══' if errors==0 else f'══ {errors} FALLARON ══'}\n")
    sys.exit(0 if errors == 0 else 1)
