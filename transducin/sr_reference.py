# transducin/sr_reference.py
# SPDX-License-Identifier: Apache-2.0
"""Elige la imagen DICOM que sirve de referencia al SR de un ojo."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def pick_reference(paths: Iterable[Path], laterality: str = "", study_type: str = ""):
    """Dataset (sin píxeles) de la imagen OPT que mejor corresponde al ojo del SR.

    Prefiere, en orden: misma lateralidad, mismo tipo de estudio (``ProtocolName``) y más
    cortes. Sin lateralidad conocida o sin coincidencia, cae a cualquier OPT y luego a la
    primera imagen legible. ``None`` si ninguna se puede leer.
    """
    import pydicom

    datasets = []
    for p in paths:
        try:
            datasets.append(pydicom.dcmread(str(p), stop_before_pixels=True))
        except Exception:  # un archivo ilegible no invalida al resto
            continue
    if not datasets:
        return None

    def score(ds) -> tuple[int, int, int, int]:
        return (
            1 if str(getattr(ds, "Modality", "")) == "OPT" else 0,
            1 if laterality and str(getattr(ds, "Laterality", "")) == laterality else 0,
            1 if study_type and str(getattr(ds, "ProtocolName", "")) == study_type else 0,
            int(getattr(ds, "NumberOfFrames", 1) or 1),
        )

    return max(datasets, key=score)
