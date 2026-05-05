# transducin/revo_opt_reader.py
# SPDX-License-Identifier: Apache-2.0
#
# Reader/converter para archivos .opt del Optopol Revo FC130.
#
# ══════════════════════════════════════════════════════════════════════════════
# MAPA COMPLETO DEL FORMATO .opt — Revo FC130 (Optopol Technology)
# Ingeniería inversa: RetinaOS / oftalmos-org, 2025-2026.
# Este es el primer documento público completo del formato propietario .opt.
# ══════════════════════════════════════════════════════════════════════════════
#
# CONTENEDOR BINARIO
# ──────────────────
# Secuencia de chunks: magic(4) | name_len(4 LE) | name(n bytes) | \x00 |
#                      meta(4) | field2(4) | <datos hasta el próximo magic>
# Magic principal: a5 a5 a5 ff
# Los datos de cada chunk se extienden hasta el siguiente magic o EOF.
# Los chunks individuales se comprimen con zlib:
#   prefijo 1B tipo + 4B LE compressed_size + zlib_stream
#   (algunos usan zlib directo sin prefijo: headers 78 01 / 78 9c / 78 da)
#
# TIPOS DE SCAN Y SUS DIMENSIONES (verificado contra archivos reales)
# ─────────────────────────────────────────────────────────────────────
# IMPORTANTE: OCTPARAMS tag6 (n_bscans) = posiciones de escaneo, NO el número
# de T-chunks. Los T-chunks reales = n_bscans × n_averages (tag20).
# Ejemplo: wide_field con 5 posiciones × 21 averages → 105 T-chunks en disco.
#
#  Tipo          OCTPARAMS n_bscans × n_ascans  sw      T-chunks reales  axial
#  macular       168 × 1024                     10 mm   168              2.800 µm/px
#  optic_nerve   192 × 640                       6 mm   192              2.800 µm/px
#  angio         320 × 320                       3 mm   320 T + 320 A    2.800 µm/px
#  hd_line        18 × 1024                      8 mm   18 × n_avg       2.800 µm/px
#  wide_field      5 × 1536                     12 mm   105 (×21 avg)    2.800 µm/px
#  ultra_wide      6 × 8192                     16 mm    24 (×4 avg)     3.724 µm/px
#  ultra_wide      1 × 10240                    14 mm    21 (×21 avg)    2.800 µm/px
#  hd_macular     21 × 1024                     10 mm   231 (×11 avg)    2.800 µm/px
#  biometry       —  (MYOPI chunk presente)
#
# Reglas de detección por OCTPARAMS (opt_extractor.py):
#   n_bscans==192 and n_ascans==640            → optic_nerve
#   n_bscans in (319,320) and n_ascans<=320    → angio
#   n_ascans >= 4096                           → ultra_wide  (sw ≥ 14mm)
#   n_bscans <= 8 and n_ascans >= 1024         → wide_field  (sw ≥ 10mm, pocas posiciones)
#   n_bscans <= 25 and n_ascans >= 512         → hd_line
#   else → depende del filename (_OCT = macular, _BMETR = biometry, etc.)
#
# CHUNKS DE B-SCANS ESTRUCTURALES (T0…T_N-1)
# ───────────────────────────────────────────
#   T0…TN    B-scans OCT estructurales, comprimidos (zlib con prefijo).
#            Cabecera descomprimida: 2B id | uint32 LE width | uint32 LE height
#            Pixel data: uint8, width × height bytes (grayscale, eje rápido = A-scan).
#            N = n_bscans según OCTPARAMS tag 6.
#
# CHUNKS DE IMÁGENES EN-FACE (formato "I8")
# ──────────────────────────────────────────
#   Header: bytes[0:2] = b'I8', bytes[2:6] = uint32 LE width,
#           bytes[6:10] = uint32 LE height, bytes[10:] = width×height uint8 pixels.
#
#   EYE      — Imagen SLO del segmento posterior (~504×378 px uint8 grayscale).
#              Imagen de referencia de la región escaneada; alta resolución.
#   SLO      — Versión reducida del EYE (192×128 px uint8).
#              Thumbnail para localización del escaneo OCT en UI.
#   PRV      — Preview estructural 320×240 px uint8.
#              Proyección en-face del cubo escaneado (no es mapa de grosor).
#   FNDSRECO — Proyección en-face reconstruida del cubo OCT.
#              Dimensiones = n_ascans × n_bscans (ej. 1024×168 macular,
#              640×192 ONH, 320×320 OCTA). Orientada igual que el cubo escaneado.
#   FNDSIR   — Imagen SLO integrada de alta resolución (632×632 px).
#              Solo válida si se capturó foto externa; si no, todo ceros.
#   ANGPRV   — Preview OCTA compuesta 320×320 px uint8. Solo en scans ANGIO.
#              Proyección en-face de la señal de flujo (superficial + profunda).
#
# CHUNKS DE CAPAS DE SEGMENTACIÓN (float32 comprimido)
# ──────────────────────────────────────────────────────
#   Header: uint32 width | uint32 n_frames | width×n_frames float32 (profundidad en px).
#   Valor 0.0 = posición inválida (disco óptico, sombras, fuera de campo).
#   Valor es la posición axial del límite de capa, medido desde el tope del A-scan.
#
#   TOP    — ILM (inner limiting membrane) = límite superior de la retina.
#   NFL    — Límite NFL/GCL = límite inferior del RNFL.
#            RNFL_µm = (NFL − TOP) × axial_µm/px
#   GCL    — Límite GCL/IPL = límite inferior de la capa ganglionar.
#            GCL_µm  = (GCL − NFL) × axial_µm/px
#   IPL    — Límite IPL/INL.
#   INL    — Límite INL/OPL.
#            mGCIPL_µm = (INL − NFL) × axial_µm/px  [GCL + IPL]
#   OPL    — Límite OPL/ONL.
#   ONL    — Límite ONL/IS (inner segment).
#   ELM    — External Limiting Membrane.
#   EZOS   — IS/OS junction (zona elipsoide = photoreceptor inner segment tip).
#   ISOS   — OS/RPE junction.
#   BM     — Membrana de Bruch = límite externo de la retina.
#   BOTTOM — ≈ BM (espesor total = (BOTTOM − TOP) × axial_µm/px).
#   CSI    — Cup Surface Image (solo ONH). Header: uint32 w | uint32 n | uint32 extra |
#            w×n×5 bytes. Estructura de 5B/px desconocida. En muestras analizadas:
#            todo ceros — datos C/D posiblemente en región encriptada ANALYSIS.DAT.
#   FIT    — Superficie ajustada para corrección de movimiento (solo ANGIO).
#            float32; rango 730-808 px. Referencia axial para OCTA.
#   ALIGN  — Mapa de desplazamiento A-scan para alineación (solo ANGIO).
#            float32; rango −40 a +18 px. Corrección de motion artifact en OCTA.
#
# CHUNKS DE PARÁMETROS DE ADQUISICIÓN
# ────────────────────────────────────
#   OCTPARAMS — Parámetros de escaneo (8B/registro: tag[1] | pad[2] | type[1] | value[4]):
#               type 0x12 = uint32 LE, type 0x22 = float32 LE.
#               tag 1  = protocol_version (uint32)
#               tag 2  = scan_protocol_id (uint32)
#               tag 3  = scan_width_mm (float32)  — ej. 6.0 ONH, 10.0 macular
#               tag 4  = scan_height_mm (float32)
#               tag 5  = n_ascans por B-scan (uint32)
#               tag 6  = n_bscans (uint32)
#               tag 7  = scan_mode flag (uint32; 1 = cube)
#               tag 8  = lateral_mm/A-scan (float32) → ×1000 = µm/A-scan
#               tag 9  = axial_mm/px (float32) → ×1000 = µm/px
#               tag 10 = n_ascans_raw (uint32; igual a tag 5 en la mayoría)
#               tag 11 = depth_px (uint32)
#               tag 12 = focus_distance_mm (float32; ej. 23.0)
#               tag 13 = working_distance_mm (float32; ej. 12.0)
#               tag 14 = far_field_mm (float32)
#               tag 15 = scan_depth_mm (float32; ej. 6.0 ONH)
#               tag 16 = offset_x_mm (float32)
#               tag 17 = offset_y_mm (float32)
#               tag 20 = averaging (uint32; ej. 2 = 2 B-scan average)
#               tag 21 = ring_outer_mm (float32; 3.6 mm para RNFL peripapillar)
#               tag 22 = ring_inner_mm (float32; 0.6 mm)
#               tag 23–25 = transform params (float32; rotación/escala del scan)
#   APARAMS   — Parámetros de análisis del algoritmo (mismo formato 8B/registro):
#               tag 7 = 0.15 (constante de análisis, función desconocida)
#               tag 8 = 1.7  (radio del círculo peripapillar interno en mm)
#               tag 9 = 1.8  (radio del círculo peripapillar externo en mm)
#               NOTA: estos son parámetros del algoritmo, NO resultados C/D.
#   PARAMS    — Registro de estudio: StudyInstanceUID DICOM (patrón 1\.[\d.]{10,}),
#               timestamp YYYYMMDDHHMMSS+GUID (tag 2, string), device serial (tag 4).
#               Formato de registro PARAMS: tag[1] | pad[2] | type[1] | value/length[4]
#               donde type 0x01 = string ASCII (length[4] bytes de texto a continuación).
#   DMARKERS  — Marcadores del límite del disco óptico (solo ONH):
#               uint32 n_frames + n_frames × 4 float32 [x1, y1, x2, y2].
#               x1/x2 = bordes izquierdo/derecho del disco en índice A-scan.
#               NaN = B-scan no cruza el disco. Válido en B-scans donde el disco
#               es visible (típicamente ~48 B-scans en el centro del cubo).
#   TOMOSQI   — Signal Quality Index por B-scan (solo OCT/ONH):
#               uint32 n_frames + n_frames × float32. Rango 0–1 (típico 0.83–0.91).
#               Calidad de señal por cada B-scan; útil para filtrar B-scans ruidosos.
#   TRAJ      — Posición espacial de cada B-scan en mm (solo OCT/ONH):
#               uint32 n_frames + n_frames × 4 float32 [x_start, y, x_end, y].
#               x_start = −scan_width_mm/2, x_end = +scan_width_mm/2.
#               y = posición del B-scan de −scan_width/2 a +scan_width/2 mm.
#               Ejemplo ONH 6mm: x_start=−3.0, x_end=+3.0, y de −3.0 a +3.0.
#   MYOPI     — JSON de biometría (solo BMETR): al, cct, k1, k2 (longitud axial,
#               grosor corneal central, queratometría plana/curva).
#   FNDCORR   — Parámetros de corrección del fundus (registros 8B).
#   FNDSPHOTO — Metadatos de la foto fundus: magic ff 55 aa ff + 4B flags +
#               timestamp ASCII YYYYMMDDHHMMSS (14 bytes) + transform matrix float32.
#               Si FNDSPHOTO[8:12] = ff ff ff ff: no hay foto externa capturada.
#   FNDCORRLINK — Parámetros de enlace de corrección fundus (mismo formato FNDCORR, ~77B).
#   FNDSSTNGS — Configuración de visualización del disco óptico. Contiene 5 secciones
#               repetidas (una por mapa/círculo de análisis) + 8B de cierre.
#               Registro: tag[1] | pad[2] | type[1] | value[variable]:
#                 type 0x13 = uint32 (4B value) → record 8B
#                 type 0x23 = float64 (8B value) → record 12B
#                 type 0x33 = bool/flag (1B value) → record 5B
#               Tags por sección (índice de sección = tag 0x18):
#                 tag 0x16 (22) = uint32, nº de círculos (valor fijo = 3)
#                 tag 0x18 (24) = uint32, índice de sección (0..4)
#                 tag 0x11 (17) = float64 = 0.0  (peso de visualización)
#                 tags 0x12–0x15 = float64 = 1.0 (pesos de los 4 sectores)
#                 tag 0x17 (23) = bool = 0 (flag de visibilidad)
#                 tag 0x19 (25) = uint32 = 0 (flag)
#                 tag 0x1b (27) = float32 = 1.0 (radio normalizado de círculo)
#                 tag 0x1d (29) = float32 = 0.5 (threshold de visualización)
#               NOTA: los valores 0.5 y 1.0 son CONFIGURACIÓN DE DISPLAY, no C/D.
#
# CHUNKS ESPECÍFICOS DE OCTA (solo scans ANGIO, 320fr × 320 A-scans)
# ─────────────────────────────────────────────────────────────────────────────
#   A0…A_N-1    — N cuadros de flujo OCTA (decorrelación speckle), uno por B-scan.
#                 raw ~508 kB, dec 583691 B cada uno.
#                 Formato F1 (ingeniería inversa RetinaOS 2026):
#                   bytes[0:2]  = b'F1'  magic
#                   byte[2]     = versión (0x36 observado)
#                   bytes[3:7]  = depth  uint32 LE (= 912 px axiales)
#                   bytes[7:9]  = n_ascans uint16 LE (= 320)
#                   bytes[9:11] = reservado (0x00 0x00)
#                   bytes[11:]  = float16 LE [depth × n_ascans] señal OCTA
#                 La señal es decorrelación de speckle (valores ~1.5–50000, sin unidades).
#                 MIP sobre profundidad → imagen en-face OCTA (ver compute_octa_enface).
#                 Nota: depth=912 < structural depth=992; primeros/últimos 40px excluidos.
#   ANGIOMAP_0…15 — Definición de 16 slabs de proyección OCTA (40B/mapa):
#                 uint32 map_id, uint32 n_layers, float32 top_offset_µm,
#                 uint32 ref_layer_top, float32 bot_offset_µm [+ 20B cero padding].
#                 Los offsets son relativos a la capa de referencia (e.g. ILM).
#   ANGIOMAPSORDER — Lista de 5 × uint32 map_ids activos (20B).
#                 Define qué 5 de los 16 mapas se muestran en la UI del Revo.
#                 Ejemplo observado: [2, 3, 4, 5, 6] = superficial, profunda,
#                 outer retina, choriocapillaris, slab combinado.
#   ANGPRV      — Proyección OCTA compuesta (I8, 320×320 uint8).
#                 Imagen de flujo en-face con todas las capas combinadas.
#
# CHUNKS PUNTERO (4 bytes = uint32 LE offset absoluto dentro del archivo)
# ───────────────────────────────────────────────────────────────────────
#   ANALYSIS.DAT — Puntero a región de análisis ENCRIPTADA dentro del .opt.
#                  Contiene C/D ratio, disc area, cup area y otros resultados
#                  del algoritmo Revo — no accesibles sin clave del fabricante.
#                  ¡IMPORTANTE: NO confundir con el archivo standalone analysis.dat!
#                  El archivo externo analysis.dat (generado por el SW del Revo)
#                  es un contenedor de chunks con las capas de segmentación
#                  (ONL/GCL/BOTTOM/BM/TOP/OPL/ISOS/EZOS/INL/ELM/IPL/NFL en
#                  640×192 float32, DMARKERS, TOMOSQI, FNDSSTNGS, etc.) pero
#                  **tampoco** contiene el C/D ratio — CSI es todo ceros en
#                  todas las muestras analizadas.
#   RESULTS.DAT  — Puntero a resultados biométricos ENCRIPTADOS (solo BMETR).
#   PREVIEW.DAT  — Puntero interno (uso de aplicación).
#   PARAMS.DAT   — Puntero a bloque de parámetros adicionales.
#                  El archivo externo params.dat contiene: PARAMS (StudyUID +
#                  timestamp + device serial "1936093/23"), OCTPARAMS (parámetros
#                  completos de adquisición), TRAJ (posiciones B-scan en mm),
#                  FNDSPREVIEWPARAMS. Estos son los parámetros del scan, NO C/D.
#   PATIENT.DAT  — Puntero a datos demográficos ENCRIPTADOS.
#   REMARKS.DAT  — Puntero a notas clínicas (no decodificables).
#
# CHUNKS SIN CONTENIDO CLÍNICO CONFIRMADO
# ─────────────────────────────────────────
#   PATIENT      — Datos demográficos encriptados (~192B).
#   EXAM_AI_RES  — Reserved placeholder for future AI results. Payload is 16 null
#                  bytes (zlib-compressed) in all files tested under SOCT v21.1.0.
#                  Preserved in passthrough buffer for forward compatibility with
#                  future firmware versions.
#   FNDSIMSTNGS  — Configuración de similaridad de fundus (4B).
#   FNDSPREVIEWPARAMS — Parámetros de preview (16B).
#   FNDSSTNGS    — Configuración del subsistema de fundus (183–517B, varios tipos).
#   TOMOSQI      — (ver arriba, sí tiene contenido clínico)
#
# Uso:
#   python -m transducin.revo_opt_reader archivo.opt -o Output/
#   from transducin.revo_opt_reader import read_opt, opt_to_dicom

