"""Batch validation: Cirrus HD-OCT SD-S2 (macular) + SD-S10 (disc) → CSV.

Dry-run — no C-STORE, no Orthanc.
Usage: .venv/bin/python validation/run_cirrus_batch.py
"""
from __future__ import annotations

import csv
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pydicom

from transducin.cirrus_extractor import (
    _MAC_N_BSCANS, _MAC_N_ASCANS, _SOP_RAW_DATA,
    _clean_tag_str, _compute_thickness_map, _etdrs_from_map,
    _parse_fovea, _parse_layers, _parse_onh,
)

DATAFILES = Path(__file__).parent.parent / "input" / "DATAFILES"
OUT_CSV   = Path(__file__).parent / "cirrus_full_results.csv"
# SD-S2/SD-S10 appear in OCT image files; SOP.66 analysis files use SD-MTA (macular)
# and SD-GOUA (disc). Both sets are included to cover either tagging convention.
ALLOWED_CODES = {"SD-S2", "SD-S10", "SD-MTA", "SD-GOUA"}


def _scan_code(ds: pydicom.Dataset) -> str:
    ppc = ds.get((0x0040, 0x0260))
    if not ppc:
        return ""
    try:
        return _clean_tag_str(ppc[0].get("CodeValue", ""))
    except Exception:
        return ""


def process_file(f: Path) -> dict:
    row = {
        "filename":     f.name,
        "study_dir":    f.parent.parent.name,
        "scan_code":    "",
        "study_type":   "",
        "laterality":   "",
        "parse_success": False,
        "cmt_um":       "",
        "etdrs_present": False,
        "rnfl_global":  "",
        "cdr":          "",
        "error":        "",
    }
    try:
        ds = pydicom.dcmread(str(f), stop_before_pixels=True)

        sop = _clean_tag_str(ds.get("SOPClassUID", "") or "")
        if not sop.startswith(_SOP_RAW_DATA):
            row["error"] = f"SOP={sop[:40]}"
            return row

        code = _scan_code(ds)
        row["scan_code"] = code
        if code not in ALLOWED_CODES:
            row["error"] = f"scan_code={code} excluded"
            return row

        # Laterality — use attribute access to get value directly
        lat = str(getattr(ds, "Laterality", "") or "").split("\x00")[0].strip().upper()
        if lat in ("OD", "R"):
            lat = "R"
        elif lat in ("OS", "L"):
            lat = "L"
        row["laterality"] = lat

        study_type = ""
        cmt_um = None
        etdrs_present = False
        rnfl_global = None
        cdr = None

        # Macular layers
        layers = _parse_layers(ds)
        if layers is not None and layers[0].shape == (_MAC_N_BSCANS, _MAC_N_ASCANS):
            ilm, bm = layers
            th = _compute_thickness_map(ilm, bm)
            cx, cy = _parse_fovea(ds)
            cmt, grid = _etdrs_from_map(th, cx, cy)
            cmt_um = round(cmt, 1) if cmt is not None else None
            etdrs_present = grid is not None and grid.C is not None
            study_type = "macular"

        # ONH
        onh = _parse_onh(ds)
        if onh.get("rnfl_avg") is not None:
            rnfl_global = round(onh["rnfl_avg"], 2)
            if onh.get("cdr") is not None:
                cdr = round(onh["cdr"], 3)
            study_type = "optic_nerve"  # ONH es primario — sobreescribe macular si ambos

        row["study_type"]    = study_type
        row["cmt_um"]        = cmt_um if cmt_um is not None else ""
        row["etdrs_present"] = etdrs_present
        row["rnfl_global"]   = rnfl_global if rnfl_global is not None else ""
        row["cdr"]           = cdr if cdr is not None else ""
        row["parse_success"] = bool(study_type)

    except Exception as exc:
        row["error"] = str(exc)[:120]

    return row


def main() -> None:
    rows = []
    dcm_files = sorted(DATAFILES.rglob("*.DCM"))
    print(f"Escaneando {len(dcm_files)} archivos .DCM en {DATAFILES}...")

    for f in dcm_files:
        row = process_file(f)
        if row["error"].startswith("SOP="):
            continue  # non-analysis files — skip silently
        if row["error"].startswith("scan_code=") and "excluded" in row["error"]:
            continue  # SD-S51/SD-S21 — skip
        rows.append(row)

    fieldnames = [
        "filename", "study_dir", "scan_code", "study_type", "laterality",
        "parse_success", "cmt_um", "etdrs_present", "rnfl_global", "cdr", "error",
    ]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"CSV escrito: {OUT_CSV}  ({len(rows)} filas)")

    # Summary
    success = [r for r in rows if r["parse_success"]]
    macular = [r for r in success if r["study_type"] == "macular"]
    disc    = [r for r in success if r["study_type"] == "optic_nerve"]
    both    = [r for r in success if r["study_type"] == "macular" and r["rnfl_global"]]
    errors  = [r for r in rows if r["error"] and not r["error"].startswith("scan_code=")]

    print(f"\n{'─'*50}")
    print(f"Procesados SD-S2 + SD-S10 : {len(rows)}")
    print(f"  parse_success=True       : {len(success)}")
    print(f"  study_type=macular       : {len(macular)}")
    print(f"  study_type=optic_nerve   : {len(disc)}")
    print(f"  ambos CMT+RNFL           : {len(both)}")
    print(f"  errores                  : {len(errors)}")
    if errors:
        for r in errors[:5]:
            print(f"    {r['filename']}: {r['error']}")


if __name__ == "__main__":
    main()
