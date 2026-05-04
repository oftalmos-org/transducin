# transducin/cirrus_pdf_extractor.py
# SPDX-License-Identifier: Apache-2.0
#
# Extractor de datos clínicos desde reportes PDF del Zeiss Cirrus HD-OCT 500.
#
# Tipos de reporte soportados:
#   1. Macular Thickness OU Analysis (Macular Cube 512×128)
#      → ETDRS 9 sectores por ojo (OD + OS)
#   2. ONH and RNFL OU Analysis (Optic Disc Cube 200×200)
#      → RNFL cuadrantes + CDR por ojo (OD + OS)
#
# Limitaciones conocidas:
#   - Los valores numéricos están en imágenes bitmap, no en texto del PDF.
#   - OCR basado en Tesseract 5.x: confiable para ETDRS (≥95%), parcial para RNFL (60-80%).
#   - Valores RNFL Nasal/Temporal pueden no extraerse si el contraste es bajo.
#
# Dependencias Python: pdfplumber (MIT), pdf2image (MIT), pytesseract, pillow
# Dependencia de sistema: poppler
#   macOS:   brew install poppler
#   Windows: conda install -c conda-forge poppler
#
# Uso:
#   from transducin.cirrus_pdf_extractor import extract_from_pdf, extract_batch
#   results = extract_from_pdf(Path("report.pdf"), noel_id="SILT19800101")

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber
import pytesseract
from PIL import Image

from transducin.clinical_data import ETDRSGrid, OCTClinicalData, RNFLSectors

logger = logging.getLogger(__name__)

# ── Constantes ──────────────────────────────────────────────────────────────
_VENDOR = "zeiss_cirrus"

# Rango válido de grosor retiniano (µm) para filtrar falsos positivos OCR
_THICKNESS_MIN = 100
_THICKNESS_MAX = 700

# Rango válido RNFL (µm)
_RNFL_MIN = 30
_RNFL_MAX = 250

# Threshold OCR para binarización de imágenes (empírico, Cirrus HD-OCT 500 v8-11)
_ETDRS_THRESH = 50
_RNFL_THRESH_MAX = 80   # píxeles oscuros para aislar texto negro (fondo coloreado)

# Regiones espaciales en el grid ETDRS (fracción normalizada sobre el tamaño de la imagen)
# Determinadas empíricamente con imágenes 390×390 px del Cirrus Macular Thickness OU
#   Center ≈ (195, 195)
#   Superior Outer  y < 0.18
#   Superior Inner  0.23 < y < 0.42  AND  |x - cx| < 0.15
#   Inferior Inner  0.56 < y < 0.72  AND  |x - cx| < 0.15
#   Inferior Outer  y > 0.76
#   Left Outer      x < 0.18        AND  |y - cy| < 0.15
#   Left Inner      0.23 < x < 0.42 AND  |y - cy| < 0.15
#   Right Inner     0.56 < x < 0.75 AND  |y - cy| < 0.15
#   Right Outer     x > 0.76        AND  |y - cy| < 0.15

_CX = 0.50   # fracción x del centro
_CY = 0.50   # fracción y del centro
_BAND = 0.18  # semi-ancho de la banda axial/medial
_CENTER_R = 0.12  # radio máximo para zona central (fracción del tamaño de imagen)

# ── Regex ────────────────────────────────────────────────────────────────────
# Nombre de archivo Cirrus:
#   "Apellido_Nombre__ID_DOB_Sex_ScanType_ScanDT_Lat_AnalysisType_AnalysisDT.pdf"
_FN_RE = re.compile(
    r"^(?P<lname>[^_]+)_(?P<fname>[^_]+)__"
    r"(?P<patient_id>[^_]+)_"
    r"(?P<dob>\d{8})_"
    r"(?P<sex>Male|Female)_"
    r"(?P<scan_type>.+?)_"
    r"(?P<scan_dt>\d{14})_"
    r"(?P<laterality>OD|OS|OU)_"
    r"(?P<analysis_type>.+?)_"
    r"(?P<analysis_dt>\d{14})"
    r"\.pdf$"
)

