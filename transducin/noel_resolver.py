# transducin/noel_resolver.py
# SPDX-License-Identifier: Apache-2.0
#
# 3-tier NOEL ID resolution for all OCT pipelines (Revo, Cirrus, etc.).
#
# Tier 1: SOCT.db lookup by patient name (± DOB)
# Tier 2: Filename cross-reference (NOEL-prefixed sibling files)
# Tier 3: PatientName as PatientID (last resort, WARNING logged)
#
# Usage:
#   from transducin.noel_resolver import resolve_noel_id
#
#   noel_id = resolve_noel_id(
#       patient_name="JAURRIETA HINOJOS^JESUS NOEL",
#       patient_dob="19870831",                       # optional
#       filename_index=build_noel_index(watch_dir),   # optional Tier 2
#   )

from __future__ import annotations

import logging
import os
import sqlite3
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

from transducin.noel_id import is_valid_noel

logger = logging.getLogger("transducin.noel_resolver")

# ── Configuration ─────────────────────────────────────────────────────────────

SOCT_DB_PATH = Path(os.environ.get("SOCT_DB_PATH", r"C:\SOCT_DATA\SOCT.db"))

# Julian Day Number offset for date.fromordinal()
_JDN_OFFSET = 1721425


# ── Text normalization ────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalize text for comparison: strip accents, casefold, underscores→spaces."""
    if not text:
        return ""
    text = text.replace("_", " ")
    # NFC first to compose characters, then NFKD to decompose for accent stripping
    text = unicodedata.normalize("NFC", text)
    text = unicodedata.normalize("NFKD", text)
    # Remove combining marks (accents)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.casefold().strip()


def _jdn_to_date(jdn: int) -> Optional[date]:
    """Convert Julian Day Number (as stored in SOCT.db) to a Python date."""
    try:
        return date.fromordinal(int(jdn) - _JDN_OFFSET)
    except (ValueError, OverflowError):
        return None


def _parse_dob(dob_str: str) -> Optional[date]:
    """Parse DOB from common formats: YYYYMMDD, YYYY-MM-DD, DD/MM/YYYY."""
    if not dob_str:
        return None
    dob_str = dob_str.strip()
    try:
        if len(dob_str) == 8 and dob_str.isdigit():
            return date(int(dob_str[:4]), int(dob_str[4:6]), int(dob_str[6:8]))
        if "-" in dob_str:
            parts = dob_str.split("-")
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        if "/" in dob_str:
            parts = dob_str.split("/")
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except (ValueError, IndexError):
        pass
    return None


# ── Tier 1: SOCT.db lookup ───────────────────────────────────────────────────

def _split_patient_name(patient_name: str) -> tuple[str, str]:
    """Split PatientName into (lname, fname).

    Accepts:
        "APELLIDOS^NOMBRES"          (DICOM)
        "APELLIDOS_NOMBRES"          (Revo filename)
        "APELLIDOS NOMBRES"          (free text — ambiguous, best effort)
    """
    if "^" in patient_name:
        parts = patient_name.split("^", 1)
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""
    if "_" in patient_name:
        parts = patient_name.split("_", 1)
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""
    # Free text — can't reliably split, use whole string as lname
    return patient_name.strip(), ""


