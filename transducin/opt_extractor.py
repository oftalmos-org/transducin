# transducin/opt_extractor.py
# SPDX-License-Identifier: Apache-2.0
#
# Extractor de OCTClinicalData desde archivos .opt del Revo FC130 (Optopol).
#
# ═══════════════════════════════════════════════════════════════════════════
# ANÁLISIS DEL FORMATO BINARIO .opt (Revo FC130)
# ═══════════════════════════════════════════════════════════════════════════
#
# Estructura contenedor:
#   Secuencia de chunks: magic(4) + name_len(4) + name(n) + null(1) +
#                        checksum(4) + data_sz(4) + data_sz_dup(4) + data
#   Magic principal: a5 a5 a5 ff
#   Magics alternativos en sub-chunks: 55 a5 aa ff, 5a 5a 5a ff
#
# Chunks top-level identificados:
#   PATIENT.DAT  — datos del paciente
#     Status: ENCRIPTADO — no hay plaintext legible en ninguna prueba
#     Contiene sub-chunk "PATIENT" con datos demográficos cifrados
#   PARAMS.DAT   — parámetros de adquisición
#     Status: COMPRIMIDO con zlib (header 78 01 o 78 9c)
#     Contiene sub-chunk "PARAMS" con floats de dimensiones del scan
#   PREVIEW.DAT  — imagen de previsualización
#     Status: JPEG comprimido (~580KB típico)
#   OCTPARAMS / TRAJ / FNDSPREVIEWPARAMS — sub-params adicionales
#
# ═══════════════════════════════════════════════════════════════════════════
# ESTRATEGIA DE EXTRACCIÓN — 3 niveles de confianza
# ═══════════════════════════════════════════════════════════════════════════
#
# CONFIRMED  — extraídos y validados del filename (fuente fiable):
#   laterality   (OD→R, OS→L)
#   study_date   (YYYYMMDD del timestamp en nombre)
#   study_time   (HHMMSS)
#   study_type   (OCT, BMETR, Color_fundus, etc.)
#   patient_name (apellidos + nombre del filename)
#   noel_id      (si presente como prefijo XXXX99999999)
#
# ASSUMED    — inferidos de PARAMS.DAT descomprimido (sin validación completa):
#   scan_dimensions (ancho, alto, n_slices) — arrays de floats en el binario
#   CMT/RNFL — NO disponibles en los archivos analizados (no confirmado)
#
# UNKNOWN    — no encontrados:
#   patient_dob  (filename sin NOEL prefix no la incluye; PATIENT.DAT encriptado)
#   cmt_um       (no localizado en PARAMS.DAT descomprimido)
#   etdrs_grid   (no localizado)
#   rnfl         (no localizado)

from __future__ import annotations

import logging
import re
import struct
import zlib
from pathlib import Path
from typing import Optional

from transducin.clinical_data import OCTClinicalData
from transducin.noel_id import is_valid_noel

logger = logging.getLogger(__name__)

# ── Constantes del formato .opt ─────────────────────────────────────────────

_MAGIC_MAIN = b"\xa5\xa5\xa5\xff"
_MAGIC_ALT1 = b"\x55\xa5\xaa\xff"
_MAGIC_ALT2 = b"ZZZ\xff"
_ALL_MAGIC  = {_MAGIC_MAIN, _MAGIC_ALT1, _MAGIC_ALT2}

# ── Parsing del filename Revo FC130 ─────────────────────────────────────────
# Formatos observados (el TYPE puede contener underscores, e.g. "Color_fundus"):
#   Sin NOEL: APELLIDOS_NOMBRES_YYYYMMDD_HHMMSS_LAT_TYPE.opt
#   Con NOEL: NOELID_APELLIDOS_NOMBRES_YYYYMMDD_HHMMSS_TYPE_LAT.opt  ← orden distinto

_NOEL_PREFIX = re.compile(r"^([A-Z]{3,4}\d{8})_(.+)$", re.IGNORECASE)
_DATE_RE     = re.compile(r"\d{8}")
_TIME_RE     = re.compile(r"\d{6}")
_LAT_RE      = re.compile(r"^(OD|OS|R|L)$", re.IGNORECASE)

_LAT_MAP  = {"OD": "R", "OS": "L", "R": "R", "L": "L"}

_TYPE_MAP = {
    "OCT":          "macular",
    "COLOR_FUNDUS": "fundus",
    "COLORFUNDUS":  "fundus",
    "BMETR":        "biometry",
    "ANGIO":        "angio",
    "TOPO":         "anterior_segment",
    "ANTERIOR":     "anterior_segment",
    "RNFL":         "rnfl",
    "OPTIC":        "optic_nerve",
}

_FILENAME_KEYWORDS: dict[str, str] = {
    "biometr": "biometry",
    "bmetr":   "biometry",
    "calculo": "biometry",
    "lio":     "biometry",
    "topo":    "anterior_segment",
    "topogr":  "anterior_segment",
    "macula":  "macular",
    "nervio":  "optic_nerve",
    "nerve":   "optic_nerve",
    "optic":   "optic_nerve",
    "disco":   "optic_nerve",
    "disc":    "optic_nerve",
    "angio":   "angio",
    "wide":    "wide_field",
    "ultra":   "ultra_wide",
}


