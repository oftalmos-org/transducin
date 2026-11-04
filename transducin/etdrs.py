# transducin/etdrs.py
# SPDX-License-Identifier: Apache-2.0
#
# ETDRS v2: nueve regiones NO solapadas, en µm reales, con orientación explícita, centro con su origen
# y cobertura por sector.
#
# Diferencias con revo_opt_reader.compute_etdrs (ruta legacy, que se conserva sin tocar):
#   - Anillos interno (0.5–1.5 mm de radio) y externo (1.5–3.0 mm) divididos en CUATRO cuadrantes de 90°
#     (cuñas diagonales: superior, nasal, inferior, temporal), disjuntos y que cubren cada anillo completo.
#     El legacy usa MEDIOS anillos que se solapan (S = B<0, I = B>=0, N/T según el eje A).
#   - La orientación (qué lado es el B-scan 0, hacia dónde crece el A-scan en cada ojo) es un parámetro
#     explícito con su estado de validación; no se supone.
#   - El centro es un objeto con ORIGEN: "geometric", "foveal_candidate_min_thickness" (algorítmico, sin
#     validar) o "foveal_registered". Una medición central NO se llama CMT clínico salvo que el centro esté
#     registrado en la fóvea Y validado.
#   - Cobertura por región: fracción del área esperada que cae dentro del escaneo y fracción de píxeles
#     válidos. Una región insuficiente queda en None, no se presenta un valor representativo.
#
# Esta ruta NO está conectada a ningún adaptador clínico ni a SR/DICOM/Graefe. Es para comparación y para
# los adaptadores experimentales; la migración de Revo/Cirrus se decide aparte (validation/).

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from transducin.etdrs_policy import STRICT, EtdrsCoveragePolicy

RADII_UM = (500.0, 1500.0, 3000.0)  # centro, anillo interno (3 mm de diámetro), anillo externo (6 mm)
REGIONS = ("C", "S1", "N1", "I1", "T1", "S2", "N2", "I2", "T2")
VALID_MIN_UM, VALID_MAX_UM = 0.0, 800.0  # mismo criterio que compute_etdrs: 0 < espesor < 800


@dataclass(frozen=True)
class Orientation:
    """Cómo se corresponden los índices del volumen con los lados del paciente.

    row0_side:                lado del paciente donde está el B-scan de índice 0 ("superior" | "inferior").
    ascan_toward_nasal_od/os: True si, en ese ojo, el índice de A-scan crece hacia el lado nasal.
    validated:                True solo si está comprobado con datos (no por convención).
    """

    row0_side: str
    ascan_toward_nasal_od: bool
    ascan_toward_nasal_os: bool
    validated: bool
    source: str


# Supuesto histórico de compute_etdrs (Revo): B-scan 0 superior; OD con nasal a la izquierda, OS a la derecha.
LEGACY_ASSUMED = Orientation("superior", False, True, False, "supuesto de compute_etdrs (Revo); sin verificar")
# Heidelberg .vol: B-scan 0 inferior (posiciones de cada B-scan en el SLO, verificado en los dos ejemplos de
# eyepydata); lado nasal por la convención de vista de fondo de ojo (OD nasal a la derecha, OS a la izquierda).
HEIDELBERG_VOL = Orientation(
    "inferior",
    True,
    False,
    False,
    "B-scan 0 inferior: posiciones del archivo (verificado); nasal: convención de fondo de ojo, sin verificar con el disco",
)


@dataclass(frozen=True)
class Center:
    row: float
    col: float
    source: str  # "geometric" | "foveal_candidate_min_thickness" | "foveal_registered"
    validated: bool = False


def geometric_center(shape: tuple[int, int]) -> Center:
    return Center((shape[0] - 1) / 2.0, (shape[1] - 1) / 2.0, "geometric", False)


