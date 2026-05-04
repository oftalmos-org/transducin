# transducin/cirrus_extractor.py
# SPDX-License-Identifier: Apache-2.0
#
# Extractor de datos clínicos desde archivos .EX.DCM del Zeiss Cirrus HD-OCT 500.
#
# Flujo:
#   1. Leer tags privados CZM directamente de los .EX.DCM (sin desofuscar pixel data).
#   2. Extraer mediciones de los archivos de análisis (Raw Data Storage, SOPClass 1.2.840.10008.5.1.4.1.1.66):
#       - (0073,1255) XML OnhAnalysisParameters → AverageRNFLThickness, CupDiscRatio
#       - (0073,1150)/(0073,1155) OB uint16 LE → capas ILM/BM para CMT y ETDRS
#       - (0073,1140) XML AnalysisParameters → FoveaPosition (X,Y normalizado 0-1)
#   3. Retornar OCTClinicalData.
#
# Tags privados CZM confirmados (Cirrus HD-OCT 500, firmware 10.x/11.x):
#   (0057,0001) UI  — SOP Class del scan original (v. privado)
#   (0057,1015) LO  — EyeName ("Imagen Ocular", "Right Eye", etc.)
#   (0057,1021) LO  — PatientName (puede estar vacío/ofuscado)
#   (0057,1023) LO  — DeviceSerialNumber
#   (0059,1000) LO  — FileName relativo original (ej. DATAFILES/E001/...EX.DCM)
#   (0073,1140) LO  — XML AnalysisParameters (FoveaPosition, OphNerveHeadOffset)
#   (0073,1150) OB  — ILM layer uint16 LE [n_bscans × n_ascans]
#   (0073,1155) OB  — BM/RPE layer uint16 LE [n_bscans × n_ascans]
#   (0073,1255) LO  — XML OnhAnalysisParameters (RNFL, C/D, disc area)
#
# Calibración Cirrus HD-OCT 500:
#   Macular Cube 512×128: 6 mm × 6 mm, 128 B-scans × 512 A-scans
#   Axial:   1024 px / 2.0 mm → 1.953 µm/px
#   Lateral: 6 mm / 512 A-scans = 11.72 µm/A-scan
#            6 mm / 128 B-scans = 46.88 µm/B-scan
#
# Uso:
#   from transducin.cirrus_extractor import extract_from_exam
#   cd = extract_from_exam("input/DATAFILES/E001", noel_id="JAHJ19870831", laterality="L")

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pydicom

from transducin.clinical_data import ETDRSGrid, OCTClinicalData, RNFLSectors

logger = logging.getLogger(__name__)

# ── Calibración Cirrus HD-OCT 500 ──────────────────────────────────────────
_AXIAL_UM_PER_PX    = 2000.0 / 1024.0   # ≈ 1.953 µm/px
_MAC_N_BSCANS       = 128
_MAC_N_ASCANS       = 512
_MAC_SCAN_MM        = 6.0                # 6×6 mm campo macular
_A_PER_MM           = _MAC_N_ASCANS / _MAC_SCAN_MM   # ≈ 85.3 A-scans/mm
_B_PER_MM           = _MAC_N_BSCANS / _MAC_SCAN_MM   # ≈ 21.3 B-scans/mm
_DISC_N_BSCANS      = 200
_DISC_N_ASCANS      = 200

# SOP Classes
_SOP_RAW_DATA = "1.2.840.10008.5.1.4.1.1.66"


# ── Signal Quality Index ───────────────────────────────────────────────────

