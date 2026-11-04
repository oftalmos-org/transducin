# transducin/topcon_experimental.py
# SPDX-License-Identifier: Apache-2.0
#
# Espesor ILM→BM EXPERIMENTAL del Topcon 3D OCT-1000 Mark II a partir de los contornos originales
# RETINA_1 y RETINA_4 del .fda (sin modificarlos).
#
# Condiciones (acordadas con el titular, 2026-11-04):
#   - Solo para 3DOCT-1000MK2 y solo con la opción explícita `experimental` del adapter/script.
#   - measurement_provenance = "vendor_contour_derived".
#   - La identidad anatómica procede de revisión visual del especialista y de evidencia
#     bibliográfica; la correspondencia con el campo binario NO está confirmada
#     (ver transducin.topcon_retina_map).
#   - Se devuelven el mapa de espesor y estadísticas EXPLORATORIAS. No se publica como CMT clínico,
#     DICOM cuantitativo ni observación en Graefe: este módulo no toca OCTClinicalData.cmt_um.
#   - Se verifica la escala axial y se marcan las regiones con contornos incompletos o geometría inválida.

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from transducin.readers.fda_reader import FDAReader
from transducin.topcon_retina_map import EXPECTED_AXIAL_UM, HYPOTHESIS_RETINA_MAP, MAP_VERSION, SUPPORTED_MODEL

PLAUSIBLE_MIN_UM = 50.0  # por debajo: geometría no plausible para ILM→BM
PLAUSIBLE_MAX_UM = 800.0  # igual que el límite de compute_etdrs
MIN_BSCAN_VALID_FRACTION = 0.9
AXIAL_TOLERANCE_UM = 0.05


class ExperimentalUnavailable(RuntimeError):
    """El espesor experimental no se puede calcular para este archivo (modelo, escala o contornos)."""


@dataclass
class ExperimentalThickness:
    thickness_um: np.ndarray  # (n_bscans, n_ascans); NaN donde no es válido
    valid_mask: np.ndarray  # bool, misma forma
    flags: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    axial_scale: dict = field(default_factory=dict)
    device: dict = field(default_factory=dict)
    status: str = "experimental"
    measurement_provenance: str = "vendor_contour_derived"
    publishable_as_clinical: bool = False
    identity: dict = field(default_factory=dict)

    def to_json_dict(self) -> dict:
        """Metadatos y estadísticas (sin el mapa completo)."""
        return {
            "status": self.status,
            "publishable_as_clinical": self.publishable_as_clinical,
            "measurement_provenance": self.measurement_provenance,
            "identity": self.identity,
            "device": self.device,
            "axial_scale": self.axial_scale,
            "flags": self.flags,
            "stats": self.stats,
        }


def thickness_from_contours(ilm: np.ndarray, bm: np.ndarray, axial_um: float) -> tuple[np.ndarray, dict]:
    """Espesor (µm) = (BM − ILM) · axial_um con validación por posición.

    Devuelve (mapa con NaN en posiciones inválidas, conteos por causa). Las causas son excluyentes
    y se aplican en este orden: contorno ausente (NaN) → geometría inválida (BM no por debajo de la
    ILM) → fuera del rango plausible.
    """
    ilm = np.asarray(ilm, dtype=float)
    bm = np.asarray(bm, dtype=float)
    raw = (bm - ilm) * axial_um
    missing = ~np.isfinite(raw)
    inverted = ~missing & (raw <= 0)
    out_of_range = ~missing & ~inverted & ((raw < PLAUSIBLE_MIN_UM) | (raw > PLAUSIBLE_MAX_UM))
    out = raw.copy()
    out[missing | inverted | out_of_range] = np.nan
    counts = {
        "contour_missing": int(missing.sum()),
        "geometry_invalid": int(inverted.sum()),
        "out_of_plausible_range": int(out_of_range.sum()),
    }
    return out, counts


