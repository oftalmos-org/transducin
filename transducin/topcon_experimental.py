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
    # Segundo cálculo experimental: ILM→RPE con RETINA_1 y RETINA_3 (RETINA_3 = posible límite RPE / OS-RPE)
    rpe_thickness_um: np.ndarray | None = None
    rpe_flags: dict = field(default_factory=dict)
    rpe_stats: dict = field(default_factory=dict)
    rpe_identity: dict = field(default_factory=dict)
    comparison: dict = field(default_factory=dict)

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
            "ilm_to_rpe_candidate": {
                "identity": self.rpe_identity,
                "flags": self.rpe_flags,
                "stats": self.rpe_stats,
            },
            "comparison_ilm_bm_vs_ilm_rpe": self.comparison,
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
    stats = _stats(t, axial_um, lateral_um, bscan_um)
    rpe_t = None
    rpe_flags: dict = {}
    rpe_stats: dict = {}
    rpe_identity: dict = {}
    comparison: dict = {}
    rpe_key = HYPOTHESIS_RETINA_MAP["used_for_candidate_thickness"][1]
    if rpe_key in vol.contours and vol.contours[rpe_key].shape == ilm.shape:
        rpe_t, rpe_counts = thickness_from_contours(ilm, vol.contours[rpe_key], axial_um)
        rpe_valid = np.isfinite(rpe_t)
        rpe_flags = {**rpe_counts, "invalid_positions_total": int((~rpe_valid).sum())}
        rpe_stats = _stats(rpe_t, axial_um, lateral_um, bscan_um)
        rpe_identity = {
            "ILM": first,
            "RPE_candidate": rpe_key,
            "anatomical_identity_source": "revisión visual del especialista (1 lector) + evidencia bibliográfica",
            "binary_correspondence_confirmed": False,
            "note": (
                f"{rpe_key} = posible límite RPE / OS-RPE; no se fija una definición anatómica más precisa de la que se "
                "puede demostrar. El algoritmo integrado de Topcon mide hasta el borde anterior del RPE (Chen 2019, "
                "PMC6488992), pero no se demuestra que este contorno sea ese borde."
            ),
        }
        both = valid & rpe_valid
        diff = (t - rpe_t)[both]  # ILM→BM − ILM→RPE = separación RETINA_3→RETINA_4
        c_bm, r_bm = stats["central_disc_r500um"]["mean_um"], stats["ring_500_1500um"]["mean_um"]
        c_rp, r_rp = rpe_stats["central_disc_r500um"]["mean_um"], rpe_stats["ring_500_1500um"]["mean_um"]
        comparison = {
            "n_positions_both_valid": int(both.sum()),
            "difference_um_ilm_bm_minus_ilm_rpe": {
                "mean": round(float(diff.mean()), 1),
                "median": round(float(np.median(diff)), 1),
                "sd": round(float(diff.std()), 1),
                "p5": round(float(np.percentile(diff, 5)), 1),
                "p95": round(float(np.percentile(diff, 95)), 1),
                "min": round(float(diff.min()), 1),
                "max": round(float(diff.max()), 1),
            },
            "central_disc_r500um": {"ilm_bm_um": c_bm, "ilm_rpe_um": c_rp, "difference_um": round(c_bm - c_rp, 1)},
            "ring_500_1500um": {"ilm_bm_um": r_bm, "ilm_rpe_um": r_rp, "difference_um": round(r_bm - r_rp, 1)},
            "foveal_dip_um_ring_minus_central": {"ilm_bm": round(r_bm - c_bm, 1), "ilm_rpe": round(r_rp - c_rp, 1)},
            "correlation_of_maps": round(float(np.corrcoef(t[both], rpe_t[both])[0, 1]), 4),
            "note": "ambos mapas son exploratorios, derivados de contornos del fabricante; ninguno es un valor reportado por Topcon",
        }
    return ExperimentalThickness(
        thickness_um=t,
        valid_mask=valid,
        flags=flags,
        stats=stats,
        axial_scale=axial,
        device=device,
        identity=identity,
        rpe_thickness_um=rpe_t,
        rpe_flags=rpe_flags,
        rpe_stats=rpe_stats,
        rpe_identity=rpe_identity,
        comparison=comparison,
    )


def experimental_thickness_from_file(path: str | Path) -> ExperimentalThickness:
    return compute_experimental_thickness(FDAReader(path))
