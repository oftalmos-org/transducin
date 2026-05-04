# transducin/dicom_config.py
# SPDX-License-Identifier: Apache-2.0
#
# Configuración global DICOM para RetinaOS/Transducin.
#
# Centraliza Transfer Syntax, SOP Classes y cadenas de equipo para que
# sr_builder.py y revo_opt_reader.py no tengan literales dispersos.
# Importar solo desde aquí; no duplicar estos valores en otros módulos.

from pydicom.uid import (
    AutorefractionMeasurementsStorage,
    ComprehensiveSRStorage,
    ExplicitVRLittleEndian,
    LensometryMeasurementsStorage,
    OphthalmicAxialMeasurementsStorage,
    OphthalmicPhotography8BitImageStorage,
    OphthalmicTomographyImageStorage,
    OphthalmicVisualFieldStaticPerimetryMeasurementsStorage,
)

# ── Transfer Syntax ──────────────────────────────────────────────────────────

RETINAOS_TRANSFER_SYNTAX = ExplicitVRLittleEndian  # 1.2.840.10008.1.2.1

# ── SOP Classes usados o previstos en el pipeline RetinaOS ──────────────────

# OCT volumétrico multiframe (Revo FC130, Bioptigen, etc.)
SOP_OPT = OphthalmicTomographyImageStorage          # 1.2.840.10008.5.1.4.1.1.77.1.5.4

# Structured Report (highdicom ComprehensiveSR)
SOP_SR_COMPREHENSIVE = ComprehensiveSRStorage        # 1.2.840.10008.5.1.4.1.1.88.33

# Fotografía de fondo (fundus color)
SOP_FUNDUS_PHOTO = OphthalmicPhotography8BitImageStorage  # 1.2.840.10008.5.1.4.1.1.77.1.5.1

# Biometría óptica (longitud axial, queratometría)
SOP_AXIAL_MEASUREMENTS = OphthalmicAxialMeasurementsStorage  # 1.2.840.10008.5.1.4.1.1.78.7

# Perimetría estática (campo visual — Humphrey, Octopus)
SOP_PERIMETRY = OphthalmicVisualFieldStaticPerimetryMeasurementsStorage  # 1.2.840.10008.5.1.4.1.1.80.1

# Autorrefractómetro
SOP_AUTOREFRACTION = AutorefractionMeasurementsStorage  # 1.2.840.10008.5.1.4.1.1.78.2

# Lensómetro / frontofocómetro
SOP_LENSOMETRY = LensometryMeasurementsStorage          # 1.2.840.10008.5.1.4.1.1.78.1

# ── Identificación del software ──────────────────────────────────────────────

MANUFACTURER        = "RetinaOS-Transducin"

# Optopol Revo FC130
MANUFACTURER_OPTOPOL = "Optopol Technology"
MODEL_REVO           = "Revo FC130"

# Carl Zeiss Meditec Cirrus HD-OCT
MANUFACTURER_ZEISS = "Carl Zeiss Meditec"
MODEL_CIRRUS       = "CIRRUS HD-OCT"

# UNICOS URK-800 autorefractómetro
MANUFACTURER_UNICOS = "UNICOS"
MODEL_URK800        = "URK-800"

# YEASN CCQ-800 lensómetro
MANUFACTURER_YEASN  = "YEASN"
MODEL_CCQ800        = "CCQ-800"

# Esquema privado 99OFTALMOS
PRIVATE_SCHEME      = "99OFTALMOS"
PRIVATE_SCHEME_NAME = "RetinaOS Private Coding Scheme"
PRIVATE_SCHEME_ORG  = "RetinaOS / oftalmos.org"

# ─── Optopol device family ────────────────────────────────────────────────────
# Source: SOCT DICOM Conformance Statement v21.1.0 (July 2025), cover page
# All models share the same SOCT software platform and .OPT format
OPTOPOL_DEVICE_FAMILY = [
    "REVO HR",
    "REVO NX 130", "REVO NX",
    "REVO FC 130", "REVO FC",
    "REVO 80", "REVO 60",
    "SOCT Copernicus REVO",
    "SOCT Copernicus",
]

# ─── Private Coding Scheme (DICOM PS3.16 §8 — designators starting with "99")─
# Registrado formalmente via CodingSchemeIdentificationSequence en cada SR.
# Usar PRIVATE_SCHEME como designator para todos los códigos provisionales.
PRIVATE_SCHEME = "99OFTALMOS"
PRIVATE_SCHEME_NAME = "RetinaOS/Transducin provisional codes"
PRIVATE_SCHEME_ORG  = "oftalmos.org"

# ─── Anatomic region SRT codes ────────────────────────────────────────────────
# Source: SOCT DICOM Conformance Statement v21.1.0, Table A.26
# Transducin uses the same codes as the native SOCT DICOM export
OPTOPOL_SRT_ANATOMIC = {
    "eye":              ("T-AA000", "SRT", "Eye"),
    "retina":           ("T-AA610", "SRT", "Retina"),
    "fovea":            ("T-AA612", "SRT", "Fovea centralis"),
    "optic_nerve_head": ("T-AA630", "SRT", "Optic nerve head"),
    "cornea":           ("T-AA200", "SRT", "Cornea"),
}
