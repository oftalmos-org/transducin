"""Registro en modo sombra de emisiones clínicas (SR y webhook a Graefe).

Propósito: cuantificar con datos reales cuántas emisiones caen en los riesgos R1–R6 (ver
``validation/matriz_brechas_2026-11-04.md``) **antes** de cambiar ninguna salida.

Garantías (cada una cubierta por pruebas):
  * No modifica ninguna medición, SR ni payload: solo lee ``OCTClinicalData``.
  * Nunca lanza excepciones hacia el pipeline (todo punto de enganche está envuelto).
  * Desactivado por defecto. Se activa solo con ``TRANSDUCIN_SHADOW_LOG_DIR`` **y** una clave HMAC.
  * Sin PHI ni payloads clínicos: no se guarda noel_id, nombre, fechas de nacimiento, nombres de archivo ni
    valores medidos. Los identificadores se correlacionan con HMAC-SHA256 (clave protegida, 0600).
  * Local y fuera de Git: se rechaza un directorio dentro de un árbol de trabajo Git.
  * Retención configurable (``TRANSDUCIN_SHADOW_RETENTION_DAYS``, 30 por defecto).
  * Sin red ni servicios externos.

Variables de entorno:
  TRANSDUCIN_SHADOW_LOG_DIR            directorio del log (si falta: desactivado)
  TRANSDUCIN_SHADOW_HMAC_KEY_FILE      archivo con la clave (hex o texto); permisos 0600
  TRANSDUCIN_SHADOW_HMAC_KEY           alternativa: clave en el entorno (menos recomendable)
  TRANSDUCIN_SHADOW_RETENTION_DAYS     días de retención (por defecto 30)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from transducin.clinical_data import OCTClinicalData

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
DEFAULT_RETENTION_DAYS = 30
_FILE_RE = re.compile(r"^shadow-(\d{8})\.jsonl$")

# Rutas cuyo ETDRS pasa por ``compute_etdrs`` legacy (medios anillos solapados S/N/I/T).
_LEGACY_OVERLAP_ROUTES = {"revo_opt", "spectralis_e2e", "topcon_fda"}
# Rutas cuyo centro es el centro geométrico del volumen (no fóvea).
_GEOMETRIC_CENTER_ROUTES = {"revo_opt", "spectralis_e2e", "topcon_fda"}


@dataclass(frozen=True)
class ShadowConfig:
    log_dir: Path
    key: bytes
    retention_days: int = DEFAULT_RETENTION_DAYS


# ── Configuración ────────────────────────────────────────────────────────────


def _inside_git_worktree(path: Path) -> bool:
    p = path.resolve()
    return any((q / ".git").exists() for q in (p, *p.parents))


def _is_windows() -> bool:
    return os.name == "nt"


def _read_key_file(path: Path) -> bytes | None:
    try:
        st = path.stat()
    except OSError:
        return None
    # En Windows los bits POSIX no reflejan la ACL (siempre parecen abiertos): allí la protección es la ACL del
    # archivo (ver deploy/shadow/INSTALL.md) y no se puede verificar desde aquí.
    if not _is_windows() and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning("shadow_log: clave HMAC con permisos demasiado abiertos; log desactivado")
        return None
    raw = path.read_bytes().strip()
    return raw or None


def load_config(env: dict[str, str] | None = None) -> ShadowConfig | None:
    """Devuelve la configuración o ``None`` si el log está desactivado o es inseguro."""
    env = os.environ if env is None else env
    raw_dir = env.get("TRANSDUCIN_SHADOW_LOG_DIR", "").strip()
    if not raw_dir:
        return None
    log_dir = Path(raw_dir).expanduser()
    if _inside_git_worktree(log_dir):
        logger.warning("shadow_log: el directorio está dentro de un repositorio Git; log desactivado")
        return None
    key: bytes | None = None
    key_file = env.get("TRANSDUCIN_SHADOW_HMAC_KEY_FILE", "").strip()
    if key_file:
        key = _read_key_file(Path(key_file).expanduser())
    elif env.get("TRANSDUCIN_SHADOW_HMAC_KEY", "").strip():
        key = env["TRANSDUCIN_SHADOW_HMAC_KEY"].strip().encode()
    if not key or len(key) < 16:
        logger.warning("shadow_log: falta clave HMAC válida (≥16 bytes); log desactivado")
        return None
    try:
        days = int(env.get("TRANSDUCIN_SHADOW_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    except ValueError:
        days = DEFAULT_RETENTION_DAYS
    return ShadowConfig(log_dir=log_dir, key=key, retention_days=max(1, days))


def pseudonym(key: bytes, value: str) -> str:
    """HMAC-SHA256 truncado (16 bytes hex) para correlación; vacío si no hay valor."""
    if not value:
        return ""
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


# ── Evaluación de riesgos (función pura) ─────────────────────────────────────


def classify_route(data: OCTClinicalData) -> str:
    vendor = (data.vendor or "").lower()
    src = (data.source_file or "").lower()
    if vendor == "optopol_revo":
        return "revo_opt"
    if vendor == "zeiss_cirrus":
        return "cirrus_pdf" if src.endswith(".pdf") else "cirrus_dcm"
    if vendor == "heidelberg_spectralis":
        return "spectralis_e2e"
    if vendor.startswith("topcon"):
        return "topcon_fda"
    return f"other:{vendor or 'unknown'}"


def laterality_source(data: OCTClinicalData, route: str) -> str:
    """Fuente de lateralidad inferida de las notas del extractor; ``unrecorded`` si no se registró."""
    notes = " ".join(data.confidence_notes or [])
    if not data.laterality:
        return "none"
    if "OCTPARAMS tag 23" in notes:
        return "octparams_tag23"
    if "laterality" in notes and "filename" in notes:
        return "filename"
    if route == "cirrus_dcm":
        return "dicom_tags_unrecorded_which"
    if route in {"spectralis_e2e", "topcon_fda"}:
        return "native_metadata"
    return "unrecorded"


def _has_etdrs(data: OCTClinicalData) -> bool:
    g = data.etdrs_grid
    return bool(g is not None and g.has_data())


def assess(data: OCTClinicalData, emission: str, lat_eval: dict[str, Any] | None = None) -> dict[str, Any]:
    """Calcula las banderas R1–R6 de una emisión. No modifica ``data`` ni usa red; no lee disco (la evidencia llega en ``lat_eval``)."""
    route = classify_route(data)
    lat = (data.laterality or "").strip()
    has_etdrs = _has_etdrs(data)
    has_cmt = data.cmt_um is not None
    has_meas = bool(data.has_measurements())

    from transducin.laterality_evidence import evaluate

    ev = lat_eval if lat_eval is not None else evaluate([], lat)
    if not lat:
        r1 = "defaulted"  # el SR asigna "R" (sr_builder) / lateralidad ausente en el webhook
    elif lat not in ("R", "L"):
        r1 = "other_value"
    elif ev["state"] == "conflict":
        r1 = "conflict"
    elif ev["state"] == "confirmed":
        r1 = "confirmed"
    elif ev["concordant_multi_group"]:
        r1 = "concordant_multi_source"
    elif ev["state"] == "declared":
        r1 = "unverified_single_source"
    elif ev["state"] == "inferred":
        r1 = "inferred_only"
    else:
        r1 = "no_evidence_available"

    if route in _GEOMETRIC_CENTER_ROUTES:
        center_origin = "geometric_volume_center"
    elif route == "cirrus_dcm":
        center_origin = "vendor_fovea_or_image_center_unrecorded"
    elif route == "cirrus_pdf":
        center_origin = "vendor_reported_in_report"
    else:
        center_origin = "unknown"

    if not has_cmt:
        r5 = "not_applicable"
    elif route in _GEOMETRIC_CENTER_ROUTES:
        r5 = "yes"
    elif route == "cirrus_dcm":
        r5 = "possible"
    else:
        r5 = "no"

    if route == "cirrus_pdf":
        value_origin = "vendor_original_ocr"
    elif route in {"revo_opt", "cirrus_dcm", "spectralis_e2e", "topcon_fda"}:
        value_origin = "reconstructed_by_transducin"
    else:
        value_origin = "unknown"

    if route in _LEGACY_OVERLAP_ROUTES:
        etdrs_impl = "legacy_compute_etdrs"
    elif route == "cirrus_dcm":
        etdrs_impl = "cirrus_etdrs_from_map"
    elif route == "cirrus_pdf":
        etdrs_impl = "vendor_report_ocr"
    else:
        etdrs_impl = "unknown"

    flags = {
        "R1_laterality": r1,
        "R2_overlap_sectors": bool(has_etdrs and route in _LEGACY_OVERLAP_ROUTES),
        "R3_cirrus_nasal_wedge": bool(has_etdrs and route == "cirrus_dcm"),
        "R4_orientation_unvalidated": bool(has_etdrs),
        "R5_geometric_center_as_foveal": r5,
        "R6_no_provenance_or_validation": bool(has_meas),  # procedencia/validación no se exportan en SR ni webhook
    }
    return {
        "route": route,
        "emission": emission,
        "vendor": data.vendor or "",
        "study_type": data.study_type or "",
        "laterality_value": lat,
        "laterality_source": laterality_source(data, route),
        "laterality_evidence": ev,
        "center_origin": center_origin,
        "value_origin": value_origin,
        "etdrs_impl": etdrs_impl,
        "orientation_validated": False,
        "has_cmt": has_cmt,
        "has_etdrs": has_etdrs,
        "has_measurements": has_meas,
        "provenance_internal": getattr(data, "measurement_provenance", "") or "",
        "extraction_confidence": data.extraction_confidence or "",
        "flags": flags,
    }


# ── Escritura ────────────────────────────────────────────────────────────────

_last_purge_day: str | None = None


def _purge(cfg: ShadowConfig, now: float) -> None:
    global _last_purge_day
    today = datetime.fromtimestamp(now, tz=UTC).strftime("%Y%m%d")
    if _last_purge_day == today:
        return
    _last_purge_day = today
    cutoff = now - cfg.retention_days * 86400
    for p in cfg.log_dir.iterdir():
        if _FILE_RE.match(p.name) and p.stat().st_mtime < cutoff:
            p.unlink()


def _disabled(cfg: ShadowConfig) -> bool:
    """Interruptor inmediato: variable ``TRANSDUCIN_SHADOW_DISABLE`` o archivo ``DISABLE`` en el directorio del log."""
    if os.environ.get("TRANSDUCIN_SHADOW_DISABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return (cfg.log_dir / "DISABLE").exists()


def record_emission(
    data: OCTClinicalData,
    emission: str,
    study_uid: str | None = None,
    *,
    config: ShadowConfig | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Registra una emisión. Devuelve el registro escrito, o ``None`` si está desactivado o falla.

    NUNCA lanza excepciones. Solo lee ``data``.
    """
    try:
        cfg = config if config is not None else load_config()
        if cfg is None:
            return None
        if _disabled(cfg):
            return None
        ts = time.time() if now is None else now
        from transducin.laterality_evidence import collect, evaluate

        route = classify_route(data)
        evidence, unavailable = collect(data, route)
        rec = assess(data, emission, evaluate(evidence, (data.laterality or "").strip(), unavailable))
        # Correlación seudónima. Sin study_uid, se usa una huella de campos del estudio (nunca el nombre de archivo en claro).
        ident = study_uid or f"{data.study_date}|{data.study_time}|{data.vendor}|{data.source_file}"
        rec.update(
            {
                "schema": SCHEMA_VERSION,
                "ts": datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="seconds"),
                "study_hmac": pseudonym(cfg.key, ident),
                "patient_hmac": pseudonym(cfg.key, data.noel_id),
                "transducin_version": _version(),
            }
        )
        cfg.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        _purge(cfg, ts)
        day = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y%m%d")
        path = cfg.log_dir / f"shadow-{day}.jsonl"
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return rec
    except Exception as exc:
        logger.debug("shadow_log: registro omitido (%s)", exc)
        return None


