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

import numpy as np
from pydicom.uid import generate_uid

from transducin.base_adapter import DeviceAdapter
from transducin.clinical_data import ETDRSGrid, OCTClinicalData, RNFLSectors
from transducin.layer_contours import attach_layers, encode_layers
from transducin.readers.e2e_reader import E2EReader
from transducin.slo_finder import attach_finder, build_finder


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


def _device_patient_id(reader) -> str:
    """Identificador interno del archivo tal cual (ADR-026); "" si el reader no lo expone o falla."""
    fn = getattr(reader, "read_patient_id", None)
    if not callable(fn):
        return ""
    try:
        pid = fn()
    except Exception:
        return ""
    return pid.strip() if isinstance(pid, str) else ""


def _empty_measurements(laterality: str | None) -> dict:
    """Valores base (vacíos) de las mediciones para OCTClinicalData."""
    return {
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
        "confidence_notes": [],
    }


def _measurements_from_reader(reader, laterality: str | None) -> dict:
    """Llama reader.read_thickness_map() y devuelve kwargs para OCTClinicalData.

    Compartido por los adapters Spectralis y Topcon. Degrada a valores vacíos si el
    reader no implementa read_thickness_map(), falla o no encuentra segmentación.
    """
    out = _empty_measurements(laterality)
    if not hasattr(reader, "read_thickness_map"):
        return out
    try:
        maps = reader.read_thickness_map()
    except Exception:
        return out
    if not maps:
        return out

    return _measurements_from_map(maps[0], out)  # el reader ordena macular con CMT primero


def _measurements_from_map(m, out: dict) -> dict:
    """Vuelca un ThicknessMapData en los kwargs de OCTClinicalData (``out`` trae los valores base)."""
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
    notes = getattr(m, "notes", None)
    if isinstance(notes, list):
        out["confidence_notes"] = [n for n in notes if isinstance(n, str)]
    scan_date = getattr(m, "scan_date", None)
    if isinstance(scan_date, datetime):
        out["study_date"] = scan_date.strftime("%Y%m%d")
        out["study_time"] = scan_date.strftime("%H%M%S")
    has_any = any(out[k] is not None for k in ("cmt_um", "etdrs_grid", "rnfl", "gcl_ipl"))
    if out["cmt_um"] is not None:
        # El mapeo de capas heyex_ids es una hipótesis validada por geometría, no documentada.
        assumed = any("heyex_ids" in n for n in out["confidence_notes"])
        out["extraction_confidence"] = "assumed" if assumed else "confirmed"
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
        cd = OCTClinicalData(
            noel_id="",  # alias NOEL: lo resuelve el llamador (p. ej. prefijo del nombre de archivo)
            device_patient_id=_device_patient_id(reader),  # ADR-026: identificador del equipo, sin modificar
            vendor="heidelberg_spectralis",
            **meas,
        )
        if meas["extraction_confidence"] != "unknown":  # hubo alguna medición calculada
            cd.measurement_provenance = "vendor_contour_derived"
        return cd

    def extract_all(self, path: Path) -> list[OCTClinicalData]:
        """Una medición por ojo: el mejor mapa (macular con CMT, el primero) de cada lateralidad.

        Cada una produce su propio SR, de modo que ambos ojos se vean en Gass. Si el archivo
        no trae mapas medibles devuelve ``[extract(path)]``.
        """
        reader = E2EReader(str(path))
        try:
            maps = reader.read_thickness_map()
        except Exception:
            maps = []
        device_pid = _device_patient_id(reader)
        result: list[OCTClinicalData] = []
        seen: set[str] = set()
        for m in maps:
            lat = (getattr(m, "laterality", None) or "").upper()
            if lat not in ("L", "R") or lat in seen:
                continue
            seen.add(lat)
            base = _measurements_from_map(m, _empty_measurements(lat))
            cd = OCTClinicalData(noel_id="", vendor="heidelberg_spectralis", device_patient_id=device_pid, **base)
            if base["extraction_confidence"] != "unknown":
                cd.measurement_provenance = "vendor_contour_derived"
            result.append(cd)
        return result or [self.extract(path)]

    def to_dicom(
        self,
        path: Path,
        clinical_data: OCTClinicalData,
        output_dir: Path,
    ) -> list[Path]:
        """Convierte cada volumen B-scan del .e2e a un DICOM OphthalmicTomographyImage (ADR-027).

        Un archivo por volumen, todos con el mismo StudyInstanceUID. El primero es el que
        coincide con la lateralidad de ``clinical_data`` y tiene más cortes: es el que el SR
        toma como referencia. Solo en local: los píxeles son identificables.

        Fidelidad (ADR-027, regla 4): no se escribe PixelSpacing ni SliceThickness porque los
        posX/posY del archivo vienen en grados de exploración, no en mm de retina; solo consta
        la escala axial. La extensión angular se deja en ImageComments. El nombre del archivo
        de origen no se incrusta (puede contener identificadores). Retorna [] si el archivo
        no trae cortes decodificables.
        """
        try:
            reader = E2EReader(str(path))
            volumes = [v for v in reader.read_bscans() if any(i is not None for i in v.images)]
        except Exception:
            return []
        try:  # contornos de capa embebidos; si fallan, la imagen se emite igual
            layer_volumes = {lv.volume_id: lv for lv in reader.read_layer_volumes()}
        except Exception:
            layer_volumes = {}
        if not volumes:
            return []

        lat = (clinical_data.laterality or "").upper()
        volumes.sort(key=lambda v: (0 if (v.laterality or "") == lat else 1, -len(v.images)))

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        study_uid = generate_uid()
        out: list[Path] = []
        for n, vol in enumerate(volumes, 1):
            ds = _build_spectralis_oct(
                vol, clinical_data, study_uid, n, primary=(n == 1), layer_volume=layer_volumes.get(vol.volume_id)
            )
            if ds is None:
                continue
            dest = output_dir / f"spectralis_vol{n:02d}_OCT.dcm"
            ds.save_as(str(dest), write_like_original=False)
            out.append(dest)
        return out


