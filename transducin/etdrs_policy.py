# transducin/etdrs_policy.py
# SPDX-License-Identifier: Apache-2.0
#
# Política de COBERTURA del ETDRS, configurable por adaptador.
#
# `revo_opt_reader.compute_etdrs` calcula los 9 sectores sin comprobar que el escaneo cubra el anillo:
# con un cubo de 3.2 mm devuelve S2/N2/I2/T2 usando solo las esquinas (~16 % del anillo externo).
# Revo y Cirrus siguen exactamente igual (policy=None). Los adaptadores NUEVOS (Spectralis, Topcon)
# pasan una política y los sectores insuficientemente cubiertos quedan en None ("no disponible"),
# en lugar de presentar un espesor sectorial representativo.
#
# Este módulo NO modifica compute_etdrs: lo llama y después anula los sectores no válidos. Las máscaras
# de sector se replican de compute_etdrs (un test las compara con él para que no se desvíen).
#
# Nota: en compute_etdrs los sectores S/I y N/T son MEDIOS anillos (S = B<0, I = B>=0, N/T según el
# eje A), por lo tanto se solapan; no son los cuadrantes de 90° del ETDRS estándar. Aquí se evalúa la
# cobertura de los sectores tal como están definidos hoy; no se cambian.
#
# Pendiente (no incluido): centrar la grilla en la fóvea en lugar del centro geométrico del volumen.

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Radios del ETDRS tal como los usa compute_etdrs (µm).
_CENTER_R = 500.0
_INNER_R = 1500.0
_OUTER_R = 3000.0
_SECTORS = ("S1", "N1", "I1", "T1", "S2", "N2", "I2", "T2")
VALID_MIN_UM, VALID_MAX_UM = 0.0, 800.0  # mismo criterio de compute_etdrs: 0 < espesor < 800


@dataclass(frozen=True)
class EtdrsCoveragePolicy:
    """Umbrales por adaptador.

    min_coverage:       fracción del área esperada del anillo/sector que cae dentro del volumen.
    min_valid_fraction: fracción de los píxeles cubiertos con espesor válido.
    """

    name: str
    min_coverage: float
    min_valid_fraction: float


# Revo y Cirrus: sin política (comportamiento actual intacto).
LEGACY: EtdrsCoveragePolicy | None = None
# Adaptadores nuevos.
STRICT = EtdrsCoveragePolicy("strict", min_coverage=0.90, min_valid_fraction=0.80)


def _masks(n_b: int, n_a: int, lateral_um: float, bscan_um: float, laterality: str) -> dict[str, np.ndarray]:
    b0, a0 = n_b // 2, n_a // 2
    db = (np.arange(n_b) - b0) * bscan_um
    da = (np.arange(n_a) - a0) * lateral_um
    B, A = np.meshgrid(db, da, indexing="ij")
    R = np.sqrt(B**2 + A**2)
    rings = {"C": R < _CENTER_R, "inner": (R >= _CENTER_R) & (R < _INNER_R), "outer": (R >= _INNER_R) & (R < _OUTER_R)}
    m_S, m_I = B < 0, B >= 0
    if laterality.upper() in ("R", "OD"):
        m_T, m_N = A > 0, A <= 0
    else:
        m_T, m_N = A < 0, A >= 0
    out = {"C": rings["C"]}
    for ring, idx in (("inner", "1"), ("outer", "2")):
        for name, half in (("S", m_S), ("N", m_N), ("I", m_I), ("T", m_T)):
            out[name + idx] = rings[ring] & half
    return out


def _expected_pixels(sector: str, lateral_um: float, bscan_um: float) -> float:
    px_area = lateral_um * bscan_um
    if sector == "C":
        return np.pi * _CENTER_R**2 / px_area
    r_in, r_out = (_CENTER_R, _INNER_R) if sector.endswith("1") else (_INNER_R, _OUTER_R)
    return np.pi * (r_out**2 - r_in**2) / 2.0 / px_area  # medio anillo


def compute_etdrs_with_policy(
    top, bm, params: dict, laterality: str = "L", policy: EtdrsCoveragePolicy | None = LEGACY, sqi=None
):
    """Llama a compute_etdrs (sin modificarlo) y aplica la política de cobertura.

    Devuelve (grid | None, informe). Con policy=None el resultado es EXACTAMENTE el de compute_etdrs y el
    informe está vacío. El informe tiene, por sector: cobertura, fracción válida, disponible y motivo.
    """
    from transducin.revo_opt_reader import compute_etdrs

    grid = compute_etdrs(top, bm, params, laterality=laterality, sqi=sqi)
    if policy is None or grid is None:
        return grid, {}
    lateral_um, bscan_um = params["lateral_um"], params["scan_width_mm"] * 1000.0 / top.shape[0]
    thickness = (np.asarray(bm, dtype=float) - np.asarray(top, dtype=float)) * params["axial_um"]
    valid = np.isfinite(thickness) & (thickness > VALID_MIN_UM) & (thickness < VALID_MAX_UM)
    report: dict[str, dict] = {}
    for sector, mask in _masks(top.shape[0], top.shape[1], lateral_um, bscan_um, laterality).items():
        n_in = int(mask.sum())
        coverage = n_in / _expected_pixels(sector, lateral_um, bscan_um)
        valid_frac = float((mask & valid).sum() / n_in) if n_in else 0.0
        if coverage < policy.min_coverage:
            reason = "coverage_below_threshold"
        elif valid_frac < policy.min_valid_fraction:
            reason = "valid_fraction_below_threshold"
        else:
            reason = None
        report[sector] = {
            "coverage": round(float(coverage), 4),
            "valid_fraction": round(valid_frac, 4),
            "available": reason is None,
            "reason": reason,
        }
        if reason is not None:
            setattr(grid, sector, None)
    report["_policy"] = {
        "name": policy.name,
        "min_coverage": policy.min_coverage,
        "min_valid_fraction": policy.min_valid_fraction,
    }
    return grid, report


def unavailable_summary(report: dict) -> str | None:
    """Texto para las notas del adaptador, o None si todo está disponible."""
    bad = [s for s in ("C",) + _SECTORS if s in report and not report[s]["available"]]
    if not bad:
        return None
    detail = ", ".join(
        f"{s} (cobertura {report[s]['coverage']:.0%}, válido {report[s]['valid_fraction']:.0%})" for s in bad
    )
    return f"ETDRS no disponible por política '{report['_policy']['name']}': {detail}"