from __future__ import annotations

import argparse
import json
import logging
import re
import struct
import zlib
from pathlib import Path
from typing import Optional

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid

from transducin.dicom_config import (
    MANUFACTURER,
    MANUFACTURER_OPTOPOL,
    MODEL_REVO,
    RETINAOS_TRANSFER_SYNTAX,
    SOP_OPT,
)
from transducin.noel_id import dob_from_noel
from transducin.clinical_data import study_description_label

logger = logging.getLogger(__name__)

_OPT_MAGIC = b"\xa5\xa5\xa5\xff"

# Resolución axial por defecto si OCTPARAMS no la provee (Revo FC130 documentada)
_DEFAULT_AXIAL_UM_PX = 2.800
# Radio del círculo central ETDRS para CMT (1 mm diámetro = 500 µm radio)
_CMT_RADIUS_UM = 500.0
# SQI mínimo para incluir un B-scan en promedios sectoriales
_SQI_MIN_QUALITY = 0.50

# SOP Class para OCT multiframe (alias local para legibilidad)
_SOP_OPT = SOP_OPT


# ── Parseo de chunks ──────────────────────────────────────────────────────────

def _find_chunk_positions(data: bytes) -> list[int]:
    """Encuentra todas las posiciones del magic a5 a5 a5 ff en el archivo."""
    positions = []
    pos = 0
    while True:
        pos = data.find(_OPT_MAGIC, pos)
        if pos == -1:
            break
        positions.append(pos)
        pos += 4
    return positions


def parse_opt_chunks(data: bytes) -> dict[str, dict]:
    """Parsea todos los chunks del archivo .opt.

    Returns:
        Diccionario nombre → {offset, real_size, meta, field2}
        donde offset es el inicio de los datos del chunk y real_size es el
        tamaño calculado hasta el siguiente magic.
    """
    positions = _find_chunk_positions(data)
    chunks: dict[str, dict] = {}

    for i, p in enumerate(positions):
        pos = p + 4  # skip magic

        name_len = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if name_len == 0 or name_len > 64:
            continue

        name = data[pos : pos + name_len].rstrip(b"\x00").decode("ascii", errors="replace")
        pos += name_len
        if pos < len(data) and data[pos] == 0:
            pos += 1  # null terminator

        meta   = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        field2 = struct.unpack_from("<I", data, pos)[0]
        pos += 4

        data_offset = pos
        next_magic  = positions[i + 1] if i + 1 < len(positions) else len(data)
        real_size   = next_magic - data_offset

        # Para nombres duplicados (PARAMS aparece dos veces) conservar el mayor
        if name not in chunks or real_size > chunks[name]["real_size"]:
            chunks[name] = {
                "offset":    data_offset,
                "real_size": real_size,
                "meta":      meta,
                "field2":    field2,
            }

    return chunks


# Chunks que el Revo FC130 añade DESPUÉS de correr segmentación AI
# (no están presentes en el quick-save inmediato al scan).
_SEGMENTATION_MARKER_CHUNKS = ("BM", "TOP")


def has_segmentation(opt_path: "str | Path") -> bool:
    """True si el .opt contiene los chunks de segmentación (BM y TOP).

    Útil para detectar si el Revo ya terminó el análisis de capas y re-guardó
    el archivo (quick-save inicial NO tiene segmentación; re-save SÍ).

    Fails closed: devuelve False ante cualquier error de parseo.
    """
    try:
        data = Path(opt_path).read_bytes()
        chunks = parse_opt_chunks(data)
        return all(name in chunks for name in _SEGMENTATION_MARKER_CHUNKS)
    except Exception:
        return False


def _decompress_block(raw: bytes) -> Optional[bytes]:
    """Descomprime un chunk con prefijo (1 byte tipo + 4 bytes LE compressed_size)."""
    if len(raw) < 6:
        return None
    bsize = struct.unpack_from("<I", raw, 1)[0]
    if bsize == 0 or bsize > len(raw) - 5:
        # Intentar zlib directo (sin prefijo)
        if raw[:2] in (b"\x78\x01", b"\x78\x9c", b"\x78\xda"):
            try:
                return zlib.decompress(raw)
            except Exception:
                return None
        return None
    try:
        return zlib.decompress(raw[5 : 5 + bsize])
    except Exception:
        return None


# ── OCTPARAMS ────────────────────────────────────────────────────────────────

def parse_octparams(data: bytes, chunks: dict[str, dict]) -> dict:
    """Extrae parámetros de escaneo del chunk OCTPARAMS.

    Formato: registros de 8 bytes (1B tag | 2B pad | 1B type | 4B value).
    type 0x12 = uint32, type 0x22 = float32.

    Tags relevantes:
      3/4 = scan width/height en mm
      5   = A-scans por B-scan (width)
      6   = número de B-scans (n_frames)
      8   = resolución lateral en mm/A-scan
      9   = resolución axial en mm/px  ← clave para CMT
      11  = profundidad en px
      23  = posición horizontal del fóvea/disco en mm (+ = OS, - = OD)

    Returns:
        {axial_um, lateral_um, scan_width_mm, n_bscans, n_ascans, depth_px,
         scan_center_x_mm}
        Con fallbacks si el chunk no existe o no es parseable.
    """
    result = {
        "axial_um":         _DEFAULT_AXIAL_UM_PX,
        "lateral_um":       9.766,
        "scan_width_mm":    10.0,
        "n_bscans":         None,
        "n_ascans":         None,
        "depth_px":         None,
        "scan_center_x_mm": None,
    }
    c = chunks.get("OCTPARAMS")
    if not c:
        return result
    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    if dec is None or len(dec) < 8:
        return result
    for i in range(0, len(dec) - 7, 8):
        tag  = dec[i]
        typ  = dec[i + 3]
        vraw = dec[i + 4 : i + 8]
        if typ == 0x12:
            val = struct.unpack_from("<I", vraw)[0]
        elif typ == 0x22:
            val = struct.unpack_from("<f", vraw)[0]
        else:
            continue
        if tag == 3:
            result["scan_width_mm"] = float(val)
        elif tag == 8:
            result["lateral_um"] = float(val) * 1000.0
        elif tag == 9:
            result["axial_um"] = float(val) * 1000.0
        elif tag == 5:
            result["n_ascans"] = int(val)
        elif tag == 6:
            result["n_bscans"] = int(val)
        elif tag == 11:
            result["depth_px"] = int(val)
        elif tag == 23:
            result["scan_center_x_mm"] = float(val)
    return result