def _map_study_type(raw_type: str) -> str:
    key = raw_type.upper().replace(" ", "_").replace("-", "_")
    return _TYPE_MAP.get(key, raw_type.lower())


# ── NOEL ID cross-reference index ────────────────────────────────────────────
# Scans sibling .OPT filenames to build {patient_name → noel_id} lookup.
# This resolves NOEL IDs for files without the prefix, as long as at least
# one file for the same patient has it (e.g. fundus exports often do).

def build_noel_index(folder: str | Path, include_processed: bool = False) -> dict[str, str]:
    """Builds a {patient_name → noel_id} lookup from all .OPT filenames in folder.

    Scans recursively, extracts NOEL prefix where present, and maps the
    patient name (apellidos_nombres) to the NOEL ID. Files without prefix
    are skipped — they become consumers of this index, not contributors.

    Args:
        folder: directory to scan (recursive).
        include_processed: if True, also scan processed/ subdirectories.

    Returns:
        dict mapping normalized patient names to NOEL IDs.
    """
    folder = Path(folder)
    index: dict[str, str] = {}

    for opt_file in folder.rglob("*.opt"):
        if not include_processed and "processed" in opt_file.parts:
            continue
        parsed = _parse_revo_filename(opt_file.name)
        if not parsed or not parsed["noel"]:
            continue
        # Files with NOEL prefix but no patient name (e.g. NEDI20160622_20260409_OD_FUNDUS)
        # can't contribute to the name→noel mapping — skip silently
        if not parsed["apellidos"] and not parsed["nombres"]:
            continue
        # Normalize name: "JAURRIETA HINOJOS_JESUS NOEL" → uppercase key
        name_key = f"{parsed['apellidos']}_{parsed['nombres']}".upper()
        if name_key not in index:
            index[name_key] = parsed["noel"]
            logger.debug("NOEL index: %s -> %s", name_key, parsed["noel"])

    logger.info("NOEL index: %d pacientes con NOEL ID en %s", len(index), folder)
    return index


def _parse_revo_filename(fname: str) -> Optional[dict]:
    """Parsea el filename del Revo FC130 de forma robusta.

    Maneja dos órdenes de los campos finales:
      ..._LAT_TYPE.opt   (archivos sin NOEL prefix)
      ..._TYPE_LAT.opt   (archivos con NOEL prefix, donde TYPE puede tener '_')
    """
    stem = fname
    if stem.lower().endswith(".opt"):
        stem = stem[:-4]

    # Detectar NOEL prefix
    noel_id = ""
    m = _NOEL_PREFIX.match(stem)
    if m and is_valid_noel(m.group(1)):
        noel_id = m.group(1).upper()
        stem = m.group(2)

    # Separar por '_' y encontrar fecha (8 dígitos) y hora (6 dígitos)
    parts = stem.split("_")
    date_idx = time_idx = -1
    for i, p in enumerate(parts):
        if _DATE_RE.fullmatch(p) and date_idx == -1:
            date_idx = i
        elif _TIME_RE.fullmatch(p) and date_idx != -1 and time_idx == -1:
            time_idx = i

    if date_idx == -1:
        return None  # no es un filename Revo reconocible

    if time_idx == -1:
        # No time field — e.g. "SILT20160101_20260409_OD_FUNDUS" or
        # "SILT19630101_SYNTHETIC_PATIENT_20260409_OD_FUNDUS" (NOEL + [name] + date + lat + type)
        if not noel_id:
            return None
        date = parts[date_idx]
        # Extract patient name from parts before date (if any)
        if date_idx >= 2:
            apellidos = "_".join(parts[:date_idx-1])
            nombres   = parts[date_idx-1]
        elif date_idx == 1:
            apellidos = ""
            nombres   = parts[0]
        else:
            apellidos = ""
            nombres   = ""
        if not apellidos and not nombres:
            logger.warning("Filename sin hora ni nombre de paciente (solo NOEL+fecha): %s", fname)
        suffix_parts = parts[date_idx+1:]
        lat = ""
        raw_type = ""
        if suffix_parts:
            lat_indices = [i for i, p in enumerate(suffix_parts) if _LAT_RE.fullmatch(p)]
            if lat_indices:
                lat_idx = lat_indices[0]
                lat = _LAT_MAP.get(suffix_parts[lat_idx].upper(), suffix_parts[lat_idx])
                type_parts = [p for i, p in enumerate(suffix_parts) if i != lat_idx]
                while type_parts and (_DATE_RE.fullmatch(type_parts[-1]) or _TIME_RE.fullmatch(type_parts[-1])):
                    type_parts.pop()
                raw_type = "_".join(type_parts)
        return {
            "noel":      noel_id,
            "apellidos": apellidos,
            "nombres":   nombres,
            "date":      date,
            "time":      "",
            "lat":       lat,
            "type":      raw_type,
        }

    date = parts[date_idx]
    time = parts[time_idx]

    # Reconstruir apellidos/nombres: todo lo que hay entre (noel stripped) y fecha
    apellidos = "_".join(parts[:date_idx-1])
    nombres   = parts[date_idx-1] if date_idx > 0 else ""

    # Los campos después de time son: LAT + TYPE o TYPE + LAT
    suffix_parts = parts[time_idx+1:]

    lat = ""
    raw_type = ""

    if not suffix_parts:
        return None

    # Estrategia: encontrar el token que sea OD/OS, el resto es tipo
    lat_indices = [i for i, p in enumerate(suffix_parts) if _LAT_RE.fullmatch(p)]
    if not lat_indices:
        return None

    lat_idx = lat_indices[0]
    lat = _LAT_MAP.get(suffix_parts[lat_idx].upper(), suffix_parts[lat_idx])
    type_parts = [p for i, p in enumerate(suffix_parts) if i != lat_idx]
    # Descartar timestamps de copia appended al final (ej. _20260402_011510)
    while type_parts and (_DATE_RE.fullmatch(type_parts[-1]) or _TIME_RE.fullmatch(type_parts[-1])):
        type_parts.pop()
    raw_type = "_".join(type_parts)

    return {
        "noel":     noel_id,
        "apellidos": apellidos,
        "nombres":   nombres,
        "date":      date,
        "time":      time,
        "lat":       lat,
        "type":      raw_type,
    }


