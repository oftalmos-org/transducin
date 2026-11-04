# transducin/adapters/spectralis.py
# SPDX-License-Identifier: Apache-2.0
#
# DeviceAdapter para Heidelberg Spectralis — archivos .e2e (HEYEX format).
#
# Usa el reader vendorizado en transducin/readers/e2e_reader.py (MIT, basado en
# marksgraham/OCT-Converter). read_thickness_map() lee los contornos de capas
# (chunk 10019) y calcula CMT/ETDRS/mRNFL/mGCIPL; no requiere el paquete oct_converter.

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import ETDRSGrid, OCTClinicalData, RNFLSectors
from transducin.readers.e2e_reader import E2EReader


def _to_rnfl_sectors(d: dict | None) -> RNFLSectors | None:
    """Mapea dict {G, S, I, N, T} a RNFLSectors."""
    if not isinstance(d, dict) or not d:
        return None
    return RNFLSectors(
        global_avg=d.get("G"),
        superior=d.get("S"),
        inferior=d.get("I"),
        nasal=d.get("N"),
        temporal=d.get("T"),
    )


def _to_etdrs_grid(d: dict | None) -> ETDRSGrid | None:
    """Mapea dict {C, S1, N1, I1, T1, S2, N2, I2, T2} a ETDRSGrid."""
    if not isinstance(d, dict) or not d:
        return None
    return ETDRSGrid(
        C=d.get("C"),
        S1=d.get("S1"),
        N1=d.get("N1"),
        I1=d.get("I1"),
        T1=d.get("T1"),
        S2=d.get("S2"),
        N2=d.get("N2"),
        I2=d.get("I2"),
        T2=d.get("T2"),
    )


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _measurements_from_reader(reader, laterality: str | None) -> dict:
    """Llama reader.read_thickness_map() y devuelve kwargs para OCTClinicalData.

    Compartido por los adapters Spectralis y Topcon. Degrada a valores vacíos si el
    reader no implementa read_thickness_map(), falla o no encuentra segmentación.
    """
    out: dict = {
        "laterality": laterality or "",
        "study_type": "macular",
        "study_date": "",
        "study_time": "",
        "cmt_um": None,
        "etdrs_grid": None,
        "rnfl": None,
        "gcl_ipl": None,
        "gcl_avg_um": None,
        "sqi_mean": None,
        "extraction_confidence": "unknown",
    }
    if not hasattr(reader, "read_thickness_map"):
        return out
    try:
        maps = reader.read_thickness_map()
    except Exception:
        return out
    if not maps:
        return out

    m = maps[0]  # el reader ordena macular con CMT primero
    if not out["laterality"]:
        lat = getattr(m, "laterality", None) or ""
        lat = lat.upper() if isinstance(lat, str) else ""
        if lat in ("L", "R"):
            out["laterality"] = lat
    out["cmt_um"] = getattr(m, "cmt_um", None)
    out["etdrs_grid"] = _to_etdrs_grid(getattr(m, "etdrs_sectors", None))
    out["rnfl"] = _to_rnfl_sectors(getattr(m, "rnfl_sectors", None))
    out["gcl_ipl"] = _to_rnfl_sectors(getattr(m, "gcl_ipl_sectors", None))
    out["gcl_avg_um"] = _num(getattr(m, "gcl_avg_um", None))
    out["sqi_mean"] = _num(getattr(m, "sqi_mean", None))
    st = getattr(m, "study_type", None)
    if isinstance(st, str) and st:
        out["study_type"] = st
    scan_date = getattr(m, "scan_date", None)
    if isinstance(scan_date, datetime):
        out["study_date"] = scan_date.strftime("%Y%m%d")
        out["study_time"] = scan_date.strftime("%H%M%S")
    has_any = any(out[k] is not None for k in ("cmt_um", "etdrs_grid", "rnfl", "gcl_ipl"))
    if out["cmt_um"] is not None:
        out["extraction_confidence"] = "confirmed"
    elif has_any:
        out["extraction_confidence"] = "assumed"
    return out


class SpectralisE2EAdapter(DeviceAdapter):
    """Adaptador para archivos .e2e del Heidelberg Spectralis (HEYEX format).

    Extrae CMT, ETDRS, mRNFL, mGCIPL (macular) o pRNFL (circle scan) desde la
    segmentación de capas embebida en el .e2e, con el reader vendorizado. Sin
    segmentación, o sin lateralidad, retorna medidas None (degradación elegante).
    """

    @property
    def vendor(self) -> str:
        return "Heidelberg Spectralis"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".e2e"})

    @property
    def adapter_id(self) -> str:
        return "spectralis"

    def extract(self, path: Path) -> OCTClinicalData:
        reader = E2EReader(str(path))

        laterality: str | None = None
        try:
            volumes = reader.read_oct_volume()
            if volumes:
                lat = (getattr(volumes[0], "laterality", None) or "").upper()
                if lat in ("L", "R"):
                    laterality = lat
        except Exception:
            pass

        meas = _measurements_from_reader(reader, laterality)
        return OCTClinicalData(
            noel_id="",  # resuelto desde filename en hot_folder_watcher
            vendor="heidelberg_spectralis",
            **meas,
        )

    def to_dicom(
        self,
        path: Path,
        clinical_data: OCTClinicalData,
        output_dir: Path,
    ) -> list[Path]:
        """Convierte volumen OCT .e2e a archivos DICOM OphthalmicTomographyImage.

        TODO: implementación pendiente de archivos .e2e de muestra para validación.
        Retorna lista vacía — el SR se genera igual desde clinical_data.
        """
        return []
