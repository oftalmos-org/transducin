# transducin/readers/e2e_reader.py
#
# Vendorized from marksgraham/OCT-Converter (MIT License)
# Source: oct_converter/readers/e2e.py
# Copyright (c) marksgraham/OCT-Converter contributors
# https://github.com/marksgraham/OCT-Converter
#
# Adapted for Transducin: laterality + layer contours (chunk 10019) + bscan metadata
# (chunk 10004). No pixel data and no patient name/DOB are read (privacy by design).
# read_thickness_map() delegates the measurement to
# transducin.spectralis_extractor.measure_e2e_volume (shared with the legacy extractor).
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from transducin.etdrs_policy import STRICT
from transducin.readers import _e2e_binary as e2e_binary
from transducin.readers._layers import (
    BScanVolume,
    LayerVolume,
    ThicknessMapData,
    clean_source_patient_id,
    sort_maps,
    thickness_map_from_clinical,
)

logger = logging.getLogger("transducin.readers.e2e")


# ── Decodificación de píxeles (ADR-027) ───────────────────────────────────────
# Formato (oct_converter, MIT): cada píxel OCT es un flotante propio de 16 bits sin signo,
# 6 bits de exponente (bits 10-15) y 10 bits de mantisa (bits 0-9, orden invertido):
#   valor = (1 + mantisa_invertida / 1024) * 2 ** (exponente - 63)
# Luego se aplica una transformada logarítmica a [0, 1] y se cuantiza a 8 bits.
_IMAGE_CHUNK = 1073741824
_MAX_PIXELS = 64_000_000  # tope de cordura ante cabeceras corruptas


@lru_cache(maxsize=1)
def _ufloat16_lut() -> np.ndarray:
    i = np.arange(65536, dtype=np.uint32)
    low = i & 0x3FF
    rev = np.zeros_like(low)
    for b in range(10):
        rev |= ((low >> b) & 1) << (9 - b)
    exponent = (i >> 10).astype(np.float64)
    return (1.0 + rev / 1024.0) * np.power(2.0, exponent - 63)


def _decode_bscan(raw: np.ndarray, height: int, width: int) -> np.ndarray:
    """uint16 crudo de HEYEX → uint8 [alto × ancho]."""
    v = _ufloat16_lut()[raw].copy()
    dark = v <= 1
    v[dark] = (np.log(v[dark] + 2.44e-4) + 8.3) / 8.285
    v = np.clip(v, 0.0, 1.0)
    return (v * 255 + 0.5).astype(np.uint8).reshape(height, width)


@dataclass
class _OCTVolume:
    """Minimal OCT volume container — carries only what Transducin needs."""

    laterality: str | None = None


# Posición del campo patient_id dentro de patient_id_structure (chunk tipo 9):
# first_name(31) + surname(51) + title(15) + birthdate(4) + sex(1) = 102; ancho 25.
_E2E_PID_OFFSET = 102
_E2E_PID_LEN = 25


