# transducin/readers/e2e_reader.py
#
# Vendorized from marksgraham/OCT-Converter (MIT License)
# Source: oct_converter/readers/e2e.py
# Copyright (c) marksgraham/OCT-Converter contributors
# https://github.com/marksgraham/OCT-Converter
#
# Adapted for Transducin: minimal laterality extraction + read_thickness_map() stub.
# Full thickness map extraction (CMT/ETDRS/RNFL) requires sample .e2e files
# for validation and will be implemented in a future session.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from transducin.readers import _e2e_binary as e2e_binary


@dataclass
class _OCTVolume:
    """Minimal OCT volume container — carries only what Transducin needs."""
    laterality: Optional[str] = None


@dataclass
class ThicknessMapData:
    """Pre-computed thickness data embedded in the .e2e file.

    Currently a stub — populated once sample .e2e files are available
    for validating the chunk offsets.
    """
    laterality: Optional[str] = None
    cmt_um: Optional[int] = None
    etdrs_sectors: Optional[dict] = None   # keys: C, S1,S2,N1,N2,I1,I2,T1,T2
    rnfl_sectors: Optional[dict] = None    # keys: G,T,S,N,I
    scan_date: Optional[datetime] = None


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

    def _read_laterality(self) -> Optional[str]:
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

    def read_thickness_map(self) -> list[ThicknessMapData]:
        """Extract pre-computed thickness maps from the .e2e file.

        Stub — returns empty list until sample files are available for validation.
        The Heidelberg software embeds CMT/ETDRS/RNFL data in proprietary chunk
        types that require empirical validation against known-correct values before
        production use.
        """
        return []
