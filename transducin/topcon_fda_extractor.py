# transducin/topcon_fda_extractor.py
# SPDX-License-Identifier: Apache-2.0
#
# Extractor de mediciones clínicas OCT para Topcon (.fda).
#
# Usa oct-converter (FDA reader) para leer el volumen + contours de capas,
# y reutiliza las funciones de cómputo de revo_opt_reader:
#   compute_etdrs()       → CMT + ETDRS 9 sectores
#   compute_rnfl_sectors()→ mRNFL S/I/N/T (macular)
#   compute_gcl_ipl()     → mGCIPL S/I/N/T
#
# Mapeo de contour keys en formato FDA (uocte wiki + oct-converter):
#   "ILM"      = Internal Limiting Membrane
#   "RNFL_GCL" = RNFL / GCL boundary
#   "GCL_IPL"  = GCL / IPL boundary
#   "IPL_INL"  = IPL / INL boundary
#   "MZ_EZ"    = Myoid / Ellipsoid zone
#   "IZ_RPE"   = Interdigitation zone / RPE boundary
#   "BM"       = Bruch's Membrane
#   "INL_OPL"  = INL / OPL boundary
#   "ELM"      = External Limiting Membrane
#   "CSI"      = Choroid / Sclera Interface
#
# Escalas (pixel_spacing desde FDA.read_scan_params):
#   pixel_spacing[0] = x_dimension_mm / width      (lateral mm/A-scan)
#   pixel_spacing[1] = y_dimension_mm / n_slices   (inter-frame mm/B-scan)
#   pixel_spacing[2] = z_resolution_um / 1000      (axial mm/px)
#
# Los contours en OCTVolumeWithMetaData ya vienen convertidos a
# "pixels desde TOP" (oct-converter aplica height - v internamente).

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from transducin.clinical_data import ETDRSGrid, OCTClinicalData, RNFLSectors
from transducin.noel_id import is_valid_noel

logger = logging.getLogger("transducin.topcon_fda")

# Defaults Topcon Triton / Atlantis si pixel_spacing no está disponible
_DEFAULT_AXIAL_UM     = 2.60   # µm/px axial (Triton: 2.6µm, Maestro: 2.6µm)
_DEFAULT_LATERAL_UM   = 11.72  # µm/A-scan (512 A-scans, 6mm FOV)
_DEFAULT_SCAN_WIDTH_Y = 6.0    # mm

# Circle scans pRNFL: 1 B-scan, A-scans típicos Topcon
_CIRCLE_ASCANS_FDA = {256, 512, 768, 1024}


def _detect_scan_type_fda(n_bscans: int, n_ascans: int, contour_keys: set) -> str:
    """Infiere tipo de scan Topcon FDA por dimensiones y contours disponibles."""
    if n_bscans == 1 and n_ascans in _CIRCLE_ASCANS_FDA:
        return "rnfl_circle"
    if n_bscans >= 9 and n_ascans >= 256:
        return "macular"
    if 2 <= n_bscans <= 8 and n_ascans >= 256:
        return "hd_line"
    return "unknown"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_layer(arr: Optional[np.ndarray], n_b: int, n_a: int) -> Optional[np.ndarray]:
    """Valida que el array 2D tenga las dimensiones correctas y valores finitos.

    Retorna None si el array no es utilizable (dimensiones erróneas o <3 B-scans válidos).
    Garantiza dtype float32 con NaN donde la segmentación falló (valor 0 = ausente en FDA).
    """
    if arr is None:
        return None
    if arr.shape != (n_b, n_a):
        logger.debug("FDA: layer shape %s != esperado (%d, %d)", arr.shape, n_b, n_a)
        return None

    out = arr.astype(np.float32)
    # En FDA, 0 indica segmentación ausente
    out[out == 0] = np.nan

    valid_rows = np.sum(np.any(np.isfinite(out), axis=1))
    if valid_rows < 3:
        return None
    return out