# ── Capas de segmentación ─────────────────────────────────────────────────────

def extract_layer(data: bytes, chunks: dict[str, dict], name: str) -> Optional[np.ndarray]:
    """Extrae una capa de segmentación retiniana como array (n_bscans, n_ascans) float32.

    El chunk almacena: uint32 width | uint32 n_frames | width×n_frames float32 (profundidad px).
    """
    c = chunks.get(name)
    if not c:
        return None
    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    if dec is None or len(dec) < 12:
        return None
    width    = struct.unpack_from("<I", dec, 0)[0]
    n_frames = struct.unpack_from("<I", dec, 4)[0]
    n_vals   = width * n_frames
    if len(dec) < 8 + n_vals * 4:
        return None
    return np.frombuffer(dec[8 : 8 + n_vals * 4], dtype="<f4").reshape(n_frames, width)


def _extract_layer_with_fallback(
    data: bytes, chunks: dict[str, dict], name: str, fallback: str
) -> Optional[np.ndarray]:
    """extract_layer con fallback si el resultado es all-zeros.

    SOCT 11.5.0 guarda la capa BM como 'BOTTOM'; SOCT 21.1.2 la guarda como 'BM'.
    Si el chunk primario descomprime a todo ceros, intenta el secundario y viceversa.
    """
    arr = extract_layer(data, chunks, name)
    if arr is not None and arr.any():
        return arr
    fallback_arr = extract_layer(data, chunks, fallback)
    if fallback_arr is not None and fallback_arr.any():
        return fallback_arr
    return arr  # retorna el primario (None o zeros) si ninguno tiene datos


def extract_disc_center(
    data: bytes, chunks: dict[str, dict], params: dict
) -> tuple[float, float]:
    """Extrae el centro del disco óptico desde el chunk DMARKERS.

    DMARKERS almacena: uint32 n_frames | n_frames × 4 float32 (x1, y1, x2, y2).
    x1/x2 son los bordes izquierdo/derecho del disco en A-scans (0 si no hay disco).
    Los frames NaN indican que ese B-scan no cruza el disco.

    Returns:
        (b_center, a_center) — posición del disco en índices de array (puede ser float).
        Si DMARKERS no está disponible, devuelve el centro geométrico del volumen.
    """
    n_b = params.get("n_bscans") or 192
    n_a = params.get("n_ascans") or 640
    default_b = n_b / 2.0
    default_a = n_a / 2.0

    c = chunks.get("DMARKERS")
    if not c:
        return default_b, default_a

    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    if dec is None or len(dec) < 8:
        return default_b, default_a

    n = struct.unpack_from("<I", dec, 0)[0]
    if n == 0 or len(dec) < 4 + n * 16:
        return default_b, default_a

    markers = np.frombuffer(dec[4 : 4 + n * 16], dtype="<f4").reshape(n, 4)

    valid_rows = ~np.any(np.isnan(markers), axis=1)
    if not valid_rows.any():
        return default_b, default_a

    b_indices = np.where(valid_rows)[0]
    b_center  = float(b_indices.mean())
    a_centers = (markers[valid_rows, 0] + markers[valid_rows, 2]) / 2.0
    a_center  = float(np.nanmean(a_centers))

    return b_center, a_center


def compute_peripapillary_rnfl(
    nfl: np.ndarray,
    top: np.ndarray,
    params: dict,
    laterality: str = "R",
    b_center: Optional[float] = None,
    a_center: Optional[float] = None,
    ring_radius_um: float = 1700.0,
    ring_width_um:  float = 200.0,
) -> Optional[object]:
    """Calcula RNFL peripapillar en sectores S/I/N/T desde cubo de disco óptico.

    El RNFL peripapillar se mide sobre el anillo de 3.4 mm de diámetro (radio 1700 µm)
    centrado en el disco óptico. Es la métrica primaria de glaucoma en scans de ONH.

    Convención de capas Revo FC130:
      TOP  — ILM (inner limiting membrane) = límite superior del RNFL
      NFL  — límite NFL/GCL = límite inferior del RNFL
      RNFL_µm = (NFL − TOP) × axial_um/px

    Convención de eje y sectores:
      B creciente → inferior; A creciente → derecha de imagen.
      Ángulo = atan2(−B, ±A) con A positivo en dirección temporal:
        OD (derecho): temporal = A > 0 (lado derecho de imagen)
        OS (izquierdo): temporal = A < 0 (lado derecho de imagen = nasal)
      T: [−45°, 45°)   S: [45°, 135°)
      N: [135°, 180°) ∪ [−180°, −135°)   I: [−135°, −45°)

    Args:
        nfl:           Capa NFL/GCL (n_bscans, n_ascans) float32.
        top:           Capa ILM/TOP (n_bscans, n_ascans) float32.
        params:        OCTPARAMS del scan (axial_um, lateral_um, scan_width_mm).
        laterality:    "R"/"OD" o "L"/"OS".
        b_center:      Índice B-scan del centro del disco (None = centro geométrico).
        a_center:      Índice A-scan del centro del disco (None = centro geométrico).
        ring_radius_um: Radio del anillo peripapillar (defecto 1700 µm = 3.4 mm Ø).
        ring_width_um:  Anchura del anillo (defecto 200 µm, ±100 µm).

    Returns:
        RNFLSectors con global_avg, superior, inferior, nasal, temporal en µm, o None.
    """
    from transducin.clinical_data import RNFLSectors

    if nfl is None or top is None or nfl.shape != top.shape:
        return None

    n_b, n_a    = nfl.shape
    axial_um    = params["axial_um"]
    lateral_um  = params["lateral_um"]
    bscan_um    = params["scan_width_mm"] * 1000.0 / n_b

    b0 = b_center if b_center is not None else n_b / 2.0
    a0 = a_center if a_center is not None else n_a / 2.0

    thickness = (nfl - top) * axial_um     # RNFL en µm

    db = (np.arange(n_b) - b0) * bscan_um   # µm desde centro; positivo = inferior
    da = (np.arange(n_a) - a0) * lateral_um  # µm desde centro; positivo = derecha
    B, A = np.meshgrid(db, da, indexing="ij")
    R    = np.sqrt(B ** 2 + A ** 2)

    r_min = ring_radius_um - ring_width_um / 2
    r_max = ring_radius_um + ring_width_um / 2
    ring  = (R >= r_min) & (R < r_max)

    # Para OD: temporal = lado derecho de imagen (A > 0)
    # Para OS: temporal = lado izquierdo de imagen (A < 0)
    if laterality.upper() in ("R", "OD"):
        angles = np.degrees(np.arctan2(-B, A))    # temporal a 0°
    else:
        angles = np.degrees(np.arctan2(-B, -A))   # temporal a 0° para OS

    valid = (thickness > 0) & (thickness < 250)

    def _sector(a_min: float, a_max: float) -> Optional[float]:
        if a_min < a_max:
            m = (angles >= a_min) & (angles < a_max)
        else:
            m = (angles >= a_min) | (angles < a_max)
        m &= ring & valid
        return float(np.mean(thickness[m])) if int(m.sum()) >= 5 else None

    g = _sector(-180.0, 180.0)
    if g is None:
        return None

    return RNFLSectors(
        global_avg = g,
        superior   = _sector(45.0, 135.0),
        inferior   = _sector(-135.0, -45.0),
        nasal      = _sector(135.0, -135.0),   # wraps through ±180°
        temporal   = _sector(-45.0, 45.0),
    )


def _apply_sqi_mask(arr: np.ndarray, sqi: Optional[np.ndarray]) -> np.ndarray:
    """Zeroes B-scan rows cuyo SQI < _SQI_MIN_QUALITY.

    Args:
        arr: (n_bscans, n_ascans) thickness o layer array.
        sqi: (n_bscans,) float32 SQI, o None para no filtrar.

    Returns:
        arr sin modificar si sqi es None o mismatch; copia con filas malas a 0.0 si hay filtrado.
    """
    if sqi is None or len(sqi) != arr.shape[0]:
        return arr
    bad = sqi < _SQI_MIN_QUALITY
    if not bad.any():
        return arr
    out = arr.copy()
    out[bad] = 0.0
    return out


def compute_cmt(
    top: np.ndarray,
    bm: np.ndarray,
    params: dict,
    sqi: Optional[np.ndarray] = None,
) -> Optional[float]:
    """Calcula CMT (Central Macular Thickness) en µm.

    CMT = mean(BM − TOP) en el círculo central de 1mm de diámetro (R < 500 µm).
    TOP = ILM (límite superior de retina); BM = membrana de Bruch (límite externo).
    Grosor total de retina desde ILM hasta BM.

    Args:
        top:    Capa TOP/ILM (n_bscans, n_ascans) float32 — posición axial en px.
        bm:     Capa BM (n_bscans, n_ascans) float32 — posición axial en px.
        params: Diccionario de parse_octparams() con resoluciones y dimensiones.
        sqi:    (n_bscans,) SQI por frame; B-scans con SQI < _SQI_MIN_QUALITY se excluyen.

    Returns:
        CMT en µm, o None si no hay datos válidos.
    """
    if top is None or bm is None:
        return None
    if top.shape != bm.shape:
        return None

    axial_um   = params["axial_um"]
    lateral_um = params["lateral_um"]
    n_b, n_a   = top.shape
    bscan_um   = params["scan_width_mm"] * 1000.0 / n_b

    thickness_um = _apply_sqi_mask((bm - top) * axial_um, sqi)

    b0, a0 = n_b // 2, n_a // 2
    db = (np.arange(n_b) - b0) * bscan_um
    da = (np.arange(n_a) - a0) * lateral_um
    B, A = np.meshgrid(db, da, indexing="ij")
    R = np.sqrt(B ** 2 + A ** 2)

    valid = (R < _CMT_RADIUS_UM) & (thickness_um > 0) & (thickness_um < 800)
    if not valid.any():
        return None

    return float(thickness_um[valid].mean())


def _sector_means(
    thickness: np.ndarray,
    params: dict,
    laterality: str,
    r_inner_um: float,
    r_outer_um: float,
    sqi: Optional[np.ndarray] = None,
) -> dict:
    """Retorna medias por sector S/I/N/T + global en un anillo radial."""
    lateral_um = params["lateral_um"]
    n_b, n_a   = thickness.shape
    bscan_um   = (params["scan_width_mm"] * 1000.0) / n_b

    thickness = _apply_sqi_mask(thickness, sqi)
    valid = (thickness > 0) & (thickness < 800)
    b0, a0 = n_b // 2, n_a // 2
    db = (np.arange(n_b) - b0) * bscan_um
    da = (np.arange(n_a) - a0) * lateral_um
    B, A = np.meshgrid(db, da, indexing="ij")
    R    = np.sqrt(B ** 2 + A ** 2)

    ring = (R >= r_inner_um) & (R < r_outer_um)
    m_S  = B < 0
    m_I  = B >= 0
    if laterality.upper() in ("R", "OD"):
        m_T, m_N = A > 0, A <= 0
    else:
        m_T, m_N = A < 0, A >= 0

    def _m(mask):
        m = mask & valid
        return float(np.mean(thickness[m])) if int(m.sum()) >= 10 else None

    return {
        "global":   _m(ring),
        "superior": _m(ring & m_S),
        "inferior": _m(ring & m_I),
        "nasal":    _m(ring & m_N),
        "temporal": _m(ring & m_T),
    }


