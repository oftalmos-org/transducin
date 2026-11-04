# transducin/clinical_data.py
# SPDX-License-Identifier: Apache-2.0
#
# Dataclass OCTClinicalData — contenedor de mediciones clínicas extraídas de
# archivos OCT propietarios. Es el contrato entre opt_extractor.py y sr_builder.py.
#
# extraction_confidence documenta la certeza de cada medición:
#   "confirmed" — extraído y validado del binario
#   "assumed"   — inferido sin validación completa (e.g., desde filename)
#   "unknown"   — no encontrado

from __future__ import annotations

from dataclasses import dataclass, field

# Mapa canónico study_type → etiqueta clínica (English, DICOM/highdicom conventions)
_STUDY_TYPE_LABEL: dict[str, str] = {
    "macular": "OCT Macular",
    "rnfl": "OCT RNFL",
    "optic_nerve": "OCT Optic Nerve",
    "angio": "OCTA",
    "hd_line": "OCT HD Line",
    "wide_field": "OCT Wide Field",
    "ultra_wide": "OCT Ultra-wide Field",
    "fundus": "Fundus Photography",
    "biometry": "Biometry",
    "anterior": "OCT Anterior Segment",
    "ganglion_cell": "OCT Ganglion Cell",
    "visual_field": "Visual Field",
    "unknown": "OCT",
}
_LAT_LABEL: dict[str, str] = {"R": "OD", "L": "OS"}


def study_description_label(study_type: str, laterality: str) -> str:
    """Genera StudyDescription clínica legible para DICOM.

    Ejemplos:
        study_description_label("macular", "R")      → "OCT Macular OD"
        study_description_label("optic_nerve", "L")  → "OCT Nervio Óptico OS"
        study_description_label("angio", "R")        → "OCTA OD"
        study_description_label("biometry", "")      → "Biometría"
    """
    label = _STUDY_TYPE_LABEL.get(study_type or "unknown", "OCT")
    lat = _LAT_LABEL.get((laterality or "").upper(), "")
    return f"{label} {lat}".strip() if lat else label


@dataclass
class ETDRSGrid:
    """Grosor macular ETDRS 9 sectores (μm).

    Sectores: C=centro, S=superior, N=nasal, I=inferior, T=temporal
    Sufijos:  1=anillo interno (1-3mm), 2=anillo externo (3-6mm)
    """

    C: float | None = None  # Fovea central (1mm)
    S1: float | None = None
    N1: float | None = None
    I1: float | None = None
    T1: float | None = None
    S2: float | None = None
    N2: float | None = None
    I2: float | None = None
    T2: float | None = None

    def has_data(self) -> bool:
        return any(v is not None for v in [self.C, self.S1, self.N1, self.I1, self.T1])


@dataclass
class RNFLSectors:
    """Grosor de capa de fibras nerviosas retinianas por sector (μm)."""

    global_avg: float | None = None
    superior: float | None = None
    inferior: float | None = None
    nasal: float | None = None
    temporal: float | None = None

    def has_data(self) -> bool:
        return self.global_avg is not None


