# transducin/spectralis_extractor.py
# SPDX-License-Identifier: Apache-2.0
#
# Extractor de mediciones clínicas OCT para Heidelberg Spectralis (.e2e).
#
# Usa oct-converter (E2E reader) para leer el volumen + contours de capas,
# y reutiliza las funciones de cómputo de revo_opt_reader:
#   compute_etdrs()       → CMT + ETDRS 9 sectores
#   compute_rnfl_sectors()→ mRNFL S/I/N/T (macular)
#   compute_gcl_ipl()     → mGCIPL S/I/N/T
#
# Mapeo de contour IDs en formato E2E (fuente: uocte wiki + LibE2E):
#   contour0 = ILM        (Internal Limiting Membrane)
#   contour1 = RNFL_GCL   (RNFL / GCL boundary)
#   contour2 = GCL_IPL    (GCL / IPL boundary)
#   contour3 = IPL_INL    (IPL / INL boundary)
#   contour4 = INL_OPL
#   contour5 = OPL_ONL
#   contour6 = ELM
#   contour7 = MZ_EZ
#   contour8 = IZ_RPE
#   contour9 = BM         (Bruch's Membrane)
#   contour10 = CSI
#
# Escalas (pixel_spacing viene de OCTVolumeWithMetaData):
#   pixel_spacing[0] = scalex   (lateral x, mm/A-scan; manual default 0.01)
#   pixel_spacing[1] = scaley   (AXIAL, mm/px; leído del archivo — fiable)
#   pixel_spacing[2] = slice_th (inter-frame y, mm/bscan; manual default 0.05)
#
# La geometría real del scan se extrae de bscan_metadata.posX1/posX2 cuando
# está disponible, y se usa para calcular lateral_um y scan_width_mm precisos.

from __future__ import annotations

import logging
from itertools import pairwise
from pathlib import Path

import numpy as np

from transducin.clinical_data import OCTClinicalData, RNFLSectors
from transducin.noel_id import is_valid_noel

logger = logging.getLogger("transducin.spectralis")


def _extract_e2e_scan_quality(filepath: Path, byte_skip: int) -> float | None:
    """Extrae el imgQuality medio de todos los B-scans en un archivo .e2e.

    El chunk type 10004 (bscan_metadata) contiene imgQuality como Float32l
    en posición byte 84 dentro del payload de 104 bytes (estructura conocida).
    Rango esperado: 0.0–1.0 (Spectralis; valores típicos 0.15–0.35 en escala interna).

    Returns:
        Media de imgQuality sobre todos los B-scans encontrados, o None si no
        se pudo leer ningún valor válido.
    """
    import struct as _struct

    try:
        with open(filepath, "rb") as f:
            # Leer header principal (36 bytes) + main_directory (52 bytes)
            f.seek(byte_skip)
            header_raw = f.read(36)
            if len(header_raw) < 36:
                return None
            f.seek(byte_skip + 36)
            main_dir_raw = f.read(52)
            if len(main_dir_raw) < 52:
                return None

            # Parsear current y prev del main_directory (offsets 12+4*10=52 → current=int32 @44)
            # main_directory_structure: magic2(12) + version(4) + unknown(20) + num_entries(4) + current(4) + prev(4)
            current = _struct.unpack_from("<I", main_dir_raw, 44)[0]

            # Construir directory_stack (linked list de directorios)
            directory_stack = []
            visited = set()
            while current != 0 and current not in visited:
                directory_stack.append(current)
                visited.add(current)
                f.seek(current + byte_skip)
                raw = f.read(52)
                if len(raw) < 52:
                    break
                current = _struct.unpack_from("<I", raw, 44)[0]  # prev

            # Construir chunk_stack desde los sub_directory entries
            # sub_directory_structure: pos(4)+start(4)+size(4)+unknown(4)+pid(4)+sid(4)+serid(4)+slid(4)+u2(2)+u3(2)+type(4)+u4(4) = 44 bytes
            chunk_stack = []
            for pos in directory_stack:
                f.seek(pos + byte_skip)
                raw = f.read(52)
                if len(raw) < 52:
                    continue
                num_entries = _struct.unpack_from("<I", raw, 40)[0]
                for _ in range(num_entries):
                    entry_raw = f.read(44)
                    if len(entry_raw) < 44:
                        break
                    entry_pos = _struct.unpack_from("<I", entry_raw, 0)[0]
                    entry_start = _struct.unpack_from("<I", entry_raw, 4)[0]
                    entry_size = _struct.unpack_from("<I", entry_raw, 8)[0]
                    if entry_start > entry_pos:
                        chunk_stack.append((entry_start, entry_size))

            # Extraer imgQuality de chunks tipo 10004
            # chunk_structure: magic3(12)+unknown(4)+unknown2(4)+pos(4)+size(4)+unknown3(4)+pid(4)+sid(4)+serid(4)+slid(4)+ind(2)+unknown4(2)+type(4)+unknown5(4) = 60 bytes
            # bscan_metadata: payload de 104 bytes; imgQuality = Float32l @ byte 84
            qualities: list[float] = []
            for start, _size in chunk_stack:
                f.seek(start + byte_skip)
                chunk_hdr = f.read(60)
                if len(chunk_hdr) < 60:
                    continue
                chunk_type = _struct.unpack_from("<I", chunk_hdr, 52)[0]
                if chunk_type == 10004:
                    payload = f.read(104)
                    if len(payload) >= 88:
                        q = _struct.unpack_from("<f", payload, 84)[0]
                        if 0.0 <= q <= 1.0:
                            qualities.append(q)

            if not qualities:
                return None
            return round(float(np.mean(qualities)), 4)
    except Exception as exc:
        logger.debug("E2E: no se pudo extraer imgQuality de %s: %s", filepath.name, exc)
        return None