def compute_rnfl_sectors(
    nfl: np.ndarray,
    gcl: np.ndarray,
    params: dict,
    laterality: str = "L",
    sqi: Optional[np.ndarray] = None,
) -> Optional[object]:
    """Calcula RNFL macular (GCL − NFL) en sectores S/I/N/T dentro de anillo 1–6mm.

    Nota: este es el RNFL macular (mRNFL), no el peripapillar. Para RNFL
    peripapillar se requiere el scan circular de disco óptico.

    Returns:
        RNFLSectors o None.
    """
    from transducin.clinical_data import RNFLSectors

    if nfl is None or gcl is None or nfl.shape != gcl.shape:
        return None

    thickness = (gcl - nfl) * params["axial_um"]
    s = _sector_means(thickness, params, laterality, r_inner_um=500, r_outer_um=3000, sqi=sqi)
    if s["global"] is None:
        return None
    return RNFLSectors(
        global_avg=s["global"],
        superior=s["superior"],
        inferior=s["inferior"],
        nasal=s["nasal"],
        temporal=s["temporal"],
    )


def compute_gcl_ipl(
    gcl: np.ndarray,
    inl: np.ndarray,
    params: dict,
    laterality: str = "L",
    sqi: Optional[np.ndarray] = None,
) -> Optional[object]:
    """Calcula mGCIPL (GCL + IPL) en sectores S/I/N/T en anillo 1–6mm.

    mGCIPL = grosor entre límite interno GCL (= límite externo NFL) y
    límite interno INL. Es el marcador estándar de pérdida ganglionar
    en escáneres maculares (más sensible que RNFL macular solo).

    Returns:
        RNFLSectors reutilizado como contenedor (mismo esquema de sectores).
    """
    from transducin.clinical_data import RNFLSectors

    if gcl is None or inl is None or gcl.shape != inl.shape:
        return None

    thickness = (inl - gcl) * params["axial_um"]
    s = _sector_means(thickness, params, laterality, r_inner_um=500, r_outer_um=3000, sqi=sqi)
    if s["global"] is None:
        return None
    return RNFLSectors(
        global_avg=s["global"],
        superior=s["superior"],
        inferior=s["inferior"],
        nasal=s["nasal"],
        temporal=s["temporal"],
    )


def compute_etdrs(
    top: np.ndarray,
    bm: np.ndarray,
    params: dict,
    laterality: str = "L",
    sqi: Optional[np.ndarray] = None,
) -> Optional[object]:
    """Calcula ETDRS 9 sectores del grosor retinal (BM − TOP/ILM).

    Grilla estándar ETDRS (diámetros):
      C  — círculo central 1mm  (r < 500µm)
      S1/N1/I1/T1 — anillo interno 3mm  (500µm ≤ r < 1500µm)
      S2/N2/I2/T2 — anillo externo 6mm  (1500µm ≤ r < 3000µm)

    El eje B-scan (vertical en imagen) es superior-inferior:
      B-scan 0 = superior, B-scan N-1 = inferior.
    El eje A-scan (horizontal) es nasal-temporal según lateralidad:
      OS: izquierda = temporal, derecha = nasal
      OD: izquierda = nasal,    derecha = temporal

    Args:
        top: Capa TOP/ILM (n_bscans, n_ascans) — límite interno de retina.
        bm:  Capa BM (n_bscans, n_ascans) — límite externo de retina.

    Returns:
        ETDRSGrid o None si no hay datos suficientes.
    """
    from transducin.clinical_data import ETDRSGrid

    if top is None or bm is None or top.shape != bm.shape:
        return None

    axial_um   = params["axial_um"]
    lateral_um = params["lateral_um"]
    n_b, n_a   = top.shape
    bscan_um   = (params["scan_width_mm"] * 1000.0) / n_b

    thickness = _apply_sqi_mask((bm - top) * axial_um, sqi)  # (n_b, n_a) en µm
    valid     = (thickness > 0) & (thickness < 800)

    b0, a0 = n_b // 2, n_a // 2

    # Distancias en µm respecto al centro del volumen
    db = (np.arange(n_b) - b0) * bscan_um    # positivo = inferior
    da = (np.arange(n_a) - a0) * lateral_um  # positivo = derecha de la imagen
    B, A = np.meshgrid(db, da, indexing="ij")  # (n_b, n_a)
    R = np.sqrt(B ** 2 + A ** 2)

    # Anillos
    m_center = R < 500
    m_inner  = (R >= 500)  & (R < 1500)
    m_outer  = (R >= 1500) & (R < 3000)

    # Superior/Inferior: eje B (db < 0 = superior)
    m_S = B < 0
    m_I = B >= 0

    # Temporal/Nasal: eje A, invertido según lateralidad
    if laterality.upper() in ("R", "OD"):
        m_T = A > 0   # temporal al lado derecho para OD
        m_N = A <= 0
    else:             # OS / L
        m_T = A < 0   # temporal al lado izquierdo para OS
        m_N = A >= 0

    def _mean(mask: np.ndarray) -> Optional[float]:
        m = mask & valid
        if int(m.sum()) < 10:
            return None
        return float(np.mean(thickness[m]))

    grid = ETDRSGrid(
        C  = _mean(m_center),
        S1 = _mean(m_inner & m_S),
        N1 = _mean(m_inner & m_N),
        I1 = _mean(m_inner & m_I),
        T1 = _mean(m_inner & m_T),
        S2 = _mean(m_outer & m_S),
        N2 = _mean(m_outer & m_N),
        I2 = _mean(m_outer & m_I),
        T2 = _mean(m_outer & m_T),
    )
    fields = (grid.C, grid.S1, grid.N1, grid.I1, grid.T1,
              grid.S2, grid.N2, grid.I2, grid.T2)
    return grid if any(v is not None for v in fields) else None


