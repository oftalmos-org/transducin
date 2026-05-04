# transducin/clinical_summary.py
# SPDX-License-Identifier: Apache-2.0
#
# Generador de resumen clínico en texto libre (español) desde OCTClinicalData.
# Función pura, sin I/O. Usada por la API /store-summary y para logging.

from __future__ import annotations

import os

from transducin.clinical_data import OCTClinicalData

# Nombres legibles por vendor
_VENDOR_NAMES: dict[str, str] = {
    "optopol_revo":          "Optopol REVO FC130",
    "zeiss_cirrus":          "Zeiss Cirrus HD-OCT",
    "heidelberg_spectralis": "Heidelberg Spectralis",
    "topcon_fda":            "Topcon DRI OCT (FDA)",
    "topcon_maestro":        "Topcon Maestro",
    "unknown":               "equipo desconocido",
}

_LAT_NAMES: dict[str, str] = {
    "R":  "ojo derecho (OD)",
    "L":  "ojo izquierdo (OS)",
    "OD": "ojo derecho (OD)",
    "OS": "ojo izquierdo (OS)",
}

# Umbral SQI — leído en tiempo de llamada para respetar override en tests
def _sqi_warn_threshold() -> float:
    return float(os.environ.get("TRANSDUCIN_SQI_MIN_WARN", "6")) / 10.0

# CMT rango normal referencial (Cirrus normativa, µm)
_CMT_NORMAL_MIN = 229.0
_CMT_NORMAL_MAX = 289.0


def _fmt_date(yyyymmdd: str) -> str:
    """Convierte '20240315' → '15/03/2024'. Retorna original si no parseable."""
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[6:8]}/{yyyymmdd[4:6]}/{yyyymmdd[0:4]}"
    return yyyymmdd


def generate_clinical_summary(cd: OCTClinicalData) -> str:
    """Genera resumen clínico en español a partir de OCTClinicalData.

    Función pura: no hace I/O, no modifica cd.
    Incluye advertencia si SQI < TRANSDUCIN_SQI_MIN_WARN (default 6/10).

    Returns:
        str — texto multilinea listo para embedding / almacenamiento.
    """
    lines: list[str] = []

    vendor   = _VENDOR_NAMES.get(cd.vendor, cd.vendor)
    lat      = _LAT_NAMES.get(cd.laterality.upper(), cd.laterality)
    date_str = _fmt_date(cd.study_date)

    lines.append(f"Estudio OCT — {vendor}")
    lines.append(f"Paciente NOEL ID: {cd.noel_id}")
    lines.append(
        f"Fecha: {date_str} | Ojo: {lat} | Tipo: {cd.study_type or 'no especificado'}"
    )
    lines.append("")

    # ── Calidad de adquisición (SQI) ──────────────────────────────────────────
    if cd.sqi_mean is not None:
        sqi_10 = cd.sqi_mean * 10.0
        warn   = " [CALIDAD SUBÓPTIMA]" if cd.sqi_mean < _sqi_warn_threshold() else ""
        lines.append(f"Calidad de adquisición (SQI): {sqi_10:.1f}/10{warn}")

    # ── Grosor macular central (CMT) ──────────────────────────────────────────
    if cd.cmt_um is not None:
        if cd.cmt_um < _CMT_NORMAL_MIN:
            note = " (↓ por debajo del rango normal 229–289 µm)"
        elif cd.cmt_um > _CMT_NORMAL_MAX:
            note = " (↑ por encima del rango normal 229–289 µm)"
        else:
            note = " (dentro del rango normal 229–289 µm)"
        lines.append(f"Grosor macular central (CMT): {cd.cmt_um:.0f} µm{note}")

    # ── ETDRS 9 sectores ──────────────────────────────────────────────────────
    if cd.etdrs_grid is not None and cd.etdrs_grid.has_data():
        g = cd.etdrs_grid
        parts: list[str] = []
        for attr, label in [
            ("C", "C"), ("S1", "S1"), ("N1", "N1"), ("I1", "I1"), ("T1", "T1"),
            ("S2", "S2"), ("N2", "N2"), ("I2", "I2"), ("T2", "T2"),
        ]:
            val = getattr(g, attr, None)
            if val is not None:
                parts.append(f"{label}={val:.0f}")
        if parts:
            lines.append(f"ETDRS 9 sectores (µm): {' | '.join(parts)}")

    # ── RNFL ──────────────────────────────────────────────────────────────────
    if cd.rnfl is not None and cd.rnfl.has_data():
        r     = cd.rnfl
        parts = []
        if r.global_avg is not None:
            parts.append(f"global={r.global_avg:.0f}")
        if r.superior is not None:
            parts.append(f"S={r.superior:.0f}")
        if r.inferior is not None:
            parts.append(f"I={r.inferior:.0f}")
        if r.nasal is not None:
            parts.append(f"N={r.nasal:.0f}")
        if r.temporal is not None:
            parts.append(f"T={r.temporal:.0f}")
        lines.append(f"RNFL (µm): {' | '.join(parts)}")

    # ── mGCIPL ───────────────────────────────────────────────────────────────
    if cd.gcl_ipl is not None and cd.gcl_ipl.has_data():
        g     = cd.gcl_ipl
        parts = []
        if g.global_avg is not None:
            parts.append(f"global={g.global_avg:.0f}")
        if g.superior is not None:
            parts.append(f"S={g.superior:.0f}")
        if g.inferior is not None:
            parts.append(f"I={g.inferior:.0f}")
        if g.nasal is not None:
            parts.append(f"N={g.nasal:.0f}")
        if g.temporal is not None:
            parts.append(f"T={g.temporal:.0f}")
        lines.append(f"mGCIPL (µm): {' | '.join(parts)}")

    # ── Cabeza del nervio óptico (ONH / C/D) ─────────────────────────────────
    cdr_parts: list[str] = []
    if cd.cup_disc_ratio is not None:
        cdr_parts.append(f"C/D={cd.cup_disc_ratio:.2f}")
    if cd.vcdr is not None:
        cdr_parts.append(f"VCDR={cd.vcdr:.2f}")
    if cd.disc_area_mm2 is not None:
        cdr_parts.append(f"disco={cd.disc_area_mm2:.2f}mm²")
    if cd.rim_area_mm2 is not None:
        cdr_parts.append(f"anillo={cd.rim_area_mm2:.2f}mm²")
    if cdr_parts:
        lines.append(f"Cabeza del nervio óptico: {' | '.join(cdr_parts)}")

    # ── Biometría ─────────────────────────────────────────────────────────────
    bio_parts: list[str] = []
    if cd.axial_length_mm is not None:
        bio_parts.append(f"LA={cd.axial_length_mm:.2f}mm")
    if cd.cct_um is not None:
        bio_parts.append(f"CCT={cd.cct_um:.0f}µm")
    if cd.k1_mm is not None:
        bio_parts.append(f"K1={cd.k1_mm:.2f}mm")
    if cd.k2_mm is not None:
        bio_parts.append(f"K2={cd.k2_mm:.2f}mm")
    if bio_parts:
        lines.append(f"Biometría: {' | '.join(bio_parts)}")

    # Fallback si no hay mediciones
    _measurement_prefixes = (
        "Calidad", "Grosor", "ETDRS", "RNFL", "mGCIPL", "Cabeza", "Biometría"
    )
    if not any(ln.startswith(_measurement_prefixes) for ln in lines):
        lines.append("Sin mediciones disponibles.")

    return "\n".join(lines)