_SIGNAL_RE = re.compile(r"(\d+)/10")
_DATE_RE    = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


# ── Helpers de parsing ───────────────────────────────────────────────────────

def _parse_filename(pdf_path: Path) -> dict:
    """Extrae metadata del nombre de archivo Cirrus.

    Returns dict con keys: patient_id, patient_name, dob, sex, scan_type,
    scan_date, scan_time, laterality, analysis_type, analysis_date.
    Vacío si el nombre no coincide con el patrón esperado.
    """
    m = _FN_RE.match(pdf_path.name)
    if not m:
        return {}
    g = m.groupdict()
    scan_dt: str = g["scan_dt"]    # YYYYMMDDHHmmss
    analysis_dt: str = g["analysis_dt"]
    return {
        "patient_id":    g["patient_id"],
        "patient_name":  f"{g['lname']}_{g['fname']}".replace(" ", "_"),
        "dob":           g["dob"],          # YYYYMMDD
        "sex":           g["sex"],
        "scan_type":     g["scan_type"],    # "Macular Cube 512x128" | "Optic Disc Cube 200x200"
        "scan_date":     scan_dt[:8],       # YYYYMMDD
        "scan_time":     scan_dt[8:],       # HHmmss
        "laterality":    g["laterality"],   # "OD" | "OS" | "OU"
        "analysis_type": g["analysis_type"],
        "analysis_date": analysis_dt[:8],
    }


def _parse_text_header(page) -> dict:
    """Extrae metadata del encabezado de texto del PDF.

    `page` es un pdfplumber.page.Page.
    Returns dict con keys: patient_name, patient_id, dob, sex, technician,
    exam_date_od, exam_time_od, signal_od, exam_date_os, exam_time_os,
    signal_os, serial_od, serial_os, sw_ver.
    """
    result: dict = {}

    text_full = page.extract_text() or ""

    # Extraer signal strengths (los números X/10 que hay dos)
    sigs = _SIGNAL_RE.findall(text_full)
    if len(sigs) >= 2:
        result["signal_od"] = int(sigs[0])
        result["signal_os"] = int(sigs[1])
    elif len(sigs) == 1:
        result["signal_od"] = int(sigs[0])

    # Extraer campos del header (layout fijo Cirrus):
    # Row 1: Name label, Name value, OD label, OS label, Exam Date label, ExamDate OD, ExamDate OS, [clinic]
    # Row 2: DOB label, DOB value
    # Row 3: Exam Time label, ExamTime OD, ExamTime OS
    # Row 4: Serial Number label, Serial OD, Serial OS
    # Row 5: Signal Strength label, Signal OD, Signal OS
    # Row 6: Technician label, Technician value
    lines = text_full.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if line == "Name:" and i + 1 < len(lines):
            result["patient_name"] = lines[i + 1].strip()
        elif line == "ID:" and i + 1 < len(lines):
            result["patient_id"] = lines[i + 1].strip()
        elif line == "DOB:" and i + 1 < len(lines):
            dob_raw = lines[i + 1].strip()
            m = _DATE_RE.match(dob_raw)
            if m:
                result["dob"] = f"{m.group(3)}{m.group(2)}{m.group(1)}"  # YYYYMMDD
        elif line == "Gender:" and i + 1 < len(lines):
            result["sex"] = lines[i + 1].strip()
        elif line == "Technician:" and i + 1 < len(lines):
            result["technician"] = lines[i + 1].strip()

    # Fechas de examen (dos fechas dd/mm/yyyy en la línea "Exam Date:")
    exam_dates = _DATE_RE.findall(text_full)
    if len(exam_dates) >= 2:
        result["exam_date_od"] = f"{exam_dates[0][2]}{exam_dates[0][1]}{exam_dates[0][0]}"
        result["exam_date_os"] = f"{exam_dates[1][2]}{exam_dates[1][1]}{exam_dates[1][0]}"
    elif len(exam_dates) == 1:
        result["exam_date_od"] = f"{exam_dates[0][2]}{exam_dates[0][1]}{exam_dates[0][0]}"

    return result


