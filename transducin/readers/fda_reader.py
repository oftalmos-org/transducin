# transducin/readers/fda_reader.py
#
# Vendorized from marksgraham/OCT-Converter (MIT License)
# Source: oct_converter/readers/fda.py
# Copyright (c) marksgraham/OCT-Converter contributors
# https://github.com/marksgraham/OCT-Converter
#
# Adapted for Transducin: laterality + layer contours (@CONTOUR_INFO) + scan geometry.
# No pixel data and no patient name/DOB are read (privacy by design).
# read_thickness_map() delegates the measurement to
# transducin.topcon_fda_extractor.measure_fda_volume (shared with the legacy extractor).
from __future__ import annotations

import logging
import struct
import typing as t
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from transducin.readers import _fda_binary as fda_binary
from transducin.readers._layers import (
    LayerVolume,
    ThicknessMapData,
    sort_maps,
    thickness_map_from_clinical,
)

logger = logging.getLogger("transducin.readers.fda")

_LAYER_NAMES = {
    "MULTILAYERS_1": "ILM", "MULTILAYERS_2": "RNFL_GCL", "MULTILAYERS_3": "GCL_IPL",
    "MULTILAYERS_4": "IPL_INL", "MULTILAYERS_5": "MZ_EZ", "MULTILAYERS_6": "IZ_RPE",
    "MULTILAYERS_7": "BM", "MULTILAYERS_8": "INL_OPL", "MULTILAYERS_9": "ELM",
    "MULTILAYERS_10": "CSI",
}


@dataclass
class _OCTVolume:
    """Minimal OCT volume container."""
    laterality: Optional[str] = None


