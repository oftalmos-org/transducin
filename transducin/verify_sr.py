# transducin/verify_sr.py
# SPDX-License-Identifier: Apache-2.0
#
# Validador de DICOM SR TID 1500 generado por sr_builder.py.
# Verifica estructura, PatientID NOEL, códigos SNOMED-CT
# y confirma llegada a Orthanc via REST API.
#
# Uso:
#   python transducin/verify_sr.py <path_al_sr.dcm> [--orthanc http://host:8042]

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import pydicom
from pydicom.dataset import Dataset

from transducin.noel_id import is_valid_noel

ORTHANC_URL_DEFAULT = "http://localhost:8042"

# SOPClassUID válidos para Comprehensive SR
_SR_SOP_CLASSES = {
    "1.2.840.10008.5.1.4.1.1.88.11": "BasicTextSR",
    "1.2.840.10008.5.1.4.1.1.88.22": "EnhancedSR",
    "1.2.840.10008.5.1.4.1.1.88.33": "ComprehensiveSR",
    "1.2.840.10008.5.1.4.1.1.88.34": "Comprehensive3DSR",
}

# Códigos SNOMED-CT esperados en TID 1500 — todos opcionales (WARN si ausentes)
_EXPECTED_SNOMED = {
    "422453003": "Foveal retinal thickness (CMT)",
    "422399008": "Macular retinal thickness (ETDRS)",
    "422995006": "Retinal nerve fiber layer thickness (RNFL)",
    "363932005": "Cup to disc ratio",
    "252017007": "Axial length of eye",
    "397545004": "Corneal thickness measurement (CCT)",
    "252014009": "Flat corneal meridian curvature (K1)",
    "252016006": "Steep corneal meridian curvature (K2)",
    "422455005": "Macular ganglion cell layer thickness (mGCIPL)",
}

_CHECK_ICON = {"PASS": "\033[92m✓ PASS\033[0m", "FAIL": "\033[91m✗ FAIL\033[0m", "WARN": "\033[93m⚠ WARN\033[0m"}


class CheckResult:
    def __init__(self, name: str, status: str, detail: str = ""):
        self.name   = name
        self.status = status  # "PASS" | "FAIL" | "WARN"
        self.detail = detail

    def __str__(self) -> str:
        icon = _CHECK_ICON.get(self.status, self.status)
        return f"  {icon}  {self.name}" + (f": {self.detail}" if self.detail else "")


def _find_codes_in_content(ds: Dataset, found: Optional[set] = None) -> set[str]:
    """Busca recursivamente CodeValue en ContentSequence."""
    if found is None:
        found = set()
    for item in getattr(ds, "ContentSequence", []):
        for seq_tag in ["ConceptNameCodeSequence", "ConceptCodeSequence"]:
            for code_item in getattr(item, seq_tag, []):
                cv = getattr(code_item, "CodeValue", None)
                if cv:
                    found.add(str(cv))
        _find_codes_in_content(item, found)
    return found