def _version() -> str:
    try:
        from transducin import __version__

        return str(__version__)
    except Exception:
        return ""


# ── Agregación (para el informe de frecuencias) ──────────────────────────────


def _bump(d: dict, key: object) -> None:
    d[str(key)] = d.get(str(key), 0) + 1


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Frecuencias agregadas por ruta y destino (``emission``) y por ruta y tipo de estudio. Solo cuentas."""
    out: dict[str, Any] = {"n_records": len(records), "by_route": {}, "by_route_study_type": {}, "unique_studies": {}}
    studies: dict[str, set[str]] = {}
    for r in records:
        studies.setdefault(r["route"], set()).add(r.get("study_hmac", ""))
        for key, section in (
            (f"{r['route']}|{r['emission']}", "by_route"),
            (f"{r['route']}|{r['study_type'] or 'sin_tipo'}", "by_route_study_type"),
        ):
            b = out[section].setdefault(
                key,
                {
                    "n": 0,
                    "flags": {},
                    "laterality_source": {},
                    "laterality_state": {},
                    "emitted_vs_evidence": {},
                    "evidence": {},
                },
            )
            b["n"] += 1
            _bump(b["laterality_source"], r["laterality_source"])
            for fname, val in r["flags"].items():
                _bump(b["flags"].setdefault(fname, {}), val)
            ev = r.get("laterality_evidence") or {}
            if ev:
                _bump(b["laterality_state"], ev.get("state"))
                _bump(b["emitted_vs_evidence"], ev.get("emitted_vs_evidence"))
                for k in ("concordant_multi_group", "concordant_independent", "confirmed_by_reference", "conflict"):
                    _bump(b["evidence"].setdefault(k, {}), bool(ev.get(k)))
                for u in ev.get("unavailable", []):
                    _bump(b["evidence"].setdefault("unavailable", {}), u)
                if ev.get("conflict"):
                    _bump(b["evidence"].setdefault("conflict_kind", {}), ev.get("conflict_kind"))
    out["unique_studies"] = {route: len(ids) for route, ids in studies.items()}
    return out


def read_records(log_dir: Path) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for p in sorted(log_dir.glob("shadow-*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs
