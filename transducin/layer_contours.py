# transducin/layer_contours.py
# SPDX-License-Identifier: Apache-2.0
"""Contornos de capa de un volumen OCT dentro del DICOM de imagen (ADR-028, propuesto).

Contrato con Gass: dos elementos privados en el bloque ``RetinaOS-Transducin`` (grupo 0009)
- (0009,1020) OB: float32 little-endian, forma [capa][corte][A-scan], NaN = sin dato.
- (0009,1021) UT: JSON con ``schema``, ``n_layers``, ``n_frames``, ``n_ascans``, ``layers``
  (``key``, ``label``, ``status``), ``coords`` y ``source``.

Cada valor es la fila (en píxeles de la imagen) del contorno; el A-scan k de N cae en la
columna (k + 0.5) * ancho / N. Son los contornos EMBEBIDOS por el equipo, sin modificar. El
nombre anatómico (``label``) es opcional y su ``status`` dice si está documentado,
inferido o desconocido: no se presenta una hipótesis como hecho.
"""

from __future__ import annotations

import json

import numpy as np
from pydicom.dataset import Dataset

from transducin.dicom_config import MANUFACTURER

TAG_CREATOR = (0x0009, 0x0010)
TAG_BLOB = (0x0009, 0x1020)
TAG_META = (0x0009, 0x1021)
SCHEMA = 1
_MAX_BLOB_BYTES = 48 * 1024 * 1024


def encode_layers(
    contours: dict[str, list],
    n_frames: int,
    labels: dict[str, tuple[str, str]] | None = None,
    note: str = "",
) -> tuple[bytes, str] | None:
    """Empaqueta contornos ``{clave: [array por corte]}`` → (blob float32, meta JSON).

    ``labels``: ``{clave: (etiqueta, estado)}``; estado ∈ documented | inferred | unknown.
    Devuelve ``None`` si no hay ningún contorno con datos o excede el tamaño máximo.
    """
    labels = labels or {}
    keys = [k for k in sorted(contours, key=_order) if _has_data(contours[k])]
    if not keys or n_frames <= 0:
        return None
    n_asc = max(len(a) for k in keys for a in contours[k] if a is not None)
    if len(keys) * n_frames * n_asc * 4 > _MAX_BLOB_BYTES:
        return None
    vol = np.full((len(keys), n_frames, n_asc), np.nan, dtype="<f4")
    for li, k in enumerate(keys):
        for fi, arr in enumerate(contours[k][:n_frames]):
            if arr is None:
                continue
            a = np.asarray(arr, dtype=np.float32)
            vol[li, fi, : len(a)] = np.where(np.isfinite(a), a, np.nan)
    meta = {
        "schema": SCHEMA,
        "n_layers": len(keys),
        "n_frames": n_frames,
        "n_ascans": n_asc,
        "coords": "image_rows_px;x=(k+0.5)*width/n_ascans",
        "source": "vendor_embedded_segmentation",
        "note": note,
        "layers": [
            {"key": k, "label": labels.get(k, ("", "unknown"))[0], "status": labels.get(k, ("", "unknown"))[1]}
            for k in keys
        ],
    }
    return vol.tobytes(), json.dumps(meta, ensure_ascii=False, separators=(",", ":"))


def attach_layers(ds: Dataset, encoded: tuple[bytes, str] | None) -> bool:
    """Añade los contornos al dataset; no hace nada (False) si ``encoded`` es None."""
    if encoded is None:
        return False
    blob, meta = encoded
    if TAG_CREATOR not in ds:
        ds.add_new(TAG_CREATOR, "LO", MANUFACTURER)
    ds.add_new(TAG_BLOB, "OB", blob)
    ds.add_new(TAG_META, "UT", meta)
    return True


def decode_layers(ds: Dataset) -> tuple[dict, np.ndarray] | None:
    """Inversa de ``attach_layers`` (para pruebas y herramientas): (meta, array [capa][corte][A-scan])."""
    if TAG_BLOB not in ds or TAG_META not in ds:
        return None
    meta = json.loads(str(ds[TAG_META].value))
    arr = np.frombuffer(bytes(ds[TAG_BLOB].value), dtype="<f4")
    return meta, arr.reshape(meta["n_layers"], meta["n_frames"], meta["n_ascans"])


def _order(key: str) -> tuple[int, str]:
    digits = "".join(c for c in key if c.isdigit())
    return (int(digits) if digits else 10**6, key)


def _has_data(slices: list) -> bool:
    return any(a is not None and np.isfinite(np.asarray(a, dtype=float)).any() for a in slices)
