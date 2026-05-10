#!/usr/bin/env python3
"""
Full pipeline batch — parse + DICOM + SR — modo streaming sin C-STORE.

Para cada .opt:
  1. Parse → OCTClinicalData
  2. opt_to_dicom → _OCT.dcm (validar + eliminar) + _SLO.dcm + _ENFACE.dcm (conservar)
  3. build_sr → SR TID 1500 (conservar)
  4. verify_sr → contar FAILs (sin check Orthanc ni NOEL-format)
  5. Row en CSV

PatientID: noel_id si disponible; "VALIDATION" si no.
Output:    /tmp/transducin_output/
CSV:       validation/pipeline_results.csv
"""

import csv
import os
import sys
import shutil
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pydicom

from transducin.opt_extractor import extract_from_opt, build_noel_index
from transducin.revo_opt_reader import opt_to_dicom
from transducin.sr_builder import build_sr
from transducin.verify_sr import verify_sr

CORPUS_ROOT = Path("corpus")
OUTPUT_ROOT = Path("/tmp/transducin_output")
OUT_CSV     = Path(__file__).parent / "pipeline_results.csv"

GROUPS = [
    ("CUU/21.1.2",         "CUU", "FC130",   "21.1.2"),
    ("CUU/21.5.0",         "CUU", "FC130",   "21.5.0"),
    ("QRO/REVO 60 NUEVOS", "QRO", "REVO60",  "11.5.x"),
    ("QRO/REVO 60 VIEJOS", "QRO", "REVO60",  "11.5.x"),
    ("QRO/REVO130/Nuevos", "QRO", "REVO130", "11.5.x"),
    ("QRO/REVO130/Viejos", "QRO", "REVO130", "11.5.x"),
]

FIELDNAMES = [
    "id", "site", "device", "soct_version", "filename",
    "parse_ok", "dicom_ok", "sr_ok",
    "study_type", "lateralidad", "cmt_um", "sqi",
    "pydicom_errors", "tiene_noel_id",
    "n_dcm_generados", "dcm_mb",
    "t_parse_s", "t_dicom_s", "t_sr_s",
    "error",
]

# Tags Type 1 obligatorios para OphthalmicTomographyImageStorage
_OCT_TYPE1 = [
    "PatientID", "StudyDate", "StudyInstanceUID",
    "SeriesInstanceUID", "SOPInstanceUID", "SOPClassUID",
    "Modality", "NumberOfFrames",
]

# Checks de verify_sr que ignoramos en el conteo de errores del batch
_VERIFY_IGNORE = {"Encontrado en Orthanc", "PatientID formato NOEL"}


def _validate_oct_dcm(dcm_path: Path) -> tuple[bool, int]:
    """Lee el OCT.dcm, verifica Type 1, devuelve (ok, n_fail)."""
    try:
        ds = pydicom.dcmread(str(dcm_path))
    except Exception:
        return False, len(_OCT_TYPE1)
    fails = sum(
        1 for tag in _OCT_TYPE1
        if not str(getattr(ds, tag, "")).strip()
    )
    return fails == 0, fails


def process_file(
    opt_path: Path,
    site: str,
    device: str,
    soct_version: str,
    idx: int,
    noel_index: dict,
) -> dict:
    row = {
        "id": idx, "site": site, "device": device,
        "soct_version": soct_version, "filename": opt_path.name,
        "parse_ok": False, "dicom_ok": False, "sr_ok": False,
        "study_type": "", "lateralidad": "", "cmt_um": "",
        "sqi": "", "pydicom_errors": 0, "tiene_noel_id": False,
        "n_dcm_generados": 0, "dcm_mb": 0.0,
        "t_parse_s": 0.0, "t_dicom_s": 0.0, "t_sr_s": 0.0,
        "error": "",
    }

    # ── 1. Parse ─────────────────────────────────────────────────────────────
    t0 = time.time()
    try:
        cd = extract_from_opt(opt_path, noel_index=noel_index)
        row["parse_ok"]   = True
        row["study_type"] = cd.study_type or ""
        row["lateralidad"]= cd.laterality or ""
        row["cmt_um"]     = f"{cd.cmt_um:.1f}" if cd.cmt_um is not None else ""
        row["sqi"]        = f"{cd.sqi_mean:.3f}" if cd.sqi_mean is not None else ""
        row["tiene_noel_id"] = bool(cd.noel_id)
    except Exception as e:
        row["error"] = f"parse: {e}"
        row["t_parse_s"] = round(time.time() - t0, 2)
        return row
    row["t_parse_s"] = round(time.time() - t0, 2)

    patient_id  = cd.noel_id or "VALIDATION"
    patient_dir = OUTPUT_ROOT / patient_id
    img_dir     = patient_dir / "images"
    sr_dir      = patient_dir / "sr"
    img_dir.mkdir(parents=True, exist_ok=True)
    sr_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. DICOM generation → validate OCT.dcm → delete OCT.dcm ─────────────
    # fundus files have no B-scans or en-face chunks readable by opt_to_dicom
    # (image lives in PREVIEW.DAT JPEG, not EYE/FNDSRECO). Mark dicom_ok=True
    # with n_dcm_generados=0 — "not applicable", not a pipeline failure.
    _FUNDUS_TYPES = {"fundus", "color_fundus"}
    t1 = time.time()
    dcm_paths: list[Path] = []
    oct_errors = 0
    if cd.study_type in _FUNDUS_TYPES:
        row["dicom_ok"]        = True
        row["n_dcm_generados"] = 0
        row["dcm_mb"]          = 0.0
    else:
        try:
            dcm_paths = opt_to_dicom(
                opt_path, img_dir,
                noel_id      = patient_id,
                study_date   = cd.study_date   or "",
                study_time   = cd.study_time   or "",
                laterality   = cd.laterality   or "",
                patient_name = cd.patient_name or "",
                study_type   = cd.study_type   or "",
            )
            # Validate + stream-delete the OCT cube (largest file)
            for p in list(dcm_paths):
                if p.name.endswith("_OCT.dcm"):
                    ok, fails = _validate_oct_dcm(p)
                    oct_errors += fails
                    p.unlink()          # eliminar inmediatamente
                    dcm_paths.remove(p)

            kept_mb = sum(p.stat().st_size for p in dcm_paths if p.exists()) / 1024 / 1024
            row["dicom_ok"]       = (oct_errors == 0)
            row["n_dcm_generados"]= len(dcm_paths)
            row["dcm_mb"]         = round(kept_mb, 1)
            row["pydicom_errors"] = oct_errors
        except Exception as e:
            row["error"] = f"dicom: {e}"
            row["t_dicom_s"] = round(time.time() - t1, 2)
            return row
    row["t_dicom_s"] = round(time.time() - t1, 2)

    # ── 3. SR TID 1500 ────────────────────────────────────────────────────────
    t2 = time.time()
    try:
        ref_ds   = pydicom.dcmread(str(dcm_paths[0])) if dcm_paths else None
        stem     = opt_path.stem.replace(" ", "_")
        sr_path  = sr_dir / f"{stem}_SR.dcm"

        # Temporarily set noel_id to "VALIDATION" on cd if needed so build_sr accepts it
        _orig_noel = cd.noel_id
        cd.noel_id = patient_id
        build_sr(cd, reference_dataset=ref_ds, output_path=sr_path)
        cd.noel_id = _orig_noel

        # verify_sr — contar FAILs (excl. Orthanc y NOEL-format)
        checks   = verify_sr(sr_path)
        sr_fails = sum(
            1 for c in checks
            if c.status == "FAIL" and c.name not in _VERIFY_IGNORE
        )
        row["pydicom_errors"] += sr_fails
        row["sr_ok"]           = (sr_fails == 0)
    except Exception as e:
        row["error"] += f" sr: {e}"
        row["sr_ok"]  = False
    row["t_sr_s"] = round(time.time() - t2, 2)

    return row


