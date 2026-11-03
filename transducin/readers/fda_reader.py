# transducin/readers/fda_reader.py
#
# Vendorized from marksgraham/OCT-Converter (MIT License)
# Source: oct_converter/readers/fda.py
# Copyright (c) marksgraham/OCT-Converter contributors
# https://github.com/marksgraham/OCT-Converter
#
# Adapted for Transducin: minimal laterality extraction + read_thickness_map() stub.
# Full thickness map extraction (CMT/ETDRS/RNFL) requires sample .fda/.fds files
# for validation and will be implemented in a future session.
from __future__ import annotations

import typing as t
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from transducin.readers import _fda_binary as fda_binary


@dataclass
class _OCTVolume:
    """Minimal OCT volume container."""
    laterality: Optional[str] = None


@dataclass
class ThicknessMapData:
    """Pre-computed thickness data embedded in the .fda/.fds file (stub)."""
    laterality: Optional[str] = None
    cmt_um: Optional[int] = None
    etdrs_sectors: Optional[dict] = None
    rnfl_sectors: Optional[dict] = None
    scan_date: Optional[datetime] = None


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
                chunk_name_size = np.fromstring(f.read(1), dtype=np.uint8)[0]
                if chunk_name_size == 0:
                    eof = True
                else:
                    chunk_name = f.read(chunk_name_size)
                    chunk_size = np.fromstring(f.read(4), dtype=np.uint32)[0]
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

    def read_thickness_map(self) -> list[ThicknessMapData]:
        """Extract pre-computed thickness maps from the .fda file (stub).

        Returns empty list until sample files are available for validation.
        """
        return []


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