def _geometry_from_pixel_spacing(ps: list, n_b: int) -> dict:
    """Extrae escalas físicas desde pixel_spacing de oct-converter FDA.

    pixel_spacing = [x_mm/A-scan, y_mm/B-scan, z_mm/px_axial]
    """
    axial_um     = _DEFAULT_AXIAL_UM
    lateral_um   = _DEFAULT_LATERAL_UM
    scan_width_mm = _DEFAULT_SCAN_WIDTH_Y

    if ps and len(ps) >= 3:
        if ps[0] and 0.001 < ps[0] < 0.5:
            lateral_um = ps[0] * 1000.0
        if ps[2] and 0.001 < ps[2] < 0.05:
            axial_um = ps[2] * 1000.0
        if ps[1] and 0.001 < ps[1] < 2.0 and n_b > 0:
            scan_width_mm = ps[1] * n_b

    return {
        "axial_um":      max(axial_um, 0.5),
        "lateral_um":    max(lateral_um, 1.0),
        "scan_width_mm": max(scan_width_mm, 1.0),
    }


def _patient_name(vol) -> str:
    parts = []
    if getattr(vol, "surname", None):
        parts.append(str(vol.surname).upper())
    if getattr(vol, "first_name", None):
        parts.append(str(vol.first_name).upper())
    return "^".join(parts) if parts else ""


def _study_date(vol) -> str:
    dt = getattr(vol, "acquisition_date", None)
    if dt is None:
        return ""
    try:
        return dt.strftime("%Y%m%d")
    except Exception:
        return str(dt)[:8].replace("-", "")


# ── Medición reutilizable (sin dependencia de oct_converter) ─────────────────