def compute_disc_metrics(
    top: np.ndarray,
    data: bytes,
    chunks: dict[str, dict],
    params: dict,
    cup_depth_threshold_um: float = 150.0,
) -> Optional[dict]:
    """Calcula métricas del disco óptico desde ILM (TOP) y DMARKERS.

    Algoritmo:
      1. DMARKERS[b] = (x1, _, x2, _) — bordes del disco por B-scan (A-scan idx).
      2. disc_area_mm2 = suma de anchos de disco × espaciado B-scan (proyección en-face).
      3. Rim depth = ILM promedio en los 3px de margen de cada lado del disco.
      4. Cup mask = columnas donde ILM > rim_depth + umbral (150 µm por defecto).
      5. cup_area_mm2 = suma de ancho copa × espaciado B-scan.
      6. rim_area_mm2  = disc_area_mm2 − cup_area_mm2.
      7. v_cdr = B-scans con copa / B-scans con disco.
         h_cdr = ancho_copa_max / ancho_disco_max.
         cdr   = (v_cdr + h_cdr) / 2.

    Nota: aproximación basada en ILM morfológica (±0.05–0.10 respecto al algoritmo
    propietario Revo que usa plano RPE/BM). disc_area y rim_area son proyecciones
    en el plano en-face, no áreas anatómicas en el plano del disco.

    Returns:
        dict con: cdr, vcdr, disc_area_mm2, rim_area_mm2, cup_vol_mm3
        None si DMARKERS ausente o insuficiente.
    """
    if top is None:
        return None

    c = chunks.get("DMARKERS")
    if not c:
        return None

    raw = data[c["offset"]: c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    if dec is None or len(dec) < 8:
        return None
    n_dm = struct.unpack_from("<I", dec, 0)[0]
    if n_dm == 0 or len(dec) < 4 + n_dm * 16:
        return None

    markers = np.frombuffer(dec[4: 4 + n_dm * 16], dtype="<f4").reshape(n_dm, 4)
    valid   = ~np.any(np.isnan(markers), axis=1)
    if valid.sum() < 3:
        return None

    n_b, n_a         = top.shape
    axial_um         = params["axial_um"]
    lateral_um       = params["lateral_um"]
    bscan_spacing_mm = params["scan_width_mm"] / n_b
    cup_thresh_px    = cup_depth_threshold_um / axial_um

    b_valid = np.where(valid)[0]
    x1_arr  = np.clip(markers[b_valid, 0].astype(int), 0, n_a - 1)
    x2_arr  = np.clip(markers[b_valid, 2].astype(int), 0, n_a - 1)
    swap    = x1_arr > x2_arr
    x1_arr[swap], x2_arr[swap] = x2_arr[swap].copy(), x1_arr[swap].copy()

    widths_px = x2_arr - x1_arr
    disc_h_px = int(np.max(widths_px))
    if disc_h_px <= 0:
        return None

    # B-scans con ancho < 30 px son bordes geométricos del disco donde no
    # puede existir excavación — excluirlos de disc_v_cnt y cup_v_cnt evita
    # que versiones del software (e.g. SOCT 11.5.0) que escriben más B-scans
    # periféricos con DMARKERS válidos pero de ancho mínimo diluyan el CDR.
    _MIN_DISC_WIDTH_PX = 30
    core_mask = widths_px >= _MIN_DISC_WIDTH_PX
    b_valid   = b_valid[core_mask]
    x1_arr    = x1_arr[core_mask]
    x2_arr    = x2_arr[core_mask]
    widths_px = widths_px[core_mask]
    if len(b_valid) == 0:
        return None

    disc_h_mm    = disc_h_px * lateral_um / 1000.0
    disc_v_cnt   = len(b_valid)
    disc_area_mm2 = float(np.sum(widths_px) * (lateral_um / 1000.0) * bscan_spacing_mm)

    cup_v_cnt    = 0
    cup_h_px     = 0
    cup_area_sum = 0.0   # A-scans dentro de copa × bscan_spacing
    cup_vol_sum  = 0.0   # depth-integrated cup volume (mm³)

    for i, b in enumerate(b_valid):
        x1 = x1_arr[i]
        x2 = x2_arr[i]
        w  = x2 - x1
        if w <= 2:
            continue
        top_row  = top[b, x1:x2]
        margin   = min(3, max(1, w // 6))
        rim_vals = np.concatenate([top_row[:margin], top_row[-margin:]])
        rim_vals = rim_vals[rim_vals > 0]
        if len(rim_vals) == 0:
            continue
        rim_depth = rim_vals.mean()
        cup_mask  = (top_row > (rim_depth + cup_thresh_px)) & (top_row > 0)
        if cup_mask.any():
            cup_v_cnt    += 1
            cup_w          = int(cup_mask.sum())
            cup_h_px       = max(cup_h_px, cup_w)
            cup_area_sum  += cup_w * (lateral_um / 1000.0)
            depth_px       = top_row[cup_mask] - rim_depth
            cup_vol_sum   += float(np.sum(depth_px)) * (axial_um / 1000.0) * (lateral_um / 1000.0) * bscan_spacing_mm

    cup_area_mm2  = cup_area_sum * bscan_spacing_mm
    rim_area_mm2  = max(0.0, disc_area_mm2 - cup_area_mm2)
    v_cdr         = cup_v_cnt / disc_v_cnt
    h_cdr         = (cup_h_px * lateral_um / 1000.0) / disc_h_mm if disc_h_mm > 0 else 0.0
    cdr           = float(np.clip((v_cdr + h_cdr) / 2.0, 0.0, 1.0))

    return {
        "cdr":           round(cdr, 3),
        "vcdr":          round(float(np.clip(v_cdr, 0.0, 1.0)), 3),
        "disc_area_mm2": round(disc_area_mm2, 4),
        "rim_area_mm2":  round(rim_area_mm2, 4),
        "cup_vol_mm3":   round(cup_vol_sum, 4),
    }


def compute_cup_disc_ratio(
    top: np.ndarray,
    data: bytes,
    chunks: dict[str, dict],
    params: dict,
    cup_depth_threshold_um: float = 150.0,
) -> Optional[float]:
    """Wrapper de compatibilidad → retorna solo el CDR float.

    Usar compute_disc_metrics() para obtener VCDR, disc_area y rim_area.
    """
    m = compute_disc_metrics(top, data, chunks, params, cup_depth_threshold_um)
    return m["cdr"] if m else None


# ── Extracción de B-scans ────────────────────────────────────────────────────

def _decode_bscan(raw_chunk: bytes) -> Optional[np.ndarray]:
    """Descomprime y decodifica un chunk T-N en un array numpy (alto, ancho) uint8."""
    decompressed = _decompress_block(raw_chunk)
    if decompressed is None or len(decompressed) < 10:
        return None

    # Cabecera: 2 bytes id + uint32 width + uint32 height
    width  = struct.unpack_from("<I", decompressed, 2)[0]
    height = struct.unpack_from("<I", decompressed, 6)[0]

    expected = width * height
    pixel_data = decompressed[10:]
    if len(pixel_data) < expected:
        return None

    return np.frombuffer(pixel_data[:expected], dtype=np.uint8).reshape(height, width)


def compute_octa_enface(
    data: bytes,
    chunks: dict[str, dict],
    percentile_low: float = 5.0,
    percentile_high: float = 99.5,
) -> Optional[np.ndarray]:
    """Genera imagen en-face OCTA (MIP log) desde los chunks A0…AN.

    Formato F1 del Revo FC130 (ingeniería inversa RetinaOS 2026):
      header 11B:  b'F1'(2) + ver(1) + depth uint32 LE(4) + n_ascans uint16 LE(2) + reserved(2)
      payload:     float16 LE [depth × n_ascans]  señal de decorrelación OCTA
                   rango típico 1.5–50000, sin unidades lineales.

    Algoritmo:
      Para cada A-chunk: proyección MIP de log1p(frame) sobre eje axial → 1D de n_ascans.
      Apilar proyecciones de todos los A-chunks → imagen (n_bscans, n_ascans).
      Normalizar al percentil [p_low, p_high] → uint8.

    Args:
        data:            Bytes raw del archivo .opt.
        chunks:          Diccionario de chunks parseados.
        percentile_low:  Percentil bajo para normalización (defecto 5).
        percentile_high: Percentil alto para normalización (defecto 99.5).

    Returns:
        np.ndarray (n_bscans, n_ascans) uint8, o None si no hay A-chunks.
    """
    a_names = sorted(
        [k for k in chunks if len(k) > 1 and k[0] == "A" and k[1:].isdigit()],
        key=lambda x: int(x[1:]),
    )
    if not a_names:
        return None

    projections: list[np.ndarray] = []
    for name in a_names:
        c = chunks[name]
        raw = data[c["offset"]: c["offset"] + c["real_size"]]
        dec = _decompress_block(raw)
        if dec is None or len(dec) < 11:
            continue
        depth = struct.unpack_from("<I", dec, 3)[0]
        n_a   = struct.unpack_from("<H", dec, 7)[0]
        n_px  = depth * n_a
        if depth == 0 or n_a == 0 or len(dec) < 11 + n_px * 2:
            continue
        frame = np.frombuffer(dec[11: 11 + n_px * 2], dtype="<f2").reshape(depth, n_a).astype(np.float32)
        projections.append(np.log1p(frame).max(axis=0))   # log-MIP → (n_a,)

    if not projections:
        return None

    enface = np.stack(projections, axis=0)                # (n_bscans, n_a)
    p_lo = float(np.percentile(enface, percentile_low))
    p_hi = float(np.percentile(enface, percentile_high))
    if p_hi <= p_lo:
        return None
    norm = np.clip((enface - p_lo) / (p_hi - p_lo), 0.0, 1.0)
    return (norm * 255).astype(np.uint8)


def extract_bscans(data: bytes, chunks: dict[str, dict]) -> list[np.ndarray]:
    """Extrae todos los B-scans T0…TN en orden numérico."""
    t_chunks = {
        int(name[1:]): chunks[name]
        for name in chunks
        if name.startswith("T") and name[1:].isdigit()
    }
    bscans = []
    for idx in sorted(t_chunks):
        c   = t_chunks[idx]
        raw = data[c["offset"] : c["offset"] + c["real_size"]]
        arr = _decode_bscan(raw)
        if arr is not None:
            bscans.append(arr)
        else:
            logger.warning("B-scan T%d no se pudo decodificar.", idx)
    return bscans


# ── Imágenes en-face (formato I8) ────────────────────────────────────────────

def decode_i8_image(
    data: bytes, chunks: dict[str, dict], name: str
) -> Optional[np.ndarray]:
    """Decodifica un chunk de imagen en-face en formato "I8" del Revo FC130.

    Formato I8 (descomprimido):
        bytes[0:2]  = b'I8'  (magic identifier)
        bytes[2:6]  = uint32 LE  width  (columnas)
        bytes[6:10] = uint32 LE  height (filas)
        bytes[10:]  = width × height bytes uint8, row-major

    Chunks que usan este formato:
        EYE      — SLO posterior (~504×378)
        SLO      — thumbnail SLO (192×128)
        PRV      — preview estructural (320×240)
        FNDSRECO — proyección en-face del cubo OCT (n_ascans × n_bscans)
        FNDSIR   — SLO integrada HR (632×632; ceros si no hay foto externa)
        ANGPRV   — preview OCTA compuesta (320×320, solo scans ANGIO)

    Returns:
        np.ndarray shape (height, width) dtype uint8, o None si no decodificable.
    """
    c = chunks.get(name)
    if not c:
        return None
    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    if dec is None:
        return None
    if len(dec) < 10 or dec[0:2] != b"I8":
        return None
    width  = struct.unpack_from("<I", dec, 2)[0]
    height = struct.unpack_from("<I", dec, 6)[0]
    n_px = width * height
    if n_px == 0 or len(dec) < 10 + n_px:
        return None
    return np.frombuffer(dec[10 : 10 + n_px], dtype=np.uint8).reshape(height, width)


# ── Calidad de señal y trayectoria ───────────────────────────────────────────

def extract_sqi(
    data: bytes, chunks: dict[str, dict]
) -> Optional[np.ndarray]:
    """Extrae el Signal Quality Index por B-scan del chunk TOMOSQI.

    Formato (descomprimido):
        uint32 LE  n_frames
        n_frames × float32 LE  sqi_value  (rango 0–1; típico 0.83–0.91)

    Un valor SQI < 0.5 generalmente indica un B-scan ruidoso o con artefactos
    de parpadeo. Útil para filtrar B-scans al calcular promedios sectoriales.

    Returns:
        np.ndarray shape (n_frames,) dtype float32, o None si chunk ausente.
    """
    c = chunks.get("TOMOSQI")
    if not c:
        return None
    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    if dec is None or len(dec) < 8:
        return None
    n = struct.unpack_from("<I", dec, 0)[0]
    if n == 0 or len(dec) < 4 + n * 4:
        return None
    return np.frombuffer(dec[4 : 4 + n * 4], dtype="<f4").copy()


def extract_traj(
    data: bytes, chunks: dict[str, dict]
) -> Optional[np.ndarray]:
    """Extrae las posiciones espaciales de cada B-scan desde el chunk TRAJ.

    Formato (descomprimido):
        uint32 LE  n_frames
        n_frames × 4 × float32 LE  [x_start, y, x_end, y]

        x_start = −scan_width_mm / 2  (borde izquierdo del B-scan en mm)
        x_end   = +scan_width_mm / 2  (borde derecho)
        y       = posición del B-scan en el eje lento (de −sw/2 a +sw/2 mm)
                  El signo de y depende de la lateralidad/dirección de escaneo.

    Returns:
        np.ndarray shape (n_frames, 4) dtype float32, columnas [x_start, y, x_end, y],
        o None si chunk ausente o mal formado.
    """
    c = chunks.get("TRAJ")
    if not c:
        return None
    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    if dec is None or len(dec) < 8:
        return None
    n = struct.unpack_from("<I", dec, 0)[0]
    if n == 0 or len(dec) < 4 + n * 16:
        return None
    return np.frombuffer(dec[4 : 4 + n * 16], dtype="<f4").reshape(n, 4).copy()


# ── Metadatos clínicos ────────────────────────────────────────────────────────

def extract_myopi_json(data: bytes, chunks: dict[str, dict]) -> Optional[dict]:
    """Extrae y parsea el chunk MYOPI (biometría JSON) si existe."""
    c = chunks.get("MYOPI")
    if not c:
        return None
    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    decompressed = _decompress_block(raw)
    text = decompressed if decompressed else raw
    try:
        # Buscar JSON dentro (puede tener bytes de relleno al inicio)
        start = text.find(b"{")
        end   = text.rfind(b"}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(text[start:end].decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("MYOPI JSON no parseable: %s", e)
        return None


def extract_study_uid(data: bytes, chunks: dict[str, dict]) -> Optional[str]:
    """Extrae el StudyInstanceUID del chunk PARAMS si está disponible."""
    c = chunks.get("PARAMS")
    if not c:
        return None
    raw = data[c["offset"] : c["offset"] + c["real_size"]]
    dec = _decompress_block(raw)
    text = (dec or raw).decode("ascii", errors="replace")
    # El UID DICOM tiene forma 1.2.616.1.113780...
    m = re.search(r"(1\.[\d.]{10,})", text)
    return m.group(1) if m else None


def extract_study_datetime(data: bytes, chunks: dict[str, dict]) -> tuple[str, str]:
    """Extrae date (YYYYMMDD) y time (HHMMSS) del StudyInstanceUID en PARAMS.

    El UID embebe la fecha/hora de adquisición como último componente:
      1.2.616.1.113780.{device}.1.{YYYYMMDDHHMMSS}
    Devuelve ("", "") si no se puede extraer.
    """
    uid = extract_study_uid(data, chunks)
    if not uid:
        return "", ""
    last = uid.rsplit(".", 1)[-1]
    if len(last) == 14 and last.isdigit():
        return last[:8], last[8:14]
    return "", ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_dicom_pn(name: str) -> str:
    """Convierte nombre Revo filename → DICOM PN (Apellidos^Nombres)."""
    if not name or "^" in name:
        return name
    if "_" in name:
        parts = name.split("_", 1)
        return f"{parts[0].strip()}^{parts[1].strip()}"
    return name


# ── Construcción DICOM ────────────────────────────────────────────────────────

def build_dicom_oct(
    bscans: list[np.ndarray],
    noel_id: str,
    study_date: str,
    laterality: str,
    patient_name: str = "",
    patient_dob: str = "",
    study_type: str = "",
    patient_sex: str = "",
    study_time: str = "",
    study_instance_uid: Optional[str] = None,
    study_description: str = "",
    source_file: str = "",
    sqi: Optional[np.ndarray] = None,
    traj: Optional[np.ndarray] = None,
    params_oct: Optional[dict] = None,
) -> Dataset:
    """Construye un Dataset DICOM OphthalmicTomographyImageStorage multi-frame.

    Args:
        bscans:              Lista de B-scans como arrays (height, width) uint8.
        noel_id:             PatientID formato NOEL.
        study_date:          Fecha del estudio YYYYMMDD.
        laterality:          "R" o "L".
        patient_name:        Nombre del paciente (DICOM PN).
        patient_dob:         Fecha de nacimiento YYYYMMDD.
        study_instance_uid:  StudyInstanceUID (si None se genera uno).
        source_file:         Nombre del archivo .opt de origen (trazabilidad).
        sqi:                 (n_frames,) float32 Signal Quality Index por B-scan.
                             Se almacena como private tag (0009,1011) y su media en (0009,1012).
        traj:                (n_frames, 4) float32 posición [x_start, y, x_end, y] en mm.
                             Se almacena como private tag (0009,1013); spacing en (0009,1014).
        params_oct:          dict de parse_octparams() para añadir tags de resolución espacial.

    Returns:
        Dataset pydicom listo para guardar con save_as().
    """
    if not bscans:
        raise ValueError("No hay B-scans para construir el DICOM.")

    n_frames = len(bscans)
    height, width = bscans[0].shape

    # Normalizar todas las frames al mismo tamaño
    pixel_array = np.stack(
        [b if b.shape == (height, width) else
         np.zeros((height, width), dtype=np.uint8)
         for b in bscans],
        axis=0,
    )  # shape: (n_frames, height, width)

    # ── File Meta ──────────────────────────────────────────────────────────────
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID    = _SOP_OPT
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID          = RETINAOS_TRANSFER_SYNTAX
    file_meta.ImplementationClassUID     = "1.2.3.4.5"

    # ── Dataset principal ──────────────────────────────────────────────────────
    ds = Dataset()
    ds.file_meta      = file_meta

    # Identifiers
    ds.SOPClassUID    = _SOP_OPT
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID

    # Patient
    ds.PatientID        = noel_id
    ds.PatientName      = _to_dicom_pn(patient_name) if patient_name else noel_id
    ds.PatientBirthDate = patient_dob or dob_from_noel(noel_id)
    ds.PatientSex       = patient_sex or ""

    # Study
    ds.StudyInstanceUID  = study_instance_uid or generate_uid()
    ds.StudyDate         = study_date
    ds.StudyTime         = study_time
    ds.StudyID           = ""
    ds.AccessionNumber   = ""
    ds.StudyDescription  = study_description or study_description_label(study_type, laterality)
    ds.ReferringPhysicianName = ""

    # Series
    ds.SeriesInstanceUID = generate_uid()
    ds.SeriesNumber      = 1
    _lat_str  = "OD" if laterality == "R" else "OS"
    _type_lbl = study_type.replace("_", " ").title() if study_type else "OCT"
    ds.SeriesDescription = f"Revo FC130 {_type_lbl} {_lat_str}"
    ds.SeriesDate        = study_date
    ds.SeriesTime        = study_time
    ds.Modality          = "OPT"
    ds.ProtocolName      = study_type
    ds.BodyPartExamined  = "EYE ANTERIOR SEGMENT" if study_type in ("anterior", "anterior_segment") else "EYE"

    from pydicom.sequence import Sequence as DicomSequence
    from pydicom.dataset import Dataset as DicomDataset
    _anat = DicomDataset()
    if study_type in ("anterior", "anterior_segment"):
        _anat.CodeValue              = "T-AA700"
        _anat.CodingSchemeDesignator = "SRT"
        _anat.CodeMeaning            = "Anterior segment of eye"
    else:
        _anat.CodeValue              = "T-AA610"
        _anat.CodingSchemeDesignator = "SRT"
        _anat.CodeMeaning            = "Retina"
    ds.AnatomicRegionSequence = DicomSequence([_anat])

    # Instance
    ds.InstanceNumber       = 1
    ds.AcquisitionDate      = study_date
    ds.AcquisitionTime      = study_time
    ds.ContentDate          = study_date
    ds.ContentTime          = study_time

    # Equipment
    ds.Manufacturer             = MANUFACTURER_OPTOPOL
    ds.ManufacturerModelName    = MODEL_REVO
    ds.SoftwareVersions         = "Transducin"

    # Image — OphthalmicTomographyImageStorage requiere estos tags
    ds.Laterality               = laterality
    ds.ImageLaterality          = laterality
    ds.SamplesPerPixel          = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows                     = height
    ds.Columns                  = width
    ds.BitsAllocated            = 8
    ds.BitsStored               = 8
    ds.HighBit                  = 7
    ds.PixelRepresentation      = 0  # unsigned
    ds.NumberOfFrames           = n_frames

    # Window óptimo desde percentiles del pixel data real
    flat = pixel_array.ravel().astype(float)
    p2, p98 = float(np.percentile(flat, 2)), float(np.percentile(flat, 98))
    ds.WindowCenter = int(round((p2 + p98) / 2))
    ds.WindowWidth  = max(1, int(round(p98 - p2)))

    # Pixel data
    ds.PixelData = pixel_array.tobytes()

    # Tag privado de trazabilidad Transducin
    if source_file:
        ds.add_new((0x0009, 0x0010), "LO", MANUFACTURER)
        ds.add_new((0x0009, 0x1010), "LO", str(Path(source_file).name)[:64])

    # ── Tags de resolución espacial (estándar OphthalmicTomography) ───────────
    if params_oct:
        ds.add_new((0x0022, 0x0035), "FL", float(params_oct.get("axial_um",  _DEFAULT_AXIAL_UM_PX)))
        ds.add_new((0x0022, 0x0037), "FL", float(params_oct.get("lateral_um", 15.0)))
        ds.add_new((0x0022, 0x0039), "CS", "HORIZONTAL")
        axial_mm   = params_oct.get("axial_um",  _DEFAULT_AXIAL_UM_PX) / 1000.0
        lateral_mm = params_oct.get("lateral_um", 15.0) / 1000.0
        ds.PixelSpacing = [axial_mm, lateral_mm]

    # ── SQI (Signal Quality Index) ─────────────────────────────────────────────
    # (0009,1011) OB  raw float32 array por B-scan
    # (0009,1012) DS  SQI medio del cubo
    if sqi is not None and len(sqi) == n_frames:
        ds.add_new((0x0009, 0x1011), "OB", sqi.astype("<f4").tobytes())
        ds.add_new((0x0009, 0x1012), "DS", f"{float(sqi.mean()):.4f}")

    # ── TRAJ (posiciones B-scan en mm) ─────────────────────────────────────────
    # (0009,1013) OB  raw float32[n_frames, 4] [x_start, y, x_end, y]
    # (0009,1014) DS  espaciado entre B-scans (|Δy| mm) — útil para reconstrucción 3D
    if traj is not None and traj.shape[0] == n_frames:
        ds.add_new((0x0009, 0x1013), "OB", traj.astype("<f4").tobytes())
        if traj.shape[0] >= 2:
            spacing_mm = float(np.abs(traj[1, 1] - traj[0, 1]))
            ds.add_new((0x0009, 0x1014), "DS", f"{spacing_mm:.6f}")

    return ds


# ── API pública ───────────────────────────────────────────────────────────────

def _resize_enface_aspect(image: np.ndarray, n_bscans: int, n_ascans: int) -> np.ndarray:
    """Resize en-face image to correct aspect ratio (square for equal-extent scans).

    The FNDSRECO en-face is (n_bscans, n_ascans) which is typically very
    non-square (e.g. 168x1024) even though the scan area is square (10x10mm).
    This resamples the slow axis (rows) to match the fast axis pixel count
    using nearest-neighbor interpolation to avoid blurring.

    Only resizes if the aspect ratio is > 2:1; otherwise returns unchanged.
    """
    h, w = image.shape
    if h == 0 or w == 0:
        return image
    ratio = w / h
    if ratio <= 2.0:
        return image  # already reasonable

    # Target: same number of rows as columns (square scan area)
    new_h = w
    # Nearest-neighbor resize using numpy (no PIL dependency)
    row_indices = np.linspace(0, h - 1, new_h).astype(int)
    resized = image[row_indices, :]
    logger.debug("En-face resize: (%d, %d) -> (%d, %d)", h, w, new_h, w)
    return resized


def build_dicom_enface(
    image: np.ndarray,
    noel_id: str,
    study_date: str,
    laterality: str,
    image_type: str = "SLO",
    patient_name: str = "",
    patient_dob: str = "",
    patient_sex: str = "",
    study_type: str = "",
    study_time: str = "",
    study_instance_uid: Optional[str] = None,
    study_description: str = "",
    source_file: str = "",
    pixel_spacing: Optional[list[float]] = None,
) -> Dataset:
    """Construye un Dataset DICOM OphthalmicPhotography8BitImageStorage de un frame.

    Usado para imágenes en-face del Revo FC130: SLO (EYE), proyección OCT
    (FNDSRECO), y preview OCTA (ANGPRV).

    Args:
        image:              Array (height, width) uint8.
        noel_id:            PatientID formato NOEL.
        study_date:         Fecha del estudio YYYYMMDD.
        laterality:         "R" o "L".
        image_type:         "SLO" | "FNDSRECO" | "ANGPRV" — afecta SeriesDescription.
        patient_name:       Nombre del paciente (DICOM PN).
        patient_dob:        Fecha de nacimiento YYYYMMDD.
        study_instance_uid: StudyInstanceUID (si None se genera uno).
        source_file:        Nombre del archivo .opt de origen (trazabilidad).

    Returns:
        Dataset pydicom listo para guardar con save_as().
    """
    from transducin.dicom_config import SOP_FUNDUS_PHOTO
    if image.ndim != 2:
        raise ValueError(f"build_dicom_enface espera array 2D, recibió shape={image.shape}")

    height, width = image.shape

    sop_uid = generate_uid()
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID    = SOP_FUNDUS_PHOTO
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID          = RETINAOS_TRANSFER_SYNTAX
    file_meta.ImplementationClassUID     = "1.2.3.4.5"

    ds = Dataset()
    ds.file_meta      = file_meta
    ds.SOPClassUID    = SOP_FUNDUS_PHOTO
    ds.SOPInstanceUID = sop_uid

    ds.PatientID        = noel_id
    ds.PatientName      = _to_dicom_pn(patient_name) if patient_name else noel_id
    ds.PatientBirthDate = patient_dob or dob_from_noel(noel_id)
    ds.PatientSex       = patient_sex or ""

    ds.StudyInstanceUID  = study_instance_uid or generate_uid()
    ds.StudyDate         = study_date
    ds.StudyTime         = study_time
    ds.StudyID           = ""
    ds.AccessionNumber   = ""
    ds.StudyDescription  = study_description or study_description_label(study_type, laterality)
    ds.ReferringPhysicianName = ""

    lat_str = "OD" if laterality == "R" else "OS"
    desc_map = {
        "SLO":      f"Revo FC130 SLO {lat_str}",
        "FNDSRECO": f"Revo FC130 OCT En-Face Projection {lat_str}",
        "ANGPRV":   f"Revo FC130 OCTA En-Face Preview {lat_str}",
    }
    ds.SeriesInstanceUID  = generate_uid()
    ds.SeriesNumber       = 2
    ds.SeriesDescription  = desc_map.get(image_type, f"Revo FC130 {image_type} {lat_str}")
    ds.SeriesDate         = study_date
    ds.SeriesTime         = study_time
    ds.Modality           = "OP"  # Ophthalmic Photography
    ds.ProtocolName       = study_type
    ds.BodyPartExamined   = "EYE ANTERIOR SEGMENT" if study_type in ("anterior", "anterior_segment") else "EYE"

    from pydicom.sequence import Sequence as DicomSequence
    from pydicom.dataset import Dataset as DicomDataset
    _anat = DicomDataset()
    if study_type in ("anterior", "anterior_segment"):
        _anat.CodeValue              = "T-AA700"
        _anat.CodingSchemeDesignator = "SRT"
        _anat.CodeMeaning            = "Anterior segment of eye"
    else:
        _anat.CodeValue              = "T-AA610"
        _anat.CodingSchemeDesignator = "SRT"
        _anat.CodeMeaning            = "Retina"
    ds.AnatomicRegionSequence = DicomSequence([_anat])

    ds.InstanceNumber       = 1
    ds.AcquisitionDate      = study_date
    ds.AcquisitionTime      = study_time
    ds.ContentDate          = study_date
    ds.ContentTime          = study_time

    ds.Manufacturer            = MANUFACTURER_OPTOPOL
    ds.ManufacturerModelName   = MODEL_REVO
    ds.SoftwareVersions        = "Transducin"

    ds.Laterality               = laterality
    ds.ImageLaterality          = laterality
    ds.SamplesPerPixel          = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows                     = height
    ds.Columns                  = width
    ds.BitsAllocated            = 8
    ds.BitsStored               = 8
    ds.HighBit                  = 7
    ds.PixelRepresentation      = 0

    if pixel_spacing is not None:
        ds.PixelSpacing = pixel_spacing

    if source_file:
        file_meta.SourceApplicationEntityTitle = "TRANSDUCIN"
        ds.ImageComments = f"Source: {source_file} [{image_type}]"

    ds.PixelData = image.tobytes()
    return ds


def read_opt(filepath: str | Path) -> dict:
    """Lee un archivo .opt Revo FC130 y retorna sus contenidos.

    Raises:
        FileNotFoundError: si el archivo no existe.
        ValueError: si el magic de 4 bytes no corresponde a un .opt Revo válido.

    Returns:
        dict con las siguientes claves:

        Metadatos del estudio:
          "study_uid"  (str | None)   — StudyInstanceUID extraído del chunk PARAMS.
          "n_frames"   (int)          — Número de B-scans decodificados.
          "shape"      (tuple[int,int]) — (height, width) en píxeles del primer B-scan,
                                         o (0, 0) si no hay B-scans.
          "params"     (dict)         — Parámetros OCTPARAMS: axial_um, lateral_um,
                                        scan_width_mm, n_bscans, n_ascans, depth_px.
          "myopi"      (dict | None)  — JSON de biometría del chunk MYOPI (solo BMETR):
                                        al, cct, k1, k2.

        Arrays de imagen:
          "bscans"     (list[np.ndarray]) — B-scans estructurales (height×width, uint8).
          "eye"        (np.ndarray | None) — Imagen SLO posterior chunk EYE (~504×378 uint8).
          "fndsreco"   (np.ndarray | None) — Proyección en-face OCT chunk FNDSRECO
                                             (n_ascans×n_bscans uint8).
          "angprv"     (np.ndarray | None) — Preview OCTA compuesto chunk ANGPRV
                                             (320×320 uint8, solo scans ANGIO).
          "octa_enface" (np.ndarray | None) — OCTA en-face MIP log desde chunks A0…AN
                                              (n_bscans×n_ascans uint8, solo ANGIO).

        Mediciones clínicas (None si el chunk de segmentación no está disponible):
          "cmt_um"     (float | None)         — CMT en µm (BM−ILM, círculo central 1mm).
          "etdrs"      (ETDRSGrid | None)      — ETDRS 9 sectores en µm.
          "rnfl"       (RNFLSectors | None)    — mRNFL macular sectores S/I/N/T en µm.
          "gcl_ipl"    (RNFLSectors | None)    — mGCIPL sectores S/I/N/T en µm.
          "cdr"        (float | None)          — C/D ratio desde ILM + DMARKERS.

        Arrays de calidad y posición:
          "sqi"        (np.ndarray | None) — SQI por B-scan (n_frames,) float32, rango 0–1.
          "traj"       (np.ndarray | None) — Posiciones espaciales B-scan (n_frames×4) float32
                                             [x_start, y, x_end, y] en mm.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(filepath)

    data   = filepath.read_bytes()
    if not data.startswith(_OPT_MAGIC):
        raise ValueError(f"No es un archivo .opt Revo válido (magic incorrecto): {filepath.name}")

    chunks = parse_opt_chunks(data)
    bscans = extract_bscans(data, chunks)
    myopi  = extract_myopi_json(data, chunks)
    uid    = extract_study_uid(data, chunks)
    params = parse_octparams(data, chunks)

    # Calidad de señal y posiciones espaciales
    sqi  = extract_sqi(data, chunks)
    traj = extract_traj(data, chunks)

    # Capas de segmentación
    nfl = extract_layer(data, chunks, "NFL")
    gcl = extract_layer(data, chunks, "GCL")
    inl = extract_layer(data, chunks, "INL")
    bm  = _extract_layer_with_fallback(data, chunks, "BM", "BOTTOM")
    top = extract_layer(data, chunks, "TOP")   # ILM — necesaria para C/D

    # Métricas con filtrado SQI (laterality se aplica en opt_extractor con el valor correcto)
    # CMT y ETDRS = grosor retinal total (BM − TOP/ILM). mRNFL macular = (NFL − TOP).
    cmt_um  = compute_cmt(top, bm, params, sqi=sqi)
    etdrs   = compute_etdrs(top, bm, params, sqi=sqi)
    rnfl    = compute_rnfl_sectors(nfl, gcl, params, sqi=sqi)
    gcl_ipl = compute_gcl_ipl(gcl, inl, params, sqi=sqi)

    # C/D ratio desde ILM morfología + bordes de disco DMARKERS
    cdr = compute_cup_disc_ratio(top, data, chunks, params)

    # Imágenes en-face I8
    eye      = decode_i8_image(data, chunks, "EYE")      # SLO alta resolución
    fndsrec  = decode_i8_image(data, chunks, "FNDSRECO") # Proyección en-face OCT
    angprv   = decode_i8_image(data, chunks, "ANGPRV")   # Preview OCTA compuesto (solo ANGIO)

    # OCTA en-face MIP desde chunks A0…AN (float16 decorrelación, solo ANGIO)
    octa_ef  = compute_octa_enface(data, chunks)

    sqi_str = f"mean={sqi.mean():.3f} n={len(sqi)}" if sqi is not None else "absent"
    shape = bscans[0].shape if bscans else (0, 0)
    logger.info(
        "read_opt: %s — %d B-scans %dx%d, CMT=%s µm, SQI=%s, C/D=%s, "
        "myopi=%s uid=%s EYE=%s FNDSRECO=%s ANGPRV=%s OCTA_MIP=%s",
        filepath.name, len(bscans), shape[1], shape[0],
        f"{cmt_um:.1f}" if cmt_um else "N/A",
        sqi_str,
        f"{cdr:.2f}" if cdr is not None else "N/A",
        "presente" if myopi else "ausente", uid,
        f"{eye.shape}" if eye is not None else "absent",
        f"{fndsrec.shape}" if fndsrec is not None else "absent",
        f"{angprv.shape}" if angprv is not None else "absent",
        f"{octa_ef.shape}" if octa_ef is not None else "absent",
    )
    return {
        "bscans":    bscans,
        "myopi":     myopi,
        "study_uid": uid,
        "n_frames":  len(bscans),
        "shape":     shape,
        "cmt_um":    cmt_um,
        "etdrs":     etdrs,
        "rnfl":      rnfl,
        "gcl_ipl":   gcl_ipl,
        "cdr":       cdr,
        "sqi":       sqi,
        "traj":      traj,
        "params":    params,
        "eye":       eye,
        "fndsreco":  fndsrec,
        "angprv":    angprv,
        "octa_enface": octa_ef,  # float16 log-MIP en-face OCTA (solo ANGIO)
    }


def opt_to_dicom(
    filepath: str | Path,
    output_dir: str | Path,
    noel_id: str,
    study_date: str,
    laterality: str,
    patient_name: str = "",
    patient_dob: str = "",
    patient_sex: str = "",
    study_type: str = "",
    study_description: str = "",
    study_time: str = "",
) -> list[Path]:
    """Convierte un .opt Revo a DICOM(s).

    Genera los DICOM disponibles según los chunks presentes en el .opt:
      - <stem>_OCT.dcm       — OphthalmicTomographyImageStorage (B-scans), si hay frames válidos
      - <stem>_SLO.dcm       — OphthalmicPhotography8Bit (chunk EYE), si disponible (todos los tipos)
      - <stem>_ENFACE.dcm    — OphthalmicPhotography8Bit (proyección FNDSRECO), si disponible
      - <stem>_ANGPRV.dcm    — OphthalmicPhotography8Bit (preview ANGPRV), si disponible
      - <stem>_OCTA_MIP.dcm  — OphthalmicPhotography8Bit (MIP decorrelación A0…AN), si disponible

    Args:
        filepath:     Ruta al archivo .opt.
        output_dir:   Directorio donde se guardarán los .dcm.
        noel_id:      PatientID formato NOEL (ej. JAHJ19870831).
        study_date:   Fecha del estudio YYYYMMDD.
        laterality:   "R" (OD) o "L" (OS).
        patient_name: Nombre en formato DICOM PN (opcional).
        patient_dob:  Fecha de nacimiento YYYYMMDD (opcional; se deriva de noel_id si vacío).
        study_type:   Tipo de estudio ("macular", "optic_nerve", etc.) para StudyDescription.

    Returns:
        Lista de Paths a los .dcm generados (todos los tipos de imagen del scan).
    """
    filepath   = Path(filepath)
    _stem_m = re.match(r'^([A-Z]{4})(\d{8})_', filepath.stem)
    if _stem_m:
        if not noel_id:
            noel_id = _stem_m.group(1) + _stem_m.group(2)
        if not patient_dob:
            patient_dob = _stem_m.group(2)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result  = read_opt(filepath)
    stem    = filepath.stem.replace(" ", "_")
    uid     = result["study_uid"]

    # When study_date/time are not provided by the caller, extract them from the
    # StudyInstanceUID embedded in PARAMS (format: ...1.YYYYMMDDHHMMSS).
    if not study_date or not study_time:
        raw_bytes = filepath.read_bytes()
        _chunks   = parse_opt_chunks(raw_bytes)
        _d, _t    = extract_study_datetime(raw_bytes, _chunks)
        if not study_date:
            study_date = _d
        if not study_time:
            study_time = _t

    common = dict(
        noel_id            = noel_id,
        study_date         = study_date,
        study_time         = study_time,
        laterality         = laterality,
        patient_name       = patient_name,
        patient_dob        = patient_dob or dob_from_noel(noel_id),
        patient_sex        = patient_sex,
        study_type         = study_type,
        study_instance_uid = uid,
        study_description  = study_description,
        source_file        = filepath.name,
    )

    generated: list[Path] = []

    # ── OCT cube (B-scans) ────────────────────────────────────────────────────
    # Filter out degenerate placeholder frames (e.g. fundus .opt T0 = 1×1 px)
    valid_bscans = [b for b in result["bscans"] if min(b.shape) > 1]
    if valid_bscans:
        ds = build_dicom_oct(
            bscans=valid_bscans,
            sqi=result.get("sqi"),
            traj=result.get("traj"),
            params_oct=result.get("params"),
            **common,
        )
        out = output_dir / f"{stem}_OCT.dcm"
        ds.save_as(str(out), write_like_original=False)
        h, w = valid_bscans[0].shape
        logger.info("DICOM OCT: %s (%d frames %dx%d)", out.name, len(valid_bscans), w, h)
        generated.append(out)
    else:
        logger.info("opt_to_dicom: sin B-scans válidos en %s", filepath.name)

    # ── En-face SLO (EYE chunk) ───────────────────────────────────────────────
    if result.get("eye") is not None:
        ds_slo = build_dicom_enface(
            image=result["eye"], image_type="SLO", **common
        )
        out_slo = output_dir / f"{stem}_SLO.dcm"
        ds_slo.save_as(str(out_slo), write_like_original=False)
        logger.info("DICOM SLO: %s %s", out_slo.name, result["eye"].shape)
        generated.append(out_slo)

    # ── En-face OCT projection (FNDSRECO chunk) ───────────────────────────────
    if result.get("fndsreco") is not None:
        enface_raw = result["fndsreco"]
        params = result.get("params", {})
        n_b = params.get("n_bscans", enface_raw.shape[0])
        n_a = params.get("n_ascans", enface_raw.shape[1])
        enface_sq = _resize_enface_aspect(enface_raw, n_b, n_a)

        # PixelSpacing after resize (both axes now same physical extent)
        scan_mm = params.get("scan_width_mm", 10.0)
        ef_ps = [scan_mm / enface_sq.shape[0], scan_mm / enface_sq.shape[1]]

        ds_ef = build_dicom_enface(
            image=enface_sq, image_type="FNDSRECO",
            pixel_spacing=ef_ps, **common
        )
        out_ef = output_dir / f"{stem}_ENFACE.dcm"
        ds_ef.save_as(str(out_ef), write_like_original=False)
        logger.info("DICOM ENFACE: %s %s (resized from %s)", out_ef.name, enface_sq.shape, enface_raw.shape)
        generated.append(out_ef)

    # ── ANGIO preview (ANGPRV chunk) ──────────────────────────────────────────
    if result.get("angprv") is not None:
        ds_ang = build_dicom_enface(
            image=result["angprv"], image_type="ANGPRV", **common
        )
        out_ang = output_dir / f"{stem}_ANGPRV.dcm"
        ds_ang.save_as(str(out_ang), write_like_original=False)
        logger.info("DICOM ANGPRV: %s %s", out_ang.name, result["angprv"].shape)
        generated.append(out_ang)

    # ── OCTA en-face MIP (desde chunks A0...AN, float16 decorrelacion) ────────
    if result.get("octa_enface") is not None:
        octa_raw = result["octa_enface"]
        _p = result.get("params") or {}
        _n_b = _p.get("n_bscans", octa_raw.shape[0])
        _n_a = _p.get("n_ascans", octa_raw.shape[1])
        _scan_mm = _p.get("scan_width_mm", 3.0)
        octa_sq = _resize_enface_aspect(octa_raw, _n_b, _n_a)
        octa_ps = [_scan_mm / octa_sq.shape[0], _scan_mm / octa_sq.shape[1]]
        ds_octa = build_dicom_enface(
            image=octa_sq, image_type="OCTA_MIP",
            pixel_spacing=octa_ps, **common
        )
        out_octa = output_dir / f"{stem}_OCTA_MIP.dcm"
        ds_octa.save_as(str(out_octa), write_like_original=False)
        logger.info("DICOM OCTA_MIP: %s %s (resized from %s)", out_octa.name, octa_sq.shape, octa_raw.shape)
        generated.append(out_octa)

    if not generated:
        raise ValueError(f"No se generó ningún DICOM desde {filepath.name} — sin B-scans ni imágenes en-face")

    return generated


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convierte .opt Revo FC130 → DICOM OphthalmicTomographyImageStorage"
    )
    parser.add_argument("opt_file",    help="Archivo .opt de entrada")
    parser.add_argument("-o", "--output", default="Output", help="Directorio de salida")
    parser.add_argument("--noel",     default="",  help="PatientID NOEL (ej. JAHJ19870831)")
    parser.add_argument("--date",     default="",  help="StudyDate YYYYMMDD")
    parser.add_argument("--lat",      default="",  help="Lateralidad: R (OD) o L (OS)")
    parser.add_argument("--name",     default="",  help="PatientName DICOM PN")
    parser.add_argument("--dob",      default="",  help="PatientBirthDate YYYYMMDD")
    parser.add_argument("--info",     action="store_true", help="Solo mostrar info, no convertir")
    args = parser.parse_args()

    import logging as _log
    _log.basicConfig(level=_log.INFO)

    result = read_opt(args.opt_file)
    shape  = result["shape"]
    myopi  = result["myopi"]

    print(f"\n  Archivo : {Path(args.opt_file).name}")
    print(f"  B-scans : {result['n_frames']}  ({shape[1]}×{shape[0]} px uint8 cada uno)")
    print(f"  StudyUID: {result['study_uid'] or '(no disponible)'}")
    if myopi:
        lp = myopi.get("leftOriginalParams", {}).get("biometry", {})
        rp = myopi.get("rightOriginalParams", {}).get("biometry", {})
        print(f"  AL OD   : {rp.get('al', 'N/A'):.3f} mm" if rp.get("al") else "  AL OD   : N/A")
        print(f"  AL OS   : {lp.get('al', 'N/A'):.3f} mm" if lp.get("al") else "  AL OS   : N/A")
        print(f"  CCT OD  : {rp.get('cct', 0)*1000:.0f} μm" if rp.get("cct") else "  CCT OD  : N/A")
        print(f"  CCT OS  : {lp.get('cct', 0)*1000:.0f} μm" if lp.get("cct") else "  CCT OS  : N/A")

    if args.info:
        return

    # Inferir parámetros desde el filename si no se pasaron
    filepath = Path(args.opt_file)
    noel_id   = args.noel
    date      = args.date
    lat       = args.lat

    if not noel_id or not date or not lat:
        try:
            from transducin.opt_extractor import extract_from_opt
            cd = extract_from_opt(filepath)
            noel_id = noel_id or cd.noel_id or ""
            date    = date    or cd.study_date or ""
            lat     = lat     or cd.laterality or ""
        except Exception:
            pass

    if not noel_id:
        print("\n  ERROR: se requiere --noel (PatientID NOEL). Usa --info para solo ver datos.")
        return

    outs = opt_to_dicom(filepath, args.output, noel_id, date, lat,
                        patient_name=args.name, patient_dob=args.dob)
    for p in outs:
        print(f"\n  DICOM guardado: {p}")


# ── TESTS ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        main()
        sys.exit()

    import logging as _log
    _log.basicConfig(level=_log.WARNING)

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

    def check_true(label, condition, detail=""):
        global errors
        ok = bool(condition)
        print(f"  {'✓' if ok else '✗'} [{G+'PASS'+E if ok else R+'FAIL'+E}] {label}" +
              (f": {detail}" if detail else ""))
        if not ok:
            errors += 1

    print("\n══ revo_opt_reader — tests con archivos reales ══")

    import glob as _glob
    import tempfile

    opt_files = _glob.glob("input/REVO/**/processed/*.opt", recursive=True) + \
                _glob.glob("input/REVO/**/*.opt", recursive=True)
    oct_files   = [f for f in opt_files if "_OCT." in f]
    bmetr_files = [f for f in opt_files if "_BMETR." in f]

    if not oct_files:
        print("  [SKIP] No se encontraron archivos OCT .opt para tests")
        sys.exit(0)

    # ── Test 1: parse_opt_chunks ──────────────────────────────────────────────
    print("\n  [OCT]", oct_files[0].split("/")[-1])
    data   = open(oct_files[0], "rb").read()
    chunks = parse_opt_chunks(data)
    t_names = [k for k in chunks if k.startswith("T") and k[1:].isdigit()]
    check_true("chunks T encontrados (≥1)",   len(t_names) >= 1,  f"{len(t_names)} chunks T")
    check_true("chunk PARAMS presente",        "PARAMS" in chunks)

    # ── Test 2: extract_bscans ────────────────────────────────────────────────
    bscans = extract_bscans(data, chunks)
    check_true("bscans extraídos (≥1)",        len(bscans) >= 1,  f"{len(bscans)} B-scans")
    if bscans:
        h, w = bscans[0].shape
        check_true("B-scan tiene dimensiones válidas", w > 0 and h > 0, f"{w}×{h} px")
        check_true("B-scan dtype uint8",         bscans[0].dtype == np.uint8)
        check_true("B-scan tiene píxeles no nulos", bscans[0].max() > 0)

    # ── Test 3: opt_to_dicom (archivo OCT) ───────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        from transducin.opt_extractor import extract_from_opt
        cd = extract_from_opt(oct_files[0])
        noel = cd.noel_id or "GHSI20000101"
        try:
            outs = opt_to_dicom(oct_files[0], tmp, noel,
                                cd.study_date or "20000101",
                                cd.laterality or "L")
            out = outs[0]  # primer DICOM (OCT cube o SLO)
            check_true("DICOM generado existe", out.exists(), out.name)

            ds = pydicom.dcmread(str(out))
            check("PatientID en DICOM",    ds.PatientID, noel)
            check("Modality es OPT",       str(ds.Modality), "OPT")
            check("SOPClassUID OPT",       str(ds.SOPClassUID), str(_SOP_OPT))
            check_true("NumberOfFrames > 0", int(ds.NumberOfFrames) > 0,
                       str(ds.NumberOfFrames))
            check_true("PixelData presente",   hasattr(ds, "PixelData"))
            print(f"       → {ds.NumberOfFrames} frames {ds.Columns}×{ds.Rows}px")
        except Exception as ex:
            print(f"  ✗ [{R}FAIL{E}] opt_to_dicom: {ex}")
            errors += 1

    # ── Test 4: MYOPI desde BMETR ────────────────────────────────────────────
    if bmetr_files:
        print(f"\n  [BMETR] {bmetr_files[0].split('/')[-1]}")
        result = read_opt(bmetr_files[0])
        myopi  = result["myopi"]
        check_true("MYOPI JSON extraído", myopi is not None)
        if myopi:
            lp = myopi.get("leftOriginalParams", {}).get("biometry", {})
            rp = myopi.get("rightOriginalParams", {}).get("biometry", {})
            al_l = lp.get("al")
            al_r = rp.get("al")
            check_true("AL OS presente (float)", isinstance(al_l, float),
                       f"{al_l:.3f} mm" if al_l else "None")
            check_true("AL OD presente (float)", isinstance(al_r, float),
                       f"{al_r:.3f} mm" if al_r else "None")

    print(f"\n{'══ TODOS LOS TESTS PASARON ══' if errors == 0 else f'══ {errors} FALLARON ══'}\n")
    sys.exit(0 if errors == 0 else 1)