def find_foveal_candidate(
    thickness_um: np.ndarray,
    lateral_um: float,
    bscan_um: float,
    search_radius_um: float = 1500.0,
    smooth_um: float = 300.0,
) -> Center:
    """Candidato a centro foveal = mínimo de espesor suavizado cerca del centro. NO validado."""
    t = np.asarray(thickness_um, dtype=float)
    fill = np.where(np.isfinite(t), t, np.nanmedian(t))
    kb, ka = max(1, round(smooth_um / bscan_um) | 1), max(1, round(smooth_um / lateral_um) | 1)

    def box(a: np.ndarray, k: int, axis: int) -> np.ndarray:
        pad = [(0, 0), (0, 0)]
        pad[axis] = (k // 2, k // 2)
        c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=float)
        c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
        n = a.shape[axis]
        return (np.take(c, range(k, k + n), axis=axis) - np.take(c, range(n), axis=axis)) / k

    sm = box(box(fill, kb, 0), ka, 1)
    g = geometric_center(t.shape)
    rr, cc = np.arange(t.shape[0])[:, None], np.arange(t.shape[1])[None, :]
    dist = np.hypot((rr - g.row) * bscan_um, (cc - g.col) * lateral_um)
    sm = np.where(dist <= search_radius_um, sm, np.inf)
    i, j = np.unravel_index(int(np.argmin(sm)), sm.shape)
    return Center(float(i), float(j), "foveal_candidate_min_thickness", False)


@dataclass
class EtdrsRegion:
    name: str
    mean_um: float | None
    n_valid: int
    n_in_scan: int
    expected_pixels: float
    coverage: float
    valid_fraction: float
    available: bool
    reason: str | None
    expected_area_mm2: float


@dataclass
class EtdrsV2Result:
    regions: dict[str, EtdrsRegion]
    center: Center
    orientation: Orientation
    laterality: str
    lateral_um: float
    bscan_um: float
    policy: str
    warnings: list[str] = field(default_factory=list)

    def value(self, name: str) -> float | None:
        return self.regions[name].mean_um

    @property
    def central_subfield_um(self) -> float | None:
        return self.regions["C"].mean_um

    @property
    def centered_on_fovea(self) -> bool:
        return self.center.source == "foveal_registered"

    @property
    def clinical_central_eligible(self) -> bool:
        """Solo con centro registrado en la fóvea Y validado (y el disco central disponible)."""
        return self.center.source == "foveal_registered" and self.center.validated and self.regions["C"].available

    @property
    def central_label(self) -> str:
        if self.clinical_central_eligible:
            return "central_subfield_thickness"
        if self.centered_on_fovea:
            return "central_subfield_foveal_registered_unvalidated"
        return f"central_disc_{self.center.source}"  # nunca "CMT"

    def to_dict(self) -> dict:
        return {
            "central_label": self.central_label,
            "clinical_central_eligible": self.clinical_central_eligible,
            "center": {
                "row": self.center.row,
                "col": self.center.col,
                "source": self.center.source,
                "validated": self.center.validated,
            },
            "orientation": {
                "row0_side": self.orientation.row0_side,
                "validated": self.orientation.validated,
                "source": self.orientation.source,
                "ascan_toward_nasal_od": self.orientation.ascan_toward_nasal_od,
                "ascan_toward_nasal_os": self.orientation.ascan_toward_nasal_os,
            },
            "laterality": self.laterality,
            "lateral_um": self.lateral_um,
            "bscan_um": self.bscan_um,
            "policy": self.policy,
            "warnings": self.warnings,
            "regions": {
                n: {
                    "mean_um": None if r.mean_um is None else round(r.mean_um, 2),
                    "coverage": round(r.coverage, 4),
                    "valid_fraction": round(r.valid_fraction, 4),
                    "available": r.available,
                    "reason": r.reason,
                    "n_valid": r.n_valid,
                }
                for n, r in self.regions.items()
            },
        }


