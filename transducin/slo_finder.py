# transducin/slo_finder.py
# SPDX-License-Identifier: Apache-2.0
"""Buscador de SLO: fondo de ojo + posición de cada corte + mapa de grosor en color (ADR-028).

Contrato con Gass: elemento privado (0009,1022) UT en el bloque ``RetinaOS-Transducin`` con un JSON:
``schema``, ``field_deg``, ``size`` [ancho, alto] del SLO ya reducido, ``slo_jpeg_b64`` (escala de
grises), ``lines`` (una [x1, y1, x2, y2] por fotograma, en píxeles del SLO reducido), y opcional
``thickness_png_b64`` (RGBA del mismo tamaño; transparente fuera del volumen) con
``thickness_range_um``.

Geometría verificada con archivos reales (ADR-027): posX/posY están en grados de exploración,
x a la derecha e y hacia abajo en la imagen del SLO, y el SLO cubre ``field_deg`` (30°) en todo
su ancho. Los píxeles del mapa de grosor se obtienen del paralelogramo que forman el primer y el
último corte; si las líneas no son paralelas y equiespaciadas no se genera el mapa.
"""

from __future__ import annotations

import base64
import io
import json

import numpy as np
from PIL import Image
from pydicom.dataset import Dataset

from transducin.dicom_config import MANUFACTURER

TAG_CREATOR = (0x0009, 0x0010)
TAG_FINDER = (0x0009, 0x1022)
SCHEMA = 1
FIELD_DEG = 30.0

# Paleta de grosor (µm): negro → gris → azul → cian → verde → amarillo → naranja → rojo → magenta → blanco
THICKNESS_MAX_UM = 800

_STOPS = [
    (0, (0, 0, 0)),
    (100, (128, 128, 128)),
    (200, (40, 80, 255)),
    (270, (0, 200, 255)),
    (320, (0, 200, 60)),
    (380, (255, 255, 0)),
    (450, (255, 150, 0)),
    (520, (255, 0, 0)),
    (650, (255, 0, 200)),
    (800, (255, 255, 255)),
]


def thickness_colormap(values_um: np.ndarray) -> np.ndarray:
    """Valores en µm → RGB uint8 con la paleta de grosor retiniano (0–800 µm)."""
    xs = np.array([s[0] for s in _STOPS], dtype=float)
    v = np.clip(np.asarray(values_um, dtype=float), xs[0], xs[-1])
    return np.stack(
        [np.interp(v, xs, [s[1][c] for s in _STOPS]) for c in range(3)],
        axis=-1,
    ).astype(np.uint8)


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _line_px(bd: dict, size: int) -> list[float]:
    k = size / FIELD_DEG
    c = size / 2.0
    return [
        round(c + bd["posX1"] * k, 2),
        round(c + bd["posY1"] * k, 2),
        round(c + bd["posX2"] * k, 2),
        round(c + bd["posY2"] * k, 2),
    ]