# ── OCR ETDRS grid ────────────────────────────────────────────────────────────

def _classify_etdrs_pos(cx_frac: float, cy_frac: float, eye: str) -> Optional[str]:
    """Asigna un valor OCR a un sector ETDRS según su posición normalizada.

    Convención de imagen Cirrus Macular Thickness OU:
      OD (izquierda): temporal=derecha, nasal=izquierda
      OS (derecha):   temporal=izquierda, nasal=derecha
    Retorna: 'C'|'S1'|'S2'|'I1'|'I2'|'N1'|'N2'|'T1'|'T2' | None

    Posiciones empíricas en imagen 390×390 px:
      C:  (190,182)=frac(0.487,0.467) → r≈0.035
      S2: (186, 30)=frac(0.477,0.077)
      S1: (186,112)=frac(0.477,0.287)
      T1_OD:(256,180)=frac(0.656,0.462)  N1_OS:(268,198)=frac(0.688,0.508)
      N1_OD:(114,178)=frac(0.292,0.456)  T1_OS:(130,196)=frac(0.333,0.503)
      T2_OD:(335,180)=frac(0.859,0.462)  N2_OS:(346,196)=frac(0.887,0.503)
      N2_OD: (37,180)=frac(0.095,0.462)  T2_OS: (50,196)=frac(0.128,0.503)
      I1: (186,247)=frac(0.477,0.633)
      I2: (186,329)=frac(0.477,0.844)
    """
    dx = cx_frac - _CX
    dy = cy_frac - _CY
    r = (dx * dx + dy * dy) ** 0.5

    # ── Centro (radio muy pequeño) ───────────────────────────────────────
    if r < _CENTER_R:
        return "C"

    # ── Eje vertical (Superior/Inferior) ─────────────────────────────────
    if cy_frac < 0.18 and abs(dx) < _BAND:
        return "S2"                 # Superior Outer
    if 0.22 < cy_frac < 0.43 and abs(dx) < _BAND:
        return "S1"                 # Superior Inner
    if 0.57 < cy_frac < 0.78 and abs(dx) < _BAND:
        return "I1"                 # Inferior Inner
    if cy_frac > 0.78 and abs(dx) < _BAND:
        return "I2"                 # Inferior Outer

    # ── Eje horizontal (Nasal/Temporal según ojo) ─────────────────────────
    if abs(dy) < _BAND:
        if cx_frac < 0.18:
            return "N2" if eye == "OD" else "T2"   # Left Outer
        if 0.22 < cx_frac < 0.43:
            return "N1" if eye == "OD" else "T1"   # Left Inner
        if 0.57 < cx_frac < 0.78:
            return "T1" if eye == "OD" else "N1"   # Right Inner
        if cx_frac > 0.78:
            return "T2" if eye == "OD" else "N2"   # Right Outer

    return None


def _ocr_single_int(text: str, min_v: int = _THICKNESS_MIN, max_v: int = _THICKNESS_MAX) -> Optional[int]:
    """Limpia y valida un texto OCR como entero en rango válido.
    Maneja errores comunes: '1435' → 143 (prefijo de 3 dígitos), '2/4' → 274.
    """
    # Reemplazar '/' por '7' (confusión común de Tesseract)
    clean = text.replace("/", "7").replace("|", "1").replace("?", "")
    # Extraer solo dígitos
    digits = re.sub(r"[^0-9]", "", clean)
    if not digits:
        return None
    # Si tiene > 3 dígitos, intentar truncar a 3
    if len(digits) > 3:
        # Tomar los primeros 3 dígitos que estén en rango
        for start in range(len(digits) - 2):
            candidate = int(digits[start:start + 3])
            if min_v <= candidate <= max_v:
                return candidate
        return None
    val = int(digits)
    if min_v <= val <= max_v:
        return val
    return None