@dataclass
class OCTClinicalData:
    """Datos clínicos estructurados extraídos de un archivo OCT.

    Es el objeto central que fluye entre extractor → sr_builder → watcher.
    """

    # Identificación
    noel_id: str = ""  # PatientID en formato NOEL (JAHJ19870831)
    vendor: str = "optopol_revo"
    source_file: str = ""  # Path al .opt original (solo referencia)

    # Estudio
    study_date: str = ""  # YYYYMMDD
    study_time: str = ""  # HHMMSS
    laterality: str = ""  # "L" | "R"
    study_type: str = ""  # "macular" | "rnfl" | "optic_nerve" | "anterior" |
    # "angio" | "hd_line" | "wide_field" | "ultra_wide" |
    # "fundus" | "biometry" | "unknown"

    # Datos paciente (pueden ser "" si no disponibles)
    patient_name: str = ""  # "APELLIDO APELLIDO_NOMBRE NOMBRE" del filename
    patient_dob: str = ""  # YYYYMMDD si disponible

    # Macular
    cmt_um: float | None = None  # Central Macular Thickness en μm
    etdrs_grid: ETDRSGrid | None = None

    # RNFL macular (mRNFL) — capa NFL en sectores maculares
    rnfl: RNFLSectors | None = None
    # mGCIPL — GCL+IPL, marcador ganglionar para glaucoma
    gcl_ipl: RNFLSectors | None = None
    gcl_avg_um: float | None = None  # mGCIPL global_avg (escalar conveniente)
    cup_disc_ratio: float | None = None
    vcdr: float | None = None  # Vertical Cup/Disc Ratio (Cirrus ONH XML)
    disc_area_mm2: float | None = None  # Área del disco óptico (mm²)
    rim_area_mm2: float | None = None  # Área del anillo neuroretiniano (mm²)
    cup_vol_mm3: float | None = None  # Volumen de la copa (mm³)
    sqi_mean: float | None = None  # SQI promedio del cubo (0–1); None si no disponible

    # Biometría (desde MYOPI JSON en archivos BMETR)
    axial_length_mm: float | None = None  # Longitud axial (mm)
    cct_um: float | None = None  # Grosor corneal central (µm)
    k1_mm: float | None = None  # Queratometría K1 (mm)
    k2_mm: float | None = None  # Queratometría K2 (mm)

    # Equipo de adquisición
    device_serial: str = ""  # Número de serie del equipo (si disponible)

    # Confianza y trazabilidad
    extraction_confidence: str = "unknown"  # "confirmed" | "assumed" | "unknown"
    confidence_notes: list[str] = field(default_factory=list)
    # Razón diagnóstica cuando cmt_um es None en un estudio macular
    # "bottom_zero"   — capa BOTTOM presente pero en ceros (drusen, fibrosis sub-RPE)
    # "sqi_low"       — SQI por debajo del umbral mínimo (imagen de baja calidad)
    # "dims_unknown"  — dimensiones del cubo no reconocidas
    # "layer_missing" — chunk BM y BOTTOM ausentes del archivo
    # "not_macular"   — study_type != macular (CMT no aplica)
    cmt_failure_reason: str | None = None

    # Procedencia del valor medido (independiente de extraction_confidence):
    #   "vendor_reported" | "vendor_contour_derived" | "independently_computed" | "manually_entered"
    # "" = no declarada (adapters anteriores). Un espesor calculado por Transducin a partir
    # de contornos del fabricante es "vendor_contour_derived", NO una medida del fabricante.
    measurement_provenance: str = ""

    def add_note(self, note: str) -> None:
        self.confidence_notes.append(note)

    def has_measurements(self) -> bool:
        """True si hay al menos una medición clínica."""
        return any(
            [
                self.cmt_um is not None,
                self.etdrs_grid is not None and self.etdrs_grid.has_data(),
                self.rnfl is not None and self.rnfl.has_data(),
                self.gcl_ipl is not None and self.gcl_ipl.has_data(),
                self.cup_disc_ratio is not None,
                self.vcdr is not None,
                self.disc_area_mm2 is not None,
                self.axial_length_mm is not None,
                self.cct_um is not None,
            ]
        )


@dataclass
class VisualFieldPoint:
    """Sensibilidad umbral en un punto del campo visual (dB)."""

    x_deg: float  # posición horizontal en grados (+ = temporal)
    y_deg: float  # posición vertical en grados (+ = superior)
    threshold_db: float | None = None  # sensibilidad umbral (dB)
    total_dev_db: float | None = None  # Total Deviation vs normativa
    pattern_dev_db: float | None = None  # Pattern Deviation vs normativa


@dataclass
class VisualFieldData:
    """Resultados de un examen de campo visual estático (PTS 925 / Humphrey).

    Pensado para mapear a DICOM 1.2.840.10008.5.1.4.1.1.80
    y al SR TID 6002 (Supplement 247 Visual Field Key Measurements).
    """

    # Identificación del estudio
    noel_id: str = ""
    laterality: str = ""  # "L" | "R"
    study_date: str = ""  # YYYYMMDD
    patient_name: str = ""
    patient_dob: str = ""

    # Parámetros del test
    test_pattern: str = ""  # "24-2" | "30-2" | "10-2" | "Macula"
    strategy: str = ""  # "SITA Standard" | "SITA Fast" | "Full Threshold"
    fixation_target: str = ""  # "Central" | "Blind spot"
    stimulus_size: str = ""  # "III" | "V"

    # Índices globales (los más clínicamente relevantes)
    md_db: float | None = None  # Mean Deviation (dB) — negativo = peor
    psd_db: float | None = None  # Pattern Standard Deviation (dB)
    vfi_pct: float | None = None  # Visual Field Index (%)
    ght: str | None = None  # Glaucoma Hemifield Test ("Within Normal Limits", etc.)

    # Puntos individuales (54 para 24-2, 76 para 30-2)
    points: list[VisualFieldPoint] = field(default_factory=list)

    # Calidad
    fixation_losses: float | None = None  # fracción (0.0–1.0)
    false_pos_pct: float | None = None
    false_neg_pct: float | None = None
    foveal_threshold_db: float | None = None

    # Metadatos
    device_serial: str = ""
    software_version: str = ""
    source_file: str = ""
    extraction_confidence: str = "unknown"  # "confirmed" | "assumed" | "unknown"
    notes: list[str] = field(default_factory=list)

    def has_data(self) -> bool:
        return self.md_db is not None or len(self.points) > 0