def _thickness_overlay(
    lines: list[list[float]], thickness_um: np.ndarray, size: int
) -> tuple[Image.Image, Image.Image] | None:
    """(RGBA coloreado, LA en grises) del grosor interpolado en el paralelogramo de cortes; ``None`` si no es trama.

    El PNG en grises (valor = µm × 255 / 800, alfa = máscara) permite a Gass aplicar cualquier escala de color.
    """
    n_b, n_a = thickness_um.shape
    if n_b < 8 or n_a < 8 or len(lines) != n_b:
        return None
    L = np.array(lines, dtype=float)
    p0 = L[0, :2]
    u = L[0, 2:] - L[0, :2]  # a lo largo de un corte
    v = L[-1, :2] - L[0, :2]  # del primer al último corte
    det = u[0] * v[1] - u[1] * v[0]
    if abs(det) < 1e-6:
        return None
    # equiespaciado y paralelo: el corte medio debe estar a medio camino
    mid = L[n_b // 2, :2] - p0
    if np.hypot(*(mid - v * ((n_b // 2) / (n_b - 1)))) > 0.02 * size:
        return None

    ys, xs = np.mgrid[0:size, 0:size].astype(float)
    qx, qy = xs - p0[0], ys - p0[1]
    a = (qx * v[1] - qy * v[0]) / det
    s = (u[0] * qy - u[1] * qx) / det
    inside = (a >= 0) & (a <= 1) & (s >= 0) & (s <= 1)

    ai, si = a * (n_a - 1), s * (n_b - 1)
    a0 = np.clip(np.floor(ai).astype(int), 0, n_a - 2)
    s0 = np.clip(np.floor(si).astype(int), 0, n_b - 2)
    fa, fs = ai - a0, si - s0
    t = thickness_um
    corners = np.stack([t[s0, a0], t[s0, a0 + 1], t[s0 + 1, a0], t[s0 + 1, a0 + 1]])
    ok = np.isfinite(corners).all(axis=0) & (corners > 0).all(axis=0) & (corners < 800).all(axis=0)
    val = (
        corners[0] * (1 - fa) * (1 - fs)
        + corners[1] * fa * (1 - fs)
        + corners[2] * (1 - fa) * fs
        + corners[3] * fa * fs
    )
    mask = inside & ok
    if not mask.any():
        return None
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., :3] = thickness_colormap(np.where(mask, val, 0))
    rgba[..., 3] = np.where(mask, 255, 0)
    gray = np.zeros((size, size, 2), dtype=np.uint8)  # L = µm·255/800, A = máscara
    gray[..., 0] = np.clip(np.where(mask, val, 0) * 255.0 / THICKNESS_MAX_UM + 0.5, 0, 255)
    gray[..., 1] = np.where(mask, 255, 0)
    return Image.fromarray(rgba, mode="RGBA"), Image.fromarray(gray, mode="LA")


def build_finder(
    fundus: np.ndarray | None,
    bscan_data: list[dict],
    n_frames: int,
    thickness_um: np.ndarray | None = None,
    out_px: int = 768,
) -> dict | None:
    """Diccionario del buscador, o ``None`` si falta el SLO o la posición de los cortes."""
    if fundus is None or not bscan_data or len(bscan_data) != n_frames:
        return None
    size = min(out_px, fundus.shape[1])
    slo = Image.fromarray(fundus, mode="L").resize((size, size), Image.LANCZOS)
    jbuf = io.BytesIO()
    slo.save(jbuf, format="JPEG", quality=88)
    lines = [_line_px(bd, size) for bd in bscan_data]
    out: dict = {
        "schema": SCHEMA,
        "field_deg": FIELD_DEG,
        "size": [size, size],
        "frame_order": "slice_index",
        "slo_jpeg_b64": base64.b64encode(jbuf.getvalue()).decode("ascii"),
        "lines": lines,
        "geometry_note": "posX/posY en grados; x a la derecha, y hacia abajo; el SLO cubre field_deg en todo su ancho.",
    }
    if thickness_um is not None:
        overlay = _thickness_overlay(lines, thickness_um, size)
        if overlay is not None:
            out["thickness_png_b64"] = _png_b64(overlay[0])
            out["thickness_gray_png_b64"] = _png_b64(overlay[1])
            out["thickness_range_um"] = [0, THICKNESS_MAX_UM]
    return out


def attach_finder(ds: Dataset, finder: dict | None) -> bool:
    if finder is None:
        return False
    if TAG_CREATOR not in ds:
        ds.add_new(TAG_CREATOR, "LO", MANUFACTURER)
    ds.add_new(TAG_FINDER, "UT", json.dumps(finder, separators=(",", ":")))
    return True


def decode_finder(ds: Dataset) -> dict | None:
    return json.loads(str(ds[TAG_FINDER].value)) if TAG_FINDER in ds else None