def _ocr_etdrs_grid(img: Image.Image, eye: str) -> Optional[ETDRSGrid]:
    """Extrae la grilla ETDRS de una imagen de grid Cirrus (≈390×390 px).

    Args:
        img:  imagen PIL del grid (renderizada desde el PDF)
        eye:  'OD' | 'OS' (para asignar Nasal/Temporal correctamente)
    Returns ETDRSGrid con valores en µm, o None si no se detectaron suficientes.
    """
    # Upscale 4x para mejorar la precisión del OCR en imágenes pequeñas
    scale = 4
    img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    W, H = img.size
    gray = img.convert("L")
    proc = gray.point(lambda p: 255 if p > _ETDRS_THRESH else 0)

    data = pytesseract.image_to_data(
        proc, output_type=pytesseract.Output.DICT,
        config="--psm 11"
    )

    grid: dict[str, float] = {}
    for i, raw_text in enumerate(data["text"]):
        raw_text = raw_text.strip()
        if not raw_text:
            continue
        conf = int(data["conf"][i])
        if conf < 0:  # tesseract usa -1 para no-detección
            continue

        cx_frac = (data["left"][i] + data["width"][i] / 2.0) / W
        cy_frac = (data["top"][i] + data["height"][i] / 2.0) / H

        sector = _classify_etdrs_pos(cx_frac, cy_frac, eye)
        if sector is None:
            continue

        val = _ocr_single_int(raw_text)
        if val is None:
            continue

        # Si ya hay un valor para este sector, preferir el de mayor conf
        if sector not in grid or conf > grid.get(f"{sector}_conf", 0):
            grid[sector] = float(val)
            grid[f"{sector}_conf"] = conf

    # Filtrar claves de confianza
    clean: dict[str, Optional[float]] = {
        k: v for k, v in grid.items() if not k.endswith("_conf")
    }

    if not clean:
        return None

    return ETDRSGrid(
        C=clean.get("C"),
        S1=clean.get("S1"), N1=clean.get("N1"),
        I1=clean.get("I1"), T1=clean.get("T1"),
        S2=clean.get("S2"), N2=clean.get("N2"),
        I2=clean.get("I2"), T2=clean.get("T2"),
    )


# ── OCR RNFL cuadrante ────────────────────────────────────────────────────────

def _ocr_rnfl_quad_rendered(img: Image.Image, eye: str) -> dict[str, Optional[float]]:
    """Extrae valores de los 4 cuadrantes RNFL desde una imagen renderizada.

    La imagen debe ser el resultado de renderizar la región del círculo polar
    desde la página PDF a alta resolución (≥400 DPI).  Los números están
    impresos en negro sobre el fondo coloreado del mapa RNFL.

    Retorna dict con keys: 'superior', 'inferior', 'nasal', 'temporal'.
    """
    import numpy as np
    arr = np.array(img.convert("RGB"))
    H, W = arr.shape[:2]

    # Aislar píxeles muy oscuros (texto negro) — fondo se convierte en blanco
    dark_mask = (arr[:, :, 0] < _RNFL_THRESH_MAX) & \
                (arr[:, :, 1] < _RNFL_THRESH_MAX) & \
                (arr[:, :, 2] < _RNFL_THRESH_MAX)
    white = arr.copy()
    white[~dark_mask] = [255, 255, 255]
    img_clean = Image.fromarray(white)

    data = pytesseract.image_to_data(
        img_clean, output_type=pytesseract.Output.DICT,
        config="--psm 11 -c tessedit_char_whitelist=0123456789"
    )

    result: dict[str, Optional[float]] = {
        "superior": None, "inferior": None, "nasal": None, "temporal": None
    }

    for i, raw_text in enumerate(data["text"]):
        raw_text = raw_text.strip()
        if not raw_text or int(data["conf"][i]) < 0:
            continue
        val = _ocr_single_int(raw_text, _RNFL_MIN, _RNFL_MAX)
        if val is None:
            continue

        cx_frac = (data["left"][i] + data["width"][i] / 2.0) / W
        cy_frac = (data["top"][i] + data["height"][i] / 2.0) / H

        # Clasificar por posición en la imagen
        if cy_frac < 0.35:
            if result["superior"] is None:
                result["superior"] = float(val)
        elif cy_frac > 0.65:
            if result["inferior"] is None:
                result["inferior"] = float(val)
        elif 0.35 <= cy_frac <= 0.65:
            if cx_frac < 0.35:
                # Izquierda: Temporal para OD, Nasal para OS
                key = "temporal" if eye == "OD" else "nasal"
                if result[key] is None:
                    result[key] = float(val)
            elif cx_frac > 0.65:
                # Derecha: Nasal para OD, Temporal para OS
                key = "nasal" if eye == "OD" else "temporal"
                if result[key] is None:
                    result[key] = float(val)

    return result


