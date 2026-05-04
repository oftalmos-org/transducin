# transducin/noel_id.py
# SPDX-License-Identifier: Apache-2.0
#
# Módulo de normalización del Protocolo NOEL para PatientID en DICOM.
#
# Formato NOEL: apellido_paterno[:2] + apellido_materno[0] + nombre[0] + YYYYMMDD
# Ejemplo: JESUS NOEL JAURRIETA HINOJOS, 1987-08-31 → JAHJ19870831
#   JA = primeras 2 letras de JAURRIETA (apellido paterno)
#   H  = primera letra de HINOJOS (apellido materno)
#   J  = primera letra de JESUS (nombre)
#
# Reglas:
#   - Normalizar unicode (tildes, ñ → sin acento)
#   - Mayúsculas siempre
#   - Nombres compuestos: usar primera palabra de cada componente
#   - Si PatientID YA es formato NOEL (3-4 letras + 8 dígitos), preservarlo
#   - Si no, intentar construir desde PatientName + DOB
#   - Si imposible, retornar ID existente con flag de advertencia en log

from __future__ import annotations

import re
import unicodedata
import logging

logger = logging.getLogger(__name__)

_EXTRA_MAP = str.maketrans({"Ñ": "N", "ñ": "N", "Ü": "U", "ü": "U"})
_NOEL_PATTERN = re.compile(r"^[A-Z]{3,4}\d{8}$")


def _normalize_str(text: str) -> str:
    """Elimina tildes, convierte a mayúsculas, retira no-ASCII."""
    text = text.translate(_EXTRA_MAP)
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ASCII", "ignore").decode("ASCII").upper().strip()


def is_valid_noel(patient_id: str) -> bool:
    """Verifica si un PatientID cumple el formato NOEL (3-4 letras + 8 dígitos)."""
    if not patient_id:
        return False
    return bool(_NOEL_PATTERN.match(patient_id.strip()))


def dob_from_noel(noel_id: str) -> str:
    """Extrae la fecha de nacimiento (YYYYMMDD) de los últimos 8 dígitos del NOEL ID.

    El protocolo NOEL garantiza que los últimos 8 caracteres son siempre YYYYMMDD.
    Ejemplo: JAHJ19870831 → '19870831'

    Returns:
        YYYYMMDD si el ID es NOEL válido, '' en caso contrario.
    """
    if is_valid_noel(noel_id):
        return noel_id.strip()[-8:]
    return ""


def noel_initials(name: str) -> str:
    """Calcula el prefijo de 4 letras del NOEL ID: paterno[:2] + materno[0] + nombre[0].

    Útil como fallback Tier 3 cuando no se dispone de fecha de nacimiento.
    NO produce un NOEL ID válido — el ID completo requiere DOB apendida.

    Args:
        name: Nombre completo. Formatos aceptados (igual que build_noel_id):
              "JESUS NOEL JAURRIETA HINOJOS"         (libre)
              "JAURRIETA HINOJOS^JESUS NOEL"          (DICOM: apellidos^nombres)
              "JAURRIETA HINOJOS_JESUS NOEL"          (Revo filename)

    Returns:
        4-char initials (ej. "JAHJ"), o "" si el nombre es insuficiente.
    """
    if not name:
        return ""

    n = _normalize_str(name)

    if "^" in n:
        apellidos_str, nombres_str = n.split("^", 1)
    elif "_" in n:
        apellidos_str, nombres_str = n.split("_", 1)
    else:
        tokens = n.split()
        if len(tokens) >= 4:
            mid = len(tokens) // 2
            nombres_str   = " ".join(tokens[:mid])
            apellidos_str = " ".join(tokens[mid:])
        elif len(tokens) == 3:
            nombres_str   = tokens[0]
            apellidos_str = " ".join(tokens[1:])
        elif len(tokens) == 2:
            nombres_str   = tokens[0]
            apellidos_str = tokens[1]
        else:
            return ""

    apellidos = apellidos_str.split()
    nombres   = nombres_str.split()

    pat = apellidos[0] if apellidos else ""
    mat = apellidos[1] if len(apellidos) > 1 else ""
    nom = nombres[0]   if nombres   else ""

    if len(pat) < 2 or not nom:
        return ""

    return pat[:2] + (mat[0] if mat else (pat[2] if len(pat) > 2 else "X")) + nom[0]


def build_noel_id(name: str, dob: str) -> str:
    """Construye un PatientID en formato NOEL.

    Algoritmo: apellido_paterno[:2] + apellido_materno[0] + nombre[0] + YYYYMMDD

    Args:
        name: Nombre completo. Formatos aceptados:
              "JESUS NOEL JAURRIETA HINOJOS"         (libre: nombre(s) apellido(s))
              "JAURRIETA HINOJOS^JESUS NOEL"          (DICOM: apellidos^nombres)
              "JAURRIETA HINOJOS_JESUS NOEL"          (Revo filename: apellidos_nombres)
        dob:  Fecha de nacimiento: "YYYYMMDD", "YYYY-MM-DD", "DD/MM/YYYY"

    Returns:
        NOEL ID de 12 caracteres, ej. "JAHJ19870831"

    Raises:
        ValueError: si nombre o fecha son insuficientes.
    """
    if not name or not dob:
        raise ValueError("Nombre y fecha requeridos para construir NOEL ID.")

    # --- Normalizar fecha → YYYYMMDD ---
    dob_digits = re.sub(r"[^\d]", "", dob)
    if len(dob_digits) != 8:
        raise ValueError(f"Fecha no parseable: '{dob}'")
    candidate_year = int(dob_digits[:4])
    if 1900 <= candidate_year <= 2100:
        dob_norm = dob_digits
    else:
        # DDMMYYYY → YYYYMMDD
        dob_norm = dob_digits[4:8] + dob_digits[2:4] + dob_digits[0:2]
    if not (1900 <= int(dob_norm[:4]) <= 2100):
        raise ValueError(f"Año inválido: {dob_norm[:4]}")

    code = noel_initials(name)
    if not code:
        raise ValueError(f"Nombre insuficiente para NOEL: '{name}'")
    return code + dob_norm