def region_masks(shape, lateral_um, bscan_um, laterality, orientation, center: Center) -> dict[str, np.ndarray]:
    """Máscaras disjuntas de las 9 regiones. Cuadrantes por ángulo: nasal [-45°,45°), superior [45°,135°),
    temporal [135°,225°), inferior [225°,315°)."""
    rows, cols = np.arange(shape[0])[:, None], np.arange(shape[1])[None, :]
    v = (rows - center.row) * bscan_um  # crece hacia índices mayores de B-scan
    h = (cols - center.col) * lateral_um  # crece hacia índices mayores de A-scan
    sup = -v if orientation.row0_side == "superior" else v
    right_eye = laterality.upper() in ("R", "OD")
    toward_nasal = orientation.ascan_toward_nasal_od if right_eye else orientation.ascan_toward_nasal_os
    nas = h if toward_nasal else -h
    r = np.hypot(sup, nas)
    ang = np.degrees(np.arctan2(sup, nas))
    quad = {
        "N": (ang >= -45) & (ang < 45),
        "S": (ang >= 45) & (ang < 135),
        "T": (ang >= 135) | (ang < -135),
        "I": (ang >= -135) & (ang < -45),
    }
    out = {"C": r < RADII_UM[0]}
    for suffix, (lo, hi) in (("1", RADII_UM[:2]), ("2", RADII_UM[1:])):
        ring = (r >= lo) & (r < hi)
        for q, m in quad.items():
            out[q + suffix] = ring & m
    return out


def _expected_pixels(name: str, lateral_um: float, bscan_um: float) -> float:
    px = lateral_um * bscan_um
    if name == "C":
        return float(np.pi * RADII_UM[0] ** 2 / px)
    lo, hi = RADII_UM[:2] if name.endswith("1") else RADII_UM[1:]
    return float(np.pi * (hi**2 - lo**2) / 4.0 / px)


def compute_etdrs_v2(
    thickness_um,
    *,
    lateral_um: float,
    bscan_um: float,
    laterality: str,
    orientation: Orientation,
    center: Center | None = None,
    policy: EtdrsCoveragePolicy | None = STRICT,
) -> EtdrsV2Result:
    """ETDRS de 9 regiones disjuntas a partir de un mapa de espesor en µm (NaN = inválido)."""
    t = np.asarray(thickness_um, dtype=float)
    center = center or geometric_center(t.shape)
    masks = region_masks(t.shape, lateral_um, bscan_um, laterality, orientation, center)
    valid = np.isfinite(t) & (t > VALID_MIN_UM) & (t < VALID_MAX_UM)
    regions: dict[str, EtdrsRegion] = {}
    for name in REGIONS:
        m = masks[name]
        n_in, n_valid = int(m.sum()), int((m & valid).sum())
        exp_px = _expected_pixels(name, lateral_um, bscan_um)
        coverage = min(1.0, n_in / exp_px)
        vf = n_valid / n_in if n_in else 0.0
        reason = None
        if policy is not None:
            if coverage < policy.min_coverage:
                reason = "coverage_below_threshold"
            elif vf < policy.min_valid_fraction:
                reason = "valid_fraction_below_threshold"
        if n_valid == 0 and reason is None:
            reason = "no_valid_pixels"
        mean = float(t[m & valid].mean()) if reason is None else None
        area = exp_px * lateral_um * bscan_um / 1e6
        regions[name] = EtdrsRegion(name, mean, n_valid, n_in, exp_px, coverage, vf, reason is None, reason, area)
    warnings = []
    if not orientation.validated:
        warnings.append(f"orientación NO validada ({orientation.source}): los rótulos S/N/I/T no son concluyentes")
    if center.source != "foveal_registered" or not center.validated:
        warnings.append(f"centro '{center.source}' sin validar: la medición central no es CMT clínico")
    return EtdrsV2Result(
        regions, center, orientation, laterality, lateral_um, bscan_um, policy.name if policy else "none", warnings
    )