def measure_fda_volume(
    contours: dict,
    n_b: int,
    n_a: int,
    pixel_spacing: Optional[list],
    laterality: str,
    cd_base: dict,
    sqi_mean: Optional[float] = None,
    label: str = "",
    require_laterality: bool = False,
) -> Optional[OCTClinicalData]:
    """Calcula mediciones clínicas de un volumen FDA a partir de sus contornos.

    Es la lógica de ``extract_from_fda`` aislada de la lectura del archivo; la
    comparten el extractor (oct_converter) y el reader vendorizado
    (``transducin.readers.fda_reader``).

    Args:
        contours:      {"ILM": 2D, "BM": 2D, ...} en píxeles desde el TOP.
        n_b, n_a:      B-scans y A-scans del volumen.
        pixel_spacing: [x_mm/A-scan, y_mm/B-scan, z_mm/px] o None.
        laterality:    "R" | "L" | otro.
        cd_base:       kwargs de OCTClinicalData SIN ``laterality``.
        require_laterality: si True y la lateralidad no es R/L, no mide
                       (retorna None) en vez de asumir "R" como el extractor legado.
    """
    from transducin.revo_opt_reader import (
        compute_etdrs,
        compute_gcl_ipl,
        compute_rnfl_sectors,
    )

    scan_type = _detect_scan_type_fda(n_b, n_a, set(contours.keys()))

    ilm      = _validate_layer(contours.get("ILM"),      n_b, n_a)
    bm       = _validate_layer(contours.get("BM"),       n_b, n_a)
    rnfl_gcl = _validate_layer(contours.get("RNFL_GCL"), n_b, n_a)
    ipl_inl  = _validate_layer(contours.get("IPL_INL"),  n_b, n_a)

    if ilm is None:
        logger.debug("FDA: ILM no disponible en %s — saltando", label)
        return None

    geo = _geometry_from_pixel_spacing(pixel_spacing or [], n_b)
    params = {
        "axial_um":      geo["axial_um"],
        "lateral_um":    geo["lateral_um"],
        "scan_width_mm": geo["scan_width_mm"],
        "n_bscans":      n_b,
        "n_ascans":      n_a,
    }

    lat = str(laterality or "").upper()
    if lat not in ("R", "L"):
        if require_laterality:
            logger.warning("FDA %s: lateralidad desconocida — no se miden sectores", label)
            return None
        lat = "R"

    logger.info(
        "FDA volumen %s: %d B-scans × %d A-scans, tipo=%s lat=%s axial=%.2fµm/px",
        label, n_b, n_a, scan_type, lat, params["axial_um"],
    )

    cd_base = dict(cd_base, laterality=lat)

    # ── Circle scan → pRNFL ───────────────────────────────────────────────
    if scan_type == "rnfl_circle":
        from transducin.spectralis_extractor import _compute_prnfl_circle
        if rnfl_gcl is None:
            return None
        prnfl = _compute_prnfl_circle(ilm, rnfl_gcl, params["axial_um"], lat)
        if not (prnfl and prnfl.global_avg is not None):
            return None
        cd = OCTClinicalData(
            **cd_base,
            study_type = "rnfl_circle",
            rnfl       = prnfl,
            sqi_mean   = sqi_mean,
            extraction_confidence = "confirmed",
        )
        cd.add_note(
            f"CONFIRMED: pRNFL={prnfl.global_avg:.1f}µm "
            f"S={prnfl.superior} I={prnfl.inferior} "
            f"N={prnfl.nasal} T={prnfl.temporal}µm (Topcon FDA circle)"
        )
        if cd.has_measurements():
            logger.info("  OK: pRNFL=%.1fµm", prnfl.global_avg)
            return cd
        return None

    if bm is None:
        logger.debug("FDA: BM no disponible en %s — saltando", label)
        return None

    etdrs  = compute_etdrs(ilm, bm, params, laterality=lat)
    cmt_um = round(float(etdrs.C), 1) if (etdrs and etdrs.C is not None) else None

    rnfl = None
    if rnfl_gcl is not None and ilm.shape == rnfl_gcl.shape:
        rnfl = compute_rnfl_sectors(ilm, rnfl_gcl, params, laterality=lat)

    gcl_ipl = None
    if rnfl_gcl is not None and ipl_inl is not None and rnfl_gcl.shape == ipl_inl.shape:
        gcl_ipl = compute_gcl_ipl(rnfl_gcl, ipl_inl, params, laterality=lat)

    cd = OCTClinicalData(
        **cd_base,
        study_type = scan_type,
        cmt_um     = cmt_um,
        etdrs_grid   = etdrs,
        rnfl         = rnfl,
        gcl_ipl      = gcl_ipl,
        gcl_avg_um   = round(float(gcl_ipl.global_avg), 1)
                       if (gcl_ipl and gcl_ipl.global_avg is not None) else None,
        sqi_mean     = sqi_mean,
        extraction_confidence = "confirmed" if cmt_um is not None else "assumed",
    )

    if cmt_um is not None:
        cd.add_note(
            f"CONFIRMED: CMT={cmt_um}µm desde ILM/BM Topcon FDA "
            f"(axial={params['axial_um']:.2f}µm/px, "
            f"lateral={params['lateral_um']:.2f}µm/A-scan)"
        )
    if rnfl and rnfl.global_avg is not None:
        cd.add_note(
            f"CONFIRMED: mRNFL={rnfl.global_avg:.1f}µm "
            f"S={rnfl.superior:.1f} N={rnfl.nasal:.1f} "
            f"I={rnfl.inferior:.1f} T={rnfl.temporal:.1f} µm"
        )
    if gcl_ipl and gcl_ipl.global_avg is not None:
        cd.add_note(
            f"CONFIRMED: mGCIPL={gcl_ipl.global_avg:.1f}µm "
            f"S={gcl_ipl.superior:.1f} N={gcl_ipl.nasal:.1f} "
            f"I={gcl_ipl.inferior:.1f} T={gcl_ipl.temporal:.1f} µm"
        )

    if not cd.has_measurements():
        logger.warning("  Sin mediciones para %s", label)
        return None
    logger.info(
        "  OK: CMT=%s mRNFL=%s mGCIPL=%s",
        f"{cmt_um}µm" if cmt_um else "N/A",
        f"{rnfl.global_avg:.1f}µm" if rnfl else "N/A",
        f"{gcl_ipl.global_avg:.1f}µm" if gcl_ipl else "N/A",
    )
    return cd