def _extract_signal_quality(ds: "pydicom.Dataset") -> Optional[float]:
    """Extrae el Signal Quality Index (SQI, 0–1) desde un DICOM Cirrus.

    Cirrus HD-OCT guarda la calidad de señal en escala 0–10 (entero).
    Se normaliza a 0–1 dividiendo entre 10.

    Candidatos de tags intentados (en orden):
      (0022,0035) DS — Image Quality Rating (DICOM estándar módulo oftalmológico)
      (0057,1025) LO — Señal Zeiss privada (posición empírica; sin confirmar)
      (0057,1027) LO — alternativa privada Zeiss (sin confirmar)

    Returns:
        sqi_mean normalizado a [0, 1] o None si no se encontró.
    """
    # 1. Tag estándar DICOM oftalmológico (0022,0035) Image Quality Rating
    for tag in [(0x0022, 0x0035), (0x0057, 0x1025), (0x0057, 0x1027)]:
        t = ds.get(tag)
        if t is None:
            continue
        try:
            raw_val = t.value
            if isinstance(raw_val, (list, tuple)):
                raw_val = raw_val[0]
            val = float(str(raw_val).split("\x00")[0].strip())
            if val < 0:
                continue
            # Normalizar: Cirrus escala 0-10; DICOM estándar 0-100 → detectar
            if val <= 1.0:
                return round(val, 4)
            elif val <= 10.0:
                return round(val / 10.0, 4)
            elif val <= 100.0:
                return round(val / 100.0, 4)
        except (TypeError, ValueError):
            continue
    return None


# ── Helpers de parsing XML ──────────────────────────────────────────────────