# ── Extractor principal ───────────────────────────────────────────────────────

def _laterality_to_lr(lat: str, eye: str) -> str:
    """Convierte 'OD'/'OS' a 'R'/'L'."""
    if eye == "OD":
        return "R"
    if eye == "OS":
        return "L"
    return {"OD": "R", "OS": "L"}.get(eye, "")


def _make_study_date(date_str: str) -> str:
    """Acepta YYYYMMDD o DDMMYYYY y devuelve YYYYMMDD."""
    if not date_str or len(date_str) != 8:
        return date_str
    if date_str[:2] in ("19", "20"):
        return date_str
    return date_str[4:8] + date_str[2:4] + date_str[:2]


def extract_from_pdf(
    pdf_path: Path,
    noel_id: str = "",
    laterality: str = "",
) -> list[OCTClinicalData]:
    """Extrae OCTClinicalData desde un reporte PDF Cirrus.

    Args:
        pdf_path:   ruta al archivo .pdf
        noel_id:    identificador del paciente; si vacío se usa el del filename
        laterality: 'L' | 'R' | '' (solo relevante para PDFs de un ojo)
    Returns lista de OCTClinicalData, uno por ojo presente en el informe.
    """
    pdf_path = Path(pdf_path)
    fn_meta = _parse_filename(pdf_path)
    logger.debug("cirrus_pdf: %s meta=%s", pdf_path.name, fn_meta)

    try:
        doc = pdfplumber.open(str(pdf_path))
    except Exception as exc:
        logger.error("No se pudo abrir %s: %s", pdf_path, exc)
        return []

    try:
        page = doc.pages[0]
        hdr = _parse_text_header(page)

        pid = noel_id or fn_meta.get("patient_id", "")
        study_date = fn_meta.get("scan_date", "")
        study_time = fn_meta.get("scan_time", "")

        patient_name = hdr.get("patient_name") or fn_meta.get("patient_name", "")
        dob = hdr.get("dob") or _make_study_date(fn_meta.get("dob", ""))

        analysis_type: str = fn_meta.get("analysis_type", "")
        lat_fn: str = fn_meta.get("laterality", "OU")  # OD | OS | OU

        is_macular = "Macular Thickness" in analysis_type
        is_onh = "RNFL" in analysis_type or "ONH" in analysis_type
        is_ganglion = "Ganglion Cell" in analysis_type

        if is_macular:
            return _extract_macular(
                page, hdr, pid, patient_name, dob,
                study_date, study_time, lat_fn
            )
        elif is_onh:
            return _extract_onh(
                page, hdr, pid, patient_name, dob,
                study_date, study_time, lat_fn
            )
        elif is_ganglion:
            return _extract_ganglion(
                page, hdr, pid, patient_name, dob,
                study_date, study_time, lat_fn
            )
        else:
            logger.warning("Tipo de análisis no reconocido: '%s'", analysis_type)
            return []
    finally:
        doc.close()