# Extensión angular máxima (grados) de un volumen de nervio óptico: el protocolo estándar
# de Spectralis es 15° x 15°; los volúmenes maculares son de 20° o más. Es una inferencia
# por geometría (ADR-027), no un campo declarado en el archivo.
_ONH_MAX_WIDTH_DEG = 16.0


def infer_study_type(bscan_data: list[dict]) -> str:
    """'optic_nerve' | 'macular' | '' (no se puede inferir) a partir de la extensión angular."""
    widths = [abs(d["posX2"] - d["posX1"]) for d in bscan_data or []]
    widest = max(widths, default=0.0)
    if widest <= 0:
        return ""
    return "optic_nerve" if widest <= _ONH_MAX_WIDTH_DEG else "macular"


def _angular_extent(bscan_data: list[dict]) -> str:
    """'a° × b°' desde posX/posY del archivo (grados de exploración), o '' si no es utilizable."""
    if not bscan_data:
        return ""
    wx = [abs(d["posX2"] - d["posX1"]) for d in bscan_data]
    ys = [d["posY1"] for d in bscan_data]
    if not wx or not ys or max(wx) <= 0:
        return ""
    return f"{max(wx):.1f}° x {max(ys) - min(ys):.1f}°"


def _thickness_um(contours: dict, bscan_data: list[dict], n_frames: int):
    """Grosor ILM→BM en µm, [corte][A-scan] en orden de corte (para el mapa de color); ``None`` si no hay."""
    from transducin.spectralis_extractor import _resolve_e2e_layers

    layers, _name = _resolve_e2e_layers(contours)
    if layers is None or layers["ilm"].shape[0] != n_frames:
        return None
    scaley = [d["scaley"] for d in bscan_data if 0.001 < d.get("scaley", 0.0) < 0.1]
    axial_um = float(np.median(scaley)) * 1000.0 if scaley else 3.87
    return (layers["bm"] - layers["ilm"]) * axial_um


