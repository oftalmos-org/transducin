"""Modelo de evidencia de lateralidad (solo observación; no decide ni cambia nada).

Para cada estudio reúne las fuentes originales disponibles, las agrupa por **origen del dato** (dos campos que
provienen del mismo dato original NO son dos pruebas independientes) y clasifica el estado:

  declared    lateralidad original declarada por el equipo/archivo, sin conflicto
  inferred    solo hay inferencia débil (p. ej. nombre de archivo)
  confirmed   confirmada por el médico (o por una referencia independiente) sin contradicción
  conflict    las fuentes disponibles se contradicen
  unknown     no hay ninguna fuente utilizable

Un **conflicto no es un ojo equivocado**: indica que alguna fuente falla o es inadecuada; demostrar el error real
exige una referencia independiente (localizador/SLO, confirmación médica). Este módulo nunca afirma lo contrario.

Grupos de origen (``group``):
  device_dicom_header     Laterality (0020,0060), ImageLaterality (0020,0062), FrameLaterality (0020,9072) y los
                          atributos privados de Cirrus: todos los escribe el mismo equipo → un solo grupo
  vendor_native           metadatos nativos del fabricante (E2E, FDA…) tal como los leyó el extractor
  acquisition_geometry    geometría de adquisición independiente del rótulo (Revo: signo de OCTPARAMS tag 23)
  filename                inferencia desde el nombre de archivo (fuente débil)
  physician_confirmed     lateralidad asignada y confirmada por el médico (aún sin productor en el pipeline)
  independent_acquisition información de adquisición independiente (p. ej. posición del disco en el localizador;
                          aún sin productor)

Privacidad: ``Evidence`` guarda solo el valor normalizado (R/L), el grupo y la fuerza; nunca el nombre de archivo.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from transducin.clinical_data import OCTClinicalData

logger = logging.getLogger(__name__)

STRONG = "strong"  # declarada por el equipo o geometría de adquisición
REFERENCE = "reference"  # confirmación médica o información independiente
WEAK = "weak"  # inferencia

GROUP_STRENGTH = {
    "device_dicom_header": STRONG,
    "vendor_native": STRONG,
    "acquisition_geometry": STRONG,
    "physician_confirmed": REFERENCE,
    "independent_acquisition": REFERENCE,
    "filename": WEAK,
}

DEFAULT_MAX_READ_MB = 400


@dataclass(frozen=True)
class Evidence:
    source: str  # identificador técnico de la fuente (p. ej. "dicom:0020,0060")
    group: str  # origen del dato (ver cabecera)
    value: str | None  # "R" | "L" | "B" | None (no interpretable)
    raw_kind: str = ""  # forma del valor crudo sin PHI (p. ej. "OD", "3.0", "sign+")

    @property
    def strength(self) -> str:
        return GROUP_STRENGTH.get(self.group, WEAK)


def normalize(raw: object) -> str | None:
    """Normaliza un valor a "R"/"L"/"B" (ambos) o ``None`` si no es interpretable."""
    s = str(raw if raw is not None else "").split("\x00")[0].strip().upper()
    if s in ("R", "OD", "RIGHT", "DERECHO"):
        return "R"
    if s in ("L", "OS", "LEFT", "IZQUIERDO"):
        return "L"
    if s in ("B", "OU", "BOTH"):
        return "B"
    return None


# ── Recolectores ─────────────────────────────────────────────────────────────

_TOKEN_SPLIT = re.compile(r"[_\-. ]+")


def from_filename(name: str) -> list[Evidence]:
    """Token aislado OD/OS en el nombre de archivo (R/L sueltos son demasiado ambiguos). Fuente débil."""
    tokens = {t.upper() for t in _TOKEN_SPLIT.split(Path(name).stem) if t}
    found = [t for t in ("OD", "OS") if t in tokens]
    if len(found) == 1:
        return [Evidence("filename:token", "filename", normalize(found[0]), found[0])]
    if len(found) == 2:  # ambiguo: ambos tokens → no interpretable
        return [Evidence("filename:token", "filename", None, "OD+OS")]
    return []


def from_dicom_dataset(ds: Any) -> list[Evidence]:
    """Atributos DICOM del equipo (un solo grupo de origen)."""
    out: list[Evidence] = []

    def add(src: str, raw: object, kind: str | None = None) -> None:
        out.append(Evidence(src, "device_dicom_header", normalize(raw), kind or str(raw).split("\x00")[0].strip()[:8]))

    for tag, src in (((0x0020, 0x0060), "dicom:0020,0060"), ((0x0020, 0x0062), "dicom:0020,0062")):
        el = ds.get(tag)
        if el is not None and str(el.value).strip():
            add(src, el.value)
    # FrameLaterality (0020,9072) dentro de FrameAnatomySequence (0020,9071) de los grupos funcionales
    seen: set[str] = set()
    for seq_tag in ((0x5200, 0x9229), (0x5200, 0x9230)):  # Shared / PerFrame FunctionalGroupsSequence
        seq = ds.get(seq_tag)
        for item in (seq.value if seq is not None else [])[:2048]:
            fa = item.get((0x0020, 0x9071))
            for sub in fa.value if fa is not None else []:
                fl = sub.get((0x0020, 0x9072))
                if fl is not None:
                    seen.add(str(fl.value).split("\x00")[0].strip())
    for v in sorted(seen):
        add("dicom:0020,9072", v)
    # Atributos privados del Cirrus (3.0 = OD, 6.0 = OS observado empíricamente)
    t = ds.get((0x0063, 0x1005))
    if t is not None:
        try:
            f = float(t.value)
            out.append(
                Evidence(
                    "dicom:0063,1005", "device_dicom_header", "R" if f == 3.0 else "L" if f == 6.0 else None, f"{f:g}"
                )
            )
        except (TypeError, ValueError):
            out.append(Evidence("dicom:0063,1005", "device_dicom_header", None, "unparsed"))
    t = ds.get((0x0057, 0x1015))
    if t is not None and str(t.value).strip():
        add("dicom:0057,1015", t.value, "text")
    return out


def _read_dicom_header(path: Path) -> Any:
    import pydicom

    return pydicom.dcmread(str(path), stop_before_pixels=True, force=True)


def _read_revo_tag23(path: Path) -> float | None:
    """Lee ``scan_center_x_mm`` (OCTPARAMS tag 23) del .opt. Positivo = OS, negativo = OD."""
    from transducin.revo_opt_reader import parse_octparams, parse_opt_chunks

    raw = path.read_bytes()
    chunks = parse_opt_chunks(raw)
    return parse_octparams(raw, chunks).get("scan_center_x_mm")


_cache: dict[tuple[str, int, int], list[Evidence]] = {}


def _max_read_bytes() -> int:
    try:
        return int(os.environ.get("TRANSDUCIN_SHADOW_MAX_READ_MB", DEFAULT_MAX_READ_MB)) * 1024 * 1024
    except ValueError:
        return DEFAULT_MAX_READ_MB * 1024 * 1024


def from_revo_file(path: Path) -> tuple[list[Evidence], list[str]]:
    """Revo: nombre de archivo (débil) + signo de OCTPARAMS tag 23 (geometría de adquisición)."""
    from transducin.opt_extractor import _parse_revo_filename

    ev: list[Evidence] = []
    unavailable: list[str] = []
    g = _parse_revo_filename(path.name)
    if g and g.get("lat"):
        ev.append(Evidence("filename:revo", "filename", normalize(g["lat"]), str(g["lat"])))
    try:
        st = path.stat()
    except OSError:
        return ev, ["file_unavailable"]
    if st.st_size > _max_read_bytes():
        return ev, ["file_too_large_for_shadow_read"]
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _cache:
        try:
            x = _read_revo_tag23(path)
            if x is None or x == 0.0:
                _cache[key] = []
            else:
                _cache[key] = [
                    Evidence(
                        "octparams:tag23", "acquisition_geometry", "L" if x > 0 else "R", "sign+" if x > 0 else "sign-"
                    )
                ]
        except Exception:
            _cache[key] = []
            unavailable.append("octparams_unreadable")
        if len(_cache) > 16:
            _cache.pop(next(iter(_cache)))
    return ev + _cache[key], unavailable


def collect(data: OCTClinicalData, route: str) -> tuple[list[Evidence], list[str]]:
    """Reúne la evidencia disponible para un estudio. Solo lectura; devuelve (evidencia, no_disponible)."""
    ev: list[Evidence] = []
    unavailable: list[str] = []
    src = Path(data.source_file) if data.source_file else None
    exists = bool(src and src.is_file())

    if route == "revo_opt":
        if exists:
            return from_revo_file(src)  # type: ignore[arg-type]
        unavailable.append("file_unavailable")
        if src is not None:
            ev += from_filename(src.name)
        return ev, unavailable

    if src is not None:
        ev += from_filename(src.name)
    if route == "cirrus_dcm":
        if exists:
            try:
                ev += from_dicom_dataset(_read_dicom_header(src))  # type: ignore[arg-type]
            except Exception:
                unavailable.append("dicom_unreadable")
        else:
            unavailable.append("file_unavailable")
    elif route in ("spectralis_e2e", "topcon_fda") and data.laterality:
        # El extractor ya leyó el metadato nativo; se registra su resultado (un solo dato original).
        ev.append(Evidence("native:extractor_output", "vendor_native", normalize(data.laterality), data.laterality))
    return ev, unavailable


# ── Evaluación ───────────────────────────────────────────────────────────────


def evaluate(evidence: list[Evidence], emitted: str, unavailable: list[str] | None = None) -> dict[str, Any]:
    """Clasifica el estado de la lateralidad y compara con el valor realmente emitido (``emitted``)."""
    usable = [e for e in evidence if e.value in ("R", "L")]  # "B" (ambos ojos) no es una afirmación lateral
    by_group: dict[str, set[str]] = {}
    for e in usable:
        by_group.setdefault(e.group, set()).add(e.value)  # type: ignore[arg-type]
    distinct = {v for vals in by_group.values() for v in vals}
    intra = sorted(g for g, vals in by_group.items() if len(vals) > 1)
    inter = len(distinct) > 1 and not intra
    conflict = bool(intra) or len(distinct) > 1

    strengths = {GROUP_STRENGTH.get(g, WEAK) for g in by_group}
    has_reference = REFERENCE in strengths
    strong_groups = [g for g in by_group if GROUP_STRENGTH.get(g, WEAK) in (STRONG, REFERENCE)]

    if not usable:
        state = "unknown"
    elif conflict:
        state = "conflict"
    elif has_reference:
        state = "confirmed"
    elif STRONG in strengths:
        state = "declared"
    else:
        state = "inferred"

    consensus = next(iter(distinct)) if len(distinct) == 1 else None
    if not emitted:
        emitted_vs = "emitted_empty"
    elif consensus is None:
        emitted_vs = "no_consensus"
    elif consensus == emitted:
        emitted_vs = "match"
    else:
        emitted_vs = "mismatch"

    return {
        "state": state,
        "n_groups": len(by_group),
        "n_strong_groups": len(strong_groups),
        "concordant_multi_group": bool(not conflict and len(by_group) >= 2),
        "concordant_independent": bool(not conflict and len(strong_groups) >= 2),
        "confirmed_by_reference": bool(has_reference and not conflict),
        "conflict": conflict,
        "conflict_kind": "intra_group" if intra else ("inter_group" if inter else ""),
        "conflict_groups": intra if intra else (sorted(by_group) if conflict else []),
        "emitted_vs_evidence": emitted_vs,
        "sources": [asdict(e) | {"strength": e.strength} for e in evidence],
        "unavailable": sorted(set(unavailable or [])),
    }
