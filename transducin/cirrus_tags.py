# SPDX-License-Identifier: Apache-2.0
"""
cirrus_tags.py
Helpers de inferencia de tags clínicos DICOM para exámenes Cirrus HD-OCT.

Usado por:
  - hot_folder_watcher.py  (pipeline live)
  - reprocess_cirrus.py    (batch retroactivo E000-E999)

Inferencias:
  infer_study_type(series_desc)      -> study_type canónico (macular, angio,
                                         optic_nerve, ganglion_cell, fundus,
                                         hd_line, en_face, analysis, unknown)
  infer_laterality(ds)               -> "R" / "L" / ""  (tag privado Zeiss
                                         0057,1015 y fallback ImageLaterality)
  apply_cirrus_study_tags(ds)        -> muta ds.StudyDescription con formato
                                         "Zeiss Cirrus HD-OCT <tipo> <OD|OS>".
                                         Vendor-specific: no pasa por el label
                                         genérico study_description_label() que
                                         consumen Revo y otros dispositivos.
                                         Idempotente; llamar ANTES de cualquier
                                         upload / C-STORE.
"""
from __future__ import annotations

import re


# 'OCT Cube 4096x5', 'OCT Cube 1024x2', etc. — HD acquisitions whose name
# lacks 'HD'/'Line'/'Raster'. The [25] + \b guard excludes 'Optic Disc Cube
# 200x200' and 'Macular Cube 512x128' (longer b-scan counts).
_HD_CUBE_SHORTHAND = re.compile(r"\bcube\s+\d+x[25]\b")


_CIRRUS_STUDY_LABELS: dict[str, str] = {
    "macular":       "Zeiss Cirrus HD-OCT Macular",
    "optic_nerve":   "Zeiss Cirrus HD-OCT Optic Disc",
    "hd_line":       "Zeiss Cirrus HD-OCT HD Line",
    "fundus":        "Zeiss Cirrus HD-OCT Fundus",
    "en_face":       "Zeiss Cirrus HD-OCT En Face",
    "ganglion_cell": "Zeiss Cirrus HD-OCT Ganglion Cell",
    "angio":         "Zeiss Cirrus HD-OCT OCTA",
    "analysis":      "Zeiss Cirrus HD-OCT Analysis Data",
    "unknown":       "Zeiss Cirrus HD-OCT",
}
_LAT_LABEL: dict[str, str] = {"R": "OD", "L": "OS"}


def infer_study_type(series_desc: str) -> str:
    """Mapea SeriesDescription de Cirrus a study_type canónico.

    Orden importa: el wrapper Raw Data Storage contiene el literal 'Cirrus
    HD-OCT' y matchearía el filtro HD si no se detecta primero como
    'analysis'. Las variantes HD por shape ('OCT Cube 4096x5', 'OCT Cube
    Nx2') se detectan aunque no lleven 'HD'/'Line'/'Raster' en el nombre.
    """
    s = (series_desc or "").lower()

    if "background processing" in s:
        return "analysis"

    if "fundus" in s or "slo" in s:
        return "fundus"
    if "en face" in s:
        return "en_face"
    if "macular" in s:
        return "macular"
    if "optic disc" in s or "optic nerve" in s:
        return "optic_nerve"
    if "ganglion" in s:
        return "ganglion_cell"
    if "angio" in s or "octa" in s:
        return "angio"

    if ("raster" in s or "hd single" in s or "hd 5-line" in s
            or "hd line" in s or " line" in s
            or "4096x5" in s
            or _HD_CUBE_SHORTHAND.search(s) is not None):
        return "hd_line"

    return "unknown"


def infer_laterality(ds) -> str:
    """Infiere lateralidad de un DICOM Cirrus (tag privado 0057,1015 o ImageLaterality)."""
    t = ds.get((0x0057, 0x1015))
    if t is not None:
        eye = "".join(c for c in str(t.value) if 0x20 <= ord(c) < 0x7F).lower()
        if "right" in eye or " od" in eye or "ojo d" in eye:
            return "R"
        if "left" in eye or " os" in eye or "ojo i" in eye:
            return "L"
    lat = str(getattr(ds, "ImageLaterality", "")).strip().upper()
    if lat in ("R", "L"):
        return lat
    return ""


def apply_cirrus_study_tags(ds) -> str:
    """Setea ds.StudyDescription con formato 'Zeiss Cirrus HD-OCT ... OD|OS'.

    No usa transducin.clinical_data.study_description_label(): ese label
    genérico lo consumen Revo y otros dispositivos; aquí queremos prefijo
    vendor-specific. Retorna el valor asignado.
    """
    series_desc = str(getattr(ds, "SeriesDescription", ""))
    study_type = infer_study_type(series_desc)
    lat = infer_laterality(ds)
    base = _CIRRUS_STUDY_LABELS.get(study_type, _CIRRUS_STUDY_LABELS["unknown"])
    suffix = _LAT_LABEL.get(lat.upper(), "")
    label = f"{base} {suffix}".strip() if suffix else base
    ds.StudyDescription = label
    return label