# ── Parsing binario del contenedor .opt ─────────────────────────────────────

def _read_top_chunks(filepath: Path, max_bytes: int = 800_000) -> dict[str, bytes]:
    """Lee los chunks top-level del archivo .opt."""
    chunks: dict[str, bytes] = {}
    with open(filepath, "rb") as fh:
        data = fh.read(max_bytes)

    pos = 0
    while pos <= len(data) - 16:
        if data[pos:pos+4] not in _ALL_MAGIC:
            pos += 1
            continue
        start = pos
        pos += 4
        name_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if name_len == 0 or name_len > 64:
            pos = start + 1
            continue
        name = data[pos:pos+name_len].decode("ascii", errors="replace").rstrip("\x00")
        pos += name_len
        if pos < len(data) and data[pos] == 0:
            pos += 1
        pos += 4  # checksum
        if pos + 8 > len(data):
            break
        sz1, _ = struct.unpack_from("<II", data, pos)
        pos += 8
        if pos + sz1 > len(data):
            break
        chunks[name] = data[pos:pos+sz1]
        pos += sz1

    return chunks


def _decompress_params(raw: bytes) -> Optional[bytes]:
    """Intenta descomprimir el contenido de PARAMS.DAT con zlib."""
    # Buscar el sub-chunk PARAMS y descomprimir
    pos = 0
    while pos <= len(raw) - 16:
        if raw[pos:pos+4] not in _ALL_MAGIC:
            pos += 1
            continue
        start = pos
        pos += 4
        name_len = struct.unpack_from("<I", raw, pos)[0]
        pos += 4
        if name_len == 0 or name_len > 64:
            pos = start + 1
            continue
        name = raw[pos:pos+name_len].decode("ascii", errors="replace").rstrip("\x00")
        pos += name_len
        if pos < len(raw) and raw[pos] == 0:
            pos += 1
        pos += 4
        if pos + 8 > len(raw):
            break
        sz1, _ = struct.unpack_from("<II", raw, pos)
        pos += 8
        sub_data = raw[pos:pos+sz1]
        pos += sz1

        if name in ("PARAMS", "OCTPARAMS"):
            # El primer byte puede ser 00 (padding), luego el header zlib 78 xx
            for offset in range(min(4, len(sub_data))):
                candidate = sub_data[offset:]
                if len(candidate) > 2 and candidate[0] == 0x78:
                    try:
                        return zlib.decompress(candidate)
                    except zlib.error:
                        continue
    return None


def _extract_floats_near_strings(decompressed: bytes) -> dict[str, float]:
    """Busca floats float32 cerca de strings conocidos en datos descomprimidos."""
    results: dict[str, float] = {}
    keywords = {
        b"width": "width_mm",
        b"height": "height_mm",
        b"depth": "depth_mm",
        b"scale": "scale",
        b"pixel": "pixel_size",
    }
    for kw, label in keywords.items():
        idx = decompressed.lower().find(kw)
        if idx != -1:
            # Buscar float32 en los 20 bytes siguientes
            for off in range(idx, min(idx + 20, len(decompressed) - 4)):
                val = struct.unpack_from("<f", decompressed, off)[0]
                if 0.001 < abs(val) < 100.0:
                    results[label] = round(val, 4)
                    break
    return results


# ── API pública ─────────────────────────────────────────────────────────────