def _extract_macular(
    page, hdr: dict,
    pid: str, patient_name: str, dob: str,
    study_date: str, study_time: str, lat_fn: str,
) -> list[OCTClinicalData]:
    """Extrae ETDRS de un informe Macular Thickness OU Analysis."""
    # pdfplumber: page.images devuelve dicts con x0, top, x1, bottom, srcsize (px)
    img_list = page.images

    # Filtrar imágenes aproximadamente cuadradas con tamaño > 100×100 px
    # srcsize = (width_px, height_px) de la imagen embebida
    grid_candidates = [
        im for im in img_list
        if im.get("srcsize", (0, 0))[0] > 100
        and im.get("srcsize", (0, 0))[1] > 100
        and abs(im["srcsize"][0] - im["srcsize"][1]) < 30
    ]

    # Ordenar por coordenada x0 del bbox (origen top-left en pdfplumber)
    grid_candidates.sort(key=lambda im: im.get("x0", 0))

    # Asignar: el de menor x0 es OD, el de mayor x0 es OS (convención Cirrus OU)
    eye_map: dict[str, dict] = {}
    if len(grid_candidates) >= 2:
        eye_map["OD"] = grid_candidates[0]
        eye_map["OS"] = grid_candidates[-1]
    elif len(grid_candidates) == 1:
        if lat_fn in ("OD",):
            eye_map["OD"] = grid_candidates[0]
        else:
            eye_map["OS"] = grid_candidates[0]
    else:
        logger.warning("No se encontraron imágenes de grid ETDRS en macular PDF")
        return []

    if lat_fn == "OU":
        eyes_to_extract = list(eye_map.keys())
    elif lat_fn == "OD":
        eyes_to_extract = ["OD"] if "OD" in eye_map else []
    else:
        eyes_to_extract = ["OS"] if "OS" in eye_map else []

    results: list[OCTClinicalData] = []

    for eye in eyes_to_extract:
        img_info = eye_map[eye]
        bbox = (img_info["x0"], img_info["top"], img_info["x1"], img_info["bottom"])

        try:
            # Renderiza la región del grid a 200 DPI via pdf2image + poppler.
            # within_bbox toma (x0, top, x1, bottom) en coords top-left de pdfplumber.
            img = page.within_bbox(bbox).to_image(resolution=200).original
        except Exception as exc:
            logger.warning("No se pudo renderizar grid ETDRS bbox=%s: %s", bbox, exc)
            continue

        etdrs = _ocr_etdrs_grid(img, eye)

        sig_key = "signal_od" if eye == "OD" else "signal_os"
        sig = hdr.get(sig_key)
        sqi = sig / 10.0 if sig is not None else None

        cmt: Optional[float] = etdrs.C if etdrs else None

        cd = OCTClinicalData(
            noel_id=pid,
            vendor=_VENDOR,
            source_file="",
            study_date=study_date,
            study_time=study_time,
            laterality=_laterality_to_lr("OU", eye),
            study_type="macular",
            patient_name=patient_name,
            patient_dob=dob,
            cmt_um=cmt,
            etdrs_grid=etdrs,
            sqi_mean=sqi,
            extraction_confidence="assumed",
        )
        if etdrs and etdrs.has_data():
            cd.add_note("ETDRS OCR desde imagen PDF Cirrus Macular Thickness OU")
        else:
            cd.add_note("OCR ETDRS fallido — imagen no legible")

        results.append(cd)
        logger.debug("cirrus_pdf macular %s: CMT=%.1f ETDRS=%s",
                     eye, cmt or 0, etdrs)

    return results


_RNFL_RENDER_DPI = 600


