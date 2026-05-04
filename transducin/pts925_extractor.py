# SPDX-License-Identifier: Apache-2.0
"""pts925_extractor.py — Extractor para el perímetro PTS 925Wi de Optopol.

El PTS 925Wi (Optopol Technology) exporta estudios de campo visual como
Secondary Capture DICOM (SOP 1.2.840.10008.5.1.4.1.1.7) con Modality OPV.
Cada estudio contiene hasta 3 tipos de archivo por ojo:
  - JPEG 2970×2100  : imagen del reporte impreso (páginas distintas)
  - PDF embebido    : reporte completo con todos los índices globales

La extracción de mediciones clínicas se realiza sobre el PDF embebido
mediante pdfplumber, igual que en cirrus_pdf_extractor.py.

SOP Class objetivo para re-exportación futura:
    1.2.840.10008.5.1.4.1.1.80  Ophthalmic Visual Field Static Perimetry
                                  Measurements Storage (DICOM PS3.3 C.8.30)

Índices globales extraídos del PDF:
    MDp  → md_db   (Mean Deviation dB, negativo = peor)
    PD   → psd_db  (Pattern Deviation / equivalente a PSD)
    MS   → notes   (Mean Sensitivity — no tiene equivalente en VisualFieldData)
    GHT  → ght     ("Outside Normal Limits", "Within Normal Limits", etc.)
    HK   → fixation_losses  (Heijl-Krakau ratio)
    FPOS → false_pos_pct
    FNEG → false_neg_pct
    SFo  → notes   (Short-term Fluctuation)

Campos NO disponibles desde PDF (requieren OCR de imagen):
    vfi_pct  — el PTS 925Wi usa MS en lugar de VFI
    points   — sensibilidades por punto (54 puntos 24-2)
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Optional

import pdfplumber
import pydicom

from transducin.clinical_data import VisualFieldData, VisualFieldPoint  # noqa: F401

logger = logging.getLogger("transducin.pts925")

# SOP Class UID para exportación futura
SOP_CLASS_VISUAL_FIELD = "1.2.840.10008.5.1.4.1.1.80"

# Identificación del equipo en tags DICOM
_MANUFACTURER      = "Optopol Technology"
_MODEL_PATTERN     = re.compile(r"PTS\s*9", re.IGNORECASE)


# ── Detección ─────────────────────────────────────────────────────────────────

def is_pts925_dicom(ds: pydicom.Dataset) -> bool:
    """True si el Dataset es un Secondary Capture OPV del PTS 925Wi."""
    return (
        str(getattr(ds, "Manufacturer", "")).strip() == _MANUFACTURER
        and bool(_MODEL_PATTERN.search(str(getattr(ds, "ManufacturerModelName", ""))))
        and str(getattr(ds, "Modality", "")).upper() == "OPV"
    )


def has_embedded_pdf(ds: pydicom.Dataset) -> bool:
    """True si el Dataset contiene un PDF encapsulado con resultados."""
    return (
        hasattr(ds, "EncapsulatedDocument")
        and str(getattr(ds, "MIMETypeOfEncapsulatedDocument", "")).startswith("application/pdf")
    )


# ── Extracción PDF ─────────────────────────────────────────────────────────────

def _parse_ratio(text: str, pattern: str) -> Optional[float]:
    """Extrae una fracción N/M del texto y devuelve el ratio float."""
    m = re.search(pattern, text)
    if not m:
        return None
    num, den = int(m.group(1)), int(m.group(2))
    if den == 0:
        return None
    return round(num / den, 4)


def _parse_vf_pdf(pdf_bytes: bytes, source_file: str = "") -> Optional[VisualFieldData]:
    """Extrae VisualFieldData del PDF embebido del PTS 925Wi."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(
                page.extract_text() or "" for page in pdf.pages
            )
    except Exception as exc:
        logger.warning("pts925: no se pudo abrir PDF embebido: %s", exc)
        return None

    if not text.strip():
        logger.warning("pts925: PDF embebido sin texto extraíble")
        return None

    vf = VisualFieldData(source_file=source_file)

    # ── PatientID / NOEL ────────────────────────────────────────────────────
    m = re.search(r"ID:\s*(\S+)", text)
    if m:
        vf.noel_id = m.group(1).strip()

    # ── Lateralidad ─────────────────────────────────────────────────────────
    m = re.search(r"Ojo:\s*(OD|OS)", text, re.IGNORECASE)
    if m:
        raw = m.group(1).upper()
        vf.laterality = "R" if raw == "OD" else "L"

    # ── Fecha del estudio ───────────────────────────────────────────────────
    m = re.search(r"Fecha:\s*(\d{2})-(\d{2})-(\d{4})", text)
    if m:
        vf.study_date = f"{m.group(3)}{m.group(2)}{m.group(1)}"  # YYYYMMDD

    # ── Patrón y estrategia (ej: "24-2 ZETA Fast") ─────────────────────────
    m = re.search(r"(\d+[-]\d+)\s+(ZETA\s+\w+|SITA\s+\w+|Full\s+Threshold)", text, re.IGNORECASE)
    if m:
        vf.test_pattern = m.group(1)          # "24-2"
        vf.strategy     = m.group(2).strip()  # "ZETA Fast"
    else:
        # Fallback: patrón sin estrategia reconocida
        m = re.search(r"\b(24-2|30-2|10-2|Macula)\b", text, re.IGNORECASE)
        if m:
            vf.test_pattern = m.group(1)
        m = re.search(r"Estrategia:\s*(.+)", text)
        if m:
            vf.strategy = m.group(1).strip()

    # ── Estímulo ─────────────────────────────────────────────────────────────
    m = re.search(r"Estímulo:\s*([IVX]+)", text)
    if m:
        vf.stimulus_size = m.group(1)

    # ── Blanco de fijación ───────────────────────────────────────────────────
    m = re.search(r"Blanco de fijación:\s*(\w+)", text, re.IGNORECASE)
    if m:
        vf.fixation_target = m.group(1).capitalize()

    # ── GHT (Glaucoma Hemifield Test) ───────────────────────────────────────
    # El GHT aparece en la línea siguiente a "GHT:"
    m = re.search(r"GHT:\s*\n\s*(.+)", text)
    if m:
        vf.ght = m.group(1).strip()
    else:
        m = re.search(r"GHT:\s*(.+)", text)
        if m:
            vf.ght = m.group(1).strip()

    # ── MDp (Mean Deviation) ────────────────────────────────────────────────
    m = re.search(r"MDp:\s*([+-]?\d+[\.,]\d+)\s*dB", text)
    if m:
        vf.md_db = float(m.group(1).replace(",", "."))

    # ── PD (Pattern Deviation — equivalente a PSD) ──────────────────────────
    m = re.search(r"\bPD:\s*(\d+[\.,]\d+)", text)
    if m:
        vf.psd_db = float(m.group(1).replace(",", "."))

    # ── Foveal threshold ─────────────────────────────────────────────────────
    m = re.search(r"Fovea:\s*([+-]?\d+[\.,]\d+)\s*dB", text)
    if m:
        vf.foveal_threshold_db = float(m.group(1).replace(",", "."))

    # ── Fixation losses — HK (Heijl-Krakau) ─────────────────────────────────
    vf.fixation_losses = _parse_ratio(text, r"HK:\s*(\d+)/(\d+)")

    # ── False positives ──────────────────────────────────────────────────────
    vf.false_pos_pct = _parse_ratio(text, r"FPOS:\s*(\d+)/(\d+)")

    # ── False negatives ──────────────────────────────────────────────────────
    vf.false_neg_pct = _parse_ratio(text, r"FNEG:\s*(\d+)/(\d+)")

    # ── Métricas adicionales → notes ─────────────────────────────────────────
    m_ms  = re.search(r"MS:\s*(\d+[\.,]\d+)\s*dB", text)
    m_sfo = re.search(r"SFo:\s*(\d+[\.,]\d+)", text)
    m_idv = re.search(r"IdV a 10º:\s*([\d.]+)\s*dB", text)
    if m_ms:
        vf.notes.append(f"MS={m_ms.group(1)} dB")
    if m_sfo:
        vf.notes.append(f"SFo={m_sfo.group(1)}")
    if m_idv:
        vf.notes.append(f"IdV10={m_idv.group(1)} dB")

    # ── Metadatos del equipo ─────────────────────────────────────────────────
    m = re.search(r"PTS\s+\S+:([\w/]+)", text)
    if m:
        vf.device_serial = m.group(1)
    m = re.search(r"SW:([\d.]+)", text)
    if m:
        vf.software_version = m.group(1)

    # ── Flags !! en índices → notas ─────────────────────────────────────────
    if re.search(r"MDp:[^\n]*!!", text):
        vf.notes.append("MDp p<0.01")
    elif re.search(r"MDp:[^\n]*!", text):
        vf.notes.append("MDp p<0.05")
    if re.search(r"\bPD:[^\n]*!!", text):
        vf.notes.append("PD p<0.01")
    elif re.search(r"\bPD:[^\n]*!", text):
        vf.notes.append("PD p<0.05")
    if re.search(r"FNEG:[^\n]*!!", text):
        vf.notes.append("FNEG alto p<0.01")
    elif re.search(r"FNEG:[^\n]*!", text):
        vf.notes.append("FNEG alto p<0.05")
    if re.search(r"HK:[^\n]*!", text):
        vf.notes.append("Fijación inestable")

    vf.extraction_confidence = "confirmed" if vf.md_db is not None else "unknown"
    return vf if vf.has_data() else None