def _xml_float(xml: str, tag: str) -> Optional[float]:
    m = re.search(rf"<{tag}>([\d.E+\-]+)</{tag}>", xml)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _xml_str(xml: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
    return m.group(1).strip() if m else None


def _clean_tag_str(val) -> str:
    """Limpia valores de tags CZM que tienen basura binaria tras \\x00."""
    if isinstance(val, list):
        val = val[0]
    s = str(val)
    return s.split("\x00")[0].strip()


# ── Extracción de capas de segmentación ────────────────────────────────────

def _parse_layers(ds: pydicom.Dataset) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Extrae ILM y BM/RPE desde tags OB.

    Infiere dimensiones desde el tamaño del payload:
      - 128×512 (65 536 px) → Macular Cube 512×128
      - 200×200 (40 000 px) → Optic Disc Cube 200×200

    Returns:
        (ilm, bm) con forma inferida, o None si tamaño desconocido.
    """
    t_ilm = ds.get((0x0073, 0x1150))
    t_bm  = ds.get((0x0073, 0x1155))
    if t_ilm is None or t_bm is None:
        return None
    if t_ilm.VR != "OB" or t_bm.VR != "OB":
        return None
    n = len(t_ilm.value) // 2
    if n == _MAC_N_BSCANS * _MAC_N_ASCANS:
        n_rows, n_cols = _MAC_N_BSCANS, _MAC_N_ASCANS
    elif n == _DISC_N_BSCANS * _DISC_N_ASCANS:
        n_rows, n_cols = _DISC_N_BSCANS, _DISC_N_ASCANS
    else:
        logger.warning(
            "_parse_layers: tamaño inesperado %d px (esperado %d ó %d) — ignorando",
            n, _MAC_N_BSCANS * _MAC_N_ASCANS, _DISC_N_BSCANS * _DISC_N_ASCANS,
        )
        return None
    ilm = np.frombuffer(t_ilm.value, dtype="<u2").reshape(n_rows, n_cols).astype(float)
    bm  = np.frombuffer(t_bm.value,  dtype="<u2").reshape(n_rows, n_cols).astype(float)
    return ilm, bm


def _compute_thickness_map(ilm: np.ndarray, bm: np.ndarray) -> np.ndarray:
    """Calcula mapa de grosor en µm: (BM - ILM) * axial_calibration."""
    return np.clip(bm - ilm, 0, None) * _AXIAL_UM_PER_PX


# ── Posición foveal ────────────────────────────────────────────────────────

def _parse_fovea(ds: pydicom.Dataset) -> tuple[int, int]:
    """Extrae posición foveal desde AnalysisParameters XML.

    Returns:
        (cx, cy) en píxeles del mapa 128×512. Default: centro (255, 63).
    """
    t = ds.get((0x0073, 0x1140))
    if t is None:
        return _MAC_N_ASCANS // 2, _MAC_N_BSCANS // 2

    xml = _clean_tag_str(t.value)
    fx = re.search(r"<FoveaPosition>\s*<X>([\d.E+\-]+)</X>\s*<Y>([\d.E+\-]+)</Y>", xml)
    if fx:
        try:
            x_norm = float(fx.group(1))
            y_norm = float(fx.group(2))
            return (
                int(round(x_norm * _MAC_N_ASCANS)),
                int(round(y_norm * _MAC_N_BSCANS)),
            )
        except (ValueError, IndexError):
            pass
    return _MAC_N_ASCANS // 2, _MAC_N_BSCANS // 2


# ── Cálculo ETDRS ─────────────────────────────────────────────────────────

def _etdrs_from_map(
    th: np.ndarray,
    cx: int,
    cy: int,
) -> tuple[Optional[float], ETDRSGrid]:
    """Calcula CMT y ETDRS 9 sectores desde el mapa de grosor.

    ETDRS rings:
      - Centro (C): ∅ 1mm  → radio 0.5 mm
      - Anillo interno (1): 1-3 mm → radio 0.5-1.5 mm
      - Anillo externo (2): 3-6 mm → radio 1.5-3.0 mm

    Sectores: S=superior (ángulo 45-135°), N=nasal (135-225° para OS / -45 a 45° para OD),
              I=inferior (-135 a -45°), T=temporal (el restante).

    Nota: para simplicidad, se usa la convención de que N=izquierda en la imagen
    (el Cirrus exporta OD con nasal a la derecha, OS con nasal a la izquierda;
    el signo de la lateralidad se aplica en el SR builder, no aquí).

    Returns:
        (cmt_um, ETDRSGrid)
    """
    y_idx, x_idx = np.ogrid[:_MAC_N_BSCANS, :_MAC_N_ASCANS]

    # Distancia radial en mm desde la fóvea
    d_mm = np.sqrt(
        ((x_idx - cx) / _A_PER_MM) ** 2 +
        ((y_idx - cy) / _B_PER_MM) ** 2
    )

    # Ángulo en grados: 0° = derecha, 90° = arriba en imagen
    # arctan2(dy_up, dx) donde dy_up = cy - y (porque y crece hacia abajo)
    ang = np.degrees(np.arctan2(cy - y_idx, x_idx - cx))

    def _sector_mean(r_min: float, r_max: float, a_min: float, a_max: float) -> Optional[float]:
        if a_min < a_max:
            mask = (d_mm >= r_min) & (d_mm < r_max) & (ang >= a_min) & (ang < a_max)
        else:
            # cruce del ±180° (ej. T2: -45° a 45° pasa por 0°, o N: 135° a -135°)
            mask = (d_mm >= r_min) & (d_mm < r_max) & ((ang >= a_min) | (ang < a_max))
        vals = th[mask]
        return float(vals.mean()) if len(vals) > 0 else None

    # CMT: círculo central 1mm
    mask_c = d_mm < 0.5
    cmt = float(th[mask_c].mean()) if mask_c.sum() > 0 else None

    grid = ETDRSGrid(
        C  = cmt,
        # Anillo interno 1-3 mm (radio 0.5-1.5)
        S1 = _sector_mean(0.5, 1.5,   45.0, 135.0),
        N1 = _sector_mean(0.5, 1.5,  135.0, 225.0),   # N = izquierda (nasal OS)
        I1 = _sector_mean(0.5, 1.5, -135.0, -45.0),
        T1 = _sector_mean(0.5, 1.5, -45.0,  45.0),    # T = derecha  (temporal OS)
        # Anillo externo 3-6 mm (radio 1.5-3.0)
        S2 = _sector_mean(1.5, 3.0,   45.0, 135.0),
        N2 = _sector_mean(1.5, 3.0,  135.0, 225.0),
        I2 = _sector_mean(1.5, 3.0, -135.0, -45.0),
        T2 = _sector_mean(1.5, 3.0,  -45.0,  45.0),
    )
    # Usar C del grid como CMT si está disponible
    cmt_out = cmt
    return cmt_out, grid


# ── Extracción ONH ────────────────────────────────────────────────────────

def _xml_first(xml: str, *tags: str) -> Optional[float]:
    """Intenta _xml_float con múltiples nombres de campo, retorna el primero no-None."""
    for tag in tags:
        v = _xml_float(xml, tag)
        if v is not None:
            return v
    return None


def _parse_onh(ds: pydicom.Dataset) -> dict:
    """Extrae mediciones ONH desde OnhAnalysisParameters XML (0073,1255).

    Returns dict con keys:
        rnfl_avg, rnfl_sup, rnfl_inf, rnfl_nas, rnfl_tem,
        cdr, vcdr, disc_area_mm2, rim_area_mm2, cup_vol_mm3

    Sectores pRNFL: el XML Cirrus usa nombres como SuperiorAvgRNFL o
    SuperiorRNFLThickness dependiendo de la versión del software.
    Se prueban ambas variantes para cada cuadrante.
    """
    t = ds.get((0x0073, 0x1255))
    if t is None:
        return {}
    xml = _clean_tag_str(t.value)

    # Validar que los datos son reales (IsDataValid=true y RNFL > 0)
    valid = _xml_str(xml, "IsDataValid")
    if valid and valid.lower() == "false":
        return {}

    rnfl = _xml_float(xml, "AverageRNFLThickness")
    if rnfl is not None and rnfl == 0.0:
        return {}   # sin datos de análisis

    return {
        "rnfl_avg": rnfl,
        # Sectores S/I/N/T — dos variantes de nombre según versión SW Cirrus
        "rnfl_sup": _xml_first(xml, "SuperiorAvgRNFL", "SuperiorRNFLThickness",
                                     "SuperiorAverage"),
        "rnfl_inf": _xml_first(xml, "InferiorAvgRNFL", "InferiorRNFLThickness",
                                     "InferiorAverage"),
        "rnfl_nas": _xml_first(xml, "NasalAvgRNFL",    "NasalRNFLThickness",
                                     "NasalAverage"),
        "rnfl_tem": _xml_first(xml, "TemporalAvgRNFL", "TemporalRNFLThickness",
                                     "TemporalAverage"),
        "cdr":           _xml_float(xml, "CupDiscRatio"),
        "vcdr":          _xml_float(xml, "VeritcalDiscRatio"),
        "disc_area_mm2": _xml_float(xml, "DiscAreaMM_2"),
        "rim_area_mm2":  _xml_float(xml, "RimAreaMM_2"),
        "cup_vol_mm3":   _xml_float(xml, "CupVolumeMM_3"),
    }


# ── Detección de lateralidad ───────────────────────────────────────────────

def _infer_laterality(ds: pydicom.Dataset) -> str:
    """Infiere lateralidad desde tags CZM (OD→R, OS→L).

    Orden de prioridad:
    1. Laterality (0020,0060)  — CS estándar DICOM; Cirrus escribe "OD"/"OS"/"R"/"L"
    2. ImageLaterality (0020,0062) — CS estándar, valor "R"/"L"
    3. (0063,1005) FL privado CZM — 3.0=OD, 6.0=OS (algunos modelos/versiones)
    4. (0057,1015) LO EyeName — texto libre "right"/"left"/"OD"/"OS"
    """
    # 1. Laterality (0020,0060) — más fiable en archivos Cirrus reales
    raw = str(getattr(ds, "Laterality", "")).split("\x00")[0].strip().upper()
    if raw in ("R", "OD"):
        return "R"
    if raw in ("L", "OS"):
        return "L"

    # 2. ImageLaterality (0020,0062)
    raw = str(getattr(ds, "ImageLaterality", "")).split("\x00")[0].strip().upper()
    if raw in ("R", "OD"):
        return "R"
    if raw in ("L", "OS"):
        return "L"

    # 3. (0063,1005) FL privado CZM — 3.0=OD, 6.0=OS (observado empíricamente)
    t = ds.get((0x0063, 0x1005))
    if t is not None:
        try:
            v = float(t.value)
            if v == 3.0:
                return "R"
            if v == 6.0:
                return "L"
        except (TypeError, ValueError):
            pass

    # 4. (0057,1015) EyeName — texto libre
    t_eye = ds.get((0x0057, 0x1015))
    if t_eye is not None:
        eye = _clean_tag_str(t_eye.value).lower()
        if "right" in eye or " od" in eye or "ojo d" in eye:
            return "R"
        if "left" in eye or " os" in eye or "ojo i" in eye:
            return "L"

    return ""


# ── Extractor principal ────────────────────────────────────────────────────

def extract_from_exam(
    exam_dir: str | Path,
    noel_id: str,
    laterality: str = "",
    study_date: str = "",
    patient_name: str = "",
    patient_dob: str = "",
) -> list[OCTClinicalData]:
    """Extrae OCTClinicalData desde un directorio de examen Cirrus (.EX.DCM).

    Lee todos los .EX.DCM del directorio, identifica los archivos de análisis
    (SOPClass Raw Data Storage) y extrae:
      - CMT y ETDRS 9 sectores desde capas ILM/BM (macular 128×512)
      - RNFL medio y C/D ratio desde OnhAnalysisParameters XML

    Un mismo examen puede contener múltiples estudios (visitas). Se agrupa por
    StudyInstanceUID y se retorna un OCTClinicalData por estudio con mediciones.

    Args:
        exam_dir:     Directorio con archivos .EX.DCM (puede ser E001, E002, etc.)
        noel_id:      PatientID formato NOEL (JAHJ19870831).
        laterality:   "R" | "L" (se autodetecta si no se provee).
        study_date:   YYYYMMDD (se lee del DICOM si no se provee).
        patient_name: Nombre del paciente (opcional).
        patient_dob:  Fecha de nacimiento YYYYMMDD (opcional).

    Returns:
        Lista de OCTClinicalData, uno por estudio con mediciones (puede ser vacía).
    """
    exam_dir = Path(exam_dir)
    dcm_files = sorted(
        list(exam_dir.glob("*.DCM")) + list(exam_dir.glob("*.dcm"))
    )
    if not dcm_files:
        logger.warning("No se encontraron .DCM en %s", exam_dir)
        return []

    # Agrupar archivos de análisis por StudyInstanceUID
    studies: dict[str, list[pydicom.Dataset]] = {}
    for fp in dcm_files:
        try:
            ds = pydicom.dcmread(str(fp), force=True, stop_before_pixels=True)
        except Exception as e:
            logger.debug("No se pudo leer %s: %s", fp.name, e)
            continue

        # Solo procesar archivos de análisis (Raw Data Storage o con capas OB)
        sop = str(getattr(ds, "SOPClassUID", "")).split("\x00")[0].strip()
        has_layers = ds.get((0x0073, 0x1150)) is not None
        has_onh    = ds.get((0x0073, 0x1255)) is not None

        if not (sop == _SOP_RAW_DATA or has_layers or has_onh):
            continue

        uid = str(getattr(ds, "StudyInstanceUID", "")).split("\x00")[0].strip()
        if not uid:
            uid = "_unknown_"
        studies.setdefault(uid, []).append(ds)

    results: list[OCTClinicalData] = []

    for uid, ds_list in studies.items():
        cd = OCTClinicalData(
            noel_id      = noel_id,
            vendor       = "zeiss_cirrus",
            study_date   = study_date,
            laterality   = laterality,
            patient_name = patient_name,
            patient_dob  = patient_dob,
            study_type   = "unknown",
            extraction_confidence = "unknown",
        )

        macular_done = False
        onh_done     = False

        for ds in ds_list:
            # Leer fecha del estudio si no se proveyó
            if not cd.study_date:
                d = str(getattr(ds, "StudyDate", "")).split("\x00")[0].strip()
                if len(d) == 8:
                    cd.study_date = d

            # Lateralidad
            if not cd.laterality:
                lat = _infer_laterality(ds)
                if lat:
                    cd.laterality = lat

            # Serial del dispositivo
            if not cd.device_serial:
                t_ser = ds.get((0x0057, 0x1023))
                if t_ser:
                    cd.device_serial = _clean_tag_str(t_ser.value)[:16]

            # SQI — Signal Quality Index (best-effort desde tags DICOM)
            if cd.sqi_mean is None:
                sqi = _extract_signal_quality(ds)
                if sqi is not None:
                    cd.sqi_mean = sqi
                    logger.debug("Cirrus SQI=%.4f desde tag DICOM", sqi)

            # ── Macular: ILM/BM layers ────────────────────────────────────
            if not macular_done:
                layers = _parse_layers(ds)
                if layers is not None and layers[0].shape == (_MAC_N_BSCANS, _MAC_N_ASCANS):
                    ilm, bm = layers
                    th = _compute_thickness_map(ilm, bm)
                    cx, cy = _parse_fovea(ds)
                    cmt, grid = _etdrs_from_map(th, cx, cy)
                    cd.cmt_um     = round(cmt, 1) if cmt is not None else None
                    cd.etdrs_grid = grid
                    cd.study_type = "macular"
                    cd.add_note(
                        f"CONFIRMED: CMT={cd.cmt_um}µm desde ILM/BM Cirrus "
                        f"(fovea px=({cx},{cy}), axial={_AXIAL_UM_PER_PX:.3f}µm/px)"
                    )
                    macular_done = True

            # ── ONH: RNFL + C/D ──────────────────────────────────────────
            if not onh_done:
                onh = _parse_onh(ds)
                if onh.get("rnfl_avg") is not None:
                    cd.rnfl = RNFLSectors(
                        global_avg = round(onh["rnfl_avg"], 2),
                        superior   = round(onh["rnfl_sup"], 2) if onh.get("rnfl_sup") is not None else None,
                        inferior   = round(onh["rnfl_inf"], 2) if onh.get("rnfl_inf") is not None else None,
                        nasal      = round(onh["rnfl_nas"], 2) if onh.get("rnfl_nas") is not None else None,
                        temporal   = round(onh["rnfl_tem"], 2) if onh.get("rnfl_tem") is not None else None,
                    )
                    if onh.get("cdr") is not None:
                        cd.cup_disc_ratio = round(onh["cdr"], 3)
                    if onh.get("vcdr") is not None:
                        cd.vcdr = round(onh["vcdr"], 3)
                    if onh.get("disc_area_mm2") is not None:
                        cd.disc_area_mm2 = round(onh["disc_area_mm2"], 4)
                    if onh.get("rim_area_mm2") is not None:
                        cd.rim_area_mm2 = round(onh["rim_area_mm2"], 4)
                    if onh.get("cup_vol_mm3") is not None:
                        cd.cup_vol_mm3 = round(onh["cup_vol_mm3"], 4)
                    cd.study_type = "optic_nerve"
                    has_sectors = cd.rnfl.superior is not None
                    cd.add_note(
                        f"CONFIRMED: pRNFL={cd.rnfl.global_avg}µm"
                        + (f" S={cd.rnfl.superior} I={cd.rnfl.inferior}"
                           f" N={cd.rnfl.nasal} T={cd.rnfl.temporal}µm"
                           if has_sectors else " (sectores no disponibles en este XML)")
                        + f", C/D={cd.cup_disc_ratio}, VCDR={cd.vcdr}, "
                        f"disc={cd.disc_area_mm2}mm², rim={cd.rim_area_mm2}mm²"
                    )
                    onh_done = True

        # Solo incluir si tiene al menos una medición
        if cd.has_measurements():
            cd.extraction_confidence = "confirmed"
            results.append(cd)
            logger.info(
                "Cirrus exam %s: CMT=%s RNFL=%s CDR=%s lat=%s date=%s",
                exam_dir.name,
                cd.cmt_um,
                cd.rnfl.global_avg if cd.rnfl else None,
                cd.cup_disc_ratio,
                cd.laterality,
                cd.study_date,
            )

    return results


def extract_batch(
    datafiles_dir: str | Path,
    noel_id: str,
    laterality: str = "",
) -> list[OCTClinicalData]:
    """Extrae OCTClinicalData de todos los subdirectorios E000, E001, … en datafiles_dir.

    Args:
        datafiles_dir: Directorio raíz con subdirectorios E000, E001, …
        noel_id:       PatientID formato NOEL.
        laterality:    "R" | "L" (se autodetecta por examen si no se provee).

    Returns:
        Lista de OCTClinicalData con todas las mediciones encontradas.
    """
    datafiles_dir = Path(datafiles_dir)
    all_results: list[OCTClinicalData] = []
    for exam_dir in sorted(datafiles_dir.iterdir()):
        if not exam_dir.is_dir():
            continue
        results = extract_from_exam(exam_dir, noel_id=noel_id, laterality=laterality)
        all_results.extend(results)
    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# TESTS — python -m transducin.cirrus_extractor
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import logging

    logging.basicConfig(level=logging.WARNING)
    G, R, E = "\033[92m", "\033[91m", "\033[0m"
    errors = 0

    def check(label, condition, detail=""):
        global errors
        ok = bool(condition)
        print(f"  {'✓' if ok else '✗'} [{G+'PASS'+E if ok else R+'FAIL'+E}] {label}" + (f": {detail}" if detail else ""))
        if not ok:
            errors += 1

    print("\n══ cirrus_extractor — tags privados CZM ══")

    # ── Unit tests (sin archivos) ──────────────────────────────────────────
    # Test XML parser
    xml = """<?xml version="1.0" encoding="utf-16"?>
<OnhAnalysisParameters>
  <AverageRNFLThickness>96.81</AverageRNFLThickness>
  <CupDiscRatio>0.503</CupDiscRatio>
  <VeritcalDiscRatio>0.507</VeritcalDiscRatio>
  <IsDataValid>true</IsDataValid>
</OnhAnalysisParameters>"""

    check("_xml_float RNFL",   _xml_float(xml, "AverageRNFLThickness") == 96.81)
    check("_xml_float CDR",    abs(_xml_float(xml, "CupDiscRatio") - 0.503) < 0.001)
    check("_xml_str IsValid",  _xml_str(xml, "IsDataValid") == "true")

    # Test ETDRS calculation with synthetic data
    th_flat = np.full((_MAC_N_BSCANS, _MAC_N_ASCANS), 300.0)
    cx_c, cy_c = _MAC_N_ASCANS // 2, _MAC_N_BSCANS // 2
    cmt, grid = _etdrs_from_map(th_flat, cx_c, cy_c)
    check("CMT flat 300µm",    cmt is not None and abs(cmt - 300.0) < 1.0, f"{cmt:.1f}" if cmt else "None")
    check("ETDRS C=300",       grid.C is not None and abs(grid.C - 300.0) < 1.0)
    check("ETDRS S1=300",      grid.S1 is not None and abs(grid.S1 - 300.0) < 1.0)
    check("ETDRS has 9 vals",  all(v is not None for v in [grid.C, grid.S1, grid.N1, grid.I1, grid.T1,
                                                             grid.S2, grid.N2, grid.I2, grid.T2]))

    # Test with gradient (thicker in center)
    y_g, x_g = np.ogrid[:_MAC_N_BSCANS, :_MAC_N_ASCANS]
    d_g = np.sqrt(((x_g - cx_c) / _A_PER_MM)**2 + ((y_g - cy_c) / _B_PER_MM)**2)
    th_grad = 350.0 - d_g * 20.0  # thicker center
    cmt_g, grid_g = _etdrs_from_map(th_grad, cx_c, cy_c)
    check("CMT > sectors (gradient)", cmt_g > grid_g.S1 and cmt_g > grid_g.I1, f"CMT={cmt_g:.1f} S1={grid_g.S1:.1f}")

    # ── Integration test (si hay archivos reales) ──────────────────────────
    exam_dirs = sorted(Path("input/DATAFILES").glob("E*")) if Path("input/DATAFILES").exists() else []

    if exam_dirs:
        print("\n══ Integration — archivos reales ══")
        results = extract_from_exam(exam_dirs[0], noel_id="SILT19900101")
        check("extract_from_exam retorna lista",  isinstance(results, list))
        check("al menos 1 resultado",             len(results) >= 1, f"n={len(results)}")

        if results:
            cd = results[0]
            check("noel_id asignado",             cd.noel_id == "SILT19900101")
            check("has_measurements()",           cd.has_measurements())
            check("confidence confirmed",         cd.extraction_confidence == "confirmed")

            if cd.cmt_um is not None:
                check("CMT en rango 100-500µm",   100 < cd.cmt_um < 500, f"{cd.cmt_um}µm")
            if cd.etdrs_grid is not None:
                check("ETDRS grid tiene datos",   cd.etdrs_grid.has_data())
            if cd.rnfl is not None:
                check("RNFL en rango 40-160µm",   40 < cd.rnfl.global_avg < 160,
                      f"{cd.rnfl.global_avg}µm")
            if cd.cup_disc_ratio is not None:
                check("C/D en rango 0-1",         0 < cd.cup_disc_ratio < 1,
                      f"{cd.cup_disc_ratio:.3f}")

            # Batch
            batch = extract_batch("input/DATAFILES", noel_id="SILT19900101")
            check("batch retorna múltiples",      len(batch) >= 1, f"n={len(batch)}")
    else:
        print("\n  (sin archivos reales — solo unit tests)")

    print(f"\n{'══ TODOS LOS TESTS PASARON ══' if errors==0 else f'══ {errors} FALLARON ══'}\n")
    sys.exit(0 if errors == 0 else 1)