def _extract_onh(
    page, hdr: dict,
    pid: str, patient_name: str, dob: str,
    study_date: str, study_time: str, lat_fn: str,
) -> list[OCTClinicalData]:
    """Extrae RNFL cuadrantes y CDR de un informe ONH and RNFL OU Analysis."""
    img_list = page.images  # pdfplumber: dicts con x0, top, x1, bottom, srcsize

    # Círculos RNFL ≈116×116 px, top ≈ 518–620 pts desde arriba de la página.
    # quad_candidates[:2] toma los de menor top (cuadrantes) sobre los clock-hour.
    quad_candidates = [
        im for im in img_list
        if 90 <= im.get("srcsize", (0, 0))[0] <= 140
        and 90 <= im.get("srcsize", (0, 0))[1] <= 140
        and abs(im["srcsize"][0] - im["srcsize"][1]) < 10
        and 500 < im.get("top", 0) < 620
    ]
    # Ordenar por (top, x0) — primero los de menor top
    quad_candidates.sort(key=lambda im: (im.get("top", 0), im.get("x0", 0)))

    # eye_bbox en formato pdfplumber: (x0, top, x1, bottom), origen top-left
    eye_bbox: dict[str, tuple] = {}
    if len(quad_candidates) >= 2:
        by_x = sorted(quad_candidates[:2], key=lambda im: im.get("x0", 0))
        eye_bbox["OD"] = (by_x[0]["x0"],  by_x[0]["top"],
                          by_x[0]["x1"],  by_x[0]["bottom"])
        eye_bbox["OS"] = (by_x[-1]["x0"], by_x[-1]["top"],
                          by_x[-1]["x1"], by_x[-1]["bottom"])
    elif len(quad_candidates) == 1:
        im = quad_candidates[0]
        key = "OD" if lat_fn == "OD" else "OS"
        eye_bbox[key] = (im["x0"], im["top"], im["x1"], im["bottom"])

    if lat_fn == "OU":
        eyes_to_extract = ["OD", "OS"]
    elif lat_fn == "OD":
        eyes_to_extract = ["OD"]
    else:
        eyes_to_extract = ["OS"]

    results: list[OCTClinicalData] = []

    for eye in eyes_to_extract:
        sig_key = "signal_od" if eye == "OD" else "signal_os"
        sig = hdr.get(sig_key)
        sqi = sig / 10.0 if sig is not None else None

        rnfl_vals: dict = {}
        if eye in eye_bbox:
            bbox = eye_bbox[eye]
            try:
                # Renderiza la región del círculo RNFL a 600 DPI via pdf2image + poppler.
                img_rendered = (
                    page.within_bbox(bbox)
                    .to_image(resolution=_RNFL_RENDER_DPI)
                    .original
                )
                rnfl_vals = _ocr_rnfl_quad_rendered(img_rendered, eye)
            except Exception as exc:
                logger.warning("No se pudo renderizar RNFL quad bbox=%s: %s", bbox, exc)

        rnfl: Optional[RNFLSectors] = None
        if any(v is not None for v in rnfl_vals.values()):
            sup = rnfl_vals.get("superior")
            inf = rnfl_vals.get("inferior")
            nas = rnfl_vals.get("nasal")
            tmp = rnfl_vals.get("temporal")
            avail = [v for v in [sup, inf, nas, tmp] if v is not None]
            avg = round(sum(avail) / len(avail), 2) if len(avail) == 4 else None
            rnfl = RNFLSectors(
                global_avg=avg,
                superior=sup,
                inferior=inf,
                nasal=nas,
                temporal=tmp,
            )

        cd = OCTClinicalData(
            noel_id=pid,
            vendor=_VENDOR,
            source_file="",
            study_date=study_date,
            study_time=study_time,
            laterality=_laterality_to_lr("OU", eye),
            study_type="optic_nerve",
            patient_name=patient_name,
            patient_dob=dob,
            rnfl=rnfl,
            sqi_mean=sqi,
            extraction_confidence="assumed",
        )
        n_found = sum(1 for v in rnfl_vals.values() if v is not None) if rnfl_vals else 0
        if n_found == 4:
            cd.add_note("RNFL cuadrantes completos (4/4) OCR desde PDF Cirrus ONH")
        elif n_found > 0:
            found_keys = [k for k, v in rnfl_vals.items() if v is not None]
            cd.add_note(f"RNFL cuadrantes parciales ({n_found}/4 OCR): {found_keys}")
        else:
            cd.add_note("OCR RNFL fallido — imágenes con bajo contraste o no encontradas")

        results.append(cd)
        logger.debug("cirrus_pdf onh %s: RNFL=%s", eye, rnfl_vals)

    return results