# ── API pública ────────────────────────────────────────────────────────────────

def extract_from_dicom(dcm_path: str | Path) -> Optional[VisualFieldData]:
    """Extrae VisualFieldData de un Secondary Capture DICOM del PTS 925Wi.

    Args:
        dcm_path: ruta al archivo .dcm exportado por el PTS 925Wi.

    Returns:
        VisualFieldData si el archivo contiene un PDF con índices globales,
        None si el DICOM es solo imagen JPEG sin PDF embebido.

    Raises:
        FileNotFoundError: si el archivo no existe.
        ValueError: si el DICOM no es del PTS 925Wi.
    """
    dcm_path = Path(dcm_path)
    if not dcm_path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {dcm_path}")

    ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=False)

    if not is_pts925_dicom(ds):
        raise ValueError(
            f"El DICOM no corresponde al PTS 925Wi "
            f"(Manufacturer={getattr(ds, 'Manufacturer', '?')!r}, "
            f"Model={getattr(ds, 'ManufacturerModelName', '?')!r}, "
            f"Modality={getattr(ds, 'Modality', '?')!r})"
        )

    if not has_embedded_pdf(ds):
        logger.debug("pts925: %s es imagen JPEG, no PDF — sin datos estructurados",
                     dcm_path.name)
        return None

    pdf_bytes = bytes(ds.EncapsulatedDocument)
    logger.info("pts925: extrayendo PDF embebido de %s (%d bytes)",
                dcm_path.name, len(pdf_bytes))

    vf = _parse_vf_pdf(pdf_bytes, source_file=str(dcm_path))
    if vf is None:
        return None

    # Completar lateralidad desde tag DICOM si el PDF no la tiene
    if not vf.laterality:
        lat_tag = str(getattr(ds, "Laterality", "")).upper()
        if lat_tag in ("R", "L"):
            vf.laterality = lat_tag

    # Completar NOEL ID desde PatientID DICOM si no salió del PDF
    if not vf.noel_id:
        vf.noel_id = str(getattr(ds, "PatientID", "")).strip()

    # Fecha del estudio desde tag DICOM como fallback
    if not vf.study_date:
        vf.study_date = str(getattr(ds, "StudyDate", "")).strip()

    return vf