def extract_from_opt(
    filepath: str | Path,
    noel_index: Optional[dict[str, str]] = None,
) -> OCTClinicalData:
    """Extrae OCTClinicalData de un archivo .opt del Revo FC130.

    Args:
        filepath:   ruta al archivo .opt (solo lectura).
        noel_index: {patient_name → noel_id} lookup from build_noel_index().
                    If provided, resolves NOEL ID for files without prefix.

    Returns:
        OCTClinicalData con campos anotados por nivel de confianza.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(filepath)

    cd = OCTClinicalData(source_file=str(filepath))

    # ── Nivel 1: CONFIRMED — filename ───────────────────────────────────────
    fname = filepath.name
    g = _parse_revo_filename(fname)

    if g:
        cd.laterality   = g["lat"]
        cd.study_date   = g["date"]
        cd.study_time   = g["time"]
        cd.study_type   = _map_study_type(g["type"])
        cd.patient_name = f"{g['apellidos']}_{g['nombres']}" if g["apellidos"] else g["nombres"]
        cd.add_note("CONFIRMED: laterality, study_date, study_time, study_type, patient_name — desde filename")

        if g["noel"]:
            cd.noel_id = g["noel"]
            cd.add_note("CONFIRMED: noel_id — presente en filename como prefijo")
        elif noel_index:
            name_key = f"{g['apellidos']}_{g['nombres']}".upper()
            resolved = noel_index.get(name_key)
            if resolved:
                cd.noel_id = resolved
                cd.extraction_confidence = "assumed"
                cd.add_note(f"ASSUMED: noel_id={resolved} — resuelto via cross-reference de archivos hermanos")
            else:
                cd.add_note("UNKNOWN: noel_id — no en filename ni en cross-reference")
        else:
            cd.add_note("UNKNOWN: noel_id — no en filename y sin DOB para construirlo")

        logger.info("Filename parseado: %s | lat=%s type=%s date=%s noel=%s",
                    fname, cd.laterality, cd.study_type, cd.study_date, cd.noel_id or "(none)")
    else:
        cd.add_note("UNKNOWN: filename no coincide con patrón Revo FC130 esperado")
        logger.warning("Filename no parseado: %s", fname)

    # ── Nivel 0: keyword matching en filename stem ─────────────────────────────
    # Corre antes de chunk inference: keywords explícitos en el stem del archivo
    # (biometria, topografia, calculo_lio) tienen precedencia sobre la detección
    # genérica por chunks/dimensiones.
    if not cd.study_type:
        _stem_lower = filepath.stem.lower()
        for _kw, _ktype in _FILENAME_KEYWORDS.items():
            if _kw in _stem_lower:
                cd.study_type = _ktype
                cd.add_note(f"ASSUMED: study_type={_ktype} — keyword '{_kw}' en filename stem")
                logger.info("study_type inferido de keyword '%s': %s", _kw, _ktype)
                break

    # ── Nivel 1.5 / 1.6: laterality y study_type desde chunks ──────────────────
    # Single file-read pass handles both inferences when filename gives neither.
    # _need_dmarkers: override macular→optic_nerve when DMARKERS chunk is present,
    # even when filename already gave study_type="macular". Validated: site-B corpus
    # v11.5.x exports disc scans as _OCT (→ "macular") but includes DMARKERS chunk.
    _need_lat      = not cd.laterality
    _need_type     = not cd.study_type
    _need_dmarkers = cd.study_type == "macular"
    if _need_lat or _need_type or _need_dmarkers:
        try:
            from transducin.revo_opt_reader import parse_opt_chunks, parse_octparams
            _raw    = filepath.read_bytes()
            _chunks = parse_opt_chunks(_raw)
            _params = parse_octparams(_raw, _chunks)

            if _need_lat:
                # Tag 23 = posición horizontal del fóvea/disco en mm.
                # Positivo → OS (L), negativo → OD (R).
                # Validado contra 13/13 archivos REVO60 con ground truth de filename.
                _x = _params.get("scan_center_x_mm")
                if _x is not None and _x != 0.0:
                    cd.laterality = "L" if _x > 0 else "R"
                    cd.add_note(
                        f"ASSUMED: laterality={cd.laterality} — OCTPARAMS tag 23 "
                        f"scan_center_x_mm={_x:.4f} mm (positivo=OS, negativo=OD)"
                    )
                    logger.info(
                        "Lateralidad inferida de OCTPARAMS tag 23: %s (x=%.4f mm)",
                        cd.laterality, _x,
                    )

            if _need_type:
                # Precedencia: ANGPRV > DMARKERS > EYE+n_frames > FNDSRECO-sin-EYE.
                # DMARKERS = marcadores de disco → optic_nerve en REVO60 y REVO130,
                # validado contra 21.1.2 (OS/OD) y QRO REVO130 (191fr); ausente en macular.
                # EYE+n<100 es fallback para cubos ONH sin DMARKERS (ej. scans legacy).
                _n = _params.get("n_bscans") or 0
                if "ANGPRV" in _chunks:
                    _inferred = "angio"
                elif "DMARKERS" in _chunks:
                    _inferred = "optic_nerve"
                elif "EYE" in _chunks:
                    _inferred = "macular" if _n >= 100 else "optic_nerve"
                elif "FNDSRECO" in _chunks:
                    _inferred = "widefield"
                else:
                    _inferred = ""
                if _inferred:
                    cd.study_type = _inferred
                    cd.add_note(
                        f"ASSUMED: study_type={_inferred} — inferido de chunks "
                        f"(ANGPRV={'ANGPRV' in _chunks}, DMARKERS={'DMARKERS' in _chunks}, "
                        f"EYE={'EYE' in _chunks}, FNDSRECO={'FNDSRECO' in _chunks}, n_bscans={_n})"
                    )
                    logger.info(
                        "study_type inferido de chunks: %s "
                        "(ANGPRV=%s, DMARKERS=%s, EYE=%s, FNDSRECO=%s, n=%d)",
                        _inferred,
                        "ANGPRV" in _chunks,
                        "DMARKERS" in _chunks,
                        "EYE" in _chunks,
                        "FNDSRECO" in _chunks,
                        _n,
                    )
            elif _need_dmarkers and "DMARKERS" in _chunks:
                cd.study_type = "optic_nerve"
                cd.add_note(
                    "CONFIRMED: study_type overridden macular→optic_nerve — DMARKERS chunk present"
                )
                logger.info("study_type overridden macular→optic_nerve — DMARKERS chunk present")
        except Exception as e:
            logger.debug("Inferencia de lateralidad/study_type desde chunks fallida: %s", e)

    # ── Nivel 2: CONFIRMED — segmentación via revo_opt_reader ──────────────────
    # Detección de tipo por dimensiones: anula el tipo del filename si las
    # dimensiones indican un tipo distinto (ej. _OCT puede ser macular u ONH).
    if cd.study_type in ("macular", "rnfl", "oct", "optic_nerve", "angio", "hd_line", "wide_field"):
        try:
            from transducin.revo_opt_reader import (
                parse_opt_chunks, parse_octparams, extract_layer,
                extract_disc_center, compute_etdrs, compute_rnfl_sectors,
                compute_gcl_ipl, compute_peripapillary_rnfl,
            )
            raw_bytes = filepath.read_bytes()
            chunks    = parse_opt_chunks(raw_bytes)
            params    = parse_octparams(raw_bytes, chunks)

            n_frames = params.get("n_bscans")
            n_ascans = params.get("n_ascans")

            # Clasificación por dimensiones — valores verificados contra archivos reales.
            # n_bscans en OCTPARAMS = posiciones de escaneo (no T-chunks reales;
            # T-chunks = n_bscans × n_averages).
            #
            #   192 × 640              → optic_nerve (ONH cube, 6mm)
            #   320 × 320 (o 319×319)  → angio (OCTA 3mm, 320 T + 320 A chunks)
            #   n_ascans ≥ 4096        → ultra_wide (1×10240 14mm, 6×8192 16mm)
            #   n_bscans ≤ 8, ≥ 1024   → wide_field (5×1536 12mm, etc.)
            #   n_bscans ≤ 25, ≥ 512   → hd_line (18×1024 8mm, 21×1024 10mm)
            #   resto                  → sin reclasificación (macular 168×1024 etc.)
            if n_frames is not None and n_ascans is not None:
                if n_frames == 192 and n_ascans == 640:
                    new_type = "optic_nerve"
                elif n_frames in (319, 320) and n_ascans <= 320:
                    new_type = "angio"
                elif n_ascans >= 4096:
                    new_type = "ultra_wide"
                elif n_frames <= 8 and n_ascans >= 1024:
                    new_type = "wide_field"
                elif n_frames <= 25 and n_ascans >= 512:
                    new_type = "hd_line"
                else:
                    new_type = None

                if new_type is not None and cd.study_type != new_type:
                    old_type = cd.study_type
                    cd.study_type = new_type
                    cd.add_note(
                        f"CONFIRMED: study_type={new_type} — detectado por dimensiones "
                        f"{n_frames}fr × {n_ascans}px (reclasificado desde {old_type!r})"
                    )
                    logger.info(
                        "Tipo reclasificado %r → %r por dimensiones %dfr × %dpx",
                        old_type, new_type, n_frames, n_ascans,
                    )
        except Exception as e:
            logger.warning("Detección de dimensiones fallida para %s: %s", filepath.name, e)

    if cd.study_type in ("macular", "rnfl", "oct"):
        try:
            from transducin.revo_opt_reader import read_opt, compute_etdrs
            result = read_opt(filepath)

            # CMT
            cmt = result.get("cmt_um")
            if cmt is not None:
                cd.cmt_um = cmt
                cd.add_note(f"CONFIRMED: cmt_um={cmt:.1f} µm — mean(BM-TOP/ILM) central 1mm, axial {result['params']['axial_um']:.3f} µm/px")
                logger.info("CMT calculado: %.1f µm", cmt)
            else:
                cd.add_note("ASSUMED: capas NFL/BM presentes pero sin valores válidos en parche central")

            # SQI (Signal Quality Index)
            sqi = result.get("sqi")
            if sqi is not None and len(sqi) > 0:
                cd.sqi_mean = float(sqi.mean())
                n_bad = int((sqi < 0.5).sum())
                cd.add_note(
                    f"CONFIRMED: sqi_mean={cd.sqi_mean:.3f} "
                    f"(n={len(sqi)} B-scans, {n_bad} excluídos por SQI<0.5)"
                )
                logger.info("SQI: mean=%.3f, %d B-scans excluídos", cd.sqi_mean, n_bad)

            # Re-calcular ETDRS, RNFL y mGCIPL con lateralidad correcta
            if cd.laterality:
                from transducin.revo_opt_reader import (
                    extract_layer, parse_octparams, parse_opt_chunks,
                    compute_etdrs, compute_rnfl_sectors, compute_gcl_ipl,
                )
                raw    = filepath.read_bytes()
                chunks = parse_opt_chunks(raw)
                params = parse_octparams(raw, chunks)
                top    = extract_layer(raw, chunks, "TOP")   # ILM = superficie retinal
                nfl    = extract_layer(raw, chunks, "NFL")
                gcl    = extract_layer(raw, chunks, "GCL")
                inl    = extract_layer(raw, chunks, "INL")
                bm     = extract_layer(raw, chunks, "BM")
                lat    = cd.laterality

                # ETDRS = grosor retinal completo (BM − TOP/ILM)
                etdrs = compute_etdrs(top, bm, params, laterality=lat)
                if etdrs is not None and etdrs.has_data():
                    cd.etdrs_grid = etdrs
                    cd.add_note(
                        f"CONFIRMED: etdrs_grid C={etdrs.C:.1f} S1={etdrs.S1:.1f} "
                        f"N1={etdrs.N1:.1f} I1={etdrs.I1:.1f} T1={etdrs.T1:.1f} µm"
                    )
                    logger.info("ETDRS: C=%.1f S1=%.1f N1=%.1f I1=%.1f T1=%.1f µm",
                                etdrs.C, etdrs.S1, etdrs.N1, etdrs.I1, etdrs.T1)

                rnfl = compute_rnfl_sectors(nfl, gcl, params, laterality=lat)
                if rnfl is not None and rnfl.has_data():
                    cd.rnfl = rnfl
                    cd.add_note(
                        f"CONFIRMED: mRNFL global={rnfl.global_avg:.1f} "
                        f"S={rnfl.superior:.1f} N={rnfl.nasal:.1f} "
                        f"I={rnfl.inferior:.1f} T={rnfl.temporal:.1f} µm"
                    )
                    logger.info("mRNFL: global=%.1f S=%.1f N=%.1f I=%.1f T=%.1f µm",
                                rnfl.global_avg, rnfl.superior, rnfl.nasal,
                                rnfl.inferior, rnfl.temporal)

                gclipl = compute_gcl_ipl(gcl, inl, params, laterality=lat)
                if gclipl is not None and gclipl.has_data():
                    cd.gcl_ipl = gclipl
                    cd.gcl_avg_um = gclipl.global_avg
                    cd.add_note(
                        f"CONFIRMED: mGCIPL global={gclipl.global_avg:.1f} "
                        f"S={gclipl.superior:.1f} N={gclipl.nasal:.1f} "
                        f"I={gclipl.inferior:.1f} T={gclipl.temporal:.1f} µm"
                    )
                    logger.info("mGCIPL: global=%.1f S=%.1f N=%.1f I=%.1f T=%.1f µm",
                                gclipl.global_avg, gclipl.superior, gclipl.nasal,
                                gclipl.inferior, gclipl.temporal)
            else:
                cd.add_note("ASSUMED: ETDRS/RNFL/mGCIPL no calculable — lateralidad desconocida")
        except Exception as e:
            cd.add_note(f"ASSUMED: CMT/ETDRS no calculable — {e}")
            logger.warning("CMT/ETDRS no calculable para %s: %s", filepath.name, e)
    elif cd.study_type == "optic_nerve":
        # RNFL peripapillar y disco óptico
        try:
            from transducin.revo_opt_reader import (
                parse_opt_chunks, parse_octparams, extract_layer,
                extract_disc_center, compute_peripapillary_rnfl,
                compute_disc_metrics,
            )
            raw_bytes = filepath.read_bytes()
            chunks    = parse_opt_chunks(raw_bytes)
            params    = parse_octparams(raw_bytes, chunks)
            nfl       = extract_layer(raw_bytes, chunks, "NFL")
            top       = extract_layer(raw_bytes, chunks, "TOP")
            lat       = cd.laterality or "R"

            b_center, a_center = extract_disc_center(raw_bytes, chunks, params)
            logger.info("Centro disco: B=%.1f A=%.1f", b_center, a_center)

            prnfl = compute_peripapillary_rnfl(
                nfl, top, params, laterality=lat,
                b_center=b_center, a_center=a_center,
            )
            if prnfl is not None and prnfl.has_data():
                cd.rnfl = prnfl
                cd.add_note(
                    f"CONFIRMED: RNFL peripapillar (3.4mm ring) "
                    f"global={prnfl.global_avg:.1f} "
                    f"S={prnfl.superior:.1f} N={prnfl.nasal:.1f} "
                    f"I={prnfl.inferior:.1f} T={prnfl.temporal:.1f} µm"
                )
                logger.info(
                    "RNFL peripapillar: global=%.1f S=%.1f N=%.1f I=%.1f T=%.1f µm",
                    prnfl.global_avg, prnfl.superior, prnfl.nasal,
                    prnfl.inferior, prnfl.temporal,
                )
            else:
                cd.add_note("ASSUMED: RNFL peripapillar no calculable — capas TOP/NFL ausentes o inválidas")

            # C/D ratio + métricas de disco desde ILM morfología + DMARKERS
            disc = compute_disc_metrics(top, raw_bytes, chunks, params)
            if disc:
                cd.cup_disc_ratio = disc["cdr"]
                cd.vcdr           = disc["vcdr"]
                cd.disc_area_mm2  = disc["disc_area_mm2"]
                cd.rim_area_mm2   = disc["rim_area_mm2"]
                cd.cup_vol_mm3    = disc["cup_vol_mm3"]
                cd.add_note(
                    f"CONFIRMED: C/D={disc['cdr']:.3f} VCDR={disc['vcdr']:.3f} "
                    f"disc={disc['disc_area_mm2']:.4f}mm² rim={disc['rim_area_mm2']:.4f}mm²"
                    f" cup_vol={disc['cup_vol_mm3']:.4f}mm³"
                )
                logger.info(
                    "Disco: CDR=%.3f VCDR=%.3f disc=%.4fmm² rim=%.4fmm² cup_vol=%.4fmm³",
                    disc["cdr"], disc["vcdr"], disc["disc_area_mm2"], disc["rim_area_mm2"],
                    disc["cup_vol_mm3"],
                )
            else:
                cd.add_note("ASSUMED: disco no calculable — DMARKERS ausente o ILM inválida")

        except Exception as e:
            cd.add_note(f"ASSUMED: RNFL peripapillar/C/D no calculable — {e}")
            logger.warning("RNFL peripapillar/C/D no calculable para %s: %s", filepath.name, e)

    elif cd.study_type == "angio":
        # AngioOCT: registrar dimensiones, preview ANGPRV y SQI si disponible
        try:
            from transducin.revo_opt_reader import (
                parse_opt_chunks, parse_octparams, decode_i8_image, extract_sqi,
            )
            raw_bytes = filepath.read_bytes()
            chunks    = parse_opt_chunks(raw_bytes)
            params    = parse_octparams(raw_bytes, chunks)
            angprv    = decode_i8_image(raw_bytes, chunks, "ANGPRV")
            n_fr  = params.get("n_bscans")
            n_asc = params.get("n_ascans")
            if angprv is not None:
                cd.add_note(
                    f"CONFIRMED: ANGIO scan {n_fr}fr × {n_asc}px — "
                    f"ANGPRV en-face preview {angprv.shape[1]}×{angprv.shape[0]} px disponible"
                )
                logger.info("ANGIO: ANGPRV %dx%d extraído", angprv.shape[1], angprv.shape[0])
            else:
                cd.add_note(f"ASSUMED: ANGIO scan {n_fr}fr × {n_asc}px — sin ANGPRV")
            # SQI del cubo OCTA (si el equipo lo registró)
            sqi = extract_sqi(raw_bytes, chunks)
            if sqi is not None and len(sqi) > 0:
                cd.sqi_mean = float(sqi.mean())
                cd.add_note(f"CONFIRMED: sqi_mean={cd.sqi_mean:.3f} (ANGIO, n={len(sqi)} frames)")
                logger.info("ANGIO SQI: mean=%.3f n=%d", cd.sqi_mean, len(sqi))
        except Exception as e:
            cd.add_note(f"ASSUMED: ANGIO no procesable — {e}")
            logger.warning("ANGIO no procesable para %s: %s", filepath.name, e)

    elif cd.study_type == "biometry":
        # Biometría MYOPI: longitud axial, CCT, queratometría
        try:
            from transducin.revo_opt_reader import read_opt
            result = read_opt(filepath)
            myopi = result.get("myopi")
            if myopi:
                side = "leftOriginalParams" if cd.laterality == "L" else "rightOriginalParams"
                params_bio = myopi.get(side, {}).get("biometry", {})
                al  = params_bio.get("al")
                cct = params_bio.get("cct")
                topo = myopi.get(side, {}).get("topo", {})
                k1  = topo.get("k1")
                k2  = topo.get("k2")
                if al is not None:
                    cd.axial_length_mm = float(al)
                    cd.add_note(f"CONFIRMED: axial_length_mm={al:.3f} mm — MYOPI JSON")
                    logger.info("Longitud axial: %.3f mm", al)
                if cct is not None:
                    cd.cct_um = float(cct) * 1000.0   # mm → µm
                    cd.add_note(f"CONFIRMED: cct_um={cd.cct_um:.1f} µm — MYOPI JSON")
                    logger.info("CCT: %.1f µm", cd.cct_um)
                if k1 is not None:
                    cd.k1_mm = float(k1)
                if k2 is not None:
                    cd.k2_mm = float(k2)
        except Exception as e:
            cd.add_note(f"ASSUMED: biometría no extraíble — {e}")
            logger.warning("Biometría no extraíble para %s: %s", filepath.name, e)
    else:
        cd.add_note("ASSUMED: estudio no es OCT macular ni biometría — métricas no aplican")

    # ── Nivel 3: UNKNOWN — datos encriptados ─────────────────────────────────
    cd.add_note("UNKNOWN: patient_dob — PATIENT.DAT encriptado")
    if cd.cmt_um is None:
        cd.add_note("UNKNOWN: cmt_um, etdrs_grid, rnfl — capas no disponibles para este tipo de estudio")

    # Determinar confianza global
    if cd.noel_id and cd.laterality and cd.study_date:
        has_metric = (
            cd.cmt_um is not None
            or (cd.rnfl is not None and cd.rnfl.has_data())
            or cd.axial_length_mm is not None
        )
        cd.extraction_confidence = "confirmed" if has_metric else "assumed"
    else:
        cd.extraction_confidence = "unknown"

    return cd


def extract_batch(folder: str | Path) -> list[OCTClinicalData]:
    """Extrae OCTClinicalData de todos los .opt en una carpeta (recursivo)."""
    folder = Path(folder)
    results = []
    for opt_file in sorted(folder.rglob("*.opt")):
        try:
            cd = extract_from_opt(opt_file)
            results.append(cd)
        except Exception as e:
            logger.error("Error en %s: %s", opt_file.name, e)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# TESTS  —  python transducin/opt_extractor.py
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.WARNING)

    G, R, E = "\033[92m", "\033[91m", "\033[0m"
    errors = 0

    def check(label, got, expected):
        global errors
        ok = got == expected
        print(f"  {'✓' if ok else '✗'} [{G+'PASS'+E if ok else R+'FAIL'+E}] {label}")
        if not ok:
            print(f"       got:      {got!r}")
            print(f"       expected: {expected!r}")
            errors += 1

    OPT_DIR = Path("input/REVO/20260309_103910")

    print("\n══ Filename parsing ══")

    # Archivo con NOEL prefix
    with_noel = OPT_DIR / "JAHJ19870831_JAURRIETA HINOJOS_JESUS NOEL_20260226_152342_Color_fundus_OS.opt"
    if with_noel.exists():
        cd = extract_from_opt(with_noel)
        check("noel_id desde filename",   cd.noel_id,    "JAHJ19870831")
        check("laterality OS→L",          cd.laterality, "L")
        check("study_date",               cd.study_date, "20260226")
        check("study_type fundus",        cd.study_type, "fundus")
        check("confidence ≥ assumed",     cd.extraction_confidence in ("assumed", "confirmed"), True)
    else:
        print(f"  ⚠ Archivo no encontrado: {with_noel.name}")

    # Archivo sin NOEL prefix
    no_noel = OPT_DIR / "JAURRIETA HINOJOS_JESUS NOEL_20260226_152342_OS_OCT.opt"
    if no_noel.exists():
        cd2 = extract_from_opt(no_noel)
        check("laterality OS→L (sin noel)", cd2.laterality, "L")
        check("study_date (sin noel)",       cd2.study_date, "20260226")
        check("study_type OCT→macular",      cd2.study_type, "macular")
        check("patient_name presente",        bool(cd2.patient_name), True)
    else:
        print(f"  ⚠ Archivo no encontrado: {no_noel.name}")

    # Biometría
    bmetr = OPT_DIR / "JAURRIETA HINOJOS_JESUS NOEL_20260226_154740_OS_BMETR.opt"
    if bmetr.exists():
        cd3 = extract_from_opt(bmetr)
        check("BMETR → biometry",   cd3.study_type, "biometry")
        check("laterality OS→L",    cd3.laterality, "L")
    else:
        print(f"  ⚠ Archivo no encontrado: {bmetr.name}")

    print("\n══ DEMO files ══")
    demo = Path("input/REVO/20260309_103910 (1)/DEMO_DEMO_20260226_145352_OD_OCT.opt")
    if demo.exists():
        cd4 = extract_from_opt(demo)
        check("DEMO laterality OD→R", cd4.laterality, "R")
        check("DEMO study_type OCT",  cd4.study_type, "macular")
        print(f"  ℹ  confidence: {cd4.extraction_confidence}")
        print("  ℹ  notes:")
        for n in cd4.confidence_notes:
            print(f"       {n}")
    else:
        print(f"  ⚠ Archivo no encontrado: {demo.name}")

    print(f"\n{'══ TODOS LOS TESTS PASARON ══' if errors==0 else f'══ {errors} FALLARON ══'}\n")
    sys.exit(0 if errors == 0 else 1)