class FDAReader:
    """Reader for Topcon .fda (and .fds fallback) binary format.

    Based on description of .fda file format:
    https://bitbucket.org/uocte/uocte/wiki/Topcon%20File%20Format
    """

    def __init__(self, filepath: str | Path, printing: bool = False) -> None:
        self.filepath = Path(filepath)
        if not self.filepath.exists():
            raise FileNotFoundError(self.filepath)
        self.chunk_dict, self.header = self._get_list_of_file_chunks(printing=printing)

    def _get_list_of_file_chunks(self, printing: bool = False) -> t.Tuple[dict, dict]:
        chunk_dict: dict = {}
        with open(self.filepath, "rb") as f:
            raw = f.read(15)
            header = dict(fda_binary.header.parse(raw))
            eof = False
            while not eof:
                first = f.read(1)
                chunk_name_size = first[0] if first else 0
                if chunk_name_size == 0:
                    eof = True
                else:
                    chunk_name = f.read(chunk_name_size)
                    # np.fromstring(binario) fue eliminado en numpy>=2.3
                    chunk_size = struct.unpack("<I", f.read(4))[0]
                    chunk_location = f.tell()
                    f.seek(chunk_size, 1)
                    if chunk_name in chunk_dict:
                        prev_loc = chunk_dict[chunk_name][0]
                        prev_size = chunk_dict[chunk_name][1]
                        if not isinstance(prev_loc, list):
                            prev_loc = [prev_loc]
                            prev_size = [prev_size]
                        chunk_location = prev_loc + [chunk_location]
                        chunk_size = prev_size + [chunk_size]
                    chunk_dict[chunk_name] = [chunk_location, chunk_size]
        return chunk_dict, header

    def read_oct_volume(self) -> list[_OCTVolume]:
        """Returns minimal volume data — laterality only."""
        laterality = self._read_laterality()
        if laterality is not None:
            return [_OCTVolume(laterality=laterality)]
        return []

    def _read_laterality(self) -> Optional[str]:
        """Extract laterality from capture_info chunk (eye: 0=R, 1=L)."""
        lat_map = {0: "R", 1: "L"}
        for key in (b"@CAPTURE_INFO_02", b"@CAPTURE_INFO"):
            if key not in self.chunk_dict:
                continue
            try:
                chunk_location, chunk_size = self.chunk_dict[key]
                if isinstance(chunk_location, list):
                    chunk_location = chunk_location[0]
                    chunk_size = chunk_size[0]
                with open(self.filepath, "rb") as f:
                    f.seek(chunk_location)
                    raw = f.read(chunk_size)
                struct_name = key.decode().split("@")[-1].lower() + "_header"
                parsed = dict(fda_binary.__dict__[struct_name].parse(raw))
                eye = parsed.get("eye")
                if eye in lat_map:
                    return lat_map[eye]
            except Exception:
                pass
        return None

    # ── Segmentación de capas ────────────────────────────────────────────────

    def _chunk_positions(self, key: bytes) -> list[tuple[int, int]]:
        """[(ubicación, tamaño)] normalizado: el dict guarda int o lista según duplicados."""
        if key not in self.chunk_dict:
            return []
        loc, size = self.chunk_dict[key]
        if isinstance(loc, list):
            return list(zip(loc, size))
        return [(loc, size)]

    def _read_oct_dims(self) -> Optional[dict]:
        """width, height, number_slices desde el header de @IMG_JPEG / @IMG_MOT_COMP_03."""
        for key, struct_, n in ((b"@IMG_JPEG", fda_binary.oct_header, 25),
                                (b"@IMG_MOT_COMP_03", fda_binary.oct_header_2, 22)):
            for loc, _size in self._chunk_positions(key)[:1]:
                with open(self.filepath, "rb") as f:
                    f.seek(loc)
                    h = struct_.parse(f.read(n))
                return {"width": h.width, "height": h.height, "number_slices": h.number_slices}
        return None

    def _read_pixel_spacing(self, dims: dict) -> Optional[list]:
        """[x_mm/A-scan, y_mm/B-scan, z_mm/px] desde @PARAM_SCAN_04 / _02."""
        for key, struct_ in ((b"@PARAM_SCAN_04", fda_binary.param_scan_04_header),
                             (b"@PARAM_SCAN_02", fda_binary.param_scan_02_header)):
            for loc, size in self._chunk_positions(key)[:1]:
                with open(self.filepath, "rb") as f:
                    f.seek(loc)
                    p = struct_.parse(f.read(size))
                if not dims.get("width") or not dims.get("number_slices"):
                    return None
                return [
                    p.x_dimension_mm / dims["width"],
                    p.y_dimension_mm / dims["number_slices"],
                    p.z_resolution_um / 1000,
                ]
        return None

    def _read_capture_date(self) -> Optional[datetime]:
        for key in (b"@CAPTURE_INFO_02", b"@CAPTURE_INFO"):
            for loc, size in self._chunk_positions(key)[:1]:
                try:
                    with open(self.filepath, "rb") as f:
                        f.seek(loc)
                        raw = f.read(size)
                    name = key.decode().split("@")[-1].lower() + "_header"
                    return datetime(*fda_binary.__dict__[name].parse(raw).cap_date)
                except Exception:
                    pass
        return None

    def _read_image_quality(self) -> Optional[float]:
        """PARAM_OBS_02.image_quality → 0–1 (misma regla que topcon_fda_extractor)."""
        for loc, size in self._chunk_positions(b"@PARAM_OBS_02")[:1]:
            if size != 90:
                return None
            try:
                with open(self.filepath, "rb") as f:
                    f.seek(loc)
                    q = fda_binary.param_obs_02_header.parse(f.read(size)).image_quality
                val = float(str(q).strip().strip("\x00").strip())
            except Exception:
                return None
            if val > 1.0:
                return round(val / 100.0, 4)
            if 0.0 <= val <= 1.0:
                return round(val, 4)
        return None

    def read_layer_volumes(self) -> list[LayerVolume]:
        """Lee @CONTOUR_INFO como mapas 2D [B-scans × A-scans] en píxeles desde el TOP.

        Segmentación ausente (valor crudo 0) → NaN *antes* de convertir a "desde el
        TOP". Portado de oct_converter/readers/fda.py (MIT). Retorna [] sin contornos.
        """
        positions = self._chunk_positions(b"@CONTOUR_INFO")
        dims = self._read_oct_dims()
        if not positions or dims is None:
            return []

        contours: dict[str, np.ndarray] = {}
        with open(self.filepath, "rb") as f:
            for loc, _size in positions:
                f.seek(loc)
                try:
                    h = fda_binary.contour_info_header.parse(f.read(34))
                except Exception:
                    continue
                n = h.width * h.height
                raw = f.read(n * 2)
                if n <= 0 or len(raw) < n * 2:
                    continue
                seg = np.frombuffer(raw, dtype="<u2").reshape(h.height, h.width)
                seg = np.flip(seg, 0).astype(np.float32)
                top = dims["height"] - seg
                top[seg == 0] = np.nan
                contours[_LAYER_NAMES.get(h.id, h.id)] = top

        if not contours:
            return []
        return [LayerVolume(
            volume_id="fda",
            contours=contours,
            pixel_spacing=self._read_pixel_spacing(dims),
            laterality=self._read_laterality(),
            acquisition_date=self._read_capture_date(),
            n_bscans=dims["number_slices"],
            n_ascans=dims["width"],
            sqi_mean=self._read_image_quality(),
        )]

    def read_thickness_map(self) -> list[ThicknessMapData]:
        """CMT / ETDRS / mRNFL / mGCIPL (o pRNFL en circle scans) desde @CONTOUR_INFO.

        Retorna [] si el archivo no trae segmentación (degradación elegante).
        """
        from transducin.topcon_fda_extractor import measure_fda_volume

        maps: list[ThicknessMapData] = []
        try:
            volumes = self.read_layer_volumes()
        except Exception as exc:
            logger.warning("FDA: no se pudo leer la segmentación de %s: %s",
                           self.filepath.name, exc)
            return maps

        for vol in volumes:
            cd_base = dict(
                noel_id="", vendor="topcon_dri_oct",
                source_file=str(self.filepath),
                study_date=vol.acquisition_date.strftime("%Y%m%d") if vol.acquisition_date else "",
                study_time="",
            )
            cd = measure_fda_volume(
                vol.contours, vol.n_bscans, vol.n_ascans, vol.pixel_spacing,
                vol.laterality or "", cd_base, vol.sqi_mean,
                label=self.filepath.stem, require_laterality=True,
            )
            if cd is not None:
                maps.append(thickness_map_from_clinical(cd, vol.acquisition_date))
        return sort_maps(maps)


# FDS files share the same chunk-based container format as FDA.
# A dedicated FDSReader (using fds_binary structs) will be added when
# .fds sample files are available for validation.
FDSReader = FDAReader


def topcon_reader(path: Path) -> FDAReader:
    """Factory: returns the appropriate Topcon reader for .fda or .fds files."""
    suffix = path.suffix.lower()
    if suffix in (".fda", ".fds"):
        return FDAReader(str(path))
    raise ValueError(f"TopconFdaAdapter: extensión no soportada: {suffix}")
