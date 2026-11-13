# transducin/topcon_fda_extractor.py
# SPDX-License-Identifier: Apache-2.0
#
# Extractor de mediciones clínicas OCT para Topcon (.fda).
#
# Medición de capas del .fda (lector propio en transducin/readers/, sin oct-converter)
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

import numpy as np

from transducin.clinical_data import OCTClinicalData

logger = logging.getLogger("transducin.topcon_fda")

# Defaults Topcon Triton / Atlantis si pixel_spacing no está disponible
_DEFAULT_AXIAL_UM = 2.60  # µm/px axial (Triton: 2.6µm, Maestro: 2.6µm)
_DEFAULT_LATERAL_UM = 11.72  # µm/A-scan (512 A-scans, 6mm FOV)
_DEFAULT_SCAN_WIDTH_Y = 6.0  # mm

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


def _validate_layer(arr: np.ndarray | None, n_b: int, n_a: int) -> np.ndarray | None:
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
    axial_um = _DEFAULT_AXIAL_UM
    lateral_um = _DEFAULT_LATERAL_UM
    scan_width_mm = _DEFAULT_SCAN_WIDTH_Y

    if ps and len(ps) >= 3:
        if ps[0] and 0.001 < ps[0] < 0.5:
            lateral_um = ps[0] * 1000.0
        if ps[2] and 0.001 < ps[2] < 0.05:
            axial_um = ps[2] * 1000.0
        if ps[1] and 0.001 < ps[1] < 2.0 and n_b > 0:
            scan_width_mm = ps[1] * n_b

    return {
        "axial_um": max(axial_um, 0.5),
        "lateral_um": max(lateral_um, 1.0),
        "scan_width_mm": max(scan_width_mm, 1.0),
    }


# ── Medición reutilizable (sin dependencia de oct_converter) ─────────────────


def measure_fda_volume(
    contours: dict,
    n_b: int,
    n_a: int,
    pixel_spacing: list | None,
    laterality: str,
    cd_base: dict,
    sqi_mean: float | None = None,
    label: str = "",
    require_laterality: bool = False,
    etdrs_policy=None,
) -> OCTClinicalData | None:
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
        etdrs_policy:       EtdrsCoveragePolicy | None. None = comportamiento histórico (sin comprobar cobertura);
                            los adaptadores nuevos pasan una política (transducin.etdrs_policy).
        require_laterality: si True y la lateralidad no es R/L, no mide
                       (retorna None) en vez de asumir "R" como el extractor legado.
    """
    from transducin.etdrs_policy import compute_etdrs_with_policy, unavailable_summary
    from transducin.revo_opt_reader import (
        compute_gcl_ipl,
        compute_rnfl_sectors,
    )

    scan_type = _detect_scan_type_fda(n_b, n_a, set(contours.keys()))

    ilm = _validate_layer(contours.get("ILM"), n_b, n_a)
    bm = _validate_layer(contours.get("BM"), n_b, n_a)
    rnfl_gcl = _validate_layer(contours.get("RNFL_GCL"), n_b, n_a)
    ipl_inl = _validate_layer(contours.get("IPL_INL"), n_b, n_a)

    if ilm is None:
        logger.debug("FDA: ILM no disponible en %s — saltando", label)
        return None

    geo = _geometry_from_pixel_spacing(pixel_spacing or [], n_b)
    params = {
        "axial_um": geo["axial_um"],
        "lateral_um": geo["lateral_um"],
        "scan_width_mm": geo["scan_width_mm"],
        "n_bscans": n_b,
        "n_ascans": n_a,
    }

    lat = str(laterality or "").upper()
    if lat not in ("R", "L"):
        if require_laterality:
            logger.warning("FDA %s: lateralidad desconocida — no se miden sectores", label)
            return None
        lat = "R"

    logger.info(
        "FDA volumen %s: %d B-scans × %d A-scans, tipo=%s lat=%s axial=%.2fµm/px",
        label,
        n_b,
        n_a,
        scan_type,
        lat,
        params["axial_um"],
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
            study_type="rnfl_circle",
            rnfl=prnfl,
            sqi_mean=sqi_mean,
            extraction_confidence="confirmed",
            measurement_provenance="vendor_contour_derived",
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

    etdrs, cov_report = compute_etdrs_with_policy(ilm, bm, params, laterality=lat, policy=etdrs_policy)
    cmt_um = round(float(etdrs.C), 1) if (etdrs and etdrs.C is not None) else None

    rnfl = None
    if rnfl_gcl is not None and ilm.shape == rnfl_gcl.shape:
        rnfl = compute_rnfl_sectors(ilm, rnfl_gcl, params, laterality=lat)

    gcl_ipl = None
    if rnfl_gcl is not None and ipl_inl is not None and rnfl_gcl.shape == ipl_inl.shape:
        gcl_ipl = compute_gcl_ipl(rnfl_gcl, ipl_inl, params, laterality=lat)

    cd = OCTClinicalData(
        **cd_base,
        study_type=scan_type,
        cmt_um=cmt_um,
        etdrs_grid=etdrs,
        rnfl=rnfl,
        gcl_ipl=gcl_ipl,
        gcl_avg_um=round(float(gcl_ipl.global_avg), 1) if (gcl_ipl and gcl_ipl.global_avg is not None) else None,
        sqi_mean=sqi_mean,
        extraction_confidence="confirmed" if cmt_um is not None else "assumed",
        measurement_provenance="vendor_contour_derived",
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

    cov_note = unavailable_summary(cov_report)
    if cov_note:
        cd.add_note(cov_note)

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