def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    all_files: list[tuple[Path, str, str, str]] = []
    for rel_dir, site, device, soct in GROUPS:
        d = CORPUS_ROOT / rel_dir
        for f in sorted(d.rglob("*.opt")):
            all_files.append((f, site, device, soct))

    total = len(all_files)
    print(f"Pipeline batch — {total} archivos\n")

    # Build NOEL cross-reference index across the full corpus
    print("Construyendo NOEL index...")
    noel_index: dict[str, str] = {}
    for rel_dir, *_ in GROUPS:
        noel_index.update(build_noel_index(CORPUS_ROOT / rel_dir))
    print(f"NOEL index: {len(noel_index)} pacientes\n")

    rows      = []
    t_start   = time.time()
    n_parse   = n_dicom = n_sr = n_noel = 0

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()

        for idx, (opt_path, site, device, soct) in enumerate(all_files, 1):
            row = process_file(opt_path, site, device, soct, idx, noel_index)
            writer.writerow(row)
            rows.append(row)

            if row["parse_ok"]: n_parse += 1
            if row["dicom_ok"]: n_dicom += 1
            if row["sr_ok"]:    n_sr    += 1
            if row["tiene_noel_id"]: n_noel += 1

            elapsed  = time.time() - t_start
            eta_s    = (elapsed / idx) * (total - idx)
            eta_min  = eta_s / 60
            status   = f"{'✓' if row['parse_ok'] else '✗'}p {'✓' if row['dicom_ok'] else '✗'}d {'✓' if row['sr_ok'] else '✗'}sr"
            cmt_s    = f"CMT={row['cmt_um']}µm" if row["cmt_um"] else ""
            err_s    = f" ERR:{row['error'][:50]}" if row["error"] else ""
            print(
                f"  [{idx:>3}/{total}] {status}  "
                f"{opt_path.name[:45]:<45}  "
                f"{row['study_type']:<12}  {cmt_s:<12}  "
                f"ETA:{eta_min:.0f}min{err_s}"
            )
            f.flush()

    # ── Resumen final ─────────────────────────────────────────────────────────
    elapsed_total = time.time() - t_start
    failed_sr     = [r for r in rows if not r["sr_ok"]]
    failed_dicom  = [r for r in rows if not r["dicom_ok"]]

    lines = [
        "",
        "=" * 65,
        "TRANSDUCIN — PIPELINE BATCH RESULTS",
        "=" * 65,
        f"Total archivos        : {total}",
        f"Parse OK              : {n_parse}/{total}",
        f"DICOM OK              : {n_dicom}/{total}",
        f"SR OK                 : {n_sr}/{total}",
        f"Con NOEL ID real      : {n_noel}/{total}",
        f"Con VALIDATION ID     : {total - n_noel}/{total}",
        f"Tiempo total          : {elapsed_total/60:.1f} min",
        f"Output conservado     : {OUTPUT_ROOT}",
        f"CSV                   : {OUT_CSV}",
    ]
    if failed_dicom:
        lines += [f"\nDICOM FAILs ({len(failed_dicom)}):"]
        for r in failed_dicom[:10]:
            lines.append(f"  [{r['site']}/{r['device']}] {r['filename']}: {r['error'][:80]}")
    if failed_sr:
        lines += [f"\nSR FAILs ({len(failed_sr)}):"]
        for r in failed_sr[:10]:
            lines.append(f"  [{r['site']}/{r['device']}] {r['filename']}: {r['error'][:80]}")

    report = "\n".join(lines)
    print(report)
    (Path(__file__).parent / "pipeline_stats.txt").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