def _lookup_soct_db(
    patient_name: str,
    patient_dob: Optional[str] = None,
) -> Optional[str]:
    """Query SOCT.db for NOEL ID by patient name (± DOB).

    Returns the `ref` field if a unique match is found, None otherwise.
    """
    if not SOCT_DB_PATH.is_file():
        logger.debug("SOCT.db not found at %s — skipping Tier 1", SOCT_DB_PATH)
        return None

    lname_input, fname_input = _split_patient_name(patient_name)
    lname_norm = _normalize(lname_input)
    fname_norm = _normalize(fname_input)

    if not lname_norm:
        return None

    dob_date = _parse_dob(patient_dob) if patient_dob else None

    try:
        conn = sqlite3.connect(str(SOCT_DB_PATH), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent reads
        cur = conn.cursor()
        cur.execute("SELECT ref, fname, lname, dob FROM patients WHERE ref IS NOT NULL AND ref != ''")
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error as e:
        logger.warning("SOCT.db query failed: %s", e)
        return None

    # Strategy 1: exact lname + fname match
    candidates = []
    for ref, db_fname, db_lname, db_dob_jdn in rows:
        if _normalize(db_lname) != lname_norm:
            continue
        # fname match: if input has fname, require match; if not, accept any
        if fname_norm and _normalize(db_fname) != fname_norm:
            continue
        candidates.append((ref, db_fname, db_lname, db_dob_jdn))

    # Strategy 2: if no exact match, compare all name tokens regardless of split.
    # Handles ambiguous lname/fname boundary (e.g. filename "SYNTHETIC_RIOS_EMMA^LORENA"
    # vs SOCT.db lname="SYNTHETIC RIOS" fname="EMMA LORENA").
    if not candidates:
        input_full = _normalize(patient_name.replace("^", " "))
        input_tokens = set(input_full.split())
        if len(input_tokens) >= 2:
            for ref, db_fname, db_lname, db_dob_jdn in rows:
                db_tokens = set((_normalize(db_lname) + " " + _normalize(db_fname)).split())
                if input_tokens == db_tokens:
                    candidates.append((ref, db_fname, db_lname, db_dob_jdn))

    if not candidates:
        logger.debug("Tier 1: no match in SOCT.db for '%s'", patient_name)
        return None

    if len(candidates) == 1:
        ref = candidates[0][0]
        logger.info("Tier 1: SOCT.db match — '%s' → %s", patient_name, ref)
        return ref

    # Multiple matches — try to disambiguate by DOB
    if dob_date:
        dob_matches = [
            c for c in candidates
            if c[3] and _jdn_to_date(c[3]) == dob_date
        ]
        if len(dob_matches) == 1:
            ref = dob_matches[0][0]
            logger.info("Tier 1: SOCT.db match (disambiguated by DOB) — '%s' → %s", patient_name, ref)
            return ref

    refs = [c[0] for c in candidates]
    logger.warning("Tier 1: %d matches in SOCT.db for '%s': %s — cannot disambiguate",
                    len(candidates), patient_name, refs)
    return None


# ── Tier 2: Filename cross-reference ─────────────────────────────────────────
# The caller provides a pre-built {patient_name_key → noel_id} index
# (from opt_extractor.build_noel_index).

def _lookup_filename_index(
    patient_name: str,
    filename_index: Optional[dict[str, str]],
) -> Optional[str]:
    """Look up NOEL ID from sibling filenames (Tier 2)."""
    if not filename_index:
        return None

    lname, fname = _split_patient_name(patient_name)
    # The index uses "APELLIDOS_NOMBRES" uppercase keys
    name_key = f"{lname}_{fname}".upper() if fname else lname.upper()
    resolved = filename_index.get(name_key)
    if resolved:
        logger.info("Tier 2: filename cross-ref — '%s' → %s", patient_name, resolved)
    return resolved


# ── Tier 3: PatientName as PatientID ─────────────────────────────────────────

def _fallback_name_as_id(patient_name: str) -> str:
    """Tier 3: compute 4-letter NOEL initials when no DOB is available.

    Returns the NOEL algorithm's name-prefix only (paterno[:2] + materno[0] +
    nombre[0]) — without the date suffix. Example: "GARCIA LOPEZ^ANA MARIA"
    → "GALA". These are NOT valid NOEL IDs (not unique without DOB) and the
    warning must always be visible so the patient can be properly registered.

    Falls back to "UNKNOWN" only if the name is empty or too short to produce
    initials.
    """
    from transducin.noel_id import noel_initials
    initials = noel_initials(patient_name)
    pid = initials or "UNKNOWN"
    logger.warning(
        "Tier 3: no NOEL ID for '%s' — using initials as PatientID: %s "
        "(NOT a real NOEL; patient missing from SOCT.db)",
        patient_name, pid,
    )
    return pid


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_noel_id(
    patient_name: str,
    patient_dob: Optional[str] = None,
    filename_index: Optional[dict[str, str]] = None,
) -> str:
    """Resolve NOEL ID for a patient using 3-tier lookup.

    Tier 1: SOCT.db (authoritative, works for all devices)
    Tier 2: Filename cross-reference (NOEL-prefixed sibling .OPT files)
    Tier 3: PatientName as PatientID (WARNING logged, never blocks pipeline)

    Args:
        patient_name: "APELLIDOS^NOMBRES" (DICOM) or "APELLIDOS_NOMBRES" (filename)
        patient_dob:  "YYYYMMDD" or "YYYY-MM-DD" (optional, helps disambiguate)
        filename_index: {name_key → noel_id} from build_noel_index() (optional)

    Returns:
        NOEL ID string (always non-empty — Tier 3 guarantees a value).
    """
    if not patient_name or patient_name.upper() in ("UNKNOWN", "UNKNOWN^UNKNOWN"):
        logger.warning("Tier 3: empty/unknown patient name — PatientID=UNKNOWN")
        return "UNKNOWN"

    # Tier 1: SOCT.db
    noel = _lookup_soct_db(patient_name, patient_dob)
    if noel and is_valid_noel(noel):
        return noel

    # Tier 2: filename cross-reference
    noel = _lookup_filename_index(patient_name, filename_index)
    if noel and is_valid_noel(noel):
        return noel

    # Tier 3: PatientName as PatientID
    return _fallback_name_as_id(patient_name)


def resolve_patient_demographics(
    patient_name: str,
    patient_dob: Optional[str] = None,
    filename_index: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Resolve NOEL ID + demographics from SOCT.db in one call.

    Returns:
        dict with keys: noel_id, patient_dob (YYYYMMDD), patient_sex ("M"/"F"/""),
        patient_name (DICOM PN format from DB if available).
    """
    result = {
        "noel_id": "",
        "patient_dob": patient_dob or "",
        "patient_sex": "",
        "patient_name": patient_name or "",
    }

    if not patient_name or patient_name.upper() in ("UNKNOWN", "UNKNOWN^UNKNOWN"):
        result["noel_id"] = "UNKNOWN"
        return result

    # Try SOCT.db for full demographics
    if SOCT_DB_PATH.is_file():
        lname_input, fname_input = _split_patient_name(patient_name)
        lname_norm = _normalize(lname_input)
        fname_norm = _normalize(fname_input)

        if lname_norm:
            try:
                conn = sqlite3.connect(str(SOCT_DB_PATH), timeout=5)
                conn.execute("PRAGMA journal_mode=WAL")
                cur = conn.cursor()
                cur.execute("SELECT ref, fname, lname, dob, gender FROM patients WHERE ref IS NOT NULL AND ref != ''")
                rows = cur.fetchall()
                conn.close()
            except sqlite3.Error:
                rows = []

            # Strategy 1: exact match
            candidates = []
            for ref, db_fname, db_lname, db_dob_jdn, db_gender in rows:
                if _normalize(db_lname) != lname_norm:
                    continue
                if fname_norm and _normalize(db_fname) != fname_norm:
                    continue
                candidates.append((ref, db_fname, db_lname, db_dob_jdn, db_gender))

            # Strategy 2: token-set match
            if not candidates:
                input_full = _normalize(patient_name.replace("^", " "))
                input_tokens = set(input_full.split())
                if len(input_tokens) >= 2:
                    for ref, db_fname, db_lname, db_dob_jdn, db_gender in rows:
                        db_tokens = set((_normalize(db_lname) + " " + _normalize(db_fname)).split())
                        if input_tokens == db_tokens:
                            candidates.append((ref, db_fname, db_lname, db_dob_jdn, db_gender))

            # DOB disambiguation if needed
            dob_date = _parse_dob(patient_dob) if patient_dob else None
            if len(candidates) > 1 and dob_date:
                dob_matches = [c for c in candidates if c[3] and _jdn_to_date(c[3]) == dob_date]
                if len(dob_matches) == 1:
                    candidates = dob_matches

            if len(candidates) == 1:
                ref, db_fname, db_lname, db_dob_jdn, db_gender = candidates[0]
                result["noel_id"] = ref
                if db_dob_jdn:
                    d = _jdn_to_date(db_dob_jdn)
                    if d:
                        result["patient_dob"] = d.strftime("%Y%m%d")
                result["patient_sex"] = {1: "M", 2: "F"}.get(db_gender, "")
                result["patient_name"] = f"{db_lname}^{db_fname}"
                logger.info("Demographics: '%s' → %s DOB=%s sex=%s",
                            patient_name, ref, result["patient_dob"], result["patient_sex"])
                return result

    # Fall through to resolve_noel_id for Tier 2/3
    result["noel_id"] = resolve_noel_id(patient_name, patient_dob, filename_index)
    return result


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s",
                        stream=sys.stdout)

    print("=" * 60)
    print("noel_resolver.py — self-test")
    print(f"SOCT_DB_PATH: {SOCT_DB_PATH} (exists={SOCT_DB_PATH.is_file()})")
    print("=" * 60)

    errors = 0

    def check(label, got, expected):
        global errors
        ok = got == expected
        mark = "\u2713" if ok else "\u2717"
        print(f"  {mark} {label}: {got!r} {'==' if ok else '!='} {expected!r}")
        if not ok:
            errors += 1

    # Tier 1: SOCT.db lookups (require DB present)
    if SOCT_DB_PATH.is_file():
        print("\n-- Tier 1: SOCT.db --")
        check("DICOM format",
              resolve_noel_id("JAURRIETA HINOJOS^JESUS NOEL"),
              "JAHJ19870831")
        check("Revo filename format",
              resolve_noel_id("SYNTHETIC_PATIENT"),
              "SYPA19800101")
        check("With accents (synthetic)",
              resolve_noel_id("GARCIA LOPEZ^ANA MARIA"),
              "GALA19671225")
        check("With DOB disambiguation",
              resolve_noel_id("MARTINEZ REYES^JUAN", patient_dob="19800101"),
              "MAJE19800101")
        check("3-letter NOEL",
              resolve_noel_id("PEREZ GOMEZ^LUIS"),
              "PEGL20000101")
    else:
        print("\n  SOCT.db not found — skipping Tier 1 tests")

    # Tier 3: fallback
    print("\n-- Tier 3: fallback --")
    check("Unknown name",
          resolve_noel_id("UNKNOWN^UNKNOWN"),
          "UNKNOWN")
    check("Name as ID",
          resolve_noel_id("GARCIA LOPEZ^MARIA"),
          "GARCIA_LOPEZ_MARIA")

    print(f"\n{'ALL TESTS PASSED' if errors == 0 else f'{errors} TESTS FAILED'}")
    sys.exit(0 if errors == 0 else 1)