def extract_from_dicom_dir(dir_path: str | Path) -> list[VisualFieldData]:
    """Procesa todos los Secondary Capture DICOM del PTS 925Wi en un directorio.

    Devuelve una lista de VisualFieldData (uno por PDF embebido encontrado),
    ignorando los archivos JPEG-only y los que no sean del PTS 925Wi.

    Args:
        dir_path: directorio con los archivos .dcm exportados.

    Returns:
        Lista de VisualFieldData, posiblemente vacía si no hay PDFs.
    """
    dir_path = Path(dir_path)
    results: list[VisualFieldData] = []

    dcm_files = sorted(dir_path.glob("*.dcm"))
    if not dcm_files:
        logger.warning("pts925: no se encontraron .dcm en %s", dir_path)
        return results

    for f in dcm_files:
        try:
            ds = pydicom.dcmread(str(f), stop_before_pixels=True)
            if not is_pts925_dicom(ds):
                continue
            if not has_embedded_pdf(ds):
                logger.debug("pts925: %s — imagen JPEG, omitida", f.name)
                continue
        except Exception as exc:
            logger.warning("pts925: no se pudo leer %s: %s", f.name, exc)
            continue

        vf = extract_from_dicom(f)
        if vf is not None:
            results.append(vf)
            logger.info("pts925: extraído %s (%s) MD=%.2f dB GHT=%s",
                        f.name, vf.laterality, vf.md_db or 0, vf.ght or "?")

    return results


# ── Legacy stub (formato binario .pvf/.pts — no implementado) ─────────────────