class E2EReader:
    """Reader for Heidelberg Spectralis .e2e (HEYEX) binary format.

    Based on description of .e2e file format:
    https://bitbucket.org/uocte/uocte/wiki/Heidelberg%20File%20Format
    """

    def __init__(self, filepath: str | Path) -> None:
        self.filepath = Path(filepath)
        if not self.filepath.exists():
            raise FileNotFoundError(self.filepath)

        self.byte_skip = 0

        with open(self.filepath, "rb") as f:
            raw = f.read(21)
            if raw == b"E2EMultipleVolumeFile":
                self.byte_skip = 64
            f.seek(self.byte_skip)
            f.read(36)  # header_structure
            raw = f.read(52)
            main_directory = e2e_binary.main_directory_structure.parse(raw)

            self.directory_stack: list[int] = []
            current = main_directory.current
            while current != 0:
                self.directory_stack.append(current)
                f.seek(current + self.byte_skip)
                raw = f.read(52)
                directory_chunk = e2e_binary.main_directory_structure.parse(raw)
                current = directory_chunk.prev

    def _build_chunk_stack(self, f) -> list[tuple[int, int]]:
        chunk_stack: list[tuple[int, int]] = []
        for position in self.directory_stack:
            f.seek(position + self.byte_skip)
            raw = f.read(52)
            directory_chunk = e2e_binary.main_directory_structure.parse(raw)
            for _ in range(directory_chunk.num_entries):
                raw = f.read(44)
                chunk = e2e_binary.sub_directory_structure.parse(raw)
                if chunk.start > chunk.pos:
                    chunk_stack.append((chunk.start, chunk.size))
        return chunk_stack

    def read_oct_volume(self) -> list[_OCTVolume]:
        """Returns minimal volume data — laterality only.

        Full pixel volume extraction is not needed for SR generation.
        """
        laterality = self._read_laterality()
        if laterality:
            return [_OCTVolume(laterality=laterality)]
        return []

    def _read_laterality(self) -> str | None:
        """Scan chunk type 3 (scan preamble) for laterality."""
        with open(self.filepath, "rb") as f:
            chunk_stack = self._build_chunk_stack(f)
            for start, _size in chunk_stack:
                f.seek(start + self.byte_skip)
                raw = f.read(60)
                try:
                    chunk = e2e_binary.chunk_structure.parse(raw)
                except Exception:
                    continue
                if chunk.type == 3:
                    raw = f.read(chunk.size)
                    try:
                        pre_data = e2e_binary.pre_data.parse(raw)
                        if pre_data.laterality in ("R", "L"):
                            return pre_data.laterality
                    except Exception:
                        pass
        return None

    def read_patient_id(self) -> str:
        """Identificador interno del paciente (chunk tipo 9), tal cual viene en el archivo.

        Solo se leen los 25 bytes del campo ``patient_id``; nombre, apellidos y fecha de
        nacimiento del mismo chunk NO se leen (privacidad por diseño). Retorna "" si no
        hay chunk de paciente o el campo está vacío.
        """
        try:
            with open(self.filepath, "rb") as f:
                for start, _size in self._build_chunk_stack(f):
                    f.seek(start + self.byte_skip)
                    try:
                        chunk = e2e_binary.chunk_structure.parse(f.read(60))
                    except Exception:
                        continue
                    if chunk.type != 9 or chunk.size < _E2E_PID_OFFSET + _E2E_PID_LEN:
                        continue
                    f.seek(_E2E_PID_OFFSET, 1)
                    pid = clean_source_patient_id(f.read(_E2E_PID_LEN))
                    if pid:
                        return pid
        except Exception as exc:  # degradación elegante ante archivos parciales
            logger.debug("E2E: no se pudo leer el identificador de paciente: %s", type(exc).__name__)
        return ""

    # ── Segmentación de capas ────────────────────────────────────────────────

    def read_layer_volumes(self) -> list[LayerVolume]:
        """Lee contornos de capas (chunk 10019) agrupados por volumen.

        Un volumen es la tupla (patient_db_id, study_id, series_id). Solo se
        devuelven los volúmenes que tienen contornos. No se leen píxeles ni PHI.
        Portado de oct_converter/readers/e2e.py (MIT).
        """
        max_slice: dict[str, float] = {}
        starts: list[int] = []
        contour_dict: dict[str, dict[str, dict[int, np.ndarray]]] = {}
        bscan_by_vol: dict[str, list[dict]] = {}
        qual_by_vol: dict[str, list[float]] = {}
        lat_by_vol: dict[str, str] = {}
        first_date: datetime | None = None
        float_max = np.finfo(np.float32).max

        with open(self.filepath, "rb") as f:
            for position in self.directory_stack:
                f.seek(position + self.byte_skip)
                directory_chunk = e2e_binary.main_directory_structure.parse(f.read(52))
                for _ in range(directory_chunk.num_entries):
                    entry = e2e_binary.sub_directory_structure.parse(f.read(44))
                    vs = f"{entry.patient_db_id}_{entry.study_id}_{entry.series_id}"
                    sl = entry.slice_id / 2
                    if vs not in max_slice or sl > max_slice[vs]:
                        max_slice[vs] = sl
                    if entry.start > entry.pos:
                        starts.append(entry.start)

            for start in starts:
                f.seek(start + self.byte_skip)
                try:
                    chunk = e2e_binary.chunk_structure.parse(f.read(60))
                except Exception:
                    continue
                vs = f"{chunk.patient_db_id}_{chunk.study_id}_{chunk.series_id}"

                if chunk.type == 10019:  # contour data
                    try:
                        head = e2e_binary.contour_structure.parse(f.read(16))
                        if head.width <= 0:
                            continue
                        raw = f.read(head.width * 4)
                        if len(raw) < head.width * 4:
                            continue
                        arr = np.frombuffer(raw, dtype="<f4").astype(np.float32)
                        arr[(arr < 1e-9) | (arr == float_max)] = np.nan
                    except Exception:
                        continue
                    contour_dict.setdefault(vs, {}).setdefault(f"contour{head.id}", {})[int(chunk.slice_id / 2)] = arr

                elif chunk.type == 10004:  # bscan metadata
                    payload = f.read(104)
                    try:
                        m = e2e_binary.bscan_metadata.parse(payload)
                    except Exception:
                        continue
                    bscan_by_vol.setdefault(vs, []).append(
                        {
                            "scaley": float(m.scaley),
                            "posX1": float(m.posX1),
                            "posX2": float(m.posX2),
                            "posY1": float(m.posY1),
                            "posY2": float(m.posY2),
                        }
                    )
                    # imgQuality: Float32 en el byte 84 del payload de 104 bytes (0.0-1.0).
                    if len(payload) >= 88:
                        q = struct.unpack_from("<f", payload, 84)[0]
                        if 0.0 <= q <= 1.0:
                            qual_by_vol.setdefault(vs, []).append(q)
                    if first_date is None:
                        try:
                            unix = m.acquisitionTime / 10_000_000 - 11_644_473_600
                            first_date = datetime(1970, 1, 1) + timedelta(seconds=unix)
                        except Exception:
                            pass

                elif chunk.type == 3:  # scan preamble → lateralidad
                    try:
                        pre = e2e_binary.pre_data.parse(f.read(chunk.size))
                        if pre.laterality in ("R", "L") and vs not in lat_by_vol:
                            lat_by_vol[vs] = pre.laterality
                    except Exception:
                        pass

        all_bscan = [d for lst in bscan_by_vol.values() for d in lst]
        all_qual = [q for lst in qual_by_vol.values() for q in lst]
        file_lats = set(lat_by_vol.values())

        volumes: list[LayerVolume] = []
        for vs, conts in contour_dict.items():
            max_idx = max(i for sl in conts.values() for i in sl)
            n_slices = max(int(max_slice.get(vs, max_idx)) + 1, max_idx + 1)
            contours: dict[str, list] = {}
            for name, per_slice in conts.items():
                lst: list = [None] * n_slices
                for i, arr in per_slice.items():
                    lst[i] = arr
                contours[name] = lst
            first = next(iter(next(iter(conts.values())).values()))

            bdata = bscan_by_vol.get(vs) or all_bscan
            quals = qual_by_vol.get(vs) or all_qual
            # Lateralidad: la del volumen; si falta, la del archivo solo si es unívoca.
            lat = lat_by_vol.get(vs) or (next(iter(file_lats)) if len(file_lats) == 1 else None)
            volumes.append(
                LayerVolume(
                    volume_id=vs,
                    contours=contours,
                    pixel_spacing=[0.01, bdata[0]["scaley"], 0.05] if bdata else None,
                    laterality=lat,
                    acquisition_date=first_date,
                    bscan_data=bdata,
                    n_bscans=n_slices,
                    n_ascans=len(first),
                    sqi_mean=round(float(np.mean(quals)), 4) if quals else None,
                )
            )
        return volumes

    def read_bscans(self) -> list[BScanVolume]:
        """Decodifica los cortes B-scan (chunk de imagen con ind=1) y el fondo de ojo (ind=0).

        Solo en local (ADR-027). No se leen nombre, fecha de nacimiento ni identificadores.
        Un volumen es la tupla (patient_db_id, study_id, series_id), igual que en
        read_layer_volumes(). Si hay varias imágenes para el mismo corte se conserva la primera.
        Retorna [] si el archivo no trae cortes.
        """
        images: dict[str, dict[int, np.ndarray]] = {}
        fundus: dict[str, np.ndarray] = {}
        bscan_by_vol: dict[str, list[dict]] = {}
        lat_by_vol: dict[str, str] = {}

        with open(self.filepath, "rb") as f:
            for start, _size in self._build_chunk_stack(f):
                f.seek(start + self.byte_skip)
                try:
                    chunk = e2e_binary.chunk_structure.parse(f.read(60))
                except Exception:
                    continue
                vs = f"{chunk.patient_db_id}_{chunk.study_id}_{chunk.series_id}"

                if chunk.type == _IMAGE_CHUNK:
                    try:
                        head = e2e_binary.image_structure.parse(f.read(20))
                    except Exception:
                        continue
                    count = head.height * head.width
                    if count <= 0 or count > _MAX_PIXELS:
                        continue
                    if chunk.ind == 1:  # corte OCT, uint16
                        buf = f.read(count * 2)
                        if len(buf) < count * 2:
                            continue
                        idx = int(chunk.slice_id / 2)
                        if idx < 0:
                            continue
                        raw = np.frombuffer(buf, dtype="<u2")
                        images.setdefault(vs, {}).setdefault(idx, _decode_bscan(raw, head.height, head.width))
                    elif chunk.ind == 0:  # fondo de ojo, uint8
                        buf = f.read(count)
                        if len(buf) < count:
                            continue
                        fundus.setdefault(
                            vs, np.frombuffer(buf, dtype=np.uint8).reshape(head.height, head.width).copy()
                        )

                elif chunk.type == 10004:  # metadatos de B-scan (geometría)
                    try:
                        m = e2e_binary.bscan_metadata.parse(f.read(104))
                    except Exception:
                        continue
                    bscan_by_vol.setdefault(vs, []).append(
                        {
                            "scaley": float(m.scaley),
                            "posX1": float(m.posX1),
                            "posX2": float(m.posX2),
                            "posY1": float(m.posY1),
                            "posY2": float(m.posY2),
                        }
                    )

                elif chunk.type == 3:  # lateralidad
                    try:
                        pre = e2e_binary.pre_data.parse(f.read(chunk.size))
                        if pre.laterality in ("R", "L") and vs not in lat_by_vol:
                            lat_by_vol[vs] = pre.laterality
                    except Exception:
                        pass

        file_lats = set(lat_by_vol.values())
        volumes: list[BScanVolume] = []
        for vs, per_slice in images.items():
            n_slices = max(per_slice) + 1
            bdata = bscan_by_vol.get(vs) or [d for lst in bscan_by_vol.values() for d in lst]
            prefix = vs.rsplit("_", 1)[0] + "_"
            fund = fundus.get(vs)
            if fund is None:
                fund = next((a for k, a in fundus.items() if k.startswith(prefix)), None)
            # Mismo marcador que read_layer_volumes(); la geometría física real sale de
            # spectralis_extractor._geometry_from_metadata (posX/posY no están en mm de retina).
            spacing = [0.01, bdata[0]["scaley"], 0.05] if bdata else None
            volumes.append(
                BScanVolume(
                    volume_id=vs,
                    laterality=lat_by_vol.get(vs) or (next(iter(file_lats)) if len(file_lats) == 1 else None),
                    images=[per_slice.get(i) for i in range(n_slices)],
                    fundus=fund,
                    bscan_data=bdata,
                    pixel_spacing=spacing,
                )
            )
        return volumes

    def read_thickness_map(self) -> list[ThicknessMapData]:
        """CMT / ETDRS / mRNFL / mGCIPL (o pRNFL en circle scans) desde los contornos.

        Retorna [] si el archivo no trae segmentación (degradación elegante).
        Los macular con CMT van primero.
        """
        from transducin.spectralis_extractor import measure_e2e_volume

        maps: list[ThicknessMapData] = []
        try:
            volumes = self.read_layer_volumes()
        except Exception as exc:
            logger.warning("E2E: no se pudo leer la segmentación de %s: %s", self.filepath.name, exc)
            return maps

        for n_vol, vol in enumerate(volumes, 1):
            cd_base = {
                "noel_id": "",
                "vendor": "heidelberg_spectralis",
                "source_file": str(self.filepath),
                "study_date": vol.acquisition_date.strftime("%Y%m%d") if vol.acquisition_date else "",
                "study_time": "",
            }
            cd = measure_e2e_volume(
                vol.contours,
                vol.pixel_spacing,
                {"bscan_data": vol.bscan_data},
                vol.laterality or "",
                cd_base,
                vol.sqi_mean,
                label=f"volumen {n_vol}",  # sin el nombre del archivo: puede llevar identificadores
                require_laterality=True,
                etdrs_policy=STRICT,
            )
            if cd is not None:
                maps.append(thickness_map_from_clinical(cd, vol.acquisition_date))
        return sort_maps(maps)