def _orthanc_query(orthanc_url: str, sop_instance_uid: str) -> Optional[dict]:
    """Busca un SOPInstanceUID en Orthanc via REST API."""
    try:
        url = f"{orthanc_url.rstrip('/')}/tools/lookup"
        data = json.dumps(sop_instance_uid).encode()
        req  = urllib.request.Request(url, data=data,
                                       headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            return result if result else None
    except urllib.error.URLError:
        return None
    except Exception:
        return None


def verify_sr(dcm_path: str | Path, orthanc_url: str = ORTHANC_URL_DEFAULT) -> list[CheckResult]:
    """Verifica un SR .dcm generado por sr_builder.

    Args:
        dcm_path:    Path al archivo .dcm SR.
        orthanc_url: URL base de Orthanc (ej. http://localhost:8042).

    Returns:
        Lista de CheckResult con estado PASS/FAIL/WARN por cada verificación.
    """
    results: list[CheckResult] = []
    dcm_path = Path(dcm_path)

    # ── 1. Archivo existe y es legible ──────────────────────────────────────
    if not dcm_path.exists():
        results.append(CheckResult("Archivo existe", "FAIL", str(dcm_path)))
        return results
    results.append(CheckResult("Archivo existe", "PASS", str(dcm_path.name)))

    try:
        ds = pydicom.dcmread(str(dcm_path))
        results.append(CheckResult("Legible con pydicom", "PASS"))
    except Exception as e:
        results.append(CheckResult("Legible con pydicom", "FAIL", str(e)))
        return results

    # ── 2. SOPClassUID es un SR válido ─────────────────────────────────────
    sop_class = str(getattr(ds, "SOPClassUID", ""))
    sr_name   = _SR_SOP_CLASSES.get(sop_class)
    if sr_name:
        results.append(CheckResult("SOPClassUID es SR", "PASS", sr_name))
    else:
        results.append(CheckResult("SOPClassUID es SR", "FAIL", f"got: {sop_class}"))

    # ── 3. Tags obligatorios TID 1500 ──────────────────────────────────────
    required_tags = [
        ("PatientID",         "PatientID presente"),
        ("StudyDate",         "StudyDate presente"),
        ("ContentDate",       "ContentDate presente"),
        ("ContentTime",       "ContentTime presente"),
        ("SOPInstanceUID",    "SOPInstanceUID presente"),
        ("StudyInstanceUID",  "StudyInstanceUID presente"),
        ("ContentSequence",   "ContentSequence presente"),
    ]
    for tag_name, label in required_tags:
        val = getattr(ds, tag_name, None)
        if val is not None and str(val).strip():
            results.append(CheckResult(label, "PASS"))
        else:
            results.append(CheckResult(label, "FAIL"))

    # ── 4. PatientID formato NOEL ───────────────────────────────────────────
    pid = str(getattr(ds, "PatientID", ""))
    if is_valid_noel(pid):
        results.append(CheckResult("PatientID formato NOEL", "PASS", pid))
    else:
        results.append(CheckResult("PatientID formato NOEL", "FAIL",
                                   f"'{pid}' no cumple formato XXXX99999999"))

    # ── 5. Códigos SNOMED-CT en ContentSequence ─────────────────────────────
    found_codes = _find_codes_in_content(ds)
    has_any_snomed = False
    for code, meaning in _EXPECTED_SNOMED.items():
        if code in found_codes:
            results.append(CheckResult(f"SNOMED {code}", "PASS", meaning))
            has_any_snomed = True
        else:
            results.append(CheckResult(f"SNOMED {code}", "WARN",
                                       f"{meaning} — no presente (puede ser normal si no aplica)"))

    if not has_any_snomed:
        results.append(CheckResult("Al menos 1 código SNOMED-CT", "FAIL",
                                   "ContentSequence sin códigos SNOMED esperados"))
    else:
        results.append(CheckResult("Al menos 1 código SNOMED-CT", "PASS"))

    # ── 6. Consulta Orthanc ─────────────────────────────────────────────────
    sop_uid = str(getattr(ds, "SOPInstanceUID", ""))
    if sop_uid:
        orthanc_result = _orthanc_query(orthanc_url, sop_uid)
        if orthanc_result:
            results.append(CheckResult("Encontrado en Orthanc", "PASS",
                                       f"SOPInstanceUID={sop_uid[:30]}..."))
        else:
            results.append(CheckResult("Encontrado en Orthanc", "WARN",
                                       "No encontrado o Orthanc no accesible"))
    else:
        results.append(CheckResult("Consulta Orthanc", "FAIL", "SOPInstanceUID vacío"))

    return results


def print_report(results: list[CheckResult], dcm_path: str) -> int:
    """Imprime el reporte y retorna 0 (todo PASS) o 1 (hay FAILs)."""
    print(f"\n{'═'*60}")
    print(f"  Transducin SR Verifier — {Path(dcm_path).name}")
    print(f"{'═'*60}")
    for r in results:
        print(r)
    print(f"{'═'*60}")

    fails = [r for r in results if r.status == "FAIL"]
    warns = [r for r in results if r.status == "WARN"]
    passes = [r for r in results if r.status == "PASS"]

    if not fails:
        print(f"\n  \033[92m✓ RESULTADO: PASS ({len(passes)} checks, {len(warns)} warnings)\033[0m\n")
        return 0
    else:
        print(f"\n  \033[91m✗ RESULTADO: FAIL ({len(fails)} errores, {len(warns)} warnings)\033[0m\n")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Verificar SR DICOM TID 1500")
    parser.add_argument("dcm_path", help="Path al archivo .dcm SR")
    parser.add_argument("--orthanc", default=ORTHANC_URL_DEFAULT,
                        help=f"URL Orthanc (default: {ORTHANC_URL_DEFAULT})")
    args = parser.parse_args()

    results = verify_sr(args.dcm_path, args.orthanc)
    sys.exit(print_report(results, args.dcm_path))


# ─────────────────────────────────────────────────────────────────────────────
# TESTS  —  python transducin/verify_sr.py (sin argumentos = modo test)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
        sys.exit()

    import tempfile
    import logging
    logging.basicConfig(level=logging.WARNING)

    from transducin.clinical_data import OCTClinicalData, ETDRSGrid
    from transducin.sr_builder import build_sr

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

    print("\n══ verify_sr — tests unitarios ══")

    cd = OCTClinicalData(
        noel_id     = "GHSI20000101",
        study_date  = "20000101",
        laterality  = "L",
        study_type  = "macular",
        patient_name = "SILTSPOOK^GHOST",
        patient_dob  = "20000101",
        cmt_um       = 312.0,
        etdrs_grid   = ETDRSGrid(C=312.0, S1=350.0),
        extraction_confidence = "confirmed",
    )

    with tempfile.TemporaryDirectory() as tmp:
        sr_path = Path(tmp) / "test_sr.dcm"
        build_sr(cd, output_path=sr_path)

        results = verify_sr(sr_path)
        status_map = {r.name: r.status for r in results}

        check("Archivo existe PASS",        status_map.get("Archivo existe"),       "PASS")
        check("Legible pydicom PASS",       status_map.get("Legible con pydicom"),  "PASS")
        check("SOPClassUID SR válido PASS", status_map.get("SOPClassUID es SR"),    "PASS")
        check("PatientID NOEL PASS",        status_map.get("PatientID formato NOEL"), "PASS")
        check("SNOMED CMT presente",        status_map.get("SNOMED 422453003"),     "PASS")
        check("≥1 SNOMED PASS",             status_map.get("Al menos 1 código SNOMED-CT"), "PASS")

        # SR inválido — sin NOEL ID
        bad_cd = OCTClinicalData(noel_id="BAD-ID", study_date="20000101")
        bad_sr = Path(tmp) / "bad_sr.dcm"
        build_sr(bad_cd, output_path=bad_sr)
        bad_results = verify_sr(bad_sr)
        bad_map = {r.name: r.status for r in bad_results}
        check("PatientID inválido → FAIL",  bad_map.get("PatientID formato NOEL"), "FAIL")

        rc = print_report(results, str(sr_path))
        check("print_report retorna 0",     rc, 0)

    print(f"\n{'══ TODOS LOS TESTS PASARON ══' if errors==0 else f'══ {errors} FALLARON ══'}\n")
    sys.exit(0 if errors == 0 else 1)