# Valores por defecto Spectralis si no hay metadata de geometría
_DEFAULT_AXIAL_UM = 3.87  # µm/px axial (Spectralis HR 1024px, ~3.97mm depth)
_DEFAULT_LATERAL_UM = 11.72  # µm/A-scan (512 A-scans, 6mm FOV)
_DEFAULT_SCAN_WIDTH_Y = 6.0  # mm (macular cube 20°×20° ≈ 6×6mm)

# Detección de tipo de scan por dimensiones (Spectralis)
# Circle scans para pRNFL: 1 B-scan, 768 o 1536 A-scans
_CIRCLE_ASCANS = {384, 512, 768, 1024, 1536}


def _detect_scan_type(n_bscans: int, n_ascans: int) -> str:
    """Infiere el tipo de scan Spectralis por dimensiones del volumen.

    Returns: "macular" | "rnfl_circle" | "optic_nerve" | "hd_line" | "unknown"
    """
    if n_bscans == 1 and n_ascans in _CIRCLE_ASCANS:
        return "rnfl_circle"
    if n_bscans >= 9 and n_ascans >= 256:
        # Macular cube típico: 19-97 B-scans; ONH: similar pero posición distinta
        # Sin metadata de posición no distinguimos macular de ONH por dimensiones solas
        return "macular"
    if 2 <= n_bscans <= 8 and n_ascans >= 512:
        return "hd_line"
    return "unknown"