def extract_from_pts(filepath: str | Path) -> Optional[VisualFieldData]:
    """Extrae VisualFieldData de un archivo binario propietario del PTS 925.

    El PTS 925Wi exporta vía DICOM (usar extract_from_dicom en su lugar).
    Esta función se mantiene para compatibilidad con versiones antiguas del
    software que pudieran exportar formatos binarios propietarios.

    Raises:
        NotImplementedError: el formato binario no está documentado.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {filepath}")
    raw = filepath.read_bytes()
    magic = raw[:4].hex()
    raise NotImplementedError(
        f"Formato binario PTS 925 no implementado. "
        f"magic={magic!r}  size={len(raw)} B  "
        f"Usar extract_from_dicom() para archivos .dcm del PTS 925Wi."
    )


# ── Tests internos ─────────────────────────────────────────────────────────────

def _run_tests() -> None:
    """Tests del extractor PTS 925Wi."""
    errs = 0

    def check(label, cond, detail=""):
        nonlocal errs
        status = "OK" if cond else "FAIL"
        if not cond:
            errs += 1
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))

    print("═══ pts925_extractor tests ═══")

    # ── Test 1: VisualFieldData instanciable ────────────────────────────────
    vf = VisualFieldData(
        noel_id="JAHJ19870831",
        laterality="L",
        study_date="20260226",
        md_db=-3.5,
        psd_db=4.2,
        ght="Within Normal Limits",
        points=[VisualFieldPoint(x_deg=-3, y_deg=3, threshold_db=28.0)],
    )
    check("VisualFieldData instanciable", vf.noel_id == "JAHJ19870831")
    check("has_data() con md_db", vf.has_data())

    # ── Test 2: extract_from_pts levanta NotImplementedError ────────────────
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pvf", delete=False) as tmp:
        tmp.write(b"\xA5\xA5\xA5\xFF" + b"\x00" * 100)
        tmp_path = tmp.name
    try:
        extract_from_pts(tmp_path)
        check("NotImplementedError levantado", False, "no se levantó la excepción")
    except NotImplementedError as e:
        check("NotImplementedError levantado", True, str(e)[:60])
    finally:
        os.unlink(tmp_path)

    # ── Test 3: SOP Class correcto ───────────────────────────────────────────
    check("SOP_CLASS_VISUAL_FIELD correcto",
          SOP_CLASS_VISUAL_FIELD == "1.2.840.10008.5.1.4.1.1.80")

    # ── Test 4: _parse_vf_pdf con texto de ejemplo ──────────────────────────
    import io as _io
    # Crear PDF mínimo con el texto de ejemplo
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        buf = _io.BytesIO()
        c = rl_canvas.Canvas(buf)
        c.drawString(10, 700, "Single Field Analysis Ojo: OD")
        c.drawString(10, 685, "ID: SPDP19700101")
        c.drawString(10, 670, "24-2 ZETA Fast")
        c.drawString(10, 655, "Fecha: 11-03-2026")
        c.drawString(10, 640, "HK: 0/31   FPOS: 1/31   FNEG: 4/29")
        c.drawString(10, 625, "Estrategia: ZETA Fast")
        c.drawString(10, 610, "GHT:")
        c.drawString(10, 595, "Outside Normal Limits")
        c.drawString(10, 580, "MS: 24.63 dB")
        c.drawString(10, 565, "MDp: -0.31 dB !")
        c.drawString(10, 550, "PD: 2.26 !!")
        c.drawString(10, 535, "SFo: 4.6")
        c.save()
        vf2 = _parse_vf_pdf(buf.getvalue(), "test_sample.dcm")
        if vf2:
            check("_parse_vf_pdf extrae MDp", vf2.md_db == -0.31, str(vf2.md_db))
            check("_parse_vf_pdf extrae PD", vf2.psd_db == 2.26, str(vf2.psd_db))
            check("_parse_vf_pdf extrae GHT", vf2.ght == "Outside Normal Limits", vf2.ght)
            check("_parse_vf_pdf extrae HK", vf2.fixation_losses == 0.0, str(vf2.fixation_losses))
            check("_parse_vf_pdf extrae lateralidad OD", vf2.laterality == "R", vf2.laterality)
            check("_parse_vf_pdf extrae patrón 24-2", vf2.test_pattern == "24-2", vf2.test_pattern)
        else:
            check("_parse_vf_pdf retorna datos", False, "retornó None")
    except ImportError:
        print("  [SKIP] Tests PDF con reportlab — reportlab no instalado")

    # ── Test 5: extract_from_dicom con archivos reales ───────────────────────
    import glob as _glob
    real_files = sorted(_glob.glob("input/PTS/export_*.dcm"))
    if real_files:
        found_pdfs = 0
        for f in real_files:
            try:
                vf_real = extract_from_dicom(f)
                if vf_real is not None:
                    found_pdfs += 1
                    check(f"Real PDF {Path(f).name[-20:]}: md_db",
                          vf_real.md_db is not None, str(vf_real.md_db))
                    check("Real PDF: laterality",
                          vf_real.laterality in ("R", "L"), vf_real.laterality)
                    check("Real PDF: noel_id",
                          bool(vf_real.noel_id), vf_real.noel_id)
            except ValueError:
                pass  # JPEG-only, esperado
            except Exception as e:
                check(f"Real {Path(f).name[-20:]}", False, str(e))
        check("Archivos reales: al menos 1 PDF extraído", found_pdfs >= 1,
              f"encontrados: {found_pdfs}")
        print(f"\n  Resumen: {found_pdfs} PDFs extraídos de {len(real_files)} DICOMs")
    else:
        print("  [SKIP] Sin archivos reales en input/PTS/")

    print(f"\n{'══ TODOS LOS TESTS PASARON ══' if errs == 0 else f'══ {errs} FALLARON ══'}\n")
    if errs:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_tests()