def _extract_ganglion(
    page, hdr: dict,
    pid: str, patient_name: str, dob: str,
    study_date: str, study_time: str, lat_fn: str,
) -> list[OCTClinicalData]:
    """Extrae mGCIPL mínimo (Fovea) de un informe Ganglion Cell OU Analysis.

    El informe Cirrus imprime en texto: 'Fovea: X, Y' donde X = mGCIPL mínimo
    (µm, área central 6×6) y Y = índice del B-scan horizontal de referencia.
    Los sectores (sup/inf/nas/temp) están en imagen y no se extraen aquí.
    """
    text = page.extract_text() or ""

    # Encontrar todos los pares 'Fovea: X, Y' en orden de aparición
    # Cirrus OU: OD aparece antes que OS
    fovea_vals = [int(m) for m in re.findall(r"Fovea:\s*(\d+),\s*\d+", text)]

    eyes_map: dict[str, Optional[int]] = {"OD": None, "OS": None}
    if len(fovea_vals) >= 2:
        eyes_map["OD"] = fovea_vals[0]
        eyes_map["OS"] = fovea_vals[1]
    elif len(fovea_vals) == 1:
        if lat_fn == "OS":
            eyes_map["OS"] = fovea_vals[0]
        else:
            eyes_map["OD"] = fovea_vals[0]

    if lat_fn == "OU":
        eyes_to_extract = ["OD", "OS"]
    elif lat_fn == "OD":
        eyes_to_extract = ["OD"]
    else:
        eyes_to_extract = ["OS"]

    results: list[OCTClinicalData] = []
    for eye in eyes_to_extract:
        gcl_min = eyes_map.get(eye)
        sig_key = "signal_od" if eye == "OD" else "signal_os"
        sig = hdr.get(sig_key)
        sqi = sig / 10.0 if sig is not None else None

        gcl_sectors = RNFLSectors(global_avg=float(gcl_min)) if gcl_min is not None else None
        cd = OCTClinicalData(
            noel_id=pid,
            vendor=_VENDOR,
            source_file="",
            study_date=study_date,
            study_time=study_time,
            laterality=_laterality_to_lr("OU", eye),
            study_type="macular",
            patient_name=patient_name,
            patient_dob=dob,
            gcl_ipl=gcl_sectors,
            gcl_avg_um=float(gcl_min) if gcl_min is not None else None,
            sqi_mean=sqi,
            extraction_confidence="assumed",
        )
        if gcl_min is not None:
            cd.add_note(f"CONFIRMED: gcl_avg_um={gcl_min} µm — Fovea mGCIPL texto PDF Cirrus GCL OU")
        else:
            cd.add_note("ASSUMED: Fovea mGCIPL no encontrado en texto del PDF")
        results.append(cd)
        logger.debug("cirrus_pdf ganglion %s: gcl_min=%s µm", eye, gcl_min)

    return results


# ── Batch ─────────────────────────────────────────────────────────────────────

def extract_batch(
    folder: Path,
    noel_id: str = "",
    laterality: str = "",
    glob: str = "**/*.pdf",
) -> list[OCTClinicalData]:
    """Procesa todos los PDFs Cirrus en un directorio.

    Args:
        folder:    directorio raíz donde buscar PDFs
        noel_id:   paciente (si vacío, usa el del nombre de archivo)
        laterality: 'L' | 'R' | '' (para PDFs de un ojo sin metadata en filename)
        glob:      patrón de búsqueda (por defecto todos los .pdf recursivos)
    Returns lista de OCTClinicalData de todos los PDFs procesados.
    """
    folder = Path(folder)
    all_results: list[OCTClinicalData] = []
    pdfs = sorted(folder.glob(glob))
    logger.info("cirrus_pdf batch: %d PDFs en %s", len(pdfs), folder)
    for pdf in pdfs:
        try:
            results = extract_from_pdf(pdf, noel_id=noel_id, laterality=laterality)
            all_results.extend(results)
            logger.debug("  %s → %d estudios", pdf.name, len(results))
        except Exception as exc:
            logger.warning("Error procesando %s: %s", pdf.name, exc)
    return all_results