def _layer_labels(contours: dict) -> tuple[dict[str, tuple[str, str]], str]:
    """Etiquetas anatómicas de los contornos con su estado (documentado / inferido)."""
    from transducin.spectralis_extractor import _E2E_LAYER_MAPS, _resolve_e2e_layers

    _layers, name = _resolve_e2e_layers(contours)
    if name is None:
        return {}, "Sin mapa de capas consistente: contornos sin etiqueta anatómica."
    status = "documented" if name == "oct_converter_contiguous" else "inferred"
    names = {"ilm": "ILM", "bm": "Bruch (BM)", "rnfl_gcl": "RNFL/GCL", "ipl_inl": "IPL/INL"}
    mapping = dict(_E2E_LAYER_MAPS)[name]
    labels = {key: (names[layer], status) for layer, key in mapping.items()}
    note = f"Mapeo de IDs de capa: {name} ({status})."
    return labels, note


def _build_spectralis_oct(vol, cd: OCTClinicalData, study_uid: str, number: int, primary: bool, layer_volume=None):
    """Dataset OPT de un volumen Spectralis; ``None`` si no hay cortes del mismo tamaño."""
    from transducin.revo_opt_reader import build_dicom_oct

    frames = [i for i in vol.images if i is not None]
    if not frames:
        return None
    shape = frames[0].shape
    frames = [f for f in frames if f.shape == shape]
    lat = vol.laterality if vol.laterality in ("R", "L") else ""
    # El tipo se infiere por volumen (extensión angular); el volumen principal conserva el del SR.
    study_type = infer_study_type(vol.bscan_data) or (cd.study_type if primary else "")

    ds = build_dicom_oct(
        bscans=frames,
        noel_id=cd.patient_id,
        study_date=cd.study_date or "",
        laterality=lat,
        study_type=study_type,
        study_time=cd.study_time or "",
        study_instance_uid=study_uid,
        source_file="",
    )
    eye = {"R": " ojo derecho", "L": " ojo izquierdo"}.get(lat, "")
    ds.Manufacturer = "Heidelberg Engineering"
    ds.ManufacturerModelName = "Spectralis"
    ds.SeriesDescription = f"Spectralis OCT{eye} (volumen {number}, {len(frames)} cortes)"
    ds.SeriesNumber = number
    ds.InstanceNumber = 1
    if layer_volume is not None and getattr(layer_volume, "contours", None):
        labels, note = _layer_labels(layer_volume.contours)
        attach_layers(ds, encode_layers(layer_volume.contours, len(frames), labels, note))
    thickness = None
    if layer_volume is not None and getattr(layer_volume, "contours", None):
        thickness = _thickness_um(layer_volume.contours, vol.bscan_data, len(frames))
    attach_finder(ds, build_finder(vol.fundus, vol.bscan_data, len(frames), thickness))
    if study_type == "optic_nerve":  # build_dicom_oct etiqueta todo lo no anterior como retina
        ds.AnatomicRegionSequence[0].CodeValue = "T-AA630"
        ds.AnatomicRegionSequence[0].CodeMeaning = "Optic nerve"
    if not lat:
        for tag in ("Laterality", "ImageLaterality"):
            if tag in ds:
                del ds[tag]
    comments = ["Derivado de .E2E por Transducin (ADR-027); el original no se modifica."]
    extent = _angular_extent(vol.bscan_data)
    if extent:
        comments.append(f"Extension de exploracion {extent} (grados); sin PixelSpacing en mm.")
    if len(frames) != len(vol.images):
        comments.append(f"Faltan {len(vol.images) - len(frames)} de {len(vol.images)} cortes.")
    ds.ImageComments = " ".join(comments)[:10240]
    return ds