# ── Extractor principal ──────────────────────────────────────────────────────

def extract_from_fda(
    filepath: Path,
    noel_id: str = "",
) -> list[OCTClinicalData]:
    """Extrae OCTClinicalData desde un archivo .fda Topcon.

    Calcula CMT, ETDRS 9 sectores, mRNFL S/I/N/T y mGCIPL S/I/N/T
    cuando el archivo contiene el chunk @CONTOUR_INFO con segmentación.

    Args:
        filepath: ruta al archivo .fda
        noel_id:  NOEL ID del paciente (si disponible desde filename)

    Returns:
        Lista de OCTClinicalData. Vacía si el archivo no tiene segmentación
        utilizable o no tiene mediciones válidas.
    """
    from oct_converter.readers.fda import FDA

    filepath = Path(filepath)
    results: list[OCTClinicalData] = []

    try:
        reader = FDA(str(filepath))
        vol    = reader.read_oct_volume()
    except Exception as exc:
        logger.error("FDA: no se pudo leer %s: %s", filepath.name, exc)
        return results

    if vol is None:
        logger.warning("FDA: sin volumen en %s", filepath.name)
        return results

    # Intentar extraer NOEL ID desde patient_id del archivo
    pid = getattr(vol, "patient_id", None) or ""
    if not noel_id and pid and is_valid_noel(str(pid)):
        noel_id = str(pid)

    # ── SQI desde PARAM_OBS_02 / image_quality ────────────────────────────
    sqi_mean: Optional[float] = None
    try:
        meta       = getattr(vol, "metadata", None) or {}
        param_obs  = meta.get("param_obs_02") or {}
        iq_raw     = param_obs.get("image_quality", "")
        iq_str     = str(iq_raw).strip().strip("\x00").strip()
        if iq_str:
            iq_val = float(iq_str)
            if iq_val > 1.0:
                sqi_mean = round(iq_val / 100.0, 4)  # escala 0–100 → 0–1
            elif 0.0 <= iq_val <= 1.0:
                sqi_mean = round(iq_val, 4)
            logger.info("FDA SQI: image_quality=%s → sqi=%.4f (%s)",
                        iq_str, sqi_mean or 0, filepath.name)
    except Exception:
        pass

    # ── Contours ──────────────────────────────────────────────────────────
    contours = getattr(vol, "contours", None)
    if not contours:
        logger.debug("FDA: %s sin @CONTOUR_INFO — solo imagen", filepath.name)
        return results

    # Dimensiones desde el volumen de B-scans
    bscans = getattr(vol, "volume", None) or []
    if not bscans:
        logger.warning("FDA: %s sin B-scans", filepath.name)
        return results

    n_b = len(bscans)
    n_a = bscans[0].shape[1] if hasattr(bscans[0], "shape") else 0
    if n_b < 1 or n_a < 10:
        logger.warning("FDA: %s dimensiones insuficientes (%d B-scans, %d A-scans)", filepath.name, n_b, n_a)
        return results

    cd_base = dict(
        noel_id      = noel_id,
        vendor       = "topcon_triton",
        source_file  = str(filepath),
        study_date   = _study_date(vol),
        study_time   = "",
        patient_name = _patient_name(vol),
        patient_dob  = str(getattr(vol, "patient_dob", "") or ""),
    )
    cd = measure_fda_volume(
        contours, n_b, n_a,
        getattr(vol, "pixel_spacing", None),
        getattr(vol, "laterality", ""),
        cd_base,
        sqi_mean,
        label=filepath.stem,
    )
    if cd is not None:
        results.append(cd)
    return results