def normalize_noel_id(
    patient_id: str,
    patient_name: str = "",
    patient_dob: str = "",
) -> tuple[str, str]:
    """Valida o construye PatientID en formato NOEL.

    Returns:
        (noel_id, status) donde status ∈ {"preserved", "built", "warn"}
    """
    pid = (patient_id or "").strip()

    if is_valid_noel(pid):
        logger.info("PatientID normalizado: %s (preservado — ya era formato NOEL)", pid)
        return pid, "preserved"

    if patient_name and patient_dob:
        try:
            noel = build_noel_id(patient_name, patient_dob)
            logger.info("PatientID normalizado: %s (construido desde nombre '%s')", noel, patient_name)
            return noel, "built"
        except ValueError as e:
            logger.warning("No se pudo construir NOEL ID: %s", e)

    fallback = pid if pid else "UNKNOWN"
    logger.warning("PatientID NO normalizado: usando '%s'", fallback)
    return fallback, "warn"


# ─────────────────────────────────────────────────────────────────────────────
# TESTS  —  python transducin/noel_id.py
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    G, R, E = "\033[92m", "\033[91m", "\033[0m"
    errors = 0

    def check(label, got, expected):
        global errors
        ok = got == expected
        print(f"  {'✓' if ok else '✗'} [{G+'PASS'+E if ok else R+'FAIL'+E}] {label}")
        if not ok:
            print(f"       got:      {got!r}")
            print(f"       expected: {expected!r}")
            errors += 1

    print("\n══ is_valid_noel ══")
    check("JAHJ19870831 válido",       is_valid_noel("JAHJ19870831"), True)
    check("minúsculas → False",        is_valid_noel("jahj19870831"), False)
    check("vacío → False",             is_valid_noel(""), False)
    check("3 letras + 8 → True",       is_valid_noel("JAH19870831"), True)
    check("5 letras + 8 → False",      is_valid_noel("JAHJX19870831"), False)
    check("4 letras + 7 → False",      is_valid_noel("JAHJ1987083"), False)
    check("espacios → False",          is_valid_noel("JAH J19870831"), False)

    print("\n══ build_noel_id — algoritmo NOEL correcto ══")
    # Caso canónico del protocolo
    check(
        "JESUS NOEL JAURRIETA HINOJOS libre 4 tokens",
        build_noel_id("JESUS NOEL JAURRIETA HINOJOS", "1987-08-31"),
        "JAHJ19870831",
    )
    # Formato DICOM apellidos^nombres
    check(
        "JAURRIETA HINOJOS^JESUS NOEL DICOM",
        build_noel_id("JAURRIETA HINOJOS^JESUS NOEL", "19870831"),
        "JAHJ19870831",
    )
    # Formato Revo filename: apellidos_nombres
    check(
        "JAURRIETA HINOJOS_JESUS NOEL Revo",
        build_noel_id("JAURRIETA HINOJOS_JESUS NOEL", "19870831"),
        "JAHJ19870831",
    )
    # Solo dos tokens: nombre apellido_paterno (sin materno → usa 3ra letra paterno como fallback)
    check(
        "MARIA GARCIA — sin materno",
        build_noel_id("MARIA GARCIA", "19901010"),
        "GARM19901010",  # GA + R(arcia[2]) + M(aria)
    )
    # Solo apellido paterno de 2 letras (sin materno, sin 3ra letra)
    check(
        "ANA LI — apellido paterno corto sin materno → X",
        build_noel_id("ANA LI", "20000101"),
        "LIXA20000101",  # LI + X(fallback) + A(na)
    )
    # Tildes y ñ normalizadas — libre 2 tokens:
    # nombres="SOFIA", apellidos="NUNEZ"
    # pat[:2]="NU", mat="" → fallback pat[2]="N", nom[0]="S" → NUNS
    check(
        "SOFIA NUNEZ 2 tokens",
        build_noel_id("SOFÍA NÚÑEZ", "20000101"),
        "NUNS20000101",
    )
    # Fecha con separadores
    check(
        "fecha DD/MM/YYYY",
        build_noel_id("JAURRIETA HINOJOS_JESUS NOEL", "31/08/1987"),
        "JAHJ19870831",
    )

    print("\n══ normalize_noel_id ══")
    noel, status = normalize_noel_id("JAHJ19870831")
    check("preservar NOEL válido",   noel,   "JAHJ19870831")
    check("status preserved",        status, "preserved")

    noel, status = normalize_noel_id("OLD-ID", "JAURRIETA HINOJOS_JESUS NOEL", "19870831")
    check("construir desde nombre",  noel,   "JAHJ19870831")
    check("status built",            status, "built")

    noel, status = normalize_noel_id("MRN-007")
    check("fallback sin datos",      noel,   "MRN-007")
    check("status warn",             status, "warn")

    noel, status = normalize_noel_id("", "", "")
    check("vacío → UNKNOWN",         noel,   "UNKNOWN")

    print(f"\n{'══ TODOS LOS TESTS PASARON ══' if errors==0 else f'══ {errors} FALLARON ══'}\n")
    sys.exit(0 if errors == 0 else 1)