def _stats(t: np.ndarray, axial_um: float, lateral_um: float, bscan_um: float) -> dict:
    n_b, n_a = t.shape
    v = t[np.isfinite(t)]
    s: dict = {"n_positions": int(t.size), "n_valid": int(v.size), "valid_fraction": round(float(v.size / t.size), 4)}
    if v.size:
        s.update(
            mean_um=round(float(v.mean()), 1),
            median_um=round(float(np.median(v)), 1),
            sd_um=round(float(v.std()), 1),
            p5_um=round(float(np.percentile(v, 5)), 1),
            p95_um=round(float(np.percentile(v, 95)), 1),
            min_um=round(float(v.min()), 1),
            max_um=round(float(v.max()), 1),
        )
    # Disco central y anillo, alrededor del CENTRO GEOMÉTRICO del volumen (la fóvea no está localizada).
    db = (np.arange(n_b) - n_b // 2) * bscan_um
    da = (np.arange(n_a) - n_a // 2) * lateral_um
    r = np.hypot(db[:, None], da[None, :])
    for name, m in (("central_disc_r500um", r < 500), ("ring_500_1500um", (r >= 500) & (r < 1500))):
        x = t[m & np.isfinite(t)]
        s[name] = {
            "mean_um": round(float(x.mean()), 1) if x.size else None,
            "sd_um": round(float(x.std()), 1) if x.size else None,
            "n_valid": int(x.size),
            "n_expected": int(m.sum()),
        }
    s["center"] = "centro geométrico del volumen (fóvea no localizada)"
    s["sector_labels"] = "no se reportan sectores S/N/I/T: la orientación del volumen Topcon no está verificada"
    return s


def compute_experimental_thickness(reader: FDAReader) -> ExperimentalThickness:
    """Calcula el espesor ILM→BM experimental con RETINA_1 y RETINA_4 de un FDAReader."""
    device = reader.read_device_info()
    if device.get("model") != SUPPORTED_MODEL:
        raise ExperimentalUnavailable(
            f"modelo {device.get('model')!r}: el espesor experimental solo está habilitado para {SUPPORTED_MODEL}"
        )
    vols = reader.read_layer_volumes()
    if not vols:
        raise ExperimentalUnavailable("el archivo no trae @CONTOUR_INFO")
    vol = vols[0]
    first, last = HYPOTHESIS_RETINA_MAP["used_for_thickness"]
    if first not in vol.contours or last not in vol.contours:
        raise ExperimentalUnavailable(f"faltan los contornos {first}/{last}")
    ilm, bm = vol.contours[first], vol.contours[last]
    if ilm.shape != bm.shape:
        raise ExperimentalUnavailable("los contornos tienen formas distintas")
    ps = vol.pixel_spacing
    if not ps or len(ps) < 3:
        raise ExperimentalUnavailable("sin escala física en el archivo (@PARAM_SCAN)")
    lateral_um, bscan_um, axial_um = ps[0] * 1000.0, ps[1] * 1000.0, ps[2] * 1000.0
    scale_ok = abs(axial_um - EXPECTED_AXIAL_UM) <= AXIAL_TOLERANCE_UM
    axial = {
        "value_um_per_px": round(axial_um, 4),
        "source": "PARAM_SCAN_04.z_resolution_um",
        "expected_um_per_px": EXPECTED_AXIAL_UM,
        "expected_source": "Yang et al. 2010 y 2011 (3D OCT-1000, 3.5 µm/píxel)",
        "verified": bool(scale_ok),
    }
    if not scale_ok:
        raise ExperimentalUnavailable(
            f"escala axial {axial_um:.3f} µm/px distinta de {EXPECTED_AXIAL_UM} µm/px: no se calcula"
        )
    t, counts = thickness_from_contours(ilm, bm, axial_um)
    valid = np.isfinite(t)
    # B-scans con muchas posiciones inválidas
    frac_b = valid.mean(axis=1)
    incomplete = [int(i) for i in np.where(frac_b < MIN_BSCAN_VALID_FRACTION)[0]]
    # Contornos intermedios: no cruzar a los extremos ni entre sí (informativo; no se usan en el cálculo)
    order_viol = None
    mids = [k for k in ("RETINA_2", "RETINA_3") if k in vol.contours and vol.contours[k].shape == ilm.shape]
    if mids:
        chain = [ilm] + [vol.contours[k] for k in mids] + [bm]
        viol = np.zeros(ilm.shape, dtype=bool)
        for a, b in itertools.pairwise(chain):
            viol |= np.isfinite(a) & np.isfinite(b) & (b < a)
        order_viol = round(float(viol.mean()), 6)
    flags = {
        **counts,
        "incomplete_bscans_below_90pct_valid": incomplete,
        "intermediate_contour_order_violation_fraction": order_viol,
        "invalid_positions_total": int((~valid).sum()),
    }
    identity = {
        "mapping_version": MAP_VERSION,
        "ILM": first,
        "BM": last,
        "anatomical_identity_source": "revisión visual del especialista (1 lector) + evidencia bibliográfica",
        "binary_correspondence_confirmed": False,
        "note": "la identidad anatómica de RETINA_1/RETINA_4 NO está confirmada contra el campo binario del archivo",
        "hypothesis": {
            k: v
            for k, v in HYPOTHESIS_RETINA_MAP.items()
            if k in ("version", "status", "device_model", "software_stamp")
        },
    }
    return ExperimentalThickness(
        thickness_um=t,
        valid_mask=valid,
        flags=flags,
        stats=_stats(t, axial_um, lateral_um, bscan_um),
        axial_scale=axial,
        device=device,
        identity=identity,
    )


def experimental_thickness_from_file(path: str | Path) -> ExperimentalThickness:
    return compute_experimental_thickness(FDAReader(path))