def _compute_prnfl_circle(
    ilm: np.ndarray,
    rnfl_gcl: np.ndarray,
    axial_um: float,
    laterality: str,
) -> RNFLSectors | None:
    """Extrae pRNFL promedio y sectores S/I/N/T desde un circle scan.

    El circle scan de Spectralis tiene 1 B-scan × N A-scans en orden TSNIT:
      T=temporal, S=superior, N=nasal, I=inferior, T=temporal
    Los A-scans comienzan en la posición temporal (0°) y recorren 360°.

    Sectores (90° cada uno, igual para OD y OS respecto a la posición temporal):
      T: A-scans 0..N//4-1  y  3*N//4..N-1  (combinados, 180° temporal)
      S: A-scans N//4 .. N//2-1
      N: A-scans N//2 .. 3*N//4-1
      I: A-scans 3*N//4 .. N-1  → corregido más abajo

    Para OS la nasal y temporal se invierten en posición anatómica pero
    el orden TSNIT del scan sigue siendo el mismo — corrección aplicada
    en compute_rnfl_sectors downstream, aquí los sectores son geométricos.
    """
    if ilm.shape != rnfl_gcl.shape or ilm.ndim != 2:
        return None
    n_b, n_a = ilm.shape
    if n_b != 1:
        return None

    # Grosor NFL por A-scan en µm (RNFL_GCL está más abajo que ILM → positivo)
    thickness = (rnfl_gcl[0] - ilm[0]) * axial_um
    thickness = np.where(np.isfinite(thickness) & (thickness > 0), thickness, np.nan)

    if np.all(np.isnan(thickness)):
        return None

    global_avg = float(np.nanmean(thickness))

    # Sectores en orden TSNIT — cuadrantes de 90°
    q = n_a // 4
    t_seg = np.concatenate([thickness[:q], thickness[3 * q :]])  # temporal (inicio+fin)
    s_seg = thickness[q : 2 * q]  # superior
    n_seg = thickness[2 * q : 3 * q]  # nasal
    i_seg = thickness[3 * q :][:q]  # inferior (simplificado)

    def _s(arr):
        v = float(np.nanmean(arr)) if np.any(np.isfinite(arr)) else None
        return round(v, 1) if v is not None else None

    # Para OS nasal y temporal se intercambian anatómicamente
    if laterality == "L":
        sup, inf, nas, tem = _s(s_seg), _s(i_seg), _s(t_seg), _s(n_seg)
    else:
        sup, inf, nas, tem = _s(s_seg), _s(i_seg), _s(n_seg), _s(t_seg)

    return RNFLSectors(
        global_avg=round(global_avg, 1),
        superior=sup,
        inferior=inf,
        nasal=nas,
        temporal=tem,
    )


# ── Helpers internos ─────────────────────────────────────────────────────────


def _build_layer_map(
    contours: dict,
    key: str,
) -> np.ndarray | None:
    """Convierte contours[key] (list of 1D arrays) en mapa 2D [n_bscans × n_ascans].

    Valores NaN donde la segmentación falló o el array es None.
    Retorna None si el contour no existe o tiene menos de 3 B-scans válidos.
    """
    if key not in contours:
        return None

    slices = contours[key]
    valid = [s for s in slices if s is not None and np.any(np.isfinite(s))]
    if len(valid) < 3:
        return None

    n_b = len(slices)
    n_a = len(valid[0])

    arr = np.full((n_b, n_a), np.nan, dtype=np.float32)
    for i, s in enumerate(slices):
        if s is not None and len(s) == n_a:
            arr[i] = s

    return arr


# Mapas candidatos de ID de contorno → capa. El orden de la lista es la prioridad
# cuando más de uno es geométricamente válido.
#   oct_converter_contiguous: numeración contigua de la documentación de oct-converter
#       (ILM=0, RNFL/GCL=1, IPL/INL=3, BM=9). Es el mapa histórico de Transducin.
#   heyex_ids: observado en archivos reales de un Spectralis (2026-11-11): volúmenes con
#       contornos 0..6, 8, 14, 15, 16 y SIN contorno 9; el más profundo es el 1, así que
#       ILM=0, BM=1, RNFL/GCL=2, IPL/INL=4. Es una HIPÓTESIS respaldada por geometría
#       (orden de profundidades y ~300 µm ILM→BM), no por documentación de Heidelberg;
#       por eso las mediciones con este mapa se marcan extraction_confidence="assumed".
_E2E_LAYER_MAPS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "oct_converter_contiguous",
        {"ilm": "contour0", "rnfl_gcl": "contour1", "ipl_inl": "contour3", "bm": "contour9"},
    ),
    (
        "heyex_ids",
        {"ilm": "contour0", "bm": "contour1", "rnfl_gcl": "contour2", "ipl_inl": "contour4"},
    ),
)

