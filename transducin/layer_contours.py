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
TAG_FOVEA = (0x0009, 0x1023)
TAG_ACQUISITION = (0x0009, 0x1030)
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


def attach_fovea(ds: Dataset, row_frac: float, col_frac: float, method: str, depth_um: float | None = None) -> None:
    """Añade la posición de la fóvea usada para medir (0009,1023, JSON).

    ``row_frac``/``col_frac`` ∈ [0, 1] son fracciones del volumen (índice de corte / n cortes y
    A-scan / n A-scans), es decir, de la imagen en-face. ``method`` dice cómo se obtuvo
    (p. ej. ``geometric_estimate``): no es un marcador del equipo.
    """
    import json

    if TAG_CREATOR not in ds:
        ds.add_new(TAG_CREATOR, "LO", MANUFACTURER)
    payload = {
        "schema": 1,
        "row_frac": round(float(row_frac), 4),
        "col_frac": round(float(col_frac), 4),
        "method": method,
    }
    if depth_um is not None:
        payload["depth_um"] = round(float(depth_um), 1)
    ds.add_new(TAG_FOVEA, "UT", json.dumps(payload, separators=(",", ":")))


def read_fovea(ds: Dataset) -> dict | None:
    """Inversa de ``attach_fovea``; ``None`` si no hay."""
    import json

    if TAG_FOVEA not in ds:
        return None
    try:
        return json.loads(str(ds[TAG_FOVEA].value))
    except ValueError:
        return None


def attach_acquisition_id(ds: Dataset, acquisition_id: str) -> None:
    """Marca todos los DICOM salidos de un mismo archivo (OCT, SLO, en-face…) con un identificador común.

    No implica registro espacial: solo dice «vienen de la misma adquisición», para que el visor
    asocie cada volumen con sus imágenes y no con las de otro escaneo del mismo ojo.
    """
    if TAG_CREATOR not in ds:
        ds.add_new(TAG_CREATOR, "LO", MANUFACTURER)
    ds.add_new(TAG_ACQUISITION, "LO", acquisition_id)