# ── Tests internos ────────────────────────────────────────────────────────────

def _run_tests() -> None:
    from transducin.clinical_data import ETDRSGrid, RNFLSectors

    errs = 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal errs
        status = "OK" if cond else "FAIL"
        if not cond:
            errs += 1
        print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))

    print("═══ clinical_summary tests ═══")

    # Test 1: resumen macular completo con SQI bajo
    cd = OCTClinicalData(
        noel_id="JAHJ19870831",
        vendor="zeiss_cirrus",
        study_date="20240315",
        laterality="R",
        study_type="macular",
        cmt_um=310.0,
        sqi_mean=0.45,  # 4.5/10 → bajo
        etdrs_grid=ETDRSGrid(C=310.0, S1=340.0, N1=325.0, I1=330.0, T1=305.0),
        rnfl=RNFLSectors(global_avg=95.0, superior=120.0, inferior=118.0),
    )
    summary = generate_clinical_summary(cd)
    check("vendor name", "Zeiss Cirrus" in summary)
    check("fecha formateada", "15/03/2024" in summary)
    check("ojo derecho", "ojo derecho" in summary)
    check("CMT presente", "310 µm" in summary)
    check("CMT sobre normal", "↑" in summary)
    check("SQI bajo warning", "CALIDAD SUBÓPTIMA" in summary)
    check("ETDRS presente", "ETDRS" in summary)
    check("RNFL presente", "RNFL" in summary)

    # Test 2: SQI normal (sin warning)
    cd2 = OCTClinicalData(
        noel_id="TEST0001",
        vendor="optopol_revo",
        study_date="20240101",
        laterality="L",
        study_type="optic_nerve",
        sqi_mean=0.85,  # 8.5/10 → OK
        vcdr=0.42,
        cup_disc_ratio=0.40,
    )
    summary2 = generate_clinical_summary(cd2)
    check("SQI OK sin warning", "CALIDAD SUBÓPTIMA" not in summary2)
    check("SQI 8.5/10", "8.5/10" in summary2)
    check("VCDR presente", "VCDR=0.42" in summary2)
    check("ojo izquierdo", "ojo izquierdo" in summary2)

    # Test 3: sin mediciones
    cd3 = OCTClinicalData(noel_id="GHOST0001", vendor="unknown", study_date="20240101", laterality="R")
    summary3 = generate_clinical_summary(cd3)
    check("fallback sin mediciones", "Sin mediciones disponibles" in summary3)

    # Test 4: fecha no estándar
    cd4 = OCTClinicalData(noel_id="X", vendor="unknown", study_date="NODATE", laterality="R")
    summary4 = generate_clinical_summary(cd4)
    check("fecha no parseable pasa sin excepción", "NODATE" in summary4)

    print(f"{'PASS' if errs == 0 else 'FAIL'} — {errs} error(es)\n")
    if errs:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_tests()