# Orden anatómico superficial → profundo; cada capa presente debe ser más profunda
# (en media) que la anterior.
_E2E_LAYER_ORDER = ("ilm", "rnfl_gcl", "ipl_inl", "bm")


def _resolve_e2e_layers(contours: dict) -> tuple[dict | None, str | None]:
    """Elige el mapa de IDs de contorno cuya geometría es consistente.

    Devuelve ({"ilm", "bm", "rnfl_gcl", "ipl_inl"} → mapas 2D | None, nombre_del_mapa),
    o (None, None) si ningún mapa tiene ILM y BM, o si el orden de profundidades no es
    el anatómico (ILM < RNFL/GCL < IPL/INL < BM). Nunca se mide con un mapa dudoso.
    """
    for name, keys in _E2E_LAYER_MAPS:
        layers = {layer: _build_layer_map(contours, key) for layer, key in keys.items()}
        if layers["ilm"] is None or layers["bm"] is None:
            continue
        depths = [float(np.nanmean(layers[layer])) for layer in _E2E_LAYER_ORDER if layers[layer] is not None]
        if any(np.isnan(depths)) or any(a >= b for a, b in pairwise(depths)):
            continue
        return layers, name
    return None, None


def _geometry_from_metadata(metadata: dict, n_ascans: int, n_bscans: int) -> dict:
    """Extrae escalas físicas reales desde bscan_data.posX1/posX2/posY1.

    Retorna dict con axial_um, lateral_um, scan_width_mm.
    Si metadata no tiene datos útiles usa defaults Spectralis.
    """
    axial_um = _DEFAULT_AXIAL_UM
    lateral_um = _DEFAULT_LATERAL_UM
    scan_width_mm = _DEFAULT_SCAN_WIDTH_Y

    bscan_data = metadata.get("bscan_data", []) if metadata else []
    if not bscan_data:
        return {"axial_um": axial_um, "lateral_um": lateral_um, "scan_width_mm": scan_width_mm}

    # Axial scale — scaley del archivo (fiable)
    scaley_vals = [bd.get("scaley", 0.0) for bd in bscan_data if 0.001 < bd.get("scaley", 0.0) < 0.1]
    if scaley_vals:
        axial_um = float(np.median(scaley_vals)) * 1000.0

    # Lateral X — desde posX1/posX2 (scan width en A-scan direction)
    widths_x = []
    posY_vals = []
    for bd in bscan_data:
        px1 = bd.get("posX1", 0.0)
        px2 = bd.get("posX2", 0.0)
        w = abs(float(px2) - float(px1))
        if 0.5 < w < 20.0:  # rango razonable mm
            widths_x.append(w)
        py1 = bd.get("posY1", 0.0)
        if py1 is not None:
            posY_vals.append(float(py1))

    if widths_x and n_ascans > 0:
        scan_x_mm = float(np.median(widths_x))
        lateral_um = scan_x_mm * 1000.0 / n_ascans

    # Scan width Y — rango de posY
    if len(posY_vals) >= 2:
        y_range = abs(max(posY_vals) - min(posY_vals))
        if 0.5 < y_range < 20.0:
            scan_width_mm = y_range

    return {
        "axial_um": max(axial_um, 0.5),  # sanity: ≥ 0.5 µm/px
        "lateral_um": max(lateral_um, 1.0),  # sanity: ≥ 1 µm/A-scan
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


def measure_e2e_volume(
    contours: dict,
    pixel_spacing: list | None,
    metadata: dict | None,
    laterality: str,
    cd_base: dict,
    sqi_mean: float | None = None,
    label: str = "",
    require_laterality: bool = False,
    etdrs_policy=None,
) -> OCTClinicalData | None:
    """Calcula mediciones clínicas de UN volumen E2E a partir de sus contornos.

    Es la lógica de ``extract_from_e2e`` aislada de la lectura del archivo, de modo
    que la comparten el extractor (oct_converter) y el reader vendorizado
    (``transducin.readers.e2e_reader``).

    Args:
        contours:           {"contour0": [arr|None, ...], ...} (un arr por B-scan).
        pixel_spacing:      [scalex, scaley_axial_mm, slice_th] o None.
        metadata:           dict con "bscan_data" (lista de dicts posX1/posX2/scaley).
        laterality:         "R" | "L" | otro.
        cd_base:            kwargs de OCTClinicalData SIN ``laterality``.
        etdrs_policy:       EtdrsCoveragePolicy | None. None = comportamiento histórico (sin comprobar cobertura);
                            los adaptadores nuevos pasan una política (transducin.etdrs_policy).
        require_laterality: si True y la lateralidad no es R/L, no mide (retorna
                            None) en lugar de asumir "R". El extractor legado
                            conserva el comportamiento histórico (asume "R").

    Returns:
        OCTClinicalData con mediciones, o None si no hay nada medible.
    """
    from transducin.etdrs_policy import compute_etdrs_with_policy, unavailable_summary
    from transducin.revo_opt_reader import (
        compute_gcl_ipl,
        compute_rnfl_sectors,
    )

    layers, layer_map_name = _resolve_e2e_layers(contours)
    if layers is None:
        logger.debug("E2E: ILM/BM no disponibles o con geometría inconsistente — saltando volumen")
        return None
    ilm, bm = layers["ilm"], layers["bm"]
    rnfl_gcl, ipl_inl = layers["rnfl_gcl"], layers["ipl_inl"]
    # El mapa histórico queda "confirmed"; el inferido por geometría, "assumed".
    layer_confidence = "confirmed" if layer_map_name == "oct_converter_contiguous" else "assumed"
    layer_map_note = f"Mapeo de capas E2E: {layer_map_name}" + (
        ""
        if layer_confidence == "confirmed"
        else " (hipótesis validada por orden geométrico, no por documentación del fabricante)"
    )

    n_b, n_a = ilm.shape
    scan_type = _detect_scan_type(n_b, n_a)

    # ── Geometría física ──────────────────────────────────────────────────
    ps = pixel_spacing or []
    if len(ps) >= 2 and 0.001 < ps[1] < 0.1:
        axial_um_file = ps[1] * 1000.0
    else:
        axial_um_file = None

    geo = _geometry_from_metadata(metadata or {}, n_a, n_b)
    if axial_um_file is not None:
        geo["axial_um"] = axial_um_file

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
            logger.warning("E2E %s: lateralidad desconocida — no se miden sectores", label)
            return None
        lat = "R"

    logger.info(
        "E2E volumen %s: %d B-scans × %d A-scans, tipo=%s lat=%s, axial=%.2fµm/px",
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
        if rnfl_gcl is None:
            logger.debug("E2E circle: sin contour1 (RNFL_GCL) — saltando")
            return None
        prnfl = _compute_prnfl_circle(ilm, rnfl_gcl, params["axial_um"], lat)
        if prnfl is None or prnfl.global_avg is None:
            logger.debug("E2E circle: pRNFL no calculable — saltando")
            return None
        cd = OCTClinicalData(
            **cd_base,
            study_type="rnfl_circle",
            rnfl=prnfl,
            sqi_mean=sqi_mean,
            extraction_confidence=layer_confidence,
            measurement_provenance="vendor_contour_derived",
        )
        cd.add_note(layer_map_note)
        cd.add_note(
            f"CONFIRMED: pRNFL={prnfl.global_avg:.1f}µm "
            f"S={prnfl.superior} I={prnfl.inferior} "
            f"N={prnfl.nasal} T={prnfl.temporal}µm "
            f"(circle scan {n_a} A-scans, axial={params['axial_um']:.2f}µm/px)"
        )
        return cd if cd.has_measurements() else None

    # ── Macular / ONH cube → CMT + ETDRS + mRNFL + mGCIPL ────────────────
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
        study_type=scan_type,  # "macular" o "unknown"
        cmt_um=cmt_um,
        etdrs_grid=etdrs,
        rnfl=rnfl,
        gcl_ipl=gcl_ipl,
        gcl_avg_um=round(float(gcl_ipl.global_avg), 1) if (gcl_ipl and gcl_ipl.global_avg is not None) else None,
        sqi_mean=sqi_mean,
        extraction_confidence=layer_confidence if cmt_um is not None else "assumed",
        measurement_provenance="vendor_contour_derived",
    )
    cd.add_note(layer_map_note)

    if cmt_um is not None:
        cd.add_note(
            f"CONFIRMED: CMT={cmt_um}µm desde ILM/BM Spectralis E2E "
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
        logger.warning("  Sin mediciones para volumen en %s", label)
        return None
    logger.info(
        "  OK: CMT=%s mRNFL=%s mGCIPL=%s",
        f"{cmt_um}µm" if cmt_um else "N/A",
        f"{rnfl.global_avg:.1f}µm" if rnfl else "N/A",
        f"{gcl_ipl.global_avg:.1f}µm" if gcl_ipl else "N/A",
    )
    return cd


# ── Extractor principal ──────────────────────────────────────────────────────


def extract_from_e2e(
    filepath: Path,
    noel_id: str = "",
) -> list[OCTClinicalData]:
    """Extrae OCTClinicalData desde un archivo .e2e Spectralis.

    Para cada volumen OCT con contours de capas (macular cube) calcula:
      - CMT (espesor central macular)
      - ETDRS 9 sectores
      - mRNFL S/I/N/T (RNFL macular)
      - mGCIPL S/I/N/T (GCL+IPL macular)

    Args:
        filepath:  ruta al archivo .e2e
        noel_id:   NOEL ID del paciente (si disponible desde filename)

    Returns:
        Lista de OCTClinicalData, uno por volumen macular con mediciones.
    """
    from oct_converter.readers.e2e import E2E

    filepath = Path(filepath)
    results: list[OCTClinicalData] = []

    try:
        reader = E2E(str(filepath))
        volumes = reader.read_oct_volume()
    except Exception as exc:
        logger.error("E2E: no se pudo leer %s: %s", filepath.name, exc)
        return results

    if not volumes:
        logger.warning("E2E: sin volúmenes en %s", filepath.name)
        return results

    # SQI: extraer imgQuality medio del archivo una sola vez
    sqi_mean = _extract_e2e_scan_quality(filepath, reader.byte_skip)
    if sqi_mean is not None:
        logger.info("E2E SQI: imgQuality_mean=%.4f (%s)", sqi_mean, filepath.name)

    # Intentar extraer NOEL ID desde el patient_id del archivo si no viene del filename
    for vol in volumes:
        pid = getattr(vol, "patient_id", None) or ""
        if not noel_id and pid and is_valid_noel(str(pid)):
            noel_id = str(pid)
            break

    # Metadata global (escalas físicas)
    try:
        metadata = reader.read_all_metadata()
    except Exception:
        metadata = {}

    for vol in volumes:
        contours = getattr(vol, "contours", None)
        if not contours:
            logger.debug("E2E: volumen sin contours — saltando")
            continue

        cd_base = {
            "noel_id": noel_id,
            "vendor": "heidelberg_spectralis",
            "source_file": str(filepath),
            "study_date": _study_date(vol),
            "study_time": "",
            "patient_name": _patient_name(vol),
            "patient_dob": str(getattr(vol, "patient_dob", "") or ""),
        }
        cd = measure_e2e_volume(
            contours,
            getattr(vol, "pixel_spacing", None),
            metadata,
            getattr(vol, "laterality", ""),
            cd_base,
            sqi_mean,
            label=filepath.stem,
        )
        if cd is not None:
            results.append(cd)

    return results
