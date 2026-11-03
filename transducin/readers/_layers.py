# transducin/readers/_layers.py
# SPDX-License-Identifier: Apache-2.0
#
# Contenedores compartidos por E2EReader y FDAReader para la segmentación de
# capas y el mapa de espesores. Sin PHI: no se leen nombre ni fecha de nacimiento.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LayerVolume:
    """Volumen OCT con contornos de capas (sin píxeles de imagen)."""
    volume_id: str = ""
    # {"contour0": [arr|None, ...]} (E2E)  |  {"ILM": 2D ndarray, ...} (FDA)
    contours: dict = field(default_factory=dict)
    pixel_spacing: Optional[list] = None
    laterality: Optional[str] = None
    acquisition_date: Optional[datetime] = None
    bscan_data: list = field(default_factory=list)   # E2E: dicts scaley/posX1/posX2/posY1
    n_bscans: int = 0
    n_ascans: int = 0
    sqi_mean: Optional[float] = None


@dataclass
class ThicknessMapData:
    """Espesores calculados desde la segmentación embebida en el archivo."""
    laterality: Optional[str] = None
    cmt_um: Optional[float] = None
    etdrs_sectors: Optional[dict] = None       # C, S1,N1,I1,T1, S2,N2,I2,T2
    rnfl_sectors: Optional[dict] = None        # G, S, I, N, T  (mRNFL o pRNFL)
    gcl_ipl_sectors: Optional[dict] = None     # G, S, I, N, T  (mGCIPL)
    gcl_avg_um: Optional[float] = None
    sqi_mean: Optional[float] = None
    study_type: Optional[str] = None           # macular | rnfl_circle | hd_line | unknown
    scan_date: Optional[datetime] = None


def _sectors(r) -> Optional[dict]:
    if r is None:
        return None
    return {
        "G": r.global_avg, "S": r.superior, "I": r.inferior,
        "N": r.nasal, "T": r.temporal,
    }


def thickness_map_from_clinical(cd, scan_date: Optional[datetime]) -> ThicknessMapData:
    """OCTClinicalData (ya medido) → ThicknessMapData."""
    grid = cd.etdrs_grid
    etdrs = None
    if grid is not None:
        etdrs = {k: getattr(grid, k) for k in ("C", "S1", "N1", "I1", "T1", "S2", "N2", "I2", "T2")}
    return ThicknessMapData(
        laterality=cd.laterality or None,
        cmt_um=cd.cmt_um,
        etdrs_sectors=etdrs,
        rnfl_sectors=_sectors(cd.rnfl),
        gcl_ipl_sectors=_sectors(cd.gcl_ipl),
        gcl_avg_um=cd.gcl_avg_um,
        sqi_mean=cd.sqi_mean,
        study_type=cd.study_type or None,
        scan_date=scan_date,
    )


def sort_maps(maps: list[ThicknessMapData]) -> list[ThicknessMapData]:
    """Macular con CMT primero, luego otros con CMT, al final circle scans."""
    def key(m: ThicknessMapData) -> int:
        if m.cmt_um is not None and m.study_type == "macular":
            return 0
        if m.cmt_um is not None:
            return 1
        return 2
    return sorted(maps, key=key)  # sorted es estable
