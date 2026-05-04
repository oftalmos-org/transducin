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
from typing import Optional

# Mapa canónico study_type → etiqueta clínica (English, DICOM/highdicom conventions)
_STUDY_TYPE_LABEL: dict[str, str] = {
    "macular":       "OCT Macular",
    "rnfl":          "OCT RNFL",
    "optic_nerve":   "OCT Optic Nerve",
    "angio":         "OCTA",
    "hd_line":       "OCT HD Line",
    "wide_field":    "OCT Wide Field",
    "ultra_wide":    "OCT Ultra-wide Field",
    "fundus":        "Fundus Photography",
    "biometry":      "Biometry",
    "anterior":      "OCT Anterior Segment",
    "ganglion_cell": "OCT Ganglion Cell",
    "visual_field":  "Visual Field",
    "unknown":       "OCT",
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
    lat   = _LAT_LABEL.get((laterality or "").upper(), "")
    return f"{label} {lat}".strip() if lat else label


@dataclass
class ETDRSGrid:
    """Grosor macular ETDRS 9 sectores (μm).

    Sectores: C=centro, S=superior, N=nasal, I=inferior, T=temporal
    Sufijos:  1=anillo interno (1-3mm), 2=anillo externo (3-6mm)
    """
    C:  Optional[float] = None  # Fovea central (1mm)
    S1: Optional[float] = None
    N1: Optional[float] = None
    I1: Optional[float] = None
    T1: Optional[float] = None
    S2: Optional[float] = None
    N2: Optional[float] = None
    I2: Optional[float] = None
    T2: Optional[float] = None

    def has_data(self) -> bool:
        return any(v is not None for v in [self.C, self.S1, self.N1, self.I1, self.T1])


@dataclass
class RNFLSectors:
    """Grosor de capa de fibras nerviosas retinianas por sector (μm)."""
    global_avg: Optional[float] = None
    superior:   Optional[float] = None
    inferior:   Optional[float] = None
    nasal:      Optional[float] = None
    temporal:   Optional[float] = None

    def has_data(self) -> bool:
        return self.global_avg is not None


@dataclass
class OCTClinicalData:
    """Datos clínicos estructurados extraídos de un archivo OCT.

    Es el objeto central que fluye entre extractor → sr_builder → watcher.
    """
    # Identificación
    noel_id:    str = ""          # PatientID en formato NOEL (JAHJ19870831)
    vendor:     str = "optopol_revo"
    source_file: str = ""         # Path al .opt original (solo referencia)

    # Estudio
    study_date:  str = ""         # YYYYMMDD
    study_time:  str = ""         # HHMMSS
    laterality:  str = ""         # "L" | "R"
    study_type:  str = ""         # "macular" | "rnfl" | "optic_nerve" | "anterior" |
                                   # "angio" | "hd_line" | "wide_field" | "ultra_wide" |
                                   # "fundus" | "biometry" | "unknown"

    # Datos paciente (pueden ser "" si no disponibles)
    patient_name: str = ""        # "APELLIDO APELLIDO_NOMBRE NOMBRE" del filename
    patient_dob:  str = ""        # YYYYMMDD si disponible

    # Macular
    cmt_um:     Optional[float] = None   # Central Macular Thickness en μm
    etdrs_grid: Optional[ETDRSGrid] = None

    # RNFL macular (mRNFL) — capa NFL en sectores maculares
    rnfl: Optional[RNFLSectors] = None
    # mGCIPL — GCL+IPL, marcador ganglionar para glaucoma
    gcl_ipl: Optional[RNFLSectors] = None
    gcl_avg_um: Optional[float] = None  # mGCIPL global_avg (escalar conveniente)
    cup_disc_ratio: Optional[float] = None
    vcdr:           Optional[float] = None   # Vertical Cup/Disc Ratio (Cirrus ONH XML)
    disc_area_mm2:  Optional[float] = None   # Área del disco óptico (mm²)
    rim_area_mm2:   Optional[float] = None   # Área del anillo neuroretiniano (mm²)
    cup_vol_mm3:    Optional[float] = None   # Volumen de la copa (mm³)
    sqi_mean: Optional[float] = None    # SQI promedio del cubo (0–1); None si no disponible

    # Biometría (desde MYOPI JSON en archivos BMETR)
    axial_length_mm: Optional[float] = None   # Longitud axial (mm)
    cct_um: Optional[float] = None            # Grosor corneal central (µm)
    k1_mm: Optional[float] = None             # Queratometría K1 (mm)
    k2_mm: Optional[float] = None             # Queratometría K2 (mm)

    # Equipo de adquisición
    device_serial: str = ""        # Número de serie del equipo (si disponible)

    # Confianza y trazabilidad
    extraction_confidence: str = "unknown"   # "confirmed" | "assumed" | "unknown"
    confidence_notes: list[str] = field(default_factory=list)

    def add_note(self, note: str) -> None:
        self.confidence_notes.append(note)

    def has_measurements(self) -> bool:
        """True si hay al menos una medición clínica."""
        return any([
            self.cmt_um is not None,
            self.etdrs_grid is not None and self.etdrs_grid.has_data(),
            self.rnfl is not None and self.rnfl.has_data(),
            self.gcl_ipl is not None and self.gcl_ipl.has_data(),
            self.cup_disc_ratio is not None,
            self.vcdr is not None,
            self.disc_area_mm2 is not None,
            self.axial_length_mm is not None,
            self.cct_um is not None,
        ])


@dataclass
class VisualFieldPoint:
    """Sensibilidad umbral en un punto del campo visual (dB)."""
    x_deg: float           # posición horizontal en grados (+ = temporal)
    y_deg: float           # posición vertical en grados (+ = superior)
    threshold_db: Optional[float] = None   # sensibilidad umbral (dB)
    total_dev_db: Optional[float] = None   # Total Deviation vs normativa
    pattern_dev_db: Optional[float] = None # Pattern Deviation vs normativa


@dataclass
class VisualFieldData:
    """Resultados de un examen de campo visual estático (PTS 925 / Humphrey).

    Pensado para mapear a DICOM 1.2.840.10008.5.1.4.1.1.80
    y al SR TID 6002 (Supplement 247 Visual Field Key Measurements).
    """
    # Identificación del estudio
    noel_id:      str = ""
    laterality:   str = ""    # "L" | "R"
    study_date:   str = ""    # YYYYMMDD
    patient_name: str = ""
    patient_dob:  str = ""

    # Parámetros del test
    test_pattern: str = ""    # "24-2" | "30-2" | "10-2" | "Macula"
    strategy:     str = ""    # "SITA Standard" | "SITA Fast" | "Full Threshold"
    fixation_target: str = "" # "Central" | "Blind spot"
    stimulus_size: str = ""   # "III" | "V"

    # Índices globales (los más clínicamente relevantes)
    md_db:  Optional[float] = None   # Mean Deviation (dB) — negativo = peor
    psd_db: Optional[float] = None   # Pattern Standard Deviation (dB)
    vfi_pct: Optional[float] = None  # Visual Field Index (%)
    ght:    Optional[str]   = None   # Glaucoma Hemifield Test ("Within Normal Limits", etc.)

    # Puntos individuales (54 para 24-2, 76 para 30-2)
    points: list[VisualFieldPoint] = field(default_factory=list)

    # Calidad
    fixation_losses: Optional[float] = None   # fracción (0.0–1.0)
    false_pos_pct:   Optional[float] = None
    false_neg_pct:   Optional[float] = None
    foveal_threshold_db: Optional[float] = None

    # Metadatos
    device_serial:   str = ""
    software_version: str = ""
    source_file:     str = ""
    extraction_confidence: str = "unknown"  # "confirmed" | "assumed" | "unknown"
    notes: list[str] = field(default_factory=list)

    def has_data(self) -> bool:
        return self.md_db is not None or len(self.points) > 0
